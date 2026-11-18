"""Movimento dentro do plano (ADR 0004 §3.2, `motion.py`): punch, zoom lento, pan, tremida, sobreposição e parado.

Quadros em cinza com 160 px no lado maior, a 5 fps, decodificados numa passada do ffmpeg. Para cada par de quadros
seguidos do mesmo plano:
- **escala**: correlação de fase sobre o espectro de amplitude em coordenadas log-polares (numpy puro). A amplitude não
  muda com translação; na log-polar, escala vira deslocamento no eixo do raio;
- **translação**: correlação de fase direta nos quadros;
- **conferência**: o quadro anterior, ampliado e deslocado pelo que foi medido, é comparado (correlação normalizada)
  com o seguinte. Só par conferido conta como movimento de câmera.

Daí saem:
- **punch**: salto de escala acima de 8% em até 3 pares (0,6 s), sem corte, concentrado (não é rampa);
- **zoom lento**: rampa de escala no mesmo sentido por 0,8 s ou mais, em %/s;
- **pan**: translação constante no mesmo sentido por 0,8 s ou mais (com o zoom lento, a "cara de Ken Burns");
- **tremida**: variância alta da translação depois de tirar a média móvel (alta frequência);
- **sobreposição**: caixa de mudança local estável (meme ou mascote sobre o trailer, ou o contrário: caixa parada
  sobre fundo que mexe) em 3 pares seguidos ou mais;
- **parado**: par sem escala, sem translação e sem mudança.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from studio.config import get_settings
from studio.media.ffmpeg import FFmpegError

FPS = 5.0
SIDE = 160  # lado maior do quadro analisado
LP = 128  # tamanho do quadro reamostrado para a escala
LP_RHO = 128
LP_THETA = 64
LP_RMIN = 2.0
PUNCH_MIN = 0.08  # salto de escala do punch (8%)
PUNCH_PAIRS = 3
PUNCH_SINGLE = 0.045  # o punch se concentra: um par já leva boa parte do salto
RAMP_MIN = 0.004  # por par (0,2 s): 2%/s
RAMP_MAX = 0.06
RAMP_PAIRS = 4
PAN_MIN = 0.004  # fração da largura por par
STATIC_SCALE = 0.004
STATIC_SHIFT = 0.003
STATIC_DIFF = 2.5  # média da diferença absoluta (0 a 255)
NCC_OK = 0.75  # conferência do par (quadro alinhado × seguinte)
NCC_GAIN = 0.6  # o movimento só vale se alinhar tira pelo menos 40% do que sobra de diferença
SHAKE_STD = 0.012  # desvio da translação em alta frequência (fração da largura)
SHAKE_PAIRS = 5
BLOCK_GRID = (16, 9)  # colunas x linhas no quadro deitado
BLOCK_ACTIVE = 10.0
OVERLAY_PAIRS = 3
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------------------------
# Quadros
# --------------------------------------------------------------------------------------------


def frame_size(w: int, h: int, side: int = SIDE) -> tuple[int, int]:
    """Largura e altura (pares) do quadro analisado: `side` no lado maior."""
    if w <= 0 or h <= 0:
        return side, side * 9 // 16 // 2 * 2
    if w >= h:
        return side, max(2, int(round(side * h / w / 2)) * 2)
    return max(2, int(round(side * w / h / 2)) * 2), side


def decode_gray(path: Path, w: int, h: int, fps: float = FPS, timeout: float = 7200) -> tuple[np.ndarray, np.ndarray]:
    """Quadros em cinza (N, altura, largura) uint8 a `fps` e os instantes de cada um."""
    fw, fh = frame_size(w, h)
    cmd = [get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-an", "-sn", "-dn",
           "-vf", f"fps={fps},scale={fw}:{fh},format=gray", "-f", "rawvideo", "-pix_fmt", "gray", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg falhou ao ler os quadros: {proc.stderr.decode(errors='replace').strip()[-500:]}")
    size = fw * fh
    n = len(proc.stdout) // size
    frames = np.frombuffer(proc.stdout[: n * size], dtype=np.uint8).reshape(n, fh, fw)
    times = np.arange(n, dtype=np.float64) / fps
    return frames, times


# --------------------------------------------------------------------------------------------
# Correlação de fase
# --------------------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _hann(h: int, w: int) -> np.ndarray:
    return np.outer(np.hanning(h), np.hanning(w)).astype(np.float64)


def phase_corr(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float]:
    """Deslocamento (dy, dx) que leva `a` em `b` e a altura do pico (0 a 1), com ajuste sub-pixel."""
    fa = np.fft.fft2(a)
    fb = np.fft.fft2(b)
    cross = fb * np.conj(fa)
    cross /= np.abs(cross) + 1e-9
    corr = np.real(np.fft.ifft2(cross))
    h, w = corr.shape
    iy, ix = np.unravel_index(int(np.argmax(corr)), corr.shape)
    peak = float(corr[iy, ix])

    def sub(c_m: float, c_0: float, c_p: float) -> float:
        den = c_m - 2 * c_0 + c_p
        return 0.5 * (c_m - c_p) / den if abs(den) > 1e-12 else 0.0

    dy = iy + sub(corr[(iy - 1) % h, ix], corr[iy, ix], corr[(iy + 1) % h, ix])
    dx = ix + sub(corr[iy, (ix - 1) % w], corr[iy, ix], corr[iy, (ix + 1) % w])
    if dy > h / 2:
        dy -= h
    if dx > w / 2:
        dx -= w
    return float(dy), float(dx), peak


@lru_cache(maxsize=4)
def _lp_grid(n: int) -> tuple[np.ndarray, np.ndarray, float]:
    """Coordenadas (y, x) da amostragem log-polar do espectro centrado de um quadro n x n, e o passo do log-raio."""
    c = n / 2.0
    rmax = n / 2.0 - 1
    dlog = np.log(rmax / LP_RMIN) / LP_RHO
    theta = np.linspace(0, np.pi, LP_THETA, endpoint=False)
    radius = LP_RMIN * np.exp(np.arange(LP_RHO) * dlog)
    ys = c + radius[None, :] * np.sin(theta)[:, None]
    xs = c + radius[None, :] * np.cos(theta)[:, None]
    return ys, xs, float(dlog)


def _bilinear(img: np.ndarray, ys: np.ndarray, xs: np.ndarray) -> np.ndarray:
    h, w = img.shape
    y0 = np.clip(np.floor(ys).astype(int), 0, h - 2)
    x0 = np.clip(np.floor(xs).astype(int), 0, w - 2)
    fy = np.clip(ys - y0, 0, 1)
    fx = np.clip(xs - x0, 0, 1)
    top = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    bot = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return top * (1 - fy) + bot * fy


def _square(frame: np.ndarray) -> np.ndarray:
    return np.asarray(Image.fromarray(frame).resize((LP, LP), Image.Resampling.BILINEAR), dtype=np.float64)


def _log_polar_spectrum(sq: np.ndarray) -> np.ndarray:
    win = _hann(LP, LP)
    mag = np.abs(np.fft.fftshift(np.fft.fft2((sq - sq.mean()) * win)))
    ys, xs, _ = _lp_grid(LP)
    return np.log1p(_bilinear(mag, ys, xs))


def scale_between(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Escala de `a` para `b` (1,2 = o conteúdo ficou 20% maior) e a altura do pico da correlação log-polar."""
    dlog = _lp_grid(LP)[2]
    la = _log_polar_spectrum(_square(a))
    lb = _log_polar_spectrum(_square(b))
    _dtheta, drho, peak = phase_corr(la, lb)
    # o espectro encolhe quando o conteúdo cresce: deslocamento negativo no raio = zoom para dentro
    return float(np.exp(-drho * dlog)), peak


