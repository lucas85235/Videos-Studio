"""Receita do YouTube pelo YouTube Analytics (opcional: escopo `yt-analytics-monetary.readonly`, escolhido ao vincular).

- Por vídeo: `estimatedRevenue` (US$, receita líquida estimada) com a dimensão `video` (relatório "Top videos"), gravada
  em `VideoMetric.revenue_usd`, e o RPM calculado (receita ÷ views × 1000) em `VideoMetric.rpm`.
- Por mês: `estimatedRevenue` por dia (`dimensions=day`) dos 3 últimos meses, somado por mês e gravado na receita
  mensal (`RevenueEntry`, loja "youtube", linha de total, US$, na coluna `commission`, que é o que o canal ganha).
  O contrato com o bloco D é só a tabela (ADR 0002: nada de import entre blocos).
- Coletar de novo substitui o número do mês (o Google ajusta a receita no fechamento): não duplica.
- Lançamento à mão manda: se a linha "youtube" do mês já existe e não foi escrita pela coleta, ela fica como está.
  A coleta guarda em `Channel.settings["youtube_revenue"]["months"]` o número que gravou em cada mês; a linha só é
  da coleta se ainda tem a nota automática, está em US$ e com esse mesmo número. Corrigir o valor em Dinheiro (mesmo
  com a observação vazia, que não troca a nota) já basta para a coleta não mexer mais no mês.
- Erro de qualquer tipo vira estado (nunca exceção): a coleta das views segue.
Sem o escopo, nada disso roda e a receita continua sendo lançada à mão.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, datetime
from typing import Any

import httpx

from studio.db import session_scope
from studio.models import Channel, RevenueEntry
from studio.workers.publish import youtube as yt

STORE = "youtube"
AUTO_NOTE = "coletado do YouTube Analytics"
MONTHS = 3
BATCH = 50
SETTINGS_KEY = "youtube_revenue"
log = logging.getLogger(__name__)
# Estados mostrados em Desempenho.
OK, DESLIGADA, SEM_ESCOPO, ERRO = "ok", "desligada", "sem_escopo", "erro"


class RevenueScopeMissing(Exception):
    """O token perdeu o escopo da receita (vincular de novo marcando a receita resolve)."""


def _query(http: httpx.Client, token: str, params: dict[str, str]) -> list[dict[str, Any]]:
    try:
        resp = http.get(yt.ANALYTICS_API, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise yt.YouTubeError(f"sem resposta do YouTube Analytics: {exc}") from exc
    if resp.status_code != 200:
        text = resp.text[:300]
        if resp.status_code == 401 or "insufficientPermissions" in text or "ACCESS_TOKEN_SCOPE_INSUFFICIENT" in text:
            raise RevenueScopeMissing(text)
        if resp.status_code == 403:
            raise yt.YouTubeError("o YouTube recusou a receita (o canal está no Programa de Parcerias?): " + text)
        raise yt.YouTubeError(f"erro do YouTube Analytics na receita ({resp.status_code}): {text}")
    data = resp.json()
    names = [h.get("name") for h in data.get("columnHeaders") or []]
    return [dict(zip(names, row, strict=False)) for row in data.get("rows") or []]


def _f(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def per_video(http: httpx.Client, token: str, ids: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
    """`{vídeo: {"revenue_usd", "rpm"}}` no período (o mesmo das views da coleta)."""
    out: dict[str, dict[str, float]] = {}
    for i in range(0, len(ids), BATCH):
        rows = _query(http, token, {
            "ids": "channel==MINE", "startDate": start, "endDate": end,
            "metrics": "views,estimatedRevenue", "dimensions": "video",
            "filters": "video==" + ",".join(ids[i:i + BATCH]), "sort": "-views", "maxResults": "200",
        })
        for row in rows:
            vid, revenue, views = row.get("video"), _f(row.get("estimatedRevenue")), _f(row.get("views"))
            if not vid or revenue is None:
                continue
            item = {"revenue_usd": round(revenue, 2)}
            if views:
                item["rpm"] = round(revenue / views * 1000, 2)
            out[str(vid)] = item
    return out


def _months_back(today: date, n: int) -> date:
    month = today.month - (n - 1)
    year = today.year
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1)


def per_month(http: httpx.Client, token: str, today: date) -> dict[str, float]:
    """`{"AAAA-MM": US$}` dos últimos `MONTHS` meses (o mês atual vai até hoje)."""
    rows = _query(http, token, {
        "ids": "channel==MINE", "startDate": _months_back(today, MONTHS).isoformat(), "endDate": today.isoformat(),
        "metrics": "estimatedRevenue", "dimensions": "day", "sort": "day",
    })
    sums: dict[str, float] = defaultdict(float)
    for row in rows:
        day, revenue = str(row.get("day") or ""), _f(row.get("estimatedRevenue"))
        if len(day) >= 7 and revenue is not None:
            sums[day[:7]] += revenue
    return {k: round(v, 2) for k, v in sums.items()}


def _written_by_collect(row: RevenueEntry, last: float | None) -> bool:
    """A linha ainda é a que a coleta gravou (ninguém corrigiu em Dinheiro)."""
    if (row.note or "") != AUTO_NOTE or (row.currency or "") != "USD":
        return False
    if last is None:
        return True  # gravada antes de a coleta guardar o número: vale a nota
    return row.commission is not None and abs(float(row.commission) - float(last)) < 0.005


def save_months(channel_id: str, months: dict[str, float]) -> dict[str, int]:
    """Grava a receita de cada mês na linha "youtube" do canal. Linha lançada ou corrigida à mão não é tocada."""
    saved = kept = 0
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            return {"saved": 0, "kept_manual": 0}
        settings = dict(channel.settings or {})
        state = dict(settings.get(SETTINGS_KEY) or {})
        written: dict[str, float] = dict(state.get("months") or {})
        for period, value in months.items():
            row = (s.query(RevenueEntry)
                   .filter_by(channel_id=channel_id, store=STORE, period=period, tracking_id="").one_or_none())
            if row is None:
                s.add(RevenueEntry(channel_id=channel_id, store=STORE, period=period, tracking_id="",
                                   commission=value, currency="USD", note=AUTO_NOTE))
            elif _written_by_collect(row, written.get(period)):
                row.commission = value  # o Google ajusta o mês: o número novo substitui (não soma)
                row.currency = "USD"
            else:
                kept += 1
                written.pop(period, None)  # passou a ser da pessoa
                continue
            written[period] = value
            saved += 1
        state["months"] = written
        settings[SETTINGS_KEY] = state
        channel.settings = settings  # JSON novo inteiro: o SQLAlchemy só vê a troca
    return {"saved": saved, "kept_manual": kept}


def collect_revenue(channel_id: str, http: httpx.Client, token: str, account: str, ids: list[str], start: str,
                    now: datetime) -> tuple[str, dict[str, dict[str, float]], str]:
    """(estado, receita por vídeo, mensagem). Sem o escopo escolhido ao vincular: "desligada", sem chamar nada."""
    if not yt.has_revenue_scope(account):
        if yt.revenue_scope_denied(account):
            return SEM_ESCOPO, {}, "a receita do YouTube não foi autorizada no login do Google"
        return DESLIGADA, {}, ""
    try:
        videos = per_video(http, token, ids, start, now.date().isoformat()) if ids else {}
        result = save_months(channel_id, per_month(http, token, now.date()))
    except RevenueScopeMissing as exc:
        return SEM_ESCOPO, {}, str(exc)
    except yt.YouTubeError as exc:
        return ERRO, {}, str(exc)
    except Exception as exc:  # noqa: BLE001 - resposta estranha do Google não derruba a coleta das views
        log.warning("receita do canal %s falhou: %s", channel_id, exc)
        return ERRO, {}, f"resposta inesperada do YouTube Analytics na receita: {exc}"
    msg = (f"{result['kept_manual']} mês(es) com receita do YouTube lançada à mão ficaram como estavam"
           if result["kept_manual"] else "")
    return OK, videos, msg
