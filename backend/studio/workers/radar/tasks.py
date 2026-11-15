"""Jobs do bloco E (registra `radar_collect`). Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.workers.jobs import JobContext, job
from studio.workers.radar import catalog, collect, ideas


@job("radar_collect")
def radar_collect(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: {niches?: [id], markets?: ["us", "br"], budget?: int, weekly?: "AAAA-Www" (rotina semanal)}."""
    catalog.update_radar_state(last_job=ctx.job_id)
    ctx.progress(0.02, "Preparando as buscas")
    budget = payload.get("budget")
    result = collect.collect(
        payload.get("niches") or None,
        payload.get("markets") or None,
        int(budget) if budget else None,
        progress=ctx.progress,
    )
    if payload.get("weekly"):  # coleta da rotina semanal: só marca a semana quando deu certo
        catalog.update_radar_state(last_week=str(payload["weekly"]))
    ideas.ensure_pending(ideas.iso_week())
    ctx.progress(1.0, f"Radar pronto: {result['videos']} vídeos de {result['canais']} canais")
    return result
