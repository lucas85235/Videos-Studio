"""Acervo de material real antes do storyboard e anexo automático do escolhido (ADR 0004 §3.4).

`gather(doc, style)` junta, ANTES do LLM do storyboard, o material real que o vídeo pode mostrar. Cada coletor é
independente: a falha de um vira aviso em `Pool.warnings` e os outros seguem.

1. Material próprio (`own`): mídia anexada à mão no projeto (também a de coletas anteriores, enquanto o arquivo
   existir), arquivos em `uploads/<projeto>/acervo/` (enviados pela tela do Acervo) e a biblioteca do canal
   (thumbkit: quadros e objetos sem transparência; os vídeos do kit viram trechos oficiais).
2. Capturas de página (`capture`): as provas já feitas no projeto e, na coleta completa com o Playwright, as fontes
   do roteiro (`doc.sources`) — sempre a imagem LIMPA, com os retângulos dos trechos para o renderer animar o destaque.
3. Trechos de links oficiais dados pelo usuário (`settings.pool_links`), cortados em planos por `media/clips.py`.
4. Fotos livres (`media_search`: Commons, Openverse, Pexels) e vídeos do Pexels, por tópico do roteiro (só online).
5. Arte oficial e notícias (`media_find`): uma chamada por vídeo, até 15 tópicos (só com o Claude Code buscando na web).
6. Anti-repetição: item parecido (dHash) ou o mesmo (`source_id`; numa imagem, também o endereço do arquivo) usado
   nos últimos 10 vídeos do canal perde nota. Trecho de vídeo conta pelo plano, nunca pelo link inteiro.

Duas coletas: a RÁPIDA (padrão, dentro do job do storyboard) só usa o que já está baixado e analisado, sem yt-dlp,
corte de vídeo novo, navegador nem chamada paga; a COMPLETA (`full=True`, job `media_pool`, botão "Atualizar acervo",
que roda sozinho quando o usuário dá links ou envia arquivos) faz tudo, com progresso e cancelamento.

`attach_chosen(doc, pool)` anexa sozinho o item que a cena cita em `visual.media.pool_id`, seja a `MediaRef`
pendente (`pending_ref`) ou uma que aponte para fora da pasta do projeto (`needs_attach`): o trecho vira proxy mudo
do plano, a imagem é normalizada e copiada. Item que não baixa cai na reserva (`fallbacks`); sem reserva, a cena
volta à imagem de IA, repete a mídia real vizinha ou fica sem imagem, e o aviso diz qual. O achado `faltou_real` é
da conferência do storyboard (`missing_real` fica de helper). `prompt_block(pool)` dá as linhas do acervo para o
pedido do storyboard.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from studio.config import get_settings
from studio.core.project.document import MediaRef, ProjectDoc, Scene
from studio.core.project.pool import POOL_ID_RE, Pool, PoolItem, load_pool
from studio.core.sources import FALLBACK, prov_of_scene
from studio.core.styles.schema import StyleSpec
from studio.workers.jobs import JobContext, job

log = logging.getLogger(__name__)

LINKS_KEY = "pool_links"
"""Chave em `projects.settings` com os links oficiais dados pelo usuário: `[{url, label, tier}]`."""
MAX_LINKS = 12
OWN_DIR = "acervo"
"""Pasta do projeto onde o usuário larga material próprio: `uploads/<projeto>/acervo/`."""
MAX_TOPICS = 15
MAX_PHOTO_TOPICS = 6
MAX_VIDEO_TOPICS = 4
MAX_SOURCE_CAPTURES = 8
"""Fontes do roteiro capturadas por coleta completa (o vídeo longo cita 6 a 8 matérias; com 4, as do fim ficavam
sem prova na tela)."""
MAX_KIT_VIDEOS = 4
MAX_ITEMS = 240
MAX_PER_TOPIC = 30
"""Por tópico, fora os níveis reservados (foto livre, banco, meme...)."""
RESERVED_TIERS = ("own", "official", "capture")
"""Os níveis mais reais têm lugar garantido no corte do acervo e no pedido do storyboard."""
RESERVED_MAX = 180
RESERVED_PER_TOPIC = 60
MAX_ID_REGISTRY = 5000
MAX_FALLBACK_TRIES = 3
"""Reservas tentadas por cena quando o item escolhido não anexa."""
MAX_ATTACH_DOWNLOADS = 24
"""Piso de downloads por anexo (rede ruim não trava o storyboard por minutos)."""
ATTACH_DOWNLOAD_SLACK = 8
"""Folga para as reservas além de um download por cena pendente."""
MAX_ATTACH_DOWNLOADS_CAP = 160
"""Teto absoluto: um vídeo longo de 90 cenas cabe, um roteiro gigante não baixa sem fim."""


def attach_download_budget(pending: int) -> int:
    """Downloads permitidos num anexo com `pending` cenas esperando mídia do acervo. O piso fixo de 24 deixava o
    vídeo longo (dezenas de cenas com foto ou trecho de banco) cair na imagem de IA a partir da 25ª cena."""
    return min(MAX_ATTACH_DOWNLOADS_CAP, max(MAX_ATTACH_DOWNLOADS, pending + ATTACH_DOWNLOAD_SLACK))
VIDEO_WALL_CLOCK_S = 180.0
NOVELTY_WINDOW = 10
NOVELTY_PENALTY = 0.5
FIND_CACHE_KIND = "pool_find"
OWN_IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp"})
OWN_VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})
VIDEO_MAX_BYTES = 120_000_000
OWN_VIDEO_MAX_BYTES = 2_000_000_000
"""Vídeo próprio enviado pela tela do Acervo."""

TIER_SCORE: dict[str, float] = {"own": 1.0, "official": 0.9, "capture": 0.85, "free": 0.7, "stock": 0.55,
                                "meme": 0.45, "graphic": 0.3, "ai": 0.0}

TOPIC_DOC = "Já no projeto"
TOPIC_FILES = "Arquivos do projeto"
TOPIC_KIT = "Biblioteca do canal"
TOPIC_SOURCES = "Fontes do roteiro"
TOPIC_LINKS = "Links oficiais"

# Origem do item (prefixo do `source_id`) → `MediaRef.source`.
_PREFIX_SOURCE = {"acervo": "upload", "kit": "library", "kitvideo": "library", "yt": "youtube", "cap": "capture",
                  "capurl": "capture", "commons": "commons", "openverse": "openverse", "pexels": "pexels",
                  "pexels-video": "pexels", "found": "link", "own": "upload"}


# --------------------------------------------------------------------------------------------
# Tópicos do roteiro
# --------------------------------------------------------------------------------------------


@dataclass
class Topic:
    id: str
    label: str
    query: str


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text if not unicodedata.combining(ch)).split())


_TOKEN = re.compile(r"[\wÀ-ÿ'’\-]+|[.!?;:]")
_CONNECTORS = frozenset({"de", "da", "do", "das", "dos", "of", "the", "del", "la"})
# Palavras que só estão em maiúscula por abrir a frase (artigo, preposição, pronome): não são nome próprio.
_LEADING = frozenset(
    "o a os as um uma uns umas e em no na nos nas de do da dos das se mas mais ele ela eles elas isso isto esse essa "
    "este esta aquele aquela quando onde por para com como que foi era é sim não já hoje agora depois antes então "
    "the a an and in on at it this that these those when where el la los las un una y en".split()
)


def _proper_runs(text: str) -> list[tuple[str, bool]]:
    """Sequências de palavras com inicial maiúscula (com números colados, "GTA 6", e conectores no meio, "Rio de
    Janeiro"). Devolve (sequência, começa a frase)."""
    runs: list[tuple[str, bool]] = []
    current: list[str] = []
    start_of_sentence = True
    run_at_start = False

    def close() -> None:
        at_start = run_at_start
        while current and current[0].lower() in _LEADING:  # "O GTA 6", "A Rockstar": o artigo da frase sai
            current.pop(0)
            at_start = False
        while current and current[-1].lower() in _CONNECTORS:
            current.pop()
        if current:
            runs.append((" ".join(current), at_start))
        current.clear()

    for token in _TOKEN.findall(text):
        if token in ".!?;:":
            close()
            start_of_sentence = True
            continue
        proper = token[:1].isupper() or (token.isdigit() and bool(current)) or (
            len(token) > 1 and token.isupper())
        if proper:
            if not current:
                run_at_start = start_of_sentence
            current.append(token)
        elif current and token.lower() in _CONNECTORS:
            current.append(token)
        else:
            close()
        start_of_sentence = False
    close()
    return runs


def topics_of(doc: ProjectDoc, max_topics: int = MAX_TOPICS) -> list[Topic]:
    """Até `max_topics` tópicos do roteiro, sem LLM: entidades reais da direção de arte, nomes próprios que o roteiro
    repete (os mais citados primeiro) e, se faltar, o tema do vídeo."""
    labels: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        clean = " ".join(label.split()).strip(" -'’")[:80]
        key = fold(clean)
        if len(key) >= 2 and key not in seen and len(labels) < max_topics:
            seen.add(key)
            labels.append(clean)

    if doc.art is not None:
        for entity in doc.art.entities:
            if entity.real and (entity.name or "").strip():
                add(entity.name)
    counts: dict[str, int] = {}
    first: dict[str, int] = {}
    shown: dict[str, str] = {}
    mid_sentence: set[str] = set()
    order = 0
    for seg in doc.segments:
        for run, at_start in _proper_runs(seg.text_for(doc.primary_language)):
            key = fold(run)
            if not key:
                continue
            counts[key] = counts.get(key, 0) + 1
            if key not in first:
                first[key], shown[key] = order, run
                order += 1
            if not at_start or len(run.split()) > 1 or any(c.isdigit() for c in run) or run.isupper():
                mid_sentence.add(key)
    ranked = sorted((k for k in counts if k in mid_sentence), key=lambda k: (-counts[k], first[k]))
    for key in ranked:
        add(shown[key])
    if len(labels) < 3:
        for text in (doc.topic, doc.title):
            if text and text.strip():
                add(text[:80])
    return [Topic(id=f"t{i}", label=label, query=label) for i, label in enumerate(labels, 1)]


def _topic_for(texts: Iterable[str], topics: list[Topic], default: str) -> str:
    """O tópico que aparece no texto (tags, título); senão `default`."""
    blob = " " + " ".join(fold(t) for t in texts if t) + " "
    for topic in topics:
        key = fold(topic.label)
        if key and f" {key} " in blob:
            return topic.label
    return default


# --------------------------------------------------------------------------------------------
# Coleta
# --------------------------------------------------------------------------------------------


@dataclass
class _Ctx:
    doc: ProjectDoc
    style: StyleSpec
    project_id: str
    topics: list[Topic]
    settings: dict[str, Any]
    offline: bool
    """`VS_OFFLINE`: só o material local. Um `http`/`open_page`/`runner` injetado vale só para o coletor que o usa."""
    full: bool = False
    """Coleta completa (job `media_pool`): baixa links, corta vídeos novos, captura páginas e faz a busca web paga.
    Rápida (dentro do storyboard): só o que já está baixado e analisado, e as buscas de foto com cache."""
    previous: Pool = field(default_factory=Pool)
    http: Any = None
    open_page: Any = None
    runner: Any = None
    job_id: str | None = None
    progress: Callable[[float, str], None] = lambda f, m: None
    cancelled: Callable[[], bool] = lambda: False
    notes: list[str] = field(default_factory=list)
    unanalyzed: int = 0
    """Vídeos locais ainda não cortados em planos (coleta rápida: ficam para o "Atualizar acervo")."""

    @property
    def width(self) -> int:
        return int(self.style.format.resolution.lower().split("x")[0])

    @property
    def height(self) -> int:
        return int(self.style.format.resolution.lower().split("x")[1])

    @property
    def aspect(self) -> float:
        return self.width / self.height if self.height else 16 / 9

    @property
    def orientation(self) -> str:
        return "portrait" if self.height > self.width else "landscape" if self.width > self.height else "square"

    @property
    def max_clip_s(self) -> float:
        return float(self.style.art.sources.official_max_clip_s)


def _data_rel(path: Path) -> str | None:
    from studio.media import clips

    return clips.rel_or_none(path)


def _image_size(path: Path) -> tuple[int, int]:
    try:
        with Image.open(path) as im:
            return int(im.width), int(im.height)
    except Exception:  # noqa: BLE001 - arquivo ruim: sem tamanho
        return 0, 0


def _analyze(ctx: _Ctx, path: Path, label: str) -> list[Any]:
    """Os planos do vídeo (pôster e descrição). Na coleta rápida, só a análise que já existe: vídeo novo conta em
    `ctx.unanalyzed` e fica para a coleta completa."""
    from studio.media import clips

    shots = clips.analyze(path, max_clip_s=ctx.max_clip_s, label=label, cached_only=not ctx.full,
                          cancelled=ctx.cancelled)
    if shots is None:
        ctx.unanalyzed += 1
        return []
    return shots
def clip_source_id(prefix: str, key: str, a: float, dur: float | None) -> str:
    """O `source_id` do trecho pelo conteúdo (`yt:<chave>#<início_ms>-<duração_ms>`), não pela posição no corte: um
    corte refeito (outra `clips.ANALYSIS_VERSION`, outro teto de duração) dá id novo ao trecho que mudou, e o subplano
    que citava o antigo cai no aviso de item ausente em vez de mostrar outro pedaço do vídeo em silêncio."""
    return f"{prefix}:{key}#{int(round(float(a) * 1000))}-{int(round(float(dur or 0) * 1000))}"


def _clip_items(shots: list[Any], *, tier: str, prefix: str, key: str, topic: str, url: str = "",
                credit: str = "", editorial: bool = False, license: str = "") -> list[dict[str, Any]]:
    """Um item por plano: `path` é o vídeo de origem (quando fica na pasta de dados) e o plano vai em `start_s` e
    `dur_s`; o proxy só sai no anexo, para o plano escolhido."""
    return [{"tier": tier, "media": "clip", "desc": s.desc, "path": s.src or None, "poster": s.poster, "url": url,
             "page_url": url, "start_s": s.a, "dur_s": s.dur, "w": s.width, "h": s.height, "credit": credit,
             "editorial": editorial, "license": license, "source_id": clip_source_id(prefix, key, s.a, s.dur),
             "topic": topic, "tags": [s.material] if s.material else []}
            for s in shots]


def _own_file_item(item: PoolItem, project_id: str) -> dict[str, Any] | None:
    """Item `file:` da coleta anterior que ainda vale: o arquivo continua na pasta do projeto."""
    data_dir = get_settings().data_dir
    if not item.path or not item.path.startswith(f"uploads/{project_id}/") or not (data_dir / item.path).is_file():
        return None
    return item.model_dump(exclude={"id", "score", "dhash"})


def _collect_own(ctx: _Ctx) -> list[dict[str, Any]]:
    """Mídia anexada à mão no projeto (também a de coletas anteriores, cujo arquivo continua no projeto, mesmo que o
    storyboard novo não a tenha escolhido) e arquivos largados em `uploads/<projeto>/acervo/`."""
    from studio.core.sources import prov_of_media

    out: list[dict[str, Any]] = []
    for scene in ctx.doc.ordered_scenes():
        media = scene.visual.media
        if media is None or media.pool_id or not (media.asset or media.video):
            continue  # o que veio do acervo volta pelo próprio coletor
        kind = {"clip": "clip", "capture": "capture", "meme": "meme"}.get(media.kind, "image")
        out.append({"tier": prov_of_media(media), "media": kind,
                    "desc": media.title or media.headline_for(ctx.doc.primary_language) or media.credit or "mídia do projeto",
                    "path": media.video or media.asset, "poster": media.asset if media.video else None,
                    "url": media.url, "page_url": media.page_url, "start_s": media.start_s, "dur_s": media.dur_s,
                    "rects": media.rects, "w": media.width or 0, "h": media.height or 0, "license": media.license,
                    "credit": media.credit, "editorial": media.editorial,
                    "source_id": f"file:{media.video or media.asset}", "topic": TOPIC_DOC})
    for old in ctx.previous.items:
        if old.source_id.startswith("file:"):
            kept = _own_file_item(old, ctx.project_id)
            if kept is not None:
                out.append(kept)
    folder = get_settings().uploads_dir / ctx.project_id / OWN_DIR
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            suffix = path.suffix.lower()
            rel = _data_rel(path)
            if not path.is_file() or rel is None or path.name.startswith("."):
                continue
            topic = _topic_for([path.stem], ctx.topics, TOPIC_FILES)
            if suffix in OWN_IMAGE_EXTS:
                w, h = _image_size(path)
                out.append({"tier": "own", "media": "image", "desc": path.stem.replace("_", " "), "path": rel,
                            "w": w, "h": h, "source_id": f"acervo:{path.name}", "topic": topic})
            elif suffix in OWN_VIDEO_EXTS:
                shots = _analyze(ctx, path, path.stem.replace("_", " "))
                out += _clip_items(shots, tier="own", prefix="acervo", key=path.name, topic=topic)
    return out


def _collect_kit(ctx: _Ctx) -> list[dict[str, Any]]:
    """Biblioteca do canal (thumbkit): quadros e objetos sem transparência como imagem (o recorte com alfa da thumb
    vira colagem malfeita em tela cheia); os vídeos registrados viram trechos."""
    if not ctx.doc.channel_id:
        return []
    from studio.media import thumb_assets

    try:
        kit = thumb_assets.thumbkit_dir(ctx.doc.channel_id)
    except ValueError:
        return []
    if not kit.is_dir():
        return []
    manifest = thumb_assets.load_manifest(kit, ctx.doc.channel_id)
    out: list[dict[str, Any]] = []
    for item in manifest.get("items") or []:
        if item.get("kind") not in ("frame", "object") or not item.get("file"):
            continue
        path = kit / str(item["file"])
        rel = _data_rel(path)
        if rel is None or not path.is_file():
            continue
        try:
            if thumb_assets.has_alpha(path):
                continue
        except Exception:  # noqa: BLE001 - imagem ilegível: fora
            continue
        tags = [str(t) for t in item.get("tags") or []]
        w, h = _image_size(path)
        editorial = bool(item.get("editorial"))
        out.append({"tier": "official" if editorial else "own", "media": "image",
                    "desc": " · ".join([str(item.get("id"))] + tags[:3]), "path": rel, "w": w, "h": h,
                    "credit": str(item.get("credit") or ""), "editorial": editorial, "tags": tags[:8],
                    "source_id": f"kit:{item['id']}", "topic": _topic_for([*tags, str(item["id"])], ctx.topics, TOPIC_KIT)})
    for name, raw in list((manifest.get("videos") or {}).items())[:MAX_KIT_VIDEOS]:
        if ctx.cancelled():
            break
        path = kit_video_path(kit, raw)
        if path is None:
            continue
        label = str(name).replace("_", " ")
        shots = _analyze(ctx, path, label)
        out += _clip_items(shots, tier="official", prefix="kitvideo", key=str(name), editorial=True, credit=label,
                           topic=_topic_for([label], ctx.topics, TOPIC_KIT))
    return out


def kit_video_path(kit: Path, raw: object) -> Path | None:
    path = Path(str(raw))
    if not path.is_absolute():
        path = kit / path
    return path if path.is_file() else None


def _capture_item(entry: dict[str, Any], source_id: str, topic: str) -> dict[str, Any]:
    desc = entry.get("title") or entry.get("text") or entry.get("domain") or "página"
    if entry.get("text") and entry.get("title"):
        desc = f"{entry['title'][:70]} — \"{entry['text'][:60]}\""
    return {"tier": "capture", "media": "capture", "desc": desc, "path": entry["asset"], "url": entry.get("url", ""),
            "page_url": entry.get("url", ""), "rects": entry.get("rects") or [], "w": entry.get("width", 0),
            "h": entry.get("height", 0), "credit": entry.get("domain", ""), "editorial": True,
            "source_id": source_id, "topic": topic}


def _collect_captures(ctx: _Ctx) -> list[dict[str, Any]]:
    """As provas já capturadas no projeto (limpas) e, na coleta completa com o Playwright, as fontes do roteiro.
    Offline, a captura nova só com `open_page` injetado (nunca abre o navegador de verdade)."""
    from studio.media import page_capture
    from studio.workers.visual import proofs

    out: list[dict[str, Any]] = []
    done_urls: set[str] = set()
    for data in proofs.list_captures(ctx.project_id):
        entry = proofs.clean_from_capture(data, ctx.aspect)
        if entry is None:
            continue
        done_urls.add(entry["url"])
        topic = _topic_for([entry.get("title", ""), entry.get("text", "")], ctx.topics, TOPIC_SOURCES)
        out.append(_capture_item(entry, f"cap:{data['id']}", topic))
    if not ctx.full:
        return out
    if ctx.open_page is None and (ctx.offline or not page_capture.browser_status()["ok"]):
        return out
    sources = [s for s in ctx.doc.sources if s.url.startswith(("http://", "https://")) and s.url not in done_urls]
    for source in sources[:MAX_SOURCE_CAPTURES]:
        if ctx.cancelled():
            break
        phrase = (source.title or source.note or "").strip()
        if not phrase:
            continue
        try:
            entry = proofs.capture_clean(ctx.project_id, source.url, [phrase[:160]], ctx.aspect,
                                         open_page=ctx.open_page)
        except Exception as exc:  # noqa: BLE001 - uma página que não abre não derruba as outras
            ctx.notes.append(f"A fonte {page_capture.domain_of(source.url)} não foi capturada: {exc}")
            continue
        out.append(_capture_item(entry, f"capurl:{source.url}", _topic_for([source.title], ctx.topics, TOPIC_SOURCES)))
    return out


def pool_links(project_id: str) -> list[dict[str, str]]:
    """Os links oficiais do projeto (`settings.pool_links`)."""
    from studio.core.project import service

    try:
        raw = (service.get_project_row(project_id).get("settings") or {}).get(LINKS_KEY) or []
    except Exception:  # noqa: BLE001 - projeto fora do banco (testes de função pura)
        return []
    return [normalize_link(x) for x in raw if isinstance(x, dict) and x.get("url")][:MAX_LINKS]


def normalize_link(raw: dict[str, Any]) -> dict[str, str]:
    url = " ".join(str(raw.get("url") or "").split())[:2000]
    tier = str(raw.get("tier") or "official")
    return {"url": url, "label": " ".join(str(raw.get("label") or "").split())[:120],
            "tier": tier if tier in ("official", "own") else "official"}


def _collect_links(ctx: _Ctx) -> list[dict[str, Any]]:
    """Trechos dos links oficiais dados pelo usuário. Só a coleta completa baixa (o yt-dlp, e offline só com `runner`
    injetado); a rápida e a offline usam os que já foram baixados."""
    from studio.media import clips

    out: list[dict[str, Any]] = []
    pending = 0
    for link in pool_links(ctx.project_id):
        if ctx.cancelled():
            break
        url = link["url"]
        found = clips.cached_source(url)
        if found is None:
            if not ctx.full or (ctx.offline and ctx.runner is None):
                pending += 1
                continue
            try:
                found = clips.fetch(url, runner=ctx.runner)
            except Exception as exc:  # noqa: BLE001 - um link ruim não derruba os outros
                ctx.notes.append(f"O link {url[:80]} não baixou: {exc}")
                continue
        label = link["label"] or found.title or "vídeo oficial"
        shots = _analyze(ctx, found.path, label)
        tier = link["tier"]
        out += _clip_items(shots, tier=tier, prefix="yt", key=clips.link_key(url), url=url,
                           credit=found.uploader or link["label"] or found.title, editorial=tier == "official",
                           topic=_topic_for([label], ctx.topics, label[:80] or TOPIC_LINKS))
    if pending:
        if ctx.offline:
            ctx.notes.append(f"Modo offline: {pending} link(s) oficial(is) ainda não baixado(s) ficaram de fora.")
        else:
            ctx.notes.append(f"{pending} link(s) oficial(is) ainda não baixado(s): use \"Atualizar acervo\" para "
                             "baixar e cortar os planos.")
    return out


def _collect_free(ctx: _Ctx) -> list[dict[str, Any]]:
    """Fotos livres e vídeos do Pexels por tópico (só online, ou com `http` injetado; as buscas têm cache)."""
    if ctx.offline and ctx.http is None:
        return []
    from studio.providers import media_search

    out: list[dict[str, Any]] = []
    for topic in ctx.topics[:MAX_PHOTO_TOPICS]:
        if ctx.cancelled():
            return out
        result = media_search.search(topic.query, orientation=ctx.orientation, http=ctx.http)  # type: ignore[arg-type]
        for c in result.candidates:
            out.append({"tier": "stock" if c.source == "pexels" else "free", "media": "image", "desc": c.title,
                        "url": c.download_url, "page_url": c.page_url, "w": c.width, "h": c.height,
                        "license": c.license, "credit": c.credit, "source_id": c.id, "topic": topic.label})
    for topic in ctx.topics[:MAX_VIDEO_TOPICS]:
        if ctx.cancelled():
            return out
        for v in media_search.search_videos(topic.query, orientation=ctx.orientation, http=ctx.http,  # type: ignore[arg-type]
                                            target=max(ctx.width, ctx.height)):
            # A cena mostra no máximo `official_max_clip_s` do vídeo: o pedido do storyboard recebe essa duração.
            dur = min(v.duration, ctx.max_clip_s) if v.duration > 0 else None
            out.append({"tier": "stock", "media": "clip", "desc": v.title or topic.label, "url": v.download_url,
                        "page_url": v.page_url, "dur_s": dur, "w": v.width, "h": v.height,
                        "license": v.license, "credit": v.credit, "source_id": v.id, "topic": topic.label})
    return out


def _collect_found(ctx: _Ctx) -> list[dict[str, Any]]:
    """Arte oficial e notícias por tópico: uma chamada do Claude Code com busca web por vídeo, só na coleta completa
    (a rápida usa o resultado em cache, de até 7 dias)."""
    if ctx.offline or not ctx.topics:
        return []
    from studio.core.graph.store import ArtifactStore
    from studio.core.hashing import hash_obj
    from studio.workers.pipeline import media_find

    topics = [(t.id, t.label, t.query) for t in ctx.topics[:media_find.MAX_TOPICS]]
    store = ArtifactStore()
    key = hash_obj({"doc": ctx.project_id, "topics": topics, "v": 1})
    cached = store.lookup(FIND_CACHE_KIND, key)
    if cached is not None and _fresh(cached.get("fetched_at")):
        found = cached.get("found") or {}
    elif not ctx.full or not media_find.web_available():
        return []
    else:
        result = media_find.find_for_topics(ctx.doc, topics, project_id=ctx.project_id, job_id=ctx.job_id,
                                            http=ctx.http)
        found = {tid: [f.model_dump(mode="json") for f in items] for tid, items in result.items()}
        store.save(FIND_CACHE_KIND, key, {"found": found, "fetched_at": datetime.now(UTC).isoformat(), "files": []})
    labels = {t.id: t.label for t in ctx.topics}
    out: list[dict[str, Any]] = []
    for tid, items in found.items():
        for f in items:
            out.append({"tier": "capture" if f.get("kind") == "news" else "official", "media": "image",
                        "desc": f.get("title") or f.get("why") or labels.get(tid, ""), "url": f.get("image", ""),
                        "page_url": f.get("page_url", ""), "w": f.get("width", 0), "h": f.get("height", 0),
                        "credit": f.get("outlet", ""), "editorial": True, "source_id": f"found:{f.get('image', '')}",
                        "topic": labels.get(tid, "")})
    return out


def _fresh(when: object, days: int = 7) -> bool:
    try:
        stamp = datetime.fromisoformat(str(when))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return (datetime.now(UTC) - stamp).days < days


COLLECTORS: list[tuple[str, Callable[[_Ctx], list[dict[str, Any]]]]] = [
    ("material próprio", _collect_own),
    ("biblioteca do canal", _collect_kit),
    ("capturas de página", _collect_captures),
    ("links oficiais", _collect_links),
    ("fotos e vídeos livres", _collect_free),
    ("arte oficial e notícias", _collect_found),
]


# --------------------------------------------------------------------------------------------
# Nota, anti-repetição, seleção e ids
# --------------------------------------------------------------------------------------------


def base_score(item: dict[str, Any]) -> float:
    """Nível primeiro (o mais real ganha); depois arquivo já local e resolução."""
    score = TIER_SCORE.get(str(item.get("tier")), 0.0)
    if item.get("path"):
        score += 0.05
    w, h = int(item.get("w") or 0), int(item.get("h") or 0)
    if w and h:
        score += min(0.1, (w * h) / (1920 * 1080) * 0.1)
    return round(score, 4)


def _hash_local(item: dict[str, Any]) -> str:
    from studio.media import novelty

    rel = item.get("poster") or item.get("path")
    if not rel or (item.get("media") == "clip" and not item.get("poster")):
        return ""  # trecho sem pôster: o hash do quadro custaria um ffmpeg por item
    return novelty.hash_file(get_settings().data_dir / str(rel)) or ""


def history_marks(doc: ProjectDoc, window: int = NOVELTY_WINDOW) -> tuple[list[str], set[str]]:
    """(hashes de imagem, endereços e ids) do material usado nos últimos `window` vídeos do canal. Só lê o que os
    outros projetos já guardaram (impressão digital do último build e a mídia anexada); nunca calcula neles.

    Trecho de vídeo entra só pelo `source_id` do plano usado ("yt:<chave>#3"), nunca pelo endereço: o endereço é o do
    vídeo inteiro, e um plano usado não pode rebaixar os outros planos do mesmo trailer."""
    if not doc.channel_id:
        return [], set()
    from studio.core.project import service
    from studio.workers import novelty as checker

    hashes: list[str] = []
    refs: set[str] = set()
    for other in checker._history(doc.id, doc.channel_id, window):
        try:
            fp = checker.cached_fingerprint(other)
            hashes += [i["hash"] for i in (fp or {}).get("images") or [] if i.get("hash")]
            other_doc = service.get_doc(other)
            other_pool = load_pool(other)
        except Exception as exc:  # noqa: BLE001 - um projeto quebrado não derruba a coleta
            log.warning("histórico do projeto %s ilegível: %s", other, exc)
            continue
        for scene in other_doc.scenes:
            media = scene.visual.media
            if media is None:
                continue
            if media.kind != "clip":
                refs.update(r for r in (media.url, media.candidate_id) if r)
            used = other_pool.item(media.pool_id)
            if used is not None and used.source_id:
                refs.add(used.source_id)
    return hashes, refs


def apply_novelty(items: list[dict[str, Any]], hashes: list[str], refs: set[str],
                  penalty: float = NOVELTY_PENALTY) -> int:
    """Tira `penalty` da nota do item já usado nos últimos vídeos (imagem parecida pelo dHash, o mesmo `source_id` ou,
    numa imagem, o mesmo endereço do arquivo) e marca a tag "repetido". Trecho de vídeo compara só o `source_id` do
    plano e o pôster; a página de origem nunca conta (vários achados da mesma página não são o mesmo material).
    Devolve quantos itens perderam nota."""
    from studio.media import novelty

    hit = 0
    for item in items:
        keys = (item.get("source_id"),) if item.get("media") == "clip" else (item.get("url"), item.get("source_id"))
        same_ref = any(r and r in refs for r in keys)
        same_image = bool(item.get("dhash")) and any(novelty.similar(item["dhash"], h) for h in hashes)
        if same_ref or same_image:
            item["score"] = round(float(item.get("score") or 0.0) - penalty, 4)
            item["tags"] = [*(item.get("tags") or []), "repetido"]
            hit += 1
    return hit


def _id_number(pid: str | None) -> int:
    return int(pid[1:]) if pid and POOL_ID_RE.match(pid) else 0


def _scene_pool_ids(doc: ProjectDoc) -> list[str]:
    out: list[str] = []
    for scene in doc.scenes:
        if scene.visual.media is not None and scene.visual.media.pool_id:
            out.append(scene.visual.media.pool_id)
        for shot in getattr(scene, "shots", None) or []:
            if shot.pool_id:
                out.append(shot.pool_id)
            if shot.media is not None and shot.media.pool_id:
                out.append(shot.media.pool_id)
    return out


def _assign_ids(items: list[dict[str, Any]], previous: Pool, doc: ProjectDoc | None = None) -> tuple[dict[str, str], int]:
    """Ids curtos e estáveis: o mesmo `source_id` mantém o id de sempre (registro `Pool.ids`, que não encolhe), e o
    item novo ganha um número que nunca foi dado (nem a item que saiu do acervo, nem a um `pool_id` que alguma cena
    cita). Devolve o registro novo e o próximo número livre."""
    registry: dict[str, str] = dict(previous.ids)
    for old in previous.items:
        if old.source_id:
            registry.setdefault(old.source_id, old.id)
    numbers = [_id_number(p) for p in registry.values()]
    numbers += [_id_number(i.id) for i in previous.items]
    numbers += [_id_number(p) for p in _scene_pool_ids(doc)] if doc is not None else []
    top = max([previous.next_id - 1, *numbers, 0])
    given: set[str] = set()
    for item in items:  # `source_id` já sem repetição
        sid = str(item.get("source_id") or "")
        pid = registry.get(sid)
        if pid is None or pid in given or not POOL_ID_RE.match(pid):
            top += 1
            pid = f"p{top}"
            registry[sid] = pid
        given.add(pid)
        item["id"] = pid
    if len(registry) > MAX_ID_REGISTRY:  # os mais antigos que não estão no acervo saem; o número nunca volta
        current = {str(i.get("source_id") or "") for i in items}
        extra = len(registry) - MAX_ID_REGISTRY
        for sid in [s for s in registry if s not in current][:extra]:
            registry.pop(sid)
    return registry, top + 1


def _select(items: list[dict[str, Any]], topics: list[Topic]) -> list[dict[str, Any]]:
    """O corte do acervo e a ordem de exibição. O corte é pela nota, com lugar garantido para o mais real: primeiro os
    níveis `own`/`official`/`capture` (até `RESERVED_MAX`, `RESERVED_PER_TOPIC` por tópico), depois o resto até
    `MAX_ITEMS` (`MAX_PER_TOPIC` por tópico). Só então a ordem: por tópico (na ordem do roteiro, depois os grupos
    fixos) e, dentro dele, pela nota."""
    ranked = sorted(items, key=lambda i: -float(i.get("score") or 0.0))
    kept: list[dict[str, Any]] = []
    taken: set[int] = set()
    per_reserved: dict[str, int] = {}
    for n, item in enumerate(ranked):
        if len(kept) >= RESERVED_MAX:
            break
        topic = str(item.get("topic") or "")
        if item.get("tier") in RESERVED_TIERS and per_reserved.get(topic, 0) < RESERVED_PER_TOPIC:
            per_reserved[topic] = per_reserved.get(topic, 0) + 1
            kept.append(item)
            taken.add(n)
    per_topic: dict[str, int] = {}
    for n, item in enumerate(ranked):
        if len(kept) >= MAX_ITEMS:
            break
        topic = str(item.get("topic") or "")
        if n not in taken and per_topic.get(topic, 0) < MAX_PER_TOPIC:
            per_topic[topic] = per_topic.get(topic, 0) + 1
            kept.append(item)
    rank = {t.label: i for i, t in enumerate(topics)}
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in items:  # a ordem de chegada desempata os grupos fixos
        groups.setdefault(str(item.get("topic") or ""), [])
    for item in kept:
        groups[str(item.get("topic") or "")].append(item)
    order = list(groups)
    keys = sorted((k for k in groups if groups[k]), key=lambda k: (rank.get(k, len(rank)), k == "", order.index(k)))
    out: list[dict[str, Any]] = []
    for key in keys:
        out += sorted(groups[key], key=lambda i: -float(i.get("score") or 0.0))
    return out


def gather(doc: ProjectDoc, style: StyleSpec, *, http: Any = None, open_page: Any = None, runner: Any = None,
           job_id: str | None = None, progress: Callable[[float, str], None] | None = None,
           cancelled: Callable[[], bool] | None = None, full: bool = False) -> Pool:
    """Coleta o acervo do projeto (falha de um coletor vira aviso em `Pool.warnings`).

    `full=False` (o padrão, dentro do storyboard): rápida, só o que já está baixado e analisado, as provas já
    capturadas, as buscas de foto (com cache) e a busca web em cache; nada de yt-dlp, corte de vídeo novo, navegador
    ou chamada paga. `full=True` (job `media_pool`, botão "Atualizar acervo"): baixa, corta, captura e busca na web,
    com `progress` e `cancelled` conferido entre os coletores e entre os planos.

    Offline (`VS_OFFLINE=1`) não toca a rede, mesmo com `http` injetado: ele só vale para os coletores httpx."""
    from studio.core.project import service

    try:
        settings = dict(service.get_project_row(doc.id).get("settings") or {})
    except Exception:  # noqa: BLE001 - documento fora do banco
        settings = {}
    try:
        previous = load_pool(doc.id)
    except ValueError:
        previous = Pool()
    say = progress or (lambda f, m: None)
    stop = cancelled or (lambda: False)
    ctx = _Ctx(doc=doc, style=style, project_id=doc.id, topics=topics_of(doc), settings=settings,
               offline=get_settings().offline, full=full, previous=previous, http=http, open_page=open_page,
               runner=runner, job_id=job_id, progress=say, cancelled=stop)
    raw: list[dict[str, Any]] = []
    warnings: list[str] = []
    for i, (label, collect) in enumerate(COLLECTORS):
        if stop():
            warnings.append("Coleta do acervo cancelada.")
            break
        say(0.05 + 0.85 * i / len(COLLECTORS), f"Acervo: {label}")
        try:
            raw += collect(ctx)
        except Exception as exc:  # noqa: BLE001 - um coletor não derruba os outros
            log.warning("coletor do acervo %r falhou: %s", label, exc)
            warnings.append(f"Acervo ({label}): {exc}")
    warnings += ctx.notes
    if ctx.unanalyzed:
        warnings.append(f"{ctx.unanalyzed} vídeo(s) do acervo ainda não cortado(s) em planos: use \"Atualizar "
                        "acervo\" para incluí-los.")
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        sid = str(item.get("source_id") or "")
        if not sid or sid in seen:
            continue
        seen.add(sid)
        item["dhash"] = _hash_local(item)
        item["score"] = base_score(item)
        items.append(item)
    try:
        hashes, refs = history_marks(doc)
        apply_novelty(items, hashes, refs)
    except Exception as exc:  # noqa: BLE001 - sem histórico, sem penalidade
        log.warning("anti-repetição do acervo falhou: %s", exc)
    items = _select(items, ctx.topics)
    registry, next_id = _assign_ids(items, previous, doc)
    pool_items: list[PoolItem] = []
    for item in items:
        try:
            pool_items.append(PoolItem.model_validate(item))
        except Exception as exc:  # noqa: BLE001 - item torto sai, o resto fica
            log.warning("item do acervo descartado (%s): %s", item.get("source_id"), exc)
    say(1.0, f"Acervo: {len(pool_items)} itens")
    return Pool(items=pool_items, gathered_at=datetime.now(UTC).isoformat(timespec="seconds"),
                warnings=list(dict.fromkeys(warnings)), ids=registry, next_id=next_id)


# --------------------------------------------------------------------------------------------
# Pedido do storyboard
# --------------------------------------------------------------------------------------------


def _line(item: PoolItem) -> str:
    desc = " ".join(item.desc.split())[:90] or "-"
    dur = f"{item.dur_s:.1f}s" if item.dur_s else "-"
    return f"{item.id} · {item.tier} · {item.media} · {desc} · {dur}"


def prompt_block(pool: Pool, max_lines: int = 120) -> str:
    """As linhas do acervo para o pedido do storyboard: `id · nível · tipo · desc · dur`, agrupadas por tópico
    (`[tópico]`). Com mais itens que linhas, cada tópico leva a sua parte (os melhores de cada um, em rodízio) e a
    última linha diz quantos ficaram de fora. Se nem todos os tópicos cabem, ficam primeiro os grupos com material
    próprio, oficial ou captura (o mais real nunca sai da lista por ser um grupo fixo do fim). Acervo vazio: ""."""
    if not pool.items or max_lines <= 0:
        return ""
    groups: dict[str, list[PoolItem]] = {}
    for item in pool.items:
        groups.setdefault(item.topic or "Outros", []).append(item)
    topics = list(groups)
    room = max_lines - 1  # a linha final de "fora da lista", se precisar
    if len(pool.items) + len(topics) <= max_lines:
        room = max_lines
    if len(topics) * 2 > room:
        keep = max(1, room // 2)
        prio = sorted(topics, key=lambda t: (0 if any(i.tier in RESERVED_TIERS for i in groups[t]) else 1,
                                             topics.index(t)))
        chosen = set(prio[:keep])
        topics = [t for t in topics if t in chosen]
    budget = max(0, room - len(topics))
    take = {t: 0 for t in topics}
    while budget > 0:
        moved = False
        for t in topics:
            if budget > 0 and take[t] < len(groups[t]):
                take[t] += 1
                budget -= 1
                moved = True
        if not moved:
            break
    lines: list[str] = []
    shown = 0
    for t in topics:
        if not take[t]:
            continue
        lines.append(f"[{t}]")
        for item in groups[t][: take[t]]:
            lines.append(_line(item))
            shown += 1
    left = len(pool.items) - shown
    if left > 0:
        lines.append(f"… mais {left} itens fora da lista")
    return "\n".join(lines[:max_lines])


# --------------------------------------------------------------------------------------------
# Anexo automático
# --------------------------------------------------------------------------------------------


def _media_kind(item: PoolItem) -> str:
    if item.media in ("clip", "capture", "meme"):
        return item.media
    if item.tier == "official":
        return "official"
    if item.tier == "capture":
        return "news" if item.source_id.startswith("found:") else "capture"
    if item.tier == "meme":
        return "meme"
    return "photo"


def _media_source(item: PoolItem) -> str:
    prefix = item.source_id.split(":", 1)[0]
    if prefix == "file":
        return {"own": "upload", "capture": "capture"}.get(item.tier, "link")
    return _PREFIX_SOURCE.get(prefix, "library" if item.tier in ("own", "official") else "link")


def pending_ref(item: PoolItem) -> MediaRef:
    """A `MediaRef` pendente que o storyboard põe na cena que escolheu o item (sem arquivo: `attach_chosen` baixa).
    É o construtor recomendado; o anexo aceita também a cena montada de outro jeito (ver `needs_attach`)."""
    return MediaRef(kind=_media_kind(item), source=_media_source(item), tier=item.tier, pool_id=item.id,  # type: ignore[arg-type]
                    page_url=item.page_url, title=item.desc[:300], credit=item.credit, editorial=item.editorial,
                    license=item.license)


def is_pending(media: MediaRef | None) -> bool:
    return media is not None and bool(media.pool_id) and not media.asset and not media.video


def needs_attach(media: MediaRef | None, project_id: str) -> bool:
    """A mídia cita um item do acervo e ainda não está pronta no projeto: sem arquivo (pendente) ou apontando para
    fora da pasta do projeto (o cache da biblioteca, o vídeo de origem inteiro, a foto crua de `acervo/`). Pronta é
    a imagem em `uploads/<projeto>/media/` e o vídeo em `uploads/<projeto>/clips/`, que é o que o anexo produz: assim
    o anexo copia, normaliza e preenche `hold`/`mute`/`tier`/`credit` qualquer que seja a forma que o storyboard deu
    à cena."""
    if media is None or not media.pool_id:
        return False
    if not media.asset and not media.video:
        return True
    root = f"uploads/{project_id}/"
    asset_ok = media.asset is None or media.asset.startswith(root + "media/")
    video_ok = media.video is None or media.video.startswith(root + "clips/")
    return not (asset_ok and video_ok)


def _store_local(rel: str, project_id: str) -> Any:
    from studio.media import real_media

    return real_media.store_image((get_settings().data_dir / rel).read_bytes(), project_id)


def _download_video(url: str, http: Any = None) -> Path:
    """Baixa o arquivo de vídeo (Pexels) uma vez para `library/clips/src/<chave>/video.mp4`, com prazo próprio (um
    MP4 de dezenas de MB não cabe nos 20 s de uma página)."""
    from studio.media import clips, safe_http

    found = clips.cached_source(url)
    if found is not None:
        return found.path
    fetched = safe_http.fetch(url, max_bytes=VIDEO_MAX_BYTES, accept="video/mp4,video/*;q=0.9,*/*;q=0.5", http=http,
                              wall_clock_s=VIDEO_WALL_CLOCK_S)
    return clips.save_source(url, fetched.content).path


def _clip_source(item: PoolItem, doc: ProjectDoc, http: Any = None) -> Path:
    """O vídeo de origem do trecho: o arquivo local do item, o vídeo do thumbkit, o link já baixado ou (Pexels) o
    download do arquivo. O anexo nunca chama o yt-dlp (isso é da coleta completa)."""
    from studio.media import clips

    data_dir = get_settings().data_dir
    if item.path and (data_dir / item.path).is_file():
        return data_dir / item.path
    if item.source_id.startswith("kitvideo:") and doc.channel_id:
        from studio.media import thumb_assets

        name = item.source_id[len("kitvideo:"):].rsplit("#", 1)[0]
        kit = thumb_assets.thumbkit_dir(doc.channel_id)
        raw = (thumb_assets.load_manifest(kit, doc.channel_id).get("videos") or {}).get(name)
        path = kit_video_path(kit, raw) if raw else None
        if path is not None:
            return path
    if item.url:
        found = clips.cached_source(item.url)
        if found is not None:
            return found.path
        if item.source_id.startswith("pexels-video:"):
            return _download_video(item.url, http)
    raise FileNotFoundError(item.id)


def _needs_network(item: PoolItem) -> bool:
    """O anexo do item vai baixar alguma coisa (conta no teto de downloads do anexo)."""
    from studio.media import clips

    data_dir = get_settings().data_dir
    if item.source_id.startswith("file:") or (item.path and (data_dir / item.path).is_file()):
        return False
    if item.media == "clip":
        if item.source_id.startswith("kitvideo:"):
            return False
        return not (item.url and clips.cached_source(item.url) is not None)
    return True


def _materialize_file(item: PoolItem, doc: ProjectDoc) -> MediaRef:
    """Item `file:` (mídia anexada à mão): a `MediaRef` da cena que ainda a mostra, ou refeita do próprio item (o
    storyboard novo pode ter reescrito a cena dona)."""
    from studio.media import clips

    rel = item.source_id[5:]
    same = next((s.visual.media for s in doc.scenes if s.visual.media is not None
                 and (s.visual.media.asset == rel or s.visual.media.video == rel)), None)
    if same is not None:
        return same.model_copy(deep=True)
    data_dir = get_settings().data_dir
    path = item.path or rel
    if not (data_dir / path).is_file():
        raise FileNotFoundError(path)
    project_media = f"uploads/{doc.id}/media/"
    video: str | None = None
    if item.media == "clip":
        video = path
        poster = item.poster
        if not poster or not (data_dir / poster).is_file():
            poster = _data_rel(clips.make_poster(data_dir / path, 0.3, data_dir / f"{path}.jpg"))
        assert poster is not None
        asset_rel = poster
    else:
        asset_rel = path
    width, height = item.w or None, item.h or None
    if not asset_rel.startswith(project_media):
        stored = _store_local(asset_rel, doc.id)
        asset_rel, width, height = stored.rel, stored.width, stored.height
    return MediaRef(kind=_media_kind(item), source=_media_source(item), asset=asset_rel, video=video,  # type: ignore[arg-type]
                    start_s=item.start_s, dur_s=item.dur_s, hold=item.media == "clip", mute=True, rects=item.rects,
                    url=item.url, page_url=item.page_url, title=item.desc[:300], credit=item.credit,
                    license=item.license, editorial=item.editorial, width=width, height=height)


def materialize(item: PoolItem, doc: ProjectDoc, style: StyleSpec | None = None, *, http: Any = None,
                base: MediaRef | None = None) -> MediaRef:
    """Baixa/copia o item para a pasta do projeto e devolve a `MediaRef` pronta, com `tier`, `pool_id`, `video`,
    `start_s`, `dur_s`, `rects`, `editorial` e `credit`. O trecho de vídeo vira um proxy mudo do plano, no tamanho e
    fps do estilo, direto em `uploads/<projeto>/clips/`. Levanta erro quando não dá (o anexo tenta a reserva)."""
    from studio.media import clips, real_media

    project_id = doc.id
    data_dir = get_settings().data_dir
    if item.source_id.startswith("file:"):
        media = _materialize_file(item, doc)
    elif item.media == "clip":
        if style is None:
            raise ValueError("sem estilo para o tamanho do trecho")
        fmt = style.format
        w, h = (int(x) for x in fmt.resolution.lower().split("x"))
        max_clip = float(style.art.sources.official_max_clip_s)
        dur = min(float(item.dur_s or max_clip), max_clip)
        src = _clip_source(item, doc, http)
        video = clips.proxy_for_project(src, float(item.start_s), dur, project_id, width=w, height=h, fps=fmt.fps,
                                        src_w=item.w, src_h=item.h)
        poster = clips.make_poster(data_dir / video, min(dur / 2, 1.0), (data_dir / video).with_suffix(".jpg"))
        poster_rel = _data_rel(poster)
        assert poster_rel is not None
        stored = _store_local(poster_rel, project_id)
        media = MediaRef(kind="clip", source=_media_source(item), asset=stored.rel, video=video, start_s=0.0,  # type: ignore[arg-type]
                         dur_s=dur, hold=True, mute=True, width=stored.width, height=stored.height, url=item.url,
                         page_url=item.page_url, title=item.desc[:300])
    elif item.path:
        stored = _store_local(item.path, project_id)
        media = MediaRef(kind=_media_kind(item), source=_media_source(item), asset=stored.rel, url=item.url,  # type: ignore[arg-type]
                         page_url=item.page_url, title=item.desc[:300], width=stored.width, height=stored.height)
    else:
        prefix = item.source_id.split(":", 1)[0]
        if prefix in ("commons", "openverse", "pexels"):
            media, _ = real_media.media_from_candidate(item.source_id, project_id, kind="photo", http=http)
        else:
            if not (item.page_url or item.url):
                raise FileNotFoundError(item.id)
            kind = "news" if _media_kind(item) == "news" else "official"
            found = {"image": item.url, "page_url": item.page_url, "title": item.desc, "outlet": item.credit}
            media, _ = real_media.media_from_link(item.page_url or item.url, project_id, kind=kind,  # type: ignore[arg-type]
                                                  lang=doc.primary_language, http=http, found=found)
    update: dict[str, Any] = {"tier": item.tier, "pool_id": item.id,
                              "editorial": bool(item.editorial or media.editorial),
                              "credit": item.credit or media.credit, "license": media.license or item.license}
    if item.rects:
        update["rects"] = item.rects
    if base is not None:
        for key in ("focus", "fit"):
            if getattr(base, key) is not None:
                update[key] = getattr(base, key)
    return MediaRef.model_validate({**media.model_dump(), **update})


def fallbacks(pool: Pool, item: PoolItem, used: set[str]) -> list[PoolItem]:
    """A reserva do item, sem os já usados nem a IA: primeiro o mesmo tópico e o mesmo tipo de mídia (trecho ou
    imagem), depois o mesmo tópico com o outro tipo, depois os tópicos vizinhos (na ordem do acervo). Em cada
    faixa, a ordem de `sources.FALLBACK` a partir do nível do item, e a nota dentro do nível."""
    order = {tier: i for i, tier in enumerate(FALLBACK)}
    start = order.get(item.tier, 0)
    topics = list(dict.fromkeys(i.topic for i in pool.items))
    here = topics.index(item.topic) if item.topic in topics else 0

    def distance(topic: str) -> int:
        return abs(topics.index(topic) - here) if topic in topics else len(topics)

    others = [i for i in pool.items if i.id != item.id and i.id not in used and i.tier != "ai"]
    return sorted(others, key=lambda i: (distance(i.topic), 0 if (i.media == "clip") == (item.media == "clip") else 1,
                                         (order.get(i.tier, len(order)) - start) % len(order), -i.score))


def _label(doc: ProjectDoc, scene: Scene) -> str:
    from studio.core.project.media_edit import scene_label

    return scene_label(doc, scene.id)


def missing_real(doc: ProjectDoc, style: StyleSpec | None = None) -> list[Scene]:
    """Cenas que mostram imagem de IA sem motivo declarado (`visual.ai_reason`), sem as travadas. A cena que
    reaproveita outra herda o motivo da dona da imagem. Com a política do estilo sem `require_ai_reason`, nenhuma.
    Helper para a conferência do storyboard (o achado `faltou_real` é dela, em `storyboard_check`)."""
    if style is not None and not style.art.sources.require_ai_reason:
        return []
    out: list[Scene] = []
    for scene in doc.ordered_scenes():
        if scene.locked or prov_of_scene(scene, doc) != "ai":
            continue
        owner = doc.reuse_root(scene) if scene.visual.reuse_of else scene
        reason = (owner or scene).visual.ai_reason or scene.visual.ai_reason
        if not (reason or "").strip():
            out.append(scene)
    return out


def faltou_real_warning(doc: ProjectDoc, style: StyleSpec | None = None) -> str | None:
    """O texto para o usuário quando há cenas de IA sem motivo e material real no acervo (sem código interno)."""
    missing = missing_real(doc, style)
    if not missing:
        return None
    labels = [_label(doc, s) for s in missing]
    shown = ", ".join(labels[:8]) + ("…" if len(labels) > 8 else "")
    return (f"{len(missing)} cena(s) usam imagem de IA sem motivo e há material real no Acervo ({shown}): troque pelo "
            "real ou diga o motivo.")


def _has_real(scene: Scene, project_id: str) -> bool:
    media = scene.visual.media
    return media is not None and bool(media.asset or media.video) and not needs_attach(media, project_id)


def attach_chosen(doc: ProjectDoc, pool: Pool, *, http: Any = None) -> list[str]:
    """Anexa (em `doc`, no lugar) a mídia do acervo nas cenas que citam `pool_id` e ainda não estão prontas no
    projeto (`needs_attach`). Item que não baixa cai na reserva (`fallbacks`, até `MAX_FALLBACK_TRIES`; no máximo
    `attach_download_budget` downloads por anexo). Sem reserva, a cena volta à imagem de IA quando tem prompt; sem
    prompt, repete a mídia real da cena vizinha, ou fica sem imagem (fundo do estilo). Devolve os avisos, dizendo o
    que de fato aconteceu."""
    from studio.core.styles.library import get_style

    warnings: list[str] = []
    try:
        style: StyleSpec | None = get_style(doc.style_id, doc.style_overrides)
    except Exception:  # noqa: BLE001 - sem estilo: só o trecho de vídeo fica de fora
        style = None
    used: set[str] = set()
    downloads = 0
    budget = attach_download_budget(sum(1 for s in doc.scenes if not s.locked and needs_attach(s.visual.media, doc.id)))
    failed: list[tuple[Scene, str]] = []
    for scene in doc.ordered_scenes():
        media = scene.visual.media
        if scene.locked or not needs_attach(media, doc.id):
            continue
        assert media is not None
        item = pool.item(media.pool_id)
        if item is None:
            scene.visual.media = None
            failed.append((scene, f"o item {media.pool_id} não está no acervo"))
            continue
        chosen: MediaRef | None = None
        source: PoolItem | None = None
        for candidate in [item, *fallbacks(pool, item, used)[:MAX_FALLBACK_TRIES]]:
            remote = _needs_network(candidate)
            if remote and downloads >= budget:
                continue
            downloads += int(remote)
            try:
                chosen = materialize(candidate, doc, style, http=http, base=media)
                source = candidate
                break
            except Exception as exc:  # noqa: BLE001 - tenta a reserva
                log.warning("item %s do acervo não anexou: %s", candidate.id, exc)
        if chosen is None or source is None:
            scene.visual.media = None
            failed.append((scene, f"a mídia do acervo ({item.id}) não baixou e a reserva também não"))
            continue
        scene.visual.media = chosen
        used.add(source.id)
        if source.id != item.id:
            warnings.append(f"{_label(doc, scene)}: o item {item.id} não baixou; entrou {source.id} ({source.tier}, "
                            f"{source.topic or 'sem tópico'}).")
    ordered = doc.ordered_scenes()
    for scene, why in failed:
        label = _label(doc, scene)
        if scene.visual.prompt.strip() and scene.visual.type in ("ai_image", "ai_video"):
            warnings.append(f"{label}: {why}; a cena ficou com a imagem de IA de reserva.")
            continue
        at = next(i for i, s in enumerate(ordered) if s.id == scene.id)
        near = sorted((s for s in ordered if s.id != scene.id and _has_real(s, doc.id)),
                      key=lambda s: (abs(ordered.index(s) - at), ordered.index(s) > at))
        if near:
            scene.visual.reuse_of = near[0].id
            warnings.append(f"{label}: {why}; a cena repete a mídia real da {_label(doc, near[0])}.")
        else:
            scene.visual.type = "none"
            warnings.append(f"{label}: {why}; a cena ficou sem imagem (fundo do estilo).")
    return warnings


# --------------------------------------------------------------------------------------------
# Job: coletar de novo (botão "Atualizar acervo")
# --------------------------------------------------------------------------------------------


BUSY_KINDS = ("media_pool", "storyboard", "autopilot")
"""Jobs que escrevem ou leem o acervo do projeto: com um deles ativo, a coleta não começa outra vez."""


def active_job(project_id: str) -> dict[str, Any] | None:
    """O job ativo do projeto que mexe no acervo (coleta, storyboard ou piloto automático), se houver."""
    from studio.workers.jobs import ACTIVE_STATUSES, list_jobs

    return next((j for j in list_jobs(project_id, limit=100)
                 if j["kind"] in BUSY_KINDS and j["status"] in ACTIVE_STATUSES), None)


def summary(pool: Pool) -> dict[str, Any]:
    by_tier: dict[str, int] = {}
    for item in pool.items:
        by_tier[item.tier] = by_tier.get(item.tier, 0) + 1
    return {"items": len(pool.items), "by_tier": by_tier, "gathered_at": pool.gathered_at}


@job("media_pool")
def run_media_pool(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Coleta completa (baixa, corta, captura e busca na web) e grava `pool.json` (mesmo vazio: a tela mostra quando
    rodou). Cancelada, não grava nada. No fim, as fontes baixadas por link passam pelo teto de espaço."""
    from studio.core.project import service
    from studio.core.project.pool import save_pool
    from studio.core.styles.library import get_style
    from studio.media import clips

    project_id = ctx.project_id
    assert project_id
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    pool = gather(doc, style, job_id=ctx.job_id, progress=lambda f, m: ctx.progress(0.02 + 0.93 * f, m),
                  cancelled=ctx.cancelled, full=True)
    if ctx.cancelled():
        return {"canceled": True}
    save_pool(project_id, pool)
    try:
        clips.prune_sources(keep={clips.link_key(link["url"]) for link in pool_links(project_id)})
    except OSError as exc:
        log.warning("limpeza das fontes de vídeo falhou: %s", exc)
    result = summary(pool)
    if pool.warnings:
        result["warnings"] = pool.warnings
    return result
