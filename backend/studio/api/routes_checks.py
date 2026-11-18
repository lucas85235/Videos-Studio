"""Rotas da conferência do vídeo final antes de publicar (ADR 0003, bloco H)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.core.project import service
from studio.workers.finalcheck.report import preflight
from studio.workers.jobs import get_runner

router = APIRouter(prefix="/api", tags=["conferencia"])


def _check_project(project_id: str) -> None:
    try:
        service.get_project_row(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


@router.get("/projects/{project_id}/preflight")
def get_preflight(project_id: str, lang: str | None = None) -> dict[str, Any]:
    """`{lang, video, report | null, fresh, quick, status}` (e `items`: tudo o que conta para o estado)."""
    _check_project(project_id)
    return preflight(project_id, lang or None)


class PreflightRequest(BaseModel):
    lang: str | None = Field(default=None, max_length=10)


@router.post("/projects/{project_id}/preflight")
def run_preflight(project_id: str, body: PreflightRequest) -> dict[str, Any]:
    """Roda a conferência completa (job `final_check`)."""
    _check_project(project_id)
    return get_runner().submit("final_check", {"lang": body.lang}, project_id)


@router.get("/projects/{project_id}/edit-metrics")
def get_edit_metrics(project_id: str, lang: str | None = None,
                     source: Literal["auto", "final", "preview", "estimate"] = "auto") -> dict[str, Any]:
    """Índice "cara de IA", aderência ao perfil, métricas, itens `perfil.*` e faixa por nível de fonte (ADR 0004
    §3.10). Só leitura. `source`: a timeline medida (auto: a do vídeo final, a da última prévia que ainda bate com as
    cenas, ou a estimada pelo roteiro)."""
    from studio.workers.finalcheck.profile_check import project_metrics

    _check_project(project_id)
    try:
        return project_metrics(project_id, lang or None, source)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
