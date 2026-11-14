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
    builder = Builder(doc.id, mode="final")  # voz de API: a prévia usaria a voz local
    tts_hash, out = builder.narration("seg_qa", "pt-BR")
    assert out["qa"]["attempts"] == 2
    assert out["qa"]["ok"] is True
    assert out["provider"] == "fake_tts"
    assert not builder.warnings
    calls_before = whisper.calls
    words = builder.alignment("seg_qa", "pt-BR", tts_hash, out)
    assert len(words) == 7
    assert whisper.calls == calls_before  # alinhamento reaproveitou a transcrição do QA


# --------------------------------------------------------------------------------------------
# Fala fora do roteiro (instrução de estilo lida em voz alta, repetição)
# --------------------------------------------------------------------------------------------

STYLE = "Narre como um documentário: voz grave, calma, pausas dramáticas, ritmo lento."
HOOK = ("Você já ouviu falar sobre GTA 6 vai ser o melhor jogo de todos os tempos? O que vou te contar agora parece "
        "impossível, mas aconteceu.")
BODY = " ".join(["Os documentos da época mostram que alguém tentou esconder o que realmente aconteceu."] * 15)


def _said(text: str) -> list[str]:
    return text.split()


def test_detects_style_instruction_read_aloud_even_in_long_segments():
    from studio.providers.align import narration_problems

    # transcrições reais do Gemini TTS no formato antigo ("estilo: texto")
    leaked = "Narre como um documentário, voz grave, calma, pausas dramáticas, ritmo lento. "
    assert narration_problems(HOOK, _said(leaked + HOOK), 19.6, style_prompt=STYLE) == ["leu a instrução de estilo em voz alta"]
    # num trecho longo o WER fica baixo (~6%), mas a instrução lida é detectada
    assert word_error_rate(BODY, _said(leaked + BODY)) < 0.1
    assert narration_problems(BODY, _said(leaked + BODY), 90, style_prompt=STYLE) == ["leu a instrução de estilo em voz alta"]
    assert narration_problems(HOOK, _said(HOOK + " " + HOOK), 21.7, style_prompt=STYLE) == ["repetiu trechos do texto"]
    assert narration_problems(HOOK, _said(HOOK), 10.1, style_prompt=STYLE) == []
    # erro de reconhecimento (troca de palavra) não é fala a mais
    assert narration_problems(HOOK, _said(HOOK.replace("impossível", "possível")), 10, style_prompt=STYLE) == []
    extra = narration_problems(HOOK, _said(HOOK + " inscreva-se no canal e ative o sininho"), 13, style_prompt=STYLE)
    assert extra and "não estão no roteiro" in extra[0]


def test_without_transcript_flags_impossible_durations():
    from studio.providers.align import narration_problems

    assert narration_problems(HOOK, None, 10.1) == []
    assert narration_problems(HOOK, None, 21.7)  # o dobro: repetição ou instrução lida
    assert narration_problems("Se você gosta de histórias assim, inscreva-se para o próximo capítulo.", None, 13.9)
    assert narration_problems(BODY, None, 95) == []  # ritmo lento de documentário é normal


def test_gemini_prompt_keeps_direction_apart_from_the_transcript():
    from studio.providers.tts import gemini_tts_prompt

    prompt = gemini_tts_prompt(HOOK, STYLE, 1.0)
    notes, transcript = prompt.split("#### TRANSCRIPT\n")
    assert transcript == HOOK
    assert "never read them aloud" in notes and "documentário, voz grave" in notes
    assert ":" not in notes.split("### DIRECTOR'S NOTES\n")[1]  # dois-pontos faziam o modelo emendar a instrução
    assert "mais rápido" in gemini_tts_prompt(HOOK, STYLE, 1.2)
    assert gemini_tts_prompt(HOOK, None, 1.0) == HOOK
    assert gemini_tts_prompt(HOOK, "  ", 1.0) == HOOK


class LeakyTTS(TTSProvider):
    """Lê a instrução de estilo sempre que ela vem no pedido (como o Gemini às vezes faz)."""

    info = ProviderInfo(id="leaky_tts", kind="tts", name="leaky", commercial_use=True)

    def __init__(self) -> None:
        self.style_prompts: list[str | None] = []

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir):
        self.style_prompts.append(style_prompt)
        spoken = f"{style_prompt} {text}" if style_prompt else text
        path = write_wav(Path(out_dir) / "voice.wav", np.zeros(4800, dtype=np.float32))
        (Path(out_dir) / "spoken.txt").write_text(spoken, encoding="utf-8")
        # tempos medidos no Gemini: texto ~0,4 s/palavra; a instrução lida sai com pausas, ~0,73 s/palavra
        duration = len(text.split()) * 0.4 + (len(style_prompt.split()) * 0.73 if style_prompt else 0.0)
        return TTSResult(path=path, duration_s=duration, words=None, usage=Usage(units=len(text), unit_name="chars"))


