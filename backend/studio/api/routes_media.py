"""Rotas da mídia real: buscar fotos livres, anexar (candidato, link ou arquivo), editar, tirar e créditos.

Todas são `def` (rodam no threadpool: a busca e o download usam a rede). O documento é lido de novo logo antes do
commit, para não desfazer uma edição feita enquanto a imagem baixava.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from studio.api.routes_projects import _rebuild
from studio.core.project import media_edit, service
from studio.core.project.document import MediaRef, ProjectDoc, RealHint, RealKind, Scene
from studio.core.project.ops import OpError
from studio.core.styles.library import get_style
from studio.media import real_media
from studio.media.real_media import SOURCE_LABELS, MediaError
from studio.media.safe_http import BlockedUrl, FetchError, TooLarge
from studio.providers import media_search
from studio.workers.publish.credits import media_credits

router = APIRouter(prefix="/api/projects", tags=["mídia real"])

NEWS_ONLY_IMAGE = "O cartão de notícia só aparece em cena do tipo Imagem."
LOCKED = "A cena está travada: destrave para mudar a mídia real."


class FoundBody(BaseModel):
    """O achado da busca na web escolhido (`FoundMedia`): o veículo vira o crédito e a página fica como fonte."""

    image: str = Field(default="", max_length=2000)
    page_url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=600)
    outlet: str = Field(default="", max_length=300)


class AttachBody(BaseModel):
    via: Literal["candidate", "link"]
    candidate_id: str | None = Field(default=None, max_length=400)
    url: str | None = Field(default=None, max_length=2000)
    kind: RealKind | None = None
    found: FoundBody | None = None
    rebuild: bool = False


class MediaPatchBody(BaseModel):
    kind: RealKind | None = None
    credit: str | None = Field(default=None, max_length=500)
    editorial: bool | None = None
    focus: str | None = Field(default=None, max_length=40)
    fit: Literal["cover", "contain", "auto"] | None = None
    outlet: str | None = Field(default=None, max_length=300)
    published: str | None = Field(default=None, max_length=60)
    headline: str | None = Field(default=None, max_length=600)
    lang: str | None = Field(default=None, max_length=20)
    rebuild: bool = False


class RealBody(BaseModel):
    real: RealHint | None = None


def _load(project_id: str, scene_id: str) -> tuple[ProjectDoc, Scene]:
    try:
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    try:
        return doc, doc.scene(scene_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="cena não encontrada") from exc


def _editable(scene: Scene) -> None:
    if scene.locked:
        raise HTTPException(status_code=409, detail=LOCKED)


@contextmanager
def _errors() -> Iterator[None]:
    """Erros da mídia em respostas HTTP com a mensagem em português."""
    try:
        yield
    except (BlockedUrl, TooLarge) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OpError as exc:
        message = str(exc)
        if message.startswith("cena não encontrada"):
            raise HTTPException(status_code=404, detail="cena não encontrada") from exc
        if "travada" in message:
            raise HTTPException(status_code=409, detail=LOCKED) from exc
        raise HTTPException(status_code=422, detail=message) from exc


def _commit_media(project_id: str, scene_id: str, media: MediaRef, warnings: list[str], rebuild: bool) -> dict[str, Any]:
    doc, _ = _load(project_id, scene_id)  # de novo: o download pode ter demorado
    with _errors():
        new_doc = media_edit.attach_media(doc, scene_id, media)
    if media.kind == "news" and new_doc.scene(scene_id).kind != "image":
        warnings = [*warnings, NEWS_ONLY_IMAGE]
    label = media_edit.scene_label(new_doc, scene_id)
    version = service.commit(project_id, new_doc, f"{label}: mídia real anexada ({SOURCE_LABELS.get(media.source, media.source)})")
    return {"version": version, "media": media.model_dump(mode="json"), "warnings": list(dict.fromkeys(warnings)),
            **_rebuild(project_id, new_doc, rebuild)}


@router.get("/{project_id}/scenes/{scene_id}/media/candidates")
def media_candidates(project_id: str, scene_id: str, q: str = "", refresh: bool = False) -> dict[str, Any]:
    """Fotos livres para a cena: a busca `q` (em inglês) ou a da sugestão do storyboard."""
    doc, scene = _load(project_id, scene_id)
    query = " ".join(q.split())[:120] or (" ".join(scene.real.query.split()) if scene.real is not None else "")
    if not query:
        return media_search.SearchResult(query="").to_dict()
    style = get_style(doc.style_id, doc.style_overrides)
    orientation = media_search.orientation_for(style.format.width, style.format.height)
    return media_search.search(query, orientation=orientation, refresh=refresh).to_dict()


@router.post("/{project_id}/scenes/{scene_id}/media")
def attach_media(project_id: str, scene_id: str, body: AttachBody) -> dict[str, Any]:
    """Anexa um candidato da busca ou o que um link aponta (página ou imagem)."""
    doc, scene = _load(project_id, scene_id)
    _editable(scene)
    with _errors():
        if body.via == "candidate":
            if not (body.candidate_id or "").strip():
                raise MediaError("Escolha uma foto.")
            media, warnings = real_media.media_from_candidate(body.candidate_id.strip(), project_id,  # type: ignore[union-attr]
                                                              kind=body.kind or "photo")
        else:
            url = (body.url or "").strip()
            if not url:
                raise MediaError("Cole um link.")
            kind = body.kind or (scene.real.kind if scene.real is not None else None)
            media, warnings = real_media.media_from_link(url, project_id, kind=kind, lang=doc.primary_language,
                                                         found=body.found.model_dump() if body.found else None)
    return _commit_media(project_id, scene_id, media, warnings, body.rebuild)


@router.post("/{project_id}/scenes/{scene_id}/media/upload")
def upload_media(
    project_id: str,
    scene_id: str,
    file: Annotated[UploadFile, File()],
    kind: Annotated[RealKind, Form()] = "photo",
    credit: Annotated[str, Form(max_length=500)] = "",
    editorial: Annotated[bool | None, Form()] = None,
    rebuild: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    """Anexa um arquivo enviado (JPG, PNG, WEBP...)."""
    _, scene = _load(project_id, scene_id)
    _editable(scene)
    data = file.file.read(real_media.UPLOAD_MAX_BYTES + 1)
    with _errors():
        media, warnings = real_media.media_from_upload(data, project_id, kind=kind, credit=credit, editorial=editorial)
    return _commit_media(project_id, scene_id, media, warnings, rebuild)


@router.patch("/{project_id}/scenes/{scene_id}/media")
def patch_media(project_id: str, scene_id: str, body: MediaPatchBody) -> dict[str, Any]:
    """Crédito, tipo, enquadramento, foco e campos da notícia (só os enviados mudam)."""
    doc, _ = _load(project_id, scene_id)
    patch = body.model_dump(exclude_unset=True)
    rebuild = bool(patch.pop("rebuild", False))
    with _errors():
        new_doc = media_edit.update_media(doc, scene_id, patch)
    media = new_doc.scene(scene_id).visual.media
    assert media is not None
    version = service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: mídia real editada")
    return {"version": version, "media": media.model_dump(mode="json"), "warnings": [],
            **_rebuild(project_id, new_doc, rebuild)}


@router.delete("/{project_id}/scenes/{scene_id}/media")
def delete_media(project_id: str, scene_id: str, rebuild: bool = False) -> dict[str, Any]:
    """Tira a mídia real (a cena volta à imagem de reserva; o arquivo fica guardado)."""
    doc, _ = _load(project_id, scene_id)
    with _errors():
        new_doc = media_edit.remove_media(doc, scene_id)
    version = service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: mídia real removida")
    return {"version": version, **_rebuild(project_id, new_doc, rebuild)}


@router.put("/{project_id}/scenes/{scene_id}/real")
def put_real(project_id: str, scene_id: str, body: RealBody) -> dict[str, Any]:
    """Troca a sugestão de mídia real do storyboard (null dispensa)."""
    doc, _ = _load(project_id, scene_id)
    with _errors():
        new_doc = media_edit.set_real(doc, scene_id, body.real)
    action = "dispensada" if body.real is None else "editada"
    version = service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: sugestão de mídia real {action}")
    return {"version": version}


@router.get("/{project_id}/media/credits")
def get_media_credits(project_id: str, lang: str | None = None) -> dict[str, Any]:
    """O que vai no fim da descrição ao publicar ("Créditos das imagens")."""
    try:
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    return media_credits(doc, lang or doc.primary_language)
