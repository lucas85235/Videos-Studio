"""Regiões do cartão de mapa: países (ISO 3166-1 alfa-3, "BRA") e estados dos EUA (ISO 3166-2, "US-TX").

Os nomes em pt/en/es vêm de `geo_regions.json`, gerado junto com os mapas do renderer por
`renderer/scripts/build-geo.mjs` (Natural Earth 1:110m e Census 1:10m, domínio público). Só existe aqui o que o
renderer sabe desenhar: país pequeno demais para a escala 1:110m (Singapura, Malta) não tem código.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import cache
from pathlib import Path
from typing import Any

_FILE = Path(__file__).with_name("geo_regions.json")
_NAME_LANGS = ("pt", "en", "es")


@cache
def regions() -> dict[str, dict[str, Any]]:
    """Código → {"pt", "en", "es", "a2"?, "alias"?}."""
    return json.loads(_FILE.read_text(encoding="utf-8"))


def _fold(text: str) -> str:
    """Sem acento, caixa nem pontuação: "Irã" = "ira", "E.U.A." = "e u a"."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return " ".join(re.findall(r"[a-z0-9]+", plain))


@cache
def _by_name() -> dict[str, str]:
    """Nome dobrado → código. Nome oficial vale mais que apelido; nome que serve a dois códigos fica com o primeiro."""
    out: dict[str, str] = {}
    data = regions()
    for code, entry in data.items():
        for lang in _NAME_LANGS:
            out.setdefault(_fold(entry[lang]), code)
    for code, entry in data.items():
        for alias in entry.get("alias", []):
            out.setdefault(_fold(alias), code)
    return out


@cache
def _by_alpha2() -> dict[str, str]:
    return {entry["a2"]: code for code, entry in regions().items() if entry.get("a2")}


def region_code(text: object) -> str | None:
    """Código da região a partir do código ("BRA", "br", "US-TX", "TX" não) ou do nome em pt/en/es ("Irã", "Iran",
    "Nova York"). None: não é uma região que o mapa sabe desenhar."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    data = regions()
    upper = raw.upper()
    if upper in data:
        return upper
    if len(upper) == 2 and upper.isalpha() and upper in _by_alpha2():
        return _by_alpha2()[upper]
    return _by_name().get(_fold(raw))


def region_name(code: str, lang: str) -> str:
    """Nome curto da região no idioma do vídeo (idioma sem nome usa o inglês); código desconhecido volta como está."""
    entry = regions().get(code)
    if entry is None:
        return code
    short = lang[:2].lower()
    return str(entry.get(short) or entry["en"])


def is_us_state(code: str) -> bool:
    return code.startswith("US-")


@cache
def _name_pattern() -> tuple[re.Pattern[str], dict[str, str]]:
    names = {name: code for name, code in _by_name().items() if len(name) >= 4}
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])"), names


def find_regions(text: str) -> list[str]:
    """Regiões citadas pelo nome no texto, na ordem em que aparecem, sem repetir ("o Brasil e a Argentina" →
    ["BRA", "ARG"]). Só nomes de 4 letras ou mais (evita "uk", "eua" casarem com palavras soltas)."""
    pattern, names = _name_pattern()
    out: list[str] = []
    for match in pattern.finditer(_fold(text)):
        code = names[match.group(0)]
        if code not in out:
            out.append(code)
    return out
