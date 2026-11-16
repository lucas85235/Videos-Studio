"""Esquemas JSON das tarefas de LLM (compatíveis com saída estruturada estrita).

Regras: todo objeto tem `additionalProperties: false` e todos os campos em `required`;
campos opcionais são anuláveis (`["string", "null"]`).
"""

from __future__ import annotations

from typing import Any

from studio.core.styles.schema import CAMERA_MOVES, GRADE_PRESETS, SCENE_KINDS, TRANSITIONS, TREATMENTS
from studio.core.styles.visual import VISUAL_LANGUAGES
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

# Tipos de cena que o storyboard pode escolher ("count" só no Top 10; "video" e "document" ficam de fora).
STORYBOARD_KINDS = ["image", "stat", "compare", "title", "text", "quote", "count"]
assert set(STORYBOARD_KINDS) <= set(SCENE_KINDS)
STORYBOARD_SOURCES = ["new", "reuse", "none"]

# Storyboard v2: direção de arte do vídeo + uma cena por batida. Os ids de catálogo (linguagem, plano,
# composição, foco, câmera, gradação) vão como string anulável e são validados no Python: enum que aceita
# null pode falhar no --json-schema do Claude.
STORYBOARD_SCHEMA = _obj(
    {
        "direction": _obj(
            {
                "genre": S,
                "approach": {"type": "string", "enum": ["literal", "misto", "alegorico"]},
                "world": S,
                "treatment": S,
                "palette": {"type": "array", "items": S},
                "languages": {"type": "array", "items": S},
                "grade": NS,
                "entities": {"type": "array", "items": _obj({"id": S, "name": S, "look": S, "real": {"type": "boolean"}})},
                "avoid": {"type": "array", "items": S},
                "style_fit": {"type": "string", "enum": ["ok", "fraco", "errado"]},
                "style_note": S,
                "suggested_style": NS,
                "hook": S,
            }
        ),
        "scenes": {
            "type": "array",
            "items": _obj(
                {
                    "key": S,
                    "kind": {"type": "string", "enum": STORYBOARD_KINDS},
                    "source": {"type": "string", "enum": STORYBOARD_SOURCES},
                    "reuse_key": NS,
                    "prompt": NS,
                    "language": NS,
                    "shot": NS,
                    "composition": NS,
                    "focus": NS,
                    "motion": NS,
                    "entities": NSA,
                    "overlay_text": NS,
                    "value": NS,
                    "label": NS,
                    "items": {"type": ["array", "null"], "items": _obj({"label": S, "value": S})},
                    "highlight": NI,
                    "headline": NS,
                    "stamp": NS,
                    # Número da fonte na lista "Fontes do roteiro" do pedido (o app escreve o nome curto dela).
                    "source_ref": NI,
                    # Sugestão de mídia real, em campos achatados (objeto anulável aninhado é arriscado no
                    # --json-schema do Claude): "photo" | "news" | "official" | null, validado no Python.
                    "real_kind": NS,
                    "real_query": NS,  # inglês, 2 a 6 palavras
                    "real_note": NS,  # português: o que mostrar e onde achar
                    "real_source_ref": NI,  # número da fonte com o link da notícia em "Fontes do roteiro"
                }
            ),
        },
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
    "set_style_id",
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
    "style_id": NS,
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
- family: still_cinema | motion_graphics | analog | ai_video | illustrated_2d (lousa) | handmade (colagem) | screen_ui
  (interface de tela) (data_geo e three_d ainda não renderizam)
- structure: none | top10 | iceberg | tierlist | quiz | timeline | history_in_n | pov | series | would_you_rather
- format.type: short | long ; format.aspect: "9:16" | "16:9" | "1:1" | "4:5" ; format.resolution: "1080x1920" | "1920x1080"
- format.duration.target_s / min_s / max_s (segundos)
- script.tone (texto), script.structure (lista de etapas), script.cta, script.hook_max_s
- voice.voice (voz do Gemini, ex.: Charon, Algenib, Orus, Puck, Kore), voice.style_prompt, voice.speed (0.5–2), voice.gap_s
- art.bible (tratamento de referência: cor, contraste e textura; sem hora do dia, clima nem luz fixa, que cada
  imagem diz a sua), art.banned (lista), art.photoreal (bool)
- art.languages: linguagens visuais preferidas, a primeira dominante (vazio = livre); ids: {", ".join(VISUAL_LANGUAGES)}
  (photo só com art.photoreal)
- edit.shot_seconds.min / max (duração de cada cena; o storyboard corta a narração por frase nessa faixa)
- edit.new_image_ratio (0.1–1): fração das cenas com imagem nova de IA (mínimo 3 por vídeo); o resto reaproveita
  imagem ou vira cartão
- edit.camera: pesos por movimento ({", ".join(CAMERA_MOVES)})
- edit.transitions: pesos por transição ({", ".join(TRANSITIONS)})
- edit.treatments: pesos do tratamento das cenas de imagem ({", ".join(TREATMENTS)}); vazio = automático pela família
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
