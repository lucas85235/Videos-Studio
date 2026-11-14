"""Áudio: trilhas e SFX procedurais (FFmpeg, livres de direitos por construção), montagem da narração e mix final."""

from __future__ import annotations

import random
from pathlib import Path
from typing import NamedTuple

import numpy as np

from studio.media.ffmpeg import SAMPLE_RATE, read_wav, run_ffmpeg, write_wav

# --------------------------------------------------------------------------------------------
# Trilhas procedurais
# --------------------------------------------------------------------------------------------

_ROOTS = [41.2, 43.65, 46.25, 49.0, 51.91, 55.0]
# Climas, dos neutros e leves (a maioria dos nichos) aos sombrios. "neutral" é o padrão: texto sem clima reconhecido cai nele.
MOODS = (
    "neutral", "upbeat", "inspiring", "lofi", "corporate", "playful", "calm",
    "epic", "pulse", "mystery", "tension", "horror", "drone",
)

# Palavras que levam a cada clima. A ordem importa: a primeira regra que casar vence.
# Mantenha igual a `moodKey` em apps/web/src/pages/styles/shared.tsx.
_MOOD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("horror", ("horror", "terror", "medo", "assust")),
    ("tension", ("tension", "tensão", "tensao", "suspense", "thriller")),
    ("mystery", ("myster", "mistér", "mister", "noir", "investiga")),
    ("epic", ("epic", "épic", "epico", "heroic", "grandios")),
    ("lofi", ("lofi", "lo-fi", "lo fi", "chill")),
    ("upbeat", ("upbeat", "animad", "alegre", "energ", "feliz", "happy", "dançant", "dancant")),
    ("inspiring", ("inspir", "motiva", "emocion", "uplifting", "esperan")),
    ("corporate", ("corporat", "corporativ", "empresa", "negócio", "negocio", "business", "tecnolog")),
    ("playful", ("playful", "divertid", "curios", "brincalh", "quirky")),
    ("pulse", ("pulse", "pulso", "batida", "heartbeat", "ritm")),
    ("calm", ("calm", "calma", "relax", "sereno")),
    ("drone", ("drone", "dark", "sombri", "escur", "sinistr", "ominous")),
)


def mood_key(mood: str | None) -> str:
    text = (mood or "").lower()
    for key, words in _MOOD_RULES:
        if any(w in text for w in words):
            return key
    return "neutral"


# --- Peças das trilhas leves (tempo, acordes, notas dedilhadas, bateria simples) ---------------
# Cada nota usa como fase o tempo desde o seu início, então começa em zero e não estala. O relógio
# (`_clock`) guarda os índices de compasso e de nota em variáveis do FFmpeg (st/ld), calculados uma
# vez por amostra. `_pick` escolhe valores por ifs aninhados, e o FFmpeg só avalia o ramo escolhido.

# Notas de cada acorde em razões sobre a tônica (registro entre a tônica e duas oitavas acima).
_CHORDS: dict[str, tuple[float, ...]] = {
    "I": (1.0, 1.25, 1.5, 2.0),
    "IV": (1.0, 1.3333, 1.6667, 2.0),
    "V": (1.125, 1.5, 1.875, 2.25),
    "vi": (1.0, 1.25, 1.6667, 2.0),
    "Imaj7": (1.0, 1.25, 1.5, 1.875),
    "IVmaj7": (1.3333, 1.6667, 2.0, 2.5),
    "vi7": (1.0, 1.25, 1.5, 1.6667),
}
# Fundamental do baixo de cada acorde (razão sobre a tônica do baixo).
_BASS = {"I": 1.0, "IV": 0.6667, "V": 0.75, "vi": 0.8333, "Imaj7": 1.0, "IVmaj7": 0.6667, "vi7": 0.8333}
_PROGRESSIONS = {
    "pop": ("I", "V", "vi", "IV"),
    "lift": ("vi", "IV", "I", "V"),
    "steady": ("I", "IV", "I", "V"),
    "soft": ("I", "vi", "IV", "V"),
    "bounce": ("I", "V", "I", "V"),
    "turn": ("I", "IV", "V", "I"),
}

