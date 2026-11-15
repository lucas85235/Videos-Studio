"""Revisão determinística do storyboard, antes de gastar imagens.

`check(doc, style, beats)` confere as cenas planejadas para as batidas e devolve os problemas:
- grave: justifica pedir a correção ao Claude (número sem cartão, motivo proibido, estilo ou marca no
  prompt, número inventado no cartão);
- corrigido: o código já arrumou no documento (plano repetido, gancho sem imagem nova);
- aviso: o usuário vê, nada muda (motivo repetido, cartões seguidos ou demais).

As correções mudam o `doc` recebido. Nada aqui chama serviço externo.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from studio.core.hashing import seed_from
from studio.core.numbers import card_number, notable_number_runs, text_numbers
from studio.core.project.document import CARD_KINDS, ProjectDoc, Scene, SceneCard
from studio.core.styles.schema import StyleSpec
from studio.core.styles.visual import (
    SHOT_ROTATION,
    SHOTS,
    compositions_for_caption,
)
from studio.core.text import normalize_token
from studio.core.text import words as split_words
from studio.workers.pipeline.build import scene_needs_image

GRAVE = "grave"
AVISO = "aviso"
CORRIGIDO = "corrigido"
_SEVERITY_ORDER = {GRAVE: 0, AVISO: 1, CORRIGIDO: 2}

# Cota estimada do Codex por imagem nova (segundos).
SECONDS_PER_IMAGE = 35
MAX_CARDS_IN_ROW = 2
MAX_CARD_SHARE = 0.45
MOTIF_SHARE = 0.30

# Estilo e técnica no prompt: o app já acrescenta a linguagem visual e o tratamento. Palavras comuns ("canvas",
# "palette", "style", "4K") só contam na forma técnica: "a canvas tent", "a retro style diner" e "a 4K TV" passam.
STYLE_TERMS = (
    "oil painting", "oil on canvas", "on canvas", "canvas texture", "watercolor", "watercolour", "color palette",
    "colour palette", "palette of", "chiaroscuro", "brushstroke", "brush strokes", "color grading", "colour grading",
    "film grain", "concept art", "digital art", "artstation", "octane render", "unreal engine", "masterpiece",
    "in the style of", "style of", "art style", "artistic style", "visual style", "painting style",
    "illustration style", "drawing style", "rendering style", "anime style", "cartoon style", "in 4k", "in 8k",
    "4k resolution", "8k resolution",
)  # fmt: skip
# Marca, material oficial e interface: risco de direitos e de imagem falsa. "Trailer" e "leak" só na forma de mídia
# ("game trailer", "leaked"): "a trailer park" e "a gas leak" passam.
BRAND_TERMS = (
    "logo", "screenshot", "gameplay", "leaked", "hud", "game trailer", "official trailer", "teaser trailer",
    "movie trailer", "film trailer", "trailer footage", "trailer scene", "trailer still", "trailer frame",
    "from the trailer", "in the trailer",
)  # fmt: skip  # o plural simples também conta
# Final que parece plural mas não é ("news", "bus", "analysis", "glass"): o "s" não sai.
_NOT_PLURAL_ENDINGS = ("ss", "us", "is", "ws")

_STOPWORDS = {
    "the", "and", "with", "from", "into", "onto", "over", "under", "near", "behind", "front", "across", "along",
    "around", "between", "through", "while", "their", "there", "this", "that", "these", "those", "some", "each",
    "other", "very", "more", "most", "only", "just", "like", "toward", "towards", "against", "above", "below",
    "shot", "scene", "image", "view", "light", "lighting", "background", "foreground", "close", "wide", "detail",
    "medium", "angle", "subject", "center", "left", "right", "upper", "lower", "half", "third", "frame", "showing",
    "shows", "looking", "standing", "sitting", "holding", "seen", "made", "full", "large", "small", "big", "huge",
    "dark", "bright", "soft", "warm", "cold", "cool", "night", "day", "time", "early", "late", "evening",
}  # fmt: skip
# Cor e brilho vêm da paleta e do tratamento da direção: repetir "pink" ou "glow" não é motivo repetido.
_COLOR_WORDS = {
    "pink", "teal", "orange", "purple", "violet", "magenta", "cyan", "turquoise", "gold", "golden", "amber",
    "crimson", "scarlet", "blue", "green", "yellow", "white", "black", "silver", "pastel", "neon", "glow",
    "glowing", "colorful", "colourful",
}  # fmt: skip
# Palavras do papel da entidade que não descrevem nada ("Lucia (original heroine)" → "heroine").
_ROLE_FILLER = {"original", "fictional", "fictitious", "generic", "unnamed", "new", "the", "a", "an"}
# Nome e papel da entidade: "Lucia (original heroine)", "Lucia Caminos - protagonist", "Vice City, the coastal city".
_NAME_SPLIT_RE = re.compile(r"\s*(?:\(|\s[-–—]\s|[–—,:;])\s*")
# Ligações minúsculas dentro de nome próprio ("Rio de Janeiro", "Bay of Pigs").
_NAME_LINKS = {"the", "a", "an", "of", "de", "do", "da", "dos", "das", "del", "la", "el", "and", "e", "y", "von", "van"}
# Fim da aparência que diz quem ou o que é a entidade (o resto é detalhe: cabelo, roupa, cores).
_SUBJECT_NOUNS = {
    "woman", "man", "girl", "boy", "person", "teen", "teenager", "child", "kid", "guy", "lady", "gentleman",
    "soldier", "officer", "detective", "agent", "driver", "worker", "figure", "hero", "heroine", "villain",
    "king", "queen", "captain", "sailor", "robot", "android", "creature", "dog", "cat", "horse", "animal", "city",
    "town", "village", "island", "street", "harbor", "harbour", "port", "beach", "coast", "state", "country",
    "district", "neighborhood", "building", "house", "mansion", "castle", "tower", "ship", "boat", "car", "truck",
    "plane", "aircraft", "station", "studio", "office", "store", "shop", "bar", "club", "temple", "church",
    "palace", "metropolis", "skyline",
}  # fmt: skip
# Corte da aparência para citar a entidade no prompt: "Latina woman in her 30s, dark hair" → "Latina woman".
_LOOK_CUT_RE = re.compile(r",|;|\.|\s(?:in|with|of|wearing|who|that|whose)\s", re.IGNORECASE)


@dataclass
class Beat:
    """Uma batida da narração: vira uma cena (chave "{seg.id}:{k}")."""

    key: str
    segment_id: str
    part: int
    parts: int
    index: int
    """Índice do segmento no documento."""
    role: str
    words: list[str]
    seconds: float
    kind_hint: str = "image"
    overlay_hint: str | None = None

    @property
    def text(self) -> str:
        return " ".join(self.words)


@dataclass
class Issue:
    code: str
    severity: str
    message: str
    scene_key: str | None = None
    scene_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "scene_key": self.scene_key,
            "scene_id": self.scene_id,
            "message": self.message,
        }


def sort_issues(issues: list[Issue]) -> list[Issue]:
    return sorted(issues, key=lambda i: _SEVERITY_ORDER.get(i.severity, 9))


def graves(issues: list[Issue]) -> list[Issue]:
    return [i for i in issues if i.severity == GRAVE]


# --- consultas ---------------------------------------------------------------------------------


def scene_for(doc: ProjectDoc, beat: Beat) -> Scene | None:
    scenes = doc.scenes_of(beat.segment_id)
    return scenes[beat.part] if beat.part < len(scenes) else None


def has_own_image(scene: Scene) -> bool:
    """A cena gera imagem nova de IA (não reaproveita nem usa asset fixo)."""
    return scene_needs_image(scene) and not scene.visual.reuse_of and not scene.visual.asset


def is_hook_beat(doc: ProjectDoc, beat: Beat) -> bool:
    return bool(doc.segments) and beat.segment_id == doc.segments[0].id and beat.part == 0


def review_counts(doc: ProjectDoc, budget: int) -> dict[str, int]:
    """Contagens do documento final (inclui as cenas dos segmentos travados)."""
    new_images = sum(1 for s in doc.scenes if scene_needs_image(s) and not s.visual.asset)
    return {
        "scenes": len(doc.scenes),
        "new_images": new_images,
        "reused": sum(1 for s in doc.scenes if s.visual.reuse_of),
        "cards": sum(1 for s in doc.scenes if s.kind in CARD_KINDS),
        "budget": budget,
        "est_seconds": new_images * SECONDS_PER_IMAGE,
    }


def _term_re(term: str) -> re.Pattern[str]:
    """Termo como palavra inteira; a última palavra aceita o plural simples ("game trailers", "logos")."""
    words = term.lower().split()
    if not words:
        return re.compile(r"(?!x)x")
    stem = words[-1]
    if len(stem) > 3 and stem.endswith("s") and not stem.endswith(_NOT_PLURAL_ENDINGS):
        stem = stem[:-1]
    if any(ch.isdigit() for ch in stem):
        last = re.escape(stem)  # "4k" não tem plural
    else:
        last = rf"{re.escape(stem)}(?:s|es)?"
    head = "".join(rf"{re.escape(w)}\s+" for w in words[:-1])
    return re.compile(rf"(?<![\w-]){head}{last}(?![\w-])", re.IGNORECASE)


_NEGATION_RE = re.compile(r"(?:\bno|\bnot|\bwithout|\bzero|\bnever)\s+(?:[\w-]+\s+){0,2}$", re.IGNORECASE)


def find_terms(text: str, terms: tuple[str, ...] | list[str]) -> list[str]:
    """Termos de `terms` presentes em `text` (palavra inteira, aceita plural simples).

    Menção negada ("no logos", "without candles") não conta: só repete o que o app já pede.
    """
    found = []
    for term in terms:
        term = (term or "").strip()
        if not term or term in found:
            continue
        for match in _term_re(term).finditer(text):
            if not _NEGATION_RE.search(text[max(0, match.start() - 40) : match.start()]):
                found.append(term)
                break
    return found


def _has_digit(text: str) -> bool:
    return any(ch.isdigit() for ch in text)


def _shows_number(scene: Scene) -> bool:
    """A cena mostra um número na tela (cartão, sobreposição, contagem ou a narração em tipografia)."""
    if scene.kind == "text":
        return True  # as palavras da batida aparecem grandes (com algarismos na legenda em pt)
    if scene.kind == "count" and scene.number is not None:
        return True
    texts: list[str] = [t for t in scene.overlay_text.values() if t]
    if scene.card:
        card = scene.card
        texts += [*card.value.values(), *card.label.values(), *card.headline.values(), *card.stamp.values()]
        for item in card.items:
            texts += [*item.label.values(), *item.value.values()]
    return any(_has_digit(t) for t in texts)


def _spoken(beat: Beat, i0: int, i1: int) -> str:
    return " ".join(beat.words[i0:i1]).strip(" .,;:!?…\"'”’»)")


def _known_number(value: float, allowed: list[float]) -> bool:
    """O valor (sem o sinal: "-30%" para "caíram trinta por cento") está entre os permitidos, com 2% de folga."""
    value = abs(value)
    for ref in allowed:
        ref = abs(ref)
        if value == ref or abs(value - ref) <= 0.02 * max(value, ref):
            return True
    return False


# "meio milhão", "meia dúzia de mil": metade da escala (o leitor de números não junta "meio" com a escala).
_HALF_WORDS = {"meio", "meia"}
_HALF_SCALES = {"mil": 1e3, "milhao": 1e6, "milhoes": 1e6, "bilhao": 1e9, "bilhoes": 1e9, "trilhao": 1e12}


def _half_scales(text: str) -> list[float]:
    tokens = [normalize_token(t) for t in split_words(text)]
    return [0.5 * _HALF_SCALES[b] for a, b in zip(tokens, tokens[1:], strict=False) if a in _HALF_WORDS and b in _HALF_SCALES]


def allowed_numbers(doc: ProjectDoc) -> list[float]:
    """Números que um cartão pode mostrar: os da narração (inclusive "GTA 6") e os das fontes.

    O 1 sempre vale ("#1", "1º", "1 DIA" para "o primeiro", "em um dia"): "um" sozinho é artigo para o leitor.
    """
    lang = doc.primary_language
    texts = [doc.full_text(lang), *(f"{source.title}. {source.note}" for source in doc.sources)]
    values = [1.0]
    for text in texts:
        values += [float(v) for v in text_numbers(text, lang, names=True)]
        if lang.lower().startswith("pt"):
            values += _half_scales(text)
    return values


def card_numbers(card: SceneCard) -> list[str]:
    """Textos de valor do cartão (value e items[].value) em todos os idiomas: o que a revisão confere."""
    out = [*card.value.values()]
    for item in card.items:
        out += [*item.value.values()]
    return [t.strip() for t in out if t and t.strip()]


# --- correções automáticas ---------------------------------------------------------------------


def fix_hook(doc: ProjectDoc, style: StyleSpec, beats: list[Beat]) -> list[Issue]:
    """A primeira cena do vídeo precisa de imagem nova (o gancho): cartão vira imagem com `art.hook`."""
    if not beats or not is_hook_beat(doc, beats[0]):
        return []
    beat = beats[0]
    scene = scene_for(doc, beat)
    if scene is None or scene.locked or scene.kind == "count" or (scene.kind == "image" and has_own_image(scene)):
        return []  # o contador do Top 10 fica como é
    art = doc.art
    lang = doc.primary_language
    languages = (art.languages if art else []) or style.art.languages or ["editorial"]
    card = scene.card or SceneCard()
    if not card.headline.get(lang):
        carried = card.value.get(lang) or scene.overlay_text.get(lang) or ""
        if carried and card.label.get(lang):
            carried = f"{carried} {card.label[lang]}"
        if carried:
            card.headline = {lang: " ".join(carried.split()[:5])}
    card.value, card.label, card.items, card.highlight = {}, {}, [], None
    scene.card = card if card.resolved(lang) else None
    scene.kind = "image"
    scene.overlay_text = {}
    scene.number = None
    scene.focus = None
    scene.motion = "punch_zoom"
    scene.visual.type = "ai_image"
    scene.visual.reuse_of = None
    scene.visual.asset = None
    scene.visual.prompt = ((art.hook if art else "") or beat.text).strip()
    scene.visual.language = languages[0]
    scene.visual.shot = scene.visual.shot or "wide"
    safe = compositions_for_caption(style.edit.captions.position)
    if scene.visual.composition not in safe:
        scene.visual.composition = safe[0]
    scene.visual.entities = []
    scene.visual.seed = seed_from(doc.seed, beat.segment_id, beat.part)
    return [Issue("gancho_sem_imagem", CORRIGIDO, "A primeira cena virou imagem nova com o gancho da direção.",
                  beat.key, scene.id)]


def fix_repeated_shots(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Imagens novas seguidas não repetem o plano: a segunda anda na sequência fixa de planos."""
    keyed = [(beat, scene) for beat in beats if (scene := scene_for(doc, beat)) is not None]
    news = [(beat, scene) for beat, scene in keyed if has_own_image(scene) and scene.visual.shot and not scene.locked]
    issues: list[Issue] = []
    for index in range(1, len(news)):
        beat, scene = news[index]
        prev_shot = news[index - 1][1].visual.shot
        if scene.visual.shot != prev_shot:
            continue
        next_shot = news[index + 1][1].visual.shot if index + 1 < len(news) else None
        base = SHOT_ROTATION.index(prev_shot) if prev_shot in SHOT_ROTATION else 0
        for step in range(1, len(SHOT_ROTATION)):
            candidate = SHOT_ROTATION[(base + step) % len(SHOT_ROTATION)]
            if candidate not in (prev_shot, next_shot):
                scene.visual.shot = candidate
                break
        label = SHOTS[scene.visual.shot].label.lower() if scene.visual.shot in SHOTS else scene.visual.shot
        issues.append(Issue("plano_repetido", CORRIGIDO, f"Plano repetido trocado para {label}.", beat.key, scene.id))
    return issues


