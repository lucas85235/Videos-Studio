"""Jobs do bloco H (registra `final_check`). Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.workers.finalcheck.report import run_final_check
from studio.workers.jobs import JobContext, job


@job("final_check")
def final_check(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Confere o vídeo final de um idioma (`payload["lang"]`) e grava o `QualityReport(kind="final")`."""
    assert ctx.project_id
    report = run_final_check(ctx.project_id, payload.get("lang") or None, progress=ctx.progress)
    return {"report_id": report["id"], "status": report["status"], "lang": report["lang"],
            "items": len(report["items"])}
