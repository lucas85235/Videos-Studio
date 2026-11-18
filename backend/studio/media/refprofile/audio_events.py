"""Efeitos sonoros, trilha e drops da referência (ADR 0004 §3.2, `audio_events.py`).

O corte antigo (`reference_video.cuts`) roda com `-an`; aqui o áudio é lido: mono a 22 kHz (`ffmpeg.decode_mono`).

- **Ataques (onsets)**: fluxo espectral (soma das subidas do log-espectro, STFT de 1024/256) com limiar adaptativo
  (mediana de 1 s + margem) e pico local; o instante é afinado no envelope de 2 ms.
- **SFX**: o ataque a ±80 ms de um corte; com transcrição, também o ataque fora das palavras; sem transcrição, o ataque
  isolado (12 dB acima dos 150 ms anteriores) ou grave e forte, com classe definida. Com narração há uns 4 ataques
  por segundo (sílabas): só conta o ataque que se destaca dos vizinhos (quartil de cima em ±3 s). Sem transcrição a
  confiança do campo é baixa (0,4).
- **Classe** grosseira por envelope e centroide: impact, whoosh, pop, riser, scratch (o resto é "other").
- **Trilha**: nível nos respiros (janelas de 0,4 s fora da fala) em relação à fala, em dB. O plano falava em
  `ebur128`/`astats`; o RMS em numpy dá o mesmo número relativo sem outra passada do ffmpeg.
- **Drop**: queda de 6 dB ou mais contra os 2 s anteriores, por 0,4 s ou mais, seguida de pico em até 2,5 s.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from studio.media.ffmpeg import FFmpegError, decode_mono

RATE = 22050
N_FFT = 1024
HOP = 256
ONSET_WINDOW_S = 1.0
ONSET_DELTA = 0.35  # margem do limiar, em desvios-padrão (robustos) do fluxo
ONSET_MIN_GAP_S = 0.1
ONSET_FLOOR_DB = -55.0
NEAR_CUT_S = 0.08
SALIENT_WINDOW_S = 3.0
WORD_PAD_S = 0.05
ISOLATED_DB = 12.0
LEVEL_WIN_S = 0.4
SILENCE_DB = -70.0
DROP_DB = 6.0


@dataclass
class Onset:
    t: float
    strength: float
    sfx: bool = False
    on_cut: bool = False
    cls: str = "other"


@dataclass
class AudioResult:
    has_audio: bool = False
    onsets: list[Onset] = field(default_factory=list)
    music_level_db: float | None = None
    drops: list[float] = field(default_factory=list)

    @property
    def sfx(self) -> list[Onset]:
        return [o for o in self.onsets if o.sfx]

    def to_dict(self) -> dict[str, Any]:
        return {"has_audio": self.has_audio,
                "onsets": [{"t": o.t, "strength": o.strength, "sfx": o.sfx, "on_cut": o.on_cut, "cls": o.cls}
                           for o in self.onsets],
                "music_level_db": self.music_level_db, "drops": self.drops}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioResult:
        return cls(has_audio=bool(data.get("has_audio")),
                   onsets=[Onset(**o) for o in data.get("onsets") or []],
                   music_level_db=data.get("music_level_db"), drops=list(data.get("drops") or []))


def load(path: Path) -> np.ndarray | None:
    """O áudio em mono a 22 kHz, ou None se o vídeo não tem áudio."""
    try:
        audio = decode_mono(path, RATE, timeout=3600)
    except (FFmpegError, OSError, subprocess.SubprocessError):
        return None
    return audio if audio.size else None


def _frames(x: np.ndarray, size: int, hop: int) -> np.ndarray:
    pad = np.pad(x, (size // 2, size // 2))
    n = 1 + max(0, (len(pad) - size) // hop)
    return np.lib.stride_tricks.as_strided(pad, shape=(n, size), strides=(pad.strides[0] * hop, pad.strides[0]))


def spectral_flux(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Fluxo espectral (positivo, log-comprimido) e energia (dB) por quadro de 11,6 ms (centrado)."""
    frames = _frames(audio.astype(np.float32), N_FFT, HOP)
    win = np.hanning(N_FFT).astype(np.float32)
    flux = np.zeros(len(frames), dtype=np.float32)
    level = np.zeros(len(frames), dtype=np.float32)
    prev: np.ndarray | None = None
    chunk = 4096
    for start in range(0, len(frames), chunk):
        block = frames[start:start + chunk]
        mag = np.abs(np.fft.rfft(block * win, axis=1)).astype(np.float32)
        logm = np.log1p(100.0 * mag)
        rms = np.sqrt((block.astype(np.float64) ** 2).mean(axis=1)) + 1e-9
        level[start:start + len(block)] = 20 * np.log10(rms)
        before = np.vstack([prev[None, :] if prev is not None else logm[:1], logm[:-1]])
        flux[start:start + len(block)] = np.maximum(0.0, logm - before).sum(axis=1)
        prev = logm[-1]
    return flux, level