def _fold(text: str) -> str:
    """Sem acento e com o mesmo tamanho ("Lúcia" → "Lucia"): os índices valem para o texto original."""
    return "".join(unicodedata.normalize("NFD", ch)[0] for ch in text or "")


def _name_re(name: str, *, article: bool = False) -> re.Pattern[str]:
    """O nome como palavra inteira, sem acento e sem caixa; `article` engole um artigo antes ("the Vice City")."""
    head = r"(?:(?:the|a|an)\s+)?" if article else ""
    return re.compile(rf"(?<![\w-]){head}{re.escape(_fold(name))}(?!\w)", re.IGNORECASE)


def _sub_folded(text: str, pattern: re.Pattern[str], replace: Any) -> str:
    """`pattern.sub` procurando no texto sem acento e trocando no original (mesmos índices)."""
    out: list[str] = []
    last = 0
    for match in pattern.finditer(_fold(text)):
        out += [text[last : match.start()], replace(text, match)]
        last = match.end()
    return "".join(out) + text[last:]


def _split_entity_name(name: str) -> tuple[str, str]:
    """"Lucia (original heroine)" → ("Lucia", "heroine"): o nome e o papel (depois de parêntese, travessão ou
    vírgula), sem enchimento."""
    parts = _NAME_SPLIT_RE.split((name or "").strip(), maxsplit=1)
    role = parts[1].strip(" ()") if len(parts) > 1 else ""
    role = " ".join(w for w in role.split() if w.lower() not in _ROLE_FILLER)
    return parts[0].strip(), role


