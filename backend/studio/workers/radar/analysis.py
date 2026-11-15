"""Análise do radar de nichos: pura (sem rede nem banco), testável. Porte fiel de estudio/radar.py do canais.

Os agregados por nicho e mercado usam os nomes das colunas de `RadarNiche` (`new_share`, `new_hit`, `best_new`,
`subs_med`, `length_med`, `best`); o resto segue o canais (linhas com `canal`, `views`, `dur`; canais com `idade_m`,
`inscritos`, `alerta`).
"""

from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from collections.abc import Iterable, Iterator
from typing import Any

MERCADOS: dict[str, dict[str, str]] = {
    "us": {"nome": "EUA", "regiao": "US", "lingua": "en"},
    "br": {"nome": "BR", "regiao": "BR", "lingua": "pt"},
}

PT_PALAVRAS = re.compile(
    r"\b(de|do|da|dos|das|que|como|por|para|não|nao|uma|um|seu|sua|mais|quando|porque|você|voce|isso|foi|são|sao|está|esta)\b",
    re.I,
)
EN_PALAVRAS = re.compile(
    r"\b(the|of|and|how|why|what|is|are|to|in|was|were|you|your|this|that|with|for|from|who|did)\b", re.I
)
ACENTOS = re.compile("[ãõçâêôáéíóúà]", re.I)
SUL_ASIA = {
    "IN",
    "PK",
    "BD",
    "NP",
    "LK",
}  # público e RPM muito diferentes dos mercados-alvo: marcado e fora das contas
OUTRA_ESCRITA = re.compile("[ऀ-ॿ؀-ۿЀ-ӿ぀-ヿ一-鿿가-힯฀-๿֐-׿]")
CANDIDATO_MESES = 18  # canal candidato (vídeos recentes lidos): até 18 meses
ASSENTADO_DIAS = 7  # vídeo "assentado": 7+ dias no ar
DIAS_POR_MES = 30.44

RISCOS = {
    "Modelar": "Formato que se repete: estude a estrutura, não copie os vídeos.",
    "Observar": "Mais de um hit, mas ainda irregular: acompanhe por mais semanas.",
    "Estudar o viral": "Um hit isolado: entenda por que funcionou antes de apostar no formato.",
}


# ----------------------------------------------------------------------------- utilidades


def segundos_iso(d: str | None) -> int:
    """Duração ISO 8601 do YouTube (PT1H2M3S, P1DT2H) em segundos."""
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", d or "")
    if not m:
        return 0
    dias, h, mi, s = (int(x or 0) for x in m.groups())
    return dias * 86400 + h * 3600 + mi * 60 + s


def quando_iso(s: str) -> dt.datetime:
    when = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=dt.UTC)


def mediana(xs: Iterable[float | int | None]) -> float:
    vals = [x for x in xs if x is not None]
    return float(statistics.median(vals)) if vals else 0.0


def quantil(xs: Iterable[float | int | None], q: float) -> float:
    """Quantil com interpolação linear (como o canais)."""
    vals = sorted(x for x in xs if x is not None)
    if not vals:
        return 0.0
    k = (len(vals) - 1) * q
    a, b = math.floor(k), math.ceil(k)
    return float(vals[a] + (vals[b] - vals[a]) * (k - a))


def fmt(v: float | int | None) -> str:
    """Número curto em PT-BR: "1,2 mi", "350 mil", "999"."""
    if v is None:
        return "-"
    if v >= 1e6:
        return f"{v / 1e6:.1f} mi".replace(".", ",").replace(",0 ", " ")
    if v >= 1e3:
        return f"{v / 1e3:.0f} mil"
    return str(int(v))


def em_lotes(xs: list[Any], n: int = 50) -> Iterator[list[Any]]:
    for i in range(0, len(xs), n):
        yield xs[i : i + n]


def idioma_ok(snippet: dict[str, Any], mercado: str) -> bool:
    """O título bate com a língua do mercado? Usa o idioma declarado do vídeo e, sem ele, as palavras do título."""
    t = snippet.get("title", "") or ""
    if OUTRA_ESCRITA.search(t):
        return False
    lang = (snippet.get("defaultAudioLanguage") or snippet.get("defaultLanguage") or "").lower()
    pt = len(PT_PALAVRAS.findall(t)) + (2 if ACENTOS.search(t) else 0)
    en = len(EN_PALAVRAS.findall(t))
    if mercado == "br":
        return lang.startswith("pt") if lang else en <= pt
    return lang.startswith("en") if lang else (pt <= en and not ACENTOS.search(t))


def _idade(c: dict[str, Any]) -> float:
    """Idade do canal em meses; desconhecida conta 999."""
    return 999 if c.get("idade_m") is None else c["idade_m"]


# ----------------------------------------------------------------------------- análise


