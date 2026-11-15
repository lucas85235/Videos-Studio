"""Hora do dia, clima e luz nos prompts de imagem.

A direção de arte (tratamento, mundo, aparência dos lugares e paleta) não fixa a hora nem o clima: cada imagem nova
diz a sua. Daqui saem o que o prompt da cena já diz de luz (`names_light`), em que momento do dia a cena se passa
(`moments`) e a limpeza do texto da direção quando a cena traz a própria luz (`strip_light`). Os textos são em inglês,
como os prompts. Nada aqui chama serviço externo.
"""

from __future__ import annotations

import re

# Momentos do dia que a revisão conta (no máximo 1 em cada 3 imagens no mesmo). A ordem importa: o primeiro que casa
# leva o trecho ("late afternoon" é pôr do sol antes de "afternoon" ser dia claro).
MOMENTS: dict[str, tuple[str, tuple[str, ...]]] = {
    "por_do_sol": ("pôr do sol", (
        "golden hour", "golden-hour", "magic hour", "sunset", "sundown", "dusk", "late afternoon", "late-afternoon",
        "twilight",
    )),
    "amanhecer": ("amanhecer", ("sunrise", "dawn", "daybreak", "early morning", "early-morning", "first light")),
    "noite": ("noite", (
        "night", "nighttime", "night-time", "midnight", "nightfall", "after dark", "blue hour", "moonlight", "moonlit",
        "starlit", "starry",
    )),
    "dia": ("dia claro", (
        "midday", "noon", "high noon", "daylight", "daytime", "broad day", "sunny", "sunlit", "sunlight", "sunshine",
        "morning", "afternoon", "blue sky", "clear sky",
    )),
}  # fmt: skip
# Clima: também fixa a luz de todas as imagens quando vem da direção.
WEATHER = (
    "overcast", "rainy", "raining", "rain-soaked", "rainfall", "drizzle", "stormy", "thunderstorm", "storm", "foggy",
    "fog", "misty", "mist", "hazy", "haze", "cloudy", "snowy", "snowfall", "snowing",
)  # fmt: skip
# Cor logo depois do momento é nome de cor ("sunset orange", "midnight blue"), não hora do dia.
_COLOR_AFTER = r"(?![\s-]+(?:orange|pink|red|blue|purple|violet|gold|golden|yellow|amber|coral|magenta)\b)"
# Qualificadores que vão junto com a hora, o céu ou a luz ("warm golden-hour light", "pink-orange sunset sky").
_MODS = (
    "warm", "cool", "cold", "soft", "hard", "harsh", "bright", "dim", "low", "pale", "deep", "golden", "gold", "pink",
    "orange", "red", "purple", "violet", "magenta", "blue", "teal", "cyan", "amber", "pastel", "saturated", "vivid",
    "dramatic", "moody", "glowing", "fiery", "late", "early", "natural", "ambient", "cinematic", "diffused", "diffuse",
    "dappled", "volumetric", "rim", "back", "side", "window", "studio", "lamp", "candle", "fire", "screen", "clear",
    "cloudless", "endless", "open", "starry", "stormy", "cloudy", "overcast",
)  # fmt: skip
_MOD = rf"(?:{'|'.join(_MODS)})(?:-(?:{'|'.join(_MODS)}))?"
# O que vem depois da hora e some junto ("sunset light", "night sky", "golden hour tones").
_AFTER = r"(?:[\s-]+(?:light(?:ing|s)?|sky|skies|sun|glow|haze|ambien(?:ce|t)|atmosphere|tones?|hues?|colou?rs?|vibes?|mood))?"
_PREP = r"(?:(?<![\w-])(?:at|during|in|under|by|against|with|from|beneath)\s+(?:the\s+|a\s+|an\s+)?)?"


def _alternation(terms: tuple[str, ...] | list[str]) -> str:
    """Termos como alternativa de regex (mais longos primeiro), espaço ou hífen tanto faz, plural simples."""
    out = []
    for term in sorted(set(terms), key=len, reverse=True):
        words = re.split(r"[\s-]+", term)
        out.append(r"[\s-]+".join(re.escape(w) for w in words) + "(?:e?s)?")
    return "|".join(out)


_MOMENT_RES = {
    key: re.compile(rf"(?<![\w-])(?:{_alternation(terms)})(?![\w-]){_COLOR_AFTER}", re.IGNORECASE)
    for key, (_label, terms) in MOMENTS.items()
}
_ALL_TIME = _alternation([t for _label, terms in MOMENTS.values() for t in terms] + list(WEATHER))
_NEGATED_RE = re.compile(r"(?:\bno|\bnot|\bwithout|\bnever|\bavoid)\s+(?:[\w-]+\s+){0,2}$", re.IGNORECASE)

