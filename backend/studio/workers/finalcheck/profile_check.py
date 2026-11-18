"""Conferência da edição (ADR 0004 §3.10): o "cara de IA" de um projeto, antes e depois do render.

- `project_metrics`: a medida pela timeline (a do vídeo final, a da última prévia ou uma estimada pelo documento),
  com o índice, a aderência ao perfil da referência, os itens `perfil.*` e a faixa por nível de fonte. Só leitura:
  serve a rota `GET /projects/{id}/edit-metrics` e o CLI `studio edit-metrics`.
- `run_edit_check`: depois do render, audita o MP4 com o mesmo extrator da referência
  (`media.refprofile.measure_video(mp4, quick=True)`, só quando o estilo tem perfil de referência) e grava o
  `QualityReport(kind="edit")`. A conferência do vídeo final (`report.run_final_check`) chama este passo.

A timeline do vídeo final só descreve o projeto de agora quando bate com as cenas do documento e é da versão atual:
depois de um storyboard novo, de um "Reeditar" ou de "Trocar IA por real" sem renderizar de novo, o `auto` cai na
prévia que ainda bate ou na estimada. Uma timeline de outra versão é medida com o documento DAQUELA versão (o nível de
fonte da timeline antiga sai do documento que foi renderizado, não do atual).
"""

from __future__ import annotations

import json
import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from studio.config import get_settings
from studio.core.editing import metrics
from studio.core.editing.profile import EditFingerprint, EditProfile
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.styles.schema import StyleSpec, grammar_for
from studio.db import session_scope
from studio.models import QualityReport, ReferenceVideo
from studio.workers.finalcheck.measure import item
from studio.workers.finalcheck.quick import FinalContext, edit_items, load_context, worst

log = logging.getLogger(__name__)

Source = Literal["auto", "final", "preview", "estimate"]
SOURCES: tuple[str, ...] = ("auto", "final", "preview", "estimate")
SOURCE_LABELS = {"final": "vídeo final", "preview": "última prévia", "estimate": "estimada pelo roteiro"}
SOURCE_TEXT = {"final": "Medido no vídeo final", "preview": "Medido na última prévia",
               "estimate": "Estimado pelo roteiro"}
"""Frase pronta para a tela (o `source_label` fica para o CLI)."""
RENDER_CODE = "perfil.render"
"""Item só da conferência auditada: o ritmo do MP4 diverge do da timeline acima da tolerância."""
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
    """A impressão auditada: ritmo e câmera do MP4 (o que o render entregou de fato) e o resto da timeline.

    O áudio fica o da timeline, que é exato: o alvo de SFX é "sem contar a transição", e o extrator do MP4 não separa
    o som de transição nem distingue SFX de narração e música. Nível de fonte, insertos, gancho e repetições também
    não se medem no modo rápido."""
    if video_fp is None or video_fp.pace.shots <= 0:
        return timeline_fp
    return timeline_fp.model_copy(update={"pace": video_fp.pace, "camera": video_fp.camera})


def render_items(timeline_fp: EditFingerprint, video_fp: EditFingerprint | None, tolerance: float) -> list[dict[str, Any]]:
    """`perfil.render`: o MP4 confere com a timeline? Compara cortes por minuto e plano mediano; acima da tolerância,
    aviso (o render perdeu ou ganhou cortes que a timeline não tem)."""
    if video_fp is None or video_fp.pace.shots <= 0:
        return []
    limit = math.log(1.0 + tolerance)
    gaps = []
    for label, mine, theirs, unit in (
        ("cortes por minuto", timeline_fp.pace.cuts_per_min, video_fp.pace.cuts_per_min, "/min"),
        ("plano mediano", timeline_fp.pace.median_s, video_fp.pace.median_s, " s"),
    ):
        if mine > 0 and theirs > 0 and abs(math.log(theirs / mine)) > limit:
            gaps.append(f"{label}: {theirs:.1f}{unit} no vídeo, {mine:.1f}{unit} na timeline".replace(".", ","))
    if not gaps:
        return [item(RENDER_CODE, "ok", "O vídeo renderizado confere com a timeline")]
    return [item(RENDER_CODE, "aviso", "O vídeo renderizado não bate com a timeline",
                 "; ".join(gaps) + ". Renderize de novo; se continuar, a detecção de cortes do vídeo pode estar "
                 "errando.")]


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


def _version_of(timeline: dict[str, Any]) -> int:
    try:
        return int(timeline.get("version") or 0)
    except (TypeError, ValueError):
        return 0


@dataclass
class Measured:
    """A timeline a medir, de onde ela veio e o documento com que medir."""

    timeline: dict[str, Any]
    source: str
    doc: ProjectDoc
    version: int | None
    """A versão do projeto que a timeline descreve (None: a estimada, que é sempre a de agora)."""
    outdated: bool
    """A timeline é de outra versão ou não bate mais com as cenas do documento atual."""


