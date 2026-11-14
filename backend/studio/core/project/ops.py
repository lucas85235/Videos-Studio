"""Operações de edição (patch) sobre o documento de projeto.

São pequenas e tipadas: o agente de edição por IA só pode propor estas operações, e cada uma
é validada antes de virar um commit. O grafo de artefatos decide depois o que precisa ser
regenerado (README §6).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, TypeAdapter

from studio.core.project.document import (
    Metadata,
    MusicCue,
    ProjectDoc,
    Scene,
    Segment,
    Visual,
    renumber_parts,
)
from studio.core.styles.library import set_path
from studio.core.styles.schema import CAMERA_MOVES, SCENE_KINDS, TRANSITIONS, StyleSpec


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


class SetMusic(_Op):
    op: Literal["set_music"] = "set_music"
    cue_id: str | None = None
    start_segment: str | None = None
    mood: str | None = None
    volume_db: float | None = None
    enabled: bool | None = None


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
    | SplitScene
    | MergeSceneWithNext
    | SetStyle
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
            doc.segments = [s for s in doc.segments if s.id != seg.id]
            doc.scenes = [s for s in doc.scenes if s.segment_id != seg.id]
        case MoveSegment():
            seg = _segment(doc, op.segment_id)
            doc.segments = [s for s in doc.segments if s.id != seg.id]
            doc.segments.insert(_insert_index(doc, op.after_segment_id), seg)
        case UpdateScenePrompt():
            scene = _scene(doc, op.scene_id)
            scene.visual.prompt = op.prompt.strip()
            scene.visual.asset = None
        case RegenerateScene():
            scene = _scene(doc, op.scene_id)
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
            scene.kind = op.kind  # type: ignore[assignment]
            if op.number is not None:
                scene.number = op.number
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
        case SplitScene():
            scene = _scene(doc, op.scene_id)
            new_scene = Scene(
                segment_id=scene.segment_id,
                part=scene.part + 1,
                kind=scene.kind,
                visual=Visual(prompt=op.prompt or scene.visual.prompt, seed=scene.visual.seed + 7919),
            )
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
            doc.scenes = [s for s in doc.scenes if s.id != later[0].id]
            renumber_parts(doc, scene.segment_id)
        case SetStyle():
            new_overrides = set_path(doc.style_overrides, op.path, op.value)
            if base_style is not None:
                from studio.core.styles.library import deep_merge

                try:
                    StyleSpec.model_validate(deep_merge(base_style.model_dump(mode="json"), new_overrides))
                except Exception as exc:  # noqa: BLE001 - mensagem vai para o usuário
                    raise OpError(f"ajuste de estilo inválido em '{op.path}': {exc}") from exc
            doc.style_overrides = new_overrides
        case SetMusic():
            cue = None
            if op.cue_id:
                cue = next((c for c in doc.music if c.id == op.cue_id), None)
                if cue is None:
                    raise OpError(f"faixa de música não encontrada: {op.cue_id}")
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
    return ProjectDoc.model_validate(new_doc.model_dump())
