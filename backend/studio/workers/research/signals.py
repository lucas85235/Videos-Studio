"""Coletores de sinais da pesquisa de vídeos. Cada um é isolado: se falhar, a rodada anota a falha e continua.

- `own_signals`: o próprio canal, longos e Shorts separados: a mediana de views por dia de cada formato (só vídeos
  públicos e assentados) e os 5 melhores e piores (`mult` = views por dia ÷ mediana). Com o login do canal, parte do
  inventário de uploads inteiro (`analytics/uploads.py`) e das views públicas de agora (videos.list: ~4 unidades para
  100 vídeos, sem `search`); sem ele, dos vídeos que o Vulto conhece com métrica;
- `competitor_signals`: vídeos fora da curva dos concorrentes (sem `search`: channels + playlistItems + videos), contra a
  mediana do PRÓPRIO concorrente no mesmo formato (vídeos com 7+ dias); Shorts entram marcados;
- `keyword_signals`: uma busca por termo e mercado ("br" e/ou "us"), com o idioma conferido e os canais fora do alvo
  (`[radar] alertas`) de fora; `ratio` = views ÷ inscritos (mínimo de 1.000). A busca (`search.list`) tem balde
  próprio no YouTube (100 chamadas por dia, somando o radar) e não pesa na cota geral (ver `publish/quota.py`);
- `comment_signals`: perguntas e pedidos dos comentários do canal (banco local, sem cota);
- `plan_units`: a conta da cota de uma rodada, sem rede.
"""

from __future__ import annotations

import datetime as dt
import math
import re
import urllib.parse
from typing import Any

from studio.db import session_scope
from studio.models import Channel, YouTubeComment
from studio.workers.radar import analysis
from studio.workers.radar.access import Api
from studio.workers.radar.analysis import MERCADOS, em_lotes, mediana, quando_iso, segundos_iso

SHORT_MAX_S = 180  # até 3 min conta como Short
SETTLED_DAYS = 7  # vídeo "assentado" (entra na linha de base do concorrente)
OWN_SETTLED_DAYS = {"long": 7, "short": 3}  # idem no próprio canal (Short assenta antes); mais novo: mult provisório
UNKNOWN_FORMAT = "desconhecido"  # sem duração (live, estreia, vídeo que não veio): fora das medianas
MIN_BASE = 3  # vídeos assentados do formato para a mediana do formato valer; menos que isso usa a do canal todo
OUTLIER_MULT = 2.0  # vídeo do concorrente fora da curva: 2× a mediana dele
TOP_N = 5
SEARCH_UNITS = 0  # `search.list` não pesa na cota geral: conta no balde próprio de 100 buscas por dia
PER_SEARCH_EXTRA = 2  # videos.list e channels.list de até 50 ids por busca (cota geral)
KEYWORD_ITEMS_PER_MARKET = 30
COMMENT_TEXT_MAX = 300

# Pedido de vídeo nos comentários (PT e EN). A pergunta vem de `inbox.is_question`.
REQUEST_RE = re.compile(
    r"(?:"
    r"fa(?:z|ça|ca|zer|zendo)\s+(?:um\s+|uma\s+)?(?:v[ií]deo|epis[oó]dio|parte)|"
    r"fala(?:r|\s+mais)?\s+(?:sobre|de|do|da|dos|das)\b|"
    r"parte\s*(?:2|dois|ii)\b|continua[cç][aã]o|continua\s+(?:a\s+)?s[eé]rie|"
    r"pr[oó]ximo\s+v[ií]deo|react\b|reage\s+a|"
    r"make\s+(?:a\s+)?video|do\s+a\s+video|part\s*(?:2|two|ii)\b|next\s+video|please\s+cover|"
    r"video\s+(?:on|about)\b"
    r")",
    re.IGNORECASE,
)


def _result(items: Any, units: int = 0, erro: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"items": items, "units": units, "erro": erro, **extra}


