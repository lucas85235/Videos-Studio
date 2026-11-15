"""Mídia real: baixar, normalizar, enquadrar e dar crédito às imagens que o usuário anexa às cenas.

O arquivo guardado é o original normalizado (JPEG, até `MAX_SIDE`), em `uploads/<projeto>/media/<sha16>.jpg`: a
limpeza do cache nunca apaga. O recorte para o tamanho do vídeo (`fit_media`) sai no build, no cache "media":
trocar o foco ou o enquadramento não baixa nada de novo.
"""

from __future__ import annotations

import hashlib
import io
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

from studio.config import get_settings
from studio.core.project.document import MediaRef, RealKind
from studio.core.styles.visual import FOCUS_POINTS, pick
from studio.media import link_preview, safe_http
from studio.providers.image import fit_image

UPLOAD_MAX_BYTES = 25_000_000
MIN_SIDE = 320
SOFT_MIN_SIDE = 800
MAX_SIDE = 3840
CONTAIN_RATIO = 1.4
"""Proporção da foto dividida pela do vídeo acima disto (ou abaixo do inverso): imagem inteira com fundo desfocado."""
SOURCE_LABELS = {"commons": "Wikimedia Commons", "openverse": "Openverse", "pexels": "Pexels",
                 "link": "link", "upload": "arquivo enviado"}
EDITORIAL_WARNING = ("Material oficial pode gerar reivindicação de direitos (Content ID); o crédito vai na tela e na "
                     "descrição.")
CREDIT_MAX = 90
BACKGROUND = (17, 17, 17)  # #111111 atrás da transparência
ACCEPTED_FORMATS = {"JPEG", "PNG", "WEBP", "GIF", "BMP", "TIFF", "MPO", "AVIF"}
UNREADABLE = "Não é uma imagem que o app lê (use JPG, PNG ou WEBP)."
_PROJECT_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class MediaError(ValueError):
    """Mídia recusada (formato, tamanho, licença, página sem imagem): mensagem em português."""


@dataclass
class StoredImage:
    rel: str
    width: int
    height: int
    warnings: list[str] = field(default_factory=list)


def media_rel(project_id: str, digest: str) -> str:
    return f"uploads/{project_id}/media/{digest}.jpg"


def _size_warnings(width: int, height: int) -> list[str]:
    side = min(width, height)
    return [f"A imagem é pequena ({side} px) e pode sair borrada."] if side < SOFT_MIN_SIDE else []


def _flatten(im: Image.Image) -> Image.Image:
    """RGB, com a transparência sobre um cinza bem escuro."""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        base = Image.new("RGB", rgba.size, BACKGROUND)
        base.paste(rgba, mask=rgba.getchannel("A"))
        return base
    if im.mode.startswith("I"):  # 16 bits por canal (PNG/TIFF de câmera): reduz para 8 bits antes do RGB
        im = im.convert("I").point(lambda v: v * (1 / 256)).convert("L")
    return im.convert("RGB")


def store_image(data: bytes, project_id: str) -> StoredImage:
    """Guarda o original normalizado (JPEG RGB, girado pelo EXIF, até `MAX_SIDE`). Os mesmos bytes reaproveitam o
    mesmo arquivo."""
    if not _PROJECT_RE.match(project_id or ""):
        raise MediaError("Projeto inválido.")
    digest = hashlib.sha256(data).hexdigest()[:16]
    rel = media_rel(project_id, digest)
    path = get_settings().data_dir / rel
    if path.is_file():
        try:
            with Image.open(path) as done:
                width, height = done.size
            return StoredImage(rel, width, height, _size_warnings(width, height))
        except (OSError, UnidentifiedImageError):
            pass  # arquivo estragado: grava de novo
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ACCEPTED_FORMATS:
                raise MediaError(UNREADABLE)
            im.load()
            image = _flatten(ImageOps.exif_transpose(im))
    except MediaError:
        raise
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError, SyntaxError, ValueError, TypeError) as exc:
        raise MediaError(UNREADABLE) from exc
    if min(image.size) < MIN_SIDE:
        raise MediaError(f"Imagem pequena demais (mínimo {MIN_SIDE} px).")
    if max(image.size) > MAX_SIDE:
        image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{digest}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        image.save(tmp, "JPEG", quality=92)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    width, height = image.size
    return StoredImage(rel, width, height, _size_warnings(width, height))


