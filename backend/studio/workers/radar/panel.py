"""Dados do painel do radar (origem: `dados_painel` e `relatorio` de estudio/radar.py e os textos de
estudio/radar/painel.html do canais)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from studio.db import session_scope
from studio.models import RadarChannel, RadarNiche, RadarRun, RadarVideo
from studio.workers.radar.analysis import DIAS_POR_MES, MERCADOS, arbitragem, fmt
from studio.workers.radar.catalog import load_catalog
from studio.workers.radar.collect import RadarError, run_dict

# Fontes citadas no painel do canais
FONTES = {
    1: "https://support.google.com/youtube/answer/9314357?hl=en",
    2: "https://support.google.com/youtube/answer/1311392?hl=en",
    3: "https://air.io/en/air-data-findings/which-youtube-niche-makes-the-most-money-in-2026-ranked-by-real-rpm-and-cpm",
    4: "https://mubert.com/blog/youtube-rpm-by-niche-and-country",
    5: "https://support.google.com/youtube/answer/72851?hl=en",
    6: "https://support.google.com/youtube/answer/6162278?hl=en",
    7: "https://support.google.com/youtube/thread/356734251/response-to-creator-questions-about-ypp-policies-july-2025?hl=en-GB",
}

# "Para monetizar" (painel.html)
FACTS: list[dict[str, str]] = [
    {
        "label": "Entrada no YPP",
        "url": FONTES[5],
        "text": "1.000 inscritos e 4.000 horas assistidas em 12 meses, ou 10 mi de views em Shorts em 90 dias.",
    },
    {
        "label": "Vídeo longo ajuda",
        "url": "",
        "text": "junta horas mais rápido. Se cada view rendesse 10 min assistidos, 24 mil views cobririam as 4.000 horas "
        "(conta ilustrativa).",
    },
    {
        "label": "Originalidade",
        "url": FONTES[2],
        "text": "conteúdo repetitivo, com template ou produzido em massa não monetiza, com ou sem IA.",
    },
    {
        "label": "Anúncios limitados",
        "url": FONTES[6],
        "text": "violência, conteúdo chocante e tragédias podem reduzir os anúncios do vídeo.",
    },
    {
        "label": "IA",
        "url": FONTES[7],
        "text": "canais com IA podem monetizar, mas conteúdo sintético realista precisa ser declarado.",
    },
    {
        "label": "RPM por categoria",
        "url": FONTES[3],
        "text": "mediana de US$ 10,22 em Educação e ciência; US$ 2,01 em Negócios e finanças, numa amostra pequena (*).",
    },
]

# RPM por país da audiência (US$ por mil views; amostra de canais entre out/2025 e out/2026)
MARKETS: list[dict[str, Any]] = [
    {"country": "Austrália", "rpm": 7.08},
    {"country": "Noruega", "rpm": 6.35},
    {"country": "EUA", "rpm": 5.12},
    {"country": "Canadá", "rpm": 5.04},
    {"country": "Reino Unido", "rpm": 4.65},
    {"country": "Média global", "rpm": 1.14},
]
MARKETS_SOURCE = FONTES[4]
RPM_SOURCE = FONTES[1]


def _run_radar(run: RadarRun, cfg: dict[str, Any]) -> dict[str, Any]:
    """Os números com que a coleta foi feita (`run.params`); o catálogo atual só cobre coletas antigas sem params."""
    params = run.params or {}
    return {
        **cfg["radar"],
        **{k: params[k] for k in ("dias", "hit_us", "hit_br", "novo_meses") if params.get(k) is not None},
    }


def _method(
    run: RadarRun, cfg: dict[str, Any], n_videos: int, n_channels: int
) -> tuple[list[str], list[str]]:
    r = _run_radar(run, cfg)
    steps = [
        f"{run.searches} buscas oficiais na YouTube Data API ({run.source or ''}): um termo por nicho e mercado, "
        f"vídeos dos últimos {r.get('dias', 30)} dias ordenados por views, na faixa de duração do nicho. EUA em inglês "
        "e Brasil em português (região e idioma da busca).",
        f"{n_videos} vídeos de {n_channels} canais, sem Shorts (3 min ou mais) e só no idioma do mercado. De cada canal: "
        "data de criação, inscritos, views totais e país.",
        f"Canais com até 18 meses e um hit ({fmt(r.get('hit_us', 100000))}+ nos EUA, {fmt(r.get('hit_br', 50000))}+ no "
        "Brasil): os ~20 vídeos mais recentes, para separar formato que se repete de viral isolado. Veredito automático: "
        "Modelar (metade ou mais dos vídeos com 7+ dias acima do hit, com 4 ou mais), Observar (2+ hits), Estudar o "
        "viral (1 hit).",
        f"Cota usada: {run.units} unidades (busca = 100, o resto = 1).",
    ]
    limits = [
        "Cada busca devolve até 50 vídeos: é amostra do que está em alta, não censo do nicho.",
        "A data de criação do canal nem sempre é a do primeiro vídeo.",
        "O idioma é filtrado pelo idioma declarado e pelas palavras do título; pode errar.",
        "RPM por categoria vem da pesquisa de 29/10/2026 (AIR Media-Tech); * = categoria com amostra pequena. Não existe "
        "RPM por nicho confiável.",
        "Não confere se o canal mostra rosto nem se reutiliza conteúdo: olhe antes de modelar.",
    ]
    return steps, limits


def panel_data(run_id: int | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tudo o que a página Radar mostra de uma coleta (a última com sucesso, sem `run_id`)."""
    cfg = cfg or load_catalog()
    nomes = {n["id"]: n for n in cfg["nichos"]}
    rpm = cfg.get("rpm", {})

    def nk(nid: str, mk: str) -> str:
        return f"{nomes.get(nid, {'nome': nid})['nome']} · {MERCADOS[mk]['nome']}"

    with session_scope() as s:
        if run_id is None:
            run = s.query(RadarRun).filter(RadarRun.status == "ok").order_by(RadarRun.id.desc()).first()
        else:
            run = s.get(RadarRun, run_id)
        if run is None:
            raise RadarError(
                "nenhuma coleta ainda: rode a coleta do radar"
                if run_id is None
                else f"coleta não encontrada: {run_id}"
            )
        quando = run.created_at
        rows = s.query(RadarNiche).filter(RadarNiche.run_id == run.id).all()
        niches = []
        for r in rows:
            if (
                not r.n or not r.med
            ):  # nicho sem vídeo aceito não entra no mapa (escala log); a arbitragem usa todos
                continue
            n = nomes.get(r.niche, {"nome": r.niche, "cat": "", "en": [], "pt": []})
            cat = n.get("cat", "")
            niches.append(
                {
                    "name": nk(r.niche, r.market),
                    "niche": r.niche,
                    "mk": r.market,
                    "q": n["en" if r.market == "us" else "pt"],
                    "n": r.n,
                    "med": round(r.med or 0),
                    "p75": round(r.p75 or 0),
                    "h100": r.h100,
                    "h500": r.h500,
                    "newShare": r.new_share or 0,
                    "newCh": r.new_hit,
                    "bestNew": r.best_new,
                    "subs": max(r.subs_med or 0, 1),
                    "len": round(r.length_med or 0, 1),
                    "cat": cat,
                    "rpm": (rpm.get(cat) or {}).get("rpm", 0.0),
                    "small": bool((rpm.get(cat) or {}).get("pequena", False)),
                }
            )
        por_video: dict[str, dict[str, Any]] = {}
        for v in s.query(RadarVideo).filter(RadarVideo.run_id == run.id).all():
            x = por_video.setdefault(
                v.youtube_id,
                {
                    "id": v.youtube_id,
                    "t": v.title,
                    "v": v.views,
                    "c": v.channel_youtube_id,
                    "a": (quando - v.published_at).days if v.published_at else None,
                    "l": round((v.duration_s or 0) / 60, 1),
                    "n": [],
                },
            )
            x["n"].append(nk(v.niche, v.market))
        canais_ids = {v["c"] for v in por_video.values()}
        channels = []
        for c in (
            s.query(RadarChannel).filter(RadarChannel.youtube_id.in_(canais_ids)).all() if canais_ids else []
        ):
            seus = sorted(
                (x for x in por_video.values() if x["c"] == c.youtube_id), key=lambda x: -(x["v"] or 0)
            )
            criado = dt.date.fromisoformat(c.created_on) if c.created_on else None
            idade_m = (quando.date() - criado).days / DIAS_POR_MES if criado else None
            ch: dict[str, Any] = {
                "id": c.youtube_id,
                "name": c.name,
                "url": c.url,
                "country": c.country,
                "joined": c.created_on,
                "ageM": idade_m,
                "subs": c.subscribers,
                "views": c.views,
                "videos": c.videos,
                "niches": sorted({n for x in seus for n in x["n"]}),
                "sample": [x["id"] for x in seus],
                "best": seus[0]["id"] if seus else None,
                "bestViews": seus[0]["v"] if seus else 0,
                "vpm": round(c.views / max(idade_m or 1, 1)) if c.views else 0,
                "flag": c.alert,
            }
            if c.recent:
                ch.update(c.recent)
            channels.append(ch)
        conhecidos = {c["id"] for c in channels}
        for v in por_video.values():
            if v["c"] not in conhecidos:
                v["c"] = None
        aggs = {
            (r.niche, r.market): {
                "med": round(r.med or 0),
                "h100": r.h100,
                "new_hit": r.new_hit,
                "n": r.n,
                "n10k": r.n10k,
                "best": r.best,
                "new_share": r.new_share or 0,
            }
            for r in rows
        }
        run_info = run_dict(run)
        steps, limits = _method(run, cfg, len(por_video), len(channels))
        rr = _run_radar(run, cfg)
    recs = []
    for mk in MERCADOS:
        bons = sorted(
            (
                c
                for c in channels
                if not c.get("flag")
                and c.get("verdict") in ("Modelar", "Observar")
                and any(n.endswith(MERCADOS[mk]["nome"]) for n in c["niches"])
            ),
            key=lambda c: (c.get("verdict") != "Modelar", -(c["vpm"] or 0)),
        )[:4]
        recs += [
            {
                "n": str(i + 1),
                "mk": mk,
                "id": c["id"],
                "t": f"{', '.join(c['niches'][:2])} · {round(c.get('typLen') or 0)} min",
            }
            for i, c in enumerate(bons)
        ]
    skipped = list(run_info["skipped"])
    return {
        "meta": {
            "run_id": run_info["id"],
            "captured": quando.strftime("%d/%m/%Y"),
            "searches": run_info["searches"],
            "units": run_info["units"],
            "videos": len(por_video),
            "channels": len(channels),
            "source": run_info["source"],
            "description": f"YouTube · EUA (inglês) e Brasil (português) · coleta de {quando:%d/%m/%Y} · "
            f"{run_info['searches']} buscas",
            "method": steps,
            "limits": limits,
            "skipped": skipped,
            "hit_us": rr.get("hit_us", 100000),
            "hit_br": rr.get("hit_br", 50000),
        },
        "niches": niches,
        "channels": channels,
        "videos": list(por_video.values()),
        "arb": arbitragem(aggs, cfg),
        "recs": recs,
        "avoid": cfg["radar"].get("evitar", []),
        "rpm": rpm,
        "facts": FACTS,
        "markets": MARKETS,
        "markets_source": MARKETS_SOURCE,
        "rpm_source": RPM_SOURCE,
    }


