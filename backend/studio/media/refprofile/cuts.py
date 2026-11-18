"""Cortes e ritmo da referência (ADR 0004 §3.2, `cuts.py`).

Uma passada só do ffmpeg num vídeo reduzido a 320 px, sem áudio:
- `freezedetect`: trechos de quadro congelado (meme parado, print segurado);
- `mpdecimate`: tira os quadros repetidos. Vídeo de 30 fps entregue a 60 fps repete cada quadro, e a nota de cena do
  ffmpeg (`|mafd - mafd anterior|`) alterna 0 e "mudança inteira": todo movimento parecia corte (na referência GTA,
  781 cortes contra ~360);
- `scdet` + `metadata=print`: a diferença média de cada quadro restante para o anterior (`mafd`, 0 a 100).

A nota de cena (0 a 1) é calculada aqui, na mesma escala do `select='gt(scene,0.3)'` de `media/reference_video.py`:
`(diferença - movimento de fundo)`, com a diferença em níveis de cinza ÷ 100, onde o fundo vem só dos quadros "contínuos" (colados no anterior). O quadro que
chega depois de um trecho parado (descartado pelo `mpdecimate`) não conta como fundo: assim o corte logo depois de um
cartão parado, ou de um micro-plano parado, não some.

Dois limiares sobre a nota:
- **duro**: nota ≥ 0,30 contra o fundo dos 0,5 s anteriores (o corte de sempre);
- **suave** (gameplay e trailer, onde o movimento já é alto): nota ≥ 0,15 contra a média móvel de 1 s em volta, e pico
  local. O candidato que, visto de perto, é só a mesma imagem ampliada vira punch (`motion.verify_cuts`).
Candidatos colados (até 0,12 s) são uma rajada (transição de glitch, flash de vários quadros): viram um micro-plano.

Os planos com menos de 0,4 s **ficam**, numa lista à parte (`micro`): são os stingers, flashes e cortes de meme. O ritmo
(mediana, quartis, CV, cortes por minuto) é medido nos planos regulares, como sempre foi (comparável ao estilo e às
medidas antigas); os micro-planos entram como densidade própria (`micro_per_min`) e como evento visual.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from studio.config import get_settings
from studio.media.ffmpeg import FFmpegError

HARD_THRESHOLD = 0.30
SOFT_THRESHOLD = 0.15
SOFT_WINDOW_S = 1.0
HARD_BASE_S = 0.5
START_GUARD_S = 0.1
CONTINUOUS_GAP_S = 0.07  # quadro a até ~2 quadros de 30 fps do anterior: movimento contínuo
BURST_GAP_S = 0.12  # candidatos a até ~3 quadros um do outro: rajada (glitch, flash), não planos
MIN_SHOT_S = 0.4  # abaixo disto o plano é micro (flash, stinger, corte de meme)
WINDOW_S = 30.0  # janelas de cortes por minuto; a primeira é a do gancho
FREEZE_NOISE_DB = -60
FREEZE_MIN_S = 0.5
SCALE_W = 320
# `scdet` dá a diferença em % do máximo (0 a 100); o `select` do ffmpeg usa níveis de cinza ÷ 100 (255 → 2,55).
MAFD_TO_SCENE = 2.56 / 100.0
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PTS = re.compile(r"pts_time:\s*([0-9.]+)")
_MAFD = re.compile(r"lavfi\.scd\.mafd=([0-9.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([0-9.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([0-9.]+)")


@dataclass
class SceneScan:
    """A passada do ffmpeg: diferença para o quadro anterior (sem os repetidos) e trechos congelados."""

    times: list[float] = field(default_factory=list)
    mafd: list[float] = field(default_factory=list)
    """Diferença média absoluta para o quadro anterior mantido (0 a 100)."""
    freezes: list[tuple[float, float]] = field(default_factory=list)
    """(início, fim) de cada trecho congelado."""


def scan(path: Path, timeout: float = 7200) -> SceneScan:
    """Diferença de cada quadro para o anterior e trechos congelados, numa passada (320 px, sem áudio)."""
    vf = (f"scale={SCALE_W}:-2,freezedetect=n={FREEZE_NOISE_DB}dB:d={FREEZE_MIN_S},"
          "mpdecimate=max=0,scdet=threshold=100,metadata=print:key=lavfi.scd.mafd")
    cmd = [get_settings().ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an", "-sn", "-dn", "-vf", vf,
           "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg falhou ao medir os cortes: {proc.stderr.strip()[-500:]}")
    return parse_scan(proc.stderr)


def parse_scan(text: str) -> SceneScan:
    out = SceneScan()
    pending: float | None = None
    starts: list[float] = []
    for line in text.splitlines():
        if (m := _PTS.search(line)) and "metadata" in line:
            pending = float(m.group(1))
            continue
        if (m := _MAFD.search(line)) and pending is not None:
            out.times.append(pending)
            out.mafd.append(float(m.group(1)))
            pending = None
            continue
        if m := _FREEZE_START.search(line):
            starts.append(float(m.group(1)))
        elif (m := _FREEZE_END.search(line)) and starts:
            out.freezes.append((starts.pop(0), float(m.group(1))))
    # congelado até o fim do vídeo: o ffmpeg não imprime o fim
    end = out.times[-1] if out.times else 0.0
    out.freezes.extend((s, end) for s in starts if end > s)
    return out


@dataclass
class Candidate:
    t: float
    score: float
    kind: str  # "hard" | "soft"


def candidates(times: list[float], mafd: list[float]) -> list[Candidate]:
    """Candidatos a corte pelos dois limiares (duro contra o fundo dos 0,5 s anteriores; suave contra a média móvel de
    1 s em volta). Pelo tempo, não pela contagem: depois do `mpdecimate` os quadros não têm espaçamento fixo."""
    n = len(times)
    if n < 2:
        return []
    t = np.asarray(times, dtype=np.float64)
    m = np.asarray(mafd, dtype=np.float64) * MAFD_TO_SCENE
    gaps = np.diff(t, prepend=t[0])  # o 1º quadro conta como contínuo
    cont = gaps <= CONTINUOUS_GAP_S  # quadros que medem movimento (não chegam depois de um trecho descartado)
    base_vals = np.where(cont, m, 0.0)
    csum = np.concatenate([[0.0], np.cumsum(base_vals)])
    ccount = np.concatenate([[0], np.cumsum(cont.astype(int))])

    def mean_between(lo: int, hi: int, skip: int | None = None) -> float:
        total = csum[hi] - csum[lo]
        count = ccount[hi] - ccount[lo]
        if skip is not None and lo <= skip < hi and cont[skip]:
            total -= base_vals[skip]
            count -= 1
        return float(total / count) if count > 0 else 0.0

    out: list[Candidate] = []
    for i in range(1, n):  # o 1º quadro não tem anterior
        if t[i] - t[0] < START_GUARD_S:  # "corte" nos primeiros quadros é só a falta de fundo para comparar
            continue
        v = m[i]
        if v < SOFT_THRESHOLD:
            continue
        lo = int(np.searchsorted(t, t[i] - HARD_BASE_S, side="left"))
        hard = v - mean_between(lo, i)
        if hard >= HARD_THRESHOLD:
            out.append(Candidate(round(float(t[i]), 4), round(float(hard), 4), "hard"))
            continue
        lo = int(np.searchsorted(t, t[i] - SOFT_WINDOW_S / 2, side="left"))
        hi = int(np.searchsorted(t, t[i] + SOFT_WINDOW_S / 2, side="right"))
        soft = v - mean_between(lo, hi, skip=i)
        peak = v >= m[i - 1] and (i + 1 >= n or v > m[i + 1])
        if peak and soft >= SOFT_THRESHOLD:
            out.append(Candidate(round(float(t[i]), 4), round(float(soft), 4), "soft"))
    return out


def merge_bursts(cands: list[Candidate], gap_s: float = BURST_GAP_S) -> list[Candidate]:
    """Rajada de candidatos colados (transição de glitch, flash de vários quadros) vira um micro-plano só: fica o
    primeiro e o último da rajada (os dois cortes que a cercam)."""
    out: list[Candidate] = []
    group: list[Candidate] = []
    for cand in [*sorted(cands, key=lambda c: c.t), None]:
        if cand is not None and group and cand.t - group[-1].t <= gap_s:
            group.append(cand)
            continue
        if group:
            out.append(group[0])
            if len(group) > 1:
                out.append(group[-1])
        group = [cand] if cand is not None else []
    return out


def shots_from_cuts(cut_times: list[float], dur: float) -> list[dict[str, Any]]:
    """Planos entre os cortes (todos, com `micro` marcado nos que têm menos de 0,4 s)."""
    ts = [0.0] + sorted(t for t in cut_times if 0.0 < t < dur)
    ts.append(dur)
    shots: list[dict[str, Any]] = []
    for a, b in zip(ts, ts[1:], strict=False):
        if b - a <= 1e-6:
            continue
        shots.append({"k": len(shots) + 1, "a": round(a, 3), "b": round(b, 3), "dur": round(b - a, 3),
                      "micro": (b - a) < MIN_SHOT_S})
    return shots


def _per_min(count: float, seconds: float) -> float:
    return round(count / (seconds / 60.0), 2) if seconds > 0 else 0.0


def regular_cut_times(shots: list[dict[str, Any]]) -> list[float]:
    """Instantes dos cortes entre planos regulares: o início de cada plano regular, menos o primeiro."""
    regular = [s for s in shots if not s.get("micro")]
    return [float(s["a"]) for s in regular[1:]]


def pace(shots: list[dict[str, Any]], dur: float) -> dict[str, Any]:
    """Os campos de `PaceBlock` que saem só dos cortes (sem `max_still_s` e `events_per_min`, que dependem dos
    eventos de câmera). Função pura: a métrica do vídeo final usa a mesma."""
    regular = [float(s["dur"]) for s in shots if not s.get("micro")]
    micro = [s for s in shots if s.get("micro")]
    out: dict[str, Any] = {"shots": len(regular), "micro_count": len(micro),
                           "micro_per_min": _per_min(len(micro), dur)}
    if regular:
        arr = np.asarray(regular)
        mean = float(arr.mean())
        out.update(
            median_s=round(float(np.median(arr)), 3),
            mean_s=round(mean, 3),
            p10_s=round(float(np.percentile(arr, 10)), 3),
            p90_s=round(float(np.percentile(arr, 90)), 3),
            cv=round(float(arr.std() / mean), 3) if mean > 0 else 0.0,
            max_shot_s=round(float(arr.max()), 3),
        )
    cuts = regular_cut_times(shots)
    out["cuts_per_min"] = _per_min(len(cuts), dur)
    windows: list[float] = []
    start = 0.0
    while start < dur - 1e-6:
        end = min(dur, start + WINDOW_S)
        if end - start >= 5.0 or not windows:
            windows.append(_per_min(sum(1 for t in cuts if start <= t < end), end - start))
        start = end
    out["cuts_per_min_windows"] = windows
    hook_end = min(WINDOW_S, dur)
    out["hook_cuts_per_min"] = _per_min(sum(1 for t in cuts if t < hook_end), hook_end)
    return out


def freezes_inside_shots(freezes: list[tuple[float, float]], shots: list[dict[str, Any]],
                         lead_s: float = 0.3) -> list[tuple[float, float]]:
    """Congelamentos que começam com o plano já andando (o efeito "freeze"), e não a imagem parada desde o corte
    (print, cartão, foto)."""
    out: list[tuple[float, float]] = []
    for start, end in freezes:
        shot = next((s for s in shots if s["a"] - 1e-6 <= start < s["b"]), None)
        if shot is None or shot.get("micro"):
            continue
        if start - shot["a"] > lead_s and end - start >= 0.3:
            out.append((round(start, 3), round(end, 3)))
    return out