def _current_version(ctx: FinalContext) -> int:
    try:
        return int(ctx.row.get("current_version") or 0)
    except (TypeError, ValueError):
        return 0


def _is_current(timeline: dict[str, Any], ctx: FinalContext) -> bool:
    """A timeline descreve o projeto de agora: mesmas cenas do documento e, quando ela diz a versão, a atual."""
    version, current = _version_of(timeline), _current_version(ctx)
    return _matches_doc(timeline, ctx.doc) and (not version or not current or version == current)


def doc_for_version(ctx: FinalContext, version: int | None) -> ProjectDoc:
    """O documento da versão que a timeline descreve (o atual quando é a mesma ou quando a versão sumiu)."""
    if not version or version == _current_version(ctx):
        return ctx.doc
    try:
        return service.get_doc(ctx.project_id, version=version)
    except service.ProjectNotFound:
        return ctx.doc


def _measured(timeline: dict[str, Any], source: str, ctx: FinalContext) -> Measured:
    version = _version_of(timeline) or None
    return Measured(timeline, source, doc_for_version(ctx, version), version, not _is_current(timeline, ctx))


def metrics_timeline(ctx: FinalContext, source: Source = "auto") -> Measured:
    """A timeline a medir. `auto`: a do vídeo final quando ainda descreve o projeto (a prévia não a regravou, as cenas
    batem com o documento e a versão é a atual), senão a da última prévia nas mesmas condições, senão a estimada. As
    fontes explícitas `final`/`preview` medem o que pedem, com `outdated` quando o projeto mudou depois."""
    final = ctx.timeline if ctx.timeline and not ctx.timeline_stale else None
    if source == "final" or (source == "auto" and final is not None and _is_current(final, ctx)):
        if final is None:
            raise ValueError(f"o projeto não tem a timeline do {SOURCE_LABELS['final']} neste idioma")
        return _measured(final, "final", ctx)
    if source in ("auto", "preview"):
        preview = _read_json(get_settings().outputs_dir / ctx.project_id / ctx.lang / "timeline.json")
        if preview is not None and (source == "preview" or _is_current(preview, ctx)):
            return _measured(preview, "preview", ctx)
        if source == "preview":
            raise ValueError(f"o projeto não tem a timeline da {SOURCE_LABELS['preview']} neste idioma")
    return Measured(estimate_timeline(ctx.doc, ctx.style, ctx.lang), "estimate", ctx.doc, None, False)


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


def channel_memes(project_id: str) -> frozenset[str]:
    """Os memes dos últimos vídeos do canal (a mesma janela do verificador de novidade, `novelty.window_of`), lidos
    das timelines do último build de cada um. Só leitura; sem canal ou com erro, vazio."""
    from studio.models import Channel
    from studio.workers import novelty

    try:
        row = service.get_project_row(project_id)
        channel_id = row.get("channel_id")
        if not channel_id:
            return frozenset()
        with session_scope() as s:
            channel = s.get(Channel, channel_id)
            window = novelty.window_of(dict(channel.settings or {}) if channel else {})
        if window <= 0:
            return frozenset()
        out: set[str] = set()
        data_dir = get_settings().data_dir
        for other in novelty._history(project_id, channel_id, window):
            build = novelty._last_build(service.get_project_row(other).get("settings") or {})
            entry = novelty._entry(build, "") if build else None
            if not entry or not entry.get("timeline"):
                continue
            timeline = _read_json(data_dir / entry["timeline"])
            if timeline is not None:
                out |= metrics.timeline_memes(timeline)
        return frozenset(out)
    except Exception as exc:  # noqa: BLE001 - a comparação com o canal é complemento
        log.warning("memes do canal não lidos: %s", exc)
        return frozenset()


