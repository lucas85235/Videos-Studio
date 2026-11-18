"""Rotas de projetos: criação, documento, versões, jobs, prévia, edição por instrução e publicação."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.core.agent import editor
from studio.core.glossary import glossary_of
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.project.ops import OpError, apply_ops, parse_ops
from studio.core.styles.library import DEFAULT_STYLE_ID, StyleNotFound, get_style
from studio.core.styles.schema import policy_warnings
from studio.db import session_scope
from studio.models import Channel
from studio.providers.registry import ChainFailed
from studio.workers.jobs import get_runner, list_jobs
from studio.workers.pipeline import dubbing
from studio.workers.pipeline.authoring import glossary_issues
from studio.workers.pipeline.build import DIRECTION_MISSING, plan, scenes_without_direction
from studio.workers.publish.package import build_dub_package, build_package, list_publications

router = APIRouter(prefix="/api/projects", tags=["projetos"])

JOB_KINDS = {"script", "storyboard", "translate", "metadata", "preview", "render", "autopilot", "media_find", "package",
             "package_thumbs"}


class VoiceChoice(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    voice: str = Field(min_length=1, max_length=120)


class CreateProject(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    style_id: str | None = None
    """Sem estilo: usa o estilo padrão do canal ou, sem canal, `DEFAULT_STYLE_ID`."""
    topic: str = ""
    brief: str = ""
    channel_id: str | None = None
    languages: list[str] | None = None
    autopilot: bool = False
    voice: VoiceChoice | None = None
    seed_candidate: bool = False
    """O título de trabalho entra como 1º candidato manual do pacote (tela Novo projeto, com título digitado)."""


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


def _preview_blocker(project_id: str, doc: ProjectDoc) -> str | None:
    """Por que a edição não deve atualizar a prévia sozinha (None: pode atualizar).

    A prévia gera as imagens que faltam: com as cenas esperando a conferência, ou sem direção de arte depois de
    trocar o estilo, ela gastaria cota com imagens que ainda podem mudar.
    """
    if scenes_without_direction(doc):
        return DIRECTION_MISSING
    gates = service.get_project_row(project_id)["settings"].get("gates", {})
    if gates.get("storyboard", False) and doc.scenes and not doc.storyboard_approved:
        return "Edição salva. Aprove as cenas para gerar as imagens e atualizar a prévia."
    return None


def _rebuild(project_id: str, doc: ProjectDoc, rebuild: bool) -> dict[str, Any]:
    """Job da prévia depois de uma edição, ou o aviso de por que ela não foi atualizada."""
    if not rebuild:
        return {"job": None, "notice": None}
    notice = _preview_blocker(project_id, doc)
    return {"job": None if notice else get_runner().submit("preview", {}, project_id), "notice": notice}


def _outputs(project_id: str, row: dict[str, Any]) -> dict[str, Any]:
    settings = row.get("settings") or {}
    return {"preview": settings.get("last_preview"), "final": settings.get("last_final")}


@router.get("")
def list_all() -> list[dict[str, Any]]:
    return service.list_projects()


def _default_style_id(channel_id: str | None) -> str:
    if channel_id:
        with session_scope() as s:
            row = s.get(Channel, channel_id)
            if row is not None and row.default_style_id:
                return row.default_style_id
    return DEFAULT_STYLE_ID


@router.post("")
def create(body: CreateProject) -> dict[str, Any]:
    try:
        doc = service.create_project(
            title=body.title,
            style_id=body.style_id or _default_style_id(body.channel_id),
            topic=body.topic,
            brief=body.brief,
            channel_id=body.channel_id,
            languages=body.languages,
            voice=body.voice.model_dump() if body.voice else None,
            seed_candidate=body.seed_candidate,
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
        "glossary_warnings": _glossary_warnings(doc),
    }


def _glossary_warnings(doc: ProjectDoc) -> list[dict[str, str]]:
    """Termos do glossário do canal que a tradução atual não respeitou (refeito a cada abertura: corrigir o texto à
    mão tira o aviso)."""
    if not doc.channel_id or len(doc.languages) < 2:
        return []
    with session_scope() as s:
        row = s.get(Channel, doc.channel_id)
        entries = glossary_of(row.settings) if row is not None else []
    return glossary_issues(doc, entries)


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
    return {"version": version, **_rebuild(project_id, new_doc, body.rebuild)}


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
        old_gates = row["settings"].get("gates", {})
        patch["gates"] = {**old_gates, **body.gates}
        if body.gates.get("storyboard") and not old_gates.get("storyboard", False):
            # Ligar a parada num projeto que já tem prévia não faz as cenas dele voltarem para a conferência.
            doc = service.get_doc(project_id)
            if doc.scenes and not doc.storyboard_approved and row["settings"].get("last_preview"):
                doc.storyboard_approved = True
                service.commit(project_id, doc, "Cenas aprovadas")
    return service.update_settings(project_id, patch)


@router.post("/{project_id}/approve")
def approve(project_id: str, body: ApproveRequest) -> dict[str, Any]:
    if body.stage == "package":
        # Alias de POST /package/approve (trava o título e a thumb escolhidos e segue para o roteiro).
        from studio.api.routes_package import approve_package

        return approve_package(project_id, continue_pipeline=body.continue_pipeline)
    doc = service.get_doc(project_id)
    if body.stage == "script":
        if not doc.segments:
            raise HTTPException(status_code=400, detail="não há roteiro para aprovar")
        doc.script_approved = True
        version = service.commit(project_id, doc, "Roteiro aprovado")
        job = get_runner().submit("autopilot", {"skip_script": True}, project_id) if body.continue_pipeline else None
    elif body.stage == "storyboard":
        if not doc.scenes:
            raise HTTPException(status_code=400, detail="não há cenas para aprovar")
        doc.storyboard_approved = True
        version = service.commit(project_id, doc, "Cenas aprovadas")
        service.set_status(project_id, "storyboard")
        # As cenas existem e estão aprovadas: o piloto segue para a tradução (se faltar) e a prévia.
        job = get_runner().submit("autopilot", {}, project_id) if body.continue_pipeline else None
    elif body.stage == "preview":
        doc.preview_approved = True
        # Aprovar a prévia aprova as cenas que estão nela (senão o projeto voltaria para a conferência das cenas).
        doc.storyboard_approved = doc.storyboard_approved or bool(doc.scenes)
        version = service.commit(project_id, doc, "Prévia aprovada")
        job = get_runner().submit("render", {}, project_id) if body.continue_pipeline else None
    else:
        raise HTTPException(status_code=400, detail="etapa inválida (use package, script, storyboard ou preview)")
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
def build_plan(project_id: str, mode: Literal["preview", "final"] = "final") -> dict[str, Any]:
    """O que a próxima prévia ou renderização vai gravar (narrações que faltam, voz, imagens)."""
    service.get_project_row(project_id)
    return plan(project_id, mode=mode)


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
    return {"version": version, **_rebuild(project_id, service.get_doc(project_id), body.rebuild)}


@router.post("/{project_id}/publish/package")
def publish_package(project_id: str, body: PackageRequest) -> dict[str, Any]:
    doc = service.get_doc(project_id)
    try:
        return build_package(project_id, body.lang or doc.primary_language, body.platforms, body.planned_at)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


class DeliveryRequest(BaseModel):
    delivery: Literal["separate_videos", "multi_audio_track"]


@router.put("/{project_id}/delivery")
def set_delivery(project_id: str, body: DeliveryRequest) -> dict[str, Any]:
    """Como os idiomas saem: um vídeo por idioma (Modo A) ou várias faixas de áudio no mesmo vídeo (Modo B, só
    YouTube). Fica nos ajustes de estilo do projeto (`style_overrides.languages.delivery`)."""
    try:
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise _not_found(exc) from exc
    overrides = dict(doc.style_overrides)
    overrides["languages"] = {**(overrides.get("languages") or {}), "delivery": body.delivery}
    try:
        get_style(doc.style_id, overrides)
    except StyleNotFound as exc:
        raise HTTPException(status_code=400, detail="o estilo deste projeto não existe mais") from exc
    doc.style_overrides = overrides
    label = "faixas de idioma no mesmo vídeo" if body.delivery == "multi_audio_track" else "um vídeo por idioma"
    version = service.commit(project_id, doc, f"Entrega dos idiomas: {label}")
    return {"version": version, "delivery": body.delivery, "over_budget": _over_budget(doc)}


def _over_budget(doc: ProjectDoc) -> dict[str, list[str]]:
    """Trechos traduzidos que passam do tempo do original (só no Modo B; vazio no Modo A)."""
    try:
        style = get_style(doc.style_id, doc.style_overrides)
    except StyleNotFound:
        return {}
    return dubbing.over_budget_report(doc, style) if dubbing.is_multi_track(style, doc) else {}


@router.get("/{project_id}/delivery/over-budget")
def delivery_over_budget(project_id: str) -> dict[str, Any]:
    """Modo B: por idioma, os trechos da tradução que passam do tempo (viram aceleração forte ou pausa no vídeo).
    A rodada que encurta só esses trechos é o job `translate` com `{"shorten": true}`."""
    try:
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise _not_found(exc) from exc
    return {"over_budget": _over_budget(doc)}


@router.post("/{project_id}/publish/dub-package")
def dub_package(project_id: str) -> dict[str, Any]:
    """Pacote para o YouTube Studio com a faixa de áudio, as legendas e o título de cada idioma (Modo B)."""
    try:
        return build_dub_package(project_id)
    except service.ProjectNotFound as exc:
        raise _not_found(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:  # arquivo do pacote aberto em outro programa (Windows)
        raise HTTPException(status_code=409, detail=dubbing.PACKAGE_BUSY) from exc


@router.post("/{project_id}/publish/dub-package/open")
def open_dub_package(project_id: str) -> dict[str, Any]:
    """Abre a pasta do pacote de faixas de idioma no Explorer (o app roda na sua máquina)."""
    import os
    import sys

    try:
        service.get_project_row(project_id)
    except service.ProjectNotFound as exc:
        raise _not_found(exc) from exc
    folder = get_settings().outputs_dir / project_id / "faixas-youtube"
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="gere o pacote de faixas primeiro")
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail=f"abra manualmente: {folder}")
    os.startfile(folder)  # noqa: S606 - pasta local do próprio app
    return {"opened": str(folder)}


@router.get("/{project_id}/publications")
def publications(project_id: str) -> list[dict[str, Any]]:
    return list_publications(project_id)
