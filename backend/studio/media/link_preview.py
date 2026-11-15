"""Metadados de uma página colada pelo usuário (Open Graph, Twitter Card, JSON-LD), num único GET.

Só o começo da página é lido (`safe_http.HTML_MAX_BYTES`). A imagem da página (og:image) não é baixada aqui:
isso só acontece quando o usuário anexa a mídia (`real_media.media_from_link`).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import deque
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx

from studio.media import safe_http

_ARTICLE_TYPES = {"newsarticle", "article", "reportagenewsarticle", "blogposting", "analysisnewsarticle",
                  "opinionnewsarticle", "reviewnewsarticle", "backgroundnewsarticle"}
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_SEPARATORS = (" | ", " - ", " — ", " – ", " · ", " :: ")
_QUOTES = "\"'“”‘’«»„"
_DATE_RE = re.compile(r"^\s*(\d{4})(?:[-/](\d{1,2})(?:[-/](\d{1,2}))?)?(?=$|[T\s])")
_META_CHARSET_RE = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-:.]+)""", re.IGNORECASE)
_LD_MAX_ITEMS = 200
"""Objetos lidos de um bloco JSON-LD (a notícia vem nos primeiros; página com milhares não trava a leitura)."""


@dataclass
class LinkPreview:
    url: str
    """Endereço final (depois dos redirecionamentos)."""
    kind: Literal["image", "page"]
    content_type: str = ""
    title: str = ""
    site_name: str = ""
    published: str = ""
    """"YYYY-MM-DD", "YYYY-MM", "YYYY" ou vazio."""
    image_url: str = ""
    """Absoluta (http/https)."""
    description: str = ""
    author: str = ""
    is_article: bool = False
    image: bytes | None = None
    """O conteúdo, quando o link já é uma imagem."""


class _MetaParser(HTMLParser):
    """Junta `<meta>`, `<title>`, `<link rel=image_src>`, `<time datetime>` e os blocos JSON-LD."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.meta: dict[str, str] = {}
        self.title_parts: list[str] = []
        self.image_src = ""
        self.time = ""
        self.ld: list[str] = []
        self._in_title = False
        self._in_ld = False
        self._ld_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (values.get("property") or values.get("name") or values.get("itemprop") or "").strip().lower()
            content = values.get("content", "").strip()
            if key and content and key not in self.meta:
                self.meta[key] = content
        elif tag == "title":
            self._in_title = True
        elif tag == "link" and "image_src" in values.get("rel", "").lower().split() and not self.image_src:
            self.image_src = values.get("href", "").strip()
        elif tag == "time" and not self.time and values.get("datetime"):
            self.time = values["datetime"].strip()
        elif tag == "script" and values.get("type", "").strip().lower() == "application/ld+json":
            self._in_ld = True
            self._ld_parts = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "script" and self._in_ld:
            self._in_ld = False
            self.ld.append("".join(self._ld_parts))

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif self._in_ld:
            self._ld_parts.append(data)


def _clean(text: object, limit: int = 400) -> str:
    return " ".join(str(text or "").split())[:limit].strip() if isinstance(text, str) else ""


def _ld_items(raw: str) -> list[dict[str, Any]]:
    """Objetos de um bloco JSON-LD (aceita lista e `@graph`), até `_LD_MAX_ITEMS`."""
    try:
        data = json.loads(raw.strip().rstrip(";"))
    except (ValueError, RecursionError):  # JSON quebrado ou aninhado demais ("[[[[...")
        return []
    out: list[dict[str, Any]] = []
    queue = deque(data if isinstance(data, list) else [data])  # fila: pop(0) numa lista longa ficava quadrático
    while queue and len(out) < _LD_MAX_ITEMS:
        item = queue.popleft()
        if isinstance(item, list):
            queue.extend(item)
        elif isinstance(item, dict):
            out.append(item)
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph)
    return out


def _types(item: dict[str, Any]) -> set[str]:
    value = item.get("@type")
    values = value if isinstance(value, list) else [value]
    return {v.lower() for v in values if isinstance(v, str)}


def _name(value: object) -> str:
    """Nome de um campo JSON-LD que pode ser texto, objeto com `name` ou lista deles."""
    if isinstance(value, list):
        return next((n for n in (_name(v) for v in value) if n), "")
    if isinstance(value, dict):
        return _clean(value.get("name"), 120)
    return _clean(value, 120)


def _ld_image(value: object) -> str:
    if isinstance(value, list):
        return next((u for u in (_ld_image(v) for v in value) if u), "")
    if isinstance(value, dict):
        return _clean(value.get("url") or value.get("contentUrl"), 2000)
    return _clean(value, 2000)


def _absolute(url: str, base_url: str) -> str:
    if not url or url.startswith("data:"):
        return ""
    full = urljoin(base_url, url)
    return full if urlsplit(full).scheme in ("http", "https") else ""


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://", "//", "www."))


def parse_meta(html: str, base_url: str) -> LinkPreview:
    """Metadados de uma página (puro: nada de rede)."""
    parser = _MetaParser()
    try:
        # Sem close(): no Python 3.12.10 o fechamento reprocessa cada tag aberta até o fim (CVE-2025-6069) e fica
        # quadrático numa página montada para isso; o que interessa já foi lido no feed.
        parser.feed(html)
    except Exception:  # noqa: BLE001 - HTML quebrado: fica o que já foi lido
        pass
    meta = parser.meta
    items = [item for raw in parser.ld for item in _ld_items(raw)]
    article = next((i for i in items if _types(i) & _ARTICLE_TYPES), None)
    ld = article or next((i for i in items if i.get("headline")), {})

    def first(*keys: str) -> str:
        return next((meta[k] for k in keys if meta.get(k)), "")

    author = first("article:author", "author")
    if _is_url(author):
        author = ""
    og_type = first("og:type").lower()
    titles = (first("og:title", "twitter:title"), ld.get("headline"), "".join(parser.title_parts))
    return LinkPreview(
        url=base_url,
        kind="page",
        content_type="text/html",
        title=next((t for t in (_clean(v, 300) for v in titles) if t), ""),
        site_name=_clean(first("og:site_name") or _name(ld.get("publisher")) or first("application-name"), 120),
        published=iso_date(first("article:published_time", "og:article:published_time")
                           or _clean(ld.get("datePublished"), 40) or parser.time),
        image_url=_absolute(first("og:image:secure_url", "og:image", "og:image:url", "twitter:image",
                                  "twitter:image:src") or _ld_image(ld.get("image")) or parser.image_src, base_url),
        description=_clean(first("og:description", "twitter:description", "description"), 500),
        author=_clean(author, 120) or _name(ld.get("author")),
        is_article=og_type.startswith(("article", "news")) or article is not None,
    )


def _decode(content: bytes, charset: str | None) -> str:
    """Texto da página: charset do cabeçalho, senão o do `<meta charset>`, senão UTF-8 (trocando o que não lê)."""
    names = [charset] if charset else []
    found = _META_CHARSET_RE.search(content[:4096])
    if found:
        names.append(found.group(1).decode("ascii", "ignore"))
    for name in names:
        try:
            return content.decode(name, errors="replace")
        except LookupError:
            continue
    return content.decode("utf-8", errors="replace")


def read_link(url: str, *, http: httpx.Client | None = None) -> LinkPreview:
    """Um GET no link: imagem (com os bytes) ou página (só os metadados)."""
    fetched = safe_http.fetch(url, max_bytes=safe_http.HTML_MAX_BYTES, accept=safe_http.ACCEPT_HTML, http=http,
                              truncate=True, image_max_bytes=safe_http.IMAGE_MAX_BYTES)
    if fetched.content_type.startswith("image/"):
        return LinkPreview(url=fetched.url, kind="image", content_type=fetched.content_type, image=fetched.content)
    if fetched.content_type in _HTML_TYPES or (not fetched.content_type and fetched.content.lstrip()[:1] == b"<"):
        preview = parse_meta(_decode(fetched.content, fetched.charset), fetched.url)
        preview.content_type = fetched.content_type or "text/html"
        return preview
    raise safe_http.FetchError("O link não é página nem imagem.")


def site_label(url: str) -> str:
    """Host sem "www." ("rockstargames.com")."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


