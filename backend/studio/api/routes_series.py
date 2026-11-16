"""Rotas das séries com piloto automático (episódios até o portão de revisão; nunca publica)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.workers.series import core

router = APIRouter(prefix="/api", tags=["series"])


class SeriesBody(BaseModel):
    channel_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    theme: str = Field(default="", max_length=4000)
    notes: str = Field(default="", max_length=4000)
    format: Literal["", "short", "long"] = ""
    style_id: str | None = Field(default=None, max_length=100)
    cadence_days: int = Field(default=7, ge=1, le=60)
    stock_target: int = Field(default=2, ge=0, le=30)
    max_per_run: int = Field(default=1, ge=1, le=5)
    source: Literal["list", "ideas"] = "list"
    topics: list[str] = Field(default_factory=list)
    idea_filter: str = Field(default="", max_length=200)
    autopilot: bool = False
    active: bool = True


class SeriesPatch(BaseModel):
    channel_id: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    theme: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=4000)
    format: Literal["", "short", "long"] | None = None
    style_id: str | None = Field(default=None, max_length=100)
    cadence_days: int | None = Field(default=None, ge=1, le=60)
    stock_target: int | None = Field(default=None, ge=0, le=30)
    max_per_run: int | None = Field(default=None, ge=1, le=5)
    source: Literal["list", "ideas"] | None = None
    topics: list[str] | None = None
    # A fila que o painel leu ao abrir: se a fila mudou desde então, salvar `topics` dá 409 (não devolve tema usado).
    topics_base: list[str] | None = None
    idea_filter: str | None = Field(default=None, max_length=200)
    autopilot: bool | None = None
    active: bool | None = None


class RunBody(BaseModel):
    # Vazio: completa o estoque da série (até o teto por rodada). Com número: cria exatamente esse tanto.
    count: int | None = Field(default=None, ge=1, le=5)


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="série não encontrada")


@router.get("/series")
def list_series(channel_id: str | None = None) -> list[dict[str, Any]]:
    return core.list_series(channel_id)


@router.post("/series")
def create_series(body: SeriesBody) -> dict[str, Any]:
    try:
        return core.create_series(body.model_dump())
    except core.SeriesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/series/{series_id}")
def get_series(series_id: str) -> dict[str, Any]:
    try:
        return core.get_series(series_id)
    except core.SeriesNotFound as exc:
        raise _not_found() from exc


@router.put("/series/{series_id}")
def update_series(series_id: str, body: SeriesPatch) -> dict[str, Any]:
    data = {k: getattr(body, k) for k in body.model_fields_set}
    # Campo obrigatório mandado vazio: ignora (só o estilo aceita null, que volta ao padrão do canal).
    data = {k: v for k, v in data.items() if v is not None or k == "style_id"}
    try:
        return core.update_series(series_id, data)
    except core.SeriesNotFound as exc:
        raise _not_found() from exc
    except core.SeriesConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except core.SeriesError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/series/{series_id}")
def delete_series(series_id: str) -> dict[str, Any]:
    try:
        core.delete_series(series_id)
    except core.SeriesNotFound as exc:
        raise _not_found() from exc
    return {"deleted": True}


@router.post("/series/{series_id}/run")
def run_series(series_id: str, body: RunBody | None = None) -> dict[str, Any]:
    """Roda o piloto da série agora (mesmo com o piloto desligado). Os episódios param no roteiro para revisão."""
    try:
        result = core.run_series(series_id, manual=True, count=(body.count if body else None))
        return {**result, "series": core.get_series(series_id)}
    except core.SeriesNotFound as exc:
        raise _not_found() from exc
