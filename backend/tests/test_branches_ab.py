"""Ramos A/B de versões: commits no ramo, edição por instrução, comparação, prévia, publicação e escolha do vencedor."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.api.routes_branches import youtube_id
from studio.core.project import branches as br
from studio.core.project import service
from studio.core.project.document import Metadata, Scene, Segment, Visual
from studio.db import session_scope
from studio.models import Experiment, Publication
from studio.workers.analytics import ab_versions
from studio.workers.analytics.experiments import evaluate
from studio.workers.analytics.metrics import upsert_metric
from studio.workers.analytics.variables import load_videos
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import youtube as yt


def _run(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    return final["result"]


def _small_project(title: str = "Farol") -> str:
    """Projeto com 3 trechos (gancho + 2) e 1 cena por trecho, sem passar pelo LLM."""
    doc = service.create_project(title=title, style_id="misterios-short")
    segs = [Segment(id=f"seg_{i}", role="hook" if i == 1 else "body", text={"pt-BR": f"Texto {i}."}) for i in (1, 2, 3)]
    scenes = [Scene(id=f"scn_{i}", segment_id=f"seg_{i}", visual=Visual(prompt=f"imagem {i}")) for i in (1, 2, 3)]
    doc = doc.model_copy(update={"segments": segs, "scenes": scenes, "storyboard_approved": True,
                                 "metadata": {"pt-BR": Metadata(title="Farol A", description="Resumo.")}})
    service.commit(doc.id, doc, "pronto")
    return doc.id


def _set_text(project_id: str, branch: str, seg_id: str, text: str) -> int:
    doc = br.branch_doc(project_id, branch)
    doc.segment(seg_id).text["pt-BR"] = text
    return service.commit(project_id, doc, f"texto de {seg_id}", branch=branch)


# --------------------------------------------------------------------------------------------
# Ramos e commits
# --------------------------------------------------------------------------------------------


def test_branch_commits_do_not_move_the_main_line(isolated_settings):
    pid = _small_project()
    main_before = service.get_project_row(pid)["current_version"]
    b = br.create_branch(pid, note="gancho com pergunta")
    assert (b["letter"], b["name"], b["base"], b["status"]) == ("B", "b", main_before, "aberto")
    assert service.get_project_row(pid)["current_version"] == main_before

    v = _set_text(pid, "b", "seg_1", "E se o farol nunca tivesse apagado?")
    assert service.get_project_row(pid)["current_version"] == main_before  # a principal não mudou
    assert service.get_doc(pid).segment("seg_1").text["pt-BR"] == "Texto 1."
    assert br.branch_doc(pid, "b").segment("seg_1").text["pt-BR"].startswith("E se")
    assert br.get_branch(pid, "b")["head"] == v

    versions = {x["number"]: x for x in service.list_versions(pid)}
    assert versions[v]["branch"] == "b" and versions[v]["parent"] == b["head"]
    assert versions[b["head"]]["parent"] == main_before
    assert versions[main_before]["branch"] == "main"

    # Commit na principal continua apontando para a principal (não para a cabeça do ramo).
    doc = service.get_doc(pid)
    doc.title = "Farol 2"
    nv = service.commit(pid, doc, "título")
    assert {x["number"]: x for x in service.list_versions(pid)}[nv]["parent"] == main_before

    c = br.create_branch(pid, from_number=main_before)
    assert c["letter"] == "C" and c["base"] == main_before


def test_branch_from_unknown_version_fails(isolated_settings):
    pid = _small_project()
    with pytest.raises(service.ProjectNotFound):
        br.create_branch(pid, from_number=999)


# --------------------------------------------------------------------------------------------
# Comparação e escolha do vencedor
# --------------------------------------------------------------------------------------------


def test_compare_marks_changed_hook_and_title(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    _set_text(pid, "b", "seg_1", "Gancho novo.")
    doc = br.branch_doc(pid, "b")
    doc.scene("scn_1").visual.prompt = "farol na tempestade"
    doc.metadata["pt-BR"].title = "Farol B"
    service.commit(pid, doc, "título e imagem", branch="b")

    diff = br.compare(pid, "main", "b")
    by_id = {s["id"]: s for s in diff["segments"]}
    assert by_id["seg_1"]["status"] == "mudou"
    assert (by_id["seg_1"]["a"], by_id["seg_1"]["b"]) == ("Texto 1.", "Gancho novo.")
    assert by_id["seg_1"]["scene_changes"] == [{"id": "scn_1", "status": "mudou", "a": "imagem 1",
                                                "b": "farol na tempestade"}]
    assert by_id["seg_2"]["status"] == "igual"
    parts = {p["key"]: p for p in diff["parts"]}
    assert parts["titulo"]["changed"] and (parts["titulo"]["a"], parts["titulo"]["b"]) == ("Farol A", "Farol B")
    assert not parts["estilo"]["changed"]
    assert diff["changed"] == 2


def test_merge_takes_chosen_segments_and_closes_branch(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    _set_text(pid, "b", "seg_1", "Gancho B.")
    _set_text(pid, "b", "seg_3", "Final B.")
    v = br.merge(pid, "b", segment_ids=["seg_1"], parts=[])
    main = service.get_doc(pid)
    assert service.get_project_row(pid)["current_version"] == v
    assert main.segment("seg_1").text["pt-BR"] == "Gancho B."
    assert main.segment("seg_3").text["pt-BR"] == "Texto 3."  # não escolhido
    assert br.get_branch(pid, "b")["status"] == "mesclado"
    assert service.list_versions(pid)[0]["message"] == "Mesclou da versão B: trecho 1"
    with pytest.raises(service.BranchNotOpen):
        _set_text(pid, "b", "seg_2", "x")
    with pytest.raises(service.BranchNotOpen):
        br.merge(pid, "b", segment_ids=["seg_2"])


def test_merge_parts_inserts_new_and_removes_dropped_segments(isolated_settings):
    pid = _small_project()
    main = service.get_doc(pid)
    branch = main.model_copy(deep=True)
    # No ramo: trecho novo depois do 1 (com cena), trecho 3 apagado, cena do 2 reaproveita a do 3.
    branch.segments.insert(1, Segment(id="seg_new", text={"pt-BR": "Novo."}))
    branch.scenes.append(Scene(id="scn_new", segment_id="seg_new", visual=Visual(prompt="nova")))
    branch.segments = [s for s in branch.segments if s.id != "seg_3"]
    branch.scenes = [s for s in branch.scenes if s.segment_id != "seg_3"]
    main.scene("scn_2").visual.reuse_of = "scn_3"
    branch.style_overrides = {"audio": {"music": {"volume_db": -20}}}

    merged = br.merge_parts(main, branch, ["seg_new", "seg_3"], ["estilo"])
    assert [s.id for s in merged.segments] == ["seg_1", "seg_new", "seg_2"]
    assert [s.id for s in merged.ordered_scenes()] == ["scn_1", "scn_new", "scn_2"]
    assert merged.scene("scn_2").visual.reuse_of is None  # a cena reaproveitada saiu
    assert merged.style_overrides == {"audio": {"music": {"volume_db": -20}}}
    assert merged.title == main.title  # título não foi pedido
    with pytest.raises(ValueError):
        br.merge_parts(main, branch, ["seg_nenhum"], [])


def test_promote_makes_branch_the_main_and_carries_its_render(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    head = _set_text(pid, "b", "seg_1", "Gancho B.")
    br.save_result(pid, "b", "final", {"langs": {"pt-BR": {"video": "x.mp4"}}}, head)
    v = br.promote(pid, "b")
    assert service.get_doc(pid).segment("seg_1").text["pt-BR"] == "Gancho B."
    assert service.get_project_row(pid)["settings"]["last_final"] == {"langs": {"pt-BR": {"video": "x.mp4"}},
                                                                      "version": v}
    assert br.get_branch(pid, "b")["status"] == "promovido"
    assert service.list_versions(pid)[0]["message"] == f"Versão B virou a principal (era a v{head})"


def test_discard_and_delete_project(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    assert br.discard(pid, "b")["status"] == "descartado"
    assert not br.has_ab(pid)
    assert service.delete_project(pid)
    with session_scope() as s:
        from studio.models import ProjectBranch

        assert s.query(ProjectBranch).count() == 0


# --------------------------------------------------------------------------------------------
# API: edição por instrução, prévia dos dois lados e timeline do ramo
# --------------------------------------------------------------------------------------------


def test_api_edit_by_instruction_on_branch_and_build_both(isolated_settings):
    with TestClient(create_app()) as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "misterios-short", "topic": "o farol"})
        pid = created.json()["project"]["id"]
        _run("script", pid)
        _run("storyboard", pid)
        main_head = service.get_project_row(pid)["current_version"]

        b = client.post(f"/api/projects/{pid}/ab/branches", json={"note": "gancho mais escuro"})
        assert b.status_code == 200, b.text
        name = b.json()["name"]

        proposal = client.post(f"/api/projects/{pid}/ab/{name}/edit/propose",
                               json={"instruction": "deixa a cena 2 mais escura"})
        assert proposal.status_code == 200, proposal.text
        body = proposal.json()
        assert body["ops"] and body["base_version"] == b.json()["head"]
        applied = client.post(f"/api/projects/{pid}/ab/{name}/edit/apply",
                              json={"ops": body["ops"], "summary": body["summary"], "rebuild": False})
        assert applied.status_code == 200, applied.text
        assert service.get_project_row(pid)["current_version"] == main_head  # a principal ficou igual
        assert service.list_versions(pid)[0]["message"].startswith("Versão B: ")

        diff = client.get(f"/api/projects/{pid}/ab/compare", params={"b": name}).json()
        assert diff["changed"] >= 1 and any(s["scene_changes"] for s in diff["segments"])

        # Prévia das duas versões num job só: cada uma na própria pasta.
        built = _run("ab_build", pid, {"branches": ["main", name], "mode": "preview"})
        assert set(built["branches"]) == {"main", name}
        b_head = br.get_branch(pid, name)["head"]
        timeline_path = isolated_settings.outputs_dir / pid / "ramos" / name / "pt-BR" / "timeline.json"
        assert json.loads(timeline_path.read_text(encoding="utf-8"))["version"] == b_head
        main_tl = client.get(f"/api/projects/{pid}/timeline").json()
        assert main_tl["version"] == main_head
        b_tl = client.get(f"/api/projects/{pid}/ab/{name}/timeline")
        assert b_tl.status_code == 200 and b_tl.json()["version"] == b_head
        assert br.get_branch(pid, name)["last_preview"]["version"] == b_head
        assert service.get_project_row(pid)["settings"]["last_preview"]["version"] == main_head
        # A edição muda só a luz: a imagem da principal é reaproveitada na B.
        assert built["branches"][name]["generated"].get("image", 0) == 0

        overview = client.get(f"/api/projects/{pid}/ab").json()
        assert overview["main"]["letter"] == "A" and overview["branches"][0]["letter"] == "B"

        promoted = client.post(f"/api/projects/{pid}/ab/{name}/promote")
        assert promoted.status_code == 200, promoted.text
        assert client.post(f"/api/projects/{pid}/ab/{name}/discard").status_code == 409
        assert client.get(f"/api/projects/{pid}/ab/zz/timeline").status_code == 404


# --------------------------------------------------------------------------------------------
# Publicação e experimento de Desempenho
# --------------------------------------------------------------------------------------------


def _rendered_both(isolated_settings) -> str:
    pid = _small_project()
    br.create_branch(pid, note="gancho com pergunta")
    head = _set_text(pid, "b", "seg_1", "Pergunta?")
    for name, version in (("main", service.get_project_row(pid)["current_version"]), ("b", head)):
        out = isolated_settings.outputs_dir / pid / ("ramos/b" if name == "b" else "") / "pt-BR"
        out.mkdir(parents=True, exist_ok=True)
        (out / "video.mp4").write_bytes(name.encode())
        rel = (out / "video.mp4").relative_to(isolated_settings.data_dir).as_posix()
        br.save_result(pid, name, "final", {"langs": {"pt-BR": {"video": rel}}, "preview_only_providers": []}, version)
    return pid


def test_youtube_publish_of_both_versions_opens_experiment(isolated_settings, monkeypatch):
    pid = _rendered_both(isolated_settings)
    sent: list[tuple[str, dict]] = []

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None):
        sent.append((path.read_bytes().decode(), snippet))
        return {"id": f"vid{len(sent)}"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    _run("publish_youtube", pid, {"lang": "pt-BR", "privacy": "public"})
    with TestClient(create_app()) as client:
        blocked = client.post(f"/api/projects/{pid}/ab/b/publish/youtube", json={"privacy": "public"})
        assert blocked.status_code == 409 and "conferência" in blocked.json()["detail"]
    result = _run("publish_youtube", pid, {"lang": "pt-BR", "privacy": "private", "branch": "b"})

    assert [body for body, _ in sent] == ["main", "b"]  # cada versão sobe o próprio vídeo
    with session_scope() as s:
        pubs = {p.external_id: p.details for p in s.query(Publication).filter_by(project_id=pid).all()}
    assert pubs["vid1"]["versao"] == "A" and pubs["vid2"]["versao"] == "B"
    assert pubs["vid2"]["version"] == br.get_branch(pid, "b")["head"]

    # B privada não ganha views: o experimento não abre e o envio explica o porquê.
    assert br.get_branch(pid, "b")["experiment_id"] is None
    assert any("privada" in w for w in result["warnings"])
    assert "privada" in ab_versions.reading(pid)[0]["notice"]

    # A B ficou pública no YouTube Studio: a coleta traz números e a tela abre o experimento.
    upsert_metric("vid2", "2026-11-10", views=5, age_hours=10)
    reading = ab_versions.reading(pid)
    exp_id = br.get_branch(pid, "b")["experiment_id"]
    assert exp_id and reading[0]["notice"] is None
    with session_scope() as s:
        exp = s.get(Experiment, exp_id)
        assert (exp.variable, exp.variants, exp.project_ids) == ("versao", ["A", "B"], [pid])
        assert "gancho com pergunta" in exp.hypothesis
    assert ab_versions.sync_experiments(pid) == []  # não duplica

    videos = {v["youtube_id"]: v for v in load_videos()}
    assert videos["vid1"]["variables"]["versao"] == "A" and videos["vid2"]["variables"]["versao"] == "B"

    upsert_metric("vid1", "2026-11-10", views=100, age_hours=200)
    upsert_metric("vid2", "2026-11-11", views=180, age_hours=200)
    reading = ab_versions.reading(pid)
    assert reading[0]["direct"]["leader"] == "B" and reading[0]["b"]["views"] == 180
    assert reading[0]["direct"]["mature"]
    # 1 vídeo por versão: a regra de 3+ vídeos nunca fecharia; a leitura 1×1 fecha depois da idade mínima.
    with session_scope() as s:
        exp = s.get(Experiment, exp_id)
        assert exp.status == "concluido" and "Versão B na frente" in exp.result
        result = evaluate(exp)
    assert {g["variant"]: g["n"] for g in result["variants"]} == {"A": 1, "B": 1}
    assert reading[0]["experiment"]["evaluation"]["winner"] == "B"


def test_branch_publish_refuses_stale_render(isolated_settings, monkeypatch):
    pid = _rendered_both(isolated_settings)
    _set_text(pid, "b", "seg_2", "mudou depois do render")
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id: "global")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "branch": "b"}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final["status"] == "failed" and "mudou depois do último render" in final["error"]


def test_package_of_branch_and_manual_link(isolated_settings):
    pid = _rendered_both(isolated_settings)
    with TestClient(create_app()) as client:
        pkg = client.post(f"/api/projects/{pid}/ab/b/publish/package", json={"platforms": ["youtube"]})
        assert pkg.status_code == 200, pkg.text
        folder = pkg.json()["items"][0]["folder"]
        assert "ramos" in folder
        with session_scope() as s:
            pub = s.query(Publication).filter_by(project_id=pid).one()
            assert pub.details["versao"] == "B"

        a = client.post(f"/api/projects/{pid}/ab/link", json={"branch": "main", "video": "https://youtu.be/AAAAAAAAAAA"})
        assert a.status_code == 200 and a.json()["experiments_created"] == []
        b = client.post(f"/api/projects/{pid}/ab/link",
                        json={"branch": "b", "video": "https://www.youtube.com/shorts/BBBBBBBBBBB"})
        assert b.status_code == 200 and len(b.json()["experiments_created"]) == 1
        bad = client.post(f"/api/projects/{pid}/ab/link", json={"branch": "b", "video": "https://exemplo.com/x"})
        assert bad.status_code == 400
        overview = client.get(f"/api/projects/{pid}/ab").json()
        assert overview["published"][0]["experiment"]["variable"] == "versao"


def test_youtube_id_parser():
    assert youtube_id("https://youtu.be/abcDEF12345?si=x") == "abcDEF12345"
    assert youtube_id("youtube.com/watch?v=abcDEF12345&t=3") == "abcDEF12345"
    assert youtube_id("https://m.youtube.com/live/abcDEF12345") == "abcDEF12345"
    assert youtube_id("abcDEF12345") == "abcDEF12345"
    with pytest.raises(ValueError):
        youtube_id("https://vimeo.com/123456789")


def test_old_database_gets_branch_column(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    from studio.db import add_missing_columns

    engine = create_engine(f"sqlite:///{tmp_path / 'velho.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE project_versions (id INTEGER PRIMARY KEY, project_id VARCHAR(32), number INTEGER,"
                          " parent_number INTEGER, doc JSON, message TEXT, author VARCHAR(40), created_at DATETIME)"))
        conn.execute(text("INSERT INTO project_versions (project_id, number, doc, message, author)"
                          " VALUES ('prj_x', 1, '{}', 'm', 'user')"))
    added = add_missing_columns(engine)
    assert "project_versions.branch" in added
    with engine.begin() as conn:
        assert conn.execute(text("SELECT branch FROM project_versions")).scalar() == "main"
    assert "branch" in {c["name"] for c in inspect(engine).get_columns("project_versions")}


# --------------------------------------------------------------------------------------------
# Correções da revisão
# --------------------------------------------------------------------------------------------


def _youtube_pub(pid: str, video_id: str, details: dict, lang: str = "pt-BR") -> None:
    with session_scope() as s:
        s.add(Publication(id=f"pub_{video_id}", project_id=pid, platform="youtube", language=lang,
                          driver="youtube_native", status="uploaded", external_id=video_id,
                          url=f"https://youtu.be/{video_id}", details=details))


def test_main_published_before_branch_counts_as_version_a(isolated_settings):
    pid = _small_project()
    _youtube_pub(pid, "vidA", {"privacy": "public"})  # a A saiu antes de existir a B: sem a marca "versao"
    br.create_branch(pid)
    _youtube_pub(pid, "vidB", {"privacy": "public", "versao": "B", "branch": "b"})
    created = ab_versions.sync_experiments(pid)
    assert len(created) == 1 and br.get_branch(pid, "b")["experiment_id"] == created[0]


def test_experiment_waits_for_version_a(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    _youtube_pub(pid, "vidB", {"privacy": "public", "versao": "B", "branch": "b"})
    assert ab_versions.sync_experiments(pid) == []
    assert "Falta o vídeo da versão A" in ab_versions.reading(pid)[0]["notice"]


def test_direct_reading_compares_same_language_and_age_by_format():
    def video(lang: str, views: int, age: float, min_age: int = 168, public: bool = True) -> dict:
        return {"language": lang, "views": views, "age_hours": age, "min_age_h": min_age, "public": public}

    a = {"videos": [video("pt-BR", 100, 200), video("en", 900, 200)], "views": 1000}
    b = {"videos": [video("pt-BR", 120, 200)], "views": 120}
    direct = ab_versions.direct_reading(a, b, "B")
    assert direct["langs"] == ["pt-BR"]  # o inglês da A não entra na conta
    assert (direct["a_views"], direct["b_views"]) == (100, 120)
    assert direct["leader"] is None and "empate técnico" in direct["text"]  # 1,2x: menos que 1,3x

    young = ab_versions.direct_reading({"videos": [video("pt-BR", 100, 100)], "views": 100},
                                       {"videos": [video("pt-BR", 300, 100)], "views": 300}, "B")
    assert young["young"] and not young["mature"]  # longo: 100 h ainda é cedo (mínimo 168 h)
    private = ab_versions.direct_reading({"videos": [video("pt-BR", 100, 200)], "views": 100},
                                         {"videos": [video("pt-BR", 0, 200, public=False)], "views": 0}, "B")
    assert private["leader"] is None and private["langs"] == []


def test_compare_marks_changes_made_only_on_main_and_merge_refuses_them(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    _set_text(pid, "b", "seg_1", "Gancho B.")
    doc = service.get_doc(pid)  # a A muda o trecho 2 e o título depois que a B nasceu
    doc.segment("seg_2").text["pt-BR"] = "Texto 2 revisto na A."
    doc.metadata["pt-BR"].title = "Farol A2"
    service.commit(pid, doc, "revisão na A")

    diff = br.compare(pid, "main", "b")
    by_id = {s["id"]: s for s in diff["segments"]}
    assert by_id["seg_1"]["status"] == "mudou" and not by_id["seg_1"]["only_a"]
    assert by_id["seg_2"]["status"] == "mudou" and by_id["seg_2"]["only_a"]
    parts = {p["key"]: p for p in diff["parts"]}
    assert parts["titulo"]["only_a"] and not parts["titulo"]["changed"]

    with pytest.raises(ValueError, match="mudou só na A"):
        br.merge(pid, "b", segment_ids=["seg_2"])
    with pytest.raises(ValueError, match="mudou só na A"):
        br.merge(pid, "b", segment_ids=[], parts=["titulo"])
    br.merge(pid, "b", segment_ids=["seg_1"])
    main = service.get_doc(pid)
    assert main.segment("seg_1").text["pt-BR"] == "Gancho B."
    assert main.segment("seg_2").text["pt-BR"] == "Texto 2 revisto na A."  # a mudança da A ficou


def test_promote_moves_branch_outputs_to_main_folder(isolated_settings):
    from studio.workers.finalcheck.quick import load_context, quick_items

    pid = _small_project()
    service.update_settings(pid, {"thumbs": {"pt-BR": {"variants": [{"path": "a.jpg"}]}}})
    br.create_branch(pid)
    head = _set_text(pid, "b", "seg_1", "Gancho B.")
    out = isolated_settings.outputs_dir / pid / "ramos" / "b" / "pt-BR"
    out.mkdir(parents=True)
    (out / "timeline.json").write_text(json.dumps({"version": head, "scenes": [], "fps": 30}), encoding="utf-8")
    (out / "legendas.srt").write_text("1\n", encoding="utf-8")
    (out / "farol.mp4").write_bytes(b"video B")
    rel = lambda name: (out / name).relative_to(isolated_settings.data_dir).as_posix()  # noqa: E731
    entry = {"timeline": rel("timeline.json"), "srt": rel("legendas.srt"), "duration": 30.0}
    br.save_result(pid, "b", "preview", {"langs": {"pt-BR": entry}}, head)
    br.save_result(pid, "b", "final", {"langs": {"pt-BR": {**entry, "video": rel("farol.mp4")}}}, head)

    v = br.promote(pid, "b")
    settings = service.get_project_row(pid)["settings"]
    final = settings["last_final"]["langs"]["pt-BR"]
    assert final["video"] == f"outputs/{pid}/pt-BR/farol.mp4"
    assert settings["last_preview"]["langs"]["pt-BR"]["timeline"] == final["timeline"] == f"outputs/{pid}/pt-BR/timeline.json"
    assert (isolated_settings.data_dir / final["video"]).read_bytes() == b"video B"
    assert settings["thumbs"] == {}  # as miniaturas eram da A antiga
    with TestClient(create_app()) as client:
        assert client.get(f"/api/projects/{pid}/timeline").json()["version"] == v
    ctx = load_context(pid)
    assert ctx.version == v
    codes = {i["code"] for i in quick_items(ctx)}
    assert "final_version" not in codes and "final_video" not in codes


def test_cache_cleanup_keeps_what_open_branches_use(isolated_settings):
    from datetime import UTC, datetime, timedelta

    from studio.core.graph import gc
    from studio.models import Artifact

    pid = _small_project()
    br.create_branch(pid)
    head = br.get_branch(pid, "b")["head"]
    br.save_result(pid, "b", "preview", {"langs": {}, "artifacts": ["mix:bmix01"]}, head)
    old = datetime.now(UTC) - timedelta(days=3)
    folder = isolated_settings.cache_dir / "mix" / "bm" / "bmix01"
    folder.mkdir(parents=True)
    (folder / "mix.wav").write_bytes(b"x" * 100)
    with session_scope() as s:
        s.add(Artifact(kind="mix", input_hash="bmix01", status="done",
                       output={"files": ["cache/mix/bm/bmix01/mix.wav"]}, created_at=old, updated_at=old))

    report = gc.collect(dry_run=True, now=datetime.now(UTC) + timedelta(days=2))
    assert "mix" not in report.removed and report.kept_in_use >= 1
    br.discard(pid, "b")
    assert gc.collect(dry_run=True).removed.get("mix") == 1  # ramo descartado: a mixagem pode sair


def test_branch_publication_keeps_its_own_duration_and_style(isolated_settings):
    pid = _small_project()
    service.update_settings(pid, {"last_final": {"langs": {"pt-BR": {"duration": 58.0}}}})
    br.create_branch(pid)
    _youtube_pub(pid, "vidA", {"privacy": "public", "versao": "A", "branch": "main"})
    _youtube_pub(pid, "vidB", {"privacy": "public", "versao": "B", "branch": "b", "duration": 31.0,
                               "style_id": "estilo-da-b"})
    videos = {v["youtube_id"]: v for v in load_videos()}
    assert videos["vidA"]["duration_s"] == 58.0
    assert videos["vidB"]["duration_s"] == 31.0
    assert videos["vidB"]["variables"]["estilo"] == "estilo-da-b"


def test_branch_publication_tag_carries_duration(isolated_settings):
    pid = _small_project()
    br.create_branch(pid)
    head = br.get_branch(pid, "b")["head"]
    br.save_result(pid, "b", "final", {"langs": {"pt-BR": {"video": "v.mp4", "duration": 42.0}}}, head)
    tag = ab_versions.publication_tag(pid, "b", "pt-BR")
    assert tag["duration"] == 42.0 and tag["style_id"] == "misterios-short" and tag["versao"] == "B"


def test_build_waits_for_approved_scenes_and_link_needs_open_branch(isolated_settings):
    pid = _small_project()
    service.update_settings(pid, {"gates": {"storyboard": True}})
    br.create_branch(pid)
    doc = br.branch_doc(pid, "b")
    doc.storyboard_approved = False
    service.commit(pid, doc, "cenas mudaram", branch="b")
    with TestClient(create_app()) as client:
        blocked = client.post(f"/api/projects/{pid}/ab/build", json={"branches": ["main", "b"], "mode": "preview"})
        assert blocked.status_code == 409 and "Versão B" in blocked.json()["detail"]
        br.discard(pid, "b")
        link = client.post(f"/api/projects/{pid}/ab/link", json={"branch": "b", "video": "https://youtu.be/BBBBBBBBBBB"})
        assert link.status_code == 409