def detect_onsets(audio: np.ndarray) -> list[tuple[float, float]]:
    """(instante, força) de cada ataque: pico local do fluxo acima da mediana de 1 s + margem."""
    flux, level = spectral_flux(audio)
    n = len(flux)
    if n < 3:
        return []
    half = max(1, int(ONSET_WINDOW_S / 2 * RATE / HOP))
    scale = float(np.median(np.abs(flux - np.median(flux)))) * 1.4826 + 1e-6
    out: list[tuple[float, float]] = []
    last_t = -1.0
    min_gap = int(ONSET_MIN_GAP_S * RATE / HOP)
    for i in range(1, n - 1):
        v = flux[i]
        quiet = level[i] < ONSET_FLOOR_DB and level[min(n - 1, i + 2)] < ONSET_FLOOR_DB
        if v <= flux[i - 1] or v < flux[i + 1] or quiet:
            continue
        lo, hi = max(0, i - half), min(n, i + half + 1)
        local = float(np.median(flux[lo:hi]))
        if v < local + ONSET_DELTA * max(scale, local) + 1.0:
            continue
        lo_peak = max(0, i - min_gap)
        if v < flux[lo_peak:i + min_gap + 1].max():
            continue
        t = _refine(audio, i * HOP / RATE)
        if t - last_t < ONSET_MIN_GAP_S:
            continue
        out.append((round(t, 4), round(float(v), 3)))
        last_t = t
    return out


def _refine(audio: np.ndarray, t: float) -> float:
    """Afina o instante no envelope de 2 ms: o 1º ponto, perto de `t`, que passa da metade do pico seguinte."""
    hop = int(0.002 * RATE)
    a = max(0, int((t - 0.04) * RATE))
    b = min(len(audio), int((t + 0.05) * RATE))
    seg = np.abs(audio[a:b])
    if len(seg) < hop * 2:
        return t
    env = seg[: len(seg) // hop * hop].reshape(-1, hop).max(axis=1)
    peak = float(env.max())
    if peak <= 0:
        return t
    first = int(np.argmax(env >= peak * 0.5))
    return (a + first * hop) / RATE


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9)) if len(x) else SILENCE_DB


