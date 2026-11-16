"""Conferência literal: o trecho da fonte (`quote`) está mesmo na página (`url`)?

Origem: skill `factcheck-source-retrieval` ("verbatim gate": trecho de 5 a 30 palavras conferido no texto salvo da
página; texto de OCR conferido sem espaços; PDF à parte) e `motor/passos/check_script.py` (o trecho marcado tem de
estar no texto da página). A página é baixada só com `media/safe_http.fetch` (nada de rede interna). Quando o site
bloqueia o download direto, vale o texto salvo pela captura de página (`PageCapture.text_path`, bloco I). PDF com
camada de texto é lido página por página (`pdftext`) e a nota diz em que página o trecho está; PDF escaneado fica para
a conferência à mão. O mesmo PDF citado por várias afirmações é baixado e lido uma vez só (memória por URL e conteúdo).
"""

from __future__ import annotations

import codecs
import hashlib
import logging
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path

import httpx

from studio.config import get_settings
from studio.db import session_scope
from studio.media import safe_http
from studio.models import PageCapture
from studio.workers.factcheck import pdftext
from studio.workers.factcheck.textify import QuoteMatch, find_quote, html_to_text, words

log = logging.getLogger(__name__)

QUOTE_MIN_WORDS = 5
QUOTE_MAX_WORDS = 30

NO_URL = "sem link da fonte"
NO_QUOTE = "sem trecho literal da fonte"
PDF_SCANNED_NOTE = ("PDF sem texto (parece escaneado, só imagem): o app não consegue ler; abra o PDF e confira o "
                    "trecho à mão")
PDF_TOO_BIG_NOTE = f"PDF grande demais (passa de {pdftext.PDF_MAX_BYTES // 1_000_000} MB): confira o trecho à mão"
PDF_UNREADABLE_NOTE = "o app não conseguiu ler o texto do PDF: confira o trecho à mão"
PDF_MISSING_NOTE = "o trecho não está no PDF: copie o texto exato da fonte (aspas curvas e retas contam)"
ACCEPT_PDF = "application/pdf,*/*;q=0.5"
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


# Linha de borda de página que é só número de página ("3", "- 3 -", "página 3 de 10", "3/10").
_PAGE_NUMBER = re.compile(r"^\W*(p[áa]g(ina)?\.?\s*)?\d{1,4}(\s*(de|of|/)\s*\d{1,4})?\W*$", re.IGNORECASE)
EDGE_LINES = 3
"""Até quantas linhas curtas (cabeçalho, rodapé, número de página) são puladas de cada lado da virada de página."""
EDGE_LINE_MAX_WORDS = 8
EDGE_CHARS = 4000
"""Só o fim de uma página e o começo da seguinte entram na busca pela virada (um trecho tem umas 30 palavras)."""


def _edge_trims(lines: list[str], *, from_end: bool) -> list[list[str]]:
    """As linhas sem 1, 2... linhas curtas da borda (o fim ou o começo da página); a 1ª opção tira só uma."""
    out: list[list[str]] = []
    rest = list(lines)
    for _ in range(EDGE_LINES):
        while rest and not (rest[-1] if from_end else rest[0]).strip():
            rest = rest[:-1] if from_end else rest[1:]  # linha em branco não conta
        if not rest:
            break
        edge = (rest[-1] if from_end else rest[0]).strip()
        if len(edge.split()) > EDGE_LINE_MAX_WORDS and not _PAGE_NUMBER.match(edge):
            break  # linha comprida: já é o texto corrido
        rest = rest[:-1] if from_end else rest[1:]
        out.append(rest)
    return out


def _page_turns(before: str, after: str) -> list[str]:
    """O fim de `before` emendado no começo de `after` pulando as linhas curtas da borda (rodapé de uma, cabeçalho da
    outra, número de página), que o `extract_text` põe no meio do texto corrido."""
    tail = before[-EDGE_CHARS:].split("\n")
    head = after[:EDGE_CHARS].split("\n")
    tails = [tail, *_edge_trims(tail, from_end=True)]
    heads = [head, *_edge_trims(head, from_end=False)]
    return ["\n".join(t) + "\n" + "\n".join(h) for t in tails for h in heads if t is not tail or h is not head]


