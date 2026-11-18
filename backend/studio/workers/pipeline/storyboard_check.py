"""Revisão determinística do storyboard, antes de gastar imagens.

`check(doc, style, beats, budget)` confere as cenas planejadas para as batidas e devolve os problemas:
- grave: justifica pedir a correção ao Claude (número sem cartão, motivo proibido, estilo ou marca no
  prompt, número inventado no cartão, imagens novas quase todas na mesma hora do dia, imagem reaproveitada para
  outra data ou outro evento quando ainda há vaga para imagem nova);
- corrigido: o código já arrumou no documento (plano repetido, gancho sem imagem nova, fala em destaque sobre a
  imagem desfocada no gancho, cartão sem fundo, carimbo
  "INÉDITO" ou que é rótulo e não veredito ("IMPRENSA", "SINAL 2"), faixa que só repete a fala, fonte do cartão que não traz o número ou que ficou sozinha, personagem de
  ficção sem rosto);
- aviso: o usuário vê, nada muda (motivo repetido, cartões seguidos ou demais, comparação de medidas diferentes, a
  mesma imagem em cenas seguidas demais, chamada final de outro estilo).

As correções mudam o `doc` recebido. Nada aqui chama serviço externo.
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from studio.core import sources as tiers
from studio.core import verdict
from studio.core.hashing import seed_from
from studio.core.numbers import card_number, is_number_token, notable_number_runs, number_runs, text_numbers
from studio.core.project.document import (
    CARD_KINDS,
    ArtDirection,
    ProjectDoc,
    Scene,
    SceneCard,
    Source,
    Visual,
)
from studio.core.project.pool import Pool
from studio.core.styles.schema import StyleSpec, sources_for
from studio.core.styles.visual import (
    FOCUS_ROTATION,
    SHOT_ROTATION,
    SHOTS,
    compositions_for_caption,
    house_language,
)
from studio.core.text import estimate_seconds, normalize_token, span_ranges
from studio.core.text import words as split_words
from studio.core.timeline import false_verdict_on_true_material
from studio.workers.pipeline import lighting
from studio.workers.pipeline.build import lean_prompts, media_pending, scene_needs_image, wants_world

GRAVE = "grave"
AVISO = "aviso"
CORRIGIDO = "corrigido"
_SEVERITY_ORDER = {GRAVE: 0, AVISO: 1, CORRIGIDO: 2}

# Cota estimada do Codex por imagem nova (segundos).
SECONDS_PER_IMAGE = 35
MAX_CARDS_IN_ROW = 2
MAX_CARD_SHARE = 0.45
MOTIF_SHARE = 0.30
# Imagens novas no mesmo momento do dia (pôr do sol, noite...): acima disso o vídeo vira um papel de parede só.
LIGHT_SHARE = 0.40
# A mesma imagem (em tela cheia ou de fundo dos cartões) em cenas seguidas: acima disso o vídeo parece parado.
MAX_SAME_IMAGE_SCENES = 3
MAX_SAME_IMAGE_SECONDS = 8.0
"""Teto de tempo na mesma imagem no short; no vídeo longo, com batidas de 4 a 7 s, vale 3 batidas longas
(ver `same_image_seconds`)."""


def same_image_seconds(style: StyleSpec) -> float:
    """Tempo máximo na mesma imagem (cartões sobre ela contam): 8 s no short; no longo, 3 batidas do tamanho máximo
    de plano do estilo, senão uma imagem com um cartão já passaria do limite."""
    if style.format.type == "short":
        return MAX_SAME_IMAGE_SECONDS
    return max(MAX_SAME_IMAGE_SECONDS, MAX_SAME_IMAGE_SCENES * style.edit.shot_seconds.max)

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
    "logo", "logotype", "wordmark", "brand mark", "title logo", "game title", "box art", "cover art",
    "game cover", "official art", "official artwork", "key art", "promo art", "promotional art",
    "screenshot", "gameplay", "leaked", "hud", "game trailer", "official trailer", "teaser trailer",
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
# Entidade que é gente (tem rosto): o personagem de ficção citado pelo nome aparece de costas ou em silhueta.
_PERSON_NOUNS = {
    "woman", "women", "man", "men", "girl", "boy", "person", "teen", "teenager", "child", "kid", "guy", "lady",
    "gentleman", "soldier", "officer", "detective", "agent", "driver", "worker", "figure", "hero", "heroine",
    "villain", "king", "queen", "captain", "sailor", "protagonist", "character", "robot", "android", "criminal",
    "gangster", "thief", "outlaw", "cop", "warrior", "knight", "wizard", "prince", "princess", "assassin", "hunter",
    "survivor", "explorer", "pilot",
}  # fmt: skip
_FACELESS_RE = re.compile(
    r"\b(?:from behind|back view|from the back|silhouett\w*|faceless|face (?:hidden|not visible|unseen|out of frame)|"
    r"no face|without (?:a )?face)\b", re.IGNORECASE)
FACELESS = "seen from behind or in silhouette, face not visible"
# Carimbo que vende a imagem ilustrativa como material novo ou vazado.
_BANNED_STAMPS = {
    "inedito", "inedita", "ineditos", "ineditas", "exclusivo", "exclusiva", "exclusivos", "exclusivas", "exclusive",
    "unseen", "leaked", "leak", "vazou", "vazado", "vazada", "vazamento", "filtrado", "filtrada",
}  # fmt: skip
# Carimbo é veredito sobre o que a cena afirma (CONFIRMADO, BOATO, FALSO, GOLPE). Tipo de fonte ("IMPRENSA") é rótulo:
# repete o que a captura e a fonte já mostram e só disputa espaço com o texto da tela.
_LABEL_STAMPS = {
    "imprensa", "noticia", "noticias", "materia", "materias", "reportagem", "jornal", "fonte", "fontes", "site",
    "press", "news", "source", "sources", "article", "report", "prensa", "fuente", "fuentes", "articulo",
}  # fmt: skip
# Numeração de lista ("SINAL 2", "PASSO 3"): o contador da tela e a fala já numeram; carimbo não é índice.
_LIST_STAMPS = {
    "sinal", "sinais", "passo", "passos", "dica", "dicas", "item", "itens", "etapa", "etapas", "parte", "step",
    "steps", "tip", "tips", "sign", "signs", "paso", "pasos", "senal", "senales", "consejo",
}  # fmt: skip
# Data de dia e mês no carimbo ("13/11", "10.11"): é crédito ou faixa, não veredito. Ano sozinho ("2013") fica: ele
# entra quando o número é dito (`stamp_from`).
_DAY_MONTH_RE = re.compile(r"\b\d{1,2}\s*[/.-]\s*\d{1,2}\b")
# Palavras de ligação que não contam ao comparar a faixa com a fala.
_FUNCTION_WORDS = {
    "de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "em", "no", "na", "nos", "nas", "um", "uma", "para",
    "pra", "por", "pelo", "pela", "com", "sem", "ao", "aos", "que", "se", "the", "an", "of", "and", "in", "on",
    "to", "for", "at", "by", "with", "el", "la", "los", "las", "y", "en", "del", "al", "con",
}  # fmt: skip
# Escala logo depois do número na faixa ("200 MI", "1,5 BI").
_SCALE_TOKENS = {
    "mi", "bi", "tri", "mil", "k", "m", "b", "mn", "bn", "milhao", "milhoes", "bilhao", "bilhoes", "trilhao",
    "trilhoes", "million", "millions", "billion", "billions", "millon", "millones",
}  # fmt: skip
_MONTHS = {
    "janeiro": 1, "fevereiro": 2, "marco": 3, "abril": 4, "maio": 5, "junho": 6, "julho": 7, "agosto": 8,
    "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12, "january": 1, "february": 2, "march": 3,
    "april": 4, "may": 5, "june": 6, "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12, "enero": 1, "febrero": 2, "marzo": 3, "mayo": 5, "junio": 6, "julio": 7, "septiembre": 9,
    "octubre": 10, "noviembre": 11, "diciembre": 12,
}  # fmt: skip
# Nome de mês que também é palavra comum ("um marco", "it may", "march on"): só conta perto de um número.
_AMBIGUOUS_MONTHS = {"marco", "may", "march", "august"}
_CURRENCY_RE = re.compile(r"R\$|US\$|U\$|€|£|¥|\$")


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


def has_real(pool: Pool | None) -> bool:
    """O acervo do projeto tem material real (o storyboard põe o real primeiro)."""
    return pool is not None and any(tiers.is_real(item.tier) for item in pool.items)


def screen_seconds(doc: ProjectDoc, style: StyleSpec, beats: list[Beat] | None = None) -> list[tuple[Scene, str, float]]:
    """Cada cena na ordem do vídeo, com o nível do que ela mostra (`sources.prov_of_scene`) e o tempo de tela
    estimado pela fala (a batida ou, na cena travada, as palavras dela na velocidade do estilo)."""
    lang = doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150)
    by_scene = {scene.id: beat for beat in beats or [] if (scene := scene_for(doc, beat)) is not None}
    out: list[tuple[Scene, str, float]] = []
    for scene in doc.ordered_scenes():
        beat = by_scene.get(scene.id)
        seconds = beat.seconds if beat is not None else estimate_seconds(
            " ".join(_scene_words(doc, scene, by_scene)), wpm, style.voice.speed)
        out.append((scene, tiers.prov_of_scene(scene, doc), seconds))
    return out


def review_counts(doc: ProjectDoc, budget: int, *, style: StyleSpec | None = None,
                  pool: Pool | None = None) -> dict[str, int]:
    """Contagens do documento final (inclui as cenas dos segmentos travados). `real_hints` (sugestões de mídia real
    ainda sem mídia) só aparece quando há alguma; `ai_pct` e `real_pct` (parte do tempo de tela com IA e com material
    real), só com acervo de material real. A mídia do acervo ainda pendente de anexo (`media_pending`, só quando há)
    não conta como real: a cena mostra a reserva até o arquivo chegar."""
    new_images = sum(1 for s in doc.scenes if scene_needs_image(s) and not s.visual.asset)
    counts = {
        "scenes": len(doc.scenes),
        "new_images": new_images,
        "reused": sum(1 for s in doc.scenes if s.visual.reuse_of),
        "cards": sum(1 for s in doc.scenes if s.kind in CARD_KINDS),
        "budget": budget,
        "est_seconds": new_images * SECONDS_PER_IMAGE,
    }
    real_hints = sum(1 for s in doc.scenes if s.real is not None and s.visual.media is None)
    if real_hints:
        counts["real_hints"] = real_hints
    pending = sum(1 for s in doc.scenes if media_pending(s.visual.media))
    if pending:
        counts["media_pending"] = pending
    if style is not None and has_real(pool):
        timed = screen_seconds(doc, style)
        total = sum(seconds for _s, _p, seconds in timed) or 1.0
        counts["ai_pct"] = round(100 * sum(sec * tiers.ai_weight(scene, doc) for scene, _p, sec in timed) / total)
        counts["real_pct"] = round(100 * sum(sec for scene, prov, sec in timed
                                             if tiers.is_real(prov) and not media_pending(doc.shown_media(scene)))
                                   / total)
    return counts


def pending_media_issues(doc: ProjectDoc) -> list[Issue]:
    """Um aviso por cena cuja mídia do acervo ficou pendente depois do anexo (rede, item sem download): o vídeo mostra
    a imagem de IA de reserva ou, sem ela, o fundo animado, e o usuário precisa saber."""
    issues: list[Issue] = []
    for position, scene in enumerate(doc.ordered_scenes(), 1):
        if not media_pending(scene.visual.media):
            continue
        shows = ("o vídeo usa a imagem de IA de reserva" if scene_needs_image(
            scene.model_copy(update={"visual": scene.visual.model_copy(update={"media": None})}))
                 else "a cena fica com o fundo animado")
        issues.append(Issue("midia_pendente", AVISO, f"A mídia do acervo da cena {position} ainda não foi baixada: "
                                                     f"{shows}. Anexe a mídia na cena ou refaça as cenas.",
                            scene_id=scene.id))
    return issues


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


def _numbers_in(text: str, lang: str) -> list[float]:
    """Números do texto (inclusive "GTA 6" e, em português, "meio milhão")."""
    values = [float(v) for v in text_numbers(text, lang, names=True)]
    if lang.lower().startswith("pt"):
        values += _half_scales(text)
    return values


def allowed_numbers(doc: ProjectDoc) -> list[float]:
    """Números que um cartão pode mostrar: os da narração (inclusive "GTA 6") e os das fontes.

    O 1 sempre vale ("#1", "1º", "1 DIA" para "o primeiro", "em um dia"): "um" sozinho é artigo para o leitor.
    """
    lang = doc.primary_language
    values = [1.0, *_numbers_in(doc.full_text(lang), lang)]
    for source in doc.sources:
        values += _numbers_in(f"{source.title}. {source.note}", lang)
    return values


def source_label(source: Source) -> str:
    """Nome curto da fonte para o cartão (`core.sources.source_label`)."""
    return tiers.source_label(source)


def card_numbers(card: SceneCard) -> list[str]:
    """Textos de valor do cartão (value e items[].value) em todos os idiomas: o que a revisão confere."""
    out = [*card.value.values()]
    for item in card.items:
        out += [*item.value.values()]
    return [t.strip() for t in out if t and t.strip()]


# --- correções automáticas ---------------------------------------------------------------------


def fix_hook(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], pool: Pool | None = None) -> list[Issue]:
    """A primeira cena do vídeo precisa de imagem (o gancho): cartão vira imagem nova de IA com `art.hook`. Só sem
    acervo de material real (ADR 0004): com ele, o storyboard já pôs no gancho o melhor item real, e o que sobrar a
    revisão acusa (`hook_not_real`) em vez de virar IA."""
    if not beats or not is_hook_beat(doc, beats[0]) or has_real(pool):
        return []
    beat = beats[0]
    scene = scene_for(doc, beat)
    if scene is None or scene.locked or scene.kind == "count" or (
            scene.kind == "image" and (has_own_image(scene) or scene.visual.media is not None)):
        return []  # o contador do Top 10 fica como é; a mídia real anexada (mantida ao refazer) também
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
    scene.visual.language = house_language(languages)  # o mesmo estilo das outras imagens de IA
    scene.visual.shot = scene.visual.shot or "wide"
    safe = compositions_for_caption(style.edit.captions.position)
    if scene.visual.composition not in safe:
        scene.visual.composition = safe[0]
    scene.visual.entities = []
    scene.visual.seed = seed_from(doc.seed, beat.segment_id, beat.part)
    return [Issue("gancho_sem_imagem", CORRIGIDO, "A primeira cena virou imagem nova com o gancho da direção.",
                  beat.key, scene.id)]


def _hook_text_image(scene: Scene) -> bool:
    """A cena de fala em destaque (`text`) tem imagem atrás (que o renderer escurece e desfoca): mídia, asset fixo,
    reaproveitada ou o prompt da imagem de IA que o build ainda vai gerar (a mesma regra de `scene_needs_image`)."""
    visual = scene.visual
    if visual.media is not None or visual.asset or visual.reuse_of:
        return True
    return bool(visual.prompt) and visual.type not in ("none", "upload")


def _hook_text_to_image(doc: ProjectDoc, scenes: list[tuple[Scene, list[str], str | None]]) -> list[Issue]:
    lang = doc.primary_language
    out: list[Issue] = []
    for scene, words, key in scenes:
        if scene.locked or scene.kind != "text" or not _hook_text_image(scene):
            continue
        scene.kind = "image"
        if notable_number_runs(words, lang) and not _shows_number(scene):
            # a fala tem um número que só o cartão de texto mostrava: sem ele, o número sumiria da tela
            # (`numero_sem_cartao`); o cartão fica
            scene.kind = "text"
            continue
        out.append(Issue("gancho_texto_desfocado", CORRIGIDO, "No gancho, o cartão de texto sobre a imagem "
                         "desfocada virou a própria imagem (nítida, com a legenda).", key, scene.id))
    return out


def _hook_segment_ids(doc: ProjectDoc) -> set[str]:
    return {seg.id for seg in doc.segments if seg.role == "hook"} or {doc.segments[0].id}


def fix_hook_text(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """No gancho, a fala em destaque (`text`) sobre a imagem da cena vira a própria imagem: o cartão de texto
    escurece e desfoca o fundo, e no gancho isso dava segundos de tela escura só com tipografia, sem nada nítido para
    reconhecer (o que a thumb prometeu tem de aparecer). A legenda volta com a fala. Fica o cartão: sem imagem na
    cena (fundo liso do estilo), cena travada ou fala com um número que só o cartão mostra."""
    if not doc.segments:
        return []
    hook_ids = _hook_segment_ids(doc)
    scenes = [(scene, beat.words, beat.key) for beat in beats
              if beat.segment_id in hook_ids and (scene := scene_for(doc, beat)) is not None]
    return _hook_text_to_image(doc, scenes)


def fix_hook_text_doc(doc: ProjectDoc) -> list[Issue]:
    """`fix_hook_text` num documento já gravado (sem as batidas do storyboard: as palavras de cada cena saem da
    divisão da narração do timeline). É o que o "Reeditar" usa, para o projeto feito antes da regra."""
    if not doc.segments:
        return []
    from studio.core.timeline import scene_word_ranges  # import tardio: o timeline é pesado
    from studio.providers.base import Word

    lang = doc.primary_language
    scenes: list[tuple[Scene, list[str], str | None]] = []
    for seg_id in _hook_segment_ids(doc):
        seg = next(s for s in doc.segments if s.id == seg_id)
        tokens = split_words(seg.text_for(lang))
        seg_scenes = doc.scenes_of(seg_id)
        ranges = scene_word_ranges(seg_scenes, [Word(t, 0.0, 0.0) for t in tokens]) if seg_scenes else []
        for k, scene in enumerate(seg_scenes):
            a, b = ranges[k] if k < len(ranges) else (len(tokens), len(tokens))
            scenes.append((scene, tokens[a:b], f"{seg_id}:{k}"))
    return _hook_text_to_image(doc, scenes)


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


def _shows_own_image(scene: Scene) -> bool:
    """A cena mostra uma imagem dela (nova, fixada, enviada ou mídia real), não a de outra cena."""
    visual = scene.visual
    if visual.media is not None:
        return True  # o cartão pode usar a mídia real de fundo
    return not visual.reuse_of and visual.type != "none" and (bool(visual.asset) or scene_needs_image(scene))


def fix_flat_cards(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Cartão sobre o fundo animado (liso, parece slide) passa a usar de fundo, desfocada, a imagem mais próxima
    antes dele. Sem custo de cota. O contador do Top 10 fica como é (nunca mostra a imagem do item anterior) e o mapa
    também (ocupa a tela inteira)."""
    ordered = doc.ordered_scenes()
    keyed = {scene.id: beat for beat in beats if (scene := scene_for(doc, beat)) is not None}
    uses: dict[str, int] = {}
    for scene in ordered:
        if scene.visual.reuse_of:
            uses[scene.visual.reuse_of] = uses.get(scene.visual.reuse_of, 0) + 1
    issues: list[Issue] = []
    for index, scene in enumerate(ordered):
        beat = keyed.get(scene.id)
        visual = scene.visual
        if beat is None or scene.locked or scene.kind not in CARD_KINDS or scene.kind in ("count", "map"):
            continue
        if visual.type != "none" or visual.reuse_of or visual.asset or visual.media is not None:
            continue  # a mídia real anexada já é o fundo do cartão
        position, root = next(((k, s) for k, s in reversed(list(enumerate(ordered[:index]))) if _shows_own_image(s)),
                              (None, None))
        if root is None or position is None:
            continue
        uses[root.id] = uses.get(root.id, 0) + 1
        scene.visual = Visual(reuse_of=root.id)
        scene.focus = scene.focus or FOCUS_ROTATION[uses[root.id] % len(FOCUS_ROTATION)]
        if scene.motion == "static":
            scene.motion = None
        issues.append(Issue("cartao_sem_fundo", CORRIGIDO,
                            f"O cartão ficava sobre o fundo liso: agora usa de fundo a imagem da cena {position + 1}, "
                            "desfocada.", beat.key, scene.id))
    return issues


