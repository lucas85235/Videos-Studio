"""Análise da referência num vídeo sintético `lavfi` (ADR 0004 §3.2): cortes, micro-plano, punch, SFX e o perfil."""

from __future__ import annotations

from pathlib import Path

import pytest
from refprofile_fixture import CUTS, DURATION, FPS, ONSET_S, PUNCH_S, ffmpeg_available, make_video

from studio.core.editing.profile import EditFingerprint, EditProfile
from studio.media import refprofile
from studio.media.refprofile import audio_events, cuts, decode
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
    monkeypatch.setattr(decode, "scan_and_gray", lambda *a, **k: pytest.fail("deveria vir do cache"))
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


def test_cancel_stops_the_analysis(video, tmp_path):
    copy = tmp_path / "cancelado.mp4"
    copy.write_bytes(video.read_bytes())
    with pytest.raises(refprofile.Cancelled):
        refprofile.analyze(copy, quick=True, cancelled=lambda: True)
    calls = {"n": 0}

    def later() -> bool:  # cancela no meio da leitura do vídeo
        calls["n"] += 1
        return calls["n"] > 2

    with pytest.raises(refprofile.Cancelled):
        refprofile.analyze(copy, quick=True, cancelled=later)
    assert not (refprofile.cache_dir_for(copy) / "base.json").exists()  # nada gravado


def test_audio_that_starts_late_is_on_the_video_clock(video, tmp_path):
    """MKV com o áudio começando 0,5 s depois do vídeo: o tom (3,7 s no áudio) toca em 4,2 s na tela."""
    import subprocess

    late = tmp_path / "atrasado.mkv"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video), "-itsoffset", "0.5",
                    "-i", str(video), "-map", "0:v", "-map", "1:a", "-c", "copy", str(late)],
                   check=True, capture_output=True, timeout=60)
    assert decode.audio_offset(late) == pytest.approx(0.5, abs=0.03)
    base = refprofile.measure_base(late)
    for got, want in zip(sorted(base["cuts"]), CUTS, strict=True):  # os cortes não mudam
        assert abs(got - want) <= 1 / FPS + 1e-6
    near = [o for o in base["audio"]["onsets"] if abs(o["t"] - (ONSET_S + 0.5)) <= 0.06]
    assert len(near) == 1 and near[0]["sfx"], base["audio"]["onsets"]


def _words_over(t0: float, t1: float) -> list[tuple[str, float, float]]:
    return [("palavra", round(t, 2), round(t + 0.25, 2)) for t in [t0 + 0.3 * i for i in range(int((t1 - t0) / 0.3))]]


def test_deep_never_counts_more_sfx_than_quick(video):
    quick, _ = refprofile.analyze(video, quick=True)

    def humor(system, prompt, context):
        return {"structure": [], "tone_tags": ["calmo"], "patterns": ["pausa antes do dado"], "gag_count": 0}

    # a narração cobre o tom de 3,7 s: o ataque está dentro de uma palavra e não é SFX
    deep, notes = refprofile.analyze(video, quick=False, vision_provider=MockVision(),
                                     transcriber=lambda p, lang: (_words_over(3.4, 4.3), "en"), humor_llm=humor,
                                     use_cache=False)
    assert notes["speech_lang"] == "en"
    assert deep.audio.sfx_per_min <= quick.audio.sfx_per_min
    assert deep.audio.sfx_per_min == 0


def test_humor_failure_is_not_cached_and_is_retried(video, tmp_path):
    copy = tmp_path / "humor.mp4"
    copy.write_bytes(video.read_bytes())
    calls = {"transcribe": 0, "humor": 0}

    def transcriber(path, lang):
        calls["transcribe"] += 1
        assert lang is None  # sem língua fixa: o faster-whisper detecta
        return [("oi", 0.5, 0.8), ("gente", 0.9, 1.3)], "pt"

    def flaky(system, prompt, context):
        calls["humor"] += 1
        if calls["humor"] == 1:
            from studio.providers.base import TransientError

            raise TransientError("limite de uso do plano")
        return {"structure": [], "tone_tags": ["conversado"], "patterns": ["pergunta e resposta"], "gag_count": 1}

    first = refprofile.speech_stage(copy, DURATION, transcriber=transcriber, humor_llm=flaky)
    assert "humor_error" in first and "humor" not in first
    second = refprofile.speech_stage(copy, DURATION, transcriber=transcriber, humor_llm=flaky)
    assert second["humor"]["tone_tags"] == ["conversado"] and "humor_error" not in second
    assert calls == {"transcribe": 1, "humor": 2}  # a transcrição veio do cache; só o humor foi refeito
    third = refprofile.speech_stage(copy, DURATION, transcriber=transcriber, humor_llm=flaky)
    assert third["humor"] == second["humor"] and calls["humor"] == 2
