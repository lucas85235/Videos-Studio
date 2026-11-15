"""Jobs do bloco A (registra `metrics_collect`). O bloco C dispara este job pelo nome nas rotinas."""

from __future__ import annotations

from typing import Any

from studio.workers.analytics.collect import collect
from studio.workers.jobs import JobContext, job


@job("metrics_collect")
def metrics_collect(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Coleta as métricas do YouTube de um canal (`payload["channel_id"]`) ou de todos (vazio)."""
    return collect(payload.get("channel_id") or None, progress=ctx.progress)
