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
from studio.media.real_media import EDITORIAL_WARNING
from studio.models import Channel, Publication
from studio.workers.business.monetization import description_links, fit_description
from studio.workers.jobs import JobContext, job
from studio.workers.ops import pending
from studio.workers.publish import quota, youtube_channels
from studio.workers.publish import youtube as yt
from studio.workers.publish.credits import credit_lines, credits_block, has_editorial
from studio.workers.visual import thumbs as thumb_ab


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


DESCRIPTION_MAX = 5000


def _description(meta: Metadata, credits: str = "", links: dict[str, Any] | None = None) -> str:
    """Descrição do YouTube: texto, créditos das imagens (mídia real) e hashtags. Com créditos, só o texto é cortado
    para caber no limite (créditos e hashtags ficam inteiros); sem créditos, como sempre foi. Com links de afiliado
    (`links` = {"top", "end"}), o topo vai antes do texto e o aviso legal no fim; também só o texto é cortado."""
    credits = credits.strip()
    if links and (links.get("top") or links.get("end")):
        tail = "\n\n".join(p for p in (credits, " ".join(meta.hashtags[:15])) if p)
        return fit_description(meta.description, tail, top=links.get("top", ""), end=links.get("end", ""),
                               limit=DESCRIPTION_MAX)
    if not credits:
        description = meta.description
        if meta.hashtags:
            description = f"{description}\n\n{' '.join(meta.hashtags[:15])}".strip()
        return description[:DESCRIPTION_MAX]
    tail = credits + (f"\n\n{' '.join(meta.hashtags[:15])}" if meta.hashtags else "")
    room = DESCRIPTION_MAX - len(tail) - 2
    body = meta.description.strip()[: max(0, room)].rstrip()
    return (f"{body}\n\n{tail}" if body else tail)[:DESCRIPTION_MAX]


def youtube_localizations(doc, lang: str, links: dict[str, Any] | None = None) -> dict[str, dict[str, str]]:
    """Título e descrição dos outros idiomas do projeto (o YouTube mostra a versão no idioma de quem assiste)."""
    out: dict[str, dict[str, str]] = {}
    for other in doc.languages:
        meta = doc.metadata.get(other)
        if other == lang or meta is None or not meta.title.strip():
            continue
        out[other] = {"title": meta.title.strip()[:100], "description": _description(meta, credits_block(doc, other), links)}
    return out


def _thumbs_followup(project_id: str, channel_id: str | None, settings: dict[str, Any], lang: str, video_id: str,
                     title: str, *, privacy: str, publish_at: str | None, is_long: bool) -> dict[str, Any]:
    """Depois do envio (ADR 0003, bloco I): sem teste A/B e com 2+ miniaturas, pendência de ver o CTR em 48 h e
    trocar pela outra opção; devolve `{"thumbs": {chosen, variants, ab_eligible}}` para `Publication.details` (vazio
    sem as miniaturas). A pendência só faz sentido para vídeo longo que vai ao ar (público ou agendado)."""
    variants = thumb_ab.existing_variants(settings, lang)
    if not variants:
        return {}
    chosen = thumb_ab.chosen_number(settings, lang)
    eligible = bool(thumb_ab.ab_rule(channel_id, chosen)["eligible"])
    if not eligible and len(variants) >= 2 and is_long and privacy in ("public", "scheduled"):
        when = "48 h depois de ir ao ar" if privacy == "scheduled" else "Em 48 h"
        if privacy == "scheduled" and publish_at:
            local = datetime.fromisoformat(publish_at.replace("Z", "+00:00")).astimezone()
            when = f"48 h depois de ir ao ar ({local:%d/%m %H:%M})"
        pending.ensure_auto(f"miniatura:{video_id}",
                            f'{when}, ver o CTR de "{title}": abaixo de 4%, trocar a miniatura pela '
                            f"{thumb_ab.swap_option(chosen)}ª opção",
                            f"/projetos/{project_id}", channel_id)
    return {"thumbs": {"chosen": chosen, "variants": [v["path"] for v in variants], "ab_eligible": eligible}}


