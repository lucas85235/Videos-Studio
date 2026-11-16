"""Jobs do pipeline: roteiro, storyboard, tradução, metadados, prévia, render final e piloto automático."""

from __future__ import annotations

import logging
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel
from studio.providers import media_search
from studio.workers.jobs import JobContext, job
from studio.workers.pipeline import authoring
from studio.workers.pipeline.build import Builder

log = logging.getLogger(__name__)


def _channel_info(channel_id: str | None) -> dict[str, Any]:
    """Nome, nicho, persona e identidade visual do canal (vazio sem canal)."""
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return {}
        return {"name": row.name or "", "niche": row.niche or "", "persona": row.persona or "",
                "style_bible": dict(row.style_bible or {})}


def _gates(project_id: str) -> dict[str, Any]:
    return service.get_project_row(project_id)["settings"].get("gates", {})


# Status do projeto parado na conferência das cenas (a lista de projetos mostra "Cenas para conferir").
SCENES_REVIEW_STATUS = "storyboard_review"


@job("script")
def run_script(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.1, "Escrevendo o roteiro")
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    new_doc, provider = authoring.generate_script(doc, style, project_id=project_id, job_id=ctx.job_id,
                                                  persona=_channel_info(doc.channel_id).get("persona", ""))
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
    new_doc, provider, review = authoring.storyboard(
        doc, style, project_id=project_id, job_id=ctx.job_id,
        channel=_channel_info(doc.channel_id), styles=authoring.style_catalog(style),
    )
    counts = review["counts"]
    message = f"Cenas planejadas ({provider}): {counts['scenes']} cenas, {counts['new_images']} imagens novas"
    version = service.commit(project_id, new_doc, message, author="ai")
    service.update_settings(project_id, {"storyboard_review": {**review, "provider": provider, "version": version}})
    # Com a parada das cenas ligada, as cenas novas esperam a conferência (storyboard() zera a aprovação).
    service.set_status(project_id, SCENES_REVIEW_STATUS if _gates(project_id).get("storyboard", False) else "storyboard")
    _prefetch_media(ctx, new_doc, style)
    ctx.progress(1.0, "Cenas prontas para revisão")
    result = {"version": version, "provider": provider, "scenes": counts["scenes"], "new_images": counts["new_images"],
              "reused": counts["reused"], "cards": counts["cards"]}
    if counts.get("real_hints"):
        result["real_hints"] = counts["real_hints"]
    return result


def _prefetch_media(ctx: JobContext, doc: Any, style: Any) -> None:
    """Busca de antemão as fotos livres das sugestões (só online): o usuário abre a cena e já vê os candidatos.
    Nunca derruba o job."""
    if get_settings().offline:
        return
    if not any(s.real is not None and s.real.kind == "photo" and s.real.query.strip() and s.visual.media is None
               for s in doc.scenes):
        return
    ctx.progress(0.9, "Buscando fotos livres para as sugestões")
    try:
        media_search.prefetch(doc, style, budget_s=25)
    except Exception as exc:  # noqa: BLE001 - a busca é só uma ajuda
        log.warning("a pré-busca de fotos livres falhou: %s", exc)


@job("translate")
def run_translate(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    targets: list[str] = payload.get("langs") or []
    # Só os textos de tela que faltam (cartões e sobreposições criados depois da tradução): a narração fica.
    screen_only = bool(payload.get("screen_only"))
    doc = service.get_doc(project_id)
    targets = targets or [lang for lang in doc.languages if lang != doc.primary_language]
    if payload.get("shorten"):
        # Modo B: só encurta os trechos que passam do tempo (correções manuais e trechos travados ficam).
        shortened: dict[str, list[str]] = {}
        for index, lang in enumerate(targets):
            ctx.progress(index / max(1, len(targets)), f"Encurtando a tradução em {authoring.lang_name(lang)}")
            doc, ids = authoring.shorten_translation(doc, lang, project_id=project_id, job_id=ctx.job_id)
            if ids:
                shortened[lang] = ids
        total = sum(len(ids) for ids in shortened.values())
        version = service.commit(project_id, doc, f"Tradução encurtada para caber no tempo ({total} trechos)",
                                 author="ai") if shortened else None
        return {"version": version, "langs": sorted(shortened), "shortened": shortened}
    done = []
    for index, lang in enumerate(targets):
        ctx.progress(index / max(1, len(targets)), f"Traduzindo para {authoring.lang_name(lang)}")
        doc, _provider = authoring.translate(doc, lang, screen_only=screen_only, project_id=project_id, job_id=ctx.job_id)
        done.append(lang)
    message = f"{'Tradução dos textos de tela' if screen_only else 'Tradução'}: {', '.join(done)}"
    version = service.commit(project_id, doc, message, author="ai") if done else None
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
    """Roteiro → cenas → prévia (→ render), parando nos portões de aprovação ativos.

    O portão das cenas (`gates.storyboard`) só existe nos projetos novos: sem a chave, segue direto.
    """
    project_id = ctx.project_id
    assert project_id
    gates = _gates(project_id)
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
        doc = service.get_doc(project_id)
    if gates.get("storyboard", False) and not doc.storyboard_approved and not payload.get("skip_gates"):
        service.set_status(project_id, SCENES_REVIEW_STATUS)
        return {"steps": steps, "stopped_at": "storyboard", "message": "Cenas aguardando sua aprovação"}
    other_langs = [lang for lang in doc.languages if lang != doc.primary_language]
    doc = service.get_doc(project_id)
    # Narração que falta: tradução completa. Só textos de tela (cartões de cenas refeitas depois da tradução):
    # traduz só eles, sem refazer a narração nem a voz.
    full = [lang for lang in other_langs if any(not seg.text_for(lang).strip() for seg in doc.segments)]
    screen = [lang for lang in other_langs if lang not in full and authoring.needs_translation(doc, lang)]
    if full:
        run_translate(ctx, {"langs": full})
    if screen:
        run_translate(ctx, {"langs": screen, "screen_only": True})
    if full or screen:
        steps.append("translate")
    result = run_preview(ctx, payload)
    steps.append("preview")
    if gates.get("preview", True) and not payload.get("skip_gates"):
        return {"steps": steps, "stopped_at": "preview", "message": "Prévia aguardando sua aprovação", "preview": result}
    final = run_render(ctx, payload)
    steps.append("render")
    return {"steps": steps, "stopped_at": None, "render": final}
