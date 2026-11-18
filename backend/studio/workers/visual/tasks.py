"""Jobs do bloco I (ADR 0003): `page_capture`, `reference_shots`, `reference_profile` (ADR 0004 §3.2) e
`thumbs_variants`. Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.models import Job
from studio.workers.jobs import ACTIVE_STATUSES, JobContext, get_runner, job, job_dict
from studio.workers.visual import proofs, references, thumbs


@job("page_capture")
def run_page_capture(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prova na tela: {scene_id, url, phrases, focus}."""
    assert ctx.project_id
    out = proofs.capture_proof(ctx.project_id, str(payload["scene_id"]), str(payload["url"]),
                               list(payload.get("phrases") or []), int(payload.get("focus") or 0),
                               progress=ctx.progress)
    return {"capture_id": out["id"], "status": out["status"], "version": out["version"], "warnings": out["warnings"]}


def active_profile_job(ref_id: str) -> dict[str, Any] | None:
    """O job `reference_profile` na fila ou rodando para esta referência (None se não há)."""
    with session_scope() as s:
        rows = (s.query(Job).filter(Job.kind == "reference_profile", Job.status.in_(ACTIVE_STATUSES))
                .order_by(Job.created_at.desc()).all())
        for row in rows:
            if str((row.payload or {}).get("reference_id")) == ref_id:
                return job_dict(row)
    return None


def submit_profile(ref_id: str, deep: bool) -> tuple[dict[str, Any], bool]:
    """Enfileira o perfil, ou devolve o job ativo da mesma referência quando ele já cobre o pedido (profundo cobre
    rápido). (job, criado agora?). Um rápido ativo não cobre um pedido profundo: `RuntimeError`."""
    active = active_profile_job(ref_id)
    if active is not None:
        if deep and not (active.get("payload") or {}).get("deep"):
            raise RuntimeError("A medição rápida do perfil desta referência ainda está rodando: espere ela terminar.")
        return active, False
    return get_runner().submit("reference_profile", {"reference_id": ref_id, "deep": deep}), True


@job("reference_shots")
def run_reference_shots(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Planos do vídeo de referência: {reference_id} (baixa antes, se veio por link). Depois enfileira o perfil de
    edição em modo rápido (`reference_profile`), a não ser que `profile: false`, que já haja perfil deste mesmo
    arquivo (reanalisar os planos não refaz nem rebaixa o perfil profundo) ou um job de perfil ativo."""
    ref = references.analyze_reference(str(payload["reference_id"]), progress=ctx.progress)
    out: dict[str, Any] = {"reference_id": ref["id"], "shots": len(ref["shots"]), "status": ref["status"]}
    if not payload.get("profile", True) or ctx.cancelled():
        return out
    path = get_settings().data_dir / ref["path"] if ref["path"] else None
    if path is not None and references.profile_matches_file(ref["stats"], path):
        return out
    job_row, _created = submit_profile(ref["id"], deep=False)
    out["profile_job"] = job_row["id"]
    return out


@job("reference_profile")
def run_reference_profile(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Perfil de edição da referência (ADR 0004 §3.2): {reference_id, deep}. Grava em `stats["profile"]`; cancelado,
    para no meio e não grava nada."""
    from studio.media.refprofile import Cancelled

    ref_id = str(payload["reference_id"])
    try:
        ref = references.profile_reference(ref_id, deep=bool(payload.get("deep")), progress=ctx.progress,
                                           cancelled=ctx.cancelled)
    except Cancelled:
        return {"reference_id": ref_id, "canceled": True}
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