def _spoken_as_name(name: str, narration: str) -> bool:
    """O nome aparece na narração sempre com maiúscula (nome próprio). "o detetive" em minúscula é palavra comum."""
    found = [narration[m.start() : m.end()] for m in _name_re(name).finditer(_fold(narration))]
    return bool(found) and all(w[:1].isupper() for f in found for w in f.split() if w.lower() not in _NAME_LINKS)


def _aliases(name: str, narration: str) -> list[str]:
    """Formas do nome a tirar dos prompts: o nome inteiro, quando a narração fala; senão, nome composto todo em
    maiúscula ("Lucia Caminos") cuja parte a narração fala ("Lucia")."""
    if len(name) >= 3 and _spoken_as_name(name, narration):
        return [name]
    words = [w for w in name.split() if w.lower() not in _NAME_LINKS]
    if len(words) < 2 or not all(w[:1].isupper() for w in words):
        return []
    parts = [w for w in words if len(w) >= 3 and _spoken_as_name(w, narration)]
    return [name, *parts] if parts else []


def _strip_names(text: str, names: list[str]) -> str:
    """Tira os nomes (com possessivo e a pontuação seguinte): "Lucia, a Latina woman" → "a Latina woman"."""
    for name in names:
        pattern = re.compile(rf"(?<![\w-]){re.escape(_fold(name))}(?:['’]s)?(?!\w)\s*[,:;-]?\s*", re.IGNORECASE)
        text = _sub_folded(text, pattern, lambda _t, _m: "")
    return text.strip(" ,;:-")