def _warp(a: np.ndarray, scale: float, dy: float, dx: float) -> np.ndarray:
    """`a` ampliado em torno do centro por `scale` e deslocado por (dy, dx)."""
    h, w = a.shape
    c_x, c_y = w / 2.0, h / 2.0
    inv = 1.0 / scale
    # ponto de saída (x, y) vem de ((x - dx - cx)/s + cx, (y - dy - cy)/s + cy)
    coeffs = (inv, 0.0, c_x - (dx + c_x) * inv, 0.0, inv, c_y - (dy + c_y) * inv)
    img = Image.fromarray(a.astype(np.uint8)).transform((w, h), Image.Transform.AFFINE, coeffs,
                                                        resample=Image.Resampling.BILINEAR)
    return np.asarray(img, dtype=np.float64)


def _ncc(a: np.ndarray, b: np.ndarray, margin: float = 0.15) -> float:
    h, w = a.shape
    my, mx = int(h * margin), int(w * margin)
    a = a[my:h - my, mx:w - mx].astype(np.float64)
    b = b[my:h - my, mx:w - mx].astype(np.float64)
    a = a - a.mean()
    b = b - b.mean()
    den = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / den) if den > 1e-6 else (1.0 if np.abs(a - b).mean() < 1 else 0.0)


