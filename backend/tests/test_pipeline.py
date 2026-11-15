from __future__ import annotations

import json
import os

import pytest

from studio.core.project import service
from studio.core.project.ops import apply_ops, parse_ops
from studio.core.styles.library import get_style
from studio.media.ffmpeg import duration_of
from studio.workers.jobs import get_runner


def _make_project(style_id: str = "misterios-short", langs: list[str] | None = None) -> str:
    doc = service.create_project(title="Teste", style_id=style_id, topic="o farol abandonado da ilha", languages=langs)
    return doc.id


def _run(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    from studio.workers.jobs import get_job

    final = get_job(job["id"])
    assert final is not None
    assert final["status"] == "done", final["error"]
    return final["result"]


def test_script_storyboard_preview_offline(isolated_settings):
    project_id = _make_project()
    result = _run("script", project_id)
    assert result["segments"] >= 3
    result = _run("storyboard", project_id)
    assert result["scenes"] >= result.get("scenes", 0) > 0
    preview = _run("preview", project_id)
    entry = preview["langs"]["pt-BR"]
    timeline = json.loads((isolated_settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    assert timeline["durationInFrames"] > 30 * 20
    assert len(timeline["scenes"]) == entry["scenes"]
    # cenas contínuas, sem buracos nem sobreposição
    for a, b in zip(timeline["scenes"], timeline["scenes"][1:], strict=False):
        assert a["from"] + a["durationInFrames"] == b["from"]
    assert timeline["scenes"][-1]["from"] + timeline["scenes"][-1]["durationInFrames"] == timeline["durationInFrames"]
    assert any(scene["captions"] for scene in timeline["scenes"])
    mix = isolated_settings.data_dir / entry["mix"]
    assert mix.exists() and abs(duration_of(mix) - timeline["duration"]) < 0.5
    assert "mock_tts" in preview["preview_only_providers"]

    # Segunda prévia sem mudanças: tudo reaproveitado
    again = _run("preview", project_id)
    assert again["generated"].get("tts", 0) == 0
    assert again["generated"].get("image", 0) == 0

    # Edição incremental: regenerar uma cena e escurecer outra só gera 1 imagem nova
    doc = service.get_doc(project_id)
    scenes = doc.ordered_scenes()
    ops = parse_ops(
        [
            {"op": "regenerate_scene", "scene_id": scenes[1].id},
            {"op": "set_scene_look", "scene_id": scenes[2].id, "brightness": -0.3},
        ]
    )
    service.commit(project_id, apply_ops(doc, ops, get_style(doc.style_id)), "edição de teste")
    edited = _run("preview", project_id)
    assert edited["generated"].get("image", 0) == 1
    assert edited["generated"].get("tts", 0) == 0


def test_translate_and_multilanguage_preview(isolated_settings):
    project_id = _make_project(langs=["pt-BR", "en-US"])
    _run("script", project_id)
    _run("storyboard", project_id)
    _run("translate", project_id)
    doc = service.get_doc(project_id)
    assert all(seg.text_for("en-US").startswith("[en-US]") for seg in doc.segments)
    preview = _run("preview", project_id)
    assert set(preview["langs"]) == {"pt-BR", "en-US"}


def test_autopilot_stops_at_script_gate(isolated_settings):
    project_id = _make_project()
    result = _run("autopilot", project_id)
    assert result["stopped_at"] == "script"


def test_autopilot_stops_at_storyboard_gate(isolated_settings):
    project_id = _make_project()
    assert service.get_project_row(project_id)["settings"]["gates"]["storyboard"] is True
    _run("script", project_id)
    result = _run("autopilot", project_id)
    assert result["stopped_at"] == "storyboard" and result["steps"] == ["storyboard"]
    assert result["message"] == "Cenas aguardando sua aprovação"
    row = service.get_project_row(project_id)
    assert "last_preview" not in row["settings"]  # nenhuma imagem gerada antes da aprovação
    assert row["settings"]["storyboard_review"]["counts"]["scenes"] > 0
    doc = service.get_doc(project_id)
    assert doc.scenes and not doc.storyboard_approved
    assert row["status"] == "storyboard_review"  # a lista de projetos mostra "Cenas para conferir"
    # com as cenas aprovadas, o piloto segue para a prévia sem refazer as cenas
    doc.storyboard_approved = True
    service.commit(project_id, doc, "Cenas aprovadas")
    result = _run("autopilot", project_id)
    assert result["steps"] == ["preview"] and result["stopped_at"] == "preview"


def test_old_project_without_gate_key_runs_straight_to_preview(isolated_settings):
    project_id = _make_project()
    service.update_settings(project_id, {"gates": {"script": True, "preview": True, "publish": True}})
    _run("script", project_id)
    result = _run("autopilot", project_id)
    assert result["steps"] == ["storyboard", "preview"] and result["stopped_at"] == "preview"


def test_commit_syncs_style_id(isolated_settings):
    project_id = _make_project()
    doc = service.get_doc(project_id)
    doc.style_id = "games-cultura-pop-short"
    service.commit(project_id, doc, "Estilo trocado")
    assert service.get_project_row(project_id)["style_id"] == "games-cultura-pop-short"


def test_autopilot_translates_screen_texts_made_after_the_translation(isolated_settings):
    # Traduziu antes das cenas: os cartões criados depois ficam só em português. O piloto traduz só eles.
    project_id = _make_project(langs=["pt-BR", "en-US"])
    service.update_settings(project_id, {"gates": {"script": True, "preview": True, "publish": True}})
    _run("script", project_id)
    _run("translate", project_id)
    _run("storyboard", project_id)
    from studio.workers.pipeline import authoring

    doc = service.get_doc(project_id)
    narration = {seg.id: seg.text_for("en-US") for seg in doc.segments}
    assert authoring.missing_screen_texts(doc, "en-US") and authoring.needs_translation(doc, "en-US")
    result = _run("autopilot", project_id)
    assert result["steps"] == ["translate", "preview"]
    doc = service.get_doc(project_id)
    assert not authoring.needs_translation(doc, "en-US")
    assert {seg.id: seg.text_for("en-US") for seg in doc.segments} == narration  # a narração não foi refeita
    hook = doc.ordered_scenes()[0].card
    assert hook.headline["en-US"].startswith("[en-US] ")
    messages = [v["message"] for v in service.list_versions(project_id)]
    assert "Tradução dos textos de tela: en-US" in messages


@pytest.mark.render
@pytest.mark.skipif(os.environ.get("VS_TEST_RENDER") != "1", reason="defina VS_TEST_RENDER=1 para renderizar de verdade")
def test_full_render_short(isolated_settings):
    from studio.core.project.document import CardItem, SceneCard

    project_id = _make_project()
    _run("script", project_id)
    _run("storyboard", project_id)
    # Cartões de dado no render de verdade: um stat e um compare sobre imagens reaproveitadas.
    doc = service.get_doc(project_id)
    scenes = doc.ordered_scenes()
    scenes[1].kind = "stat"
    scenes[1].card = SceneCard(value={"pt-BR": "200 MI"}, label={"pt-BR": "cópias vendidas"}, source="Teste")
    scenes[2].kind = "compare"
    scenes[2].card = SceneCard(
        items=[CardItem(label={"pt-BR": "Antes"}, value={"pt-BR": "90 MI"}),
               CardItem(label={"pt-BR": "Depois"}, value={"pt-BR": "475 MI"})],
        highlight=1, headline={"pt-BR": "RECORDE"},
    )  # fmt: skip
    doc.storyboard_approved = True
    service.commit(project_id, doc, "Cartões de teste")
    final = _run("render", project_id)
    entry = final["langs"]["pt-BR"]
    video = isolated_settings.data_dir / entry["video"]
    assert video.exists()
    assert abs(duration_of(video) - entry["duration"]) < 0.6
    assert (isolated_settings.data_dir / entry["thumbnail"]).exists()
