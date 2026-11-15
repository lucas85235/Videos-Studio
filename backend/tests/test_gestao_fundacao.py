"""Fundação da gestão de canal (ADR 0002): tabelas, colunas novas, campos do canal, motivo do horário e routers."""

from __future__ import annotations

import sqlite3

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from studio.api.app import create_app
from studio.config import REPO_ROOT, Settings, set_settings
from studio.db import get_engine, init_db, reset_engine, session_scope
from studio.models import Channel, Project
from studio.workers.publish.schedule import plan_of

NEW_TABLES = {
    "video_metrics",
    "experiments",
    "lessons",
    "pending_tasks",
    "ideas",
    "affiliate_offers",
    "revenue_entries",
}


def test_new_tables_exist(isolated_settings):
    inspector = inspect(get_engine())
    assert NEW_TABLES <= set(inspector.get_table_names())
    assert "where_hint" in {c["name"] for c in inspector.get_columns("pending_tasks")}
    assert "parent_id" in {c["name"] for c in inspector.get_columns("projects")}


def test_old_database_gets_new_channel_columns_with_defaults(tmp_path):
    data_dir = tmp_path / "antigo"
    data_dir.mkdir()
    # banco de antes desta onda: canais sem os campos de gestão e projetos sem parent_id
    con = sqlite3.connect(data_dir / "studio.db")
    con.execute(
        "CREATE TABLE channels (id VARCHAR(32) PRIMARY KEY, name VARCHAR(200), niche VARCHAR(200), persona TEXT, "
        "languages JSON, default_style_id VARCHAR(100), style_bible JSON, settings JSON, created_at DATETIME)"
    )
    con.execute(
        "INSERT INTO channels (id, name, niche, persona, languages, style_bible, settings) "
        "VALUES ('chn_1', 'Antigo', '', '', '[\"pt-BR\"]', '{}', '{}')"
    )
    con.commit()
    con.close()

    set_settings(Settings(data_dir=data_dir, presets_dir=REPO_ROOT / "presets", offline=True, workers_enabled=False))
    reset_engine()
    init_db()
    columns = {c["name"] for c in inspect(get_engine()).get_columns("channels")}
    assert {
        "audience", "cadence", "posts_per_day", "min_stock", "stock_format", "short_hashtag",
        "credits_per_char", "credit_price_usd", "voice_defaults",
    } <= columns
    with session_scope() as s:
        row = s.get(Channel, "chn_1")
        assert row.audience == "" and row.cadence == ""
        assert row.posts_per_day == 0 and row.min_stock == 0
        assert row.stock_format == "short" and row.short_hashtag == ""
        assert row.credits_per_char == 0.0 and row.credit_price_usd == 0.0
        assert row.voice_defaults == {}
    assert NEW_TABLES <= set(inspect(get_engine()).get_table_names())
    init_db()  # rodar de novo não muda nada
    reset_engine()


def test_channel_new_fields_round_trip(isolated_settings):
    with TestClient(create_app()) as client:
        created = client.post("/api/channels", json={"name": "Canal G"}).json()
        assert created["stock_format"] == "short" and created["voice_defaults"] == {}
        assert created["posts_per_day"] == 0 and created["short_hashtag"] == ""
        body = {
            "name": "Canal G",
            "audience": "fãs de mistério",
            "cadence": "1 longo por semana",
            "posts_per_day": 3,
            "min_stock": 5,
            "stock_format": "any",
            "short_hashtag": "#misterio",
            "credits_per_char": 1.5,
            "credit_price_usd": 0.0002,
            "voice_defaults": {"final": {"provider": "elevenlabs", "voice": "abc"}},
        }
        updated = client.put(f"/api/channels/{created['id']}", json=body).json()
        for key, value in body.items():
            assert updated[key] == value
        # PUT parcial não apaga os campos novos
        again = client.put(f"/api/channels/{created['id']}", json={"name": "Canal G2"}).json()
        assert again["name"] == "Canal G2" and again["posts_per_day"] == 3 and again["short_hashtag"] == "#misterio"
        listed = next(c for c in client.get("/api/channels").json() if c["id"] == created["id"])
        assert listed["voice_defaults"]["final"]["voice"] == "abc"

        cid = created["id"]
        for bad in (
            {"posts_per_day": 21},
            {"min_stock": -1},
            {"stock_format": "medio"},
            {"short_hashtag": "misterio"},
            {"short_hashtag": "#dois termos"},
            {"credits_per_char": -1},
            {"credit_price_usd": -0.1},
            {"voice_defaults": "x"},
        ):
            assert client.put(f"/api/channels/{cid}", json={"name": "X", **bad}).status_code == 422, bad


def test_slot_reason_round_trip(isolated_settings):
    # slot antigo sem motivo continua lendo
    assert plan_of({"posting": {"timezone": "UTC", "slots": [{"weekdays": [0], "time": "18:00"}]}}).slots[0].reason == ""
    with TestClient(create_app()) as client:
        posting = {"timezone": "UTC", "slots": [{"weekdays": [0, 2], "time": "19:00", "reason": "pico depois do trabalho"}]}
        channel = client.post("/api/channels", json={"name": "Canal R", "posting": posting}).json()
        assert channel["posting"]["slots"][0]["reason"] == "pico depois do trabalho"
        too_long = {"timezone": "UTC", "slots": [{"weekdays": [0], "time": "19:00", "reason": "x" * 121}]}
        assert client.put(f"/api/channels/{channel['id']}", json={"name": "R", "posting": too_long}).status_code == 422


def test_parent_id_in_project_list(isolated_settings):
    with session_scope() as s:
        s.add(Project(id="prj_longo", title="Longo", style_id="x", settings={}))
        s.add(Project(id="prj_curto", title="Corte", style_id="x", settings={}, parent_id="prj_longo"))
    with TestClient(create_app()) as client:
        rows = {r["id"]: r for r in client.get("/api/projects").json()}
        assert rows["prj_curto"]["parent_id"] == "prj_longo"
        assert rows["prj_longo"]["parent_id"] is None


def test_new_routers_mounted(isolated_settings, monkeypatch):
    from studio.api import routes_analytics, routes_business, routes_ops, routes_shorts

    included = []
    original = __import__("fastapi").FastAPI.include_router

    def spy(self, router, *args, **kwargs):
        included.append(router)
        return original(self, router, *args, **kwargs)

    monkeypatch.setattr("fastapi.FastAPI.include_router", spy)
    app = create_app()
    for module in (routes_analytics, routes_shorts, routes_ops, routes_business):
        assert module.router.prefix == "/api"
        assert any(r is module.router for r in included), module.__name__
    with TestClient(app) as client:  # o app sobe com as rotinas (desligadas nos testes)
        assert client.get("/api/health").json() == {"status": "ok"}