def classify(audio: np.ndarray, t: float) -> tuple[str, dict[str, float]]:
    """Classe grosseira do som que começa em `t`: impact, whoosh, pop, riser, scratch ou other."""
    i0 = int(t * RATE)
    seg = audio[i0:i0 + int(0.8 * RATE)].astype(np.float64)
    if len(seg) < 256:
        return "other", {}
    hop = int(0.01 * RATE)
    env = np.sqrt((seg[: len(seg) // hop * hop].reshape(-1, hop) ** 2).mean(axis=1)) + 1e-9
    env_db = 20 * np.log10(env)
    peak_i = int(np.argmax(env_db))
    attack_s = peak_i * 0.01
    after = np.nonzero(env_db[peak_i:] < env_db[peak_i] - 20)[0]
    dur_s = (peak_i + (int(after[0]) if len(after) else len(env_db) - peak_i)) * 0.01
    a = i0 + peak_i * hop
    win = audio[a:a + N_FFT].astype(np.float64)
    if len(win) < N_FFT:
        win = np.pad(win, (0, N_FFT - len(win)))
    power = np.abs(np.fft.rfft(win * np.hanning(N_FFT))) ** 2 + 1e-12
    freqs = np.fft.rfftfreq(N_FFT, 1 / RATE)
    centroid = float((freqs * power).sum() / power.sum())
    flatness = float(np.exp(np.mean(np.log(power))) / np.mean(power))
    facts = {"attack_s": round(attack_s, 3), "dur_s": round(dur_s, 3), "centroid": round(centroid, 1),
             "flatness": round(flatness, 4)}
    if attack_s >= 0.4 and flatness >= 0.05:
        return "riser", facts
    if attack_s >= 0.08 and flatness >= 0.15:
        return "whoosh", facts
    if flatness >= 0.3 and centroid >= 2000 and dur_s < 0.4:
        return "scratch", facts
    if attack_s <= 0.03 and centroid < 1500 and dur_s >= 0.15:
        return "impact", facts
    if dur_s < 0.15 and centroid >= 500:
        return "pop", facts
    return "other", facts


def _inside(t: float, spans: list[tuple[float, float]]) -> bool:
    lo, hi = 0, len(spans)
    while lo < hi:  # busca binária no início dos trechos ordenados
        mid = (lo + hi) // 2
        if spans[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return lo > 0 and spans[lo - 1][0] <= t <= spans[lo - 1][1]


def _levels(audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    n = int(LEVEL_WIN_S * RATE)
    count = len(audio) // n
    if count == 0:
        return np.zeros(0), np.zeros(0)
    blocks = audio[: count * n].astype(np.float64).reshape(count, n)
    db = 20 * np.log10(np.sqrt((blocks ** 2).mean(axis=1)) + 1e-9)
    return np.arange(count) * LEVEL_WIN_S, db


def music_level(audio: np.ndarray, words: list[tuple[float, float]] | None) -> float | None:
    """Nível dos respiros menos o nível da fala (dB, negativo; -60 = respiro mudo)."""
    times, db = _levels(audio)
    if not len(db):
        return None
    if words:
        spans = sorted(words)
        speech = [d for t, d in zip(times, db, strict=False)
                  if _inside(t + LEVEL_WIN_S / 2, [(a - 0.1, b + 0.1) for a, b in spans])]
        breath = [d for t, d in zip(times, db, strict=False)
                  if not _inside(t, [(a - 0.15, b + 0.15) for a, b in spans])
                  and not _inside(t + LEVEL_WIN_S, [(a - 0.15, b + 0.15) for a, b in spans])]
        if not speech or not breath:
            return None
        diff = float(np.median(breath) - np.median(speech))
    else:
        loud = db[db > SILENCE_DB]
        if len(loud) < 5:
            return None
        diff = float(np.percentile(db, 10) - np.percentile(loud, 90))
    return round(max(-60.0, min(0.0, diff)), 1)


def find_drops(audio: np.ndarray) -> list[float]:
    """Instantes das quedas de 6 dB+ (0,4 s ou mais) seguidas de pico em até 2,5 s: o "drop" antes da revelação."""
    times, db = _levels(audio)
    out: list[float] = []
    k = int(2.0 / LEVEL_WIN_S)
    i = k
    while i < len(db) - 1:
        before = float(np.mean(db[i - k:i]))
        if before > SILENCE_DB + 20 and db[i] <= before - DROP_DB and db[i + 1] <= before - DROP_DB + 2:
            ahead = db[i + 2:i + 2 + int(2.5 / LEVEL_WIN_S)]
            if len(ahead) and float(ahead.max()) >= before + 1.0:
                out.append(round(float(times[i]), 2))
                i += int(3.0 / LEVEL_WIN_S)
                continue
        i += 1
    return out


def analyze(audio: np.ndarray | None, cut_times: list[float], words: list[tuple[float, float]] | None = None
            ) -> AudioResult:
    """Ataques, SFX (com classe), nível da trilha e drops. `words`: (início, fim) de cada palavra, se houver."""
    if audio is None or not len(audio):
        return AudioResult()
    cuts_sorted = sorted(cut_times)
    spans = sorted((a - WORD_PAD_S, b + WORD_PAD_S) for a, b in words) if words else None
    result = AudioResult(has_audio=True)
    onsets = detect_onsets(audio)
    times = np.asarray([t for t, _ in onsets])
    strengths = np.asarray([s for _, s in onsets])
    for t, strength in onsets:
        j = int(np.searchsorted(cuts_sorted, t))
        near = [abs(cuts_sorted[x] - t) for x in (j - 1, j) if 0 <= x < len(cuts_sorted)]
        # com narração há ~4 ataques por segundo: perto do corte só conta o ataque que se destaca dos vizinhos
        lo, hi = np.searchsorted(times, t - SALIENT_WINDOW_S), np.searchsorted(times, t + SALIENT_WINDOW_S)
        salient = hi - lo < 4 or strength >= float(np.percentile(strengths[lo:hi], 75))
        on_cut = bool(near) and min(near) <= NEAR_CUT_S and salient
        cls, facts = classify(audio, t)
        if on_cut:
            sfx = True
        elif spans is not None:
            sfx = not _inside(t, spans)
        else:
            i0 = int(t * RATE)
            pre = _rms_db(audio[max(0, i0 - int(0.15 * RATE)):max(0, i0 - int(0.01 * RATE))])
            post = _rms_db(audio[i0:i0 + int(0.05 * RATE)])
            centroid = facts.get("centroid", 1000.0)
            sfx = salient and cls != "other" and (post - pre >= ISOLATED_DB or (centroid < 150 and post > -25))
        result.onsets.append(Onset(t=t, strength=strength, sfx=sfx, on_cut=on_cut and sfx, cls=cls))
    result.music_level_db = music_level(audio, words)
    result.drops = find_drops(audio)
    return result
