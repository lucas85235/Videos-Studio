"""Medidas do vídeo final com o FFmpeg (origem: canais `motor/passos/check_render.py` e `estudio-revisao`).

- Volume: loudness integrada perto do alvo do estilo (±1 LU) e pico real até −1,0 dBTP.
- Voz × música: em até 4 respiros entre narrações, a fala precisa ficar 10 dB ou mais acima da música sozinha.
- Trechos pretos no meio do vídeo (`blackdetect`).
- Duração do Short (regra 15 do padrão do canais e limite de 3 min do YouTube Shorts).
"""

from __future__ import annotations

import math
import re
import subprocess
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.media import audio

LUFS_TOLERANCE = 1.0  # ±1 LU do alvo é ok
TRUE_PEAK_MAX = -1.0  # dBTP
PAUSE_MIN_S = 0.25  # respiro entre narrações que vale medir
PAUSE_SLOTS = (1, 3, 5, 7)  # posições 1/8, 3/8, 5/8, 7/8 da lista de respiros
SPEECH_AT = 0.4  # a fala é medida a partir de 40% da narração anterior...
SPEECH_S = 1.0  # ...por 1,0 s
MUSIC_OFFSET_S = 0.03  # a música sozinha começa 0,03 s depois do fim da fala...
MUSIC_MAX_S = 0.18  # ...e dura até 0,18 s (respiro − 0,06 nos respiros curtos)
MUSIC_GAP_MIN_DB = 10.0  # voz menos música: abaixo disso, aviso
SPEECH_FLOOR_DB = -50.0  # fala mais baixa que isso: o trecho não tem voz de verdade; ignora
BLACK_FILTER = "blackdetect=d=0.5:pix_th=0.10"
BLACK_EDGE_S = 0.5  # preto no primeiro ou no último 0,5 s é fade, não problema
SHORT_MAX_S = 180.0  # acima disso o YouTube não trata como Short
SHORT_EDITORIAL_MAX_S = 60.0  # Short de mais de 1 min com reivindicação fica bloqueado no mundo todo

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def item(code: str, level: str, title: str, detail: str = "", at_s: float | None = None,
         scene_id: str | None = None) -> dict[str, Any]:
    """Item de conferência (formato comum do ADR 0003)."""
    return {"code": code, "level": level, "title": title, "detail": detail,
            "at_s": None if at_s is None else round(float(at_s), 2), "scene_id": scene_id}


def clock(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60}:{s % 60:02d}"