# Variáveis do relógio: compasso no ciclo, nota no compasso, tempo desde a nota, desde o tempo e desde o compasso.
_BAR, _STEP, _SINCE_STEP, _SINCE_BEAT, _SINCE_BAR = "ld(0)", "ld(1)", "ld(2)", "ld(3)", "ld(4)"


def _clock(step: float, beat: float, steps_per_bar: int, bars: int) -> str:
    bar = step * steps_per_bar
    return (
        f"st(4,mod(t,{bar:.5f}));st(0,floor(mod(t,{bar * bars:.5f})/{bar:.5f}));"
        f"st(1,floor(ld(4)/{step:.5f}));st(2,mod(ld(4),{step:.5f}));st(3,mod(ld(4),{beat:.5f}));"
    )


def _pick(index: str, values: list[str]) -> str:
    """values[index], com ifs aninhados (índice fora da faixa cai no último valor)."""
    expr = values[-1]
    for k in range(len(values) - 2, -1, -1):
        expr = f"if(lt({index},{k + 1}),{values[k]},{expr})"
    return expr


def _arp_freq(base: float, chords: list[str], pattern: tuple[int, ...]) -> str:
    """Frequência da nota dedilhada: um acorde por compasso, `pattern` percorre as notas do acorde."""
    per_bar = [_pick(_STEP, [f"{_CHORDS[c][p]:.4f}" for p in pattern]) for c in chords]
    return f"{base:.3f}*({_pick(_BAR, per_bar)})"


def _bass_freq(base: float, chords: list[str]) -> str:
    return f"{base:.3f}*({_pick(_BAR, [f'{_BASS[c]:.4f}' for c in chords])})"


def _since(period: float, offset: float = 0.0) -> str:
    """Tempo desde o último disparo de um evento periódico."""
    shift = round((period - offset) % period, 5)
    return f"mod(t+{shift:.5f},{period:.5f})" if shift else f"mod(t,{period:.5f})"


def _pluck(freq: str, amp: float, decay: float) -> str:
    return f"{amp}*sin(2*PI*{freq}*{_SINCE_STEP})*exp(-{decay}*{_SINCE_STEP})"


def _kick(m: str, amp: float) -> str:
    return f"{amp}*sin(2*PI*(46+64*exp(-28*{m}))*{m})*exp(-9*{m})"


def _hat(m: str, amp: float) -> str:
    return f"{amp}*(sin(2*PI*6400*{m})+0.6*sin(2*PI*9300*{m}))*exp(-70*{m})"


def _clap(m: str, amp: float) -> str:
    return f"{amp}*(sin(2*PI*190*{m})+0.6*sin(2*PI*340*{m})+0.3*sin(2*PI*4700*{m})*sin(2*PI*5900*{m}))*exp(-24*{m})"


def _woodblock(m: str, amp: float) -> str:
    return f"{amp}*sin(2*PI*1750*{m})*exp(-55*{m})"


def _bass(freq: str, beat: float, amp: float) -> str:
    """Baixo que respira a cada tempo (janela seno: começa e termina em zero, sem estalo)."""
    return f"{amp}*sin(2*PI*{freq}*{_SINCE_BEAT})*sin(PI*{_SINCE_BEAT}/{beat:.5f})"


def _pad(root: float, amp: float, lfo: float) -> str:
    """Tônica + quinta + oitava, fixo (combina com todos os acordes das progressões)."""
    return (
        f"{amp}*(sin(2*PI*{root:.3f}*t)+0.7*sin(2*PI*{root * 1.5:.3f}*t)+0.35*sin(2*PI*{root * 2:.3f}*t))"
        f"*(0.65+0.35*sin(2*PI*{lfo:.4f}*t))"
    )


def _eighths(rnd: random.Random, bpm: tuple[float, float]) -> tuple[float, float]:
    """(colcheia, tempo) em segundos, arredondados para os compassos fecharem certinho."""
    step = round(30 / rnd.uniform(*bpm), 5)
    return step, step * 2


