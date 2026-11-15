"""Fundação da onda 2, qualidade e pesquisa (ADR 0003): as 8 tabelas novas, banco antigo e os 5 routers."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from studio.api.app import create_app
from studio.config import REPO_ROOT, Settings, set_settings
from studio.db import get_engine, init_db, reset_engine, session_scope
from studio.ids import new_id
from studio.models import (
    FactClaim,
    PageCapture,
    Project,
    QualityReport,
    RadarChannel,
    RadarNiche,
    RadarRun,
    RadarVideo,
    ReferenceVideo,
)

NEW_TABLES = {
    "radar_runs",
    "radar_niches",
    "radar_videos",
    "radar_channels",
    "fact_claims",
    "page_captures",
    "reference_videos",
    "quality_reports",
}


def test_new_tables_exist(isolated_settings):
    inspector = inspect(get_engine())
    assert NEW_TABLES <= set(inspector.get_table_names())
    columns = {c["name"]: c for c in inspector.get_columns("fact_claims")}
    assert columns["quote_found"]["nullable"] is True
    uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("radar_niches")}
    assert ("run_id", "niche", "market") in uniques
    uniques = {tuple(u["column_names"]) for u in inspector.get_unique_constraints("radar_videos")}
    assert ("run_id", "youtube_id", "niche", "market") in uniques
    fks = inspector.get_foreign_keys("quality_reports")
    assert fks[0]["referred_table"] == "projects" and fks[0]["options"].get("ondelete") == "CASCADE"


def test_old_database_gets_new_tables(tmp_path):
    data_dir = tmp_path / "antigo"
    data_dir.mkdir()
    # banco de antes desta onda: só uma tabela antiga, sem nenhuma das tabelas novas
    con = sqlite3.connect(data_dir / "studio.db")
    con.execute("CREATE TABLE app_settings (key VARCHAR(100) PRIMARY KEY, value JSON)")
    con.execute("INSERT INTO app_settings (key, value) VALUES ('radar', '{\"auto\": false}')")
    con.commit()
    con.close()

    set_settings(
        Settings(data_dir=data_dir, presets_dir=REPO_ROOT / "presets", offline=True, workers_enabled=False)
    )
    reset_engine()
    try:
        init_db()
        assert NEW_TABLES <= set(inspect(get_engine()).get_table_names())
        init_db()  # rodar de novo não muda nada
        assert NEW_TABLES <= set(inspect(get_engine()).get_table_names())
    finally:
        reset_engine()


def test_round_trip_of_each_table(isolated_settings):
    published = datetime(2026, 10, 1, 12, 30, tzinfo=UTC)
    with session_scope() as s:
        s.add(Project(id="prj_q", title="Projeto Q", style_id="x", settings={}))
        run = RadarRun(
            source="chave própria (YOUTUBE_API_KEY)",
            searches=33,
            units=3440,
            skipped=["cripto/br (falhou)"],
            params={"dias": 30, "mercados": ["us", "br"]},
        )
        s.add(run)
        s.flush()
        run_id = run.id
        s.add(
            RadarNiche(
                run_id=run_id, niche="misterio", market="us", n=120, h100=9, med=15400.5, new_share=0.25
            )
        )
        s.add(
            RadarVideo(
                run_id=run_id,
                youtube_id="abc123def45",
                niche="misterio",
                market="us",
                title="Título",
                channel_youtube_id="UC123",
                published_at=published,
                views=250000,
                duration_s=720,
            )
        )
        s.add(
            RadarChannel(
                youtube_id="UC123",
                name="Canal X",
                country="US",
                created_on="2026-03-01",
                recent={"verdict": "Modelar", "hits": 3},
            )
        )
        s.add(FactClaim(id="fat_1", project_id="prj_q", segment_id="seg_1", claim="A torre tem 300 m"))
        s.add(
            PageCapture(
                id="cap_1",
                url="https://exemplo.org/a",
                phrases=[{"text": "trecho", "rects": [[0.1, 0.2, 0.3, 0.04]], "view": None, "lock": False}],
            )
        )
        s.add(
            ReferenceVideo(
                id="ref_1",
                title="Trailer",
                shots=[{"k": 1, "a": 0.0, "b": 1.2, "dur": 1.2}],
                stats={"palette": ["#112233"]},
            )
        )
        s.add(
            QualityReport(
                project_id="prj_q",
                kind="audio",
                lang="pt-BR",
                status="aviso",
                items=[
                    {
                        "code": "music_distance",
                        "level": "aviso",
                        "title": "música alta",
                        "detail": "",
                        "at_s": None,
                        "scene_id": None,
                    }
                ],
                data={"chapters": []},
            )
        )

    with session_scope() as s:
        run = s.get(RadarRun, run_id)
        assert run.status == "ok" and run.error is None and run.videos == 0
        assert run.skipped == ["cripto/br (falhou)"] and run.params["mercados"] == ["us", "br"]
        niche = s.query(RadarNiche).filter_by(run_id=run_id).one()
        assert niche.h500 == 0 and niche.p75 == 0.0 and niche.med == 15400.5 and niche.new_share == 0.25
        video = s.query(RadarVideo).filter_by(run_id=run_id).one()
        assert video.published_at == published and video.views == 250000
        channel = s.get(RadarChannel, "UC123")
        assert channel.subscribers is None and channel.alert is None and channel.views == 0
        assert channel.recent == {"verdict": "Modelar", "hits": 3} and channel.updated_at is not None
        claim = s.get(FactClaim, "fat_1")
        assert claim.quote_found is None and claim.verdict == "sem_fonte" and claim.status == "aberta"
        assert claim.lang == "pt-BR" and claim.run_id is None and claim.checked_at is None
        claim.quote_found = False
        capture = s.get(PageCapture, "cap_1")
        assert capture.dark is False and capture.status == "ok" and capture.phrases[0]["rects"][0][2] == 0.3
        ref = s.get(ReferenceVideo, "ref_1")
        assert (
            ref.status == "novo"
            and ref.sheets == []
            and ref.info == {}
            and ref.stats["palette"] == ["#112233"]
        )
        report = s.query(QualityReport).filter_by(project_id="prj_q").one()
        assert (
            report.version == 0 and report.contact_sheet == "" and report.items[0]["code"] == "music_distance"
        )

    with session_scope() as s:
        assert s.get(FactClaim, "fat_1").quote_found is False  # Boolean falso não vira nulo


def test_project_rows_go_away_with_project(isolated_settings):
    with TestClient(create_app()) as client:
        with session_scope() as s:
            s.add(Project(id="prj_apagar", title="Apagar", style_id="x", settings={}))
        with session_scope() as s:
            s.add(FactClaim(id=new_id("fat"), project_id="prj_apagar", segment_id="seg_1", claim="x"))
            s.add(QualityReport(project_id="prj_apagar", kind="final", lang="pt-BR"))
        assert client.delete("/api/projects/prj_apagar").status_code == 200
    with session_scope() as s:
        assert s.query(FactClaim).filter_by(project_id="prj_apagar").count() == 0
        assert s.query(QualityReport).filter_by(project_id="prj_apagar").count() == 0


def test_new_routers_mounted(isolated_settings, monkeypatch):
    from studio.api import routes_audio, routes_checks, routes_factcheck, routes_radar, routes_visual

    included = []
    original = __import__("fastapi").FastAPI.include_router

    def spy(self, router, *args, **kwargs):
        included.append(router)
        return original(self, router, *args, **kwargs)

    monkeypatch.setattr("fastapi.FastAPI.include_router", spy)
    app = create_app()
    for module in (routes_radar, routes_factcheck, routes_audio, routes_checks, routes_visual):
        assert module.router.prefix == "/api"
        assert any(r is module.router for r in included), module.__name__
    with TestClient(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
