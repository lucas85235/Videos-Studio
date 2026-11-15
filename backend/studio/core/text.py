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


def span_ranges(spans: list[int], n_words: int) -> list[tuple[int, int]]:
    """Faixas [início, fim) das cenas a partir de `Scene.span`.

    Exatas quando a soma dos spans é `n_words`; proporcionais quando não é (tradução, texto editado ou
    alinhamento com outra contagem). Contíguas, cobrem 0..n_words e nunca andam para trás.
    """
    if not spans:
        return []
    total = sum(max(0, s) for s in spans)
    if total <= 0:
        return split_word_indices(n_words, len(spans))
    ranges: list[tuple[int, int]] = []
    acc = 0
    start = 0
    for index, span in enumerate(spans):
        acc += max(0, span)
        end = n_words if index == len(spans) - 1 else (acc if total == n_words else round(acc * n_words / total))
        end = max(start, min(end, n_words))
        ranges.append((start, end))
        start = end
    return ranges


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]


# --- batidas (uma frase ou ideia por cena) ---------------------------------------------------

# Depois destas palavras o corte não cai (preposição, artigo, conjunção que liga ao que vem).
_NO_CUT_AFTER = {
    "pt": {
        "de", "do", "da", "dos", "das", "o", "a", "os", "as", "um", "uma", "uns", "umas", "em", "no", "na",
        "nos", "nas", "num", "numa", "para", "pra", "por", "pelo", "pela", "pelos", "pelas", "e", "ou",
        "com", "sem", "ao", "aos", "à", "às", "que", "se", "sobre", "entre", "até", "desde", "sua", "seu",
        "suas", "seus", "meu", "minha", "nosso", "nossa", "este", "esta", "esse", "essa", "aquele",
        "aquela", "mais", "muito", "muita", "tão", "nem", "mas", "porque", "quando", "como", "onde",
    },
    "en": {
        "the", "a", "an", "of", "to", "in", "on", "at", "for", "by", "with", "and", "or", "but", "from",
        "into", "than", "that", "as", "its", "his", "her", "their", "our", "my", "your", "this", "these",
    },
    "es": {
        "de", "del", "el", "la", "los", "las", "un", "una", "en", "para", "por", "y", "o", "con", "sin",
        "al", "que", "se", "su", "sus", "mi", "este", "esta", "ese", "esa", "más", "muy", "pero",
    },
}  # fmt: skip
# Começo bom de nova parte (conjunção ou advérbio que abre oração).
_GOOD_CUT_BEFORE = {
    "pt": {"e", "mas", "porque", "quando", "que", "depois", "até", "enquanto", "só", "porém", "então", "com"},
    "en": {"and", "but", "because", "when", "while", "after", "until", "so", "then", "with"},
    "es": {"y", "pero", "porque", "cuando", "que", "después", "hasta", "mientras", "entonces", "con"},
}
_DE_AFTER_NUMBER = {"de", "do", "da", "dos", "das", "of", "del"}
# Palavra que pede o número logo depois ("anos | 90", "século | XIX", "temporada | 2").
_NUMBER_HEADS = {
    "ano", "anos", "decada", "decadas", "seculo", "seculos", "numero", "capitulo", "temporada", "parte", "geracao",
    "episodio", "fase", "nivel", "versao", "edicao", "volume", "year", "years", "decade", "century", "number",
    "chapter", "season", "part", "generation", "episode", "level", "version", "edition", "siglo", "generacion",
}  # fmt: skip
_ROMAN_RE = re.compile(r"^[IVXLC]{1,6}$")
# Números falados em inglês e espanhol (o leitor de números só entende algarismos nesses idiomas).
_SPOKEN_NUMBERS = {
    "en": {
        "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve",
        "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
        "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred", "hundreds", "thousand", "thousands",
        "million", "millions", "billion", "billions", "trillion", "trillions",
    },
    "es": {
        "cero", "uno", "una", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez", "once",
        "doce", "trece", "catorce", "quince", "dieciseis", "diecisiete", "dieciocho", "diecinueve", "veinte",
        "veintiuno", "veintidos", "veintitres", "veinticuatro", "veinticinco", "veintiseis", "veintisiete",
        "veintiocho", "veintinueve", "treinta", "cuarenta", "cincuenta", "sesenta", "setenta", "ochenta", "noventa",
        "cien", "ciento", "doscientos", "trescientos", "cuatrocientos", "quinientos", "seiscientos", "setecientos",
        "ochocientos", "novecientos", "mil", "millon", "millones", "billon", "billones",
    },
}  # fmt: skip
_SENTENCE_END = (".", "!", "?", "…")
_TRAILING_CLOSERS = "\"'”’»)]}"


def _lang_key(lang: str) -> str:
    key = lang.lower()[:2]
    return key if key in _NO_CUT_AFTER else "pt"


def _norm_set(items: set[str]) -> set[str]:
    return {normalize_token(x) for x in items}


def _spoken_number(token: str, key: str) -> bool:
    """Número falado em inglês ou espanhol ("seventy-five", "million", "millones")."""
    table = _SPOKEN_NUMBERS.get(key)
    if not table:
        return False
    parts = [normalize_token(p) for p in token.split("-")]
    return bool(parts) and all(p in table for p in parts if p) and any(parts)


