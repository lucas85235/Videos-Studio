"""Conferência de áudio (ADR 0003, bloco G): apito, tom puro, loudness, máscara de fala, música × voz e o job."""

from __future__ import annotations

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.db import session_scope
from studio.media import audio, audio_check
from studio.media.ffmpeg import read_wav, write_wav
from studio.models import QualityReport
from studio.workers.jobs import get_job, get_runner

SR = audio_check.SR


def _pink(seconds: float, seed: int = 1) -> np.ndarray:
    """Ruído rosa (1/f na potência) normalizado a 0,3 de pico."""
    n = int(seconds * SR)
    rng = np.random.default_rng(seed)
    spec = np.fft.rfft(rng.standard_normal(n))
    f = np.fft.rfftfreq(n, 1 / SR)
    spec[1:] /= np.sqrt(f[1:])
    spec[0] = 0
    x = np.fft.irfft(spec, n)
    return (0.3 * x / np.abs(x).max()).astype(np.float32)


def _sine(hz: float, seconds: float, amp: float = 0.3) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def test_tone_report_flags_whistle_and_not_noise(isolated_settings, tmp_path):
    whistle = audio_check.tone_report(_sine(3000, 0.2))
    assert whistle["apito"] and whistle["tonal_ms"] >= 60 and abs(whistle["hz"] - 3000) < 60
    assert not audio_check.tone_report(_pink(1.0))["apito"]
    # os 5 efeitos procedurais passam na guarda (regra 13: sem apito)
    for kind in audio.SFX_KINDS:
        sound, _ = read_wav(audio.generate_sfx(kind, tmp_path / f"{kind}.wav"))
        report = audio_check.tone_report(sound)
        assert not report["apito"], (kind, report)
        assert audio_check.tonal_fraction(sound)[0] <= audio_check.PURE_TONE, kind
    # arquivo: mesma medida
    path = write_wav(tmp_path / "apito.wav", _sine(3000, 0.2))
    assert audio_check.tone_report_file(path)["apito"]
    assert {"whip", "whip2", "ding", "trombone"} == set(audio_check.SFX_BANNED)


def test_tonal_fraction_pure_tone():
    frac, hz = audio_check.tonal_fraction(_sine(2500, 0.5))
    assert frac > audio_check.PURE_TONE and abs(hz - 2500) < 15
    assert audio_check.tonal_fraction(_pink(0.5))[0] < audio_check.PURE_TONE
    # tom grave (abaixo de 1 kHz) não conta
    assert audio_check.tonal_fraction(_sine(400, 0.5))[0] == 0.0


def test_lufs_matches_ffmpeg(isolated_settings, tmp_path):
    steady = _pink(6.0, seed=3)
    path = write_wav(tmp_path / "rosa.wav", steady)
    ref = audio.measure_loudness(path)["lufs"]
    x, _ = read_wav(path)
    assert abs(audio_check.integrated_lufs(x) - ref) <= 0.5
    momentary = audio_check.momentary_lufs(x)
    assert momentary.size == (x.size - int(0.4 * SR)) // (SR // 10) + 1
    assert np.all(np.abs(momentary[5:-5] - ref) <= 0.5)
    # com trechos em silêncio e níveis diferentes, as portas -70 e -10 seguem o FFmpeg
    mixed = np.concatenate([steady[: 2 * SR], np.zeros(2 * SR, np.float32), steady[: 3 * SR] * 0.2, steady[: 2 * SR] * 0.01])
    path2 = write_wav(tmp_path / "variado.wav", mixed)
    x2, _ = read_wav(path2)
    assert abs(audio_check.integrated_lufs(x2) - audio.measure_loudness(path2)["lufs"]) <= 0.5
    # estéreo: soma das potências dos canais (+3 dB para canais iguais)
    st = np.stack([x, x], axis=1)
    assert abs(audio_check.integrated_lufs(st) - audio_check.integrated_lufs(x) - 3.01) < 0.05
    with pytest.raises(ValueError):
        audio_check.momentary_lufs(x, 44100)


def test_speech_mask():
    fps = 30
    timeline = {"fps": fps, "durationInFrames": 120, "scenes": [
        {"from": 0, "words": []},
        {"from": 30, "words": [{"text": "oi", "from": 0, "to": 15}]},
    ]}
    n = 4 * SR
    mask = audio_check.speech_mask(timeline, n)
    assert mask.size == n
    on = np.flatnonzero(mask)
    # 0,12 s antes (4 quadros) e 0,3 s depois (9 quadros)
    assert abs(on[0] / SR - (30 - 4) / fps) < 1 / fps + 1e-6
    assert abs(on[-1] / SR - (45 + 9) / fps) < 1 / fps + 1e-6
    assert not mask[: int(0.8 * SR)].any() and not mask[int(1.9 * SR):].any()


