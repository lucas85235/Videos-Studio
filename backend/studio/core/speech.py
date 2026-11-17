"""Texto falado: o que vai para a voz, separado do roteiro (que continua na legenda e nos cartões).

Duas camadas:

1. **Dicionário de pronúncia do canal** (Canais › Pronúncia): "termo → como falar", por idioma ou para todos.
   Vale para todas as vozes, porque é o usuário dizendo como a palavra soa.
2. **Normalização automática** (só nas vozes locais, que leem mal o texto cru): números, moedas, porcentagens,
   datas, horas, siglas, símbolos e estrangeirismos comuns. As vozes de API (Gemini, ElevenLabs, Chirp) já
   fazem isso bem e recebem o texto como está.

`speech_plan` devolve o texto falado e o mapa de grupos (palavras do roteiro ↔ palavras faladas). Com o mapa,
os tempos das palavras faladas voltam para as palavras do roteiro (`script_words`): a legenda nunca mostra
"dois mil e vinte e cinco" no lugar de "2025".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from studio.core.numbers import number_words, speech_words, spoken_number_runs
from studio.core.text import normalize_token, words
from studio.providers.base import Word

SPEECH_VERSION = 1
"""Muda quando a normalização automática muda de um jeito que pede gravar de novo. Entra na chave do cache da
narração quando o texto falado é diferente do roteiro (`Builder.narration_inputs`)."""

_WORD_RE = re.compile(r"[\w'’\-]+", re.UNICODE)
_OPENERS = "\"'“‘«([{"
_TRAIL_RE = re.compile(r"[.,;:!?…\"'”’»)\]}]+$")
_PLACEHOLDER = "§"


class PronunciationEntry(BaseModel):
    """Uma linha do dicionário de pronúncia do canal."""

    term: str = Field(min_length=1, max_length=100)
    """Como está escrito no roteiro ("Nvidia", "GTA 6"). Pode ter mais de uma palavra."""
    say: str = Field(min_length=1, max_length=200)
    """Como a voz deve falar ("envídia")."""
    lang: str = Field(default="", max_length=20)
    """Idioma ("pt-BR", ou só "pt"); vazio vale para todos."""
    voices: Literal["all", "local", "api"] = "all"
    """Para quais vozes a troca vale: todas, só as locais (Kokoro, Windows, Qwen3: precisam de grafia fonética, como
    "gê tê á seis") ou só as de API (ElevenLabs, Gemini, Chirp: leem siglas e números sozinhas e soletram devagar
    uma grafia fonética feita para a voz local)."""

    @field_validator("term", "say", "lang")
    @classmethod
    def _strip(cls, value: str) -> str:
        return " ".join(value.split())

    def for_voice(self, local: bool) -> bool:
        """A troca vale para esta voz? (`local`: voz local, como a da prévia.)"""
        return self.voices == "all" or (self.voices == "local") == local

    def applies_to(self, lang: str) -> bool:
        if not self.lang:
            return True
        mine, other = self.lang.lower(), lang.lower()
        return other == mine or other.split("-")[0] == mine.split("-")[0] and "-" not in mine


def pronunciation_of(settings: dict[str, Any] | None) -> list[PronunciationEntry]:
    """Dicionário de pronúncia guardado em `Channel.settings["pronunciation"]` (entradas inválidas são puladas)."""
    out: list[PronunciationEntry] = []
    for raw in (settings or {}).get("pronunciation") or []:
        try:
            out.append(PronunciationEntry.model_validate(raw))
        except ValueError:
            continue
    return out


# --- regras automáticas (vozes locais) -------------------------------------------------------

# Estrangeirismos comuns, escritos como se leem em português (o conversor de fonemas da voz local lê como
# português e erra "YouTube", "streaming"...). O dicionário do canal vem antes e pode trocar qualquer um.
FOREIGN_PT: dict[str, str] = {
    "youtube": "iutúbi", "youtuber": "iutúber", "youtubers": "iutúbers", "google": "gúgol", "facebook": "feicibúqui",
    "instagram": "instagrâm", "tiktok": "tiquetóque", "whatsapp": "uótsápi", "netflix": "nétiflix", "twitter": "tuíter",
    "iphone": "aifôni", "apple": "épou", "microsoft": "maicrossófti", "windows": "uíndous", "playstation": "pleisteitchon",
    "xbox": "équisbóx", "amazon": "ámazon", "spotify": "ispótifai", "online": "onláine", "offline": "ofiláine",
    "show": "xôu", "shows": "xôus", "streaming": "istrímin", "podcast": "pódquést", "site": "sáite", "sites": "sáites",
    "app": "épi", "apps": "épis", "smartphone": "ismartifôun", "software": "sófituér", "hardware": "rárduer",
    "download": "dáunloud", "upload": "âploud", "mouse": "máusi", "notebook": "nôutibúqui", "design": "dizáin",
    "designer": "dizáiner", "marketing": "márquetin", "feedback": "fídibéqui", "hacker": "ráquer", "hackers": "ráquers",
    "startup": "istartâpi", "startups": "istartâpis", "ok": "oquêi", "e-mail": "imêil", "email": "imêil",
    "wi-fi": "uaifai", "wifi": "uaifai", "game": "guêimi", "games": "guêimis", "gamer": "guêimer", "gamers": "guêimers",
    "trailer": "trêiler", "spoiler": "ispóiler", "spoilers": "ispóilers", "fake": "fêiqui", "news": "níus",
    "fake news": "fêiqui níus", "chatgpt": "tchét gê pê tê", "openai": "ôupen ei ai", "nvidia": "envídia",
    "tesla": "tésla", "elon": "ílon", "musk": "mâsqui", "bitcoin": "bitcóin", "blockchain": "blóquitchêin",
    "ceo": "ci-i-ôu", "big tech": "big téqui", "big techs": "big téquis", "deepfake": "dípfêiqui",
}  # fmt: skip

# Siglas que se leem como palavra (as outras são soletradas quando não dá para ler: "FBI", "EUA", "IBGE").
_ACRONYM_AS_WORD = {"ONU", "NASA", "OTAN", "FIFA", "UNESCO", "UNICEF", "USP", "PIB", "CIA", "OVNI", "AIDS", "LED",
                    "ENEM", "SUS", "EU", "ONG", "ONGS", "PETROBRAS", "EMBRAER", "MERCOSUL", "DETRAN", "INMETRO",
                    "BRICS", "FIES", "UNICAMP", "FAPESP", "NAFTA", "FUNAI", "ANVISA", "ANATEL", "SENAI", "PROCON"}
# Palavras curtas em maiúsculas que são palavra comum (ênfase), não sigla. As outras de 2 letras são soletradas
# ("TV", "IA", "SP").
_SHORT_WORDS = {"DE", "DO", "DA", "EM", "NO", "NA", "UM", "OU", "AO", "OS", "AS", "EU", "SE", "JÁ", "SÓ", "NÃO",
                "SÃO", "LÁ", "AÍ", "É"}
# Grupos de consoantes que começam sílaba em português ("GRANDE", "PLANO", "CHAVE") e consoantes que podem fechar
# sílaba ("NUNCA", "SEMPRE", "ISSO"). Palavra que segue isso é pronunciável: é ênfase, não sigla.
_ONSETS = {"BR", "CR", "DR", "FR", "GR", "PR", "TR", "VR", "BL", "CL", "FL", "GL", "PL", "CH", "LH", "NH", "QU", "GU"}
_CODAS = set("SRLMNZX")
# Algarismo romano sem I, V ou X ("CD", "DC", "MD" costumam ser siglas) só conta depois destas palavras.
_ROMAN_CONTEXT = {"século", "seculo", "dom", "papa", "rei", "rainha", "capítulo", "capitulo", "parte", "volume",
                  "ato", "livro"}
_LETTERS_PT = {
    "A": "á", "B": "bê", "C": "cê", "D": "dê", "E": "é", "F": "éfe", "G": "gê", "H": "agá", "I": "í", "J": "jóta",
    "K": "cá", "L": "éle", "M": "ême", "N": "êne", "O": "ó", "P": "pê", "Q": "quê", "R": "érre", "S": "ésse", "T": "tê",
    "U": "ú", "V": "vê", "W": "dáblio", "X": "xis", "Y": "ípsilon", "Z": "zê",
}  # fmt: skip
_VOWELS = set("AEIOU")

_SYMBOLS: dict[str, dict[str, str]] = {
    "pt": {"&": "e", "+": "mais", "=": "igual a", "%": "por cento", "@": "arroba", "#": "réxitégui", "vs": "versus",
           "vs.": "versus", "°": "graus", "km/h": "quilômetros por hora", "°c": "graus Celsius", "ºc": "graus Celsius"},
    "en": {"&": "and", "+": "plus", "=": "equals", "%": "percent", "@": "at", "#": "hashtag", "vs": "versus",
           "vs.": "versus", "°": "degrees", "km/h": "kilometers per hour", "°c": "degrees Celsius", "ºc": "degrees Celsius"},
    "es": {"&": "y", "+": "más", "=": "igual a", "%": "por ciento", "@": "arroba", "#": "hashtag", "vs": "versus",
           "vs.": "versus", "°": "grados", "km/h": "kilómetros por hora", "°c": "grados Celsius", "ºc": "grados Celsius"},
}  # fmt: skip
# Unidade solta depois de número ("100 km", "5 kg").
_UNITS_AFTER_NUMBER = {"km", "kg", "m", "h", "min", "gb", "mb", "tb", "fps"}

_MONTHS: dict[str, list[str]] = {
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
           "novembro", "dezembro"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
           "noviembre", "diciembre"],
}  # fmt: skip
_DATE_RE = re.compile(r"^(\d{1,2})/(\d{1,2})(?:/(\d{4}|\d{2}))?$")
_SLASH_RE = re.compile(r"^(\d{1,3})/(\d{1,3})$")
_TIME_RE = re.compile(r"^(\d{1,2})(h|:)(\d{2})(?:min)?$", re.IGNORECASE)
# "º" sozinho é ordinal ("1º lugar", fica para `spoken_number_runs`); só é grau seguido de C/F ("30ºC").
_DEGREES_RE = re.compile(r"^([+\-−]?\d+(?:[.,]\d+)?)(?:°|º(?=[CcFf]))([CcFf])?$")
# Palavra antes de data sem ano ("em 15/11", "dia 1/3", "on 11/15", "el 3/5").
_DATE_CONTEXT = {
    "pt": {"em", "dia", "desde", "até", "ate", "no", "para", "pra", "entre", "após", "apos", "antes", "hoje", "ontem",
           "amanhã", "amanha"},
    "en": {"on", "since", "until", "till", "by", "from", "before", "after", "dated"},
    "es": {"el", "día", "dia", "desde", "hasta", "del", "para", "entre"},
}  # fmt: skip
# Hora com ':' e um algarismo só ("9:30") precisa disto antes; sem isso é duração ("1:30").
_TIME_CONTEXT = {"às", "as", "at", "las", "la", "das", "until", "till", "desde", "até", "ate", "hasta", "from", "by"}
_TIME_WORDS: dict[str, dict[str, str]] = {
    "pt": {"and": "e", "hour": "hora", "hours": "horas", "minute": "minuto", "minutes": "minutos", "second": "segundo",
           "seconds": "segundos"},
    "es": {"and": "y", "hour": "hora", "hours": "horas", "minute": "minuto", "minutes": "minutos", "second": "segundo",
           "seconds": "segundos"},
    "en": {"and": "and", "hour": "hour", "hours": "hours", "minute": "minute", "minutes": "minutes",
           "second": "second", "seconds": "seconds"},
}  # fmt: skip
# Frações: denominador → (singular, plural). Fora da tabela: "avos" (pt), "sobre" (es), ordinal (en).
_FRACTIONS: dict[str, dict[int, tuple[str, str]]] = {
    "pt": {2: ("meio", "meios"), 3: ("terço", "terços"), 4: ("quarto", "quartos"), 5: ("quinto", "quintos"),
           6: ("sexto", "sextos"), 7: ("sétimo", "sétimos"), 8: ("oitavo", "oitavos"), 9: ("nono", "nonos"),
           10: ("décimo", "décimos")},
    "es": {2: ("medio", "medios"), 3: ("tercio", "tercios"), 4: ("cuarto", "cuartos"), 5: ("quinto", "quintos"),
           6: ("sexto", "sextos"), 7: ("séptimo", "séptimos"), 8: ("octavo", "octavos"), 9: ("noveno", "novenos"),
           10: ("décimo", "décimos")},
    "en": {2: ("half", "halves"), 4: ("quarter", "quarters")},
}  # fmt: skip
_ALL_DAY = {"pt": "vinte e quatro horas por dia, sete dias por semana", "en": "twenty four seven",
            "es": "veinticuatro horas al día, siete días a la semana"}
_ACRONYM_RE = re.compile(r"^([A-Z]{2,6})(s?)$")
_ROMAN_RE = re.compile(r"M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})")


def _base(lang: str) -> str:
    return lang.lower().split("-")[0]


def _split_punct(token: str) -> tuple[str, str, str]:
    """(abertura, miolo, fechamento) do token: '("FBI",' → ('("', 'FBI', '",')."""
    lead = token[: len(token) - len(token.lstrip(_OPENERS))]
    rest = token[len(lead):]
    match = _TRAIL_RE.search(rest)
    trail = match.group(0) if match else ""
    core = rest[: len(rest) - len(trail)] if trail else rest
    if not core:  # token só de pontuação ("…")
        return "", token, ""
    return lead, core, trail


def _number(text: str, lang: str) -> float | None:
    s = text.replace("−", "-")
    s = s.replace(",", ".") if _base(lang) != "en" else s
    try:
        return float(s)
    except ValueError:
        return None


def _say_date(core: str, lang: str, prev: str = "") -> str | None:
    """Data: com ano sempre; sem ano ("15/11") só depois de palavra de data ("em", "dia", "on"). Sem isso, "1/3" e
    "24/7" não são datas (ver `_say_slash`)."""
    match = _DATE_RE.match(core)
    months = _MONTHS.get(_base(lang))
    if not match or not months:
        return None
    a, b, year = int(match.group(1)), int(match.group(2)), match.group(3)
    if year is None and prev.casefold() not in _DATE_CONTEXT.get(_base(lang), set()):
        return None
    day, month = (b, a) if _base(lang) == "en" else (a, b)
    if not (1 <= day <= 31 and 1 <= month <= 12):
        return None
    full_year = int(year) if year and len(year) == 4 else (2000 + int(year) if year else None)
    month_name = months[month - 1]
    if _base(lang) == "en":
        day_words = number_words(day, lang, ordinal=True)
        text = f"{month_name} {day_words}"
        return f"{text}, {number_words(full_year, lang, year=True)}" if full_year else text
    if _base(lang) == "pt" and day == 1:
        day_words = "primeiro"
    else:
        day_words = number_words(day, lang)
    text = f"{day_words} de {month_name}"
    return f"{text} de {number_words(full_year, lang)}" if full_year else text


def _say_slash(core: str, lang: str) -> str | None:
    """'a/b' que não é data: '24/7' e frações ('1/2' → 'meio', '2/3' → 'dois terços', '1/20' → 'um vinte avos')."""
    match = _SLASH_RE.match(core)
    base = _base(lang)
    if not match or base not in _FRACTIONS:
        return None
    num, den = int(match.group(1)), int(match.group(2))
    if (num, den) == (24, 7):
        return _ALL_DAY[base]
    n = number_words(num, lang)
    if den < 2 or n is None:
        return None
    names = _FRACTIONS[base].get(den)
    if base == "en":
        if names is None:
            ordinal = number_words(den, lang, ordinal=True)
            if ordinal is None:
                return None
            names = (ordinal, ordinal + "s")
        return f"{n} {names[0] if num == 1 else names[1]}"
    if base == "pt" and (num, den) == (1, 2):
        return "meio"
    if names is None:
        d = number_words(den, lang)
        if d is None:
            return None
        return f"{n} {d} avos" if base == "pt" else f"{n} sobre {d}"
    if base == "es" and num == 1:
        n = "un"
    return f"{n} {names[0] if num == 1 else names[1]}"


def _say_time(core: str, lang: str, prev: str = "") -> str | None:
    """Hora ('14h30', '21:15', 'às 9:30'). Com ':' e hora de um algarismo, só depois de 'às'/'at'/'a las'; sem isso
    é duração ('1:30' → 'um minuto e trinta segundos')."""
    match = _TIME_RE.match(core)
    names = _TIME_WORDS.get(_base(lang))
    if not match or names is None:
        return None
    hours, sep, minutes = int(match.group(1)), match.group(2), int(match.group(3))
    if minutes > 59:
        return None
    clock = sep.lower() == "h" or len(match.group(1)) == 2 or prev.casefold() in _TIME_CONTEXT
    if not clock:
        whole, rest = number_words(hours, lang), number_words(minutes, lang)
        if whole is None or rest is None:
            return None
        if _base(lang) == "es":  # "un minuto", "veintiún minutos"
            whole = re.sub(r"uno$", "ún", whole) if hours != 1 else "un"
            rest = re.sub(r"uno$", "ún", rest) if minutes != 1 else "un"
        text = f"{whole} {names['minute'] if hours == 1 else names['minutes']}"
        if minutes:
            text += f" {names['and']} {rest} {names['second'] if minutes == 1 else names['seconds']}"
        return text
    if hours > 23:
        return None
    h = number_words(hours, lang, feminine=True)
    if _base(lang) == "en":
        if minutes == 0:
            return f"{h} o'clock"
        m = number_words(minutes, lang)
        return f"{h} oh {m}" if minutes < 10 else f"{h} {m}"
    if _base(lang) == "es" and hours in (1, 21):
        h = "una" if hours == 1 else "veintiuna"
    if minutes == 0:
        return f"{h} {names['hour'] if hours == 1 else names['hours']}"
    return f"{h} {names['and']} {number_words(minutes, lang)}"


def _say_degrees(core: str, lang: str) -> str | None:
    match = _DEGREES_RE.match(core)
    table = _SYMBOLS.get(_base(lang))
    if not match or not table:
        return None
    value = _number(match.group(1), lang)
    if value is None:
        return None
    spoken = number_words(value, lang, written=match.group(1))
    if spoken is None:
        return None
    scale = (match.group(2) or "").upper()
    unit = table["°"] + (" Celsius" if scale == "C" else " Fahrenheit" if scale == "F" else "")
    return f"{spoken} {unit}"


def _pronounceable(letters: str) -> bool:
    """A palavra em maiúsculas se lê em português ("NUNCA", "GRANDE", "ISSO")? Sílabas de consoante (ou grupo como
    "GR", "CH") + vogais + consoante final permitida. "IBGE", "FBI", "EUA" não seguem isso."""
    if all(ch in _VOWELS for ch in letters) or not any(ch in _VOWELS for ch in letters):
        return False
    parts = re.findall(r"[AEIOU]+|[^AEIOU]+", letters)
    for index, part in enumerate(parts):
        if part[0] in _VOWELS:
            continue
        if index == 0:
            ok = len(part) == 1 or part in _ONSETS
        elif index == len(parts) - 1:
            ok = len(part) == 1 and part in _CODAS
        else:  # entre vogais: começo da próxima sílaba, ou fim desta + começo da próxima
            ok = len(part) == 1 or part in _ONSETS or (part[0] in _CODAS and (len(part) == 2 or part[1:] in _ONSETS))
        if not ok:
            return False
    return True


def _spell_acronym(core: str, all_caps_text: bool, prev: str = "") -> str | None:
    """Sigla que não dá para ler como palavra vira letras ('FBI' → 'éfe bê í'). Só em português.

    Palavra em maiúsculas que se lê em português ("NUNCA", "MUITO GRANDE") é ênfase e fica como está; de 2 letras,
    só as da lista `_SHORT_WORDS` ("DE", "EM") ficam.
    """
    if core in _SHORT_WORDS:
        return None
    match = _ACRONYM_RE.match(core)
    if not match or all_caps_text:
        return None
    letters, plural = match.group(1), match.group(2)
    if letters in _ACRONYM_AS_WORD or core.upper() in _ACRONYM_AS_WORD:
        return None
    if not plural and _ROMAN_RE.fullmatch(letters) and (set(letters) & set("IVX") or prev.casefold() in _ROMAN_CONTEXT):
        return None  # algarismo romano ("Dom Pedro II", "século XX") fica como está
    if len(core) >= 3 and _pronounceable(core):
        return None
    spelled = " ".join(_LETTERS_PT[ch] for ch in letters)
    return spelled + ("s" if plural else "")


def _handle(core: str, lang: str) -> str | None:
    """@perfil e #tag."""
    table = _SYMBOLS.get(_base(lang))
    if not table or len(core) < 2:
        return None
    if core[0] == "@" and _WORD_RE.fullmatch(core[1:]):
        return f"{table['@']} {core[1:]}"
    if core[0] == "#" and _WORD_RE.fullmatch(core[1:]):
        return f"{table['#']} {core[1:]}"
    return None


