"""Personagens fixos no storyboard: o pedido ao LLM, quais cenas mostram cada personagem e o nome fora do prompt.

O nome do personagem serve só para achá-lo na narração e no storyboard. No prompt da imagem ele vira a descrição
visual do cadastro (o nome puxa o personagem oficial ou faz o gerador recusar; ver `storyboard_check.fix_entity_names`).
"""

from __future__ import annotations

from studio.core.project import characters as characters_mod
from studio.core.project.document import ArtDirection, Character, ProjectDoc
from studio.core.text import normalize_token
from studio.workers.pipeline.storyboard_check import (
    CORRIGIDO,
    Beat,
    Issue,
    _fold,
    _name_re,
    _replace_name,
    entity_reference,
    has_own_image,
    scene_for,
)


def prompt_lines(characters: list[Character]) -> list[str]:
    """Bloco do pedido do storyboard com os personagens fixos (vazio sem personagem)."""
    if not characters:
        return []
    lines = ["", "Personagens fixos do canal/projeto (já cadastrados: use o id exato em direction.entities e nas cenas "
                 "onde o personagem aparece; o look já está definido, copie como está; o nome é só para achar o "
                 "personagem na narração e NUNCA entra no prompt, que cita pela aparência ou pelo papel):"]
    for c in characters:
        names = ", ".join(c.names()) or "(sem nome)"
        lines.append(f"- {c.id}: chamado na narração de {names}; look: {c.look or '(sem descrição)'}")
    return lines


def _key(text: str) -> str:
    return normalize_token(_fold(text or "")).replace("_", " ").strip()


def _cites(text: str, character: Character) -> str | None:
    """A forma do nome (nome ou apelido) que o texto cita, como palavra inteira, sem acento e sem caixa."""
    folded = _fold(text or "")
    for name in sorted(character.names(), key=len, reverse=True):
        if len(name) >= 2 and _name_re(name).search(folded):
            return name
    return None


def prepare(art: ArtDirection, characters: list[Character]) -> tuple[ArtDirection, dict[str, str]]:
    """Direção com os personagens como entidades e o mapa das entidades que a IA criou para um personagem
    cadastrado com outro id ("lucia" → "detetive"). A entidade repetida fica até `finish` trocar os ids nas cenas."""
    art = characters_mod.with_characters(art, characters)
    ids = {c.id for c in characters}
    remap: dict[str, str] = {}
    for entity in art.entities:
        if entity.id in ids:
            continue
        for c in characters:
            keys = {_key(n) for n in (*c.names(), c.id)}
            if _key(entity.id) in keys or (entity.name and _key(entity.name.split("(")[0]) in keys):
                remap[entity.id] = c.id
                break
    return art, remap


def finish(doc: ProjectDoc, beats: list[Beat], characters: list[Character], remap: dict[str, str]) -> list[Issue]:
    """Depois das cenas: troca as entidades repetidas pelo personagem, marca as cenas onde a batida ou o prompt citam
    o personagem e tira o nome dele dos prompts (fica a descrição)."""
    art = doc.art
    if art is None or not characters:
        return []
    if remap:
        art.entities = [e for e in art.entities if e.id not in remap]
        for scene in doc.scenes:
            if scene.locked:
                continue
            scene.visual.entities = list(dict.fromkeys(remap.get(i, i) for i in scene.visual.entities))
    references = {c.id: entity_reference(c.look, "") if c.look.strip() else "the character" for c in characters}
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or not has_own_image(scene) or not scene.visual.language:
            continue
        for c in characters:
            cited = _cites(beat.text, c) or _cites(scene.visual.prompt, c)
            if cited and c.id not in scene.visual.entities:
                scene.visual.entities.append(c.id)
                issues.append(Issue("personagem_na_cena", CORRIGIDO,
                                    f'A batida cita "{cited}": a cena usa a descrição e as imagens âncora do personagem.',
                                    beat.key, scene.id))
            name = _cites(scene.visual.prompt, c)
            if name:
                scene.visual.prompt = _scrub(scene.visual.prompt, c, references[c.id])
                issues.append(Issue("nome_no_prompt", CORRIGIDO,
                                    f'O prompt citava "{name}", nome do personagem: a imagem usa a descrição dele.',
                                    beat.key, scene.id))
    if art.source != "user":
        for c in characters:
            art.hook = _scrub(art.hook, c, references[c.id])
            art.world = _scrub(art.world, c, references[c.id])
            for entity in art.entities:
                if not entity.real and entity.id != c.id:
                    entity.look = _scrub(entity.look, c, references[c.id])
    return issues


def _scrub(text: str, character: Character, reference: str) -> str:
    for name in sorted(character.names(), key=len, reverse=True):
        if len(name) >= 2:
            text = _replace_name(text, name, reference)
    return text
