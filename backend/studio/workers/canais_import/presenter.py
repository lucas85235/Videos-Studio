"""Importação das poses do avatar do apresentador (PNG com alfa) para a biblioteca do Vulto.

Fonte padrão: `<canais>/plantao-vice/video/public/apresentador/` (17 poses, 1024×1536, 2-3 MB cada). A pasta do
canais é só lida. Cada pose:
  1. é validada: PNG, com alfa, e o pixel (0,0) transparente (JPG e PNG sem alfa são recusados). Tocar a lateral
     (braço cortado no PNG original) ou a base (corte na cintura) é só aviso;
  2. é copiada como está para `library/presenter/<canal>/<pose>.png` (a thumb usa o original);
  3. ganha a versão de vídeo `<pose>.r900.png`: alfa aparado e 900 px de altura;
  4. recebe emoções, apelidos, `thumb_ok` e `facing` da tabela do `plantao-vice/playbook.md` e do `poseDe()` do
     `Reacao.tsx` (pose nova; a que já existe no canal mantém o que o usuário editou).
O avatar importado fica desligado (`enabled=False`) a não ser que `enable=True`: ligar é decisão do usuário.
"""

from __future__ import annotations

import io
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from studio.config import get_settings
from studio.core.project.document import PRESENTER_POSE_ID_RE, Presenter, PresenterPose
from studio.workers.canais_import.reader import default_root

RENDER_HEIGHT = 900
MAX_POSE_BYTES = 25_000_000
SOURCE_SUBDIR = Path("plantao-vice") / "video" / "public" / "apresentador"
CHARACTER_ID = "lucas"
ANCHOR_POSES = ("base", "oculos", "apontando")

# Metadados das 17 poses (playbook.md, seção "Avatar nos vídeos longos", e `poseDe()` do Reacao.tsx).
POSE_TABLE: dict[str, dict[str, Any]] = {
    "base": {"emotions": ["neutro", "braços cruzados"]},
    "feliz": {"emotions": ["concordar", "boa notícia", "cta", "joinha"], "aliases": ["piscando"]},
    "pensativo": {"emotions": ["dúvida", "pergunta"]},
    "preocupado": {"emotions": ["cuidado", "risco"]},
    "surpreso": {"emotions": ["choque", "surpresa"], "aliases": ["espantado"], "thumb_ok": True},
    "apontando": {"emotions": ["explicar", "mostrar a prova"], "aliases": ["explicando"], "thumb_ok": True,
                  "facing": "right"},
    "rindo": {"emotions": ["humor", "absurdo"]},
    "mao-na-testa": {"emotions": ["vergonha alheia", "erro"], "aliases": ["vergonha"], "thumb_ok": True},
    "controle": {"emotions": ["gameplay", "jogar"], "aliases": ["jogando"]},
    "celular": {"emotions": ["notícia", "vazamento", "post"]},
    "lupa": {"emotions": ["detalhe escondido", "investigação"], "aliases": ["investigando"], "thumb_ok": True},
    "segredo": {"emotions": ["rumor", "segredo", "spoiler"], "thumb_ok": True},
    "bravo": {"emotions": ["atraso", "abuso", "raiva"]},
    "comemorando": {"emotions": ["boa notícia grande", "lançamento", "comemoração"], "aliases": ["animado"],
                    "thumb_ok": True},
    "suando": {"emotions": ["nervosismo", "espaço", "tensão"], "aliases": ["nervoso"]},
    "oculos": {"emotions": ["abertura", "fecho", "estilo"]},
    "dinheiro": {"emotions": ["custo", "preço", "vendas"], "thumb_ok": True},
}


class PoseError(ValueError):
    """Pose recusada (formato, sem alfa, fundo opaco): mensagem em português."""


@dataclass
class PoseCheck:
    width: int
    height: int
    warnings: list[str] = field(default_factory=list)


@dataclass
class ImportReport:
    channel_id: str
    source: str
    imported: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    rejected: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    enabled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"channel_id": self.channel_id, "source": self.source, "imported": self.imported,
                "updated": self.updated, "rejected": self.rejected, "warnings": self.warnings,
                "anchors": self.anchors, "enabled": self.enabled}


def default_source() -> Path:
    return default_root() / SOURCE_SUBDIR


