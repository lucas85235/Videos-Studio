"""Conferência de áudio sem renderizar (ADR 0003, bloco G).

Medidas trazidas do canais (`motor/passos/sfx_guard.py`, `sfx_audit.py` e `mix_preview.py`), com os mesmos números:

- **apito** (`tone_report`): pico estreito entre 1,2 e 9 kHz com 18 dB de destaque sobre a vizinhança por 60 ms ou mais
  (regra 13 do padrão do estúdio: "sem apito, assobio ou efeito agudo");
- **tom puro** (`tonal_fraction`): 30% ou mais da energia em ±60 Hz de um pico acima de 1 kHz;
- **loudness** momentânea (400 ms) e integrada (BS.1770, portas −70 e −10), com o filtro K de `audio._k_weight`;
- **efeitos** (`sfx_events`): cada efeito no áudio separado, comparado com a mediana da voz;
- **música por capítulo** (`chapter_levels`): voz × trilha sob a fala e nas pausas; a regra 12 pede a música de 14 a
  19 dB abaixo da voz.

Tudo em numpy; o FFmpeg só decodifica.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

from studio.config import get_settings
from studio.media.audio import _k_weight
from studio.media.ffmpeg import SAMPLE_RATE, FFmpegError

SR = SAMPLE_RATE
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Efeitos já ouvidos como apito no canais (Money Rerun e Plantão Vice): nunca, mesmo que a medida passe.
SFX_BANNED = frozenset({"whip", "whip2", "ding", "trombone"})

# Apito (sfx_guard)
TONE_LO, TONE_HI = 1200, 9000  # faixa do assobio (Hz)
TONE_PROM_DB = 18.0  # destaque do pico sobre a vizinhança (±6 bins, ~±280 Hz)
TONE_MIN_MS = 60  # duração mínima do tom para contar como apito
TONE_MIN_LEVEL = -45.0  # quadros mais baixos que isso (dB RMS) não contam
# Tom puro (sfx_audit)
PURE_TONE = 0.3
# Efeitos (sfx_audit)
SFX_START_LUFS, SFX_HOLD_LUFS = -50.0, -55.0
SPEECH_LUFS = -45.0  # janelas da voz acima disso entram na mediana
# Máscara de fala (mix_preview)
SPEECH_PRE_S, SPEECH_POST_S = 0.12, 0.3
# Regra 12: música de 14 a 19 dB abaixo da voz
MUSIC_DISTANCE_DB = (14.0, 19.0)

LEVELS = ("ok", "aviso", "erro")


# --------------------------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------------------------


def decode(path: Path, sr: int = SR, channels: int = 1, timeout: float = 1800) -> np.ndarray:
    """Decodifica qualquer áudio em float32 (mono: (n,); estéreo: (n, canais))."""
    cmd = [get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn",
           "-ac", str(channels), "-ar", str(sr), "-f", "f32le", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg não decodificou {Path(path).name}: {proc.stderr.decode(errors='replace').strip()[-500:]}")
    x = np.frombuffer(proc.stdout, dtype="<f4")
    return x.reshape(-1, channels).copy() if channels > 1 else x.copy()


# --------------------------------------------------------------------------------------------
# Apito e tom puro
# --------------------------------------------------------------------------------------------


def tone_report(x: np.ndarray, sr: int = SR) -> dict[str, Any]:
    """Apito/assobio num efeito (= `sfx_guard.tone_report`): pico estreito entre 1,2 e 9 kHz que se destaca da
    vizinhança e dura. Devolve `dur`, `prom` (maior destaque, dB), `hz`, `tonal_ms` e `apito` (tonal_ms ≥ 60)."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    dur = x.size / sr
    n, hop = 1024, 256
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    f = np.fft.rfftfreq(n, 1 / sr)
    band = np.where((f >= TONE_LO) & (f <= TONE_HI))[0]
    w = np.hanning(n)
    half = 6  # ±6 bins ≈ ±280 Hz a 48 kHz
    tonal_frames, best = 0, (0.0, 0.0)
    for i in range(0, x.size - n + 1, hop):
        seg = x[i:i + n]
        lv = 20 * np.log10(np.sqrt(np.mean(seg ** 2)) + 1e-12)
        if lv < TONE_MIN_LEVEL:
            continue
        S = 20 * np.log10(np.abs(np.fft.rfft(seg * w)) + 1e-12)
        k = int(band[int(np.argmax(S[band]))])
        neigh = np.concatenate([S[max(0, k - 3 * half):k - 2], S[k + 3:k + 3 * half]])
        prom = float(S[k] - np.median(neigh))
        # o pico também tem de ser forte no quadro (não um resto de ruído no agudo)
        strong = S[k] > np.max(S) - 30
        if prom > best[0]:
            best = (prom, float(f[k]))
        if prom >= TONE_PROM_DB and strong:
            tonal_frames += 1
    tonal_ms = tonal_frames * hop * 1000 / sr
    return {"dur": round(dur, 2), "prom": round(best[0], 1), "hz": int(round(best[1])), "tonal_ms": int(round(tonal_ms)),
            "apito": bool(tonal_ms >= TONE_MIN_MS)}


