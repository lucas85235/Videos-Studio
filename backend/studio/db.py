"""Banco de dados (SQLAlchemy 2). SQLite em modo WAL por padrão."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from studio.config import get_settings


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

    Base.metadata.create_all(get_engine())


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
