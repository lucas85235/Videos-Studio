"""Conferência da edição (ADR 0004 §3.10): o "cara de IA" de um projeto, antes e depois do render.

- `project_metrics`: a medida pela timeline (a do vídeo final, a da última prévia ou uma estimada pelo documento),
  com o índice, a aderência ao perfil da referência, os itens `perfil.*` e a faixa por nível de fonte. Só leitura:
  serve a rota `GET /projects/{id}/edit-metrics` e o CLI `studio edit-metrics`.
- `run_edit_check`: depois do render, audita o MP4 com o mesmo extrator da referência
  (`media.refprofile.measure_video(mp4, quick=True)`) e grava o
  `QualityReport(kind="edit")`. A conferência do vídeo final (`report.run_final_check`) chama este passo.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from studio.config import get_settings
from studio.core.editing import metrics
from studio.core.editing.profile import EditFingerprint, EditProfile
from studio.core.project.document import ProjectDoc
from studio.core.styles.schema import StyleSpec, grammar_for
from studio.db import session_scope
from studio.models import QualityReport, ReferenceVideo
from studio.workers.finalcheck.quick import FinalContext, edit_items, load_context, worst

log = logging.getLogger(__name__)

Source = Literal["auto", "final", "preview", "estimate"]
SOURCES: tuple[str, ...] = ("auto", "final", "preview", "estimate")
SOURCE_LABELS = {"final": "vídeo final", "preview": "última prévia", "estimate": "estimada pelo roteiro"}
Progress = Callable[[float, str], None]


# --- Medida do render -----------------------------------------------------------------------------------------------


def measure_render(path: Path) -> EditFingerprint | None:
    """A impressão do MP4 pelo extrator da referência (`media.refprofile.measure_video`), em modo rápido. None quando
    a medida falha: a conferência fica só com a timeline."""
    from studio.media.refprofile import measure_video

    try:
        result = measure_video(path, quick=True)
    except Exception as exc:  # noqa: BLE001 - a auditoria do MP4 é complemento, nunca derruba a conferência
        log.warning("medida do vídeo final falhou: %s", exc)
        return None
    return result if isinstance(result, EditFingerprint) else None


def merge_fingerprints(timeline_fp: EditFingerprint, video_fp: EditFingerprint | None) -> EditFingerprint:
    """A impressão auditada: ritmo, câmera e áudio do MP4 (o que o render entregou de fato) e o resto da timeline
    (nível de fonte, insertos, gancho e repetições não se medem no modo rápido)."""
    if video_fp is None or video_fp.pace.shots <= 0:
        return timeline_fp
    return timeline_fp.model_copy(update={"pace": video_fp.pace, "camera": video_fp.camera, "audio": video_fp.audio})


# --- Timeline a medir -----------------------------------------------------------------------------------------------


def _image_key(doc: ProjectDoc, scene_id: str) -> str | None:
    """A identidade da imagem que a cena mostra (para as repetições na timeline estimada)."""
    scene = doc.scene(scene_id)
    media = doc.shown_media(scene)
    if media is not None:
        return media.video or media.asset or f"media:{scene.id}"
    if scene.visual.asset:
        return scene.visual.asset
    root = doc.reuse_root(scene)
    return f"img:{root.id}" if root is not None else None


def estimate_timeline(doc: ProjectDoc, style: StyleSpec, lang: str | None = None) -> dict[str, Any]:
    """A timeline do documento com a narração estimada pela velocidade da fala (sem narração, sem imagens, sem
    gastar nada): o índice antes de gastar cota."""
    from studio.core.text import words as split_words
    from studio.core.timeline import NarrationClip, build_timeline
    from studio.providers.base import Word

    lang = lang or doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150) * style.voice.speed
    step = 60.0 / max(60.0, wpm)
    narration: dict[str, NarrationClip] = {}
    for seg in doc.segments:
        tokens = split_words(seg.text_for(lang, doc.primary_language)) or ["…"]
        narration[seg.id] = NarrationClip(
            segment_id=seg.id, path="", duration=len(tokens) * step,
            words=[Word(tok, i * step, (i + 0.9) * step) for i, tok in enumerate(tokens)],
        )
    images = {s.id: _image_key(doc, s.id) for s in doc.scenes if s.kind in ("image", "video")}
    return build_timeline(doc, style, lang, narration, images)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("scenes") else None


def _matches_doc(timeline: dict[str, Any], doc: ProjectDoc) -> bool:
    ids = {str(s.get("id")) for s in timeline.get("scenes") or []}
    return bool(ids) and ids == {s.id for s in doc.scenes}


def metrics_timeline(ctx: FinalContext, source: Source = "auto") -> tuple[dict[str, Any], str]:
    """A timeline a medir e de onde ela veio. `auto`: a do vídeo final (quando a prévia não a regravou), senão a da
    última prévia quando ainda bate com as cenas do documento, senão a estimada."""
    if source in ("auto", "final") and ctx.timeline and not ctx.timeline_stale:
        return ctx.timeline, "final"
    if source in ("auto", "preview"):
        preview = _read_json(get_settings().outputs_dir / ctx.project_id / ctx.lang / "timeline.json")
        if preview is not None and (source == "preview" or _matches_doc(preview, ctx.doc)):
            return preview, "preview"
    if source in ("final", "preview"):
        raise ValueError(f"o projeto não tem a timeline da {SOURCE_LABELS[source]} neste idioma")
    return estimate_timeline(ctx.doc, ctx.style, ctx.lang), "estimate"


def style_profile(style: StyleSpec) -> EditProfile | None:
    """O perfil medido da referência que gerou a gramática do estilo (`grammar.profile_refs`; o primeiro que existir
    em `reference_videos.stats["profile"]`)."""
    refs = grammar_for(style).profile_refs
    if not refs:
        return None
    with session_scope() as s:
        rows = {r.id: dict(r.stats or {}) for r in s.query(ReferenceVideo).filter(ReferenceVideo.id.in_(refs)).all()}
    for ref in refs:
        data = (rows.get(ref) or {}).get("profile")
        if isinstance(data, dict):
            try:
                return EditProfile.model_validate(data)
            except ValueError:
                log.warning("perfil inválido na referência %s", ref)
    return None


def compute(timeline: dict[str, Any], doc: ProjectDoc, style: StyleSpec, profile: EditProfile | None = None,
            video_fp: EditFingerprint | None = None) -> dict[str, Any]:
    """Índice, aderência, métricas, itens e faixa de uma timeline (sem banco)."""
    measure = metrics.measure_timeline(timeline, doc)
    fp = merge_fingerprints(measure.fp, video_fp)
    index = metrics.ai_look_index(fp, style, profile)
    grammar = grammar_for(style)
    adh = metrics.adherence(fp, profile, grammar.tolerance) if profile is not None else None
    items = edit_items(timeline, doc, style, profile, fp if fp is not measure.fp else None)
    return {
        "index": index.model_dump(),
        "metrics": metrics.flat_metrics(fp),
        "fingerprint": fp.model_dump(),
        "timeline_fingerprint": measure.fp.model_dump() if fp is not measure.fp else None,
        "adherence": adh.model_dump() if adh is not None else None,
        "targets": metrics.targets_for(style, profile).model_dump(),
        "items": items,
        "status": worst(items),
        "strip": metrics.prov_strip(timeline, doc),
        "duration": round(measure.duration, 3),
    }


def latest_edit_report(project_id: str, lang: str) -> dict[str, Any] | None:
    from studio.workers.finalcheck.report import report_dict

    with session_scope() as s:
        row = (s.query(QualityReport).filter_by(project_id=project_id, kind="edit", lang=lang)
               .order_by(QualityReport.created_at.desc(), QualityReport.id.desc()).first())
        return report_dict(row) if row is not None else None


def project_metrics(project_id: str, lang: str | None = None, source: Source = "auto") -> dict[str, Any]:
    """A medida do projeto num idioma (só leitura): `{lang, source, source_label, ...compute, report}`."""
    ctx = load_context(project_id, lang)
    timeline, used = metrics_timeline(ctx, source)
    profile = style_profile(ctx.style)
    out = compute(timeline, ctx.doc, ctx.style, profile)
    return {"project_id": project_id, "lang": ctx.lang, "source": used, "source_label": SOURCE_LABELS[used],
            "version": ctx.version or None, "profile_refs": list(grammar_for(ctx.style).profile_refs),
            **out, "report": latest_edit_report(project_id, ctx.lang)}


# --- Conferência depois do render -----------------------------------------------------------------------------------


def run_edit_check(ctx: FinalContext, progress: Progress | None = None) -> dict[str, Any] | None:
    """Mede a edição do vídeo final (timeline do render + auditoria do MP4) e grava o `QualityReport(kind="edit")`.
    None quando não há o que medir (sem vídeo, ou a timeline salva é a da prévia e não a deste MP4)."""
    from studio.workers.finalcheck.report import report_dict

    step = progress or (lambda _f, _m: None)
    if not ctx.has_video or not ctx.timeline or ctx.timeline_stale:
        return None
    video = ctx.video_path
    assert video is not None
    step(0.0, "Medindo a edição do vídeo")
    video_fp = measure_render(video)
    profile = style_profile(ctx.style)
    out = compute(ctx.timeline, ctx.doc, ctx.style, profile, video_fp)
    data = {k: out[k] for k in ("index", "metrics", "fingerprint", "timeline_fingerprint", "adherence", "targets",
                                "strip", "duration")}
    data["video_measured"] = video_fp is not None
    with session_scope() as s:
        row = QualityReport(project_id=ctx.project_id, kind="edit", lang=ctx.lang, version=ctx.version,
                            source=ctx.video_rel or "", status=out["status"], items=out["items"], data=data)
        s.add(row)
        s.flush()
        result = report_dict(row)
    step(1.0, "Edição medida")
    return result


def run_for_project(project_id: str, lang: str | None = None, progress: Progress | None = None) -> dict[str, Any] | None:
    return run_edit_check(load_context(project_id, lang), progress)
