"""Migrações com Alembic: base sem mudanças, bancos antigos sintéticos e uma migração futura de teste."""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import inspect

from studio import db
from studio.config import REPO_ROOT, Settings, set_settings
from studio.db import BASE_REVISION, current_revision, get_engine, init_db, reset_engine, session_scope
from studio.models import Project, ProjectVersion

# Revisão de teste: o "modelo" trocou projects.nome por title e deixou de usar projects.lixo.
FUTURE_REVISION = '''
from studio.migrations import helpers

revision = "0099_teste"
down_revision = "{down}"
branch_labels = None
depends_on = None


def upgrade():
    helpers.rename_column("projects", "nome", "title")
    helpers.drop_column("projects", "lixo")


def downgrade():
    pass
'''


def _use(data_dir: Path) -> None:
    set_settings(Settings(data_dir=data_dir, presets_dir=REPO_ROOT / "presets", offline=True, workers_enabled=False))
    reset_engine()


def _old_database(data_dir: Path, *, renamed: bool = False) -> None:
    """Banco de uma versão antiga do app: sem alembic_version, com colunas a menos e chaves estrangeiras."""
    data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(data_dir / "studio.db")
    title = "nome VARCHAR(300), lixo TEXT" if renamed else "title VARCHAR(300)"
    con.execute(f"CREATE TABLE projects (id VARCHAR(32) PRIMARY KEY, {title}, style_id VARCHAR(100), "
                "status VARCHAR(40), current_version INTEGER, settings JSON)")
    con.execute("CREATE TABLE project_versions (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "project_id VARCHAR(32) REFERENCES projects(id) ON DELETE CASCADE, number INTEGER, "
                "parent_number INTEGER, doc JSON, message TEXT, author VARCHAR(40))")
    cols = "id, nome, lixo" if renamed else "id, title"
    vals = "'prj_1', 'Antigo', 'sobra'" if renamed else "'prj_1', 'Antigo'"
    con.execute(f"INSERT INTO projects ({cols}, style_id, status, current_version, settings) "
                f"VALUES ({vals}, 'misterios-short', 'draft', 2, '{{}}')")
    for n in (1, 2):
        con.execute("INSERT INTO project_versions (project_id, number, doc, message, author) "
                    f"VALUES ('prj_1', {n}, '{{}}', 'v{n}', 'user')")
    con.commit()
    con.close()


def _head() -> str:
    from alembic.script import ScriptDirectory

    return ScriptDirectory.from_config(db._alembic_config()).get_current_head()


def test_new_database_is_stamped_at_head(tmp_path):
    _use(tmp_path / "novo")
    init_db()
    assert current_revision(get_engine()) == _head()
    assert "alembic_version" in inspect(get_engine()).get_table_names()
    assert db.migrate(get_engine()) == []  # rodar de novo não faz nada


def test_old_database_gets_baseline_without_changes(tmp_path):
    data_dir = tmp_path / "antigo"
    _old_database(data_dir)
    _use(data_dir)
    init_db()
    engine = get_engine()
    assert current_revision(engine) == _head()
    columns = {c["name"] for c in inspect(engine).get_columns("projects")}
    assert {"title", "channel_id", "created_at", "updated_at"} <= columns  # add_missing_columns continua valendo
    with session_scope() as s:
        project = s.get(Project, "prj_1")
        assert project.title == "Antigo" and project.current_version == 2
        assert s.query(ProjectVersion).filter_by(project_id="prj_1").count() == 2
    init_db()  # de novo: nada muda
    assert current_revision(engine) == _head()


