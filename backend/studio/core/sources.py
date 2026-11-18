"""Níveis de fonte do material na tela (ADR 0004 §3.0). Só funções puras.

A hierarquia vai do mais real ao gerado: material próprio (`own`), oficial (trailer, arte, gameplay), captura de
página/notícia, foto livre, banco de imagens, gráfico desenhado pelo app, meme e IA. `prov_of_scene` é a fonte única de
verdade do nível de cada cena: a timeline (`clean`, efeitos por nível) e as métricas (tempo de tela de IA) leem daqui.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from studio.core.project.document import CARD_KINDS, DATA_KINDS, PROVS, MediaRef, Prov, Visual

if TYPE_CHECKING:
    from studio.core.project.document import ProjectDoc, Scene

TIERS: tuple[str, ...] = PROVS
"""Os níveis, do mais real ao gerado (T0 a T7)."""
FALLBACK: list[str] = ["own", "capture", "official", "free", "stock", "graphic", "ai"]
"""Ordem de reserva quando o item escolhido falta: o próximo nível disponível no mesmo tópico."""
REAL_TIERS = frozenset({"own", "official", "capture", "free", "stock"})
"""Níveis que contam como material real (`real_share`)."""
HOOK_DEFAULT: list[str] = ["own", "official", "capture", "free"]
"""Níveis aceitos no gancho quando o estilo não diz."""
PANEL_PROV_KINDS = DATA_KINDS
"""Cartões de dado (stat, compare, chart, map): o painel ocupa a tela e o fundo fica desfocado atrás (`BLUR` no
renderer), então a cena conta como `graphic`, seja qual for o fundo (não entra no tempo de IA nem no teto de material
oficial). Os cartões de texto (text, title, quote, count, document) deixam o fundo à vista e contam pelo nível dele."""

# Mídia anexada sem `tier`: o nível pelo tipo e, em foto e clipe, pela origem.
_KIND_TIER: dict[str, Prov] = {"news": "capture", "capture": "capture", "official": "official", "meme": "meme"}
_SOURCE_TIER: dict[str, Prov] = {"upload": "own", "library": "own", "pexels": "stock", "commons": "free",
                                 "openverse": "free", "capture": "capture", "youtube": "official"}
# Licença livre declarada: Creative Commons (CC0, CC BY...), domínio público ou licença de banco gratuito.
_FREE_LICENSE_RE = re.compile(r"^\s*(cc(?:[\s\-_0-9]|$)|creative commons|public domain|dom[ií]nio p[uú]blico|"
                              r"pd(?:m|\b)|pexels|unsplash|pixabay)", re.IGNORECASE)


def is_free_license(license_name: str) -> bool:
    """A licença declarada é livre (CC*, domínio público ou licença de banco gratuito). Vazia: não (direitos
    reservados ou desconhecida)."""
    return bool(_FREE_LICENSE_RE.match(license_name or ""))


def prov_of_media(media: MediaRef) -> Prov:
    """O nível de uma mídia anexada: `tier` quando existe; senão pelo tipo (notícia/captura, oficial, meme) e, em foto
    e clipe, pela origem: arquivo enviado e biblioteca são próprios, Pexels é banco, Commons/Openverse são foto livre,
    YouTube é oficial. Link só é `free` com licença livre declarada; sem ela é material de terceiros com direitos
    (`official`: entra no teto de Content ID e fica `editorial`)."""
    if media.tier is not None:
        return media.tier
    if media.kind in _KIND_TIER:
        return _KIND_TIER[media.kind]
    if media.source in _SOURCE_TIER:
        return _SOURCE_TIER[media.source]
    return "free" if is_free_license(media.license) else "official"


def _prov_of_visual(visual: Visual) -> Prov:
    if visual.type == "none":
        return "graphic"
    if visual.type == "stock":
        return "stock"
    if visual.type == "upload":
        return "own"
    return "ai"  # ai_image, ai_video


def prov_of_scene(scene: Scene, doc: ProjectDoc | None = None) -> Prov:
    """O nível do que a cena mostra. Regras, em ordem:

    1. cartão de dado (`PANEL_PROV_KINDS`) → `graphic` (o painel cobre o fundo desfocado);
    2. mídia real (a da cena ou, com `doc`, a da cena que ela reaproveita): `media.tier`, ou `prov_of_media`;
    3. visual `none`, ou cartão de texto sem visual próprio (sem prompt, imagem fixada nem reaproveitamento) → `graphic`;
    4. com `doc`, cena que reaproveita outra (`reuse_of`, sem imagem fixada própria): vale o visual da cena dona (raiz
       `upload` → `own`, `stock` → `stock`...); referência solta (cena apagada, ciclo) → `graphic`, porque o renderer
       mostra o fundo animado;
    5. o tipo do visual: `ai_image`/`ai_video` → `ai`; `stock` → `stock`; `upload` → `own`.

    Imagem fixada (`visual.asset`) sem mídia continua com o nível do tipo do visual (é a imagem de IA fixada). Sem
    `doc`, o reaproveitamento não é seguido (a cena vale pelo próprio visual)."""
    if scene.kind in PANEL_PROV_KINDS:
        return "graphic"
    media = doc.shown_media(scene) if doc is not None else scene.visual.media
    if media is not None:
        return prov_of_media(media)
    if _bare_card(scene):
        return "graphic"
    if doc is not None and scene.visual.reuse_of and not scene.visual.asset:
        root = doc.reuse_root(scene)
        if root is None or _bare_card(root):
            return "graphic"
        return _prov_of_visual(root.visual)
    return _prov_of_visual(scene.visual)


def _bare_card(scene: Scene) -> bool:
    """Sem imagem nenhuma: visual `none`, ou cartão sem prompt, imagem fixada nem reaproveitamento."""
    visual = scene.visual
    return visual.type == "none" or (
        scene.kind in CARD_KINDS and not visual.prompt and not visual.asset and not visual.reuse_of)


def is_real(prov: str) -> bool:
    return prov in REAL_TIERS
