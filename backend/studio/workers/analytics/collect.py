"""Coleta das métricas no YouTube (origem: `rotinas.importar_metricas_gmg` e `gravar_metrica` do canais).

Por canal com token: `videos.list` da Data API (views, likes, comentários, data e duração) e, se o token tiver o
escopo, o YouTube Analytics (duração média, % assistido, inscritos ganhos). Regras trazidas do canais:
- privado ou agendado sem views fica fora da conta (entrava com 0 views e derrubava a mediana; lição #205);
- vídeo sem dado do Analytics fica com retenção VAZIA, nunca 0 (o Analytics atrasa 2 a 3 dias; lição #202);
- agendado: a idade conta da hora em que foi ao ar, não do upload.
Token sem o escopo do Analytics (vinculado antes desta versão) não é erro: segue só com a Data API.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx

from studio.db import session_scope
from studio.models import Channel, Project, Publication
from studio.workers.analytics.metrics import parse_when, upsert_metric
from studio.workers.analytics.variables import parse_iso_duration
from studio.workers.publish import youtube as yt

log = logging.getLogger(__name__)

BATCH = 50  # videos.list aceita até 50 ids por chamada
SOURCE = "youtube_api"
# Estados da coleta por canal (mostrados na página Desempenho).
OK, SEM_ESCOPO, SEM_TOKEN, PUBLICO, ERRO = "ok", "sem_escopo", "sem_token", "publico", "erro"

Progress = Callable[[float, str], None]


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def channel_video_ids(channel_id: str) -> tuple[list[str], dict[str, datetime]]:
    """Ids dos vídeos do canal: publicações do YouTube (`external_id`) + vídeos recentes do canal vinculado.

    Devolve também o horário agendado de cada publicação (a idade de um agendado conta de quando saiu).
    """
    ids: list[str] = []
    scheduled: dict[str, datetime] = {}
    with session_scope() as s:
        rows = (
            s.query(Publication)
            .join(Project, Project.id == Publication.project_id)
            .filter(Project.channel_id == channel_id, Publication.platform == "youtube",
                    Publication.external_id.isnot(None))
            .all()
        )
        for pub in rows:
            if pub.external_id and pub.external_id not in ids:
                ids.append(pub.external_id)
                if pub.scheduled_at:
                    scheduled[pub.external_id] = pub.scheduled_at
        channel = s.get(Channel, channel_id)
        recent = (((channel.settings or {}).get("youtube") or {}).get("recent_videos") or []) if channel else []
    for v in recent:
        vid = v.get("video_id") if isinstance(v, dict) else None
        if vid and vid not in ids:
            ids.append(vid)
    return ids, scheduled


def _save_state(channel_id: str, **state: Any) -> dict[str, Any]:
    """Atualiza `Channel.settings["analytics"]` (estado da coleta e dados dos vídeos), sem mexer no resto."""
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            return {}
        settings = dict(channel.settings or {})
        current = dict(settings.get("analytics") or {})
        videos = state.pop("videos", None)
        if videos:
            current["videos"] = {**(current.get("videos") or {}), **videos}
        current.update(state)
        settings["analytics"] = current
        channel.settings = settings  # atribuição nova: o SQLAlchemy só vê a troca do JSON inteiro
        return {k: v for k, v in current.items() if k != "videos"}


def fetch_videos(http: httpx.Client, token: str, ids: list[str]) -> list[dict[str, Any]]:
    """videos.list (1 unidade de cota por chamada), em lotes de 50 ids."""
    items: list[dict[str, Any]] = []
    for i in range(0, len(ids), BATCH):
        data = yt._get(http, f"{yt.API}/videos", token, params={
            "part": "statistics,snippet,contentDetails,status", "id": ",".join(ids[i:i + BATCH]),
            "maxResults": str(BATCH),
        })
        items.extend(data.get("items") or [])
    return items


class ScopeMissing(Exception):
    """O token não tem o escopo do YouTube Analytics (401, ou 403 por permissão insuficiente)."""


# Motivos do 403 que querem dizer "o token não tem o escopo" (vincular de novo resolve).
_SCOPE_REASONS = {"insufficientPermissions", "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}


def _error_reasons(resp: httpx.Response) -> set[str]:
    """`reason` de `error.errors[]` e de `error.details[]`, mais o `error.status` do corpo de erro do Google."""
    try:
        err = (resp.json() or {}).get("error") or {}
    except ValueError:
        return set()
    if not isinstance(err, dict):
        return set()
    reasons = {str(e.get("reason")) for e in err.get("errors") or [] if isinstance(e, dict) and e.get("reason")}
    reasons |= {str(d.get("reason")) for d in err.get("details") or [] if isinstance(d, dict) and d.get("reason")}
    if err.get("status"):
        reasons.add(str(err["status"]))
    return reasons


def _analytics_error(resp: httpx.Response) -> Exception:
    """Erro do Analytics: escopo faltando vira `ScopeMissing`; cota, API desligada ou outro 403 vira `YouTubeError`."""
    reasons = _error_reasons(resp)
    if resp.status_code == 401 or reasons & _SCOPE_REASONS:
        return ScopeMissing(resp.text[:300])
    if "accessNotConfigured" in reasons or "SERVICE_DISABLED" in reasons:
        return yt.YouTubeError("ative a YouTube Analytics API no Google Cloud (o projeto do OAuth está sem ela)")
    if reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded", "RESOURCE_EXHAUSTED"}:
        return yt.YouTubeError("cota do YouTube Analytics esgotada; tente de novo amanhã")
    return yt.YouTubeError(f"erro do YouTube Analytics ({resp.status_code}): {resp.text[:300]}")


def fetch_analytics(http: httpx.Client, token: str, ids: list[str], start: str, end: str) -> dict[str, dict[str, Any]]:
    """YouTube Analytics por vídeo. Escopo faltando levanta `ScopeMissing`; outro erro levanta `YouTubeError`.

    O relatório por vídeo (dimensão `video`) exige `sort` e `maxResults` (até 200); sem eles a API responde
    400 "The query is not supported".
    """
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(ids), BATCH):
        params = {
            "ids": "channel==MINE",
            "startDate": start,
            "endDate": end,
            "metrics": "views,averageViewDuration,averageViewPercentage,subscribersGained",
            "dimensions": "video",
            "filters": "video==" + ",".join(ids[i:i + BATCH]),
            "sort": "-views",
            "maxResults": "200",
        }
        try:
            resp = http.get(yt.ANALYTICS_API, params=params, headers={"Authorization": f"Bearer {token}"})
        except httpx.HTTPError as exc:
            raise yt.YouTubeError(f"sem resposta do YouTube Analytics: {exc}") from exc
        if resp.status_code != 200:
            raise _analytics_error(resp)
        data = resp.json()
        names = [h.get("name") for h in data.get("columnHeaders") or []]
        for row in data.get("rows") or []:
            item = dict(zip(names, row, strict=False))
            if item.get("video"):
                out[str(item["video"])] = item
    return out


def collect_channel(channel_id: str, http: httpx.Client, now: datetime | None = None) -> dict[str, Any]:
    """Coleta um canal. Devolve o resumo (`state`, `saved`, `skipped`, `scope`)."""
    now = now or datetime.now(UTC)
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            return {"channel_id": channel_id, "state": ERRO, "error": "canal não encontrado"}
        link = dict((channel.settings or {}).get("youtube") or {})
        prev_scope = ((channel.settings or {}).get("analytics") or {}).get("scope")
    try:
        account = yt.account_for_channel(channel_id)
        token = yt.access_token(http, account)
    except yt.YouTubeError as exc:
        state = PUBLICO if link.get("source") == "public" else SEM_TOKEN
        saved = _save_state(channel_id, state=state, error=str(exc), checked_at=now.isoformat())
        return {"channel_id": channel_id, **saved, "saved": 0, "skipped": 0}

    ids, scheduled = channel_video_ids(channel_id)
    if not ids:
        # Analytics não foi chamado: o escopo conhecido continua valendo (não vira "ok" sem testar o token).
        saved = _save_state(channel_id, state=SEM_ESCOPO if prev_scope == "missing" else OK, error="",
                            last_run=now.isoformat())
        return {"channel_id": channel_id, **saved, "saved": 0, "skipped": 0}
    try:
        items = fetch_videos(http, token, ids)
    except yt.YouTubeError as exc:
        saved = _save_state(channel_id, state=ERRO, error=str(exc), checked_at=now.isoformat())
        return {"channel_id": channel_id, **saved, "saved": 0, "skipped": 0}

    kept: dict[str, dict[str, Any]] = {}
    info: dict[str, dict[str, Any]] = {}
    skipped = 0
    for item in items:
        vid = item.get("id")
        if not vid:
            continue
        snippet, status = item.get("snippet") or {}, item.get("status") or {}
        stats, details = item.get("statistics") or {}, item.get("contentDetails") or {}
        views = _int(stats.get("viewCount"))
        privacy = status.get("privacyStatus") or "public"
        # Privado ou agendado que ainda não foi ao ar: fora da conta (lição #205). Privado com views continua.
        if privacy != "public" and not views:
            skipped += 1
            continue
        # Só as datas do YouTube; o `scheduled_at` local fica de reserva (se o vídeo foi ao ar antes da hora marcada
        # no Vulto, a data antiga do agendamento ficaria no futuro e o vídeo perderia a idade).
        published = max(
            filter(None, (parse_when(snippet.get("publishedAt")), parse_when(status.get("publishAt")))),
            default=None,
        ) or parse_when(scheduled.get(vid))
        if published is not None and published > now:
            published = None  # ainda não saiu: sem idade
        kept[vid] = {"published": published, "views": views, "likes": _int(stats.get("likeCount")),
                     "comments": _int(stats.get("commentCount"))}
        info[vid] = {"title": snippet.get("title") or "", "published_at": published.isoformat() if published else None,
                     "duration_s": parse_iso_duration(details.get("duration")), "privacy": privacy}

    # `scope` só muda quando o Analytics respondeu (ok ou faltando); sem chamada ou com outra falha, fica o de antes.
    scope: str | None = None
    error = ""
    analytics: dict[str, dict[str, Any]] = {}
    if kept:
        dates = [k["published"] for k in kept.values() if k["published"]]
        start = min(dates).date().isoformat() if dates else now.date().isoformat()
        try:
            analytics = fetch_analytics(http, token, list(kept), start, now.date().isoformat())
            scope = "ok"
        except ScopeMissing as exc:
            scope, error = "missing", str(exc)
        except yt.YouTubeError as exc:  # Analytics fora do ar não impede gravar o que a Data API trouxe
            log.warning("YouTube Analytics falhou para o canal %s: %s", channel_id, exc)
            error = f"views vieram, mas o YouTube Analytics falhou: {exc}"

    for vid, k in kept.items():
        a = analytics.get(vid) or {}
        # Sem views no Analytics, o dado ainda não chegou: retenção vazia, não 0 (lição #202).
        has_analytics = bool(_int(a.get("views")))
        upsert_metric(
            vid, source=SOURCE, published_at=k["published"], channel_id=channel_id, now=now,
            views=k["views"], likes=k["likes"], comments=k["comments"],
            avg_view_s=_float(a.get("averageViewDuration")) if has_analytics else None,
            pct_watched=_float(a.get("averageViewPercentage")) if has_analytics else None,
            subscribers_gained=_int(a.get("subscribersGained")) if has_analytics else None,
        )
    known_scope = scope or prev_scope
    if scope is None and error:
        state = ERRO  # aparece na tela com a mensagem; não fica um "ok" mudo sem retenção
    else:
        state = SEM_ESCOPO if known_scope == "missing" else OK
    extra = {"scope": scope} if scope is not None else {}
    saved = _save_state(channel_id, state=state, error=error, last_run=now.isoformat(), videos=info, **extra)
    return {"channel_id": channel_id, **saved, "saved": len(kept), "skipped": skipped}


def collect(channel_id: str | None = None, *, http: httpx.Client | None = None, now: datetime | None = None,
            progress: Progress | None = None) -> dict[str, Any]:
    """Coleta um canal ou todos. HTTP sempre pelo `youtube.new_client()` (os testes trocam por um simulado)."""
    with session_scope() as s:
        if channel_id:
            if s.get(Channel, channel_id) is None:
                raise ValueError("canal não encontrado")
            ids = [channel_id]
        else:
            ids = [c.id for c in s.query(Channel).order_by(Channel.name).all()]
    own = http is None
    http = http or yt.new_client()
    out = []
    try:
        for i, cid in enumerate(ids):
            if progress:
                progress(i / max(len(ids), 1), f"Canal {i + 1} de {len(ids)}")
            out.append(collect_channel(cid, http, now))
    finally:
        if own:
            http.close()
    if progress:
        progress(1.0, "Métricas atualizadas")
    return {"channels": out, "saved": sum(c.get("saved", 0) for c in out)}
