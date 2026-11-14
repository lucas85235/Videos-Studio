"""Alinhamento de palavras: timestamps por palavra para legendas karaokê e cortes.

1. Se o TTS já devolveu timestamps (ElevenLabs, voz do Windows, mock), eles são usados.
2. faster-whisper local (opcional): transcreve com timestamps e casa com o roteiro conhecido.
3. Estimativa: distribui as palavras pela duração da fala, ponderando o tamanho e as pausas.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

from studio.config import get_settings
from studio.core.text import normalize_token
from studio.core.text import words as split_words
from studio.media.ffmpeg import read_wav, speech_bounds
from studio.providers.base import AlignProvider, NotConfigured, ProviderInfo, Word

log = logging.getLogger(__name__)


def _weight(token: str) -> float:
    weight = max(1.0, len(normalize_token(token)))
    if token.endswith((".", "!", "?", "…")):
        weight += 4.0
    elif token.endswith((",", ";", ":", "—")):
        weight += 2.0
    return weight


def estimate_words(text: str, start: float, end: float) -> list[Word]:
    tokens = split_words(text)
    if not tokens:
        return []
    weights = [_weight(t) for t in tokens]
    total = sum(weights)
    span = max(0.1, end - start)
    words: list[Word] = []
    t = start
    for token, weight in zip(tokens, weights, strict=True):
        dur = span * weight / total
        speak = dur * (0.82 if token.endswith((".", "!", "?", ",", ";", ":", "…")) else 0.95)
        words.append(Word(token, t, t + speak))
        t += dur
    return words


class EstimateAligner(AlignProvider):
    info = ProviderInfo(
        id="estimate",
        kind="align",
        name="Estimativa por duração",
        commercial_use=True,
        local=True,
        free=True,
        notes="Sempre disponível. Precisão menor que o Whisper; bom para prévias.",
    )

    def align(self, *, audio_path: Path, text: str, lang: str) -> list[Word]:
        audio, rate = read_wav(audio_path)
        start, end = speech_bounds(audio, rate)
        return estimate_words(text, start, end)


class FasterWhisperAligner(AlignProvider):
    info = ProviderInfo(
        id="faster_whisper",
        kind="align",
        name="faster-whisper (local)",
        commercial_use=True,
        local=True,
        free=True,
        notes="MIT. Baixa o modelo na primeira execução. Instale com: uv sync --extra asr",
    )

    _models: dict[str, object] = {}

    def availability(self) -> tuple[bool, str]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False, "faster-whisper não instalado (uv sync --extra asr)"
        return True, "ok"

    def _model(self):
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise NotConfigured("faster-whisper não instalado") from exc
        name = get_settings().whisper_model
        if name not in self._models:
            try:
                self._models[name] = WhisperModel(name, device="cuda", compute_type="int8_float16")
            except Exception as exc:  # noqa: BLE001 - CUDA ausente/sem cuDNN: cai para CPU
                log.info("faster-whisper sem CUDA (%s); usando CPU", exc)
                self._models[name] = WhisperModel(name, device="cpu", compute_type="int8")
        return self._models[name]

    def transcribe(self, audio_path: Path, lang: str) -> list[Word]:
        """Palavras ouvidas no áudio, com timestamps."""
        model = self._model()
        segments, _info = model.transcribe(  # type: ignore[attr-defined]
            str(audio_path), language=lang.split("-")[0], word_timestamps=True, beam_size=1, vad_filter=False
        )
        heard: list[Word] = []
        for seg in segments:
            for w in seg.words or []:
                heard.append(Word(w.word.strip(), float(w.start), float(w.end)))
        return heard

    def align(self, *, audio_path: Path, text: str, lang: str) -> list[Word]:
        return map_to_script(text, self.transcribe(audio_path, lang), audio_path)


def word_error_rate(reference: str, heard: list[str]) -> float:
    """WER entre o roteiro e o que foi ouvido (distância de edição por palavra normalizada)."""
    ref = [t for t in (normalize_token(w) for w in split_words(reference)) if t]
    hyp = [t for t in (normalize_token(w) for w in heard) if t]
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (0 if r == h else 1))
        prev = cur
    return prev[-1] / len(ref)


def map_to_script(text: str, heard: list[Word], audio_path: Path | None = None) -> list[Word]:
    """Casa as palavras do roteiro com as ouvidas e interpola as que não casaram."""
    script = split_words(text)
    if not script:
        return []
    if not heard:
        if audio_path is None:
            return estimate_words(text, 0.0, 1.0)
        audio, rate = read_wav(audio_path)
        return estimate_words(text, *speech_bounds(audio, rate))
    a = [normalize_token(t) for t in script]
    b = [normalize_token(w.text) for w in heard]
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    times: list[tuple[float, float] | None] = [None] * len(script)
    for block in matcher.get_matching_blocks():
        for k in range(block.size):
            w = heard[block.b + k]
            times[block.a + k] = (w.start, w.end)
    # Interpolação para palavras sem correspondência
    result: list[Word] = []
    known = [i for i, t in enumerate(times) if t is not None]
    for i, token in enumerate(script):
        if times[i] is not None:
            s, e = times[i]  # type: ignore[misc]
            result.append(Word(token, s, e))
            continue
        prev_i = max((k for k in known if k < i), default=None)
        next_i = min((k for k in known if k > i), default=None)
        left = times[prev_i][1] if prev_i is not None else (heard[0].start if heard else 0.0)  # type: ignore[index]
        right = times[next_i][0] if next_i is not None else (heard[-1].end if heard else left + 0.3)  # type: ignore[index]
        gap_items = (next_i if next_i is not None else len(script)) - (prev_i if prev_i is not None else -1) - 1
        pos = i - (prev_i if prev_i is not None else -1)
        step = max(0.05, (right - left) / max(1, gap_items + 1))
        s = left + step * (pos - 1) + step * 0.1
        result.append(Word(token, s, s + step * 0.8))
    # Garante ordem crescente
    for i in range(1, len(result)):
        if result[i].start < result[i - 1].start:
            result[i].start = result[i - 1].start
        if result[i].end < result[i].start:
            result[i].end = result[i].start + 0.05
    return result
