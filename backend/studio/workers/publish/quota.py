"""Medidor da cota da YouTube Data API v3.

A cota é do projeto do Google Cloud (o mesmo client_id serve a todas as contas e canais vinculados), conta por dia e
zera à meia-noite do horário do Pacífico. Custos conforme a documentação oficial
(developers.google.com/youtube/v3/determine_quota_cost, conferida em 15/11/2026):

- 10.000 unidades por dia para todos os métodos, menos `videos.insert` e `search.list`;
- `videos.insert` e `search.list` têm a própria cota: 100 chamadas por dia cada (1 unidade por chamada);
- leituras (`*.list`) custam 1; `playlists.insert`, `playlistItems.insert` e `thumbnails.set` custam 50;
  `captions.insert` custa 400.

O app só conta o que ele mesmo chamou: outro programa usando o mesmo projeto do Google Cloud não aparece aqui.
Chamada recusada (4xx/5xx) conta 1 unidade ("toda chamada, mesmo inválida, custa ao menos 1 ponto"), não o custo
cheio. Quando o Google responde `quotaExceeded`, o balde daquele método (geral ou de envios) fica esgotado no dia,
mesmo que a conta local seja menor.

Os baldes são separados: sem a cota geral o vídeo ainda sobe (só a capa, as legendas e a playlist falham); sem a
cota de envios nada sobe. Por isso só o balde de envios bloqueia o envio; o geral vira aviso.
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
SEARCH_LIMIT = 100  # chamadas de `search.list` por dia (balde próprio)
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
    "commentThreads.list": (1, GENERAL),  # caixa de comentários
    "comments.insert": (50, GENERAL),  # resposta pela caixa de comentários
}

LIMIT_KEY = "youtube_quota_limit"
UPLOAD_LIMIT_KEY = "youtube_upload_limit"
EXHAUSTED_KEY = "youtube_quota_exhausted"  # {"day": "AAAA-MM-DD", "buckets": ["general", "uploads"]}


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


def bucket(method: str) -> str:
    return COSTS.get(method, (1, GENERAL))[1]


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


def upload_limit() -> int:
    """100 envios de vídeo por dia, ou o número maior que o Google liberou para o seu projeto (Configurações)."""
    value = _setting(UPLOAD_LIMIT_KEY)
    try:
        number = int(value)
    except (TypeError, ValueError):
        return UPLOAD_LIMIT
    return number if number > 0 else UPLOAD_LIMIT


def set_upload_limit(limit: int | None) -> int:
    if limit is not None and not 1 <= limit <= 1_000_000:
        raise ValueError("limite de envios inválido")
    _set_setting(UPLOAD_LIMIT_KEY, limit)
    return upload_limit()


def spend(method: str, calls: int = 1, now: datetime | None = None, *, failed: bool = False) -> None:
    """Soma o custo de uma chamada à API. Chamada recusada pelo Google (`failed`) custa 1 unidade, não o custo cheio."""
    day = pacific_day(now)
    units = (1 if failed else cost(method)) * calls
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


def _exhausted_buckets(day: str) -> set[str]:
    value = _setting(EXHAUSTED_KEY)
    if isinstance(value, str):  # formato antigo (só o dia): sem saber o balde, vale para os dois
        return {GENERAL, UPLOADS} if value == day else set()
    if isinstance(value, dict) and value.get("day") == day:
        return {b for b in value.get("buckets") or [] if isinstance(b, str)}
    return set()


def mark_exhausted(bucket_name: str = GENERAL, now: datetime | None = None) -> None:
    """O Google respondeu quotaExceeded: aquele balde fica esgotado até a meia-noite do Pacífico."""
    day = pacific_day(now)
    buckets = _exhausted_buckets(day) | {bucket_name}
    _set_setting(EXHAUSTED_KEY, {"day": day, "buckets": sorted(buckets)})


def search_left(now: datetime | None = None) -> int:
    """Buscas (`search.list`) que ainda cabem hoje no balde próprio, pelo que o app contou (pesquisa e radar com o token
    de um canal vinculado). Zero se o Google já respondeu quotaExceeded nesse balde."""
    day = pacific_day(now)
    if SEARCH in _exhausted_buckets(day):
        return 0
    with session_scope() as s:
        row = s.query(YouTubeQuotaUsage).filter_by(day=day, method="search.list").one_or_none()
        calls = row.calls if row is not None else 0
    return max(0, SEARCH_LIMIT - calls)


def publish_estimate(*, thumbnail: bool = True, captions: bool = True, playlist: bool = False) -> int:
    """Unidades da cota geral que um envio gasta (o vídeo em si conta na cota de uploads)."""
    return (cost("thumbnails.set") if thumbnail else 0) + (cost("captions.insert") if captions else 0) + (
        cost("playlistItems.insert") if playlist else 0)


RESET = "Ela volta à meia-noite do horário do Pacífico."


def status(now: datetime | None = None) -> dict[str, Any]:
    """O que a tela mostra: gasto de hoje por balde, limites, nível (ok, near, over) e quando zera.

    `uploads_over` bloqueia o envio; `general_over` só avisa que capa, legendas e playlist vão falhar.
    """
    day = pacific_day(now)
    with session_scope() as s:
        rows = [(r.method, r.calls, r.units) for r in s.query(YouTubeQuotaUsage).filter_by(day=day).all()]
    limit = daily_limit()
    up_limit = upload_limit()
    used = sum(units for method, _, units in rows if bucket(method) == GENERAL)
    uploads = sum(calls for method, calls, _ in rows if method == "videos.insert")
    exhausted = _exhausted_buckets(day) & {GENERAL, UPLOADS}  # o balde das buscas não afeta os envios
    general_over = GENERAL in exhausted or used >= limit
    uploads_over = UPLOADS in exhausted or uploads >= up_limit
    over = general_over or uploads_over
    near = not over and (used >= limit * NEAR_FRACTION or uploads >= up_limit * NEAR_FRACTION)
    reset = resets_at(now)
    if UPLOADS in exhausted:
        message = f"O Google avisou que a cota de envios de vídeo de hoje acabou. {RESET}"
    elif uploads_over:
        message = f"Os {up_limit} envios de vídeo do dia já foram usados. {RESET}"
    elif GENERAL in exhausted:
        message = ("O Google avisou que a cota geral do YouTube de hoje acabou: o vídeo ainda sobe, mas a capa, "
                   f"as legendas e a playlist vão falhar. {RESET}")
    elif general_over:
        message = ("A cota geral do YouTube de hoje acabou: o vídeo ainda sobe, mas a capa, as legendas e a playlist "
                   f"vão falhar. {RESET}")
    elif near:
        message = (f"Perto do limite: {used:,} de {limit:,} unidades e {uploads} de {up_limit:,} envios "
                   "usados hoje.").replace(",", ".")
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
        "upload_limit": up_limit,
        "custom_upload_limit": up_limit != UPLOAD_LIMIT,
        "uploads_remaining": 0 if uploads_over else max(0, up_limit - uploads),
        "exhausted": bool(exhausted),
        "general_over": general_over,
        "uploads_over": uploads_over,
        "level": "over" if over else "near" if near else "ok",
        "message": message,
        "publish_estimate": publish_estimate(),
        "by_method": sorted(
            ({"method": m, "calls": c, "units": u} for m, c, u in rows), key=lambda r: (-r["units"], r["method"])
        ),
    }


GENERAL_OVER_WARNING = ("a cota geral do YouTube de hoje acabou: o vídeo sobe, mas a capa, as legendas e a playlist "
                        "devem falhar")


def check_publish(estimate: int, now: datetime | None = None) -> list[str]:
    """Antes de um envio: erro só se a cota de envios de vídeo acabou; avisos se a cota geral não der conta."""
    info = status(now)
    if info["uploads_over"]:
        raise QuotaExhausted(info["message"])
    if not estimate:  # sem capa, legendas nem playlist: a cota geral não importa
        return []
    if info["general_over"]:
        return [GENERAL_OVER_WARNING]
    if info["used"] + estimate > info["limit"]:
        return [f"este envio gasta cerca de {estimate} unidades e restam {info['remaining']} hoje: "
                "a capa, as legendas ou a playlist podem falhar"]
    return []
