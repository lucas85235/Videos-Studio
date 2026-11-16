"""Jobs de publicação (YouTube nativo e Buffer para TikTok e Instagram)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import Metadata
from studio.core.styles.library import get_style
from studio.core.styles.schema import Family
from studio.db import session_scope
from studio.ids import new_id
from studio.media.real_media import EDITORIAL_WARNING
from studio.models import Publication
from studio.workers.jobs import JobContext, job
from studio.workers.publish import buffer, buffer_flow, r2
from studio.workers.publish import youtube as yt
from studio.workers.publish.credits import credit_lines, credits_block, has_editorial
from studio.workers.publish.package import caption_for

log = logging.getLogger(__name__)


def youtube_status_body(style, privacy: str, publish_at: str | None) -> dict[str, Any]:
    status: dict[str, Any] = {"selfDeclaredMadeForKids": False}
    if privacy == "scheduled":
        if not publish_at:
            raise ValueError("informe a data e hora do agendamento")
        when = datetime.fromisoformat(publish_at.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.astimezone()
        if when <= datetime.now(UTC):
            raise ValueError("o horário do agendamento precisa estar no futuro")
        status.update({"privacyStatus": "private", "publishAt": when.astimezone(UTC).isoformat().replace("+00:00", "Z")})
    elif privacy in ("private", "unlisted", "public"):
        status["privacyStatus"] = privacy
    else:
        raise ValueError("privacidade inválida (use private, unlisted, public ou scheduled)")
    if style.publish.ai_label != "never":
        status["containsSyntheticMedia"] = synthetic_media(style)
    return status


def synthetic_media(style) -> bool:
    """Rótulo de conteúdo gerado por IA: arte fotorrealista, vídeo de IA ou o estilo pede sempre (nunca, se pedir)."""
    if style.publish.ai_label == "never":
        return False
    return bool(style.art.photoreal or style.publish.ai_label == "always" or Family.ai_video in style.all_families())


DESCRIPTION_MAX = 5000


def _description(meta: Metadata, credits: str = "") -> str:
    """Descrição do YouTube: texto, créditos das imagens (mídia real) e hashtags. Com créditos, só o texto é cortado
    para caber no limite (créditos e hashtags ficam inteiros); sem créditos, como sempre foi."""
    credits = credits.strip()
    if not credits:
        description = meta.description
        if meta.hashtags:
            description = f"{description}\n\n{' '.join(meta.hashtags[:15])}".strip()
        return description[:DESCRIPTION_MAX]
    tail = credits + (f"\n\n{' '.join(meta.hashtags[:15])}" if meta.hashtags else "")
    room = DESCRIPTION_MAX - len(tail) - 2
    body = meta.description.strip()[: max(0, room)].rstrip()
    return (f"{body}\n\n{tail}" if body else tail)[:DESCRIPTION_MAX]


def youtube_localizations(doc, lang: str) -> dict[str, dict[str, str]]:
    """Título e descrição dos outros idiomas do projeto (o YouTube mostra a versão no idioma de quem assiste)."""
    out: dict[str, dict[str, str]] = {}
    for other in doc.languages:
        meta = doc.metadata.get(other)
        if other == lang or meta is None or not meta.title.strip():
            continue
        out[other] = {"title": meta.title.strip()[:100], "description": _description(meta, credits_block(doc, other))}
    return out


@job("publish_youtube")
def run_publish_youtube(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Envia o vídeo final de um idioma ao YouTube (privado, não listado, público ou agendado)."""
    project_id = ctx.project_id
    assert project_id
    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    lang = payload.get("lang") or doc.primary_language
    privacy = payload.get("privacy", "private")
    last = row["settings"].get("last_final") or {}
    entry = (last.get("langs") or {}).get(lang)
    if not entry or not entry.get("video"):
        raise ValueError("renderize o vídeo final deste idioma antes de publicar")
    preview_only = last.get("preview_only_providers") or []
    if preview_only and privacy != "private":
        raise ValueError(
            f"bloqueado: o vídeo usou provedores só de prévia ({', '.join(preview_only)}). "
            "Envie como privado para testar, ou configure voz/imagens comerciais e renderize de novo."
        )
    status = youtube_status_body(style, privacy, payload.get("publish_at"))
    meta = doc.metadata.get(lang) or Metadata(title=doc.title)
    snippet = {
        "title": (meta.title or doc.title)[:100],
        "description": _description(meta, credits_block(doc, lang)),
        "tags": meta.tags[:30],
        "categoryId": str(payload.get("category_id") or "24"),
        "defaultLanguage": lang,
        "defaultAudioLanguage": lang,
    }
    localizations = youtube_localizations(doc, lang) if payload.get("localize") else {}
    # Token do canal do projeto (vinculado em Canais); sem vínculo, a conta global como antes.
    account = yt.account_for_channel(row.get("channel_id") or doc.channel_id)
    data_dir = get_settings().data_dir
    warnings: list[str] = []
    credits_details: dict[str, Any] = {}
    if (lines := credit_lines(doc, lang)):
        credits_details["credits"] = len(lines)
    if has_editorial(doc):
        credits_details["editorial"] = True
        warnings.append(EDITORIAL_WARNING)
    with httpx.Client(timeout=httpx.Timeout(120, read=900, write=900)) as client:
        ctx.progress(0.02, "Enviando vídeo ao YouTube")
        video = yt.upload_video(
            data_dir / entry["video"],
            snippet=snippet,
            status=status,
            localizations=localizations or None,
            http=client,
            on_progress=lambda p: ctx.progress(0.02 + p * 0.85, "Enviando vídeo ao YouTube"),
            account=account,
        )
        video_id = video["id"]
        if entry.get("thumbnail"):
            try:
                yt.set_thumbnail(video_id, data_dir / entry["thumbnail"], client, account=account)
            except yt.YouTubeError as exc:
                warnings.append(str(exc))
        if entry.get("srt") and payload.get("captions", True):
            try:
                yt.upload_captions(video_id, data_dir / entry["srt"], lang, client, account=account)
            except yt.YouTubeError as exc:
                warnings.append(str(exc))
    url = f"https://youtu.be/{video_id}"
    with session_scope() as s:
        s.add(
            Publication(
                id=new_id("pub"),
                project_id=project_id,
                platform="youtube",
                language=lang,
                driver="youtube_native",
                status="scheduled" if privacy == "scheduled" else "uploaded",
                scheduled_at=datetime.fromisoformat(status["publishAt"].replace("Z", "+00:00")) if status.get("publishAt") else None,
                external_id=video_id,
                url=url,
                details={"privacy": privacy, "publish_at": status.get("publishAt"), "warnings": warnings,
                         "synthetic": status.get("containsSyntheticMedia"), "localizations": sorted(localizations),
                         "youtube_channel": yt.account_title(account), **credits_details},
            )
        )
    ctx.progress(1.0, "Agendado no YouTube" if privacy == "scheduled" else "Enviado ao YouTube")
    return {"video_id": video_id, "url": url, "privacy": privacy, "publish_at": status.get("publishAt"), "warnings": warnings}


