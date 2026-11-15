"""Curadoria das trilhas da biblioteca (ADR 0003, bloco G; = `motor/passos/music_curate.py` do canais).

Para cada faixa, acha o trecho mais "só a batida" (pouca coisa na banda da voz, 1 a 4 kHz, pouca "textura" e volume
estável) e prepara um loop alinhado ao compasso, que repete sem emenda. Regra 12 do padrão do estúdio: "só o trecho de
batida da faixa, em loop no compasso, sem vocal nem crescendo".

Por quê (nota do canais): o começo da trilha da abertura, "só a batida meio sinistra", foi aprovado; o resto da faixa
"fica mais alta e com voz e rouba a atenção". As partes com voz são as que a banda de 1 a 4 kHz sobe ~10 dB acima da
batida.

Saída com as chaves do canais (`bpm`, `loop_start_s`, `loop_s`, `loop_bars`, `emenda_batida`, `emenda_onda`, `voz_db`,
`voz_vs_pico_db`, `variacao_db`, `comeco_ok_s`, `textura`, `intro`) mais `ok`, `dur_s` e `loop` (o WAV do loop a
48 kHz no cache). `spectro_png` desenha a faixa (batida em azul, nota sustentada em laranja).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from studio.media.audio import write_stereo_wav
from studio.media.audio_check import decode
from studio.media.ffmpeg import SAMPLE_RATE

ASR = 22050  # análise
HOP_S = 0.25
WIN_S = 0.5
BPM_RANGE = (68, 165)
FAST_BPM = 140  # acima disso a batida detectada é o tempo dobrado (trap): o compasso tem 8 batidas
LOOP_BARS = 8
LOOP_MAX_SHARE = 0.45  # 8 compassos passando disso → 4
FIRST_WINDOW_S = 1.0
TAIL_GUARD_S = 6.0  # o loop não chega nos últimos 6 s da faixa
SHORT_REASON = "faixa curta demais para escolher um trecho"
OVERRIDE_KEYS = ("loop_start_s", "loop_bars", "exact", "avoid", "intro", "beat_mult", "trim_db")


def smooth(x: np.ndarray, k: int = 8) -> np.ndarray:
    """Mediana móvel de `k` pontos (bordas repetidas)."""
    if x.size == 0:
        return x
    pad = np.pad(x, (k // 2, k - k // 2 - 1), mode="edge")
    win = np.lib.stride_tricks.sliding_window_view(pad, k)
    return np.median(win, axis=1)


def features(st: np.ndarray) -> dict[str, np.ndarray]:
    """Medidas por janela de 0,5 s a cada 0,25 s: `vol` (120 Hz a 11 kHz), `pres` (banda da voz, 1 a 4 kHz), `centro`
    (300 Hz a 3,4 kHz no meio − lados) e `flat` (planura de 400 Hz a 3 kHz: 0 = notas isoladas, 1 = faixa cheia, como
    voz picotada ou ruído). Todas com mediana móvel de 8."""
    mid = st.mean(1)
    side = (st[:, 0] - st[:, 1]) / 2
    win, hop = int(WIN_S * ASR), int(HOP_S * ASR)
    n = max(1, (mid.size - win) // hop)
    if mid.size < win:
        mid = np.pad(mid, (0, win - mid.size))
        side = np.pad(side, (0, win - side.size))
    w = np.hanning(win).astype(np.float32)
    f = np.fft.rfftfreq(win, 1 / ASR)
    out: dict[str, list[np.ndarray]] = {"vol": [], "pres": [], "centro": [], "flat": []}
    band = (f >= 400) & (f < 3000)

    def db(X: np.ndarray, lo: float, hi: float) -> np.ndarray:
        return 10 * np.log10(X[:, (f >= lo) & (f < hi)].sum(1) + 1e-10)

    step = 256  # janelas por bloco (memória)
    for b0 in range(0, n, step):
        b1 = min(n, b0 + step)
        idx = np.arange(win)[None, :] + hop * np.arange(b0, b1)[:, None]
        M = np.abs(np.fft.rfft(mid[idx] * w, axis=1)) ** 2
        S = np.abs(np.fft.rfft(side[idx] * w, axis=1)) ** 2
        B = np.sqrt(M[:, band]) + 1e-9
        out["vol"].append(db(M, 120, 11000))
        out["pres"].append(db(M, 1000, 4000))
        out["centro"].append(db(M, 300, 3400) - db(S, 300, 3400))
        out["flat"].append(np.exp(np.log(B).mean(1)) / B.mean(1))
    return {k: smooth(np.concatenate(v)) for k, v in out.items()}


def onsets(mono: np.ndarray) -> tuple[np.ndarray, float]:
    """Fluxo espectral (log) com salto de 256 amostras, normalizado; devolve (fluxo, quadros por segundo)."""
    hop = 256
    n = mono.size // hop
    fr = mono[: n * hop].reshape(n, hop)
    spec = np.abs(np.fft.rfft(fr * np.hanning(hop), axis=1))
    flux = np.maximum(0, np.diff(np.log1p(spec * 50), axis=0)).sum(1)
    return (flux - flux.mean()) / (flux.std() + 1e-9), ASR / hop


def beat_period(flux: np.ndarray, fps: float) -> float:
    """Período da batida (s) pela autocorrelação do fluxo, entre 68 e 165 bpm, com refinamento parabólico."""
    size = 1 << (2 * flux.size - 1).bit_length()
    F = np.fft.rfft(flux, size)
    ac = np.fft.irfft(F * np.conj(F), size)[: flux.size]
    lo, hi = int(fps * 60 / BPM_RANGE[1]), int(fps * 60 / BPM_RANGE[0])
    lag = lo + int(np.argmax(ac[lo:hi]))
    a, b, c = ac[lag - 1], ac[lag], ac[lag + 1]
    return float((lag + 0.5 * (a - c) / (a - 2 * b + c + 1e-9)) / fps)


def ncc(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))


def first_change(F: dict[str, np.ndarray], ref_lo: int, ref_hi: int) -> float:
    """Primeiro momento (depois do trecho de referência) em que a banda da voz sobe 6 dB ou o volume 3 dB, por 2 s."""
    if ref_hi <= ref_lo or ref_lo >= F["pres"].size:
        return 0.0
    rp, rv = np.median(F["pres"][ref_lo:ref_hi]), np.median(F["vol"][ref_lo:ref_hi])
    bad = (F["pres"] > rp + 6) | (F["vol"] > rv + 3)
    need, run = int(2 / HOP_S), 0
    for i in range(ref_hi, bad.size):
        run = run + 1 if bad[i] else 0
        if run >= need:
            return (i - need + 1) * HOP_S
    return bad.size * HOP_S


def _short() -> dict[str, Any]:
    return {"ok": False, "reason": SHORT_REASON}


def curate(path: Path, override: dict[str, Any] | None = None, loop_out: Path | None = None) -> dict[str, Any]:
    """Escolhe o trecho de loop da faixa (= `curate` do canais).

    `override` (ajustes manuais): `loop_start_s` (com `exact`, sem alinhar ao ataque), `loop_bars`, `avoid`
    ([[a, b], ...] trechos proibidos), `intro` (toca o começo original antes do loop), `beat_mult` (corrige tempo
    dobrado/metade) e `trim_db` (volume da cama, vence o corte automático). Com `loop_out`, grava o loop em 48 kHz."""
    ov = dict(override or {})
    st = decode(path, ASR, 2).astype(np.float32)
    dur = st.shape[0] / ASR
    if dur < FIRST_WINDOW_S + TAIL_GUARD_S + 4:
        return {**_short(), "dur_s": round(dur, 2)}
    F = features(st)
    flux, fps = onsets(st.mean(1))
    if flux.size < int(fps * 60 / BPM_RANGE[0]) + 2:
        return {**_short(), "dur_s": round(dur, 2)}
    period = beat_period(flux, fps) * float(ov.get("beat_mult", 1) or 1)
    # trap costuma ser detectado em tempo dobrado (>140 bpm): aí o compasso tem 8 dessas batidas
    bar = (8 if 60 / period > FAST_BPM else 4) * period
    n = F["vol"].size
    # janelas de N compassos, de compasso em compasso: menos banda da voz, menos "textura" e mais estabilidade = melhor
    L = int(ov.get("loop_bars", LOOP_BARS)) * bar
    if L > LOOP_MAX_SHARE * dur and "loop_bars" not in ov:
        L = 4 * bar
    avoid = ov.get("avoid") or []
    best: tuple[float, float] | None = None
    if "loop_start_s" in ov:
        start0 = float(ov["loop_start_s"])
        best = (0.0, start0 if ov.get("exact") else max(0.0, start0 - 0.15))
    else:
        t = FIRST_WINDOW_S
        while t + L < dur - TAIL_GUARD_S:
            if not any(t < b2 and t + L > a2 for a2, b2 in avoid):
                a, b = int(t / HOP_S), min(n, int((t + L) / HOP_S))
                if b > a:
                    pres, vol, fl = F["pres"][a:b], F["vol"][a:b], F["flat"][a:b]
                    score = float(pres.mean() + 1.5 * (vol.std() + pres.std()) + 20 * fl.mean())
                    if best is None or score < best[0]:
                        best = (score, t)
            t += bar
    if best is None:
        return {**_short(), "dur_s": round(dur, 2), "bpm": round(60 / period, 1)}
    start = best[1]
    # alinha o começo no ataque mais forte dentro de um período
    i0 = min(int(start * fps), flux.size - 1)
    if not ov.get("exact"):
        span_beat = flux[i0:i0 + max(1, int(period * fps))]
        i0 = i0 + int(np.argmax(span_beat))
    start = i0 / fps
    k = int(2 * bar * fps)
    # tamanho exato do loop medido DENTRO do trecho: a frase de meio loop se repete na segunda metade (não depende do
    # que vem depois do fim do loop, que pode ser outra parte da música)
    half = L / 2
    span = int(0.3 * period * fps)
    h0 = int(half * fps)
    reps = [m for m in (0, int(bar * fps), int(2 * bar * fps)) if m + k <= h0]

    def rep_score(lag: int) -> float:
        if not reps:
            return 0.0
        return float(np.mean([ncc(flux[i0 + m:i0 + m + k], flux[i0 + m + lag:i0 + m + lag + k]) for m in reps]))

    cands = [(rep_score(lag), lag) for lag in range(h0 - span, h0 + span + 1) if i0 + lag + k < flux.size]
    onset_sim, lag = max(cands) if cands else (0.0, h0)
    j = i0 + 2 * lag
    full = decode(path, SAMPLE_RATE, 2)
    mono = full.mean(1)
    s, e0 = int(start * SAMPLE_RATE), int(j / fps * SAMPLE_RATE)
    w = int(0.08 * SAMPLE_RATE)
    # fim afinado na onda: ±10 ms, janela de 80 ms, passo de 4 amostras
    fine = [(ncc(mono[s:s + w], mono[e:e + w]), e)
            for e in range(e0 - int(0.010 * SAMPLE_RATE), e0 + int(0.010 * SAMPLE_RATE), 4) if e > s and e + w < mono.size]
    wave_sim, e = max(fine) if fine else (0.0, min(e0, mono.size))
    if e - s < SAMPLE_RATE:
        return {**_short(), "dur_s": round(dur, 2), "bpm": round(60 / period, 1)}
    loop = full[s:e]
    a, b = int(start / HOP_S), max(int(start / HOP_S) + 1, int((start + loop.shape[0] / SAMPLE_RATE) / HOP_S))
    # trecho original do começo que dá para usar antes do loop (até a primeira mudança grande)
    intro_ok = first_change(F, int(1 / HOP_S), int(7 / HOP_S))
    out: dict[str, Any] = {
        "ok": True,
        "dur_s": round(dur, 2),
        "bpm": round(60 / period, 1),
        "loop_start_s": round(start, 2),
        "loop_s": round(loop.shape[0] / SAMPLE_RATE, 3),
        "loop_bars": int(round(loop.shape[0] / SAMPLE_RATE / bar)),
        "bar_s": round(bar, 4),
        "emenda_batida": round(onset_sim, 2),
        "emenda_onda": round(wave_sim, 2),
        "voz_db": round(float(F["pres"][a:b].mean() - F["vol"][a:b].mean()), 1),
        "voz_vs_pico_db": round(float(F["pres"][a:b].mean() - np.percentile(F["pres"], 95)), 1),
        "variacao_db": round(float(F["vol"][a:b].std()), 1),
        "comeco_ok_s": round(intro_ok, 1),
        "textura": round(float(F["flat"][a:b].mean()), 2),
        "intro": bool(ov.get("intro", False)),
    }
    if "trim_db" in ov:
        out["trim_db"] = float(ov["trim_db"])
    if loop_out is not None:
        out["loop"] = str(write_stereo_wav(loop_out, loop))
    return out


# --------------------------------------------------------------------------------------------
# Espectrograma (= music_spectro do canais): batida em azul × nota sustentada em laranja
# --------------------------------------------------------------------------------------------

SPEC_SR = 22050
NFFT, SPEC_HOP = 2048, 512
FMIN, FMAX, NB = 40, 8000, 220
PX_S, ROW_H = 16, 230
_MEDIAN_K = 17


def _median_rows(S: np.ndarray, k: int) -> np.ndarray:
    """Mediana móvel de `k` ao longo das colunas (tempo) de cada linha, em blocos (sem scipy)."""
    out = np.empty_like(S)
    pad = np.pad(S, ((0, 0), (k // 2, k // 2)), mode="edge")
    for r0 in range(0, S.shape[0], 64):
        win = np.lib.stride_tricks.sliding_window_view(pad[r0:r0 + 64], k, axis=1)
        out[r0:r0 + 64] = np.median(win, axis=2)
    return out


def spectro_png(path: Path, out: Path, dur: float = 100.0, start: float = 0.0,
                loop: tuple[float, float] | None = None) -> Path:
    """Desenha a faixa: azul = batida (percussivo), laranja = nota sustentada (melodia, voz, acordes); 40 Hz a 8 kHz em
    220 faixas, 16 px/s, 230 px de altura; o trecho do loop vem marcado em verde."""
    from PIL import Image, ImageDraw, ImageFont

    x = decode(path, SPEC_SR, 1)[int(start * SPEC_SR): int((start + dur) * SPEC_SR)].astype(np.float32)
    if x.size < NFFT:
        x = np.pad(x, (0, NFFT - x.size))
    real = x.size / SPEC_SR
    n = max(1, (x.size - NFFT) // SPEC_HOP)
    idx = np.arange(NFFT)[None, :] + SPEC_HOP * np.arange(n)[:, None]
    S = np.abs(np.fft.rfft(x[idx] * np.hanning(NFFT).astype(np.float32), axis=1)).T.astype(np.float32)  # freq × tempo
    H = _median_rows(S, _MEDIAN_K)  # harmônico: mediana no tempo
    P = _median_rows(S.T.copy(), _MEDIAN_K).T  # percussivo: mediana na frequência
    den = H ** 2 + P ** 2 + 1e-12
    mh, mp = H ** 2 / den, P ** 2 / den
    f = np.fft.rfftfreq(NFFT, 1 / SPEC_SR)
    edges = np.geomspace(FMIN, FMAX, NB + 1)

    def logf(M: np.ndarray) -> np.ndarray:
        res = np.zeros((NB, M.shape[1]), dtype=np.float32)
        for i in range(NB):
            m = (f >= edges[i]) & (f < edges[i + 1])
            res[i] = M[m].mean(0) if m.any() else M[int(np.argmin(np.abs(f - edges[i])))]
        return res

    Hs, Ps = logf(S * mh), logf(S * mp)
    ref = max(float(Hs.max()), float(Ps.max()), 1e-12)

    def db(M: np.ndarray) -> np.ndarray:
        return np.clip((20 * np.log10(M / ref + 1e-9) + 60) / 60, 0, 1)[::-1]

    W = max(1, int(dur * PX_S))
    hh = np.asarray(Image.fromarray((db(Hs) * 255).astype(np.uint8)).resize((W, ROW_H))) / 255.0
    pp = np.asarray(Image.fromarray((db(Ps) * 255).astype(np.uint8)).resize((W, ROW_H))) / 255.0
    rgb = np.stack([np.clip(hh + pp * 0.15, 0, 1), np.clip(hh * 0.55 + pp * 0.45, 0, 1), np.clip(pp, 0, 1)], -1)
    im = Image.fromarray((rgb * 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default()
    for fq in (100, 300, 1000, 3000):
        y = ROW_H - ROW_H * math.log(fq / FMIN) / math.log(FMAX / FMIN)
        d.line([(0, y), (W, y)], fill=(90, 90, 90), width=1)
        d.text((4, y - 12), f"{fq if fq < 1000 else str(fq // 1000) + 'k'}Hz", fill=(200, 200, 200), font=font)
    for t in range(0, int(dur) + 1, 10):
        d.line([(t * PX_S, 0), (t * PX_S, ROW_H)], fill=(255, 255, 255), width=1)
        d.text((t * PX_S + 3, ROW_H - 14), f"{int(start + t)}s", fill=(255, 255, 255), font=font)
    if loop:
        a, b = (loop[0] - start) * PX_S, (loop[0] + loop[1] - start) * PX_S
        d.rectangle([a, 1, b, ROW_H - 2], outline=(60, 255, 90), width=4)
        d.text((a + 6, 4), "loop", fill=(60, 255, 90), font=font)
    if real < dur - 0.2 and int(real * PX_S) < W:
        d.rectangle([int(real * PX_S), 0, W, ROW_H], fill=(30, 30, 30))
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out)
    return out
