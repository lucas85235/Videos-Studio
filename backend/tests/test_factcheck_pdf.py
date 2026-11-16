"""Checagem de fatos em PDF: texto lido página por página, PDF escaneado, protegido e grande demais.

Os PDFs são montados aqui mesmo, à mão (texto com a fonte Helvetica); nenhum serviço real é chamado.
"""

from __future__ import annotations

import io
import re

import httpx
import pytest

from studio.media import safe_http
from studio.workers.factcheck import pdftext
from studio.workers.factcheck.claims import check_all
from studio.workers.factcheck.verify import (
    LOOSE_NOTE,
    PDF_MISSING_NOTE,
    PDF_SCANNED_NOTE,
    PDF_TOO_BIG_NOTE,
    PDF_UNREADABLE_NOTE,
    _find_in_pages,
    clear_pdf_cache,
    verify,
)

PUBLIC_IP = "93.184.216.34"
PDF_URL = "https://dados.example.com/relatorio.pdf"
QUOTE = "o farol foi aceso em 1872 pela Marinha"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: [PUBLIC_IP])
    clear_pdf_cache()
    yield
    clear_pdf_cache()


def _escape(text: str) -> bytes:
    raw = text.encode("cp1252")
    return raw.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def make_pdf(pages: list[list[str] | None], *, padding: int = 0) -> bytes:
    """PDF mínimo: cada página é uma lista de linhas de texto, ou None para página só com desenho (escaneada).
    `padding` acrescenta bytes inúteis (um fluxo que ninguém usa) para testar os limites de tamanho."""
    objects: list[bytes] = []
    n = len(pages)
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(n))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
    for i, lines in enumerate(pages):
        if lines is None:
            stream = b"q 0.5 g 50 50 500 700 re f Q"
        else:
            parts = [b"BT /F1 12 Tf 14 TL 72 760 Td"]
            parts += [b"(" + _escape(line) + b") Tj T*" for line in lines]
            parts.append(b"ET")
            stream = b"\n".join(parts)
        objects.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> "
                       f"/Contents {5 + 2 * i} 0 R >>".encode())
        objects.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    if padding:
        objects.append(b"<< /Length %d >>\nstream\n" % padding + b"0" * padding + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def pdf_client(data: bytes, *, content_type: str = "application/pdf", calls: list[str] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        if str(request.url) != PDF_URL:
            return httpx.Response(404)
        return httpx.Response(200, headers={"content-type": content_type}, content=data)

    return httpx.Client(transport=httpx.MockTransport(handler))


REPORT = make_pdf([
    ["Relatório anual da capitania", "Sumário"],
    ["Histórico", "Segundo os registros, o farol foi aceso em 1872 pela Marinha."],
    ["Anexos", "Fotos e mapas", "O faroleiro disse: “a luz nunca apagou desde então”."],
])


# ------------------------------------------------------------------------------------------------
# Leitura do PDF
# ------------------------------------------------------------------------------------------------


def test_extract_reads_each_page():
    doc = pdftext.extract(REPORT)
    assert doc.total_pages == 3 and len(doc.pages) == 3 and not doc.cut and doc.has_text
    assert "Relatório anual" in doc.pages[0]
    assert "aceso em 1872" in doc.pages[1]


def test_extract_respects_page_and_char_limits():
    doc = pdftext.extract(REPORT, max_pages=2)
    assert len(doc.pages) == 2 and doc.total_pages == 3 and doc.cut
    doc = pdftext.extract(REPORT, max_chars=40)
    assert sum(len(p) for p in doc.pages) <= 40 and doc.cut


def test_extract_broken_and_password_pdf():
    with pytest.raises(pdftext.PdfError, match="não abriu"):
        pdftext.extract(b"%PDF-1.7 isto nao e um pdf")
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(REPORT)).pages:
        writer.add_page(page)
    writer.encrypt(user_password="segredo", owner_password="dono", algorithm="RC4-128")
    locked = io.BytesIO()
    writer.write(locked)
    with pytest.raises(pdftext.PdfError, match="senha"):
        pdftext.extract(locked.getvalue())
    # Só a senha de dono (abre sem pedir senha): lê normalmente.
    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(REPORT)).pages:
        writer.add_page(page)
    writer.encrypt(user_password="", owner_password="dono", algorithm="RC4-128")
    open_pdf = io.BytesIO()
    writer.write(open_pdf)
    assert "aceso em 1872" in pdftext.extract(open_pdf.getvalue()).pages[1]


# ------------------------------------------------------------------------------------------------
# Conferência no PDF
# ------------------------------------------------------------------------------------------------


