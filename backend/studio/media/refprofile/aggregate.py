"""Montagem do `EditProfile` (ADR 0004 §3.2, `aggregate.py`): das medidas brutas para os blocos, com confiança e origem
por campo (`meta`), e o perfil de canal (várias referências pela mediana, campo a campo).

Confiança (0 a 1), pela qualidade do extrator:
- ritmo (cortes): 0,9 (0,6 com menos de 10 planos);
- câmera (punch, zoom, pan, tremida, parado): 0,6;
- sobreposição por movimento local: 0,4;
- SFX: 0,4 sem transcrição, 0,6 com; trilha e drops: 0,3 (0,5 com transcrição);
- visão real: 0,7; `MockVision` (offline): 0,3;
- fala medida: 0,8; tom, estrutura e humor (LLM): 0,5.

Duas definições alinhadas com a métrica da timeline (`core/editing/metrics`):
- `audio.sfx_per_min` não conta os SFX da transição: o whoosh colado no corte (`on_cut`) é o equivalente da referência
  ao som de transição da timeline. `sfx_classes` também é dos SFX sem os da transição; `sfx_on_cut_share` continua
  sendo de todos;
- `camera.kb_share` é a fração do tempo dos planos de IMAGEM parada (`motion`: quase todo par é a mesma imagem movida)
  com zoom lento ou pan, como a timeline mede; a câmera que anda na filmagem (gameplay, trailer) não conta.

Perfil de canal (`merge_profiles`): cada campo usa só os perfis que o MEDIRAM (`meta[campo]` com confiança > 0). O
padrão de um campo não medido (wpm 0 de um perfil rápido, mistura vazia de quem não teve visão) não entra na mediana.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from statistics import median
from typing import Any

from studio.config import REPO_ROOT
from studio.core.editing.profile import (
    MATERIAL_TO_PROV,
    EditFingerprint,
    EditProfile,
    FieldMeta,
    prov_mix_from_materials,
)
from studio.media.refprofile import cuts as cuts_mod

ANNOT_KINDS = frozenset({"arrow", "circle", "highlight"})


def _per_min(count: float, dur: float) -> float:
    return round(count / (dur / 60.0), 2) if dur > 0 else 0.0


def _span(items: list[dict[str, Any]]) -> float:
    return sum(max(0.0, float(i["end"]) - float(i["start"])) for i in items)


def visual_events(base: dict[str, Any]) -> list[float]:
    """Instantes de evento visual: cortes regulares, micro-planos, punches, sobreposições e congelamentos."""
    shots = base.get("shots") or []
    events = list(cuts_mod.regular_cut_times(shots))
    events += [float(s["a"]) for s in shots if s.get("micro")]
    events += [float(p["t"]) for p in base.get("punches") or []]
    events += [float(o["start"]) for o in base.get("overlays") or []]
    events += [float(f[0]) for f in base.get("freezes") or []]
    return sorted(events)


def max_still(events: list[float], dur: float, start: float = 0.0) -> float:
    """Maior trecho sem evento visual entre `start` e `dur`."""
    marks = [start, *[e for e in events if start < e < dur], dur]
    return round(max((b - a for a, b in zip(marks, marks[1:], strict=False)), default=dur - start), 2)


def _kb_share(base: dict[str, Any]) -> float:
    """Fração do tempo de imagem parada com Ken Burns ou pan (ver o topo)."""
    image = float(base.get("image_s") or 0.0)
    return round(min(1.0, float(base.get("kb_s") or 0.0) / image), 3) if image > 0 else 0.0


def transition_sfx(onset: dict[str, Any]) -> bool:
    """O SFX que faz o papel do som de transição da timeline: whoosh colado no corte."""
    return bool(onset.get("on_cut")) and onset.get("cls") == "whoosh"


def measured_blocks(base: dict[str, Any], audio: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Blocos `pace`, `camera`, `overlays` (parte de movimento) e `audio` medidos sem visão nem fala."""
    dur = float(base["info"]["dur"])
    shots = base.get("shots") or []
    pace = cuts_mod.pace(shots, dur)
    # como o ritmo: sem a abertura e o encerramento sem cortes (o outro de 55 s não vira "parado máximo" do estilo)
    lo, hi = cuts_mod.active_span(cuts_mod.regular_cut_times(shots), dur)
    events = [e for e in visual_events(base) if lo <= e < hi]
    pace["max_still_s"] = max_still(events, hi, lo)
    pace["events_per_min"] = _per_min(len(events), hi - lo)
    punches = base.get("punches") or []
    ramps = base.get("ramps") or []
    analyzed = float(base.get("analyzed_s") or 0.0)
    camera = {
        "punch_per_min": _per_min(len(punches), dur),
        "punch_scale": round(median(abs(float(p["scale"])) for p in punches), 3) if punches else 0.0,
        "shake_per_min": _per_min(len(base.get("shakes") or []), dur),
        "slow_zoom_share": round(min(1.0, _span(ramps) / dur), 3) if dur else 0.0,
        "zoom_rate_pct_s": round(median(float(r["rate_pct_s"]) for r in ramps), 2) if ramps else 0.0,
        "kb_share": _kb_share(base),
        "static_share": round(min(1.0, float(base.get("static_s") or 0.0) / analyzed), 3) if analyzed else 0.0,
        "freeze_per_min": _per_min(len(base.get("freezes") or []), dur),
    }
    overlays = {"overlay_per_min": _per_min(len(base.get("overlays") or []), dur)}
    audio_block: dict[str, Any] = {}
    data = audio if audio is not None else base.get("audio") or {}
    if data.get("has_audio"):
        sfx = [o for o in data.get("onsets") or [] if o.get("sfx")]
        accents = [o for o in sfx if not transition_sfx(o)]
        classes = Counter(o.get("cls") or "other" for o in accents)
        audio_block = {
            "sfx_per_min": _per_min(len(accents), dur),
            "sfx_on_cut_share": round(sum(1 for o in sfx if o.get("on_cut")) / len(sfx), 3) if sfx else 0.0,
            "sfx_classes": {k: round(v / len(accents), 3) for k, v in classes.items()} if accents else {},
            "music_level_db": data.get("music_level_db"),
            "drops_per_min": _per_min(len(data.get("drops") or []), dur),
        }
    return {"pace": pace, "camera": camera, "overlays": overlays, "audio": audio_block}