# --- plano ---------------------------------------------------------------------------------


@dataclass
class SpeechPlan:
    text: str
    """Roteiro original."""
    spoken: str
    """Texto que vai para a voz."""
    groups: list[tuple[int, int]] = field(default_factory=list)
    """Em ordem: (palavras do roteiro, palavras faladas) de cada trecho. A soma das primeiras é
    `len(words(text))` e a das segundas, `len(words(spoken))`."""

    @property
    def changed(self) -> bool:
        return self.spoken != self.text


def _match_entry(cores: list[str], start: int, entry_tokens: list[str], case_sensitive: bool) -> bool:
    """Compara os miolos (sem a pontuação das pontas) do termo e do texto."""
    if start + len(entry_tokens) > len(cores):
        return False
    for offset, wanted in enumerate(entry_tokens):
        got = cores[start + offset]
        if case_sensitive:
            if got != wanted:
                return False
        elif got.casefold() != wanted.casefold():
            return False
    return True


def _dictionary(lang: str, entries: list[PronunciationEntry], auto: bool) -> list[tuple[list[str], str, bool, str]]:
    """(miolos do termo, fala, diferencia maiúsculas, pontuação final do termo) do mais longo para o mais curto.

    Sigla ("GTA", "PIB") só casa com a mesma caixa; o resto ignora maiúsculas. A pontuação do termo ("Dr.", "etc.")
    sai da comparação, que é feita com os miolos, como no texto."""
    out: list[tuple[list[str], str, bool, str]] = []
    seen: set[str] = set()
    for entry in entries:
        if not entry.applies_to(lang) or not entry.for_voice(auto):
            continue
        parts = [_split_punct(tok) for tok in entry.term.split()]
        tokens = [core for _, core, _ in parts]
        key = " ".join(tokens).casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        upper = sum(1 for ch in entry.term if ch.isupper())
        out.append((tokens, entry.say, upper >= 2 and entry.term == entry.term.upper(), parts[-1][2]))
    if auto and _base(lang) == "pt":
        for term, say in FOREIGN_PT.items():
            if term not in seen:
                out.append((term.split(), say, False, ""))
    out.sort(key=lambda item: -len(item[0]))
    return out