@dataclass
class Relation:
    scale: float = 1.0
    dx: float = 0.0
    """Translação horizontal em fração da largura."""
    dy: float = 0.0
    diff: float = 0.0
    """Diferença absoluta média sem alinhar (0 a 255)."""
    ncc: float = 0.0
    """Correlação depois de alinhar (1 = a mesma imagem movida)."""

    @property
    def ok(self) -> bool:
        return self.ncc >= NCC_OK


def relate(a: np.ndarray, b: np.ndarray) -> Relation:
    """Escala, translação e conferência de `a` para `b` (quadros em cinza do mesmo tamanho)."""
    af = a.astype(np.float64)
    bf = b.astype(np.float64)
    diff = float(np.abs(af - bf).mean())
    if diff < 0.5:
        return Relation(diff=diff, ncc=1.0)
    estimate, _peak = scale_between(a, b)
    if not 0.6 < estimate < 1.7:
        estimate = 1.0
    h, w = a.shape
    win = _hann(h, w)
    bw = (bf - bf.mean()) * win
    tried: dict[float, tuple[float, float, float]] = {}

    def attempt(scale: float) -> float:
        scale = round(scale, 4)
        if scale not in tried:
            warped = _warp(a, scale, 0.0, 0.0) if abs(scale - 1) > 0.002 else af
            dy, dx, _ = phase_corr((warped - warped.mean()) * win, bw)
            tried[scale] = (_ncc(_warp(a, scale, dy, dx), bf), dy, dx)
        return tried[scale][0]

    # a log-polar dá o palpite; a conferência escolhe entre ele, o parado e vizinhos, e afina em ±1% e ±0,5%
    for scale in (1.0, estimate, *(estimate * f for f in (0.95, 0.975, 1.025, 1.05))):
        attempt(scale)
    best = max(tried, key=lambda s: tried[s][0])
    for delta in (0.01, 0.005):
        for scale in (best * (1 - delta), best * (1 + delta)):
            attempt(scale)
        best = max(tried, key=lambda s: tried[s][0])
    ncc, dy, dx = tried[best]
    ncc_plain = _ncc(af, bf)
    if (1 - ncc) > NCC_GAIN * (1 - ncc_plain):  # o alinhamento quase não ajudou: fica parado
        return Relation(1.0, 0.0, 0.0, diff, ncc_plain)
    return Relation(best, dx / w, dy / h, diff, ncc)


# --------------------------------------------------------------------------------------------
# Conferência dos cortes
# --------------------------------------------------------------------------------------------


