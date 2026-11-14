"""Esquemas JSON das tarefas de LLM (compatíveis com saída estruturada estrita).

Regras: todo objeto tem `additionalProperties: false` e todos os campos em `required`;
campos opcionais são anuláveis (`["string", "null"]`).
"""

from __future__ import annotations

from typing import Any

from studio.core.styles.schema import CAMERA_MOVES, GRADE_PRESETS, SCENE_KINDS, TRANSITIONS
from studio.media.audio import MOODS


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}


S = {"type": "string"}
NS = {"type": ["string", "null"]}
NN = {"type": ["number", "null"]}
NI = {"type": ["integer", "null"]}
NB = {"type": ["boolean", "null"]}
NSA = {"type": ["array", "null"], "items": {"type": "string"}}

SCRIPT_SCHEMA = _obj(
    {
        "title": S,
        "segments": {
            "type": "array",
            "items": _obj(
                {
                    "role": {"type": "string", "enum": ["hook", "body", "chapter", "item", "cta"]},
                    "speaker": S,
                    "chapter": NS,
                    "number": NI,
                    "text": S,
                }
            ),
        },
        "sources": {"type": "array", "items": _obj({"title": S, "url": S, "note": S})},
    }
)

STORYBOARD_SCHEMA = _obj(
    {
        "scenes": {
            "type": "array",
            "items": _obj(
                {
                    "key": S,
                    "kind": {"type": "string", "enum": list(SCENE_KINDS)},
                    "prompt": S,
                    "overlay_text": NS,
                }
            ),
        }
    }
)

TRANSLATE_SCHEMA = _obj(
    {
        "title": S,
        "segments": {"type": "array", "items": _obj({"id": S, "text": S})},
        "overlays": {"type": "array", "items": _obj({"id": S, "text": S})},
    }
)

METADATA_SCHEMA = _obj(
    {
        "title": S,
        "description": S,
        "tags": {"type": "array", "items": S},
        "hashtags": {"type": "array", "items": S},
        "thumbnail_text": S,
    }
)

EDIT_OPS = [
    "update_segment_text",
    "add_segment",
    "delete_segment",
    "move_segment",
    "update_scene_prompt",
    "regenerate_scene",
    "set_scene_motion",
    "set_scene_transition",
    "set_scene_kind",
    "set_scene_text",
    "set_scene_look",
    "split_scene",
    "merge_scene_with_next",
    "set_style",
    "set_music",
    "set_lock",
    "set_title",
    "set_metadata",
    "set_thumbnail",
]

FLAT_OP_FIELDS: dict[str, Any] = {
    "op": {"type": "string", "enum": EDIT_OPS},
    "reason": S,
    "segment_id": NS,
    "scene_id": NS,
    "after_segment_id": NS,
    "cue_id": NS,
    "start_segment": NS,
    "lang": NS,
    "text": NS,
    "prompt": NS,
    "role": NS,
    "speaker": NS,
    "motion": NS,
    "transition": NS,
    "kind": NS,
    "number": NI,
    "brightness": NN,
    "contrast": NN,
    "saturation": NN,
    "warmth": NN,
    "blur": NN,
    "grade": NS,
    "path": NS,
    "value_json": NS,
    "mood": NS,
    "volume_db": NN,
    "enabled": NB,
    "ids": NSA,
    "locked": NB,
    "title": NS,
    "description": NS,
    "tags": NSA,
    "hashtags": NSA,
    "regenerate": NB,
}

EDIT_SCHEMA = _obj({"summary": S, "ops": {"type": "array", "items": _obj(FLAT_OP_FIELDS)}})

STYLE_PATCH_SCHEMA = _obj(
    {
        "summary": S,
        "changes": {"type": "array", "items": _obj({"path": S, "value_json": S, "reason": S})},
    }
)

STYLE_FIELDS_HELP = f"""
Campos editáveis do StyleSpec (use caminhos com pontos):
- name, description
- family: still_cinema | motion_graphics | analog | ai_video (outras famílias ainda não renderizam)
- structure: none | top10 | iceberg | tierlist | quiz | timeline | history_in_n | pov | series | would_you_rather
- format.type: short | long ; format.aspect: "9:16" | "16:9" | "1:1" | "4:5" ; format.resolution: "1080x1920" | "1920x1080"
- format.duration.target_s / min_s / max_s (segundos)
- script.tone (texto), script.structure (lista de etapas), script.cta, script.hook_max_s
- voice.voice (voz do Gemini, ex.: Charon, Algenib, Orus, Puck, Kore), voice.style_prompt, voice.speed (0.5–2), voice.gap_s
- art.bible (descrição do estilo de arte), art.banned (lista), art.photoreal (bool)
- edit.shot_seconds.min / max
- edit.camera: pesos por movimento ({", ".join(CAMERA_MOVES)})
- edit.transitions: pesos por transição ({", ".join(TRANSITIONS)})
- edit.transition_seconds, edit.zoom_strength (0–0.6)
- edit.effects: lista de {{type, amount 0–1, preset?}}; tipos: grain, vignette, grade, scanlines, chromatic, vhs, light_leak,
  film_scratches, halftone, duotone, sepia, blur_edges, flicker, dust; presets de grade: {", ".join(GRADE_PRESETS)}
- edit.captions.style: karaoke | word | line | none ; edit.captions.font, size (12–240), color, highlight, stroke,
  position: top | center | bottom ; max_words_per_line ; uppercase
- edit.text_cards.font, color, accent, background, animation: fade_up | typewriter | pop | slide | flicker
- edit.progress_bar (bool), edit.progress_color
- audio.music.mood (um destes climas: {", ".join(MOODS)}), audio.music.volume_db (-60–0)
- audio.music.source: procedural | library | none ; audio.sfx.auto (bool) ; audio.loudness_lufs
Fontes disponíveis: Montserrat, Bebas Neue, Cinzel, Special Elite, VT323, Oswald, Anton, Noto Sans, IBM Plex Sans.
"""
