"""Créditos da mídia real para a descrição da publicação ("Créditos das imagens").

Entra cada mídia que aparece no vídeo (a da cena ou a reaproveitada), na ordem da primeira aparição e sem repetir.
CC0 e domínio público também entram: a fonte é mostrada assim mesmo. Sem mídia real, nada muda na descrição.
"""

from __future__ import annotations

from typing import Any

from studio.core.project.document import MediaRef, ProjectDoc
from studio.core.timeline import news_date

CREDITS_HEADING = {"pt": "Créditos das imagens", "en": "Image credits", "es": "Créditos de las imágenes"}


def heading(lang: str) -> str:
    return CREDITS_HEADING.get(lang.lower()[:2], CREDITS_HEADING["en"])


def shown_media(doc: ProjectDoc) -> list[MediaRef]:
    """Mídias mostradas no vídeo, na ordem da primeira aparição, sem repetir."""
    seen: set[str] = set()
    out: list[MediaRef] = []
    for scene in doc.ordered_scenes():
        media = doc.shown_media(scene)
        if media is None:
            continue
        key = media.candidate_id or media.asset or media.page_url or f"#{scene.id}"
        if key in seen:
            continue
        seen.add(key)
        out.append(media)
    return out


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def credit_line(media: MediaRef, lang: str, fallback: str | None = None) -> str:
    """Uma linha (partes unidas por " — "); vazio quando não há o que creditar (arquivo enviado sem dados)."""
    if media.source == "upload" and not (media.credit.strip() or media.license.strip() or media.page_url.strip()):
        return ""
    if media.kind == "news":
        lead = ", ".join(p for p in (_clean(media.outlet), news_date(media.published, lang)) if p)
        headline = _clean(media.headline_for(lang, fallback)).strip(' "“”«»')
        parts = [lead or _clean(media.credit), f"“{headline}”" if headline else "", media.page_url or media.url]
    else:
        title = _clean(media.title) or _clean(media.credit)
        author = _clean(media.author)
        if author and author.lower() in title.lower():
            author = ""
        license_name = _clean(media.license)
        license_text = f"{license_name} ({media.license_url})" if license_name and media.license_url else license_name
        parts = [title, author, license_text, media.page_url or media.url]
    return " — ".join(p.strip() for p in parts if p and p.strip())


def credit_lines(doc: ProjectDoc, lang: str) -> list[str]:
    lines = (credit_line(media, lang, doc.primary_language) for media in shown_media(doc))
    return [line for line in lines if line]


def credits_block(doc: ProjectDoc, lang: str) -> str:
    """"Créditos das imagens:\\n- ...\\n- ..." no idioma, ou "" sem mídia real."""
    lines = credit_lines(doc, lang)
    if not lines:
        return ""
    return f"{heading(lang)}:\n" + "\n".join(f"- {line}" for line in lines)


def has_editorial(doc: ProjectDoc) -> bool:
    """Alguma mídia mostrada é material de terceiros com direitos (aviso de Content ID)."""
    return any(media.editorial for media in shown_media(doc))


def media_credits(doc: ProjectDoc, lang: str) -> dict[str, Any]:
    """Formato de `MediaCredits` (api.ts)."""
    lines = credit_lines(doc, lang)
    return {"lang": lang, "heading": heading(lang), "lines": lines, "text": credits_block(doc, lang),
            "editorial": has_editorial(doc)}