def _neighbors(times: np.ndarray, t: float, reach: float) -> tuple[int, int] | None:
    before = int(np.searchsorted(times, t - 1e-3, side="left")) - 1
    after = int(np.searchsorted(times, t - 1e-3, side="left"))
    if before < 0 or after >= len(times):
        return None
    if t - times[before] > reach or times[after] - t > reach:
        return None
    return before, after


def verify_cuts(cands: list[Any], frames: np.ndarray, times: np.ndarray) -> tuple[list[float], list[dict[str, Any]]]:
    """Separa os candidatos a corte que são, na verdade, a mesma imagem ampliada (punch por corte): devolve os cortes
    e os punches (`{t, scale}`)."""
    cuts: list[float] = []
    punches: list[dict[str, Any]] = []
    for cand in cands:
        pair = _neighbors(times, cand.t, 1.5 / FPS)
        if pair is not None:
            rel = relate(frames[pair[0]], frames[pair[1]])
            if rel.ok and abs(np.log(rel.scale)) >= np.log(1 + PUNCH_MIN * 0.6):
                punches.append({"t": round(float(cand.t), 3), "scale": round(rel.scale - 1, 3), "by": "cut"})
                continue
        cuts.append(cand.t)
    return cuts, punches


# --------------------------------------------------------------------------------------------
# Sobreposição (caixa de mudança local)
# --------------------------------------------------------------------------------------------


def _components(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    seen = np.zeros_like(mask, dtype=bool)
    rows, cols = mask.shape
    comps: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(cols):
            if not mask[r, c] or seen[r, c]:
                continue
            stack = [(r, c)]
            seen[r, c] = True
            comp: list[tuple[int, int]] = []
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                    if 0 <= ny < rows and 0 <= nx < cols and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            comps.append(comp)
    return comps


def overlay_box(a: np.ndarray, b: np.ndarray) -> tuple[float, float, float, float] | None:
    """Caixa (x, y, w, h de 0 a 1) da sobreposição entre dois quadros, ou None.

    - Pouca área mudou (4% a 45%) e ela forma uma caixa compacta: algo aparece ou mexe sobre um fundo parado.
    - Quase tudo mudou (60% ou mais) e sobra uma caixa compacta parada (4% a 40%): algo parado sobre fundo que mexe.
    """
    h, w = a.shape
    cols, rows = BLOCK_GRID if w >= h else BLOCK_GRID[::-1]
    bh, bw = h // rows, w // cols
    if bh < 2 or bw < 2:
        return None
    diff = np.abs(a[: rows * bh, : cols * bw].astype(np.int16) - b[: rows * bh, : cols * bw].astype(np.int16))
    blocks = diff.reshape(rows, bh, cols, bw).mean(axis=(1, 3))
    active = blocks > BLOCK_ACTIVE
    frac = float(active.mean())
    if 0.04 <= frac <= 0.45:
        mask = active
    elif frac >= 0.6:
        mask = ~active
    else:
        return None
    comps = _components(mask)
    if not comps:
        return None
    comp = max(comps, key=len)
    total = int(mask.sum())
    size = len(comp)
    if size / (rows * cols) < 0.04 or size / (rows * cols) > 0.45 or size / max(1, total) < 0.7:
        return None
    ys = [p[0] for p in comp]
    xs = [p[1] for p in comp]
    y0, y1, x0, x1 = min(ys), max(ys) + 1, min(xs), max(xs) + 1
    if size / ((y1 - y0) * (x1 - x0)) < 0.5:
        return None
    # caixa colada em duas bordas opostas não é sobreposição (faixa, letreiro rolando)
    if (x0 == 0 and x1 == cols) or (y0 == 0 and y1 == rows):
        return None
    return (x0 / cols, y0 / rows, (x1 - x0) / cols, (y1 - y0) / rows)


def _iou(p: tuple[float, ...], q: tuple[float, ...]) -> float:
    ax0, ay0, aw, ah = p
    bx0, by0, bw, bh = q
    ix = max(0.0, min(ax0 + aw, bx0 + bw) - max(ax0, bx0))
    iy = max(0.0, min(ay0 + ah, by0 + bh) - max(ay0, by0))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


# --------------------------------------------------------------------------------------------
# Análise por plano
# --------------------------------------------------------------------------------------------


@dataclass
class MotionResult:
    pairs: int = 0
    punches: list[dict[str, Any]] = field(default_factory=list)
    """{t, scale, by: "zoom"|"cut"}."""
    ramps: list[dict[str, Any]] = field(default_factory=list)
    """Zoom lento: {start, end, rate_pct_s}."""
    pans: list[dict[str, Any]] = field(default_factory=list)
    shakes: list[dict[str, Any]] = field(default_factory=list)
    overlays: list[dict[str, Any]] = field(default_factory=list)
    """{start, end, box: [x, y, w, h], frame: índice do quadro do meio}."""
    static_s: float = 0.0
    analyzed_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"pairs": self.pairs, "punches": self.punches, "ramps": self.ramps, "pans": self.pans,
                "shakes": self.shakes, "overlays": self.overlays, "static_s": round(self.static_s, 3),
                "analyzed_s": round(self.analyzed_s, 3)}


