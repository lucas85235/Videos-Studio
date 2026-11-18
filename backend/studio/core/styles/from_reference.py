"""Estilo a partir de um vídeo de referência: as medidas (ritmo, paleta, proporção) viram um StyleSpec.

Parte do preset mais próximo das medidas e troca só o que elas dizem: ritmo de corte → duração das cenas e
transições, paleta → gradação e cores dos cartões, proporção → formato. Se houver LLM, ele completa o resto
(arte, câmera, voz) com as medidas como restrições: mudança do LLM num campo medido é descartada. Sem LLM, fica
só medidas + preset. Cada valor trocado sai com a origem ("de onde veio"), para a tela mostrar.
"""

from __future__ import annotations

import colorsys
import copy
import json
import math
import re
import unicodedata
from collections.abc import Callable
from statistics import median
from typing import Any

from studio.core.media_title import display_title
from studio.core.styles.library import load_presets, set_path
from studio.core.styles.schema import StyleSpec

# (proporção, largura/altura, resolução)
ASPECTS: tuple[tuple[str, float, str], ...] = (
    ("9:16", 9 / 16, "1080x1920"),
    ("4:5", 4 / 5, "1080x1350"),
    ("1:1", 1.0, "1080x1080"),
    ("16:9", 16 / 9, "1920x1080"),
)
FPS_CHOICES = (24, 25, 30, 50, 60)
SHOT_MIN_S, SHOT_MAX_S = 0.8, 15.0
FAST_MEDIAN_S, SLOW_MEDIAN_S = 1.8, 4.0  # plano mediano abaixo: ritmo rápido; acima: lento
# Parte dos cortes secos por ritmo (o resto fica com as transições do preset) e a duração das transições.
PACE = {
    "rápido": {"cut_share": 0.85, "transition_s": 0.2},
    "médio": {"cut_share": 0.7, "transition_s": 0.35},
    "lento": {"cut_share": 0.5, "transition_s": 0.6},
}
GRADE_AMOUNT = 0.6
TEXT_HEAVY, TEXT_LIGHT = 0.4, 0.1  # fração de quadros com texto (quando medida)

SOURCE_LABELS = {
    "ritmo": "Ritmo dos cortes",
    "paleta": "Paleta",
    "proporcao": "Proporção",
    "texto": "Texto na tela",
    "preset": "Preset mais próximo",
    "llm": "IA (completou o que as medidas não dizem)",
}

# Funções de LLM: (sistema, prompt, contexto) -> (resposta {"summary", "changes"}, id do provedor).
LLMCall = Callable[[str, str, dict[str, Any]], tuple[dict[str, Any], str]]


# --------------------------------------------------------------------------------------------
# Cores
# --------------------------------------------------------------------------------------------


