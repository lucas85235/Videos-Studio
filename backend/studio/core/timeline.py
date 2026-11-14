"""Timeline: converte documento + estilo + narração em quadros, legendas e blocos de render.

Regras importantes (README §5.4 e §6.5):
- Cenas são contínuas; o tempo vem da narração real (palavras com timestamps).
- Escolhas aleatórias (câmera, transição) usam sementes por cena: mudar uma cena não mexe nas outras.
- Blocos de render são definidos pelo conteúdo (hash do ID da cena), então inserir/remover cenas
  só afeta blocos vizinhos, e mudanças de tempo em outros pontos não invalidam o bloco.
- Elementos que dependem do tempo global (barra de progresso, marca d'água) ficam fora dos blocos.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from typing import Any

from studio.core.hashing import hash_obj, seed_from
from studio.core.project.document import ProjectDoc, Scene
from studio.core.styles.library import pick_weighted
from studio.core.styles.schema import StyleSpec
from studio.core.text import split_word_indices
from studio.providers.base import Word

SENTENCE_END = (".", "!", "?", "…", ":", ";")
SFX_FOR_TRANSITION = {
    "flash": "whoosh",
    "zoom_blur": "whoosh",
    "light_leak": "whoosh",
    "film_burn": "whoosh",
    "glitch": "glitch",
    "static_burst": "static",
}


@dataclass
class NarrationClip:
    segment_id: str
    path: str
    """Caminho relativo ao diretório de dados."""
    duration: float
    words: list[Word]
    """Timestamps relativos ao início do clipe, um por palavra do roteiro."""


class TimelineError(ValueError):
    pass


def lead_in(style: StyleSpec) -> float:
    return 0.3 if style.format.type == "short" else 0.6


def tail_out(style: StyleSpec) -> float:
    return 0.7 if style.format.type == "short" else 1.5


def normalize_words(words: list[Word], duration: float) -> list[Word]:
    """Mantém os tempos das palavras dentro do clipe.

    Alguns provedores (ex.: a voz do Windows) relatam posições além do fim real do áudio; sem isso, a
    última cena de um segmento poderia começar depois do segmento seguinte.
    """
    if not words:
        return []
    last = max(w.end for w in words)
    scale = duration / last if last > duration + 0.05 and last > 0 else 1.0
    out: list[Word] = []
    prev = 0.0
    for w in words:
        start = min(max(0.0, w.start * scale), duration)
        start = max(start, prev)
        end = min(max(start, w.end * scale), duration)
        out.append(Word(w.text, start, end))
        prev = start
    return out


def _caption_lines(words: list[Word], max_words: int, mode: str) -> list[list[Word]]:
    """Linhas de legenda equilibradas dentro de uma cena (quebra também no fim das frases)."""
    if mode == "word":
        return [[w] for w in words]
    sentences: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        current.append(w)
        if w.text.endswith(SENTENCE_END):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    lines: list[list[Word]] = []
    for sentence in sentences:
        parts = max(1, math.ceil(len(sentence) / max(1, max_words)))
        size = len(sentence) / parts
        for i in range(parts):
            chunk = sentence[int(math.floor(i * size + 0.5)) : int(math.floor((i + 1) * size + 0.5))]
            if chunk:
                lines.append(chunk)
    return lines


def build_timeline(
    doc: ProjectDoc,
    style: StyleSpec,
    lang: str,
    narration: dict[str, NarrationClip],
    images: dict[str, str | None],
) -> dict[str, Any]:
    """Monta a timeline completa de um idioma (em quadros)."""
    fps = style.format.fps
    if not doc.segments:
        raise TimelineError("o projeto não tem roteiro")
    gap = style.voice.gap_s
    t = lead_in(style)
    placements: list[tuple[Any, float, NarrationClip]] = []
    for seg in doc.segments:
        clip = narration.get(seg.id)
        if clip is None:
            raise TimelineError(f"falta a narração do segmento {seg.id} ({lang})")
        placements.append((seg, t, clip))
        t += clip.duration + gap
    total = t - gap + tail_out(style)

    def frame(seconds: float) -> int:
        return int(math.floor(seconds * fps + 0.5))

    scenes_out: list[dict[str, Any]] = []
    sfx: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    caption_style = style.edit.captions
    for index, (seg, start, clip) in enumerate(placements):
        scenes = doc.scenes_of(seg.id)
        if not scenes:
            raise TimelineError(f"o segmento {seg.id} não tem cenas (rode o storyboard)")
        if seg.role == "chapter" or (seg.chapter and seg.role in ("chapter", "item")):
            chapters.append({"title": seg.chapter or seg.text_for(lang, doc.primary_language)[:40], "start": round(start, 2)})
        words_rel = normalize_words(clip.words, clip.duration)
        abs_words = [Word(w.text, start + w.start, start + w.end) for w in words_rel]
        seg_begin = 0.0 if index == 0 else start
        seg_end = placements[index + 1][1] if index + 1 < len(placements) else total
        n_scenes = len(scenes)
        min_step = 1.5 / fps
        ranges = split_word_indices(len(abs_words), n_scenes, [w.text for w in abs_words])
        bounds: list[float] = [seg_begin]
        for k in range(1, n_scenes):
            a = ranges[k][0] if k < len(ranges) else len(abs_words)
            if a < len(abs_words):
                candidate = abs_words[a].start - 0.05
            else:
                candidate = bounds[-1] + (seg_end - bounds[-1]) / (n_scenes - k + 1)
            low = bounds[-1] + min_step
            high = seg_end - (n_scenes - k) * min_step
            bounds.append(max(low, min(candidate, high)) if high >= low else low)
        bounds.append(seg_end)
        for k, scene in enumerate(scenes):
            s0, s1 = bounds[k], bounds[k + 1]
            f0, f1 = frame(s0), max(frame(s0) + 1, frame(s1))
            scene_words = [w for w in abs_words if s0 <= w.start < s1]
            scene_lines = []
            if caption_style.style != "none":
                for part in _caption_lines(scene_words, caption_style.max_words_per_line, caption_style.style):
                    l_start = frame(part[0].start) - f0
                    l_end = min(f1 - f0, frame(part[-1].end + 0.25) - f0)
                    scene_lines.append(
                        {
                            "from": max(0, l_start),
                            "to": max(l_start + 1, l_end),
                            "words": [
                                {"text": w.text, "from": max(0, frame(w.start) - f0), "to": max(1, frame(w.end) - f0)}
                                for w in part
                            ],
                        }
                    )
                # Cada linha some quando a próxima começa
                for i in range(len(scene_lines) - 1):
                    scene_lines[i]["to"] = min(scene_lines[i]["to"], scene_lines[i + 1]["from"]) or scene_lines[i]["to"]
            resolved = resolve_scene(doc, style, scene, lang, is_first=(index == 0 and k == 0))
            resolved.update(
                {
                    "from": f0,
                    "durationInFrames": f1 - f0,
                    "src": images.get(scene.id),
                    "captions": scene_lines,
                    "words": [{"text": w.text, "from": frame(w.start) - f0, "to": frame(w.end) - f0} for w in scene_words],
                }
            )
            scenes_out.append(resolved)
            if style.audio.sfx.auto and not (index == 0 and k == 0):
                kind = scene.sfx or SFX_FOR_TRANSITION.get(resolved["transition"])
                if scene.kind in ("title", "count") and not scene.sfx:
                    kind = "impact"
                if kind:
                    sfx.append({"type": kind, "start": round(max(0.0, s0 - 0.12), 3), "scene_id": scene.id})
            elif scene.sfx:
                sfx.append({"type": scene.sfx, "start": round(max(0.0, s0 - 0.12), 3), "scene_id": scene.id})

    # Garante inícios estritamente crescentes (arredondamento) e cenas contínuas: cada uma termina onde a
    # próxima começa. Sem isso, blocos de render poderiam ter quadros a mais e o vídeo sairia deslocado.
    for i in range(1, len(scenes_out)):
        if scenes_out[i]["from"] <= scenes_out[i - 1]["from"]:
            scenes_out[i]["from"] = scenes_out[i - 1]["from"] + 1
    for i in range(len(scenes_out) - 1):
        scenes_out[i]["durationInFrames"] = scenes_out[i + 1]["from"] - scenes_out[i]["from"]
    total_frames = max(frame(total), scenes_out[-1]["from"] + scenes_out[-1]["durationInFrames"])
    scenes_out[-1]["durationInFrames"] = total_frames - scenes_out[-1]["from"]

    return {
        "version": 1,
        "lang": lang,
        "fps": fps,
        "width": style.format.width,
        "height": style.format.height,
        "durationInFrames": total_frames,
        "duration": round(total_frames / fps, 3),
        "style": render_style(style, doc),
        "scenes": scenes_out,
        "narration": [
            {"segment_id": seg.id, "path": clip.path, "start": round(start, 3), "duration": round(clip.duration, 3)}
            for seg, start, clip in placements
        ],
        "sfx": sfx,
        "chapters": chapters,
    }


def resolve_scene(doc: ProjectDoc, style: StyleSpec, scene: Scene, lang: str, *, is_first: bool) -> dict[str, Any]:
    """Escolhas visuais da cena (determinísticas pela semente da cena)."""
    motion = scene.motion or pick_weighted(style.edit.camera, seed_from(doc.seed, scene.id, "motion"))
    if is_first:
        transition = "cut"
    else:
        transition = scene.transition_in or pick_weighted(style.edit.transitions, seed_from(doc.seed, scene.id, "transition"))
    text = scene.overlay_text.get(lang) or scene.overlay_text.get(doc.primary_language) or ""
    return {
        "id": scene.id,
        "kind": scene.kind,
        "motion": motion,
        "transition": transition,
        "seed": seed_from(doc.seed, scene.id, scene.visual.variation),
        "look": scene.look.model_dump(),
        "text": text,
        "number": scene.number,
    }


def render_style(style: StyleSpec, doc: ProjectDoc | None = None) -> dict[str, Any]:
    """Subconjunto do estilo que o renderer usa (entra no hash dos blocos)."""
    return {
        "family": style.family.value,
        "families": [f.value for f in style.all_families()],
        "effects": [e.model_dump() for e in style.edit.effects],
        "captions": style.edit.captions.model_dump(),
        "textCards": style.edit.text_cards.model_dump(),
        "zoomStrength": style.edit.zoom_strength,
        "transitionFrames": max(1, int(round(style.edit.transition_seconds * style.format.fps))),
        "progressBar": style.edit.progress_bar,
        "progressColor": style.edit.progress_color,
    }


# --------------------------------------------------------------------------------------------
# Blocos de render
# --------------------------------------------------------------------------------------------

CHUNK_AVG_SCENES = 6
CHUNK_MAX_SCENES = 12


def _is_boundary(scene_id: str) -> bool:
    value = int(hashlib.sha256(scene_id.encode("utf-8")).hexdigest()[:8], 16)
    return value % CHUNK_AVG_SCENES == 0


def chunk_timeline(timeline: dict[str, Any], renderer_version: str) -> list[dict[str, Any]]:
    """Divide a timeline em blocos definidos pelo conteúdo. Cada bloco tem props e um hash próprio."""
    scenes = timeline["scenes"]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for scene in scenes:
        current.append(scene)
        if _is_boundary(scene["id"]) or len(current) >= CHUNK_MAX_SCENES:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    chunks = []
    for group in groups:
        start = group[0]["from"]
        duration = sum(s["durationInFrames"] for s in group)
        rel_scenes = [{**s, "from": s["from"] - start} for s in group]
        props = {
            "fps": timeline["fps"],
            "width": timeline["width"],
            "height": timeline["height"],
            "durationInFrames": duration,
            "style": timeline["style"],
            "scenes": rel_scenes,
            "isChunk": True,
            "lang": timeline["lang"],
        }
        key = hash_obj({"props": props, "renderer": renderer_version})
        chunks.append({"key": key, "from": start, "durationInFrames": duration, "scene_ids": [s["id"] for s in group], "props": props})
    return chunks