def _runs(flags: list[bool]) -> list[tuple[int, int]]:
    """Trechos [i, j) de True seguidos."""
    out: list[tuple[int, int]] = []
    start: int | None = None
    for i, flag in enumerate([*flags, False]):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i))
            start = None
    return out


def find_punches(logs: list[float]) -> list[tuple[int, int, float]]:
    """Punches numa série de log-escalas por par: (1º par, quantos pares, log da escala total).

    Começa num par com salto de 4,5% ou mais, estende pelos pares seguintes no mesmo sentido (até 3, cada um com 2%
    ou mais), e vale se o total passa de 8% e os 2 pares de cada lado andam bem menos (senão é rampa)."""
    out: list[tuple[int, int, float]] = []
    k = 0
    n = len(logs)
    while k < n:
        v = logs[k]
        if abs(v) < np.log(1 + PUNCH_SINGLE):
            k += 1
            continue
        span = 1
        while (span < PUNCH_PAIRS and k + span < n and np.sign(logs[k + span]) == np.sign(v)
               and abs(logs[k + span]) >= np.log(1.02)):
            span += 1
        total = float(sum(logs[k:k + span]))
        around = [abs(x) for x in [*logs[max(0, k - 2):k], *logs[k + span:k + span + 2]]]
        rate = sum(around) / len(around) if around else 0.0
        if abs(total) >= np.log(1 + PUNCH_MIN) and rate < abs(total) / span / 3:
            out.append((k, span, total))
            k += span
        else:
            k += 1
    return out


def _shot_pairs(times: np.ndarray, shots: list[dict[str, Any]]) -> list[list[int]]:
    """Índices dos quadros de cada plano regular (só planos com 2 quadros ou mais)."""
    groups: list[list[int]] = []
    for shot in shots:
        if shot.get("micro"):
            continue
        # margem de meio quadro do 5 fps: o quadro na fronteira pode ser do plano vizinho
        idx = [int(i) for i in np.nonzero((times >= shot["a"] + 0.05) & (times < shot["b"] - 0.05))[0]]
        if len(idx) >= 2:
            groups.append(idx)
    return groups


