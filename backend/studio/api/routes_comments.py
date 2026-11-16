"""Rotas da caixa de comentários do YouTube (leitura, filtros, lido, sugestão da IA e resposta confirmada)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from studio.providers.base import ProviderError
from studio.workers.comments import inbox
from studio.workers.publish.youtube import YouTubeError

router = APIRouter(prefix="/api", tags=["comentarios"])

Filter = Literal["todos", "nao_respondidos", "perguntas", "nao_lidos"]


@router.get("/comments")
def list_comments(channel_id: str | None = None, filter: Filter = "nao_respondidos",  # noqa: A002 - nome da query
                  video_id: str | None = None, limit: int = Query(default=100, ge=1, le=500),
                  offset: int = Query(default=0, ge=0)) -> dict[str, Any]:
    out = inbox.list_comments(channel_id or None, filter, video_id or None, limit, offset)
    out["channels"] = inbox.channel_states(channel_id or None)
    out["quota"] = inbox.quota_today()
    return out


class RefreshBody(BaseModel):
    channel_id: str | None = None


@router.post("/comments/refresh")
def refresh(body: RefreshBody) -> dict[str, Any]:
    try:
        out = inbox.refresh(body.channel_id or None)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    out["quota"] = inbox.quota_today()
    return out


class ReadBody(BaseModel):
    ids: list[str] = Field(default_factory=list, max_length=1000)
    read: bool = True


@router.post("/comments/read")
def mark_read(body: ReadBody) -> dict[str, int]:
    return {"changed": inbox.mark_read(body.ids, body.read)}


class ReadAllBody(BaseModel):
    channel_id: str | None = None
    filter: Filter = "todos"


@router.post("/comments/read-all")
def mark_all_read(body: ReadAllBody) -> dict[str, int]:
    return {"changed": inbox.mark_all_read(body.channel_id or None, body.filter)}


@router.post("/comments/{comment_id}/suggest")
def suggest(comment_id: str) -> dict[str, Any]:
    try:
        return inbox.suggest_reply(comment_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="comentário não encontrado") from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"a IA não sugeriu a resposta: {exc}") from exc


class ReplyBody(BaseModel):
    text: str = Field(min_length=1, max_length=inbox.REPLY_MAX)
    # A resposta é pública: só sai com a confirmação explícita da tela.
    confirm: bool = False


@router.post("/comments/{comment_id}/reply")
def reply(comment_id: str, body: ReplyBody) -> dict[str, Any]:
    if not body.confirm:
        raise HTTPException(status_code=409, detail="confirme o envio: a resposta fica pública no YouTube")
    try:
        out = inbox.reply(comment_id, body.text)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="comentário não encontrado") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except inbox.ScopeMissing as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except YouTubeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"comment": out, "quota": inbox.quota_today()}
