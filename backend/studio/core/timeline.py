"""Timeline: converte documento + estilo + narração em quadros, legendas e blocos de render.

Regras importantes (README §5.4 e §6.5):
- Cenas são contínuas; o tempo vem da narração real (palavras com timestamps).
- Escolhas aleatórias (câmera, transição) usam sementes por cena: mudar uma cena não mexe nas outras.
- Blocos de render são definidos pelo conteúdo (hash do ID da cena), então inserir/remover cenas
  só afeta blocos vizinhos, e mudanças de tempo em outros pontos não invalidam o bloco.
- Elementos que dependem do tempo global (barra de progresso, marca d'água) ficam fora dos blocos.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from studio.core.hashing import hash_obj, seed_from
from studio.core.numbers import card_number, merge_number_words, notable_number_runs, number_runs
from studio.core.project.document import MediaRef, ProjectDoc, Scene
from studio.core.project.media_edit import scene_label
from studio.core.styles.library import pick_weighted
from studio.core.styles.schema import GRADE_PRESETS, StyleSpec, treatment_pool
from studio.core.styles.visual import FOCUS_POINTS, pick, rotate_variant
from studio.core.text import normalize_token, span_ranges, split_word_indices
from studio.media.link_preview import _SEPARATORS as _HEADLINE_SEPARATORS
from studio.media.link_preview import clean_headline, site_label
from studio.providers.base import Word

SENTENCE_END = (".", "!", "?", "…", ":", ";")
# Cartões que entram com o som de impacto (quando a cena não escolheu outro).
IMPACT_KINDS = ("title", "count", "stat", "compare")
# Cartões de dado: entram quando o número é dito ("reveal") e já mostram o carimbo e a fonte no painel.
REVEAL_KINDS = ("stat", "compare")
# Teto da gradação trocada pela direção de arte (a do estilo pode ser mais forte, a da direção não).
ART_GRADE_MAX = 0.45
SFX_FOR_TRANSITION = {
    "flash": "whoosh",
    "zoom_blur": "whoosh",
    "light_leak": "whoosh",
    "film_burn": "whoosh",
    "glitch": "glitch",
    "static_burst": "static",
}
# Quanto cada efeito começa antes do corte (s): o impacto bate no quadro do corte, o whoosh chega ao pico nele
# (o arquivo cresce por ~0,35 s) e o riser sobe até o corte. É o ponto forte de cada arquivo (`media.audio.SFX_HIT_S`,
# conferido nos testes); só a timeline adianta o efeito, a mixagem põe cada um no `start` que recebe.
SFX_LEAD = {"impact": 0.0, "whoosh": 0.35, "glitch": 0.0, "static": 0.05, "riser": 1.5}
SFX_LEAD_DEFAULT = 0.12
# Carimbo solto: entra depois de STAMP_DELAY quadros (o mesmo `delay` do Stamp no renderer) e pousa ~0,1 s depois.
STAMP_DELAY = 6
STAMP_LAND_S = 0.1
# O cartão de dado e o carimbo com número entram quando ele é dito, mas ficam na tela pelo menos isto.
REVEAL_MIN_VISIBLE_S = 1.2
STAMP_MIN_VISIBLE_S = 0.8
# Número dito até aqui depois do corte: o cartão entra no corte, parado (o SceneView do renderer usa a mesma janela).
REVEAL_POP_S = 0.3
# Composições do catálogo em que o carimbo muda de lugar (sem composição conhecida, fica no padrão: à direita).
# Plano de gente (rosto na altura de 0,2-0,45 da tela): com o sujeito no centro, o carimbo desce.
PERSON_SHOTS = frozenset({"medium", "close", "low_angle", "over_shoulder"})
# Câmera da foto real sem movimento escolhido: só movimentos suaves (a foto não é feita para pan nem soco de zoom).
REAL_MOTIONS = ("ken_burns_in", "ken_burns_out", "drift")
# Tempo para ler a manchete do cartão de notícia: por palavra, mais a entrada do cartão (abaixo disso, aviso no build).
NEWS_READ_WORD_S = 0.3
NEWS_READ_BASE_S = 0.6
# Meses abreviados da data do cartão de notícia (idioma sem lista usa o inglês).
NEWS_MONTHS: dict[str, tuple[str, ...]] = {
    "pt": ("jan.", "fev.", "mar.", "abr.", "mai.", "jun.", "jul.", "ago.", "set.", "out.", "nov.", "dez."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
    "es": ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"),
}


@dataclass
class NarrationClip:
    segment_id: str
    path: str
    """Caminho relativo ao diretório de dados."""
    duration: float
    words: list[Word]
    """Timestamps relativos ao início do clipe, um por palavra do roteiro."""


class TimelineError(ValueError):
    pass


def lead_in(style: StyleSpec) -> float:
    return 0.3 if style.format.type == "short" else 0.6


def tail_out(style: StyleSpec) -> float:
    return 0.7 if style.format.type == "short" else 1.5


def normalize_words(words: list[Word], duration: float) -> list[Word]:
    """Mantém os tempos das palavras dentro do clipe.

    Alguns provedores (ex.: a voz do Windows) relatam posições além do fim real do áudio; sem isso, a
    última cena de um segmento poderia começar depois do segmento seguinte.
    """
    if not words:
        return []
    last = max(w.end for w in words)
    scale = duration / last if last > duration + 0.05 and last > 0 else 1.0
    out: list[Word] = []
    prev = 0.0
    for w in words:
        start = min(max(0.0, w.start * scale), duration)
        start = max(start, prev)
        end = min(max(start, w.end * scale), duration)
        out.append(Word(w.text, start, end))
        prev = start
    return out


# --- legendas ---------------------------------------------------------------------------------

# Palavras que não fecham linha de legenda (artigo, preposição, conjunção, pronome, auxiliar), já normalizadas
# como `normalize_token` (minúsculas, sem acento: "é" vira "e", "à" vira "a"). Idioma sem lista: só o tempo na
# tela, a vírgula e o equilíbrio decidem.
FUNCTION_WORDS: dict[str, frozenset[str]] = {
    "pt": frozenset({
        "a", "o", "as", "os", "ao", "aos", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "em", "no",
        "na", "nos", "nas", "num", "numa", "naquele", "naquela", "nesse", "nessa", "neste", "nesta", "pelo", "pela",
        "pelos", "pelas", "por", "para", "pra", "com", "sem", "sob", "sobre", "entre", "ate", "desde", "contra", "e",
        "ou", "mas", "nem", "que", "se", "como", "quando", "onde", "porque", "cujo", "cuja", "ele", "ela", "eles",
        "elas", "eu", "voce", "lhe", "me", "te", "seu", "sua", "seus", "suas", "meu", "minha", "nosso", "nossa",
        "esse", "essa", "este", "esta", "aquele", "aquela", "nao", "ja", "tao", "quase",
    }),
    "en": frozenset({
        "a", "an", "the", "of", "to", "in", "on", "at", "by", "for", "with", "from", "into", "onto", "and", "or",
        "but", "nor", "as", "if", "that", "than", "so", "its", "his", "their", "our", "your", "my", "is", "are",
        "was", "were", "be", "been", "has", "have", "had", "will", "would", "can", "could", "should", "not", "very",
        "who", "which", "whose", "where", "when", "these", "those", "i", "we", "they", "he", "she",
    }),
    "es": frozenset({
        "a", "al", "el", "la", "los", "las", "lo", "un", "una", "unos", "unas", "de", "del", "en", "con", "por",
        "para", "sin", "sobre", "entre", "hasta", "desde", "y", "e", "o", "u", "ni", "pero", "que", "se", "su",
        "sus", "mi", "mis", "tu", "tus", "es", "son", "fue", "muy", "no", "ya", "como", "cuando", "donde", "porque",
        "le", "les", "me", "te", "nos",
    }),
}  # fmt: skip
# Escalas e unidades que não abrem linha: são o fim de um número ("ninety | million"). No pt os números por
# extenso já são reconhecidos inteiros (`number_runs`); "mil" abre linha ("mil pessoas").
NO_LINE_START: dict[str, frozenset[str]] = {
    "pt": frozenset({"milhao", "milhoes", "bilhao", "bilhoes", "trilhao", "trilhoes", "cento"}),
    "en": frozenset({"hundred", "thousand", "million", "millions", "billion", "billions", "trillion", "percent"}),
    "es": frozenset({"millon", "millones", "billon", "billones", "ciento"}),
}  # fmt: skip
# Linha que fica menos que isto na tela pisca (o "pop" de entrada nem termina).
CAPTION_MIN_S = 0.6
# Rede de segurança depois da divisão: linha abaixo disto entra na vizinha, se couber.
CAPTION_MERGE_S = 0.5
# Linha curta (em maiúsculas) pode ter uma palavra a mais que `max_words_per_line`: "EM MAIO DE 2025,".
CAPTION_EXTRA_CHARS = 18
# Na rede de segurança, a linha que piscaria pode juntar com a vizinha da mesma frase até este tamanho.
CAPTION_FLASH_CHARS = 24
# Pontuação que o renderer tira do fim da linha (karaokê): não conta no tamanho.
_TRAILING_PUNCT = ".,;:!?…\"“”"
# Pesos da divisão (custo de cada linha; vence a divisão de menor custo total).
_LINE_COST = 1.0
_FUNCTION_END = 5.0
_DANGLING = 1.0
_NUMBER_SPLIT = 5.0
_SHORT_LINE = 4.0
_COMMA_BONUS = 2.0
_NEW_PHRASE_BONUS = 1.0
_CLOSERS = "\"'”’»)]}"


def _function_words(lang: str) -> frozenset[str]:
    return FUNCTION_WORDS.get(lang.lower()[:2], frozenset())


def _number_breaks(texts: list[str], lang: str) -> set[int]:
    """Quebras (índice da primeira palavra da linha seguinte) que cortariam um número no meio ("dois mil | e
    treze", "90 | million")."""
    keys = NO_LINE_START.get(lang.lower()[:2], frozenset())
    out = {j for i0, i1, _ in number_runs(texts, lang) for j in range(i0 + 1, i1)}
    out |= {j for j in range(1, len(texts)) if normalize_token(texts[j]) in keys}
    return out


def _line_fits(texts: list[str], max_words: int, max_chars: int = CAPTION_EXTRA_CHARS) -> bool:
    """Cabe numa linha: até `max_words` palavras, ou uma a mais quando a linha inteira é curta (até `max_chars`
    caracteres em maiúsculas, sem a pontuação do fim, que o renderer tira: "DE TODOS OS TEMPOS?" conta 18)."""
    if len(texts) <= max(1, max_words):
        return True
    chars = len(" ".join(texts).upper().rstrip(_TRAILING_PUNCT))
    return len(texts) == max(1, max_words) + 1 and chars <= max_chars


def _ends_phrase(text: str) -> bool:
    return text.rstrip(_CLOSERS).endswith((",", "—", "–"))


def _split_sentence(sentence: list[Word], max_words: int, lang: str, end: float = math.inf) -> list[list[Word]]:
    """Divide uma frase em linhas por programação dinâmica (menor custo total).

    Custos de cada linha: +1 por linha; +5 se termina em palavra funcional ("de", "que", "a") ou corta um número
    no meio; +1 se termina no meio de um sintagma ("da era | moderna"); +4 se fica menos de CAPTION_MIN_S na
    tela; −2 se termina em vírgula ou travessão; −1 se a próxima começa em palavra funcional (sintagma novo);
    e um termo de equilíbrio (desvio do tamanho médio em caracteres, ao quadrado). A última linha fica até 0,25 s
    depois da última palavra, ou até o fim da cena (`end`, em segundos), se vier antes.
    """
    n = len(sentence)
    if n <= 1:
        return [sentence] if sentence else []
    fn = _function_words(lang)
    texts = [w.text for w in sentence]
    keys = [normalize_token(t) for t in texts]
    number_breaks = _number_breaks(texts, lang)
    ideal = max(1, math.ceil(n / max(1, max_words)))
    target = max(1.0, (sum(len(t) for t in texts) + n - 1) / ideal)

    def cost(i: int, j: int) -> float:
        if not _line_fits(texts[i:j], max_words):
            return math.inf
        c = _LINE_COST
        if j < n:
            if _ends_phrase(texts[j - 1]):
                c -= _COMMA_BONUS
            elif keys[j - 1] in fn:
                c += _FUNCTION_END
            elif j - i >= 2 and keys[j - 2] in fn and keys[j] not in fn:
                c += _DANGLING
            if keys[j] in fn:
                c -= _NEW_PHRASE_BONUS
            if j in number_breaks:
                c += _NUMBER_SPLIT
            shown = sentence[j].start - sentence[i].start
        else:
            shown = min(sentence[-1].end + 0.25, end) - sentence[i].start
        if shown < CAPTION_MIN_S:
            c += _SHORT_LINE
        chars = len(" ".join(texts[i:j]))
        return c + ((chars - target) / target) ** 2

    best = [0.0] + [math.inf] * n
    back = [0] * (n + 1)
    for j in range(1, n + 1):
        for i in range(max(0, j - max(1, max_words) - 1), j):
            total = best[i] + cost(i, j)
            if total < best[j]:
                best[j], back[j] = total, i
    cuts: list[tuple[int, int]] = []
    j = n
    while j > 0:
        cuts.append((back[j], j))
        j = back[j]
    return [sentence[a:b] for a, b in reversed(cuts)]


def _shown(line: list[Word], nxt: list[Word] | None, end: float) -> float:
    """Quanto a linha fica na tela: até a próxima começar; a última, até 0,25 s depois da última palavra (sem
    passar do fim da cena, `end`)."""
    return (nxt[0].start if nxt else min(line[-1].end + 0.25, end)) - line[0].start


def _merge_flashes(lines: list[list[Word]], max_words: int, end: float = math.inf,
                   lang: str = "pt-BR") -> list[list[Word]]:
    """Rede de segurança: linha que ainda fica menos de CAPTION_MERGE_S na tela some. Em ordem de preferência:

    1. junta com a vizinha da mesma frase: no tamanho normal ou, para não piscar, com a palavra a mais até
       CAPTION_FLASH_CHARS caracteres ("FOI AINDA | MAIS VISTO." → "FOI AINDA MAIS VISTO."); de preferência a menor;
    2. pega uma palavra da vizinha da mesma frase ("A PRIMEIRA PROTAGONISTA | MULHER" → "A PRIMEIRA | PROTAGONISTA
       MULHER"), sem a linha da frente terminar em palavra funcional nem partir número, e sem nenhuma das duas piscar;
    3. por último, junta com a vizinha de outra frase, no tamanho normal (o fim da frase fica no meio da linha).

    Só dentro da cena: as linhas são montadas por cena, e a última fica até o fim da cena (`end`), se vier antes."""
    out = [list(line) for line in lines if line]
    fn = _function_words(lang)
    i = 0
    while i < len(out) and len(out) > 1:
        if _shown(out[i], out[i + 1] if i + 1 < len(out) else None, end) >= CAPTION_MERGE_S:
            i += 1
            continue
        options: list[tuple[int, int, int, list[list[Word]]]] = []  # (preferência, tamanho da vizinha, índice, linhas)
        for k in (i - 1, i + 1):
            if not 0 <= k < len(out):
                continue
            low = min(i, k)
            left, right = out[low], out[low + 1]
            pair = left + right
            texts = [w.text for w in pair]
            if left[-1].text.rstrip(_CLOSERS).endswith(SENTENCE_END):
                if _line_fits(texts, max_words):
                    options.append((3, len(out[k]), k, [pair]))
                continue
            if _line_fits(texts, max_words):
                options.append((0, len(out[k]), k, [pair]))
            elif _line_fits(texts, max_words, CAPTION_FLASH_CHARS):
                options.append((1, len(out[k]), k, [pair]))
            if len(out[k]) < 2:
                continue
            cut = len(left) + 1 if k > i else len(left) - 1  # a palavra da vizinha passa para a linha que pisca
            head, tail = pair[:cut], pair[cut:]
            follow = out[low + 2] if low + 2 < len(out) else None
            if (
                _line_fits([w.text for w in (head if k > i else tail)], max_words, CAPTION_FLASH_CHARS)
                and normalize_token(head[-1].text) not in fn
                and cut not in _number_breaks(texts, lang)
                and _shown(head, tail, end) >= CAPTION_MERGE_S
                and _shown(tail, follow, end) >= CAPTION_MERGE_S
            ):
                options.append((2, len(out[k]), k, [head, tail]))
        if not options:
            i += 1
            continue
        _, _, k, replacement = min(options, key=lambda o: (o[0], o[1], o[2]))
        low = min(i, k)
        out[low : low + 2] = replacement
        i = low
    return out


def _caption_lines(words: list[Word], max_words: int, mode: str, lang: str = "pt-BR",
                   end: float = math.inf) -> list[list[Word]]:
    """Linhas de legenda dentro de uma cena: quebra no fim das frases e, dentro delas, pela divisão de menor
    custo (`_split_sentence`); depois junta as linhas que ainda piscariam. `end`: fim da cena em segundos (a
    última linha só fica até ele, mesmo quando a frase continua na cena seguinte)."""
    if mode == "word":
        return [[w] for w in words]
    sentences: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        current.append(w)
        if w.text.rstrip(_CLOSERS).endswith(SENTENCE_END):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    lines: list[list[Word]] = []
    for sentence in sentences:
        lines.extend(_split_sentence(sentence, max_words, lang, end))
    return _merge_flashes(lines, max_words, end, lang)


def digit_captions(doc: ProjectDoc) -> bool:
    """Legenda e texto cinético com algarismos ('dois mil e treze' → '2013')?

    Só nos documentos do storyboard novo (com direção de arte ou cortes por `span`): os antigos continuam
    com a legenda de antes, e os blocos já renderizados não mudam.
    """
    return doc.art is not None or any(s.span is not None for s in doc.scenes)


def scene_word_ranges(scenes: list[Scene], words: list[Word]) -> list[tuple[int, int]]:
    """Faixas [início, fim) das palavras de cada cena: pelos spans do storyboard quando todas as cenas têm
    (proporcionais se a contagem não bate, ex.: tradução), senão a divisão equilibrada antiga."""
    spans = [s.span for s in scenes]
    if spans and all(span is not None for span in spans):
        return span_ranges([int(span) for span in spans], len(words))  # type: ignore[arg-type]
    return split_word_indices(len(words), len(scenes), [w.text for w in words])


def _same_number(a: float, b: float) -> bool:
    return abs(a - b) <= 0.05 * max(abs(a), abs(b)) if a or b else True


def _spoken_number(words: list[Word], lang: str, targets: list[float], *, fallback: bool) -> Word | None:
    """Palavra em que começa o número dito que vale um dos `targets` (o do cartão); sem ele e com `fallback`,
    o primeiro número que vale cartão ("dois adiamentos" só conta quando bate com o valor)."""
    tokens = [w.text for w in words]
    for i0, _i1, value in number_runs(tokens, lang):
        if any(_same_number(float(value), t) for t in targets):
            return words[i0]
    if fallback:
        notable = notable_number_runs(tokens, lang)
        if notable:
            return words[notable[0][0]]
    return None


def _card_targets(scene: Scene, card: dict[str, Any]) -> list[float]:
    texts = [card.get("value")] if scene.kind == "stat" else [item.get("value") for item in card.get("items", [])]
    values = [v for v in (card_number(t or "") for t in texts) if v is not None]
    if scene.kind == "stat" and not values and scene.number is not None:
        values.append(float(scene.number))
    return values


def _spoken_timing(
    resolved: dict[str, Any],
    scene: Scene,
    words: list[Word],
    lang: str,
    f0: int,
    frame: Callable[[float], int],
    fps: int,
) -> None:
    """Quando o número é dito (quadro relativo à cena), só nas cenas que têm o que esperar.

    - `reveal` (stat/compare): o cartão entra quando o número do cartão é dito, não no corte.
    - `card.stamp_from`: carimbo com número ("2013") entra quando o número é dito, não 6 quadros depois do corte.
    Os números vêm das palavras da narração antes de virar algarismo ("dois" de "dois adiamentos" também
    conta quando bate com o cartão). Sem número dito, a chave não aparece (e o renderer faz como antes).
    """
    duration = resolved["durationInFrames"]
    card = resolved.get("card") or {}
    if scene.kind in REVEAL_KINDS and (card or scene.number is not None):
        hit = _spoken_number(words, lang, _card_targets(scene, card), fallback=True)
        if hit is not None:
            latest = duration - round(REVEAL_MIN_VISIBLE_S * fps)
            resolved["reveal"] = max(0, min(frame(hit.start) - f0, latest))
    stamp = card.get("stamp")
    if stamp and scene.kind not in REVEAL_KINDS and re.search(r"\d", stamp):
        value = card_number(stamp)
        hit = _spoken_number(words, lang, [value], fallback=False) if value is not None else None
        if hit is not None:
            at = min(frame(hit.start) - f0, duration - round(STAMP_MIN_VISIBLE_S * fps))
            if at > STAMP_DELAY:
                card["stamp_from"] = at


def _sfx_event(kind: str, at: float, scene_id: str) -> dict[str, Any]:
    lead = SFX_LEAD.get(kind, SFX_LEAD_DEFAULT)
    return {"type": kind, "start": round(max(0.0, at - lead), 3), "scene_id": scene_id}


def _scene_sfx(style: StyleSpec, scene: Scene, resolved: dict[str, Any], s0: float, fps: int, *, first: bool) -> list[dict[str, Any]]:
    """Efeitos sonoros da cena.

    - No corte: o som escolhido na cena, o da transição ou, nos cartões, o impacto (a primeira cena só tem o
      escolhido). No cartão de dado com `reveal`, o impacto vai para quando o número é dito e o corte fica com o
      som da transição, se houver.
    - Faixa do topo sem outro som: impacto no corte (ela desce no quadro 0).
    - Carimbo solto: impacto quando ele pousa (o do painel entra com o cartão).
    """
    if not style.audio.sfx.auto:
        return [_sfx_event(scene.sfx, s0, scene.id)] if scene.sfx else []
    card = resolved.get("card") or {}
    transition_sfx = None if first else SFX_FOR_TRANSITION.get(resolved["transition"])
    kind = scene.sfx or transition_sfx
    if scene.kind in IMPACT_KINDS and not scene.sfx and not first:
        kind = "impact"
    events: list[dict[str, Any]] = []
    at = s0
    reveal = resolved.get("reveal")
    if kind == "impact" and reveal and reveal > round(REVEAL_POP_S * fps):
        at = s0 + reveal / fps
        if transition_sfx and not scene.sfx:
            events.append(_sfx_event(transition_sfx, s0, scene.id))
    if not kind and card.get("headline"):
        kind = "impact"
    if kind:
        events.append(_sfx_event(kind, at, scene.id))
    if card.get("stamp") and scene.kind not in REVEAL_KINDS:
        land = s0 + card.get("stamp_from", STAMP_DELAY) / fps + STAMP_LAND_S
        events.append(_sfx_event("impact", land, scene.id))
    return sorted(events, key=lambda e: e["start"])


def build_timeline(
    doc: ProjectDoc,
    style: StyleSpec,
    lang: str,
    narration: dict[str, NarrationClip],
    images: dict[str, str | None],
) -> dict[str, Any]:
    """Monta a timeline completa de um idioma (em quadros)."""
    fps = style.format.fps
    if not doc.segments:
        raise TimelineError("o projeto não tem roteiro")
    gap = style.voice.gap_s
    t = lead_in(style)
    placements: list[tuple[Any, float, NarrationClip]] = []
    for seg in doc.segments:
        clip = narration.get(seg.id)
        if clip is None:
            raise TimelineError(f"falta a narração do segmento {seg.id} ({lang})")
        placements.append((seg, t, clip))
        t += clip.duration + gap
    total = t - gap + tail_out(style)

    def frame(seconds: float) -> int:
        return int(math.floor(seconds * fps + 0.5))

    scenes_out: list[dict[str, Any]] = []
    sfx: list[dict[str, Any]] = []
    chapters: list[dict[str, Any]] = []
    caption_style = style.edit.captions
    digits = digit_captions(doc)
    for index, (seg, start, clip) in enumerate(placements):
        scenes = doc.scenes_of(seg.id)
        if not scenes:
            raise TimelineError(f"o segmento {seg.id} não tem cenas (rode o storyboard)")
        if seg.role == "chapter" or (seg.chapter and seg.role in ("chapter", "item")):
            chapters.append({"title": seg.chapter or seg.text_for(lang, doc.primary_language)[:40], "start": round(start, 2)})
        words_rel = normalize_words(clip.words, clip.duration)
        abs_words = [Word(w.text, start + w.start, start + w.end) for w in words_rel]
        seg_begin = 0.0 if index == 0 else start
        seg_end = placements[index + 1][1] if index + 1 < len(placements) else total
        n_scenes = len(scenes)
        min_step = 1.5 / fps
        ranges = scene_word_ranges(scenes, abs_words)
        bounds: list[float] = [seg_begin]
        for k in range(1, n_scenes):
            a = ranges[k][0] if k < len(ranges) else len(abs_words)
            if a < len(abs_words):
                candidate = abs_words[a].start - 0.05
            else:
                candidate = bounds[-1] + (seg_end - bounds[-1]) / (n_scenes - k + 1)
            low = bounds[-1] + min_step
            high = seg_end - (n_scenes - k) * min_step
            bounds.append(max(low, min(candidate, high)) if high >= low else low)
        bounds.append(seg_end)
        for k, scene in enumerate(scenes):
            s0, s1 = bounds[k], bounds[k + 1]
            f0, f1 = frame(s0), max(frame(s0) + 1, frame(s1))
            raw_words = [w for w in abs_words if s0 <= w.start < s1]
            scene_words = merge_number_words(raw_words, lang) if digits else raw_words
            scene_lines = []
            if caption_style.style != "none":
                for part in _caption_lines(scene_words, caption_style.max_words_per_line, caption_style.style, lang,
                                           s1):
                    l_start = frame(part[0].start) - f0
                    l_end = min(f1 - f0, frame(part[-1].end + 0.25) - f0)
                    scene_lines.append(
                        {
                            "from": max(0, l_start),
                            "to": max(l_start + 1, l_end),
                            "words": [
                                {"text": w.text, "from": max(0, frame(w.start) - f0), "to": max(1, frame(w.end) - f0)}
                                for w in part
                            ],
                        }
                    )
                # Cada linha some quando a próxima começa
                for i in range(len(scene_lines) - 1):
                    scene_lines[i]["to"] = min(scene_lines[i]["to"], scene_lines[i + 1]["from"]) or scene_lines[i]["to"]
            resolved = resolve_scene(doc, style, scene, lang, is_first=(index == 0 and k == 0))
            resolved.update(
                {
                    "from": f0,
                    "durationInFrames": f1 - f0,
                    "src": images.get(scene.id),
                    "captions": scene_lines,
                    "words": [{"text": w.text, "from": frame(w.start) - f0, "to": frame(w.end) - f0} for w in scene_words],
                }
            )
            _spoken_timing(resolved, scene, raw_words, lang, f0, frame, fps)
            scenes_out.append(resolved)
            sfx.extend(_scene_sfx(style, scene, resolved, s0, fps, first=(index == 0 and k == 0)))

    # Garante inícios estritamente crescentes (arredondamento) e cenas contínuas: cada uma termina onde a
    # próxima começa. Sem isso, blocos de render poderiam ter quadros a mais e o vídeo sairia deslocado.
    for i in range(1, len(scenes_out)):
        if scenes_out[i]["from"] <= scenes_out[i - 1]["from"]:
            scenes_out[i]["from"] = scenes_out[i - 1]["from"] + 1
    for i in range(len(scenes_out) - 1):
        scenes_out[i]["durationInFrames"] = scenes_out[i + 1]["from"] - scenes_out[i]["from"]
    total_frames = max(frame(total), scenes_out[-1]["from"] + scenes_out[-1]["durationInFrames"])
    scenes_out[-1]["durationInFrames"] = total_frames - scenes_out[-1]["from"]

    out: dict[str, Any] = {
        "version": 1,
        "lang": lang,
        "fps": fps,
        "width": style.format.width,
        "height": style.format.height,
        "durationInFrames": total_frames,
        "duration": round(total_frames / fps, 3),
        "style": render_style(style, doc),
        "scenes": scenes_out,
        "narration": [
            {"segment_id": seg.id, "path": clip.path, "start": round(start, 3), "duration": round(clip.duration, 3)}
            for seg, start, clip in placements
        ],
        "sfx": sfx,
        "chapters": chapters,
    }
    # Avisos para o usuário (o build junta aos do job e tira da timeline); só quando há.
    warnings = news_reading_warnings(doc, lang, scenes_out, fps)
    if warnings:
        out["warnings"] = warnings
    return out


def news_reading_warnings(doc: ProjectDoc, lang: str, scenes: list[dict[str, Any]], fps: int) -> list[str]:
    """Cenas com cartão de notícia que somem antes de dar para ler a manchete (NEWS_READ_WORD_S por palavra mais
    NEWS_READ_BASE_S), com o tamanho que caberia no tempo da cena."""
    tag = f" ({lang})" if len(doc.languages) > 1 else ""
    out: list[str] = []
    for scene in scenes:
        n = len(((scene.get("news") or {}).get("headline") or "").split())
        shown = scene["durationInFrames"] / fps
        if not n or shown >= NEWS_READ_BASE_S + NEWS_READ_WORD_S * n - 1e-9:
            continue
        limit = int((shown - NEWS_READ_BASE_S) / NEWS_READ_WORD_S + 1e-9)
        tip = f"encurte para até {limit} palavras" if limit >= 2 else "deixe a cena mais longa"
        words = f"{n} palavra{'s' if n > 1 else ''}"
        seconds = f"{shown:.1f}".replace(".", ",")
        out.append(f"{scene_label(doc, scene['id'])}{tag}: a manchete da notícia ({words}) fica {seconds} s na tela; {tip}.")
    return out


def real_motion_pool(style: StyleSpec) -> dict[str, float]:
    """Câmeras da foto real (movimento suave): os pesos do estilo nestes movimentos, ou peso igual sem nenhum."""
    pool = {move: style.edit.camera.get(move, 0.0) for move in REAL_MOTIONS}
    return pool if any(w > 0 for w in pool.values()) else dict.fromkeys(REAL_MOTIONS, 1.0)


def news_date(published: str, lang: str) -> str:
    """Data da notícia como aparece no cartão: "14 nov. 2026" (pt), "Nov 14, 2026" (en), "14 nov 2026" (es).
    Mês e ano: "nov. 2026"; só o ano: "2026"; vazio ou inválido: ""."""
    found = re.fullmatch(r"(\d{4})(?:-(\d{2})(?:-(\d{2}))?)?", (published or "").strip())
    if not found:
        return ""
    year, month, day = found.group(1), found.group(2), found.group(3)
    if month is None:
        return year
    if not 1 <= int(month) <= 12 or (day is not None and not 1 <= int(day) <= 31):
        return ""
    code = lang.lower()[:2]
    code = code if code in NEWS_MONTHS else "en"
    name = NEWS_MONTHS[code][int(month) - 1]
    if day is None:
        return f"{name} {year}"
    if code == "en":
        return f"{name} {int(day)}, {year}"
    return f"{int(day)} {name} {year}"


def _fold_headline(text: str) -> str:
    return " ".join(text.split()).strip(' "“”«»').casefold()


def verbatim_headline(media: MediaRef, headline: str) -> bool:
    """A manchete mostrada é a do veículo, palavra por palavra? Compara (sem diferença de espaços e maiúsculas) com o
    título da página, cru e limpo como na importação (`clean_headline`, sem o nome do site). Só então o cartão põe
    aspas: a tradução ou a manchete reescrita pelo usuário não é citação."""
    title = " ".join(media.title.split())
    shown = _fold_headline(headline)
    if not title or not shown:
        return False
    # O nome do site saiu na importação pelo og:site_name ou pelo host; o veículo pode ter sido editado depois
    # ("IGN Brasil" → "IGN"). Vale também o título sem a primeira ou a última parte separada por " | ", " - " etc.
    options = [title, clean_headline(title, media.outlet), clean_headline(title, site_label(media.page_url))]
    for sep in _HEADLINE_SEPARATORS:
        head, found, tail = title.rpartition(sep)
        if found:
            options += [head, title.partition(sep)[2]]
    return shown in {_fold_headline(clean_headline(o)) for o in options if o.strip()} | {_fold_headline(title)}


def _real_media_keys(doc: ProjectDoc, scene: Scene, lang: str, out: dict[str, Any]) -> None:
    """Crédito na tela (texto pronto) e cartão de notícia, só quando a cena mostra mídia real."""
    media = doc.shown_media(scene)
    if media is not None and media.asset:
        line = media.credit_line(lang)
        if line:
            out["credit"] = line
    own = scene.visual.media
    if own is not None and own.kind == "news" and scene.kind == "image":
        outlet = own.outlet.strip()
        headline = own.headline_for(lang, doc.primary_language).strip(' "“”«»')
        if outlet or headline:
            news: dict[str, Any] = {"outlet": outlet, "headline": headline,
                                    "quoted": verbatim_headline(own, headline)}
            date = news_date(own.published, lang)
            if date:
                news["date"] = date
            if own.asset:
                news["image"] = own.asset  # original (miniatura do cartão); o `src` da cena é a versão enquadrada
            out["news"] = news


def resolve_scene(doc: ProjectDoc, style: StyleSpec, scene: Scene, lang: str, *, is_first: bool) -> dict[str, Any]:
    """Escolhas visuais da cena (determinísticas pela semente da cena)."""
    media = doc.shown_media(scene)
    camera = real_motion_pool(style) if media is not None else style.edit.camera
    motion = scene.motion or pick_weighted(camera, seed_from(doc.seed, scene.id, "motion"))
    if is_first:
        transition = "cut"
    else:
        transition = scene.transition_in or pick_weighted(style.edit.transitions, seed_from(doc.seed, scene.id, "transition"))
    text = scene.overlay_text.get(lang) or scene.overlay_text.get(doc.primary_language) or ""
    out: dict[str, Any] = {
        "id": scene.id,
        "kind": scene.kind,
        "motion": motion,
        "transition": transition,
        "seed": seed_from(doc.seed, scene.id, scene.visual.variation),
        "look": scene.look.model_dump(),
        "text": text,
        "number": scene.number,
    }
    # Chaves novas só quando preenchidas: a cena antiga gera o mesmo dict (e o mesmo hash de bloco) de antes.
    card = scene.card.resolved(lang, doc.primary_language) if scene.card is not None else {}
    if card.get("stamp") and scene.kind not in REVEAL_KINDS:
        place = stamp_place(*image_framing(doc, scene))
        if place:
            card["stamp_at"] = place
    if card:
        out["card"] = card
    focus = pick(scene.focus, FOCUS_POINTS)
    if focus:
        out["focus"] = focus
    elif media is not None and media.asset and pick(media.focus, FOCUS_POINTS) not in (None, "center"):
        # O recorte da foto centra no sujeito: a câmera também (ele fica na mesma posição relativa).
        out["focus"] = pick(media.focus, FOCUS_POINTS)
    _real_media_keys(doc, scene, lang, out)
    treatment = scene_treatment(doc, style, scene, has_news="news" in out)
    if treatment != "plain":
        out["treatment"] = treatment
    return out


def scene_treatment(doc: ProjectDoc, style: StyleSpec, scene: Scene, *, has_news: bool = False) -> str:
    """Variação de família da cena de imagem (whiteboard, colagem, tela), sorteada pelos pesos do estilo.
    "plain" (imagem com câmera, sem chave na timeline) para os outros tipos de cena, para a cena com cartão
    de notícia (o cartão já é o desenho dela) e para os estilos sem variação."""
    if scene.kind != "image" or has_news:
        return "plain"
    pool = treatment_pool(style)
    if not pool or set(pool) == {"plain"}:
        return "plain"
    return pick_weighted(pool, seed_from(doc.seed, scene.id, "treatment"))


def image_framing(doc: ProjectDoc, scene: Scene) -> tuple[str | None, str | None]:
    """Plano e composição da imagem que a cena mostra (a própria ou a reaproveitada), já com a variação de
    "Outra imagem" (a mesma conta do prompt dirigido). (None, None) quando não se sabe: cena antiga (sem
    linguagem visual), sem direção de arte, foto de banco, upload, referência solta, mídia real."""
    root = doc.reuse_root(scene)
    if root is None or doc.art is None or root.visual.media is not None:
        return None, None
    visual = root.visual
    if visual.type != "ai_image" or not visual.language:
        return None, None
    shot, composition, _ = rotate_variant(visual.shot, visual.composition, visual.language, doc.art.languages, visual.variation)
    return shot, composition


def stamp_place(shot: str | None, composition: str | None) -> str | None:
    """Onde o carimbo solto fica para não cobrir o sujeito: do lado oposto a ele, na altura de 0,3 da tela
    ("left"/"right"), ou embaixo ("low") quando o sujeito ocupa o meio ou a metade de cima, para nunca cair num
    rosto na altura da cabeça. None: composição desconhecida, o renderer usa o padrão (à direita)."""
    if not composition:
        return None
    if composition == "right_third":
        return "left"
    if composition == "upper_half" or (composition == "center" and shot in PERSON_SHOTS):
        return "low"
    return "right"


def render_effects(style: StyleSpec, doc: ProjectDoc | None = None) -> list[dict[str, Any]]:
    """Efeitos do estilo; a gradação da direção de arte (`doc.art.grade`) troca o preset do efeito `grade`
    do estilo, com intensidade até `ART_GRADE_MAX`. Estilo sem efeito de cor continua sem."""
    effects = [e.model_dump() for e in style.edit.effects]
    grade = doc.art.grade if doc is not None and doc.art is not None else None
    if grade in GRADE_PRESETS:
        for effect in effects:
            if effect["type"] == "grade":
                effect["preset"] = grade
                effect["amount"] = min(effect["amount"], ART_GRADE_MAX)
                break
    return effects


def render_style(style: StyleSpec, doc: ProjectDoc | None = None) -> dict[str, Any]:
    """Subconjunto do estilo que o renderer usa (entra no hash dos blocos)."""
    return {
        "family": style.family.value,
        "families": [f.value for f in style.all_families()],
        "effects": render_effects(style, doc),
        "captions": style.edit.captions.model_dump(),
        "textCards": style.edit.text_cards.model_dump(),
        "zoomStrength": style.edit.zoom_strength,
        "transitionFrames": max(1, int(round(style.edit.transition_seconds * style.format.fps))),
        "progressBar": style.edit.progress_bar,
        "progressColor": style.edit.progress_color,
    }


# --------------------------------------------------------------------------------------------
# Blocos de render
# --------------------------------------------------------------------------------------------

CHUNK_AVG_SCENES = 6
CHUNK_MAX_SCENES = 12


def _is_boundary(scene_id: str) -> bool:
    value = int(hashlib.sha256(scene_id.encode("utf-8")).hexdigest()[:8], 16)
    return value % CHUNK_AVG_SCENES == 0


def chunk_timeline(timeline: dict[str, Any], renderer_version: str) -> list[dict[str, Any]]:
    """Divide a timeline em blocos definidos pelo conteúdo. Cada bloco tem props e um hash próprio."""
    scenes = timeline["scenes"]
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for scene in scenes:
        current.append(scene)
        if _is_boundary(scene["id"]) or len(current) >= CHUNK_MAX_SCENES:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    chunks = []
    for group in groups:
        start = group[0]["from"]
        duration = sum(s["durationInFrames"] for s in group)
        rel_scenes = [{**s, "from": s["from"] - start} for s in group]
        props = {
            "fps": timeline["fps"],
            "width": timeline["width"],
            "height": timeline["height"],
            "durationInFrames": duration,
            "style": timeline["style"],
            "scenes": rel_scenes,
            "isChunk": True,
            "lang": timeline["lang"],
        }
        key = hash_obj({"props": props, "renderer": renderer_version})
        chunks.append({"key": key, "from": start, "durationInFrames": duration, "scene_ids": [s["id"] for s in group], "props": props})
    return chunks
