"""Fumaça dos endpoints da gestão de canal (ADR 0002) e da qualidade e pesquisa (ADR 0003): cada rota nova
responde sem erro de servidor."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel


@pytest.fixture
def ctx():
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name="Canal", settings={}))
    doc = service.create_project(title="Vídeo", style_id="misterios-short", topic="tema", channel_id=cid)
    with TestClient(create_app()) as c:
        yield c, cid, doc.id


GETS = [
    "/api/analytics/overview",
    "/api/analytics/videos/abc123",
    "/api/analytics/variables",
    "/api/analytics/experiments",
    "/api/analytics/lessons",
    "/api/ideas",
    "/api/channels/{cid}/monetization",
    "/api/channels/{cid}/offers",
    "/api/projects/{pid}/affiliate",
    "/api/revenue",
    "/api/money/summary",
    "/api/today",
    "/api/pending",
    "/api/ops/velocity",
    "/api/ops/backups",
    "/api/ops/routines",
    "/api/projects/{pid}/shorts",
    # Onda 2 (ADR 0003)
    "/api/radar",
    "/api/radar/catalog",
    "/api/radar/runs",
    "/api/radar/runs/999999/panel",
    "/api/radar/report",
    "/api/projects/{pid}/factcheck",
    "/api/projects/{pid}/factcheck/sources.md",
    "/api/library/music/curation",
    "/api/library/music/spectro?track=nao-existe.mp3",
    "/api/sfx/guard",
    "/api/projects/{pid}/audio-check",
    "/api/projects/{pid}/preflight",
    "/api/visual/capabilities",
    "/api/projects/{pid}/captures",
    "/api/captures/nao-existe",
    "/api/references",
    "/api/references/nao-existe",
    "/api/projects/{pid}/thumbs",
]


@pytest.mark.parametrize("path", GETS)
def test_gets_respondem(ctx, path):
    c, cid, pid = ctx
    r = c.get(path.format(cid=cid, pid=pid))
    assert r.status_code < 500, (path, r.status_code, r.text)
    assert r.status_code in (200, 404), (path, r.status_code, r.text)


def test_gets_principais_dao_200(ctx):
    c, cid, pid = ctx
    for path in ("/api/analytics/overview", "/api/ideas", "/api/money/summary", "/api/today", "/api/pending",
                 "/api/ops/velocity", "/api/ops/routines", f"/api/channels/{cid}/monetization",
                 f"/api/projects/{pid}/shorts", "/api/analytics/experiments", "/api/analytics/lessons"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text)


def test_gets_onda2_principais_dao_200(ctx):
    c, cid, pid = ctx
    for path in ("/api/radar", "/api/radar/catalog", "/api/radar/runs", "/api/radar/report",
                 f"/api/projects/{pid}/factcheck", f"/api/projects/{pid}/factcheck/sources.md",
                 "/api/library/music/curation", "/api/sfx/guard", f"/api/projects/{pid}/audio-check",
                 f"/api/projects/{pid}/preflight", "/api/visual/capabilities", f"/api/projects/{pid}/captures",
                 "/api/references", f"/api/projects/{pid}/thumbs"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.text)