def test_verify_pdf_finds_quote_and_says_the_page():
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(REPORT))
    assert (found, note) == (True, "achado no PDF, página 2")
    # aspas curvas e caixa diferente: mesma normalização do HTML
    found, note = verify(PDF_URL, 'O FAROLEIRO disse: "a luz nunca apagou desde então"', http=pdf_client(REPORT))
    assert (found, note) == (True, "achado no PDF, página 3")
    # sem content-type de PDF: reconhecido pelo começo do arquivo
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(REPORT, content_type="application/octet-stream"))
    assert (found, note) == (True, "achado no PDF, página 2")


def test_verify_pdf_missing_quote():
    found, note = verify(PDF_URL, "o farol foi apagado em 1990 pela Marinha", http=pdf_client(REPORT))
    assert (found, note) == (False, PDF_MISSING_NOTE)
    found, note = verify(PDF_URL, "o farol foi apagado", http=pdf_client(REPORT))
    assert found is False and note == PDF_MISSING_NOTE + "; trecho com 4 palavras (o certo é de 5 a 30)"


def test_verify_pdf_quote_across_lines_pages_and_hyphens():
    data = make_pdf([
        ["Capítulo 1", "No inverno daquele ano, o farol"],
        ["foi aceso em 1872 pela Marinha, que mantinha a ilha."],
        ["Mais tarde o farol foi aceso em 18-", "72 pela mari-", "nha do Império."],
    ])
    found, note = verify(PDF_URL, "o farol foi aceso em 1872 pela Marinha, que", http=pdf_client(data))
    assert (found, note) == (True, "achado no PDF, páginas 1 e 2")
    found, note = verify(PDF_URL, "o farol foi aceso em 1872 pela marinha do Império", http=pdf_client(data))
    assert (found, note) == (True, "achado no PDF, página 3")


def test_verify_pdf_loose_match():
    data = make_pdf([["ofarol foiaceso em1872 pela Marinha"]])
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(data))
    assert (found, note) == (True, f"{LOOSE_NOTE}; achado no PDF, página 1")


def test_verify_scanned_pdf_asks_for_manual_check():
    data = make_pdf([None, None])
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(data))
    assert (found, note) == (None, PDF_SCANNED_NOTE)
    assert "escaneado" in note and "à mão" in note


def test_verify_long_pdf_only_reads_the_first_pages(monkeypatch):
    monkeypatch.setattr(pdftext, "PDF_MAX_PAGES", 2)
    found, note = verify(PDF_URL, "Fotos e mapas do farol de 1872", http=pdf_client(REPORT))
    assert found is None and "só as primeiras 2 de 3 páginas" in note
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(REPORT))
    assert (found, note) == (True, "achado no PDF, página 2")


def test_verify_pdf_bigger_than_the_page_limit_is_downloaded_whole(monkeypatch):
    big = make_pdf([["Histórico", "Segundo os registros, o farol foi aceso em 1872 pela Marinha."]], padding=50_000)
    monkeypatch.setattr(safe_http, "HTML_MAX_BYTES", 20_000)  # o 1º download corta o PDF
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(big))
    assert (found, note) == (True, "achado no PDF, página 1")


def test_verify_pdf_too_big(monkeypatch):
    big = make_pdf([["Histórico", "o farol foi aceso em 1872 pela Marinha."]], padding=50_000)
    monkeypatch.setattr(safe_http, "HTML_MAX_BYTES", 20_000)
    monkeypatch.setattr(pdftext, "PDF_MAX_BYTES", 30_000)
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(big))
    assert (found, note) == (None, PDF_TOO_BIG_NOTE)


def test_verify_pdf_redownload_still_blocks_private_address(monkeypatch):
    big = make_pdf([["o farol foi aceso em 1872 pela Marinha."]], padding=50_000)
    monkeypatch.setattr(safe_http, "HTML_MAX_BYTES", 20_000)
    calls = []

    def resolve(host):
        calls.append(host)
        return [PUBLIC_IP] if len(calls) == 1 else ["10.0.0.5"]  # o 2º download cai na rede interna

    monkeypatch.setattr(safe_http, "resolve", resolve)
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(big))
    assert found is None and note.startswith("o PDF não baixou inteiro") and len(calls) == 2


# ------------------------------------------------------------------------------------------------
# Correções da revisão
# ------------------------------------------------------------------------------------------------


