"""Reenquadramento das imagens 16:9 do longo no Short vertical (9:16).

Cada cena com imagem fixada ganha um ponto de foco (`Scene.frame`, x e y de 0 a 1) sugerido pela própria imagem: uma
saliência simples (bordas + cor que destoa do resto), sem modelo nenhum. O renderer recorta a janela vertical em volta
do foco (`crop`) ou mostra a imagem inteira sobre ela mesma ampliada e desfocada (`blur`); o modo padrão é do Short
(`ProjectDoc.frame_mode`) e cada cena pode trocar. A terceira saída é gerar a imagem da cena de novo, já em 9:16, pela
cadeia de imagens de sempre: é a operação "Outra imagem" (`regenerate_scene`), que tira a imagem fixada; o pedido
fica em `SceneFrame.vertical`, com a imagem 16:9 guardada para voltar a ela.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageFilter, UnidentifiedImageError

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import FrameMode, ProjectDoc, Scene, SceneFrame
from studio.core.project.ops import OpError, RegenerateScene, apply_op
from studio.core.styles.library import get_style
from studio.workers.pipeline.build import scene_needs_image
from studio.workers.shorts.derive import ShortsError

# Lado maior da miniatura analisada (px): a saliência é grosseira de propósito, e rápida.
SAMPLE_SIDE = 192
# Fração dos pontos mais salientes que entra na média do foco.
TOP_FRACTION = 0.08
# Abaixo disso a imagem é lisa demais para ter sujeito: fica o meio.
FLAT = 1e-3

NOT_A_SHORT = "Só os Shorts cortados de um vídeo longo têm reenquadramento"


# ---------------------------------------------------------------------------------------------
# foco sugerido
# ---------------------------------------------------------------------------------------------
def _box_blur(a: np.ndarray, r: int) -> np.ndarray:
    """Média numa janela (2r+1)² com bordas repetidas (somas acumuladas, sem SciPy)."""
    if r <= 0:
        return a
    p = np.pad(a, r, mode="edge")
    c = np.cumsum(np.cumsum(p, axis=0), axis=1)
    c = np.pad(c, ((1, 0), (1, 0)))
    k = 2 * r + 1
    s = c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]
    return s / (k * k)


def _norm(a: np.ndarray) -> np.ndarray:
    top = float(a.max()) if a.size else 0.0
    return a / top if top > FLAT else np.zeros_like(a)


def saliency_map(image: Image.Image) -> np.ndarray:
    """Mapa de saliência (0 a 1) de uma miniatura: bordas suavizadas + distância da cor de cada ponto à cor média da
    imagem, com um puxão leve para o meio (o sujeito costuma estar perto dele)."""
    small = image.convert("RGB")
    small.thumbnail((SAMPLE_SIDE, SAMPLE_SIDE), Image.Resampling.BILINEAR)
    smooth = np.asarray(small.filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32) / 255.0
    lum = smooth @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    gy, gx = np.gradient(lum)
    radius = max(1, round(min(lum.shape) * 0.04))
    edges = _box_blur(np.hypot(gx, gy), radius)
    mean = smooth.reshape(-1, 3).mean(axis=0)
    color = np.linalg.norm(smooth - mean, axis=2)
    sal = 0.5 * _norm(edges) + 0.5 * _norm(color)
    h, w = sal.shape
    yy, xx = np.mgrid[0:h, 0:w]
    dist = ((xx / max(1, w - 1) - 0.5) ** 2 + (yy / max(1, h - 1) - 0.5) ** 2) / 0.5
    return sal * (1.0 - 0.25 * dist)


def suggest_focus(image: Image.Image) -> tuple[float, float]:
    """Ponto de foco (x, y de 0 a 1): média dos pontos mais salientes, pesada pela saliência. Imagem lisa: o meio."""
    sal = saliency_map(image)
    if sal.size == 0 or float(sal.max()) <= FLAT:
        return 0.5, 0.5
    cut = float(np.quantile(sal, 1.0 - TOP_FRACTION))
    weights = np.where(sal >= cut, sal, 0.0)
    total = float(weights.sum())
    if total <= FLAT:
        return 0.5, 0.5
    h, w = sal.shape
    yy, xx = np.mgrid[0:h, 0:w]
    x = float((weights * (xx + 0.5)).sum() / total / w)
    y = float((weights * (yy + 0.5)).sum() / total / h)
    return round(min(1.0, max(0.0, x)), 3), round(min(1.0, max(0.0, y)), 3)


def suggest_frame(path: Path, fallback_aspect: float = 16 / 9) -> SceneFrame:
    """Reenquadramento sugerido para o arquivo (foco e proporção dele). Arquivo sumido ou ilegível: o meio, com a
    proporção do longo."""
    try:
        with Image.open(path) as im:
            im.load()
            width, height = im.size
            x, y = suggest_focus(im)
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        return SceneFrame(aspect=fallback_aspect)
    if not width or not height:
        return SceneFrame(aspect=fallback_aspect)
    return SceneFrame(x=x, y=y, aspect=round(width / height, 4))


def frame_pinned(scenes: list[Any], fallback_aspect: float, video_aspect: float) -> int:
    """Sugere o foco de cada cena com imagem fixada (e sem mídia real) que não está na proporção do vídeo. Devolve
    quantas ganharam reenquadramento."""
    data_dir = get_settings().data_dir
    count = 0
    for scene in scenes:
        if scene.visual.media is not None or not scene.visual.asset:
            continue
        frame = suggest_frame(data_dir / scene.visual.asset, fallback_aspect)
        if abs(frame.aspect - video_aspect) / video_aspect < 0.02:
            continue
        scene.frame = frame
        count += 1
    return count


# ---------------------------------------------------------------------------------------------
# edição no Short
# ---------------------------------------------------------------------------------------------
def _child(project_id: str) -> tuple[dict[str, Any], ProjectDoc]:
    try:
        row = service.get_project_row(project_id)
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise ShortsError("Projeto não encontrado", 404) from exc
    if not row.get("parent_id"):
        raise ShortsError(NOT_A_SHORT, 409)
    return row, doc


def _long_aspect(row: dict[str, Any]) -> float:
    """Proporção do vídeo longo de onde o Short saiu (reserva para imagem que não dá para ler)."""
    try:
        parent = service.get_doc(str(row["parent_id"]))
    except service.ProjectNotFound:
        return 16 / 9
    fmt = get_style(parent.style_id, parent.style_overrides).format
    return fmt.width / fmt.height


def _fill_missing_frames(project_id: str, row: dict[str, Any], doc: ProjectDoc) -> None:
    """Short criado antes do reenquadramento: as cenas com imagem fixada ainda sem foco ganham o sugerido (e o
    documento é salvo, para a timeline e a edição usarem)."""
    missing = [s for s in doc.scenes if s.frame is None and s.visual.asset and s.visual.media is None]
    if not missing:
        return
    style = get_style(doc.style_id, doc.style_overrides)
    if frame_pinned(missing, _long_aspect(row), style.format.width / style.format.height):
        service.commit(project_id, doc, "Foco sugerido das imagens do longo")


def _generated_images(doc: ProjectDoc, scenes: list[Scene]) -> dict[str, str]:
    """Imagem já gerada (no cache do build) de cada cena. Só olha o cache: não gera nada."""
    from studio.core.graph.store import ArtifactStore
    from studio.workers.pipeline.build import Builder

    builder = Builder.__new__(Builder)  # o mesmo atalho do `plan`: só a busca no cache
    builder.doc = doc
    builder.style = get_style(doc.style_id, doc.style_overrides)
    builder.store = ArtifactStore()
    out: dict[str, str] = {}
    for scene in scenes:
        inputs = builder.image_inputs(scene)
        if inputs is None:
            continue
        _, cached = builder.cached_image(inputs, scene)
        if cached and cached.get("path"):
            out[scene.id] = cached["path"]
    return out


def _followers(doc: ProjectDoc, root: Scene, asset: str) -> list[Scene]:
    """Cenas do Short que reaproveitam a imagem de `root` e fixaram o mesmo arquivo dela."""
    return [s for s in doc.scenes
            if s.id != root.id and s.visual.reuse_of and s.visual.media is None and s.visual.asset == asset
            and doc.reuse_root(s) is root]


def frames_overview(project_id: str) -> dict[str, Any]:
    """Cenas do Short com o reenquadramento: as de imagem 16:9 fixada (foco, modo) e as que o usuário pediu em 9:16
    (`vertical`; `src` é a imagem já gerada, quando a prévia já passou). As cenas que reaproveitam uma imagem pedida
    em 9:16 seguem a raiz (`follows`)."""
    row, doc = _child(project_id)
    _fill_missing_frames(project_id, row, doc)
    style = get_style(doc.style_id, doc.style_overrides)
    ordered = doc.ordered_scenes()
    position = {s.id: i for i, s in enumerate(ordered)}
    roots: dict[str, Scene | None] = {
        s.id: doc.reuse_root(s) for s in ordered if s.frame is not None and s.frame.vertical and s.visual.media is None
    }
    generated = _generated_images(doc, list({r.id: r for r in roots.values() if r is not None}.values()))
    scenes = []
    for index, scene in enumerate(ordered):
        frame = scene.frame
        if scene.visual.media is not None or frame is None:
            continue
        follows = None
        if frame.vertical:
            if scene.visual.asset:
                continue  # ganhou outra imagem fixada depois do pedido (upload no roteiro): nada a mostrar aqui
            root = roots.get(scene.id)
            follows = root.id if root is not None and root.id != scene.id else None
            src = generated.get(root.id) if root is not None else None
        elif scene.visual.asset:
            src = scene.visual.asset
        else:
            continue  # perdeu a imagem fixada por outro caminho (roteiro): a imagem nova já sai em 9:16
        scenes.append(
            {
                "id": scene.id,
                "index": index,
                "kind": scene.kind,
                "src": src,
                "frame": frame.model_dump(mode="json"),
                "vertical": frame.vertical,
                "follows": follows,
                "follows_index": position.get(follows) if follows else None,
            }
        )
    return {
        "default_mode": doc.frame_mode,
        "width": style.format.width,
        "height": style.format.height,
        "scenes": scenes,
    }


def _restore(doc: ProjectDoc, scene: Scene) -> None:
    """Desfaz o pedido de imagem vertical: a cena (e quem seguia ela) volta à imagem 16:9 com o foco de antes."""
    frame = scene.frame
    if frame is None or not frame.vertical or not frame.source or scene.visual.media is not None:
        raise ShortsError("Esta cena não tem imagem 16:9 para voltar", 422)
    source = frame.source
    followers = [s for s in doc.scenes
                 if s.id != scene.id and s.frame is not None and s.frame.vertical and s.frame.source == source
                 and not s.visual.asset and doc.reuse_root(s) is scene]
    for target in (scene, *followers):
        target.visual.asset = source
        target.frame = target.frame.model_copy(update={"vertical": False, "source": None})


def update_frames(project_id: str, default_mode: FrameMode | None, edits: list[dict[str, Any]]) -> dict[str, Any]:
    """Grava o modo padrão e, por cena, o foco (x, y), o modo ("crop", "blur" ou "default"), `reset` (foco sugerido
    de novo) ou `restore` (desfaz o pedido de imagem vertical: volta à imagem 16:9)."""
    _, doc = _child(project_id)
    if default_mode is not None:
        doc.frame_mode = default_mode
    data_dir = get_settings().data_dir
    by_id = {s.id: s for s in doc.scenes}
    for edit in edits:
        scene = by_id.get(str(edit.get("id")))
        if scene is None:
            raise ShortsError("Cena que não existe neste Short", 422)
        if edit.get("restore"):
            _restore(doc, scene)
            continue
        frame = scene.frame
        if frame is None or frame.vertical or not scene.visual.asset or scene.visual.media is not None:
            raise ShortsError("Esta cena não tem imagem para reenquadrar", 422)
        if edit.get("reset"):
            fresh = suggest_frame(data_dir / scene.visual.asset, frame.aspect)
            frame = frame.model_copy(update={"x": fresh.x, "y": fresh.y, "auto": True})
        if edit.get("x") is not None or edit.get("y") is not None:
            x = frame.x if edit.get("x") is None else float(edit["x"])
            y = frame.y if edit.get("y") is None else float(edit["y"])
            frame = frame.model_copy(update={"x": min(1.0, max(0.0, x)), "y": min(1.0, max(0.0, y)), "auto": False})
        mode: Literal["crop", "blur", "default"] | None = edit.get("mode")
        if mode is not None:
            frame = frame.model_copy(update={"mode": None if mode == "default" else mode})
        scene.frame = SceneFrame.model_validate(frame.model_dump())
    service.commit(project_id, doc, "Enquadramento do Short")
    return frames_overview(project_id)


def vertical_image(project_id: str, scene_id: str) -> dict[str, Any]:
    """Pede a imagem da cena de novo, já em 9:16: a operação "Outra imagem" (tira a imagem fixada; a próxima prévia
    gera pela cadeia de imagens do estilo, na proporção do Short). O pedido fica em `frame.vertical`, com a imagem
    16:9 guardada para voltar. As cenas que reaproveitam esta imagem largam a cópia 16:9 e passam a mostrar a nova."""
    _, doc = _child(project_id)
    scene = next((s for s in doc.scenes if s.id == scene_id), None)
    if scene is None:
        raise ShortsError("Cena que não existe neste Short", 422)
    frame = scene.frame
    if frame is None or frame.vertical or not scene.visual.asset or scene.visual.media is not None:
        raise ShortsError("Esta cena não tem imagem 16:9 para trocar", 422)
    source = scene.visual.asset
    # Quem reaproveita a imagem desta cena (antes da troca: "Outra imagem" pode cortar o `reuse_of` dela).
    followers = _followers(doc, scene, source)
    locked = scene.locked
    scene.locked = False  # as cenas do Short ficam travadas (o roteiro é o do longo); a troca da imagem é pedida aqui
    try:
        apply_op(doc, RegenerateScene(scene_id=scene_id))
    except OpError as exc:
        raise ShortsError(str(exc), 422) from exc
    scene.locked = locked
    if not scene_needs_image(scene):
        raise ShortsError("Esta cena não tem prompt de imagem (imagem enviada ou cartão): não dá para gerar de novo", 422)
    scene.frame = frame.model_copy(update={"vertical": True, "source": source})
    for other in followers:
        other.visual.asset = None  # volta a mostrar a imagem da raiz (a vertical)
        base = other.frame or SceneFrame(aspect=frame.aspect)
        other.frame = base.model_copy(update={"vertical": True, "source": source})
    service.commit(project_id, ProjectDoc.model_validate(doc.model_dump()), "Imagem vertical da cena do Short")
    return frames_overview(project_id)
