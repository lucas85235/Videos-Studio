"""Receita por loja e mês (bloco D, ADR 0002). Origem: `cmd_dinheiro_receita` e `receita_canal` do `estudio/estudio.py`.

- Uma linha por (canal, loja, mês, ID de rastreamento); `tracking_id` vazio é a linha de total da loja.
- Lançar de novo a mesma chave atualiza só os números enviados (COALESCE: nunca apaga um número com vazio).
- O resumo do mês soma as linhas por ID de rastreamento e ignora a linha de total quando há linhas por ID, para não
  contar duas vezes.
"""

from __future__ import annotations

import re
from typing import Any

from studio.db import session_scope
from studio.models import Channel, RevenueEntry

PERIOD_RE = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
NUMBERS = ("clicks", "orders", "sales", "commission")


class RevenueError(ValueError):
    pass


def entry_dict(row: RevenueEntry) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "store": row.store,
        "period": row.period,
        "tracking_id": row.tracking_id or "",
        "clicks": row.clicks,
        "orders": row.orders,
        "sales": row.sales,
        "commission": row.commission,
        "currency": row.currency or "BRL",
        "note": row.note or "",
    }


def upsert(channel_id: str, store: str, period: str, tracking_id: str = "", *, clicks: int | None = None,
           orders: int | None = None, sales: float | None = None, commission: float | None = None,
           currency: str = "BRL", note: str | None = None) -> dict[str, Any]:
    if not PERIOD_RE.fullmatch(period or ""):
        raise RevenueError("período no formato AAAA-MM (ex.: 2026-11)")
    store = (store or "").strip().lower()
    if not store:
        raise RevenueError("diga a loja (ex.: amazon)")
    values = {"clicks": clicks, "orders": orders, "sales": sales, "commission": commission}
    if all(v is None for v in values.values()):
        raise RevenueError("diga ao menos um número: cliques, pedidos, vendas ou comissão")
    tracking_id = (tracking_id or "").strip()
    currency = (currency or "BRL").strip().upper()[:3]
    with session_scope() as s:
        if s.get(Channel, channel_id) is None:
            raise LookupError(channel_id)
        row = s.query(RevenueEntry).filter_by(channel_id=channel_id, store=store, period=period,
                                              tracking_id=tracking_id).one_or_none()
        if row is None:
            row = RevenueEntry(channel_id=channel_id, store=store, period=period, tracking_id=tracking_id,
                               currency=currency, note=note or "", **values)
            s.add(row)
        else:
            for key, value in values.items():
                if value is not None:
                    setattr(row, key, value)
            row.currency = currency
            if note is not None:
                row.note = note
        s.flush()
        return entry_dict(row)


def list_entries(channel_id: str | None = None, period: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(RevenueEntry)
        if channel_id:
            q = q.filter(RevenueEntry.channel_id == channel_id)
        if period:
            q = q.filter(RevenueEntry.period == period)
        rows = q.order_by(RevenueEntry.period.desc(), RevenueEntry.store, RevenueEntry.tracking_id).all()
        return [entry_dict(r) for r in rows]


def delete(entry_id: int) -> None:
    with session_scope() as s:
        row = s.get(RevenueEntry, entry_id)
        if row is None:
            raise LookupError(entry_id)
        s.delete(row)


def summary(channel_id: str, months: int = 12) -> list[dict[str, Any]]:
    """Receita por mês, loja e moeda, do mês mais recente para o mais antigo (só os `months` meses mais recentes)."""
    groups: dict[tuple[str, str, str], list[RevenueEntry]] = {}
    with session_scope() as s:
        rows = (s.query(RevenueEntry).filter_by(channel_id=channel_id)
                .order_by(RevenueEntry.period.desc(), RevenueEntry.store).all())
        for r in rows:
            groups.setdefault((r.period, r.store, r.currency or "BRL"), []).append(r)
        out = []
        for (period, store, currency), items in groups.items():
            by_id = [r for r in items if r.tracking_id] or items

            def total(key: str, rows: list[RevenueEntry] = by_id) -> float | int | None:
                vals = [getattr(r, key) for r in rows if getattr(r, key) is not None]
                return sum(vals) if vals else None

            out.append({
                "period": period, "store": store, "currency": currency,
                **{k: total(k) for k in NUMBERS},
                "by_tracking": [{"tracking_id": r.tracking_id, **{k: getattr(r, k) for k in NUMBERS}}
                                for r in items if r.tracking_id],
            })
    seen = sorted({o["period"] for o in out}, reverse=True)[: max(1, months)]
    return [o for o in out if o["period"] in seen]