def analyze(frames: np.ndarray, times: np.ndarray, shots: list[dict[str, Any]]) -> MotionResult:
    """Punch, rampas, pans, tremidas, sobreposições e tempo parado, plano a plano."""
    out = MotionResult()
    step = 1.0 / FPS
    for idx in _shot_pairs(times, shots):
        rels = [relate(frames[i], frames[j]) for i, j in zip(idx, idx[1:], strict=False)]
        out.pairs += len(rels)
        out.analyzed_s += step * len(rels)
        logs = [float(np.log(r.scale)) if r.ok else 0.0 for r in rels]
        # parado
        out.static_s += step * sum(
            1 for r in rels
            if r.diff < STATIC_DIFF or (r.ok and abs(r.scale - 1) < STATIC_SCALE
                                        and abs(r.dx) < STATIC_SHIFT and abs(r.dy) < STATIC_SHIFT))
        # punch: salto concentrado em até 3 pares
        used: set[int] = set()
        for k, span, total in find_punches(logs):
            out.punches.append({"t": round(float(times[idx[k]]) + step / 2, 3),
                                "scale": round(float(np.exp(total)) - 1, 3), "by": "zoom"})
            used.update(range(k, k + span))
        # zoom lento (rampa) e pan
        ramp_flags = [i not in used and RAMP_MIN <= abs(v) <= RAMP_MAX for i, v in enumerate(logs)]
        for a, b in _runs(ramp_flags):
            seg = logs[a:b]
            if b - a < RAMP_PAIRS:
                continue
            pos = sum(1 for v in seg if v > 0)
            if max(pos, len(seg) - pos) < 0.8 * len(seg):
                continue
            rate = abs(float(np.mean(seg))) / step * 100
            out.ramps.append({"start": round(float(times[idx[a]]), 3), "end": round(float(times[idx[b]]), 3),
                              "rate_pct_s": round(rate, 2)})
        moves = [(r.dx, r.dy) if r.ok else (0.0, 0.0) for r in rels]
        pan_flags = [i not in used and np.hypot(*m) >= PAN_MIN and not ramp_flags[i] for i, m in enumerate(moves)]
        for a, b in _runs(pan_flags):
            if b - a < RAMP_PAIRS:
                continue
            seg = np.asarray(moves[a:b])
            mean = seg.mean(axis=0)
            norm = float(np.hypot(*mean))
            if norm < PAN_MIN or float(np.linalg.norm(seg - mean, axis=1).mean()) > norm:
                continue
            out.pans.append({"start": round(float(times[idx[a]]), 3), "end": round(float(times[idx[b]]), 3)})
        # tremida: alta frequência da translação
        if len(moves) >= SHAKE_PAIRS:
            dx = np.asarray([m[0] for m in moves])
            dy = np.asarray([m[1] for m in moves])
            shake_flags: list[bool] = []
            for i in range(len(moves)):
                lo, hi = max(0, i - SHAKE_PAIRS // 2), min(len(moves), i + SHAKE_PAIRS // 2 + 1)
                if hi - lo < SHAKE_PAIRS:
                    shake_flags.append(False)
                    continue
                rx = dx[lo:hi] - dx[lo:hi].mean()
                ry = dy[lo:hi] - dy[lo:hi].mean()
                ok = all(rels[j].ok and j not in used for j in range(lo, hi))
                shake_flags.append(ok and float(np.sqrt((rx ** 2 + ry ** 2).mean())) >= SHAKE_STD)
            for a, b in _runs(shake_flags):
                out.shakes.append({"start": round(float(times[idx[a]]), 3), "end": round(float(times[idx[b]]), 3)})
        # sobreposição estável
        boxes = [overlay_box(frames[i], frames[j]) for i, j in zip(idx, idx[1:], strict=False)]
        run: list[int] = []
        for i, box in enumerate([*boxes, None]):
            if box is not None and run and boxes[run[-1]] is not None and _iou(boxes[run[-1]], box) >= 0.5:
                run.append(i)
                continue
            if len(run) >= OVERLAY_PAIRS:
                sel = [boxes[j] for j in run]
                med = tuple(round(float(np.median([b[n] for b in sel])), 3) for n in range(4))  # type: ignore[index]
                mid = idx[run[len(run) // 2] + 1]
                out.overlays.append({"start": round(float(times[idx[run[0]]]), 3),
                                     "end": round(float(times[idx[run[-1] + 1]]), 3),
                                     "box": list(med), "frame": mid})
            run = [i] if box is not None else []
    return out