# --------------------------------------------------------------------------------------------
# Buffer (TikTok e Instagram Reels), com o vídeo numa URL pública do Cloudflare R2
# --------------------------------------------------------------------------------------------


@job("publish_buffer")
def run_publish_buffer(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Sobe o vídeo final ao R2 e cria o post no Buffer (fila, agora, próximo da fila ou horário marcado)."""
    project_id = ctx.project_id
    assert project_id
    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    lang = payload.get("lang") or doc.primary_language
    platform = payload.get("platform") or ""
    channel = payload.get("buffer_channel_id") or ""
    mode = payload.get("mode") or "queue"
    if platform not in buffer.PLATFORMS:
        raise ValueError("o Buffer publica aqui só no TikTok e no Instagram")
    if not channel:
        raise ValueError("escolha o perfil do Buffer")
    last = row["settings"].get("last_final") or {}
    entry = (last.get("langs") or {}).get(lang)
    if not entry or not entry.get("video"):
        raise ValueError("renderize o vídeo final deste idioma antes de publicar")
    preview_only = last.get("preview_only_providers") or []
    if preview_only:
        raise ValueError(
            f"bloqueado: o vídeo usou provedores só de prévia ({', '.join(preview_only)}) e o Buffer publica como "
            "público. Configure voz e imagens comerciais e renderize de novo."
        )
    meta = doc.metadata.get(lang)
    meta_dict = meta.model_dump() if meta else {"title": doc.title, "description": "", "tags": [], "hashtags": []}
    caption = caption_for(platform, meta_dict, doc.title, credits_block(doc, lang))
    synthetic = synthetic_media(style)
    due = None
    if mode == "scheduled":
        stamp = payload.get("publish_at")
        if not stamp:
            raise ValueError("informe a data e a hora do agendamento")
        due = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))

    def post_data(video_url: str) -> dict[str, Any]:
        return buffer.post_input(channel_id=channel, platform=platform, text=caption, video_url=video_url, mode=mode,
                                 due_at=due, ai_generated=synthetic)

    post_data("https://exemplo.invalid/v.mp4")  # valida (modo, horário no futuro) antes de subir o vídeo
    warnings: list[str] = [EDITORIAL_WARNING] if has_editorial(doc) else []
    video_path = get_settings().data_dir / entry["video"]
    key = r2.new_key(project_id, f"{platform}-{lang}")
    ctx.progress(0.02, "Enviando o vídeo ao Cloudflare R2")
    with httpx.Client(timeout=httpx.Timeout(60, read=900, write=900)) as client:
        url = r2.upload(video_path, key, http=client,
                        on_progress=lambda p: ctx.progress(0.02 + p * 0.8, "Enviando o vídeo ao Cloudflare R2"))
        ctx.progress(0.85, "Criando o post no Buffer")
        try:
            post = buffer.create_post(post_data(url), http=client)
        except Exception:
            try:
                r2.delete(key, http=client)  # o post não existe: o vídeo não precisa ficar no ar
            except r2.R2Error as exc:
                log.warning("o vídeo %s ficou no R2: %s", key, exc)
            raise
    status = buffer_flow.status_for(post)
    due_at = buffer_flow.parse_time(post.get("dueAt"))
    details: dict[str, Any] = {
        "caption": caption,
        "mode": mode,
        "buffer_status": post.get("status"),
        "buffer_channel_id": channel,
        "buffer_channel_name": payload.get("buffer_channel_name") or "",
        "r2_key": key,
        "video_url": url,
        "synthetic": synthetic,
        "warnings": warnings,
        "tracking": True,
        "last_checked": datetime.now(UTC).isoformat(),
    }
    if due_at:
        details["publish_at"] = due_at.isoformat()
    if (lines := credit_lines(doc, lang)):
        details["credits"] = len(lines)
    if has_editorial(doc):
        details["editorial"] = True
    pub_id = new_id("pub")
    with session_scope() as s:
        s.add(Publication(id=pub_id, project_id=project_id, platform=platform, language=lang, driver=buffer_flow.DRIVER,
                          status=status, scheduled_at=due_at, external_id=post.get("id"),
                          url=post.get("externalLink") or None, details=details))
    buffer_flow.remember_channel(row.get("channel_id") or doc.channel_id, platform, channel)
    if status in (buffer_flow.PUBLISHED, buffer_flow.FAILED):
        try:
            buffer_flow.sync_one(pub_id)  # já terminou: registra o fim e apaga o vídeo do R2
        except buffer.BufferError as exc:
            warnings.append(f"não deu para conferir o post agora: {exc}")
    ctx.progress(1.0, "Post criado no Buffer")
    return {"publication_id": pub_id, "post_id": post.get("id"), "status": status,
            "due_at": due_at.isoformat() if due_at else None, "warnings": warnings}