def alertas(cfg: dict[str, Any], linhas: list[dict[str, Any]], canais: dict[str, dict[str, Any]]) -> None:
    """Marca canais fora do alvo (ficam no painel, marcados, e fora das contas): país do sul da Ásia, ou formato a
    evitar pelo nome do canal ou por metade ou mais dos títulos dele na amostra (`[radar] alertas` do catálogo)."""
    regras = [(k.lower(), msg) for k, msg in cfg["radar"].get("alertas", [])]
    titulos: dict[str, list[str]] = {}
    for a in linhas:
        titulos.setdefault(a["canal"], []).append((a.get("titulo") or "").lower())
    for cid, c in canais.items():
        if c.get("pais") in SUL_ASIA:
            c["alerta"] = (
                f"Canal do sul da Ásia (país {c['pais']}): público e RPM diferentes dos mercados-alvo"
            )
            continue
        ts, nome = titulos.get(cid, []), (c.get("nome") or "").lower()
        c["alerta"] = next(
            (msg for k, msg in regras if k in nome or (ts and 2 * sum(k in t for t in ts) >= len(ts))), None
        )


def agregar(
    cfg: dict[str, Any],
    linhas: list[dict[str, Any]],
    canais: dict[str, dict[str, Any]],
    chaves: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Por nicho e mercado, só com vídeos de canais sem alerta: demanda, abertura para canal novo e concorrência."""
    r = cfg["radar"]
    novo_m = r.get("novo_meses", 12)
    grupos: dict[tuple[str, str], list[dict[str, Any]]] = {k: [] for k in chaves}
    for a in linhas:
        if not canais[a["canal"]].get("alerta"):
            grupos.setdefault((a["nicho"], a["mercado"]), []).append(a)
    nichos: dict[tuple[str, str], dict[str, Any]] = {}
    for (nid, mk), limpos in grupos.items():
        hit = r.get(f"hit_{mk}", 100000)
        vs = [a["views"] for a in limpos]
        novos = [a for a in limpos if _idade(canais[a["canal"]]) <= novo_m]
        canais_n = {a["canal"] for a in limpos}
        nichos[(nid, mk)] = {
            "n": len(limpos),
            "med": mediana(vs),
            "p75": quantil(vs, 0.75),
            "h100": sum(v >= hit for v in vs),
            "h500": sum(v >= 5 * hit for v in vs),
            "new_share": len(novos) / len(limpos) if limpos else 0.0,
            "new_hit": len({a["canal"] for a in novos if a["views"] >= hit}),
            "best_new": max((a["views"] for a in novos), default=0),
            "subs_med": mediana(canais[x].get("inscritos") for x in canais_n),
            "length_med": mediana(a["dur"] / 60 for a in limpos),
            "n10k": sum(v >= 10000 for v in vs),
            "best": max(vs, default=0),
            "hit": hit,
        }
    return nichos


def analisar(
    cfg: dict[str, Any],
    buscas: dict[tuple[str, str], list[str]],
    videos: dict[str, dict[str, Any]],
    canais: dict[str, dict[str, Any]],
    agora: dt.datetime,
) -> dict[str, Any]:
    """buscas: {(nicho, mercado): [ids]}; videos: {id: item de videos.list}; canais: {id: item de channels.list}.
    Devolve {"nichos": {(nicho, mercado): agregados}, "linhas": [vídeos aceitos], "canais": {id: resumo}}."""
    dias = cfg["radar"].get("dias", 30)
    resumo_c: dict[str, dict[str, Any]] = {}
    for cid, c in canais.items():
        sn, st = c.get("snippet", {}), c.get("statistics", {})
        criado = quando_iso(sn["publishedAt"]) if sn.get("publishedAt") else None
        resumo_c[cid] = {
            "id": cid,
            "nome": sn.get("title", cid),
            "pais": sn.get("country"),
            "criado": criado.date().isoformat() if criado else None,
            "idade_m": (agora - criado).days / DIAS_POR_MES if criado else None,
            "inscritos": int(st["subscriberCount"]) if st.get("subscriberCount") else None,
            "views": int(st.get("viewCount", 0) or 0),
            "videos": int(st.get("videoCount", 0) or 0),
            "url": f"https://www.youtube.com/{sn['customUrl']}"
            if sn.get("customUrl")
            else f"https://www.youtube.com/channel/{cid}",
        }
    linhas: list[dict[str, Any]] = []
    for (nid, mk), ids in buscas.items():
        for vid in dict.fromkeys(ids):
            v = videos.get(vid)
            if not v:
                continue
            sn, st, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
            pub = quando_iso(sn["publishedAt"]) if sn.get("publishedAt") else None
            dur = segundos_iso(cd.get("duration"))
            if (
                not pub
                or (agora - pub).days > dias
                or dur < 180
                or not idioma_ok(sn, mk)
                or sn.get("channelId") not in resumo_c
            ):
                continue
            linhas.append(
                {
                    "id": vid,
                    "nicho": nid,
                    "mercado": mk,
                    "titulo": sn.get("title", ""),
                    "canal": sn["channelId"],
                    "publicado": pub.isoformat(),
                    "views": int(st.get("viewCount", 0) or 0),
                    "dur": dur,
                    "idade_d": (agora - pub).days,
                }
            )
    alertas(cfg, linhas, resumo_c)
    return {"nichos": agregar(cfg, linhas, resumo_c, buscas.keys()), "linhas": linhas, "canais": resumo_c}


def candidatos(cfg: dict[str, Any], analise: dict[str, Any], limite: int) -> list[tuple[str, str]]:
    """Canais com até 18 meses e um vídeo acima do hit do mercado: [(canal, mercado)], o melhor vídeo primeiro."""
    melhor: dict[str, tuple[int, str]] = {}
    for a in analise["linhas"]:
        c = analise["canais"][a["canal"]]
        hit = cfg["radar"].get(f"hit_{a['mercado']}", 100000)
        if (
            not c.get("alerta")
            and _idade(c) <= CANDIDATO_MESES
            and a["views"] >= hit
            and a["views"] > melhor.get(a["canal"], (0, ""))[0]
        ):
            melhor[a["canal"]] = (a["views"], a["mercado"])
    ordem = sorted(melhor.items(), key=lambda x: -x[1][0])[: max(0, limite)]
    return [(cid, mk) for cid, (_, mk) in ordem]


def recentes(itens: list[dict[str, Any]], hit: int, agora: dt.datetime) -> dict[str, Any]:
    """Vídeos recentes de um canal → consistência: mediana, hits, uploads em 30 dias, duração típica, veredito."""
    lat: list[dict[str, Any]] = []
    for v in itens:
        sn, st, cd = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {})
        if not sn.get("publishedAt"):
            continue
        idade = (agora - quando_iso(sn["publishedAt"])).days
        lat.append(
            {
                "id": v["id"],
                "t": sn.get("title", ""),
                "v": int(st["viewCount"]) if st.get("viewCount") else None,
                "a": idade,
                "l": round(segundos_iso(cd.get("duration")) / 60, 1),
            }
        )
    lat.sort(key=lambda x: x["a"])
    assentados = [x for x in lat if x["a"] >= ASSENTADO_DIAS and x["v"] is not None]
    hits = sum(x["v"] >= hit for x in assentados)
    r: dict[str, Any] = {
        "latest": lat,
        "med": mediana(x["v"] for x in assentados),
        "hits": hits,
        "settled": len(assentados),
        "up30": sum(x["a"] <= 30 for x in lat),
        "typLen": mediana(x["l"] for x in lat if x["l"] >= 3),
    }
    if len(assentados) >= 4 and hits / len(assentados) >= 0.5:
        r["verdict"] = "Modelar"
    elif hits >= 2:
        r["verdict"] = "Observar"
    elif hits == 1:
        r["verdict"] = "Estudar o viral"
    if r.get("verdict"):
        r["format"] = (
            f"{round(r['typLen'] or 0)} min · {r['up30']} vídeos em 30 dias · "
            f"{hits}/{len(assentados)} com {fmt(hit)}+"
        )
        r["risk"] = RISCOS[r["verdict"]]
    return r


def leitura_arbitragem(demanda: float, oferta: int) -> str:
    if demanda >= 100000 and oferta <= 5:
        return "Aberto em PT"
    if oferta >= 15:
        return "Concorrido em PT"
    return "Morno"


def score_arbitragem(demanda: float, oferta: int) -> float:
    return round(math.log10(max(demanda, 1)) * (1 - min(oferta, 20) / 20) * 10, 1)


def arbitragem(nichos: dict[tuple[str, str], dict[str, Any]], cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """EN→PT: demanda no mercado dos EUA (mediana) contra a oferta recente em português (vídeos de 10 mil+).
    Maior score = mais espaço em PT."""
    linhas = []
    for n in cfg.get("nichos", []):
        us, br = nichos.get((n["id"], "us")), nichos.get((n["id"], "br"))
        if not us or not br:
            continue
        demanda, oferta = us["med"], br["n10k"]
        linhas.append(
            {
                "id": n["id"],
                "nome": n["nome"],
                "en": {"med": us["med"], "h100": us["h100"], "new_hit": us["new_hit"], "n": us["n"]},
                "pt": {
                    "med": br["med"],
                    "n10k": oferta,
                    "best": br["best"],
                    "new_share": br["new_share"],
                    "n": br["n"],
                },
                "leitura": leitura_arbitragem(demanda, oferta),
                "score": score_arbitragem(demanda, oferta),
            }
        )
    return sorted(linhas, key=lambda x: -x["score"])
