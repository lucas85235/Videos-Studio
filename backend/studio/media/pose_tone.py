"""Tom (valência) das poses do avatar e do texto da thumb: a variedade nunca troca a cara de alerta por uma de festa.

Cada pose ganha um tom pelas emoções do manifesto do apresentador (`style_bible["presenter"].poses[].emotions`; sem
emoção conhecida, pelo id e pelos apelidos; sem nada conhecido, neutra), pelo mapa `EMOTION_TONES` (configurável:
a emoção nova entra aqui). O texto da thumb (título, palavra, conceito, promessa) ganha um tom pelas palavras de
`TEXT_TONES` (golpe/vírus/falso/adiado → alerta; a palavra negada logo antes, "sem golpe", "ninguém ganhou", não
conta). No conceito, a pose citada (`cited_in`) e a cara pedida (`avatar_tone`, "avatar chocado") valem só no trecho
logo depois da menção ao avatar ("quanto dinheiro a Rockstar perdeu" não pede a pose dinheiro). Quem usa: `authoring._pick_pose` (gerador do pacote) e
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
    "decepcao": ALERTA, "tristeza": ALERTA, "indignacao": ALERTA, "chocada": ALERTA, "espantada": ALERTA,
    "assustada": ALERTA, "preocupada": ALERTA, "nervosa": ALERTA, "brava": ALERTA, "apavorado": ALERTA,
    "apavorada": ALERTA, "desesperado": ALERTA, "desesperada": ALERTA, "suando": ALERTA, "irritado": ALERTA,
    # mistério: segredo, rumor, dúvida, investigação
    "segredo": MISTERIO, "rumor": MISTERIO, "boato": MISTERIO, "spoiler": MISTERIO, "misterio": MISTERIO,
    "detalhe escondido": MISTERIO, "escondido": MISTERIO, "investigacao": MISTERIO, "investigando": MISTERIO,
    "duvida": MISTERIO, "pergunta": MISTERIO, "pensativo": MISTERIO, "curiosidade": MISTERIO, "teoria": MISTERIO,
    "vazamento": MISTERIO, "lupa": MISTERIO, "pensativa": MISTERIO, "desconfiado": MISTERIO,
    "desconfiada": MISTERIO, "intrigado": MISTERIO, "intrigada": MISTERIO, "curioso": MISTERIO, "curiosa": MISTERIO,
    # positivo: alegria, vitória, dinheiro, comemoração
    "comemoracao": POSITIVO, "comemorando": POSITIVO, "celebracao": POSITIVO, "alegria": POSITIVO,
    "feliz": POSITIVO, "animado": POSITIVO, "vitoria": POSITIVO, "boa noticia": POSITIVO, "lancamento": POSITIVO,
    "animada": POSITIVO, "empolgado": POSITIVO, "empolgada": POSITIVO, "sorrindo": POSITIVO,
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
             "ameac*", "invad*", "clonad*", "nao baixe", "nao instale",
             # notícia ruim: a cara de festa nela é tão absurda quanto num golpe
             "adiad*", "adiament*", "cancelad*", "cancelament*", "bug", "bugs", "bugad*", "trava", "travou",
             "travando", "travament*", "crash*", "pior", "piores", "polemic*", "problem*", "decepc*", "fracass*",
             "proibid*", "censur*", "desastr*", "vergonh*"),
    MISTERIO: ("segred*", "secret*", "escondid*", "rumor*", "boato*", "vazou", "vazament*", "vazad*", "spoiler*",
               "misteri*", "ninguem sabe", "duvida*", "easter egg", "teoria*"),
    # só palavra de festa sem ambiguidade: "chegou", "lançou" e "confirmado" também chegam com notícia ruim
    POSITIVO: ("gratis", "de graca", "finalmente", "comemor*", "vitori*", "ganh*", "descont*", "promoc*", "ofert*",
               "barat*"),
}
"""Tom do texto da thumb: palavra exata, prefixo (`*`) ou frase. A palavra negada logo antes ("sem golpe", "não é
vírus", "ninguém ganhou") não conta. Alerta vence sempre (um "grátis" num texto de golpe continua sendo golpe); entre
mistério e positivo, o de mais ocorrências (empate: sem tom)."""

NEGATORS = frozenset({"sem", "nao", "nenhum", "nenhuma", "ninguem", "nunca", "nem", "zero", "nada", "livre", "anti"})
"""Palavras que negam o termo logo depois ("sem golpe", "sem erro", "ninguém ganhou", "livre de vírus")."""
_FILLERS = frozenset({"e", "foi", "era", "sera", "tem", "teve", "ha", "de", "do", "da", "o", "a", "os", "as", "um",
                      "uma", "mais"})
"""Palavras puladas entre a negação e o termo ("não é golpe", "sem nenhum risco", "não tem vírus"), até duas."""

AVATAR_WORDS = frozenset({"avatar", "apresentador", "apresentadora", "host", "narrador", "pose", "expressao",
                          "reacao"})
"""Palavras do conceito que falam do avatar: a pose citada vale só no trecho logo depois delas."""
AVATAR_SPAN = 8
"""Quantas palavras depois da menção ao avatar valem como a pose dele (até a vírgula que fecha o trecho)."""


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
    return _vote(pose.get("emotions") or []) or _vote(_names(pose)) or NEUTRO


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


def _negated(words: Sequence[str], i: int) -> bool:
    j, skipped = i - 1, 0
    while j >= 0 and words[j] in _FILLERS and skipped < 2:
        j, skipped = j - 1, skipped + 1
    return j >= 0 and words[j] in NEGATORS


def _hits(words: Sequence[str], entries: Iterable[str]) -> int:
    n = 0
    for entry in entries:
        if " " in entry:
            phrase = entry.split()
            n += sum(1 for i in range(len(words) - len(phrase) + 1) if list(words[i:i + len(phrase)]) == phrase
                     and (phrase[0] in NEGATORS or not _negated(words, i)))
        else:
            stem, prefix = entry.rstrip("*"), entry.endswith("*")
            n += sum(1 for i, w in enumerate(words)
                     if (w.startswith(stem) if prefix else w == stem) and not _negated(words, i))
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


def _avatar_spans(text: str | None) -> list[list[str]] | None:
    """Os trechos do texto logo depois de cada menção ao avatar ("o avatar pequeno fazendo segredo" → "pequeno
    fazendo segredo"): até `AVATAR_SPAN` palavras, na mesma frase, até a vírgula que fecha o trecho. None quando o
    texto não fala do avatar."""
    spans: list[list[str]] = []
    mentioned = False
    for sentence in re.split(r"[.;:!?\n]+", text or ""):
        tokens = [t for part in sentence.split(",") for t in [*_words(part), ","]]
        for i, tok in enumerate(tokens):
            if tok not in AVATAR_WORDS:
                continue
            mentioned = True
            span: list[str] = []
            for nxt in tokens[i + 1:]:
                if nxt == ",":
                    if span:
                        break
                    continue
                if nxt in AVATAR_WORDS or len(span) >= AVATAR_SPAN:
                    break
                span.append(nxt)
            if span:
                spans.append(span)
    return spans if mentioned else None


def _names(pose: Mapping[str, Any]) -> list[str]:
    aliases = pose.get("aliases") or []
    return [str(pose.get("id") or ""), *([aliases] if isinstance(aliases, str) else aliases)]


def cited_in(text: str | None, poses: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """As poses citadas pelo id ou por um apelido num texto livre (o conceito: "o avatar com a mão na testa",
    "apresentador surpreso", "fazendo segredo"), na ordem em que aparecem. Quando o texto fala do avatar, só vale a
    citação no trecho logo depois dele ("Ângulo dinheiro: quanto dinheiro a Rockstar perdeu; avatar chocado" não
    cita a pose dinheiro); sem menção ao avatar, vale o texto inteiro."""
    spans = _avatar_spans(text)
    chunks = spans if spans is not None else [_words(text)]
    found: list[tuple[int, int, int, Mapping[str, Any]]] = []
    for order, pose in enumerate(poses):
        where = [(c, i) for c, words in enumerate(chunks) for name in _names(pose) if (phrase := _words(name))
                 for i in range(len(words) - len(phrase) + 1) if words[i:i + len(phrase)] == phrase]
        if where:
            c, i = min(where)
            found.append((c, i, order, pose))
    return [p for *_, p in sorted(found, key=lambda f: f[:3])]


def avatar_tone(text: str | None) -> str | None:
    """O tom da cara que o texto pede para o avatar sem citar uma pose ("avatar chocado", "apresentador
    preocupado"): a emoção conhecida (`EMOTION_TONES`) mais perto da menção ao avatar. None sem menção ou sem
    emoção conhecida."""
    best: tuple[int, str] | None = None
    for span in _avatar_spans(text) or []:
        for key in _KEYS:
            phrase = key.split()
            for i in range(len(span) - len(phrase) + 1):
                if span[i:i + len(phrase)] == phrase and (best is None or i < best[0]):
                    best = (i, EMOTION_TONES[key])
    return best[1] if best else None