class _LightTrack(NamedTuple):
    bed: str
    """Parte contínua (cama harmônica), calculada no vídeo inteiro."""
    loop: str
    """Parte rítmica (notas, baixo, bateria): exatamente periódica, gerada uma vez por ciclo e repetida."""
    cycle: float
    """Duração do ciclo da parte rítmica, em segundos."""
    filters: str


def _light_track(key: str, f: float, rnd: random.Random) -> _LightTrack:
    """Climas leves (sem bateria pesada nem dissonância): neutro, animado, inspirador, lo-fi, corporativo, curioso."""
    g = f * 4  # tônica das notas (165–220 Hz)
    lfo = rnd.uniform(0.04, 0.09)
    bed = "0"
    if key == "upbeat":
        step, beat = _eighths(rnd, (118, 126))
        chords = list(_PROGRESSIONS[rnd.choice(("pop", "steady"))])
        parts = [
            _pluck(_arp_freq(g * 2, chords, (0, 2, 1, 3, 0, 2, 1, 3)), 0.10, 9),
            _bass(_bass_freq(f * 2, chords), beat, 0.16),
            _kick(_SINCE_BEAT, 0.45),
            _clap(_since(beat * 2, beat), 0.16),
            _hat(_since(beat, step), 0.05),
        ]
        filters = "highpass=f=30,aecho=0.8:0.6:180:0.1"
    elif key == "inspiring":
        step, beat = _eighths(rnd, (88, 96))
        chords = list(_PROGRESSIONS[rnd.choice(("pop", "lift"))])
        bed = f"{_pad(g, 0.06, lfo)}*(0.6+0.4*sin(2*PI*t/16))"
        parts = [
            _pluck(_arp_freq(g * 2, chords, (0, 1, 2, 3, 2, 1, 2, 3)), 0.09, 4.5),
            _bass(_bass_freq(f * 2, chords), beat, 0.14),
            _kick(_since(beat * 2), 0.32),
            _hat(_since(beat, step), 0.025),
        ]
        filters = "lowpass=f=6000,aecho=0.8:0.8:300|600:0.2|0.12"
    elif key == "corporate":
        step, beat = _eighths(rnd, (100, 108))
        chords = list(_PROGRESSIONS[rnd.choice(("steady", "soft"))])
        bed = _pad(g, 0.05, lfo)
        parts = [
            _pluck(_arp_freq(g * 2, chords, (0, 1, 2, 1, 3, 1, 2, 1)), 0.09, 7),
            _bass(_bass_freq(f * 2, chords), beat, 0.12),
            _kick(_SINCE_BEAT, 0.28),
            _hat(_since(beat, step), 0.035),
        ]
        filters = "lowpass=f=8500,aecho=0.8:0.7:240:0.12"
    elif key == "lofi":
        step, beat = _eighths(rnd, (70, 80))
        bar = step * 8
        chords = list(rnd.choice((("Imaj7", "IVmaj7"), ("Imaj7", "vi7"), ("IVmaj7", "Imaj7"))))
        mb = _SINCE_BAR
        env = f"(1-exp(-30*{mb}))*exp(-0.8*{mb})*(1-exp(-6*({bar:.5f}-{mb})))"
        voices = "+".join(f"sin(2*PI*{g:.3f}*{_pick(_BAR, [f'{_CHORDS[c][n]:.4f}' for c in chords])}*{mb})" for n in range(4))
        parts = [
            f"0.05*({voices})*{env}*(0.85+0.15*sin(2*PI*5*{mb}))",
            f"0.14*sin(2*PI*{_bass_freq(f * 2, chords)}*{mb})*sin(PI*{mb}/{bar:.5f})",
            _kick(mb, 0.34),
            _kick(_since(bar, beat * 2.5), 0.24),
            _clap(_since(beat * 2, beat), 0.10),
        ]
        filters = "lowpass=f=2400,aecho=0.8:0.75:280:0.18"
    elif key == "playful":
        step, beat = _eighths(rnd, (126, 136))
        chords = list(_PROGRESSIONS[rnd.choice(("bounce", "turn"))])
        mb = f"mod({_SINCE_BAR},{beat * 2:.5f})"
        parts = [
            _pluck(_arp_freq(g * 2, chords, (0, 2, 1, 3, 2, 0, 3, 1)), 0.12, 16),
            f"0.16*sin(2*PI*{_bass_freq(f * 2, chords)}*{mb})*exp(-10*{mb})",
            _woodblock(_SINCE_BEAT, 0.03),
        ]
        filters = "lowpass=f=7000,aecho=0.8:0.55:150:0.1"
    else:  # neutral: cama leve sem bateria, uma nota longa por tempo sobre um acorde aberto
        step, _ = _eighths(rnd, (36, 40))  # "colcheia" de 36–40 bpm = semínima de 72–80 bpm
        beat = step
        chords = list(_PROGRESSIONS[rnd.choice(("soft", "pop"))])
        bed = _pad(f * 2, 0.07, lfo)
        parts = [_pluck(_arp_freq(g, chords, (0, 1, 2, 3)), 0.10, 2.2)]
        filters = "lowpass=f=3200,aecho=0.8:0.85:350|700:0.25|0.15"
    steps_per_bar = 4 if key == "neutral" else 8
    clock = _clock(step, beat, steps_per_bar, len(chords))
    return _LightTrack(bed, clock + "+".join(parts), step * steps_per_bar * len(chords), filters)


