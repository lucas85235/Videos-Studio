"""Jobs do pipeline: roteiro, storyboard, tradução, metadados, prévia, render final e piloto automático."""

from __future__ import annotations

from typing import Any

from studio.core.project import service
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel
from studio.workers.jobs import JobContext, job
from studio.workers.pipeline import authoring
from studio.workers.pipeline.build import Builder


def _channel_persona(channel_id: str | None) -> str:
    if not channel_id:
        return ""
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return row.persona if row else ""


@job("script")
def run_script(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.1, "Escrevendo o roteiro")
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    new_doc, provider = authoring.generate_script(doc, style, project_id=project_id, job_id=ctx.job_id,
                                                  persona=_channel_persona(doc.channel_id))
    version = service.commit(project_id, new_doc, f"Roteiro gerado ({provider})", author="ai")
    service.set_status(project_id, "script_review")
    ctx.progress(1.0, "Roteiro pronto para revisão")
    return {"version": version, "provider": provider, "segments": len(new_doc.segments)}


@job("storyboard")
def run_storyboard(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.1, "Planejando as cenas")
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    new_doc, provider = authoring.storyboard(doc, style, project_id=project_id, job_id=ctx.job_id)
    version = service.commit(project_id, new_doc, f"Storyboard gerado ({provider})", author="ai")
    service.set_status(project_id, "storyboard")
    ctx.progress(1.0, "Storyboard pronto")
    return {"version": version, "provider": provider, "scenes": len(new_doc.scenes)}


@job("translate")
def run_translate(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    targets: list[str] = payload.get("langs") or []
    doc = service.get_doc(project_id)
    targets = targets or [lang for lang in doc.languages if lang != doc.primary_language]
    done = []
    for index, lang in enumerate(targets):
        ctx.progress(index / max(1, len(targets)), f"Traduzindo para {authoring.lang_name(lang)}")
        doc, _provider = authoring.translate(doc, lang, project_id=project_id, job_id=ctx.job_id)
        done.append(lang)
    version = service.commit(project_id, doc, f"Tradução: {', '.join(done)}", author="ai") if done else None
    return {"version": version, "langs": done}


@job("metadata")
def run_metadata(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    langs = payload.get("langs") or doc.languages
    chapters = payload.get("chapters") or []
    for index, lang in enumerate(langs):
        ctx.progress(index / max(1, len(langs)), f"Metadados em {authoring.lang_name(lang)}")
        doc, _ = authoring.metadata(doc, style, lang, chapters=chapters, project_id=project_id, job_id=ctx.job_id)
    version = service.commit(project_id, doc, "Metadados (título, descrição, tags)", author="ai")
    return {"version": version, "langs": langs}


def _build(ctx: JobContext, payload: dict[str, Any], mode: str) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    builder = Builder(project_id, mode=mode, langs=payload.get("langs"), sink=ctx)  # type: ignore[arg-type]
    result = builder.run()
    service.update_settings(project_id, {f"last_{mode}": result})
    service.set_status(project_id, "preview_ready" if mode == "preview" else "rendered")
    return result


@job("preview")
def run_preview(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _build(ctx, payload, "preview")


@job("render")
def run_render(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _build(ctx, payload, "final")


@job("autopilot")
def run_autopilot(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Roteiro → storyboard → prévia (→ render), parando nos portões de aprovação ativos."""
    project_id = ctx.project_id
    assert project_id
    gates = service.get_project_row(project_id)["settings"].get("gates", {})
    steps: list[str] = []
    doc = service.get_doc(project_id)
    if not doc.segments or payload.get("rewrite"):
        run_script(ctx, payload)
        steps.append("script")
        if gates.get("script", True) and not payload.get("skip_gates"):
            return {"steps": steps, "stopped_at": "script", "message": "Roteiro aguardando sua aprovação"}
    doc = service.get_doc(project_id)
    if not doc.scenes:
        run_storyboard(ctx, payload)
        steps.append("storyboard")
    other_langs = [lang for lang in doc.languages if lang != doc.primary_language]
    doc = service.get_doc(project_id)
    if other_langs and any(not seg.text_for(lang).strip() for seg in doc.segments for lang in other_langs):
        run_translate(ctx, {"langs": other_langs})
        steps.append("translate")
    result = run_preview(ctx, payload)
    steps.append("preview")
    if gates.get("preview", True) and not payload.get("skip_gates"):
        return {"steps": steps, "stopped_at": "preview", "message": "Prévia aguardando sua aprovação", "preview": result}
    final = run_render(ctx, payload)
    steps.append("render")
    return {"steps": steps, "stopped_at": None, "render": final}
