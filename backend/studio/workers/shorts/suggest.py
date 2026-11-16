"""Sugestões de cortes de Short a partir do longo: 3 a 5 trechos contínuos de 15 a 30 s.

Com um LLM disponível, ele escolhe os trechos (pela cadeia de provedores, como `pipeline/authoring.py`). No modo
offline, ou se o LLM falhar ou não devolver trecho que preste, uma heurística escolhe por duração e começo de capítulo.
Cada sugestão leva os avisos de `check_short`.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.providers import registry
from studio.workers.shorts.derive import segments_overview, short_duration
from studio.workers.shorts.rules import MAX_S, TIPOS, ShortLimits, ShortTooLong, channel_rules, check_short

log = logging.getLogger(__name__)

MIN_SUGGESTIONS = 3
MAX_SUGGESTIONS = 5
SWEET_SPOT_S = 22.0

SUGGEST_SYSTEM = """Você corta Shorts (vídeos verticais de 15 a 30 s) de um vídeo longo do YouTube já narrado.
Cada Short é um trecho CONTÍNUO de segmentos do roteiro (do primeiro ao último, sem pular nenhum), que se entende sozinho,
abre com uma frase forte e fecha com uma ideia completa. Prefira trechos que começam num capítulo ou numa revelação.
Título: até 50 caracteres, sem citar a fonte ("segundo a BBC") e sem gancho gasto ("URGENTE", "só 1% sabe").
Gancho: frase curta (até 8 palavras) que aparece na tela no primeiro segundo.
Tipo: "lista" ou "iceberg" só quando o trecho é uma lista ou um iceberg; senão "normal"."""

SUGGEST_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "shorts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "first": {"type": "integer"},
                    "last": {"type": "integer"},
                    "title": {"type": "string"},
                    "hook": {"type": "string"},
                    "tipo": {"type": "string", "enum": list(TIPOS)},
                    "reason": {"type": "string"},
                },
                "required": ["first", "last", "title", "hook", "tipo"],
            },
        }
    },
    "required": ["shorts"],
}


def _clip(text: str, limit: int) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-–— ")
    return cut or text[:limit]


def _first_sentence(text: str) -> str:
    parts = re.split(r"(?<=[.!?…])\s+", " ".join((text or "").split()), maxsplit=1)
    return parts[0] if parts else ""


def _item(overview: dict[str, Any], a: int, b: int, title: str, hook: str, tipo: str, reason: str,
          limits: ShortLimits) -> dict[str, Any] | None:
    segs = overview["segments"]
    ids = [s["id"] for s in segs[a : b + 1]]
    if any(segs[i]["duration_s"] is None for i in range(a, b + 1)):
        return None
    duration = short_duration(overview, ids)
    tipo = tipo if tipo in TIPOS else "normal"
    title = _clip(title, 100)
    if not title:
        return None
    try:
        warnings = check_short(title, duration, tipo, limits)
    except ShortTooLong:
        return None
    return {
        "ranges": [[ids[0], ids[-1]]],
        "segment_ids": ids,
        "title": title,
        "hook": _clip(hook, 80),
        "tipo": tipo,
        "duration_s": duration,
        "reason": _clip(reason, 200),
        "warnings": warnings,
    }


def heuristic(overview: dict[str, Any], limits: ShortLimits) -> list[dict[str, Any]]:
    """Trechos de 15 a 30 s (alvo do canal), preferindo começo de capítulo e duração perto de 22 s, sem sobreposição.
    Com menos de 3 no alvo, aceita até o teto (35 s). Se nenhum trecho cabe (segmentos longos demais), propõe os
    segmentos sozinhos mais curtos, com os avisos de duração."""
    segs = overview["segments"]
    low, high = limits.target

    def windows(top: float, floor: float = low, single: bool = False) -> list[tuple[float, int, int, float]]:
        out = []
        for a in range(len(segs)):
            for b in range(a, a + 1 if single else len(segs)):
                if any(segs[i]["duration_s"] is None for i in range(a, b + 1)):
                    break
                dur = short_duration(overview, [s["id"] for s in segs[a : b + 1]])
                if dur > top:
                    break
                if dur < floor:
                    continue
                score = abs(dur - SWEET_SPOT_S) / 10
                if segs[a]["chapter_start"]:
                    score -= 1.0
                if segs[a]["role"] == "hook" or a == 0:
                    score -= 0.5
                if segs[b]["role"] == "cta":
                    score += 1.5  # chamada para se inscrever não fecha Short
                out.append((score, a, b, dur))
        return sorted(out)

    picked: list[tuple[int, int]] = []
    for top in (high, limits.max_s):
        for _score, a, b, _dur in windows(top):
            if len(picked) >= MAX_SUGGESTIONS:
                break
            if any(not (b < pa or a > pb) for pa, pb in picked):
                continue
            picked.append((a, b))
        if len(picked) >= MIN_SUGGESTIONS:
            break
    if not picked:
        for _score, a, b, _dur in windows(MAX_S, 0.0, single=True)[:MIN_SUGGESTIONS]:
            picked.append((a, b))
    items = []
    for a, b in sorted(picked):
        first = segs[a]
        title = first["chapter"] or _first_sentence(first["text"])
        dur = short_duration(overview, [s["id"] for s in segs[a : b + 1]])
        if not low <= dur <= limits.max_s:
            # Reserva: nenhum trecho coube no alvo. Não dizer que a duração é boa (os avisos dizem o que fazer).
            reason = "Nenhum trecho cabe no alvo de duração: este é um dos mais curtos (ajuste o corte)"
        elif first["chapter_start"]:
            reason = "Começa num capítulo"
        elif dur <= high:
            reason = "Trecho com duração boa para Short"
        else:
            reason = "Trecho até o teto de duração"
        item = _item(overview, a, b, _clip(title, limits.title_max), _first_sentence(first["text"]), "normal",
                     reason, limits)
        if item:
            items.append(item)
    return items


def _llm_suggestions(project_id: str, overview: dict[str, Any], limits: ShortLimits, title: str,
                     job_id: str | None) -> tuple[list[dict[str, Any]], str] | None:
    lines = [
        f"{i + 1}. ({s['duration_s']:.1f} s{', capítulo: ' + s['chapter'] if s['chapter_start'] and s['chapter'] else ''}) "
        f"{_clip(s['text'], 400)}"
        for i, s in enumerate(overview["segments"])
        if s["duration_s"] is not None
    ]
    prompt = (
        f"Vídeo longo: {title}\n"
        f"Cada Short soma as durações dos segmentos + {overview['lead_in_s'] + overview['tail_out_s']:.1f} s de abertura "
        f"e fim + {overview['gap_s']:.2f} s entre segmentos. Alvo: {limits.target[0]:g} a {limits.target[1]:g} s "
        f"(teto {limits.max_s:g} s; lista e iceberg até {limits.max_list_s:g} s).\n"
        f"Proponha de {MIN_SUGGESTIONS} a {MAX_SUGGESTIONS} Shorts sem sobreposição, pelo número dos segmentos "
        "(first e last, base 1).\n\nSegmentos:\n" + "\n".join(lines)
    )
    chain = registry.resolve_chain("llm", None)

    def call(provider):
        return provider.complete_json(task="shorts_suggest", system=SUGGEST_SYSTEM, prompt=prompt,
                                      schema=SUGGEST_SCHEMA, context={})

    try:
        result, provider, _ = registry.run_chain(
            "llm", chain, call,
            usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
            project_id=project_id, job_id=job_id,
        )
    except Exception as exc:  # noqa: BLE001 - sem LLM, a heurística assume
        log.warning("sugestão de Shorts pelo LLM falhou (%s); usando a heurística", exc)
        return None
    raw = (result.data or {}).get("shorts") if isinstance(result.data, dict) else None
    items: list[dict[str, Any]] = []
    used: list[tuple[int, int]] = []
    n = len(overview["segments"])
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        try:
            a, b = int(entry.get("first", 0)) - 1, int(entry.get("last", 0)) - 1
        except (TypeError, ValueError):
            continue
        if not (0 <= a <= b < n) or any(not (b < pa or a > pb) for pa, pb in used):
            continue
        item = _item(overview, a, b, str(entry.get("title") or ""), str(entry.get("hook") or ""),
                     str(entry.get("tipo") or "normal"), str(entry.get("reason") or ""), limits)
        if item:
            used.append((a, b))
            items.append(item)
        if len(items) >= MAX_SUGGESTIONS:
            break
    return (items, provider.info.id) if items else None


def suggest(project_id: str, lang: str | None = None, job_id: str | None = None) -> dict[str, Any]:
    """Gera as sugestões e guarda em `Project.settings["shorts_suggestions"]` do longo."""
    overview = segments_overview(project_id, lang)
    row = service.get_project_row(project_id)
    limits, _hashtag = channel_rules(row.get("channel_id"))
    found = None if get_settings().offline else _llm_suggestions(project_id, overview, limits, row["title"], job_id)
    if found:
        items, provider = found
    else:
        items, provider = heuristic(overview, limits), "heuristica"
    data = {
        "lang": overview["lang"],
        "items": items,
        "provider": provider,
        "created_at": datetime.now(UTC).isoformat(),
    }
    service.update_settings(project_id, {"shorts_suggestions": data})
    return data
