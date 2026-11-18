"""Edição automática que ficou velha (ADR 0004 §3.6): subplanos e eventos do planejador presos a uma fala ou a uma
mídia que mudou.

Os gatilhos (`at`) apontam palavras da fala da cena, e os subplanos foram escolhidos para a mídia que ela mostrava.
Quando a fala da cena muda (texto editado, cena dividida ou juntada), ou a mídia mostrada muda (anexo, troca, remoção),
o que o planejador pôs ali não vale mais: a timeline poria tudo no início da cena. `drop_stale_edit` tira da cena o que
é do planejador e não está travado, e a marca da batida que ele inferiu (`mark.origin="planner"`). O que é do usuário
ou travado fica. O "Reeditar" (ou o próximo storyboard) planeja de novo.

Funções puras sobre o documento: as operações (`ops.apply_ops`), as edições de mídia (`media_edit`) e o PUT do
documento chamam com o antes e o depois.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from studio.core.text import span_ranges, split_word_indices, words

if TYPE_CHECKING:
    from studio.core.project.document import ProjectDoc, Scene


def scene_speech(doc: ProjectDoc, scene: Scene) -> tuple[str, ...]:
    """As palavras da narração que a cena cobre (idioma principal), com a mesma divisão da timeline: pelos spans do
    storyboard quando todas as cenas do segmento têm, senão a divisão equilibrada."""
    try:
        text = doc.segment(scene.segment_id).text_for(doc.primary_language)
    except KeyError:
        return ()
    tokens = words(text)
    siblings = doc.scenes_of(scene.segment_id)
    index = next((i for i, s in enumerate(siblings) if s.id == scene.id), None)
    if index is None:
        return tuple(tokens)
    if all(s.span is not None for s in siblings):
        ranges = span_ranges([int(s.span or 0) for s in siblings], len(tokens))
    else:
        ranges = split_word_indices(len(tokens), len(siblings), tokens)
    start, end = ranges[index] if index < len(ranges) else (0, len(tokens))
    return tuple(tokens[start:end])


def _picture(doc: ProjectDoc, scene: Scene) -> tuple[object, ...]:
    """O que a cena mostra: tipo da cena e do visual, a imagem (própria ou reaproveitada) e a mídia real."""
    media = doc.shown_media(scene)
    root = doc.reuse_root(scene)
    image = scene.visual.asset or (root.visual.asset if root is not None else None)
    media_key = (media.kind, media.asset, media.video, media.pool_id, media.start_s, media.dur_s) if media else None
    return scene.kind, scene.visual.type, scene.visual.reuse_of, image, media_key


def _has_planned(scene: Scene) -> bool:
    return (any(not (s.locked or s.origin == "user") for s in scene.shots)
            or any(not (e.locked or e.origin == "user") for e in scene.fx)
            or (scene.mark is not None and scene.mark.origin == "planner"))


def drop_stale_edit(before: ProjectDoc, after: ProjectDoc) -> list[str]:
    """Em `after` (no lugar): tira os subplanos e eventos do planejador (não travados) e a marca inferida das cenas
    cuja fala ou mídia mudou desde `before`. Cena travada não muda. Devolve os ids das cenas limpas."""
    old = {s.id: s for s in before.scenes}
    cleaned: list[str] = []
    for scene in after.scenes:
        if scene.locked or not _has_planned(scene):
            continue
        prev = old.get(scene.id)
        if prev is not None and scene_speech(before, prev) == scene_speech(after, scene) \
                and _picture(before, prev) == _picture(after, scene):
            continue
        scene.shots = [s for s in scene.shots if s.locked or s.origin == "user"]
        scene.fx = [e for e in scene.fx if e.locked or e.origin == "user"]
        if scene.mark is not None and scene.mark.origin == "planner":
            scene.mark = None
        cleaned.append(scene.id)
    return cleaned
