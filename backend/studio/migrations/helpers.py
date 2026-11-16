"""Operações seguras para as migrações (SQLite e outros bancos).

Lembre: no `init_db`, `add_missing_columns` roda ANTES das migrações. Se o modelo trocou `nome` por `titulo`,
o banco antigo já chega aqui com `titulo` criada (vazia ou com o valor padrão) e `nome` ainda cheia. As funções
abaixo tratam esse caso e não fazem nada quando a mudança já foi aplicada (rodar duas vezes não quebra).
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import inspect


def columns(table: str) -> set[str]:
    """Colunas que a tabela tem agora (vazio se a tabela não existe)."""
    inspector = inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {c["name"] for c in inspector.get_columns(table)}


def drop_column(table: str, column: str) -> None:
    """Apaga a coluna, se ela existir. No SQLite o Alembic recria a tabela copiando os dados (modo batch)."""
    if column not in columns(table):
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_column(column)


def rename_column(table: str, old: str, new: str) -> None:
    """Renomeia a coluna mantendo os dados.

    Se `new` já existe (criada pelo `add_missing_columns`), copia os valores de `old` para ela e apaga `old`.
    Se `old` não existe mais, não faz nada.
    """
    present = columns(table)
    if old not in present:
        return
    if new in present:
        op.execute(f'UPDATE "{table}" SET "{new}" = "{old}"')
        drop_column(table, old)
        return
    with op.batch_alter_table(table) as batch:
        batch.alter_column(old, new_column_name=new)
