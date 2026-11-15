"""Rotas do radar de nichos (ADR 0003, bloco E): acesso à YouTube Data API, catálogo, coletas, painel e ideias."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.providers import credentials
from studio.workers.jobs import get_runner
from studio.workers.radar import access, catalog, collect, ideas, panel
from studio.workers.radar.analysis import MERCADOS

router = APIRouter(prefix="/api", tags=["radar"])

Market = Literal["us", "br"]


class KeyBody(BaseModel):
    api_key: str = Field(min_length=10, max_length=200)


class CollectBody(BaseModel):
    niches: list[str] | None = None
    markets: list[Market] | None = None
    budget: int | None = Field(default=None, ge=100, le=10000)


class IdeaBody(BaseModel):
    run_id: int
    youtube_id: str | None = None
    channel_youtube_id: str | None = None
    channel_id: str | None = None


class SettingsBody(BaseModel):
    auto: bool


def state() -> dict[str, Any]:
    st = catalog.radar_state()
    cfg = catalog.load_catalog()
    return {
        "access": access.access_state(),
        "auto": bool(st.get("auto")),
        "last_week": st.get("last_week"),
        "last_run": collect.last_run(ok_only=False),
        "last_job": st.get("last_job"),
        "catalog": {"niches": len(cfg["nichos"]), "markets": list(MERCADOS), "custom": catalog.is_custom()},
    }


@router.get("/radar")
def radar_state() -> dict[str, Any]:
    return state()


@router.put("/radar/key")
def set_key(body: KeyBody) -> dict[str, Any]:
    key = body.api_key.strip()
    if any(c.isspace() for c in key):
        raise HTTPException(status_code=422, detail="a chave não pode ter espaços")
    credentials.set_credential(access.PROVIDER, {access.FIELD: key})
    return {"ok": True, "masked": credentials.mask(key)}


@router.delete("/radar/key")
def delete_key() -> dict[str, Any]:
    credentials.delete_credential(access.PROVIDER)
    return {"ok": True}


def _catalog_out(cfg: dict[str, Any]) -> dict[str, Any]:
    return {**cfg, "custom": catalog.is_custom()}


@router.get("/radar/catalog")
def get_catalog() -> dict[str, Any]:
    return _catalog_out(catalog.load_catalog())


@router.put("/radar/catalog")
def put_catalog(body: dict[str, Any]) -> dict[str, Any]:
    body.pop("custom", None)
    try:
        return _catalog_out(catalog.save_catalog(body))
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/radar/catalog/reset")
def reset_catalog() -> dict[str, Any]:
    return _catalog_out(catalog.reset_catalog())


@router.post("/radar/simulate")
def simulate(body: CollectBody) -> dict[str, Any]:
    try:
        return collect.simulate(None, body.niches or None, list(body.markets or []) or None, body.budget)
    except collect.RadarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/radar/collect")
def start_collect(body: CollectBody) -> dict[str, Any]:
    try:
        access.resolve_access()
    except access.RadarAccessError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        collect.plan(
            catalog.load_catalog(), body.niches or None, list(body.markets or []) or None, body.budget
        )
    except collect.RadarError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if collect.active_job() is not None:  # outra aba ou a rotina semanal: não gasta a cota do dia duas vezes
        raise HTTPException(status_code=409, detail="já há uma coleta do radar em andamento")
    payload: dict[str, Any] = {}
    if body.niches:
        payload["niches"] = body.niches
    if body.markets:
        payload["markets"] = list(body.markets)
    if body.budget:
        payload["budget"] = body.budget
    return get_runner().submit("radar_collect", payload)


@router.get("/radar/runs")
def runs() -> list[dict[str, Any]]:
    return collect.list_runs()


@router.get("/radar/runs/{run_id}/panel")
def run_panel(run_id: int) -> dict[str, Any]:
    try:
        return panel.panel_data(run_id)
    except collect.RadarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/radar/runs/{run_id}/reprocess")
def reprocess(run_id: int) -> dict[str, Any]:
    try:
        return collect.reprocess(run_id)
    except collect.RadarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/radar/report")
def report() -> dict[str, Any]:
    return {"lines": panel.report_lines()}


@router.post("/radar/ideas")
def create_idea(body: IdeaBody) -> dict[str, Any]:
    if bool(body.youtube_id) == bool(body.channel_youtube_id):
        raise HTTPException(
            status_code=422, detail="mande o vídeo (youtube_id) ou o canal (channel_youtube_id)"
        )
    try:
        if body.youtube_id:
            return ideas.idea_from_video(body.run_id, body.youtube_id, body.channel_id or None)
        return ideas.idea_from_channel(body.run_id, body.channel_youtube_id or "", body.channel_id or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal do Vulto não encontrado") from exc
    except collect.RadarError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.put("/radar/settings")
def put_settings(body: SettingsBody) -> dict[str, Any]:
    catalog.update_radar_state(auto=body.auto)
    return state()
