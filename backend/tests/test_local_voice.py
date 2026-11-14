"""Prévia com a voz local (Kokoro) e vídeo final só com voz que pode ir para o canal."""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest

from studio.config import get_settings, set_settings
from studio.core.project import service
from studio.core.project.document import Scene, Segment, Visual
from studio.core.styles.library import get_style
from studio.media.ffmpeg import write_wav
from studio.providers import kokoro, registry
from studio.providers.base import ProviderInfo, TTSProvider, TTSResult, Usage, VoiceOption
from studio.workers.pipeline.build import Builder, BuildError, plan


class RecordingTTS(TTSProvider):
    def __init__(self, provider_id: str, commercial: bool = True) -> None:
        self.info = ProviderInfo(id=provider_id, kind="tts", name=provider_id, commercial_use=commercial,
                                 deterministic=True)
        self.calls: list[tuple[str, str | None, str | None]] = []
        self.available = True

    def availability(self):
        return (True, "ok") if self.available else (False, "desligada")

    def voices(self, lang):
        return [VoiceOption(id="Charon", label="Charon", gender="masculina"),
                VoiceOption(id="Kore", label="Kore", gender="feminina")]

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir):
        self.calls.append((lang, voice, style_prompt))
        path = write_wav(Path(out_dir) / "voice.wav", np.zeros(4800, dtype=np.float32))
        return TTSResult(path=path, duration_s=0.1, words=None, usage=Usage(units=len(text), unit_name="chars"))


