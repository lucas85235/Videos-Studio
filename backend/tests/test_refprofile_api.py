"""Perfil de edição da referência no app (ADR 0004 §3.2): job `reference_profile`, rotas e `stats["profile"]`."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient
from refprofile_fixture import ffmpeg_available, make_video

from studio.api import routes_visual
from studio.api.app import create_app
from studio.core.editing.profile import EditProfile
from studio.db import session_scope
from studio.media import reference_video as rv
from studio.models import ReferenceVideo
from studio.workers.jobs import get_job, get_runner
from studio.workers.visual import references
from studio.workers.visual import tasks as visual_tasks

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg ausente")


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("refprofile-api") / "sintetico.mp4")


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def _reference(synthetic, ref_id: str = "ref_sint", status: str = "pronto") -> str:
    folder = rv.references_dir()
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(synthetic, folder / f"{ref_id}.mp4")
    with session_scope() as s:
        s.add(ReferenceVideo(id=ref_id, title="Sintético", path=f"library/references/{ref_id}.mp4", status=status,
                             stats={"shots": 4, "median_s": 1.4, "cuts_per_min": 27.7, "palette": ["#101010"]}))
    return ref_id


def test_profile_job_saves_the_profile_in_stats(synthetic):
    ref_id = _reference(synthetic)
    assert references.get_profile(ref_id) is None
    job = get_runner().submit("reference_profile", {"reference_id": ref_id, "deep": False}, run_inline=True)
    done = get_job(job["id"])
    assert done["status"] == "done", done["error"]
    assert done["result"]["quick"] is True and done["result"]["cuts_per_min"] > 0
    ref = references.get_reference(ref_id)
    # o perfil entra em stats["profile"] sem apagar as medidas antigas
    assert ref["stats"]["median_s"] == 1.4 and ref["stats"]["palette"] == ["#101010"]
    profile = EditProfile.model_validate(ref["stats"]["profile"])
    assert profile.refs == [ref_id] and profile.pace.micro_count == 1
    assert ref["stats"]["profile_notes"]["skipped"]["vision"] == "modo rápido"


def test_deep_profile_offline_uses_mock_vision(synthetic):
    ref_id = _reference(synthetic)
    out = references.profile_reference(ref_id, deep=True)
    profile = EditProfile.model_validate(out["stats"]["profile"])
    assert not profile.quick and profile.materials.mix
    assert out["stats"]["profile_notes"]["vision_provider"] == "mock_vision"


def test_reanalyzing_the_shots_keeps_the_profile(synthetic):
    ref_id = _reference(synthetic)
    references.profile_reference(ref_id)
    again = references.analyze_reference(ref_id)
    assert again["status"] == "pronto" and "profile" in again["stats"]


def test_profile_needs_the_video_file(synthetic):
    ref_id = _reference(synthetic, "ref_sem_arquivo")
    (rv.references_dir() / f"{ref_id}.mp4").unlink()
    with pytest.raises(rv.ReferenceVideoError):
        references.profile_reference(ref_id)
    with pytest.raises(LookupError):
        references.profile_reference("ref_nao_existe")


def test_profile_routes(synthetic, client, monkeypatch):
    submitted = []

    class Runner:
        def submit(self, kind, payload=None, project_id=None, **kw):
            submitted.append((kind, payload))
            return {"id": "job_x", "kind": kind}

    monkeypatch.setattr(routes_visual, "get_runner", lambda: Runner())
    ref_id = _reference(synthetic)
    _reference(synthetic, "ref_novo", status="novo")
    assert client.get(f"/api/references/{ref_id}/profile").json() == {"reference_id": ref_id, "profile": None,
                                                                       "notes": {}}
    r = client.post(f"/api/references/{ref_id}/profile", json={"deep": True})
    assert r.status_code == 200 and submitted == [("reference_profile", {"reference_id": ref_id, "deep": True})]
    assert client.post(f"/api/references/{ref_id}/profile").status_code == 200
    assert submitted[-1][1]["deep"] is False
    assert client.post("/api/references/ref_novo/profile").status_code == 409
    assert client.post("/api/references/nao_existe/profile").status_code == 404
    assert client.get("/api/references/nao_existe/profile").status_code == 404
    references.profile_reference(ref_id)
    body = client.get(f"/api/references/{ref_id}/profile").json()
    assert body["profile"]["pace"]["shots"] == 4 and body["notes"]["skipped"]


def test_shots_job_queues_the_quick_profile(synthetic, monkeypatch):
    ref_id = _reference(synthetic, status="novo")
    queued = []

    class Runner:
        def submit(self, kind, payload=None, project_id=None, **kw):
            queued.append((kind, payload))
            return {"id": "job_perfil"}

    monkeypatch.setattr(visual_tasks, "get_runner", lambda: Runner())

    class Ctx:
        def progress(self, f, m=""):
            pass

        def cancelled(self):
            return False

    out = visual_tasks.run_reference_shots(Ctx(), {"reference_id": ref_id})
    assert out["profile_job"] == "job_perfil"
    assert queued == [("reference_profile", {"reference_id": ref_id, "deep": False})]
    out = visual_tasks.run_reference_shots(Ctx(), {"reference_id": ref_id, "profile": False})
    assert "profile_job" not in out and len(queued) == 1


def test_recurring_thumbnails_are_deleted_with_the_reference(synthetic):
    ref_id = _reference(synthetic)
    thumb = rv.references_dir() / f"{ref_id}-rec-1.jpg"
    thumb.write_bytes(b"x")
    assert references.delete_reference(ref_id)
    assert not thumb.exists()