def format_of(duration_s: float | int | None) -> str:
    if not duration_s:
        return UNKNOWN_FORMAT
    return "short" if duration_s <= SHORT_MAX_S else "long"


def _watch(video_id: str, fmt: str = "long") -> str:
    return f"https://www.youtube.com/shorts/{video_id}" if fmt == "short" else f"https://youtu.be/{video_id}"


# ----------------------------------------------------------------------------- próprio canal


def _own_candidates(channel_id: str, videos: list[dict[str, Any]], http: Any, now: dt.datetime,
                    inv: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Os vídeos do canal com views, data, formato e privacidade. Com o login do canal: o inventário inteiro e as
    views de agora; senão (ou se a leitura falhar): os vídeos que o Vulto conhece, com a privacidade da última coleta.
    Devolve (vídeos, `{fonte, aviso, inventario}`)."""
    from studio.workers.analytics import uploads
    from studio.workers.publish import youtube as yt

    known = {v["youtube_id"]: v for v in videos}
    aviso = None
    own = http is None
    client = http or yt.new_client()
    try:
        inv = inv if inv is not None else uploads.inventory(channel_id, client, now=now)
        if inv["completo"] and inv["items"]:
            with session_scope() as s:
                row = s.get(Channel, channel_id)
                link = dict(((row.settings or {}) if row else {}).get("youtube") or {})
            token = inv.get("token") or uploads.channel_token(client, link)
            fresh = uploads.video_stats(client, token, [i["video_id"] for i in inv["items"]])
            out = []
            for yid, f in fresh.items():
                v = known.get(yid) or {}
                fmt = format_of(f["duration_s"])
                if fmt == UNKNOWN_FORMAT and v.get("format") in ("long", "short"):
                    fmt = v["format"]
                out.append({"youtube_id": yid, "title": f["title"] or v.get("title") or "", "format": fmt,
                            "privacy": f["privacy"], "views": f["views"], "published_at": f["published"],
                            "metric": v.get("metric") or {}})
            pages = math.ceil(len(inv["items"]) / 50) if inv.get("fonte") == "api" else 0  # playlistItems
            units = pages + math.ceil(len(inv["items"]) / 50)  # + videos.list
            return out, {"fonte": "youtube", "aviso": None, "inventario": len(inv["items"]), "units": units}
        aviso = inv.get("erro")
    except yt.YouTubeError as exc:
        aviso = str(exc)
    finally:
        if own:
            client.close()
    out = []
    for v in videos:
        m = v.get("metric") or {}
        out.append({"youtube_id": v["youtube_id"], "title": v.get("title") or "", "format": v.get("format"),
                    "privacy": v.get("privacy") or "public", "views": m.get("views"),
                    "published_at": v.get("published_at"), "metric": m, "vpd": v.get("views_per_day")})
    return out, {"fonte": "vulto", "aviso": aviso, "inventario": None, "units": 0}


def own_signals(channel_id: str, *, http: Any = None, now: dt.datetime | None = None,
                inventory: dict[str, Any] | None = None) -> dict[str, Any]:
    """Desempenho do próprio canal por formato. Fica de fora: vídeo que hoje não é público (um privado com views
    antigas ficaria com a idade subindo e as views paradas), formato desconhecido e vídeo sem views. A mediana usa só
    os assentados (`OWN_SETTLED_DAYS`); com menos de `MIN_BASE` no formato, vale a do canal todo (`base: "canal"`);
    sem nenhum, a dos novos (`base: "novos"`). Vídeo novo sai com `provisorio`. Por formato: `n_publicos` (públicos
    no canal), `n` (com views), `n_assentados` e `base_fraca` (menos de `MIN_BASE` assentados no formato)."""
    from studio.workers.analytics import experiments
    from studio.workers.analytics.variables import load_videos

    now = now or dt.datetime.now(dt.UTC)
    videos = load_videos(channel_id)
    cands, meta = _own_candidates(channel_id, videos, http, now, inventory)
    rows: list[dict[str, Any]] = []
    publicos = {"long": 0, "short": 0}
    fora_do_ar = 0
    for c in cands:
        fmt = c["format"]
        if fmt not in publicos:
            continue
        if c["privacy"] != "public":
            fora_do_ar += 1 if c.get("views") else 0
            continue
        publicos[fmt] += 1
        when = quando_iso(c["published_at"]) if c.get("published_at") else None
        age_d = (now - when).total_seconds() / 86400 if when else None
        if age_d is not None and age_d < 0:
            continue  # agendado que ainda não saiu
        if c.get("vpd") is not None:
            vpd = float(c["vpd"])
        elif c.get("views") is not None:
            vpd = c["views"] / max(age_d if age_d is not None else 1, 1)  # como `metrics.views_per_day`
        else:
            continue
        m = c.get("metric") or {}
        settled = age_d is None or age_d >= OWN_SETTLED_DAYS[fmt]  # sem data, não dá para dizer que é novo
        rows.append({"youtube_id": c["youtube_id"], "title": c["title"], "format": fmt, "views": c.get("views"),
                     "vpd": round(vpd, 1), "ctr": m.get("ctr"), "pct_watched": m.get("pct_watched"),
                     "published_at": c.get("published_at"), "idade_d": round(age_d, 1) if age_d is not None else None,
                     "provisorio": not settled, "url": _watch(c["youtube_id"], fmt)})
    settled_all = [r["vpd"] for r in rows if not r["provisorio"]]
    out: dict[str, Any] = {"fonte": meta["fonte"], "aviso": meta["aviso"], "inventario": meta["inventario"],
                           "fora_do_ar": fora_do_ar, "units": meta["units"]}
    for fmt in ("long", "short"):
        mine = [r for r in rows if r["format"] == fmt]
        settled = [r["vpd"] for r in mine if not r["provisorio"]]
        if len(settled) >= MIN_BASE:
            med, base = mediana(settled), "formato"
        elif len(settled_all) >= MIN_BASE:
            med, base = mediana(settled_all), "canal"
        else:
            med, base = mediana(r["vpd"] for r in mine), "novos"
        for r in mine:
            r["mult"] = round(r["vpd"] / med, 2) if med else None
        mine.sort(key=lambda r: -r["vpd"])
        out[fmt] = {"n": len(mine), "n_publicos": publicos[fmt], "n_assentados": len(settled),
                    "base_fraca": len(settled) < MIN_BASE, "base": base, "med": round(med, 1), "top": mine[:TOP_N],
                    "bottom": mine[TOP_N:][-TOP_N:][::-1]}
    try:
        out["suggestions"] = experiments.suggestions(experiments.results(channel_id, videos))
    except Exception:  # noqa: BLE001 - as sugestões são um extra
        out["suggestions"] = []
    return out


# ----------------------------------------------------------------------------- cota


def keyword_searches(cfg: dict[str, Any]) -> list[dict[str, str]]:
    """As buscas da rodada, intercalando os mercados termo a termo (o 1º termo de cada mercado vem antes do 2º)."""
    per_market: dict[str, list[str]] = {}
    for mk in cfg.get("markets") or ["br"]:
        terms = cfg.get("keywords") or []
        if mk == "us" and cfg.get("keywords_us"):
            terms = cfg["keywords_us"]
        per_market[mk] = list(terms)
    out: list[dict[str, str]] = []
    for i in range(max((len(t) for t in per_market.values()), default=0)):
        for mk, terms in per_market.items():
            if i < len(terms):
                out.append({"term": terms[i], "market": mk})
    return out


def competitor_units(n: int) -> int:
    """channels (50 por pedido) + uma playlistItems por concorrente + videos (50 ids por pedido, 50 por concorrente)."""
    return math.ceil(n / 50) + n + n if n else 0


def plan_units(cfg: dict[str, Any], skip: list[str] | tuple[str, ...] = (),
               search_left: int | None = None) -> dict[str, Any]:
    """A conta da cota de uma rodada, sem rede. Duas cotas do YouTube: a geral (unidades; concorrentes e as leituras
    de cada busca, com o teto `budget`) e a das buscas (`search_calls`, do balde de 100 buscas por dia; `search_left` =
    quantas ainda cabem hoje, pelo medidor). As buscas que não cabem vão para `pulados` (`pulados_motivo` diz qual
    teto barrou)."""
    from studio.workers.publish.quota import SEARCH_LIMIT

    n = 0 if "competitors" in skip else len(cfg.get("competitors") or [])
    comp = competitor_units(n)
    searches = [] if "keywords" in skip else keyword_searches(cfg)
    budget = int(cfg.get("budget") or 600)
    each = SEARCH_UNITS + PER_SEARCH_EXTRA
    fit = max(0, (budget - comp) // each)
    motivo = "orçamento" if fit < len(searches) else ""
    if search_left is not None and search_left < min(fit, len(searches)):
        fit, motivo = max(0, search_left), "buscas do dia"
    done, left = searches[:fit], searches[fit:]
    total = comp + each * len(done)
    return {
        "competitors": n,
        "competitors_units": comp,
        "keyword_units": each * len(done),
        "total": total,
        "budget": budget,
        "cabe": not left and total <= budget,
        "searches": done,
        "search_calls": len(done),
        "search_left": search_left,
        "search_limit": SEARCH_LIMIT,
        "pulados": [f"{s['term']} ({MERCADOS[s['market']]['nome']})" for s in left],
        "pulados_motivo": motivo if left else "",
    }


# ----------------------------------------------------------------------------- concorrentes


def _video_row(item: dict[str, Any], agora: dt.datetime) -> dict[str, Any] | None:
    sn, st = item.get("snippet") or {}, item.get("statistics") or {}
    if not sn.get("publishedAt"):
        return None
    dur = segundos_iso((item.get("contentDetails") or {}).get("duration"))
    fmt = format_of(dur)
    return {
        "id": item["id"],
        "title": sn.get("title", ""),
        "channel_id": sn.get("channelId", ""),
        "channel": sn.get("channelTitle", ""),
        "views": int(st.get("viewCount", 0) or 0),
        "dur": dur,
        "format": fmt,
        "idade_d": max(0, (agora - quando_iso(sn["publishedAt"])).days),
        "published_at": sn["publishedAt"],
        "url": _watch(item["id"], fmt),
    }


def competitor_signals(api: Api, competitors: list[dict[str, Any]], *, days: int, agora: dt.datetime,
                       min_mult: float = OUTLIER_MULT) -> dict[str, Any]:
    """Fora da curva dos concorrentes. Mediana do próprio concorrente por formato (vídeos com 7+ dias; com menos de 3
    no formato, a do canal todo); outlier = views ÷ mediana ≥ `min_mult`, publicado nos últimos `days` dias."""
    start = api.unidades
    ids = [c["id"] for c in competitors if c.get("id")]
    if not ids:
        return _result([], channels=[])
    chans: dict[str, dict[str, Any]] = {}
    for d in api.lote([f"channels?part=snippet,statistics,contentDetails&id={','.join(b)}" for b in em_lotes(ids)]):
        for it in (d or {}).get("items", []):
            chans[it["id"]] = it
    found = [c for c in ids if c in chans]
    erros = [f"concorrente {c} não encontrado no YouTube" for c in ids if c not in chans]
    playlists = []
    for cid in found:
        uploads = ((chans[cid].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
        playlists.append(uploads or f"UU{cid[2:]}")
    pls = api.lote([f"playlistItems?part=contentDetails&maxResults=50&playlistId={p}" for p in playlists], strict=False)
    por_canal = {
        cid: [it["contentDetails"]["videoId"] for it in (d or {}).get("items", [])
              if (it.get("contentDetails") or {}).get("videoId")]
        for cid, d in zip(found, pls, strict=True)
    }
    all_ids = [v for vs in por_canal.values() for v in vs]
    videos: dict[str, dict[str, Any]] = {}
    for d in api.lote([f"videos?part=snippet,statistics,contentDetails&id={','.join(b)}" for b in em_lotes(all_ids)],
                      strict=False):
        for it in (d or {}).get("items", []):
            videos[it["id"]] = it
    items: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for cid in found:
        sn, st = chans[cid].get("snippet") or {}, chans[cid].get("statistics") or {}
        name = sn.get("title", cid)
        rows = [r for v in por_canal[cid] if v in videos and (r := _video_row(videos[v], agora))]
        settled = [r for r in rows if r["idade_d"] >= SETTLED_DAYS]
        med_all = mediana(r["views"] for r in settled)
        meds: dict[str, float] = {}
        for fmt in ("long", "short"):
            same = [r["views"] for r in settled if r["format"] == fmt]
            meds[fmt] = mediana(same) if len(same) >= MIN_BASE else med_all
        subs = int(st["subscriberCount"]) if st.get("subscriberCount") else None
        summaries.append({"id": cid, "name": name, "handle": sn.get("customUrl", ""), "subs": subs, "n": len(rows),
                          "med_long": meds["long"], "med_short": meds["short"],
                          "url": f"https://www.youtube.com/channel/{cid}"})
        for r in rows:
            med = meds.get(r["format"])  # formato desconhecido (sem duração): sem mediana, fica de fora
            if not med or r["idade_d"] > days:
                continue
            mult = r["views"] / med
            if mult >= min_mult:
                items.append({**r, "channel": name, "channel_id": cid, "med": med, "mult": round(mult, 2)})
    items.sort(key=lambda r: -r["mult"])
    return _result(items, api.unidades - start, "; ".join(erros) or None, channels=summaries)


# ----------------------------------------------------------------------------- palavras-chave


def _search_path(term: str, market: str, desde: str) -> str:
    m = MERCADOS[market]
    return "search?" + urllib.parse.urlencode({
        "part": "snippet", "type": "video", "order": "viewCount", "maxResults": 50, "publishedAfter": desde,
        "q": term, "regionCode": m["regiao"], "relevanceLanguage": m["lingua"], "safeSearch": "none",
    })


def keyword_signals(api: Api, searches: list[dict[str, str]], *, days: int, agora: dt.datetime,
                    radar_cfg: dict[str, Any] | None = None,
                    per_market: int = KEYWORD_ITEMS_PER_MARKET) -> dict[str, Any]:
    """Uma busca por termo e mercado (sem duração: Shorts e longos vêm juntos e saem marcados). Vídeo no idioma
    errado ou de canal fora do alvo fica de fora. `outlier`: views ≥ `hit_<mercado>` e views ≥ inscritos."""
    if not searches:
        return _result([], searches=[])
    from studio.workers.radar.catalog import load_catalog

    radar_cfg = radar_cfg or load_catalog()
    start = api.unidades
    desde = (agora - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    results = api.lote([_search_path(s["term"], s["market"], desde) for s in searches])
    found: list[tuple[dict[str, str], list[str]]] = []
    for s, d in zip(searches, results, strict=True):
        ids = [it["id"]["videoId"] for it in (d or {}).get("items", []) if (it.get("id") or {}).get("videoId")]
        found.append((s, ids))
    all_ids = list(dict.fromkeys(v for _, ids in found for v in ids))
    videos: dict[str, dict[str, Any]] = {}
    for d in api.lote([f"videos?part=snippet,statistics,contentDetails&id={','.join(b)}" for b in em_lotes(all_ids)]):
        for it in (d or {}).get("items", []):
            videos[it["id"]] = it
    chan_ids = list(dict.fromkeys(c for v in videos.values() if (c := (v.get("snippet") or {}).get("channelId"))))
    canais: dict[str, dict[str, Any]] = {}
    for d in api.lote([f"channels?part=snippet,statistics&id={','.join(b)}" for b in em_lotes(chan_ids)],
                      strict=False):
        for it in (d or {}).get("items", []):
            sn, st = it.get("snippet") or {}, it.get("statistics") or {}
            canais[it["id"]] = {"nome": sn.get("title", it["id"]), "pais": sn.get("country"),
                                "inscritos": int(st["subscriberCount"]) if st.get("subscriberCount") else None}
    linhas: list[dict[str, Any]] = []
    seen: set[str] = set()
    for s, ids in found:
        for vid in ids:
            item = videos.get(vid)
            if vid in seen or item is None or not analysis.idioma_ok(item.get("snippet") or {}, s["market"]):
                continue
            row = _video_row(item, agora)
            if row is None or row["channel_id"] not in canais:
                continue
            seen.add(vid)
            linhas.append({**row, "term": s["term"], "market": s["market"], "canal": row["channel_id"],
                           "titulo": row["title"]})
    analysis.alertas(radar_cfg, linhas, canais)
    hits = radar_cfg.get("radar", {})
    items: list[dict[str, Any]] = []
    for r in linhas:
        c = canais[r["channel_id"]]
        if c.get("alerta"):
            continue
        subs = c.get("inscritos")
        ratio = r["views"] / max(subs or 0, 1000)
        hit = hits.get(f"hit_{r['market']}", 100000)
        items.append({
            "id": r["id"], "title": r["title"], "channel": c["nome"], "channel_id": r["channel_id"],
            "views": r["views"], "subs": subs, "ratio": round(ratio, 2), "term": r["term"], "market": r["market"],
            "format": r["format"], "dur": r["dur"], "idade_d": r["idade_d"], "outlier": r["views"] >= hit and ratio >= 1,
            "url": r["url"],
        })
    items.sort(key=lambda r: (not r["outlier"], -r["ratio"]))
    kept: list[dict[str, Any]] = []
    count: dict[str, int] = {}
    for r in items:
        if count.get(r["market"], 0) < per_market:
            kept.append(r)
            count[r["market"]] = count.get(r["market"], 0) + 1
    summary = [{"term": s["term"], "market": s["market"], "n": len(ids)} for s, ids in found]
    return _result(kept, api.unidades - start, searches=summary)


# ----------------------------------------------------------------------------- comentários


def comment_kind(text: str, is_question: bool | None = None) -> str | None:
    """"pedido" (pede vídeo, parte 2, react...), "pergunta" ou None (comentário comum)."""
    from studio.workers.comments.inbox import is_question as looks_like_question

    if REQUEST_RE.search(text or ""):
        return "pedido"
    if is_question or looks_like_question(text or ""):
        return "pergunta"
    return None


def comment_signals(channel_id: str, *, days: int = 90, limit: int = 60,
                    now: dt.datetime | None = None) -> dict[str, Any]:
    """Perguntas e pedidos dos últimos `days` dias, os mais curtidos primeiro (banco local, sem cota)."""
    from studio.workers.publish import youtube as yt

    since = (now or dt.datetime.now(dt.UTC)) - dt.timedelta(days=days)
    out: list[dict[str, Any]] = []
    with session_scope() as s:
        rows = (s.query(YouTubeComment)
                .filter(YouTubeComment.channel_id == channel_id, YouTubeComment.published_at >= since)
                .order_by(YouTubeComment.like_count.desc(), YouTubeComment.published_at.desc())
                .all())
        for row in rows:
            kind = comment_kind(row.text or "", row.is_question)
            if kind is None:
                continue
            out.append({
                "id": row.id, "video_id": row.video_id, "video_title": row.video_title or "",
                "text": (row.text or "")[:COMMENT_TEXT_MAX], "likes": row.like_count or 0, "kind": kind,
                "url": f"{yt.SITE}/watch?v={row.video_id}&lc={row.comment_id}" if row.video_id else "",
            })
            if len(out) >= limit:
                break
    return _result(out)
