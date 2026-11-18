"""Rotas do editor automático (ADR 0004 §3.6 e §3.11).

- `POST /projects/{id}/reedit`: refaz os subplanos e eventos do projeto. Determinístico (a semente do projeto) e sem
  custo: não chama LLM nem gera imagem. O que o usuário travou ou fez à mão fica. A intensidade escolhida fica gravada
  no ajuste de estilo do projeto (`style_overrides.edit.grammar.intensity`), para o próximo storyboard sair com ela.
- `GET /projects/{id}/edit-track`: por cena, as palavras com o gatilho de cada uma, os subplanos e os eventos (a tela
  "Edição" da prévia).
- `PATCH /projects/{id}/scenes/{sid}/fx` e `.../shots`: a lista inteira da cena, como o usuário deixou (travar, apagar,
  mover o gatilho). O gatilho tem de achar uma palavra falada da cena (ou ser "@ms").
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.core.project import service
from studio.core.project.document import FxEvent, ProjectDoc, Scene, SubShot
from studio.core.project.media_edit import scene_label
from studio.core.project.pool import load_pool
from studio.core.project.presenter import trigger_index
from studio.core.styles.library import StyleNotFound, deep_merge, get_style
from studio.core.styles.schema import StyleSpec, grammar_for
from studio.workers.pipeline import edit_plan

router = APIRouter(prefix="/api", tags=["edição"])


class ReeditBody(BaseModel):
    intensity: int | None = Field(default=None, ge=0, le=3)
    """0 calmo (0,5× das cotas), 1 (0,75×), 2 o padrão (1×), 3 agitado (1,4×). None: a do estilo."""


@router.post("/projects/{project_id}/reedit")
def reedit(project_id: str, body: ReeditBody | None = None) -> dict[str, Any]:
    body = body or ReeditBody()
    try:
        doc = service.get_doc(project_id).model_copy(deep=True)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    before = doc.model_dump(mode="json")
    if body.intensity is not None:
        doc.style_overrides = deep_merge(doc.style_overrides, {"edit": {"grammar": {"intensity": body.intensity}}})
    try:
        style = get_style(doc.style_id, doc.style_overrides)
    except StyleNotFound as exc:
        raise HTTPException(status_code=422, detail=f"estilo não encontrado: {doc.style_id}") from exc
    grammar = grammar_for(style)
    if grammar.editor == "off":
        return {"version": None, "changed": False, "editor": "off", "intensity": grammar.intensity,
                "summary": edit_plan.summary(doc)}
    edit_plan.plan(doc, style, load_pool(project_id))
    changed = doc.model_dump(mode="json") != before
    version = (service.commit(project_id, doc, f"Edição refeita (intensidade {grammar.intensity})")
               if changed else None)
    return {"version": version, "changed": changed, "editor": grammar.editor, "intensity": grammar.intensity,
            "summary": edit_plan.summary(doc)}


# --------------------------------------------------------------------------------------------
# Trilha de edição (ver, travar, apagar e mover)
# --------------------------------------------------------------------------------------------

MAX_FX = 60
MAX_SHOTS = 24
SCENE_LOCKED = "A cena está travada: destrave para mudar a edição dela."


class FxBody(BaseModel):
    fx: list[FxEvent] = Field(max_length=MAX_FX)


class ShotsBody(BaseModel):
    shots: list[SubShot] = Field(max_length=MAX_SHOTS)


def _doc_and_style(project_id: str) -> tuple[ProjectDoc, StyleSpec]:
    try:
        doc = service.get_doc(project_id).model_copy(deep=True)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    try:
        style = get_style(doc.style_id, doc.style_overrides)
    except StyleNotFound as exc:
        raise HTTPException(status_code=422, detail=f"estilo não encontrado: {doc.style_id}") from exc
    return doc, style


def _scene(doc: ProjectDoc, scene_id: str) -> Scene:
    try:
        scene = doc.scene(scene_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="cena não encontrada") from exc
    if scene.locked:
        raise HTTPException(status_code=409, detail=SCENE_LOCKED)
    return scene


def _check_triggers(doc: ProjectDoc, style: StyleSpec, scene_id: str, ats: list[str | None]) -> None:
    """Cada gatilho em palavra tem de achar uma palavra falada da cena; "@ms" e o início (None) valem sempre."""
    words = edit_plan.scene_triggers(doc, style, scene_id) or []
    for at in ats:
        if at is None or at.startswith("@"):
            continue
        if trigger_index(words, at) is None:
            raise HTTPException(status_code=422, detail=f"O gatilho “{at}” não é uma palavra falada nesta cena.")


@router.get("/projects/{project_id}/edit-track")
def edit_track(project_id: str) -> dict[str, Any]:
    """Por cena (na ordem do vídeo): palavras e gatilhos, subplanos e eventos com o instante estimado."""
    doc, style = _doc_and_style(project_id)
    grammar = grammar_for(style)
    return {"editor": grammar.editor, "intensity": grammar.intensity, "summary": edit_plan.summary(doc),
            "scenes": edit_plan.track(doc, style, load_pool(project_id))}


@router.patch("/projects/{project_id}/scenes/{scene_id}/fx")
def patch_fx(project_id: str, scene_id: str, body: FxBody) -> dict[str, Any]:
    """Troca os eventos da cena pela lista enviada (o "Reeditar" preserva os travados e os do usuário)."""
    doc, style = _doc_and_style(project_id)
    scene = _scene(doc, scene_id)
    _check_triggers(doc, style, scene_id, [e.at for e in body.fx])
    if [e.model_dump(mode="json") for e in body.fx] == [e.model_dump(mode="json") for e in scene.fx]:
        return {"version": None, "changed": False, "fx": [e.model_dump(mode="json") for e in scene.fx]}
    scene.fx = list(body.fx)
    version = service.commit(project_id, doc, f"{scene_label(doc, scene_id)}: eventos da edição ajustados")
    return {"version": version, "changed": True, "fx": [e.model_dump(mode="json") for e in scene.fx]}


@router.patch("/projects/{project_id}/scenes/{scene_id}/shots")
def patch_shots(project_id: str, scene_id: str, body: ShotsBody) -> dict[str, Any]:
    """Troca os subplanos da cena pela lista enviada. O primeiro plano começa no início da cena (sem gatilho)."""
    doc, style = _doc_and_style(project_id)
    scene = _scene(doc, scene_id)
    shots = list(body.shots)
    if shots and shots[0].at is not None:
        shots[0] = shots[0].model_copy(update={"at": None})  # apagado o 1º plano, o seguinte passa a abrir a cena
    _check_triggers(doc, style, scene_id, [s.at for s in shots])
    if [s.model_dump(mode="json") for s in shots] == [s.model_dump(mode="json") for s in scene.shots]:
        return {"version": None, "changed": False, "shots": [s.model_dump(mode="json") for s in scene.shots]}
    scene.shots = shots
    version = service.commit(project_id, doc, f"{scene_label(doc, scene_id)}: planos da edição ajustados")
    return {"version": version, "changed": True, "shots": [s.model_dump(mode="json") for s in scene.shots]}
