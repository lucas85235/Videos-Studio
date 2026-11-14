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


@pytest.mark.render
@pytest.mark.skipif(os.environ.get("VS_TEST_RENDER") != "1", reason="defina VS_TEST_RENDER=1 para renderizar de verdade")
def test_full_render_short(isolated_settings):
    project_id = _make_project()
    _run("script", project_id)
    _run("storyboard", project_id)
    final = _run("render", project_id)
    entry = final["langs"]["pt-BR"]
    video = isolated_settings.data_dir / entry["video"]
    assert video.exists()
    assert abs(duration_of(video) - entry["duration"]) < 0.6
    assert (isolated_settings.data_dir / entry["thumbnail"]).exists()
