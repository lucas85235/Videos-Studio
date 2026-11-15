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
    posting: dict[str, Any] | None = None  # {"timezone": ..., "slots": [{"weekdays": [...], "time": "HH:MM"}]}


def _channel_dict(row: Channel) -> dict[str, Any]:
    from studio.workers.publish.schedule import plan_of
    from studio.workers.publish.youtube_channels import public_info

    return {
        "id": row.id,
        "name": row.name,
        "niche": row.niche,
        "persona": row.persona,
        "languages": row.languages,
        "default_style_id": row.default_style_id,
        "style_bible": row.style_bible,
        "posting": plan_of(row.settings).model_dump(),
        "youtube": public_info(row.settings),  # canal do YouTube ligado (None se não houver)
    }


def _channel_or_404(channel_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        return _channel_dict(row)


def _apply_channel(row: Channel, body: ChannelBody, fields: set[str]) -> None:
    from studio.workers.publish.schedule import PostingPlan

    data = body.model_dump()
    for key in fields - {"posting"}:
        setattr(row, key, data[key])
    if "posting" in fields and body.posting is not None:
        try:
            plan = PostingPlan.model_validate(body.posting)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"horários de postagem inválidos: {exc}") from exc
        row.settings = {**(row.settings or {}), "posting": plan.model_dump()}


@router.get("/channels")
def channels() -> list[dict[str, Any]]:
    with session_scope() as s:
        return [_channel_dict(r) for r in s.query(Channel).order_by(Channel.name).all()]


@router.post("/channels")
def create_channel(body: ChannelBody) -> dict[str, Any]:
    with session_scope() as s:
        row = Channel(id=new_id("chn"), name=body.name, settings={})
        _apply_channel(row, body, set(ChannelBody.model_fields))
        s.add(row)
        s.flush()
        return _channel_dict(row)


@router.put("/channels/{channel_id}")
def update_channel(channel_id: str, body: ChannelBody) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        # só os campos enviados: a tela de canais antiga não manda `posting` e não pode apagar os horários
        _apply_channel(row, body, set(body.model_fields_set) | {"name"})
        return _channel_dict(row)


@router.get("/channels/{channel_id}/slots")
def channel_slots(channel_id: str, count: int = 5) -> dict[str, Any]:
    from studio.workers.publish import schedule

    try:
        return schedule.channel_slots(channel_id, count=max(1, min(count, 20)))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc


@router.delete("/channels/{channel_id}")
def delete_channel(channel_id: str) -> dict[str, Any]:
    from studio.workers.publish import youtube_channels

    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return {"deleted": False}
        settings = dict(row.settings or {})
        s.delete(row)
    youtube_channels.channel_deleted(channel_id, settings)
    return {"deleted": True}


class ChannelLinkBody(BaseModel):
    url: str = Field(min_length=2, max_length=500)  # link do canal, @handle ou id UC…
    channel_id: str | None = Field(default=None, max_length=32)  # canal do Vulto; vazio cria um novo


@router.post("/channels/from-link")
def channel_from_link(body: ChannelLinkBody) -> dict[str, Any]:
    """Liga (ou cria) um canal pelo link público do YouTube: nome e vídeos recentes, sem login. Não publica."""
    from studio.workers.publish import youtube as yt
    from studio.workers.publish import youtube_channels

    try:
        channel_id, created = youtube_channels.link_from_public(body.url, body.channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"channel": _channel_or_404(channel_id), "created": created}


@router.post("/channels/{channel_id}/youtube/refresh")
def channel_youtube_refresh(channel_id: str) -> dict[str, Any]:
    """Lê de novo nome, foto, descrição, números e vídeos recentes do canal no YouTube."""
    from studio.workers.publish import youtube as yt
    from studio.workers.publish import youtube_channels

    try:
        warnings = youtube_channels.refresh(channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except yt.YouTubeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"channel": _channel_or_404(channel_id), "warnings": warnings}


@router.delete("/channels/{channel_id}/youtube")
def channel_youtube_unlink(channel_id: str, forget: bool = False) -> dict[str, Any]:
    """Desvincula: apaga só o token deste canal. `forget=true` também tira os dados do YouTube do canal."""
    from studio.workers.publish import youtube_channels

    try:
        youtube_channels.unlink(channel_id, forget=forget)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    return {"channel": _channel_or_404(channel_id)}


@router.post("/channels/{channel_id}/suggest")
def channel_suggest(channel_id: str) -> dict[str, Any]:
    """Proposta de nicho, persona, idiomas, estilo padrão e identidade visual pelo Claude (não salva nada)."""
    from studio.providers.base import ProviderError
    from studio.workers.publish import youtube_channels

    try:
        return youtube_channels.suggest_profile(channel_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="canal não encontrado") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ProviderError as exc:  # inclui ChainFailed (Claude fora do ar ou sem login)
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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


# --------------------------------------------------------------------------------------------
# Biblioteca de músicas do usuário
# --------------------------------------------------------------------------------------------


@router.get("/library/music")
def library_music() -> dict[str, Any]:
    from studio.media import audio, music_library

    tracks = music_library.list_tracks()
    counts = {mood: sum(1 for t in tracks if t.mood == mood) for mood in audio.MOODS}
    return {"dir": str(music_library.music_root()), "tracks": [t.to_dict() for t in tracks], "counts": counts,
            "moods": list(audio.MOODS)}


