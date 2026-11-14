"""Rotas de publicação no YouTube (conexão do canal por OAuth e envio de vídeos)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from studio.config import get_settings
from studio.providers import credentials
from studio.workers.jobs import get_runner
from studio.workers.publish import youtube as yt

router = APIRouter(prefix="/api", tags=["publicação"])


@router.get("/youtube/status")
def youtube_status() -> dict[str, Any]:
    configured = bool(credentials.resolve("youtube", "client_id") and credentials.resolve("youtube", "client_secret"))
    return {"client_configured": configured, **yt.account()}


@router.post("/youtube/connect")
def youtube_connect() -> dict[str, Any]:
    try:
        url = yt.start_oauth(f"http://127.0.0.1:{get_settings().port}/api/youtube/callback")
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"auth_url": url}


@router.get("/youtube/callback", include_in_schema=False)
def youtube_callback(code: str | None = None, state: str | None = None, error: str | None = None) -> HTMLResponse:
    if error or not code or not state:
        return HTMLResponse(f"<h2>Conexão cancelada</h2><p>{error or 'sem código'}</p>", status_code=400)
    try:
        info = yt.finish_oauth(code, state)
    except yt.YouTubeError as exc:
        return HTMLResponse(f"<h2>Não foi possível conectar</h2><p>{exc}</p>", status_code=400)
    title = info["channel_title"] or "sem nome"
    return HTMLResponse(f"<h2>Canal conectado: {title}</h2><p>Pode fechar esta aba e voltar ao Videos Studio.</p>")


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


@router.post("/projects/{project_id}/publish/youtube")
def publish_youtube(project_id: str, body: YouTubePublishRequest) -> dict[str, Any]:
    if not yt.account()["connected"]:
        raise HTTPException(status_code=400, detail="conecte o canal do YouTube primeiro (aba Vídeo e publicação)")
    if body.privacy not in ("private", "unlisted", "public", "scheduled"):
        raise HTTPException(status_code=400, detail="privacidade inválida")
    return get_runner().submit("publish_youtube", body.model_dump(), project_id)
