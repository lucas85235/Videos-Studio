from __future__ import annotations

from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.workers.jobs import get_job, get_runner


def _client() -> TestClient:
    return TestClient(create_app())


def _inline(kind: str, project_id: str) -> None:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]


def test_styles_and_meta():
    with _client() as client:
        styles = client.get("/api/styles").json()
        ids = {item["style"]["id"] for item in styles}
        assert "misterios-short" in ids
        meta = client.get("/api/styles/meta").json()
        assert any(f["id"] == "motion_graphics" and f["implemented"] for f in meta["families"])
        dup = client.post("/api/styles/misterios-short/duplicate", json={"new_id": "meu-misterio", "name": "Meu mistério"})
        assert dup.status_code == 200, dup.text
        bad = client.post("/api/styles", json={"style": {**dup.json()["style"], "id": "x-bad", "edit": {"camera": {"voar": 1}}}})
        assert bad.status_code == 422
        from_text = client.post(
            "/api/styles/from-text",
            json={"description": "documentário de jornal antigo, lento, sépia, máquina de escrever", "base_style_id": "documentario-sombrio"},
        )
        assert from_text.status_code == 200, from_text.text
        data = from_text.json()
        assert data["style"]["edit"]["text_cards"]["animation"] == "typewriter"
        assert data["applied"]


def test_project_flow_and_edit_agent():
    with _client() as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "misterios-short", "topic": "o farol"})
        assert created.status_code == 200, created.text
        project_id = created.json()["project"]["id"]
        _inline("script", project_id)
        _inline("storyboard", project_id)
        detail = client.get(f"/api/projects/{project_id}").json()
        scenes = detail["doc"]["scenes"]
        assert scenes and detail["versions"][0]["number"] == 3

        proposal = client.post(f"/api/projects/{project_id}/edit/propose", json={"instruction": "deixa a cena 2 mais escura e a legenda maior"})
        assert proposal.status_code == 200, proposal.text
        body = proposal.json()
        kinds = [op["op"] for op in body["ops"]]
        assert "set_scene_look" in kinds and "set_style" in kinds
        assert body["estimate"]["images_missing"] is not None

        applied = client.post(f"/api/projects/{project_id}/edit/apply", json={"ops": body["ops"], "summary": body["summary"], "rebuild": False})
        assert applied.status_code == 200, applied.text
        doc = client.get(f"/api/projects/{project_id}").json()["doc"]
        assert doc["style_overrides"]["edit"]["captions"]["size"] > 84

        undo = client.post(f"/api/projects/{project_id}/checkout", json={"number": 3})
        assert undo.status_code == 200
        assert client.get(f"/api/projects/{project_id}").json()["doc"]["style_overrides"] == {}

        bad = client.post(f"/api/projects/{project_id}/ops", json={"ops": [{"op": "regenerate_scene", "scene_id": "nao_existe"}]})
        assert bad.status_code == 422


