"""Resultados por variável, leitura de experimentos e sugestões (origem: estudio/melhoria.py do canais:
`resultados`, `avaliar`, `sugestoes`, `spearman`). Mesmos números do canais: idade mínima de 72 h (Short) e 168 h
(longo), 3+ vídeos por variante, vencedor com 1,3x, sugestões só com 8+ Shorts (1,5x e 0,5x da mediana geral).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from typing import Any

from studio.models import Experiment
from studio.workers.analytics.variables import SHORT_VARIABLES, VARIABLES, load_videos

IDADE_MIN_H = {"short": 72, "long": 168}  # a métrica só conta depois disso (views ainda sobem nos primeiros dias)
METRICS = ("views", "views_dia", "ctr", "retencao", "avd_s", "likes")
METRIC_LABELS = {
    "views": "views",
    "views_dia": "views por dia",
    "ctr": "CTR",
    "retencao": "retenção",
    "avd_s": "duração média vista",
    "likes": "likes",
}
MIN_POR_VARIANTE = 3
VANTAGEM = 1.3
MIN_SHORTS_SUGESTAO = 8
MAX_SUGESTOES = 4


def mediana(xs: Iterable[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return float(statistics.median(vals)) if vals else None


def vezes(r: float) -> str:
    return f"{r:.1f}x".replace(".", ",")


def fmt(v: float | None) -> str:
    if v is None:
        return "-"
    if v >= 1e6:
        return f"{v / 1e6:.1f} mi".replace(".", ",")
    if v >= 1e3:
        return f"{v / 1e3:.1f} mil".replace(".", ",").replace(",0 mil", " mil")
    return str(int(round(v)))


def _num(v: float) -> str:
    return str(v).replace(".", ",")


def spearman(xs: list[float], ys: list[float]) -> float | None:
    """Correlação de postos (sem bibliotecas). None com menos de 5 pares."""
    if len(xs) < 5:
        return None

    def postos(v: list[float]) -> list[float]:
        ordem = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(ordem):
            j = i
            while j + 1 < len(ordem) and v[ordem[j + 1]] == v[ordem[i]]:
                j += 1
            for k in range(i, j + 1):
                r[ordem[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = postos(xs), postos(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return round(num / den, 2) if den else None


def _age(v: dict[str, Any]) -> float:
    return (v.get("metric") or {}).get("age_hours") or 0


# --------------------------------------------------------------------------------------------
# Resultados por variável
# --------------------------------------------------------------------------------------------


def results(channel_id: str | None = None, videos: list[dict[str, Any]] | None = None,
            cut: float | None = None) -> dict[str, Any]:
    """Shorts com 72 h ou mais agrupados por variável, mediana geral, calibração da nota e vídeos longos.

    `cut` é a nota de corte das ideias, quando houver: se todos os Shorts ficaram acima dele, a calibração não diz nada
    sobre o corte (só sobre os critérios).
    """
    videos = load_videos(channel_id) if videos is None else videos
    shorts: list[dict[str, Any]] = []
    longs: list[dict[str, Any]] = []
    for v in videos:
        m = v.get("metric")
        if not m or m.get("views") is None:
            continue
        if v.get("format") == "short":
            if _age(v) >= IDADE_MIN_H["short"]:
                shorts.append(v)
        else:
            longs.append(v)
    geral = mediana(s["metric"]["views"] for s in shorts)
    por_var = []
    for var in SHORT_VARIABLES:
        grupos: dict[str, list[float]] = {}
        for s in shorts:
            value = s["variables"].get(var)
            if value is not None:
                grupos.setdefault(str(value), []).append(s["metric"]["views"])
        if len(grupos) < 2:
            continue
        groups = sorted(
            ({"value": k, "n": len(vs), "median": mediana(vs)} for k, vs in grupos.items()),
            key=lambda g: -(g["median"] or 0),
        )
        por_var.append({"variable": var, "label": VARIABLES.get(var, var), "groups": groups})
    pares = [(s["score"], s["metric"]["views"]) for s in shorts if s.get("score") is not None]
    notas = [a for a, _ in pares]
    calib = {
        "n": len(pares),
        "rho": spearman(notas, [b for _, b in pares]),
        "min": min(notas) if notas else None,
        "max": max(notas) if notas else None,
        "cut": cut,
    }
    return {
        "shorts_n": len(shorts),
        "overall_median": geral,
        "variables": por_var,
        "calibration": calib,
        "longs": [
            {"youtube_id": x["youtube_id"], "title": x.get("title"), "views": x["metric"]["views"],
             "days": round(_age(x) / 24)}
            for x in longs
        ],
        "min_age_h": IDADE_MIN_H["short"],
    }


# --------------------------------------------------------------------------------------------
# Experimentos
# --------------------------------------------------------------------------------------------


def experiment_dict(row: Experiment) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "hypothesis": row.hypothesis,
        "variable": row.variable,
        "variants": list(row.variants or []),
        "metric": row.metric,
        "project_ids": list(row.project_ids or []),
        "status": row.status,
        "result": row.result,
        "learning": row.learning,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }


def _metric_value(metric: dict[str, Any], name: str) -> float | None:
    if name == "views_dia":
        return (metric.get("views") or 0) / max((metric.get("age_hours") or 24) / 24, 1)
    key = {"retencao": "pct_watched", "avd_s": "avg_view_s"}.get(name, name)
    return metric.get(key)


def evaluate(exp: Experiment | dict[str, Any], videos: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Leitura de um experimento (regra de `avaliar`): mediana da métrica por variante, só com vídeos na idade mínima.

    Vencedor só quando todas as variantes têm 3+ vídeos e a 1ª tem 1,3x ou mais a mediana da 2ª.
    """
    e = experiment_dict(exp) if isinstance(exp, Experiment) else exp
    var = e.get("variable") or ""
    metric = e.get("metric") or "views"
    variants = [str(x) for x in e.get("variants") or []]
    project_ids = set(e.get("project_ids") or [])
    if videos is None:
        videos = load_videos(None if project_ids else e.get("channel_id"))
    if project_ids:
        videos = [v for v in videos if v.get("project_id") in project_ids]
    grupos: dict[str, list[float]] = {x: [] for x in variants}
    jovens = 0
    for v in videos:
        value = v["variables"].get(var)
        if value is None or (variants and str(value) not in variants):
            continue
        m = v.get("metric")
        minimo = IDADE_MIN_H.get(v.get("format") or "long", IDADE_MIN_H["long"])
        if not m or (m.get("age_hours") or 0) < minimo:
            jovens += 1
            continue
        x = _metric_value(m, metric)
        if x is not None:
            grupos.setdefault(str(value), []).append(x)
    tab = sorted(
        ({"variant": k, "n": len(vs), "median": mediana(vs)} for k, vs in grupos.items()),
        key=lambda g: -(g["median"] or -1),
    )
    prontos = [g for g in tab if g["n"] >= MIN_POR_VARIANTE]
    winner = None
    if len(prontos) >= 2 and len(prontos) == len(tab) and prontos[1]["median"]:
        r = prontos[0]["median"] / prontos[1]["median"]
        if r >= VANTAGEM:
            winner = prontos[0]["variant"]
            text = f"{winner} na frente: {vezes(r)} a mediana de {prontos[1]['variant']}"
        else:
            text = "sem diferença clara (menos de 1,3x)"
    else:
        falta = ", ".join(f"{g['variant']} ({g['n']})" for g in tab if g["n"] < MIN_POR_VARIANTE) or "variantes"
        text = f"cedo: precisa de 3+ vídeos por variante na idade mínima (faltam: {falta})"
    return {"text": text, "winner": winner, "young": jovens, "variants": tab, "variable": var, "metric": metric}


