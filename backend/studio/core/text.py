"""Utilidades de texto: contagem de palavras, estimativas de duração e divisão em grupos."""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[\w'’\-]+", re.UNICODE)


def words(text: str) -> list[str]:
    """Palavras 'faladas' (sem pontuação solta), na ordem do texto."""
    return [w for w in text.split() if _WORD_RE.search(w)]


def word_count(text: str) -> int:
    return len(words(text))


def estimate_seconds(text: str, wpm: int = 150, speed: float = 1.0) -> float:
    count = word_count(text)
    if count == 0:
        return 0.0
    return count / (wpm * speed) * 60.0


def normalize_token(token: str) -> str:
    """Normaliza palavra para comparação (minúsculas, sem acentos e sem pontuação)."""
    token = unicodedata.normalize("NFKD", token.lower())
    token = "".join(ch for ch in token if not unicodedata.combining(ch))
    return re.sub(r"[^\w]", "", token)


def split_balanced(items: list[str], parts: int) -> list[list[str]]:
    """Divide uma lista em `parts` grupos contíguos, equilibrando pelo tamanho em caracteres."""
    parts = max(1, min(parts, len(items))) if items else 1
    if not items:
        return [[]]
    lengths = [len(x) + 1 for x in items]
    total = sum(lengths)
    groups: list[list[str]] = []
    current: list[str] = []
    acc = 0
    target_index = 1
    for item, length in zip(items, lengths, strict=True):
        current.append(item)
        acc += length
        remaining_items = len(items) - sum(len(g) for g in groups) - len(current)
        remaining_groups = parts - len(groups) - 1
        if remaining_groups > 0 and (acc >= total * target_index / parts or remaining_items == remaining_groups):
            if remaining_items >= remaining_groups:
                groups.append(current)
                current = []
                target_index += 1
    if current:
        groups.append(current)
    while len(groups) < parts:
        groups.append([])
    return groups


def split_word_indices(n_words: int, parts: int, texts: list[str] | None = None) -> list[tuple[int, int]]:
    """Faixas [início, fim) de índices de palavras para cada parte."""
    if n_words == 0:
        return [(0, 0)] * max(1, parts)
    tokens = texts if texts is not None else ["x"] * n_words
    groups = split_balanced(tokens, parts)
    ranges: list[tuple[int, int]] = []
    start = 0
    for group in groups:
        end = start + len(group)
        ranges.append((start, end))
        start = end
    return ranges


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]
