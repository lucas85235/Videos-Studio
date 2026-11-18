"""Relatório da conferência do vídeo final (`QualityReport(kind="final")`) e o estado mostrado antes de publicar."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.media.ffmpeg import duration_of
from studio.models import QualityReport
from studio.workers.finalcheck import measure
from studio.workers.finalcheck.contact import SHEET_NAME, contact_sheet
from studio.workers.finalcheck.quick import QUICK_CODES, FinalContext, load_context, quick_items, worst
from studio.workers.finalcheck.triggers import late_triggers

Progress = Callable[[float, str], None]


def report_dict(row: QualityReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "kind": row.kind,
        "lang": row.lang,
        "version": row.version,
        "source": row.source,
        "status": row.status,
        "items": list(row.items or []),
        "data": dict(row.data or {}),
        "contact_sheet": row.contact_sheet,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def run_final_check(project_id: str, lang: str | None = None, progress: Progress | None = None) -> dict[str, Any]:
    """Junta a conferência rápida, as medidas, os gatilhos e a folha de contato e grava o relatório."""
    step = progress or (lambda _f, _m: None)
    ctx = load_context(project_id, lang)
    if not ctx.has_video:
        raise ValueError("renderize o vídeo final deste idioma antes de conferir")
    video = ctx.video_path
    assert video is not None
    items = quick_items(ctx)
    step(0.1, "Medindo o volume")
    duration = duration_of(video) or ctx.duration or 0.0
    volume = measure.loudness(video)
    items += measure.loudness_items(volume, float(ctx.style.audio.loudness_lufs))
    pauses: list[dict[str, Any]] = []
    if ctx.timeline_stale:
        # O timeline.json é o da prévia: cenas e respiros não batem com este MP4.
        items.append(measure.item("timeline_skipped", "aviso", "Voz × música e textos perto do corte não conferidos",
                                  "a timeline salva é a da prévia, não a deste vídeo final: renderize de novo."))
    else:
        step(0.35, "Voz e música nos respiros")
        pauses, music_items = measure.voice_vs_music(video, ctx.timeline)
        items += music_items
    step(0.5, "Procurando tela preta")
    black = measure.black_segments(video, duration)
    items += measure.black_items(black)
    if not ctx.timeline_stale:
        step(0.65, "Textos perto do corte")
        items += late_triggers(ctx.timeline)
    step(0.7, "Novidade frente aos últimos vídeos")
    from studio.workers import novelty  # import tardio: o verificador puxa o canal e o YouTube

    items += [measure.item("novelty", "aviso", "Parecido com vídeo recente do canal", text)
              for text in novelty.safe_warnings(project_id)]
    if not ctx.timeline_stale:
        step(0.72, "Medindo a edição")
        from studio.workers.finalcheck import profile_check  # relatório próprio: QualityReport(kind="edit")

        try:
            profile_check.run_edit_check(ctx)
        except Exception as exc:  # noqa: BLE001 - a medida da edição nunca derruba a conferência do vídeo
            logging.getLogger(__name__).warning("conferência da edição falhou: %s", exc)
    step(0.75, "Folha de quadros")
    out = get_settings().outputs_dir / project_id / ctx.lang / SHEET_NAME
    contact_sheet(video, out, duration)
    sheet_rel = out.relative_to(get_settings().data_dir).as_posix()
    data = {**volume, "duration": round(duration, 2), "pauses": pauses, "black": black,
            "timeline_stale": ctx.timeline_stale}
    with session_scope() as s:
        row = QualityReport(project_id=project_id, kind="final", lang=ctx.lang, version=ctx.version,
                            source=ctx.video_rel or "", status=worst(items), items=items, data=data,
                            contact_sheet=sheet_rel)
        s.add(row)
        s.flush()
        result = report_dict(row)
    step(1.0, "Conferência pronta")
    return result


def latest_report(project_id: str, lang: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = (s.query(QualityReport).filter_by(project_id=project_id, kind="final", lang=lang)
               .order_by(QualityReport.created_at.desc(), QualityReport.id.desc()).first())
        return report_dict(row) if row is not None else None


def is_fresh(ctx: FinalContext, report: dict[str, Any] | None) -> bool:
    """Relatório do mesmo vídeo, da mesma versão e feito depois do arquivo (render novo no mesmo caminho fica velho).

    Com a timeline regravada pela Prévia, a versão do vídeo fica desconhecida: vale o relatório feito depois deste MP4
    (o de antes da prévia usou a timeline do final; o de depois pulou o que depende dela e grava versão 0). Com a
    timeline certa, o relatório que pulou essa parte fica velho.
    """
    if not report or not ctx.has_video or report.get("source") != ctx.video_rel:
        return False
    if not ctx.timeline_stale and (report.get("version") != ctx.version
                                   or (report.get("data") or {}).get("timeline_stale")):
        return False
    created = report.get("created_at")
    if not created:
        return False
    try:
        made = datetime.fromisoformat(created).timestamp()
    except ValueError:
        return False
    video = ctx.video_path
    assert video is not None
    return made + 1.0 >= video.stat().st_mtime


def edit_section(ctx: FinalContext) -> dict[str, Any] | None:
    """A conferência da edição auditada no MP4 (`QualityReport(kind="edit")`) para a Conferência final, quando ela
    vale para este vídeo (mesma regra do `is_fresh`): `{report_id, status, score, band, items}`. Os itens `perfil.*`
    nunca passam de aviso e ficam fora do `status` do preflight: edição é gosto, não trava o envio."""
    from studio.workers.finalcheck.profile_check import fresh_edit_report

    report, _ = fresh_edit_report(ctx)
    if report is None:
        return None
    index = (report.get("data") or {}).get("index") or {}
    items = [{**i, "level": "aviso" if i.get("level") == "erro" else i.get("level", "ok")}
             for i in report.get("items") or []]
    return {"report_id": report["id"], "status": worst(items), "score": index.get("score"), "band": index.get("band"),
            "items": items}


def preflight(project_id: str, lang: str | None = None) -> dict[str, Any]:
    """Estado antes de publicar: avisos rápidos de agora + o relatório salvo (o pior nível decide). `edit`: os itens
    `perfil.*` da conferência da edição em dia, à parte (não entram no `status`)."""
    ctx = load_context(project_id, lang)
    quick = quick_items(ctx)
    report = latest_report(project_id, ctx.lang)
    fresh = is_fresh(ctx, report)
    items = list(quick)
    if fresh and report:
        items += [i for i in report["items"] if i.get("code") not in QUICK_CODES]
    return {"lang": ctx.lang, "video": ctx.video_rel if ctx.has_video else None, "report": report, "fresh": fresh,
            "quick": quick, "status": worst(items), "items": items, "edit": edit_section(ctx)}