def resolve_fit(media: MediaRef, width: int, height: int) -> Literal["cover", "contain"]:
    """Enquadramento da mídia no vídeo: o escolhido; senão preenche a tela, ou mostra a imagem inteira (com fundo
    desfocado) quando a proporção dela é muito diferente da do vídeo (foto deitada num vídeo em pé)."""
    if media.fit:
        return media.fit
    if media.kind == "news" or not media.width or not media.height or not width or not height:
        return "cover"
    ratio = (media.width / media.height) / (width / height)
    return "contain" if max(ratio, 1 / ratio) > CONTAIN_RATIO else "cover"


def focus_centering(focus: str | None) -> tuple[float, float]:
    """Centro do recorte a partir do foco ("22% 50%" → (0.22, 0.5)); sem foco, o meio."""
    key = pick(focus, FOCUS_POINTS)
    if key is None:
        return 0.5, 0.5
    x, y = (float(v.rstrip("%")) / 100 for v in FOCUS_POINTS[key].text.split())
    return x, y


def anchor_for(width: int, height: int, caption_position: str) -> float:
    """Altura (0 a 1) do centro da imagem inteira no vídeo em pé, longe da legenda; no deitado, o meio."""
    if height <= width:
        return 0.5
    return 0.60 if caption_position == "top" else 0.40