@pytest.fixture
def future_migrations(tmp_path, monkeypatch):
    """Cópia das migrações do app com uma revisão a mais (só para o teste)."""
    folder = tmp_path / "migracoes"
    shutil.copytree(db.MIGRATIONS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "versions" / "0099_teste.py").write_text(FUTURE_REVISION.format(down=_head()), encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", folder)
    return folder


def test_rename_and_drop_keep_data_and_children(tmp_path, future_migrations):
    data_dir = tmp_path / "renomear"
    _old_database(data_dir, renamed=True)
    _use(data_dir)
    init_db()  # add_missing_columns cria `title` vazia; a migração copia de `nome` e apaga `nome` e `lixo`
    engine = get_engine()
    assert current_revision(engine) == "0099_teste"
    columns = {c["name"] for c in inspect(engine).get_columns("projects")}
    assert "title" in columns and "nome" not in columns and "lixo" not in columns
    with session_scope() as s:
        assert s.get(Project, "prj_1").title == "Antigo"
        # recriar `projects` não disparou o ON DELETE CASCADE das versões
        assert s.query(ProjectVersion).filter_by(project_id="prj_1").count() == 2
    with engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
    # as chaves estrangeiras continuam valendo depois da migração
    with session_scope() as s:
        s.delete(s.get(Project, "prj_1"))
    with session_scope() as s:
        assert s.query(ProjectVersion).count() == 0


def test_database_already_at_base_runs_only_new_revision(tmp_path, monkeypatch):
    data_dir = tmp_path / "base"
    _use(data_dir)
    init_db()  # banco novo marcado na última revisão real (a base)
    assert current_revision(get_engine()) == BASE_REVISION
    with session_scope() as s:
        s.add(Project(id="prj_2", title="Novo", style_id="misterios-short"))
    folder = tmp_path / "migracoes"
    shutil.copytree(db.MIGRATIONS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "versions" / "0099_teste.py").write_text(FUTURE_REVISION.format(down=BASE_REVISION), encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", folder)
    reset_engine()
    init_db()  # app atualizado: só a revisão nova roda (sem `nome`/`lixo`, ela não faz nada)
    assert current_revision(get_engine()) == "0099_teste"
    with session_scope() as s:
        assert s.get(Project, "prj_2").title == "Novo"


def test_database_from_newer_app_still_opens(tmp_path, caplog):
    """Voltar para uma versão anterior do app (ou trocar de branch) não derruba o init_db."""
    _use(tmp_path / "mais-novo")
    init_db()
    with get_engine().begin() as conn:
        conn.exec_driver_sql("UPDATE alembic_version SET version_num='0002_futura'")
    reset_engine()
    with caplog.at_level("WARNING", logger="studio.db"):
        init_db()
    assert current_revision(get_engine()) == "0002_futura"  # nada muda no banco
    assert "versão mais nova do app" in caplog.text
    with session_scope() as s:
        s.add(Project(id="prj_3", title="Segue", style_id="misterios-short"))


def test_missing_alembic_skips_only_while_there_is_just_the_base(tmp_path, monkeypatch, caplog):
    """App atualizado sem rodar o setup (venv sem Alembic): abre enquanto só existe a base; com migração nova, avisa."""
    import builtins

    real_import = builtins.__import__

    def no_alembic(name, *args, **kwargs):
        if name == "alembic" or name.startswith("alembic."):
            raise ImportError("No module named 'alembic'")
        return real_import(name, *args, **kwargs)

    _use(tmp_path / "sem-alembic")
    monkeypatch.setattr(builtins, "__import__", no_alembic)
    with caplog.at_level("WARNING", logger="studio.db"):
        init_db()
    assert "setup.ps1" in caplog.text
    with session_scope() as s:
        s.add(Project(id="prj_4", title="Abre", style_id="misterios-short"))
    folder = tmp_path / "migracoes"
    shutil.copytree(db.MIGRATIONS_DIR, folder, ignore=shutil.ignore_patterns("__pycache__"))
    (folder / "versions" / "0099_teste.py").write_text(FUTURE_REVISION.format(down=BASE_REVISION), encoding="utf-8")
    monkeypatch.setattr(db, "MIGRATIONS_DIR", folder)
    with pytest.raises(RuntimeError, match="setup.ps1"):
        init_db()
