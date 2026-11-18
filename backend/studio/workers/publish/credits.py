"""Créditos da mídia real para a descrição da publicação ("Créditos das imagens").

Entra cada mídia que aparece no vídeo, na ordem da primeira aparição e sem repetir:
- a da cena (ou a reaproveitada de outra cena);
- a dos subplanos da edição (ADR 0004): item do acervo do projeto (`source="pool"`, com o crédito, a licença e o
  `editorial` do `pool.json`) e mídia própria do subplano (`source="media"`);
- no fim, os memes da biblioteca do usuário que aparecem como evento (`Scene.fx`) com licença que pede crédito
  (`cc_by`: autor e fonte) ou que é de terceiros (`fair_comment`: crédito e aviso de Content ID).
CC0 e domínio público também entram: a fonte é mostrada assim mesmo. Sem mídia real, nada muda na descrição.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from studio.core.edit_resolve import shown_materials
from studio.core.project.document import MediaRef, ProjectDoc
from studio.core.project.pool import PoolItem
from studio.core.timeline import edit_pool, news_date

if TYPE_CHECKING:
    from studio.core.project.pool import Pool

log = logging.getLogger(__name__)

# Licenças de meme da biblioteca que entram nos créditos: CC BY exige autor e fonte; a citação (fair_comment) é
# material de terceiros (crédito e aviso de Content ID). Próprio e CC0 ficam de fora.
MEME_CREDIT_LICENSES = {"cc_by": "CC BY", "fair_comment": "citação/comentário"}

CREDITS_HEADING = {"pt": "Créditos das imagens", "en": "Image credits", "es": "Créditos de las imágenes"}


def heading(lang: str) -> str:
    return CREDITS_HEADING.get(lang.lower()[:2], CREDITS_HEADING["en"])


def _meme_refs() -> dict[str, MediaRef]:
    """Os memes da biblioteca com licença que pede crédito, por id, como `MediaRef` de crédito."""
    try:
        from studio.media import meme_library

        items = meme_library.user_items()
    except Exception as exc:  # noqa: BLE001 - biblioteca ilegível: os créditos saem sem os memes
        log.warning("biblioteca de memes ilegível nos créditos: %s", exc)
        return {}
    out: dict[str, MediaRef] = {}
    for item in items:
        license_name = MEME_CREDIT_LICENSES.get(item.license)
        if license_name is None:
            continue
        out[item.id] = MediaRef(kind="meme", source="library", title=item.note or item.id, author=item.author,
                                license=license_name, page_url=item.source_url,
                                editorial=item.license == "fair_comment")
    return out


def shown_media(doc: ProjectDoc, pool: Pool | None = None) -> list[MediaRef | PoolItem]:
    """Mídias mostradas no vídeo, na ordem da primeira aparição, sem repetir: as das cenas e as dos subplanos
    (`edit_resolve.shown_materials`, inclusive o item do acervo) e, depois, os memes da biblioteca que pedem crédito.
    `pool`: o acervo do projeto (None: o `pool.json`, só quando algum subplano cita o acervo)."""
    if pool is None:
        pool = edit_pool(doc)
    seen: set[str] = set()
    out: list[MediaRef | PoolItem] = []

    def add(material: MediaRef | PoolItem, *keys: str) -> None:
        keys = tuple(k for k in keys if k)
        if any(k in seen for k in keys):
            return
        seen.update(keys)
        out.append(material)

    for k, material in enumerate(shown_materials(doc, pool)):
        if isinstance(material, PoolItem):
            add(material, f"pool:{material.id}")
        else:
            add(material, material.candidate_id or material.asset or material.video or material.page_url or f"#{k}",
                f"pool:{material.pool_id}" if material.pool_id else "")
    memes: dict[str, MediaRef] | None = None
    for scene in doc.ordered_scenes():
        for event in scene.fx:
            lib_id = event.params.get("lib_id")
            if event.type != "meme" or event.off or not isinstance(lib_id, str) or not lib_id:
                continue
            if memes is None:
                memes = _meme_refs()
            ref = memes.get(lib_id)
            if ref is not None:
                add(ref, f"meme:{lib_id}")
    return out


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def pool_credit_line(item: PoolItem) -> str:
    """A linha do item do acervo: crédito, licença e página de origem (vazio sem nenhum)."""
    parts = [_clean(item.credit), _clean(item.license), item.page_url or item.url]
    return " — ".join(p.strip() for p in parts if p and p.strip())


def credit_line(media: MediaRef | PoolItem, lang: str, fallback: str | None = None) -> str:
    """Uma linha (partes unidas por " — "); vazio quando não há o que creditar (arquivo enviado sem dados)."""
    if isinstance(media, PoolItem):
        return pool_credit_line(media)
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


def credit_lines(doc: ProjectDoc, lang: str, pool: Pool | None = None) -> list[str]:
    lines = (credit_line(media, lang, doc.primary_language) for media in shown_media(doc, pool))
    return [line for line in lines if line]


def credits_block(doc: ProjectDoc, lang: str, pool: Pool | None = None) -> str:
    """"Créditos das imagens:\\n- ...\\n- ..." no idioma, ou "" sem mídia real."""
    lines = credit_lines(doc, lang, pool)
    if not lines:
        return ""
    return f"{heading(lang)}:\n" + "\n".join(f"- {line}" for line in lines)


def has_editorial(doc: ProjectDoc, pool: Pool | None = None) -> bool:
    """Alguma mídia mostrada (cena, subplano ou meme) é material de terceiros com direitos (aviso de Content ID)."""
    return any(media.editorial for media in shown_media(doc, pool))


def media_credits(doc: ProjectDoc, lang: str) -> dict[str, Any]:
    """Formato de `MediaCredits` (api.ts)."""
    shown = shown_media(doc)
    lines = [line for line in (credit_line(m, lang, doc.primary_language) for m in shown) if line]
    text = f"{heading(lang)}:\n" + "\n".join(f"- {line}" for line in lines) if lines else ""
    return {"lang": lang, "heading": heading(lang), "lines": lines, "text": text,
            "editorial": any(m.editorial for m in shown)}
