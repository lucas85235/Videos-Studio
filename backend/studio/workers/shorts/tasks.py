"""Jobs do bloco B (registra `shorts_suggest`)."""

from __future__ import annotations

from typing import Any

from studio.workers.jobs import JobContext, job
from studio.workers.shorts.suggest import suggest


@job("shorts_suggest")
def run_shorts_suggest(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Propõe 3 a 5 cortes de Short do vídeo longo e guarda no projeto."""
    assert ctx.project_id
    ctx.progress(0.1, "Lendo os trechos do vídeo")
    data = suggest(ctx.project_id, payload.get("lang"), ctx.job_id)
    ctx.progress(1.0, f"{len(data['items'])} sugestões de Short")
    return {"count": len(data["items"]), "provider": data["provider"], "lang": data["lang"]}
