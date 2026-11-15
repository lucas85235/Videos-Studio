"""Coleta do radar de nichos (origem: `coletar`, `gravar`, `gravar_nichos` e `reprocessar` de estudio/radar.py).

1. Uma busca por termo, nicho e mercado (vídeos dos últimos N dias, por views, na duração do nicho). Um termo por
   busca: o OR (`|`) da API devolve vídeos minúsculos (teste do canais em 03/11: mediana 12 com OR × 12.672 com um
   termo). Primeiro o 1º termo de todos os nichos e mercados, depois o 2º, até o orçamento acabar.
2. Estatísticas dos vídeos e dos canais (lotes de 50).
3. Canais novos com hit: os ~20 vídeos mais recentes, para separar formato que se repete de viral isolado.
4. Grava `RadarRun`, `RadarNiche`, `RadarVideo` e atualiza `RadarChannel`.
"""

from __future__ import annotations

import datetime as dt
import urllib.parse
from collections.abc import Callable
from typing import Any

import httpx

from studio.db import session_scope
from studio.models import Job, RadarChannel, RadarNiche, RadarRun, RadarVideo
from studio.workers.radar import access as radar_access
from studio.workers.radar.analysis import (
    DIAS_POR_MES,
    MERCADOS,
    agregar,
    alertas,
    analisar,
    candidatos,
    em_lotes,
    quando_iso,
    recentes,
)
from studio.workers.radar.catalog import load_catalog

Progress = Callable[[float, str], None]


class RadarError(ValueError):
    """Pedido inválido (nicho ou mercado desconhecido, coleta inexistente)."""


def _now(agora: dt.datetime | None) -> dt.datetime:
    agora = agora or dt.datetime.now(dt.UTC)
    return agora if agora.tzinfo else agora.replace(tzinfo=dt.UTC)


def _choose(
    cfg: dict[str, Any], nichos: list[str] | None, mercados: list[str] | None
) -> tuple[list[dict], list[str]]:
    mercados = list(dict.fromkeys(mercados or list(MERCADOS)))
    unknown = [m for m in mercados if m not in MERCADOS]
    if unknown:
        raise RadarError(f"mercado desconhecido: {', '.join(unknown)} (use us ou br)")
    ids = {n["id"] for n in cfg["nichos"]}
    missing = [n for n in (nichos or []) if n not in ids]
    if missing:
        raise RadarError(f"nicho fora do catálogo: {', '.join(missing)}")
    return [n for n in cfg["nichos"] if not nichos or n["id"] in nichos], mercados


