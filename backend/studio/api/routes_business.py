"""Rotas de ideias e dinheiro (bloco D, ADR 0002): banco de ideias, ofertas de afiliado, links do vídeo e receita."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError

from studio.core.styles.library import StyleNotFound
from studio.db import session_scope
from studio.ids import new_id
from studio.models import AffiliateOffer, Channel
from studio.workers.business import ideas, monetization, revenue

router = APIRouter(prefix="/api", tags=["dinheiro"])

IdeaStatus = Literal["nova", "aprovada", "rejeitada", "em_producao", "feita", "descartada"]
OfferStatus = Literal["ideia", "ativa", "pausada", "descartada"]
FrontStatus = Literal["ideia", "preparando", "ativa", "bloqueada", "pausada", "descartada"]


# --------------------------------------------------------------------------------------------
# Ideias
# --------------------------------------------------------------------------------------------


class IdeaBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    channel_id: str | None = Field(default=None, max_length=32)
    angle: str = Field(default="", max_length=4000)
    format: Literal["short", "long", ""] = ""
    origin: str = Field(default="manual", max_length=60)
    evidence: str = Field(default="", max_length=4000)
    search: str = Field(default="", max_length=1000)
    score: float | None = Field(default=None, ge=0, le=10)
    status: IdeaStatus = "nova"
    reason: str = Field(default="", max_length=2000)
    force: bool = False


class IdeaPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    channel_id: str | None = Field(default=None, max_length=32)
    angle: str | None = Field(default=None, max_length=4000)
    format: Literal["short", "long", ""] | None = None
    origin: str | None = Field(default=None, max_length=60)
    evidence: str | None = Field(default=None, max_length=4000)
    search: str | None = Field(default=None, max_length=1000)
    score: float | None = Field(default=None, ge=0, le=10)
    status: IdeaStatus | None = None
    reason: str | None = Field(default=None, max_length=2000)
    force: bool = False


class DecideBody(BaseModel):
    ids: list[str] = Field(min_length=1, max_length=200)
    status: IdeaStatus
    reason: str | None = Field(default=None, max_length=2000)


class VoiceBody(BaseModel):
    provider: str
    voice: str


class IdeaProjectBody(BaseModel):
    style_id: str | None = None
    languages: list[str] | None = None
    voice: VoiceBody | None = None


def _duplicate_response(exc: ideas.DuplicateIdea) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc), "id": exc.idea["id"], "idea": exc.idea})


@router.get("/ideas")
def list_ideas(channel_id: str | None = None, status: IdeaStatus | None = None, min_score: float | None = None) -> list[dict[str, Any]]:
    return ideas.list_ideas(channel_id=channel_id, status=status, min_score=min_score)


@router.post("/ideas", response_model=None)
def create_idea(body: IdeaBody) -> dict[str, Any] | JSONResponse:
    data = body.model_dump(exclude={"force"})
    try:
        return ideas.create(data, force=body.force)
    except ideas.DuplicateIdea as exc:
        return _duplicate_response(exc)
    except ideas.IdeaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/ideas/decide")
def decide_ideas(body: DecideBody) -> list[dict[str, Any]]:
    try:
        return ideas.decide(body.ids, body.status, body.reason)
    except ideas.IdeaNotFound as exc:
        raise HTTPException(status_code=404, detail=f"ideia não encontrada: {exc}") from exc
    except ideas.IdeaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/ideas/{idea_id}", response_model=None)
def update_idea(idea_id: str, body: IdeaPatch) -> dict[str, Any] | JSONResponse:
    data = {k: getattr(body, k) for k in body.model_fields_set if k != "force"}
    try:
        return ideas.update(idea_id, data, force=body.force)
    except ideas.DuplicateIdea as exc:
        return _duplicate_response(exc)
    except ideas.IdeaNotFound as exc:
        raise HTTPException(status_code=404, detail=f"ideia não encontrada: {exc}") from exc
    except ideas.IdeaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/ideas/{idea_id}")
def delete_idea(idea_id: str) -> dict[str, Any]:
    try:
        ideas.delete(idea_id)
    except ideas.IdeaNotFound as exc:
        raise HTTPException(status_code=404, detail=f"ideia não encontrada: {exc}") from exc
    return {"deleted": True}


@router.post("/ideas/{idea_id}/project")
def idea_to_project(idea_id: str, body: IdeaProjectBody) -> dict[str, Any]:
    try:
        return ideas.to_project(idea_id, style_id=body.style_id, languages=body.languages,
                                voice=body.voice.model_dump() if body.voice else None)
    except ideas.IdeaNotFound as exc:
        raise HTTPException(status_code=404, detail=f"ideia não encontrada: {exc}") from exc
    except StyleNotFound as exc:
        raise HTTPException(status_code=400, detail=f"estilo não encontrado: {exc}") from exc
    except ideas.IdeaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------------
# Configuração de monetização do canal
# --------------------------------------------------------------------------------------------


class FrontBody(BaseModel):
    id: str = Field(default="", max_length=60)
    name: str = Field(min_length=1, max_length=200)
    status: FrontStatus = "ideia"
    next: str = Field(default="", max_length=500)
    requirement: str = Field(default="", max_length=300)


class MonetizationBody(BaseModel):
    plan: str | None = Field(default=None, max_length=4000)
    since: str | None = Field(default=None, pattern=r"^(\d{4}-(0[1-9]|1[0-2]))?$")
    amazon_tag: str | None = Field(default=None, max_length=60, pattern=r"^\S*$")
    amazon_domain: str | None = Field(default=None, min_length=1, max_length=60, pattern=r"^[a-z0-9.-]+$")
    max_links: int | None = Field(default=None, ge=1, le=10)
    publi: str | None = Field(default=None, min_length=1, max_length=300)
    publi_pinned: str | None = Field(default=None, max_length=300)
    disclosure: str | None = Field(default=None, max_length=500)
    tracking_ids: dict[str, str] | None = None
    fronts: list[FrontBody] | None = None


def _channel_exists(channel_id: str) -> None:
    with session_scope() as s:
        if s.get(Channel, channel_id) is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")


@router.get("/channels/{channel_id}/monetization")
def get_monetization(channel_id: str) -> dict[str, Any]:
    try:
        return monetization.channel_config(channel_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc


@router.put("/channels/{channel_id}/monetization")
def put_monetization(channel_id: str, body: MonetizationBody) -> dict[str, Any]:
    patch = {k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None}
    if "fronts" in patch:
        patch["fronts"] = [f.model_dump() for f in body.fronts or []]
        for i, f in enumerate(patch["fronts"]):
            f["id"] = f["id"] or re.sub(r"[^a-z0-9]+", "-", ideas.norm(f["name"])).strip("-") or f"frente-{i + 1}"
    try:
        return monetization.save_channel_config(channel_id, patch)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except monetization.MonetizationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------------
# Ofertas
# --------------------------------------------------------------------------------------------

SLUG = r"^[a-z0-9][a-z0-9-]*$"
DAY = r"^(\d{4}-\d{2}-\d{2})?$"


def _valid_day(value: str | None) -> str | None:
    if value:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("data no formato AAAA-MM-DD") from exc
    return value


class OfferBody(BaseModel):
    slug: str = Field(min_length=1, max_length=60, pattern=SLUG)
    name: str = Field(min_length=1, max_length=200)
    store: str = Field(default="amazon", min_length=1, max_length=40)
    asin: str = Field(default="", max_length=20)
    link: str = Field(default="", max_length=2000)
    text: str = Field(default="", max_length=300)
    topics: list[str] = Field(default_factory=list, max_length=30)
    status: OfferStatus = "ideia"
    checked_on: str = Field(default="", pattern=DAY)
    note: str = Field(default="", max_length=2000)

    _day = field_validator("checked_on")(_valid_day)


class OfferPatch(BaseModel):
    slug: str | None = Field(default=None, min_length=1, max_length=60, pattern=SLUG)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    store: str | None = Field(default=None, min_length=1, max_length=40)
    asin: str | None = Field(default=None, max_length=20)
    link: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=300)
    topics: list[str] | None = Field(default=None, max_length=30)
    status: OfferStatus | None = None
    checked_on: str | None = Field(default=None, pattern=DAY)
    note: str | None = Field(default=None, max_length=2000)

    _day = field_validator("checked_on")(_valid_day)


def _clean_offer(data: dict[str, Any]) -> dict[str, Any]:
    if "store" in data and data["store"]:
        data["store"] = data["store"].strip().lower()
    if "asin" in data and data["asin"] is not None:
        data["asin"] = data["asin"].strip().upper()
    if "topics" in data and data["topics"] is not None:
        data["topics"] = [t.strip().lower() for t in data["topics"] if t.strip()]
    return data


@router.get("/channels/{channel_id}/offers")
def list_offers(channel_id: str) -> list[dict[str, Any]]:
    _channel_exists(channel_id)
    return monetization.channel_offers(channel_id)


@router.post("/channels/{channel_id}/offers")
def create_offer(channel_id: str, body: OfferBody) -> dict[str, Any]:
    _channel_exists(channel_id)
    try:
        with session_scope() as s:
            if s.query(AffiliateOffer).filter_by(channel_id=channel_id, slug=body.slug).first():
                raise HTTPException(status_code=409, detail=f"já existe uma oferta {body.slug} neste canal")
            row = AffiliateOffer(id=new_id("ofe"), channel_id=channel_id, **_clean_offer(body.model_dump()))
            s.add(row)
            s.flush()
            return monetization.offer_dict(row)
    except IntegrityError as exc:
        raise HTTPException(status_code=409, detail=f"já existe uma oferta {body.slug} neste canal") from exc


@router.put("/channels/{channel_id}/offers/{offer_id}")
def update_offer(channel_id: str, offer_id: str, body: OfferPatch) -> dict[str, Any]:
    data = _clean_offer({k: getattr(body, k) for k in body.model_fields_set if getattr(body, k) is not None})
    with session_scope() as s:
        row = s.get(AffiliateOffer, offer_id)
        if row is None or row.channel_id != channel_id:
            raise HTTPException(status_code=404, detail="oferta não encontrada")
        if "slug" in data and data["slug"] != row.slug and (
            s.query(AffiliateOffer).filter_by(channel_id=channel_id, slug=data["slug"]).first()
        ):
            raise HTTPException(status_code=409, detail=f"já existe uma oferta {data['slug']} neste canal")
        for key, value in data.items():
            setattr(row, key, value)
        s.flush()
        return monetization.offer_dict(row)


@router.delete("/channels/{channel_id}/offers/{offer_id}")
def delete_offer(channel_id: str, offer_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(AffiliateOffer, offer_id)
        if row is None or row.channel_id != channel_id:
            raise HTTPException(status_code=404, detail="oferta não encontrada")
        s.delete(row)
    return {"deleted": True}


# --------------------------------------------------------------------------------------------
# Links do projeto
# --------------------------------------------------------------------------------------------


class AffiliateBody(BaseModel):
    enabled: bool | None = None
    mode: Literal["temas", "ofertas"] | None = None
    offer_ids: list[str] | None = Field(default=None, max_length=10)
    topics: list[str] | None = Field(default=None, max_length=30)
    tracking_id: str | None = Field(default=None, max_length=60, pattern=r"^\S*$")


@router.get("/projects/{project_id}/affiliate")
def get_affiliate(project_id: str) -> dict[str, Any]:
    try:
        return monetization.project_affiliate(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


@router.put("/projects/{project_id}/affiliate")
def put_affiliate(project_id: str, body: AffiliateBody) -> dict[str, Any]:
    patch = {k: getattr(body, k) for k in body.model_fields_set}
    if patch.get("topics") is not None:
        patch["topics"] = [t.strip().lower() for t in patch["topics"] if t.strip()]
    try:
        monetization.save_project_affiliate(project_id, patch)
        return monetization.project_affiliate(project_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


# --------------------------------------------------------------------------------------------
# Receita
# --------------------------------------------------------------------------------------------


class RevenueBody(BaseModel):
    channel_id: str = Field(min_length=1, max_length=32)
    store: str = Field(min_length=1, max_length=40)
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    tracking_id: str = Field(default="", max_length=60)
    clicks: int | None = Field(default=None, ge=0)
    orders: int | None = Field(default=None, ge=0)
    sales: float | None = Field(default=None, ge=0)
    commission: float | None = None
    currency: str = Field(default="BRL", min_length=3, max_length=3)
    note: str | None = Field(default=None, max_length=1000)


@router.get("/revenue")
def get_revenue(channel_id: str | None = None, months: int = 12, period: str | None = None) -> dict[str, Any]:
    """Linhas lançadas e, com canal, o resumo por mês e loja (sem contar o total da loja duas vezes)."""
    months = max(1, min(months, 60))
    out: dict[str, Any] = {"entries": revenue.list_entries(channel_id, period)}
    if channel_id:
        out["summary"] = revenue.summary(channel_id, months)
    return out


@router.post("/revenue")
def post_revenue(body: RevenueBody) -> dict[str, Any]:
    try:
        return revenue.upsert(body.channel_id, body.store, body.period, body.tracking_id, clicks=body.clicks,
                              orders=body.orders, sales=body.sales, commission=body.commission,
                              currency=body.currency, note=body.note)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except revenue.RevenueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/revenue/{entry_id}")
def delete_revenue(entry_id: int) -> dict[str, Any]:
    try:
        revenue.delete(entry_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="lançamento não encontrado") from exc
    return {"deleted": True}


# --------------------------------------------------------------------------------------------
# Resumo
# --------------------------------------------------------------------------------------------


@router.get("/money/summary")
def money_summary(channel_id: str | None = None, months: int = 12) -> dict[str, Any]:
    """Por canal: frentes, ofertas (com link, status e conferência) e receita por mês e loja."""
    with session_scope() as s:
        q = s.query(Channel).order_by(Channel.name)
        if channel_id:
            q = q.filter(Channel.id == channel_id)
        channels = [(c.id, c.name) for c in q.all()]
    months = max(1, min(months, 60))
    return {"channels": [
        {"channel_id": cid, "channel_name": name, **monetization.channel_money(cid),
         "revenue": revenue.summary(cid, months)}
        for cid, name in channels
    ]}

