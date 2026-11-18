"""Tom (valência) das poses do avatar e do texto da thumb: a variedade nunca troca a cara de alerta por uma de festa.

Cada pose ganha um tom pelas emoções do manifesto do apresentador (`style_bible["presenter"].poses[].emotions`; sem
emoção conhecida, pelo id e pelos apelidos; sem nada conhecido, neutra), pelo mapa `EMOTION_TONES` (configurável:
a emoção nova entra aqui). O texto da thumb (título, palavra, conceito, promessa) ganha um tom pelas palavras de
`TEXT_TONES` (golpe/vírus/falso → alerta). Quem usa: `authoring._pick_pose` (gerador do pacote) e
`tasks._swap_pose` (troca da pose no render quando a thumb sai parecida com uma recente): troca só para pose da
mesma emoção ou do mesmo tom, e pose de festa (positiva) nunca entra em thumb de alerta.

Puro (sem banco nem arquivos).
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

ALERTA, MISTERIO, POSITIVO, NEUTRO = "alerta", "misterio", "positivo", "neutro"
TONES = (ALERTA, MISTERIO, POSITIVO, NEUTRO)
TONE_NAMES = {ALERTA: "alerta", MISTERIO: "mistério", POSITIVO: "positivo", NEUTRO: "neutro"}

EMOTION_TONES: dict[str, str] = {
    # alerta: choque, medo, erro, perigo, preocupação, raiva
    "choque": ALERTA, "chocado": ALERTA, "surpresa": ALERTA, "surpreso": ALERTA, "espantado": ALERTA,
    "susto": ALERTA, "assustado": ALERTA, "medo": ALERTA, "panico": ALERTA, "perigo": ALERTA, "alerta": ALERTA,
    "cuidado": ALERTA, "risco": ALERTA, "erro": ALERTA, "vergonha": ALERTA, "vergonha alheia": ALERTA,
    "mao na testa": ALERTA, "preocupacao": ALERTA, "preocupado": ALERTA, "nervosismo": ALERTA, "nervoso": ALERTA,
    "tensao": ALERTA, "raiva": ALERTA, "bravo": ALERTA, "abuso": ALERTA, "atraso": ALERTA, "golpe": ALERTA,
    "decepcao": ALERTA, "tristeza": ALERTA, "indignacao": ALERTA,
    # mistério: segredo, rumor, dúvida, investigação
    "segredo": MISTERIO, "rumor": MISTERIO, "boato": MISTERIO, "spoiler": MISTERIO, "misterio": MISTERIO,
    "detalhe escondido": MISTERIO, "escondido": MISTERIO, "investigacao": MISTERIO, "investigando": MISTERIO,
    "duvida": MISTERIO, "pergunta": MISTERIO, "pensativo": MISTERIO, "curiosidade": MISTERIO, "teoria": MISTERIO,
    "vazamento": MISTERIO, "lupa": MISTERIO,
    # positivo: alegria, vitória, dinheiro, comemoração
    "comemoracao": POSITIVO, "comemorando": POSITIVO, "celebracao": POSITIVO, "alegria": POSITIVO,
    "feliz": POSITIVO, "animado": POSITIVO, "vitoria": POSITIVO, "boa noticia": POSITIVO, "lancamento": POSITIVO,
    "dinheiro": POSITIVO, "custo": POSITIVO, "preco": POSITIVO, "vendas": POSITIVO, "lucro": POSITIVO,
    "concordar": POSITIVO, "joinha": POSITIVO, "humor": POSITIVO, "rindo": POSITIVO, "risada": POSITIVO,
    # neutro: explicar, apontar, notícia
    "neutro": NEUTRO, "explicar": NEUTRO, "explicando": NEUTRO, "apontando": NEUTRO, "mostrar a prova": NEUTRO,
    "noticia": NEUTRO, "post": NEUTRO, "gameplay": NEUTRO, "jogar": NEUTRO, "abertura": NEUTRO, "fecho": NEUTRO,
}
"""Emoção (ou nome de pose) → tom. Chave normalizada (minúsculas, sem acento, palavras separadas por espaço); a
emoção do manifesto pega a chave mais longa que aparece nela inteira ("boa notícia grande" → "boa noticia")."""

TEXT_TONES: dict[str, tuple[str, ...]] = {
    ALERTA: ("golp*", "virus", "malware*", "trojan*", "ransomware", "spyware", "phishing", "hack*", "roub*",
             "senha*", "perig*", "cuidado", "alerta*", "falso*", "falsa*", "falsific*", "fake", "fraud*", "scam*",
             "armadilh*", "crime*", "criminos*", "infect*", "banid*", "banimento", "erro", "erros", "risco*",
             "ameac*", "invad*", "clonad*", "nao baixe", "nao instale", "nao compre"),
    MISTERIO: ("segred*", "secret*", "escondid*", "rumor*", "boato*", "vazou", "vazament*", "vazad*", "spoiler*",
               "misteri*", "ninguem sabe", "duvida*", "easter egg", "teoria*"),
    POSITIVO: ("gratis", "de graca", "finalmente", "chegou", "lancou", "comemor*", "vitori*", "ganh*", "descont*",
               "promoc*", "ofert*", "barat*", "confirmad*"),
}
"""Tom do texto da thumb: palavra exata, prefixo (`*`) ou frase. Alerta vence sempre (um "grátis" num texto de golpe
continua sendo golpe); entre mistério e positivo, o de mais ocorrências (empate: sem tom)."""


def _words(text: str | None) -> list[str]:
    raw = unicodedata.normalize("NFKD", (text or "").lower())
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return [w for w in re.split(r"[^a-z0-9]+", raw) if w]


def _norm(text: str | None) -> str:
    return " ".join(_words(text))


def _has_phrase(words: Sequence[str], phrase: Sequence[str]) -> bool:
    n = len(phrase)
    return n > 0 and any(list(words[i:i + n]) == list(phrase) for i in range(len(words) - n + 1))


_KEYS = sorted(EMOTION_TONES, key=lambda k: -len(k.split()))


def emotion_tone(emotion: str | None) -> str | None:
    """O tom de uma emoção (ou nome) pelo `EMOTION_TONES`, ou None quando nenhuma chave aparece nela."""
    words = _words(emotion)
    for key in _KEYS:
        if _has_phrase(words, key.split()):
            return EMOTION_TONES[key]
    return None


def _vote(names: Iterable[str]) -> str | None:
    tones = [t for t in (emotion_tone(n) for n in names) if t]
    if not tones:
        return None
    counts = Counter(tones)
    best = max(counts.values())
    return next(t for t in tones if counts[t] == best)  # empate: o da primeira emoção listada


def pose_tone(pose: Mapping[str, Any] | None) -> str:
    """Tom da pose: `tone` explícito (quando o manifesto traz), senão a maioria das emoções conhecidas, senão o id e
    os apelidos, senão neutro."""
    if not pose:
        return NEUTRO
    explicit = str(pose.get("tone") or "").strip().lower()
    if explicit in TONES:
        return explicit
    aliases = pose.get("aliases") or []
    names = [str(pose.get("id") or ""), *([aliases] if isinstance(aliases, str) else aliases)]
    return _vote(pose.get("emotions") or []) or _vote(names) or NEUTRO


def _emotions(pose: Mapping[str, Any]) -> set[str]:
    return {_norm(e) for e in pose.get("emotions") or [] if _norm(e)}


def shares_emotion(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return bool(_emotions(a) & _emotions(b))


def same_tone(a: Mapping[str, Any], b: Mapping[str, Any]) -> bool:
    return pose_tone(a) == pose_tone(b)


def can_swap(current: Mapping[str, Any], other: Mapping[str, Any]) -> bool:
    """A troca por variedade só vai para pose da mesma emoção ou do mesmo tom (nunca inverte o tom)."""
    return shares_emotion(current, other) or same_tone(current, other)


def fits(tone: str, text: str | None) -> bool:
    """A pose deste tom combina com uma thumb de tom `text` (`text_tone`)? Só a festa na thumb de alerta não."""
    return not (text == ALERTA and tone == POSITIVO)


def _hits(words: Sequence[str], entries: Iterable[str]) -> int:
    n = 0
    for entry in entries:
        if " " in entry:
            n += _has_phrase(words, entry.split())
        elif entry.endswith("*"):
            stem = entry[:-1]
            n += sum(1 for w in words if w.startswith(stem))
        else:
            n += sum(1 for w in words if w == entry)
    return n


def text_tone(*texts: str | None) -> str | None:
    """O tom do texto da thumb (título, palavra, conceito, promessa) ou None quando nada indica um."""
    words = _words(" ".join(t or "" for t in texts))
    if _hits(words, TEXT_TONES[ALERTA]):
        return ALERTA
    mystery, positive = _hits(words, TEXT_TONES[MISTERIO]), _hits(words, TEXT_TONES[POSITIVO])
    if mystery == positive:
        return None
    return MISTERIO if mystery > positive else POSITIVO


def cited_in(text: str | None, poses: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """As poses citadas pelo id ou por um apelido num texto livre (o conceito: "o avatar com a mão na testa",
    "apresentador surpreso", "fazendo segredo"), na ordem em que aparecem."""
    words = _words(text)
    found: list[tuple[int, int, Mapping[str, Any]]] = []
    for order, pose in enumerate(poses):
        aliases = pose.get("aliases") or []
        names = [str(pose.get("id") or ""), *([aliases] if isinstance(aliases, str) else aliases)]
        where = [i for name in names if (phrase := _words(name))
                 for i in range(len(words) - len(phrase) + 1) if words[i:i + len(phrase)] == phrase]
        if where:
            found.append((min(where), order, pose))
    return [p for _, _, p in sorted(found, key=lambda f: (f[0], f[1]))]
