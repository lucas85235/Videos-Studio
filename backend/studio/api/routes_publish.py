"""Rotas de publicação no YouTube (conexão e vínculo de canais por OAuth e envio de vídeos)."""

from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.core.project import service
from studio.db import session_scope
from studio.models import Channel
from studio.workers.jobs import get_runner
from studio.workers.publish import quota, youtube_channels
from studio.workers.publish import youtube as yt

router = APIRouter(prefix="/api", tags=["publicação"])


def _redirect_uri() -> str:
    return f"http://127.0.0.1:{get_settings().port}/api/youtube/callback"


def _project_channel(project_id: str) -> str | None:
    try:
        return service.get_project_row(project_id)["channel_id"]
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


def _channel_settings(channel_id: str | None) -> dict[str, Any]:
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return dict(row.settings or {}) if row is not None else {}


@router.get("/youtube/status")
def youtube_status(project_id: str | None = None) -> dict[str, Any]:
    """Conta global (aba de publicação), canais vinculados em Canais e todas as contas que podem publicar.

    `connected` é verdadeiro quando há alguma conta para publicar. Com `project_id`, `connected` e `channel_title`
    passam a ser os da conta usada por aquele projeto (o canal dele, ou a global). `can_choose_account` diz se o
    projeto pode enviar por outra conta (só quando o canal dele não está vinculado ao YouTube).
    """
    glob = yt.account()
    linked = yt.linked_accounts()
    out: dict[str, Any] = {
        "client_configured": yt.client_configured(),
        **glob,
        "global_connected": glob["connected"],
        "connected": glob["connected"] or bool(linked),
        "linked": linked,
        "accounts": yt.accounts(),
    }
    if not glob["connected"] and len(linked) == 1:
        out.update(channel_title=linked[0]["channel_title"], channel_id=linked[0]["channel_id"])
    if project_id:
        channel_id = _project_channel(project_id)
        settings = _channel_settings(channel_id)
        linked_channel = bool(youtube_channels.youtube_data(settings).get("channel_id"))
        out.update(can_choose_account=not linked_channel,
                   default_playlist=youtube_channels.default_playlist(settings))
        try:
            key = yt.account_for_channel(channel_id)
            out.update(connected=True, channel_title=yt.account_title(key),
                       account_channel_id=yt.account_channel_id(key), reason=None)
        except yt.YouTubeError as exc:
            out.update(connected=False, channel_title="", account_channel_id="", reason=str(exc))
            if not linked_channel and out["accounts"]:
                # Sem conta padrão para este projeto, mas dá para escolher uma das contas conectadas.
                out.update(connected=True, reason=None)
    return out


@router.get("/youtube/quota")
def youtube_quota() -> dict[str, Any]:
    """Cota da YouTube Data API usada hoje (dia do Pacífico), com aviso perto do limite."""
    return quota.status()


class QuotaLimitBody(BaseModel):
    limit: int | None = Field(default=None, ge=1, le=100_000_000)  # vazio volta aos 10.000 padrão


@router.put("/youtube/quota")
def youtube_quota_limit(body: QuotaLimitBody) -> dict[str, Any]:
    """Limite diário do projeto do Google Cloud (para quem conseguiu aumento de cota)."""
    quota.set_daily_limit(body.limit)
    return quota.status()


def _account_key(project_id: str | None, channel_id: str | None, account: str | None) -> str:
    if project_id:
        channel_id = _project_channel(project_id)
    try:
        return yt.account_for_channel(channel_id, account or None)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/youtube/playlists")
def youtube_playlists(project_id: str | None = None, channel_id: str | None = None,
                      account: str | None = None) -> dict[str, Any]:
    """Playlists do canal do YouTube que publica o projeto (ou o canal do Vulto, ou a conta escolhida).
    Custa 1 unidade de cota por página de 50 playlists."""
    key = _account_key(project_id, channel_id, account)
    try:
        items = yt.playlists_for(key)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"account_title": yt.account_title(key), "playlists": items}


