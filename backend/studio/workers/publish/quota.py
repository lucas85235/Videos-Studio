"""Medidor da cota da YouTube Data API v3.

A cota é do projeto do Google Cloud (o mesmo client_id serve a todas as contas e canais vinculados), conta por dia e
zera à meia-noite do horário do Pacífico. Custos conforme a documentação oficial
(developers.google.com/youtube/v3/determine_quota_cost, conferida em 15/11/2026):

- 10.000 unidades por dia para todos os métodos, menos `videos.insert` e `search.list`;
- `videos.insert` e `search.list` têm a própria cota: 100 chamadas por dia cada (1 unidade por chamada);
- leituras (`*.list`) custam 1; `playlists.insert`, `playlistItems.insert` e `thumbnails.set` custam 50;
  `captions.insert` custa 400.

O app só conta o que ele mesmo chamou: outro programa usando o mesmo projeto do Google Cloud não aparece aqui.
Quando o Google responde `quotaExceeded`, o dia fica marcado como esgotado, mesmo que a conta local seja menor.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.exc import IntegrityError

from studio.db import session_scope
from studio.models import AppSetting, YouTubeQuotaUsage

PACIFIC = ZoneInfo("America/Los_Angeles")

DAILY_LIMIT = 10_000
UPLOAD_LIMIT = 100
NEAR_FRACTION = 0.8

GENERAL = "general"
UPLOADS = "uploads"
SEARCH = "search"

# método -> (unidades por chamada, balde da cota)
COSTS: dict[str, tuple[int, str]] = {
    "channels.list": (1, GENERAL),
    "playlists.list": (1, GENERAL),
    "playlists.insert": (50, GENERAL),
    "playlistItems.list": (1, GENERAL),
    "playlistItems.insert": (50, GENERAL),
    "videos.list": (1, GENERAL),
    "videos.insert": (1, UPLOADS),
    "thumbnails.set": (50, GENERAL),
    "captions.insert": (400, GENERAL),
    "search.list": (1, SEARCH),
}

LIMIT_KEY = "youtube_quota_limit"
EXHAUSTED_KEY = "youtube_quota_exhausted"


class QuotaExhausted(RuntimeError):
    pass


def pacific_day(now: datetime | None = None) -> str:
    """Dia da cota (AAAA-MM-DD no horário do Pacífico, com horário de verão)."""
    moment = now or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(PACIFIC).date().isoformat()


def resets_at(now: datetime | None = None) -> datetime:
    """Próxima meia-noite do Pacífico, em UTC."""
    moment = (now or datetime.now(UTC)).astimezone(PACIFIC)
    tomorrow = moment.date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=PACIFIC).astimezone(UTC)


def cost(method: str) -> int:
    return COSTS.get(method, (1, GENERAL))[0]


def _setting(key: str) -> Any:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        return row.value if row is not None else None


def _set_setting(key: str, value: Any) -> None:
    with session_scope() as s:
        row = s.get(AppSetting, key)
        if row is None:
            s.add(AppSetting(key=key, value=value))
        else:
            row.value = value


def daily_limit() -> int:
    """10.000, ou o limite maior que o Google liberou para o seu projeto (Configurações)."""
    value = _setting(LIMIT_KEY)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DAILY_LIMIT
    return number if number > 0 else DAILY_LIMIT


def set_daily_limit(limit: int | None) -> int:
    if limit is not None and not 1 <= limit <= 100_000_000:
        raise ValueError("limite de cota inválido")
    _set_setting(LIMIT_KEY, limit)
    return daily_limit()


def spend(method: str, calls: int = 1, now: datetime | None = None) -> None:
    """Soma o custo de uma chamada à API (o Google cobra mesmo quando a chamada falha)."""
    day = pacific_day(now)
    units = cost(method) * calls
    for _ in range(2):  # duas tentativas: outro envio pode criar a linha do dia ao mesmo tempo
        try:
            with session_scope() as s:
                row = s.query(YouTubeQuotaUsage).filter_by(day=day, method=method).one_or_none()
                if row is None:
                    s.add(YouTubeQuotaUsage(day=day, method=method, calls=calls, units=units))
                else:
                    row.calls += calls
                    row.units += units
            return
        except IntegrityError:
            continue


def mark_exhausted(now: datetime | None = None) -> None:
    """O Google respondeu quotaExceeded: o dia fica esgotado até a meia-noite do Pacífico."""
    _set_setting(EXHAUSTED_KEY, pacific_day(now))


def publish_estimate(*, thumbnail: bool = True, captions: bool = True, playlist: bool = False) -> int:
    """Unidades da cota geral que um envio gasta (o vídeo em si conta na cota de uploads)."""
    return (cost("thumbnails.set") if thumbnail else 0) + (cost("captions.insert") if captions else 0) + (
        cost("playlistItems.insert") if playlist else 0)


def status(now: datetime | None = None) -> dict[str, Any]:
    """O que a tela mostra: gasto de hoje, limite, nível (ok, near, over) e quando zera."""
    day = pacific_day(now)
    with session_scope() as s:
        rows = [(r.method, r.calls, r.units) for r in s.query(YouTubeQuotaUsage).filter_by(day=day).all()]
    limit = daily_limit()
    used = sum(units for method, _, units in rows if COSTS.get(method, (1, GENERAL))[1] == GENERAL)
    uploads = sum(calls for method, calls, _ in rows if method == "videos.insert")
    exhausted = _setting(EXHAUSTED_KEY) == day
    over = exhausted or used >= limit or uploads >= UPLOAD_LIMIT
    near = not over and (used >= limit * NEAR_FRACTION or uploads >= UPLOAD_LIMIT * NEAR_FRACTION)
    reset = resets_at(now)
    if exhausted:
        message = "O Google avisou que a cota do YouTube de hoje acabou. Ela volta à meia-noite do horário do Pacífico."
    elif uploads >= UPLOAD_LIMIT:
        message = f"Os {UPLOAD_LIMIT} envios de vídeo do dia já foram usados. A cota volta à meia-noite do horário do Pacífico."
    elif used >= limit:
        message = "A cota do YouTube de hoje acabou. Ela volta à meia-noite do horário do Pacífico."
    elif near:
        message = f"Perto do limite: {used:,} de {limit:,} unidades usadas hoje.".replace(",", ".")
    else:
        message = None
    return {
        "day": day,
        "resets_at": reset.isoformat().replace("+00:00", "Z"),
        "limit": limit,
        "custom_limit": limit != DAILY_LIMIT,
        "used": used,
        "remaining": max(0, limit - used),
        "uploads": uploads,
        "upload_limit": UPLOAD_LIMIT,
        "exhausted": exhausted,
        "level": "over" if over else "near" if near else "ok",
        "message": message,
        "publish_estimate": publish_estimate(),
        "by_method": sorted(
            ({"method": m, "calls": c, "units": u} for m, c, u in rows), key=lambda r: (-r["units"], r["method"])
        ),
    }


def check_publish(estimate: int, now: datetime | None = None) -> list[str]:
    """Antes de um envio: erro se a cota já acabou; aviso se o envio deve passar do limite."""
    info = status(now)
    if info["level"] == "over":
        raise QuotaExhausted(info["message"])
    if info["used"] + estimate > info["limit"]:
        return [f"este envio gasta cerca de {estimate} unidades e restam {info['remaining']} hoje: "
                "a capa, as legendas ou a playlist podem falhar"]
    return []
