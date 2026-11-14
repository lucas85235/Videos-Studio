"""Alinhamento de palavras: timestamps por palavra para legendas karaokê e cortes.

1. Se o TTS já devolveu timestamps (ElevenLabs, voz do Windows, mock), eles são usados.
2. faster-whisper local (opcional): transcreve com timestamps e casa com o roteiro conhecido.
3. Estimativa: distribui as palavras pela duração da fala, ponderando o tamanho e as pausas.
"""

from __future__ import annotations

import difflib
import logging
import re
import threading
from pathlib import Path

from studio.config import get_settings
from studio.core.text import normalize_token
from studio.core.text import words as split_words
from studio.media.ffmpeg import decode_mono, read_wav, speech_bounds
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

    _models: dict[str, tuple[object, str]] = {}
    _gpu_broken = False
    _lock = threading.Lock()

    def availability(self) -> tuple[bool, str]:
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False, "faster-whisper não instalado (uv sync --extra asr)"
        return True, "ok"

    def _model(self) -> tuple[object, str]:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise NotConfigured("faster-whisper não instalado") from exc
        name = get_settings().whisper_model
        with self._lock:
            cached = self._models.get(name)
            if cached and not (cached[1] == "cuda" and FasterWhisperAligner._gpu_broken):
                return cached
            model, device = None, "cpu"
            if not FasterWhisperAligner._gpu_broken:
                try:
                    model, device = WhisperModel(name, device="cuda", compute_type="int8_float16"), "cuda"
                except Exception as exc:  # noqa: BLE001 - sem CUDA: cai para CPU
                    log.info("faster-whisper sem CUDA (%s); usando CPU", exc)
            if model is None:
                model = WhisperModel(name, device="cpu", compute_type="int8")
            self._models[name] = (model, device)
            return model, device

    @staticmethod
    def _run(model, audio, lang: str) -> list[Word]:
        segments, _info = model.transcribe(  # type: ignore[attr-defined]
            audio, language=lang.split("-")[0], word_timestamps=True, beam_size=1, vad_filter=False
        )
        return [Word(w.word.strip(), float(w.start), float(w.end)) for seg in segments for w in seg.words or []]

    def transcribe(self, audio_path: Path, lang: str) -> list[Word]:
        """Palavras ouvidas no áudio, com timestamps."""
        # Decodifica pelo ffmpeg do app: a leitura interna do faster-whisper (PyAV) quebra com algumas versões do
        # pacote `av` ("unexpected keyword argument 'metadata_errors'").
        audio = decode_mono(audio_path, 16000)
        model, device = self._model()
        try:
            return self._run(model, audio, lang)
        except RuntimeError as exc:
            # A GPU é aceita ao criar o modelo, mas sem as DLLs do CUDA 12 (cuBLAS/cuDNN) falha só ao transcrever.
            if device == "cpu" or not re.search(r"cuda|cublas|cudnn", str(exc), re.IGNORECASE):
                raise
            log.info("faster-whisper sem as bibliotecas do CUDA (%s); usando CPU", exc)
            FasterWhisperAligner._gpu_broken = True
            model, _ = self._model()
            return self._run(model, audio, lang)

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


LEAK_RUN = 4  # palavras seguidas a mais que contam como fala fora do roteiro


def narration_problems(text: str, heard: list[str] | None, duration_s: float, *, style_prompt: str | None = None,
                       words_per_minute: int = 150, speed: float = 1.0) -> list[str]:
    """Problemas de uma narração: o que a voz falou além do roteiro.

    Com a transcrição (Whisper): instrução de estilo lida em voz alta, texto repetido ou trechos a mais. O erro
    médio por palavra (WER) não basta: 13 palavras de instrução num trecho de 220 dão só 6%. Sem transcrição, só
    a duração: fala muito mais longa do que o roteiro permite (repetição ou instrução lida em trecho curto).
    """
    script = [t for t in (normalize_token(w) for w in split_words(text)) if t]
    problems: list[str] = []
    if heard is not None and script:
        said = [t for t in (normalize_token(w) for w in heard) if t]
        style = {t for t in (normalize_token(w) for w in split_words(style_prompt or "")) if len(t) > 2}
        script_set = set(script)
        extra_runs: list[list[str]] = []
        matcher = difflib.SequenceMatcher(a=script, b=said, autojunk=False)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag in ("insert", "replace") and (j2 - j1) - (i2 - i1) >= LEAK_RUN:
                extra_runs.append(said[j1:j2])
        leaked = [run for run in extra_runs
                  if style and sum(1 for t in run if t in style and t not in script_set) >= 3]
        if leaked:
            problems.append("leu a instrução de estilo em voz alta")
        if len(said) - sum(len(run) for run in leaked) > 1.4 * len(script) + 3:
            problems.append("repetiu trechos do texto")
        elif extra_runs and not leaked:
            longest = max(len(r) for r in extra_runs)
            problems.append(f"falou {longest} palavras que não estão no roteiro")
    elif script:
        # Medido no Gemini TTS: gancho de 26 palavras = 10 s limpo, 19,6 s com a instrução lida, 21,7 s repetido.
        # Narração lenta de verdade (sussurro, pausas) fica abaixo de 1,4× o esperado.
        expected = len(script) / (words_per_minute * (speed or 1.0)) * 60
        limit = expected * 1.5 + 1.5
        if duration_s > limit:
            problems.append(f"durou {duration_s:.0f} s para {len(script)} palavras (o esperado é ~{expected:.0f} s): "
                            "pode ter lido a instrução ou repetido o texto")
    return problems


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
