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
from studio.workers.finalcheck.report import preflight
from studio.workers.jobs import get_runner
from studio.workers.publish import youtube as yt

router = APIRouter(prefix="/api", tags=["publicação"])


def _redirect_uri() -> str:
    return f"http://127.0.0.1:{get_settings().port}/api/youtube/callback"


@router.get("/youtube/status")
def youtube_status(project_id: str | None = None) -> dict[str, Any]:
    """Conta global (aba de publicação) e canais vinculados em Canais.

    `connected` é verdadeiro quando há alguma conta para publicar. Com `project_id`, `connected` e `channel_title`
    passam a ser os da conta usada por aquele projeto (o canal dele, ou a global).
    """
    glob = yt.account()
    linked = yt.linked_accounts()
    out: dict[str, Any] = {
        "client_configured": yt.client_configured(),
        **glob,
        "global_connected": glob["connected"],
        "connected": glob["connected"] or bool(linked),
        "linked": linked,
    }
    if not glob["connected"] and len(linked) == 1:
        out.update(channel_title=linked[0]["channel_title"], channel_id=linked[0]["channel_id"])
    if project_id:
        try:
            channel_id = service.get_project_row(project_id)["channel_id"]
        except service.ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
        try:
            key = yt.account_for_channel(channel_id)
            out.update(connected=True, channel_title=yt.account_title(key), reason=None)
        except yt.YouTubeError as exc:
            out.update(connected=False, channel_title="", reason=str(exc))
    return out


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
    ignore_checks: bool = False  # "publicar mesmo assim": passa por cima da conferência antes de publicar (ADR 0003)


def _checks_block(project_id: str, lang: str | None, ignore_checks: bool = False) -> str | None:
    """Mensagem do 409 quando a conferência antes de publicar (avisos rápidos + relatório fresco) tem erro.

    `ignore_checks` ("publicar mesmo assim") só libera erros que o envio aceita; os marcados `blocking` (ex.: voz ou
    imagens só de prévia, que o job de envio recusa) continuam travando.
    """
    state = preflight(project_id, lang)
    errors = [i for i in state["items"] if i.get("level") == "erro"]
    blocking = [i for i in errors if i.get("blocking")]
    if ignore_checks:
        if not blocking:
            return None
        return (f"Não dá para publicar mesmo assim: {'; '.join(i['title'] for i in blocking[:3])}. Corrija e "
                "renderize de novo, ou envie como privado.")
    if not errors:
        return None
    titles = [i["title"] for i in errors][:3]
    if blocking:
        return (f"A conferência antes de publicar achou problema: {'; '.join(titles)}. Corrija ou envie como "
                "privado.")
    return (f"A conferência antes de publicar achou problema: {'; '.join(titles)}. Corrija, envie como privado ou "
            "confirme 'publicar mesmo assim'.")


@router.post("/projects/{project_id}/publish/youtube")
def publish_youtube(project_id: str, body: YouTubePublishRequest) -> dict[str, Any]:
    try:
        channel_id = service.get_project_row(project_id)["channel_id"]
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    try:
        yt.account_for_channel(channel_id)
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.privacy not in ("private", "unlisted", "public", "scheduled"):
        raise HTTPException(status_code=400, detail="privacidade inválida")
    # Privado sempre passa; público, não listado e agendado param na conferência com erro (ADR 0003, bloco H).
    if body.privacy != "private":
        message = _checks_block(project_id, body.lang, body.ignore_checks)
        if message:
            raise HTTPException(status_code=409, detail=message)
    return get_runner().submit("publish_youtube", body.model_dump(), project_id)
