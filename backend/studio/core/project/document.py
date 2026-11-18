"""Documento de projeto: a fonte da verdade de cada vídeo (README §6.2).

Segmentos (narração) e cenas (visual) têm IDs estáveis. Os artefatos gerados (áudio, imagens,
blocos de render) não ficam aqui: ficam no cache endereçado por hash.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from studio.core.styles.schema import SCENE_KINDS, SOURCE_TIERS, THUMB_TEMPLATES
from studio.ids import new_id

SceneKind = Literal["image", "text", "title", "count", "document", "quote", "video", "stat", "compare", "chart", "map"]
assert set(SceneKind.__args__) == set(SCENE_KINDS)  # type: ignore[attr-defined]

# Cenas sem imagem própria em tela cheia (cartões desenhados pelo renderer).
CARD_KINDS = frozenset({"text", "title", "count", "document", "quote", "stat", "compare", "chart", "map"})
# Cartões de dado que guardam os números em `card.items` (o tipo só funciona com eles).
DATA_KINDS = frozenset({"stat", "compare", "chart", "map"})
ChartType = Literal["bar", "line", "pie", "donut"]

# Mídia real: foto de licença livre, cartão de notícia ou material oficial (arte, trailer, foto de imprensa).
RealKind = Literal["photo", "news", "official"]
REAL_KINDS: tuple[str, ...] = ("photo", "news", "official")
# Tipo da mídia anexada (`MediaRef.kind`): os de `RealKind` mais clipe de vídeo real (trecho de trailer/gameplay com
# proxy mudo), captura de página (com os retângulos do trecho) e meme (biblioteca do usuário). ADR 0004 §3.0.
MediaKind = Literal["photo", "news", "official", "clip", "capture", "meme"]
MEDIA_KINDS: tuple[str, ...] = ("photo", "news", "official", "clip", "capture", "meme")
MediaSource = Literal["commons", "openverse", "pexels", "link", "upload", "youtube", "library", "capture"]
# Arquivos do próprio usuário: ele declara se tem os direitos (o `editorial` não é forçado).
OWN_MEDIA_SOURCES = frozenset({"upload", "library"})
MEDIA_SOURCES: tuple[str, ...] = ("commons", "openverse", "pexels", "link", "upload", "youtube", "library", "capture")
# Nível (origem) do material na tela, do mais real ao gerado: a hierarquia T0-T7 do ADR 0004. `core.sources` decide
# o nível de cada cena (`prov_of_scene`), a fonte única usada pela timeline e pelas métricas.
Prov = Literal["own", "official", "capture", "free", "stock", "graphic", "meme", "ai"]
PROVS: tuple[str, ...] = SOURCE_TIERS
assert Prov.__args__ == SOURCE_TIERS  # type: ignore[attr-defined]
MediaFit = Literal["cover", "contain"]
# Enquadramento da imagem deitada no Short em pé: recorte em volta do foco ou imagem inteira sobre ela desfocada.
FrameMode = Literal["crop", "blur"]

# Prefixo do crédito na tela por idioma (2 letras): (foto, imagem). Idioma sem entrada usa o inglês.
CREDIT_PREFIX: dict[str, tuple[str, str]] = {
    "pt": ("Foto", "Imagem"),
    "en": ("Photo", "Image"),
    "es": ("Foto", "Imagen"),
    "fr": ("Photo", "Image"),
    "de": ("Foto", "Bild"),
    "it": ("Foto", "Immagine"),
}
_CREDIT_LEADS = ("foto:", "imagem:", "photo:", "image:", "imagen:", "bild:", "immagine:", "crédito:", "credito:",
                 "credit:", "créditos:", "fonte:", "source:")


class MediaRef(BaseModel):
    """Mídia real anexada pelo usuário: a cena mostra este arquivo no lugar da imagem de IA (o prompt fica de reserva
    e volta a valer quando a mídia sai). Guarda a origem e a licença para o crédito na descrição."""

    kind: MediaKind = "photo"
    """photo | news | official (imagem) ou clip | capture | meme (ADR 0004). Clipe e meme em vídeo tocam `video`;
    `asset` continua sendo o pôster JPEG."""
    source: MediaSource = "upload"
    asset: str | None = None
    """Original normalizado (JPEG), relativo a VS_DATA_DIR: `uploads/<projeto>/media/<sha256[:16]>.jpg`. None só no
    cartão de notícia sem imagem. O enquadramento para o vídeo sai no build (cache "media")."""
    url: str = ""
    """Endereço de onde a imagem foi baixada (vazio no arquivo enviado)."""
    page_url: str = ""
    """Página de origem: arquivo do Commons, página da foto, da notícia ou do material oficial."""
    title: str = ""
    author: str = ""
    license: str = ""
    """Nome curto da licença ("CC BY-SA 4.0", "CC0", "Public domain", "Pexels License"); vazio: direitos reservados
    ou desconhecida (material oficial, link, arquivo enviado)."""
    license_url: str = ""
    credit: str = ""
    """Texto do crédito, sem o prefixo ("Fulano / Wikimedia Commons · CC BY-SA 4.0", "Rockstar Games"). O prefixo
    ("Foto:", "Imagem:") vem do idioma do vídeo (`credit_line`). Editável pelo usuário."""
    editorial: bool = False
    """Material de terceiros com direitos (oficial, imprensa): aviso de Content ID e crédito obrigatório. Forçado em
    material de nível `official` (ver `_third_party_is_editorial`)."""
    width: int | None = None
    height: int | None = None
    """Tamanho do original normalizado (px)."""
    focus: str | None = None
    """Id de `styles.visual.FOCUS_POINTS`: onde está o sujeito na foto (centro do recorte e origem do zoom)."""
    fit: MediaFit | None = None
    """cover: preenche a tela (recorta); contain: imagem inteira sobre ela mesma desfocada; None: pela proporção."""
    outlet: str = ""
    """Veículo da notícia ("IGN Brasil")."""
    published: str = ""
    """Data da notícia: "YYYY-MM-DD", "YYYY-MM", "YYYY" ou vazio."""
    headline: dict[str, str] = {}
    """Manchete curta por idioma (citação, até ~14 palavras, sem as aspas)."""
    candidate_id: str = ""
    """Id do candidato da busca ("commons:File:X.jpg", "openverse:<uuid>", "pexels:<id>"); vazio em link e arquivo."""
    # --- ADR 0004 (material real como padrão). Todos opcionais: mídia antiga valida e sai igual na timeline. ---------
    video: str | None = None
    """Proxy de vídeo (mp4 h264 mudo), relativo a VS_DATA_DIR, para `clip` e meme animado. None: só a imagem."""
    start_s: float = Field(default=0.0, ge=0)
    """Ponto de entrada no `video`, em segundos."""
    dur_s: float | None = Field(default=None, gt=0)
    """Duração útil do trecho do `video` (None: até o fim)."""
    hold: bool = False
    """Congela no último quadro quando o trecho acaba antes da cena."""
    mute: bool = True
    """O som do `video` fica de fora (o padrão: material oficial sempre mudo)."""
    rects: list[list[float]] = []
    """Retângulos de destaque na imagem (captura de página): `[x, y, w, h]` em fração (0 a 1) da largura/altura."""
    tier: Prov | None = None
    """Nível do material (`Prov`); None: deduzido do `kind`/`source`/`license` por `core.sources.prov_of_media`."""
    pool_id: str = ""
    """Id do item do acervo do projeto (`pool.json`) de onde a mídia veio; vazio: anexada à mão."""

    @field_validator("asset", "video")
    @classmethod
    def _relative_asset(cls, value: str | None) -> str | None:
        """Só caminho relativo dentro da pasta de dados (nada de "..", unidade ou barra inicial)."""
        if value is None:
            return None
        clean = value.replace("\\", "/").strip()
        if not clean or clean.startswith("/") or ":" in clean or any(p in ("", ".", "..") for p in clean.split("/")):
            raise ValueError("caminho de mídia inválido")
        return clean

    @field_validator("rects")
    @classmethod
    def _rects(cls, value: list[list[float]]) -> list[list[float]]:
        return [_unit_rect(r) for r in value]

    @model_validator(mode="after")
    def _third_party_is_editorial(self) -> MediaRef:
        """Material oficial de terceiros (tipo `official` ou nível `official` por `core.sources.prov_of_media`: clipe
        de trailer/gameplay, link sem licença livre, YouTube) é sempre `editorial`: aviso de Content ID e crédito
        obrigatório. Exceção: arquivo do próprio usuário (`upload`, `library`), em que ele pode declarar que tem os
        direitos (`media_from_upload(editorial=False)`). Não mexe na timeline (o campo não entra nela)."""
        from studio.core.sources import prov_of_media  # sources importa este módulo

        if self.editorial or self.source in OWN_MEDIA_SOURCES:
            return self
        if self.kind == "official" or prov_of_media(self) == "official":
            self.editorial = True
        return self

    def credit_line(self, lang: str) -> str:
        """Crédito na tela no idioma do vídeo ("Foto: Fulano / Wikimedia Commons · CC BY-SA 4.0"); vazio sem crédito."""
        text = " ".join(self.credit.split())
        while text.lower().startswith(_CREDIT_LEADS):
            text = text.split(":", 1)[1].strip()
        if not text:
            return ""
        photo, image = CREDIT_PREFIX.get(lang.lower()[:2], CREDIT_PREFIX["en"])
        return f"{photo if self.kind == 'photo' else image}: {text}"

    def headline_for(self, lang: str, fallback: str | None = None) -> str:
        """Manchete da notícia no idioma (reserva: `fallback`, depois o título)."""
        return _pick_text(self.headline, lang, fallback) or " ".join(self.title.split())


def _unit_rect(rect: list[float]) -> list[float]:
    """`[x, y, w, h]` em fração do quadro (0 a 1), com largura e altura positivas e dentro da imagem."""
    if len(rect) != 4:
        raise ValueError("retângulo precisa de 4 números: x, y, w, h")
    x, y, w, h = (float(v) for v in rect)
    if not (0 <= x <= 1 and 0 <= y <= 1 and 0 < w <= 1 and 0 < h <= 1) or x + w > 1.0001 or y + h > 1.0001:
        raise ValueError(f"retângulo fora da imagem (frações de 0 a 1): {rect}")
    return [x, y, w, h]


class FoundMedia(BaseModel):
    """Candidato achado na web pela busca automática (`media_find`) para uma sugestão de mídia real. Só o endereço e
    os dados para a miniatura: o arquivo é baixado quando o usuário anexa (rota de mídia, via link)."""

    image: str
    """Endereço direto da imagem (https): a miniatura e o que se anexa em "official"/"photo"."""
    page_url: str = ""
    """Página onde a imagem está (a notícia ou a página oficial de mídia)."""
    title: str = ""
    """Descrição curta da imagem ou manchete da notícia."""
    outlet: str = ""
    """Site ou veículo ("Rockstar Games", "PC Gamer")."""
    kind: RealKind
    width: int = 0
    height: int = 0
    why: str = ""
    """Uma frase curta: por que serve para a cena."""


class RealHint(BaseModel):
    """Sugestão do storyboard: nesta batida, mídia real (anexada pelo usuário) vale mais que a imagem de IA. Nada é
    aplicado sozinho; a cena continua com o prompt de IA de reserva."""

    kind: RealKind
    query: str = ""
    """Busca em inglês para o Commons/Openverse (só faz sentido em "photo")."""
    note: str = ""
    """O que mostrar e onde achar, em português ("arte oficial da Lucia no Newswire da Rockstar")."""
    source_ref: int | None = None
    """Posição (base 1) em `ProjectDoc.sources` da fonte com link que vira o cartão de notícia."""
    found: list[FoundMedia] = []
    """Candidatos da busca automática na web (arte oficial, notícia), já conferidos (imagem grande o bastante)."""
    searched: bool = False
    """A busca automática já rodou para esta sugestão (mesmo sem achar nada): não repete sem pedido."""
    searched_at: str = ""
    """Quando a busca automática rodou por último (ISO, UTC): a busca de novo começa pelas mais antigas."""


class SceneFrame(BaseModel):
    """Reenquadramento no vídeo em pé de uma imagem de outra proporção (as 16:9 que o Short reaproveita do longo).

    O renderer recorta a janela vertical em volta do foco (`crop`) ou põe a imagem inteira sobre ela mesma ampliada e
    desfocada (`blur`); a câmera (Ken Burns, zoom no foco) parte do foco nos dois modos."""

    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)
    """Ponto de foco na imagem (0 a 1, da esquerda e de cima)."""
    mode: FrameMode | None = None
    """None: o padrão do projeto (`ProjectDoc.frame_mode`)."""
    aspect: float = Field(default=16 / 9, gt=0)
    """Largura ÷ altura da imagem de origem."""
    auto: bool = True
    """Foco sugerido pela imagem (False: escolhido pelo usuário)."""
    vertical: bool = False
    """O usuário pediu a imagem de novo já em 9:16 ("Gerar imagem vertical"): a 16:9 saiu e a prévia gera a nova."""
    source: str | None = None
    """Com `vertical`: a imagem 16:9 que saiu (para voltar a ela)."""


CLIP_SECONDS_MIN = 3
CLIP_SECONDS_MAX = 10


class ClipSpec(BaseModel):
    """Vídeo IA (image-to-video) da cena-chave: a imagem da cena é o primeiro quadro e o modelo anima por alguns
    segundos. Só vale com `Scene.kind == "video"`; tirar a marcação deixa isto guardado de reserva."""

    prompt: str = Field(default="", max_length=600)
    """Movimento em inglês ("slow push-in, smoke drifting, the lantern flickers"). Vazio: a sugestão do storyboard
    (`Scene.video_hint`) ou um movimento de câmera suave."""
    seconds: int = Field(default=5, ge=CLIP_SECONDS_MIN, le=CLIP_SECONDS_MAX)
    """Duração pedida; o modelo arredonda para a que aceita (o Kling 2.5: 5 ou 10 s)."""


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
    """Caminho relativo (dentro de VS_DATA_DIR) de um asset fixo (upload ou imagem fixada); vale para qualquer tipo."""
    seed: int = 0
    variation: int = 0
    language: str | None = None
    """Id de `styles.visual.VISUAL_LANGUAGES`. None: cena antiga, prompt composto como antes (cache intacto)."""
    shot: str | None = None
    """Id de `styles.visual.SHOTS` (plano)."""
    composition: str | None = None
    """Id de `styles.visual.COMPOSITIONS`."""
    entities: list[str] = []
    """Ids de `ArtDirection.entities` que aparecem na imagem (a aparência fixa entra no prompt)."""
    reuse_of: str | None = None
    """Id da cena cuja imagem esta usa (nenhuma imagem nova). Ver `ProjectDoc.reuse_root`."""
    media: MediaRef | None = None
    """Mídia real anexada: vale mais que a imagem de IA, o `asset` e o `reuse_of` (que ficam de reserva). None: como
    antes."""
    ai_reason: str | None = None
    """Por que esta imagem é de IA (hipótese, futuro, conceito), dito pelo storyboard (ADR 0004). None: cena antiga
    ou IA sem motivo declarado; cena de IA sem motivo vira o aviso `faltou_real` (no anexo do acervo e na revisão)."""


def _pick_text(texts: dict[str, str], lang: str, fallback: str | None) -> str:
    value = (texts.get(lang) or "").strip()
    if not value and fallback:
        value = (texts.get(fallback) or "").strip()
    return value


class CardItem(BaseModel):
    """Linha de um cartão de comparação, ponto do gráfico ou região do mapa."""

    label: dict[str, str] = {}
    value: dict[str, str] = {}
    region: str | None = None
    """Mapa: código da região (`core.regions`: "BRA", "US-TX"). Sem rótulo, a timeline usa o nome dela no idioma."""


class SceneCard(BaseModel):
    """Dados de cartão e sobreposições da cena, por idioma (números com algarismos: "475 MI", "2013")."""

    value: dict[str, str] = {}
    """Número grande do cartão `stat`."""
    label: dict[str, str] = {}
    """Rótulo curto embaixo do número (até 6 palavras)."""
    items: list[CardItem] = []
    """Itens do cartão `compare` (2 ou 3), pontos do `chart` (3 a 8, na ordem do eixo) ou regiões do `map` (1 a 6)."""
    highlight: int | None = None
    """Índice (base 0) do item em destaque em `items`."""
    chart: ChartType | None = None
    """Tipo do cartão `chart` (None: barras)."""
    headline: dict[str, str] = {}
    """Faixa no topo (qualquer tipo de cena, até 5 palavras)."""
    stamp: dict[str, str] = {}
    """Carimbo (até 2 palavras, ex.: "RECORDE")."""
    source: str | None = None
    """Fonte curta do dado (não é traduzida)."""

    def resolved(self, lang: str, fallback: str | None = None) -> dict[str, Any]:
        """Cartão no idioma `lang` (reserva: `fallback`), só com as chaves preenchidas (vai para a timeline)."""
        out: dict[str, Any] = {}
        for key in ("value", "label"):
            text = _pick_text(getattr(self, key), lang, fallback)
            if text:
                out[key] = text
        items: list[dict[str, str]] = []
        for item in self.items:
            label = _pick_text(item.label, lang, fallback)
            value = _pick_text(item.value, lang, fallback)
            if label or value or item.region:
                items.append({"label": label, "value": value, **({"region": item.region} if item.region else {})})
        if self.chart:
            out["chart"] = self.chart
        if items:
            out["items"] = items
            if self.highlight is not None and 0 <= self.highlight < len(items):
                out["highlight"] = self.highlight
        for key in ("headline", "stamp"):
            text = _pick_text(getattr(self, key), lang, fallback)
            if text:
                out[key] = text
        if self.source and self.source.strip():
            out["source"] = self.source.strip()
        return out

    def texts(self) -> list[str]:
        """Todos os textos do cartão em todos os idiomas (para a revisão procurar números)."""
        out = [*self.value.values(), *self.label.values(), *self.headline.values(), *self.stamp.values()]
        for item in self.items:
            out += [*item.label.values(), *item.value.values()]
        if self.source:
            out.append(self.source)
        return [t.strip() for t in out if t and t.strip()]


class SceneLook(BaseModel):
    """Ajustes visuais por cena que NÃO exigem nova geração de imagem (só re-render do bloco)."""

    brightness: float = Field(default=0.0, ge=-1, le=1)
    contrast: float = Field(default=0.0, ge=-1, le=1)
    saturation: float = Field(default=0.0, ge=-1, le=1)
    warmth: float = Field(default=0.0, ge=-1, le=1)
    blur: float = Field(default=0.0, ge=0, le=1)
    grade: str | None = None


PresenterMode = Literal["reacao", "explica"]
PresenterSide = Literal["auto", "left", "right"]
# Gatilho da pose na cena: uma palavra da narração, "palavra#2" (2ª ocorrência) ou "@1200" (milissegundos na cena).
PRESENTER_AT_RE = re.compile(r"^(@\d{1,7}|[^\s#@][^\s#]{0,59}(#\d{1,3})?)$")


class ScenePresenter(BaseModel):
    """Avatar do apresentador numa cena (`Scene.presenter`). Escrito pela distribuição automática (`assign`) ou pelo
    usuário (`source="manual"`, que a distribuição não troca)."""

    pose: str | None = None
    """Id da pose (ou apelido/emoção; `resolve_pose` resolve). None: a pose padrão do canal."""
    at: str | None = None
    """Palavra-gatilho: "palavra", "palavra#2" ou "@1200" (ms desde o início da cena). None: o começo da cena."""
    mode: PresenterMode = "reacao"
    side: PresenterSide = "auto"
    source: Literal["auto", "manual"] = "auto"
    off: bool = False
    """Desliga o avatar nesta cena (vale mais que a distribuição automática)."""

    @field_validator("at")
    @classmethod
    def _at(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None
        if not PRESENTER_AT_RE.match(value):
            raise ValueError(f"gatilho do avatar inválido: {value!r} (use palavra, palavra#2 ou @1200)")
        return value


# --- Camada de edição (ADR 0004 §3.0): subplanos, eventos e marcas da batida ---------------------------------------
# Gatilhos usam a mesma sintaxe do avatar (`PRESENTER_AT_RE`): "palavra", "palavra#2" ou "@1200" (ms na cena). A
# timeline resolve com `presenter.parse_at`/`trigger_index`; gatilho que não bate cai no início da cena.
FxType = Literal["punch", "shake", "zoom_rect", "freeze", "flash", "text_pop", "meme", "sfx", "highlight", "arrow",
                 "circle", "counter", "music_drop", "speed", "whip", "pip"]
FX_TYPES: tuple[str, ...] = FxType.__args__  # type: ignore[attr-defined]
FxParam = str | float | bool | list[float]
ShotSource = Literal["self", "pool", "media", "reuse", "ai"]
ShotMotion = Literal["static", "push", "drift", "pan_left", "pan_right", "ken_burns_in", "ken_burns_out", "none"]
ShotEasing = Literal["linear", "outCubic", "inOutQuad", "snap"]
BeatRole = Literal["hook", "reveal", "joke", "aside", "list", "number", "tension", "breath", "cta"]
BEAT_ROLES: tuple[str, ...] = BeatRole.__args__  # type: ignore[attr-defined]
GagPayoff = Literal["meme", "freeze", "zoom", "sfx", "text"]
GAG_PAYOFFS: tuple[str, ...] = GagPayoff.__args__  # type: ignore[attr-defined]
EditOrigin = Literal["planner", "llm", "user"]
EMPHASIS_MAX = 3
"""Palavras de ênfase por batida (`BeatMark.emphasis`)."""


def _check_at(value: str | None, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ValueError("gatilho obrigatório (use palavra, palavra#2 ou @1200)")
        return None
    value = value.strip()
    if not value:
        if required:
            raise ValueError("gatilho obrigatório (use palavra, palavra#2 ou @1200)")
        return None
    if not PRESENTER_AT_RE.match(value):
        raise ValueError(f"gatilho inválido: {value!r} (use palavra, palavra#2 ou @1200)")
    return value


class FxEvent(BaseModel):
    """Evento dentro da cena preso a uma palavra (punch, texto que pula, SFX, meme, marca-texto...). Escrito pelo
    planejador (`edit_plan`), pelo LLM ou pelo usuário (`origin`); o planejador não troca o que está `locked` ou é do
    usuário."""

    type: FxType
    at: str
    """Gatilho: "palavra", "palavra#2" (2ª ocorrência na cena) ou "@1200" (ms desde o início da cena)."""
    dur_ms: int | None = Field(default=None, ge=40, le=8000)
    """Duração; None: a padrão do tipo (no renderer)."""
    params: dict[str, FxParam] = {}
    """Parâmetros por tipo: text, scale, color, emoji, rect ([x, y, w, h] de 0 a 1), lib_id, side, payoff..."""
    origin: EditOrigin = "planner"
    locked: bool = False

    @field_validator("at")
    @classmethod
    def _at(cls, value: str) -> str:
        return _check_at(value, required=True) or ""


class ShotFraming(BaseModel):
    """Enquadramento do subplano: escala (1 = quadro inteiro) e centro (0 a 1)."""

    scale: float = Field(default=1.0, ge=1.0, le=1.8)
    x: float = Field(default=0.5, ge=0, le=1)
    y: float = Field(default=0.5, ge=0, le=1)


class SubShot(BaseModel):
    """Plano dentro da cena (vários por batida: jump cut, B-roll do acervo, reenquadramento). Sem `shots`, a cena é
    um plano só, como antes."""

    at: str | None = None
    """Gatilho do corte (sintaxe de `FxEvent.at`); None: o início da cena."""
    source: ShotSource = "self"
    """self: o visual da própria cena; pool: item do acervo (`pool_id`); media: `media`; reuse: a imagem de outra cena
    (`reuse_of`); ai: imagem de IA da cena."""
    pool_id: str | None = None
    media: MediaRef | None = None
    reuse_of: str | None = None
    framing: ShotFraming | None = None
    motion: ShotMotion | None = None
    """None: o planejador/a timeline escolhe."""
    easing: ShotEasing | None = None
    """Curva do movimento do plano (espelho de `ShotEasing` em `types.ts`). None: a da timeline."""
    strength: float | None = Field(default=None, ge=0.2, le=2.0)
    """Força relativa do movimento (1 = a do estilo; o planejador sorteia ±40%). None: a do estilo."""
    clip_in: float | None = Field(default=None, ge=0)
    """Entrada no vídeo do plano (s), vale mais que `MediaRef.start_s`."""
    speed: float = Field(default=1.0, ge=0.25, le=2.0)
    hold: bool = False
    origin: Literal["planner", "user"] = "planner"
    locked: bool = False

    @field_validator("at")
    @classmethod
    def _at(cls, value: str | None) -> str | None:
        return _check_at(value, required=False)


class GagHint(BaseModel):
    """Piada da batida: onde cai (`at`), como paga (`payoff`) e a frase do "meme sem meme" (`line`)."""

    at: str | None = None
    payoff: GagPayoff = "freeze"
    tag: str = ""
    """Etiqueta para achar meme na biblioteca do usuário ("contradição", "choque")."""
    line: str = ""
    """Texto curto que entra com o `text_pop` quando não há meme."""

    @field_validator("at")
    @classmethod
    def _at(cls, value: str | None) -> str | None:
        return _check_at(value, required=False)


class BeatMark(BaseModel):
    """Marcas de edição da batida (vêm do storyboard, achatadas no esquema do LLM): papel, palavras de ênfase,
    energia, se é séria (nada cômico) e a piada."""

    role: BeatRole = "breath"
    emphasis: list[str] = []
    """Até `EMPHASIS_MAX` palavras faladas na batida (ênfase de legenda, texto que pula, ponto de corte). O excesso
    do LLM é cortado, não recusado (a saída estrita do CLI não aceita `maxItems`)."""
    energy: int = Field(default=1, ge=0, le=3)
    serious: bool = False
    gag: GagHint | None = None

    @field_validator("emphasis", mode="before")
    @classmethod
    def _emphasis(cls, value: Any) -> Any:
        """Espaços normalizados, sem vazias nem repetidas, e no máximo `EMPHASIS_MAX` (as primeiras ficam)."""
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        clean = (" ".join(v.split()) if isinstance(v, str) else v for v in value)
        return [w for w in dict.fromkeys(clean) if w != ""][:EMPHASIS_MAX]


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
    span: int | None = Field(default=None, ge=0)
    """Nº de palavras do segmento (idioma principal, `core.text.words`) que a cena cobre. None: divisão antiga."""
    card: SceneCard | None = None
    focus: str | None = None
    """Id de `styles.visual.FOCUS_POINTS`: para onde a câmera olha (reaproveitamento e `zoom_to`)."""
    real: RealHint | None = None
    """Sugestão de mídia real do storyboard (None: nenhuma)."""
    frame: SceneFrame | None = None
    """Reenquadramento da imagem fixada no vídeo em pé (Short cortado do longo). None: como antes."""
    clip: ClipSpec | None = None
    """Parâmetros do vídeo IA (só valem com kind "video"). None: os padrões."""
    video_hint: str | None = None
    """Sugestão do storyboard: esta batida ganha com vídeo IA; o texto é o movimento, em inglês. Nada é aplicado
    sozinho (vídeo IA é pago): o usuário marca a cena."""
    presenter: ScenePresenter | None = None
    """Avatar do apresentador nesta cena (pose, palavra-gatilho, modo). None: a cena não tem avatar (ou ainda não
    passou pela distribuição automática)."""
    shots: list[SubShot] = []
    """Subplanos da cena (ADR 0004). Vazio: um plano só, como antes."""
    fx: list[FxEvent] = []
    """Eventos presos a palavras (punch, texto, SFX, meme...). Vazio: nenhum, como antes."""
    mark: BeatMark | None = None
    """Marcas de edição da batida (papel, ênfase, piada). None: cena antiga ou sem marca."""


class Entity(BaseModel):
    """Personagem, lugar ou objeto que volta em mais de uma cena, com aparência fixa."""

    id: str
    name: str = ""
    look: str = ""
    """Aparência em inglês (entra no prompt das cenas que citam a entidade)."""
    real: bool = False
    """Figura histórica ou lugar real: o prompt pode citar pelo nome. Ficção, marca, produto ou pessoa viva (o padrão):
    o nome não vai para o gerador de imagem, só a aparência."""
    character: bool = False
    """Personagem cadastrado pelo usuário (canal ou projeto, ver `Character`): a aparência e as imagens âncora vêm do
    cadastro, e o personagem é do canal (aparece de frente, sem a regra de "sem rosto" do personagem oficial)."""


# Imagens âncora: só as guardadas pelo app (o caminho vai para o gerador de imagem, que envia o arquivo).
CHARACTER_IMAGE_RE = r"^library/characters/[0-9a-f]{16}\.jpg$"
CHARACTER_MAX_IMAGES = 3


class Character(BaseModel):
    """Personagem fixo do canal ou do projeto (StyleBible): descrição visual e de 1 a 3 imagens âncora.

    O nome e os apelidos servem só para achar o personagem na narração e no storyboard: o prompt da imagem usa a
    descrição (`look`), nunca o nome (o nome puxa o personagem oficial ou é recusado pelo gerador)."""

    id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_]*$")
    name: str = Field(default="", max_length=80)
    aliases: list[str] = []
    """Outras formas de citar o personagem na narração ("o detetive", "Sam")."""
    look: str = Field(default="", max_length=400)
    """Descrição visual fixa, em inglês (entra no prompt das cenas onde ele aparece)."""
    images: list[str] = []
    """Imagens âncora (caminhos relativos em `library/characters/`), até `CHARACTER_MAX_IMAGES`."""

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, value: list[str]) -> list[str]:
        clean = [" ".join(a.split())[:60] for a in value if a and a.strip()]
        return list(dict.fromkeys(clean))[:8]

    @field_validator("images")
    @classmethod
    def _images(cls, value: list[str]) -> list[str]:
        if len(value) > CHARACTER_MAX_IMAGES:
            raise ValueError(f"no máximo {CHARACTER_MAX_IMAGES} imagens por personagem")
        for path in value:
            if not re.match(CHARACTER_IMAGE_RE, path):
                raise ValueError(f"imagem de personagem inválida: {path}")
        return list(dict.fromkeys(value))

    def names(self) -> list[str]:
        """Nome e apelidos (as formas de achar o personagem no texto)."""
        return [n for n in dict.fromkeys([self.name.strip(), *self.aliases]) if n]


# --- Avatar do apresentador (style_bible["presenter"] do canal; ProjectDoc.presenter; Scene.presenter) -----------
PRESENTER_KEY = "presenter"
"""Chave do avatar no `style_bible` do canal (como `characters`, sem migração de banco)."""
PRESENTER_POSE_ID_RE = r"^[a-z0-9][a-z0-9_\-]{0,39}$"
# Arquivos das poses: só os guardados pelo app, na biblioteca do apresentador (PNG com alfa).
PRESENTER_FILE_RE = r"^library/presenter/[A-Za-z0-9_\-]+/[A-Za-z0-9_\-.]+\.png$"


def _clean_words(value: list[str], limit: int = 12, size: int = 40) -> list[str]:
    clean = [" ".join(w.split()).lower()[:size] for w in value if w and w.strip()]
    return list(dict.fromkeys(clean))[:limit]


class PresenterPose(BaseModel):
    """Uma pose do avatar (PNG RGBA recortado). O `render_file` (900 px de altura) vai para o vídeo; a thumb usa o
    original (`file`), que aguenta o busto ampliado a 3840×2160."""

    id: str = Field(pattern=PRESENTER_POSE_ID_RE)
    file: str = Field(pattern=PRESENTER_FILE_RE)
    render_file: str | None = Field(default=None, pattern=PRESENTER_FILE_RE)
    emotions: list[str] = []
    """Emoções que a pose expressa ("choque", "surpresa"): é por elas que o storyboard e o pacote escolhem."""
    aliases: list[str] = []
    """Outros nomes da pose ("espantado")."""
    thumb_ok: bool = False
    """Serve para thumb (expressão forte e legível pequena)."""
    facing: Literal["center", "left", "right"] = "center"
    """Para onde a pose olha/aponta (de quem vê): a thumb vira (`flip`) a pose que olha para o lado errado."""

    @field_validator("emotions", "aliases")
    @classmethod
    def _words(cls, value: list[str]) -> list[str]:
        return _clean_words(value)

    def names(self) -> list[str]:
        """Id e apelidos (as formas de citar a pose)."""
        return list(dict.fromkeys([self.id, *self.aliases]))


class PresenterVideo(BaseModel):
    """Regras do avatar no vídeo (distribuição automática e render)."""

    every_s: tuple[int, int] = (60, 90)
    """Espaçamento entre aparições, em segundos (mínimo, máximo)."""
    min_gap_scenes: int = Field(default=2, ge=0, le=20)
    height_reacao: float = Field(default=0.55, gt=0, le=1.2)
    height_explica: float = Field(default=0.48, gt=0, le=1.2)
    outline_px: int = Field(default=5, ge=0, le=30)
    hide_on_kinds: list[str] = ["document", "quote", "chart", "map", "news"]
    hide_on_vertical: bool = True
    skip_first_scene: bool = True

    @field_validator("every_s")
    @classmethod
    def _every(cls, value: tuple[int, int]) -> tuple[int, int]:
        low, high = value
        if low < 0 or high < low:
            raise ValueError("every_s precisa ser (mínimo, máximo) com 0 <= mínimo <= máximo")
        return value


class Presenter(BaseModel):
    """Avatar do apresentador do canal (`style_bible["presenter"]`). Desligado por padrão: só o canal que o usuário
    ligar usa (decisão de 17/11: ligado no Lucas em Vice para thumb e vídeo longo, desligado nos Shorts)."""

    enabled: bool = False
    character_id: str | None = None
    """Liga ao `Character` do canal (aparência e âncoras de IA). None: avatar sem personagem."""
    poses: list[PresenterPose] = []
    default_pose: str = "base"
    video: PresenterVideo = PresenterVideo()
    use_in_thumb: bool = True
    """Com o avatar ligado: entra nas thumbs (templates `avatar-reage`, `numero-lista`, canto do `heroi-palavra`)."""
    use_in_video: bool = True
    """Com o avatar ligado: aparece nas cenas do vídeo longo (reações)."""
    use_in_shorts: bool = False
    """Com o avatar ligado: aparece também nos Shorts (vídeo e capa vertical). Padrão: não."""
    thumb_height: float = Field(default=1.5, gt=0, le=2.5)
    thumb_crop: Literal["busto", "meio"] = "busto"
    thumb_outline_px: int = Field(default=10, ge=0, le=40)

    @model_validator(mode="after")
    def _unique(self) -> Presenter:
        ids = [p.id for p in self.poses]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de pose repetidos")
        return self

    @classmethod
    def from_bible(cls, bible: dict[str, Any] | None) -> Presenter:
        """O avatar guardado no `style_bible` do canal; ausente ou inválido vira o padrão (desligado)."""
        raw = (bible or {}).get(PRESENTER_KEY)
        if not isinstance(raw, dict):
            return cls()
        try:
            return cls.model_validate(raw)
        except ValueError:
            return cls()

    def pose(self, pose_id: str | None) -> PresenterPose | None:
        return next((p for p in self.poses if p.id == pose_id), None)

    def thumb_poses(self) -> list[PresenterPose]:
        """As poses marcadas como boas para thumb."""
        return [p for p in self.poses if p.thumb_ok]


class PresenterOverride(BaseModel):
    """Ajuste do avatar no projeto (`ProjectDoc.presenter`). `enabled` None segue o canal."""

    enabled: bool | None = None
    thumb: bool | None = None
    """Avatar nas thumbs deste projeto: None segue o canal (`use_in_thumb`)."""
    video: bool | None = None
    """Avatar nas cenas deste projeto: None segue o canal (`use_in_video`/`use_in_shorts`)."""
    poses_off: list[str] = []
    """Poses que este projeto não usa."""


# --- Thumbs do canal (style_bible["thumbs"]) -------------------------------------------------------------------------
CHANNEL_THUMBS_KEY = "thumbs"
"""Chave das preferências de thumb no `style_bible` do canal."""
EDITORIAL_NOTICE = (
    "Thumb com material oficial (quadros de trailer, capa, logos): o risco de reivindicação de direitos fica com o "
    "canal. Personagens oficiais nunca são gerados por IA."
)


class ChannelThumbs(BaseModel):
    """Preferências de thumb do canal. `allow_editorial` None segue o estilo (`ThumbnailSpec.allow_editorial`)."""

    allow_editorial: bool | None = None
    """Libera frames e arte oficial (trailer, capa, logo) nas thumbs deste canal, com aviso na tela."""
    ai_official_characters: Literal[False] = False
    """Gerar por IA personagens oficiais (ex.: Jason/Lucia) é sempre proibido; o campo existe para deixar a regra
    explícita no dado (qualquer valor diferente de False é recusado)."""

    @classmethod
    def from_bible(cls, bible: dict[str, Any] | None) -> ChannelThumbs:
        raw = (bible or {}).get(CHANNEL_THUMBS_KEY)
        if not isinstance(raw, dict):
            return cls()
        try:
            return cls.model_validate(raw)
        except ValueError:
            return cls()


def effective_allow_editorial(style_allow: bool, bible: dict[str, Any] | None) -> bool:
    """Material oficial na thumb: o valor do canal (`style_bible["thumbs"]`) vale mais que o do estilo."""
    channel = ChannelThumbs.from_bible(bible).allow_editorial
    return style_allow if channel is None else channel


class ArtDirection(BaseModel):
    """Direção de arte do vídeo, decidida pelo assunto (o estilo é só referência)."""

    genre: str = ""
    """Id de `styles.visual.GENRES` (ou vazio)."""
    approach: Literal["literal", "misto", "alegorico"] = "literal"
    world: str = ""
    """O mundo concreto das imagens (em inglês)."""
    treatment: str = ""
    """Luz, contraste e textura (em inglês, sem objetos)."""
    palette: list[str] = []
    languages: list[str] = []
    """Ids de `styles.visual.VISUAL_LANGUAGES`, o primeiro dominante."""
    entities: list[Entity] = []
    avoid: list[str] = []
    """Objetos e clichês que não pertencem ao assunto (em inglês)."""
    grade: str | None = None
    """Id de `GRADE_PRESETS` para trocar a gradação do estilo; None mantém a do estilo."""
    style_fit: Literal["ok", "fraco", "errado"] = "ok"
    style_note: str = ""
    suggested_style: str | None = None
    hook: str = ""
    """O que o primeiro quadro mostra."""
    source: Literal["ai", "user"] = "ai"
    """Com "user" (definida pelo usuário), o storyboard não muda a direção."""

    def entity(self, entity_id: str) -> Entity | None:
        return next((e for e in self.entities if e.id == entity_id), None)


class MusicCue(BaseModel):
    id: str = Field(default_factory=lambda: new_id("mus", 6))
    start_segment: str | None = None
    mood: str | None = None
    volume_db: float | None = None
    enabled: bool = True
    track: str | None = None  # faixa fixada da biblioteca do usuário (caminho relativo em library/music)
    loop: bool = False  # "só a batida, em loop no compasso": toca só o trecho curado da faixa da biblioteca


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


# --- Pacote: título e thumb antes do roteiro ---------------------------------------------------------------------
ThumbTemplate = Literal["heroi-palavra", "avatar-reage", "lugar-mapa", "numero-lista", "versus", "livre"]
assert set(ThumbTemplate.__args__) == set(THUMB_TEMPLATES)  # type: ignore[attr-defined]
PACKAGE_TITLE_MAX = 90
THUMB_WORDS_MAX = 3
PACKAGE_STATUS = "package_review"
"""Status do projeto parado na escolha do pacote (entre `draft` e `script_review`)."""


class BgLayer(BaseModel):
    """Fundo da thumb. `ref` depende do `kind`: "frame" usa `frame:<trailer>@<t>` ou o id do quadro da biblioteca,
    "media" o id da mídia do projeto, "upload" o caminho do arquivo, "gradient" o nome/cores, "ai" o prompt."""

    kind: Literal["frame", "media", "upload", "gradient", "ai"] = "gradient"
    ref: str | None = None
    t: float | None = Field(default=None, ge=0)
    """Segundos no trailer (frames de vídeo)."""
    blur: int = Field(default=0, ge=0, le=60)
    darken: float = Field(default=0.0, ge=0, le=1)
    zoom: float = Field(default=1.0, ge=1.0, le=4.0)
    focus_x: float = Field(default=0.5, ge=0, le=1)
    """Onde fica o rosto/assunto no quadro (fração da largura), para o enquadramento."""


class CutoutLayer(BaseModel):
    """Figura recortada (PNG com alfa) sobre o fundo."""

    asset: str
    x: float = Field(default=0.7, ge=-0.5, le=1.5)
    y: float = Field(default=1.0, ge=-0.5, le=1.5)
    h: float = Field(default=0.9, gt=0, le=2.5)
    """Altura em fração da altura do quadro (> 1 vaza por baixo)."""
    flip: bool = False


class PresenterThumbLayer(BaseModel):
    """Avatar do apresentador na thumb (PNG original da pose)."""

    pose: str
    side: Literal["left", "right"] = "right"
    crop: Literal["busto", "meio"] = "busto"
    h: float = Field(default=1.5, gt=0, le=2.5)
    outline_px: int = Field(default=10, ge=0, le=40)
    flip: bool = False


class TextLayer(BaseModel):
    words: str = ""
    """Texto principal (até 3 palavras no longo; a checagem avisa, não corta)."""
    fill: Literal["yellow", "vice", "white"] = "yellow"
    pos: Literal["top", "left", "center"] = "left"
    size: float = Field(default=0.3, ge=0, le=1)


class CardLayer(BaseModel):
    asset: str | None = None
    label: str = ""


class ArrowLayer(BaseModel):
    """Seta (coordenadas em fração do quadro)."""

    from_x: float = Field(default=0.35, ge=0, le=1)
    from_y: float = Field(default=0.55, ge=0, le=1)
    to_x: float = Field(default=0.55, ge=0, le=1)
    to_y: float = Field(default=0.45, ge=0, le=1)
    color: str = "yellow"


class ThumbLayers(BaseModel):
    """Descrição declarativa de uma thumb, consumida pelo compositor (`media/thumb_compose.py`). Ordem das camadas:
    fundo, VI gigante, personagens, avatar, texto/pílula/cartões, seta, logo."""

    template: ThumbTemplate = "heroi-palavra"
    background: BgLayer = BgLayer()
    big_vi: bool = False
    characters: list[CutoutLayer] = []
    presenter: PresenterThumbLayer | None = None
    text: TextLayer | None = None
    pill: str | None = None
    """Pílula secundária ("SEM SPOILER", "ANTES DE JOGAR")."""
    cards: list[CardLayer] = Field(default=[], max_length=3)
    logo: Literal["corner", "none"] = "corner"
    arrow: ArrowLayer | None = None
    strike: bool = False
    """Risco vermelho sobre a palavra."""


class PackageCandidate(BaseModel):
    """Um par título + thumb candidato. A `promise` vira o contrato do gancho do roteiro."""

    id: str = Field(default_factory=lambda: new_id("pkc", 8))
    title: str = ""
    thumb_text: str = ""
    promise: str = ""
    concept: str = ""
    layers: ThumbLayers = ThumbLayers()
    score: dict[str, Any] = {}
    """Saída da checagem de legibilidade (`thumb_check`): métricas e `warnings`."""
    image: str | None = None
    """Caminho relativo da prévia renderizada (1280×720 no longo, 1080×1920 no Short)."""
    approved: bool = False
    """Entra no A/B final."""
    source: Literal["auto", "manual"] = "auto"

    @field_validator("title", "thumb_text", "promise", "concept")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    def thumb_words(self) -> int:
        return len(self.thumb_text.split())


class PackageDoc(BaseModel):
    candidates: list[PackageCandidate] = []
    chosen_id: str | None = None
    locked: bool = False
    """True depois de aprovado: roteiro e metadata não trocam o título nem o texto da thumb."""
    references: list[str] = []
    """Ids de vídeos da pesquisa/radar usados como referência."""
    generated_at: str | None = None

    @model_validator(mode="after")
    def _check(self) -> PackageDoc:
        ids = [c.id for c in self.candidates]
        if len(set(ids)) != len(ids):
            raise ValueError("ids de candidato repetidos")
        if self.chosen_id is not None and self.chosen_id not in ids:
            # Escolhido apagado: perde a escolha (e a trava, que não vale sem escolhido).
            self.chosen_id = None
            self.locked = False
        return self

    def candidate(self, candidate_id: str) -> PackageCandidate | None:
        return next((c for c in self.candidates if c.id == candidate_id), None)

    def chosen(self) -> PackageCandidate | None:
        return self.candidate(self.chosen_id) if self.chosen_id else None

    def approved(self) -> list[PackageCandidate]:
        """Os aprovados para o A/B, o escolhido primeiro."""
        chosen = self.chosen()
        rest = [c for c in self.candidates if c.approved and (chosen is None or c.id != chosen.id)]
        return ([chosen] if chosen else []) + rest


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
    art: ArtDirection | None = None
    """Direção de arte do vídeo (vem do storyboard). None: projeto antigo, imagens compostas como antes."""
    characters: list[Character] = []
    """Personagens fixos do projeto (somam aos do canal; o mesmo id troca o do canal)."""
    storyboard_approved: bool = False
    """Cenas aprovadas no portão `gates.storyboard` (volta a False quando o storyboard roda de novo)."""
    frame_mode: FrameMode = "crop"
    """Modo padrão do reenquadramento das cenas com `Scene.frame` (o Short escolhe; a cena pode trocar)."""
    package: PackageDoc = PackageDoc()
    """Título e thumb escolhidos antes do roteiro. Vazio: projeto antigo ou sem pacote (nada muda)."""
    presenter: PresenterOverride = PresenterOverride()
    """Avatar neste projeto: padrão do canal, ligado ou desligado."""

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

    def chosen_package(self) -> PackageCandidate | None:
        """O candidato escolhido do pacote (None: sem pacote ou sem escolha)."""
        return self.package.chosen()

    def locked_package(self) -> PackageCandidate | None:
        """O candidato escolhido com o pacote travado (aprovado); None sem pacote, sem escolha ou destravado. A capa
        do render e as miniaturas do A/B só seguem o pacote travado: com ele destravado, o título dos metadados é
        outro e o par título + thumb se desfaz."""
        return self.chosen_package() if self.package.locked else None

    def effective_title(self) -> str:
        """O título do candidato escolhido quando o pacote está travado; senão `title`."""
        chosen = self.chosen_package()
        if self.package.locked and chosen is not None and chosen.title:
            return chosen.title
        return self.title

    def scenes_of(self, segment_id: str) -> list[Scene]:
        return sorted((s for s in self.scenes if s.segment_id == segment_id), key=lambda s: s.part)

    def reuse_root(self, scene: Scene, max_depth: int = 3) -> Scene | None:
        """A cena dona da imagem que `scene` mostra: ela mesma, ou o fim da cadeia de `reuse_of`.

        None quando a referência está solta (cena apagada), em ciclo ou com mais de `max_depth` saltos:
        a cena fica sem imagem e o renderer usa o fundo animado. Cena com mídia real é dona da imagem que mostra: a
        cadeia para nela (o `reuse_of` dela fica de reserva para quando a mídia sair).
        """
        current = scene
        seen = {scene.id}
        for _ in range(max_depth + 1):
            target = current.visual.reuse_of
            if not target or current.visual.media is not None:
                return current
            nxt = next((s for s in self.scenes if s.id == target), None)
            if nxt is None or nxt.id in seen:
                return None
            seen.add(nxt.id)
            current = nxt
        return None

    def shown_media(self, scene: Scene) -> MediaRef | None:
        """Mídia real que a cena mostra: a dela, ou a da cena que ela reaproveita (imagem fixada na própria cena vale
        mais que a da raiz, como no build). None: imagem de IA, cartão ou fundo animado."""
        if scene.visual.media is not None:
            return scene.visual.media
        if scene.visual.asset or not scene.visual.reuse_of:
            return None
        root = self.reuse_root(scene)
        return root.visual.media if root is not None else None

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
                card = scene.card.resolved(lang, self.primary_language) if scene.card else {}
                desc = scene.visual.prompt or scene.overlay_text.get(lang, "") or " · ".join(
                    str(card[k]) for k in ("value", "label", "headline") if k in card
                )
                extra = []
                if scene.visual.reuse_of:
                    extra.append(f"reaproveita={scene.visual.reuse_of}")
                if scene.visual.media is not None:
                    extra.append(f"mídia real={scene.visual.media.kind}")
                elif scene.real is not None:
                    extra.append(f"sugestão de mídia real={scene.real.kind}")
                if scene.kind == "video":
                    extra.append("vídeo IA")
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
