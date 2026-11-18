"""Edições da mídia real no documento (funções puras, chamadas só pelas rotas de mídia).

Ficam fora de `ops.py` de propósito: o agente de edição por IA só conhece as operações de lá (`EDIT_OPS`) e não tem
como baixar arquivos. Anexar mídia só põe `visual.media`: prompt, `reuse_of`, imagem fixada e semente ficam de
reserva, e tirar a mídia devolve a cena ao que era. A exceção é a edição automática (ADR 0004): os subplanos e eventos
que o planejador escolheu para a mídia anterior saem (`core.editing.stale`); os do usuário e os travados ficam.
"""

from __future__ import annotations

from typing import Any

from studio.core.editing.stale import drop_stale_edit
from studio.core.project.document import REAL_KINDS, MediaRef, ProjectDoc, RealHint, Scene
from studio.core.project.ops import OpError
from studio.core.styles.visual import FOCUS_POINTS, pick
from studio.media.link_preview import clean_headline, iso_date

CREDIT_MAX = 120
OUTLET_MAX = 60
_FITS = {"cover": "cover", "contain": "contain", "auto": None}


def _scene(doc: ProjectDoc, scene_id: str) -> Scene:
    try:
        scene = doc.scene(scene_id)
    except KeyError as exc:
        raise OpError(f"cena não encontrada: {scene_id}") from exc
    if scene.locked:
        raise OpError(f"a cena {scene_id} está travada")
    return scene


def scene_label(doc: ProjectDoc, scene_id: str) -> str:
    """"Cena 3": a posição da cena no vídeo (base 1)."""
    for index, scene in enumerate(doc.ordered_scenes(), 1):
        if scene.id == scene_id:
            return f"Cena {index}"
    return "Cena"


def _done(doc: ProjectDoc, before: ProjectDoc | None = None) -> ProjectDoc:
    if before is not None:
        drop_stale_edit(before, doc)  # a mídia mostrada mudou: os subplanos e eventos automáticos da cena ficam velhos
    return ProjectDoc.model_validate(doc.model_dump())


def attach_media(doc: ProjectDoc, scene_id: str, media: MediaRef) -> ProjectDoc:
    """Cópia do documento com a mídia na cena (nada mais muda)."""
    new_doc = doc.model_copy(deep=True)
    _scene(new_doc, scene_id).visual.media = MediaRef.model_validate(media.model_dump())
    return _done(new_doc, doc)


def _spaces(value: object, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit].strip()


def update_media(doc: ProjectDoc, scene_id: str, patch: dict[str, Any]) -> ProjectDoc:
    """Cópia do documento com os campos do `patch` mudados na mídia da cena (só os que vieram)."""
    new_doc = doc.model_copy(deep=True)
    scene = _scene(new_doc, scene_id)
    if scene.visual.media is None:
        raise OpError("a cena não tem mídia real")
    data = scene.visual.media.model_dump()
    if patch.get("kind") is not None:
        if patch["kind"] not in REAL_KINDS:
            raise OpError(f"tipo de mídia real inválido: {patch['kind']}")
        data["kind"] = patch["kind"]
    if "credit" in patch:
        data["credit"] = _spaces(patch["credit"], CREDIT_MAX)
    if patch.get("editorial") is not None:
        data["editorial"] = bool(patch["editorial"])
    if "focus" in patch:
        data["focus"] = pick(patch["focus"], FOCUS_POINTS)
    if "fit" in patch:
        fit = patch["fit"] if patch["fit"] is not None else "auto"
        if fit not in _FITS:
            raise OpError(f"enquadramento inválido: {fit}")
        data["fit"] = _FITS[fit]
    if "outlet" in patch:
        data["outlet"] = _spaces(patch["outlet"], OUTLET_MAX)
    if "published" in patch:
        text = _spaces(patch["published"], 40)
        published = iso_date(text) if text else ""
        if text and not published:
            raise OpError("data inválida (use AAAA-MM-DD)")
        data["published"] = published
    if "headline" in patch:
        lang = patch.get("lang") or new_doc.primary_language
        headline = clean_headline(str(patch["headline"] or ""), max_words=14)
        texts = dict(data.get("headline") or {})
        if headline:
            texts[lang] = headline
        else:
            texts.pop(lang, None)
        data["headline"] = texts
    scene.visual.media = MediaRef.model_validate(data)
    return _done(new_doc, doc)


def remove_media(doc: ProjectDoc, scene_id: str) -> ProjectDoc:
    """Cópia do documento sem a mídia na cena (o arquivo fica guardado; a cena volta à imagem de reserva)."""
    new_doc = doc.model_copy(deep=True)
    scene = _scene(new_doc, scene_id)
    if scene.visual.media is None:
        raise OpError("a cena não tem mídia real")
    scene.visual.media = None
    return _done(new_doc, doc)


def set_real(doc: ProjectDoc, scene_id: str, real: RealHint | None) -> ProjectDoc:
    """Cópia do documento com a sugestão de mídia real trocada (None dispensa)."""
    new_doc = doc.model_copy(deep=True)
    _scene(new_doc, scene_id).real = RealHint.model_validate(real.model_dump()) if real is not None else None
    return _done(new_doc)
