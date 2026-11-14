"""Rotas de estilos, canais, provedores, jobs, custos e status."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func

from studio.config import get_settings
from studio.core.agent import editor
from studio.core.styles.library import (
    StyleNotFound,
    delete_user_style,
    get_style,
    list_styles,
    load_presets,
    save_user_style,
)
from studio.core.styles.schema import FAMILIES_V1, FAMILY_LABELS, StyleSpec, policy_warnings
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, CostEntry, ProviderUsage
from studio.providers import credentials, registry
from studio.providers.registry import ChainFailed
from studio.workers.jobs import get_job, get_runner, list_jobs

router = APIRouter(prefix="/api", tags=["geral"])


# --------------------------------------------------------------------------------------------
# Estilos
# --------------------------------------------------------------------------------------------


class StyleBody(BaseModel):
    style: dict[str, Any]
    parent_id: str | None = None


class DuplicateBody(BaseModel):
    new_id: str
    name: str


class FromTextBody(BaseModel):
    description: str = Field(min_length=3, max_length=2000)
    base_style_id: str
    new_id: str | None = None
    name: str | None = None


@router.get("/styles")
def styles() -> list[dict[str, Any]]:
    items = list_styles()
    for item in items:
        try:
            item["warnings"] = policy_warnings(StyleSpec.model_validate(item["style"]))
        except Exception as exc:  # noqa: BLE001
            item["warnings"] = [f"estilo inválido: {exc}"]
    return items


@router.get("/styles/meta")
def styles_meta() -> dict[str, Any]:
    return {
        "families": [{"id": f.value, "label": FAMILY_LABELS[f], "implemented": f in FAMILIES_V1} for f in FAMILY_LABELS],
        "schema": StyleSpec.model_json_schema(),
    }


@router.get("/styles/{style_id}")
def style_detail(style_id: str) -> dict[str, Any]:
    try:
        style = get_style(style_id)
    except StyleNotFound as exc:
        raise HTTPException(status_code=404, detail=f"estilo não encontrado: {exc}") from exc
    return {"style": style.model_dump(mode="json"), "preset": style_id in load_presets(), "warnings": policy_warnings(style)}


@router.post("/styles")
def create_style(body: StyleBody) -> dict[str, Any]:
    try:
        style = StyleSpec.model_validate(body.style)
        version = save_user_style(style, body.parent_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"style": style.model_dump(mode="json"), "version": version, "warnings": policy_warnings(style)}


@router.put("/styles/{style_id}")
def update_style(style_id: str, body: StyleBody) -> dict[str, Any]:
    data = {**body.style, "id": style_id}
    return create_style(StyleBody(style=data, parent_id=body.parent_id))


@router.delete("/styles/{style_id}")
def remove_style(style_id: str) -> dict[str, Any]:
    if style_id in load_presets():
        raise HTTPException(status_code=400, detail="presets não podem ser apagados")
    return {"deleted": delete_user_style(style_id)}


@router.post("/styles/{style_id}/duplicate")
def duplicate_style(style_id: str, body: DuplicateBody) -> dict[str, Any]:
    try:
        data = get_style(style_id).model_dump(mode="json")
    except StyleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    data.update({"id": body.new_id, "name": body.name})
    return create_style(StyleBody(style=data, parent_id=style_id))


@router.post("/styles/from-text")
def style_from_text(body: FromTextBody) -> dict[str, Any]:
    try:
        return editor.style_from_text(body.description, body.base_style_id, new_id=body.new_id, name=body.name)
    except StyleNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ChainFailed as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------------
# Canais
# --------------------------------------------------------------------------------------------


class ChannelBody(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    niche: str = ""
    persona: str = ""
    languages: list[str] = ["pt-BR"]
    default_style_id: str | None = None
    style_bible: dict[str, Any] = {}


def _channel_dict(row: Channel) -> dict[str, Any]:
    return {
        "id": row.id,
        "name": row.name,
        "niche": row.niche,
        "persona": row.persona,
        "languages": row.languages,
        "default_style_id": row.default_style_id,
        "style_bible": row.style_bible,
    }


@router.get("/channels")
def channels() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [_channel_dict(r) for r in s.query(Channel).order_by(Channel.name).all()]


@router.post("/channels")
def create_channel(body: ChannelBody) -> dict[str, Any]:
    with session_scope() as s:
        row = Channel(id=new_id("chn"), **body.model_dump())
        s.add(row)
        s.flush()
        return _channel_dict(row)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: str, body: ChannelBody) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        for key, value in body.model_dump().items():
            setattr(row, key, value)
        return _channel_dict(row)


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return {"deleted": False}
        s.delete(row)
        return {"deleted": True}


# --------------------------------------------------------------------------------------------
# Provedores e custos
# --------------------------------------------------------------------------------------------


class CredentialBody(BaseModel):
    fields: dict[str, str]


@router.get("/providers")
def providers() -> list[dict[str, Any]]:
    return registry.describe_all()


@router.put("/providers/{provider_id}/credentials")
def set_credentials(provider_id: str, body: CredentialBody) -> dict[str, Any]:
    provider = registry.get(provider_id)
    allowed = set(provider.info.credential_fields)
    unknown = set(body.fields) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"campos desconhecidos: {', '.join(sorted(unknown))}")
    current = credentials.get_credential(provider_id)
    current.update({k: v for k, v in body.fields.items() if v})
    credentials.set_credential(provider_id, current)
    ok, reason = provider.availability()
    return {"available": ok, "reason": reason}


@router.delete("/providers/{provider_id}/credentials")
def delete_credentials(provider_id: str) -> dict[str, Any]:
    credentials.delete_credential(provider_id)
    return {"deleted": True}


@router.get("/costs")
def costs(project_id: str | None = None) -> dict[str, Any]:
    with session_scope() as s:
        query = s.query(CostEntry.provider, CostEntry.kind, func.sum(CostEntry.units), func.sum(CostEntry.cost_usd), func.count())
        if project_id:
            query = query.filter(CostEntry.project_id == project_id)
        rows = query.group_by(CostEntry.provider, CostEntry.kind).all()
        usage = s.query(ProviderUsage).order_by(ProviderUsage.day.desc()).limit(60).all()
        return {
            "by_provider": [
                {"provider": p, "kind": k, "units": round(u or 0, 2), "cost_usd": round(c or 0, 4), "calls": n}
                for p, k, u, c, n in rows
            ],
            "total_usd": round(sum((c or 0) for _, _, _, c, _ in rows), 4),
            "usage": [
                {"provider": r.provider, "day": r.day, "calls": r.calls, "units": r.units, "errors": r.errors,
                 "quota_exhausted": r.quota_exhausted}
                for r in usage
            ],
        }


# --------------------------------------------------------------------------------------------
# Jobs e status
# --------------------------------------------------------------------------------------------


@router.get("/jobs")
def jobs(project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    return list_jobs(project_id, limit)


@router.get("/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    data = get_job(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="job não encontrado")
    return data


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    return {"canceled": get_runner().cancel(job_id)}


@router.get("/status")
def status() -> dict[str, Any]:
    from studio.media.ffmpeg import available_encoders, video_encoder_args
    from studio.media.render import renderer_version

    settings = get_settings()
    provs = registry.describe_all()
    return {
        "data_dir": str(settings.data_dir),
        "offline": settings.offline,
        "renderer_version": renderer_version(),
        "video_encoder": video_encoder_args()[1],
        "nvenc": "h264_nvenc" in available_encoders(),
        "providers_available": {kind: [p["id"] for p in provs if p["kind"] == kind and p["available"]]
                                for kind in ("llm", "tts", "image", "align")},
    }