class PlaylistCreateBody(BaseModel):
    project_id: str | None = None
    channel_id: str | None = Field(default=None, max_length=32)
    account: str | None = Field(default=None, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    privacy: str = "public"


@router.post("/youtube/playlists")
def youtube_playlist_create(body: PlaylistCreateBody) -> dict[str, Any]:
    """Cria uma playlist no canal do YouTube (50 unidades de cota)."""
    key = _account_key(body.project_id, body.channel_id, body.account)
    try:
        return yt.new_playlist(key, body.title, body.privacy)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/youtube/connect")
def youtube_connect() -> dict[str, Any]:
    try:
        url = yt.start_oauth(_redirect_uri())
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"auth_url": url}


class YouTubeLinkRequest(BaseModel):
    channel_id: str | None = Field(default=None, max_length=32)  # canal do Vulto; vazio cria um canal novo


@router.post("/youtube/link")
def youtube_link(body: YouTubeLinkRequest) -> dict[str, Any]:
    """Abre o login do Google para vincular um canal do YouTube (cria ou atualiza o canal do Vulto)."""
    if body.channel_id:
        with session_scope() as s:
            if s.get(Channel, body.channel_id) is None:
                raise HTTPException(status_code=404, detail="canal não encontrado")
    try:
        return yt.start_link(_redirect_uri(), body.channel_id)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/youtube/link/{state}")
def youtube_link_status(state: str) -> dict[str, Any]:
    return yt.link_status(state)


_PAGE = """<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>Vulto</title>
<style>body{{font-family:system-ui,sans-serif;background:#111114;color:#ececf1;margin:0;display:grid;place-items:center;
min-height:100vh}}main{{max-width:480px;padding:24px}}h2{{margin:0 0 8px}}p{{color:#a9a9b6;line-height:1.5}}</style>
</head><body><main><h2>{title}</h2><p>{body}</p></main></body></html>"""


def _page(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(title=escape(title), body=escape(body)), status_code=status_code)


@router.get("/youtube/callback", include_in_schema=False)
def youtube_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> HTMLResponse:
    if error or not code or not state:
        return _page("Conexão cancelada", error or "sem código", status_code=400)
    try:
        info = yt.finish_oauth(code, state)
    except yt.YouTubeError as exc:
        return _page("Não foi possível conectar", str(exc), status_code=400)
    title = info.get("channel_title") or "sem nome"
    if info.get("vulto_channel_id"):
        return _page(f"Canal vinculado: {title}", "Os dados do canal já estão na tela de Canais. Pode fechar esta aba e voltar ao Vulto.")
    return _page(f"Canal conectado: {title}", "Pode fechar esta aba e voltar ao Vulto.")


@router.post("/youtube/disconnect")
def youtube_disconnect() -> dict[str, Any]:
    yt.disconnect()
    return {"connected": False}


class YouTubePublishRequest(BaseModel):
    lang: str | None = None
    privacy: str = "private"
    publish_at: str | None = None
    category_id: str = "24"
    captions: bool = True
    localize: bool = False  # títulos/descrições dos outros idiomas do projeto no mesmo vídeo
    # None: a playlist padrão do canal (se houver); "": nenhuma; senão o id da playlist.
    playlist_id: str | None = Field(default=None, max_length=64)
    playlist_title: str | None = Field(default=None, max_length=200)
    # Id do canal no YouTube da conta que envia; só vale para projeto cujo canal não está vinculado.
    account: str | None = Field(default=None, max_length=64)


@router.post("/projects/{project_id}/publish/youtube")
def publish_youtube(project_id: str, body: YouTubePublishRequest) -> dict[str, Any]:
    channel_id = _project_channel(project_id)
    try:
        yt.account_for_channel(channel_id, body.account or None)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.privacy not in ("private", "unlisted", "public", "scheduled"):
        raise HTTPException(status_code=400, detail="privacidade inválida")
    playlist = body.playlist_id if body.playlist_id is not None else (
        (youtube_channels.default_playlist(_channel_settings(channel_id)) or {}).get("id"))
    try:
        quota.check_publish(quota.publish_estimate(captions=body.captions, playlist=bool(playlist)))
    except quota.QuotaExhausted as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    return get_runner().submit("publish_youtube", body.model_dump(), project_id)
