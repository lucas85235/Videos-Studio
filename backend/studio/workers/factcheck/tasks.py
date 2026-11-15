"""Jobs do bloco F (registra `factcheck`). Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.workers.factcheck.claims import run_factcheck
from studio.workers.jobs import JobContext, job


@job("factcheck")
def run_factcheck_job(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Lista as afirmações do roteiro com fonte e confere cada trecho na página. Payload: `{lang?, segment_ids?}`."""
    assert ctx.project_id
    ids = payload.get("segment_ids")
    result = run_factcheck(ctx.project_id, payload.get("lang"), list(ids) if ids else None, job_id=ctx.job_id,
                           progress=ctx.progress, cancelled=ctx.cancelled)
    ctx.progress(1.0, f"{result['claims']} afirmações checadas")
    return result
