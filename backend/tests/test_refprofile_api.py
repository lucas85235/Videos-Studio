"""Perfil de edição da referência no app (ADR 0004 §3.2): job `reference_profile`, rotas e `stats["profile"]`."""

from __future__ import annotations

import os
import shutil
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from refprofile_fixture import ffmpeg_available, make_video

from studio.api.app import create_app
from studio.core.editing.profile import EditProfile
from studio.db import session_scope
from studio.media import reference_video as rv
from studio.media import refprofile
from studio.models import Job, ReferenceVideo
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
    assert again["stats"]["profile_notes"]["file_key"] == refprofile.file_key(rv.references_dir() / f"{ref_id}.mp4")


def test_profile_of_another_file_is_dropped_on_reanalysis(synthetic):
    ref_id = _reference(synthetic)
    references.profile_reference(ref_id)
    video = rv.references_dir() / f"{ref_id}.mp4"
    later = video.stat().st_mtime + 60
    os.utime(video, (later, later))  # o arquivo foi baixado de novo: outra chave
    again = references.analyze_reference(ref_id)
    assert "profile" not in again["stats"] and "profile_notes" not in again["stats"]


class _Ctx:
    def __init__(self, cancel: bool = False) -> None:
        self.cancel = cancel

    def progress(self, f, m=""):
        pass

    def cancelled(self):
        return self.cancel


def test_deep_profile_survives_reanalysis_and_quick_requests(synthetic, monkeypatch):
    ref_id = _reference(synthetic)
    references.profile_reference(ref_id, deep=True)
    queued = []

    class Runner:
        def submit(self, kind, payload=None, project_id=None, **kw):
            queued.append((kind, payload))
            return {"id": "job_perfil"}

    monkeypatch.setattr(visual_tasks, "get_runner", lambda: Runner())
    out = visual_tasks.run_reference_shots(_Ctx(), {"reference_id": ref_id})
    assert "profile_job" not in out and queued == []  # perfil do mesmo arquivo: nada a refazer
    ref = references.get_reference(ref_id)
    assert ref["stats"]["profile"]["quick"] is False
    # um pedido rápido (o automático, ou o usuário) não rebaixa o profundo do mesmo arquivo
    after = references.profile_reference(ref_id, deep=False)
    assert after["stats"]["profile"]["quick"] is False
    assert after["stats"]["profile"]["materials"]["mix"]


def test_cancelled_profile_job_writes_nothing(synthetic):
    ref_id = _reference(synthetic)
    with pytest.raises(refprofile.Cancelled):
        references.profile_reference(ref_id, cancelled=lambda: True)
    assert references.get_profile(ref_id) is None
    out = visual_tasks.run_reference_profile(_Ctx(cancel=True), {"reference_id": ref_id, "deep": False})
    assert out == {"reference_id": ref_id, "canceled": True} and references.get_profile(ref_id) is None


def _active_job(ref_id: str, deep: bool) -> str:
    with session_scope() as s:
        s.add(Job(id=f"job_{ref_id}_{int(deep)}", kind="reference_profile", status="running",
                  payload={"reference_id": ref_id, "deep": deep}, message="Executando"))
    return f"job_{ref_id}_{int(deep)}"


def test_profile_route_reuses_the_active_job(synthetic, client, monkeypatch):
    submitted = []

    class Runner:
        def submit(self, kind, payload=None, project_id=None, **kw):
            submitted.append((kind, payload))
            return {"id": "job_novo", "kind": kind}

    monkeypatch.setattr(visual_tasks, "get_runner", lambda: Runner())
    ref_id = _reference(synthetic)
    quick_id = _active_job(ref_id, deep=False)
    r = client.post(f"/api/references/{ref_id}/profile", json={"deep": False})
    assert r.status_code == 200 and r.json()["id"] == quick_id and submitted == []
    r = client.post(f"/api/references/{ref_id}/profile", json={"deep": True})
    assert r.status_code == 409 and "rápida" in r.json()["detail"]
    deep_ref = _reference(synthetic, "ref_profundo")
    deep_id = _active_job(deep_ref, deep=True)
    r = client.post(f"/api/references/{deep_ref}/profile", json={"deep": False})
    assert r.status_code == 200 and r.json()["id"] == deep_id  # o profundo já cobre o pedido rápido
    # o automático do reference_shots também não enfileira outro
    out = visual_tasks.run_reference_shots(_Ctx(), {"reference_id": ref_id})
    assert out["profile_job"] == quick_id and submitted == []


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

    monkeypatch.setattr(visual_tasks, "get_runner", lambda: Runner())
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


def test_profile_cache_is_deleted_with_the_reference(synthetic):
    ref_id = _reference(synthetic)
    references.profile_reference(ref_id, deep=True)
    cache = refprofile.cache_dir_for(rv.references_dir() / f"{ref_id}.mp4")
    assert (cache / "base.json").is_file() and any((cache / "strips").iterdir())
    assert references.delete_reference(ref_id)
    assert not cache.exists()


def test_gc_sweeps_orphan_profile_caches_as_a_unit(synthetic):
    from studio.config import get_settings
    from studio.core.graph import gc

    ref_id = _reference(synthetic)
    references.profile_reference(ref_id)
    live = refprofile.cache_dir_for(rv.references_dir() / f"{ref_id}.mp4")
    root = get_settings().cache_dir / "refprofile"
    orphan = root / "0123456789abcdef0123"  # medida de um render, ou referência apagada
    (orphan / "strips").mkdir(parents=True)
    (orphan / "base.json").write_text("{}", encoding="utf-8")
    (orphan / "speech-auto.json").write_text("{}", encoding="utf-8")
    (orphan / "strips" / "strip-1.jpg").write_bytes(b"x")
    (root / "vision").mkdir(parents=True, exist_ok=True)
    (root / "vision" / "abc.json").write_text("{}", encoding="utf-8")
    later = datetime.now(UTC) + timedelta(days=2)
    report = gc.collect(dry_run=False, now=later)
    assert not orphan.exists()  # a pasta inteira, com os arquivos soltos
    assert (live / "base.json").is_file()  # a da referência da biblioteca fica
    assert (root / "vision" / "abc.json").is_file()  # as respostas da visão ficam
    assert report.freed.get("orphan", 0) > 0
