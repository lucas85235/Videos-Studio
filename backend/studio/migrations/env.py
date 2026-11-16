"""Ambiente do Alembic.

O app (`studio.db.migrate`) passa a conexão em `config.attributes["connection"]`, já com as chaves estrangeiras
desligadas. Pela linha de comando (`uv run alembic ...` dentro de `backend/`), abre o banco configurado no app
e faz o mesmo.
"""

from __future__ import annotations

from alembic import context

from studio import models  # noqa: F401  (registra as tabelas)
from studio.db import Base, foreign_keys_off, get_engine

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:
    # render_as_batch: no SQLite, renomear/apagar coluna recria a tabela copiando os dados.
    context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(url=str(get_engine().url), target_metadata=target_metadata, literal_binds=True,
                      render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with get_engine().connect() as conn, foreign_keys_off(conn):
        _run(conn)
        conn.commit()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