@pytest.fixture
def online(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    api = RecordingTTS("api_tts")
    local = RecordingTTS("kokoro_local")
    sapi = RecordingTTS("windows_sapi", commercial=False)
    for tts in (api, local, sapi):
        monkeypatch.setitem(registry._PROVIDERS, tts.info.id, tts)
    monkeypatch.setitem(registry._PROVIDERS, "mock_tts", RecordingTTS("mock_tts", commercial=False))
    return api, local, sapi


def _project(voice: dict | None = None, languages: list[str] | None = None):
    doc = service.create_project(title="Voz", style_id="misterios-short")
    langs = languages or ["pt-BR"]
    seg = Segment(id="seg_1", text={lang: "Um texto curto para narrar." for lang in langs})
    doc = doc.model_copy(update={
        "languages": langs,
        "segments": [seg],
        "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=Visual(prompt="x"))],
        "style_overrides": {"voice": {"chain": ["api_tts"], "voice": "Kore", "style_prompt": "voz grave",
                                      "qa": {"enabled": False}, **(voice or {})}},
    })
    service.commit(doc.id, doc, "teste")
    return doc


def test_pick_voice_keeps_language_and_gender():
    assert kokoro.pick_voice("pt-BR", "pm_santa") == "pm_santa"
    assert kokoro.pick_voice("pt-BR", None, "feminina") == "pf_dora"
    assert kokoro.pick_voice("pt-BR", None, "masculina") == "pm_alex"
    # voz de outro idioma: troca por uma do mesmo gênero no idioma pedido
    assert kokoro.pick_voice("en-US", "pm_alex") == "am_michael"
    assert kokoro.pick_voice("pt-BR", None, "masculina", avoid="pm_alex") == "pm_santa"
    assert kokoro.pick_voice("en-GB", None, "feminina") == "bf_emma"
    assert kokoro.pick_voice("ja-JP", None) is None and not kokoro.supports("ja-JP")
    assert [v.id for v in kokoro.voices_for("pt-BR")] == ["pf_dora", "pm_alex", "pm_santa"]


def test_preview_uses_local_voice_and_final_uses_chosen_voice(online):
    api, local, _ = online
    doc = _project()
    preview = Builder(doc.id, mode="preview")
    h_preview, out = preview.narration("seg_1", "pt-BR")
    # prévia: voz local do mesmo gênero da voz final (Kore é feminina), sem instrução de estilo
    assert local.calls == [("pt-BR", "pf_dora", None)] and not api.calls
    assert out["provider"] == "kokoro_local"

    final = Builder(doc.id, mode="final")
    h_final, out = final.narration("seg_1", "pt-BR")
    assert api.calls == [("pt-BR", "Kore", "voz grave")] and out["provider"] == "api_tts"
    assert h_final != h_preview
    # cada uma fica no cache: refazer a prévia ou o final não grava de novo
    Builder(doc.id, mode="preview").narration("seg_1", "pt-BR")
    Builder(doc.id, mode="final").narration("seg_1", "pt-BR")
    assert len(local.calls) == 1 and len(api.calls) == 1


def test_preview_can_use_the_final_voice_and_languages_without_local_voice(online):
    api, local, _ = online
    doc = _project({"preview": {"local": False}})
    Builder(doc.id, mode="preview").narration("seg_1", "pt-BR")
    assert api.calls and not local.calls

    doc = _project({"preview": {"voice": "pm_santa"}}, languages=["pt-BR", "ja-JP"])
    builder = Builder(doc.id, mode="preview")
    builder.narration("seg_1", "pt-BR")
    builder.narration("seg_1", "ja-JP")  # japonês não tem voz local: a prévia usa a voz final
    assert local.calls == [("pt-BR", "pm_santa", None)]
    assert api.calls[-1] == ("ja-JP", "Kore", "voz grave")
    # e o vídeo final reaproveita essa gravação em japonês
    before = len(api.calls)
    Builder(doc.id, mode="final").narration("seg_1", "ja-JP")
    assert len(api.calls) == before


def test_final_never_falls_back_to_a_test_voice(online):
    api, _, sapi = online
    doc = _project()
    api.available = False
    # na prévia sem voz local, a voz de teste ainda serve para conferir
    preview = Builder(doc.id, mode="preview")
    preview.style.voice.preview.local = False
    h, out = preview.narration("seg_1", "pt-BR")
    assert out["provider"] == "windows_sapi" and sapi.calls

    with pytest.raises(BuildError, match="voz do vídeo final não está disponível"):
        Builder(doc.id, mode="final").run()
    estimate = plan(doc.id)
    assert estimate["tts_chars"] > 0 and estimate["tts_provider"] is None and not estimate["tts_local"]

    # com a voz final de volta, a gravação de teste da prévia não é reaproveitada no vídeo final
    api.available = True
    _, out = Builder(doc.id, mode="final").narration("seg_1", "pt-BR")
    assert out["provider"] == "api_tts"
    assert plan(doc.id)["tts_missing"] == []


def test_plan_reports_local_preview_as_free(online):
    doc = _project()
    estimate = plan(doc.id, mode="preview")
    assert estimate["tts_local"] and estimate["tts_provider"] == "kokoro_local"
    final = plan(doc.id)
    assert not final["tts_local"] and final["tts_provider"] == "api_tts" and final["tts_voice"] == "Kore"


def test_preview_voice_is_a_style_setting():
    style = get_style("misterios-short", {"voice": {"preview": {"voice": "pm_alex"}}})
    assert style.voice.preview.local is True and style.voice.preview.voice == "pm_alex"


@pytest.mark.skipif(not os.environ.get("VS_TEST_LOCAL_VOICE"), reason="defina VS_TEST_LOCAL_VOICE=<pasta do modelo>")
def test_kokoro_speaks_portuguese(tmp_path, monkeypatch):
    monkeypatch.setattr(kokoro, "models_dir", lambda: Path(os.environ["VS_TEST_LOCAL_VOICE"]))
    result = kokoro.KokoroTTS().synthesize(text="Olá! Esta é a voz local da prévia.", lang="pt-BR", voice=None,
                                           style_prompt=None, speed=1.0, out_dir=tmp_path)
    assert result.path.is_file() and 1.0 < result.duration_s < 8.0
    assert result.model == "kokoro-v1.0/pf_dora"
