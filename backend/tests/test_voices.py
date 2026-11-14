from __future__ import annotations

from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.config import get_settings, set_settings
from studio.core.project import service
from studio.core.styles.library import get_style
from studio.media.ffmpeg import write_wav
from studio.providers import registry
from studio.providers.base import ProviderInfo, QuotaExceeded, TTSProvider, TTSResult, Usage, VoiceOption
from studio.providers.voices import sample_text


class SampleTTS(TTSProvider):
    info = ProviderInfo(id="sample_tts", kind="tts", name="Voz de teste", commercial_use=True)

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []
        self.fail: Exception | None = None

    def availability(self):
        return True, "ok"

    def voices(self, lang):
        return [VoiceOption(id="Charon", label="Charon", gender="masculina", tone="informativa"),
                VoiceOption(id="Kore", label="Kore", gender="feminina", tone="firme")]

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir):
        if self.fail:
            raise self.fail
        self.calls.append((text, voice, style_prompt))
        path = write_wav(Path(out_dir) / "voice.wav", np.zeros(4800, dtype=np.float32))
        return TTSResult(path=path, duration_s=0.1, words=None, usage=Usage(units=len(text), unit_name="chars"))


def _online(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    tts = SampleTTS()
    monkeypatch.setitem(registry._PROVIDERS, "sample_tts", tts)
    return tts


def test_catalog_lists_gemini_voices_with_gender_and_tone(isolated_settings):
    with TestClient(create_app()) as client:
        data = client.get("/api/voices?lang=pt-BR&providers=gemini_tts,windows_sapi,mock_llm").json()
    assert data["sample_text"].startswith("Olá!")
    by_id = {p["id"]: p for p in data["providers"]}
    assert list(by_id) == ["gemini_tts", "windows_sapi"]  # na ordem pedida, só provedores de voz
    gemini = by_id["gemini_tts"]
    assert len(gemini["voices"]) == 30
    charon = next(v for v in gemini["voices"] if v["id"] == "Charon")
    assert charon == {"id": "Charon", "label": "Charon", "gender": "masculina", "tone": "informativa", "sample": None}
    assert by_id["windows_sapi"]["preview_only"] is True
    assert sample_text("ja-JP") != sample_text("pt-BR") and sample_text("xx-YY") == sample_text("en-US")


def test_sample_is_generated_once_without_style_and_served(isolated_settings, monkeypatch):
    tts = _online(monkeypatch)
    with TestClient(create_app()) as client:
        first = client.post("/api/voices/sample", json={"provider": "sample_tts", "voice": "Kore", "lang": "pt-BR"})
        assert first.status_code == 200, first.text
        again = client.post("/api/voices/sample", json={"provider": "sample_tts", "voice": "Kore", "lang": "pt-BR"}).json()
        assert again == first.json() and len(tts.calls) == 1  # guardada: não gasta cota de novo
        assert tts.calls[0] == (sample_text("pt-BR"), "Kore", None)
        assert client.get(first.json()["url"]).status_code == 200
        listed = client.get("/api/voices?lang=pt-BR&providers=sample_tts").json()["providers"][0]["voices"]
        assert next(v for v in listed if v["id"] == "Kore")["sample"] == first.json()["path"]
        assert next(v for v in listed if v["id"] == "Charon")["sample"] is None
        # fora da pasta de cache: a limpeza do cache não apaga as amostras
        assert first.json()["path"].startswith("samples/voices/sample_tts/pt-BR/")

        assert client.post("/api/voices/sample", json={"provider": "sample_tts", "voice": "Inventada"}).status_code == 404
        assert client.post("/api/voices/sample", json={"provider": "mock_llm", "voice": "x"}).status_code == 404
        tts.fail = QuotaExceeded("acabou")
        quota = client.post("/api/voices/sample", json={"provider": "sample_tts", "voice": "Charon", "lang": "pt-BR"})
        assert quota.status_code == 429
        assert registry.quota_exhausted_today("sample_tts")


def test_project_created_with_chosen_voice(isolated_settings, monkeypatch):
    _online(monkeypatch)
    with TestClient(create_app()) as client:
        created = client.post("/api/projects", json={"title": "Voz", "style_id": "misterios-short", "topic": "farol",
                                                     "voice": {"provider": "google_chirp3hd", "voice": "Kore"}}).json()
    doc = service.get_doc(created["project"]["id"])
    style = get_style(doc.style_id, doc.style_overrides)
    assert style.voice.voice == "Kore"
    assert style.voice.chain == ["google_chirp3hd", "gemini_tts", "qwen3_tts_local"]
    plain = service.create_project(title="Sem voz", style_id="misterios-short")
    assert plain.style_overrides == {}
