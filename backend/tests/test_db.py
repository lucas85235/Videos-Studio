from __future__ import annotations

import sqlite3

from sqlalchemy import inspect

from studio.config import REPO_ROOT, Settings, set_settings
from studio.db import get_engine, init_db, reset_engine, session_scope
from studio.models import Channel


def test_init_db_adds_new_columns_to_old_database(tmp_path):
    data_dir = tmp_path / "antigo"
    data_dir.mkdir()
    # banco de uma versão antiga: a tabela de canais ainda não tinha `settings` nem `created_at`
    con = sqlite3.connect(data_dir / "studio.db")
    con.execute(
        "CREATE TABLE channels (id VARCHAR(32) PRIMARY KEY, name VARCHAR(200), niche VARCHAR(200), persona TEXT, "
        "languages JSON, default_style_id VARCHAR(100), style_bible JSON)"
    )
    con.execute("INSERT INTO channels (id, name, niche, persona, languages, style_bible) VALUES ('chn_1', 'Antigo', '', '', '[\"pt-BR\"]', '{}')")
    con.commit()
    con.close()

    set_settings(Settings(data_dir=data_dir, presets_dir=REPO_ROOT / "presets", offline=True, workers_enabled=False))
    reset_engine()
    init_db()
    columns = {c["name"] for c in inspect(get_engine()).get_columns("channels")}
    assert {"settings", "created_at"} <= columns
    with session_scope() as s:
        row = s.get(Channel, "chn_1")
        assert row.name == "Antigo" and row.settings == {} and row.created_at is None
        row.settings = {"posting": {"timezone": "UTC", "slots": []}}
    with session_scope() as s:
        assert s.get(Channel, "chn_1").settings["posting"]["timezone"] == "UTC"
    # rodar de novo não muda nada
    init_db()