# --------------------------------------------------------------------------------------------
# Sugestões
# --------------------------------------------------------------------------------------------


def suggestions(res: dict[str, Any], open_experiments: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Até 4 sugestões a partir dos resultados (regra de `sugestoes`, sem a parte de velocidade, que é do bloco C)."""
    acompanha = {e.get("variable"): e.get("id") for e in open_experiments or []}

    def teste(var: str) -> str:
        if var in acompanha:
            return f"O experimento {acompanha[var]} já acompanha."
        return f"Vale abrir um experimento sobre {VARIABLES.get(var, var).lower()} ({var}) com a métrica views."

    out: list[dict[str, Any]] = []
    g = res.get("overall_median")
    if g and res.get("shorts_n", 0) >= MIN_SHORTS_SUGESTAO:
        cands = [(v["variable"], gr) for v in res["variables"] for gr in v["groups"]
                 if gr["n"] >= 3 and gr["median"] and gr["median"] >= 1.5 * g]
        if cands:
            var, gr = max(cands, key=lambda x: x[1]["median"])
            out.append({
                "kind": "Resultado", "variable": var, "experiment_id": acompanha.get(var),
                "text": f"Resultado: Shorts com {var} = {gr['value']} têm {vezes(gr['median'] / g)} a mediana de views "
                        f"(n={gr['n']}). {teste(var)}",
            })
        ruins = [(v["variable"], gr) for v in res["variables"] for gr in v["groups"]
                 if gr["n"] >= 3 and gr["median"] is not None and gr["median"] <= 0.5 * g]
        if ruins:
            var, gr = min(ruins, key=lambda x: x[1]["median"])
            out.append({
                "kind": "Alerta", "variable": var, "experiment_id": acompanha.get(var),
                "text": f"Alerta: Shorts com {var} = {gr['value']} ficaram em {fmt(gr['median'])} views de mediana, "
                        f"contra {fmt(g)} no geral (n={gr['n']}). Evite repetir até ter mais dados. {teste(var)}",
            })
    c = res.get("calibration") or {}
    if c.get("rho") is not None and c.get("n", 0) >= 8 and abs(c["rho"]) < 0.2:
        rho = _num(c["rho"])
        if c.get("cut") is not None and c.get("min") is not None and c["min"] >= c["cut"]:
            # Só saiu o que passou do corte: a correlação não diz nada sobre o corte, só sobre os critérios.
            text = (f"A nota da ideia quase não prevê as views (correlação {rho}, n={c['n']}), mas todas ficaram entre "
                    f"{_num(c['min'])} e {_num(c['max'])}, acima do corte {_num(c['cut'])}: o corte não dá para avaliar; "
                    "compare os critérios dos Shorts que ficaram muito abaixo da mediana.")
        else:
            text = (f"A nota da ideia quase não prevê as views (correlação {rho}, n={c['n']}): "
                    "revise os critérios ou o corte.")
        out.append({"kind": "Calibração", "variable": "faixa_nota", "experiment_id": None, "text": text})
    return out[:MAX_SUGESTOES]