def tone_report_file(path: Path) -> dict[str, Any]:
    """`tone_report` de um arquivo (mono, 48 kHz)."""
    return tone_report(decode(path, SR, 1), SR)


def tonal_fraction(seg: np.ndarray, sr: int = SR) -> tuple[float, float]:
    """Fração da energia em ±60 Hz do pico, quando o pico passa de 1 kHz (= `sfx_audit.tonal_frac`).

    Devolve (fração, Hz); acima de `PURE_TONE` (0,3) é tom puro."""
    seg = np.asarray(seg, dtype=np.float64)
    n = 4096
    if seg.size < n:
        return 0.0, 0.0
    f = np.fft.rfftfreq(n, 1 / sr)
    w = np.hanning(n)
    best, fk = 0.0, 0.0
    for i in range(0, seg.size - n + 1, n // 2):
        P = np.abs(np.fft.rfft(seg[i:i + n] * w)) ** 2
        k = int(np.argmax(P))
        if f[k] > 1000:
            fr = float(P[np.abs(f - f[k]) <= 60].sum() / (P.sum() + 1e-12))
            if fr > best:
                best, fk = fr, float(f[k])
    return best, fk


# --------------------------------------------------------------------------------------------
# Loudness (BS.1770, filtro K de audio._k_weight)
# --------------------------------------------------------------------------------------------

_CHUNK_S = 60.0  # o filtro K roda em blocos (um vídeo de 20 min inteiro numa FFT só pesaria GBs)
_WARMUP_S = 0.5  # o filtro "esquenta" com meio segundo antes de cada bloco


def _k_power(x: np.ndarray) -> np.ndarray:
    """Quadrado do sinal com a ponderação K (mono, 48 kHz), em blocos com aquecimento."""
    x = np.asarray(x, dtype=np.float64)
    chunk, warm = int(_CHUNK_S * SR), int(_WARMUP_S * SR)
    if x.size <= chunk:
        return _k_weight(x) ** 2
    out = np.empty(x.size, dtype=np.float64)
    for a in range(0, x.size, chunk):
        b = min(x.size, a + chunk)
        lo = max(0, a - warm)
        out[a:b] = _k_weight(x[lo:b])[a - lo:] ** 2
    return out


def _channel_powers(x: np.ndarray) -> np.ndarray:
    """Potência K somada nos canais (BS.1770: peso 1 para esquerdo e direito)."""
    x = np.asarray(x)
    if x.ndim == 1:
        return _k_power(x)
    return sum(_k_power(x[:, c]) for c in range(x.shape[1]))  # type: ignore[return-value]


def _check_rate(sr: int) -> None:
    if sr != SR:
        raise ValueError(f"o filtro K está calibrado para {SR} Hz (recebi {sr})")


def _block_power(power: np.ndarray, win: int, hop: int) -> np.ndarray:
    if power.size < win:
        return np.array([], dtype=np.float64)
    acc = np.concatenate(([0.0], np.cumsum(power)))
    idx = np.arange(0, power.size - win + 1, hop)
    return (acc[idx + win] - acc[idx]) / win


def momentary_lufs(x: np.ndarray, sr: int = SR) -> np.ndarray:
    """Loudness momentânea (janela de 400 ms, salto de 100 ms) em LUFS, uma por janela."""
    _check_rate(sr)
    ms = _block_power(_channel_powers(x), int(0.4 * sr), sr // 10)
    return -0.691 + 10 * np.log10(ms + 1e-12)


def integrated_lufs(x: np.ndarray, sr: int = SR) -> float:
    """Loudness integrada (BS.1770: blocos de 400 ms a cada 100 ms, porta absoluta −70 e relativa −10).

    `nan` quando o trecho é curto demais ou mudo."""
    _check_rate(sr)
    if np.asarray(x).shape[0] < int(0.4 * sr):
        return math.nan
    ms = _block_power(_channel_powers(x), int(0.4 * sr), sr // 10)
    lk = -0.691 + 10 * np.log10(ms + 1e-12)
    gated = ms[lk > -70.0]
    if gated.size == 0:
        return math.nan
    rel = -0.691 + 10 * np.log10(gated.mean()) - 10.0
    gated = ms[(lk > -70.0) & (lk > rel)]
    if gated.size == 0:
        return math.nan
    return float(-0.691 + 10 * np.log10(gated.mean()))


# --------------------------------------------------------------------------------------------
# Efeitos no áudio separado
# --------------------------------------------------------------------------------------------


def voice_reference(voice_m: np.ndarray) -> float:
    """Mediana da loudness momentânea da voz nas janelas com fala (> −45 LUFS)."""
    speech = voice_m[voice_m > SPEECH_LUFS]
    return float(np.median(speech)) if speech.size else math.nan


def sfx_events(sfx: np.ndarray, voice: np.ndarray, sr: int = SR) -> list[dict[str, Any]]:
    """Cada efeito do áudio separado dos SFX (= `sfx_audit`): começa acima de −50 LUFS e segue enquanto passa de −55.

    Por evento: `t` (s, centro da janela do pico), `lufs`, `vs_voz` (pico − mediana da voz) e `tom`/`tom_hz` (fração
    tonal em 0,5 s a partir do pico)."""
    sfx = np.asarray(sfx, dtype=np.float64)
    ms = momentary_lufs(sfx, sr)
    vref = voice_reference(momentary_lufs(voice, sr))
    hop = sr // 10
    events: list[dict[str, Any]] = []
    i = 0
    while i < ms.size:
        if ms[i] > SFX_START_LUFS:
            j = i
            while j < ms.size and ms[j] > SFX_HOLD_LUFS:
                j += 1
            k = i + int(np.argmax(ms[i:j]))
            a = k * hop
            tf, fk = tonal_fraction(sfx[a:a + int(0.5 * sr)], sr)
            events.append({
                "t": round(a / sr + 0.2, 2),
                "lufs": round(float(ms[k]), 1),
                "vs_voz": round(float(ms[k] - vref), 1) if math.isfinite(vref) else None,
                "tom": round(tf, 2),
                "tom_hz": int(round(fk)),
            })
            i = j
        else:
            i += 1
    return events


# --------------------------------------------------------------------------------------------
# Música × voz por capítulo
# --------------------------------------------------------------------------------------------


def speech_mask(timeline: dict[str, Any], n: int, sr: int = SR) -> np.ndarray:
    """Amostras com fala: palavras das cenas (quadros relativos ao `from` da cena) com 0,12 s antes e 0,3 s depois."""
    fps = int(timeline.get("fps") or 30)
    nf = int(timeline.get("durationInFrames") or math.ceil(n * fps / sr)) + 1
    frames = np.zeros(nf, dtype=bool)
    pre, post = round(SPEECH_PRE_S * fps), round(SPEECH_POST_S * fps)
    for scene in timeline.get("scenes", []):
        f0 = int(scene.get("from", 0))
        for w in scene.get("words") or []:
            a = max(0, f0 + int(w["from"]) - pre)
            b = min(nf, f0 + int(w["to"]) + post)
            if b > a:
                frames[a:b] = True
    idx = np.minimum(np.arange(n, dtype=np.int64) * fps // sr, nf - 1)
    return frames[idx]


def chapter_marks(timeline: dict[str, Any]) -> list[tuple[float, str]]:
    """[(0, "Abertura")] + os capítulos da timeline que começam depois de 0."""
    marks = [(0.0, "Abertura")]
    for ch in timeline.get("chapters") or []:
        start = float(ch.get("start") or 0.0)
        if start > 0:
            marks.append((start, str(ch.get("title") or "Capítulo")))
    return marks


def _lufs_or_none(x: np.ndarray, sr: int) -> float | None:
    v = integrated_lufs(x, sr) if x.size else math.nan
    return round(v, 1) if math.isfinite(v) else None


def _level_row(title: str, a_s: float, b_s: float, voice: np.ndarray, music: np.ndarray, mask: np.ndarray,
               sr: int) -> dict[str, Any]:
    a, b = int(a_s * sr), int(b_s * sr)
    m = mask[a:b]
    v = _lufs_or_none(voice[a:b][m], sr)
    ms = _lufs_or_none(music[a:b][m], sr)
    mp = _lufs_or_none(music[a:b][~m], sr)
    dist = round(v - ms, 1) if v is not None and ms is not None else None
    return {"title": title, "start_s": round(a_s, 2), "end_s": round(b_s, 2), "voice_db": v, "music_speech_db": ms,
            "music_pause_db": mp, "distance_db": dist}


def chapter_levels(voice: np.ndarray, music: np.ndarray, mask: np.ndarray, timeline: dict[str, Any],
                   sr: int = SR) -> dict[str, Any]:
    """Por capítulo: voz sob a fala, trilha sob a fala, trilha nas pausas e a distância (voz − trilha), em LUFS.

    Devolve `{"chapters": [...], "episode": {...}}` (o episódio é o vídeo inteiro)."""
    n = min(voice.size, music.size, mask.size)
    voice, music, mask = voice[:n], music[:n], mask[:n]
    total = n / sr
    marks = chapter_marks(timeline)
    rows = []
    for k, (t0, name) in enumerate(marks):
        t1 = marks[k + 1][0] if k + 1 < len(marks) else total
        if t1 - t0 <= 0:
            continue
        rows.append(_level_row(name, t0, min(t1, total), voice, music, mask, sr))
    return {"chapters": rows, "episode": _level_row("Vídeo inteiro", 0.0, total, voice, music, mask, sr)}


# --------------------------------------------------------------------------------------------
# Itens da conferência
# --------------------------------------------------------------------------------------------


def item(code: str, level: str, title: str, detail: str = "", at_s: float | None = None,
         scene_id: str | None = None) -> dict[str, Any]:
    return {"code": code, "level": level, "title": title, "detail": detail, "at_s": at_s, "scene_id": scene_id}


def worst(items: list[dict[str, Any]]) -> str:
    return max((it["level"] for it in items), key=LEVELS.index, default="ok")


def _fmt(db: float) -> str:
    return f"{db:.1f}".replace(".", ",")


def distance_item(row: dict[str, Any]) -> dict[str, Any] | None:
    """Item da regra 12 para uma linha de `chapter_levels` (None quando não há fala ou trilha no trecho)."""
    dist = row.get("distance_db")
    if dist is None:
        return None
    lo, hi = MUSIC_DISTANCE_DB
    where = f"{row['title']}: " if row.get("title") else ""
    if dist < lo:
        return item("music_distance", "aviso", f"{where}música alta: {_fmt(dist)} dB abaixo da voz (o padrão é de 14 a 19)",
                    "Baixe o volume da trilha deste trecho.", at_s=row.get("start_s"))
    if dist > hi:
        return item("music_distance", "aviso", f"{where}música baixa demais: {_fmt(dist)} dB abaixo da voz",
                    "O padrão é de 14 a 19 dB abaixo da voz; suba um pouco a trilha.", at_s=row.get("start_s"))
    return item("music_distance", "ok", f"{where}música {_fmt(dist)} dB abaixo da voz",
                "Dentro do padrão (de 14 a 19 dB).", at_s=row.get("start_s"))


def sfx_items(events: list[dict[str, Any]], files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Itens dos efeitos: apito/tom puro (erro), efeito mais alto que a voz (aviso)."""
    out: list[dict[str, Any]] = []
    for f in files:
        kind = f.get("kind", "")
        if kind in SFX_BANNED or f.get("apito"):
            out.append(item("sfx_tone", "erro", f"efeito com apito: '{kind}'",
                            f"Tom de {f.get('hz', 0)} Hz por {f.get('tonal_ms', 0)} ms (padrão do estúdio: sem apito)."))
    for e in events:
        if e.get("tom", 0) > PURE_TONE:
            out.append(item("sfx_tone", "erro", "efeito com apito",
                            f"Tom puro em {e.get('tom_hz', 0)} Hz ({round(e['tom'] * 100)}% da energia).",
                            at_s=e.get("t"), scene_id=e.get("scene_id")))
        vs = e.get("vs_voz")
        if vs is not None and vs > 0:
            out.append(item("sfx_loud", "aviso", "efeito mais alto que a voz",
                            f"{_fmt(vs)} dB acima da voz. Baixe o volume dos efeitos.", at_s=e.get("t"),
                            scene_id=e.get("scene_id")))
    return out