def _roman(token: str) -> bool:
    return bool(_ROMAN_RE.match(token.strip().strip("\"'”’»),.;:!?…(«“‘")))


def _capitalized(token: str) -> bool:
    letters = [ch for ch in token if ch.isalpha()]
    return bool(letters) and letters[0].isupper()


def split_beats(
    text: str,
    *,
    wpm: int = 150,
    speed: float = 1.0,
    min_s: float = 1.8,
    max_s: float = 3.0,
    lang: str = "pt-BR",
) -> list[list[str]]:
    """Corta a narração em batidas (uma por cena), por frase.

    Devolve as palavras de `words(text)` em grupos contíguos: a soma dos tamanhos é `word_count(text)`
    e `len(batida)` é o `Scene.span`. Ordem: (1) separa as frases; (2) junta frase com menos de
    `min_s` à seguinte (a última à anterior); (3) divide frase com mais de `1.6 × max_s` num ponto
    válido: nunca dentro de número, depois de preposição/artigo/conjunção nem antes de "de" que segue
    número; depois de vírgula pode e é preferido, assim como antes de conjunção; cada parte com pelo
    menos `min_s`; (4) sem ponto válido, a frase fica inteira. Duração por `estimate_seconds`.
    """
    from studio.core.numbers import is_number_token, number_runs

    tokens = words(text)
    if not tokens:
        return []
    key = _lang_key(lang)
    no_cut_after = _norm_set(_NO_CUT_AFTER[key])
    good_before = _norm_set(_GOOD_CUT_BEFORE[key])
    runs = number_runs(tokens, lang)
    inside: set[int] = set()  # posições k com corte proibido (entre tokens k-1 e k)
    run_end: set[int] = set()  # posições logo depois de um número
    for i0, i1, _value in runs:
        inside.update(range(i0 + 1, i1))
        run_end.add(i1)

    def dur(n_words: int) -> float:
        return n_words / (wpm * speed) * 60.0 if n_words else 0.0

    def core(tok: str) -> str:
        return tok.rstrip(_TRAILING_CLOSERS)

    def number_like(tok: str) -> bool:
        return is_number_token(tok, lang) or _spoken_number(tok, key)

    # 1) frases (faixas [início, fim) em `tokens`)
    spans: list[tuple[int, int]] = []
    start = 0
    for index, tok in enumerate(tokens):
        if core(tok).endswith(_SENTENCE_END):
            spans.append((start, index + 1))
            start = index + 1
    if start < len(tokens):
        spans.append((start, len(tokens)))

    # 2) frase curta junta à seguinte; a última, à anterior
    merged: list[tuple[int, int]] = []
    carry: int | None = None
    for a, b in spans:
        a = carry if carry is not None else a
        carry = None
        if dur(b - a) < min_s:
            carry = a
            continue
        merged.append((a, b))
    if carry is not None:
        if merged:
            merged[-1] = (merged[-1][0], len(tokens))
        else:
            merged.append((carry, len(tokens)))

    def can_cut(k: int) -> bool:
        prev, nxt = tokens[k - 1], tokens[k]
        if k in inside:
            return False
        if normalize_token(prev) in no_cut_after:
            return False
        prev_core = core(prev)
        ends_punct = prev_core.endswith((",", ";", ":", *_SENTENCE_END))
        # depois de número só com pontuação ("dois adiamentos", "noventa milhões de", "seventy-five million views")
        if (k in run_end or number_like(prev)) and not ends_punct:
            return False
        if normalize_token(nxt) in _DE_AFTER_NUMBER and (k in run_end or number_like(prev)):
            return False
        if ends_punct:
            return True
        # nome com número ("GTA | 6", "GTA | VI") e palavra que pede número ("anos | 90", "século | XIX")
        head = normalize_token(prev) in _NUMBER_HEADS
        if (head or any(ch.isupper() for ch in prev)) and (number_like(nxt) or _roman(nxt)):
            return False
        # nome próprio de várias palavras ("Grand | Theft Auto", "Vice | City") fica junto
        return not (_capitalized(prev) and _capitalized(nxt))

    def score(a: int, k: int, b: int) -> float:
        prev_core = core(tokens[k - 1])
        value = abs(dur(k - a) - dur(b - k))
        if prev_core.endswith(_SENTENCE_END):
            value -= 1.5
        elif prev_core.endswith((",", ";", ":")):
            value -= 1.0
        if normalize_token(tokens[k]) in good_before:
            value -= 0.4
        return value

    # 3) e 4) divide as frases longas
    def divide(a: int, b: int) -> list[tuple[int, int]]:
        if dur(b - a) <= 1.6 * max_s:
            return [(a, b)]
        best: tuple[float, int] | None = None
        for k in range(a + 1, b):
            if dur(k - a) < min_s or dur(b - k) < min_s or not can_cut(k):
                continue
            candidate = (score(a, k, b), k)
            if best is None or candidate < best:
                best = candidate
        if best is None:
            return [(a, b)]
        k = best[1]
        return divide(a, k) + divide(k, b)

    beats: list[list[str]] = []
    for a, b in merged:
        for x, y in divide(a, b):
            beats.append(tokens[x:y])
    return beats