def speech_plan(text: str, lang: str, entries: list[PronunciationEntry] | None = None, *,
                auto: bool = False) -> SpeechPlan:
    """Texto falado de `text` em `lang`: dicionário do canal sempre; com `auto`, também as regras das vozes locais."""
    raw = text.split()
    if not raw:
        return SpeechPlan(text, text, [])
    lead_trail = [_split_punct(tok) for tok in raw]
    cores = [core for _, core, _ in lead_trail]
    is_word = [bool(_WORD_RE.search(tok)) for tok in raw]
    pieces: dict[int, tuple[int, str]] = {}  # início → (fim exclusivo, fala) em índices de `raw`
    taken = [False] * len(raw)

    def put(i0: int, i1: int, spoken: str, *, keep_punct: bool = True, drop_trail: str = "") -> None:
        if keep_punct:
            trail = lead_trail[i1 - 1][2]
            if drop_trail and trail.startswith(drop_trail):
                trail = trail[len(drop_trail):]
            spoken = lead_trail[i0][0] + spoken + trail
        pieces[i0] = (i1, spoken)
        for k in range(i0, i1):
            taken[k] = True

    # 1. Dicionário (do canal e, nas vozes locais, os estrangeirismos)
    dictionary = _dictionary(lang, entries or [], auto)
    i = 0
    while i < len(raw) and dictionary:
        for tokens, say, case_sensitive, term_trail in dictionary:
            end = i + len(tokens)
            if (_match_entry(cores, i, tokens, case_sensitive) and not any(taken[i:end])
                    and lead_trail[end - 1][2].startswith(term_trail)):
                # O ponto de abreviação ("Dr.", "etc.") é do termo: sai da fala para não virar pausa no meio da
                # frase (no fim do texto fica, porque também fecha a frase).
                # Pontuação que a própria fala já traz ("Vulto!" → "Vúlto!") também não se repete.
                drop = term_trail if term_trail and (say.endswith(term_trail) or term_trail == "." and end < len(raw)) else ""
                put(i, end, say, drop_trail=drop)
                i += len(tokens)
                break
        else:
            i += 1

    if auto:
        table = _SYMBOLS.get(_base(lang), {})
        # Texto quase todo em maiúsculas (título gritado): as palavras em caixa alta não são siglas.
        letter_words = [c for c, w in zip(cores, is_word, strict=True) if w and any(ch.isalpha() for ch in c)]
        lower = sum(1 for c in letter_words if any(ch.islower() for ch in c))
        all_caps = bool(letter_words) and lower < 0.2 * len(letter_words)
        # 2. Datas, horas, graus, siglas, símbolos e @/#
        for k, core in enumerate(cores):
            if taken[k]:
                continue
            prev = cores[k - 1] if k > 0 else ""
            spoken = (_say_date(core, lang, prev) or _say_slash(core, lang) or _say_time(core, lang, prev)
                      or _say_degrees(core, lang) or _handle(core, lang)
                      or (_spell_acronym(core, all_caps, prev) if _base(lang) == "pt" else None))
            if spoken is None:
                symbol = table.get(core.lower()) or (speech_words(lang).get("%", ("", ""))[1] if core == "%" else None)
                prev_number = k > 0 and any(ch.isdigit() for ch in raw[k - 1])
                if symbol:
                    spoken = symbol
                elif prev_number and core.lower() in _UNITS_AFTER_NUMBER:
                    names = speech_words(lang).get(core.lower())
                    spoken = (names[0] if cores[k - 1] in ("1", "-1") else names[1]) if names else None
            if spoken is not None:
                put(k, k + 1, spoken)
        # 3. Números (com moeda, escala, porcentagem e unidade)
        word_index = [k for k in range(len(raw)) if is_word[k]]
        tokens = [_PLACEHOLDER if taken[k] else raw[k] for k in word_index]
        for w0, w1, spoken in spoken_number_runs(tokens, lang):
            k0, k1 = word_index[w0], word_index[w1 - 1] + 1
            if not any(taken[k0:k1]):
                put(k0, k1, spoken, keep_punct=False)  # a pontuação já vem em `spoken`

    out: list[str] = []
    groups: list[tuple[int, int]] = []
    k = 0
    while k < len(raw):
        if k in pieces:
            end, spoken = pieces[k]
            out.append(spoken)
            groups.append((sum(is_word[k:end]), len(words(spoken))))
            k = end
        else:
            out.append(raw[k])
            n = 1 if is_word[k] else 0
            groups.append((n, n))
            k += 1
    spoken_text = " ".join(out)
    if spoken_text == " ".join(raw):
        return SpeechPlan(text, text, [])
    return SpeechPlan(text, spoken_text, _compact(groups))


