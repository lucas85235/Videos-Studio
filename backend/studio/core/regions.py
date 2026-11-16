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


@cache
def _state_by_name() -> dict[str, str]:
    """Nome dobrado → estado dos EUA (para o nome que também é de país: "Georgia")."""
    out: dict[str, str] = {}
    for code, entry in regions().items():
        if is_us_state(code):
            for lang in _NAME_LANGS:
                out.setdefault(_fold(entry[lang]), code)
    return out


def region_code(text: object, *, states: bool = False) -> str | None:
    """Código da região a partir do código ("BRA", "br", "US-TX", "TX") ou do nome em pt/en/es ("Irã", "Iran",
    "Nova York"). None: não é uma região que o mapa sabe desenhar.

    Sigla de 2 letras e nome que servem a país e a estado ("CA" é Canadá e Califórnia, "GA" é Gabão e Geórgia, "Georgia"
    é país e estado) ficam com o país, a não ser com `states=True` (o mesmo mapa tem estado dos EUA; ver
    `region_codes`). Sigla que só é de estado ("TX") vira o estado."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if not raw:
        return None
    data = regions()
    upper = raw.upper()
    if upper in data:
        return upper
    if len(upper) == 2 and upper.isalpha():
        state = f"US-{upper}"
        if state in data and (states or upper not in _by_alpha2()):
            return state
        return _by_alpha2().get(upper)
    folded = _fold(raw)
    if states and folded in _state_by_name():
        return _state_by_name()[folded]
    return _by_name().get(folded)


def region_codes(texts: list[tuple[object, object]]) -> list[str | None]:
    """Códigos das regiões de um mapa, item a item (região, rótulo de reserva), lidos juntos: quando um item é estado
    dos EUA sem dúvida ("US-TX", "Texas"), sigla e nome ambíguos dos outros ("CA", "Georgia") também viram estado."""

    def one(region: object, label: object, states: bool) -> str | None:
        return region_code(region, states=states) or region_code(label, states=states)

    states = any((code := one(region, label, False)) is not None and is_us_state(code) for region, label in texts)
    return [one(region, label, states) for region, label in texts]


def region_name(code: str, lang: str) -> str:
    """Nome curto da região no idioma do vídeo (idioma sem nome usa o inglês); código desconhecido volta como está."""
    entry = regions().get(code)
    if entry is None:
        return code
    short = lang[:2].lower()
    return str(entry.get(short) or entry["en"])


def is_us_state(code: str) -> bool:
    return code.startswith("US-")


# Nomes que servem a dois lugares ou costumam ser outra coisa com maiúscula: a dica não arrisca. "georgia" (país e
# estado), "washington" (capital e estado), "jordan" e "chad" (nomes de pessoa em inglês).
_AMBIGUOUS_NAMES = {"georgia", "washington", "jordan", "chad"}
# Palavra antes do nome que diz que ele é rio, lago ou cidade ("rio Mississippi", "lago Michigan"), não a região.
_NOT_PLACE_BEFORE = {"rio", "river", "lago", "lake", "cidade", "city", "baia", "bay", "ilha", "island"}
# Pontuação depois da qual a palavra com maiúscula pode ser só início de frase.
_BREAKS = '.!?:;…"“(\n'


@cache
def _name_pattern() -> tuple[re.Pattern[str], dict[str, str]]:
    names = {name: code for name, code in _by_name().items() if len(name) >= 4}
    alternatives = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternatives})(?![a-z0-9])"), names


def find_regions(text: str) -> list[str]:
    """Regiões citadas pelo nome no texto, na ordem em que aparecem, sem repetir ("o Brasil e a Argentina" →
    ["BRA", "ARG"]). Só nome próprio (com maiúscula no texto) de 4 letras ou mais: "uk" e "eua" não casam com palavras
    soltas, nem "o peru de Natal" com o Peru. Nome que serve a dois lugares (Georgia, Washington) ou que costuma ser
    sobrenome ("Michael Jordan") fica de fora: é só uma dica para o storyboard, melhor faltar do que errar."""
    pattern, names = _name_pattern()
    tokens = list(re.finditer(r"\w+", text))
    folded: list[str] = []
    owner: list[int] = []  # token original de cada palavra dobrada
    for i, token in enumerate(tokens):
        for part in _fold(token.group(0)).split():
            folded.append(part)
            owner.append(i)
    joined = " ".join(folded)
    out: list[str] = []
    for match in pattern.finditer(joined):
        name = match.group(0)
        first = owner[joined.count(" ", 0, match.start())]
        original = tokens[first].group(0)
        if not original[:1].isupper():
            continue
        if name in _AMBIGUOUS_NAMES:
            continue
        if first > 0 and (_fold(tokens[first - 1].group(0)) in _NOT_PLACE_BEFORE
                          or _inside_name(text, tokens[first - 1], tokens[first])):
            continue
        code = names[name]
        if code not in out:
            out.append(code)
    return out


def _inside_name(text: str, prev: re.Match[str], token: re.Match[str]) -> bool:
    """O nome vem logo depois de outro nome próprio, no meio da frase ("Michael Jordan", "Real Madrid")?"""
    if not prev.group(0)[:1].isupper() or text[prev.end() : token.start()].strip():
        return False
    before = text[: prev.start()].rstrip()
    return bool(before) and before[-1] not in _BREAKS