def test_extract_any_pypdf_error_on_open_is_a_pdf_error():
    # xref com tamanho exagerado: o teto anti-bomba do pypdf (LimitReachedError, que não é PdfReadError)
    huge = re.sub(rb"xref\n0 \d+", b"xref\n0 99999999", REPORT)
    with pytest.raises(pdftext.PdfError, match="não abriu"):
        pdftext.extract(huge)
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(huge))
    assert found is None and note.startswith("o PDF não abriu") and note.endswith("confira o trecho à mão")


def test_pages_that_fail_to_read_are_not_called_scanned(monkeypatch):
    from pypdf import PageObject

    def broken(self, *args, **kwargs):
        raise RuntimeError("conteúdo grande demais")

    monkeypatch.setattr(PageObject, "extract_text", broken)
    doc = pdftext.extract(REPORT)
    assert doc.failed_pages == 3 and not doc.has_text
    found, note = verify(PDF_URL, QUOTE, http=pdf_client(REPORT))
    assert (found, note) == (None, PDF_UNREADABLE_NOTE)
    assert "escaneado" not in note


def test_missing_quote_with_some_failed_pages_is_not_a_no(monkeypatch):
    from pypdf import PageObject

    real = PageObject.extract_text
    seen = []

    def flaky(self, *args, **kwargs):
        seen.append(1)
        if len(seen) == 2:
            raise RuntimeError("fonte quebrada")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(PageObject, "extract_text", flaky)
    found, note = verify(PDF_URL, "o farol foi apagado em 1990 pela Marinha", http=pdf_client(REPORT))
    assert found is None and "1 de 3 páginas" in note and note.endswith("confira à mão")


def test_extract_stops_when_reading_takes_too_long():
    doc = pdftext.extract(REPORT, max_seconds=0)
    assert len(doc.pages) == 1 and doc.cut and doc.total_pages == 3


def test_quote_across_page_turn_with_header_footer_and_page_number():
    pages = ["Relatório anual 3\no relatório diz que a taxa", "Relatório anual 4\ncaiu muito no ano"]
    assert _find_in_pages("o relatório diz que a taxa caiu muito no ano", pages) == ("páginas 1 e 2", "found")
    pages = ["Relatório anual\nNo inverno daquele ano, o farol\n\n- 7 -\nRelatório anual",
             "Capítulo 2 | Relatório anual\nfoi aceso em 1872 pela Marinha."]
    assert _find_in_pages("o farol foi aceso em 1872 pela Marinha", pages) == ("páginas 1 e 2", "found")
    data = make_pdf([
        ["Relatório anual da capitania", "No inverno daquele ano, o farol", "Página 3 de 10"],
        ["Relatório anual da capitania", "foi aceso em 1872 pela Marinha, que mantinha a ilha.", "Página 4 de 10"],
    ])
    found, note = verify(PDF_URL, "o farol foi aceso em 1872 pela Marinha", http=pdf_client(data))
    assert (found, note) == (True, "achado no PDF, páginas 1 e 2")
    # texto corrido de verdade não é pulado: o trecho que não está continua "não está"
    assert _find_in_pages("o farol caiu muito no ano", pages)[1] == "missing"


def test_compound_word_broken_at_line_end_keeps_the_hyphen():
    pages = ["a pesquisa mediu o bem-\nestar das famílias da ilha"]
    assert _find_in_pages("a pesquisa mediu o bem-estar das famílias", pages) == ("página 1", "found")
    # palavra quebrada comum continua achada juntando as duas partes
    assert _find_in_pages("Mais tarde o farol foi aceso em 1872", ["Mais tarde o farol foi aceso em 18-\n72"]) == (
        "página 1", "found")
    data = make_pdf([["a pesquisa mediu o bem-", "estar das famílias da ilha"]])
    found, note = verify(PDF_URL, "a pesquisa mediu o bem-estar das famílias", http=pdf_client(data))
    assert (found, note) == (True, "achado no PDF, página 1")


def test_same_pdf_is_downloaded_whole_and_read_once(monkeypatch):
    big = make_pdf([["Histórico", "Segundo os registros, o farol foi aceso em 1872 pela Marinha."]], padding=50_000)
    monkeypatch.setattr(safe_http, "HTML_MAX_BYTES", 20_000)
    reads = []
    real_extract = pdftext.extract

    def counting(data, **kwargs):
        reads.append(len(data))
        return real_extract(data, **kwargs)

    monkeypatch.setattr(pdftext, "extract", counting)
    calls: list[str] = []
    items = [{"url": PDF_URL, "quote": QUOTE} for _ in range(6)]
    results = check_all(items, http=pdf_client(big, calls=calls))
    assert results == [(True, "achado no PDF, página 1")] * 6
    assert len(reads) == 1
    assert len(calls) == 6 + 1  # o começo de cada conferência + um download inteiro só
