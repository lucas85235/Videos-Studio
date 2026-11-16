"""Rotas dos Shorts cortados do vídeo longo (ADR 0002, bloco B).

- `GET /api/projects/{id}/shorts`: segmentos do longo, sugestões, Shorts já cortados e, no filho, o longo de origem.
- `POST /api/projects/{id}/shorts/suggest`: job `shorts_suggest`.
- `POST /api/projects/{id}/shorts`: cria o Short (projeto filho).
- `POST /api/shorts/check`: avisos das regras e título final (com a hashtag do canal).
- `GET/PUT /api/projects/{id}/shorts/frames`: reenquadramento das imagens 16:9 no Short (foco e modo por cena).
- `POST /api/projects/{id}/shorts/frames/{scene_id}/vertical`: gera a imagem da cena de novo, em 9:16.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.core.project import service
from studio.workers.jobs import get_runner
from studio.workers.shorts.derive import ShortsError, children_of, derive_short, segments_overview
from studio.workers.shorts.reframe import frames_overview, update_frames, vertical_image
from studio.workers.shorts.rules import ShortTooLong, channel_rules, check_short, final_title

router = APIRouter(prefix="/api", tags=["shorts"])

Tipo = Literal["normal", "lista", "iceberg"]


class SuggestBody(BaseModel):
    lang: str | None = Field(default=None, max_length=20)


class CreateShort(BaseModel):
    ranges: list[list[str]] = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200)
    hook: str = Field(default="", max_length=200)
    tipo: Tipo = "normal"
    lang: str | None = Field(default=None, max_length=20)


FrameMode = Literal["crop", "blur"]


class FrameEdit(BaseModel):
    id: str = Field(min_length=1, max_length=40)
    x: float | None = Field(default=None, ge=0, le=1)
    y: float | None = Field(default=None, ge=0, le=1)
    mode: Literal["crop", "blur", "default"] | None = None
    reset: bool = False
    restore: bool = False
    """Desfaz o pedido de imagem vertical (volta à imagem 16:9)."""


class FramesBody(BaseModel):
    default_mode: FrameMode | None = None
    scenes: list[FrameEdit] = Field(default_factory=list, max_length=200)


class CheckBody(BaseModel):
    title: str = Field(default="", max_length=300)
    duration_s: float = Field(ge=0)
    tipo: Tipo = "normal"
    channel_id: str | None = Field(default=None, max_length=32)


def _http(exc: ShortsError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _row(project_id: str) -> dict[str, Any]:
    try:
        return service.get_project_row(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc


@router.get("/projects/{project_id}/shorts")
def project_shorts(project_id: str, lang: str | None = None) -> dict[str, Any]:
    row = _row(project_id)
    settings = row.get("settings") or {}
    limits, hashtag = channel_rules(row.get("channel_id"))
    out: dict[str, Any] = {
        "lang": lang,
        "segments": [],
        "source": None,
        "stale": False,
        "gap_s": 0.0,
        "lead_in_s": 0.0,
        "tail_out_s": 0.0,
        "blocker": None,
        "suggestions": settings.get("shorts_suggestions"),
        "children": children_of(project_id),
        "parent": None,
        "short_of": settings.get("short_of"),
        "hashtag": hashtag,
        "limits": {"target": list(limits.target), "max_s": limits.max_s, "max_list_s": limits.max_list_s,
                   "title_max": limits.title_max},
    }
    parent_id = row.get("parent_id")
    if parent_id:
        try:
            parent = service.get_project_row(parent_id)
            out["parent"] = {"project_id": parent_id, "title": parent["title"], "exists": True}
        except service.ProjectNotFound:
            out["parent"] = {"project_id": parent_id, "title": "", "exists": False}
        return out
    try:
        overview = segments_overview(project_id, lang)
    except ShortsError as exc:
        if exc.status != 409:
            raise _http(exc) from exc
        out["blocker"] = exc.message
        return out
    out.update(overview)
    return out


@router.post("/projects/{project_id}/shorts/suggest")
def suggest_shorts(project_id: str, body: SuggestBody | None = None) -> dict[str, Any]:
    row = _row(project_id)
    if row.get("parent_id"):
        raise HTTPException(status_code=409, detail="Este projeto já é um Short cortado de outro vídeo")
    lang = body.lang if body else None
    try:
        segments_overview(project_id, lang)  # sem timeline: 409 já aqui, não no job
    except ShortsError as exc:
        raise _http(exc) from exc
    return get_runner().submit("shorts_suggest", {"lang": lang} if lang else {}, project_id)


@router.post("/projects/{project_id}/shorts")
def create_short(project_id: str, body: CreateShort) -> dict[str, Any]:
    _row(project_id)
    try:
        return derive_short(project_id, body.ranges, body.title, body.hook, body.tipo, body.lang)
    except ShortsError as exc:
        raise _http(exc) from exc


@router.post("/shorts/check")
def check(body: CheckBody) -> dict[str, Any]:
    limits, hashtag = channel_rules(body.channel_id)
    try:
        warnings = check_short(body.title, body.duration_s, body.tipo, limits)
    except ShortTooLong as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"warnings": warnings, "final_title": final_title(body.title, hashtag)}



@router.get("/projects/{project_id}/shorts/frames")
def short_frames(project_id: str) -> dict[str, Any]:
    try:
        return frames_overview(project_id)
    except ShortsError as exc:
        raise _http(exc) from exc


@router.put("/projects/{project_id}/shorts/frames")
def save_frames(project_id: str, body: FramesBody) -> dict[str, Any]:
    try:
        return update_frames(project_id, body.default_mode, [e.model_dump() for e in body.scenes])
    except ShortsError as exc:
        raise _http(exc) from exc


@router.post("/projects/{project_id}/shorts/frames/{scene_id}/vertical")
def vertical_scene_image(project_id: str, scene_id: str) -> dict[str, Any]:
    try:
        return vertical_image(project_id, scene_id)
    except ShortsError as exc:
        raise _http(exc) from exc