def plan(
    cfg: dict[str, Any],
    nichos: list[str] | None = None,
    mercados: list[str] | None = None,
    orcamento: int | None = None,
    agora: dt.datetime | None = None,
) -> dict[str, Any]:
    """As buscas que cabem no orçamento (lógica exata do `coletar` do canais)."""
    r = cfg["radar"]
    escolhidos, mercados = _choose(cfg, nichos, mercados)
    desde = (_now(agora) - dt.timedelta(days=r.get("dias", 30))).strftime("%Y-%m-%dT%H:%M:%SZ")
    planos: list[tuple[tuple[str, str], str]] = []
    for i in range(max((len(n[k]) for n in escolhidos for k in ("en", "pt")), default=0)):
        for n in escolhidos:
            for mk in mercados:
                termos = n["en" if mk == "us" else "pt"]
                if i >= len(termos):
                    continue
                m = MERCADOS[mk]
                planos.append(
                    (
                        (n["id"], mk),
                        "search?"
                        + urllib.parse.urlencode(
                            {
                                "part": "snippet",
                                "type": "video",
                                "order": "viewCount",
                                "maxResults": 50,
                                "publishedAfter": desde,
                                "q": termos[i],
                                "regionCode": m["regiao"],
                                "relevanceLanguage": m["lingua"],
                                "videoDuration": n.get("duracao", "medium"),
                                "safeSearch": "none",
                            }
                        ),
                    )
                )
    orc = orcamento or r.get("orcamento", 3500)
    reserva = 2 * r.get("candidatos", 40) + 60  # vídeos, canais e os recentes dos candidatos
    cabem = max(0, (orc - reserva) // 100)
    if planos and cabem == 0:  # sem isso a coleta gravaria uma RadarRun "ok" vazia, que viraria a última coleta
        raise RadarError(
            f"o orçamento não cobre nenhuma busca (reserva de {reserva} unidades): use pelo menos {reserva + 100}"
        )
    feitos = planos[:cabem]
    cobertos = {k for k, _ in feitos}
    pulados = sorted(
        {f"{k[0]}/{k[1]}" for k, _ in planos[cabem:] if k not in cobertos}
    )  # só o que ficou sem termo
    return {
        "feitos": feitos,
        "pulados": pulados,
        "reserva": reserva,
        "cabem": cabem,
        "orcamento": orc,
        "nichos": [n["id"] for n in escolhidos],
        "mercados": mercados,
    }


def simulate(
    cfg: dict[str, Any] | None = None,
    nichos: list[str] | None = None,
    mercados: list[str] | None = None,
    orcamento: int | None = None,
    agora: dt.datetime | None = None,
) -> dict[str, Any]:
    """Quantas buscas e quanta cota, sem rede."""
    p = plan(cfg or load_catalog(), nichos, mercados, orcamento, agora)
    feitos = p["feitos"]
    return {
        "buscas": len(feitos),
        "unidades_previstas": len(feitos) * 100 + p["reserva"],
        "orcamento": p["orcamento"],
        "pulados": p["pulados"],
        "exemplo": feitos[0][1] if feitos else None,
    }


def collect(
    nichos: list[str] | None = None,
    mercados: list[str] | None = None,
    orcamento: int | None = None,
    *,
    cfg: dict[str, Any] | None = None,
    http: httpx.Client | None = None,
    agora: dt.datetime | None = None,
    progress: Progress | None = None,
) -> dict[str, Any]:
    """Coleta e grava. Sem acesso: `RadarAccessError` antes de gastar cota. Se a API recusar tudo no meio, grava a
    coleta como "falhou" e repassa o erro."""
    cfg = cfg or load_catalog()
    r = cfg["radar"]
    agora = _now(agora)
    say = progress or (lambda _f, _m: None)
    p = plan(cfg, nichos, mercados, orcamento, agora)
    feitos, pulados, orc = p["feitos"], p["pulados"], p["orcamento"]
    acc = radar_access.resolve_access()
    api = radar_access.Api(acc, orc, http)
    params = {k: r.get(k) for k in ("dias", "hit_us", "hit_br", "novo_meses", "candidatos")}
    params.update({"orcamento": orc, "nichos": p["nichos"], "mercados": p["mercados"]})
    try:
        say(0.05, f"{len(feitos)} buscas no YouTube")
        res = api.lote([c for _, c in feitos])
        buscas: dict[tuple[str, str], list[str]] = {}
        for (k, _), d in zip(feitos, res, strict=True):
            buscas.setdefault(k, []).extend(
                it["id"]["videoId"]
                for it in (d or {}).get("items", [])
                if (it.get("id") or {}).get("videoId")
            )
        falhas = sorted({f"{k[0]}/{k[1]}" for (k, _), d in zip(feitos, res, strict=True) if d is None})
        ids = list(dict.fromkeys(i for v in buscas.values() for i in v))
        say(0.35, f"Estatísticas de {len(ids)} vídeos")
        videos: dict[str, dict[str, Any]] = {}
        for d in api.lote(
            [f"videos?part=snippet,statistics,contentDetails&id={','.join(lt)}" for lt in em_lotes(ids)]
        ):
            for it in (d or {}).get("items", []):
                videos[it["id"]] = it
        cids = list(
            dict.fromkeys(
                v["snippet"]["channelId"]
                for v in videos.values()
                if (v.get("snippet") or {}).get("channelId")
            )
        )
        say(0.55, f"Dados de {len(cids)} canais")
        canais: dict[str, dict[str, Any]] = {}
        for d in api.lote([f"channels?part=snippet,statistics&id={','.join(lt)}" for lt in em_lotes(cids)]):
            for it in (d or {}).get("items", []):
                canais[it["id"]] = it
        analise = analisar(cfg, buscas, videos, canais, agora)
        livres = max(0, (api.orcamento - api.unidades) // 2)  # cada candidato custa 2 (playlist + vídeos)
        cand = candidatos(cfg, analise, min(r.get("candidatos", 40), livres))
        say(0.7, f"Vídeos recentes de {len(cand)} canais novos com hit")
        # recentes dos candidatos: pedido que falha (ex.: 404 de canal sem uploads públicos) só deixa o canal sem
        # "recentes"; não derruba a coleta depois de as buscas já terem gastado a cota
        pls = api.lote(
            [f"playlistItems?part=contentDetails&maxResults=20&playlistId=UU{c[2:]}" for c, _ in cand],
            strict=False,
        )
        vids_por_canal = {
            c: [
                it["contentDetails"]["videoId"]
                for it in (d or {}).get("items", [])
                if (it.get("contentDetails") or {}).get("videoId")
            ]
            for (c, _), d in zip(cand, pls, strict=True)
        }
        com_videos = [x for x in cand if vids_por_canal.get(x[0])]
        det = api.lote(
            [
                f"videos?part=snippet,statistics,contentDetails&id={','.join(vids_por_canal[c])}"
                for c, _ in com_videos
            ],
            strict=False,
        )
        for (c, mk), d in zip(com_videos, det, strict=True):
            if d is not None:
                analise["canais"][c]["recentes"] = recentes(
                    d.get("items", []), r.get(f"hit_{mk}", 100000), agora
                )
    except radar_access.RadarAccessError as exc:
        with session_scope() as s:
            s.add(
                RadarRun(
                    created_at=agora,
                    source=api.fonte,
                    searches=len(feitos),
                    units=api.unidades,
                    videos=0,
                    channels=0,
                    skipped=pulados,
                    status="falhou",
                    error=str(exc),
                    params=params,
                )
            )
        raise
    say(0.9, "Gravando")
    run_id = _save(
        analise,
        agora,
        {
            "fonte": api.fonte,
            "buscas": len(feitos),
            "unidades": api.unidades,
            "pulados": pulados + [f"{x} (falhou)" for x in falhas],
            "params": params,
        },
    )
    return {
        "run_id": run_id,
        "buscas": len(feitos),
        "unidades": api.unidades,
        "videos": len(analise["linhas"]),
        "canais": len({a["canal"] for a in analise["linhas"]}),
        "pulados": pulados,
        "falhas": falhas,
        "fonte": api.fonte,
    }


def _niche_row(run_id: int, nid: str, mk: str, a: dict[str, Any]) -> RadarNiche:
    return RadarNiche(
        run_id=run_id,
        niche=nid,
        market=mk,
        n=a["n"],
        med=a["med"],
        p75=a["p75"],
        h100=a["h100"],
        h500=a["h500"],
        new_share=a["new_share"],
        new_hit=a["new_hit"],
        best_new=a["best_new"],
        subs_med=a["subs_med"],
        length_med=a["length_med"],
        n10k=a["n10k"],
        best=a["best"],
    )


def _save(analise: dict[str, Any], agora: dt.datetime, info: dict[str, Any]) -> int:
    linhas = analise["linhas"]
    usados = list(dict.fromkeys(a["canal"] for a in linhas))
    with session_scope() as s:
        run = RadarRun(
            created_at=agora,
            source=info["fonte"],
            searches=info["buscas"],
            units=info["unidades"],
            videos=len(linhas),
            channels=len(usados),
            skipped=info["pulados"],
            status="ok",
            params=info["params"],
        )
        s.add(run)
        s.flush()
        run_id = run.id
        for (nid, mk), a in analise["nichos"].items():
            s.add(_niche_row(run_id, nid, mk, a))
        vistos: set[tuple[str, str, str]] = set()
        for v in linhas:
            chave = (v["id"], v["nicho"], v["mercado"])
            if chave in vistos:
                continue
            vistos.add(chave)
            s.add(
                RadarVideo(
                    run_id=run_id,
                    youtube_id=v["id"],
                    niche=v["nicho"],
                    market=v["mercado"],
                    title=v["titulo"],
                    channel_youtube_id=v["canal"],
                    published_at=quando_iso(v["publicado"]),
                    views=v["views"],
                    duration_s=v["dur"],
                )
            )
        for cid in usados:
            c = analise["canais"][cid]
            row = s.get(RadarChannel, cid)
            if row is None:
                row = RadarChannel(youtube_id=cid)
                s.add(row)
            row.name, row.url, row.country, row.created_on = c["nome"] or "", c["url"], c["pais"], c["criado"]
            row.subscribers, row.views, row.videos = c["inscritos"], c["views"], c["videos"]
            if c.get("recentes"):  # recent novo só substitui quando veio: nunca apaga com vazio
                row.recent = c["recentes"]
            row.alert = c.get("alerta")
            row.updated_at = agora
    return run_id


def active_job() -> str | None:
    """Id do job `radar_collect` na fila ou rodando, se houver (evita gastar a cota do dia duas vezes)."""
    with session_scope() as s:
        row = (
            s.query(Job)
            .filter(Job.kind == "radar_collect", Job.status.in_(("queued", "running")))
            .order_by(Job.created_at.desc())
            .first()
        )
        return row.id if row else None


def run_dict(row: RadarRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "source": row.source or "",
        "searches": row.searches or 0,
        "units": row.units or 0,
        "videos": row.videos or 0,
        "channels": row.channels or 0,
        "skipped": list(row.skipped or []),
        "status": row.status or "ok",
        "error": row.error,
        "params": dict(row.params or {}),
    }


def list_runs(limit: int = 30) -> list[dict[str, Any]]:
    with session_scope() as s:
        return [run_dict(r) for r in s.query(RadarRun).order_by(RadarRun.id.desc()).limit(limit).all()]


def last_run(ok_only: bool = True) -> dict[str, Any] | None:
    with session_scope() as s:
        q = s.query(RadarRun)
        if ok_only:
            q = q.filter(RadarRun.status == "ok")
        row = q.order_by(RadarRun.id.desc()).first()
        return run_dict(row) if row else None


def reprocess(run_id: int | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Refaz alertas e contas de uma coleta com o que já está no banco (sem gastar cota): use depois de mudar o
    catálogo (palavras de alerta, hits, canal novo)."""
    cfg = cfg or load_catalog()
    with session_scope() as s:
        if run_id is None:
            row = s.query(RadarRun).filter(RadarRun.status == "ok").order_by(RadarRun.id.desc()).first()
        else:
            row = s.get(RadarRun, run_id)
        if row is None or row.status != "ok":
            raise RadarError(
                "nenhuma coleta ainda: rode a coleta do radar"
                if run_id is None
                else f"coleta não encontrada: {run_id}"
            )
        run_id = row.id
        quando = row.created_at
        linhas = [
            {
                "id": v.youtube_id,
                "nicho": v.niche,
                "mercado": v.market,
                "titulo": v.title or "",
                "canal": v.channel_youtube_id,
                "views": v.views or 0,
                "dur": v.duration_s or 0,
            }
            for v in s.query(RadarVideo).filter(RadarVideo.run_id == run_id).all()
        ]
        ids = sorted({a["canal"] for a in linhas})
        canais: dict[str, dict[str, Any]] = {}
        for c in s.query(RadarChannel).filter(RadarChannel.youtube_id.in_(ids)).all() if ids else []:
            criado = dt.date.fromisoformat(c.created_on) if c.created_on else None
            canais[c.youtube_id] = {
                "id": c.youtube_id,
                "nome": c.name,
                "pais": c.country,
                "inscritos": c.subscribers,
                "idade_m": (quando.date() - criado).days / DIAS_POR_MES if criado else None,
            }
        linhas = [a for a in linhas if a["canal"] in canais]
        alertas(cfg, linhas, canais)
        chaves = [(n.niche, n.market) for n in s.query(RadarNiche).filter(RadarNiche.run_id == run_id).all()]
        s.query(RadarNiche).filter(RadarNiche.run_id == run_id).delete()
        s.flush()
        for (nid, mk), a in agregar(cfg, linhas, canais, chaves).items():
            s.add(_niche_row(run_id, nid, mk, a))
        for cid, c in canais.items():
            ch = s.get(RadarChannel, cid)
            if ch is not None:
                ch.alert = c.get("alerta")
        # as contas agora usam os hits do catálogo atual: o painel lê os números de `params`
        r = cfg["radar"]
        row.params = {**(row.params or {}), **{k: r.get(k) for k in ("hit_us", "hit_br", "novo_meses")}}
        s.flush()
        return run_dict(row)