def entity_reference(look: str, role: str) -> str:
    """Como o prompt cita a entidade sem o nome: o começo da aparência que diz quem ou o que é ("the Latina woman",
    "the seaside city"); senão o papel; senão o começo da aparência ou "the character"."""
    heads: list[list[str]] = []
    for part in re.split(r"[,;.]", look or ""):
        head = _LOOK_CUT_RE.split(part, maxsplit=1)[0]
        words = [w for w in head.split() if w.lower() not in _ROLE_FILLER][:6]
        if words:
            heads.append(words)
    for words in heads:
        if _singular(words[-1].lower().strip("'’")) in _SUBJECT_NOUNS:
            return "the " + " ".join(words)
    if role:
        return f"the {role}"
    return "the " + " ".join(heads[0]) if heads else "the character"


def _replace_name(text: str, name: str, reference: str) -> str:
    """Troca o nome (palavra inteira, com o artigo antes e o possessivo depois) pela referência; maiúscula no
    começo da frase."""

    def sub(original: str, match: re.Match[str]) -> str:
        before = original[: match.start()].rstrip()
        sentence_start = not before or before[-1] in ".!?;:"
        return reference[:1].upper() + reference[1:] if sentence_start else reference

    return _sub_folded(text, _name_re(name, article=True), sub)