def fix_stamps(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Carimbo "INÉDITO", "EXCLUSIVO" ou "VAZOU" sai: sobre imagem ilustrativa de IA ele vende a ilustração como
    material oficial ou vazado. Só a foto enviada pelo usuário (ou a mídia real anexada) pode ter."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None:
            continue
        stamp = (scene.card.stamp.get(lang) or "").strip()
        if not stamp or not any(normalize_token(w) in _BANNED_STAMPS for w in stamp.split()):
            continue
        root = doc.reuse_root(scene)
        if root is not None and (root.visual.type == "upload" or root.visual.media is not None):
            continue
        scene.card.stamp = {}
        scene.card = scene.card if scene.card.resolved(lang) else None
        issues.append(Issue("carimbo_tirado", CORRIGIDO,
                            f'O carimbo "{stamp}" saiu: a imagem é uma ilustração, não material inédito.',
                            beat.key, scene.id))
    return issues


def _echoes(short: str, text: str) -> bool:
    """Todas as palavras de `short` (fora as de ligação) estão em `text`, número igual a número ("2013" não ecoa
    "2023")."""
    split = r"[\s·•|/—–:,.!?]+"
    have = {normalize_token(t) for t in re.split(split, text or "")} - {""}
    want = [n for t in re.split(split, short or "") if (n := normalize_token(t)) and n not in _FUNCTION_WORDS]
    return bool(want) and all(n in have for n in want)


def fix_echo_stamps(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Carimbo que só repete o texto grande da própria cena sai ("RECORDE" sobre o título "RECORDE NO YOUTUBE"): a
    mesma palavra duas vezes na tela não acrescenta nada."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None:
            continue
        stamp = (scene.card.stamp.get(lang) or "").strip()
        if not stamp:
            continue
        others = [scene.overlay_text.get(lang) or "", scene.card.headline.get(lang) or "",
                  scene.card.label.get(lang) or ""]
        echoed = next((t.strip() for t in others if t.strip() and _echoes(stamp, t)), None)
        if echoed is None:
            continue
        scene.card.stamp = {}
        scene.card = scene.card if scene.card.resolved(lang) else None
        issues.append(Issue("carimbo_repetido", CORRIGIDO, f'O carimbo "{stamp}" só repetia "{echoed}" na mesma cena: saiu.',
                            beat.key, scene.id))
    return issues


def _label_stamp(stamp: str, source: str | None) -> str | None:
    """Por que o carimbo é rótulo e não veredito (None: é veredito): tipo de fonte ("IMPRENSA"), o nome da própria
    fonte ou data de dia e mês ("ESET 13/11"), numeração de lista ("SINAL 2", "SINAIS 3·4")."""
    tokens = [t for t in (normalize_token(p) for p in re.split(r"[\s·•|/—–:,.!?()-]+", stamp)) if t]
    if not tokens:
        return None
    if all(t in _LABEL_STAMPS for t in tokens):
        return "tipo de fonte"
    if _DAY_MONTH_RE.search(stamp) or any(t in _MONTHS and t not in _AMBIGUOUS_MONTHS for t in tokens):
        return "data"
    # Só o nome da fonte ("GAMESBEAT", "ESET IMPRENSA"); "ROCKSTAR CONFIRMA" é veredito e fica.
    named = {n for p in re.split(r"[\s()·,]+", source or "") if len(n := normalize_token(p)) >= 3} - _FUNCTION_WORDS
    if named and all(t in named or t in _LABEL_STAMPS for t in tokens):
        return "nome da fonte"
    if tokens[0] in _LIST_STAMPS and len(tokens) > 1 and all(t.isdigit() for t in tokens[1:]):
        return "numeração de lista"
    return None


def fix_label_stamps(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Carimbo que é rótulo e não veredito sai ("IMPRENSA", "ESET 13/11", "SINAL 2"): o carimbo girado é para dizer o
    que vale a afirmação (CONFIRMADO, BOATO, FALSO). Rótulo repete a fonte, a faixa ou o contador e, a cada cena, ainda
    disputa a tela com a manchete da captura e o texto grande."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None:
            continue
        stamp = (scene.card.stamp.get(lang) or "").strip()
        why = _label_stamp(stamp, scene.card.source) if stamp else None
        if why is None:
            continue
        scene.card.stamp = {}
        scene.card = scene.card if scene.card.resolved(lang) else None
        issues.append(Issue("carimbo_rotulo", CORRIGIDO,
                            f'O carimbo "{stamp}" saiu: é {why}, não veredito (carimbo é para CONFIRMADO, BOATO, '
                            "FALSO).", beat.key, scene.id))
    return issues


def fix_verdict_stamps(doc: ProjectDoc, beats: list[Beat], pool: Pool | None = None) -> list[Issue]:
    """Veredito negativo (FALSO, GOLPE, FAKE) sobre material oficial ou de imprensa sai: sobre o quadro do trailer, a
    arte oficial ou a matéria, ele diz que o trailer, a arte ou a imprensa é que são falsos. Fica sobre o artefato do
    golpe (site falso, instalador, post de acesso pago), sobre ilustração e sobre o cartão de texto com a afirmação em
    fundo liso ou ilustração; o cartão com o trailer ou a matéria de fundo perde o carimbo (`core.verdict`)."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None:
            continue
        stamp = (scene.card.stamp.get(lang) or "").strip()
        if not stamp or not false_verdict_on_true_material(doc, scene, stamp, pool):
            continue
        scene.card.stamp = {}
        scene.card = scene.card if scene.card.resolved(lang) else None
        issues.append(Issue("carimbo_sobre_material_real", CORRIGIDO,
                            f'O carimbo "{stamp}" saiu: a cena mostra material oficial ou de imprensa, e o carimbo '
                            "diria que ele é que é falso. Veredito negativo só sobre o material do golpe ou sobre o "
                            "cartão com a afirmação.", beat.key, scene.id))
    return issues


def fix_ambiguous_stamps(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Veredito positivo ("CONFIRMADO") ao lado de frase negativa na tela ("PC: NÃO ANUNCIADO", "NÃO EXISTE") sai:
    a leitura rápida é contraditória (confirmado o quê?). O texto e a fonte do cartão já dizem quem afirma
    (`core.verdict.ambiguous_positive`)."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None:
            continue
        stamp = (scene.card.stamp.get(lang) or "").strip()
        texts = [scene.overlay_text.get(lang) or "", scene.card.headline.get(lang, ""), scene.card.value.get(lang, ""),
                 scene.card.label.get(lang, "")]
        if not stamp or not verdict.ambiguous_positive(stamp, texts, lang):
            continue
        scene.card.stamp = {}
        scene.card = scene.card if scene.card.resolved(lang) else None
        issues.append(Issue("carimbo_ambiguo", CORRIGIDO,
                            f'O carimbo "{stamp}" saiu: ao lado de uma frase negativa na tela ele lê como contradição '
                            "(confirmado o quê?). O texto e a fonte já dizem quem afirma.", beat.key, scene.id))
    return issues


def _other_digits(scene: Scene, lang: str) -> bool:
    """A cena mostra algum número além da faixa (contador, cartão, itens, carimbo ou sobreposição)."""
    if scene.number is not None:
        return True
    texts = [scene.overlay_text.get(lang) or ""]
    if scene.card:
        texts += [scene.card.value.get(lang, ""), scene.card.label.get(lang, ""), scene.card.stamp.get(lang, "")]
        for item in scene.card.items:
            texts += [item.value.get(lang, ""), item.label.get(lang, "")]
    return any(_has_digit(t) for t in texts)


def repeats_speech(text: str, words: list[str], lang: str) -> bool:
    """Todas as palavras do texto (fora as de ligação) já estão na fala: "DEZ 2023" para "Até dezembro de dois mil e
    vinte e três" repete; "TRAILER 1 · DEZ 2023" acrescenta. Abreviação conta ("DEZ" de "dezembro")."""
    spoken = {normalize_token(w) for w in words} - {""}
    numbers = _numbers_in(" ".join(words), lang)
    tokens = [t for t in re.split(r"[\s·•|/—–]+", text or "") if t]
    content = 0
    i = 0
    while i < len(tokens):
        token, norm = tokens[i], normalize_token(tokens[i])
        i += 1
        if not norm:
            continue
        if _has_digit(token):
            if i < len(tokens) and normalize_token(tokens[i]) in _SCALE_TOKENS:
                token, i = f"{token} {tokens[i]}", i + 1
            value = card_number(token, lang)
            if value is None or not _known_number(value, numbers):
                return False
        elif norm in _FUNCTION_WORDS:
            continue
        elif norm not in spoken and not (len(norm) >= 3 and any(w.startswith(norm) for w in spoken)):
            return False
        content += 1
    return content > 0


def fix_repeated_headlines(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Faixa no topo que só repete a fala da batida sai (a legenda já mostra): fica a do gancho e a que é o único
    lugar onde o número da batida aparece (sem ela a revisão acusaria número sem cartão)."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None or is_hook_beat(doc, beat):
            continue
        headline = (scene.card.headline.get(lang) or "").strip()
        if not headline or not repeats_speech(headline, beat.words, lang):
            continue
        if (scene.kind != "text" and _has_digit(headline) and notable_number_runs(beat.words, lang)
                and not _other_digits(scene, lang)):
            continue
        scene.card.headline = {}
        scene.card = scene.card if scene.card.resolved(lang) else None
        issues.append(Issue("faixa_repetida", CORRIGIDO, f'A faixa "{headline}" só repetia a fala da batida: saiu.',
                            beat.key, scene.id))
    return issues


# Cartões que mostram em destaque o texto da própria cena (o título, a citação, a fala, o contador).
_TEXT_KINDS = ("title", "count", "quote", "text", "document")


def _backs_scene_text(doc: ProjectDoc, scene: Scene, lang: str, source: str = "") -> bool:
    """A fonte apoia algo da própria cena fora do cartão (a mesma conta do renderer): o texto em destaque (título,
    citação, contador, a fala em tipografia) ou o cartão de notícia da mídia real. No título sem faixa nem carimbo, só
    quando ele diz algo da fonte ou o mesmo trecho já a credita (`title_keeps_source`): "2. TAMANHO QUE NÃO BATE" não
    é dado de ninguém. No cartão tipográfico (a fala do apresentador), só quando a frase nomeia a fonte ou traz um
    número dela (`scene_claims_source`): "cartão ou Pix, fala com o seu banco" não é dado da Malwarebytes."""
    title = (scene.overlay_text.get(lang) or "").strip()
    if scene.kind == "title" and title and source and not tiers.title_keeps_source(doc, scene, source):
        return False
    if scene.kind == "text" and source and not tiers.scene_claims_source(doc, scene, source):
        return False
    if scene.kind in _TEXT_KINDS and (title or scene.kind == "text"
                                      or (scene.kind == "count" and scene.number is not None)):
        return True
    media = scene.visual.media
    return (scene.kind == "image" and media is not None and media.kind == "news"
            and bool(media.outlet.strip() or media.headline_for(lang).strip()))


def fix_lone_sources(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Cartão que ficou só com a fonte sai, e a fonte com ele: sem número, faixa, carimbo nem texto da cena que ela
    apoie, o "Fonte: X" solto sobre a imagem parece crédito da imagem. Acontece quando outra correção tira a faixa ou o
    carimbo (`SceneCard.resolved` conta a fonte, então o cartão ficava). Cartão vazio sai sem aviso: não mostrava nada."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None:
            continue
        shown = scene.card.resolved(lang)
        source = shown.pop("source", "")
        if shown or (source and _backs_scene_text(doc, scene, lang, source)):
            continue
        scene.card = None
        if source:
            issues.append(Issue("fonte_solta", CORRIGIDO, f'A fonte "{source}" ficava sozinha na cena, sem número, faixa '
                                                          "nem texto que ela apoiasse: saiu.", beat.key, scene.id))
    return issues


# Algarismos de um texto de cartão ("87 GB+", "R$ 1,5 BI", "1.000"): o grupo que `fix_card_speech` reescreve.
_CARD_DIGITS_RE = re.compile(r"\d+(?:[.,]\d+)*")
# Índice de lista no começo do título ("2. TAMANHO..."): numera o vídeo, não é dado de fonte.
_LIST_INDEX_RE = re.compile(r"^\s*#?\d{1,2}\s*[.)ºª°:\-–—]\s*")


def _card_digits(value: float, lang: str) -> str:
    """O número no formato do cartão: "100", "1,1" e "1.000" em português; "1.1" e "1,000" em inglês."""
    en = lang.lower().startswith("en")
    if abs(value - round(value)) < 1e-9:
        text = f"{round(value):,}"
        return text if en else text.replace(",", ".")
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text if en else text.replace(".", ",")


def _run_of(beat: Beat, value: float, lang: str) -> tuple[int, int]:
    for a, b, v in notable_number_runs(beat.words, lang):
        if float(v) == value:
            return a, b
    return 0, 0


def fix_card_speech(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """O número do cartão de dado é o que a batida fala: o cartão entra quando o número é dito e não pode mostrar
    outro no mesmo quadro ("87 GB+" de uma fonte enquanto a fala e a legenda dizem "cem gigabytes").

    Com um só número que vale cartão na fala, perto do valor do cartão (metade ao dobro: o mesmo dado contado de outro
    jeito) e um só número no cartão, o cartão segue a fala, com a escala e a unidade dele ("87 GB+" → "100 GB+").
    Fora isso (vários números falados, ordem de grandeza diferente, cena travada), a cena é sinalizada para o ajuste
    à mão. Sem número dito, o cartão fica como está (é o dado que a fala não lê). Ordinal ("1º TRI 2027", "o primeiro
    trimestre") não entra: o leitor de números não lê ordinal falado."""
    lang = doc.primary_language
    allowed = allowed_numbers(doc)
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.kind != "stat" or scene.card is None:
            continue
        text = (scene.card.value.get(lang) or "").strip()
        value = card_number(text, lang) if text else None
        if not value or "º" in text or "ª" in text:
            continue
        spoken = [float(v) for _a, _b, v in number_runs(beat.words, lang)]
        if lang.lower().startswith("pt"):
            spoken += _half_scales(" ".join(beat.words))
        notable = sorted({float(v) for _a, _b, v in notable_number_runs(beat.words, lang)})
        if not notable:
            continue
        # O 1 vale sempre ("#1", "1 DIA" para "em um dia"), menos quando a fala tem o mesmo dado mais preciso
        # ("1 MB" com "um vírgula um megabyte").
        if not any(0.5 <= v <= 2 for v in notable):
            spoken.append(1.0)
        shown = [value, *(float(v) for v in text_numbers(text, lang, names=True))]
        if any(_known_number(v, spoken) for v in shown) or not _known_number(value, allowed):
            continue  # bate com a fala, ou é número inventado (`numero_inventado` troca o cartão)
        said = notable[0]
        digits = _CARD_DIGITS_RE.findall(text)
        base = card_number(digits[0], lang) if len(digits) == 1 else None
        new_base = said * base / abs(value) if base else 0.0
        if len(notable) == 1 and not scene.locked and 0.5 <= said / abs(value) <= 2 and new_base >= 0.01:
            for code, card_text in list(scene.card.value.items()):
                other = card_number(card_text, code)
                found = _CARD_DIGITS_RE.findall(card_text or "")
                if other is not None and len(found) == 1 and _known_number(other, [value]):
                    scene.card.value[code] = card_text.replace(found[0], _card_digits(new_base, code), 1)
            after = scene.card.value.get(lang, "")
            issues.append(Issue("cartao_segue_fala", CORRIGIDO,
                                f'O cartão mostrava "{text}" enquanto a fala diz {_spoken(beat, *_run_of(beat, said, lang))}'
                                f': agora mostra "{after}".', beat.key, scene.id, {"before": text, "after": after}))
            continue
        said_text = ", ".join(_spoken(beat, *_run_of(beat, v, lang)) for v in notable[:3])
        issues.append(Issue("cartao_contradiz_fala", GRAVE,
                            f'O cartão mostra "{text}" e a fala diz {said_text}: o cartão entra quando o número é dito, '
                            "então os dois têm de ser o mesmo número (ajuste o cartão ou a fala).",
                            beat.key, scene.id, {"text": text}))
    return issues


def fix_fact_sources(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """O mesmo fato tem a mesma fonte no vídeo inteiro: o número que a cena mostra (valor, itens, faixa, carimbo, o
    texto em destaque) e que só uma fonte do roteiro traz fica creditado a ela. "Fonte: Malwarebytes" num cartão da ISO
    de 100 GB (que está na nota da ESET, não na da Malwarebytes) vira "Fonte: WeLiveSecurity (ESET)", como nas outras
    cenas do mesmo dado. Número que a fonte citada também traz, ou que várias fontes trazem, fica como está."""
    lang = doc.primary_language
    held = [(s, _numbers_in(f"{s.title}. {s.note}", lang)) for s in doc.sources]
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.card is None or not scene.card.source:
            continue
        cited = scene.card.source
        mine = [nums for s, nums in held if tiers.cites_source(s, cited)]
        if not mine:
            continue  # fonte fora do roteiro: não dá para conferir
        card = scene.card
        texts = [card.value.get(lang), card.headline.get(lang), card.stamp.get(lang),
                 *(item.value.get(lang) for item in card.items)]
        if scene.kind in _TEXT_KINDS:
            texts.append(_LIST_INDEX_RE.sub("", scene.overlay_text.get(lang) or ""))
        values = {abs(float(v)) for t in texts if t for v in text_numbers(t, lang, names=True)}
        values -= {0.0, 1.0}
        if not values or any(_known_number(v, nums) for v in values for nums in mine):
            continue
        holders = {source_label(s) for s, nums in held
                   if not tiers.cites_source(s, cited) and any(_known_number(v, nums) for v in values)}
        holders.discard("")
        if len(holders) != 1:
            continue
        new = holders.pop()
        card.source = new
        issues.append(Issue("fonte_do_dado", CORRIGIDO, f'O dado da cena vem de "{new}", não de "{cited}": o crédito '
                                                       "agora é o mesmo das outras cenas com esse dado.",
                            beat.key, scene.id, {"before": cited, "after": new}))
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


def official_characters(art: ArtDirection | None, narration: str) -> dict[str, list[str]]:
    """Personagens oficiais de ficção da direção de arte: entidade de gente, inventada (não `real`), fora dos
    personagens do canal, que a narração cita pelo nome (protagonista de jogo, filme, série): `{id: nomes}`. Em
    história original (gênero narrativa) o personagem é do canal: nenhum."""
    if art is None or art.genre == "narrativa":
        return {}
    out: dict[str, list[str]] = {}
    for entity in art.entities:
        if entity.real or entity.character:
            continue
        name, role = _split_entity_name(entity.name)
        aliases = _aliases(name, narration)
        if aliases and _is_person(entity.look, role):
            out[entity.id] = aliases
    return out


def brand_request(prompt: str, entities: list[str], characters: dict[str, list[str]]) -> str | None:
    """O que o pedido de imagem de IA tem de marca ou de personagem oficial (logotipo, capa, arte oficial, captura
    de jogo, ou um personagem oficial pela entidade ou pelo nome): o termo ou o nome; None quando não tem. A IA nunca
    imita marca nem personagem oficial: a batida mostra o material oficial do acervo (ou vira cartão)."""
    brands = find_terms(prompt or "", BRAND_TERMS)
    if brands:
        return brands[0]
    for eid in entities:
        if eid in characters:
            return characters[eid][0]
    folded = _fold(prompt or "")
    for names in characters.values():
        for name in names:
            if _name_re(name).search(folded):
                return name
    return None


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
    faceless: list[str] = []
    for entity in art.entities:
        if entity.real or entity.character:
            continue  # personagem cadastrado: `characters.finish` já trocou o nome pela descrição
        name, role = _split_entity_name(entity.name)
        aliases = _aliases(name, narration)
        if not aliases:
            continue
        look = _strip_names(entity.look, aliases)
        if not user:
            entity.look = look
        reference = entity_reference(look, role)
        names += [(alias, reference) for alias in aliases]
        # Personagem oficial de ficção citado pelo nome (protagonista de jogo, filme, série) não aparece de frente:
        # a imagem de rosto parece arte oficial ou vazada. Em história original (narrativa) o personagem é do canal.
        if not user and art.genre != "narrativa" and _is_person(look, role) and not _FACELESS_RE.search(look):
            entity.look = f"{look.rstrip(' .,;')}, {FACELESS}" if look.strip() else FACELESS
            faceless.append(name)
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
            if not entity.real and not entity.character:
                entity.look = scrub(entity.look)
    for name in faceless:
        issues.append(Issue("personagem_sem_rosto", CORRIGIDO,
                            f'"{name}" é personagem de ficção citado pelo nome: aparece de costas ou em silhueta, '
                            "sem rosto."))
    return issues


def _is_person(look: str, role: str) -> bool:
    """A entidade é gente: quem a aparência diz que é ("Latina woman", "the detective"), o papel ou, sem nenhum dos
    dois, cabelo, barba, olhos ou rosto na descrição."""
    for part in re.split(r"[,;.]", look or ""):
        head = _LOOK_CUT_RE.split(part, maxsplit=1)[0]
        words = [w.lower().strip("'’") for w in head.split()]
        if words and _singular(words[-1]) in _SUBJECT_NOUNS | _PERSON_NOUNS:
            return _singular(words[-1]) in _PERSON_NOUNS
    if role and _singular(role.split()[-1].lower()) in _PERSON_NOUNS:
        return True
    return bool(re.search(r"\b(?:hair|beard|eyes|skin|face)\b", look or "", re.IGNORECASE))


def _with_faceless(prompt: str) -> str:
    """O prompt com `FACELESS` no fim (antes do ponto final, se houver)."""
    text = prompt.rstrip()
    end = "." if text.endswith(".") else ""
    return f"{text.rstrip(' .,;')}, {FACELESS}{end}" if text.strip(" .") else FACELESS


# Gente na imagem de IA além dos substantivos de entidade: quem a cena de serviço costuma mostrar.
_SCENE_PEOPLE = _PERSON_NOUNS | {
    "gamer", "player", "user", "people", "family", "couple", "crowd", "friend", "student", "teen", "parent",
    "mother", "father", "son", "daughter", "victim", "customer", "employee", "streamer", "youtuber",
}  # fmt: skip
_BACK_VIEW_RE = re.compile(r"\bover[- ]the[- ]shoulder\b|\bseen from behind\b|\brear view\b", re.IGNORECASE)


def ai_face(doc: ProjectDoc, scene: Scene) -> bool:
    """A cena mostra imagem de IA com gente de frente (rosto): o prompt da imagem que ela mostra (a dela ou a da cena
    que ela reaproveita) cita pessoa ou personagem de entidade, sem pedir costas, silhueta ou ombro. O avatar do
    apresentador ao lado de um rosto de IA vira dois personagens disputando o quadro, e o de IA denuncia o gerador
    (QA do prj_gq4krrs11w, 4:24: o avatar ao lado do garoto anime)."""
    if tiers.prov_of_scene(scene, doc) != "ai":
        return False
    owner = doc.reuse_root(scene) if scene.visual.reuse_of else scene
    visual = (owner or scene).visual
    prompt = visual.prompt or ""
    if not prompt.strip() or visual.shot == "over_shoulder" or _FACELESS_RE.search(prompt) or _BACK_VIEW_RE.search(prompt):
        return False
    art = doc.art
    entities = [e for e in (art.entity(i) for i in visual.entities) if e] if art else []
    if any(_is_person(e.look, _split_entity_name(e.name)[1]) and not _FACELESS_RE.search(e.look) for e in entities):
        return True
    return any(_singular(w) in _SCENE_PEOPLE for w in re.findall(r"[a-z]+", prompt.lower()))


def fix_real_reserve(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Batida que sugere material oficial (personagem, arte, foto de imprensa): a imagem de IA de reserva não mostra
    o rosto da pessoa (pareceria arte oficial ou vazada). O rosto real só entra pela mídia que o usuário anexar."""
    art = doc.art
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.real is None or scene.real.kind != "official":
            continue
        if not has_own_image(scene):
            continue
        prompt = scene.visual.prompt
        entities = [e for e in (art.entity(i) for i in scene.visual.entities) if e] if art else []
        people = [e for e in entities if _is_person(e.look, _split_entity_name(e.name)[1])]
        named = any(_singular(w) in _PERSON_NOUNS for w in re.findall(r"[a-z]+", prompt.lower()))
        if not (named or people):
            continue
        if _FACELESS_RE.search(prompt) or any(_FACELESS_RE.search(e.look) for e in people):
            continue
        scene.visual.prompt = _with_faceless(prompt)
        issues.append(Issue("reserva_sem_rosto", CORRIGIDO,
                            "A imagem de reserva mostra a pessoa de costas ou em silhueta: o rosto real só entra pela "
                            "mídia que você anexar.", beat.key, scene.id))
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
                value = card_number(text, lang)
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


# Aparelho que a fala cita e o que a imagem de IA põe em primeiro lugar (sem acento, minúsculo). "Tela" e "monitor"
# servem a qualquer um: não contam.
DEVICE_CLASSES: dict[str, frozenset[str]] = {
    "pc": frozenset({"pc", "pcs", "computer", "computers", "computador", "computadores", "desktop", "desktops",
                     "laptop", "laptops", "notebook", "notebooks"}),
    "console": frozenset({"console", "consoles", "playstation", "ps5", "ps4", "xbox", "nintendo"}),
    "celular": frozenset({"phone", "phones", "smartphone", "smartphones", "cellphone", "celular", "celulares",
                          "iphone", "telefone"}),
    "tv": frozenset({"tv", "tvs", "television", "televisao", "televisor", "televisores"}),
}  # fmt: skip
_DEVICE_OF = {word: name for name, words in DEVICE_CLASSES.items() for word in words}


def _device_words(text: str) -> list[str]:
    """As classes de aparelho do texto, na ordem em que aparecem."""
    return [_DEVICE_OF[w] for w in re.findall(r"[a-z0-9]+", _fold(text).lower()) if w in _DEVICE_OF]


def device_mismatch(speech: str, prompt: str) -> tuple[str, list[str]] | None:
    """A imagem de IA abre com um aparelho que a fala não cita, e a fala cita outro: "O jogo roda no seu console" com
    o prompt "a gamer at a desktop PC" (QA do prj_gq4krrs11w, 2:21, o PC gamer ilustrava o console). Devolve (o
    aparelho do prompt, os da fala) ou None."""
    cited = list(dict.fromkeys(_device_words(speech)))
    shown = _device_words(prompt)
    if not cited or not shown or shown[0] in cited:
        return None
    return shown[0], cited


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
        mismatch = device_mismatch(beat.text, prompt)
        if mismatch:
            shown, cited = mismatch
            issues.append(Issue("aparelho_trocado", GRAVE,
                                f'A fala cita {" e ".join(cited)} e a imagem abre com {shown}: o sujeito da imagem é o '
                                "aparelho que a fala diz.", beat.key, scene.id))
        brands = find_terms(prompt, BRAND_TERMS)
        if brands:
            issues.append(Issue("marca", GRAVE,
                                f'O prompt pede "{brands[0]}": nada de logotipo, captura de tela, interface ou trailer.',
                                beat.key, scene.id))
    return issues


def _singular(word: str) -> str:
    """Singular simples em inglês: "cities" → "city", "beaches" → "beach", "glasses" → "glass", "palms" → "palm"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes", "zzes")):
        return word[:-2]
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
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
    # Só cor e o nome das entidades ficam de fora: palavra do mundo, do tratamento ou da aparência repetida em quase
    # toda imagem ("sunset", "beach") é o motivo repetido que deixa o vídeo engessado.
    skip: set[str] = set(_COLOR_WORDS)
    if doc.art:
        skip |= _prompt_words(" ".join(f"{e.id.replace('_', ' ')} {e.name}" for e in doc.art.entities))
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


def _direction_light_text(doc: ProjectDoc, scene: Scene) -> str:
    """O que a direção acrescenta ao prompt da imagem e pode fixar a hora: tratamento, mundo, aparências e paleta."""
    art = doc.art
    if art is None:
        return ""
    if lean_prompts(doc) and not wants_world(art, scene.visual):
        return art.treatment  # storyboard novo, sem entidade (nem direção do usuário): só o tratamento entra
    looks = [e.look for e in (art.entity(i) for i in scene.visual.entities) if e]
    return ". ".join(t for t in (art.treatment, art.world, *looks, *art.palette) if t)


def _check_lighting(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Imagens novas demais no mesmo momento do dia (todas no pôr do sol, todas à noite) deixam o vídeo com uma cara
    só. Conta o momento que vai para o gerador: o do prompt da cena ou, quando ele não diz luz nenhuma, o da direção
    (a mesma regra de `compose_directed_prompt`)."""
    scenes = [s for b in beats if (s := scene_for(doc, b)) is not None and has_own_image(s)]
    if len(scenes) < 4:
        return []
    user = doc.art is not None and doc.art.source == "user"
    counts: dict[str, int] = {}
    fixed: set[str] = set()  # momentos que a direção definida pelo usuário impõe (o Claude não pode mudar)
    for scene in scenes:
        text = scene.visual.prompt
        if doc.art is not None and (user or not lighting.names_light(text)):
            extra = _direction_light_text(doc, scene)
            text = f"{text}. {extra}"
            if user:
                fixed |= lighting.moments(extra)
        for moment in lighting.moments(text):
            counts[moment] = counts.get(moment, 0) + 1
    issues: list[Issue] = []
    for moment, n in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if n / len(scenes) <= LIGHT_SHARE:
            continue
        where = f"{n} de {len(scenes)} imagens novas se passam no mesmo momento ({lighting.moment_label(moment)})"
        if moment in fixed:
            issues.append(Issue("luz_repetida", AVISO, f"{where}: a direção definida por você fixa esse momento."))
        else:
            issues.append(Issue("luz_repetida", GRAVE, f"{where}: cada imagem diz a sua hora e luz, no máximo 1 em "
                                                       "cada 3 no mesmo momento, seguindo o clima da narração."))
    return issues


def _dates(words: list[str], lang: str) -> tuple[set[int], set[int]]:
    """Anos (1900 a 2100) e meses que a fala cita."""
    years = {int(v) for v in text_numbers(" ".join(words), lang) if float(v).is_integer() and 1900 <= v <= 2100}
    months: set[int] = set()
    for index, word in enumerate(words):
        token = normalize_token(word)
        month = _MONTHS.get(token)
        if month is None:
            continue
        near = words[index + 1 : index + 3]
        if token in _AMBIGUOUS_MONTHS and not any(_has_digit(w) or is_number_token(w, lang) for w in near):
            continue
        months.add(month)
    return years, months


def _scene_words(doc: ProjectDoc, scene: Scene, by_scene: dict[str, Beat]) -> list[str]:
    """As palavras da narração que a cena cobre (pela batida ou, em cena travada, pelos spans do segmento)."""
    beat = by_scene.get(scene.id)
    if beat is not None:
        return beat.words
    try:
        segment = doc.segment(scene.segment_id)
    except KeyError:
        return []
    tokens = split_words(segment.text_for(doc.primary_language))
    scenes = doc.scenes_of(segment.id)
    if any(s.span is None for s in scenes):
        return []
    for other, (a, b) in zip(scenes, span_ranges([s.span or 0 for s in scenes], len(tokens)), strict=True):
        if other.id == scene.id:
            return tokens[a:b]
    return []


def _quote(words: list[str], limit: int = 6) -> str:
    text = " ".join(words[:limit]).strip(" .,;:!?…")
    return text + ("…" if len(words) > limit else "")


def _headline_of(scene: Scene, lang: str) -> str:
    return (scene.card.headline.get(lang) or "").strip() if scene.card else ""


def _headline_topic(headline: str) -> tuple[set[str], set[str]]:
    """O assunto da faixa: as palavras (sem número, escala nem ligação) e os anos. "475 MI" não tem assunto (é só o
    número da batida); "TRAILER 2 · MAI 2025" tem {trailer, mai} e {2025}."""
    words: set[str] = set()
    years: set[str] = set()
    for token in re.split(r"[\s·•|/—–]+", headline or ""):
        norm = normalize_token(token)
        if not norm:
            continue
        if _has_digit(norm):
            if re.fullmatch(r"(?:19|20)\d\d", norm):
                years.add(norm)
        elif norm not in _FUNCTION_WORDS and norm not in _SCALE_TOKENS:
            words.add(norm)
    return words, years


def _other_topic(headline: str, root_headline: str) -> bool:
    """A faixa da cena anuncia outro assunto que a da cena dona da imagem (outra data, outro nome)."""
    if not headline or not root_headline:
        return False
    words, years = _headline_topic(headline)
    root_words, root_years = _headline_topic(root_headline)

    def known(word: str) -> bool:  # abreviação conta ("MAI" e "MAIO")
        return any(word == r or (min(len(word), len(r)) >= 3 and (r.startswith(word) or word.startswith(r)))
                   for r in root_words)

    return not all(known(w) for w in words) or bool(years and root_years and years != root_years)


def _check_reuse(doc: ProjectDoc, beats: list[Beat], budget: int | None) -> list[Issue]:
    """Imagem em tela cheia reaproveitada numa batida com outra data ou outra faixa que a da cena dona da imagem
    (dezembro de 2023 e maio de 2025 com a mesma multidão): o público lê como clipe repetido. Grave enquanto sobra
    vaga para imagem nova (a correção troca por imagem nova); senão, aviso. Fundo desfocado de cartão pode."""
    lang = doc.primary_language
    by_scene = {scene.id: beat for beat in beats if (scene := scene_for(doc, beat)) is not None}
    has_slot = budget is not None and review_counts(doc, budget)["new_images"] < budget
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or scene.kind != "image" or not scene.visual.reuse_of or beat.role == "cta":
            continue
        root = doc.reuse_root(scene)
        if root is None or root.id == scene.id:
            continue
        root_words = _scene_words(doc, root, by_scene)
        years, months = _dates(beat.words, lang)
        root_years, root_months = _dates(root_words, lang)
        other_date = bool(years and root_years and not years <= root_years) or bool(
            months and root_months and not months <= root_months)
        other_headline = _other_topic(_headline_of(scene, lang), _headline_of(root, lang))
        if not (other_date or other_headline):
            continue
        where = (f'A batida "{_quote(beat.words)}" reaproveita a imagem de "{_quote(root_words)}"' if root_words
                 else f'A batida "{_quote(beat.words)}" reaproveita a imagem de outra cena')
        if has_slot:
            issues.append(Issue("reuso_outra_data", GRAVE, f"{where}: é outra data ou outro evento, use imagem nova.",
                                beat.key, scene.id))
        else:
            issues.append(Issue("reuso_outra_data", AVISO, f"{where}: é outra data ou outro evento, e não sobra vaga "
                                                           "para imagem nova.", beat.key, scene.id))
    return issues


def _image_of(doc: ProjectDoc, scene: Scene) -> str | None:
    """A imagem que a cena mostra (em tela cheia ou de fundo do cartão): a mídia real, a imagem fixada ou a da cena dona
    da imagem. None: fundo animado."""
    media = doc.shown_media(scene)
    if media is not None and (media.asset or media.url):
        return f"media:{media.asset or media.url}"
    if scene.visual.asset:
        return f"asset:{scene.visual.asset}"
    root = doc.reuse_root(scene)
    if root is None or root.visual.type == "none":
        return None
    return f"asset:{root.visual.asset}" if root.visual.asset else f"scene:{root.id}"


def _check_same_image(doc: ProjectDoc, style: StyleSpec, beats: list[Beat]) -> list[Issue]:
    """A mesma imagem em cenas seguidas demais (mais de `MAX_SAME_IMAGE_SCENES` cenas ou mais de
    `MAX_SAME_IMAGE_SECONDS` de narração estimada, contando os cartões sobre ela): o vídeo parece parado. Aviso, sem
    pedir correção (trocar por imagem nova gasta cota). Sequência só de cenas travadas fica como o usuário deixou."""
    lang = doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150)
    by_scene = {scene.id: beat for beat in beats if (scene := scene_for(doc, beat)) is not None}
    runs: list[list[tuple[int, Scene, float]]] = []
    last: str | None = None
    for position, scene in enumerate(doc.ordered_scenes(), 1):
        image = _image_of(doc, scene)
        beat = by_scene.get(scene.id)
        seconds = beat.seconds if beat is not None else estimate_seconds(
            " ".join(_scene_words(doc, scene, by_scene)), wpm, style.voice.speed)
        if image is not None and image == last:
            runs[-1].append((position, scene, seconds))
        else:
            runs.append([(position, scene, seconds)])
        last = image
    limit = same_image_seconds(style)
    issues: list[Issue] = []
    for run in runs:
        total = sum(seconds for _p, _s, seconds in run)
        if len(run) < 2 or (len(run) <= MAX_SAME_IMAGE_SCENES and total <= limit):
            continue
        if not any(scene.id in by_scene for _p, scene, _s in run):
            continue
        # A cena em que a sequência passa do limite (a que pede outra imagem).
        acc, at = 0.0, run[-1][1]
        for count, (_p, scene, seconds) in enumerate(run, 1):
            acc += seconds
            if count > MAX_SAME_IMAGE_SCENES or acc > limit:
                at = scene
                break
        beat = by_scene.get(at.id)
        shown = f"{total:.1f}".replace(".", ",")
        where = f"cenas {run[0][0]} a {run[-1][0]}"
        if len(run) > MAX_SAME_IMAGE_SCENES:
            message = (f"A mesma imagem fica em {len(run)} cenas seguidas ({where}, ~{shown} s; o limite é "
                       f"{MAX_SAME_IMAGE_SCENES}): alterne com imagem nova ou mídia real.")
        else:
            message = (f"A mesma imagem fica ~{shown} s na tela, em {len(run)} cenas seguidas ({where}; o limite é "
                       f"{limit:.0f} s): alterne com imagem nova ou mídia real.")
        issues.append(Issue("imagem_seguida", AVISO, message, beat.key if beat is not None else None, at.id))
    return issues


def _check_sources(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """A fonte do cartão precisa sustentar o número dele: o número está na fonte citada (título e nota) ou, se não
    está em fonte nenhuma, na narração. Se outra fonte (uma só) traz o número, o cartão passa a citar essa; se a fonte
    não é do roteiro ou nada sustenta o número, a fonte sai do cartão."""
    lang = doc.primary_language
    sources = doc.sources
    labels = [source_label(s) for s in sources]
    backed = [_numbers_in(f"{s.title}. {s.note}", lang) for s in sources]
    narration = [1.0, *_numbers_in(doc.full_text(lang), lang)]
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        card = scene.card if scene is not None else None
        if scene is None or scene.locked or card is None or not (card.source or "").strip():
            continue
        values = [(text, value) for text in card_numbers(card) if (value := card_number(text, lang)) is not None]
        if not values:
            continue
        cited = (card.source or "").strip()
        # Duas fontes podem ter o mesmo nome curto (dois posts do Rockstar Newswire): vale a que traz o número.
        matches = [i for i, s in enumerate(sources) if cited.lower() in (labels[i].lower(), s.title.strip().lower())]
        ref = next((i for i in matches if all(_known_number(v, backed[i]) for _t, v in values)),
                   matches[0] if matches else None)
        missing = [(text, value) for text, value in values if ref is None or not _known_number(value, backed[ref])]
        if not missing:
            continue
        others = [i for i in range(len(sources))
                  if i not in matches and all(_known_number(v, backed[i]) for _t, v in values)
                  and all(_unit_backed(t, v, f"{sources[i].title}. {sources[i].note}") for t, v in values)]
        other_labels = list(dict.fromkeys(labels[i] for i in others))
        text = missing[0][0]
        if len(other_labels) == 1:
            card.source = other_labels[0]
            issues.append(Issue("fonte_trocada", CORRIGIDO, f'O número "{text}" está na fonte "{card.source}", não em '
                                                            f'"{cited}": o cartão passou a citar essa fonte.',
                                beat.key, scene.id))
            continue
        if ref is not None and not others and all(_known_number(v, narration) for _t, v in missing):
            continue  # número da narração, que o roteiro tira das fontes dele
        card.source = None
        scene.card = card if card.resolved(lang) else None
        message = (f'A fonte "{cited}" não está nas fontes do roteiro: saiu do cartão.' if ref is None
                   else f'A fonte "{cited}" não traz o número "{text}": saiu do cartão.')
        issues.append(Issue("fonte_tirada", CORRIGIDO, message, beat.key, scene.id))
    return issues


SWAP_MIN_BARE = 12
"""Número sem unidade até este valor não troca a fonte do cartão: "1", "2", "6" aparecem em qualquer nota ("1º
trimestre", "GTA 6") e a troca pela coincidência pôs "Fonte: GamesBeat" num cartão de "1 MB" da Malwarebytes."""

_UNIT_WORD_RE = re.compile(r"[\d.,]+\s*([A-Za-zÀ-ÿ]{1,6})\b")
_SCALE_WORDS = {"m", "b", "k", "mi", "bi", "mil", "mi+", "bi+", "x"}


def _unit_backed(card_text: str, value: float, source_text: str) -> bool:
    """A outra fonte sustenta o número com a mesma unidade do cartão ("1 MB" pede "MB" na fonte; "US$ 250" pede o
    símbolo; "30%" pede "%"). Número pequeno sem unidade nunca basta para trocar a fonte."""
    unit = _unit(card_text)
    if not unit:
        word = _UNIT_WORD_RE.search(card_text)
        unit = word.group(1) if word and word.group(1).lower() not in _SCALE_WORDS else ""
    if not unit:
        return abs(value) > SWAP_MIN_BARE
    return unit.lower() in source_text.lower()


def _unit(value: str) -> str:
    if "%" in value:
        return "%"
    match = _CURRENCY_RE.search(value)
    return match.group(0) if match else ""


def _check_compare_units(doc: ProjectDoc, beats: list[Beat]) -> list[Issue]:
    """Comparação ou gráfico entre medidas diferentes (porcentagem contra total, reais contra dólares): as barras
    enganam."""
    lang = doc.primary_language
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.kind not in ("compare", "chart") or scene.card is None:
            continue
        values = [v for v in (item.value.get(lang, "") for item in scene.card.items) if _has_digit(v)]
        units = {_unit(v) for v in values}
        if len(units) > 1:
            shown = ", ".join(sorted(u or "número" for u in units))
            what = "O gráfico mistura" if scene.kind == "chart" else "O cartão compara"
            issues.append(Issue("comparacao_desigual", AVISO,
                                f"{what} medidas diferentes ({shown}): compare só números medidos do mesmo jeito.",
                                beat.key, scene.id))
    return issues


def _phrase(text: str) -> str:
    """Texto para comparar frases: minúsculas, sem acento nem pontuação, uma palavra por espaço, com espaço nas
    pontas (procurar " siga para " não acha "persiga para")."""
    return " " + " ".join(t for t in (normalize_token(w) for w in split_words(text or "")) if t) + " "


def _check_cta(doc: ProjectDoc, style: StyleSpec) -> list[Issue]:
    """A chamada final do roteiro é a de outro estilo ("Siga para descobrir o próximo mistério." num vídeo de games): o
    roteiro foi escrito com um estilo e o projeto trocou de estilo depois. Aviso: a narração é a do roteiro aprovado."""
    from studio.core.styles.library import load_presets

    if not doc.segments:
        return []
    closing = _phrase(doc.segments[-1].text_for(doc.primary_language))
    own = (style.script.cta or "").strip()
    if own and _phrase(own) in closing:
        return []
    for preset in load_presets().values():
        cta = (preset.script.cta or "").strip()
        if preset.id == style.id or not cta or _phrase(cta) == _phrase(own) or _phrase(cta) not in closing:
            continue
        now = (f'A do estilo atual, {style.name}, é "{own}"' if own
               else f"O estilo atual, {style.name}, não tem essa chamada")
        return [Issue("cta_outro_estilo", AVISO, f'A chamada final "{cta}" é do estilo {preset.name}. {now}: ajuste o '
                                                 "fim do roteiro.")]
    return []


SAME_TIER_RUN_S = 20.0
"""Tempo máximo seguido no mesmo nível de material (só IA, só trailer...): acima disso o vídeo fica monótono."""


def _check_real_first(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], pool: Pool | None) -> list[Issue]:
    """Achados do material real (ADR 0004), todos avisos: IA acima do teto em tempo de tela (`ai_over_budget`; com a
    média no teto, num trecho de `tiers.AI_WINDOW_S`, `ai_window_over`),
    gancho sem material real (`hook_not_real`), o mesmo nível de material mais de 20 s seguidos (`same_tier_run`),
    material oficial acima do teto (`official_over_share`) e imagem de IA sem motivo (`faltou_real`: por cena com
    acervo; sem acervo, um aviso só)."""
    policy = sources_for(style)
    in_beats = {scene.id: beat for beat in beats if (scene := scene_for(doc, beat)) is not None}
    timed = screen_seconds(doc, style, beats)
    issues: list[Issue] = []
    ai_scenes = [(scene, in_beats[scene.id]) for scene, prov, _s in timed
                 if prov == "ai" and scene.id in in_beats and has_own_image(scene) and not scene.locked
                 and not (scene.visual.ai_reason or "").strip()]
    if not has_real(pool):
        if ai_scenes and policy.require_ai_reason:
            n = len(ai_scenes)
            what = "1 cena é imagem de IA" if n == 1 else f"{n} cenas são imagem de IA"
            issues.append(Issue("faltou_real", AVISO, f"{what} sem motivo declarado e o projeto não tem material "
                                                      "real: anexe mídia real nas cenas (foto, link oficial ou "
                                                      "notícia) ou cole os links nas Fontes do roteiro; quando a IA "
                                                      'for de propósito, escreva o motivo em "Por que IA" na cena.',
                                ai_scenes[0][1].key, ai_scenes[0][0].id, {"count": n}))
        return issues
    total = sum(seconds for _s, _p, seconds in timed) or 1.0
    # Todo pixel de IA na tela (`tiers.ai_weight`): também o fundo de IA atrás do cartão de dado, com peso.
    weights = [tiers.ai_weight(scene, doc) for scene, _p, _s in timed]
    ai_share = sum(w * seconds for w, (_s, _p, seconds) in zip(weights, timed, strict=True)) / total
    if ai_share > policy.ai_max_share + 0.005:
        issues.append(Issue("ai_over_budget", AVISO, f"Imagem de IA em {round(ai_share * 100)}% do tempo de tela; o "
                                                     f"teto é {round(policy.ai_max_share * 100)}%.",
                            extra={"share": round(ai_share, 3)}))
    else:
        issues += _ai_window_issue(timed, in_beats, policy.ai_max_share, weights)
    official = sum(seconds for _s, prov, seconds in timed if prov == "official") / total
    if official > policy.official_max_share + 0.005:
        issues.append(Issue("official_over_share", AVISO, f"Material oficial em {round(official * 100)}% do tempo; o "
                                                          f"teto é {round(policy.official_max_share * 100)}% (risco de "
                                                          "Content ID).", extra={"share": round(official, 3)}))
    if beats and is_hook_beat(doc, beats[0]):
        hook = scene_for(doc, beats[0])
        if hook is not None and hook.kind != "count":
            prov = tiers.prov_of_scene(hook, doc)
            if prov not in policy.hook:
                issues.append(Issue("hook_not_real", AVISO, f"O gancho mostra {PROV_LABELS.get(prov, prov)}; o ideal "
                                                            "é material próprio, oficial, captura ou foto livre.",
                                    beats[0].key, hook.id))
    run: list[tuple[int, Scene, str, float]] = []
    for position, (scene, prov, seconds) in enumerate(timed, 1):
        if run and run[-1][2] != prov:
            issues += _tier_run(run, in_beats)
            run = []
        run.append((position, scene, prov, seconds))
    issues += _tier_run(run, in_beats)
    if policy.require_ai_reason:
        for scene, beat in ai_scenes:
            issues.append(Issue("faltou_real", AVISO, "Imagem de IA sem motivo e sem item do acervo sobre o assunto: "
                                                      'anexe mídia real na cena ou escreva o motivo em "Por que IA".',
                                beat.key, scene.id))
    return issues


def _clock(seconds: float) -> str:
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _ai_window_issue(timed: list[tuple[Scene, str, float]], in_beats: dict[str, Beat],
                     ai_max_share: float, weights: list[float] | None = None) -> list[Issue]:
    """`ai_window_over`: a média do vídeo cabe no teto de IA, mas um trecho de `tiers.AI_WINDOW_S` passa do teto por
    trecho (`tiers.ai_window_cap`: a IA concentrada num bloco, o slideshow de imagens geradas). Aponta a primeira cena
    de IA do trecho. `weights`: o peso de IA de cada cena (`tiers.ai_weight`); sem ele, o nível `ai` conta inteiro."""
    cap = tiers.ai_window_cap(ai_max_share)
    if weights is None:
        weights = [1.0 if prov == "ai" else 0.0 for _s, prov, _x in timed]
    spans: list[tuple[float, float, Scene]] = []
    t = 0.0
    for scene, _prov, seconds in timed:
        spans.append((t, t + seconds, scene))
        t += seconds
    ai = [(a, b, w) for (a, b, _s), w in zip(spans, weights, strict=True) if w > 0]
    share, start = tiers.window_share(ai, t)
    if share <= cap + 1e-6:
        return []
    end = start + min(tiers.AI_WINDOW_S, t)
    at = next((scene for (a, b, scene), w in zip(spans, weights, strict=True)
               if w > 0 and b > start and a < end), timed[0][0])
    beat = in_beats.get(at.id)
    return [Issue("ai_window_over", AVISO, f"Imagem de IA em {round(share * 100)}% do trecho de {_clock(start)} a "
                                           f"{_clock(end)}; o teto é {round(cap * 100)}% em qualquer trecho de "
                                           f"{tiers.AI_WINDOW_S:.0f} s: espalhe a IA ou troque por material real.",
                  beat.key if beat else None, at.id, {"share": round(share, 3), "start_s": round(start, 1)})]


PROV_LABELS: dict[str, str] = {
    "own": "material próprio", "official": "material oficial", "capture": "captura de página", "free": "foto livre",
    "stock": "foto de banco de imagens", "graphic": "cartão", "meme": "meme", "ai": "imagem de IA",
}  # fmt: skip
"""O nível do material (`Prov`) em português, para as mensagens (o código do nível não diz nada ao usuário)."""


def _tier_run(run: list[tuple[int, Scene, str, float]], in_beats: dict[str, Beat]) -> list[Issue]:
    total = sum(seconds for *_x, seconds in run)
    if total <= SAME_TIER_RUN_S or not any(scene.id in in_beats for _p, scene, _v, _s in run):
        return []
    first, last = run[0][0], run[-1][0]
    prov = run[0][2]
    at = next((scene for _p, scene, _v, _s in run if scene.id in in_beats), run[0][1])
    beat = in_beats.get(at.id)
    return [Issue("same_tier_run", AVISO, f"Cenas {first} a {last} ficam ~{total:.0f} s seguidos no mesmo tipo de "
                                          f"material ({PROV_LABELS.get(prov, prov)}): alterne com outro tipo.",
                  beat.key if beat else None, at.id, {"prov": prov, "seconds": round(total, 1)})]


def check(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], budget: int | None = None, *,
          pool: Pool | None = None) -> list[Issue]:
    """Revisa as cenas das batidas: corrige o que dá (no próprio `doc`) e devolve os problemas.

    `budget` é o limite de imagens novas: com vaga sobrando, imagem reaproveitada para outra data é grave.
    `pool`: o acervo de material real do storyboard. Com material real, o gancho não vira IA e o cartão de fundo liso
    fica liso (o storyboard já pôs o real de fundo onde cabia).
    """
    issues = fix_hook(doc, style, beats, pool)
    issues += fix_hook_text(doc, beats)
    issues += fix_entity_names(doc, beats)
    issues += fix_real_reserve(doc, beats)
    issues += fix_repeated_shots(doc, beats)
    if not has_real(pool):
        issues += fix_flat_cards(doc, beats)
    issues += fix_stamps(doc, beats)
    issues += fix_echo_stamps(doc, beats)
    issues += fix_label_stamps(doc, beats)
    issues += fix_verdict_stamps(doc, beats, pool)
    issues += fix_ambiguous_stamps(doc, beats)
    issues += fix_repeated_headlines(doc, beats)
    issues += _check_sources(doc, beats)
    issues += fix_fact_sources(doc, beats)
    issues += fix_lone_sources(doc, beats)
    issues += fix_card_speech(doc, beats)
    issues += _check_numbers(doc, beats)
    issues += _check_prompts(doc, beats)
    issues += _check_lighting(doc, beats)
    issues += _check_reuse(doc, beats, budget)
    issues += _check_same_image(doc, style, beats)
    issues += _check_motifs(doc, beats)
    issues += _check_cards(doc, beats)
    issues += _check_compare_units(doc, beats)
    issues += _check_cta(doc, style)
    issues += _check_real_first(doc, style, beats, pool)
    return issues
