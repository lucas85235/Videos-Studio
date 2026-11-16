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
from studio.models import Channel, Publication
from studio.workers.business.monetization import description_links, fit_description
from studio.workers.jobs import JobContext, job
from studio.workers.ops import pending
from studio.workers.publish import buffer, buffer_flow, quota, r2, youtube_channels
from studio.workers.publish import youtube as yt
from studio.workers.publish.credits import credit_lines, credits_block, has_editorial
from studio.workers.publish.package import caption_for
from studio.workers.visual import thumbs as thumb_ab

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
    # O Buffer só baixa e valida o vídeo na hora de publicar: avisa já o que o TikTok ou o Reel não aceitam.
    warnings += buffer.video_warnings(platform, aspect=style.format.aspect, duration_s=entry.get("duration"),
                                      size_bytes=video_path.stat().st_size if video_path.exists() else None)
    key = r2.new_key(project_id, f"{platform}-{lang}")
    details: dict[str, Any] = {
        "caption": caption,
        "mode": mode,
        "buffer_channel_id": channel,
        "buffer_channel_name": payload.get("buffer_channel_name") or "",
        "r2_key": key,
        **r2.location(),
        "synthetic": synthetic,
        "warnings": warnings,
        "tracking": True,
    }
    if (lines := credit_lines(doc, lang)):
        details["credits"] = len(lines)
    if has_editorial(doc):
        details["editorial"] = True
    pub_id = new_id("pub")
    ctx.progress(0.02, "Enviando o vídeo ao Cloudflare R2")
    with httpx.Client(timeout=httpx.Timeout(60, read=900, write=900)) as client:
        url = r2.upload(video_path, key, http=client,
                        on_progress=lambda p: ctx.progress(0.02 + p * 0.8, "Enviando o vídeo ao Cloudflare R2"))
        details["video_url"] = url
        ctx.progress(0.85, "Criando o post no Buffer")
        try:
            post = buffer.create_post(post_data(url), http=client)
        except buffer.BufferError as exc:
            if not exc.uncertain:
                _drop_video(key, client)  # o Buffer recusou: o post não existe e o vídeo não precisa ficar no ar
                raise
            # Sem resposta clara (rede, erro 5xx): o post pode ter sido criado. O vídeo fica no R2 por uns dias e a
            # publicação fica registrada para a pessoa conferir no Buffer ou cancelar (o que apaga o vídeo).
            now = datetime.now(UTC)
            message = (f"não deu para confirmar se o Buffer criou o post ({exc}). Confira a fila do Buffer: se o post "
                       "estiver lá, ele usa o vídeo que ficou no R2. Não envie de novo antes de conferir (criaria um "
                       "post duplicado); cancele aqui para apagar o vídeo.")
            details = buffer_flow.failed_details({**details, "uncertain": True}, now, buffer_flow.UNCERTAIN_KEEP)
            details["last_checked"] = now.isoformat()
            _save_buffer_publication(pub_id, project_id, platform, lang, buffer_flow.FAILED, None, None, None,
                                     details, error=message)
            raise RuntimeError(message) from exc
        except Exception:
            _drop_video(key, client)
            raise
    now = datetime.now(UTC)
    status = buffer_flow.status_for(post)
    due_at = buffer_flow.parse_time(post.get("dueAt"))
    details["buffer_status"] = post.get("status")
    details["last_checked"] = now.isoformat()
    if due_at:
        details["publish_at"] = due_at.isoformat()
    error = None
    if status == buffer_flow.FAILED:
        error = ((post.get("error") or {}).get("message")) or "o Buffer não conseguiu publicar"
        details = buffer_flow.failed_details(details, now)
    else:
        details["checks"] = 0
        details["next_check"] = buffer_flow.next_check(status, due_at, 0, now).isoformat()
    _save_buffer_publication(pub_id, project_id, platform, lang, status, due_at, post.get("id"),
                             post.get("externalLink") or None, details, error=error)
    buffer_flow.remember_channel(row.get("channel_id") or doc.channel_id, platform, channel)
    if status == buffer_flow.PUBLISHED:
        try:
            buffer_flow.sync_one(pub_id)  # já saiu: registra o fim e apaga o vídeo do R2 (sem consultar o Buffer)
        except buffer.BufferError as exc:
            warnings.append(f"não deu para conferir o post agora: {exc}")
    ctx.progress(1.0, "Post criado no Buffer")
    return {"publication_id": pub_id, "post_id": post.get("id"), "status": status,
            "due_at": due_at.isoformat() if due_at else None, "warnings": warnings, "error": error}


def _drop_video(key: str, client: httpx.Client) -> None:
    try:
        r2.delete(key, http=client)
    except r2.R2Error as exc:
        log.warning("o vídeo %s ficou no R2: %s", key, exc)


def _save_buffer_publication(pub_id: str, project_id: str, platform: str, lang: str, status: str,
                             due_at: datetime | None, post_id: str | None, url: str | None,
                             details: dict[str, Any], *, error: str | None = None) -> None:
    with session_scope() as s:
        s.add(Publication(id=pub_id, project_id=project_id, platform=platform, language=lang,
                          driver=buffer_flow.DRIVER, status=status, scheduled_at=due_at, external_id=post_id,
                          url=url, error=error, details=details))
