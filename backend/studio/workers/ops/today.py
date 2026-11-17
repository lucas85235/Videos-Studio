"""Resumo do dia (origem: `resumo` de estudio/rotinas.py do canais) para GET /api/today.

- `waiting`: o que só você pode fazer, calculado na hora (revisões, pacotes manuais a postar, YouTube com token
  falhando, oferta de afiliado sem conferência recente, escopo do Analytics faltando, perguntas sem resposta na caixa
  de comentários, caixa de comentários sem a permissão do YouTube e ideias novas da pesquisa de vídeos);
- `pending`: pendências abertas (à mão ou das rotinas);
- `stock`: estoque de cada canal com mínimo;
- `series`: séries ativas com o estoque de episódios, revisões e falhas;
- `yesterday`: o que saiu ontem e os jobs que falharam;
- `routines`: estado das rotinas;
- `time`: as etapas que mais levaram tempo nos últimos 7 dias, com as sugestões.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

from studio.db import session_scope
from studio.models import AffiliateOffer, Channel, Job, Project

log = logging.getLogger(__name__)

OFFER_CHECK_DAYS = 3  # oferta conferida há mais de 3 dias pede nova conferência

# status do projeto que esperam uma decisão sua: (texto, aba do projeto)
REVIEWS = {
    "script_review": ("Revisar o roteiro de", "roteiro"),
    "storyboard_review": ("Conferir as cenas de", "cenas"),
    "preview_ready": ("Assistir à prévia de", "previa"),
}


def _local(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone()


def _item(kind: str, text: str, where: str, channel_id: str | None = None,
          project_id: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "text": text, "where": where, "channel_id": channel_id, "project_id": project_id}


def waiting(now: datetime | None = None) -> list[dict[str, Any]]:
    """O que só você pode fazer agora (calculado na hora, não vira pendência)."""
    from studio.workers.publish.schedule import agenda
    from studio.workers.publish.youtube_channels import public_info

    today = _local(now).date()
    out: list[dict[str, Any]] = []
    with session_scope() as s:
        for p in (
            s.query(Project).filter(Project.status.in_(tuple(REVIEWS))).order_by(Project.updated_at).all()
        ):
            text, tab = REVIEWS[p.status]
            out.append(_item("review", f"{text} {p.title}", f"/projetos/{p.id}?aba={tab}", p.channel_id, p.id))
        channels = [(c.id, c.name, dict(c.settings or {})) for c in s.query(Channel).order_by(Channel.name).all()]
        offers = [
            (o.id, o.channel_id, o.name, o.checked_on or "")
            for o in s.query(AffiliateOffer).filter(AffiliateOffer.status == "ativa").order_by(AffiliateOffer.name).all()
        ]
    for item in agenda(now=now)["manual"]:
        out.append(_item("post", f"Postar {item['project_title']} ({item['platform']})", "/agenda",
                         item["channel_id"], item["project_id"]))
    for cid, name, settings in channels:
        try:
            info = public_info(settings)
        except Exception as exc:  # noqa: BLE001 - situação do token indisponível não derruba o resumo
            log.debug("token do canal %s não lido: %s", cid, exc)
            info = None
        if info and info.get("token") == "error":
            out.append(_item("youtube", f"Vincular de novo o YouTube do {name}: o Google recusou o token", "/canais",
                             cid))
        if ((settings.get("analytics") or {}).get("scope")) == "missing":
            out.append(_item("analytics", f"Vincular de novo o YouTube do {name} para ler retenção e duração média",
                             "/canais", cid))
        if ((settings.get("comments") or {}).get("state")) == "sem_escopo":
            out.append(_item("comments", f"Vincular de novo o YouTube do {name} para responder comentários pelo Vulto",
                             "/canais", cid))
    for q in _open_questions():
        plural = "pergunta sem resposta" if q["count"] == 1 else "perguntas sem resposta"
        out.append(_item("comments", f"Responder {q['count']} {plural} nos comentários do {q['name']}",
                         f"/comentarios?canal={q['channel_id']}&filtro=perguntas", q["channel_id"]))
    for cid, name, settings in channels:
        n = _research_waiting(cid, settings)
        if n:
            plural = "ideia nova" if n == 1 else "ideias novas"
            out.append(_item("research", f"{n} {plural} da pesquisa de vídeos do {name}", f"/ideias?canal={cid}",
                             cid))
    names = {cid: name for cid, name, _ in channels}
    for _, cid, name, checked in offers:
        try:
            age = (today - date.fromisoformat(checked)).days if checked else None
        except ValueError:
            age = None
        if age is not None and age <= OFFER_CHECK_DAYS:
            continue
        when = f"última conferência em {checked}" if age is not None else "nunca conferida"
        out.append(_item("offer", f"Conferir a oferta {name} do {names.get(cid, cid)} (vendedor, estoque e preço): "
                                  f"{when}", "/dinheiro", cid))
    return out


def _research_waiting(channel_id: str, settings: dict[str, Any]) -> int:
    """Ideias da pesquisa de vídeos do canal ainda esperando decisão (só canais que já rodaram a pesquisa)."""
    if not ((settings.get("research") or {}).get("last_run_id")):
        return 0
    from studio.workers.research.service import new_ideas_waiting

    try:
        return new_ideas_waiting(channel_id)
    except Exception as exc:  # noqa: BLE001 - a pesquisa não derruba o resumo
        log.debug("ideias da pesquisa do canal %s não lidas: %s", channel_id, exc)
        return 0


def _open_questions() -> list[dict[str, Any]]:
    from studio.workers.comments.inbox import open_questions

    try:
        return open_questions()
    except Exception as exc:  # noqa: BLE001 - a caixa de comentários não derruba o resumo
        log.debug("perguntas dos comentários não lidas: %s", exc)
        return []


def yesterday(now: datetime | None = None) -> dict[str, Any]:
    """O que saiu ontem (dia local) e os jobs que falharam."""
    from studio.workers.ops.velocity import label
    from studio.workers.publish.schedule import agenda

    local = _local(now)
    day = (local - timedelta(days=1)).date()
    start = datetime.combine(day, datetime.min.time(), tzinfo=local.tzinfo)
    end = start + timedelta(days=1)

    def on_day(stamp: str | None) -> bool:
        if not stamp:
            return False
        dt = datetime.fromisoformat(stamp)
        dt = dt if dt.tzinfo else dt.replace(tzinfo=UTC)
        return start <= dt < end

    published = [
        {"project_id": i["project_id"], "title": i["project_title"], "channel_name": i["channel_name"],
         "platform": i["platform"], "url": i["url"], "at": i["scheduled_at"] or i["published_at"] or i["created_at"]}
        for i in agenda(days_back=3, now=now)["recent"]
        if on_day(i["scheduled_at"] or i["published_at"] or i["created_at"])
    ]
    with session_scope() as s:
        rows = (
            s.query(Job, Project.title)
            .outerjoin(Project, Project.id == Job.project_id)
            .filter(Job.finished_at.isnot(None), Job.finished_at >= start.astimezone(UTC),
                    Job.finished_at < end.astimezone(UTC))
            .order_by(Job.finished_at)
            .all()
        )
        jobs = [(j.id, j.kind, j.project_id, title, j.status, j.error, j.started_at, j.finished_at) for j, title in rows]
    seconds = sum((f - st).total_seconds() for _, _, _, _, _, _, st, f in jobs if st and f)
    failed = [
        {"id": jid, "kind": kind, "label": label(kind), "project_id": pid, "project_title": title,
         "error": (error or "")[:300]}
        for jid, kind, pid, title, status, error, _, _ in jobs if status == "failed"
    ]
    return {"date": day.isoformat(), "published": published, "jobs": len(jobs), "seconds": round(seconds),
            "failed": failed}


def summary(now: datetime | None = None) -> dict[str, Any]:
    from studio.workers.ops.pending import list_pending
    from studio.workers.ops.routines import public_state
    from studio.workers.ops.stock import stock
    from studio.workers.ops.velocity import report
    from studio.workers.series.core import today as series_today

    vel = report(days=7, now=now)
    return {
        "date": _local(now).date().isoformat(),
        "waiting": waiting(now),
        "pending": list_pending("aberta"),
        "stock": stock(now),
        "series": series_today(),
        "yesterday": yesterday(now),
        "routines": public_state(),
        "time": {"days": vel["days"], "total_s": vel["total_s"], "steps": vel["steps"][:3],
                 "suggestions": vel["suggestions"]},
    }
