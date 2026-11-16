"""Personagens fixos (StyleBible do canal e do projeto): descrição visual e imagens âncora.

O canal guarda os personagens em `style_bible["characters"]`; o projeto, em `ProjectDoc.characters`. O projeto soma
aos do canal e o mesmo id troca o do canal. Na direção de arte cada personagem vira uma `Entity` com `character=True`:
o prompt das cenas onde ele aparece leva a descrição (nunca o nome) e o gerador que aceita referência recebe as
imagens âncora junto.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from studio.core.project.document import ArtDirection, Character, Entity, ProjectDoc, Visual

log = logging.getLogger(__name__)

BIBLE_KEY = "characters"
MAX_CHARACTERS = 12
MAX_REFERENCES = 4
"""Imagens âncora enviadas por cena, somando os personagens (mais que isso confunde o gerador e gasta tempo)."""


def from_bible(bible: dict[str, Any] | None) -> list[Character]:
    """Personagens do `style_bible` do canal (os inválidos ficam de fora, com aviso no log)."""
    out: list[Character] = []
    for raw in (bible or {}).get(BIBLE_KEY) or []:
        try:
            character = Character.model_validate(raw)
        except ValidationError as exc:
            log.warning("personagem inválido no canal ignorado: %s", exc)
            continue
        if all(c.id != character.id for c in out):
            out.append(character)
    return out[:MAX_CHARACTERS]


def check_list(characters: list[Character]) -> list[Character]:
    """Confere a lista vinda da interface: ids únicos e limite de personagens."""
    ids = [c.id for c in characters]
    if len(set(ids)) != len(ids):
        raise ValueError("dois personagens com o mesmo id")
    if len(characters) > MAX_CHARACTERS:
        raise ValueError(f"no máximo {MAX_CHARACTERS} personagens")
    return characters


def channel_bible(channel_id: str | None) -> dict[str, Any]:
    """O `style_bible` do canal (vazio sem canal)."""
    if not channel_id:
        return {}
    from studio.db import session_scope
    from studio.models import Channel

    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return dict(row.style_bible or {}) if row is not None else {}


def merged(project: list[Character], channel: list[Character]) -> list[Character]:
    """Os do projeto primeiro; os do canal que o projeto não trocou (mesmo id) depois."""
    own = {c.id for c in project}
    return [*project, *(c for c in channel if c.id not in own)][:MAX_CHARACTERS]


def for_doc(doc: ProjectDoc, bible: dict[str, Any] | None = None) -> list[Character]:
    """Personagens que valem no projeto. Sem `bible`, lê o canal do projeto no banco."""
    if bible is None:
        bible = channel_bible(doc.channel_id)
    return merged(list(doc.characters), from_bible(bible))


def as_entity(character: Character) -> Entity:
    return Entity(id=character.id, name=character.name, look=character.look.strip(), real=False, character=True)


def with_characters(art: ArtDirection, characters: list[Character]) -> ArtDirection:
    """Cópia da direção com cada personagem como entidade: a descrição do cadastro vale sobre a da IA (editar o
    personagem muda os prompts sem refazer as cenas) e personagem que a direção não tinha entra no fim."""
    if not characters:
        return art
    art = art.model_copy(deep=True)
    by_id = {c.id: c for c in characters}
    entities: list[Entity] = []
    for entity in art.entities:
        character = by_id.pop(entity.id, None)
        if character is None:
            entities.append(entity)
            continue
        entities.append(Entity(id=entity.id, name=character.name or entity.name,
                               look=character.look.strip() or entity.look, real=False, character=True))
    entities += [as_entity(c) for c in characters if c.id in by_id]
    art.entities = entities
    return art


def reference_images(visual: Visual, characters: list[Character], data_dir: Path,
                     limit: int = MAX_REFERENCES) -> list[Path]:
    """Imagens âncora dos personagens que a cena mostra (as que sumiram do disco ficam de fora). Com dois ou mais
    personagens, vai a 1ª imagem de cada um antes da 2ª de qualquer um: todos chegam ao gerador."""
    by_id = {c.id: c for c in characters}
    root = data_dir.resolve()
    per_character: list[list[Path]] = []
    for entity_id in dict.fromkeys(visual.entities):
        character = by_id.get(entity_id)
        if character is None:
            continue
        files = [p for p in ((root / rel).resolve() for rel in character.images)
                 if p.is_relative_to(root) and p.is_file()]
        if files:
            per_character.append(files)
    out: list[Path] = []
    for round_ in range(max((len(f) for f in per_character), default=0)):
        out += [files[round_] for files in per_character if round_ < len(files) and files[round_] not in out]
    return out[:limit]
