"""Estoque de vídeos de cada canal (origem: "Estoque" do resumo da manhã em estudio/rotinas.py).

Conta, por canal com estoque mínimo (`Channel.min_stock > 0`), os projetos no formato do estoque
(`Channel.stock_format`: `short`, `long` ou `any`) que estão prontos (vídeo final renderizado e ainda não enviado,
agendado nem postado) mais os que têm publicação agendada no futuro. Abaixo do mínimo = alerta.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from studio.db import session_scope
from studio.models import Channel, Project, Publication
from studio.workers.publish.schedule import PACKAGE_READY, PUBLISHED, SCHEDULED, UPLOADED

log = logging.getLogger(__name__)

READY_STATUS = "rendered"  # vídeo final pronto
GONE = (SCHEDULED, UPLOADED, PUBLISHED)  # já saiu (ou vai sair sozinho) e não está mais "na prateleira"


def _gone(status: str, details: dict[str, Any] | None) -> bool:
    """A publicação tirou o vídeo da prateleira? Envio privado (UPLOADED com privacy=private) não: o vídeo não saiu."""
    if status not in GONE:
        return False
    return not (status == UPLOADED and (details or {}).get("privacy") == "private")
FORMAT_LABELS = {"short": "Shorts", "long": "vídeos longos", "any": "vídeos"}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def project_format(project_id: str, style_id: str, settings: dict[str, Any] | None) -> str | None:
    """`short` ou `long` pelo estilo do projeto (com os ajustes do próprio projeto); Short cortado do longo é short."""
    if (settings or {}).get("short_of"):
        return "short"
    try:
        from studio.core.project import service
        from studio.core.styles.library import get_style

        doc = service.get_doc(project_id)
        return get_style(doc.style_id, doc.style_overrides).format.type
    except Exception as exc:  # noqa: BLE001 - projeto quebrado não derruba o resumo
        log.debug("formato do projeto %s (%s) não lido: %s", project_id, style_id, exc)
        return None


def channel_stock(channel: Channel, now: datetime | None = None) -> dict[str, Any]:
    now = _aware(now or datetime.now(UTC))
    fmt = channel.stock_format or "short"
    with session_scope() as s:
        projects = s.query(Project).filter(Project.channel_id == channel.id).all()
        pubs = (
            s.query(Publication)
            .join(Project, Project.id == Publication.project_id)
            .filter(Project.channel_id == channel.id)
            .all()
        )
        info = {p.id: (p.style_id, dict(p.settings or {}), p.status) for p in projects}
        pub_rows = [(p.project_id, p.status, p.scheduled_at, dict(p.details or {})) for p in pubs]
    gone = {pid for pid, status, _, details in pub_rows if _gone(status, details)}
    scheduled = {
        pid for pid, status, when, _ in pub_rows
        if status in (SCHEDULED, PACKAGE_READY) and when is not None and _aware(when) > now
    }
    ready = {pid for pid, (_, _, status) in info.items() if status == READY_STATUS and pid not in gone}
    counted: set[str] = set()
    for pid in ready | scheduled:
        if pid not in info:
            continue
        style_id, settings, _ = info[pid]
        if fmt == "any" or project_format(pid, style_id, settings) == fmt:
            counted.add(pid)
    n_ready = len(counted & ready)
    total = len(counted)
    minimum = channel.min_stock or 0
    return {
        "channel_id": channel.id,
        "channel_name": channel.name,
        "format": fmt,
        "format_label": FORMAT_LABELS.get(fmt, "vídeos"),
        "ready": n_ready,
        "scheduled": total - n_ready,
        "total": total,
        "min_stock": minimum,
        "below": total < minimum,
    }


def stock(now: datetime | None = None) -> list[dict[str, Any]]:
    """Estoque dos canais que têm mínimo configurado, por nome do canal."""
    with session_scope() as s:
        channels = s.query(Channel).filter(Channel.min_stock > 0).order_by(Channel.name).all()
        s.expunge_all()
    return [channel_stock(c, now) for c in channels]
