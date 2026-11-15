"""Jobs do bloco I (ADR 0003): `page_capture`, `reference_shots` e `thumbs_variants`. Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.workers.jobs import JobContext, job
from studio.workers.visual import proofs, references, thumbs


@job("page_capture")
def run_page_capture(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prova na tela: {scene_id, url, phrases, focus}."""
    assert ctx.project_id
    out = proofs.capture_proof(ctx.project_id, str(payload["scene_id"]), str(payload["url"]),
                               list(payload.get("phrases") or []), int(payload.get("focus") or 0),
                               progress=ctx.progress)
    return {"capture_id": out["id"], "status": out["status"], "version": out["version"], "warnings": out["warnings"]}


@job("reference_shots")
def run_reference_shots(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Planos do vídeo de referência: {reference_id} (baixa antes, se veio por link)."""
    ref = references.analyze_reference(str(payload["reference_id"]), progress=ctx.progress)
    return {"reference_id": ref["id"], "shots": len(ref["shots"]), "status": ref["status"]}


@job("thumbs_variants")
def run_thumbs_variants(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """As miniaturas do teste A/B: {lang}."""
    assert ctx.project_id
    state = thumbs.make_variants(ctx.project_id, payload.get("lang"), progress=ctx.progress)
    return {"lang": state["lang"], "variants": len(state["variants"]),
            "errors": [e for v in state["variants"] for e in v.get("errors", [])]}
