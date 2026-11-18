"""Leitura do vídeo da referência (ADR 0004 §3.2): uma decodificação só para os cortes e para o movimento.

O modo rápido decodificava o vídeo inteiro duas vezes (o `scdet` a 320 px e os quadros em cinza a 160 px/5 fps), além do
áudio. Aqui uma passada do ffmpeg divide o vídeo (`split`) em dois ramos:
- **cortes** (`cuts`): `freezedetect`, `mpdecimate`, `scdet` e `metadata=print`, com o log no stderr;
- **movimento** (`motion`): 5 fps, 160 px, cinza, em `rawvideo` no stdout.

**Origem dos tempos.** Tudo é medido a partir do 1º quadro de vídeo (`setpts=PTS-STARTPTS`): o quadro n do ramo de
movimento está em n/5 s, e os `pts_time` do ramo de cortes estão na mesma régua. O áudio é decodificado desde a 1ª
amostra; a diferença entre o início do áudio e o do vídeo (`stream_offsets`, MKV/TS/lista de edição) é somada aos
instantes do áudio (`audio_events`, fala), para o casamento SFX x corte (±80 ms) comparar a mesma régua.

**Cancelamento.** A leitura confere `cancelled()` a cada bloco de quadros e mata o ffmpeg se o job foi cancelado
(`Cancelled`); o mesmo vale para as tiras da visão.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from studio.config import get_settings
from studio.core.subproc import kill_tree
from studio.media.ffmpeg import FFmpegError
from studio.media.ffmpeg import probe as ff_probe
from studio.media.refprofile import cuts, motion

Cancel = Callable[[], bool]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Cancelled(Exception):  # noqa: N818 - nome do domínio ("cancelado"), como o BuildCancelled
    """O job da análise foi cancelado: nada é gravado."""


def check(cancelled: Cancel | None) -> None:
    if cancelled is not None and cancelled():
        raise Cancelled()


def stream_offsets(path: Path) -> tuple[float, float]:
    """(início do vídeo, início do áudio) em segundos, pelo `start_time` de cada stream (0 quando falta)."""
    try:
        data = ff_probe(path)
    except (FFmpegError, OSError, ValueError):
        return 0.0, 0.0

    def start(kind: str) -> float | None:
        stream = next((s for s in data.get("streams", []) if s.get("codec_type") == kind), None)
        try:
            return float(stream["start_time"]) if stream and stream.get("start_time") not in (None, "N/A") else None
        except (TypeError, ValueError):
            return None

    v, a = start("video"), start("audio")
    return (v or 0.0), (a if a is not None else (v or 0.0))


def audio_offset(path: Path) -> float:
    """Quanto somar a um instante do áudio decodificado para ele cair na régua do vídeo (início do áudio - início do
    vídeo). 0 no MP4 comum."""
    v, a = stream_offsets(path)
    return round(a - v, 4)


class _Reader:
    """Lê o stderr do ffmpeg numa thread (o `metadata=print` escreve muito: sem isso o pipe enche e trava)."""

    def __init__(self, stream: object) -> None:
        self.lines: list[str] = []
        self._stream = stream
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        for raw in iter(self._stream.readline, b""):  # type: ignore[attr-defined]
            self.lines.append(raw.decode("utf-8", errors="replace"))

    def text(self, timeout: float = 30) -> str:
        self._thread.join(timeout)
        return "".join(self.lines)


def scan_and_gray(path: Path, w: int, h: int, dur: float | None = None, *, cancelled: Cancel | None = None,
                  timeout: float = 7200) -> tuple[cuts.SceneScan, np.ndarray, np.ndarray]:
    """Uma passada do ffmpeg: o `SceneScan` dos cortes e os quadros em cinza do movimento (N, altura, largura) com os
    instantes de cada um (na régua do 1º quadro de vídeo)."""
    fw, fh = motion.frame_size(w, h)
    fps = motion.FPS
    graph = (
        "[0:v:0]setpts=PTS-STARTPTS,split=2[c][m];"
        f"[c]scale={cuts.SCALE_W}:-2,freezedetect=n={cuts.FREEZE_NOISE_DB}dB:d={cuts.FREEZE_MIN_S},"
        "mpdecimate=max=0,scdet=threshold=100,metadata=print:key=lavfi.scd.mafd[co];"
        f"[m]fps={fps},scale={fw}:{fh},format=gray[mo]"
    )
    cmd = [get_settings().ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-filter_complex", graph,
           "-map", "[co]", "-f", "null", "-",
           "-map", "[mo]", "-f", "rawvideo", "-pix_fmt", "gray", "pipe:1"]
    check(cancelled)
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=_NO_WINDOW)
    assert proc.stdout is not None and proc.stderr is not None
    reader = _Reader(proc.stderr)
    size = fw * fh
    chunk = size * 25  # 5 s de quadros por leitura
    parts: list[bytes] = []
    started = time.monotonic()
    try:
        while True:
            buf = proc.stdout.read(chunk)
            if buf:
                parts.append(buf)
            if cancelled is not None and cancelled():
                raise Cancelled()
            if time.monotonic() - started > timeout:
                raise FFmpegError("ffmpeg demorou demais ao ler o vídeo")
            if len(buf) < chunk:
                break
        proc.wait(timeout=60)
    except BaseException:
        kill_tree(proc)
        raise
    text = reader.text()
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg falhou ao ler o vídeo: {text.strip()[-500:]}")
    data = b"".join(parts)
    n = len(data) // size
    frames = np.frombuffer(data[: n * size], dtype=np.uint8).reshape(n, fh, fw)
    times = np.arange(n, dtype=np.float64) / fps
    return cuts.parse_scan(text, dur), frames, times
