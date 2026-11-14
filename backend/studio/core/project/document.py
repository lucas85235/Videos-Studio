"""Documento de projeto: a fonte da verdade de cada vídeo (README §6.2).

Segmentos (narração) e cenas (visual) têm IDs estáveis. Os artefatos gerados (áudio, imagens,
blocos de render) não ficam aqui: ficam no cache endereçado por hash.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from studio.core.styles.schema import SCENE_KINDS
from studio.ids import new_id

SceneKind = Literal["image", "text", "title", "count", "document", "quote", "video"]
assert set(SceneKind.__args__) == set(SCENE_KINDS)  # type: ignore[attr-defined]


class Segment(BaseModel):
    id: str = Field(default_factory=lambda: new_id("seg", 8))
    speaker: str = "narrator"
    role: str = "body"
    """hook | body | chapter | cta (orienta o storyboard e os cartões de texto)."""
    chapter: str | None = None
    text: dict[str, str] = {}
    locked: bool = False

    def text_for(self, lang: str, fallback: str | None = None) -> str:
        if lang in self.text and self.text[lang].strip():
            return self.text[lang]
        if fallback and fallback in self.text:
            return self.text[fallback]
        return ""


class Visual(BaseModel):
    type: Literal["ai_image", "stock", "ai_video", "upload", "none"] = "ai_image"
    prompt: str = ""
    query: str | None = None
    asset: str | None = None
    """Caminho relativo (dentro de VS_DATA_DIR) de um asset fixo: upload ou variação escolhida."""
    seed: int = 0
    variation: int = 0


class SceneLook(BaseModel):
    """Ajustes visuais por cena que NÃO exigem nova geração de imagem (só re-render do bloco)."""

    brightness: float = Field(default=0.0, ge=-1, le=1)
    contrast: float = Field(default=0.0, ge=-1, le=1)
    saturation: float = Field(default=0.0, ge=-1, le=1)
    warmth: float = Field(default=0.0, ge=-1, le=1)
    blur: float = Field(default=0.0, ge=0, le=1)
    grade: str | None = None


class Scene(BaseModel):
    id: str = Field(default_factory=lambda: new_id("scn", 8))
    segment_id: str
    part: int = 0
    parts: int = 1
    kind: SceneKind = "image"
    visual: Visual = Visual()
    overlay_text: dict[str, str] = {}
    number: int | None = None
    motion: str | None = None
    transition_in: str | None = None
    look: SceneLook = SceneLook()
    sfx: str | None = None
    locked: bool = False
    notes: str = ""


class MusicCue(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mus", 6))
    start_segment: str | None = None
    mood: str | None = None
    volume_db: float | None = None
    enabled: bool = True


class Metadata(BaseModel):
    title: str = ""
    description: str = ""
    tags: list[str] = []
    hashtags: list[str] = []


class ThumbnailDoc(BaseModel):
    text: dict[str, str] = {}
    scene_id: str | None = None
    prompt: str | None = None
    variation: int = 0


class Source(BaseModel):
    title: str
    url: str = ""
    note: str = ""


class ProjectDoc(BaseModel):
    schema_version: int = 1
    id: str
    title: str
    topic: str = ""
    brief: str = ""
    channel_id: str | None = None
    style_id: str
    style_overrides: dict[str, Any] = {}
    primary_language: str = "pt-BR"
    languages: list[str] = ["pt-BR"]
    seed: int = 0
    segments: list[Segment] = []
    scenes: list[Scene] = []
    music: list[MusicCue] = [MusicCue()]
    thumbnail: ThumbnailDoc = ThumbnailDoc()
    metadata: dict[str, Metadata] = {}
    sources: list[Source] = []
    script_approved: bool = False
    preview_approved: bool = False

    @model_validator(mode="after")
    def _check(self) -> ProjectDoc:
        if self.primary_language not in self.languages:
            self.languages = [self.primary_language, *self.languages]
        seg_ids = [s.id for s in self.segments]
        if len(set(seg_ids)) != len(seg_ids):
            raise ValueError("IDs de segmento repetidos")
        scene_ids = [s.id for s in self.scenes]
        if len(set(scene_ids)) != len(scene_ids):
            raise ValueError("IDs de cena repetidos")
        known = set(seg_ids)
        for scene in self.scenes:
            if scene.segment_id not in known:
                raise ValueError(f"cena {scene.id} aponta para segmento inexistente {scene.segment_id}")
        return self

    # --- consultas -------------------------------------------------------------------------
    def segment(self, segment_id: str) -> Segment:
        for seg in self.segments:
            if seg.id == segment_id:
                return seg
        raise KeyError(f"segmento não encontrado: {segment_id}")

    def scene(self, scene_id: str) -> Scene:
        for scene in self.scenes:
            if scene.id == scene_id:
                return scene
        raise KeyError(f"cena não encontrada: {scene_id}")

    def scenes_of(self, segment_id: str) -> list[Scene]:
        return sorted((s for s in self.scenes if s.segment_id == segment_id), key=lambda s: s.part)

    def ordered_scenes(self) -> list[Scene]:
        order = {seg.id: i for i, seg in enumerate(self.segments)}
        return sorted(self.scenes, key=lambda s: (order.get(s.segment_id, 10**9), s.part))

    def full_text(self, lang: str | None = None) -> str:
        lang = lang or self.primary_language
        return "\n".join(seg.text_for(lang, self.primary_language) for seg in self.segments)

    def outline(self, lang: str | None = None, max_chars: int = 90) -> str:
        """Resumo compacto (1 linha por segmento e por cena) para o agente de edição."""
        lang = lang or self.primary_language
        lines = [f"Projeto {self.id}: {self.title} | estilo {self.style_id} | idiomas {','.join(self.languages)}"]
        for i, seg in enumerate(self.segments, 1):
            text = seg.text_for(lang, self.primary_language).replace("\n", " ")
            lock = " [travado]" if seg.locked else ""
            lines.append(f"S{i} {seg.id} ({seg.role}){lock}: {text[:max_chars]}")
            for scene in self.scenes_of(seg.id):
                desc = scene.visual.prompt or scene.overlay_text.get(lang, "")
                extra = []
                if scene.motion:
                    extra.append(f"câmera={scene.motion}")
                if scene.transition_in:
                    extra.append(f"transição={scene.transition_in}")
                if scene.locked:
                    extra.append("travada")
                suffix = f" [{', '.join(extra)}]" if extra else ""
                lines.append(f"   C {scene.id} {scene.kind}: {desc[:max_chars]}{suffix}")
        return "\n".join(lines)


def renumber_parts(doc: ProjectDoc, segment_id: str) -> None:
    """Reindexa `part`/`parts` das cenas de um segmento depois de dividir/juntar."""
    scenes = doc.scenes_of(segment_id)
    for index, scene in enumerate(scenes):
        scene.part = index
        scene.parts = len(scenes)
