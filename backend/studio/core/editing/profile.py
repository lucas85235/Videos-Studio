"""Perfil de edição medido de uma referência e impressão digital de um vídeo (ADR 0004 §3.0).

`EditProfile` é o que a análise da referência (`media/refprofile`) produz: ritmo, câmera, materiais, sobreposições,
legendas, áudio, fala, humor e elementos recorrentes, com confiança e origem por campo (`meta`). Mora em
`reference_videos.stats["profile"]` (sem migração).

`EditFingerprint` tem os mesmos blocos mensuráveis (`pace/camera/materials/overlays/audio`) e sai do mesmo extrator,
seja da timeline (antes do render, `core/editing/metrics.fingerprint_from_timeline`) ou do MP4 final: a comparação com o
perfil é de igual para igual.

Só contratos: nenhum cálculo mora aqui além de normalizar a mistura de materiais.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from studio.core.project.document import PROVS, Prov

# Materiais que a visão classifica em cada plano da referência (`materials.mix`).
MATERIALS: tuple[str, ...] = (
    "gameplay",
    "official_trailer",
    "page_capture",
    "news",
    "meme_reaction",
    "facecam",
    "mascot",
    "photo_real",
    "stock_video",
    "text_card",
    "chart_map",
    "ai_illustration",
    "other",
)
# Material → nível de fonte (`Prov`). facecam e mascote não viram nível: sugerem ligar o apresentador do canal.
MATERIAL_TO_PROV: dict[str, Prov] = {
    "gameplay": "official",
    "official_trailer": "official",
    "page_capture": "capture",
    "news": "capture",
    "photo_real": "free",
    "stock_video": "stock",
    "text_card": "graphic",
    "chart_map": "graphic",
    "meme_reaction": "meme",
    "ai_illustration": "ai",
}
PRESENTER_MATERIALS = frozenset({"facecam", "mascot"})
"""Materiais que sugerem ligar o apresentador (avatar) quando passam de 10% do tempo."""

FieldOrigin = Literal["medido", "visão", "llm", "padrão", "usuário"]
SfxClass = Literal["impact", "whoosh", "pop", "riser", "scratch", "other"]


def _check_mix(value: dict[str, float], allowed: tuple[str, ...], label: str) -> dict[str, float]:
    for key, share in value.items():
        if key not in allowed:
            raise ValueError(f"{label} desconhecido: {key} (válidos: {', '.join(allowed)})")
        if share < 0:
            raise ValueError(f"fração negativa para {key}")
    return value


class FieldMeta(BaseModel):
    """Confiança (0 a 1) e origem de um campo do perfil ("pace.median_s": medido, 0.9)."""

    confidence: float = Field(default=0.0, ge=0, le=1)
    origin: FieldOrigin = "padrão"


class PaceBlock(BaseModel):
    """Ritmo de cortes. Mediana, quartis, CV e cortes por minuto são dos planos regulares (≥ 0,4 s), como as medidas
    antigas da referência; os micro-planos (< 0,4 s: stinger, flash, corte de meme) contam à parte (`micro_*`) e como
    evento visual (`events_per_min`, `max_still_s`). Ver `media/refprofile/cuts.pace`."""

    shots: int = Field(default=0, ge=0)
    """Planos regulares (sem os micro)."""
    median_s: float = Field(default=0.0, ge=0)
    mean_s: float = Field(default=0.0, ge=0)
    p10_s: float = Field(default=0.0, ge=0)
    p90_s: float = Field(default=0.0, ge=0)
    cv: float = Field(default=0.0, ge=0)
    """Coeficiente de variação das durações dos planos (desvio ÷ média)."""
    max_shot_s: float = Field(default=0.0, ge=0)
    cuts_per_min: float = Field(default=0.0, ge=0)
    cuts_per_min_windows: list[float] = []
    """Cortes por minuto em janelas de 30 s, na ordem."""
    hook_cuts_per_min: float = Field(default=0.0, ge=0)
    """Cortes por minuto nos primeiros 30 s."""
    micro_count: int = Field(default=0, ge=0)
    micro_per_min: float = Field(default=0.0, ge=0)
    max_still_s: float = Field(default=0.0, ge=0)
    """Maior trecho sem evento visual (corte, punch, inserto)."""
    events_per_min: float = Field(default=0.0, ge=0)
    """Cortes + punch + fx + insertos por minuto."""


class CameraBlock(BaseModel):
    """Movimento dentro do plano."""

    punch_per_min: float = Field(default=0.0, ge=0)
    punch_scale: float = Field(default=0.0, ge=0)
    """Salto de escala típico do punch (0,2 = 1,0→1,2)."""
    shake_per_min: float = Field(default=0.0, ge=0)
    slow_zoom_share: float = Field(default=0.0, ge=0, le=1)
    """Fração do tempo com zoom lento (rampa)."""
    zoom_rate_pct_s: float = Field(default=0.0, ge=0)
    kb_share: float = Field(default=0.0, ge=0, le=1)
    """Fração do tempo de imagem com Ken Burns ou pan (a assinatura de slideshow)."""
    static_share: float = Field(default=0.0, ge=0, le=1)
    freeze_per_min: float = Field(default=0.0, ge=0)


class MaterialsBlock(BaseModel):
    """O que aparece na tela, em fração do tempo."""

    mix: dict[str, float] = {}
    """Por material (`MATERIALS`), somando ~1."""
    prov_mix: dict[str, float] = {}
    """Por nível de fonte (`Prov`), somando ~1."""
    switch_per_min: float = Field(default=0.0, ge=0)
    """Trocas de TIPO de mídia por minuto."""
    max_same_prov_run: int = Field(default=0, ge=0)
    """Maior sequência de planos seguidos com o mesmo nível."""
    max_same_prov_run_s: float = Field(default=0.0, ge=0)

    @field_validator("mix")
    @classmethod
    def _mix(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_mix(value, MATERIALS, "material")

    @field_validator("prov_mix")
    @classmethod
    def _prov_mix(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_mix(value, PROVS, "nível de fonte")


class OverlaysBlock(BaseModel):
    """Insertos por cima do plano."""

    text_pop_per_min: float = Field(default=0.0, ge=0)
    meme_per_min: float = Field(default=0.0, ge=0)
    annot_per_min: float = Field(default=0.0, ge=0)
    """Setas, círculos e marca-texto."""
    overlay_per_min: float = Field(default=0.0, ge=0)
    """Qualquer caixa sobre o plano (mascote, meme, print) detectada por movimento local."""
    on_screen_text_share: float = Field(default=0.0, ge=0, le=1)
    text_case: Literal["upper", "lower", "mixed", ""] = ""
    text_pos: Literal["top", "center", "bottom", "mixed", ""] = ""
    font_desc: str = ""


class CaptionsBlock(BaseModel):
    """Legenda na tela da referência."""

    style: Literal["karaoke", "word", "line", "none", ""] = ""
    words_per_line: float = Field(default=0.0, ge=0)
    uppercase: bool | None = None
    position: Literal["top", "center", "bottom", ""] = ""
    font_desc: str = ""
    font_match: str = ""
    """Fonte livre (OFL) mais próxima do catálogo `assets/fonts/fonts.json`."""
    emphasis: Literal["none", "color", "pop", "emoji", ""] = ""


class AudioBlock(BaseModel):
    """Efeitos sonoros e trilha."""

    sfx_per_min: float = Field(default=0.0, ge=0)
    """SFX por minuto, sem contar os da transição."""
    sfx_on_cut_share: float = Field(default=0.0, ge=0, le=1)
    sfx_classes: dict[str, float] = {}
    """Fração dos SFX por classe (`SfxClass`)."""
    music_level_db: float | None = None
    """Nível da trilha nos respiros, relativo à fala (dB)."""
    drops_per_min: float = Field(default=0.0, ge=0)

    @field_validator("sfx_classes")
    @classmethod
    def _classes(cls, value: dict[str, float]) -> dict[str, float]:
        return _check_mix(value, SfxClass.__args__, "classe de SFX")  # type: ignore[attr-defined]


class StructureBeat(BaseModel):
    role: str
    start_s: float = Field(default=0.0, ge=0)
    end_s: float = Field(default=0.0, ge=0)


class SpeechBlock(BaseModel):
    """Narração (com transcrição): ritmo da fala, pessoa, tom e estrutura."""

    wpm: float = Field(default=0.0, ge=0)
    pauses_per_min: float = Field(default=0.0, ge=0)
    questions_per_min: float = Field(default=0.0, ge=0)
    person: Literal["1", "2", "3", ""] = ""
    """Pessoa gramatical dominante ("1": eu/a gente, "2": você)."""
    tone_tags: list[str] = []
    structure: list[StructureBeat] = []


class HumorBlock(BaseModel):
    """Padrões de humor ABSTRATOS (nunca frases da referência)."""

    patterns: list[str] = []
    gag_per_min: float = Field(default=0.0, ge=0)


class RecurringItem(BaseModel):
    """Elemento que volta muitas vezes (mascote, logo, meme), achado por hash perceptual."""

    id: str
    kind: Literal["mascot", "logo", "meme", "other"] = "other"
    count: int = Field(default=0, ge=0)
    thumb: str | None = None
    """Miniatura (relativa a VS_DATA_DIR)."""
    first_s: float = Field(default=0.0, ge=0)


class EditProfile(BaseModel):
    """Perfil de edição medido de uma (ou várias, pela mediana) referência."""

    version: int = 1
    quick: bool = True
    """Modo rápido (sem visão nem fala)."""
    duration_s: float = Field(default=0.0, ge=0)
    measured_at: str | None = None
    refs: list[str] = []
    """Ids das referências medidas (mais de uma: perfil de canal)."""
    pace: PaceBlock = PaceBlock()
    camera: CameraBlock = CameraBlock()
    materials: MaterialsBlock = MaterialsBlock()
    overlays: OverlaysBlock = OverlaysBlock()
    captions: CaptionsBlock = CaptionsBlock()
    audio: AudioBlock = AudioBlock()
    speech: SpeechBlock = SpeechBlock()
    humor: HumorBlock = HumorBlock()
    recurring: list[RecurringItem] = []
    meta: dict[str, FieldMeta] = {}
    """Por campo ("pace.median_s", "materials.mix"): confiança e origem. Campo sem entrada: padrão, confiança 0."""

    def confidence(self, field: str) -> float:
        meta = self.meta.get(field)
        return meta.confidence if meta is not None else 0.0


class EditFingerprint(BaseModel):
    """Impressão digital de um vídeo (timeline ou MP4), nos mesmos blocos e unidades do perfil."""

    source: Literal["timeline", "video"] = "timeline"
    duration_s: float = Field(default=0.0, ge=0)
    pace: PaceBlock = PaceBlock()
    camera: CameraBlock = CameraBlock()
    materials: MaterialsBlock = MaterialsBlock()
    overlays: OverlaysBlock = OverlaysBlock()
    audio: AudioBlock = AudioBlock()
    hook_prov: str | None = None
    """Nível dominante nos 3 s iniciais (None: não medido)."""
    repeat_hits: int = Field(default=0, ge=0)
    """dHash, meme ou SFX repetidos no vídeo e nos últimos vídeos do canal."""


def prov_mix_from_materials(mix: dict[str, float]) -> dict[str, float]:
    """A mistura por nível de fonte a partir da mistura por material (`MATERIAL_TO_PROV`), renormalizada para somar 1.
    facecam, mascote e "other" ficam de fora (não são nível de fonte). Vazio quando não sobra nada."""
    out: dict[str, float] = {}
    for material, share in mix.items():
        prov = MATERIAL_TO_PROV.get(material)
        if prov is not None and share > 0:
            out[prov] = out.get(prov, 0.0) + share
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total > 0 else {}


def presenter_share(mix: dict[str, float]) -> float:
    """Fração do tempo com facecam ou mascote (acima de 0,10, sugere ligar o apresentador)."""
    return sum(share for material, share in mix.items() if material in PRESENTER_MATERIALS)