def fingerprint(base: dict[str, Any]) -> EditFingerprint:
    """Impressão digital do vídeo (o mesmo extrator do perfil, modo rápido)."""
    blocks = measured_blocks(base)
    return EditFingerprint(source="video", duration_s=round(float(base["info"]["dur"]), 3), pace=blocks["pace"],
                           camera=blocks["camera"], overlays=blocks["overlays"], audio=blocks["audio"] or {})


# --------------------------------------------------------------------------------------------
# Visão
# --------------------------------------------------------------------------------------------


def _mode(values: list[str]) -> str:
    values = [v for v in values if v]
    return Counter(values).most_common(1)[0][0] if values else ""


def font_match(desc: str) -> str:
    """A fonte livre do catálogo (`assets/fonts/fonts.json`) mais parecida com a descrição. Pricedown (a do GTA) nunca:
    licença não verificada (ADR 0004 §4); a mais próxima é a Anton."""
    if not desc:
        return ""
    try:
        catalog = set(json.loads((REPO_ROOT / "assets" / "fonts" / "fonts.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        catalog = set()
    text = desc.lower()
    rules = [
        (("pricedown", "gta", "grafite", "graffiti", "contorno", "outline", "display"), "Anton"),
        (("máquina", "maquina", "typewriter", "datilograf"), "Special Elite"),
        (("pixel", "8-bit", "8 bit", "retrô", "retro", "terminal"), "VT323"),
        (("serif", "serifa", "romana", "clássica", "classica"), "Cinzel"),
        (("condensada", "condensed", "estreita", "narrow", "alta", "tall"), "Bebas Neue"),
        (("pesada", "heavy", "black", "grossa", "impact"), "Anton"),
        (("mono", "código", "codigo"), "IBM Plex Sans"),
        (("geométrica", "geometrica", "arredondada", "rounded", "sans", "bold", "negrito"), "Montserrat"),
    ]
    for words, font in rules:
        if any(w in text for w in words) and (font in catalog or not catalog):
            return font
    return "Montserrat" if "Montserrat" in catalog or not catalog else ""


def vision_blocks(shots: list[dict[str, Any]], dur: float) -> dict[str, dict[str, Any]]:
    """`materials`, a parte de texto/meme de `overlays` e `captions`, a partir das respostas por plano
    (`{"a", "dur", "material", "on_screen_text", ...}`, na ordem do tempo)."""
    total = sum(float(s["dur"]) for s in shots)
    if total <= 0:
        return {}
    mix: dict[str, float] = {}
    for s in shots:
        mix[s["material"]] = mix.get(s["material"], 0.0) + float(s["dur"])
    mix = {k: round(v / total, 4) for k, v in sorted(mix.items(), key=lambda kv: -kv[1])}
    provs = [MATERIAL_TO_PROV.get(s["material"], s["material"]) for s in shots]
    switches = sum(1 for a, b in zip(provs, provs[1:], strict=False) if a != b)
    best = best_s = run = run_s = 0.0
    for n, prov in enumerate(provs):
        if n and prov == provs[n - 1]:
            run, run_s = run + 1, run_s + float(shots[n]["dur"])
        else:
            run, run_s = 1, float(shots[n]["dur"])
        best, best_s = max(best, run), max(best_s, run_s)
    texty = [s for s in shots if s.get("on_screen_text")]
    text_time = sum(float(s["dur"]) for s in texty)
    text_pops = sum(1 for a, b in zip([None, *shots], shots, strict=False)
                    if b.get("on_screen_text") and (a is None or a.get("on_screen_text") != b.get("on_screen_text")))
    memes = sum(1 for a, b in zip([None, *shots], shots, strict=False)
                if (b.get("is_meme") or b["material"] == "meme_reaction")
                and not (a and (a.get("is_meme") or a["material"] == "meme_reaction")))
    annots = sum(1 for s in shots if s.get("has_overlay") and s.get("overlay_kind") in ANNOT_KINDS)
    font_desc = _mode([s.get("font_desc") or "" for s in texty])
    case = _mode([s.get("text_case") or "" for s in texty])
    pos = _mode([s.get("text_pos") or "" for s in texty])
    words = [len(str(s["on_screen_text"]).split()) for s in texty]
    share = min(1.0, text_time / total)
    style = ""
    if share >= 0.3 and words:
        style = "word" if median(words) <= 3 else "line"
    overlays = {"text_pop_per_min": _per_min(text_pops, dur), "meme_per_min": _per_min(memes, dur),
                "annot_per_min": _per_min(annots, dur), "on_screen_text_share": round(share, 3),
                "text_case": case, "text_pos": pos, "font_desc": font_desc}
    captions = {"style": style, "words_per_line": round(float(median(words)), 1) if words else 0.0,
                "uppercase": (case == "upper") if case else None,
                "position": pos if pos in ("top", "center", "bottom") else "",
                "font_desc": font_desc, "font_match": font_match(font_desc)}
    materials = {"mix": mix, "prov_mix": {k: round(v, 4) for k, v in prov_mix_from_materials(mix).items()},
                 "switch_per_min": _per_min(switches, dur), "max_same_prov_run": int(best),
                 "max_same_prov_run_s": round(best_s, 2)}
    return {"materials": materials, "overlays": overlays, "captions": captions}


# --------------------------------------------------------------------------------------------
# Perfil
# --------------------------------------------------------------------------------------------


def _meta(fields: list[str], confidence: float, origin: str) -> dict[str, FieldMeta]:
    return {f: FieldMeta(confidence=round(confidence, 2), origin=origin) for f in fields}  # type: ignore[arg-type]


def build(base: dict[str, Any], *, quick: bool = True, speech: dict[str, Any] | None = None,
          vision: dict[str, Any] | None = None, recurring: list[dict[str, Any]] | None = None,
          audio: dict[str, Any] | None = None) -> EditProfile:
    """O perfil a partir das medidas: `base` (cortes, movimento, áudio), e no modo profundo `speech` e `vision`."""
    dur = float(base["info"]["dur"])
    has_words = bool(speech and speech.get("words"))
    blocks = measured_blocks(base, audio)
    meta: dict[str, FieldMeta] = {}
    pace_conf = 0.9 if blocks["pace"].get("shots", 0) >= 10 else 0.6
    meta |= _meta([f"pace.{k}" for k in blocks["pace"]], pace_conf, "medido")
    meta |= _meta([f"camera.{k}" for k in blocks["camera"]], 0.6, "medido")
    meta |= _meta(["overlays.overlay_per_min"], 0.4, "medido")
    if blocks["audio"]:
        meta |= _meta(["audio.sfx_per_min", "audio.sfx_on_cut_share", "audio.sfx_classes"],
                      0.6 if has_words else 0.4, "medido")
        meta |= _meta(["audio.music_level_db", "audio.drops_per_min"], 0.5 if has_words else 0.3, "medido")
    data: dict[str, Any] = {"pace": blocks["pace"], "camera": blocks["camera"], "overlays": blocks["overlays"],
                            "audio": blocks["audio"]}
    if vision and vision.get("shots"):
        vb = vision_blocks(vision["shots"], dur)
        conf = 0.3 if vision.get("mock") else 0.7
        data["materials"] = vb["materials"]
        data["overlays"] = {**data["overlays"], **vb["overlays"]}
        data["captions"] = vb["captions"]
        meta |= _meta([f"materials.{k}" for k in vb["materials"]], conf, "visão")
        meta |= _meta([f"overlays.{k}" for k in vb["overlays"]], conf, "visão")
        meta |= _meta([f"captions.{k}" for k in vb["captions"] if vb["captions"][k] not in ("", None)],
                      conf * 0.6, "visão")
    if speech and speech.get("measure"):
        data["speech"] = dict(speech["measure"])
        meta |= _meta([f"speech.{k}" for k in speech["measure"]], 0.8, "medido")
        humor = speech.get("humor") or {}
        if humor:
            data["speech"]["tone_tags"] = humor.get("tone_tags") or []
            data["speech"]["structure"] = humor.get("structure") or []
            data["humor"] = {"patterns": humor.get("patterns") or [], "gag_per_min": humor.get("gag_per_min") or 0.0}
            meta |= _meta(["speech.tone_tags", "speech.structure", "humor.patterns", "humor.gag_per_min"], 0.5, "llm")
    if recurring is not None:
        data["recurring"] = recurring
        meta |= _meta(["recurring"], 0.5, "medido")
    return EditProfile(version=1, quick=quick, duration_s=round(dur, 3),
                       measured_at=datetime.now(UTC).isoformat(timespec="seconds"), meta=meta, **data)


# --------------------------------------------------------------------------------------------
# Perfil de canal: várias referências pela mediana
# --------------------------------------------------------------------------------------------


def _merge_values(values: list[Any]) -> Any:
    """A mediana (número), a moda (texto, booleano), a mistura mediana renormalizada (dict) ou a lista do primeiro
    perfil (números) / a união (textos), só sobre os valores MEDIDOS (quem chama já filtrou)."""
    present = [v for v in values if v is not None]
    if not present:
        return None
    first = present[0]
    if isinstance(first, bool):
        return Counter(present).most_common(1)[0][0]
    if isinstance(first, int | float):
        mid = float(median(float(v) for v in present))
        return int(round(mid)) if all(isinstance(v, int) for v in present) else round(mid, 4)
    if isinstance(first, str):
        return _mode(present) or ""
    if isinstance(first, dict):
        present = [v for v in present if v]  # mistura vazia: ninguém mediu de verdade
        if not present:
            return {}
        keys = {k for v in present for k in v}
        out = {k: float(median(float(v.get(k, 0.0)) for v in present)) for k in keys}
        total = sum(out.values())
        if total <= 0:
            return {}
        if all(0 <= x <= 1 for x in out.values()):
            out = {k: round(x / total, 4) for k, x in out.items() if x > 0}
        return out
    if isinstance(first, list):
        if any(v and not isinstance(v[0], str) for v in present):
            # janelas de cortes e estrutura: as do 1º perfil que tem a lista (não se somam)
            return next(v for v in present if v)
        merged: list[Any] = []
        for v in present:
            for item in v:
                if item not in merged:
                    merged.append(item)
        return merged
    return first


def _measured(profile: EditProfile, block: str, key: str) -> bool:
    meta = profile.meta.get(f"{block}.{key}")
    return meta is not None and meta.confidence > 0


def merge_profiles(profiles: list[EditProfile]) -> EditProfile:
    """Perfil de canal: a mediana, campo a campo, dos perfis que MEDIRAM o campo (`meta` com confiança > 0; mistura
    renormalizada; texto pela moda; listas de texto unidas). Campo que ninguém mediu fica no padrão, sem `meta`."""
    if not profiles:
        return EditProfile()
    if len(profiles) == 1:
        return profiles[0]
    dumps = [p.model_dump() for p in profiles]
    defaults = EditProfile().model_dump()
    out: dict[str, Any] = {}
    meta: dict[str, FieldMeta] = {}
    for block in ("pace", "camera", "materials", "overlays", "captions", "audio", "speech", "humor"):
        out[block] = {}
        for k in defaults[block]:
            pairs = [(p, d[block][k]) for p, d in zip(profiles, dumps, strict=False) if _measured(p, block, k)]
            if not pairs:
                out[block][k] = defaults[block][k]
                continue
            value = _merge_values([v for _, v in pairs])
            out[block][k] = defaults[block][k] if value is None else value
            metas = [p.meta[f"{block}.{k}"] for p, _ in pairs]
            meta[f"{block}.{k}"] = FieldMeta(confidence=round(float(median(m.confidence for m in metas)), 2),
                                             origin=metas[0].origin)
        for k in ("tone_tags", "patterns"):
            if k in out[block]:
                out[block][k] = out[block][k][:6]
    recurring = sorted((r for d in dumps for r in d["recurring"]), key=lambda r: -r["count"])[:10]
    rec_metas = [p.meta["recurring"] for p in profiles if "recurring" in p.meta and p.meta["recurring"].confidence > 0]
    if rec_metas:
        meta["recurring"] = FieldMeta(confidence=round(float(median(m.confidence for m in rec_metas)), 2),
                                      origin=rec_metas[0].origin)
    return EditProfile(
        version=1, quick=all(p.quick for p in profiles),
        duration_s=round(float(median(p.duration_s for p in profiles)), 3),
        measured_at=max((p.measured_at or "" for p in profiles), default="") or None,
        refs=[r for p in profiles for r in p.refs], recurring=recurring, meta=meta, **out,
    )