def _voice_and_music(distance_db: float) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict]:
    """Voz sintética (ruído rosa em frases de 2 s com pausa de 1 s) e trilha contínua `distance_db` abaixo."""
    total = 18.0
    n = int(total * SR)
    voice = np.zeros(n, np.float32)
    words = []
    fps = 30
    for k in range(6):
        a = k * 3.0
        voice[int(a * SR): int((a + 2) * SR)] = _pink(2.0, seed=10 + k)
        words.append({"text": "fala", "from": int(a * fps), "to": int((a + 2) * fps)})
    timeline = {"fps": fps, "durationInFrames": int(total * fps), "scenes": [{"from": 0, "words": words}],
                "chapters": [{"title": "Parte 2", "start": 9.0}]}
    mask = audio_check.speech_mask(timeline, n)
    v_level = audio_check.integrated_lufs(voice[mask])
    music = _pink(total, seed=99)
    m_level = audio_check.integrated_lufs(music[mask])
    music = (music * 10 ** ((v_level - distance_db - m_level) / 20)).astype(np.float32)
    return voice, music, mask, timeline


def test_music_distance_items():
    voice, music, mask, timeline = _voice_and_music(17.0)
    levels = audio_check.chapter_levels(voice, music, mask, timeline)
    assert [c["title"] for c in levels["chapters"]] == ["Abertura", "Parte 2"]
    assert abs(levels["episode"]["distance_db"] - 17.0) < 0.6
    for row in levels["chapters"]:
        assert row["music_pause_db"] is not None
        assert audio_check.distance_item(row)["level"] == "ok"

    voice, music, mask, timeline = _voice_and_music(8.0)
    levels = audio_check.chapter_levels(voice, music, mask, timeline)
    item = audio_check.distance_item(levels["chapters"][0])
    assert item["level"] == "aviso" and "música alta" in item["title"] and "de 14 a 19" in item["title"]
    low = audio_check.distance_item({"title": "x", "distance_db": 23.0, "start_s": 0})
    assert low["level"] == "aviso" and "baixa demais" in low["title"]
    assert audio_check.worst([item, {"level": "erro"}]) == "erro"


def test_sfx_events_and_items():
    voice = np.zeros(6 * SR, np.float32)
    voice[: 6 * SR] = _pink(6.0, seed=4)
    sfx = np.zeros(6 * SR, np.float32)
    sfx[2 * SR: int(2.2 * SR)] = _sine(3000, 0.2, amp=0.9)  # efeito com apito e mais alto que a voz
    events = audio_check.sfx_events(sfx, voice)
    assert len(events) == 1
    e = events[0]
    assert 1.9 <= e["t"] <= 2.4 and e["tom"] > audio_check.PURE_TONE and e["vs_voz"] > 0
    items = audio_check.sfx_items(events, [{"kind": "ding", "apito": False}])
    codes = {(i["code"], i["level"]) for i in items}
    assert ("sfx_tone", "erro") in codes and ("sfx_loud", "aviso") in codes
    assert any("ding" in i["title"] for i in items)


def _run(kind: str, project_id: str | None, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def test_audio_check_job_on_offline_preview(isolated_settings):
    client = TestClient(create_app())
    project_id = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado").id
    # sem prévia: a rota explica
    assert client.post(f"/api/projects/{project_id}/audio-check", json={}).status_code == 409
    _run("script", project_id)
    _run("storyboard", project_id)
    preview = _run("preview", project_id)
    result = _run("audio_check", project_id, {"source": "preview"})
    assert result["lang"] == "pt-BR" and result["status"] in ("ok", "aviso", "erro")
    with session_scope() as s:
        rows = s.query(QualityReport).filter_by(project_id=project_id, kind="audio").all()
        assert len(rows) == 1
        row = rows[0]
        assert row.source == preview["langs"]["pt-BR"]["mix"]
        assert row.data["chapters"] and row.data["chapters"][0]["title"] == "Abertura"
        assert row.data["episode"]["voice_db"] is not None
        assert all(i["level"] in ("ok", "aviso", "erro") for i in row.items)
        assert any(i["code"] == "music_distance" for i in row.items)
    got = client.get(f"/api/projects/{project_id}/audio-check?lang=pt-BR").json()
    assert got["report"]["kind"] == "audio" and got["report"]["items"]
    guard = client.get("/api/sfx/guard").json()
    assert guard["ok"] and {i["kind"] for i in guard["items"]} == set(audio.SFX_KINDS)


def test_audio_check_without_music_levels_the_voice_like_the_mix(isolated_settings, monkeypatch):
    from studio.workers.audiocheck import report
    from studio.workers.pipeline.build import Builder

    project_id = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado").id
    _run("script", project_id)
    _run("storyboard", project_id)
    preview = _run("preview", project_id)
    monkeypatch.setattr(Builder, "music_track", lambda self, timeline: None)
    result = report.measure(project_id, source="preview")
    import json

    timeline = json.loads((isolated_settings.data_dir / preview["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    store = Builder(project_id).store
    voice = audio.place_clips([(store.abs(n["path"]), n["start"], 1.0) for n in timeline["narration"]],
                              timeline["duration"], isolated_settings.tmp_dir / "voz.wav")
    # sem trilha a voz também vai a VOICE_LUFS, como no mix (o `vs_voz` dos efeitos fica na mesma referência)
    assert result["data"]["voice_gain_db"] == pytest.approx(audio.voice_gain_db(voice), abs=0.05)
    assert any(i["code"] == "music_none" for i in result["items"])
