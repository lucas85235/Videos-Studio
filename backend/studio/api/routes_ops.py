"""Rotas da operação do canal (ADR 0002, bloco C): resumo do dia, pendências, rotinas, tempo por etapa e cópia do banco."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from studio.workers.jobs import get_runner
from studio.workers.ops import pending, routines
from studio.workers.ops.backup import list_backups
from studio.workers.ops.today import summary
from studio.workers.ops.velocity import report

router = APIRouter(prefix="/api", tags=["operacao"])

HHMM = r"^([01]\d|2[0-3]):[0-5]\d$"


@router.get("/today")
def today() -> dict[str, Any]:
    return summary()


# ----------------------------------------------------------------------------- pendências


class PendingBody(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    where: str = Field(default="", max_length=500)
    channel_id: str | None = None


class PendingPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)
    where: str | None = Field(default=None, max_length=500)
    channel_id: str | None = None
    order: int | None = None


class StatusBody(BaseModel):
    status: Literal["aberta", "feita", "descartada"]


@router.get("/pending")
def list_pending(status: Literal["aberta", "feita", "descartada", "todas"] = "aberta",
                 channel_id: str | None = None) -> list[dict[str, Any]]:
    return pending.list_pending(status, channel_id)


@router.post("/pending")
def create_pending(body: PendingBody) -> dict[str, Any]:
    try:
        return pending.create_pending(body.text, body.where, body.channel_id or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/pending/{pending_id}")
def update_pending(pending_id: str, body: PendingPatch) -> dict[str, Any]:
    fields = set(body.model_fields_set)
    if "text" in fields and body.text is None:
        raise HTTPException(status_code=422, detail="a pendência precisa de um texto")
    try:
        pending.get_pending(pending_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="pendência não encontrada") from exc
    try:
        return pending.update_pending(pending_id, text=body.text, where=body.where, channel_id=body.channel_id,
                                      order=body.order, fields=fields)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pending/{pending_id}/status")
def pending_status(pending_id: str, body: StatusBody) -> dict[str, Any]:
    try:
        return pending.set_status(pending_id, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="pendência não encontrada") from exc


# ----------------------------------------------------------------------------- tempo, rotinas e cópia do banco


@router.get("/ops/velocity")
def velocity(days: int = Query(default=7, ge=1, le=90)) -> dict[str, Any]:
    return report(days=days)


@router.post("/ops/backup")
def backup_now() -> dict[str, Any]:
    return get_runner().submit("backup", {})


@router.get("/ops/backups")
def backups() -> list[dict[str, Any]]:
    return list_backups()


@router.post("/ops/morning")
def morning_now() -> dict[str, Any]:
    job = get_runner().submit("routine_morning", {})
    # Conta como a manhã do dia: o laço não roda de novo (nem pede outra coleta de métricas) no horário configurado.
    routines.update_state(morning_day=routines.local_today(), last_morning_job=job.get("id"))
    return job


class RoutinesBody(BaseModel):
    morning_time: str | None = Field(default=None, pattern=HHMM)
    backup: bool | None = None
    metrics: bool | None = None


@router.get("/ops/routines")
def get_routines() -> dict[str, Any]:
    return routines.public_state()


@router.put("/ops/routines")
def put_routines(body: RoutinesBody) -> dict[str, Any]:
    changes = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set and v is not None}
    if changes:
        routines.update_state(**changes)
    return routines.public_state()
