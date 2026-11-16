"""Texto de PDF para a conferência literal (o trecho está mesmo no documento?), página por página.

Usa o `pypdf` (licença BSD), que já traz tetos contra arquivo-bomba (fluxo comprimido que vira GB). Por cima disso:
tamanho do arquivo (`PDF_MAX_BYTES`, aplicado no download), número de páginas lidas (`PDF_MAX_PAGES`) e de letras
guardadas (`PDF_MAX_CHARS`), e tempo total de leitura (`PDF_MAX_SECONDS`). PDF escaneado (só imagem, sem camada de texto) não tem o que ler: quem chama pede a
conferência à mão.
"""

from __future__ import annotations

import io
import logging
import re
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

PDF_MAX_BYTES = 15_000_000
PDF_MAX_PAGES = 300
PDF_MAX_CHARS = 3_000_000
PDF_MAX_SECONDS = 60.0

# Palavra quebrada no fim da linha ("aconte-\nceu"): outra leitura junta as duas partes; palavra composta
# ("bem-\nestar") só perde a quebra de linha e mantém o hífen.
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")


class PdfError(Exception):
    """O PDF não abriu (corrompido, protegido por senha...). Mensagem em português em `args[0]`."""


@dataclass
class PdfText:
    pages: list[str] = field(default_factory=list)
    """Texto de cada página lida, na ordem (a página 1 é `pages[0]`)."""
    total_pages: int = 0
    cut: bool = False
    """True quando nem todas as páginas foram lidas (limite de páginas, de letras ou de tempo)."""
    failed_pages: int = 0
    """Páginas cujo texto deu erro na leitura (ficam vazias em `pages`)."""

    @property
    def has_text(self) -> bool:
        return any(ch.isalnum() for page in self.pages for ch in page)


def dehyphenate(text: str) -> str:
    """Junta a palavra quebrada no fim da linha: "aconte-\\nceu" vira "aconteceu"."""
    return _HYPHEN_BREAK.sub(r"\1\2", text)


def join_hyphen_breaks(text: str) -> str:
    """Tira só a quebra de linha depois do hífen: "bem-\\nestar" vira "bem-estar" (palavra composta)."""
    return _HYPHEN_BREAK.sub(r"\1-\2", text)


def extract(data: bytes, *, max_pages: int | None = None, max_chars: int | None = None,
            max_seconds: float | None = None) -> PdfText:
    """Texto das primeiras `max_pages` páginas (até `max_chars` letras no total e `max_seconds` de leitura)."""
    from pypdf import PdfReader  # aqui: só a checagem de PDF carrega a biblioteca

    max_pages = PDF_MAX_PAGES if max_pages is None else max_pages
    max_chars = PDF_MAX_CHARS if max_chars is None else max_chars
    max_seconds = PDF_MAX_SECONDS if max_seconds is None else max_seconds
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):  # PDF com senha de dono abre com a senha vazia
            raise PdfError("o PDF é protegido por senha")
        total = len(reader.pages)
    except PdfError:
        raise
    except Exception as exc:  # noqa: BLE001 - arquivo da internet: qualquer erro do pypdf (até o teto anti-bomba)
        log.info("PDF não abriu: %s: %s", type(exc).__name__, exc)
        raise PdfError("o PDF não abriu (arquivo corrompido ou num formato que o app não lê)") from exc
    out = PdfText(total_pages=total, cut=total > max_pages)
    size = 0
    deadline = time.monotonic() + max_seconds
    for number in range(min(total, max_pages)):
        if number and time.monotonic() > deadline:
            out.cut = True  # leitura lenta demais: fica com as páginas já lidas
            break
        try:
            text = reader.pages[number].extract_text() or ""
        except Exception as exc:  # noqa: BLE001 - página quebrada não derruba as outras
            log.debug("página %s do PDF não leu: %s", number + 1, exc)
            out.failed_pages += 1
            text = ""
        if size + len(text) > max_chars:
            out.pages.append(text[: max_chars - size])
            out.cut = True
            break
        out.pages.append(text)
        size += len(text)
    return out
