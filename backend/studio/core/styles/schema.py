"""StyleSpec: a especificação validada de um estilo (README §5.4).

O mesmo formato é usado pelos presets em YAML, pelos estilos do usuário e pelo renderer.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from studio.core.styles.visual import VISUAL_LANGUAGES, validate_languages


class Family(StrEnum):
    still_cinema = "still_cinema"
    motion_graphics = "motion_graphics"
    data_geo = "data_geo"
    illustrated_2d = "illustrated_2d"
    handmade = "handmade"
    three_d = "three_d"
    analog = "analog"
    screen_ui = "screen_ui"
    ai_video = "ai_video"


FAMILY_LABELS: dict[Family, str] = {
    Family.still_cinema: "Cinema de imagens paradas",
    Family.motion_graphics: "Motion graphics editorial",
    Family.data_geo: "Dados e geografia",
    Family.illustrated_2d: "Ilustrado 2D",
    Family.handmade: "Textura artesanal",
    Family.three_d: "3D e simulação",
    Family.analog: "Analógico e atmosfera",
    Family.screen_ui: "Interface de tela",
    Family.ai_video: "Vídeo IA",
}

# Famílias que o renderer já desenha (as outras caem no modo imagens paradas).
FAMILIES_V1 = {
    Family.still_cinema,
    Family.motion_graphics,
    Family.analog,
    Family.ai_video,
    Family.data_geo,
    Family.illustrated_2d,
    Family.handmade,
    Family.screen_ui,
}


class Structure(StrEnum):
    none = "none"
    top10 = "top10"
    iceberg = "iceberg"
    tierlist = "tierlist"
    quiz = "quiz"
    timeline = "timeline"
    history_in_n = "history_in_n"
    pov = "pov"
    series = "series"
    would_you_rather = "would_you_rather"


CAMERA_MOVES = (
    "ken_burns_in",
    "ken_burns_out",
    "pan_left",
    "pan_right",
    "pan_up",
    "pan_down",
    "punch_zoom",
    "drift",
    "static",
    "zoom_to",  # aproxima do foco da cena (Scene.focus); feito para imagem reaproveitada
)
TRANSITIONS = ("cut", "flash", "fade_black", "glitch", "light_leak", "zoom_blur", "film_burn", "static_burst")
EFFECTS = (
    "grain",
    "vignette",
    "grade",
    "scanlines",
    "chromatic",
    "vhs",
    "light_leak",
    "film_scratches",
    "halftone",
    "duotone",
    "sepia",
    "blur_edges",
    "flicker",
    "dust",
)
GRADE_PRESETS = (
    "neutral",
    "vivid",
    "warm-soft",
    "clean-cool",
    "cold-teal-dark",
    "warm-sepia",
    "noir",
    "bleach-bypass",
    "vhs-faded",
    "blood-red",
    "night-green",
)
# Como a cena de imagem é desenhada: "plain" é a imagem com câmera; as outras são variações de família
# (whiteboard: o traço vai sendo desenhado; collage: recorte de papel sobre textura; screen: dentro de uma
# janela, celular ou feed, com cursor).
TREATMENTS = ("plain", "whiteboard", "collage", "screen")
# Tratamento de cada família quando o estilo não define `edit.treatments`.
FAMILY_TREATMENTS: dict[Family, str] = {
    Family.illustrated_2d: "whiteboard",
    Family.handmade: "collage",
    Family.screen_ui: "screen",
}
SCENE_KINDS = ("image", "text", "title", "count", "document", "quote", "video", "stat", "compare", "chart", "map")
# Tipos do cartão `chart` (família de dados): barras, linha, pizza e rosca.
CHART_TYPES = ("bar", "line", "pie", "donut")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Range(StrictModel):
    min: float
    max: float

    @model_validator(mode="after")
    def _order(self) -> Range:
        if self.min > self.max:
            raise ValueError("min deve ser menor ou igual a max")
        return self


class DurationSpec(StrictModel):
    target_s: float = Field(gt=0)
    min_s: float | None = None
    max_s: float | None = None


class FormatSpec(StrictModel):
    type: Literal["short", "long"] = "short"
    aspect: Literal["9:16", "16:9", "1:1", "4:5"] = "9:16"
    resolution: str = "1080x1920"
    fps: int = Field(default=30, ge=12, le=60)
    duration: DurationSpec = DurationSpec(target_s=55, min_s=40, max_s=59)

    @field_validator("resolution")
    @classmethod
    def _res(cls, value: str) -> str:
        try:
            w, h = (int(x) for x in value.lower().split("x"))
        except ValueError as exc:  # pragma: no cover - mensagem
            raise ValueError("resolução deve ser LARGURAxALTURA, ex.: 1080x1920") from exc
        if w % 2 or h % 2:
            raise ValueError("largura e altura precisam ser pares")
        return f"{w}x{h}"

    @property
    def width(self) -> int:
        return int(self.resolution.split("x")[0])

    @property
    def height(self) -> int:
        return int(self.resolution.split("x")[1])


class LanguagesSpec(StrictModel):
    primary: str = "pt-BR"
    outputs: list[str] = ["pt-BR"]
    delivery: Literal["separate_videos", "multi_audio_track"] = "separate_videos"

    @model_validator(mode="after")
    def _primary_in_outputs(self) -> LanguagesSpec:
        if self.primary not in self.outputs:
            self.outputs = [self.primary, *self.outputs]
        return self


class SpeakerSpec(StrictModel):
    voice_profile: str | None = None
    voice: str | None = None
    style_prompt: str | None = None


class ScriptSpec(StrictModel):
    tone: str = "narrativo, envolvente"
    structure: list[str] = ["hook", "contexto", "desenvolvimento", "cta"]
    hook_max_s: float = 3.0
    fact_check: bool = False
    cta: str | None = None
    banned_phrases: list[str] = []
    speakers: dict[str, SpeakerSpec] = {"narrator": SpeakerSpec()}
    words_per_minute: dict[str, int] = {"pt-BR": 150, "en-US": 155, "es-ES": 160}


class TimbreSpec(StrictModel):
    rvc_model: str | None = None
    enabled: bool = False


class QaSpec(StrictModel):
    """Controle de qualidade da narração: transcreve cada trecho e regenera se errar demais."""

    enabled: bool = True
    max_wer: float = Field(default=0.2, ge=0, le=1)
    retries: int = Field(default=1, ge=0, le=3)


class PreviewVoiceSpec(StrictModel):
    local: bool = True
    """Prévia com a voz local grátis (Kokoro). Desligado, a prévia já usa a voz final e gasta a cota dela."""
    voice: str | None = None
    """Voz local (ex.: 'pf_dora'). Vazio: uma do mesmo gênero da voz final."""


class VoiceSpec(StrictModel):
    chain: list[str] = ["gemini_tts", "google_chirp3hd", "qwen3_tts_local"]
    """Voz do vídeo final, em ordem de preferência (a prévia usa a voz local, ver `preview`)."""
    preview: PreviewVoiceSpec = PreviewVoiceSpec()
    voice_profile: str | None = None
    voice: str | None = None
    """Nome da voz no provedor (ex.: 'Charon' no Gemini)."""
    style_prompt: str | None = None
    """Instrução de interpretação (ex.: 'voz grave e baixa, ritmo lento, suspense')."""
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    timbre: TimbreSpec = TimbreSpec()
    qa: QaSpec = QaSpec()
    gap_s: float = Field(default=0.25, ge=0, le=3)
    """Pausa entre segmentos de narração."""


class ArtSpec(StrictModel):
    bible: str = "ilustração digital detalhada e limpa, cores naturais e equilibradas, luz suave, composição clara com um sujeito em destaque"
    banned: list[str] = ["texto", "letras", "logotipos", "marca d'água"]
    anchors: list[str] = []
    image_chain: list[str] = ["codex_cli"]
    thumbnail_chain: list[str] = ["codex_cli"]
    sources_mix: dict[str, float] = {"ai_image": 1.0}
    photoreal: bool = False
    """Arte fotorrealista exige rótulo de conteúdo sintético nas plataformas."""
    languages: list[str] = []
    """Linguagens visuais preferidas (ids de `visual.VISUAL_LANGUAGES`), a primeira dominante. Vazio: livre."""

    @field_validator("languages")
    @classmethod
    def _languages(cls, value: list[str]) -> list[str]:
        return validate_languages(value)

    @model_validator(mode="after")
    def _photoreal_languages(self) -> ArtSpec:
        # Sem fotorrealismo, as linguagens fotorrealistas são ignoradas (não derruba estilo com ajuste antigo).
        if not self.photoreal:
            self.languages = [x for x in self.languages if not VISUAL_LANGUAGES[x].photoreal]
        return self


class EffectSpec(StrictModel):
    type: str
    amount: float = Field(default=0.5, ge=0, le=1)
    preset: str | None = None
    params: dict[str, Any] = {}

    @field_validator("type")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in EFFECTS:
            raise ValueError(f"efeito desconhecido: {value} (válidos: {', '.join(EFFECTS)})")
        return value

    @model_validator(mode="after")
    def _grade_preset(self) -> EffectSpec:
        if self.type == "grade":
            preset = self.preset or "neutral"
            if preset not in GRADE_PRESETS:
                raise ValueError(f"preset de cor desconhecido: {preset}")
            self.preset = preset
        return self


class CaptionSpec(StrictModel):
    style: Literal["karaoke", "word", "line", "none"] = "karaoke"
    font: str = "Montserrat"
    weight: int = 800
    size: int = Field(default=72, ge=12, le=240)
    color: str = "#FFFFFF"
    highlight: str = "#FFD400"
    stroke: int = Field(default=6, ge=0, le=30)
    stroke_color: str = "#000000"
    position: Literal["top", "center", "bottom"] = "center"
    max_words_per_line: int = Field(default=3, ge=1, le=12)
    uppercase: bool = True
    background: str | None = None


class TextCardSpec(StrictModel):
    font: str = "Bebas Neue"
    color: str = "#F2F2F2"
    accent: str = "#C9A227"
    background: str = "#07080B"
    animation: Literal["fade_up", "typewriter", "pop", "slide", "flicker"] = "fade_up"


class EditSpec(StrictModel):
    shot_seconds: Range = Range(min=2.0, max=3.5)
    """Duração de cada cena (batida) em segundos: o storyboard corta a narração por frase nessa faixa."""
    new_image_ratio: float = Field(default=0.5, ge=0.1, le=1.0)
    """Fração das cenas que ganha imagem nova de IA (mínimo 3 por vídeo); o resto reaproveita ou é cartão."""
    camera: dict[str, float] = {"ken_burns_in": 3, "ken_burns_out": 2, "pan_left": 1, "pan_right": 1}
    transitions: dict[str, float] = {"cut": 6, "flash": 1}
    transition_seconds: float = Field(default=0.35, ge=0.05, le=2)
    effects: list[EffectSpec] = [EffectSpec(type="grain", amount=0.15), EffectSpec(type="vignette", amount=0.25)]
    captions: CaptionSpec = CaptionSpec()
    text_cards: TextCardSpec = TextCardSpec()
    progress_bar: bool = False
    progress_color: str = "#FFD400"
    watermark: str | None = None
    zoom_strength: float = Field(default=0.12, ge=0, le=0.6)
    treatments: dict[str, float] = {}
    """Pesos do tratamento das cenas de imagem (ids de `TREATMENTS`). Vazio: automático pela família."""

    @field_validator("treatments")
    @classmethod
    def _treatments(cls, value: dict[str, float]) -> dict[str, float]:
        # Vazio vale (automático pela família); preenchido segue a regra dos outros sorteios.
        return _validate_pool(value, TREATMENTS, "tratamento de cena") if value else value

    @field_validator("camera")
    @classmethod
    def _camera(cls, value: dict[str, float]) -> dict[str, float]:
        return _validate_pool(value, CAMERA_MOVES, "movimento de câmera")

    @field_validator("transitions")
    @classmethod
    def _transitions(cls, value: dict[str, float]) -> dict[str, float]:
        return _validate_pool(value, TRANSITIONS, "transição")


def _validate_pool(value: dict[str, float], allowed: tuple[str, ...], label: str) -> dict[str, float]:
    if not value:
        raise ValueError(f"defina pelo menos um {label}")
    for key, weight in value.items():
        if key not in allowed:
            raise ValueError(f"{label} desconhecido: {key} (válidos: {', '.join(allowed)})")
        if weight < 0:
            raise ValueError(f"peso negativo para {key}")
    if sum(value.values()) <= 0:
        raise ValueError(f"a soma dos pesos de {label} precisa ser positiva")
    return value


class MusicSpec(StrictModel):
    source: Literal["procedural", "library", "none"] = "procedural"
    mood: str = "neutral"
    """Clima da trilha (neutral, upbeat, inspiring, lofi, corporate, playful, calm, epic...; ver `media/audio.py`)."""
    volume_db: float = Field(default=-22, ge=-60, le=0)
    ducking: bool = True


class SfxSpec(StrictModel):
    auto: bool = True
    pack: str = "basic"
    volume_db: float = Field(default=-10, ge=-60, le=6)


class AudioSpec(StrictModel):
    music: MusicSpec = MusicSpec()
    sfx: SfxSpec = SfxSpec()
    loudness_lufs: float = Field(default=-14, ge=-30, le=-5)


class ThumbnailSpec(StrictModel):
    template: Literal["texto-grande-esquerda", "texto-centro", "texto-baixo", "sem-texto"] = "texto-grande-esquerda"
    variations: int = Field(default=3, ge=1, le=6)
    per_language: bool = True
    font: str = "Bebas Neue"
    color: str = "#FFFFFF"
    accent: str = "#FFD400"


class VariationSpec(StrictModel):
    seed_scope: Literal["video", "channel"] = "video"
    avoid_repeat_last: int = Field(default=5, ge=0, le=50)


class PublishTarget(StrictModel):
    platform: Literal["youtube", "tiktok", "instagram", "facebook", "threads", "x"]
    driver: Literal["youtube_native", "instagram_native", "buffer", "manual_helper", "uploadpost"] = "manual_helper"
    privacy: str | None = None
    schedule: str | None = None


class PublishSpec(StrictModel):
    targets: list[PublishTarget] = [PublishTarget(platform="youtube", driver="youtube_native", privacy="private")]
    ai_label: Literal["auto", "always", "never"] = "auto"


class StyleSpec(StrictModel):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9\-]{1,80}$")
    name: str
    description: str = ""
    family: Family = Family.still_cinema
    families_mix: list[Family] = []
    structure: Structure = Structure.none
    tags: list[str] = []
    format: FormatSpec = FormatSpec()
    languages: LanguagesSpec = LanguagesSpec()
    script: ScriptSpec = ScriptSpec()
    voice: VoiceSpec = VoiceSpec()
    art: ArtSpec = ArtSpec()
    edit: EditSpec = EditSpec()
    audio: AudioSpec = AudioSpec()
    thumbnail: ThumbnailSpec = ThumbnailSpec()
    variation: VariationSpec = VariationSpec()
    publish: PublishSpec = PublishSpec()

    @model_validator(mode="after")
    def _mix_contains_family(self) -> StyleSpec:
        if self.families_mix and self.family not in self.families_mix:
            self.families_mix = [self.family, *self.families_mix]
        return self

    def all_families(self) -> list[Family]:
        return self.families_mix or [self.family]


def treatment_pool(style: StyleSpec) -> dict[str, float]:
    """Pesos do tratamento das cenas de imagem: os do estilo ou, sem eles, um por família com variação
    (família sem variação conta como "plain"). Só "plain" no resultado: imagem com câmera, como sempre."""
    if style.edit.treatments:
        return {k: v for k, v in style.edit.treatments.items() if v > 0}
    pool: dict[str, float] = {}
    for family in style.all_families():
        key = FAMILY_TREATMENTS.get(family, "plain")
        pool[key] = pool.get(key, 0.0) + 1.0
    return pool


# Formatos de conteúdo com alto risco de desmonetização/remoção (README §5.6).
BLOCKED_TAGS: dict[str, str] = {
    "reddit-gameplay": "histórias do Reddit sobre gameplay (conteúdo reutilizado)",
    "talking-animals": "micro-dramas com animais ou objetos falantes",
    "brainrot": "'brainrot' e personagens absurdos de IA",
    "fake-trailer": "trailers falsos",
    "ai-expert-persona": "persona de IA dando conselho de saúde, finanças ou direito",
    "fabricated-true-crime": "true crime ou história inventados apresentados como reais",
}


def policy_warnings(style: StyleSpec) -> list[str]:
    """Avisos de política de monetização para um estilo."""
    warnings: list[str] = []
    for tag in style.tags:
        if tag in BLOCKED_TAGS:
            warnings.append(f"Bloqueado para canal monetizado: {BLOCKED_TAGS[tag]}.")
    if style.art.photoreal:
        warnings.append("Arte fotorrealista: o rótulo de conteúdo sintético será ligado em todas as plataformas.")
    if Family.ai_video in style.all_families():
        warnings.append("Vídeo IA é pago e deve ficar restrito a poucos planos-chave.")
    if len(style.edit.camera) == 1 and len(style.edit.transitions) == 1:
        warnings.append("Pouca variação de câmera e transições: risco de parecer template repetido.")
    unsupported = [f for f in style.all_families() if f not in FAMILIES_V1]
    if unsupported:
        names = ", ".join(FAMILY_LABELS[f] for f in unsupported)
        warnings.append(f"Famílias ainda não implementadas no renderer (usarão o modo imagens paradas): {names}.")
    return warnings
