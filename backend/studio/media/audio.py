"""Áudio: trilhas e SFX procedurais (FFmpeg, livres de direitos por construção), montagem da narração e mix final."""

from __future__ import annotations

import random
from pathlib import Path

import numpy as np

from studio.media.ffmpeg import SAMPLE_RATE, read_wav, run_ffmpeg, write_wav

# --------------------------------------------------------------------------------------------
# Trilhas procedurais
# --------------------------------------------------------------------------------------------

_ROOTS = [41.2, 43.65, 46.25, 49.0, 51.91, 55.0]
MOODS = ("drone", "tension", "horror", "mystery", "epic", "pulse", "calm")


def mood_key(mood: str | None) -> str:
    text = (mood or "").lower()
    for key, words in (
        ("horror", ("horror", "terror", "medo", "assust")),
        ("tension", ("tension", "tensão", "tensao", "suspense", "thriller")),
        ("mystery", ("myster", "mistér", "mister", "noir", "investiga")),
        ("epic", ("epic", "épic", "epico", "heroic", "grandios")),
        ("pulse", ("pulse", "pulso", "batida", "heartbeat", "ritm")),
        ("calm", ("calm", "calma", "relax", "sereno", "lofi", "lo-fi")),
    ):
        if any(w in text for w in words):
            return key
    return "drone"


def _music_expr(key: str, f: float, rnd: random.Random) -> tuple[str, str]:
    """(expressão aevalsrc, cadeia de filtros) para cada clima."""
    lfo = rnd.uniform(0.04, 0.09)
    if key == "tension":
        expr = (
            f"0.16*sin(2*PI*{f}*t)+0.10*sin(2*PI*{f * 1.5:.3f}*t)*(0.5+0.5*sin(2*PI*1.2*t))"
            f"+0.025*sin(2*PI*({f * 16:.2f}+3*sin(2*PI*5*t))*t)+0.06*sin(2*PI*{f * 2:.3f}*t)*(0.6+0.4*sin(2*PI*{lfo}*t))"
        )
        filters = "lowpass=f=2400,aecho=0.8:0.85:500|900:0.25|0.18"
    elif key == "horror":
        expr = (
            f"(0.15*sin(2*PI*{f}*t)+0.11*sin(2*PI*{f * 1.06:.3f}*t)+0.08*sin(2*PI*{f * 1.414:.3f}*t))*(0.6+0.4*sin(2*PI*{lfo}*t))"
            f"+0.35*sin(2*PI*33*t)*exp(-3*mod(t,{rnd.uniform(6.5, 9.5):.2f}))"
        )
        filters = "lowpass=f=1600,aecho=0.8:0.9:900|1700:0.3|0.22"
    elif key == "mystery":
        g = f * 2
        expr = (
            f"(0.10*sin(2*PI*{g:.3f}*t)+0.08*sin(2*PI*{g * 1.189:.3f}*t)+0.07*sin(2*PI*{g * 1.498:.3f}*t))*(0.55+0.45*sin(2*PI*{lfo}*t))"
            f"+0.12*sin(2*PI*{f}*t)"
        )
        filters = "lowpass=f=1800,aecho=0.8:0.88:700|1300:0.28|0.2"
    elif key == "epic":
        g = f * 2
        expr = (
            f"(0.10*sin(2*PI*{g:.3f}*t)+0.08*sin(2*PI*{g * 1.5:.3f}*t)+0.06*sin(2*PI*{g * 2:.3f}*t))*(0.5+0.5*sin(2*PI*0.06*t))"
            f"+0.35*sin(2*PI*50*t)*exp(-6*mod(t,1.0))"
        )
        filters = "lowpass=f=3000,aecho=0.8:0.85:400|800:0.22|0.15"
    elif key == "pulse":
        expr = (
            f"0.45*sin(2*PI*48*t)*exp(-12*mod(t,0.9))+0.3*sin(2*PI*48*t)*exp(-12*mod(t+0.68,0.9))"
            f"+0.08*sin(2*PI*{f * 2:.3f}*t)*(0.6+0.4*sin(2*PI*{lfo}*t))"
        )
        filters = "lowpass=f=1500,aecho=0.8:0.8:300:0.15"
    elif key == "calm":
        g = f * 4
        expr = (
            f"(0.07*sin(2*PI*{g:.3f}*t)+0.06*sin(2*PI*{g * 1.26:.3f}*t)+0.05*sin(2*PI*{g * 1.5:.3f}*t))*(0.6+0.4*sin(2*PI*{lfo}*t))"
        )
        filters = "lowpass=f=2500,aecho=0.8:0.9:800|1500:0.3|0.25"
    else:  # drone
        expr = (
            f"0.20*sin(2*PI*{f}*t)+0.13*sin(2*PI*{f * 1.5:.3f}*t)*(0.6+0.4*sin(2*PI*{lfo}*t))"
            f"+0.07*sin(2*PI*{f * 2:.3f}*t+0.5*sin(2*PI*0.2*t))+0.04*sin(2*PI*{f * 3:.3f}*t)*(0.5+0.5*sin(2*PI*0.11*t))"
        )
        filters = "lowpass=f=1800,aecho=0.8:0.88:600|1100:0.25|0.18"
    return expr, filters


