"""Acervo de material real antes do storyboard (ADR 0004 §3.4).

Encaixe da fundação: `gather` devolve um acervo vazio e `attach_chosen` não anexa nada. O item `acervo-real`
implementa os coletores (uploads, capturas das fontes, clipes de links oficiais, fotos e vídeos livres, `media_find`)
e o anexo automático das cenas que citam `pool_id`.
"""

from __future__ import annotations

from studio.core.project.document import ProjectDoc
from studio.core.project.pool import Pool
from studio.core.styles.schema import StyleSpec


def gather(doc: ProjectDoc, style: StyleSpec) -> Pool:
    """Coleta o acervo do projeto (falha de um coletor vira aviso em `Pool.warnings`). Encaixe vazio."""
    return Pool()


def attach_chosen(doc: ProjectDoc, pool: Pool) -> list[str]:
    """Anexa (em `doc`, no lugar) a mídia do acervo nas cenas que citam `pool_id`, preenchendo `MediaRef.tier` e o
    resto. Devolve os avisos. Encaixe vazio: nada muda."""
    return []


def prompt_block(pool: Pool, max_lines: int = 120) -> str:
    """As linhas do acervo para o pedido do storyboard (`id · nível · tipo · desc · dur`). Encaixe vazio: ""."""
    return ""
