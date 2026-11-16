"""Documento de projeto: a fonte da verdade de cada vídeo (README §6.2).

Segmentos (narração) e cenas (visual) têm IDs estáveis. Os artefatos gerados (áudio, imagens,
blocos de render) não ficam aqui: ficam no cache endereçado por hash.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from studio.core.styles.schema import SCENE_KINDS
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
MediaSource = Literal["commons", "openverse", "pexels", "link", "upload"]
MEDIA_SOURCES: tuple[str, ...] = ("commons", "openverse", "pexels", "link", "upload")
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
    e volta a valer quando a mídia sai). Guarda a origem e a licença para o crédito na tela e na descrição."""

    kind: RealKind = "photo"
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
    """Material de terceiros com direitos (oficial, imprensa): aviso de Content ID e crédito obrigatório."""
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

    @field_validator("asset")
    @classmethod
    def _relative_asset(cls, value: str | None) -> str | None:
        """Só caminho relativo dentro da pasta de dados (nada de "..", unidade ou barra inicial)."""
        if value is None:
            return None
        clean = value.replace("\\", "/").strip()
        if not clean or clean.startswith("/") or ":" in clean or any(p in ("", ".", "..") for p in clean.split("/")):
            raise ValueError("caminho de mídia inválido")
        return clean

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


class Entity(BaseModel):
    """Personagem, lugar ou objeto que volta em mais de uma cena, com aparência fixa."""

    id: str
    name: str = ""
    look: str = ""
    """Aparência em inglês (entra no prompt das cenas que citam a entidade)."""
    real: bool = False
    """Figura histórica ou lugar real: o prompt pode citar pelo nome. Ficção, marca, produto ou pessoa viva (o padrão):
    o nome não vai para o gerador de imagem, só a aparência."""


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
    storyboard_approved: bool = False
    """Cenas aprovadas no portão `gates.storyboard` (volta a False quando o storyboard roda de novo)."""
    frame_mode: FrameMode = "crop"
    """Modo padrão do reenquadramento das cenas com `Scene.frame` (o Short escolhe; a cena pode trocar)."""

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
