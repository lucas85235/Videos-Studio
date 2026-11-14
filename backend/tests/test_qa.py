from __future__ import annotations

from pathlib import Path

import numpy as np

from studio.config import REPO_ROOT, Settings, set_settings
from studio.core.project import service
from studio.core.project.document import Scene, Segment, Visual
from studio.db import init_db, reset_engine
from studio.media.ffmpeg import write_wav
from studio.providers import registry
from studio.providers.align import word_error_rate
from studio.providers.base import ProviderInfo, TTSProvider, TTSResult, Usage, Word
from studio.workers.pipeline.build import Builder

TEXT = "O farol ficou aceso por quarenta anos."


def test_word_error_rate_normalizes_and_counts():
    assert word_error_rate(TEXT, ["o", "farol", "ficou", "aceso", "por", "quarenta", "anos"]) == 0.0
    assert word_error_rate("Olá, mundo!", ["ola", "mundo"]) == 0.0
    assert abs(word_error_rate("um dois três quatro", ["um", "dois", "tres", "cinco"]) - 0.25) < 1e-9
    assert word_error_rate("um dois", []) == 1.0


class FakeTTS(TTSProvider):
    info = ProviderInfo(id="fake_tts", kind="tts", name="fake", commercial_use=True)
    calls = 0

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir):
        FakeTTS.calls += 1
        path = write_wav(Path(out_dir) / "voice.wav", np.zeros(48000, dtype=np.float32))
        return TTSResult(path=path, duration_s=1.0, words=None, usage=Usage(units=len(text), unit_name="chars"))


class FakeWhisper:
    info = ProviderInfo(id="faster_whisper", kind="align", name="fake whisper", commercial_use=True)

    def __init__(self) -> None:
        self.calls = 0

    def availability(self):
        return True, "ok"

    def transcribe(self, audio_path, lang):
        self.calls += 1
        words = ["o", "farol", "ficou", "aceso", "por", "quarenta", "anos"] if self.calls > 1 else ["blá", "blá"]
        return [Word(w, i * 0.1, i * 0.1 + 0.08) for i, w in enumerate(words)]


def test_narration_qa_retries_and_reuses_transcript(tmp_path, monkeypatch):
    settings = Settings(data_dir=tmp_path / "qa", presets_dir=REPO_ROOT / "presets", offline=False, workers_enabled=False)
    set_settings(settings)
    reset_engine()
    init_db()
    whisper = FakeWhisper()
    monkeypatch.setitem(registry._PROVIDERS, "fake_tts", FakeTTS())
    monkeypatch.setitem(registry._PROVIDERS, "faster_whisper", whisper)
    doc = service.create_project(title="QA", style_id="misterios-short")
    seg = Segment(id="seg_qa", text={"pt-BR": TEXT})
    doc = doc.model_copy(update={
        "segments": [seg],
        "scenes": [Scene(id="scn_qa", segment_id="seg_qa", visual=Visual(prompt="x"))],
        "style_overrides": {"voice": {"chain": ["fake_tts"], "qa": {"max_wer": 0.1, "retries": 2}}},
    })
    service.commit(doc.id, doc, "teste")
    builder = Builder(doc.id)
    tts_hash, out = builder.narration("seg_qa", "pt-BR")
    assert out["qa"]["attempts"] == 2
    assert out["qa"]["ok"] is True
    assert out["provider"] == "fake_tts"
    assert not builder.warnings
    calls_before = whisper.calls
    words = builder.alignment("seg_qa", "pt-BR", tts_hash, out)
    assert len(words) == 7
    assert whisper.calls == calls_before  # alinhamento reaproveitou a transcrição do QA
