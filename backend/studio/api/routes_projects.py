"""Rotas de projetos: criação, documento, versões, jobs, prévia, edição por instrução e publicação."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.core.agent import editor
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.project.ops import OpError, apply_ops, parse_ops
from studio.core.styles.library import StyleNotFound, get_style
from studio.core.styles.schema import policy_warnings
from studio.providers.registry import ChainFailed
from studio.workers.jobs import get_runner, list_jobs
from studio.workers.pipeline.build import plan
from studio.workers.publish.package import build_package, list_publications

router = APIRouter(prefix="/api/projects", tags=["projetos"])

JOB_KINDS = {"script", "storyboard", "translate", "metadata", "preview", "render", "autopilot"}


class CreateProject(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    style_id: str
    topic: str = ""
    brief: str = ""
    channel_id: str | None = None
    languages: list[str] | None = None
    autopilot: bool = False


class DocUpdate(BaseModel):
    doc: dict[str, Any]
    message: str = "Edição manual"


class OpsRequest(BaseModel):
    ops: list[dict[str, Any]]
    message: str = "Edição"
    rebuild: bool = False


class JobRequest(BaseModel):
    kind: str
    payload: dict[str, Any] = {}


class ProposeRequest(BaseModel):
    instruction: str = Field(min_length=2, max_length=4000)
    scope_scene_ids: list[str] | None = None
    lang: str | None = None


class ApplyEditRequest(BaseModel):
    ops: list[dict[str, Any]]
    summary: str = ""
    rebuild: bool = True


class CheckoutRequest(BaseModel):
    number: int


class SettingsPatch(BaseModel):
    gates: dict[str, bool] | None = None


class ApproveRequest(BaseModel):
    stage: str
    continue_pipeline: bool = True


class PackageRequest(BaseModel):
    lang: str | None = None
    platforms: list[str] = ["youtube", "tiktok", "instagram"]
    planned_at: datetime | None = None  # horário planejado para postar (vai para a agenda)


def _not_found(exc: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail=str(exc))


def _outputs(project_id: str, row: dict[str, Any]) -> dict[str, Any]:
    settings = row.get("settings") or {}
    return {"preview": settings.get("last_preview"), "final": settings.get("last_final")}


@router.get("")
def list_all() -> list[dict[str, Any]]:
    return service.list_projects()


@router.post("")
def create(body: CreateProject) -> dict[str, Any]:
    try:
        doc = service.create_project(
            title=body.title,
            style_id=body.style_id,
            topic=body.topic,
            brief=body.brief,
            channel_id=body.channel_id,
            languages=body.languages,
        )
    except StyleNotFound as exc:
        raise HTTPException(status_code=400, detail=f"estilo não encontrado: {exc}") from exc
    job = None
    if body.autopilot:
        job = get_runner().submit("autopilot", {}, doc.id)
    return {"project": service.get_project_row(doc.id), "doc": doc.model_dump(mode="json"), "job": job}


@router.get("/{project_id}")
def detail(project_id: str) -> dict[str, Any]:
    try:
        row = service.get_project_row(project_id)
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise _not_found(exc) from exc
    try:
        style = get_style(doc.style_id, doc.style_overrides)
        style_data = style.model_dump(mode="json")
        warnings = policy_warnings(style)
    except StyleNotFound:
        style_data, warnings = None, ["O estilo deste projeto não existe mais."]
    return {
        "project": row,
        "doc": doc.model_dump(mode="json"),
        "style": style_data,
        "warnings": warnings,
        "versions": service.list_versions(project_id)[:30],
        "jobs": list_jobs(project_id, limit=20),
        "outputs": _outputs(project_id, row),
        "publications": list_publications(project_id),
    }


@router.delete("/{project_id}")
def delete(project_id: str) -> dict[str, Any]:
    return {"deleted": service.delete_project(project_id)}


@router.put("/{project_id}/doc")
def update_doc(project_id: str, body: DocUpdate) -> dict[str, Any]:
    try:
        doc = ProjectDoc.model_validate(body.doc)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail=f"documento inválido: {exc}") from exc
    if doc.id != project_id:
        raise HTTPException(status_code=400, detail="o documento pertence a outro projeto")
    version = service.commit(project_id, doc, body.message)
    return {"version": version}


@router.post("/{project_id}/ops")
def apply_operations(project_id: str, body: OpsRequest) -> dict[str, Any]:
    doc = service.get_doc(project_id)
    try:
        new_doc = apply_ops(doc, parse_ops(body.ops), get_style(doc.style_id))
    except (OpError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    version = service.commit(project_id, new_doc, body.message)
    job = get_runner().submit("preview", {}, project_id) if body.rebuild else None
    return {"version": version, "job": job}


@router.get("/{project_id}/versions")
def versions(project_id: str) -> list[dict[str, Any]]:
    return service.list_versions(project_id)


@router.post("/{project_id}/checkout")
def checkout(project_id: str, body: CheckoutRequest) -> dict[str, Any]:
    try:
        return {"version": service.checkout(project_id, body.number)}
    except service.ProjectNotFound as exc:
        raise _not_found(exc) from exc


@router.patch("/{project_id}/settings")
def patch_settings(project_id: str, body: SettingsPatch) -> dict[str, Any]:
    row = service.get_project_row(project_id)
    patch: dict[str, Any] = {}
    if body.gates is not None:
        patch["gates"] = {**row["settings"].get("gates", {}), **body.gates}
    return service.update_settings(project_id, patch)


@router.post("/{project_id}/approve")
def approve(project_id: str, body: ApproveRequest) -> dict[str, Any]:
    doc = service.get_doc(project_id)
    if body.stage == "script":
        if not doc.segments:
            raise HTTPException(status_code=400, detail="não há roteiro para aprovar")
        doc.script_approved = True
        version = service.commit(project_id, doc, "Roteiro aprovado")
        job = get_runner().submit("autopilot", {"skip_script": True}, project_id) if body.continue_pipeline else None
    elif body.stage == "preview":
        doc.preview_approved = True
        version = service.commit(project_id, doc, "Prévia aprovada")
        job = get_runner().submit("render", {}, project_id) if body.continue_pipeline else None
    else:
        raise HTTPException(status_code=400, detail="etapa inválida (use script ou preview)")
    return {"version": version, "job": job}


@router.post("/{project_id}/jobs")
def start_job(project_id: str, body: JobRequest) -> dict[str, Any]:
    if body.kind not in JOB_KINDS:
        raise HTTPException(status_code=400, detail=f"tipo de job inválido: {body.kind}")
    service.get_project_row(project_id)
    return get_runner().submit(body.kind, body.payload, project_id)


@router.get("/{project_id}/timeline")
def timeline(project_id: str, lang: str | None = None) -> dict[str, Any]:
    doc = service.get_doc(project_id)
    lang = lang or doc.primary_language
    path = get_settings().outputs_dir / project_id / lang / "timeline.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="gere a prévia primeiro")
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/{project_id}/plan")
def build_plan(project_id: str) -> dict[str, Any]:
    return plan(project_id)


@router.post("/{project_id}/edit/propose")
def propose(project_id: str, body: ProposeRequest) -> dict[str, Any]:
    try:
        return editor.propose_edit(project_id, body.instruction, scope_scene_ids=body.scope_scene_ids, lang=body.lang)
    except OpError as exc:
        raise HTTPException(status_code=422, detail=f"o agente propôs algo inválido: {exc}") from exc
    except ChainFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{project_id}/edit/apply")
def apply_edit(project_id: str, body: ApplyEditRequest) -> dict[str, Any]:
    try:
        version = editor.apply_edit(project_id, body.ops, message=body.summary or "Edição por instrução")
    except (OpError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = get_runner().submit("preview", {}, project_id) if body.rebuild else None
    return {"version": version, "job": job}


@router.post("/{project_id}/publish/package")
def publish_package(project_id: str, body: PackageRequest) -> dict[str, Any]:
    doc = service.get_doc(project_id)
    try:
        return build_package(project_id, body.lang or doc.primary_language, body.platforms, body.planned_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{project_id}/publications")
def publications(project_id: str) -> list[dict[str, Any]]:
    return list_publications(project_id)