_LIGHT = ("neutral", "upbeat", "inspiring", "corporate", "lofi", "playful")
# Ruído grave de fundo (dá corpo); mais forte nos climas sombrios e no lo-fi (chiado de vinil).
_NOISE = {"drone": 0.05, "horror": 0.05, "tension": 0.05, "lofi": 0.035, "upbeat": 0.008, "corporate": 0.008, "playful": 0.008,
          "neutral": 0.015, "inspiring": 0.015}


def _music_expr(key: str, f: float, rnd: random.Random) -> tuple[str, str]:
    """(expressão aevalsrc, cadeia de filtros) dos climas de uma camada só (ambientes e sombrios)."""
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
    duration = max(2.0, duration)
    fade_out = min(4.0, duration / 4)
    noise_amp = _NOISE.get(key, 0.02)
    master = f"afade=t=in:d=2.5,afade=t=out:st={duration - fade_out:.2f}:d={fade_out:.2f},loudnorm=I=-16:TP=-2:LRA=7[out]"
    noise = ["-f", "lavfi", "-i", f"anoisesrc=color=brown:amplitude={noise_amp}:d={duration:.2f}:r={SAMPLE_RATE}:seed={seed % 100000}"]
    if key in _LIGHT:
        # A parte rítmica se repete a cada ciclo: gera um ciclo só e repete (vídeo longo não fica mais lento por isso).
        track = _light_track(key, f, rnd)
        inputs = [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='{track.bed}':s={SAMPLE_RATE}:d={duration:.2f}",
            *noise,
            "-f", "lavfi", "-i", f"aevalsrc=exprs='{track.loop}':s={SAMPLE_RATE}:d={track.cycle + 0.1:.3f}",
        ]
        graph = (
            f"[2:a]aloop=loop=-1:size={round(track.cycle * SAMPLE_RATE)},atrim=end={duration:.2f}[r];[1:a]lowpass=f=320[n];"
            f"[0:a][r][n]amix=inputs=3:normalize=0,{track.filters},{master}"
        )
    else:
        expr, filters = _music_expr(key, f, rnd)
        inputs = ["-f", "lavfi", "-i", f"aevalsrc=exprs='{expr}':s={SAMPLE_RATE}:d={duration:.2f}", *noise]
        graph = f"[1:a]lowpass=f=320[n];[0:a][n]amix=inputs=2:normalize=0,{filters},{master}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [*inputs, "-filter_complex", graph, "-map", "[out]", "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_path)]
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
