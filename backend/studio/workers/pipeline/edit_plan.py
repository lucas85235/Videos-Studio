"""Planejador de edição determinístico (ADR 0004 §3.6): subplanos e eventos presos a palavras.

Encaixe da fundação: `plan` não faz nada. O item `editor-planejador` escreve `Scene.shots`, `Scene.fx` e completa
`Scene.mark` a partir da gramática do estilo (`grammar_for`), do acervo e da semente do projeto, sem LLM.
"""

from __future__ import annotations

from studio.core.project.document import ProjectDoc
from studio.core.project.pool import Pool
from studio.core.styles.schema import StyleSpec


def plan(doc: ProjectDoc, style: StyleSpec, pool: Pool) -> None:
    """Planeja (em `doc`, no lugar) os subplanos e eventos das cenas. Encaixe vazio: nada muda."""
    return None
