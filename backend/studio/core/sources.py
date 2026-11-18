"""Níveis de fonte do material na tela (ADR 0004 §3.0). Só funções puras.

A hierarquia vai do mais real ao gerado: material próprio (`own`), oficial (trailer, arte, gameplay), captura de
página/notícia, foto livre, banco de imagens, gráfico desenhado pelo app, meme e IA. `prov_of_scene` é a fonte única de
verdade do nível de cada cena: a timeline (`clean`, efeitos por nível) e as métricas (tempo de tela de IA) leem daqui.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from studio.core.project.document import CARD_KINDS, PROVS, MediaRef, Prov

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

# Mídia anexada sem `tier`: o nível pelo tipo e, na foto, pela origem.
_KIND_TIER: dict[str, Prov] = {"news": "capture", "capture": "capture", "official": "official", "clip": "official",
                               "meme": "meme"}
_PHOTO_SOURCE_TIER: dict[str, Prov] = {"upload": "own", "library": "own", "pexels": "stock", "commons": "free",
                                       "openverse": "free", "capture": "capture", "youtube": "official",
                                       "link": "free"}


def prov_of_media(media: MediaRef) -> Prov:
    """O nível de uma mídia anexada: `tier` quando existe; senão pelo tipo (notícia/captura, oficial/clipe, meme) e,
    na foto, pela origem (arquivo enviado é próprio, Pexels é banco, Commons/Openverse/link é foto livre)."""
    if media.tier is not None:
        return media.tier
    if media.kind in _KIND_TIER:
        return _KIND_TIER[media.kind]
    return _PHOTO_SOURCE_TIER.get(media.source, "free")


def prov_of_scene(scene: Scene, doc: ProjectDoc | None = None) -> Prov:
    """O nível do que a cena mostra. Regras, em ordem:

    1. mídia real (a da cena ou, com `doc`, a da cena que ela reaproveita): `media.tier`, ou o nível pelo tipo;
    2. visual `ai_image`/`ai_video` → `ai`; `stock` → `stock`; `upload` → `own`;
    3. cartão sem visual (ou visual `none`) → `graphic`.

    Imagem fixada (`visual.asset`) sem mídia continua com o nível do tipo do visual (é a imagem de IA fixada)."""
    media = doc.shown_media(scene) if doc is not None else scene.visual.media
    if media is not None:
        return prov_of_media(media)
    vtype = scene.visual.type
    if vtype == "none":
        return "graphic"
    if scene.kind in CARD_KINDS and not scene.visual.prompt and not scene.visual.asset and not scene.visual.reuse_of:
        return "graphic"
    if vtype in ("ai_image", "ai_video"):
        return "ai"
    if vtype == "stock":
        return "stock"
    if vtype == "upload":
        return "own"
    return "ai"


def is_real(prov: str) -> bool:
    return prov in REAL_TIERS
