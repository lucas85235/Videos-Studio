"""Operações de edição (patch) sobre o documento de projeto.

São pequenas e tipadas: o agente de edição por IA só pode propor estas operações, e cada uma
é validada antes de virar um commit. O grafo de artefatos decide depois o que precisa ser
regenerado (README §6).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from studio.core.editing.stale import drop_stale_edit
from studio.core.hashing import seed_from
from studio.core.project.document import (
    CARD_KINDS,
    DATA_KINDS,
    Metadata,
    MusicCue,
    ProjectDoc,
    Scene,
    Segment,
    Visual,
    renumber_parts,
)
from studio.core.styles.library import StyleNotFound, get_style, set_style_override
from studio.core.styles.schema import CAMERA_MOVES, SCENE_KINDS, TRANSITIONS, StyleSpec
from studio.core.styles.visual import FOCUS_ROTATION, rotate_variant
from studio.core.text import span_ranges, split_word_indices, words


class OpError(ValueError):
    pass


class _Op(BaseModel):
    reason: str = ""
    """Justificativa curta (mostrada ao usuário na prévia do patch)."""


class UpdateSegmentText(_Op):
    op: Literal["update_segment_text"] = "update_segment_text"
    segment_id: str
    lang: str
    text: str


class AddSegment(_Op):
    op: Literal["add_segment"] = "add_segment"
    after_segment_id: str | None = None
    lang: str
    text: str
    role: str = "body"
    speaker: str = "narrator"
    prompt: str = ""


class DeleteSegment(_Op):
    op: Literal["delete_segment"] = "delete_segment"
    segment_id: str


class MoveSegment(_Op):
    op: Literal["move_segment"] = "move_segment"
    segment_id: str
    after_segment_id: str | None = None


class UpdateScenePrompt(_Op):
    op: Literal["update_scene_prompt"] = "update_scene_prompt"
    scene_id: str
    prompt: str


class RegenerateScene(_Op):
    op: Literal["regenerate_scene"] = "regenerate_scene"
    scene_id: str


class SetSceneMotion(_Op):
    op: Literal["set_scene_motion"] = "set_scene_motion"
    scene_id: str
    motion: str | None


class SetSceneTransition(_Op):
    op: Literal["set_scene_transition"] = "set_scene_transition"
    scene_id: str
    transition: str | None


class SetSceneKind(_Op):
    op: Literal["set_scene_kind"] = "set_scene_kind"
    scene_id: str
    kind: str
    number: int | None = None


class SetSceneText(_Op):
    op: Literal["set_scene_text"] = "set_scene_text"
    scene_id: str
    lang: str
    text: str


class SetSceneLook(_Op):
    op: Literal["set_scene_look"] = "set_scene_look"
    scene_id: str
    brightness: float | None = None
    contrast: float | None = None
    saturation: float | None = None
    warmth: float | None = None
    blur: float | None = None
    grade: str | None = None


class SetAiReason(_Op):
    """Por que a imagem da cena é de IA (hipótese, futuro, conceito; ADR 0004). Vazio tira o motivo."""

    op: Literal["set_ai_reason"] = "set_ai_reason"
    scene_id: str
    text: str = ""


class SplitScene(_Op):
    op: Literal["split_scene"] = "split_scene"
    scene_id: str
    prompt: str = ""


class MergeSceneWithNext(_Op):
    op: Literal["merge_scene_with_next"] = "merge_scene_with_next"
    scene_id: str


class SetStyle(_Op):
    op: Literal["set_style"] = "set_style"
    path: str
    """Caminho no StyleSpec, ex.: 'edit.captions.size' ou 'audio.music.volume_db'."""
    value: Any


class SetStyleId(_Op):
    op: Literal["set_style_id"] = "set_style_id"
    style_id: str
    """Troca o estilo do projeto (os ajustes ficam; a direção de arte é refeita no próximo storyboard)."""


class SetMusic(_Op):
    op: Literal["set_music"] = "set_music"
    cue_id: str | None = None
    start_segment: str | None = None
    mood: str | None = None
    volume_db: float | None = None
    enabled: bool | None = None
    track: str | None = None  # "" solta a faixa fixada
    loop: bool | None = None  # só a batida, em loop no compasso


class SetLock(_Op):
    op: Literal["set_lock"] = "set_lock"
    ids: list[str]
    locked: bool = True


class SetTitle(_Op):
    op: Literal["set_title"] = "set_title"
    title: str


class SetMetadata(_Op):
    op: Literal["set_metadata"] = "set_metadata"
    lang: str
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    hashtags: list[str] | None = None


class SetThumbnail(_Op):
    op: Literal["set_thumbnail"] = "set_thumbnail"
    lang: str | None = None
    text: str | None = None
    scene_id: str | None = None
    regenerate: bool = False


Operation = Annotated[
    UpdateSegmentText
    | AddSegment
    | DeleteSegment
    | MoveSegment
    | UpdateScenePrompt
    | RegenerateScene
    | SetSceneMotion
    | SetSceneTransition
    | SetSceneKind
    | SetSceneText
    | SetSceneLook
    | SetAiReason
    | SplitScene
    | MergeSceneWithNext
    | SetStyle
    | SetStyleId
    | SetMusic
    | SetLock
    | SetTitle
    | SetMetadata
    | SetThumbnail,
    Field(discriminator="op"),
]

OPERATIONS_ADAPTER = TypeAdapter(list[Operation])


def parse_ops(raw: list[dict[str, Any]]) -> list[Any]:
    return OPERATIONS_ADAPTER.validate_python(raw)


def ops_json_schema() -> dict[str, Any]:
    return OPERATIONS_ADAPTER.json_schema()


def _segment(doc: ProjectDoc, segment_id: str, *, editable: bool = True) -> Segment:
    try:
        seg = doc.segment(segment_id)
    except KeyError as exc:
        raise OpError(str(exc)) from exc
    if editable and seg.locked:
        raise OpError(f"o segmento {segment_id} está travado")
    return seg


def _scene(doc: ProjectDoc, scene_id: str, *, editable: bool = True) -> Scene:
    try:
        scene = doc.scene(scene_id)
    except KeyError as exc:
        raise OpError(str(exc)) from exc
    if editable and scene.locked:
        raise OpError(f"a cena {scene_id} está travada")
    return scene


def _insert_index(doc: ProjectDoc, after_segment_id: str | None) -> int:
    if after_segment_id is None:
        return 0
    for index, seg in enumerate(doc.segments):
        if seg.id == after_segment_id:
            return index + 1
    raise OpError(f"segmento não encontrado: {after_segment_id}")


def _next_focus(focus: str | None) -> str:
    """Próximo foco do rodízio (outro enquadramento da mesma imagem)."""
    index = FOCUS_ROTATION.index(focus) if focus in FOCUS_ROTATION else 0
    return FOCUS_ROTATION[(index + 1) % len(FOCUS_ROTATION)]


def _reuse_chain(doc: ProjectDoc, scene: Scene) -> list[str]:
    """Ids da cadeia de `reuse_of` a partir de `scene` (só chamar quando `doc.reuse_root(scene)` existe). Para na
    cena com mídia real, como `reuse_root`."""
    ids: list[str] = []
    current = scene
    while current.visual.reuse_of and current.visual.media is None:
        ids.append(current.visual.reuse_of)
        current = doc.scene(current.visual.reuse_of)
    return ids


def _reserve(doc: ProjectDoc, scene: Scene) -> tuple[list[str], Scene | None]:
    """Cadeia e raiz da reserva de uma cena com mídia real: o reaproveitamento que volta a valer quando a mídia
    sai (como `reuse_root` sem a parada na própria cena). Raiz None: referência solta ou ciclo."""
    ids: list[str] = []
    current = scene
    seen = {scene.id}
    while current.visual.reuse_of and (current is scene or current.visual.media is None):
        target = current.visual.reuse_of
        ids.append(target)
        nxt = next((s for s in doc.scenes if s.id == target), None)
        if nxt is None or nxt.id in seen:
            return ids, None
        seen.add(nxt.id)
        current = nxt
    return ids, current


def _remove_scenes(doc: ProjectDoc, removed: set[str]) -> None:
    """Tira cenas sem perder a imagem que outras reaproveitavam: a primeira cena (na ordem do vídeo) que mostrava
    a imagem de uma cena removida herda o Visual dela, que tem a mesma chave de cache (nenhuma imagem nova), e as
    outras passam a apontar para a herdeira. A herdeira de uma cena com mídia real herda a mídia também."""
    heirs: dict[str, str] = {}  # id da raiz removida → cena que herdou o Visual
    for scene in doc.ordered_scenes():
        if scene.id in removed or not scene.visual.reuse_of or scene.visual.media is not None:
            continue  # cena com mídia mostra a mídia: o `reuse_of` dela é só reserva (abaixo)
        root = doc.reuse_root(scene)
        if root is None or not removed.intersection(_reuse_chain(doc, scene)):
            continue  # referência solta continua sem imagem; cadeia sem cena removida não muda
        if root.id not in removed:
            scene.visual.reuse_of = root.id
        elif root.id in heirs:
            scene.visual.reuse_of = heirs[root.id]
        else:
            scene.visual = root.visual.model_copy(deep=True)
            heirs[root.id] = scene.id
    # Reserva das cenas com mídia: continua apontando para a mesma imagem de IA (ou fica com uma cópia dela).
    for scene in doc.ordered_scenes():
        if scene.id in removed or not scene.visual.reuse_of or scene.visual.media is None:
            continue
        chain, root = _reserve(doc, scene)
        if root is None or not removed.intersection(chain):
            continue
        if root.id not in removed:
            scene.visual.reuse_of = root.id
        elif root.id in heirs:
            scene.visual.reuse_of = heirs[root.id]
        else:
            scene.visual = root.visual.model_copy(deep=True, update={"media": scene.visual.media})
    doc.scenes = [s for s in doc.scenes if s.id not in removed]


def _own_visual(doc: ProjectDoc, scene: Scene) -> Visual:
    """Visual próprio para uma cena que reaproveitava outra (ganha imagem nova): copia o da raiz, inclusive a
    linguagem da cena dirigida, sem `reuse_of` nem imagem fixada. A semente é da própria cena: "Outra imagem"
    nela e depois na raiz não dá a mesma imagem nas duas. Raiz com mídia real: a cena pediu imagem de IA própria,
    então vale a imagem de reserva da raiz, sem a mídia."""
    root = doc.reuse_root(scene)
    if root is None or root.id == scene.id:
        return scene.visual.model_copy(update={"reuse_of": None})
    if root.visual.media is not None and root.visual.reuse_of:
        root = _reserve(doc, root)[1] or root
    return root.visual.model_copy(deep=True, update={"reuse_of": None, "asset": None, "media": None,
                                                     "seed": seed_from(root.visual.seed, scene.id)})


def _scene_text(doc: ProjectDoc, scene: Scene) -> str:
    """As palavras da narração que a cena cobre (idioma principal), pelos spans ou pela divisão antiga."""
    try:
        text = doc.segment(scene.segment_id).text_for(doc.primary_language)
    except KeyError:
        return ""
    tokens = words(text)
    siblings = doc.scenes_of(scene.segment_id)
    index = next((i for i, s in enumerate(siblings) if s.id == scene.id), 0)
    if siblings and all(s.span is not None for s in siblings):
        ranges = span_ranges([s.span or 0 for s in siblings], len(tokens))
    else:
        ranges = split_word_indices(len(tokens), len(siblings) or 1)
    start, end = ranges[index] if index < len(ranges) else (0, len(tokens))
    return " ".join(tokens[start:end]) or text


def _image_from_card(doc: ProjectDoc, scene: Scene) -> Visual:
    """Visual de imagem nova para um cartão sem fundo (`type="none"`) que virou imagem: o prompt começa pela fala
    da cena e, com direção de arte, a cena fica dirigida."""
    art = doc.art
    return Visual(prompt=scene.visual.prompt or _scene_text(doc, scene), seed=seed_from(doc.seed, scene.id),
                  language=art.languages[0] if art and art.languages else None)


def _split_visual(doc: ProjectDoc, scene: Scene, prompt: str) -> Visual:
    """Visual da segunda parte de uma cena dividida: reaproveita a imagem (custo zero) ou, com `prompt`, pede
    uma imagem nova (a cena dirigida continua dirigida, com outro plano)."""
    visual = scene.visual
    if prompt.strip():
        shot, composition = visual.shot, visual.composition
        if visual.language:
            shot, composition, _ = rotate_variant(shot, composition, visual.language, [], 1)
        return Visual(prompt=prompt.strip(), seed=visual.seed + 7919, language=visual.language, shot=shot,
                      composition=composition, entities=list(visual.entities))
    if not (visual.reuse_of or visual.asset or visual.prompt or visual.query or visual.media):
        return Visual(type=visual.type, seed=visual.seed + 7919)  # cena sem imagem (cartão): nada a reaproveitar
    root = doc.reuse_root(scene)
    return Visual(reuse_of=root.id if root else visual.reuse_of)


def _set_style_id(doc: ProjectDoc, style_id: str) -> None:
    style_id = style_id.strip()
    if style_id == doc.style_id:
        return
    try:
        get_style(style_id, doc.style_overrides)
    except StyleNotFound as exc:
        raise OpError(f"estilo não encontrado: {style_id}") from exc
    except Exception as exc:  # noqa: BLE001 - mensagem vai para o usuário
        raise OpError(f"os ajustes do projeto não valem no estilo {style_id}: {str(exc)[:400]}") from exc
    doc.style_id = style_id
    # A direção de arte foi decidida para o estilo antigo e as cenas precisam ser conferidas de novo.
    doc.art = None
    doc.storyboard_approved = False


def apply_op(doc: ProjectDoc, op: Any, base_style: StyleSpec | None = None) -> None:
    """Aplica uma operação no documento (in place). Lança OpError se for inválida."""
    match op:
        case UpdateSegmentText():
            seg = _segment(doc, op.segment_id)
            if not op.text.strip():
                raise OpError("o texto do segmento não pode ficar vazio")
            seg.text[op.lang] = op.text.strip()
        case AddSegment():
            index = _insert_index(doc, op.after_segment_id)
            seg = Segment(role=op.role, speaker=op.speaker, text={op.lang: op.text.strip()})
            doc.segments.insert(index, seg)
            doc.scenes.append(Scene(segment_id=seg.id, visual=Visual(prompt=op.prompt, seed=len(doc.scenes) + 1)))
        case DeleteSegment():
            seg = _segment(doc, op.segment_id)
            if any(s.locked for s in doc.scenes_of(seg.id)):
                raise OpError("o segmento tem cenas travadas")
            _remove_scenes(doc, {s.id for s in doc.scenes if s.segment_id == seg.id})
            index = next(i for i, s in enumerate(doc.segments) if s.id == seg.id)
            doc.segments = [s for s in doc.segments if s.id != seg.id]
            # faixa de música que trocava neste segmento passa a trocar no seguinte; sem seguinte, é desligada
            following = doc.segments[index].id if index < len(doc.segments) else None
            for cue in doc.music:
                if cue.start_segment == seg.id:
                    cue.start_segment = following
                    cue.enabled = cue.enabled and following is not None
        case MoveSegment():
            seg = _segment(doc, op.segment_id)
            doc.segments = [s for s in doc.segments if s.id != seg.id]
            doc.segments.insert(_insert_index(doc, op.after_segment_id), seg)
        case UpdateScenePrompt():
            scene = _scene(doc, op.scene_id)
            if scene.visual.reuse_of:
                scene.visual = _own_visual(doc, scene)  # prompt novo: a cena ganha imagem própria
            elif scene.visual.type == "none":
                scene.visual = _image_from_card(doc, scene)  # cartão sem fundo: o prompt pede imagem
            scene.visual.prompt = op.prompt.strip()
            scene.visual.asset = None
        case RegenerateScene():
            scene = _scene(doc, op.scene_id)
            if scene.visual.media is not None:
                raise OpError("a cena mostra mídia real: tire a mídia para pedir outra imagem de IA")
            if scene.visual.reuse_of:
                # "Outra imagem" numa cena que reaproveita: uma variação da imagem da raiz, só nesta cena.
                if doc.reuse_root(scene) is None:
                    raise OpError(f"a cena {scene.id} não tem imagem para variar")
                scene.visual = _own_visual(doc, scene)
            scene.visual.variation += 1
            scene.visual.asset = None
        case SetSceneMotion():
            scene = _scene(doc, op.scene_id)
            if op.motion is not None and op.motion not in CAMERA_MOVES:
                raise OpError(f"movimento inválido: {op.motion}")
            scene.motion = op.motion
        case SetSceneTransition():
            scene = _scene(doc, op.scene_id)
            if op.transition is not None and op.transition not in TRANSITIONS:
                raise OpError(f"transição inválida: {op.transition}")
            scene.transition_in = op.transition
        case SetSceneKind():
            scene = _scene(doc, op.scene_id)
            if op.kind not in SCENE_KINDS:
                raise OpError(f"tipo de cena inválido: {op.kind}")
            if op.kind == "video" and scene.kind != "video":
                # Vídeo IA tem limite por vídeo e custo confirmado: só pelo botão da cena (rota do vídeo IA).
                raise OpError("para animar a cena com vídeo IA, use \"Animar com vídeo IA\" na aba Cenas")
            scene.kind = op.kind  # type: ignore[assignment]
            if op.number is not None:
                scene.number = op.number
            if op.kind not in CARD_KINDS and scene.visual.type == "none" and scene.visual.media is None:
                # Cartão sem fundo virou imagem: sem isso a cena ficaria sem prompt, sem "Outra imagem" e sem imagem.
                # Com mídia real, a imagem é a mídia (o Visual fica como está).
                scene.visual = _image_from_card(doc, scene)
                if scene.motion == "static":
                    scene.motion = None
        case SetSceneText():
            scene = _scene(doc, op.scene_id)
            scene.overlay_text[op.lang] = op.text
        case SetSceneLook():
            scene = _scene(doc, op.scene_id)
            data = scene.look.model_dump()
            for field in ("brightness", "contrast", "saturation", "warmth", "blur", "grade"):
                value = getattr(op, field)
                if value is not None:
                    data[field] = value
            scene.look = type(scene.look).model_validate(data)
        case SetAiReason():
            scene = _scene(doc, op.scene_id)
            scene.visual.ai_reason = " ".join(op.text.split())[:200] or None  # não muda a imagem: só a revisão
        case SplitScene():
            scene = _scene(doc, op.scene_id)
            visual = _split_visual(doc, scene, op.prompt)
            kind = scene.kind
            if kind in DATA_KINDS:
                # Os dados ficam na primeira parte; a segunda mostra a imagem de fundo ou, sem ela, a fala.
                kind = "image" if visual.reuse_of or visual.prompt else "text"
            new_scene = Scene(
                segment_id=scene.segment_id,
                part=scene.part + 1,
                kind=kind,
                visual=visual,
                focus=_next_focus(scene.focus) if visual.reuse_of else None,
            )
            if scene.span is not None:
                new_scene.span = scene.span // 2
                scene.span -= new_scene.span
            for other in doc.scenes_of(scene.segment_id):
                if other.part > scene.part:
                    other.part += 1
            doc.scenes.append(new_scene)
            renumber_parts(doc, scene.segment_id)
        case MergeSceneWithNext():
            scene = _scene(doc, op.scene_id)
            siblings = doc.scenes_of(scene.segment_id)
            later = [s for s in siblings if s.part == scene.part + 1]
            if not later:
                raise OpError("não há cena seguinte no mesmo segmento para juntar")
            if later[0].locked:
                raise OpError("a cena seguinte está travada")
            scene.span = scene.span + later[0].span if scene.span is not None and later[0].span is not None else None
            _remove_scenes(doc, {later[0].id})
            renumber_parts(doc, scene.segment_id)
        case SetStyle():
            new_overrides = set_style_override(doc.style_overrides, op.path, op.value, base_style)
            if base_style is not None:
                from studio.core.styles.library import deep_merge

                try:
                    StyleSpec.model_validate(deep_merge(base_style.model_dump(mode="json"), new_overrides))
                except Exception as exc:  # noqa: BLE001 - mensagem vai para o usuário
                    raise OpError(f"ajuste de estilo inválido em '{op.path}': {exc}") from exc
            doc.style_overrides = new_overrides
        case SetStyleId():
            _set_style_id(doc, op.style_id)
        case SetMusic():
            cue = None
            if op.cue_id:
                cue = next((c for c in doc.music if c.id == op.cue_id), None)
                if cue is None:
                    raise OpError(f"faixa de música não encontrada: {op.cue_id}")
                if op.start_segment is not None:
                    # troca o segmento de início de uma faixa existente ("" = começo do vídeo)
                    if op.start_segment:
                        _segment(doc, op.start_segment, editable=False)
                    cue.start_segment = op.start_segment or None
            elif op.start_segment:
                cue = MusicCue(start_segment=op.start_segment)
                _segment(doc, op.start_segment, editable=False)
                doc.music.append(cue)
            elif doc.music:
                cue = doc.music[0]
            else:
                cue = MusicCue()
                doc.music.append(cue)
            if op.mood is not None:
                cue.mood = op.mood
            if op.volume_db is not None:
                cue.volume_db = op.volume_db
            if op.enabled is not None:
                cue.enabled = op.enabled
            if op.track is not None:
                cue.track = op.track or None
            if op.loop is not None:
                cue.loop = op.loop
        case SetLock():
            for target in op.ids:
                if target.startswith("seg"):
                    _segment(doc, target, editable=False).locked = op.locked
                else:
                    _scene(doc, target, editable=False).locked = op.locked
        case SetTitle():
            if not op.title.strip():
                raise OpError("título vazio")
            doc.title = op.title.strip()
        case SetMetadata():
            meta = doc.metadata.get(op.lang) or Metadata()
            for field in ("title", "description", "tags", "hashtags"):
                value = getattr(op, field)
                if value is not None:
                    setattr(meta, field, value)
            doc.metadata[op.lang] = meta
        case SetThumbnail():
            if op.lang and op.text is not None:
                doc.thumbnail.text[op.lang] = op.text
            if op.scene_id:
                _scene(doc, op.scene_id, editable=False)
                doc.thumbnail.scene_id = op.scene_id
            if op.regenerate:
                doc.thumbnail.variation += 1
        case _:
            raise OpError(f"operação desconhecida: {op!r}")


def apply_ops(doc: ProjectDoc, ops: list[Any], base_style: StyleSpec | None = None) -> ProjectDoc:
    """Aplica uma lista de operações numa cópia do documento e revalida o resultado."""
    new_doc = doc.model_copy(deep=True)
    for index, op in enumerate(ops, 1):
        try:
            apply_op(new_doc, op, base_style)
        except OpError as exc:
            raise OpError(f"operação {index} ({getattr(op, 'op', '?')}): {exc}") from exc
    drop_stale_edit(doc, new_doc)  # fala ou imagem da cena mudou: a edição automática dela fica velha
    return ProjectDoc.model_validate(new_doc.model_dump())