def _rgb(hex_color: str) -> tuple[float, float, float] | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(hex_color or "").strip())
    if not m:
        return None
    v = m.group(1)
    return tuple(int(v[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _lum(c: tuple[float, float, float]) -> float:
    return 0.299 * c[0] + 0.587 * c[1] + 0.114 * c[2]


def _sat(c: tuple[float, float, float]) -> float:
    return colorsys.rgb_to_hsv(*c)[1]


def palette_traits(palette: list[str]) -> dict[str, float] | None:
    """Brilho, saturação e "temperatura" médios da paleta (a 1ª cor é a que mais aparece e pesa mais)."""
    cols = [c for c in (_rgb(x) for x in palette) if c]
    if not cols:
        return None
    weights = [len(cols) - i for i in range(len(cols))]
    total = sum(weights)

    def avg(fn: Callable[[tuple[float, float, float]], float]) -> float:
        return sum(fn(c) * w for c, w in zip(cols, weights, strict=True)) / total

    return {
        "lum": round(avg(_lum), 3),
        "sat": round(avg(_sat), 3),
        "warm": round(avg(lambda c: c[0] - c[2]), 3),  # vermelho menos azul
        "green": round(avg(lambda c: c[1] - (c[0] + c[2]) / 2), 3),
        "red": round(avg(lambda c: c[0] - (c[1] + c[2]) / 2), 3),
    }


def grade_for(traits: dict[str, float]) -> tuple[str, str]:
    """Gradação do StyleSpec mais parecida com a paleta, e o porquê em palavras simples."""
    lum, sat, warm = traits["lum"], traits["sat"], traits["warm"]
    if sat < 0.08:
        return ("noir", "quase sem cor") if lum < 0.45 else ("bleach-bypass", "cores lavadas, quase sem saturação")
    if lum < 0.3:
        if traits["red"] > 0.12:
            return "blood-red", "escura, puxada para o vermelho"
        if traits["green"] > 0.06:
            return "night-green", "escura, puxada para o verde"
        if warm > 0.06:
            return "warm-sepia", "escura e quente"
        return "cold-teal-dark", "escura e fria"
    if sat > 0.55:
        return "vivid", "cores fortes e saturadas"
    if warm > 0.08:
        return ("warm-sepia", "quente e pouco saturada") if sat < 0.35 else ("warm-soft", "quente")
    if warm < -0.05:
        return "clean-cool", "clara e fria"
    return "neutral", "equilibrada"


# --------------------------------------------------------------------------------------------
# Medidas → valores
# --------------------------------------------------------------------------------------------


def aspect_for(w: int, h: int) -> tuple[str, str] | None:
    if w <= 0 or h <= 0:
        return None
    ratio = w / h
    name, _, res = min(ASPECTS, key=lambda a: abs(math.log(ratio / a[1])))
    return name, res


def pace_of(median_s: float) -> str:
    if median_s < FAST_MEDIAN_S:
        return "rápido"
    if median_s > SLOW_MEDIAN_S:
        return "lento"
    return "médio"


def _quantile(values: list[float], q: float) -> float:
    vals = sorted(values)
    pos = (len(vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return vals[lo] + (vals[hi] - vals[lo]) * (pos - lo)


def shot_range(stats: dict[str, Any], shots: list[dict[str, Any]] | None) -> tuple[float, float] | None:
    """Faixa da duração das cenas: do 1º ao 3º quartil dos planos (ou 0,75x a 1,35x o mediano)."""
    durs = [float(s.get("dur") or 0) for s in shots or [] if float(s.get("dur") or 0) > 0]
    med = float(stats.get("median_s") or (median(durs) if durs else 0))
    if med <= 0:
        return None
    if len(durs) >= 4:
        lo, hi = _quantile(durs, 0.25), _quantile(durs, 0.75)
    else:
        lo, hi = med * 0.75, med * 1.35
    lo = min(max(SHOT_MIN_S, round(lo, 1)), SHOT_MAX_S)
    hi = min(max(lo + 0.4, round(hi, 1)), SHOT_MAX_S)
    return round(lo, 1), round(hi, 1)


def transitions_for(base: dict[str, float], pace: str) -> dict[str, float]:
    """Mantém as transições do preset e acerta a parte dos cortes secos ao ritmo; lento ganha fade_black."""
    share = PACE[pace]["cut_share"]
    others = {k: v for k, v in base.items() if k != "cut" and v > 0}
    if pace == "lento" and "fade_black" not in others:
        others["fade_black"] = 1.0
    if not others:
        return {"cut": 1.0}
    rest = sum(others.values())
    cut = round(rest * share / (1 - share), 1)
    return {"cut": cut, **{k: round(v, 1) for k, v in others.items()}}


def _fps(value: Any) -> int | None:
    try:
        fps = float(value or 0)
    except (TypeError, ValueError):
        return None
    if fps < 12:
        return None
    return min(FPS_CHOICES, key=lambda c: abs(c - fps))


def _grade_of(style: StyleSpec) -> str | None:
    return next((e.preset for e in style.edit.effects if e.type == "grade"), None)


def nearest_preset(info: dict[str, Any], stats: dict[str, Any], presets: dict[str, StyleSpec]) -> tuple[str, str]:
    """O preset mais perto das medidas: mesma proporção, ritmo parecido e a mesma gradação. Devolve id e motivo."""
    if not presets:
        raise ValueError("nenhum preset de estilo encontrado")
    asp = aspect_for(int(info.get("w") or 0), int(info.get("h") or 0))
    med = float(stats.get("median_s") or 0)
    traits = palette_traits(stats.get("palette") or [])
    grade = grade_for(traits)[0] if traits else None

    def score(style: StyleSpec) -> float:
        s = 0.0
        if asp:
            vertical = asp[0] in ("9:16", "4:5", "1:1")
            if style.format.aspect != asp[0] and (style.format.aspect == "9:16") != vertical:
                s += 10
        if med > 0:
            mid = (style.edit.shot_seconds.min + style.edit.shot_seconds.max) / 2
            s += abs(math.log(mid / med)) * 2
        if grade and _grade_of(style) == grade:
            s -= 0.6
        return s

    best = min(presets.values(), key=score)  # empate: a ordem dos presets decide
    why = []
    if asp and best.format.aspect == asp[0]:
        why.append(f"mesma proporção ({asp[0]})")
    if med > 0:
        why.append(f"cenas de {best.edit.shot_seconds.min:g} a {best.edit.shot_seconds.max:g} s, perto do plano "
                   f"mediano de {med:g} s")
    if grade and _grade_of(best) == grade:
        why.append(f"mesma gradação ({grade})")
    return best.id, ", ".join(why) or "o mais próximo"


def measured_changes(info: dict[str, Any], stats: dict[str, Any], shots: list[dict[str, Any]] | None,
                     base: StyleSpec) -> list[dict[str, Any]]:
    """O que as medidas dizem, como trocas {"path", "value", "source", "detail"} sobre o preset base."""
    out: list[dict[str, Any]] = []

    def put(path: str, value: Any, source: str, detail: str) -> None:
        out.append({"path": path, "value": value, "source": source, "detail": detail})

    w, h = int(info.get("w") or 0), int(info.get("h") or 0)
    asp = aspect_for(w, h)
    if asp:
        name, res = asp
        put("format.aspect", name, "proporcao", f"vídeo de {w}x{h}")
        put("format.resolution", res, "proporcao", f"resolução padrão para {name}")
        kind = "long" if name == "16:9" else "short"
        # Sempre sai (mesmo igual ao preset): assim o formato fica travado para a IA.
        put("format.type", kind, "proporcao", "horizontal vira vídeo longo" if kind == "long"
            else "vertical ou quadrado vira Short")
        if kind != base.format.type:
            put("format.duration", {"target_s": 600, "min_s": 300, "max_s": 1200} if kind == "long"
                else {"target_s": 55, "min_s": 40, "max_s": 59}, "proporcao", "duração padrão do formato")
    fps = _fps(info.get("fps"))
    if fps:
        raw = info.get("fps")
        near = "" if float(raw) == fps else f" (o mais perto que o estilo usa: {fps})"
        put("format.fps", fps, "proporcao", f"{raw:g} quadros por segundo no vídeo{near}"
            if isinstance(raw, (int, float)) else f"{raw} quadros por segundo no vídeo{near}")

    rng = shot_range(stats, shots)
    if rng:
        med = float(stats.get("median_s") or 0) or (rng[0] + rng[1]) / 2
        pace = pace_of(med)
        cpm = stats.get("cuts_per_min")
        cpm_txt = f", {cpm} cortes por minuto" if cpm else ""
        put("edit.shot_seconds", {"min": rng[0], "max": rng[1]}, "ritmo",
            f"plano mediano de {med:g} s{cpm_txt} (faixa do 1º ao 3º quartil dos planos)")
        trans = transitions_for(dict(base.edit.transitions), pace)
        cut_pct = round(trans.get("cut", 0) / (sum(trans.values()) or 1) * 100)
        put("edit.transitions", trans, "ritmo", f"ritmo {pace}: {cut_pct}% de cortes secos")
        put("edit.transition_seconds", PACE[pace]["transition_s"], "ritmo", f"transições de ritmo {pace}")

    palette = [p for p in stats.get("palette") or [] if _rgb(p)]
    traits = palette_traits(palette)
    if traits:
        grade, why = grade_for(traits)
        effects = [e.model_dump(mode="json") for e in base.edit.effects if e.type != "grade"]
        effects.append({"type": "grade", "amount": GRADE_AMOUNT, "preset": grade, "params": {}})
        put("edit.effects", effects, "paleta", f"gradação {grade}: paleta {why}")
        cols = [(p, _rgb(p)) for p in palette]
        darkest = min(cols, key=lambda pc: _lum(pc[1]))  # type: ignore[arg-type]
        if _lum(darkest[1]) < 0.35:  # type: ignore[arg-type]
            put("edit.text_cards.background", darkest[0].upper(), "paleta", "a cor mais escura da paleta")
        vivid = [pc for pc in cols if _sat(pc[1]) > 0.35 and _lum(pc[1]) > 0.3]  # type: ignore[arg-type]
        if vivid:
            accent = max(vivid, key=lambda pc: _sat(pc[1]))[0].upper()  # type: ignore[arg-type]
            paths = ["edit.text_cards.accent", "edit.captions.highlight", "thumbnail.accent"]
            if base.edit.progress_bar:  # sem barra de progresso, a cor dela não aparece no vídeo
                paths.insert(2, "edit.progress_color")
            for path in paths:
                put(path, accent, "paleta", "a cor mais viva da paleta")

    text_ratio = stats.get("text_ratio")
    if isinstance(text_ratio, (int, float)):
        pct = round(float(text_ratio) * 100)
        if text_ratio >= TEXT_HEAVY:
            put("edit.captions.style", "karaoke", "texto", f"texto em {pct}% dos quadros: legenda palavra a palavra")
        elif text_ratio <= TEXT_LIGHT:
            put("edit.captions.style", "line", "texto", f"texto em só {pct}% dos quadros: legenda discreta")
    return out


# --------------------------------------------------------------------------------------------
# LLM (opcional)
# --------------------------------------------------------------------------------------------


def _locked(path: str, locked: set[str]) -> bool:
    """Campo medido (ou que contém/está dentro de um campo medido): o LLM não mexe."""
    if path in {"id", "name", "description", "format", "edit", ""}:
        return True
    return any(path == p or path.startswith(p + ".") or p.startswith(path + ".") for p in locked)


def default_llm(system: str, prompt: str, context: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Cadeia de LLM do app (tarefa "style", a mesma do estilo por texto)."""
    from studio.providers import registry
    from studio.workers.pipeline.schemas import STYLE_PATCH_SCHEMA

    chain = registry.resolve_chain("llm", None)
    result, provider, _ = registry.run_chain(
        "llm", chain,
        lambda p: p.complete_json(task="style", system=system, prompt=prompt, schema=STYLE_PATCH_SCHEMA,
                                  context=context),
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
    )
    return result.data, provider.info.id


def _llm_system() -> str:
    from studio.workers.pipeline.schemas import STYLE_FIELDS_HELP

    return ("Você completa um estilo de vídeo (StyleSpec) criado a partir das medidas de um vídeo de referência. "
            "As medidas são restrições: NÃO mude os campos listados como medidos (ritmo, transições, cores, "
            "formato; a gradação de cor em edit.effects também é medida e volta sempre). Proponha só o que as "
            "medidas não dizem (arte, câmera, efeitos de textura, trilha, voz), "
            "coerente com elas, como pares path/value_json (JSON válido). Poucas mudanças; mantenha o resto.\n"
            + STYLE_FIELDS_HELP)


def _llm_prompt(title: str, info: dict[str, Any], stats: dict[str, Any], data: dict[str, Any],
                locked: list[str]) -> str:
    measures = {"titulo": title, "video": info, "medidas": {k: stats.get(k) for k in
                ("shots", "median_s", "mean_s", "cuts_per_min", "palette", "text_ratio") if k in stats}}
    current = {k: data[k] for k in ("family", "format", "art", "edit", "audio")}
    return (f"Medidas do vídeo de referência:\n{json.dumps(measures, ensure_ascii=False)}\n\n"
            f"Campos já definidos pelas medidas (não mude): {', '.join(locked)}\n\n"
            f"Estilo atual (preset base + medidas):\n{json.dumps(current, ensure_ascii=False)[:6000]}")


# --------------------------------------------------------------------------------------------
# Montagem
# --------------------------------------------------------------------------------------------


def slugify(text: str) -> str:
    norm = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", norm)).strip("-")[:50].strip("-")


# Mesmo limite do campo Nome no editor de estilos: um nome automático maior só poderia ser apagado, não editado.
NAME_MAX = 120


def reference_title(reference: dict[str, Any]) -> str:
    """Título limpo da referência (as antigas guardaram o nome do arquivo do downloader)."""
    return display_title(str(reference.get("title") or "")) or "Referência"


REF_SUFFIX = " (referência)"


def default_style_name(title: str) -> str:
    """Nome padrão "<título> (referência)" em até NAME_MAX caracteres: corta o título, nunca o sufixo."""
    return f"{title[: NAME_MAX - len(REF_SUFFIX)].rstrip()}{REF_SUFFIX}"


def unique_id(base: str, taken: set[str]) -> str:
    candidate = base
    n = 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def build_style(reference: dict[str, Any], *, presets: dict[str, StyleSpec] | None = None,
                llm: LLMCall | None = None, use_llm: bool = True, new_id: str | None = None,
                name: str | None = None) -> dict[str, Any]:
    """StyleSpec a partir de uma referência analisada (`reference_dict`). Não salva.

    Devolve {"style", "base_style_id", "base_reason", "origins", "llm": {"used", "provider", "summary",
    "message"}, "rejected"}.
    """
    info = dict(reference.get("info") or {})
    stats = dict(reference.get("stats") or {})
    shots = list(reference.get("shots") or [])
    if not stats.get("median_s") and not shots and not stats.get("palette") and not info.get("w"):
        raise ValueError("A referência ainda não tem medidas: analise o vídeo primeiro.")
    presets = presets if presets is not None else load_presets()
    base_id, base_reason = nearest_preset(info, stats, presets)
    base = presets[base_id]
    data: dict[str, Any] = copy.deepcopy(base.model_dump(mode="json"))

    origins: list[dict[str, Any]] = [{"path": "base", "value": base_id, "source": "preset",
                                      "detail": f"{base.name}: {base_reason}"}]
    changes = measured_changes(info, stats, shots, base)
    for ch in changes:
        data = set_path(data, ch["path"], ch["value"])
    StyleSpec.model_validate(data)  # as medidas sozinhas já dão um estilo válido
    origins += changes
    # Os efeitos ficam livres para a IA (textura), mas a gradação medida volta sempre.
    locked_set = {ch["path"] for ch in changes} - {"edit.effects"}
    if "format.aspect" in locked_set:  # proporção medida: o formato (e a duração dele) também fica
        locked_set |= {"format.type", "format.duration"}
    locked = sorted(locked_set)
    measured_grade = None
    if any(ch["path"] == "edit.effects" for ch in changes):
        measured_grade = next((e for e in data["edit"]["effects"] if e.get("type") == "grade"), None)

    title = reference_title(reference)
    llm_out = {"used": False, "provider": None, "summary": "", "message": ""}
    rejected: list[dict[str, Any]] = []
    if use_llm:
        call = llm or default_llm
        try:
            context = {"description": f"Estilo a partir da referência {title}",
                       "effects": data["edit"]["effects"]}
            answer, provider_id = call(_llm_system(), _llm_prompt(title, info, stats, data, locked), context)
            llm_out.update(used=True, provider=provider_id, summary=str(answer.get("summary") or ""))
            for change in answer.get("changes") or []:
                path = str(change.get("path") or "")
                if _locked(path, set(locked)):
                    rejected.append({"path": path, "error": "campo definido pelas medidas da referência"})
                    continue
                try:
                    value = json.loads(change.get("value_json") or "null")
                    if path == "edit.effects" and measured_grade and isinstance(value, list):
                        value = [e for e in value if not (isinstance(e, dict) and e.get("type") == "grade")]
                        value.append(measured_grade)
                    candidate = set_path(data, path, value)
                    StyleSpec.model_validate(candidate)
                except Exception as exc:  # noqa: BLE001 - mudança inválida é descartada e reportada
                    rejected.append({"path": path, "error": str(exc)[:300]})
                    continue
                data = candidate
                origins.append({"path": path, "value": value, "source": "llm",
                                "detail": str(change.get("reason") or "")[:300]})
            if not any(o["source"] == "llm" for o in origins):
                llm_out["message"] = ("A IA não mudou nada: as sugestões dela ficaram de fora (campo medido ou "
                                      "valor inválido).")
        except Exception as exc:  # noqa: BLE001 - sem LLM: fica só medidas + preset
            llm_out["message"] = f"Sem IA disponível; o estilo usa só as medidas e o preset ({str(exc)[:200]})."
    else:
        llm_out["message"] = "IA desligada: o estilo usa só as medidas e o preset."

    taken = set(presets)
    data["id"] = new_id or unique_id(f"ref-{slugify(title) or 'estilo'}", taken)
    data["name"] = (name or default_style_name(title))[:NAME_MAX]
    med = stats.get("median_s")
    bits = [f"plano mediano de {med:g} s" if med else "", f"{stats['cuts_per_min']} cortes/min"
            if stats.get("cuts_per_min") else "", f"paleta {' '.join(stats['palette'][:5])}"
            if stats.get("palette") else ""]
    data["description"] = (f"Criado a partir da referência “{title}” sobre o preset {base.name}: "
                           + ", ".join(b for b in bits if b))[:500]
    tags = [t for t in data.get("tags") or [] if t != "referencia"]
    data["tags"] = [*tags, "referencia"]
    style = StyleSpec.model_validate(data)
    return {"style": style.model_dump(mode="json"), "base_style_id": base_id, "base_reason": base_reason,
            "origins": origins, "llm": llm_out, "rejected": rejected}


def create_from_reference(reference: dict[str, Any], *, llm: LLMCall | None = None, use_llm: bool = True,
                          name: str | None = None) -> dict[str, Any]:
    """Monta e salva como estilo do usuário (parent_id = preset base). O id não repete nenhum estilo existente.

    O id é escolhido só na hora de salvar (a IA pode demorar) e a gravação é só inserção: se outro pedido pegou o
    mesmo id nesse meio-tempo, tenta o próximo (-2, -3...) em vez de sobrescrever.
    """
    from sqlalchemy.exc import IntegrityError

    from studio.db import session_scope
    from studio.models import Style

    presets = load_presets()
    title = reference_title(reference)
    out = build_style(reference, presets=presets, llm=llm, use_llm=use_llm, name=name)
    base_slug = f"ref-{slugify(title) or 'estilo'}"
    for _ in range(20):
        try:
            with session_scope() as s:
                taken = {row[0] for row in s.query(Style.id).all()} | set(presets)
                style = StyleSpec.model_validate({**out["style"], "id": unique_id(base_slug, taken)})
                s.add(Style(id=style.id, name=style.name, data=style.model_dump(mode="json"), version=1,
                            parent_id=out["base_style_id"]))
            return {**out, "style": style.model_dump(mode="json"), "version": 1}
        except IntegrityError:
            continue
    raise RuntimeError("não foi possível achar um id livre para o estilo")
