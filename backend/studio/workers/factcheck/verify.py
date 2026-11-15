"""Conferência literal: o trecho da fonte (`quote`) está mesmo na página (`url`)?

Origem: skill `factcheck-source-retrieval` ("verbatim gate": trecho de 5 a 30 palavras conferido no texto salvo da
página; texto de OCR conferido sem espaços; PDF à parte) e `motor/passos/check_script.py` (o trecho marcado tem de
estar no texto da página). A página é baixada só com `media/safe_http.fetch` (nada de rede interna). Quando o site
bloqueia o download direto, vale o texto salvo pela captura de página (`PageCapture.text_path`, bloco I).
"""

from __future__ import annotations

import codecs
import logging
import re
from pathlib import Path

import httpx

from studio.config import get_settings
from studio.db import session_scope
from studio.media import safe_http
from studio.models import PageCapture
from studio.workers.factcheck.textify import find_quote, html_to_text, words

log = logging.getLogger(__name__)

QUOTE_MIN_WORDS = 5
QUOTE_MAX_WORDS = 30

NO_URL = "sem link da fonte"
NO_QUOTE = "sem trecho literal da fonte"
PDF_NOTE = "PDF: confira o trecho à mão (o app ainda não lê PDF)"
LOOSE_NOTE = "achado ignorando os espaços (texto de OCR?)"
MISSING_NOTE = "o trecho não está na página: copie o texto exato da fonte (aspas curvas e retas contam)"
CAPTURE_NOTE = "conferido no texto da captura da página"
TRUNCATED_NOTE = "página grande demais: só o começo foi lido; confira à mão ou capture a página na aba Cenas"

_HTML_TYPES = ("text/html", "application/xhtml+xml")


def _length_note(quote: str) -> str:
    n = len(words(quote))
    if QUOTE_MIN_WORDS <= n <= QUOTE_MAX_WORDS:
        return ""
    return f"trecho com {n} {'palavra' if n == 1 else 'palavras'} (o certo é de {QUOTE_MIN_WORDS} a {QUOTE_MAX_WORDS})"


def _join(*notes: str) -> str:
    return "; ".join(n for n in notes if n)


def _same_url(a: str, b: str) -> bool:
    def clean(u: str) -> str:
        return u.strip().split("#", 1)[0].rstrip("/").lower()

    return clean(a) == clean(b)


def capture_text(url: str) -> str | None:
    """Texto salvo (innerText) da captura mais recente desta URL, se houver."""
    base = url.strip().split("#", 1)[0].rstrip("/")
    with session_scope() as s:
        rows = (
            s.query(PageCapture)
            .filter(PageCapture.url.in_([base, base + "/", url.strip()]), PageCapture.text_path != "")
            .order_by(PageCapture.created_at.desc())
            .all()
        )
        paths = [r.text_path for r in rows if _same_url(r.url, url)]
    data_dir = get_settings().data_dir
    for rel in paths:
        path = Path(rel) if Path(rel).is_absolute() else data_dir / rel
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return None


def _is_pdf(fetched: safe_http.Fetched) -> bool:
    return fetched.content_type == "application/pdf" or fetched.content[:5] == b"%PDF-"


# <meta charset="..."> ou <meta http-equiv="content-type" content="text/html; charset=...">
_META_CHARSET = re.compile(rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_.:-]+)""", re.IGNORECASE)


def _known(charset: str | None) -> str | None:
    if not charset:
        return None
    try:
        return codecs.lookup(charset).name
    except LookupError:  # charset desconhecido
        return None


def _decode(fetched: safe_http.Fetched) -> str:
    """Texto da resposta: charset do cabeçalho; senão o do <meta> (sites públicos em ISO-8859-1 só declaram ali);
    senão UTF-8, e windows-1252 quando o UTF-8 não serve."""
    content = fetched.content
    charset = _known(fetched.charset)
    if charset is None:
        meta = _META_CHARSET.search(content[:4096])
        charset = _known(meta.group(1).decode("ascii", "ignore")) if meta else None
        if charset in ("latin-1", "iso8859-1"):
            charset = "cp1252"  # como os navegadores: ISO-8859-1 declarado é lido como windows-1252
    if charset is not None:
        return content.decode(charset, errors="replace")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return content.decode("cp1252")
        except UnicodeDecodeError:
            return content.decode("utf-8", errors="replace")


def _is_html(fetched: safe_http.Fetched, raw: str) -> bool:
    head = raw[:500].lstrip().lower()
    return fetched.content_type in _HTML_TYPES or head.startswith(("<!doctype html", "<html"))


def page_text(fetched: safe_http.Fetched) -> str | None:
    """Texto da resposta (HTML vira texto); None quando não é página de texto."""
    raw = _decode(fetched)
    if _is_html(fetched, raw):
        return html_to_text(raw)
    if fetched.content_type.startswith("text/") or fetched.content_type in ("application/json", ""):
        return raw
    return None


def _judge(quote: str, text: str, length: str, origin: str = "") -> tuple[bool, str]:
    match = find_quote(quote, text)
    if match == "found":
        return True, _join(origin, length)
    if match == "loose":
        return True, _join(LOOSE_NOTE, origin, length)
    return False, _join(MISSING_NOTE, length)


def verify(url: str, quote: str, *, http: httpx.Client | None = None) -> tuple[bool | None, str]:
    """`(True, nota)` achado, `(False, nota)` não está na página, `(None, nota)` não deu para conferir."""
    url, quote = (url or "").strip(), (quote or "").strip()
    if not url:
        return None, NO_URL
    if not quote:
        return None, NO_QUOTE
    length = _length_note(quote)
    try:
        fetched = safe_http.fetch(url, max_bytes=safe_http.HTML_MAX_BYTES, accept=safe_http.ACCEPT_HTML,
                                  truncate=True, http=http)
    except safe_http.FetchError as exc:
        reason = (str(exc) or "erro").rstrip(".")
        saved = capture_text(url)
        if saved is not None:
            return _judge(quote, saved, length, CAPTURE_NOTE)
        return None, _join(f"a página não abriu ({reason}): confira à mão ou capture a página na aba Cenas", length)
    if _is_pdf(fetched):
        return None, _join(PDF_NOTE, length)
    text = page_text(fetched)
    if text is None:
        return None, _join(f"o link não é uma página de texto ({fetched.content_type or 'tipo desconhecido'})", length)
    found, note = _judge(quote, text, length)
    if found:
        return found, note
    raw = _decode(fetched)
    if _is_html(fetched, raw):
        # O trecho pode estar no que o filtro da matéria tira (cabeçalho, menu, rodapé fora do <article>).
        whole = html_to_text(raw, whole_page=True)
        if find_quote(quote, whole) != "missing":
            return _judge(quote, whole, length)
    # Página montada por JavaScript: o download direto não traz o texto que a captura (navegador) salvou.
    saved = capture_text(url)
    if saved is not None and find_quote(quote, saved) != "missing":
        return _judge(quote, saved, length, CAPTURE_NOTE)
    if len(fetched.content) >= safe_http.HTML_MAX_BYTES:
        # Só o começo da página foi baixado: o trecho pode estar depois do corte.
        return None, _join(TRUNCATED_NOTE, length)
    return found, note
