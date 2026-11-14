"""Banco de dados (SQLAlchemy 2). SQLite em modo WAL por padrão."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from studio.config import get_settings

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None
_lock = threading.Lock()


def _configure_sqlite(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def get_engine() -> Engine:
    global _engine, _session_factory
    with _lock:
        if _engine is None:
            url = get_settings().db_url
            kwargs: dict = {"future": True}
            if url.startswith("sqlite"):
                kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
            _engine = create_engine(url, **kwargs)
            if url.startswith("sqlite"):
                event.listen(_engine, "connect", _configure_sqlite)
            _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine


def init_db() -> None:
    from studio import models  # noqa: F401  (registra as tabelas)

    engine = get_engine()
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        added = add_missing_columns(engine)
        if added:
            log.info("banco atualizado: colunas novas %s", ", ".join(added))


def _sql_literal(value: Any) -> str | None:
    if value is None or isinstance(value, datetime):
        return None  # datas novas ficam vazias nas linhas antigas
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict | list):
        value = json.dumps(value, ensure_ascii=False)
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _column_default(column: Any) -> str | None:
    default = column.default
    if default is None:
        return None
    if default.is_scalar:
        return _sql_literal(default.arg)
    if default.is_callable:
        try:
            return _sql_literal(default.arg(None))
        except Exception:  # noqa: BLE001 - default que depende do contexto: fica vazio
            return None
    return None


def add_missing_columns(engine: Engine) -> list[str]:
    """Migração leve do SQLite: cria colunas e índices novos dos modelos em tabelas que já existem.

    Cobre o caso comum de atualização do app (coluna nova). As linhas antigas recebem o valor padrão do
    modelo quando ele é simples (texto, número, JSON vazio); datas novas ficam vazias. Renomear ou apagar
    colunas ainda exige migração manual.
    """
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            present = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column.type.compile(dialect=engine.dialect)}'
                default = _column_default(column)
                if default is not None:
                    ddl += f" DEFAULT {default}"
                conn.exec_driver_sql(ddl)
                added.append(f"{table.name}.{column.name}")
            for index in table.indexes:
                index.create(conn, checkfirst=True)
    return added


def reset_engine() -> None:
    """Descarta o engine atual (usado nos testes ao trocar de banco)."""
    global _engine, _session_factory
    with _lock:
        if _engine is not None:
            _engine.dispose()
        _engine = None
        _session_factory = None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _session_factory is not None
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