def _find_in_pages(quote: str, pages: list[str]) -> tuple[str, QuoteMatch]:
    """Onde o trecho está (`"página 3"`, `"páginas 3 e 4"` quando atravessa a virada) e como foi achado. Cada texto é
    tentado também sem a quebra de linha depois do hífen ("bem-\\nestar" vira "bem-estar") e com a palavra quebrada
    juntada ("aconte-\\nceu" vira "aconteceu"). Na virada de página, também sem o rodapé, o cabeçalho e o número de
    página que ficam entre as duas partes."""
    def match(text: str) -> QuoteMatch:
        results = [find_quote(quote, text)]
        if results[0] != "found" and pdftext.dehyphenate(text) != text:
            results.append(find_quote(quote, pdftext.join_hyphen_breaks(text)))
            results.append(find_quote(quote, pdftext.dehyphenate(text)))
        if "found" in results:
            return "found"
        return "loose" if "loose" in results else "missing"

    def places():
        for i, page in enumerate(pages):
            yield f"página {i + 1}", page
        for i in range(len(pages) - 1):
            yield f"páginas {i + 1} e {i + 2}", pages[i] + "\n" + pages[i + 1]

    def turns():
        for i in range(len(pages) - 1):
            for text in _page_turns(pages[i], pages[i + 1]):
                yield f"páginas {i + 1} e {i + 2}", text

    loose: str | None = None
    for group in (places(), turns()):
        for where, text in group:
            result = match(text)
            if result == "found":
                return where, "found"
            if result == "loose" and loose is None:
                loose = where
    return (loose, "loose") if loose else ("", "missing")


PDF_CACHE_SIZE = 4
PDF_CACHE_SECONDS = 600.0
"""O mesmo PDF citado por várias afirmações é baixado inteiro e lido uma vez só (dentro de 10 minutos)."""

_pdf_cache: OrderedDict[tuple[str, str], tuple[float, pdftext.PdfText | str]] = OrderedDict()
_pdf_cache_lock = threading.Lock()
_pdf_key_locks: dict[tuple[str, str], threading.Lock] = {}


def _read_pdf(fetched: safe_http.Fetched, http: httpx.Client | None) -> tuple[pdftext.PdfText | str, bool]:
    """Texto do PDF, ou a nota de por que não deu para ler; e se o resultado pode ser guardado (falha de rede não)."""
    data = fetched.content
    if len(data) >= safe_http.HTML_MAX_BYTES:
        # O 1º download corta no limite de página; PDF cortado não abre (o índice fica no fim): baixa de novo inteiro.
        try:
            data = safe_http.fetch(fetched.url, max_bytes=pdftext.PDF_MAX_BYTES, accept=ACCEPT_PDF, http=http).content
        except safe_http.TooLarge:
            return PDF_TOO_BIG_NOTE, True
        except safe_http.FetchError as exc:
            reason = (str(exc) or "erro").rstrip(".")
            return f"o PDF não baixou inteiro ({reason}): confira o trecho à mão", False
    try:
        return pdftext.extract(data), True
    except pdftext.PdfError as exc:
        return f"{exc}: confira o trecho à mão", True


def _load_pdf(fetched: safe_http.Fetched, http: httpx.Client | None) -> pdftext.PdfText | str:
    """`_read_pdf` com memória por URL e conteúdo: conferências do mesmo PDF ao mesmo tempo esperam a 1ª leitura."""
    key = (fetched.url, hashlib.sha256(fetched.content).hexdigest())
    with _pdf_cache_lock:
        key_lock = _pdf_key_locks.setdefault(key, threading.Lock())
    with key_lock:
        with _pdf_cache_lock:
            hit = _pdf_cache.get(key)
            if hit is not None and time.monotonic() - hit[0] < PDF_CACHE_SECONDS:
                _pdf_cache.move_to_end(key)
                return hit[1]
        result, keep = _read_pdf(fetched, http)
        with _pdf_cache_lock:
            if keep:
                _pdf_cache[key] = (time.monotonic(), result)
                _pdf_cache.move_to_end(key)
                while len(_pdf_cache) > PDF_CACHE_SIZE:
                    _pdf_cache.popitem(last=False)
            _pdf_key_locks.pop(key, None)  # quem já pegou esta trava ainda espera nela; quem chega depois acha a memória
    return result


def clear_pdf_cache() -> None:
    with _pdf_cache_lock:
        _pdf_cache.clear()


def _verify_pdf(fetched: safe_http.Fetched, quote: str, length: str,
                http: httpx.Client | None) -> tuple[bool | None, str]:
    doc = _load_pdf(fetched, http)
    if isinstance(doc, str):
        return None, _join(doc, length)
    if not doc.has_text:
        return None, _join(PDF_UNREADABLE_NOTE if doc.failed_pages else PDF_SCANNED_NOTE, length)
    where, found = _find_in_pages(quote, doc.pages)
    if found == "found":
        return True, _join(f"achado no PDF, {where}", length)
    if found == "loose":
        return True, _join(LOOSE_NOTE, f"achado no PDF, {where}", length)
    if doc.cut:
        return None, _join(f"PDF longo: só as primeiras {len(doc.pages)} de {doc.total_pages} páginas foram lidas e "
                           "o trecho não está nelas; confira à mão", length)
    if doc.failed_pages:
        return None, _join(f"o trecho não está nas páginas lidas, mas {doc.failed_pages} de {len(doc.pages)} páginas "
                           "do PDF não leram: confira à mão", length)
    return False, _join(PDF_MISSING_NOTE, length)


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
        return _verify_pdf(fetched, quote, length, http)
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
