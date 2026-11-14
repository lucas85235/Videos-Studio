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