def compute(timeline: dict[str, Any], doc: ProjectDoc, style: StyleSpec, profile: EditProfile | None = None,
            video_fp: EditFingerprint | None = None, *, memes: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Índice, aderência, métricas, itens e faixa de uma timeline (sem banco). `memes`: os do canal."""
    measure = metrics.measure_timeline(timeline, doc, memes)
    fp = merge_fingerprints(measure.fp, video_fp)
    index = metrics.ai_look_index(fp, style, profile)
    grammar = grammar_for(style)
    targets = metrics.targets_for(style, profile)
    adh = metrics.adherence(fp, profile, grammar.tolerance) if profile is not None else None
    items = edit_items(timeline, doc, style, profile, fp if fp is not measure.fp else None, channel_memes=memes)
    items += render_items(measure.fp, video_fp, grammar.tolerance)
    return {
        "index": index.model_dump(),
        "metrics": metrics.flat_metrics(fp, targets),
        "fingerprint": fp.model_dump(),
        "timeline_fingerprint": measure.fp.model_dump() if fp is not measure.fp else None,
        "adherence": adh.model_dump() if adh is not None else None,
        "targets": targets.model_dump(),
        "items": items,
        "status": worst(items),
        "strip": metrics.prov_strip(timeline, doc),
        "ai_scene_ids": measure.ai_scene_ids,
        "duration": round(measure.duration, 3),
    }


def latest_edit_report(project_id: str, lang: str) -> dict[str, Any] | None:
    from studio.workers.finalcheck.report import report_dict

    with session_scope() as s:
        row = (s.query(QualityReport).filter_by(project_id=project_id, kind="edit", lang=lang)
               .order_by(QualityReport.created_at.desc(), QualityReport.id.desc()).first())
        return report_dict(row) if row is not None else None


def fresh_edit_report(ctx: FinalContext) -> tuple[dict[str, Any] | None, bool]:
    """O último `QualityReport(kind="edit")` quando ainda vale para o vídeo final de agora (mesma regra do
    `report.is_fresh`: mesmo arquivo, mesma versão e feito depois dele) e se havia um, mas velho."""
    from studio.workers.finalcheck.report import is_fresh

    report = latest_edit_report(ctx.project_id, ctx.lang)
    if report is None:
        return None, False
    return (report, False) if is_fresh(ctx, report) else (None, True)


REPORT_KEYS = ("index", "metrics", "fingerprint", "timeline_fingerprint", "adherence", "targets", "strip",
               "ai_scene_ids", "duration")


def project_metrics(project_id: str, lang: str | None = None, source: Source = "auto") -> dict[str, Any]:
    """A medida do projeto num idioma (só leitura): `{lang, source, source_label, source_text, outdated, ...}`.

    Medindo o vídeo final que ainda é o de agora, com a conferência auditada (`QualityReport(kind="edit")`) em dia,
    os números são os dela (`measured_from: "render"`: ritmo e câmera do MP4); senão, os da timeline
    (`measured_from: "timeline"`). `report` só vem quando está em dia com o MP4; `report_outdated` diz que havia um
    de outro render."""
    ctx = load_context(project_id, lang)
    measured = metrics_timeline(ctx, source)
    profile = style_profile(ctx.style)
    out = compute(measured.timeline, measured.doc, ctx.style, profile, memes=channel_memes(project_id))
    report, report_outdated = fresh_edit_report(ctx)
    measured_from = "timeline"
    if report is not None and measured.source == "final" and not measured.outdated:
        data = report.get("data") or {}
        if data.get("index") and data.get("metrics"):
            out.update({k: data[k] for k in REPORT_KEYS if k in data})
            out["items"] = list(report.get("items") or [])
            out["status"] = report.get("status") or worst(out["items"])
            measured_from = "render"
    current = _current_version(ctx)
    text = SOURCE_TEXT[measured.source]
    if measured.outdated:
        text += (f" (versão {measured.version}; o projeto mudou e está na {current})" if measured.version
                 else " (o projeto mudou depois)")
    return {"project_id": project_id, "lang": ctx.lang, "source": measured.source,
            "source_label": SOURCE_LABELS[measured.source], "source_text": text,
            "version": measured.version, "current_version": current or None, "outdated": measured.outdated,
            "measured_from": measured_from, "profile_refs": list(grammar_for(ctx.style).profile_refs),
            **out, "report": report, "report_outdated": report_outdated}


# --- Conferência depois do render -----------------------------------------------------------------------------------


def run_edit_check(ctx: FinalContext, progress: Progress | None = None, *,
                   audit_video: bool | None = None) -> dict[str, Any] | None:
    """Mede a edição do vídeo final (timeline do render + auditoria do MP4) e grava o `QualityReport(kind="edit")`.
    None quando não há o que medir (sem vídeo, ou a timeline salva é a da prévia e não a deste MP4). O nível de fonte
    sai do documento da versão renderizada.

    A auditoria do MP4 (scan de cortes mais decodificação a 5 fps, minutos num vídeo longo e sem como cancelar no
    meio) só roda quando há perfil da referência para comparar (`grammar.profile_refs` no estilo) ou quando
    `audit_video=True`; nos outros casos a medida sai da timeline do render, que é a mesma edição."""
    from studio.workers.finalcheck.report import report_dict

    step = progress or (lambda _f, _m: None)
    if not ctx.has_video or not ctx.timeline or ctx.timeline_stale:
        return None
    video = ctx.video_path
    assert video is not None
    step(0.0, "Medindo a edição do vídeo")
    audit = bool(grammar_for(ctx.style).profile_refs) if audit_video is None else audit_video
    video_fp = None
    if audit:
        step(0.05, "Medindo os cortes e o movimento do vídeo final")
        video_fp = measure_render(video)
    profile = style_profile(ctx.style)
    doc = doc_for_version(ctx, ctx.version or None)
    out = compute(ctx.timeline, doc, ctx.style, profile, video_fp, memes=channel_memes(ctx.project_id))
    data = {k: out[k] for k in REPORT_KEYS}
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