def _playlist_choice(payload: dict[str, Any], channel_id: str | None) -> dict[str, str] | None:
    """Playlist do envio: a escolhida (id), nenhuma ("") ou, sem escolha, a playlist padrão do canal."""
    chosen = payload.get("playlist_id")
    if chosen:
        return {"id": str(chosen), "title": str(payload.get("playlist_title") or "")}
    if chosen == "" or not channel_id:
        return None
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        settings = dict(row.settings or {}) if row is not None else {}
    return youtube_channels.default_playlist(settings)


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
    links = description_links(project_id)  # links de afiliado (bloco D): vazio quando o projeto não liga
    snippet = {
        "title": (meta.title or doc.title)[:100],
        "description": _description(meta, credits_block(doc, lang), links),
        "tags": meta.tags[:30],
        "categoryId": str(payload.get("category_id") or "24"),
        "defaultLanguage": lang,
        "defaultAudioLanguage": lang,
    }
    localizations = youtube_localizations(doc, lang, links) if payload.get("localize") else {}
    # Token do canal do projeto (vinculado em Canais); sem vínculo, a conta escolhida no envio ou a global.
    channel_id = row.get("channel_id") or doc.channel_id
    account = yt.account_for_channel(channel_id, payload.get("account") or None)
    playlist = _playlist_choice(payload, channel_id)
    data_dir = get_settings().data_dir
    warnings: list[str] = []
    credits_details: dict[str, Any] = {}
    if (lines := credit_lines(doc, lang)):
        credits_details["credits"] = len(lines)
    if has_editorial(doc):
        credits_details["editorial"] = True
        warnings.append(EDITORIAL_WARNING)
    paid = bool(links["top"])
    if paid:
        # O pacote manual tem o CHECKLIST.md; no envio direto, o que pede ação vai nos avisos e o resto em details.
        credits_details["affiliate"] = {"pinned": links["pinned"], "checklist": links["checklist"]}
        warnings.extend(c for c in links["checklist"] if c.startswith("CONFERIR"))
        if links["pinned"]:
            warnings.append(f"Fixe este comentário no vídeo:\n{links['pinned']}")
    warnings.extend(f"links de afiliado fora da descrição: {err}" for err in links["errors"])
    # Miniatura escolhida entre as 3 do teste A/B (ADR 0003, bloco I); sem ela, a do build.
    thumb_rel, thumb_note = thumb_ab.pick_thumbnail(row["settings"], lang, project_id)
    thumb_rel = thumb_rel or entry.get("thumbnail")
    if thumb_note:
        warnings.append(thumb_note)
    estimate = quota.publish_estimate(thumbnail=bool(thumb_rel),
                                      captions=bool(entry.get("srt") and payload.get("captions", True)),
                                      playlist=bool(playlist))
    try:
        warnings.extend(quota.check_publish(estimate))
    except quota.QuotaExhausted as exc:
        raise ValueError(str(exc)) from exc
    with yt.new_client(timeout=httpx.Timeout(120, read=900, write=900)) as client:
        ctx.progress(0.02, "Enviando vídeo ao YouTube")
        video = yt.upload_video(
            data_dir / entry["video"],
            snippet=snippet,
            status=status,
            localizations=localizations or None,
            http=client,
            on_progress=lambda p: ctx.progress(0.02 + p * 0.85, "Enviando vídeo ao YouTube"),
            account=account,
            # Só com links: os mocks e chamadas antigas não conhecem o parâmetro.
            **({"paid_placement": True} if paid else {}),
        )
        video_id = video["id"]
        if thumb_rel:
            try:
                yt.set_thumbnail(video_id, data_dir / thumb_rel, client, account=account)
            except yt.YouTubeError as exc:
                warnings.append(str(exc))
        if entry.get("srt") and payload.get("captions", True):
            try:
                yt.upload_captions(video_id, data_dir / entry["srt"], lang, client, account=account)
            except yt.YouTubeError as exc:
                warnings.append(str(exc))
        if playlist:
            ctx.progress(0.95, "Colocando o vídeo na playlist")
            try:
                yt.add_to_playlist(client, yt.access_token(client, account), playlist["id"], video_id)
            except yt.YouTubeError as exc:
                warnings.append(str(exc))
                playlist = {**playlist, "error": str(exc)}
    url = f"https://youtu.be/{video_id}"
    try:  # o vídeo já subiu: uma falha aqui não pode impedir o registro da publicação
        thumbs_details = _thumbs_followup(project_id, row.get("channel_id") or doc.channel_id, row["settings"], lang,
                                          video_id, (meta.title or doc.title)[:100], privacy=privacy,
                                          publish_at=status.get("publishAt"), is_long=style.format.type == "long")
    except Exception as exc:  # noqa: BLE001
        thumbs_details = {}
        warnings.append(f"Não deu para registrar o acompanhamento das miniaturas: {exc}")
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
                         "youtube_channel": yt.account_title(account), "playlist": playlist, **credits_details,
                         **thumbs_details},
            )
        )
    ctx.progress(1.0, "Agendado no YouTube" if privacy == "scheduled" else "Enviado ao YouTube")
    return {"video_id": video_id, "url": url, "privacy": privacy, "publish_at": status.get("publishAt"), "warnings": warnings}
