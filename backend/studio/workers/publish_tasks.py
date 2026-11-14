"""Jobs de publicação (YouTube nativo)."""

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
from studio.models import Publication
from studio.workers.jobs import JobContext, job
from studio.workers.publish import youtube as yt


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
    synthetic = style.art.photoreal or style.publish.ai_label == "always" or Family.ai_video in style.all_families()
    if style.publish.ai_label != "never":
        status["containsSyntheticMedia"] = bool(synthetic)
    return status


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
    description = meta.description
    if meta.hashtags:
        description = f"{description}\n\n{' '.join(meta.hashtags[:15])}".strip()
    snippet = {
        "title": (meta.title or doc.title)[:100],
        "description": description[:5000],
        "tags": meta.tags[:30],
        "categoryId": str(payload.get("category_id") or "24"),
        "defaultLanguage": lang,
        "defaultAudioLanguage": lang,
    }
    data_dir = get_settings().data_dir
    warnings: list[str] = []
    with httpx.Client(timeout=httpx.Timeout(120, read=900, write=900)) as client:
        ctx.progress(0.02, "Enviando vídeo ao YouTube")
        video = yt.upload_video(
            data_dir / entry["video"],
            snippet=snippet,
            status=status,
            http=client,
            on_progress=lambda p: ctx.progress(0.02 + p * 0.85, "Enviando vídeo ao YouTube"),
        )
        video_id = video["id"]
        if entry.get("thumbnail"):
            try:
                yt.set_thumbnail(video_id, data_dir / entry["thumbnail"], client)
            except yt.YouTubeError as exc:
                warnings.append(str(exc))
        if entry.get("srt") and payload.get("captions", True):
            try:
                yt.upload_captions(video_id, data_dir / entry["srt"], lang, client)
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
                external_id=video_id,
                url=url,
                details={"privacy": privacy, "publish_at": status.get("publishAt"), "warnings": warnings,
                         "synthetic": status.get("containsSyntheticMedia")},
            )
        )
    ctx.progress(1.0, "Agendado no YouTube" if privacy == "scheduled" else "Enviado ao YouTube")
    return {"video_id": video_id, "url": url, "privacy": privacy, "publish_at": status.get("publishAt"), "warnings": warnings}
