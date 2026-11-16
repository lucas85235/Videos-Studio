"""Glossário do canal na tradução: termos que não se traduzem ou que têm tradução fixa por idioma.

Fica em `Channel.settings["glossary"]` (editado em Canais). Entra no pedido de tradução (`glossary_prompt`) e é
conferido depois (`check_pairs`; os textos do projeto vêm de `authoring.glossary_issues`): o termo do roteiro
original que não aparece como combinado no texto traduzido vira aviso no projeto. A conferência só avisa, não mexe
na tradução.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from pydantic import BaseModel, Field, field_validator


class GlossaryEntry(BaseModel):
    term: str = Field(min_length=1, max_length=100)
    """Como aparece no roteiro do idioma principal ("Vulto", "dark channel")."""
    keep: bool = True
    """Não traduzir: fica igual em todos os idiomas sem tradução fixa."""
    translations: dict[str, str] = {}
    """Tradução fixa por idioma ({"en-US": "faceless channel"}); vale mais que `keep` naquele idioma."""
    note: str = Field(default="", max_length=300)
    """Contexto para o tradutor ("nome do canal", "gíria do nicho")."""

    @field_validator("term", "note")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("translations")
    @classmethod
    def _clean(cls, value: dict[str, str]) -> dict[str, str]:
        return {lang.strip(): " ".join(text.split()) for lang, text in value.items() if lang.strip() and text.strip()}

    def expected(self, lang: str) -> str | None:
        """Como o termo deve ficar em `lang` (None: tradução livre)."""
        if self.translations.get(lang):
            return self.translations[lang]
        base = lang.lower().split("-")[0]
        for code, text in self.translations.items():
            if code.lower().split("-")[0] == base:
                return text
        return self.term if self.keep else None


def glossary_of(settings: dict[str, Any] | None) -> list[GlossaryEntry]:
    """Glossário guardado no canal (entradas inválidas são puladas)."""
    out: list[GlossaryEntry] = []
    for raw in (settings or {}).get("glossary") or []:
        try:
            out.append(GlossaryEntry.model_validate(raw))
        except ValueError:
            continue
    return out


def _fold(text: str) -> str:
    """Minúsculas e sem acentos (a comparação não depende de caixa nem de acento)."""
    text = unicodedata.normalize("NFKD", text.casefold())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def contains(text: str, term: str) -> bool:
    """`term` aparece em `text` como palavra(s) inteira(s), sem diferenciar caixa e acento."""
    if not term.strip():
        return False
    pattern = r"(?<!\w)" + r"\s+".join(re.escape(part) for part in _fold(term).split()) + r"(?!\w)"
    return re.search(pattern, _fold(text)) is not None


def relevant(entries: list[GlossaryEntry], texts: list[str], lang: str) -> list[GlossaryEntry]:
    """Entradas que aparecem em algum dos textos e têm forma combinada em `lang`."""
    return [e for e in entries if e.expected(lang) is not None and any(contains(t, e.term) for t in texts)]


def glossary_prompt(entries: list[GlossaryEntry], texts: list[str], target_lang: str) -> str:
    """Bloco do glossário para o pedido de tradução (vazio quando nenhum termo aparece nos textos)."""
    lines = []
    for entry in relevant(entries, texts, target_lang):
        expected = entry.expected(target_lang)
        rule = (f'não traduza, mantenha "{entry.term}"' if expected == entry.term
                else f'traduza sempre como "{expected}"')
        lines.append(f'- "{entry.term}": {rule}' + (f" ({entry.note})" if entry.note else ""))
    if not lines:
        return ""
    return "Glossário do canal (obrigatório, respeite exatamente):\n" + "\n".join(lines)


def check_pairs(pairs: list[tuple[str, str, str, str]], entries: list[GlossaryEntry], lang: str) -> list[dict[str, str]]:
    """Termos não respeitados num idioma. `pairs`: (tipo, id, texto original, texto traduzido). O termo está no
    original e a forma combinada não está no traduzido: um aviso por termo e trecho."""
    issues: list[dict[str, str]] = []
    for kind, ident, original, translated in pairs:
        for entry in entries:
            expected = entry.expected(lang)
            if expected is None or not contains(original, entry.term) or contains(translated, expected):
                continue
            issues.append({
                "lang": lang, "kind": kind, "id": ident, "term": entry.term, "expected": expected,
                "message": f'"{entry.term}" devia aparecer como "{expected}"',
            })
    return issues

