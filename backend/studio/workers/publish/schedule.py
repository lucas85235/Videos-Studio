"""Agenda de publicação: horários semanais por canal (com fuso), próximo horário livre e visão geral.

Os horários ficam em `Channel.settings["posting"]`:

    {"timezone": "America/Sao_Paulo", "slots": [{"weekdays": [0, 2, 4], "time": "18:00"}]}

`weekdays` usa 0 = segunda ... 6 = domingo. O próximo horário livre ignora horários a menos de
20 min de agora (o envio precisa terminar antes) e horários a menos de 45 min de outra publicação
agendada do mesmo canal.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, ValidationError, field_validator

from studio.db import session_scope
from studio.models import Channel, Job, Project, Publication

DEFAULT_TZ = "America/Sao_Paulo"
MIN_LEAD = timedelta(minutes=20)
CONFLICT = timedelta(minutes=45)

# Status de publicação
SCHEDULED = "scheduled"  # agendado no YouTube (sai sozinho)
UPLOADED = "uploaded"  # enviado (privado, não listado ou público)
PACKAGE_READY = "package_ready"  # pacote manual pronto, falta postar
PUBLISHED = "published"  # postado à mão e marcado como feito


class Slot(BaseModel):
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)))
    time: str = "18:00"

    @field_validator("time")
    @classmethod
    def _hhmm(cls, value: str) -> str:
        try:
            hour, minute = (int(part) for part in value.strip().split(":"))
        except ValueError as exc:
            raise ValueError("horário no formato HH:MM") from exc
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError("horário inválido")
        return f"{hour:02d}:{minute:02d}"

    @field_validator("weekdays")
    @classmethod
    def _days(cls, value: list[int]) -> list[int]:
        days = sorted({d for d in value if 0 <= d <= 6})
        if not days:
            raise ValueError("escolha pelo menos um dia da semana")
        return days

    def clock(self) -> time:
        hour, minute = (int(part) for part in self.time.split(":"))
        return time(hour, minute)


class PostingPlan(BaseModel):
    timezone: str = DEFAULT_TZ
    slots: list[Slot] = []

    @field_validator("timezone")
    @classmethod
    def _tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"fuso horário desconhecido: {value}") from exc
        return value


def plan_of(channel_settings: dict[str, Any] | None) -> PostingPlan:
    raw = (channel_settings or {}).get("posting") or {}
    try:
        return PostingPlan.model_validate(raw)
    except ValidationError:
        return PostingPlan()


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def next_free_slots(
    plan: PostingPlan,
    taken: list[datetime],
    *,
    after: datetime | None = None,
    count: int = 5,
    horizon_days: int = 60,
) -> list[datetime]:
    """Próximos horários do plano que não colidem com publicações já agendadas."""
    if not plan.slots:
        return []
    tz = ZoneInfo(plan.timezone)
    now = _aware(after or datetime.now(UTC)).astimezone(tz)
    earliest = now + MIN_LEAD
    busy = [_aware(t).astimezone(UTC) for t in taken]
    found: list[datetime] = []
    for offset in range(horizon_days + 1):
        day = (now + timedelta(days=offset)).date()
        times = sorted({slot.clock() for slot in plan.slots if day.weekday() in slot.weekdays})
        for clock in times:
            when = datetime.combine(day, clock, tzinfo=tz)
            if when < earliest:
                continue
            if any(abs(when.astimezone(UTC) - t) < CONFLICT for t in busy):
                continue
            found.append(when)
            if len(found) >= count:
                return found
    return found


def taken_for_channel(channel_id: str, now: datetime | None = None) -> list[datetime]:
    """Horários já agendados (YouTube ou postagem manual planejada) dos projetos do canal."""
    since = _aware(now or datetime.now(UTC)) - timedelta(days=1)
    with session_scope() as s:
        rows = (
            s.query(Publication.scheduled_at)
            .join(Project, Project.id == Publication.project_id)
            .filter(Project.channel_id == channel_id, Publication.scheduled_at.isnot(None))
            .filter(Publication.status.in_([SCHEDULED, PACKAGE_READY]))
            .all()
        )
    return [t for (t,) in rows if t is not None and _aware(t) >= since]


def channel_slots(channel_id: str, count: int = 5) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        plan = plan_of(row.settings)
    slots = next_free_slots(plan, taken_for_channel(channel_id), count=count)
    return {"timezone": plan.timezone, "configured": bool(plan.slots), "slots": [t.isoformat() for t in slots]}


def _item(pub: Publication, project: Project, channels: dict[str, str], now: datetime) -> dict[str, Any]:
    scheduled = _aware(pub.scheduled_at) if pub.scheduled_at else None
    status = pub.status
    if status == SCHEDULED and scheduled and scheduled <= now:
        status = PUBLISHED  # o YouTube publicou no horário agendado
    details = pub.details or {}
    return {
        "id": pub.id,
        "project_id": project.id,
        "project_title": project.title,
        "channel_id": project.channel_id,
        "channel_name": channels.get(project.channel_id or "", ""),
        "platform": pub.platform,
        "language": pub.language,
        "driver": pub.driver,
        "status": status,
        "scheduled_at": scheduled.isoformat() if scheduled else None,
        "created_at": _aware(pub.created_at).isoformat() if pub.created_at else None,
        "published_at": details.get("published_at"),
        "url": pub.url,
        "package_path": pub.package_path,
        "caption": details.get("caption"),
        "upload_url": details.get("url"),
        "privacy": details.get("privacy"),
    }


def agenda(days_back: int = 14, now: datetime | None = None) -> dict[str, Any]:
    """Próximas publicações, pacotes manuais por postar e o que saiu recentemente."""
    now = _aware(now or datetime.now(UTC))
    since = now - timedelta(days=days_back)
    with session_scope() as s:
        channels = {c.id: c.name for c in s.query(Channel).all()}
        pairs = s.query(Publication, Project).join(Project, Project.id == Publication.project_id).all()
        items = [_item(pub, project, channels, now) for pub, project in pairs]

    def when(item: dict[str, Any]) -> datetime:
        stamp = item["scheduled_at"] or item["published_at"] or item["created_at"]
        return _aware(datetime.fromisoformat(stamp)) if stamp else since

    upcoming = sorted((i for i in items if i["status"] == SCHEDULED), key=when)
    manual = sorted((i for i in items if i["status"] == PACKAGE_READY), key=lambda i: (i["scheduled_at"] is None, when(i)))
    recent = sorted(
        (i for i in items if i["status"] in (UPLOADED, PUBLISHED) and when(i) >= since),
        key=when,
        reverse=True,
    )
    return {"now": now.isoformat(), "upcoming": upcoming, "manual": manual, "recent": recent}


def mark_published(publication_id: str, url: str | None = None) -> dict[str, Any]:
    """Marca um pacote manual como postado (com o link do post, se houver)."""
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        if row is None:
            raise KeyError(publication_id)
        row.status = PUBLISHED
        if url:
            row.url = url.strip()
        row.details = {**(row.details or {}), "published_at": datetime.now(UTC).isoformat()}
        return {"id": row.id, "status": row.status, "url": row.url}


# --------------------------------------------------------------------------------------------
# Fila do YouTube: agendar vários vídeos de uma vez nos próximos horários livres
# --------------------------------------------------------------------------------------------


def _pending_publish_jobs() -> list[tuple[str | None, str | None, str | None]]:
    """(projeto, idioma, publish_at) dos envios ao YouTube ainda na fila ou rodando."""
    with session_scope() as s:
        rows = s.query(Job).filter(Job.kind == "publish_youtube", Job.status.in_(("queued", "running"))).all()
        return [(r.project_id, (r.payload or {}).get("lang"), (r.payload or {}).get("publish_at")) for r in rows]


def ready_for_youtube() -> list[dict[str, Any]]:
    """Vídeos finais renderizados que ainda não foram enviados ao YouTube (nem estão na fila)."""
    pending = {(pid, lang) for pid, lang, _ in _pending_publish_jobs()}
    with session_scope() as s:
        channels = {c.id: c.name for c in s.query(Channel).all()}
        sent = {
            (pub.project_id, pub.language)
            for pub in s.query(Publication).filter(Publication.platform == "youtube", Publication.driver == "youtube_native").all()
        }
        out: list[dict[str, Any]] = []
        for project in s.query(Project).all():
            last = (project.settings or {}).get("last_final") or {}
            for lang, entry in (last.get("langs") or {}).items():
                key = (project.id, lang)
                if not isinstance(entry, dict) or not entry.get("video") or key in sent or key in pending:
                    continue
                out.append(
                    {
                        "project_id": project.id,
                        "title": project.title,
                        "lang": lang,
                        "channel_id": project.channel_id,
                        "channel_name": channels.get(project.channel_id or "", ""),
                        "duration": entry.get("duration"),
                        "preview_only": bool(last.get("preview_only_providers")),
                    }
                )
    return sorted(out, key=lambda i: (i["channel_name"], i["title"], i["lang"]))


def plan_youtube_batch(
    items: list[dict[str, str]],
    submit: Callable[[str, dict[str, Any], str], dict[str, Any]],
) -> list[dict[str, Any]]:
    """Agenda cada vídeo (na ordem dada) no próximo horário livre do canal dele e enfileira o envio."""
    ready = {(r["project_id"], r["lang"]): r for r in ready_for_youtube()}
    pending_times = [_aware(datetime.fromisoformat(at.replace("Z", "+00:00"))) for _, _, at in _pending_publish_jobs() if at]
    assigned: dict[str, list[datetime]] = {}
    results: list[dict[str, Any]] = []
    for item in items:
        project_id, lang = item.get("project_id", ""), item.get("lang", "")
        base = {"project_id": project_id, "lang": lang}
        info = ready.get((project_id, lang))
        if info is None:
            results.append({**base, "error": "sem vídeo final novo neste idioma (ou já enviado/na fila)"})
            continue
        base.update(title=info["title"], channel_name=info["channel_name"])
        if info["preview_only"]:
            results.append({**base, "error": "usou provedores só de prévia: renderize com voz e imagens comerciais antes de agendar"})
            continue
        channel_id = info["channel_id"]
        if not channel_id:
            results.append({**base, "error": "o projeto não tem canal; escolha um canal com horários de postagem"})
            continue
        with session_scope() as s:
            channel = s.get(Channel, channel_id)
            plan = plan_of(channel.settings if channel else None)
        if not plan.slots:
            results.append({**base, "error": "o canal não tem horários de postagem (configure em Canais)"})
            continue
        taken = taken_for_channel(channel_id) + pending_times + assigned.get(channel_id, [])
        slots = next_free_slots(plan, taken, count=1)
        if not slots:
            results.append({**base, "error": "nenhum horário livre nos próximos 60 dias"})
            continue
        when = slots[0]
        payload = {"lang": lang, "privacy": "scheduled", "publish_at": when.astimezone(UTC).isoformat().replace("+00:00", "Z")}
        job = submit("publish_youtube", payload, project_id)
        assigned.setdefault(channel_id, []).append(when)
        results.append({**base, "publish_at": when.isoformat(), "job_id": job["id"]})
    return results