@router.post("/library/music/open")
def open_library_music() -> dict[str, Any]:
    """Abre a pasta de músicas no Explorer (o app roda na sua máquina)."""
    import os
    import sys

    from studio.media import music_library

    root = music_library.music_root()
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail=f"abra manualmente: {root}")
    os.startfile(root)  # noqa: S606 - pasta local do próprio app
    return {"opened": str(root)}


# --------------------------------------------------------------------------------------------
# Manutenção: limpeza do cache
# --------------------------------------------------------------------------------------------


@router.get("/maintenance/cache")
def cache_report() -> dict[str, Any]:
    """Quanto o cache ocupa e quanto a limpeza liberaria (não apaga nada)."""
    from studio.core.graph import gc

    return gc.collect(dry_run=True).to_dict()


@router.post("/maintenance/cache/clean")
def cache_clean() -> dict[str, Any]:
    from studio.core.graph import gc

    if any(j["status"] in ("queued", "running") for j in list_jobs(limit=200)):
        raise HTTPException(status_code=409, detail="há tarefas em andamento; espere terminarem para limpar o cache")
    return gc.collect(dry_run=False).to_dict()


# --------------------------------------------------------------------------------------------
# Agenda de publicações
# --------------------------------------------------------------------------------------------


class MarkPublishedBody(BaseModel):
    url: str | None = Field(default=None, max_length=2000)


@router.get("/agenda")
def publishing_agenda(days_back: int = 14) -> dict[str, Any]:
    from studio.workers.publish import schedule

    return schedule.agenda(days_back=max(1, min(days_back, 120)))


@router.post("/publications/{publication_id}/published")
def mark_publication_published(publication_id: str, body: MarkPublishedBody) -> dict[str, Any]:
    from studio.workers.publish import schedule

    if body.url and not body.url.strip().lower().startswith(("http://", "https://")):
        raise HTTPException(status_code=422, detail="o link do post precisa começar com http:// ou https://")
    try:
        return schedule.mark_published(publication_id, body.url)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="publicação não encontrada") from exc


@router.post("/publications/{publication_id}/open")
def open_publication_folder(publication_id: str) -> dict[str, Any]:
    """Abre a pasta do pacote manual no Explorer."""
    import os
    import sys
    from pathlib import Path

    from studio.models import Publication

    with session_scope() as s:
        row = s.get(Publication, publication_id)
        folder = Path(row.package_path) if row and row.package_path else None
    data_root = get_settings().data_dir.resolve()
    if folder is None or not folder.is_dir() or not folder.resolve().is_relative_to(data_root):
        raise HTTPException(status_code=404, detail="pasta do pacote não encontrada")
    if sys.platform != "win32":
        raise HTTPException(status_code=501, detail=f"abra manualmente: {folder}")
    os.startfile(folder)  # noqa: S606 - pasta local do próprio app
    return {"opened": str(folder)}


class YouTubeBatchBody(BaseModel):
    items: list[dict[str, str]] = Field(min_length=1, max_length=50)


@router.get("/agenda/youtube-ready")
def youtube_ready() -> list[dict[str, Any]]:
    from studio.workers.publish import schedule

    return schedule.ready_for_youtube()


@router.post("/agenda/youtube-batch")
def youtube_batch(body: YouTubeBatchBody) -> dict[str, Any]:
    """Agenda vários vídeos de uma vez, cada um no próximo horário livre do seu canal."""
    from studio.workers.publish import schedule
    from studio.workers.publish import youtube as yt

    if not yt.any_connected():
        raise HTTPException(status_code=400, detail="vincule um canal do YouTube primeiro (em Canais ou na aba Vídeo e publicação de um projeto)")
    return {"results": schedule.plan_youtube_batch(body.items, get_runner().submit)}


# --------------------------------------------------------------------------------------------
# Vozes: catálogo e amostras
# --------------------------------------------------------------------------------------------


class VoiceSampleRequest(BaseModel):
    provider: str = Field(min_length=1, max_length=64)
    voice: str = Field(min_length=1, max_length=120)
    lang: str = Field(default="pt-BR", min_length=2, max_length=16)


@router.get("/voices")
def voice_catalog(lang: str = "pt-BR", providers: str | None = None, purpose: str | None = None) -> dict[str, Any]:
    """Vozes com amostra. `purpose=preview` lista a voz local da prévia; `purpose=final`, as vozes do vídeo final."""
    from studio.providers import voices

    ids = [p for p in (providers or "").split(",") if p] or None
    if ids is None and purpose in ("preview", "final"):
        ids = voices.providers_for(purpose)
    return {"lang": lang, "sample_text": voices.sample_text(lang), "providers": voices.catalog(lang, ids)}


@router.post("/voices/sample")
def voice_sample(body: VoiceSampleRequest) -> dict[str, Any]:
    """Gera a amostra na primeira vez (um pedido da cota do provedor) e devolve o arquivo."""
    from studio.providers.base import NotConfigured, ProviderError, QuotaExceeded, TransientError
    from studio.providers.voices import VoiceError, make_sample

    try:
        rel = make_sample(body.provider, body.voice, body.lang)
    except VoiceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except NotConfigured as exc:
        raise HTTPException(status_code=409, detail=f"provedor indisponível: {exc}") from exc
    except QuotaExceeded as exc:
        raise HTTPException(status_code=429, detail=f"cota esgotada: {exc}") from exc
    except TransientError as exc:
        raise HTTPException(status_code=503, detail=f"falha temporária, tente de novo: {exc}") from exc
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"path": rel, "url": f"/files/{rel}"}
