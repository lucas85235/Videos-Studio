"""Rotas dos personagens fixos (StyleBible do canal e do projeto): cadastro e imagens âncora; e do avatar do
apresentador (poses do canal, importação do canais, ajuste do projeto e da cena, redistribuição automática).

O canal guarda os personagens em `style_bible["characters"]` (o formulário do canal não mexe neles); o projeto, no
documento (`ProjectDoc.characters`, cada mudança é uma versão nova).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.core.project import characters as characters_mod
from studio.core.project import presenter as presenter_core
from studio.core.project import service
from studio.core.project.document import Character, Presenter, PresenterOverride, ProjectDoc, ScenePresenter
from studio.db import session_scope
from studio.media.real_media import UPLOAD_MAX_BYTES, MediaError, store_character_image
from studio.models import Channel
from studio.workers.canais_import import presenter as presenter_import
from studio.workers.canais_import.reader import default_root
from studio.workers.pipeline import presenter as presenter_scenes

router = APIRouter(prefix="/api", tags=["personagens"])


class CharactersBody(BaseModel):
    characters: list[Character] = Field(default_factory=list, max_length=characters_mod.MAX_CHARACTERS)


def _dump(items: list[Character]) -> list[dict[str, Any]]:
    return [c.model_dump(mode="json") for c in items]


def _checked(items: list[Character]) -> list[Character]:
    try:
        return characters_mod.check_list(items)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/characters/images")
def upload_character_image(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
    """Guarda uma imagem âncora (JPG, PNG ou WEBP). O caminho vai na lista `images` do personagem."""
    data = file.file.read(UPLOAD_MAX_BYTES + 1)
    try:
        rel = store_character_image(data)
    except MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"path": rel, "url": f"/files/{rel}"}


@router.get("/channels/{channel_id}/characters")
def channel_characters(channel_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        return _dump(characters_mod.from_bible(row.style_bible))


@router.put("/channels/{channel_id}/characters")
def save_channel_characters(channel_id: str, body: CharactersBody) -> list[dict[str, Any]]:
    items = _checked(body.characters)
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        row.style_bible = {**(row.style_bible or {}), characters_mod.BIBLE_KEY: _dump(items)}
    return _dump(items)


def _project_view(doc: ProjectDoc) -> dict[str, Any]:
    channel = characters_mod.from_bible(characters_mod.channel_bible(doc.channel_id))
    return {"channel_id": doc.channel_id, "project": _dump(doc.characters), "channel": _dump(channel),
            "effective": _dump(characters_mod.merged(list(doc.characters), channel))}


def _doc(project_id: str) -> ProjectDoc:
    try:
        return service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


@router.get("/projects/{project_id}/characters")
def project_characters(project_id: str) -> dict[str, Any]:
    return _project_view(_doc(project_id))


@router.put("/projects/{project_id}/characters")
def save_project_characters(project_id: str, body: CharactersBody) -> dict[str, Any]:
    items = _checked(body.characters)
    new_doc = _doc(project_id).model_copy(deep=True)
    new_doc.characters = items
    version = service.commit(project_id, new_doc, "Personagens do projeto atualizados")
    return {"version": version, **_project_view(new_doc)}


# --- Avatar do apresentador (style_bible["presenter"] do canal; ProjectDoc.presenter; Scene.presenter) ------------


class PresenterImportBody(BaseModel):
    path: str | None = None
    """Pasta com os PNG (dentro da pasta do canais ou da biblioteca). None: a pasta padrão do canais."""
    enable: bool | None = None
    """Liga/desliga o avatar junto. None: mantém (avatar novo fica desligado)."""
    replace_anchors: bool = False


class ScenePresenterBody(BaseModel):
    presenter: ScenePresenter | None = None
    """None: a cena volta para a distribuição automática."""


def _presenter_view(channel_id: str, presenter: Presenter, warnings: list[str] | None = None) -> dict[str, Any]:
    urls = {p.id: {"file": f"/files/{p.file}", "render": f"/files/{p.render_file or p.file}"} for p in presenter.poses}
    out: dict[str, Any] = {"channel_id": channel_id, "presenter": presenter.model_dump(mode="json"), "urls": urls,
                           "thumb_poses": [p.id for p in presenter.thumb_poses()]}
    if warnings is not None:
        out["warnings"] = warnings
    return out


def _channel_presenter_or_404(channel_id: str) -> Presenter:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        return Presenter.from_bible(row.style_bible)


@router.get("/channels/{channel_id}/presenter")
def channel_presenter(channel_id: str) -> dict[str, Any]:
    return _presenter_view(channel_id, _channel_presenter_or_404(channel_id))


@router.put("/channels/{channel_id}/presenter")
def save_channel_presenter(channel_id: str, body: Presenter) -> dict[str, Any]:
    """Grava o avatar do canal (toggle, poses, emoções, regras). Os arquivos das poses precisam existir (vêm do
    upload ou da importação)."""
    _channel_presenter_or_404(channel_id)
    root = get_settings().data_dir
    missing = [p.id for p in body.poses if not (root / p.file).is_file()
               or (p.render_file and not (root / p.render_file).is_file())]
    if missing:
        raise HTTPException(status_code=422, detail=f"pose sem arquivo: {', '.join(missing)} (envie o PNG de novo)")
    if body.poses and body.pose(body.default_pose) is None:
        raise HTTPException(status_code=422, detail=f"a pose padrão '{body.default_pose}' não existe")
    presenter_core.save_channel_presenter(channel_id, body)
    return _presenter_view(channel_id, body)


@router.post("/channels/{channel_id}/presenter/poses")
def upload_presenter_pose(
    channel_id: str,
    file: Annotated[UploadFile, File()],
    pose_id: Annotated[str | None, Form(max_length=60)] = None,
) -> dict[str, Any]:
    """Envia (ou troca) uma pose: PNG com fundo transparente. Sem `pose_id`, o id vem do nome do arquivo. A pose
    nova recebe os metadados conhecidos (tabela do canais); a que já existe mantém os editados."""
    current = _channel_presenter_or_404(channel_id)
    pid = (pose_id or presenter_import.pose_id_from(file.filename or "")).strip()
    data = file.file.read(presenter_import.MAX_POSE_BYTES + 1)
    try:
        pose, warnings = presenter_import.store_pose(channel_id, pid, data, file.filename or pid)
    except presenter_import.PoseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    merged, _existed = presenter_import.merge_pose(current, pose)
    if merged.pose(merged.default_pose) is None:
        merged.default_pose = merged.poses[0].id
    presenter_core.save_channel_presenter(channel_id, merged)
    saved = merged.pose(pid)
    return {"pose": saved.model_dump(mode="json") if saved else None, **_presenter_view(channel_id, merged, warnings)}


def _allowed_import_dir(raw: str | None) -> Path:
    """A pasta da importação: só pastas locais dentro da pasta do canais ou da biblioteca do Vulto."""
    if not raw:
        return presenter_import.default_source()
    try:
        path = Path(raw).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail="caminho inválido") from exc
    for root in (default_root(), get_settings().library_dir):
        try:
            if path.is_relative_to(root.expanduser().resolve()):
                return path
        except OSError:
            continue
    raise HTTPException(status_code=422, detail="a pasta precisa ficar dentro da pasta do canais ou da biblioteca")


@router.post("/channels/{channel_id}/presenter/import")
def import_presenter(channel_id: str, body: PresenterImportBody | None = None) -> dict[str, Any]:
    """Importa as poses de uma pasta (padrão: a do apresentador do Plantão Vice no canais)."""
    body = body or PresenterImportBody()
    _channel_presenter_or_404(channel_id)
    folder = _allowed_import_dir(body.path)
    try:
        report = presenter_import.import_folder(channel_id, folder, enable=body.enable,
                                                replace_anchors=body.replace_anchors)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"pasta não encontrada: {folder}") from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    return {"report": report.as_dict(), **_presenter_view(channel_id, _channel_presenter_or_404(channel_id))}


def _style_of(doc: ProjectDoc) -> Any:
    from studio.core.styles.library import get_style

    try:
        return get_style(doc.style_id, doc.style_overrides)
    except Exception:  # noqa: BLE001 - estilo apagado: o avatar segue só canal e projeto
        return None


def _project_presenter_view(doc: ProjectDoc) -> dict[str, Any]:
    style = _style_of(doc)
    channel = presenter_core.channel_presenter(doc.channel_id)
    video = presenter_core.effective(channel, doc, style, purpose="video")
    thumb = presenter_core.effective(channel, doc, style, purpose="thumb")
    return {"override": doc.presenter.model_dump(mode="json"), "channel_enabled": channel.enabled,
            "video": video is not None, "thumb": thumb is not None,
            "thumb_poses": [p.id for p in presenter_core.thumb_poses(thumb)],
            **presenter_scenes.summary(doc)}


@router.get("/projects/{project_id}/presenter")
def project_presenter(project_id: str) -> dict[str, Any]:
    return _project_presenter_view(_doc(project_id))


@router.put("/projects/{project_id}/presenter")
def save_project_presenter(project_id: str, body: PresenterOverride) -> dict[str, Any]:
    """Avatar neste projeto: padrão do canal (`enabled` null), ligado ou desligado; thumb, vídeo e poses fora."""
    new_doc = _doc(project_id).model_copy(deep=True)
    new_doc.presenter = body
    version = service.commit(project_id, new_doc, "Avatar do projeto atualizado")
    return {"version": version, **_project_presenter_view(new_doc)}


@router.put("/projects/{project_id}/scenes/{scene_id}/presenter")
def save_scene_presenter(project_id: str, scene_id: str, body: ScenePresenterBody) -> dict[str, Any]:
    """Avatar de uma cena escolhido à mão (vence a distribuição automática). `{"presenter": {"off": true}}` tira o
    avatar da cena; `{"presenter": null}` devolve a cena à distribuição automática."""
    new_doc = _doc(project_id).model_copy(deep=True)
    try:
        scene = new_doc.scene(scene_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="cena não encontrada") from exc
    scene.presenter = body.presenter.model_copy(update={"source": "manual"}) if body.presenter is not None else None
    version = service.commit(project_id, new_doc, "Avatar da cena ajustado")
    return {"version": version, "scene_id": scene_id,
            "presenter": scene.presenter.model_dump(mode="json") if scene.presenter else None}


@router.post("/projects/{project_id}/presenter/assign")
def assign_project_presenter(project_id: str) -> dict[str, Any]:
    """Redistribui o avatar automático pelas cenas (o manual fica). Sem narração, os tempos vêm da velocidade da
    fala do estilo."""
    doc = _doc(project_id).model_copy(deep=True)
    style = _style_of(doc)
    presenter = presenter_core.project_presenter(doc, style, purpose="video")
    lang = doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150) if style is not None else 150
    words = presenter_scenes.estimate_words(doc, lang, wpm)
    vertical = bool(style is not None and style.format.height > style.format.width)
    issues = presenter_scenes.assign(doc, presenter, words, vertical=vertical)
    version = service.commit(project_id, doc, "Avatar redistribuído")
    return {"version": version, "issues": [i.as_dict() for i in issues], **_project_presenter_view(doc)}
