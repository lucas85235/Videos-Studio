"""Catálogo da curadoria visual: gêneros, linguagens visuais, planos, composições e focos.

Os ids são permanentes (ficam gravados nos documentos e estilos): dá para acrescentar, nunca renomear
nem remover. O texto em inglês de cada item entra no prompt da imagem; o rótulo em português vai para a
interface. Este módulo não importa nada do projeto (o schema dos estilos depende dele).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class CatalogItem:
    label: str
    """Rótulo em português (interface)."""
    text: str
    """Texto em inglês (prompt da imagem)."""


@dataclass(frozen=True)
class VisualLanguage:
    label: str
    prefix: str
    """Começo do prompt em inglês que define a técnica da imagem."""
    photoreal: bool = False
    """Só vale com `art.photoreal` ligado no estilo (rótulo de conteúdo sintético)."""


# Tipo de vídeo (decidido pelo assunto, não pelo nome do estilo).
GENRES: dict[str, str] = {
    "noticia": "Notícia",
    "games": "Games",
    "tecnologia": "Tecnologia",
    "curiosidades": "Curiosidades",
    "explicador": "Explicativo",
    "lista": "Lista e ranking",
    "historia": "História",
    "biografia": "Biografia",
    "financas": "Finanças",
    "misterio": "Mistério",
    "terror": "Terror",
    "true_crime": "True crime",
    "narrativa": "Narrativa",
    "motivacional": "Motivacional",
}

VISUAL_LANGUAGES: dict[str, VisualLanguage] = {
    "key_art": VisualLanguage("Pôster ilustrado", "illustrated key-art poster, bold painterly shapes, saturated gradients"),
    "editorial": VisualLanguage("Ilustração editorial", "modern editorial illustration, clean shapes"),
    "render_3d": VisualLanguage("Render 3D", "stylized 3D render, clean materials, soft global illumination"),
    "flat_vector": VisualLanguage("Vetor plano", "flat vector illustration, simple geometric shapes, clean outlines"),
    "isometric": VisualLanguage("Isométrico", "isometric illustration, miniature diorama view, clean geometry"),
    "ui_mockup": VisualLanguage("Maquete de interface", "generic device/interface mockup, abstract UI blocks, no readable text"),
    "painting": VisualLanguage("Pintura", "painterly digital painting, visible brushstrokes, cinematic composition"),
    "archival": VisualLanguage("Arquivo antigo", "archival documentary look, aged print with grain and paper texture, period-accurate details"),
    "collage": VisualLanguage("Colagem", "mixed-media paper collage, cut-out shapes, layered textures"),
    "line_art": VisualLanguage("Traço de lousa", "hand-drawn black marker line drawing on plain white background, whiteboard sketch, bold clean outlines, minimal shading"),
    "photo": VisualLanguage("Foto realista", "realistic editorial photograph, natural light, shallow depth of field", photoreal=True),
    # Linguagens assumidamente não fotográficas (ADR 0004): a IA que sobra é apoio declarado (hipótese, futuro,
    # conceito) e não se passa por foto, nem por "pintura digital" limpa e coerente, a assinatura de IA mais reconhecível.
    "collage_cutout": VisualLanguage("Recorte de colagem", "cut-out paper collage with torn edges, halftone photo scraps, visible tape and paper grain, flat hard shadows"),
    "ink_sketch": VisualLanguage("Esboço a nanquim", "loose ink sketch with cross-hatching on off-white paper, uneven pen pressure, sparse color wash"),
    "halftone_print": VisualLanguage("Impressão em retícula", "two-color halftone print, coarse dot screen, slight misregistration, flat newsprint texture"),
    "diagram": VisualLanguage("Diagrama", "explanatory schematic illustration, simple shapes, arrows and outlines on a flat background, no labels"),
    "paper_cut": VisualLanguage("Papel recortado", "layered paper-cut diorama, stacked colored card stock, soft drop shadows between layers, handmade look"),
}
# Linguagens da IA "declarada" (não fotográficas): o storyboard com acervo de material real alterna entre elas.
NON_PHOTO_LANGUAGES: tuple[str, ...] = ("collage_cutout", "ink_sketch", "halftone_print", "diagram", "paper_cut")

SHOTS: dict[str, CatalogItem] = {
    "wide": CatalogItem("Plano aberto", "wide establishing shot"),
    "medium": CatalogItem("Plano médio", "medium shot"),
    "close": CatalogItem("Close", "close-up shot"),
    "detail": CatalogItem("Detalhe", "extreme close-up detail shot"),
    "aerial": CatalogItem("Aéreo", "high aerial view"),
    "low_angle": CatalogItem("De baixo para cima", "low-angle shot looking up"),
    "over_shoulder": CatalogItem("Por cima do ombro", "over-the-shoulder shot"),
    "pov": CatalogItem("Ponto de vista", "first-person point-of-view shot"),
    "top_down": CatalogItem("De cima", "top-down view"),
}

COMPOSITIONS: dict[str, CatalogItem] = {
    "center": CatalogItem("Centro", "subject centered"),
    "left_third": CatalogItem("Terço esquerdo", "subject on the left third"),
    "right_third": CatalogItem("Terço direito", "subject on the right third"),
    "upper_half": CatalogItem("Metade de cima", "subject in the upper half, calm lower area"),
    "lower_half": CatalogItem("Metade de baixo", "subject in the lower half, calm upper area"),
}

# Ponto da imagem para onde a câmera olha (reaproveitamento e `zoom_to`). O texto é a origem em CSS.
FOCUS_POINTS: dict[str, CatalogItem] = {
    "center": CatalogItem("Centro", "50% 50%"),
    "top": CatalogItem("Em cima", "50% 22%"),
    "bottom": CatalogItem("Embaixo", "50% 78%"),
    "left": CatalogItem("Esquerda", "22% 50%"),
    "right": CatalogItem("Direita", "78% 50%"),
    "top_left": CatalogItem("Em cima à esquerda", "22% 22%"),
    "top_right": CatalogItem("Em cima à direita", "78% 22%"),
    "bottom_left": CatalogItem("Embaixo à esquerda", "22% 78%"),
    "bottom_right": CatalogItem("Embaixo à direita", "78% 78%"),
}

# Composições que deixam livre a faixa da legenda (posição da legenda → composições boas).
CAPTION_SAFE_COMPOSITIONS: dict[str, tuple[str, ...]] = {
    "top": ("lower_half", "center", "left_third", "right_third"),
    "center": ("upper_half", "left_third", "right_third"),
    "bottom": ("upper_half", "center", "left_third", "right_third"),
}

# Sequências fixas de "Outra imagem": cada variação anda um passo a partir do valor atual.
SHOT_ROTATION: tuple[str, ...] = ("wide", "close", "low_angle", "medium", "detail", "aerial", "over_shoulder", "top_down", "pov")
COMPOSITION_ROTATION: tuple[str, ...] = ("center", "left_third", "right_third", "upper_half", "lower_half")
# Focos usados em rodízio quando várias cenas reaproveitam a mesma imagem.
FOCUS_ROTATION: tuple[str, ...] = ("center", "top", "bottom_left", "right", "top_left", "bottom", "left", "bottom_right", "top_right")

APPROACHES: dict[str, str] = {"literal": "Literal", "misto": "Misto", "alegorico": "Alegórico"}
STYLE_FITS: dict[str, str] = {"ok": "Combina", "fraco": "Combina pouco", "errado": "Não combina"}


# --- validação ------------------------------------------------------------------------------


def clean_id(value: object) -> str | None:
    """Id normalizado (minúsculas, sem espaços nas pontas; hífen e espaço viram '_'), ou None."""
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("-", "_").replace(" ", "_")
    return text or None


def pick(value: object, catalog: Mapping[str, object], default: str | None = None) -> str | None:
    """O id se ele estiver no catálogo; senão `default` (tolerante: serve para normalizar a resposta da IA)."""
    key = clean_id(value)
    return key if key is not None and key in catalog else default


def is_genre(value: object) -> bool:
    return pick(value, GENRES) is not None


def is_language(value: object) -> bool:
    return pick(value, VISUAL_LANGUAGES) is not None


def is_shot(value: object) -> bool:
    return pick(value, SHOTS) is not None


def is_composition(value: object) -> bool:
    return pick(value, COMPOSITIONS) is not None


def is_focus(value: object) -> bool:
    return pick(value, FOCUS_POINTS) is not None


def allowed_languages(photoreal: bool) -> list[str]:
    """Linguagens que um estilo pode usar (as fotorrealistas só com `photoreal`)."""
    return [key for key, lang in VISUAL_LANGUAGES.items() if photoreal or not lang.photoreal]


def validate_languages(ids: Iterable[str]) -> list[str]:
    """Valida ids de linguagem (estrito, para estilos): ValueError no id desconhecido; tira repetidos."""
    out: list[str] = []
    for raw in ids:
        key = clean_id(raw)
        if key is None or key not in VISUAL_LANGUAGES:
            raise ValueError(f"linguagem visual desconhecida: {raw} (válidas: {', '.join(VISUAL_LANGUAGES)})")
        if key not in out:
            out.append(key)
    return out


def clean_languages(ids: Iterable[object], *, photoreal: bool, limit: int = 3) -> list[str]:
    """Versão tolerante (resposta da IA): descarta o desconhecido e o fotorrealista sem `photoreal`."""
    allowed = set(allowed_languages(photoreal))
    out: list[str] = []
    for raw in ids:
        key = clean_id(raw)
        if key in allowed and key not in out:
            out.append(key)
        if len(out) >= limit:
            break
    return out


def language_prefix(language_id: str | None) -> str:
    lang = VISUAL_LANGUAGES.get(language_id or "")
    return lang.prefix if lang else ""


def shot_text(shot_id: str | None) -> str:
    item = SHOTS.get(shot_id or "")
    return item.text if item else ""


def composition_text(composition_id: str | None) -> str:
    item = COMPOSITIONS.get(composition_id or "")
    return item.text if item else ""


def compositions_for_caption(position: str) -> tuple[str, ...]:
    return CAPTION_SAFE_COMPOSITIONS.get(position, tuple(COMPOSITIONS))


def _step(sequence: tuple[str, ...], current: str | None, steps: int) -> str:
    base = sequence.index(current) if current in sequence else 0
    return sequence[(base + steps) % len(sequence)]


def rotate_variant(
    shot: str | None,
    composition: str | None,
    language: str | None,
    languages: list[str],
    variation: int,
) -> tuple[str | None, str | None, str | None]:
    """Plano, composição e linguagem da variação `variation` de uma cena dirigida ("Outra imagem").

    Variação 0 devolve tudo igual. A partir de 1, plano e composição andam `variation` passos nas
    sequências fixas; a partir de 2, a linguagem também alterna dentro de `languages` (um passo a cada
    duas variações). Determinístico: a mesma variação dá sempre o mesmo resultado.
    """
    if variation <= 0:
        return shot, composition, language
    new_shot = _step(SHOT_ROTATION, shot, variation)
    new_comp = _step(COMPOSITION_ROTATION, composition, variation)
    new_lang = language
    pool = [x for x in languages if x in VISUAL_LANGUAGES]
    if variation >= 2 and len(pool) > 1:
        base = pool.index(language) if language in pool else 0
        new_lang = pool[(base + variation // 2) % len(pool)]
    return new_shot, new_comp, new_lang


def catalog_text(*, photoreal: bool) -> str:
    """Catálogos em texto para o pedido do storyboard (id: rótulo — texto)."""
    lines = ["Linguagens visuais (id: nome — técnica):"]
    lines += [f"- {key}: {VISUAL_LANGUAGES[key].label} — {VISUAL_LANGUAGES[key].prefix}" for key in allowed_languages(photoreal)]
    lines.append("Gêneros: " + ", ".join(GENRES))
    lines.append("Planos (shot): " + ", ".join(f"{k} ({v.text})" for k, v in SHOTS.items()))
    lines.append("Composições: " + ", ".join(f"{k} ({v.text})" for k, v in COMPOSITIONS.items()))
    lines.append("Focos (para reaproveitar imagem): " + ", ".join(FOCUS_POINTS))
    return "\n".join(lines)