class _InlineRunner:
    """Roda o job enviado pela rota na hora (sem a fila em segundo plano)."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def submit(self, kind, payload=None, project_id=None, **_kw):
        job = get_runner().submit(kind, payload, project_id, run_inline=True)
        self.jobs.append(job)
        return job


def test_approve_storyboard_continues_to_preview(monkeypatch):
    from studio.api import routes_projects

    runner = _InlineRunner()
    monkeypatch.setattr(routes_projects, "get_runner", lambda: runner)
    with _client() as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "misterios-short", "topic": "o farol"})
        project_id = created.json()["project"]["id"]
        assert created.json()["project"]["settings"]["gates"]["storyboard"] is True
        empty = client.post(f"/api/projects/{project_id}/approve", json={"stage": "storyboard"})
        assert empty.status_code == 400 and empty.json()["detail"] == "não há cenas para aprovar"
        bad = client.post(f"/api/projects/{project_id}/approve", json={"stage": "cenas"})
        assert bad.status_code == 400 and "script, storyboard ou preview" in bad.json()["detail"]

        _inline("script", project_id)
        # aprovar o roteiro roda as cenas e para no portão, antes de qualquer imagem
        approved = client.post(f"/api/projects/{project_id}/approve", json={"stage": "script"})
        assert approved.status_code == 200, approved.text
        stopped = get_job(runner.jobs[-1]["id"])
        assert stopped["result"]["stopped_at"] == "storyboard"
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["doc"]["storyboard_approved"] is False and detail["doc"]["art"]["genre"]
        assert detail["project"]["settings"]["storyboard_review"]["provider"] == "mock_llm"

        approved = client.post(f"/api/projects/{project_id}/approve", json={"stage": "storyboard"})
        assert approved.status_code == 200, approved.text
        body = approved.json()
        job = get_job(body["job"]["id"])
        assert job["status"] == "done", job["error"]
        assert job["result"]["stopped_at"] == "preview" and job["result"]["steps"] == ["preview"]
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["doc"]["storyboard_approved"] is True
        assert any(v["message"] == "Cenas aprovadas" and v["number"] == body["version"] for v in detail["versions"])

        # refazer as cenas usa o job que já existe e desmarca a aprovação
        redo = client.post(f"/api/projects/{project_id}/jobs", json={"kind": "storyboard", "payload": {}})
        assert redo.status_code == 200
        assert get_job(redo.json()["id"])["status"] == "done"
        assert client.get(f"/api/projects/{project_id}").json()["doc"]["storyboard_approved"] is False


def test_edits_wait_for_scene_check_and_style_switch_waits_for_new_scenes(monkeypatch):
    from studio.api import routes_projects
    from studio.workers.pipeline.build import DIRECTION_MISSING

    runner = _InlineRunner()
    monkeypatch.setattr(routes_projects, "get_runner", lambda: runner)
    with _client() as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "misterios-short", "topic": "o farol"})
        project_id = created.json()["project"]["id"]
        _inline("script", project_id)
        _inline("storyboard", project_id)
        doc = client.get(f"/api/projects/{project_id}").json()["doc"]
        look = [{"op": "set_scene_look", "scene_id": doc["scenes"][1]["id"], "brightness": 0.2}]
        # cenas esperando a conferência: a edição fica salva e a prévia não roda (não gasta imagens)
        applied = client.post(f"/api/projects/{project_id}/edit/apply", json={"ops": look, "summary": "mais claro"})
        assert applied.status_code == 200, applied.text
        assert applied.json()["job"] is None and "Aprove as cenas" in applied.json()["notice"]
        ops = client.post(f"/api/projects/{project_id}/ops", json={"ops": look, "rebuild": True}).json()
        assert ops["job"] is None and ops["notice"] and not runner.jobs
        # cenas aprovadas: a edição atualiza a prévia
        client.post(f"/api/projects/{project_id}/approve", json={"stage": "storyboard", "continue_pipeline": False})
        assert client.get(f"/api/projects/{project_id}").json()["project"]["status"] == "storyboard"
        ops = client.post(f"/api/projects/{project_id}/ops", json={"ops": look, "rebuild": True}).json()
        assert ops["notice"] is None and get_job(ops["job"]["id"])["status"] == "done"
        # trocar o estilo apaga a direção: a prévia espera as cenas novas
        switch = [{"op": "set_style_id", "style_id": "games-cultura-pop-short"}]
        switched = client.post(f"/api/projects/{project_id}/ops", json={"ops": switch, "rebuild": True}).json()
        assert switched["job"] is None and switched["notice"] == DIRECTION_MISSING
        estimate = client.get(f"/api/projects/{project_id}/plan?mode=preview").json()
        assert estimate["images_missing"] == [] and estimate["direction_missing"]
        job = get_runner().submit("preview", {}, project_id, run_inline=True)
        failed = get_job(job["id"])
        assert failed["status"] == "failed" and "refaça as cenas" in failed["error"]


def test_scene_gate_on_old_project_and_preview_approval_keep_scenes_approved(monkeypatch):
    from studio.api import routes_projects
    from studio.core.project import service

    monkeypatch.setattr(routes_projects, "get_runner", lambda: _InlineRunner())
    with _client() as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "misterios-short", "topic": "o farol"})
        project_id = created.json()["project"]["id"]
        # projeto de antes da curadoria: sem a chave da parada das cenas, com cenas e prévia
        service.update_settings(project_id, {"gates": {"script": True, "preview": True, "publish": True}})
        _inline("script", project_id)
        _inline("storyboard", project_id)
        fresh = client.patch(f"/api/projects/{project_id}/settings", json={"gates": {"storyboard": True}})
        assert fresh.status_code == 200
        assert client.get(f"/api/projects/{project_id}").json()["doc"]["storyboard_approved"] is False  # sem prévia
        client.patch(f"/api/projects/{project_id}/settings", json={"gates": {"storyboard": False}})
        service.update_settings(project_id, {"last_preview": {"mode": "preview"}})
        on = client.patch(f"/api/projects/{project_id}/settings", json={"gates": {"storyboard": True}})
        assert on.json()["gates"]["storyboard"] is True
        detail = client.get(f"/api/projects/{project_id}").json()
        assert detail["doc"]["storyboard_approved"] is True and detail["versions"][0]["message"] == "Cenas aprovadas"

        # aprovar a prévia aprova as cenas que estão nela
        _inline("storyboard", project_id)
        assert client.get(f"/api/projects/{project_id}").json()["doc"]["storyboard_approved"] is False
        approved = client.post(f"/api/projects/{project_id}/approve", json={"stage": "preview", "continue_pipeline": False})
        assert approved.status_code == 200
        doc = client.get(f"/api/projects/{project_id}").json()["doc"]
        assert doc["preview_approved"] is True and doc["storyboard_approved"] is True


def test_providers_and_files_security(isolated_settings):
    with _client() as client:
        providers = client.get("/api/providers").json()
        assert any(p["id"] == "gemini_tts" for p in providers)
        assert client.get("/files/%2e%2e/%2e%2e/Windows/win.ini").status_code == 404
        (isolated_settings.data_dir / "x.txt").write_text("oi", encoding="utf-8")
        assert client.get("/files/x.txt").text == "oi"
        assert client.get("/files/studio.db").status_code == 403
        status = client.get("/api/status").json()
        assert "renderer_version" in status
