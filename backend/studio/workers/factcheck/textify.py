"""Texto de página e comparação literal do trecho da fonte (o "verbatim gate" da checagem).

Origem: skill `factcheck-source-retrieval` (texto da página por `HTMLParser`, sem script/style/nav; aspas curvas e
retas quebram a comparação exata; texto de OCR junta palavras) e o `norm` do `FIND_JS` em `motor/passos/shot_helper.py`
(‘ ’ ʼ → ', “ ” → ", U+2010 a U+2015 e U+2212 → -, espaços juntos, minúsculas).
"""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser
from typing import Literal

# Código e desenho: nunca são texto da página.
CODE_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})
# Partes da página que não são o texto da matéria (menu, rodapé, cabeçalho do site). `form` não entra: sites em
# ASP.NET WebForms põem a página inteira dentro de um <form>.
SKIP_TAGS = CODE_TAGS | {"nav", "footer", "header", "aside"}
# `header` e `footer` dentro da matéria (título, linha fina, crédito) são texto dela, como no innerText do FIND_JS.
CONTENT_TAGS = frozenset({"article", "main"})
_IN_CONTENT_KEEP = frozenset({"header", "footer"})
# Elementos que quebram a linha: o texto de dois parágrafos não gruda.
BLOCK_TAGS = frozenset({
    "address", "article", "blockquote", "br", "caption", "dd", "div", "dl", "dt", "figcaption", "figure", "h1", "h2",
    "h3", "h4", "h5", "h6", "hr", "li", "main", "ol", "p", "pre", "section", "table", "td", "th", "title", "tr", "ul",
})  # fmt: skip
VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})

_QUOTES = str.maketrans({
    "‘": "'", "’": "'", "ʼ": "'", "‚": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "«": '"', "»": '"', "″": '"',
    **{chr(c): "-" for c in range(0x2010, 0x2016)}, "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ", " ": " ",
    "​": "", "‌": "", "‍": "", "⁠": "", "﻿": "", "­": "",
})  # fmt: skip
_SPACES = re.compile(r"\s+")

QuoteMatch = Literal["found", "loose", "missing"]


class _TextParser(HTMLParser):
    def __init__(self, skip_tags: frozenset[str] = SKIP_TAGS) -> None:
        super().__init__(convert_charrefs=True)  # converte &amp;, &nbsp;, &#8217;... (o html.unescape)
        self.parts: list[str] = []
        self.skip: list[str] = []
        self.skip_tags = skip_tags
        self.content = 0  # quantos <article>/<main> estão abertos

    def _skips(self, tag: str) -> bool:
        if tag not in self.skip_tags:
            return False
        return bool(self.skip) or not (tag in _IN_CONTENT_KEEP and self.content > 0)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._skips(tag):
            self.skip.append(tag)
            return
        if tag in CONTENT_TAGS and not self.skip:
            self.content += 1
        if (tag in BLOCK_TAGS or tag in _IN_CONTENT_KEEP) and not self.skip:
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/>, <img/>, <path/> dentro do svg: não abrem bloco ignorado
        if tag in BLOCK_TAGS and not self.skip:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.skip:
            while self.skip and self.skip.pop() != tag:
                pass
            return
        if self.skip:
            return
        if tag in CONTENT_TAGS and self.content:
            self.content -= 1
        if tag in BLOCK_TAGS or tag in _IN_CONTENT_KEEP:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def html_to_text(html: str, *, whole_page: bool = False) -> str:
    """Texto visível de uma página HTML, uma linha por bloco, sem menus, rodapés e scripts. `whole_page=True` só tira
    código (script, style, svg): é a 2ª tentativa quando o trecho não está no texto da matéria."""
    parser = _TextParser(CODE_TAGS if whole_page else SKIP_TAGS)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:  # noqa: BLE001 - HTML quebrado: fica o que deu para ler
        pass
    lines = (" ".join(line.split()) for line in "".join(parser.parts).split("\n"))
    return "\n".join(line for line in lines if line)


def normalize(s: str) -> str:
    """Forma de comparação: NFKC, aspas e traços retos, espaço duro vira espaço, espaços juntos e `casefold`."""
    text = unicodedata.normalize("NFKC", s or "").translate(_QUOTES)
    return _SPACES.sub(" ", text).strip().casefold()


def find_quote(quote: str, text: str) -> QuoteMatch:
    """`found`: o trecho está no texto (normalizado); `loose`: só achado sem nenhum espaço (OCR que junta palavras);
    `missing`: não está."""
    q = normalize(quote)
    if not q:
        return "missing"
    t = normalize(text)
    if q in t:
        return "found"
    if q.replace(" ", "") in t.replace(" ", ""):
        return "loose"
    return "missing"


def words(s: str) -> list[str]:
    """Palavras do trecho (separadas por espaço), como a regra de 5 a 30 palavras conta."""
    return [w for w in normalize(s).split(" ") if any(ch.isalnum() for ch in w)]


def _clusters(text: str) -> list[tuple[int, str]]:
    """`(posição, letra com os acentos combinantes que a seguem)` de cada letra do texto."""
    out: list[tuple[int, str]] = []
    for pos, ch in enumerate(text):
        if out and unicodedata.combining(ch) and not out[-1][1].isspace():
            out[-1] = (out[-1][0], out[-1][1] + ch)
        else:
            out.append((pos, ch))
    return out


def literal_span(excerpt: str, text: str) -> str | None:
    """O pedaço literal de `text` que corresponde a `excerpt` (ignorando espaços a mais, aspas, traços e caixa), ou
    None. Serve para guardar o trecho exatamente como está no roteiro (a correção troca esse texto)."""
    if not excerpt.strip():
        return None
    if excerpt in text:
        return excerpt
    # Compara letra a letra na forma normalizada, guardando onde cada uma começa no texto original. A letra leva junto
    # os acentos soltos que vêm depois dela (texto colado do macOS, "c" + cedilha): o NFKC só os compõe juntos.
    index: list[int] = []
    folded: list[str] = []
    last_space = True
    for pos, ch in _clusters(text):
        piece = normalize(ch) if not ch.isspace() else " "
        if piece == " ":
            if last_space:
                continue
            last_space = True
        else:
            last_space = False
        for c in piece:
            folded.append(c)
            index.append(pos)
    hay = "".join(folded)
    needle = normalize(excerpt)
    at = hay.find(needle)
    if at < 0 or not needle:
        return None
    start, end = index[at], index[at + len(needle) - 1] + 1
    while end < len(text) and unicodedata.combining(text[end]):  # acentos soltos da última letra
        end += 1
    return text[start:end]
