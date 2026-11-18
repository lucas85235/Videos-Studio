"""Jobs do bloco I (ADR 0003): `page_capture`, `reference_shots`, `reference_profile` (ADR 0004 §3.2) e
`thumbs_variants`. Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.workers.jobs import JobContext, get_runner, job
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
    """Planos do vídeo de referência: {reference_id} (baixa antes, se veio por link). Depois enfileira o perfil de
    edição em modo rápido (`reference_profile`), a não ser que `profile: false`."""
    ref = references.analyze_reference(str(payload["reference_id"]), progress=ctx.progress)
    out: dict[str, Any] = {"reference_id": ref["id"], "shots": len(ref["shots"]), "status": ref["status"]}
    if payload.get("profile", True) and not ctx.cancelled():
        out["profile_job"] = get_runner().submit("reference_profile", {"reference_id": ref["id"], "deep": False})["id"]
    return out


@job("reference_profile")
def run_reference_profile(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Perfil de edição da referência (ADR 0004 §3.2): {reference_id, deep}. Grava em `stats["profile"]`."""
    ref = references.profile_reference(str(payload["reference_id"]), deep=bool(payload.get("deep")),
                                       progress=ctx.progress)
    profile = ref["stats"].get("profile") or {}
    notes = ref["stats"].get("profile_notes") or {}
    return {"reference_id": ref["id"], "quick": profile.get("quick", True),
            "cuts_per_min": (profile.get("pace") or {}).get("cuts_per_min"),
            "skipped": notes.get("skipped") or {}}


@job("thumbs_variants")
def run_thumbs_variants(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """As miniaturas do teste A/B: {lang}."""
    assert ctx.project_id
    state = thumbs.make_variants(ctx.project_id, payload.get("lang"), progress=ctx.progress)
    return {"lang": state["lang"], "variants": len(state["variants"]),
            "errors": [e for v in state["variants"] for e in v.get("errors", [])]}
