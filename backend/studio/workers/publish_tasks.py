"""Jobs de publicação (YouTube nativo e Reels pelo Instagram nativo)."""

from __future__ import annotations

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
from studio.workers.publish import instagram as ig
from studio.workers.publish import youtube as yt
from studio.workers.publish.credits import credit_lines, credits_block, has_editorial
from studio.workers.publish.package import caption_for


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
    label = ai_label(style)
    if label is not None:
        status["containsSyntheticMedia"] = label
    return status


def ai_label(style) -> bool | None:
    """Rótulo de IA do estilo: None quando o estilo pede para nunca declarar; senão, se o vídeo é sintético
    (imagens realistas, vídeo IA ou "sempre")."""
    if style.publish.ai_label == "never":
        return None
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
# Instagram nativo (Reels)
# --------------------------------------------------------------------------------------------


def instagram_entry(project_id: str, lang: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Linha do projeto e saída final do idioma; bloqueia render com provedores de prévia (não há post privado)."""
    row = service.get_project_row(project_id)
    last = row["settings"].get("last_final") or {}
    entry = (last.get("langs") or {}).get(lang)
    if not entry or not entry.get("video"):
        raise ValueError("renderize o vídeo final deste idioma antes de publicar")
    preview_only = last.get("preview_only_providers") or []
    if preview_only:
        raise ValueError(
            f"bloqueado: o vídeo usou provedores só de prévia ({', '.join(preview_only)}) e o Instagram não tem envio "
            "privado. Configure voz/imagens comerciais e renderize de novo."
        )
    return row, entry


def instagram_video_url(entry: dict[str, Any], given: str | None) -> str:
    """URL pública do vídeo: a informada na hora ou o endereço público das opções + o caminho na pasta de dados."""
    url = (given or "").strip() or ig.public_url_for(entry.get("video") or "")
    if not url:
        raise ValueError("informe a URL pública do vídeo (o Instagram baixa o arquivo de lá) ou configure o endereço "
                         "público nas opções do Instagram")
    if not url.startswith("https://"):
        raise ValueError("a URL do vídeo precisa começar com https://")
    return url


@job("publish_instagram")
def run_publish_instagram(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Publica o vídeo final de um idioma como Reel: cria o envio, espera o processamento e publica.

    Com `container_id` (e `publication_id`), retoma um envio que ficou processando além do tempo de espera."""
    project_id = ctx.project_id
    assert project_id
    doc = service.get_doc(project_id)
    lang = payload.get("lang") or doc.primary_language
    row, entry = instagram_entry(project_id, lang)
    user_id = ig.account_for_channel(row.get("channel_id") or doc.channel_id)
    username = ig.get_account(user_id).get("username", "")
    resume = str(payload.get("container_id") or "")
    publication_id = str(payload.get("publication_id") or "")
    warnings: list[str] = []
    details: dict[str, Any] = {"account": username}
    if not resume:
        style = get_style(doc.style_id, doc.style_overrides)
        ig.check_video(get_settings().data_dir / entry["video"], entry.get("duration"))
        video_url = instagram_video_url(entry, payload.get("video_url"))
        thumb = str(entry.get("thumbnail") or "")
        cover_url = str(payload.get("cover_url") or "").strip()
        if not cover_url and thumb.lower().endswith((".jpg", ".jpeg")):
            cover_url = ig.public_url_for(thumb)
        meta = doc.metadata.get(lang) or Metadata(title=doc.title)
        caption = ig.fit_caption(caption_for("instagram", meta.model_dump(), doc.title, credits_block(doc, lang)))
        if has_editorial(doc):
            warnings.append(EDITORIAL_WARNING)
            details["editorial"] = True
        details.update(video_url=video_url, cover_url=cover_url or None, caption=caption, ai_label=ai_label(style),
                       share_to_feed=bool(payload.get("share_to_feed", True)))
    with ig.new_client(120) as client:
        ctx.progress(0.02, "Conferindo a conta do Instagram")
        token = ig.access_token(client, user_id)
        limit = ig.publishing_limit(client, user_id, token)
        if limit["used"] >= limit["total"]:
            raise ig.RateLimited(f"a conta já publicou {limit['used']} dos {limit['total']} posts permitidos pela API "
                                 "nas últimas 24 h; tente mais tarde")
        if resume:
            container = resume
        else:
            ctx.progress(0.05, "Conferindo a URL do vídeo")
            ig.check_public_url(client, details["video_url"])
            ctx.progress(0.08, "Enviando o Reel ao Instagram")
            container, extra = ig.create_container(
                client, user_id, token, video_url=details["video_url"], caption=details["caption"],
                share_to_feed=details["share_to_feed"], cover_url=details["cover_url"] or "",
                ai_generated=bool(details["ai_label"]),
            )
            if extra:
                warnings.extend(extra)
                details["ai_label"] = False
        try:
            ig.wait_container(
                client, container, token, interval_s=ig.POLL_INTERVAL_S, timeout_s=ig.POLL_TIMEOUT_S, sleep=ig.pause,
                on_tick=lambda p: ctx.progress(0.1 + p * 0.8, "O Instagram está processando o vídeo"),
                cancelled=ctx.cancelled,
            )
        except ig.ContainerPending as exc:
            pub_id = save_instagram(project_id, lang, publication_id, status="processing", external_id=container,
                                    url=None, details={**details, "container_id": container, "warnings": warnings},
                                    error=str(exc))
            ctx.progress(1.0, "Ainda processando no Instagram")
            return {"pending": True, "container_id": container, "publication_id": pub_id, "message": str(exc),
                    "warnings": warnings}
        except ig.InstagramError as exc:
            if publication_id:
                save_instagram(project_id, lang, publication_id, status="failed", external_id=container, url=None,
                               details={}, error=str(exc))
            raise
        ctx.progress(0.92, "Publicando o Reel")
        media_id = ig.publish_container(client, user_id, token, container)
        url = ig.permalink(client, media_id, token)
    pub_id = save_instagram(
        project_id, lang, publication_id, status="published", external_id=media_id, url=url or None,
        details={**details, "container_id": container, "warnings": warnings, "published_at": datetime.now(UTC).isoformat()},
        error=None,
    )
    ctx.progress(1.0, "Publicado no Instagram")
    return {"media_id": media_id, "url": url, "publication_id": pub_id, "warnings": warnings}


def save_instagram(project_id: str, lang: str, publication_id: str, *, status: str, external_id: str | None,
                   url: str | None, details: dict[str, Any], error: str | None) -> str:
    """Cria (ou atualiza, ao retomar) a publicação do Reel."""
    with session_scope() as s:
        row = s.get(Publication, publication_id) if publication_id else None
        if row is None:
            row = Publication(id=new_id("pub"), project_id=project_id, platform="instagram", language=lang,
                              driver="instagram_native", details={})
            s.add(row)
        row.status = status
        row.external_id = external_id
        if url:
            row.url = url
        row.details = {**(row.details or {}), **details}
        row.error = error
        return row.id
