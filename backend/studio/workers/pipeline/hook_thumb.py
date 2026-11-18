"""O gancho cumpre a thumb escolhida (com o pacote travado): o elemento central dela entra nos primeiros segundos do vídeo.

Quem clicou numa thumb espera ver nos primeiros 3 s o que ela prometeu. O elemento central depende do molde:

- **avatar** (a thumb tem a camada do apresentador): a mesma pose entra na 1ª cena do gancho
  (`presenter.assign`, por cima da regra "nunca na primeira cena");
- **figura** (heroi-palavra, lugar-mapa, versus): o personagem recortado (o quadro de onde o recorte saiu, enquadrado
  no recorte) ou, sem personagem, o quadro de fundo da thumb;
- **prova/objeto**: a captura/mídia do próprio vídeo que a thumb mostra no cartão.

A figura e a prova viram o plano de abertura da 1ª cena do gancho no planejador de edição (`edit_plan`), como
subplano do acervo: o resto da cena continua com o visual dela. Só itens que o acervo do projeto tem (o planejador só
corta para o que o timeline consegue resolver).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from studio.core.project.document import ProjectDoc
from studio.core.project.pool import Pool, PoolItem

log = logging.getLogger(__name__)

FIGURE_TEMPLATES = ("heroi-palavra", "lugar-mapa", "versus")
"""Moldes em que a figura (personagem ou quadro) é o foco da thumb."""
PROOF_TEMPLATES = ("prova", "objeto")
"""Moldes em que a captura/mídia do próprio vídeo é o foco da thumb."""
ZOOM_MAX = 1.6
"""Escala máxima do plano que fecha no personagem (o recorte de uma capa com mais gente)."""


@dataclass(frozen=True)
class Anchor:
    """Um material do acervo que mostra o elemento central da thumb (`focus`: centro x, y do elemento nele; `zoom`:
    a escala que fecha nele, como o recorte do personagem tirado de uma capa com dois)."""

    item: PoolItem
    focus: tuple[float, float] | None = None
    zoom: float = 1.0


@dataclass(frozen=True)
class ThumbHook:
    template: str
    pose: str | None = None
    """Pose do avatar na thumb (None: a thumb não tem avatar)."""
    anchors: list[Anchor] = field(default_factory=list)
    """O elemento central no acervo, na ordem de preferência (vazio: nada a cortar)."""


def _norm(path: str | None) -> str:
    return (path or "").replace("\\", "/").strip().lstrip("./")


def _manifest(channel_id: str | None) -> dict[str, Any]:
    if not channel_id:
        return {}
    try:
        from studio.media.thumb_assets import load_manifest, thumbkit_dir

        return load_manifest(thumbkit_dir(channel_id), channel_id)
    except Exception as exc:  # noqa: BLE001 - sem biblioteca, só a mídia do projeto
        log.debug("biblioteca de thumbs indisponível para o gancho: %s", exc)
        return {}


def _kit_item(manifest: dict[str, Any], ref: str | None) -> dict[str, Any] | None:
    ref = (ref or "").strip()
    if not ref:
        return None
    rest = ref.split(":", 1)[1] if ":" in ref else ref
    return next((i for i in manifest.get("items", []) if i.get("id") == rest), None)


def _by_path(pool: Pool, rel: str) -> PoolItem | None:
    rel = _norm(rel)
    if not rel:
        return None
    return next((i for i in pool.items if _norm(i.path) == rel), None)


def _by_kit_file(pool: Pool, channel_id: str, file: str) -> PoolItem | None:
    tail = f"thumbkit/{channel_id}/{_norm(file)}"
    return next((i for i in pool.items if _norm(i.path).endswith(tail)), None)


def thumb_hook(doc: ProjectDoc, pool: Pool | None) -> ThumbHook | None:
    """O que o gancho precisa mostrar da thumb do vídeo (`doc.locked_package`: a escolhida com o pacote travado, a
    que sai na capa e no A/B; só clicada, sem aprovar, ainda não manda no vídeo); None sem ela."""
    cand = doc.locked_package()
    if cand is None:
        return None
    layers = cand.layers
    pose = layers.presenter.pose if layers.presenter is not None and layers.presenter.pose else None
    anchors: list[Anchor] = []
    if pool is not None and pool.items:
        bg = layers.background
        if layers.template in PROOF_TEMPLATES and bg.kind in ("media", "upload") and bg.ref:
            rel = bg.ref.split(":", 1)[1] if ":" in bg.ref else bg.ref
            item = _by_path(pool, rel)
            if item is not None:
                anchors.append(Anchor(item))
        elif layers.template in FIGURE_TEMPLATES:
            manifest = _manifest(doc.channel_id)
            cid = doc.channel_id or ""
            for cut in layers.characters:
                it = _kit_item(manifest, cut.asset)
                source = (it or {}).get("source")
                src_id = source.get("from") if isinstance(source, dict) else None
                frame = _kit_item(manifest, src_id) if src_id else None
                item = _by_kit_file(pool, cid, frame["file"]) if frame and frame.get("file") else None
                if item is None:
                    continue
                crop = source.get("crop") if isinstance(source, dict) else None
                boxed = isinstance(crop, list) and len(crop) == 4
                cx = (float(crop[0]) + float(crop[2])) / 2 if boxed else 0.5
                cw = float(crop[2]) - float(crop[0]) if boxed else 1.0
                fy = float((it or {}).get("focus_y", 0.45))
                zoom = round(min(ZOOM_MAX, max(1.0, 1.0 / max(0.05, cw))), 3)
                anchors.append(Anchor(item, (round(cx, 3), round(fy, 3)), zoom))
            frame = _kit_item(manifest, bg.ref) if bg.kind == "frame" else None
            item = _by_kit_file(pool, cid, frame["file"]) if frame and frame.get("file") else None
            if item is not None and all(a.item.id != item.id for a in anchors):
                anchors.append(Anchor(item, (float(frame.get("focus_x", 0.5)), float(frame.get("focus_y", 0.5)))))
    if pose is None and not anchors:
        return None
    return ThumbHook(template=layers.template, pose=pose, anchors=anchors)
