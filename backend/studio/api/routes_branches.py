"""Rotas dos ramos A/B de versões: criar, editar por instrução, comparar, gerar, publicar e escolher o vencedor."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.core.agent import editor
from studio.core.project import branches as br
from studio.core.project import service
from studio.core.project.ops import OpError, apply_ops, parse_ops
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Publication
from studio.providers.registry import ChainFailed
from studio.workers.analytics import ab_versions
from studio.workers.jobs import get_runner
from studio.workers.pipeline.build import branch_outputs_dir
from studio.workers.publish.package import build_package

router = APIRouter(prefix="/api/projects/{project_id}/ab", tags=["versões A/B"])


class CreateBranch(BaseModel):
    from_number: int | None = None
    note: str = Field(default="", max_length=300)


class ProposeBody(BaseModel):
    instruction: str = Field(min_length=2, max_length=4000)
    scope_scene_ids: list[str] | None = None
    lang: str | None = None


class ApplyBody(BaseModel):
    ops: list[dict[str, Any]]
    summary: str = ""
    rebuild: bool = True


class OpsBody(BaseModel):
    ops: list[dict[str, Any]]
    message: str = "Edição"


class BuildBody(BaseModel):
    branches: list[str] = Field(default_factory=lambda: [br.MAIN], min_length=1, max_length=6)
    mode: Literal["preview", "final"] = "preview"
    langs: list[str] | None = None


class MergeBody(BaseModel):
    segment_ids: list[str] = []
    parts: list[Literal["titulo", "estilo"]] = []


class YouTubeBody(BaseModel):
    lang: str | None = None
    privacy: Literal["private", "unlisted", "public", "scheduled"] = "private"
    publish_at: str | None = None
    category_id: str = "24"
    captions: bool = True
    localize: bool = False
    ignore_checks: bool = False


class PackageBody(BaseModel):
    lang: str | None = None
    platforms: list[str] = ["youtube", "tiktok", "instagram"]
    planned_at: datetime | None = None


class LinkBody(BaseModel):
    branch: str = br.MAIN
    video: str = Field(min_length=6, max_length=300, description="link ou id do vídeo no YouTube")
    lang: str | None = None


_ID = re.compile(r"^[\w-]{6,20}$")


def youtube_id(value: str) -> str:
    """Id do vídeo a partir do link (youtu.be/ID, watch?v=ID, /shorts/ID, /live/ID) ou do próprio id."""
    value = value.strip()
    if _ID.match(value):
        return value
    url = urlparse(value if "://" in value else f"https://{value}")
    host = (url.hostname or "").lower()
    candidate = ""
    if host.endswith("youtu.be"):
        candidate = url.path.strip("/").split("/")[0]
    elif host.endswith("youtube.com"):
        candidate = (parse_qs(url.query).get("v") or [""])[0]
        parts = [p for p in url.path.split("/") if p]
        if not candidate and len(parts) >= 2 and parts[0] in ("shorts", "live", "embed"):
            candidate = parts[1]
    if not _ID.match(candidate):
        raise ValueError("não reconheci o vídeo: cole o link do YouTube (youtu.be/… ou youtube.com/watch?v=…) ou o id")
    return candidate


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, service.ProjectNotFound | br.BranchNotFound):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, service.BranchNotOpen):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=400, detail=str(exc))


def _branch(project_id: str, name: str) -> dict[str, Any]:
    try:
        return br.get_branch(project_id, name)
    except (service.ProjectNotFound, br.BranchNotFound) as exc:
        raise _http(exc) from exc


def _open(project_id: str, name: str) -> dict[str, Any]:
    info = _branch(project_id, name)
    if not info["is_main"] and info["status"] != br.OPEN:
        raise HTTPException(status_code=409, detail=f"a versão {info['letter']} já foi fechada ({info['status']})")
    return info


def _rebuild(project_id: str, name: str, rebuild: bool) -> dict[str, Any]:
    """Depois da edição: prévia da versão editada (ou o aviso de por que ela espera)."""
    from studio.api.routes_projects import _preview_blocker

    if not rebuild:
        return {"job": None, "notice": None}
    notice = _preview_blocker(project_id, br.branch_doc(project_id, name))
    job = None if notice else get_runner().submit("ab_build", {"branches": [name], "mode": "preview"}, project_id)
    return {"job": job, "notice": notice}


@router.get("")
def overview(project_id: str) -> dict[str, Any]:
    """A principal (A), os ramos e, para os ramos publicados, a comparação dos números."""
    try:
        main = br.main_dict(project_id)
        items = br.list_branches(project_id)
    except service.ProjectNotFound as exc:
        raise _http(exc) from exc
    try:
        published = ab_versions.reading(project_id)
    except Exception as exc:  # noqa: BLE001 - os números são extras: a tela dos ramos abre mesmo sem eles
        published = []
        main["reading_error"] = str(exc)
    return {"main": main, "branches": items, "published": published}


@router.post("/branches")
def create_branch(project_id: str, body: CreateBranch) -> dict[str, Any]:
    try:
        return br.create_branch(project_id, from_number=body.from_number, note=body.note)
    except service.ProjectNotFound as exc:
        raise _http(exc) from exc
    except ValueError as exc:
        raise _http(exc) from exc


@router.get("/compare")
def compare(project_id: str, b: str, a: str = br.MAIN, lang: str | None = None) -> dict[str, Any]:
    _branch(project_id, a)
    _branch(project_id, b)
    return br.compare(project_id, a, b, lang)


@router.post("/{name}/edit/propose")
def propose(project_id: str, name: str, body: ProposeBody) -> dict[str, Any]:
    _open(project_id, name)
    try:
        return editor.propose_edit(project_id, body.instruction, scope_scene_ids=body.scope_scene_ids, lang=body.lang,
                                   branch=name)
    except OpError as exc:
        raise HTTPException(status_code=422, detail=f"o agente propôs algo inválido: {exc}") from exc
    except ChainFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{name}/edit/apply")
def apply(project_id: str, name: str, body: ApplyBody) -> dict[str, Any]:
    info = _open(project_id, name)
    try:
        version = editor.apply_edit(project_id, body.ops,
                                    message=f"Versão {info['letter']}: {body.summary or 'edição por instrução'}"
                                    if not info["is_main"] else body.summary or "Edição por instrução",
                                    branch=name)
    except service.BranchNotOpen as exc:
        raise _http(exc) from exc
    except (OpError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"version": version, **_rebuild(project_id, name, body.rebuild)}


@router.post("/{name}/ops")
def operations(project_id: str, name: str, body: OpsBody) -> dict[str, Any]:
    info = _open(project_id, name)
    doc = br.branch_doc(project_id, name)
    try:
        new_doc = apply_ops(doc, parse_ops(body.ops), get_style(doc.style_id))
    except (OpError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    message = body.message if info["is_main"] else f"Versão {info['letter']}: {body.message}"
    try:
        return {"version": service.commit(project_id, new_doc, message, branch=name)}
    except service.BranchNotOpen as exc:
        raise _http(exc) from exc


@router.post("/build")
def build(project_id: str, body: BuildBody) -> dict[str, Any]:
    """Prévia ou vídeo final das versões pedidas (um job só, uma versão depois da outra)."""
    for name in body.branches:
        _open(project_id, name)
    return get_runner().submit("ab_build", body.model_dump(), project_id)


@router.get("/{name}/timeline")
def timeline(project_id: str, name: str, lang: str | None = None) -> dict[str, Any]:
    info = _branch(project_id, name)
    doc = service.get_doc(project_id, info["head"])
    lang = lang or doc.primary_language
    path = branch_outputs_dir(get_settings().outputs_dir, project_id, name) / lang / "timeline.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"gere a prévia da versão {info['letter']} primeiro")
    return json.loads(path.read_text(encoding="utf-8"))


@router.post("/{name}/merge")
def merge(project_id: str, name: str, body: MergeBody) -> dict[str, Any]:
    _branch(project_id, name)
    try:
        version = br.merge(project_id, name, segment_ids=body.segment_ids, parts=list(body.parts))
    except (service.BranchNotOpen, ValueError) as exc:
        raise _http(exc) from exc
    return {"version": version, "branch": br.get_branch(project_id, name)}


@router.post("/{name}/promote")
def promote(project_id: str, name: str) -> dict[str, Any]:
    _branch(project_id, name)
    try:
        version = br.promote(project_id, name)
    except (service.BranchNotOpen, ValueError) as exc:
        raise _http(exc) from exc
    return {"version": version, "branch": br.get_branch(project_id, name)}


@router.post("/{name}/discard")
def discard(project_id: str, name: str) -> dict[str, Any]:
    _branch(project_id, name)
    try:
        return br.discard(project_id, name)
    except service.BranchNotOpen as exc:
        raise _http(exc) from exc


@router.post("/{name}/publish/youtube")
def publish_youtube(project_id: str, name: str, body: YouTubeBody) -> dict[str, Any]:
    """Envia o vídeo final da versão ao YouTube.

    A conferência antes de publicar (ADR 0003) olha a principal: para a principal, use a rota de sempre; um ramo só
    sai como não privado com `ignore_checks` (você confirma que conferiu o vídeo).
    """
    from studio.workers.publish import youtube as yt

    info = _open(project_id, name)
    if info["is_main"]:
        raise HTTPException(status_code=400, detail="a versão A é publicada pela aba Vídeo e publicação")
    if not (info["last_final"] or {}).get("langs"):
        raise HTTPException(status_code=400, detail=f"renderize o vídeo final da versão {info['letter']} antes")
    if body.privacy != "private" and not body.ignore_checks:
        raise HTTPException(
            status_code=409,
            detail=(f"A conferência antes de publicar ainda não roda nos ramos. Assista ao vídeo da versão "
                    f"{info['letter']} e confirme 'publicar mesmo assim', ou envie como privado."),
        )
    try:
        yt.account_for_channel(service.get_project_row(project_id)["channel_id"])
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = {**body.model_dump(exclude={"ignore_checks"}), "branch": name}
    return get_runner().submit("publish_youtube", payload, project_id)


@router.post("/{name}/publish/package")
def publish_package(project_id: str, name: str, body: PackageBody) -> dict[str, Any]:
    info = _branch(project_id, name)
    doc = service.get_doc(project_id, info["head"])
    try:
        return build_package(project_id, body.lang or doc.primary_language, body.platforms, body.planned_at,
                             branch=None if info["is_main"] else name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/link")
def link_video(project_id: str, body: LinkBody) -> dict[str, Any]:
    """Liga um vídeo já publicado no YouTube (à mão ou pelo pacote) a uma versão, para entrar no experimento."""
    info = _branch(project_id, body.branch)
    try:
        video_id = youtube_id(body.video)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    doc = service.get_doc(project_id, info["head"])
    tag = {"versao": info["letter"], "branch": info["name"], "version": info["head"]}
    with session_scope() as s:
        row = (
            s.query(Publication)
            .filter(Publication.project_id == project_id, Publication.platform == "youtube",
                    Publication.external_id == video_id)
            .one_or_none()
        )
        if row is None:
            row = Publication(id=new_id("pub"), project_id=project_id, platform="youtube",
                              language=body.lang or doc.primary_language, driver="manual_helper", status="published",
                              external_id=video_id, url=f"https://youtu.be/{video_id}", details={})
            s.add(row)
        row.details = {**(row.details or {}), **tag}
        pub_id = row.id
    created = ab_versions.sync_experiments(project_id)
    return {"publication_id": pub_id, "youtube_id": video_id, "versao": info["letter"], "experiments_created": created}
