"""Rotas de desempenho: métricas dos vídeos, experimentos e lições (ADR 0002, bloco A).

Origem das regras: estudio/estudio.py (`cmd_metricas_add`, `cmd_exp_*`, `cmd_licao_*`) e estudio/melhoria.py do canais.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import or_

from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Experiment, Lesson
from studio.workers.analytics import experiments as ex
from studio.workers.analytics import metrics
from studio.workers.analytics.variables import VARIABLES, known_published_at, load_videos
from studio.workers.jobs import get_runner

router = APIRouter(prefix="/api", tags=["desempenho"])

_SCOPE_RE = re.compile(r"^(global|canal:\S+|estilo:\S+|formato:(short|long))$")


def _channel_or_404(channel_id: str | None) -> None:
    if not channel_id:
        return
    with session_scope() as s:
        if s.get(Channel, channel_id) is None:
            raise HTTPException(404, "canal não encontrado")


# --------------------------------------------------------------------------------------------
# Visão geral e métricas
# --------------------------------------------------------------------------------------------


def _collect_states(channel_id: str | None) -> list[dict[str, Any]]:
    """Estado da coleta por canal: ok | sem_escopo | sem_token | publico | erro | nunca."""
    with session_scope() as s:
        q = s.query(Channel).order_by(Channel.name)
        if channel_id:
            q = q.filter(Channel.id == channel_id)
        out = []
        for c in q.all():
            a = (c.settings or {}).get("analytics") or {}
            out.append({
                "channel_id": c.id,
                "name": c.name,
                "state": a.get("state") or "nunca",
                "scope": a.get("scope"),
                "last_run": a.get("last_run"),
                "error": a.get("error") or "",
                # Impressões e CTR (Reporting API): ok | esperando | sem_escopo | erro | None (ainda não tentou).
                "reach": a.get("reach"),
                "reach_error": a.get("reach_error") or "",
                # Receita do YouTube (escopo opcional): ok | desligada | sem_escopo | erro | None.
                "revenue": a.get("revenue"),
                "revenue_error": a.get("revenue_error") or "",
            })
        return out


@router.get("/analytics/overview")
def overview(channel_id: str | None = None) -> dict[str, Any]:
    _channel_or_404(channel_id)
    videos = load_videos(channel_id)
    res = ex.results(channel_id, videos=videos)
    with session_scope() as s:
        q = s.query(Experiment).filter(Experiment.status == "aberto")
        if channel_id:
            # Experimento aberto em "Todos os canais" também vale para este canal (é avaliado com todos os vídeos).
            q = q.filter(or_(Experiment.channel_id == channel_id, Experiment.channel_id.is_(None)))
        open_exps = [ex.experiment_dict(e) for e in q.order_by(Experiment.created_at).all()]
    for e in open_exps:
        e["evaluation"] = ex.evaluate(e, videos=None if e["project_ids"] or e["channel_id"] != channel_id else videos)
    return {
        "channel_id": channel_id,
        "collect": _collect_states(channel_id),
        "videos": videos,
        "results": res,
        "suggestions": ex.suggestions(res, open_exps),
        "experiments": open_exps,
    }


@router.get("/analytics/videos/{youtube_id}")
def video_history(youtube_id: str) -> dict[str, Any]:
    rows = metrics.history(youtube_id)
    video = next((v for v in load_videos() if v["youtube_id"] == youtube_id), None)
    if not rows and video is None:
        raise HTTPException(404, "vídeo sem métricas")
    return {"video": video, "history": rows}


class MetricBody(BaseModel):
    youtube_id: str = Field(min_length=1, max_length=32)
    # Canal do vídeo quando ele não tem publicação no Vulto (a ligação pela publicação manda).
    channel_id: str | None = None
    day: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    age_hours: float | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    likes: int | None = Field(default=None, ge=0)
    comments: int | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    ctr: float | None = Field(default=None, ge=0, le=100)
    avg_view_s: float | None = Field(default=None, ge=0)
    pct_watched: float | None = Field(default=None, ge=0, le=100)
    subscribers_gained: int | None = Field(default=None, ge=0)
    revenue_usd: float | None = Field(default=None, ge=0)
    rpm: float | None = Field(default=None, ge=0)

    @field_validator("youtube_id")
    @classmethod
    def _strip(cls, value: str) -> str:
        value = value.strip()
        if not re.fullmatch(r"[\w-]+", value):
            raise ValueError("id do vídeo inválido (só letras, números, - e _)")
        return value

    @field_validator("day")
    @classmethod
    def _date(cls, value: str | None) -> str | None:
        if value:
            try:
                date.fromisoformat(value)
            except ValueError as exc:
                raise ValueError("data inválida (AAAA-MM-DD)") from exc
        return value


@router.post("/analytics/metrics")
def add_metric(body: MetricBody) -> dict[str, Any]:
    _channel_or_404(body.channel_id)
    fields = body.model_dump(exclude={"youtube_id", "day", "channel_id"})
    day = body.day or metrics.today()
    published_at = None
    now = None
    if fields.get("age_hours") is None:
        # Idade calculada pela data de publicação que o Vulto conhece, medida até o fim do dia informado (sem passar
        # de agora). Sem ela o vídeo nunca entraria em "O que funcionou" nem nos experimentos.
        published_at = known_published_at(body.youtube_id)
        now = min(metrics.day_end(day), datetime.now(UTC))
    return metrics.upsert_metric(body.youtube_id, day, "manual", published_at=published_at,
                                 channel_id=body.channel_id, now=now, **fields)


class CollectBody(BaseModel):
    channel_id: str | None = None


@router.post("/analytics/collect")
def collect(body: CollectBody) -> dict[str, Any]:
    _channel_or_404(body.channel_id)
    return get_runner().submit("metrics_collect", {"channel_id": body.channel_id})


@router.get("/analytics/variables")
def variables() -> list[dict[str, str]]:
    return [{"id": k, "label": v} for k, v in VARIABLES.items()]


# --------------------------------------------------------------------------------------------
# Experimentos
# --------------------------------------------------------------------------------------------

Metric = Literal["views", "views_dia", "ctr", "retencao", "avd_s", "likes"]


def _check_variable(value: str) -> str:
    if value not in VARIABLES:
        raise ValueError(f"variável desconhecida; use uma de: {', '.join(VARIABLES)}")
    return value


def _clean_variants(value: list[str]) -> list[str]:
    out: list[str] = []
    for v in value:
        v = str(v).strip()
        if v and v not in out:
            out.append(v)
    if len(out) < 2:
        raise ValueError("informe pelo menos 2 variantes diferentes")
    return out


class ExperimentBody(BaseModel):
    channel_id: str | None = None
    hypothesis: str = Field(min_length=3, max_length=2000)
    variable: str
    variants: list[str] = Field(max_length=10)
    metric: Metric = "views"
    project_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("variable")
    @classmethod
    def _variable(cls, value: str) -> str:
        return _check_variable(value)

    @field_validator("variants")
    @classmethod
    def _variants(cls, value: list[str]) -> list[str]:
        return _clean_variants(value)


class ExperimentUpdate(BaseModel):
    hypothesis: str | None = Field(default=None, min_length=3, max_length=2000)
    variable: str | None = None
    variants: list[str] | None = Field(default=None, max_length=10)
    metric: Metric | None = None
    project_ids: list[str] | None = Field(default=None, max_length=200)

    @field_validator("variable")
    @classmethod
    def _variable(cls, value: str | None) -> str | None:
        return value if value is None else _check_variable(value)

    @field_validator("variants")
    @classmethod
    def _variants(cls, value: list[str] | None) -> list[str] | None:
        return value if value is None else _clean_variants(value)


def _experiment_out(row: Experiment) -> dict[str, Any]:
    data = ex.experiment_dict(row)
    data["evaluation"] = ex.evaluate(data)
    return data


@router.get("/analytics/experiments")
def list_experiments(channel_id: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(Experiment)
        if channel_id:
            q = q.filter(or_(Experiment.channel_id == channel_id, Experiment.channel_id.is_(None)))
        if status:
            q = q.filter(Experiment.status == status)
        rows = q.order_by(Experiment.created_at.desc()).all()
    videos_by_channel: dict[str | None, list[dict[str, Any]]] = {}
    out = []
    for row in rows:
        data = ex.experiment_dict(row)
        if row.status == "aberto":
            key = None if row.project_ids else row.channel_id
            if key not in videos_by_channel:
                videos_by_channel[key] = load_videos(key)
            data["evaluation"] = ex.evaluate(data, videos=videos_by_channel[key])
        else:
            data["evaluation"] = None
        out.append(data)
    return out


@router.post("/analytics/experiments")
def create_experiment(body: ExperimentBody) -> dict[str, Any]:
    _channel_or_404(body.channel_id)
    with session_scope() as s:
        row = Experiment(
            id=new_id("exp"), channel_id=body.channel_id, hypothesis=body.hypothesis.strip(),
            variable=body.variable, variants=body.variants, metric=body.metric,
            project_ids=[p for p in dict.fromkeys(body.project_ids) if p],
        )
        s.add(row)
        s.flush()
    return _experiment_out(row)


def _get_experiment(s: Any, experiment_id: str) -> Experiment:
    row = s.get(Experiment, experiment_id)
    if row is None:
        raise HTTPException(404, "experimento não encontrado")
    return row


@router.get("/analytics/experiments/{experiment_id}")
def get_experiment(experiment_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = _get_experiment(s, experiment_id)
    return _experiment_out(row)


@router.put("/analytics/experiments/{experiment_id}")
def update_experiment(experiment_id: str, body: ExperimentUpdate) -> dict[str, Any]:
    with session_scope() as s:
        row = _get_experiment(s, experiment_id)
        for key in body.model_fields_set:
            value = getattr(body, key)
            if value is None:
                continue
            setattr(row, key, value.strip() if isinstance(value, str) and key == "hypothesis" else value)
    return _experiment_out(row)


class CloseBody(BaseModel):
    result: str = Field(default="", max_length=4000)
    learning: str = Field(default="", max_length=4000)
    abandon: bool = False
    # Registrar o aprendizado como lição proposta junto com o fechamento (tudo ou nada: se falhar, nada fecha).
    save_lesson: bool = False


@router.post("/analytics/experiments/{experiment_id}/close")
def close_experiment(experiment_id: str, body: CloseBody) -> dict[str, Any]:
    with session_scope() as s:
        row = _get_experiment(s, experiment_id)
        if row.status != "aberto":
            raise HTTPException(409, "este experimento já foi fechado")
        row.status = "abandonado" if body.abandon else "concluido"
        row.result = body.result.strip()
        row.learning = body.learning.strip()
        row.closed_at = datetime.now(UTC)
        if body.save_lesson and len(row.learning) >= 3:
            s.add(Lesson(
                id=new_id("lic"), scope=f"canal:{row.channel_id}" if row.channel_id else "global", text=row.learning,
                evidence=f"{row.hypothesis} — {row.result}"[:4000], origin="experimento", experiment_id=row.id,
            ))
    return _experiment_out(row)


# --------------------------------------------------------------------------------------------
# Lições
# --------------------------------------------------------------------------------------------


def lesson_dict(row: Lesson) -> dict[str, Any]:
    return {
        "id": row.id,
        "scope": row.scope,
        "text": row.text,
        "evidence": row.evidence,
        "origin": row.origin,
        "experiment_id": row.experiment_id,
        "project_id": row.project_id,
        "status": row.status,
        "applied_where": row.applied_where,
        "reason": row.reason,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "decided_at": row.decided_at.isoformat() if row.decided_at else None,
    }


class LessonBody(BaseModel):
    scope: str = Field(default="global", max_length=120)
    text: str = Field(min_length=3, max_length=4000)
    evidence: str = Field(default="", max_length=4000)
    origin: Literal["manual", "experimento", "metricas", "retro"] = "manual"
    experiment_id: str | None = None
    project_id: str | None = None

    @field_validator("scope")
    @classmethod
    def _scope(cls, value: str) -> str:
        value = value.strip()
        if not _SCOPE_RE.fullmatch(value):
            raise ValueError("escopo: global, canal:<id>, estilo:<id>, formato:short ou formato:long")
        return value


@router.get("/analytics/lessons")
def list_lessons(status: str | None = None, scope: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(Lesson)
        if status:
            q = q.filter(Lesson.status == status)
        if scope:
            q = q.filter(Lesson.scope.startswith(scope, autoescape=True))
        return [lesson_dict(r) for r in q.order_by(Lesson.created_at.desc()).all()]


@router.post("/analytics/lessons")
def create_lesson(body: LessonBody) -> dict[str, Any]:
    with session_scope() as s:
        if body.experiment_id and s.get(Experiment, body.experiment_id) is None:
            raise HTTPException(404, "experimento não encontrado")
        row = Lesson(
            id=new_id("lic"), scope=body.scope, text=body.text.strip(), evidence=body.evidence.strip(),
            origin=body.origin, experiment_id=body.experiment_id, project_id=body.project_id,
        )
        s.add(row)
        s.flush()
        return lesson_dict(row)


class DecideBody(BaseModel):
    status: Literal["aplicada", "descartada"]
    applied_where: str | None = Field(default=None, max_length=2000)
    reason: str | None = Field(default=None, max_length=2000)


@router.post("/analytics/lessons/{lesson_id}/decide")
def decide_lesson(lesson_id: str, body: DecideBody) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Lesson, lesson_id)
        if row is None:
            raise HTTPException(404, "lição não encontrada")
        row.status = body.status
        # COALESCE como no canais: campo não enviado mantém o que já havia.
        if body.applied_where is not None:
            row.applied_where = body.applied_where.strip()
        if body.reason is not None:
            row.reason = body.reason.strip()
        row.decided_at = datetime.now(UTC)
        return lesson_dict(row)
