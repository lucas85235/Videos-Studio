"""Números na narração: onde estão na fala, quanto valem e como aparecem na legenda e nos cartões.

O português do Brasil é completo (números por extenso: "dois mil e vinte e três", "um milhão e meio");
nos outros idiomas só os algarismos são reconhecidos. Algarismo logo depois de nome ("GTA 6",
"iPhone 15") é parte do nome, não número.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from studio.core.text import normalize_token, words
from studio.providers.base import Word

_UNITS: dict[str, int] = {
    "zero": 0, "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "catorze": 14,
    "quatorze": 14, "quinze": 15, "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19,
    "vinte": 20, "trinta": 30, "quarenta": 40, "cinquenta": 50, "sessenta": 60, "setenta": 70,
    "oitenta": 80, "noventa": 90, "cem": 100, "cento": 100, "duzentos": 200, "duzentas": 200,
    "trezentos": 300, "trezentas": 300, "quatrocentos": 400, "quatrocentas": 400, "quinhentos": 500,
    "quinhentas": 500, "seiscentos": 600, "seiscentas": 600, "setecentos": 700, "setecentas": 700,
    "oitocentos": 800, "oitocentas": 800, "novecentos": 900, "novecentas": 900,
}  # fmt: skip
_MIL = "mil"
_SCALES_PT: dict[str, int] = {
    "milhao": 10**6, "milhoes": 10**6, "bilhao": 10**9, "bilhoes": 10**9, "trilhao": 10**12, "trilhoes": 10**12,
}  # fmt: skip
# Escalas depois de algarismos em outros idiomas ("200 million").
_SCALES_OTHER: dict[str, int] = {
    "thousand": 10**3, "million": 10**6, "millions": 10**6, "billion": 10**9, "billions": 10**9,
    "millon": 10**6, "millones": 10**6,
}  # fmt: skip
_ARTICLES = {"um", "uma"}
_HALF = {"meio", "meia"}
_CURRENCY = {"r$", "us$", "u$", "$", "€", "£"}
# Palavras que, mesmo com maiúscula no meio da frase (depois de dois-pontos, num título), não são nome.
_NOT_NAMES = {
    "em", "no", "na", "nos", "nas", "ate", "desde", "de", "do", "da", "entre", "por", "para", "pra", "ha",
    "sao", "foram", "e", "o", "a", "os", "as", "mais", "menos", "quase", "apenas", "so", "cerca", "ano",
    "anos", "in", "on", "since", "by", "the", "en", "el", "la",
}  # fmt: skip

_CLOSERS = "\"'”’»)]}"
_OPENERS = "\"'“‘«([{"
_CLAUSE_END = ".,;:!?…"
_DIGITS_RE = re.compile(r"^(r\$|us\$|u\$|\$|€|£)?([+\-−]?\d+(?:[.,]\d+)*)(.*)$", re.IGNORECASE)
_NUM_IN_TEXT_RE = re.compile(r"[+\-−]?\d+(?:[.,]\d+)*")
# Escalas escritas nos cartões ("475 MI", "2 BI", "10 MIL", "200 MI+").
_CARD_SCALES: dict[str, int] = {
    "mil": 10**3, "k": 10**3, "mi": 10**6, "milhao": 10**6, "milhoes": 10**6, "mm": 10**6,
    "bi": 10**9, "bilhao": 10**9, "bilhoes": 10**9, "tri": 10**12, "trilhao": 10**12, "trilhoes": 10**12,
    "thousand": 10**3, "million": 10**6, "millions": 10**6, "billion": 10**9, "billions": 10**9,
}  # fmt: skip
# Letras que podem vir grudadas no algarismo sem tirar o valor ('200mi', '24h', '2x', '1º', '1080p').
_DIGIT_SUFFIXES = {
    *_CARD_SCALES, "m", "b", "h", "min", "s", "x", "km", "kg", "o", "a", "th", "st", "nd", "rd", "p", "fps",
    "gb", "tb", "mb", "anos",
}  # fmt: skip


def _is_pt(lang: str) -> bool:
    return lang.lower().startswith("pt")


def _core(token: str) -> str:
    """Token sem aspas e parênteses nas pontas."""
    return token.strip().lstrip(_OPENERS).rstrip(_CLOSERS)


def _ends_clause(token: str) -> bool:
    core = _core(token)
    return bool(core) and core[-1] in _CLAUSE_END


def _ends_sentence(token: str) -> bool:
    core = _core(token)
    return bool(core) and core[-1] in ".!?…"


def _to_number(text: str, lang: str = "pt-BR") -> float | None:
    """'1.000' → 1000, '2,5' → 2.5 (pt); '1,000' → 1000 e '2.5' → 2.5 (en)."""
    s = text.replace("−", "-").strip()
    if not s:
        return None
    if lang.lower().startswith("en"):
        if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", s):
            s = s.replace(",", "")
    else:
        if re.fullmatch(r"[+-]?\d{1,3}(\.\d{3})+(,\d+)?", s):
            s = s.replace(".", "")
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _clean_value(value: float) -> int | float:
    return int(value) if float(value).is_integer() else value


def _digit_parts(token: str) -> tuple[str | None, str, str] | None:
    """(moeda, algarismos, resto) de um token com algarismos, ou None ('3D' e '5G' não são números)."""
    core = _core(token).rstrip(_CLAUSE_END) or _core(token)
    match = _DIGITS_RE.match(core)
    if not match:
        return None
    suffix = match.group(3)
    if any(ch.isalpha() for ch in suffix) and normalize_token(suffix) not in _DIGIT_SUFFIXES:
        return None
    return match.group(1), match.group(2), suffix


def _suffix_scale(suffix: str) -> int:
    """Escala grudada no algarismo: '200mi', '2bi', '10k', '475M' (M e B só em maiúscula)."""
    raw = suffix.strip().rstrip("+")
    if raw in ("M", "B", "K"):
        return {"M": 10**6, "B": 10**9, "K": 10**3}[raw]
    key = normalize_token(raw)
    return _CARD_SCALES.get(key, 1) if key not in ("m", "b") else 1


def _is_currency(token: str) -> bool:
    return _core(token).lower() in _CURRENCY


def _looks_like_name(tokens: list[str], index: int) -> bool:
    """O token antes de `index` é nome ("GTA", "iPhone", "Windows" no meio da frase)?"""
    if index <= 0:
        return False
    prev = tokens[index - 1]
    if _ends_clause(prev) or _is_currency(prev):
        return False
    letters = "".join(ch for ch in _core(prev) if ch.isalpha())
    if not letters:
        return False
    if len(letters) >= 2 and letters.isupper():
        return True
    if any(ch.isupper() for ch in letters[1:]):
        return True
    if letters[0].isupper():
        sentence_start = index - 1 == 0 or _ends_sentence(tokens[index - 2])
        return not sentence_start and normalize_token(prev) not in _NOT_NAMES
    return False


def is_number_token(tok: str, lang: str = "pt-BR") -> bool:
    """O token sozinho é número: algarismos ('2013', '475mi', '30%') ou, em pt, número por extenso.

    'um'/'uma' contam (o contexto decide se é artigo); o conectivo 'e' não conta.
    """
    if _digit_parts(tok) is not None:
        return True
    if not _is_pt(lang):
        return False
    key = normalize_token(tok)
    return key in _UNITS or key == _MIL or key in _SCALES_PT


@dataclass
class _Run:
    i0: int
    i1: int
    value: float
    has_digit: bool = False
    percent: bool = False
    scale_at: int | None = None
    """Índice do token da maior escala (milhão/bilhão) quando o número termina nela."""
    scale: int = 1


def _percent_after(tokens: list[str], end: int) -> int:
    """Consome 'por cento' depois do número; devolve o novo fim."""
    if (
        end + 1 < len(tokens)
        and not _ends_clause(tokens[end - 1])
        and normalize_token(tokens[end]) == "por"
        and normalize_token(tokens[end + 1]) == "cento"
    ):
        return end + 2
    return end


def _match_digits(tokens: list[str], i: int, lang: str, names: bool) -> _Run | None:
    j = i
    has_prefix = False
    if _is_currency(tokens[j]) and j + 1 < len(tokens) and _digit_parts(tokens[j + 1]) and not _ends_clause(tokens[j]):
        has_prefix = True
        j += 1
    parts = _digit_parts(tokens[j])
    if parts is None:
        return None
    currency, digits, suffix = parts
    if not names and not has_prefix and not currency and _looks_like_name(tokens, j):
        return None
    base = _to_number(digits, lang)
    if base is None:
        return None
    value = base * _suffix_scale(suffix)
    end = j + 1
    if end < len(tokens) and not _ends_clause(tokens[end - 1]):
        key = normalize_token(tokens[end])
        scale = (_SCALES_PT.get(key) or (1000 if key == _MIL else None)) if _is_pt(lang) else _SCALES_OTHER.get(key)
        if scale:
            value *= scale
            end += 1
    run = _Run(i, end, value, has_digit=True)
    if _is_pt(lang):
        new_end = _percent_after(tokens, end)
        run.percent = new_end != end
        run.i1 = new_end
    return run


def _match_words_pt(tokens: list[str], i: int) -> _Run | None:
    total = 0.0
    current = 0.0
    last_kind: str | None = None  # unit | mil | scale | half
    last_val = 0
    joined = False  # veio um "e": o próximo número soma ao atual
    big_scale = 1
    scale_at: int | None = None
    j = i
    n = len(tokens)
    while j < n:
        key = normalize_token(tokens[j])
        if key in _UNITS:
            if (last_kind == "unit" and not joined) or last_kind == "half":
                break  # "dois três": dois números seguidos sem "e"
            current += _UNITS[key]
            last_kind, last_val, joined = "unit", _UNITS[key], False
        elif key == _MIL:
            if last_kind in ("mil", "half") or joined:
                break
            current = (current or 1) * 1000
            total += current
            current = 0
            last_kind, last_val = "mil", 1000
        elif key in _SCALES_PT:
            if last_kind in (None, "half") or joined:
                break  # "milhões de pessoas" sozinho não é número
            scale = _SCALES_PT[key]
            current = (current or 1) * scale
            total += current
            current = 0
            last_kind, last_val = "scale", scale
            if scale > big_scale:
                big_scale, scale_at = scale, j
        elif key == "e" and last_kind in ("unit", "mil", "scale") and not joined and j + 1 < n and not _ends_clause(tokens[j]):
            nxt = normalize_token(tokens[j + 1])
            if nxt in _HALF and last_kind == "scale":
                total += last_val / 2  # "um milhão e meio"
                last_kind = "half"
                j += 2
                if _ends_clause(tokens[j - 1]):
                    break
                continue
            if nxt in _UNITS and (last_kind in ("mil", "scale") or _UNITS[nxt] < last_val):
                joined = True
                j += 1
                continue
            break
        else:
            break
        j += 1
        if _ends_clause(tokens[j - 1]):
            break
    if j == i:
        return None
    if j - i == 1 and normalize_token(tokens[i]) in _ARTICLES:
        return None  # "um dia", "uma vez": artigo
    run = _Run(i, j, total + current, scale_at=scale_at, scale=big_scale)
    new_end = _percent_after(tokens, j)
    run.percent = new_end != j
    run.i1 = new_end
    return run


def _runs(tokens: list[str], lang: str, names: bool = False) -> list[_Run]:
    out: list[_Run] = []
    i = 0
    pt = _is_pt(lang)
    while i < len(tokens):
        run = _match_digits(tokens, i, lang, names)
        if run is None and pt:
            run = _match_words_pt(tokens, i)
        if run is None:
            i += 1
            continue
        out.append(run)
        i = run.i1
    return out


def number_runs(tokens: list[str], lang: str = "pt-BR", *, names: bool = False) -> list[tuple[int, int, int | float]]:
    """Sequências de tokens que formam um número: [(início, fim exclusivo, valor)].

    `tokens` são palavras como as de `core.text.words` (pontuação grudada). Um número termina na
    pontuação ("treze." encerra). 'um'/'uma' sozinhos são artigo. Com `names=True`, o algarismo depois
    de nome ("GTA 6") também conta.
    """
    return [(r.i0, r.i1, _clean_value(r.value)) for r in _runs(tokens, lang, names)]


def notable_number_runs(tokens: list[str], lang: str = "pt-BR", *, names: bool = False) -> list[tuple[int, int, int | float]]:
    """Como `number_runs`, só com os números que valem um cartão: com algarismo, ou por extenso com 2 ou mais
    palavras ou valor ≥ 10 (a regra da legenda). Contagem pequena falada ("dois adiamentos", "três anos",
    "do zero") fica de fora."""
    return [
        (r.i0, r.i1, _clean_value(r.value))
        for r in _runs(tokens, lang, names)
        if r.has_digit or r.i1 - r.i0 >= 2 or r.value >= 10
    ]


def text_numbers(text: str, lang: str = "pt-BR", *, names: bool = False) -> list[int | float]:
    """Valores dos números de um texto, na ordem (usa `core.text.words`)."""
    return [value for _, _, value in number_runs(words(text), lang, names=names)]


def card_number(value: str) -> float | None:
    """Valor numérico do texto de um cartão: '475 MI' → 475e6, '200 MI+' → 200e6, '2×' → 2,
    '24 H' → 24, 'DEZ 2023' → 2023, 'R$ 1,5 BI' → 1.5e9. Sem algarismo, tenta o número por extenso."""
    text = (value or "").strip()
    if not text:
        return None
    match = _NUM_IN_TEXT_RE.search(text)
    if match is None:
        found = text_numbers(text, "pt-BR", names=True)
        return float(found[0]) if found else None
    base = _to_number(match.group(0))
    if base is None:
        return None
    rest = text[match.end() :]
    scale = 1
    word = re.match(r"\s*([A-Za-zÀ-ÿ]+)", rest)
    if word:
        raw = word.group(1)
        attached = rest[:1].isalpha()
        if raw in ("M", "B", "K") and attached:
            scale = {"M": 10**6, "B": 10**9, "K": 10**3}[raw]
        else:
            scale = _CARD_SCALES.get(normalize_token(raw), 1)
    return float(base * scale)


# --- legenda ---------------------------------------------------------------------------------


def _group(value: float, sep: str = ".") -> str:
    return f"{int(value):,}".replace(",", sep)


def _decimal(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _display(run: _Run, tokens: list[str]) -> str:
    value = run.value
    if run.scale_at is not None and run.scale > 1:
        group = value / run.scale
        if abs(group * 100 - round(group * 100)) < 1e-9:
            scale_word = _core(tokens[run.scale_at]).rstrip(_CLAUSE_END)
            number = str(int(group)) if float(group).is_integer() else _decimal(group)
            text = f"{number} {scale_word}"
            return text + ("%" if run.percent else "")
    if not float(value).is_integer():
        text = _decimal(value)
    elif 1000 <= value <= 2999:
        text = str(int(value))  # anos ("2013") sem separador
    elif value >= 1000:
        text = _group(value)
    else:
        text = str(int(value))
    return text + ("%" if run.percent else "")


def merge_number_words(words_in: list[Word], lang: str = "pt-BR") -> list[Word]:
    """Legenda: número por extenso vira algarismo numa palavra só ('dois mil e treze' → '2013').

    Só converte sequência com 2 ou mais palavras ou valor ≥ 10 ('dois adiamentos' fica). A palavra nova
    vai do início da primeira ao fim da última e mantém a pontuação das pontas. Fora do pt, nada muda.
    """
    if not _is_pt(lang) or not words_in:
        return list(words_in)
    tokens = [w.text for w in words_in]
    out: list[Word] = []
    i = 0
    for run in _runs(tokens, lang):
        out.extend(words_in[i : run.i0])
        size = run.i1 - run.i0
        if not run.has_digit and (size >= 2 or run.value >= 10):
            first, last = tokens[run.i0], tokens[run.i1 - 1]
            lead = first[: len(first) - len(first.lstrip(_OPENERS))]
            trail = re.search(r"[.,;:!?…]*[" + re.escape(_CLOSERS) + r"]*$", last.strip())
            text = lead + _display(run, tokens) + (trail.group(0) if trail else "")
            out.append(Word(text, words_in[run.i0].start, words_in[run.i1 - 1].end))
        else:
            out.extend(words_in[run.i0 : run.i1])
        i = run.i1
    out.extend(words_in[i:])
    return out


# --- fala (voz local) --------------------------------------------------------------------------
# As vozes locais (Kokoro, Windows) leem mal algarismos, moedas e porcentagens. Aqui o número com algarismo vira
# palavras para o texto que vai para a voz; a legenda continua com o roteiro (ver `core.speech`).

# Palavras de cada idioma para o que vem junto do número: (singular, plural). Idioma fora da tabela: só o número.
_SPEECH_WORDS: dict[str, dict[str, tuple[str, str]]] = {
    "pt": {
        "%": ("por cento", "por cento"), "r$": ("real", "reais"), "us$": ("dólar", "dólares"), "u$": ("dólar", "dólares"),
        "$": ("dólar", "dólares"), "€": ("euro", "euros"), "£": ("libra", "libras"),
        "h": ("hora", "horas"), "min": ("minuto", "minutos"), "s": ("segundo", "segundos"), "km": ("quilômetro", "quilômetros"),
        "kg": ("quilo", "quilos"), "m": ("metro", "metros"), "x": ("vez", "vezes"), "anos": ("ano", "anos"),
        "gb": ("gigabyte", "gigabytes"), "tb": ("terabyte", "terabytes"), "mb": ("megabyte", "megabytes"),
        "fps": ("quadro por segundo", "quadros por segundo"),
    },
    "en": {
        "%": ("percent", "percent"), "r$": ("real", "reais"), "us$": ("dollar", "dollars"), "u$": ("dollar", "dollars"),
        "$": ("dollar", "dollars"), "€": ("euro", "euros"), "£": ("pound", "pounds"),
        "h": ("hour", "hours"), "min": ("minute", "minutes"), "s": ("second", "seconds"), "km": ("kilometer", "kilometers"),
        "kg": ("kilo", "kilos"), "m": ("meter", "meters"), "x": ("time", "times"),
        "gb": ("gigabyte", "gigabytes"), "tb": ("terabyte", "terabytes"), "mb": ("megabyte", "megabytes"),
        "fps": ("frame per second", "frames per second"),
    },
    "es": {
        "%": ("por ciento", "por ciento"), "r$": ("real", "reales"), "us$": ("dólar", "dólares"), "u$": ("dólar", "dólares"),
        "$": ("dólar", "dólares"), "€": ("euro", "euros"), "£": ("libra", "libras"),
        "h": ("hora", "horas"), "min": ("minuto", "minutos"), "s": ("segundo", "segundos"), "km": ("kilómetro", "kilómetros"),
        "kg": ("kilo", "kilos"), "m": ("metro", "metros"), "x": ("vez", "veces"), "anos": ("año", "años"),
    },
    "fr": {"%": ("pour cent", "pour cent"), "€": ("euro", "euros"), "$": ("dollar", "dollars")},
    "it": {"%": ("per cento", "per cento"), "€": ("euro", "euro"), "$": ("dollaro", "dollari")},
    "de": {"%": ("Prozent", "Prozent"), "€": ("Euro", "Euro"), "$": ("Dollar", "Dollar")},
}  # fmt: skip
# Palavras femininas depois do número (pt): "2 horas" → "duas horas", "2 mil pessoas" → "duas mil pessoas".
_FEMININE_PT = {
    "hora", "horas", "vez", "vezes", "pessoa", "pessoas", "semana", "semanas", "libra", "libras", "tonelada", "toneladas",
    "pagina", "paginas", "coisa", "coisas", "mulher", "mulheres", "crianca", "criancas", "empresa", "empresas", "cidade",
    "cidades", "familia", "familias", "especie", "especies", "noite", "noites", "casa", "casas", "vitima", "vitimas",
    "temporada", "temporadas", "edicao", "edicoes", "guerra", "guerras", "copa", "copas", "partida", "partidas",
    "unidade", "unidades", "copia", "copias", "versao", "versoes", "missao", "missoes", "decada", "decadas",
}  # fmt: skip
_FEMININE_UNITS = {"h", "x"}
_FEMININE_SWAP = {
    "um": "uma", "dois": "duas", "duzentos": "duzentas", "trezentos": "trezentas", "quatrocentos": "quatrocentas",
    "quinhentos": "quinhentas", "seiscentos": "seiscentas", "setecentos": "setecentas", "oitocentos": "oitocentas",
    "novecentos": "novecentas",
}  # fmt: skip
_BIG_SCALE_WORDS = {
    "milhao", "milhoes", "bilhao", "bilhoes", "trilhao", "trilhoes", "million", "billion", "trillion", "millon",
    "millones",
}  # fmt: skip
# Sufixo de ordinal → é feminino?
_ORDINAL_SUFFIXES = {"o": False, "a": True, "º": False, "ª": True, "th": False, "st": False, "nd": False, "rd": False}


def _speech_lang(lang: str) -> str:
    return lang.lower().split("-")[0]


def _n2w_code(lang: str) -> str:
    base = _speech_lang(lang)
    return "pt_BR" if base == "pt" else base


def speech_words(lang: str) -> dict[str, tuple[str, str]]:
    """Palavras (singular, plural) de moedas, unidades e porcentagem no idioma (vazio fora da tabela)."""
    return _SPEECH_WORDS.get(_speech_lang(lang), {})


def _feminine(text: str) -> str:
    """'dois mil e duzentos' → 'duas mil e duzentas'; o que vem antes de milhão/bilhão fica no masculino."""
    tokens = text.split()
    out = []
    for i, tok in enumerate(tokens):
        nxt = normalize_token(tokens[i + 1]) if i + 1 < len(tokens) else ""
        out.append(_FEMININE_SWAP.get(tok, tok) if nxt not in _BIG_SCALE_WORDS else tok)
    return " ".join(out)


def _join_groups_pt(text: str) -> str:
    """O num2words separa os grupos com vírgula ('um bilhão, quinhentos milhões'), e na fala a vírgula vira pausa.
    O último grupo ganha 'e' quando não tem um ('um bilhão e quinhentos milhões')."""
    parts = text.split(", ")
    if len(parts) == 1:
        return text
    last = parts[-1]
    joiner = " " if " e " in f" {last} " else " e "
    return " ".join(parts[:-1]) + joiner + last


def number_words(value: float, lang: str = "pt-BR", *, year: bool = False, ordinal: bool = False,
                 feminine: bool = False) -> str | None:
    """Número por extenso no idioma (num2words). None se o idioma não é suportado."""
    try:
        from decimal import Decimal

        from num2words import num2words
    except ImportError:
        return None
    code = _n2w_code(lang)
    number: int | Decimal = int(value) if float(value).is_integer() else Decimal(repr(round(value, 6)))
    try:
        if ordinal and isinstance(number, int):
            text = num2words(number, lang=code, to="ordinal")
        elif year and isinstance(number, int) and code == "en" and 1100 <= number <= 2099 and number % 1000 >= 10:
            text = num2words(number, lang=code, to="year")  # "twenty thirteen"
        else:
            text = num2words(number, lang=code)
    except (NotImplementedError, OverflowError, ValueError, KeyError, TypeError):
        return None
    if code == "en":
        text = text.replace("-", " ")
    if code == "pt_BR":
        text = _join_groups_pt(text)
        if feminine:
            text = " ".join(w[:-1] + "a" if w.endswith("o") else w for w in text.split()) if ordinal else _feminine(text)
    return " ".join(text.replace(",", "").split())


def _unit_key(suffix: str) -> str:
    """Unidade grudada no algarismo ('h', 'km', 'º'); vazio quando é escala ('mi', '475M') ou não há."""
    raw = suffix.strip().rstrip("+").replace("%", "")
    if not raw or raw in ("M", "B", "K"):
        return ""
    if raw.lower() in ("º", "ª"):
        return raw.lower()
    key = normalize_token(raw)
    return "" if key in _CARD_SCALES and key not in ("m", "b") else key


def spoken_number_runs(tokens: list[str], lang: str = "pt-BR") -> list[tuple[int, int, str]]:
    """Números com algarismo nos tokens e como falá-los: [(início, fim exclusivo, fala)].

    Junta moeda, escala, porcentagem e unidade ('R$ 2 milhões' → 'dois milhões de reais', '30%' → 'trinta por
    cento', '24h' → 'vinte e quatro horas', '1º' → 'primeiro'). Algarismo depois de nome também conta ('GTA 6' →
    'GTA seis'). Números já por extenso ficam como estão. A pontuação das pontas é mantida.
    """
    table = speech_words(lang)
    pt = _speech_lang(lang) == "pt"
    out: list[tuple[int, int, str]] = []
    for run in _runs(tokens, lang, names=True):
        if not run.has_digit:
            continue
        group = tokens[run.i0 : run.i1]
        currency = _core(group[0]).lower() if _is_currency(group[0]) else None
        digit_index = 1 if currency else 0
        parts = _digit_parts(group[digit_index]) if digit_index < len(group) else None
        if parts is None:
            continue
        prefix, digits, suffix = parts
        currency = currency or (prefix.lower() if prefix else None)
        percent = run.percent or "%" in suffix
        unit = _unit_key(suffix)
        ordinal = unit in _ORDINAL_SUFFIXES
        nxt = normalize_token(tokens[run.i1]) if run.i1 < len(tokens) else ""
        feminine = pt and (unit in _FEMININE_UNITS or nxt in _FEMININE_PT or _ORDINAL_SUFFIXES.get(unit, False))
        plain_year = re.fullmatch(r"\d{4}", digits) is not None and not (currency or unit or percent)
        spoken = number_words(run.value, lang, year=plain_year, ordinal=ordinal, feminine=feminine)
        if spoken is None:
            continue
        if percent and "%" in table:
            spoken += " " + table["%"][1]
        if unit and not ordinal:
            names = table.get(unit)
            spoken += " " + ((names[0] if run.value == 1 else names[1]) if names else suffix.strip().rstrip("+"))
        if currency and currency in table:
            singular, plural = table[currency]
            big = normalize_token(spoken.split()[-1]) in _BIG_SCALE_WORDS
            link = " de" if big and _speech_lang(lang) in ("pt", "es") else ""
            spoken += f"{link} {singular if run.value == 1 else plural}"
        first, last = group[0], group[-1]
        lead = first[: len(first) - len(first.lstrip(_OPENERS))]
        trail = re.search(r"[.,;:!?…]*[" + re.escape(_CLOSERS) + r"]*$", last.strip())
        out.append((run.i0, run.i1, lead + spoken + (trail.group(0) if trail else "")))
    return out
