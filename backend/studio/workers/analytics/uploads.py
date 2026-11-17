"""Inventário dos uploads de um canal vinculado: todos os vídeos da playlist de uploads (públicos, privados e
agendados), não só os 15 de `settings["youtube"]["recent_videos"]` (a vitrine da tela de Canais).

Fica em `Channel.settings["uploads"]` = `{fetched_at, playlist, items: [{video_id, title, published, privacy,
description}]}`, fora de `settings["youtube"]` (que o vínculo regrava inteiro). Vale por `TTL_H` horas; renovar custa
1 unidade de cota por 50 vídeos (playlistItems.list, nunca `search`). Só com o token do próprio canal: sem ele fica o
inventário guardado (mesmo velho) ou a vitrine, e a resposta diz `completo=False` e o motivo em `erro`.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from studio.db import session_scope
from studio.models import Channel
from studio.workers.analytics.metrics import parse_when
from studio.workers.analytics.variables import parse_iso_duration
from studio.workers.publish import youtube as yt

log = logging.getLogger(__name__)

KEY = "uploads"
TTL_H = 6


def _playlist(link: dict[str, Any]) -> str:
    youtube_id = str(link.get("channel_id") or "")
    return str(link.get("uploads_playlist") or "") or (f"UU{youtube_id[2:]}" if youtube_id.startswith("UC") else "")


def channel_token(http: httpx.Client, link: dict[str, Any]) -> str:
    """Token do PRÓPRIO canal do YouTube (nunca o de outro canal: privados e agendados só o dono vê)."""
    account = yt.account_by_youtube_id(link.get("channel_id"))
    if account is None:
        raise yt.YouTubeError("o canal não está vinculado com o login do Google (só o dono vê privados e agendados)")
    return yt.access_token(http, account)


def cached(settings: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Os itens do inventário guardado (sem rede)."""
    return [v for v in ((settings or {}).get(KEY) or {}).get("items") or [] if isinstance(v, dict)]


def _save(channel_id: str, data: dict[str, Any]) -> None:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is not None:
            row.settings = {**(row.settings or {}), KEY: data}  # dict novo: o SQLAlchemy só grava JSON reatribuído


def inventory(channel_id: str, http: httpx.Client | None = None, *, now: datetime | None = None,
              max_age_h: float = TTL_H, token: str | None = None, save: bool = True) -> dict[str, Any]:
    """`{items, fetched_at, fonte, completo, erro, token}`. `fonte`: "cache" (guardado há menos de `max_age_h` h),
    "api" (lido agora), "cache_velho" ou "vitrine" (sem token; `completo=False`). `token` volta preenchido quando foi
    preciso renovar (para quem ainda vai ler os vídeos). Com `save=False` nada é gravado no banco."""
    now = now or datetime.now(UTC)
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        settings = dict(row.settings or {}) if row is not None else {}
    link = dict(settings.get("youtube") or {})
    stored = dict(settings.get(KEY) or {})
    playlist = _playlist(link)
    fetched = parse_when(stored.get("fetched_at"))
    if (playlist and stored.get("playlist") == playlist and fetched is not None
            and now - fetched < timedelta(hours=max_age_h)):
        return {"items": cached(settings), "fetched_at": stored["fetched_at"], "fonte": "cache", "completo": True,
                "erro": None, "token": token}
    erro = "canal sem vínculo com o YouTube" if not playlist else None
    if playlist:
        own = http is None
        client = http or yt.new_client()
        try:
            token = token or channel_token(client, link)
            items = yt.fetch_all_uploads(client, token, playlist)
        except yt.YouTubeError as exc:
            erro = str(exc)
        else:
            data = {"fetched_at": now.isoformat(), "playlist": playlist, "items": items}
            if save:
                _save(channel_id, data)
            return {**data, "fonte": "api", "completo": True, "erro": None, "token": token}
        finally:
            if own:
                client.close()
    if stored.get("items"):
        return {"items": cached(settings), "fetched_at": stored.get("fetched_at"), "fonte": "cache_velho",
                "completo": False, "erro": erro, "token": None}
    recent = [{"video_id": v.get("video_id") or "", "title": v.get("title") or "", "published": v.get("published")
               or "", "privacy": "", "description": ""}
              for v in link.get("recent_videos") or [] if isinstance(v, dict) and v.get("video_id")]
    return {"items": recent, "fetched_at": None, "fonte": "vitrine", "completo": False, "erro": erro, "token": None}


def video_stats(http: httpx.Client, token: str, ids: list[str]) -> dict[str, dict[str, Any]]:
    """videos.list (1 unidade por 50 ids): views, duração, privacidade e quando foi (ou vai) ao ar, por id."""
    from studio.workers.analytics.collect import fetch_videos  # aqui: o coletor importa este módulo

    out: dict[str, dict[str, Any]] = {}
    for item in fetch_videos(http, token, ids):
        sn, st = item.get("snippet") or {}, item.get("status") or {}
        views = (item.get("statistics") or {}).get("viewCount")
        when = max(filter(None, (parse_when(sn.get("publishedAt")), parse_when(st.get("publishAt")))), default=None)
        out[item["id"]] = {
            "title": sn.get("title") or "",
            "views": int(views) if str(views or "").isdigit() else None,
            "duration_s": parse_iso_duration((item.get("contentDetails") or {}).get("duration")),
            "privacy": st.get("privacyStatus") or "public",
            "published": when.isoformat() if when else None,
        }
    return out
