"""Rotas do vídeo IA nas cenas-chave: estado e custo, marcar/desmarcar, sugerir e gerar (com o custo confirmado)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.api.routes_projects import _rebuild
from studio.core.project import service
from studio.core.project.document import CLIP_SECONDS_MAX, CLIP_SECONDS_MIN, ProjectDoc
from studio.core.project.media_edit import scene_label
from studio.core.styles.library import get_style
from studio.workers.jobs import get_runner, list_jobs
from studio.workers.pipeline import video_clips
from studio.workers.pipeline.build import Builder
from studio.workers.pipeline.video_clips import ClipError

router = APIRouter(prefix="/api/projects", tags=["vídeo IA"])


class VideoBody(BaseModel):
    enabled: bool = True
    prompt: str | None = Field(default=None, max_length=600)
    seconds: int | None = Field(default=None, ge=CLIP_SECONDS_MIN, le=CLIP_SECONDS_MAX)
    rebuild: bool = False


class SuggestBody(BaseModel):
    apply: bool = False


class GenerateBody(BaseModel):
    confirm_cost_usd: float = Field(ge=0, le=1000)
    """O valor máximo que o usuário viu e confirmou ("até US$ X")."""


def _doc(project_id: str) -> ProjectDoc:
    try:
        return service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


def _builder(project_id: str) -> Builder:
    """Builder só para consultas (não roda nada): o mesmo que o plano usa."""
    return Builder(project_id, mode="preview")


def _clip_error(exc: ClipError) -> HTTPException:
    message = str(exc)
    if message == "cena não encontrada":
        return HTTPException(status_code=404, detail=message)
    if "travada" in message or "limite" in message or "mudou" in message:
        return HTTPException(status_code=409, detail=message)
    return HTTPException(status_code=422, detail=message)


@router.get("/{project_id}/video")
def video_status(project_id: str) -> dict[str, Any]:
    """Cenas com vídeo IA, o que já está pronto, o custo do que falta e as sugestões."""
    _doc(project_id)
    status = video_clips.estimate(_builder(project_id))
    running = [j for j in list_jobs(project_id, limit=20) if j["kind"] == "video_clips" and j["status"] in ("queued", "running")]
    status["job"] = running[0] if running else None
    return status


@router.put("/{project_id}/scenes/{scene_id}/video")
def put_video(project_id: str, scene_id: str, body: VideoBody) -> dict[str, Any]:
    """Marca ou desmarca a cena-chave e ajusta o movimento e a duração do clipe."""
    doc = _doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    try:
        new_doc = video_clips.set_video(doc, style, scene_id, enabled=body.enabled, prompt=body.prompt,
                                        seconds=body.seconds)
    except ClipError as exc:
        raise _clip_error(exc) from exc
    label = scene_label(new_doc, scene_id)
    action = "vídeo IA ajustado" if body.enabled and doc.scene(scene_id).kind == "video" else (
        "marcada para vídeo IA" if body.enabled else "vídeo IA tirado")
    version = service.commit(project_id, new_doc, f"{label}: {action}")
    scene = new_doc.scene(scene_id)
    return {"version": version, "kind": scene.kind, "clip": scene.clip.model_dump() if scene.clip else None,
            **_rebuild(project_id, new_doc, body.rebuild)}


@router.post("/{project_id}/video/suggest")
def suggest_video(project_id: str, body: SuggestBody) -> dict[str, Any]:
    """Sugere cenas-chave (as do storyboard primeiro). Com `apply`, já marca as sugeridas."""
    doc = _doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    picks = video_clips.suggest(doc, style)
    version = None
    if body.apply and picks:
        new_doc = doc
        for scene_id in picks:
            new_doc = video_clips.set_video(new_doc, style, scene_id, enabled=True)
        names = ", ".join(scene_label(new_doc, s) for s in picks)
        version = service.commit(project_id, new_doc, f"Vídeo IA sugerido: {names}")
    return {"scene_ids": picks, "applied": bool(body.apply and picks), "version": version}


@router.post("/{project_id}/video/generate")
def generate_video(project_id: str, body: GenerateBody) -> dict[str, Any]:
    """Gera os clipes que faltam. Recusa se o custo passou do confirmado (outra cena marcada depois, outro modelo)."""
    _doc(project_id)
    status = video_clips.estimate(_builder(project_id))
    try:
        video_clips.check_confirmed(status, body.confirm_cost_usd)
    except ClipError as exc:
        raise _clip_error(exc) from exc
    if any(j["kind"] == "video_clips" and j["status"] in ("queued", "running") for j in list_jobs(project_id, limit=20)):
        raise HTTPException(status_code=409, detail="Os clipes já estão sendo gerados.")
    job = get_runner().submit("video_clips", {"max_cost_usd": round(body.confirm_cost_usd, 4)}, project_id)
    return {"job": job, "estimated_cost_usd": status["estimated_cost_usd"], "max_cost_usd": status["max_cost_usd"]}