class TranscriptWhisper(FakeWhisper):
    def transcribe(self, audio_path, lang):
        self.calls += 1
        spoken = (Path(audio_path).parent / "spoken.txt").read_text(encoding="utf-8").split()
        return [Word(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate(spoken)]


def _leaky_builder(tmp_path, monkeypatch, *, whisper: bool):
    settings = Settings(data_dir=tmp_path / "leak", presets_dir=REPO_ROOT / "presets", offline=False, workers_enabled=False)
    set_settings(settings)
    reset_engine()
    init_db()
    tts = LeakyTTS()
    monkeypatch.setitem(registry._PROVIDERS, "leaky_tts", tts)
    if whisper:
        monkeypatch.setitem(registry._PROVIDERS, "faster_whisper", TranscriptWhisper())
    else:
        monkeypatch.setattr(registry._PROVIDERS["faster_whisper"], "availability", lambda: (False, "não instalado"))
    doc = service.create_project(title="Vazamento", style_id="documentario-sombrio")
    doc = doc.model_copy(update={
        "segments": [Segment(id="seg_hook", text={"pt-BR": HOOK})],
        "scenes": [Scene(id="scn_1", segment_id="seg_hook", visual=Visual(prompt="x"))],
        "style_overrides": {"voice": {"chain": ["leaky_tts"]}},
    })
    service.commit(doc.id, doc, "teste")
    return Builder(doc.id, mode="final"), tts  # voz de API: a prévia usaria a voz local


def test_narration_that_reads_the_instruction_is_redone_without_it(tmp_path, monkeypatch):
    builder, tts = _leaky_builder(tmp_path, monkeypatch, whisper=True)
    _, out = builder.narration("seg_hook", "pt-BR")
    assert tts.style_prompts[0] and tts.style_prompts[-1] is None  # a última tentativa vai sem estilo
    assert out["attempts"] == 3 and out["problems"] == []
    assert not builder.warnings
    spoken = builder.store.abs(out["path"]).parent.joinpath("spoken.txt").read_text(encoding="utf-8")
    assert spoken == HOOK


def test_duration_check_catches_the_leak_without_whisper(tmp_path, monkeypatch):
    builder, tts = _leaky_builder(tmp_path, monkeypatch, whisper=False)
    _, out = builder.narration("seg_hook", "pt-BR")
    assert len(tts.style_prompts) == 3 and tts.style_prompts[-1] is None
    assert out["problems"] == [] and out["qa"] is None and not builder.warnings


def test_short_segment_with_instruction_is_not_also_called_repetition():
    from studio.providers.align import narration_problems

    end = "Se você gosta de histórias assim, inscreva-se para o próximo capítulo."
    heard = _said("Narre como um documentário, voz grave, calma, pausas dramáticas, ritmo lento. " + end)
    assert narration_problems(end, heard, 13.9, style_prompt=STYLE) == ["leu a instrução de estilo em voz alta"]


def test_gemini_tts_sends_only_the_text_and_applies_speed_to_audio(tmp_path, monkeypatch):
    import base64

    from studio.media.ffmpeg import wav_duration
    from studio.providers import tts

    sent: list[str] = []

    def fake_post(path, key, body, timeout=300):
        sent.append(body["contents"][0]["parts"][0]["text"])
        pcm = (np.zeros(24000, dtype="<i2")).tobytes()  # 1 s de silêncio a 24 kHz
        return {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "audio/L16;rate=24000",
                                                                       "data": base64.b64encode(pcm).decode()}}]}}]}

    monkeypatch.setattr(tts, "gemini_post", fake_post)
    monkeypatch.setattr(tts.GeminiTTS, "_key", lambda self: "k")
    monkeypatch.delenv("VS_GEMINI_TTS_STYLE", raising=False)
    out = tts.GeminiTTS().synthesize(text=HOOK, lang="pt-BR", voice="Charon", style_prompt=STYLE, speed=1.25, out_dir=tmp_path)
    assert sent == [HOOK]  # nenhuma instrução para o modelo ler em voz alta
    assert abs(wav_duration(out.path) - 0.8) < 0.05  # 1 s a 1,25x
    monkeypatch.setenv("VS_GEMINI_TTS_STYLE", "1")
    tts.GeminiTTS().synthesize(text=HOOK, lang="pt-BR", voice="Charon", style_prompt=STYLE, speed=1.0, out_dir=tmp_path / "b")
    assert sent[-1].endswith("#### TRANSCRIPT\n" + HOOK)


def test_whisper_runs_one_transcription_at_a_time(monkeypatch, tmp_path):
    """Com o modelo na GPU sem cuBLAS, chamadas simultâneas travavam para sempre: agora vão uma por vez, e a que
    recebe o erro do CUDA passa para a CPU."""
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor

    from studio.providers import align

    state = {"now": 0, "max": 0, "cuda_calls": 0}
    lock = threading.Lock()

    def fake_run(model, audio, lang):
        with lock:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        if model == "cuda-model":
            state["cuda_calls"] += 1
            raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        return [Word("ok", 0.0, 0.1)]

    def fake_model(self):
        return ("cpu-model", "cpu") if align.FasterWhisperAligner._gpu_broken else ("cuda-model", "cuda")

    monkeypatch.setattr(align.FasterWhisperAligner, "_gpu_broken", False)
    monkeypatch.setattr(align.FasterWhisperAligner, "_run", staticmethod(fake_run))
    monkeypatch.setattr(align.FasterWhisperAligner, "_model", fake_model)
    monkeypatch.setattr(align, "decode_mono", lambda path, rate: np.zeros(160, dtype=np.float32))
    aligner = align.FasterWhisperAligner()
    with ThreadPoolExecutor(3) as pool:
        results = list(pool.map(lambda _i: aligner.transcribe(tmp_path / "x.wav", "pt-BR"), range(3)))
    assert all(r and r[0].text == "ok" for r in results)
    assert state["max"] == 1 and state["cuda_calls"] == 1


def test_numbers_in_digits_are_not_counted_as_errors():
    script = "GTA 5 saiu em dois mil e treze e vendeu mais de duzentos milhões de cópias."
    heard = "GTA 5 saiu em 2013 e vendeu mais de 200 milhões de copias.".split()
    assert word_error_rate(script, heard, "pt-BR") == 0.0
    assert word_error_rate(script, heard) > 0.2  # sem o idioma, os números não são convertidos
    assert word_error_rate("In twenty thirteen it sold twenty four copies.", "In 2013 it sold 24 copies.".split(),
                           "en-US") == 0.0