# Hora, clima ou céu com o que vem junto: "at sunset", "under a pink-orange sunset sky", "golden-hour light".
_TIME_STRIP_RE = re.compile(
    rf"{_PREP}(?:{_MOD}[\s-]+){{0,3}}(?<![\w-])(?:{_ALL_TIME})(?![\w-]){_AFTER}(?![\w-])", re.IGNORECASE)
_SKY_STRIP_RE = re.compile(rf"{_PREP}(?:{_MOD}[\s-]+){{0,3}}(?<![\w-])sk(?:y|ies)(?![\w-])", re.IGNORECASE)
# Luz do tratamento ("soft window light", "neon glow", "candlelight", "studio lighting").
_LIGHT_STRIP_RE = re.compile(
    rf"{_PREP}(?:{_MOD}[\s-]+){{0,3}}(?:(?<![\w-])[\w]+[\s-]+)?"
    r"(?<![\w-])(?:light(?:ing|s)?|glow|illumination)(?![\w-])"
    r"|(?<![\w-])(?:candle|lamp|fire|torch|sun|moon)light(?![\w-])"
    r"|(?<![\w-])[\w]+-lit(?![\w-])", re.IGNORECASE)
# A cena já diz de luz: hora, clima ou fonte de luz ("lit by the TV", "neon-lit", "screen glow", "candlelight").
_OWN_LIGHT_RE = re.compile(
    r"(?<![\w-])(?:light(?:ing|s)?|lit|glow(?:ing|s)?|backlit|silhouetted|shadows?)(?![\w-])"
    r"(?![\s-]+(?:blue|green|gray|grey|brown|pink|wood|weight|beige|yellow)\b)"
    r"|(?<![\w-])(?:candle|lamp|fire|torch|sun|moon|star|spot|flash|head|street|neon|back|twi|sky|half|un)"
    r"(?:light(?:s|ing)?|lit)(?![\w-])"
    r"|(?<![\w-])[\w]+-lit(?![\w-])", re.IGNORECASE)
_WEATHER_RE = re.compile(rf"(?<![\w-])(?:{_alternation(list(WEATHER))})(?![\w-])", re.IGNORECASE)


def _not_negated(text: str, start: int) -> bool:
    return not _NEGATED_RE.search(text[max(0, start - 30) : start])


def moments(text: str) -> set[str]:
    """Momentos do dia que o texto cita ("at sunset" → por_do_sol); menção negada ("no sunset") não conta."""
    found: set[str] = set()
    masked = text or ""
    for key, pattern in _MOMENT_RES.items():
        for match in pattern.finditer(masked):
            if _not_negated(masked, match.start()):
                found.add(key)
        masked = pattern.sub(lambda m: " " * len(m.group(0)), masked)
    return found


def moment_label(key: str) -> str:
    return MOMENTS[key][0] if key in MOMENTS else key


def names_light(text: str) -> bool:
    """O prompt da cena já diz a hora, o clima ou a luz: a direção não impõe a dela."""
    text = text or ""
    return bool(moments(text) or _WEATHER_RE.search(text) or _OWN_LIGHT_RE.search(text))


def _tidy(text: str) -> str:
    """Arruma o que sobrou depois de tirar trechos: espaços, vírgulas, conectivos soltos e partes vazias."""
    text = re.sub(r"\s+", " ", text)
    parts = []
    for part in re.split(r"\s*[,;]\s*", text):
        part = part.strip(" .")
        for _ in range(3):  # conectivo solto no começo ou no fim ("and neon glow", "palm trees and")
            part = re.sub(r"^(?:and|with|of|or|plus)\s+", "", part, flags=re.IGNORECASE)
            part = re.sub(r"\s+(?:and|with|of|or|in|at|under|by|during|the|a|an)$", "", part, flags=re.IGNORECASE)
            part = re.sub(r"^(?:and|with|of|or|in|at|under|by|during|the|a|an)$", "", part, flags=re.IGNORECASE)
        part = re.sub(r"\b(and|with)\s+(?:and|with)\b", r"\1", part, flags=re.IGNORECASE).strip(" .")
        if part:
            parts.append(part)
    return ", ".join(parts)


def strip_light(text: str, *, light: bool = False) -> str:
    """Tira do texto da direção a hora do dia, o clima e o céu (e, com `light`, as fontes de luz).

    "saturated sunset light, clean contrast" → "saturated, clean contrast"; "beaches at sunset" → "beaches". Texto sem
    nada disso volta igual (mesmo prompt, mesma chave de cache).
    """
    original = (text or "").strip()
    out = _TIME_STRIP_RE.sub(" ", original)
    out = _SKY_STRIP_RE.sub(" ", out)
    if light:
        out = _LIGHT_STRIP_RE.sub(" ", out)
    if out == original:
        return original
    return _tidy(out)
