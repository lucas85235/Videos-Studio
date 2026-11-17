"""Rotas da pesquisa de vídeos do canal: configuração, simulação da cota, rodadas, relatório e ideias."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from studio.workers.jobs import get_job, get_runner
from studio.workers.research import config, service, signals

router = APIRouter(prefix="/api", tags=["research"])

Market = Literal["br", "us"]
Format = Literal["long", "short"]
Step = Literal["own", "competitors", "keywords", "comments", "synthesis"]


class Competitor(BaseModel):
    id: str = Field(min_length=24, max_length=24)
    name: str = Field(default="", max_length=200)
    handle: str = Field(default="", max_length=100)


class ConfigBody(BaseModel):
    channel_id: str
    competitors: list[Competitor] | None = Field(default=None, max_length=config.MAX_COMPETITORS)
    keywords: list[str] | None = None
    keywords_us: list[str] | None = None
    markets: list[Market] | None = Field(default=None, min_length=1, max_length=2)
    formats: list[Format] | None = Field(default=None, max_length=2)
    budget: int | None = Field(default=None, ge=config.BUDGET_RANGE[0], le=config.BUDGET_RANGE[1])
    days: int | None = Field(default=None, ge=config.DAYS_RANGE[0], le=config.DAYS_RANGE[1])
    auto: bool | None = None


class ResolveBody(BaseModel):
    link: str = Field(min_length=1, max_length=500)


class RunBody(BaseModel):
    channel_id: str
    skip: list[Step] = Field(default_factory=list)


class IdeasBody(BaseModel):
    ranks: list[int] = Field(min_length=1, max_length=50)
    force: bool = False


def _config(channel_id: str) -> dict[str, Any]:
    try:
        return config.get_config(channel_id)
    except config.ChannelNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/research")
def research_state(channel_id: str = Query(min_length=1)) -> dict[str, Any]:
    cfg = _config(channel_id)
    ready = service.readiness(channel_id)
    job_id = service.active_job(channel_id)
    return {
        "channel_id": channel_id,
        "config": cfg,
        "formats": ready["formats"],
        "access": ready["access"],
        "web": ready["web"],
        "plan": ready["plan"],
        "last_run": service.last_run(channel_id),
        "next_manual_at": service.next_manual_at(channel_id),
        "active_job": get_job(job_id) if job_id else None,
    }


@router.put("/research/config")
def put_config(body: ConfigBody) -> dict[str, Any]:
    _config(body.channel_id)
    changes = body.model_dump(exclude_unset=True, exclude={"channel_id"})
    try:
        return config.save_config(body.channel_id, **changes)
    except config.ResearchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/research/competitors/resolve")
def resolve_competitor(body: ResolveBody) -> dict[str, Any]:
    try:
        return config.resolve_competitor(body.link)
    except config.ResearchError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/research/simulate")
def simulate(body: RunBody) -> dict[str, Any]:
    """A conta da cota com a configuração SALVA do canal (salve a aba Configuração antes de simular)."""
    return signals.plan_units(_config(body.channel_id), list(body.skip), search_left=service.search_left_today())


def _cooldown_text(channel_id: str) -> str | None:
    free = service.next_manual_at(channel_id)
    if free is None:
        return None
    at = datetime.fromisoformat(free.replace("Z", "+00:00")).astimezone().strftime("%H:%M")
    return (f"A próxima pesquisa manual deste canal libera às {at}: são {service.MANUAL_COOLDOWN_H} h entre as "
            "rodadas, por causa do limite de uso do plano Claude.")


@router.post("/research/run")
def start_run(body: RunBody) -> dict[str, Any]:
    _config(body.channel_id)
    ready = service.readiness(body.channel_id)
    if not ready["access"]["ok"] and not ready["web"]["ok"]:
        raise HTTPException(
            status_code=422,
            detail="Sem acesso à YouTube Data API nem busca na web: a pesquisa teria só os dados locais. "
                   + ready["access"]["message"],
        )
    payload: dict[str, Any] = {"channel_id": body.channel_id}
    if body.skip:
        payload["skip"] = list(body.skip)
    # checagem e envio juntos: dois cliques (ou duas abas) não criam duas rodadas
    with service.SUBMIT_LOCK:
        if service.active_job(body.channel_id) is not None:  # outra aba ou a rotina semanal
            raise HTTPException(status_code=409, detail="Já há uma pesquisa deste canal em andamento.")
        wait = _cooldown_text(body.channel_id)
        if wait:
            raise HTTPException(status_code=409, detail=wait)
        return get_runner().submit(service.KIND, payload)


@router.get("/research/runs")
def runs(channel_id: str = Query(min_length=1), limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    return service.list_runs(channel_id, limit)


def _run(run_id: str) -> dict[str, Any]:
    try:
        return service.get_run(run_id)
    except service.RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/research/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return _run(run_id)


@router.get("/research/runs/{run_id}/report", response_model=None)
def report(run_id: str, download: bool = False) -> dict[str, Any] | PlainTextResponse:
    """Relatório em Markdown: `{markdown, filename}` ou, com `download=true`, o arquivo .md."""
    run = _run(run_id)
    text = service.report_markdown(run_id)
    filename = f"pesquisa-{(run['created_at'] or '')[:10]}-{run_id}.md"
    if download:
        return PlainTextResponse(text, media_type="text/markdown; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{filename}"'})
    return {"markdown": text, "filename": filename}


@router.post("/research/runs/{run_id}/ideas")
def save_ideas(run_id: str, body: IdeasBody) -> dict[str, Any]:
    try:
        return service.to_ideas(run_id, body.ranks, force=body.force)
    except service.RunNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
