"""Banco de dados (SQLAlchemy 2). SQLite em modo WAL por padrão."""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, event, inspect
from sqlalchemy.engine import Connection, Engine
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
    # Banco novo (nenhuma tabela do app): o create_all já cria o esquema atual, então ele é marcado direto na
    # última migração. Banco que já existia: marcado na base e sobe até a última.
    existing = set(inspect(engine).get_table_names())
    fresh = not (existing & set(Base.metadata.tables))
    Base.metadata.create_all(engine)
    if engine.dialect.name == "sqlite":
        added = add_missing_columns(engine)
        if added:
            log.info("banco atualizado: colunas novas %s", ", ".join(added))
    migrate(engine, fresh=fresh)


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BASE_REVISION = "0001_base"


@contextmanager
def foreign_keys_off(conn: Connection) -> Iterator[None]:
    """Desliga as chaves estrangeiras do SQLite nesta conexão enquanto as migrações rodam.

    O modo batch do Alembic recria a tabela (cria a nova, copia, apaga a velha); com as chaves ligadas, apagar
    `projects` dispararia o ON DELETE CASCADE e levaria versões e publicações junto. O PRAGMA só vale fora de
    transação, por isso vem antes de qualquer begin.
    """
    if conn.dialect.name != "sqlite":
        yield
        return
    conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
    conn.commit()
    try:
        yield
    finally:
        if conn.in_transaction():
            conn.rollback()
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()


def _alembic_config(script_location: Path | None = None):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(script_location or MIGRATIONS_DIR))
    return cfg


def current_revision(engine: Engine) -> str | None:
    from alembic.runtime.migration import MigrationContext

    with engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def _only_base_revision(script_location: Path | None = None) -> bool:
    """As migrações desta versão do app são só a base (que não muda nada no banco)?"""
    versions = (script_location or MIGRATIONS_DIR) / "versions"
    names = {p.stem for p in versions.glob("*.py") if not p.name.startswith("__")}
    return names <= {BASE_REVISION}


def migrate(engine: Engine, *, fresh: bool = False, script_location: Path | None = None) -> list[str]:
    """Roda `alembic upgrade head`. Banco sem `alembic_version` é marcado antes: na última revisão se é novo
    (`fresh`), na base se já existia. Retorna as revisões aplicadas agora.

    Não derruba o app em dois casos: Alembic ainda não instalado no ambiente (app atualizado sem rodar o
    `scripts\\setup.ps1`) enquanto só existe a base, e banco marcado numa revisão que esta versão do app não conhece
    (o banco é de uma versão mais nova; voltar de versão sempre funcionou com `add_missing_columns`)."""
    try:
        from alembic import command
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory
    except ImportError as exc:
        if _only_base_revision(script_location):
            log.warning("Alembic não instalado: migrações do banco puladas (rode scripts\\setup.ps1 para atualizar "
                        "as dependências do app)")
            return []
        raise RuntimeError("Falta o Alembic para atualizar o banco desta versão do app. Rode scripts\\setup.ps1 "
                           "(ou 'uv sync --project backend') e abra o app de novo.") from exc

    cfg = _alembic_config(script_location)
    script = ScriptDirectory.from_config(cfg)
    with engine.connect() as conn, foreign_keys_off(conn):
        cfg.attributes["connection"] = conn
        before = MigrationContext.configure(conn).get_current_revision()
        if before is None:
            command.stamp(cfg, "head" if fresh else BASE_REVISION)
            conn.commit()
            before = MigrationContext.configure(conn).get_current_revision()
        try:
            known = script.get_revision(before) is not None
        except Exception:  # noqa: BLE001 - revisão desconhecida (ResolutionError/CommandError, conforme a versão)
            known = False
        if not known:
            log.warning("banco de uma versão mais nova do app (revisão %s, desconhecida aqui): migrações puladas. "
                        "Atualize o app para a versão mais recente.", before)
            return []
        heads = set(script.get_heads())
        if before in heads:
            return []
        command.upgrade(cfg, "head")
        conn.commit()
        if conn.dialect.name == "sqlite":
            broken = conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if broken:
                log.warning("migração deixou %d referências quebradas: %s", len(broken), broken[:5])
    applied = [rev.revision for rev in script.iterate_revisions("head", before)]
    applied.reverse()
    if applied:
        log.info("banco migrado: %s", ", ".join(applied))
    return applied


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
    colunas é com o Alembic (`studio/migrations`), que roda logo depois desta função.
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


def _configure_readonly(dbapi_connection, _record) -> None:  # pragma: no cover - trivial
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA query_only=ON")
    cursor.execute("PRAGMA busy_timeout=10000")
    cursor.close()


def sqlite_path(url: str) -> Path:
    """O arquivo de um URL `sqlite:///caminho` (também `sqlite+pysqlite:///` e `sqlite:///file:caminho?...`)."""
    from sqlalchemy.engine import make_url

    parsed = make_url(url)
    if parsed.get_backend_name() != "sqlite":
        raise ValueError(f"o modo só leitura só funciona com SQLite, e o banco configurado é {parsed.get_backend_name()}")
    database = parsed.database or ""
    if not database or database == ":memory:" or database.startswith("file::memory:"):
        raise ValueError("o modo só leitura precisa de um banco SQLite em arquivo")
    if database.startswith("file:"):
        database = database[len("file:"):].split("?", 1)[0]
    return Path(database)


@contextmanager
def readonly_engine() -> Iterator[Engine]:
    """Durante o bloco, todo `session_scope` lê o banco SQLite em modo só leitura (`mode=ro` e `query_only`): nada
    é gravado (nem migração, nem checkpoint do WAL). Para comandos que só consultam, como `studio edit-metrics`.
    O banco é o da configuração (`Settings.db_url`, que respeita `VS_DATABASE_URL`) e precisa existir; banco que não
    é arquivo SQLite é recusado (ValueError)."""
    global _engine, _session_factory
    path = sqlite_path(get_settings().db_url)
    if not path.is_file():
        raise FileNotFoundError(f"banco não encontrado: {path}")
    # Sem o arquivo -wal (banco fechado e com checkpoint), `mode=ro` ainda criaria um -wal vazio: `immutable` lê o
    # arquivo como está, sem criar nada. Com o -wal (app aberto), `mode=ro` lê também o que ainda está nele.
    flag = "mode=ro" if path.with_name(path.name + "-wal").is_file() else "immutable=1"
    url = f"sqlite:///file:{path.resolve().as_posix()}?{flag}&uri=true"
    engine = create_engine(url, future=True, connect_args={"check_same_thread": False, "timeout": 30})
    event.listen(engine, "connect", _configure_readonly)
    with _lock:
        saved = (_engine, _session_factory)
        _engine, _session_factory = engine, sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield engine
    finally:
        with _lock:
            _engine, _session_factory = saved
        engine.dispose()


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
