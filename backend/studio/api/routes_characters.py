"""Rotas dos personagens fixos (StyleBible do canal e do projeto): cadastro e imagens âncora.

O canal guarda os personagens em `style_bible["characters"]` (o formulário do canal não mexe neles); o projeto, no
documento (`ProjectDoc.characters`, cada mudança é uma versão nova).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from studio.core.project import characters as characters_mod
from studio.core.project import service
from studio.core.project.document import Character, ProjectDoc
from studio.db import session_scope
from studio.media.real_media import UPLOAD_MAX_BYTES, MediaError, store_character_image
from studio.models import Channel

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