def _num(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _finite(value: Any) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _stderr(args: list[str], timeout: float = 3600) -> str:
    cmd = [get_settings().ffmpeg, "-hide_banner", "-nostats", *args, "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)
    return proc.stderr


# ---------------------------------------------------------------- volume


def loudness(video: Path) -> dict[str, float | None]:
    """LUFS integrada, LRA e pico real do áudio do vídeo (reaproveita `audio.measure_loudness`)."""
    m = audio.measure_loudness(video)
    return {"lufs": _finite(m.get("lufs")), "lra": _finite(m.get("lra")), "true_peak": _finite(m.get("true_peak"))}


def loudness_items(measured: dict[str, Any], target: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    lufs = measured.get("lufs")
    if lufs is None:
        out.append(item("lufs", "aviso", "O vídeo está sem som", "a loudness não pôde ser medida (áudio mudo)."))
    elif abs(lufs - target) <= LUFS_TOLERANCE + 1e-9:
        out.append(item("lufs", "ok", f"Volume em {_num(lufs)} LUFS", f"alvo do estilo: {_num(target)} LUFS (±1)."))
    else:
        side = "alto" if lufs > target else "baixo"
        out.append(item("lufs", "aviso", f"Volume {side}: {_num(lufs)} LUFS",
                        f"o alvo do estilo é {_num(target)} LUFS (±1); renderize de novo ou ajuste o volume do estilo."))
    peak = measured.get("true_peak")
    if peak is not None and peak > TRUE_PEAK_MAX + 1e-9:
        out.append(item("true_peak", "aviso", f"Pico real em {_num(peak)} dBTP",
                        "acima de −1,0 dBTP o som pode distorcer depois da compressão do YouTube."))
    return out


# ---------------------------------------------------------------- voz × música


def vol(video: Path, start: float, duration: float) -> float | None:
    """`mean_volume` (dB) do `volumedetect` num trecho (`check_render.vol`)."""
    err = _stderr(["-ss", f"{max(0.0, start):.2f}", "-t", f"{duration:.3f}", "-i", str(video), "-vn", "-af", "volumedetect"],
                  timeout=120)
    found = re.findall(r"mean_volume:\s*(-?inf|-?[\d.]+)\s*dB", err)
    if not found:
        return None
    return _finite(found[-1]) if found[-1] not in ("-inf", "inf") else -91.0


def pauses(narration: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Respiros de 0,25 s ou mais entre narrações; até 4, espalhados (1/8, 3/8, 5/8, 7/8 da lista)."""
    clips = sorted((n for n in narration or [] if isinstance(n, dict)), key=lambda n: float(n.get("start") or 0))
    gaps: list[dict[str, Any]] = []
    for prev, nxt in zip(clips, clips[1:], strict=False):
        end = float(prev.get("start") or 0) + float(prev.get("duration") or 0)
        gap = float(nxt.get("start") or 0) - end
        if gap >= PAUSE_MIN_S - 1e-9:
            gaps.append({"prev_start": float(prev.get("start") or 0), "prev_duration": float(prev.get("duration") or 0),
                         "start": end, "gap": gap, "segment_id": prev.get("segment_id")})
    if len(gaps) <= len(PAUSE_SLOTS):
        return gaps
    picks = sorted({int(len(gaps) * k / 8) for k in PAUSE_SLOTS})
    return [gaps[i] for i in picks]


def voice_vs_music(video: Path, timeline: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Mede fala e música sozinha em cada respiro escolhido. Devolve (medidas, itens)."""
    measured: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for p in pauses(timeline.get("narration") or []):
        speech = vol(video, p["prev_start"] + p["prev_duration"] * SPEECH_AT, SPEECH_S)
        span = min(MUSIC_MAX_S, p["gap"] - 0.06)
        music = vol(video, p["start"] + MUSIC_OFFSET_S, span) if span > 0 else None
        diff = round(speech - music, 1) if speech is not None and music is not None else None
        measured.append({"at_s": round(p["start"], 2), "voice_db": speech, "music_db": music, "diff_db": diff})
        if speech is None or music is None or speech < SPEECH_FLOOR_DB:
            continue
        if diff is not None and diff < MUSIC_GAP_MIN_DB:
            items.append(item("music_distance", "aviso",
                              f"Música só {_num(diff)} dB abaixo da voz no respiro de {clock(p['start'])}",
                              "a música precisa ficar pelo menos 10 dB abaixo da voz (o ideal é 14 a 19 dB): "
                              "abaixe o volume da trilha no estilo ou no projeto.", at_s=p["start"]))
    return measured, items


# ---------------------------------------------------------------- preto


def black_segments(video: Path, duration: float | None = None) -> list[dict[str, float]]:
    """Trechos pretos (`blackdetect=d=0.5:pix_th=0.10`), sem os que tocam o primeiro ou o último 0,5 s."""
    err = _stderr(["-i", str(video), "-an", "-vf", BLACK_FILTER])
    out: list[dict[str, float]] = []
    total = duration
    if total is None:
        m = re.search(r"Duration:\s*(\d+):(\d+):([\d.]+)", err)
        total = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None
    for m in re.finditer(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)\s+black_duration:\s*([\d.]+)", err):
        start, end, dur = float(m.group(1)), float(m.group(2)), float(m.group(3))
        if start < BLACK_EDGE_S:
            continue
        if total is not None and end > total - BLACK_EDGE_S:
            continue
        out.append({"start": round(start, 2), "end": round(end, 2), "dur": round(dur, 2)})
    return out


def black_items(segments: list[dict[str, float]]) -> list[dict[str, Any]]:
    return [
        item("black", "aviso", f"Tela preta de {_num(b['dur'])} s em {clock(b['start'])}",
             "confira se a cena tem imagem e se o render terminou certo.", at_s=b["start"])
        for b in segments
    ]


# ---------------------------------------------------------------- duração


def duration_items(is_short: bool, duration: float | None, editorial: bool) -> list[dict[str, Any]]:
    """Short com mais de 3 min não vira Short (erro); Short de mais de 1 min com mídia editorial (aviso)."""
    if not is_short or duration is None:
        return []
    if duration > SHORT_MAX_S:
        n = int(round(duration))
        return [item("short_duration", "erro", f"Short com {n} s passa de 3 min e não vira Short",
                     "encurte o roteiro para até 3 min (o ideal é menos de 1 min).")]
    if duration > SHORT_EDITORIAL_MAX_S and editorial:
        return [item("short_editorial", "aviso", "Short de mais de 1 min com material oficial",
                     "Short de mais de 1 min com reivindicação de direitos fica bloqueado no mundo todo: "
                     "encurte para até 1 min ou troque a mídia oficial.")]
    return []