def pose_id_from(name: str) -> str:
    """Id da pose a partir do nome do arquivo ("Mão na Testa.png" → "mao-na-testa")."""
    from studio.core.project.presenter import fold

    return re.sub(r"[^a-z0-9_\-]", "", fold(Path(name).stem))[:40]


def check_pose(data: bytes, name: str = "pose") -> tuple[Image.Image, PoseCheck]:
    """Valida o PNG da pose e devolve a imagem RGBA carregada. PoseError: não é PNG, não tem alfa ou o canto (0,0)
    não é transparente (fundo não recortado)."""
    if len(data) > MAX_POSE_BYTES:
        raise PoseError(f"{name}: o arquivo passa de 25 MB.")
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format
            im.load()
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            image = im.convert("RGBA") if has_alpha else None
    except (OSError, ValueError) as exc:
        raise PoseError(f"{name}: não deu para abrir a imagem.") from exc
    if fmt != "PNG":
        raise PoseError(f"{name}: a pose precisa ser PNG com fundo transparente (veio {fmt or 'formato desconhecido'}).")
    if image is None:
        raise PoseError(f"{name}: o PNG não tem canal alfa (fundo transparente).")
    alpha = image.getchannel("A")
    if alpha.getpixel((0, 0)) != 0:
        raise PoseError(f"{name}: o canto de cima à esquerda não é transparente (fundo não recortado).")
    bbox = alpha.getbbox()
    if bbox is None:
        raise PoseError(f"{name}: a imagem é toda transparente.")
    width, height = image.size
    check = PoseCheck(width, height)
    left, top, right, bottom = bbox
    if left == 0 or right == width:
        check.warnings.append(f"{name}: a figura encosta na lateral (braço ou cotovelo cortado no PNG).")
    if top == 0:
        check.warnings.append(f"{name}: a figura encosta no topo (cabeça cortada?).")
    return image, check


def render_version(image: Image.Image, height: int = RENDER_HEIGHT) -> Image.Image:
    """Alfa aparado e redimensionado para `height` px de altura (versão leve para o vídeo)."""
    bbox = image.getchannel("A").getbbox()
    cropped = image.crop(bbox) if bbox else image
    w, h = cropped.size
    if h != height:
        cropped = cropped.resize((max(1, round(w * height / h)), height), Image.Resampling.LANCZOS)
    return cropped


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def pose_paths(channel_id: str, pose_id: str) -> tuple[str, str]:
    """Caminhos relativos (ao diretório de dados) do original e da versão de vídeo."""
    base = f"library/presenter/{channel_id}"
    return f"{base}/{pose_id}.png", f"{base}/{pose_id}.r{RENDER_HEIGHT}.png"


def store_pose(channel_id: str, pose_id: str, data: bytes, name: str | None = None) -> tuple[PresenterPose, list[str]]:
    """Valida e guarda uma pose (original + versão de vídeo). Devolve a pose (metadados da tabela quando o id é
    conhecido) e os avisos. PoseError: pose recusada ou id inválido."""
    if not re.match(PRESENTER_POSE_ID_RE, pose_id or ""):
        raise PoseError(f"id de pose inválido: {pose_id!r} (minúsculas, números e hífen)")
    if not re.match(r"^[A-Za-z0-9_\-]+$", channel_id or ""):
        raise PoseError("canal inválido")
    image, check = check_pose(data, name or pose_id)
    rel_file, rel_render = pose_paths(channel_id, pose_id)
    root = get_settings().data_dir
    target = root / rel_file
    if not target.is_file() or target.read_bytes() != data:
        _atomic_write(target, data)
    _atomic_write(root / rel_render, _png_bytes(render_version(image)))
    meta = POSE_TABLE.get(pose_id, {})
    pose = PresenterPose(id=pose_id, file=rel_file, render_file=rel_render, emotions=list(meta.get("emotions", [])),
                         aliases=list(meta.get("aliases", [])), thumb_ok=bool(meta.get("thumb_ok", False)),
                         facing=meta.get("facing", "center"))
    return pose, check.warnings


def merge_pose(presenter: Presenter, pose: PresenterPose, *, keep_meta: bool = True) -> tuple[Presenter, bool]:
    """Põe a pose no avatar. A que já existe troca só os arquivos (e mantém emoções, apelidos, thumb e direção
    editados pelo usuário) quando `keep_meta`. Devolve (avatar novo, se já existia)."""
    out = presenter.model_copy(deep=True)
    for i, old in enumerate(out.poses):
        if old.id == pose.id:
            out.poses[i] = old.model_copy(update={"file": pose.file, "render_file": pose.render_file}) if keep_meta \
                else pose
            return out, True
    out.poses.append(pose)
    return out, False


