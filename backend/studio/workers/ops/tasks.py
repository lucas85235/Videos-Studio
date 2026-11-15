"""Jobs do bloco C (registra `routine_morning` e `backup`)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from studio.workers.jobs import JobContext, job
from studio.workers.ops import routines
from studio.workers.ops.backup import run_backup


@job("routine_morning")
def routine_morning(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.progress(0.1, "Rotina da manhã")
    lines = routines.morning()
    routines.update_state(last_morning=datetime.now(UTC).isoformat(), last_morning_job=ctx.job_id,
                          last_morning_lines=lines)
    return {"lines": lines}


@job("backup")
def backup(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    ctx.progress(0.1, "Copiando o banco")
    path = run_backup()
    routines.update_state(last_backup=datetime.now(UTC).isoformat(), last_backup_job=ctx.job_id)
    return {"file": path.name, "size": path.stat().st_size}
