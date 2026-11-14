"""Chamadas ao FFmpeg/FFprobe e utilidades de WAV (numpy)."""

from __future__ import annotations

import json
import logging
import subprocess
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

from studio.config import get_settings

log = logging.getLogger(__name__)

SAMPLE_RATE = 48000

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args: list[str], timeout: float | None = 3600) -> None:
    cmd = [get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg falhou ({proc.returncode}): {proc.stderr.strip()[-2000:]}")


def probe(path: Path) -> dict:
    cmd = [get_settings().ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe falhou: {proc.stderr.strip()[-500:]}")
    return json.loads(proc.stdout)


def duration_of(path: Path) -> float:
    info = probe(path)
    return float(info.get("format", {}).get("duration") or 0.0)


@lru_cache(maxsize=1)
def available_encoders() -> frozenset[str]:
    try:
        proc = subprocess.run(
            [get_settings().ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            creationflags=_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        return frozenset()
    names = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].startswith("V"):
            names.add(parts[1])
    return frozenset(names)


def video_encoder_args(quality: str = "high") -> list[str]:
    """Argumentos de codificação H.264: NVENC quando existir, senão libx264."""
    encoders = available_encoders()
    if "h264_nvenc" in encoders and _nvenc_works():
        cq = "19" if quality == "high" else "24"
        return ["-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", cq, "-b:v", "0", "-pix_fmt", "yuv420p"]
    crf = "18" if quality == "high" else "23"
    return ["-c:v", "libx264", "-preset", "medium", "-crf", crf, "-pix_fmt", "yuv420p"]


@lru_cache(maxsize=1)
def _nvenc_works() -> bool:
    """Testa o NVENC com um quadro preto (drivers podem existir sem GPU disponível)."""
    try:
        cmd = [
            get_settings().ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=256x256:d=0.1",
            "-c:v",
            "h264_nvenc",
            "-f",
            "null",
            "-",
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=_NO_WINDOW)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def to_wav(src: Path, dst: Path, rate: int = SAMPLE_RATE, tempo: float = 1.0) -> Path:
    """Converte qualquer áudio para WAV PCM 16 bits mono (`tempo` muda a velocidade sem mudar o tom)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    filters = ["-af", f"atempo={tempo:.4f}"] if abs(tempo - 1.0) >= 0.005 else []
    run_ffmpeg(["-i", str(src), *filters, "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", str(dst)])
    return dst


def pcm16_to_wav(data: bytes, rate: int, dst: Path, channels: int = 1) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(dst), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(data)
    return dst


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Lê WAV PCM16 como float32 mono em [-1, 1]."""
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"WAV com {width * 8} bits não suportado; converta para 16 bits")
    audio = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)
    return audio, rate


def decode_mono(path: Path, rate: int = 16000, timeout: float = 600) -> np.ndarray:
    """Decodifica qualquer áudio para float32 mono na taxa pedida (ex.: 16 kHz para o Whisper)."""
    cmd = [get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path),
           "-f", "s16le", "-ac", "1", "-ar", str(rate), "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg não decodificou {path.name}: {proc.stderr.decode(errors='replace').strip()[-500:]}")
    return np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32) / 32768.0


def write_wav(path: Path, audio: np.ndarray, rate: int = SAMPLE_RATE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    clipped = np.clip(audio, -1.0, 1.0)
    data = (clipped * 32767.0).astype("<i2").tobytes()
    return pcm16_to_wav(data, rate, path)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as wf:
        return wf.getnframes() / float(wf.getframerate())


def speech_bounds(audio: np.ndarray, rate: int, threshold_db: float = -40.0) -> tuple[float, float]:
    """Início e fim (s) da fala, ignorando silêncio nas pontas."""
    if audio.size == 0:
        return 0.0, 0.0
    win = max(1, int(rate * 0.02))
    usable = audio[: (audio.size // win) * win]
    if usable.size == 0:
        return 0.0, audio.size / rate
    rms = np.sqrt(np.mean(usable.reshape(-1, win) ** 2, axis=1) + 1e-12)
    db = 20 * np.log10(rms)
    voiced = np.where(db > threshold_db)[0]
    if voiced.size == 0:
        return 0.0, audio.size / rate
    start = voiced[0] * win / rate
    end = min(audio.size / rate, (voiced[-1] + 1) * win / rate)
    return start, end