def fix_entity_names(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Nome de entidade de ficção, marca ou produto tirado da narração ("Lucia", "Vice City") não vai para o
    gerador de imagem: o nome puxa o personagem ou o lugar oficial (a imagem sai cópia ou é recusada). O prompt passa
    a citar a entidade pela aparência. Ficam: entidade real (figura histórica, lugar real), nome descritivo que a
    narração não fala ("Neon coastal city") e a direção definida pelo usuário (vira aviso).
    """
    art = doc.art
    if art is None or not art.entities:
        return []
    narration = doc.full_text(doc.primary_language)
    user = art.source == "user"
    names: list[tuple[str, str]] = []
    for entity in art.entities:
        if entity.real:
            continue
        name, role = _split_entity_name(entity.name)
        aliases = _aliases(name, narration)
        if not aliases:
            continue
        look = _strip_names(entity.look, aliases)
        if not user:
            entity.look = look
        reference = entity_reference(look, role)
        names += [(alias, reference) for alias in aliases]
    if not names:
        return []
    names.sort(key=lambda item: -len(item[0]))  # "Lucia Caminos" antes de "Lucia"

    def scrub(text: str) -> str:
        for alias, reference in names:
            text = _replace_name(text, alias, reference)
        return text

    def cited(text: str) -> str | None:
        return next((alias for alias, _ in names if _name_re(alias).search(_fold(text))), None)

    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or not has_own_image(scene):
            continue
        name = cited(scene.visual.prompt)
        if name is None:
            continue
        scene.visual.prompt = scrub(scene.visual.prompt)
        issues.append(Issue("nome_no_prompt", CORRIGIDO,
                            f'O prompt citava "{name}", nome da narração: a imagem descreve a aparência.',
                            beat.key, scene.id))
    if user:
        name = cited(" ".join([art.hook, art.world, *(e.look for e in art.entities)]))
        if name:
            issues.append(Issue("nome_na_direcao", AVISO,
                                f'A direção definida por você cita "{name}", nome da narração: o gerador de imagem pode '
                                "copiar o original."))
    else:
        art.hook = scrub(art.hook)
        art.world = scrub(art.world)
        for entity in art.entities:
            if not entity.real:
                entity.look = scrub(entity.look)
    return issues


# --- conferências --------------------------------------------------------------------------------


def _check_numbers(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    lang = doc.primary_language
    # Fora do português, o leitor só reconhece algarismos: número falado por extenso ("two hundred million")
    # não é conferido, então o número do cartão que não bate vira aviso (o cartão fica).
    full_check = lang.lower().startswith("pt")
    allowed = allowed_numbers(doc)
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None:
            continue
        runs = notable_number_runs(beat.words, lang)
        if runs and not _shows_number(scene):
            spoken = _spoken(beat, runs[0][0], runs[0][1])
            issues.append(Issue("numero_sem_cartao", GRAVE, f"A batida fala de {spoken} e não mostra o número.",
                                beat.key, scene.id))
        if scene.card:
            for text in card_numbers(scene.card):
                value = card_number(text)
                if value is None or _known_number(value, allowed):
                    continue
                if full_check:
                    issues.append(Issue("numero_inventado", GRAVE,
                                        f'O cartão mostra "{text}", que não está na narração nem nas fontes.',
                                        beat.key, scene.id, {"text": text}))
                else:
                    issues.append(Issue("numero_inventado", AVISO,
                                        f'O cartão mostra "{text}": confira se é o número da narração (neste idioma só '
                                        "os algarismos são conferidos).", beat.key, scene.id, {"text": text}))
    return issues


def _check_prompts(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    avoid = list(doc.art.avoid) if doc.art else []
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or not has_own_image(scene):
            continue
        prompt = scene.visual.prompt
        for term in find_terms(prompt, avoid):
            issues.append(Issue("motivo_proibido", GRAVE, f'O prompt usa "{term}", que a direção manda evitar.',
                                beat.key, scene.id))
        found = find_terms(prompt, STYLE_TERMS)
        if found:
            issues.append(Issue("estilo_no_prompt", GRAVE,
                                f'O prompt descreve estilo ("{found[0]}"): o app já acrescenta a linguagem visual.',
                                beat.key, scene.id))
        brands = find_terms(prompt, BRAND_TERMS)
        if brands:
            issues.append(Issue("marca", GRAVE,
                                f'O prompt pede "{brands[0]}": nada de logotipo, captura de tela, interface ou trailer.',
                                beat.key, scene.id))
    return issues


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _prompt_words(text: str) -> set[str]:
    out = set()
    for raw in re.findall(r"[A-Za-zÀ-ÿ]+", text.lower()):
        word = _singular(raw)
        if len(word) >= 4 and word not in _STOPWORDS:
            out.add(word)
    return out


def _check_motifs(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    prompts = [s.visual.prompt for b in beats if (s := scene_for(doc, b)) is not None and has_own_image(s)]
    if len(prompts) < 4:
        return []
    spoken = {_singular(normalize_token(w)) for w in split_words(doc.full_text(doc.primary_language))}
    skip: set[str] = set(_COLOR_WORDS)
    if doc.art:
        skip |= _prompt_words(" ".join([doc.art.world, doc.art.hook, doc.art.treatment, *doc.art.palette,
                                        *(f"{e.id} {e.name} {e.look}" for e in doc.art.entities)]))
    counts: dict[str, int] = {}
    for prompt in prompts:
        for word in _prompt_words(prompt):
            counts[word] = counts.get(word, 0) + 1
    limit = max(3, math.floor(len(prompts) * MOTIF_SHARE) + 1)
    repeated = sorted(
        ((n, w) for w, n in counts.items() if n >= limit and w not in spoken and w not in skip),
        key=lambda item: (-item[0], item[1]),
    )
    return [
        Issue("motivo_repetido", AVISO, f'O motivo "{word}" aparece em {n} de {len(prompts)} imagens e não está na narração.')
        for n, word in repeated[:3]
    ]


def _check_cards(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    ordered = doc.ordered_scenes()
    if not ordered:
        return []
    key_of = {scene.id: beat.key for beat in beats if (scene := scene_for(doc, beat)) is not None}
    issues: list[Issue] = []
    run = 0
    for position, scene in enumerate(ordered, 1):
        run = run + 1 if scene.kind in CARD_KINDS else 0
        if run == MAX_CARDS_IN_ROW + 1:
            issues.append(Issue("cartoes_seguidos", AVISO,
                                f"Mais de {MAX_CARDS_IN_ROW} cartões seguidos (até a cena {position}): o vídeo pode parecer "
                                "apresentação de slides.", key_of.get(scene.id), scene.id))
    cards = sum(1 for s in ordered if s.kind in CARD_KINDS)
    share = cards / len(ordered)
    if share > MAX_CARD_SHARE:
        issues.append(Issue("cartoes_demais", AVISO,
                            f"{cards} de {len(ordered)} cenas são cartões ({round(share * 100)}%); o ideal é até "
                            f"{round(MAX_CARD_SHARE * 100)}%."))
    return issues


def check(doc: ProjectDoc, style: StyleSpec, beats: list[Beat]) -> list[Issue]:
    """Revisa as cenas das batidas: corrige o que dá (no próprio `doc`) e devolve os problemas."""
    issues = fix_hook(doc, style, beats)
    issues += fix_entity_names(doc, beats)
    issues += fix_repeated_shots(doc, beats)
    issues += _check_numbers(doc, beats)
    issues += _check_prompts(doc, beats)
    issues += _check_motifs(doc, beats)
    issues += _check_cards(doc, beats)
    return issues
