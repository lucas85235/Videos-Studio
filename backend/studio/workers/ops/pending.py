"""Pendências do canal (origem: tabela `pendencias` do canais).

Pendência à mão (`kind="manual"`) ou aberta por rotina (`kind="auto"`, com `auto_key` única: rodar a rotina de novo
não duplica, e uma pendência já feita ou descartada não reabre). Na API o "onde fazer" se chama `where`; no banco,
`where_hint`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, PendingTask

STATUSES = ("aberta", "feita", "descartada")


def pending_dict(row: PendingTask) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "text": row.text,
        "where": row.where_hint or "",
        "kind": row.kind,
        "auto_key": row.auto_key,
        "status": row.status,
        "order": row.order or 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "done_at": row.done_at.isoformat() if row.done_at else None,
    }


def _check_channel(s: Any, channel_id: str | None) -> None:
    if channel_id and s.get(Channel, channel_id) is None:
        raise KeyError(f"canal não encontrado: {channel_id}")


def list_pending(status: str = "aberta", channel_id: str | None = None) -> list[dict[str, Any]]:
    """Pendências por status ("todas" traz todas), na ordem de criação (`order`)."""
    with session_scope() as s:
        query = s.query(PendingTask)
        if status != "todas":
            query = query.filter(PendingTask.status == status)
        if channel_id:
            query = query.filter(PendingTask.channel_id == channel_id)
        rows = query.order_by(PendingTask.order, PendingTask.created_at).all()
        return [pending_dict(r) for r in rows]


def get_pending(pending_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(PendingTask, pending_id)
        if row is None:
            raise KeyError(pending_id)
        return pending_dict(row)


def _next_order(s: Any) -> int:
    return int(s.query(func.coalesce(func.max(PendingTask.order), 0)).scalar() or 0) + 1


def create_pending(text: str, where: str = "", channel_id: str | None = None, *, kind: str = "manual",
                   auto_key: str | None = None) -> dict[str, Any]:
    text = text.strip()
    if not text:
        raise ValueError("a pendência precisa de um texto")
    with session_scope() as s:
        _check_channel(s, channel_id)
        row = PendingTask(id=new_id("pen"), channel_id=channel_id, text=text, where_hint=(where or "").strip(),
                          kind=kind, auto_key=auto_key, status="aberta", order=_next_order(s))
        s.add(row)
        s.flush()
        return pending_dict(row)


def update_pending(pending_id: str, *, text: str | None = None, where: str | None = None,
                   channel_id: str | None = None, order: int | None = None,
                   fields: set[str] | None = None) -> dict[str, Any]:
    """Altera só os campos enviados (`fields`; sem ele, os que não são None)."""
    values = {"text": text, "where": where, "channel_id": channel_id, "order": order}
    chosen = fields if fields is not None else {k for k, v in values.items() if v is not None}
    with session_scope() as s:
        row = s.get(PendingTask, pending_id)
        if row is None:
            raise KeyError(pending_id)
        if "text" in chosen:
            new_text = (text or "").strip()
            if not new_text:
                raise ValueError("a pendência precisa de um texto")
            row.text = new_text
        if "where" in chosen:
            row.where_hint = (where or "").strip()
        if "channel_id" in chosen:
            _check_channel(s, channel_id)
            row.channel_id = channel_id or None
        if "order" in chosen and order is not None:
            row.order = order
        s.flush()
        return pending_dict(row)


def set_status(pending_id: str, status: str) -> dict[str, Any]:
    if status not in STATUSES:
        raise ValueError(f"status inválido: {status}")
    with session_scope() as s:
        row = s.get(PendingTask, pending_id)
        if row is None:
            raise KeyError(pending_id)
        row.status = status
        row.done_at = None if status == "aberta" else datetime.now(UTC)
        s.flush()
        return pending_dict(row)


def ensure_auto(auto_key: str, text: str, where: str = "", channel_id: str | None = None) -> dict[str, Any] | None:
    """Abre a pendência da rotina se a chave ainda não existe. Devolve a pendência criada, ou None se já havia uma
    (aberta, feita ou descartada: nunca reabre nem duplica)."""
    with session_scope() as s:
        if s.query(PendingTask.id).filter(PendingTask.auto_key == auto_key).first() is not None:
            return None
        if channel_id and s.get(Channel, channel_id) is None:
            channel_id = None
    try:
        return create_pending(text, where, channel_id, kind="auto", auto_key=auto_key)
    except IntegrityError:  # outra rotina criou a mesma chave ao mesmo tempo
        return None
