"""Pacote de publicação (assistente manual, custo zero): tudo pronto para postar na página oficial de cada rede.

Não automatiza o navegador (os termos de uso das redes proíbem; README §10.2.1). O app prepara os arquivos,
a legenda adaptada por rede e um checklist; você arrasta o vídeo e agenda.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.ids import new_id
from studio.media.real_media import EDITORIAL_WARNING
from studio.models import Publication
from studio.workers.publish.credits import credits_block, has_editorial

UPLOAD_URLS = {
    "youtube": "https://studio.youtube.com/",
    "tiktok": "https://www.tiktok.com/tiktokstudio/upload",
    "instagram": "https://business.facebook.com/latest/reels_composer",
    "facebook": "https://business.facebook.com/latest/reels_composer",
    "threads": "https://www.threads.com/",
    "x": "https://x.com/compose/post",
}

LIMITS = {
    "youtube": {"title": 100, "description": 5000, "hashtags": 15},
    "tiktok": {"description": 2200, "hashtags": 5},
    "instagram": {"description": 2200, "hashtags": 5},
    "facebook": {"description": 2200, "hashtags": 5},
    "threads": {"description": 500, "hashtags": 1},
    "x": {"description": 280, "hashtags": 2},
}

AI_LABEL_HOWTO = {
    "youtube": "No Studio: Detalhes > 'Conteúdo alterado ou sintético' = Sim (obrigatório se as imagens forem realistas).",
    "tiktok": "Em 'Mais opções', ative 'Conteúdo gerado por IA'.",
    "instagram": "Marque 'Rótulo de IA' (só existe no app do celular; no computador finalize pelo celular).",
    "facebook": "Ative o rótulo 'Informações de IA' ao publicar.",
    "threads": "Marque 'Feito com IA' ao publicar.",
    "x": "Mencione na legenda que o conteúdo usa IA.",
}


def caption_for(platform: str, meta: dict[str, Any], fallback_title: str, credits: str = "") -> str:
    """Legenda da rede. Com os créditos das imagens (mídia real): no YouTube vão antes das hashtags (só o texto é
    cortado para caber); no TikTok, Instagram e Facebook entram só se tudo couber; no Threads e no X, nunca."""
    limits = LIMITS.get(platform, {"description": 2200, "hashtags": 5})
    title = meta.get("title") or fallback_title
    hashtags = [h if h.startswith("#") else f"#{h}" for h in meta.get("hashtags", [])][: limits["hashtags"]]
    description = meta.get("description", "")
    if platform == "youtube":
        body = description
    elif platform in ("tiktok", "instagram", "facebook"):
        first = description.split("\n\n")[0] if description else ""
        body = f"{title}\n\n{first}".strip()
    else:
        body = title
    text = f"{body}\n\n{' '.join(hashtags)}".strip()
    credits = credits.strip()
    limit = limits["description"]
    if not credits or platform not in ("youtube", "tiktok", "instagram", "facebook"):
        return text[:limit]
    tail = credits + (f"\n\n{' '.join(hashtags)}" if hashtags else "")
    if platform == "youtube":
        room = limit - len(tail) - 2
        cut = body.strip()[: max(0, room)].rstrip()
        return (f"{cut}\n\n{tail}" if cut else tail)[:limit]
    full = f"{body}\n\n{tail}".strip()
    return full if len(full) <= limit else text[:limit]


def build_package(project_id: str, lang: str, platforms: list[str], planned_at: datetime | None = None) -> dict[str, Any]:
    """Pacote para postar à mão em cada rede; `planned_at` entra na agenda como horário planejado."""
    settings = get_settings()
    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    last = row["settings"].get("last_final") or {}
    entry = (last.get("langs") or {}).get(lang)
    if not entry or not entry.get("video"):
        raise ValueError("renderize o vídeo final deste idioma antes de gerar o pacote")
    preview_only = last.get("preview_only_providers") or []
    video = settings.data_dir / entry["video"]
    thumb = settings.data_dir / entry["thumbnail"] if entry.get("thumbnail") else None
    srt = settings.data_dir / entry["srt"] if entry.get("srt") else None
    meta = doc.metadata.get(lang)
    meta_dict = meta.model_dump() if meta else {"title": doc.title, "description": "", "tags": [], "hashtags": []}
    base = settings.outputs_dir / project_id / lang / "publicar"
    credits = credits_block(doc, lang)  # mídia real: "Créditos das imagens" (vazio sem mídia)
    editorial = has_editorial(doc)
    results = []
    for platform in platforms:
        folder = base / platform
        folder.mkdir(parents=True, exist_ok=True)
        target_video = folder / video.name
        if not target_video.exists():
            try:
                target_video.hardlink_to(video)
            except OSError:
                shutil.copyfile(video, target_video)
        if thumb and thumb.exists():
            shutil.copyfile(thumb, folder / "capa.jpg")
        if srt and srt.exists() and platform == "youtube":
            shutil.copyfile(srt, folder / "legendas.srt")
        caption = caption_for(platform, meta_dict, doc.title, credits)
        (folder / "legenda.txt").write_text(caption, encoding="utf-8")
        if credits:
            (folder / "creditos.txt").write_text(credits + "\n", encoding="utf-8")
        if platform == "youtube":
            (folder / "titulo.txt").write_text((meta_dict.get("title") or doc.title)[:100], encoding="utf-8")
            (folder / "tags.txt").write_text(", ".join(meta_dict.get("tags", [])), encoding="utf-8")
        checklist = [
            f"# Publicar em {platform}",
            "",
            f"1. Abra {UPLOAD_URLS.get(platform, '')}",
            f"2. Envie o arquivo `{target_video.name}` desta pasta.",
            "3. Cole o texto de `legenda.txt`" + (" e o título de `titulo.txt`" if platform == "youtube" else "") + ".",
            "4. Use `capa.jpg` como capa/thumbnail." if thumb else "4. Escolha um quadro forte como capa.",
            f"5. Rótulo de IA: {AI_LABEL_HOWTO.get(platform, '')}",
            "6. Agende o horário (o agendamento nativo publica mesmo com o notebook desligado).",
        ]
        if platform == "youtube" and srt:
            checklist.append("7. Em Legendas, envie `legendas.srt`.")
        if credits:
            checklist.append("\nCréditos das imagens: já estão na legenda." if credits in caption
                             else "\nCréditos das imagens: cole o texto de creditos.txt na descrição.")
        if editorial:
            checklist.append("\n**Atenção:** " + EDITORIAL_WARNING)
        if style.art.photoreal:
            checklist.append("\n**Atenção:** o estilo usa arte fotorrealista: o rótulo de conteúdo sintético é obrigatório.")
        if preview_only:
            checklist.append(
                "\n**BLOQUEADO PARA CANAL MONETIZADO:** este vídeo usou provedores só de prévia "
                f"({', '.join(preview_only)}). Configure voz/imagens comerciais e renderize de novo."
            )
        (folder / "CHECKLIST.md").write_text("\n".join(checklist) + "\n", encoding="utf-8")
        pub_id = new_id("pub")
        with session_scope() as s:
            s.add(Publication(id=pub_id, project_id=project_id, platform=platform, language=lang, driver="manual_helper",
                              status="package_ready", package_path=str(folder), scheduled_at=planned_at,
                              details={"caption": caption, "url": UPLOAD_URLS.get(platform)}))
        results.append({"id": pub_id, "platform": platform, "folder": str(folder), "upload_url": UPLOAD_URLS.get(platform),
                        "caption": caption, "blocked": bool(preview_only)})
    (base / "pacote.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"lang": lang, "items": results, "preview_only_providers": preview_only}


def list_publications(project_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.query(Publication).filter_by(project_id=project_id).order_by(Publication.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "platform": r.platform,
                "language": r.language,
                "driver": r.driver,
                "status": r.status,
                "package_path": r.package_path,
                "url": r.url,
                "details": r.details,
                "error": r.error,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def build_dub_package(project_id: str) -> dict[str, Any]:
    """Refaz o pacote de faixas de idioma (Modo B) a partir do último vídeo final: pega os títulos atuais e, se o
    vídeo já foi enviado pelo app, o link direto dele no Studio."""
    from studio.workers.pipeline.authoring import lang_name
    from studio.workers.pipeline.dubbing import MULTI_TRACK, write_package

    settings = get_settings()
    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    last = row["settings"].get("last_final") or {}
    if last.get("delivery") != MULTI_TRACK:
        raise ValueError("renderize o vídeo final com as faixas de idioma no mesmo vídeo antes de gerar o pacote")
    entries = {lang: e for lang, e in (last.get("langs") or {}).items() if e.get("mix") or e.get("srt")}
    with session_scope() as s:
        pub = (s.query(Publication).filter_by(project_id=project_id, platform="youtube", language=doc.primary_language)
               .filter(Publication.external_id.isnot(None)).order_by(Publication.created_at.desc()).first())
        video_id = pub.external_id if pub else None
    folder = settings.outputs_dir / project_id / "faixas-youtube"
    manifest = write_package(folder, doc=doc, entries=entries, data_dir=settings.data_dir, summary=last.get("dub"),
                             video_id=video_id, warnings=last.get("warnings") or [], label=lang_name)
    rel = folder.resolve().relative_to(settings.data_dir.resolve()).as_posix()
    return {"folder": rel, "path": str(folder), "checklist": f"{rel}/CHECKLIST.md", **manifest}
