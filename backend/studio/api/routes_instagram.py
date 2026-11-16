"""Rotas do Instagram nativo: conectar contas (login ou token colado), opções, conta e jeito de publicar por canal
e publicação de Reels."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from studio.api.routes_publish import _page
from studio.config import get_settings
from studio.core.project import service
from studio.db import session_scope
from studio.models import Channel, Job, Publication
from studio.workers.jobs import get_runner
from studio.workers.publish import instagram as ig

router = APIRouter(prefix="/api", tags=["publicação"])


def _local_redirect() -> str:
    return f"http://127.0.0.1:{get_settings().port}/api/instagram/callback"


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


def _channel_or_404(channel_id: str) -> None:
    with session_scope() as s:
        if s.get(Channel, channel_id) is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")


@router.get("/instagram/status")
def instagram_status(project_id: str | None = None) -> dict[str, Any]:
    """Contas conectadas, opções e, com `project_id`, a conta e o jeito de publicar daquele projeto."""
    opts = ig.options()
    out: dict[str, Any] = {
        "app_configured": ig.app_configured(),
        "accounts": ig.linked_accounts(),
        "redirect_uri": opts["redirect_uri"],
        "default_redirect_uri": _local_redirect(),
        "public_base_url": opts["public_base_url"],
    }
    if project_id:
        try:
            channel_id = service.get_project_row(project_id)["channel_id"]
        except service.ProjectNotFound as exc:
            raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
        link = ig.channel_link(channel_id)
        out.update(channel_id=channel_id, driver=link.get("driver") or ("native" if out["accounts"] else "manual"),
                   channel_user_id=link.get("user_id") or None)
        try:
            user_id = ig.account_for_channel(channel_id)
            account = next((a for a in out["accounts"] if a["user_id"] == user_id), None)
            out.update(ready=True, account=account, reason=None)
        except ig.InstagramError as exc:
            out.update(ready=False, account=None, reason=str(exc))
    return out


class OptionsBody(BaseModel):
    redirect_uri: str | None = Field(default=None, max_length=500)
    public_base_url: str | None = Field(default=None, max_length=500)


@router.put("/instagram/options")
def instagram_options(body: OptionsBody) -> dict[str, str]:
    try:
        return ig.set_options(body.redirect_uri, body.public_base_url)
    except ig.InstagramError as exc:
        raise _bad(exc) from exc


class LinkBody(BaseModel):
    channel_id: str | None = Field(default=None, max_length=32)


@router.post("/instagram/link")
def instagram_link(body: LinkBody) -> dict[str, str]:
    """Abre o login do Instagram (com `channel_id`, a conta vira a do canal)."""
    if body.channel_id:
        _channel_or_404(body.channel_id)
    try:
        return ig.start_link(_local_redirect(), body.channel_id)
    except ig.InstagramError as exc:
        raise _bad(exc) from exc


@router.get("/instagram/link/{state}")
def instagram_link_status(state: str) -> dict[str, Any]:
    return ig.link_status(state)


class CodeBody(BaseModel):
    text: str = Field(min_length=1, max_length=4000)  # endereço final do login ou só o código
    state: str | None = Field(default=None, max_length=100)


@router.post("/instagram/link/code")
def instagram_link_code(body: CodeBody) -> dict[str, Any]:
    """Termina o login colando o endereço final (quando o retorno cadastrado no app não é o local do Vulto)."""
    try:
        code, state = ig.parse_pasted(body.text)
        # Prefere o state do endereço colado ou o que a tela mandou; sem nenhum, aceita o único login pendente
        # (é o usuário colando o código na própria tela do Vulto, não uma página de fora).
        return ig.finish_oauth(code, state or body.state, allow_single_pending=True)
    except ig.InstagramError as exc:
        raise _bad(exc) from exc


@router.get("/instagram/callback", include_in_schema=False)
def instagram_callback(code: str | None = None, state: str | None = None, error: str | None = None,
                       error_description: str | None = None) -> HTMLResponse:
    if error or not code or not state:
        # Sem state não dá para saber se o login foi aberto pelo Vulto (proteção contra CSRF de login).
        return _page("Conexão cancelada", error_description or error or "sem código ou sem state", status_code=400)
    try:
        info = ig.finish_oauth(code, state)
    except ig.InstagramError as exc:
        return _page("Não foi possível conectar", str(exc), status_code=400)
    return _page(f"Instagram conectado: @{info.get('username') or info.get('user_id')}",
                 "Pode fechar esta aba e voltar ao Vulto.")


class TokenBody(BaseModel):
    access_token: str = Field(min_length=10, max_length=2000)
    channel_id: str | None = Field(default=None, max_length=32)


@router.post("/instagram/token")
def instagram_token(body: TokenBody) -> dict[str, Any]:
    """Conecta com um token gerado no painel da Meta (Instagram > API setup > Generate token)."""
    if body.channel_id:
        _channel_or_404(body.channel_id)
    try:
        return ig.connect_with_token(body.access_token, channel_id=body.channel_id)
    except ig.InstagramError as exc:
        raise _bad(exc) from exc


@router.get("/instagram/accounts/{user_id}")
def instagram_account(user_id: str) -> dict[str, Any]:
    """Conta e uso do limite de posts das últimas 24 h (consulta a Meta)."""
    if not ig.get_account(user_id).get("access_token"):
        raise HTTPException(status_code=404, detail="conta não conectada")
    try:
        return ig.account_status(user_id)
    except ig.InstagramError as exc:
        raise _bad(exc) from exc


@router.delete("/instagram/accounts/{user_id}")
def instagram_forget(user_id: str) -> dict[str, Any]:
    ig.forget_account(user_id)
    return {"deleted": True}


class ChannelInstagramBody(BaseModel):
    user_id: str | None = Field(default=None, max_length=64)  # vazio: tira a conta escolhida
    driver: str | None = Field(default=None, max_length=10)  # native | manual


@router.put("/channels/{channel_id}/instagram")
def channel_instagram(channel_id: str, body: ChannelInstagramBody) -> dict[str, Any]:
    """Conta do Instagram e jeito de publicar (API oficial ou pacote manual) de um canal."""
    _channel_or_404(channel_id)
    fields = body.model_fields_set
    try:
        return ig.set_channel_link(
            channel_id,
            user_id=body.user_id or None,
            driver=body.driver,
            clear_account="user_id" in fields and not body.user_id,
        )
    except ig.InstagramError as exc:
        raise _bad(exc) from exc


class InstagramPublishBody(BaseModel):
    lang: str | None = None
    video_url: str | None = Field(default=None, max_length=2000)
    cover_url: str | None = Field(default=None, max_length=2000)
    share_to_feed: bool = True
    publication_id: str | None = Field(default=None, max_length=32)  # retomar um envio que ficou processando


@router.post("/projects/{project_id}/publish/instagram")
def publish_instagram(project_id: str, body: InstagramPublishBody) -> dict[str, Any]:
    try:
        channel_id = service.get_project_row(project_id)["channel_id"]
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    if not body.publication_id:
        lang = body.lang or service.get_doc(project_id).primary_language
        with session_scope() as s:
            pending = (
                s.query(Publication)
                .filter(Publication.project_id == project_id, Publication.driver == "instagram_native",
                        Publication.language == lang, Publication.status == "processing",
                        Publication.created_at >= datetime.now(UTC) - timedelta(hours=24))  # o envio vale 24 h
                .first()
            )
            if pending is not None:
                # Publicar de novo criaria outro envio e, ao retomar o antigo, dois Reels iguais.
                raise HTTPException(status_code=409, detail="já há um Reel deste idioma esperando o Instagram: "
                                    "use Retomar envio em vez de publicar de novo")
    for url in (body.video_url, body.cover_url):
        if url and not url.strip().startswith("https://"):
            raise HTTPException(status_code=400, detail="as URLs do vídeo e da capa precisam começar com https://")
    with session_scope() as s:
        busy = s.query(Job).filter(Job.project_id == project_id, Job.kind == "publish_instagram",
                                   Job.status.in_(("queued", "running"))).first()
        if busy is not None:
            raise HTTPException(status_code=409, detail="já há uma publicação no Instagram em andamento neste projeto")
    payload = body.model_dump()
    if body.publication_id:
        with session_scope() as s:
            pub = s.get(Publication, body.publication_id)
            if pub is None or pub.project_id != project_id or pub.driver != "instagram_native":
                raise HTTPException(status_code=404, detail="publicação não encontrada")
            if pub.status != "processing" or not (pub.details or {}).get("container_id"):
                raise HTTPException(status_code=400, detail="esta publicação não está esperando o Instagram")
            # Retoma com a conta que criou o envio (o canal pode ter mudado de conta depois).
            payload.update(container_id=pub.details["container_id"], lang=pub.language,
                           user_id=pub.details.get("user_id") or None)
    else:
        try:
            ig.account_for_channel(channel_id)
        except ig.InstagramError as exc:
            raise _bad(exc) from exc
    return get_runner().submit("publish_instagram", payload, project_id)