def _is_site(part: str, site: str) -> bool:
    """O pedaço da manchete é o nome do site ("IGN Brasil", "rockstargames.com" ~ "Rockstar Games")?"""
    a, b = _fold(part), _fold(site)
    if not a or not b:
        return False
    if a == b:
        return True
    host = _fold(site.split(".")[0]) if "." in site and " " not in site.strip() else ""
    return bool(host) and a == host or (len(a) >= 4 and len(b) >= 4 and (a in b or b in a) and len(part.split()) <= 4)


def clean_headline(title: str, site_name: str = "", max_words: int = 14) -> str:
    """Manchete curta para o cartão: sem o nome do site no começo ou no fim, sem aspas de fora e com até
    `max_words` palavras (cortada na palavra, com "…")."""
    text = " ".join((title or "").split())
    if site_name:
        changed = True
        while changed and text:
            changed = False
            for sep in _SEPARATORS:
                head, found, tail = text.rpartition(sep)
                if found and head and _is_site(tail, site_name):
                    text, changed = head.strip(), True
                    break
                head, found, tail = text.partition(sep)
                if found and tail and _is_site(head, site_name):
                    text, changed = tail.strip(), True
                    break
    text = text.strip().strip(_QUOTES).strip()
    words = text.split()
    if len(words) > max_words:
        text = " ".join(words[:max_words]).rstrip(" ,;:.—–-") + "…"
    return text


def iso_date(value: str) -> str:
    """Data ISO curta: "2025-05-06T14:00:00Z" → "2025-05-06"; "2025-05" e "2025" ficam; inválida → ""."""
    found = _DATE_RE.match(value or "")
    if not found:
        return ""
    year, month, day = found.group(1), found.group(2), found.group(3)
    if not 1000 <= int(year) <= 2999:
        return ""
    if month is None:
        return year
    if not 1 <= int(month) <= 12:
        return ""
    if day is None:
        return f"{year}-{int(month):02d}"
    try:
        return date(int(year), int(month), int(day)).isoformat()
    except ValueError:
        return ""
