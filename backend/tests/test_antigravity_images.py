"""Antigravity CLI: proporção, eventos do stream-json, imagem certa no `brain`, erros e o fluxo do provedor."""

from __future__ import annotations

import json
import subprocess
import time

import pytest
from PIL import Image

from studio.providers import image as image_mod
from studio.providers.base import NotConfigured, QuotaExceeded, Refused, TransientError
from studio.providers.image import (
    AntigravityCLIImage,
    agy_aspect,
    agy_conversation_ids,
    agy_error_text,
    agy_events,
    agy_instruction,
    agy_new_images,
)

MAIN = "491f2678-2938-4e49-bcc0-eae47bdee560"
SUB = "bb4489b0-31be-43fc-a227-0644793ff1db"
OTHER = "68523962-083e-4189-8bad-0ce8b78c63a2"


def _jpg(path, mtime=None, size=(1376, 768)):
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (200, 40, 160)).save(path, "JPEG")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


def _stream(response="", status="SUCCESS", main=MAIN, sub=SUB, errors=()):
    lines = [
        {"event": "init", "conversation_id": main, "init": {"cwd": "x"}},
        {"event": "step_update", "step_update": {"conversation_id": main, "step_index": 2, "step_type": "subagent",
                                                  "subagent_info": {"subagents": [{"conversation_id": sub}]}}},
    ]
    for message in errors:
        lines.append({"event": "step_update", "step_update": {"conversation_id": main, "state": "ERROR",
                                                               "tool_info": {"error": {"message": message}}}})
    lines.append({"event": "result", "result": {"conversation_id": main, "status": status, "response": response}})
    return "\n".join(json.dumps(line) for line in lines)


def test_aspect_is_the_closest_supported_ratio():
    assert agy_aspect(1920, 1080) == "16:9"
    assert agy_aspect(1080, 1920) == "9:16"
    assert agy_aspect(1024, 1024) == "1:1"
    assert agy_aspect(1280, 1024) == "5:4"
    assert agy_aspect(2560, 1080) == "21:9"


def test_conversation_ids_main_first_then_subagents():
    events = agy_events("lixo\n" + _stream())
    assert agy_conversation_ids(events) == [MAIN, SUB]
    assert agy_events("") == []


def test_image_cited_in_the_response_wins(tmp_path):
    started = time.time()
    cited = _jpg(tmp_path / MAIN / "vulto_1.jpg", started + 1)
    _jpg(tmp_path / OTHER / "outra_2.jpg", started + 5)  # outra conversa, mais nova
    events = agy_events(_stream(response=f"Pronto.\n`{cited}`"))
    assert agy_new_images(tmp_path, set(), started, events) == [cited]


def test_falls_back_to_our_conversation_folder(tmp_path):
    started = time.time()
    ours = _jpg(tmp_path / MAIN / "vulto_1.jpg", started + 1)
    _jpg(tmp_path / OTHER / "outra_2.jpg", started + 5)
    events = agy_events(_stream(response="Generating the image now."))
    assert agy_new_images(tmp_path, set(), started, events) == [ours]


def test_without_ids_takes_new_files_and_skips_old_ones(tmp_path):
    started = time.time()
    old = _jpg(tmp_path / "antiga" / "a.jpg", started - 60)
    new = _jpg(tmp_path / OTHER / "b.jpg", started + 1)
    _jpg(tmp_path / OTHER / ".system_generated" / "c.jpg", started + 1)
    assert agy_new_images(tmp_path, {old}, started, []) == [new]
    assert agy_new_images(tmp_path / "nao-existe", set(), started, []) == []


def test_error_text_leaves_the_prompt_out():
    stream = _stream(response="I could not generate the image.", errors=["RESOURCE_EXHAUSTED: quota"])
    text = agy_error_text(agy_events(stream))
    assert "RESOURCE_EXHAUSTED" in text and "could not generate" in text and "cwd" not in text


def test_instruction_keeps_the_prompt_verbatim_and_lists_references(tmp_path):
    ref = tmp_path / "ref1.jpg"
    text = agy_instruction("Neon boulevard; Palette: pink, cyan.", "16:9", [ref])
    assert "<prompt>Neon boulevard; Palette: pink, cyan.</prompt>" in text
    assert 'AspectRatio="16:9"' in text and f'ImagePaths=["{ref}"]' in text and "subagent" in text
    assert "ImagePaths" not in agy_instruction("x", "1:1", [])


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


@pytest.fixture
def agy(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    monkeypatch.setenv("VS_AGY_BRAIN", str(brain))
    monkeypatch.setattr(image_mod, "agy_executable", lambda: "agy.exe")
    calls = []

    def install(make_output):
        def fake_run(cmd, cwd=None, timeout=None, **_):
            calls.append(cmd)
            return make_output(brain, cmd)

        monkeypatch.setattr(image_mod, "run_tree", fake_run)
        return calls

    return install


def test_generate_fits_the_image_and_sends_references(agy, tmp_path):
    ref = _jpg(tmp_path / "personagem.jpg", size=(512, 768))

    def output(brain, cmd):
        prompt = cmd[cmd.index("-p") + 1]
        assert "ImagePaths=[" in prompt and "ref1.jpg" in prompt and 'AspectRatio="16:9"' in prompt
        made = _jpg(brain / MAIN / "vulto_1.jpg")
        return _Proc(_stream(response=str(made)))

    calls = agy(output)
    result = AntigravityCLIImage().generate(prompt="cena", width=1920, height=1080, seed=1, out_dir=tmp_path / "out",
                                            references=[ref])
    with Image.open(result.path) as im:
        assert im.size == (1920, 1080)
    assert result.model == "antigravity-nano-banana" and result.usage.units == 1
    assert calls[0][calls[0].index("--model") + 1] == image_mod.AGY_MODEL
    assert "--mode" in calls[0] and "accept-edits" in calls[0]


@pytest.mark.parametrize(("stream", "stderr", "error"), [
    (_stream(response="I could not do it.", errors=["429 RESOURCE_EXHAUSTED"]), "", QuotaExceeded),
    ("", "Error: Please sign in to view available models.", NotConfigured),
    (_stream(response="I can't help with that request due to safety policy."), "", Refused),
    (_stream(response="Done."), "", TransientError),
])
def test_failures_map_to_chain_errors(agy, tmp_path, stream, stderr, error):
    agy(lambda brain, cmd: _Proc(stream, stderr, 1))
    with pytest.raises(error):
        AntigravityCLIImage().generate(prompt="cena", width=1080, height=1920, seed=1, out_dir=tmp_path / "out")


def test_timeout_is_transient(agy, tmp_path):
    def output(brain, cmd):
        raise subprocess.TimeoutExpired(cmd, 1)

    agy(output)
    with pytest.raises(TransientError):
        AntigravityCLIImage().generate(prompt="cena", width=1024, height=1024, seed=1, out_dir=tmp_path / "out")


def test_unavailable_without_the_binary(monkeypatch):
    monkeypatch.setattr(image_mod, "agy_executable", lambda: None)
    ok, reason = AntigravityCLIImage().availability()
    assert not ok and "agy" in reason


def test_registered_after_codex_in_the_default_chain():
    from studio.core.styles.schema import ArtSpec
    from studio.providers import registry

    assert registry.DEFAULT_CHAINS["image"] == ["codex_cli", "antigravity_cli"]
    assert ArtSpec().image_chain == ["codex_cli", "antigravity_cli"]
    assert registry.get("antigravity_cli").info.accepts_references