def generate_music(mood: str | None, duration: float, seed: int, out_path: Path) -> Path:
    key = mood_key(mood)
    rnd = random.Random(seed)
    f = rnd.choice(_ROOTS)
    expr, filters = _music_expr(key, f, rnd)
    duration = max(2.0, duration)
    fade_out = min(4.0, duration / 4)
    noise_amp = 0.05 if key in ("drone", "horror", "tension") else 0.02
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='{expr}':s={SAMPLE_RATE}:d={duration:.2f}",
            "-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude={noise_amp}:d={duration:.2f}:r={SAMPLE_RATE}:seed={seed % 100000}",
            "-filter_complex",
            f"[1:a]lowpass=f=320[n];[0:a][n]amix=inputs=2:normalize=0,{filters},"
            f"afade=t=in:d=2.5,afade=t=out:st={duration - fade_out:.2f}:d={fade_out:.2f},loudnorm=I=-16:TP=-2:LRA=7[out]",
            "-map", "[out]", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_path),
        ]
    )
    return out_path


# --------------------------------------------------------------------------------------------
# SFX procedurais
# --------------------------------------------------------------------------------------------

SFX_KINDS = ("whoosh", "impact", "glitch", "static", "riser")


def generate_sfx(kind: str, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "impact":
        args = [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='0.9*sin(2*PI*(42+70*exp(-9*t))*t)*exp(-3.2*t)':s={SAMPLE_RATE}:d=1.6",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.5:d=0.25:r={SAMPLE_RATE}",
            "-filter_complex", "[1:a]lowpass=f=900,afade=t=out:d=0.25[n];[0:a][n]amix=inputs=2:normalize=0,aecho=0.8:0.7:120:0.25[out]",
        ]
    elif kind == "glitch":
        args = [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='0.35*sgn(sin(2*PI*820*t))*lt(mod(t,0.07),0.035)+0.2*sin(2*PI*3100*t)*lt(mod(t,0.05),0.02)':s={SAMPLE_RATE}:d=0.35",
            "-filter_complex", "[0:a]acrusher=bits=6:mix=0.7,afade=t=out:st=0.25:d=0.1[out]",
        ]
    elif kind == "static":
        args = [
            "-f", "lavfi", "-i", f"anoisesrc=color=white:amplitude=0.45:d=0.5:r={SAMPLE_RATE}",
            "-filter_complex", "[0:a]highpass=f=1200,afade=t=in:d=0.05,afade=t=out:st=0.2:d=0.3[out]",
        ]
    elif kind == "riser":
        args = [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='0.25*sin(2*PI*(180*t+260*t*t)*t)*(t/1.5)':s={SAMPLE_RATE}:d=1.5",
            "-filter_complex", "[0:a]aecho=0.8:0.6:90:0.3,afade=t=out:st=1.35:d=0.15[out]",
        ]
    else:  # whoosh
        args = [
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.7:d=0.7:r={SAMPLE_RATE}",
            "-filter_complex", "[0:a]bandpass=f=1400:width_type=o:w=2.5,afade=t=in:d=0.35:curve=exp,afade=t=out:st=0.35:d=0.35[out]",
        ]
    run_ffmpeg([*args, "-map", "[out]", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_path)])
    return out_path


# --------------------------------------------------------------------------------------------
# Montagem e mix
# --------------------------------------------------------------------------------------------


def place_clips(clips: list[tuple[Path, float, float]], total: float, out_path: Path) -> Path:
    """Posiciona clipes (caminho, início em s, ganho linear) numa trilha mono do tamanho total."""
    n = int(np.ceil(total * SAMPLE_RATE)) + 1
    track = np.zeros(n, dtype=np.float32)
    for path, start, gain in clips:
        audio, rate = read_wav(path)
        if rate != SAMPLE_RATE:
            raise ValueError(f"taxa inesperada em {path}: {rate}")
        a = int(round(start * SAMPLE_RATE))
        if a >= n:
            continue
        b = min(n, a + audio.size)
        track[a:b] += audio[: b - a] * gain
    return write_wav(out_path, track)


def mix_tracks(
    voice: Path,
    out_path: Path,
    *,
    music: Path | None = None,
    music_db: float = -22.0,
    ducking: bool = True,
    sfx: Path | None = None,
    loudness: float = -14.0,
) -> Path:
    """Mix final: voz + música (com ducking) + SFX, normalizado em loudness. Sai em AAC estéreo."""
    inputs = ["-i", str(voice)]
    graph: list[str] = ["[0:a]loudnorm=I=-16:TP=-2:LRA=9[vn]"]
    mix_inputs = ["[vout]"]
    if music is not None:
        inputs += ["-i", str(music)]
        gain = music_db + 10.0
        graph.append(f"[1:a]volume={gain:.1f}dB[m]")
        if ducking:
            graph.append("[vn]asplit=2[vout][vsc]")
            graph.append("[m][vsc]sidechaincompress=threshold=0.025:ratio=6:attack=15:release=380:makeup=1[md]")
            mix_inputs.append("[md]")
        else:
            graph.append("[vn]anull[vout]")
            mix_inputs.append("[m]")
    else:
        graph.append("[vn]anull[vout]")
    if sfx is not None:
        idx = 1 + (1 if music is not None else 0)
        inputs += ["-i", str(sfx)]
        mix_inputs.append(f"[{idx}:a]")
    graph.append(f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:normalize=0:duration=first[mx]")
    graph.append(f"[mx]loudnorm=I={loudness}:TP=-1.5:LRA=11[out]")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([*inputs, "-filter_complex", ";".join(graph), "-map", "[out]", "-ac", "2", "-ar", str(SAMPLE_RATE),
                "-c:a", "aac", "-b:a", "192k", str(out_path)])
    return out_path
