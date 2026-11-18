"""Rotas do editor automático (ADR 0004 §3.6): "Reeditar" refaz os subplanos e eventos do projeto.

Determinístico (a semente do projeto) e sem custo: não chama LLM nem gera imagem. O que o usuário travou ou fez à mão
fica. A intensidade escolhida fica gravada no ajuste de estilo do projeto (`style_overrides.edit.grammar.intensity`),
para o próximo storyboard sair com ela.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.core.project import service
from studio.core.project.pool import load_pool
from studio.core.styles.library import StyleNotFound, deep_merge, get_style
from studio.core.styles.schema import grammar_for
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
