"""Job da pesquisa de vídeos (registra `video_research`). Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.workers.jobs import JobContext, job
from studio.workers.pipeline.build import BuildCancelled
from studio.workers.research import config, service


@job(service.KIND)
def video_research(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Payload: {channel_id, weekly?: "AAAA-Www" (rotina semanal), skip?: ["own"|"competitors"|"keywords"|
    "comments"|"synthesis"]}.

    A semana fica marcada (`last_week`: a rotina não roda o canal de novo nela) quando a rodada fechou de vez: síntese
    feita, ou sem síntese por um motivo que não muda até amanhã (sem a web, etapa pulada). Um erro passageiro do Claude
    (`retry`: limite do plano, tempo esgotado) não marca: a rotina tenta de novo numa manhã seguinte. A rodada manual
    que fecha "ok" também marca a semana (a rotina não repete o canal logo depois). Cancelar a rodada semanal marca a
    semana: foi você que parou. O aviso das ideias novas aparece em Hoje (calculado na hora, some quando você decide)."""
    from studio.workers.radar.ideas import iso_week

    channel_id = str(payload["channel_id"])
    week = payload.get("weekly")
    config.update_state(channel_id, last_job=ctx.job_id)
    try:
        result = service.run(channel_id, job_id=ctx.job_id, skip=list(payload.get("skip") or []),
                             weekly=week, progress=ctx.progress, cancelled=ctx.cancelled)
    except BuildCancelled:
        if week:
            config.update_state(channel_id, last_week=str(week))
        raise
    if week and not result.get("retry"):
        config.update_state(channel_id, last_week=str(week))
    elif not week and result["status"] == "ok":
        config.update_state(channel_id, last_week=iso_week())
    # a rodada voltou: as ideias já foram gravadas, então um "cancelar" que chegou depois não vale mais
    ctx.uncancel()
    n = result["saved"]
    ctx.progress(1.0, f"{n} ideia salva" if n == 1 else f"{n} ideias salvas")
    return result