def _replace_anchors(channel_id: str, folder: Path, bible: dict[str, Any], report: ImportReport) -> dict[str, Any]:
    """Troca as imagens âncora do personagem `lucas` pelas poses base, oculos e apontando (fundo branco)."""
    from studio.core.project import characters as characters_mod
    from studio.media.real_media import MediaError, store_character_image

    chars = characters_mod.from_bible(bible)
    target = next((c for c in chars if c.id == CHARACTER_ID), None)
    if target is None:
        report.warnings.append(f"Âncoras: o canal não tem o personagem '{CHARACTER_ID}'; nada trocado.")
        return bible
    images: list[str] = []
    for pose_id in ANCHOR_POSES:
        path = folder / f"{pose_id}.png"
        if not path.is_file():
            continue
        with Image.open(path) as im:
            rgba = im.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        try:
            images.append(store_character_image(_png_bytes(flat)))
        except MediaError as exc:
            report.warnings.append(f"Âncora {pose_id}: {exc}")
    if not images:
        return bible
    report.anchors = images
    chars = [c.model_copy(update={"images": images}) if c.id == CHARACTER_ID else c for c in chars]
    return {**bible, characters_mod.BIBLE_KEY: [c.model_dump(mode="json") for c in chars]}


def import_folder(channel_id: str, folder: str | Path | None = None, *, enable: bool | None = None,
                  replace_anchors: bool = False) -> ImportReport:
    """Importa todas as poses PNG da pasta para o canal e grava o avatar no `style_bible`. KeyError: canal
    inexistente; FileNotFoundError: pasta inexistente. `enable` None mantém o que o canal tinha (novo: desligado)."""
    from studio.core.project.document import PRESENTER_KEY
    from studio.db import session_scope
    from studio.models import Channel

    src = Path(folder) if folder else default_source()
    if not src.is_dir():
        raise FileNotFoundError(str(src))
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        bible = dict(row.style_bible or {})
    had = isinstance(bible.get(PRESENTER_KEY), dict)
    presenter = Presenter.from_bible(bible)
    if not had:
        presenter = presenter.model_copy(update={"character_id": CHARACTER_ID, "default_pose": "base"})
    report = ImportReport(channel_id=channel_id, source=str(src))
    for path in sorted(src.iterdir()):
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
            continue
        pose_id = pose_id_from(path.name)
        try:
            pose, warnings = store_pose(channel_id, pose_id, path.read_bytes(), path.name)
        except PoseError as exc:
            report.rejected[path.name] = str(exc)
            continue
        report.warnings += warnings
        presenter, existed = merge_pose(presenter, pose)
        (report.updated if existed else report.imported).append(pose_id)
    if enable is not None:
        presenter.enabled = enable
    if presenter.pose(presenter.default_pose) is None and presenter.poses:
        presenter.default_pose = presenter.poses[0].id
    report.enabled = presenter.enabled
    if replace_anchors:
        bible = _replace_anchors(channel_id, src, bible, report)
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        fresh = dict(row.style_bible or {})
        if replace_anchors and report.anchors:
            from studio.core.project.characters import BIBLE_KEY

            fresh[BIBLE_KEY] = bible[BIBLE_KEY]
        fresh[PRESENTER_KEY] = presenter.model_dump(mode="json")
        row.style_bible = fresh
    return report


def format_report(report: ImportReport) -> str:
    lines = [f"Avatar do canal {report.channel_id} (de {report.source}):",
             f"  {len(report.imported)} poses novas, {len(report.updated)} atualizadas, "
             f"{len(report.rejected)} recusadas; avatar {'ligado' if report.enabled else 'desligado'}."]
    lines += [f"  recusada {name}: {why}" for name, why in report.rejected.items()]
    if report.warnings:
        lines.append(f"  {len(report.warnings)} avisos (figura encostando na borda é normal nas poses do canais).")
    if report.anchors:
        lines.append(f"  âncoras do personagem '{CHARACTER_ID}' trocadas: {len(report.anchors)}")
    return "\n".join(lines)
