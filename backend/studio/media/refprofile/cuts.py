"""Cortes e ritmo da referência (ADR 0004 §3.2, `cuts.py`).

O ramo de cortes da passada do ffmpeg (`decode.scan_and_gray`), num vídeo reduzido a 320 px, sem áudio:
- `freezedetect`: trechos de quadro congelado (meme parado, print segurado);
- `mpdecimate`: tira os quadros repetidos. Vídeo de 30 fps entregue a 60 fps repete cada quadro, e a nota de cena do
  ffmpeg (`|mafd - mafd anterior|`) alterna 0 e "mudança inteira": todo movimento parecia corte (na referência GTA,
  781 cortes contra ~360);
- `scdet` + `metadata=print`: a diferença média de cada quadro restante para o anterior (`mafd`, 0 a 100).

A nota de cena (0 a 1) é calculada aqui, na mesma escala do `select='gt(scene,0.3)'` de `media/reference_video.py`:
`(diferença - movimento de fundo)`, com a diferença em níveis de cinza ÷ 100, onde o fundo vem só dos quadros "contínuos" (colados no anterior). O quadro que
chega depois de um trecho parado (descartado pelo `mpdecimate`) não conta como fundo: assim o corte logo depois de um
cartão parado, ou de um micro-plano parado, não some.

Três regras sobre a nota:
- **duro**: nota ≥ 0,30 contra o fundo dos 0,5 s anteriores (o corte de sempre);
- **suave** (gameplay e trailer, onde o movimento já é alto): nota ≥ 0,15 contra a média móvel de 1 s em volta, e pico
  local;
- **relativo** (cena escura, ou o mascote parado cobrindo o meio da tela): a diferença absoluta é pequena (0,08 a 0,15),
  mas é um pico 3 vezes acima da média de 1 s em volta. Na contagem manual da GTA (120 a 180 s), metade dos cortes
  perdidos eram assim. Como pico relativo também aparece em clarão e movimento brusco, o candidato relativo só vale se
  os quadros de antes e de depois NÃO forem a mesma imagem movida (`motion.verify_cuts`).
O candidato que, visto de perto, é só a mesma imagem ampliada vira punch (`motion.verify_cuts`).
Candidatos colados (até 0,12 s) são uma rajada:
- dois candidatos a 2 quadros ou mais um do outro cercam um plano curto de verdade (flash de imagem, meme relâmpago):
  viram um micro-plano;
- dois candidatos a menos de 2 quadros (flash de 1 quadro) ou três ou mais seguidos (transição de glitch, em que todo
  quadro muda) são uma **transição**: contam como um corte só, no primeiro candidato.

Os planos com menos de 0,4 s **ficam**, numa lista à parte (`micro`): são os stingers, flashes e cortes de meme.

O ritmo (`pace`) segue a semântica do `PaceBlock` (ADR 0004 §3.0, emenda da fundação): mediana, quartis, CV, maior
plano e cortes por minuto são dos planos regulares (>= 0,4 s); os micro-planos contam à parte (`micro_*`) e como evento
visual. Dois cuidados para que um trecho atípico não deforme a fórmula:
- **pontas sem cortes**: as janelas de 30 s do início e do fim com taxa abaixo de 25% da mediana das janelas (abertura
  longa, outro, tela final) saem do ritmo (planos pelo meio, taxas pelo trecho que sobra). As janelas
  (`cuts_per_min_windows`) continuam sendo do vídeo inteiro; o gancho (`hook_cuts_per_min`) é sempre dos 30 s iniciais;
- **CV robusto**: o CV é o da lognormal com o mesmo desvio do log das durações (`sqrt(exp(σ²) - 1)`), o mesmo parâmetro
  com que o editor-planejador sorteia os planos. Um plano de 55 s no meio de planos de 1,4 s quase não mexe nele.
A última janela com menos de 5 s não entra em `cuts_per_min_windows` (a taxa de um pedaço curto não é taxa).

`pace` é função pura e é O extrator do ritmo: a métrica da timeline (`core/editing/metrics`) deve chamá-la sobre os
planos da timeline (`shots_from_durations`), e o render é medido por `measure_video`, que também passa por ela.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import numpy as np

HARD_THRESHOLD = 0.30
SOFT_THRESHOLD = 0.15
SOFT_WINDOW_S = 1.0
HARD_BASE_S = 0.5
REL_FLOOR = 0.08  # pico relativo: nota mínima (cena escura)
REL_RATIO = 3.0  # pico relativo: tantas vezes a média de 1 s em volta
START_GUARD_S = 0.1
CONTINUOUS_GAP_S = 0.07  # quadro a até ~2 quadros de 30 fps do anterior: movimento contínuo
BURST_GAP_S = 0.12  # candidatos a até ~3 quadros um do outro: rajada (glitch, flash), não planos
MICRO_MIN_S = 0.06  # rajada de 2 candidatos mais curta que ~2 quadros de 30 fps: flash de transição, não micro-plano
MIN_SHOT_S = 0.4  # abaixo disto o plano é micro (flash, stinger, corte de meme)
WINDOW_S = 30.0  # janelas de cortes por minuto; a primeira é a do gancho
WINDOW_MIN_S = 5.0  # a última janela mais curta que isto não entra nas janelas
EDGE_LOW = 0.25  # janela da ponta com taxa abaixo disto x a mediana das janelas: abertura ou outro, fora do ritmo
FREEZE_NOISE_DB = -60
FREEZE_MIN_S = 0.5
SCALE_W = 320
# `scdet` dá a diferença em % do máximo (0 a 100); o `select` do ffmpeg usa níveis de cinza ÷ 100 (255 → 2,55).
MAFD_TO_SCENE = 2.56 / 100.0

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


def parse_scan(text: str, dur: float | None = None) -> SceneScan:
    """Lê o log do `metadata=print` e do `freezedetect`. `dur`: duração do vídeo, para fechar o congelamento que vai
    até o fim (o ffmpeg não imprime o fim, e o `mpdecimate` já descartou os quadros congelados: o último quadro mantido
    fica perto do início do congelamento)."""
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
    end = float(dur) if dur else (out.times[-1] if out.times else 0.0)
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
    # pico relativo (cena escura): nota pequena, mas muito acima da vizinhança
    taken = {c.t for c in out}
    for i in range(1, n):
        v = m[i]
        if t[i] - t[0] < START_GUARD_S or v < REL_FLOOR or round(float(t[i]), 4) in taken:
            continue
        lo = int(np.searchsorted(t, t[i] - SOFT_WINDOW_S / 2, side="left"))
        hi = int(np.searchsorted(t, t[i] + SOFT_WINDOW_S / 2, side="right"))
        local = mean_between(lo, hi, skip=i)
        peak = v >= m[i - 1] and (i + 1 >= n or v > m[i + 1])
        if peak and v >= REL_RATIO * local:
            out.append(Candidate(round(float(t[i]), 4), round(float(v - local), 4), "relative"))
    return sorted(out, key=lambda c: c.t)


def merge_bursts(cands: list[Candidate], gap_s: float = BURST_GAP_S,
                 micro_min_s: float = MICRO_MIN_S) -> list[Candidate]:
    """Rajadas de candidatos colados (até `gap_s` entre um e outro):
    - dois candidatos a `micro_min_s` ou mais um do outro: um micro-plano (ficam os dois cortes que o cercam);
    - dois candidatos mais colados que isso (flash de 1 quadro) ou três ou mais (glitch, todo quadro muda): uma
      transição, que conta como um corte só (o primeiro)."""
    out: list[Candidate] = []
    group: list[Candidate] = []
    for cand in [*sorted(cands, key=lambda c: c.t), None]:
        if cand is not None and group and cand.t - group[-1].t <= gap_s:
            group.append(cand)
            continue
        if group:
            out.append(group[0])
            if len(group) == 2 and group[1].t - group[0].t >= micro_min_s:
                out.append(group[1])
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


def shots_from_durations(durations: list[float]) -> list[dict[str, Any]]:
    """Planos seguidos com estas durações (os planos de uma timeline), no formato de `shots_from_cuts`."""
    cuts_at: list[float] = []
    t = 0.0
    for d in durations[:-1]:
        t += float(d)
        cuts_at.append(t)
    return shots_from_cuts(cuts_at, float(sum(durations)))


def regular_cut_times(shots: list[dict[str, Any]]) -> list[float]:
    """Instantes dos cortes entre planos regulares: o início de cada plano regular, menos o primeiro."""
    regular = [s for s in shots if not s.get("micro")]
    return [float(s["a"]) for s in regular[1:]]


def windows(cuts: list[float], dur: float) -> list[tuple[float, float, float]]:
    """(início, fim, cortes por minuto) de cada janela de 30 s; a última, se tiver menos de 5 s, fica de fora."""
    out: list[tuple[float, float, float]] = []
    start = 0.0
    while start < dur - 1e-6:
        end = min(dur, start + WINDOW_S)
        if end - start >= WINDOW_MIN_S or not out:
            out.append((start, end, _per_min(sum(1 for t in cuts if start <= t < end), end - start)))
        start = end
    return out


def active_span(cuts: list[float], dur: float) -> tuple[float, float]:
    """O trecho do ritmo: o vídeo sem as janelas das pontas com taxa abaixo de 25% da mediana das janelas (abertura
    longa, outro, tela final). Com menos de 3 janelas, o vídeo inteiro."""
    wins = windows(cuts, dur)
    if len(wins) < 3:
        return 0.0, dur
    ref = float(np.median([w[2] for w in wins]))
    if ref <= 0:
        return 0.0, dur
    lo, hi = 0, len(wins)
    while lo < hi - 1 and wins[lo][2] < EDGE_LOW * ref:
        lo += 1
    while hi - 1 > lo and wins[hi - 1][2] < EDGE_LOW * ref:
        hi -= 1
    end = dur if hi == len(wins) else wins[hi - 1][1]
    return wins[lo][0], end


def robust_cv(durations: list[float]) -> float:
    """CV da lognormal com o mesmo desvio do log das durações, `sqrt(exp(σ²) - 1)`: o parâmetro com que o
    editor-planejador sorteia os planos. Um plano atípico (outro de 55 s) quase não mexe nele."""
    arr = np.asarray([d for d in durations if d > 0], dtype=np.float64)
    if len(arr) < 2:
        return 0.0
    sigma = float(np.log(arr).std())
    return float(np.sqrt(np.expm1(sigma * sigma)))


def pace(shots: list[dict[str, Any]], dur: float) -> dict[str, Any]:
    """Os campos de `PaceBlock` que saem só dos cortes (sem `max_still_s` e `events_per_min`, que dependem dos
    eventos de câmera). Função pura e extrator único do ritmo: a métrica da timeline usa a mesma (ver o topo)."""
    cuts = regular_cut_times(shots)
    lo, hi = active_span(cuts, dur)
    whole = lo <= 0.0 and hi >= dur
    span = max(0.0, hi - lo)

    def inside(t: float) -> bool:
        return whole or lo <= t < hi

    regular = [float(s["dur"]) for s in shots
               if not s.get("micro") and inside((float(s["a"]) + float(s["b"])) / 2)]
    micro = [s for s in shots if s.get("micro") and inside(float(s["a"]))]
    out: dict[str, Any] = {"shots": len(regular), "micro_count": len(micro),
                           "micro_per_min": _per_min(len(micro), span)}
    if regular:
        arr = np.asarray(regular)
        out.update(
            median_s=round(float(np.median(arr)), 3),
            mean_s=round(float(arr.mean()), 3),
            p10_s=round(float(np.percentile(arr, 10)), 3),
            p90_s=round(float(np.percentile(arr, 90)), 3),
            cv=round(robust_cv(regular), 3),
            max_shot_s=round(float(arr.max()), 3),
        )
    out["cuts_per_min"] = _per_min(sum(1 for t in cuts if inside(t)), span)
    out["cuts_per_min_windows"] = [w[2] for w in windows(cuts, dur)]
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
