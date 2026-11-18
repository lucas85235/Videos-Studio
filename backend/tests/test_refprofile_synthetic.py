"""Análise da referência num vídeo sintético `lavfi` (ADR 0004 §3.2): cortes, micro-plano, punch, SFX e o perfil."""

from __future__ import annotations

from pathlib import Path

import pytest
from refprofile_fixture import CUTS, DURATION, FPS, ONSET_S, PUNCH_S, ffmpeg_available, make_video

from studio.core.editing.profile import EditFingerprint, EditProfile
from studio.media import refprofile
from studio.media.refprofile import audio_events, cuts, motion
from studio.providers.vision import MockVision

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg ausente")


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("refprofile") / "sintetico.mp4")


def _base(video: Path) -> dict:
    return refprofile.measure_base(video)


def test_cuts_within_one_frame_and_the_micro_shot_is_kept(video):
    base = _base(video)
    found = base["cuts"]
    assert len(found) == len(CUTS), found
    for got, want in zip(sorted(found), CUTS, strict=True):
        assert abs(got - want) <= 1 / FPS + 1e-6
    micro = [s for s in base["shots"] if s["micro"]]
    assert len(micro) == 1
    assert abs(micro[0]["a"] - 2.3) <= 1 / FPS and abs(micro[0]["dur"] - 0.3) <= 1 / FPS
    pace = cuts.pace(base["shots"], DURATION)
    assert pace["micro_count"] == 1 and pace["shots"] == 4


def test_punch_is_detected_and_is_not_a_cut(video):
    base = _base(video)
    assert not any(abs(t - PUNCH_S) < 0.2 for t in base["cuts"])
    punches = [p for p in base["punches"] if abs(p["t"] - PUNCH_S) <= 0.3]
    assert len(punches) == 1, base["punches"]
    assert 0.15 <= punches[0]["scale"] <= 0.35
    assert len(base["punches"]) == 1


def test_sfx_onset_within_60ms_and_away_from_the_cuts(video):
    base = _base(video)
    onsets = base["audio"]["onsets"]
    near = [o for o in onsets if abs(o["t"] - ONSET_S) <= 0.06]
    assert len(near) == 1, onsets
    assert near[0]["sfx"] and not near[0]["on_cut"]
    assert near[0]["cls"] == "pop"


def test_quick_profile_and_cache(video, monkeypatch):
    profile, notes = refprofile.analyze(video, quick=True)
    assert isinstance(profile, EditProfile) and profile.quick
    assert abs(profile.duration_s - DURATION) < 0.05
    assert profile.pace.shots == 4 and profile.pace.micro_count == 1
    assert profile.camera.punch_per_min > 0 and 0.15 <= profile.camera.punch_scale <= 0.35
    assert profile.audio.sfx_per_min > 0
    assert profile.meta["pace.median_s"].origin == "medido" and profile.meta["pace.median_s"].confidence > 0
    assert profile.confidence("materials.mix") == 0  # visão só no modo profundo
    assert notes["skipped"]["vision"] == "modo rápido"
    assert profile.materials.mix == {}
    # em cache: a segunda medição não chama o ffmpeg
    monkeypatch.setattr(cuts, "scan", lambda *a, **k: pytest.fail("deveria vir do cache"))
    monkeypatch.setattr(motion, "decode_gray", lambda *a, **k: pytest.fail("deveria vir do cache"))
    again, _ = refprofile.analyze(video, quick=True)
    assert again.pace == profile.pace and again.camera == profile.camera


def test_measure_video_is_the_same_extractor(video):
    fp = refprofile.measure_video(video)
    assert isinstance(fp, EditFingerprint) and fp.source == "video"
    profile, _ = refprofile.analyze(video, quick=True)
    assert fp.pace == profile.pace and fp.camera == profile.camera and fp.audio == profile.audio


def test_deep_offline_uses_mock_vision_and_skips_speech(video):
    profile, notes = refprofile.analyze(video, quick=False, vision_provider=MockVision())
    assert not profile.quick
    assert "speech" in notes["skipped"]  # offline: sem transcrição
    assert profile.confidence("speech.wpm") == 0
    assert profile.materials.mix and abs(sum(profile.materials.mix.values()) - 1) < 0.01
    assert profile.meta["materials.mix"].origin == "visão"
    assert profile.meta["materials.mix"].confidence <= 0.3  # visão de mentira: confiança baixa
    assert notes["vision_provider"] == "mock_vision"


def test_video_without_audio_has_no_audio_block(tmp_path):
    import subprocess

    silent = tmp_path / "mudo.mp4"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc2=size=320x180:rate=25:duration=2", "-c:v", "libx264", "-preset", "veryfast",
                    str(silent)], check=True, capture_output=True, timeout=60)
    assert audio_events.load(silent) is None
    profile, _ = refprofile.analyze(silent, quick=True)
    assert profile.audio.sfx_per_min == 0 and "audio.sfx_per_min" not in profile.meta
