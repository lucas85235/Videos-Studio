"""Jobs de publicação (YouTube nativo)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from studio.config import get_settings
from studio.core.project import branches, service
from studio.core.project.document import Metadata
from studio.core.styles.library import get_style
from studio.core.styles.schema import Family
from studio.db import session_scope
from studio.ids import new_id
from studio.media.real_media import EDITORIAL_WARNING
from studio.models import Publication
from studio.workers.analytics import ab_versions
from studio.workers.business.monetization import description_links, fit_description
from studio.workers.jobs import JobContext, job
from studio.workers.ops import pending
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


def branch_source(project_id: str, branch: str | None, row: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Documento e último vídeo final da principal ou de um ramo A/B (o vídeo tem que ser da cabeça do ramo)."""
    if branch in (None, branches.MAIN):
        return service.get_doc(project_id), row["settings"].get("last_final") or {}
    info = branches.get_branch(project_id, branch)
    last = info["last_final"] or {}
    if last and last.get("version") != info["head"]:
        raise ValueError(f"a versão {info['letter']} mudou depois do último render: renderize o vídeo final dela de novo")
    return service.get_doc(project_id, info["head"]), last


@job("publish_youtube")
def run_publish_youtube(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Envia o vídeo final de um idioma ao YouTube (privado, não listado, público ou agendado)."""
    project_id = ctx.project_id
    assert project_id
    row = service.get_project_row(project_id)
    # Ramo A/B ("versão B"): o documento e o vídeo final vêm da cabeça do ramo, não da principal.
    branch = payload.get("branch") or None
    doc, last = branch_source(project_id, branch, row)
    on_branch = branch not in (None, branches.MAIN)
    style = get_style(doc.style_id, doc.style_overrides)
    lang = payload.get("lang") or doc.primary_language
    privacy = payload.get("privacy", "private")
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
    paid = bool(links["top"])
    if paid:
        # O pacote manual tem o CHECKLIST.md; no envio direto, o que pede ação vai nos avisos e o resto em details.
        credits_details["affiliate"] = {"pinned": links["pinned"], "checklist": links["checklist"]}
        warnings.extend(c for c in links["checklist"] if c.startswith("CONFERIR"))
        if links["pinned"]:
            warnings.append(f"Fixe este comentário no vídeo:\n{links['pinned']}")
    warnings.extend(f"links de afiliado fora da descrição: {err}" for err in links["errors"])
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
            # Só com links: os mocks e chamadas antigas não conhecem o parâmetro.
            **({"paid_placement": True} if paid else {}),
        )
        video_id = video["id"]
        # Miniatura escolhida entre as 3 do teste A/B (ADR 0003, bloco I); sem ela, a do build. O ramo usa a dele.
        thumb_rel, thumb_note = (None, None) if on_branch else thumb_ab.pick_thumbnail(row["settings"], lang, project_id)
        thumb_rel = thumb_rel or entry.get("thumbnail")
        if thumb_note:
            warnings.append(thumb_note)
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
    url = f"https://youtu.be/{video_id}"
    try:
        ab_tag = ab_versions.publication_tag(project_id, branch, lang)
    except Exception:  # noqa: BLE001 - ramo apagado no meio do envio: publica sem a marca
        ab_tag = {}
    try:  # o vídeo já subiu: uma falha aqui não pode impedir o registro da publicação
        thumbs_details = {} if on_branch else _thumbs_followup(project_id, row.get("channel_id") or doc.channel_id, row["settings"], lang,
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
                         "youtube_channel": yt.account_title(account), **credits_details, **thumbs_details,
                         **ab_tag},
            )
        )
    if ab_tag:
        try:  # A e B públicos no YouTube: abre o experimento de Desempenho (variável "versao")
            ab_versions.sync_experiments(project_id)
            if on_branch and not branches.get_branch(project_id, branch)["experiment_id"]:
                reason = ab_versions.missing_reason(ab_versions.published_videos(project_id), ab_tag["versao"])
                if reason:
                    warnings.append(f"O experimento A/B ainda não abriu. {reason}")
        except Exception as exc:  # noqa: BLE001 - o vídeo já subiu
            warnings.append(f"Não deu para abrir o experimento A/B: {exc}")
    ctx.progress(1.0, "Agendado no YouTube" if privacy == "scheduled" else "Enviado ao YouTube")
    return {"video_id": video_id, "url": url, "privacy": privacy, "publish_at": status.get("publishAt"), "warnings": warnings}