def _compact(groups: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Junta trechos seguidos com o mesmo número de palavras dos dois lados (o mapa guardado no cache fica curto)."""
    out: list[tuple[int, int]] = []
    for a, b in groups:
        if a == 0 and b == 0:
            continue
        if out and a == b and out[-1][0] == out[-1][1]:
            out[-1] = (out[-1][0] + a, out[-1][1] + b)
        else:
            out.append((a, b))
    return out


def expand_groups(groups: list[tuple[int, int]] | list[list[int]]) -> list[tuple[int, int]]:
    """Desfaz a junção de `_compact`: um trecho (n, n) com n > 1 vira n trechos 1↔1 (ali nada mudou)."""
    out: list[tuple[int, int]] = []
    for a, b in groups:
        if a == b and a > 1:
            out.extend([(1, 1)] * a)
        else:
            out.append((int(a), int(b)))
    return out


def script_words(text: str, spoken_words: list[Word], groups: list[tuple[int, int]] | list[list[int]]) -> list[Word]:
    """Tempos das palavras do roteiro a partir das palavras faladas (alinhadas ao texto falado).

    Cada trecho do roteiro ocupa o tempo do trecho falado correspondente; com mais de uma palavra no roteiro
    ("R$ 2 milhões" → "dois milhões de reais"), o tempo é dividido pelo tamanho de cada palavra.
    """
    script = words(text)
    if not groups:  # nada mudou: uma palavra falada para cada palavra do roteiro
        groups = [(len(script), len(script))]
    out: list[Word] = []
    si = 0
    wi = 0
    last_end = spoken_words[0].start if spoken_words else 0.0
    for n_script, n_spoken in expand_groups(groups):
        chunk = spoken_words[wi : wi + n_spoken]
        wi += n_spoken
        tokens = script[si : si + n_script]
        si += n_script
        if not tokens:
            if chunk:
                last_end = chunk[-1].end
            continue
        start = chunk[0].start if chunk else last_end
        end = chunk[-1].end if chunk else last_end + 0.05 * len(tokens)
        weights = [max(1, len(normalize_token(t))) for t in tokens]
        total = sum(weights)
        t = start
        for token, weight in zip(tokens, weights, strict=True):
            span = (end - start) * weight / total
            out.append(Word(token, t, t + span))
            t += span
        last_end = end
    # Sobrou roteiro (mapa de outra versão do texto): estima no fim.
    for token in script[si:]:
        out.append(Word(token, last_end, last_end + 0.1))
        last_end += 0.1
    return out