def fit_media(src: Path, dst: Path, width: int, height: int, *, fit: str, focus: str | None = None,
              anchor_y: float = 0.5) -> Path:
    """A mídia no tamanho do vídeo: "cover" preenche e recorta em volta do foco; "contain" põe a imagem inteira
    sobre ela mesma desfocada e escurecida."""
    centering = focus_centering(focus)
    if fit != "contain":
        return fit_image(src, dst, width, height, centering=centering)
    with Image.open(src) as im:
        im = im.convert("RGB")
        back = ImageOps.fit(im, (width, height), method=Image.Resampling.LANCZOS, centering=centering)
        small = back.resize((max(1, width // 4), max(1, height // 4)), Image.Resampling.BILINEAR)
        radius = max(1.0, min(width, height) * 0.04 / 4)
        back = small.filter(ImageFilter.GaussianBlur(radius)).resize((width, height), Image.Resampling.BICUBIC)
        back = ImageEnhance.Brightness(back).enhance(0.5)
        front = ImageOps.contain(im, (width, height), method=Image.Resampling.LANCZOS)
        x = (width - front.width) // 2
        y = round(anchor_y * height - front.height / 2)
        back.paste(front, (x, max(0, min(height - front.height, y))))
        dst.parent.mkdir(parents=True, exist_ok=True)
        back.save(dst, "JPEG", quality=93)
    return dst


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: max(1, limit - 1)].rstrip(" ,;·/") + "…"


def default_credit(source: str, *, author: str = "", license: str = "", provider: str = "", site: str = "") -> str:
    """Crédito sem o prefixo, até `CREDIT_MAX` caracteres ("Fulano / Wikimedia Commons · CC BY-SA 4.0")."""
    author = " ".join(author.split())
    if source in ("commons", "openverse"):
        where = "Wikimedia Commons" if source == "commons" else (provider or "Openverse")
        tail = f"{where} · {license}" if license else where
    elif source == "pexels":
        tail = "Pexels"
    else:
        return _short(site, CREDIT_MAX)
    if not author:
        return _short(tail, CREDIT_MAX)
    room = CREDIT_MAX - len(tail) - 3
    return f"{_short(author, max(8, room))} / {tail}"[:CREDIT_MAX]


def _media_fetch(url: str, http: httpx.Client | None) -> bytes:
    fetched = safe_http.fetch(url, max_bytes=safe_http.IMAGE_MAX_BYTES, accept=safe_http.ACCEPT_IMAGE, http=http)
    if fetched.content_type and not fetched.content_type.startswith("image/") and fetched.content_type not in (
            "application/octet-stream", "binary/octet-stream"):
        raise MediaError("O link não é uma imagem.")
    return fetched.content


def media_from_candidate(candidate_id: str, project_id: str, *, kind: RealKind = "photo",
                         http: httpx.Client | None = None) -> tuple[MediaRef, list[str]]:
    """Baixa um candidato da busca (Commons, Openverse, Pexels) depois de conferir a licença na fonte."""
    # Import aqui: a busca importa MediaError e default_credit deste módulo.
    from studio.providers import media_search

    candidate = media_search.lookup(candidate_id, http=http)
    stored = store_image(_media_fetch(candidate.download_url, http), project_id)
    media = MediaRef(kind=kind, source=candidate.source, asset=stored.rel, url=candidate.download_url,  # type: ignore[arg-type]
                     page_url=candidate.page_url, title=candidate.title, author=candidate.author,
                     license=candidate.license, license_url=candidate.license_url, credit=candidate.credit,
                     editorial=False, width=stored.width, height=stored.height, candidate_id=candidate.id)
    warnings = [*stored.warnings, *([candidate.warning] if candidate.warning else [])]
    return media, warnings


_FILE_NAMESPACES = ("file", "image", "arquivo", "ficheiro", "imagem", "archivo")
_FILE_REDIRECTS = ("Special:FilePath/", "Special:Redirect/file/", "Especial:FilePath/", "Especial:Redirecionar/file/")


def _file_page(page: str) -> str | None:
    """"File:<nome>" de um título de página de arquivo ("Ficheiro:Nome.jpg", "Special:FilePath/Nome.jpg")."""
    for prefix in _FILE_REDIRECTS:
        if page.startswith(prefix) and len(page) > len(prefix):
            return "File:" + page[len(prefix):].replace("_", " ")
    ns, sep, name = page.partition(":")
    if sep and name.strip() and ns.lower() in _FILE_NAMESPACES:
        return "File:" + name.replace("_", " ")
    return None


def _is_wikipedia(host: str) -> bool:
    return host == "wikipedia.org" or host.endswith(".wikipedia.org")


def commons_title(url: str) -> str | None:
    """"File:<nome>" de um link do Commons ou da Wikipedia (página do arquivo, visualizador de mídia, Special:FilePath)
    ou de uma imagem do Commons em upload/thumb.wikimedia.org."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = unquote(parts.path)
    if host in ("commons.wikimedia.org", "commons.m.wikimedia.org") or _is_wikipedia(host):
        # Visualizador de mídia ("/wiki/Artigo#/media/File:Nome.jpg"): vale o arquivo escolhido, não a página.
        fragment = unquote(parts.fragment)
        if fragment.startswith("/media/") and (title := _file_page(fragment[len("/media/"):])):
            return title
        return _file_page(path[len("/wiki/"):]) if path.startswith("/wiki/") else None
    if host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
        pieces = [p for p in path.split("/") if p]
        # /wikipedia/commons/[thumb/]a/ab/Nome.jpg[/500px-Nome.jpg]: só o projeto commons (os outros têm uso justo)
        if len(pieces) >= 5 and pieces[:2] == ["wikipedia", "commons"]:
            rest = pieces[3:] if pieces[2] == "thumb" else pieces[2:]
            if len(rest) >= 3:
                return "File:" + rest[2].replace("_", " ")
    return None


def media_from_link(url: str, project_id: str, *, kind: RealKind | None, lang: str,
                    http: httpx.Client | None = None) -> tuple[MediaRef, list[str]]:
    """Mídia de um link colado: arquivo do Commons (licença conferida), imagem direta ou página (notícia, material
    oficial). Material de terceiros fica `editorial` e ganha o aviso de Content ID."""
    title = commons_title(url)
    if title:
        try:
            return media_from_candidate(f"commons:{title}", project_id, kind=kind or "photo", http=http)
        except MediaError:
            parts = urlsplit(url)
            if not _is_wikipedia((parts.hostname or "").lower()):
                raise
            # Arquivo só da Wikipedia (uso justo, fora do Commons): segue como material de terceiros, lido da página
            # do arquivo escolhido (a página do artigo traria a imagem principal), nunca como notícia.
            url = urlunsplit((parts.scheme, parts.netloc, "/wiki/" + quote(title.replace(" ", "_"), safe=":"), "", ""))
            kind = kind or "official"
    preview = link_preview.read_link(url, http=http)
    if preview.kind == "image" and (title := commons_title(preview.url)):
        # Redirecionou para uma imagem do Commons (Special:FilePath, encurtador): licença conferida na fonte.
        return media_from_candidate(f"commons:{title}", project_id, kind=kind or "photo", http=http)
    wikipedia = _is_wikipedia((urlsplit(preview.url).hostname or "").lower())
    if preview.kind == "page" and wikipedia and kind in (None, "photo") and (title := commons_title(preview.image_url)):
        # Artigo da Wikipedia: a imagem principal é do Commons; vai com o autor e a licença, não como notícia.
        try:
            return media_from_candidate(f"commons:{title}", project_id, kind="photo", http=http)
        except MediaError:
            pass
    warnings: list[str] = []
    if preview.kind == "image":
        stored = store_image(preview.image or b"", project_id)
        warnings += stored.warnings
        media = MediaRef(kind=kind or "official", source="link", asset=stored.rel, url=preview.url,
                         page_url=preview.url, credit=default_credit("link", site=link_preview.site_label(preview.url)),
                         editorial=True, width=stored.width, height=stored.height)
        warnings.append("Confira o crédito: escreva o dono do material (ex.: Rockstar Games).")
    else:
        site = preview.site_name or link_preview.site_label(preview.url)
        final_kind: RealKind = kind or ("news" if preview.is_article else "official")
        if final_kind == "news":
            stored = None
            if preview.image_url:
                try:
                    # Sem aviso de imagem pequena: a og:image comum (1200x630) só vira miniatura e fundo desfocado.
                    stored = store_image(_media_fetch(preview.image_url, http), project_id)
                except (safe_http.FetchError, MediaError):
                    warnings.append("Não consegui baixar a imagem da notícia: o cartão usa o fundo animado.")
            headline = link_preview.clean_headline(preview.title, site)
            outlet = _short(site, 60)
            media = MediaRef(kind="news", source="link", asset=stored.rel if stored else None,
                             url=preview.image_url if stored else "", page_url=preview.url, title=preview.title,
                             author=preview.author, credit=outlet, editorial=True,
                             width=stored.width if stored else None, height=stored.height if stored else None,
                             outlet=outlet, published=preview.published, headline={lang: headline} if headline else {})
        else:
            if not preview.image_url:
                raise MediaError("A página não tem imagem para usar. Cole o link da imagem ou envie o arquivo.")
            stored = store_image(_media_fetch(preview.image_url, http), project_id)
            warnings += stored.warnings
            media = MediaRef(kind=final_kind, source="link", asset=stored.rel, url=preview.image_url,
                             page_url=preview.url, title=preview.title, author=preview.author,
                             credit=default_credit("link", site=site), editorial=True, width=stored.width,
                             height=stored.height)
    warnings.append(EDITORIAL_WARNING)
    return media, warnings


def media_from_upload(data: bytes, project_id: str, *, kind: RealKind, credit: str = "",
                      editorial: bool | None = None) -> tuple[MediaRef, list[str]]:
    """Mídia de um arquivo enviado. Sem dizer, só a foto não é material de terceiros."""
    if len(data) > UPLOAD_MAX_BYTES:
        raise MediaError("O arquivo passa de 25 MB.")
    stored = store_image(data, project_id)
    warnings = list(stored.warnings) if kind != "news" else []  # notícia: miniatura e fundo desfocado
    credit = " ".join((credit or "").split())[:120]
    editorial = kind != "photo" if editorial is None else bool(editorial)
    if kind in ("official", "news") and not credit:
        warnings.append("Escreva o crédito (ex.: Rockstar Games).")
    if editorial:
        warnings.append(EDITORIAL_WARNING)
    media = MediaRef(kind=kind, source="upload", asset=stored.rel, credit=credit, editorial=editorial,
                     width=stored.width, height=stored.height)
    return media, warnings
