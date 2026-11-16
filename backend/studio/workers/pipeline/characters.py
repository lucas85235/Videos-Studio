"""Personagens fixos no storyboard: o pedido ao LLM, quais cenas mostram cada personagem e o nome fora do prompt.

O nome do personagem serve só para achá-lo na narração e no storyboard. No prompt da imagem ele vira a descrição
visual do cadastro (o nome puxa o personagem oficial ou faz o gerador recusar; ver `storyboard_check.fix_entity_names`).
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from studio.core.project import characters as characters_mod
from studio.core.project.document import ArtDirection, Character, ProjectDoc
from studio.core.text import normalize_token
from studio.workers.pipeline.storyboard_check import (
    _NAME_LINKS,
    _PERSON_NOUNS,
    AVISO,
    CORRIGIDO,
    Beat,
    Issue,
    _fold,
    _name_re,
    _replace_name,
    _split_entity_name,
    _spoken_as_name,
    entity_reference,
    has_own_image,
    scene_for,
)

# Palavras do prompt que mostram gente no quadro (além de `_PERSON_NOUNS`): a cena que só a narração liga ao
# personagem recebe o personagem só quando a imagem já mostra alguém.
_SHOWS_PERSON = _PERSON_NOUNS | {"people", "silhouette", "silhouetted", "hand", "hands", "face", "profile", "she", "he",
                                 "her", "his", "him"}


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


def _name_words(name: str) -> list[str]:
    """As palavras de nome próprio de um nome composto ("Lucia Caminos" → ["Lucia", "Caminos"]); vazio quando o nome
    não é todo em maiúscula ("o detetive") ou tem uma palavra só."""
    words = [w for w in name.split() if w.lower() not in _NAME_LINKS]
    if len(words) < 2 or not all(w[:1].isupper() for w in words):
        return []
    return [w for w in words if len(w) >= 3]


def forms(character: Character, text: str, others: Sequence[Character] = ()) -> list[str]:
    """Formas de achar o personagem: nome, apelidos e as partes do nome composto que o texto fala como nome
    ("Lucia" de "Lucia Caminos"), como `storyboard_check._aliases`. A parte que também é de outro personagem (o
    sobrenome da família) fica de fora. Da mais longa para a mais curta."""
    shared = {_key(w) for o in others if o.id != character.id for n in o.names() for w in (n, *_name_words(n))}
    out = list(character.names())
    for name in character.names():
        out += [w for w in _name_words(name) if _key(w) not in shared and _spoken_as_name(w, text)]
    return sorted((n for n in dict.fromkeys(out) if len(n) >= 2), key=len, reverse=True)


def _cites(text: str, names: list[str]) -> str | None:
    """A forma do nome que o texto cita, como palavra inteira, sem acento e sem caixa."""
    folded = _fold(text or "")
    return next((name for name in names if _name_re(name).search(folded)), None)


def _shows_person(prompt: str) -> bool:
    return any(w in _SHOWS_PERSON for w in re.findall(r"[a-z]+", (prompt or "").lower()))


def _matches(entity_id: str, entity_name: str, character: Character) -> bool:
    """A entidade da IA é o personagem: o id ou o nome (antes do papel) é um dos nomes dele, ou as palavras do nome
    da entidade estão todas no nome do personagem, em qualquer ordem ("Sam (detective)" e "Detetive Sam";
    "Lucia" e "Lucia Caminos")."""
    keys = {_key(n) for n in (*character.names(), character.id)}
    name = _split_entity_name(entity_name)[0] if entity_name else ""
    if _key(entity_id) in keys or (name and _key(name) in keys):
        return True
    words = {_key(w) for n in character.names() for w in n.split() if w.lower() not in _NAME_LINKS and len(w) >= 3}
    own = {_key(w) for w in name.split() if w.lower() not in _NAME_LINKS}
    return bool(own) and own <= words


def prepare(art: ArtDirection, characters: list[Character]) -> tuple[ArtDirection, dict[str, str]]:
    """Direção com os personagens como entidades e o mapa das entidades que a IA criou para um personagem
    cadastrado com outro id ("lucia" → "heroina"). A entidade repetida fica até `finish` trocar os ids nas cenas.
    A entidade que casa com dois personagens (o sobrenome da família) fica como está."""
    art = characters_mod.with_characters(art, characters)
    ids = {c.id for c in characters}
    remap: dict[str, str] = {}
    for entity in art.entities:
        if entity.id in ids or entity.real:
            continue
        found = [c.id for c in characters if _matches(entity.id, entity.name, c)]
        if len(found) == 1:
            remap[entity.id] = found[0]
    return art, remap


def finish(doc: ProjectDoc, beats: list[Beat], characters: list[Character], remap: dict[str, str]) -> list[Issue]:
    """Depois das cenas: troca as entidades repetidas pelo personagem, marca as cenas onde o prompt cita o
    personagem (ou a batida cita e a imagem já mostra alguém; senão fica um aviso) e tira o nome dele dos prompts
    (fica a descrição)."""
    art = doc.art
    if art is None or not characters:
        return []
    if remap:
        art.entities = [e for e in art.entities if e.id not in remap]
        for scene in doc.scenes:
            if scene.locked:
                continue
            scene.visual.entities = list(dict.fromkeys(remap.get(i, i) for i in scene.visual.entities))
    text = " ".join([doc.full_text(doc.primary_language), *(s.visual.prompt for s in doc.scenes)])
    names = {c.id: forms(c, text, characters) for c in characters}
    references = {c.id: reference_for(c.look) for c in characters}
    issues: list[Issue] = []
    for beat in beats:
        scene = scene_for(doc, beat)
        if scene is None or scene.locked or not has_own_image(scene) or not scene.visual.language:
            continue
        for c in characters:
            in_prompt = _cites(scene.visual.prompt, names[c.id])
            in_beat = _cites(beat.text, names[c.id])
            if c.id not in scene.visual.entities and (in_prompt or in_beat):
                if in_prompt or _shows_person(scene.visual.prompt):
                    scene.visual.entities.append(c.id)
                    issues.append(Issue("personagem_na_cena", CORRIGIDO,
                                        f'A cena cita "{in_prompt or in_beat}": usa a descrição e as imagens âncora '
                                        "do personagem.", beat.key, scene.id))
                else:
                    issues.append(Issue("personagem_na_cena", AVISO,
                                        f'A batida cita "{in_beat}", mas a imagem não mostra ninguém: se o personagem '
                                        "deve aparecer, marque-o na cena.", beat.key, scene.id))
            if in_prompt:
                scene.visual.prompt = _scrub(scene.visual.prompt, names[c.id], references[c.id])
                issues.append(Issue("nome_no_prompt", CORRIGIDO,
                                    f'O prompt citava "{in_prompt}", nome do personagem: a imagem usa a descrição dele.',
                                    beat.key, scene.id))
    if art.source != "user":
        for c in characters:
            art.hook = _scrub(art.hook, names[c.id], references[c.id])
            art.world = _scrub(art.world, names[c.id], references[c.id])
            for entity in art.entities:
                if not entity.real and entity.id != c.id:
                    entity.look = _scrub(entity.look, names[c.id], references[c.id])
    return issues


def reference_for(look: str) -> str:
    """Como o prompt cita o personagem no lugar do nome: o começo da descrição ("the tall woman")."""
    return entity_reference(look, "") if look.strip() else "the character"


def current_prompt(prompt: str, art: ArtDirection, characters: list[Character], entity_ids: list[str]) -> str:
    """O prompt da cena com a referência ao personagem em dia. O storyboard gravou o começo da descrição de então
    ("The tall woman walks..."); se a descrição do cadastro mudou depois, a referência velha vira a nova e o prompt
    não contradiz a descrição que vai junto. `art` é a direção gravada (com a descrição do storyboard)."""
    by_id = {c.id: c for c in characters}
    for entity_id in entity_ids:
        character, entity = by_id.get(entity_id), art.entity(entity_id)
        if character is None or entity is None or not entity.look.strip():
            continue
        old, new = reference_for(entity.look), reference_for(character.look)
        if old != new:
            # `_replace_name` engole o artigo antes: "The tall woman" vira "The old man" (maiúscula no começo).
            prompt = _replace_name(prompt, old[4:] if old.startswith("the ") else old, new)
    return prompt


def _scrub(text: str, names: list[str], reference: str) -> str:
    for name in names:
        text = _replace_name(text, name, reference)
    return text