def report_lines(top: int = 4, cfg: dict[str, Any] | None = None) -> list[str]:
    """O resumo da última coleta em linhas de texto (= `relatorio` do canais)."""
    try:
        d = panel_data(None, cfg)
    except RadarError:
        return ["nenhuma coleta ainda: rode a coleta do radar"]
    linhas = [d["meta"]["description"] + f" · {d['meta']['videos']} vídeos, {d['meta']['channels']} canais"]
    for mk, m in MERCADOS.items():
        ns = sorted(
            (n for n in d["niches"] if n["mk"] == mk and n["n"]), key=lambda n: (-n["newCh"], -n["med"])
        )[:top]
        if ns:
            linhas.append(
                f"{m['nome']}, mais abertos para canal novo: "
                + "; ".join(
                    f"{n['name'].rsplit(' · ', 1)[0]} ({n['newCh']} {'canal novo' if n['newCh'] == 1 else 'canais novos'} "
                    f"com hit, mediana {fmt(n['med'])}, {round(n['newShare'] * 100)}% dos vídeos de canais novos)"
                    for n in ns
                )
            )
    abertos = [a for a in d["arb"] if a["leitura"] == "Aberto em PT"][:top]
    if abertos:
        linhas.append(
            "Arbitragem EN→PT (aberto em português): "
            + "; ".join(
                f"{a['nome']} (EUA mediana {fmt(a['en']['med'])}; em PT só {a['pt']['n10k']} "
                f"{'vídeo' if a['pt']['n10k'] == 1 else 'vídeos'} de 10 mil+ no mês)"
                for a in abertos
            )
        )
    chs = {c["id"]: c for c in d["channels"]}
    for r in d["recs"][:top]:
        c = chs.get(r["id"])
        if c:
            meses = max(1, round(c["ageM"] or 0))
            linhas.append(
                f"Para modelar ({MERCADOS[r['mk']]['nome']}): {c['name']}, criado há {meses} "
                f"{'mês' if meses == 1 else 'meses'}, {c.get('hits', 0)}/{c.get('settled', 0)} vídeos "
                f"recentes com hit, {c['url']}"
            )
    return linhas
