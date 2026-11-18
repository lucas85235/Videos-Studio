"""Áudio: trilhas e SFX procedurais (FFmpeg, livres de direitos por construção), montagem da narração e mix final."""

from __future__ import annotations

import math
import random
import re
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

import numpy as np

from studio.config import get_settings
from studio.media.ffmpeg import (
    SAMPLE_RATE,
    FFmpegError,
    pcm16_to_wav,
    read_wav,
    run_ffmpeg,
    wav_duration,
    write_wav,
)

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
    """Trilha procedural com `duration` s, em -16 LUFS.

    Só leva fades curtos contra estalo: a entrada e a saída de verdade ficam no mix (`mix_tracks`), que sabe
    onde o vídeo acaba e se é short (a trilha já sobe no gancho) ou vídeo longo.
    """
    key = mood_key(mood)
    rnd = random.Random(seed)
    f = rnd.choice(_ROOTS)
    duration = max(2.0, duration)
    noise_amp = _NOISE.get(key, 0.02)
    master = f"afade=t=in:d=0.05,afade=t=out:st={duration - 0.5:.2f}:d=0.5,loudnorm=I=-16:TP=-2:LRA=7[out]"
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

TRANSITION_SFX_KINDS = ("whoosh", "impact", "glitch", "static", "riser")
"""Os efeitos de antes do ADR 0004 (transição, cartão e carimbo): a timeline adianta cada um pelo `SFX_LEAD`."""
EVENT_SFX_KINDS = ("pop", "tick", "click", "thump", "boom", "scratch", "shutter", "swish", "bass_drop", "riser_short")
"""Efeitos de evento (ADR 0004 §3.8), presos a palavras: todos sem tom afinado, gerados em numpy com semente fixa."""
SFX_KINDS = TRANSITION_SFX_KINDS + EVENT_SFX_KINDS
SFX_VERSION = 4
"""Versão dos arquivos de efeito no cache. v4: efeitos de evento (`EVENT_SFX_KINDS`); os cinco de antes não mudaram
de som (um tipo novo pedido antes dela saía como whoosh e ficou no cache com a chave da v3). v3: glitch e riser sem tom
agudo afinado (passam na guarda de apito, regra 13 do padrão do estúdio); v2: impacto em camadas e loudness
normalizada por arquivo."""
SFX_RETUNED = frozenset({"glitch", "riser"})
"""Tipos que mudaram de som em `SFX_RETUNED_VERSION`: um mix com eles leva essa versão na chave (é refeito) e a
conferência acusa a prévia montada antes dela (que tinha apito)."""
SFX_RETUNED_VERSION = 3
"""Versão em que os `SFX_RETUNED` mudaram de som. Fica separada da `SFX_VERSION`: subir a versão por causa de um tipo
novo não refaz nem acusa o mix de quem não mudou."""

VOICE_LUFS = -16.0
"""Loudness da narração no mix, antes do master. Os SFX são normalizados nessa mesma referência."""
SFX_TRIM_DB = {
    "whoosh": 0.0, "impact": 0.0, "glitch": -4.0, "static": -4.0, "riser": -3.0,
    # Efeitos de evento: os curtos (tick, click, pop, shutter) ficam abaixo da referência porque a loudness
    # momentânea mede 400 ms; com o pico em -1 dBFS eles já soam como um estalo forte.
    "pop": -9.0, "tick": -14.0, "click": -13.0, "thump": -4.0, "boom": -1.0, "scratch": -5.0, "shutter": -10.0,
    "swish": -4.0, "bass_drop": -2.0, "riser_short": -4.0,
}
"""Ajuste fino por tipo sobre a referência: glitch e chiado são ásperos, o riser é longo."""
SFX_HIT_S = {"whoosh": 0.35, "impact": 0.0, "glitch": 0.0, "static": 0.05, "riser": 1.5}
"""Onde fica o ponto forte de cada efeito de transição, em s desde o início do arquivo (o pico do whoosh, o golpe do
impacto). É o mesmo número do `SFX_LEAD` da timeline (conferido nos testes)."""
EVENT_SFX_HIT_S = {
    "pop": 0.0, "tick": 0.0, "click": 0.0, "thump": 0.0, "boom": 0.0, "scratch": 0.0, "shutter": 0.0,
    "swish": 0.12, "bass_drop": 0.0, "riser_short": 0.6,
}
"""Ponto forte dos efeitos de evento. A timeline não adianta estes: o evento chega com `at` (a palavra) e a mixagem
põe o arquivo em `at - hit_s` (`sfx_library.event_start`)."""


def sfx_hit_s(kind: str) -> float:
    """Ponto forte (s desde o início do arquivo) de um efeito procedural; 0 para tipo desconhecido."""
    return SFX_HIT_S.get(kind, EVENT_SFX_HIT_S.get(kind, 0.0))
_PEAK_CEILING = 10 ** (-1 / 20)  # -1 dBFS

# Ponderação K do BS.1770 a 48 kHz: prateleira de agudos + passa-altas RLB (coeficientes b, a de cada biquad).
_K_STAGES = (
    ((1.53512485958697, -2.69169618940638, 1.19839281085285), (1.0, -1.69065929318241, 0.73248077421585)),
    ((1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621)),
)


def _k_weight(audio: np.ndarray) -> np.ndarray:
    """Aplica a ponderação K (no domínio da frequência, com folga para a cauda dos filtros)."""
    size = audio.size + SAMPLE_RATE
    nfft = 1 << (size - 1).bit_length()
    z1 = np.exp(-2j * np.pi * np.fft.rfftfreq(nfft))  # z^-1 em cada bin
    response = np.ones_like(z1)
    for b, a in _K_STAGES:
        response *= (b[0] + b[1] * z1 + b[2] * z1 * z1) / (a[0] + a[1] * z1 + a[2] * z1 * z1)
    return np.fft.irfft(np.fft.rfft(audio, nfft) * response, nfft)[: audio.size]


def peak_momentary_lufs(audio: np.ndarray) -> float:
    """Maior loudness momentânea (janela de 400 ms, BS.1770) de um áudio mono a 48 kHz, em LUFS."""
    if audio.size == 0:
        return float("-inf")
    power = _k_weight(audio.astype(np.float64)) ** 2
    win = int(0.4 * SAMPLE_RATE)
    if power.size < win:
        power = np.pad(power, (0, win - power.size))
    acc = np.concatenate(([0.0], np.cumsum(power)))
    mean_square = float(((acc[win:] - acc[:-win]) / win).max())
    return -0.691 + 10 * np.log10(mean_square) if mean_square > 1e-12 else float("-inf")


def normalize_sfx(audio: np.ndarray, target_lufs: float) -> np.ndarray:
    """Leva o efeito à loudness momentânea `target_lufs`, sem passar de -1 dBFS de pico."""
    loudness = peak_momentary_lufs(audio)
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if not np.isfinite(loudness) or peak <= 0:
        return audio
    gain = min(10 ** ((target_lufs - loudness) / 20), _PEAK_CEILING / peak)
    return (audio * gain).astype(np.float32)


def _sfx_args(kind: str) -> list[str]:
    """Entradas e filtros do FFmpeg de cada efeito (ruídos com semente fixa: o arquivo sai sempre igual)."""
    r = SAMPLE_RATE
    if kind == "impact":
        # Camadas: sub (sentido no fone), corpo em 170–640 Hz, estalo agudo e ruído médio (o que o alto-falante do
        # celular toca). O limitador no fim deixa o golpe mais denso, para ele soar alto sem estourar o pico.
        sub = "0.4*sin(2*PI*(42+70*exp(-9*t))*t)*exp(-7*t)"
        body = "0.45*sin(2*PI*(170+150*exp(-30*t))*t)*exp(-9*t)+0.25*sin(2*PI*(340+300*exp(-30*t))*t)*exp(-14*t)"
        return [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='{sub}+{body}':s={r}:d=1.2",
            "-f", "lavfi", "-i", f"anoisesrc=color=white:amplitude=0.45:d=0.04:r={r}:seed=11",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.5:d=0.25:r={r}:seed=12",
            "-filter_complex",
            "[1:a]bandpass=f=3500:width_type=o:w=1.5,afade=t=out:d=0.035[c];[2:a]lowpass=f=2500,afade=t=out:d=0.25[n];"
            "[0:a][c][n]amix=inputs=3:normalize=0,aecho=0.8:0.6:120:0.25,"
            "alimiter=limit=0.35:attack=1:release=60:level=false:latency=true[out]",
        ]
    if kind == "glitch":
        # Rajadas de ruído e um pulso quadrado grave (sem agudo afinado): a versão com seno de 3,1 kHz e quadrada de
        # 820 Hz dava apito na guarda (padrão do estúdio, regra 13).
        return [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='0.4*(2*random(0)-1)*lt(mod(t,0.07),0.035)':s={r}:d=0.35",
            "-f", "lavfi", "-i", f"aevalsrc=exprs='0.3*sgn(sin(2*PI*150*t))*lt(mod(t,0.05),0.02)':s={r}:d=0.35",
            "-filter_complex",
            "[1:a]lowpass=f=900[q];[0:a][q]amix=inputs=2:normalize=0,acrusher=bits=6:mix=0.7,afade=t=out:st=0.25:d=0.1[out]",
        ]
    if kind == "static":
        return [
            "-f", "lavfi", "-i", f"anoisesrc=color=white:amplitude=0.45:d=0.5:r={r}:seed=13",
            "-filter_complex", "[0:a]highpass=f=1200,afade=t=in:d=0.05,afade=t=out:st=0.2:d=0.3[out]",
        ]
    if kind == "riser":
        # Subida em tom grave (até ~850 Hz) com ruído que cresce: a varredura de seno até 2,3 kHz dava apito na guarda.
        return [
            "-f", "lavfi", "-i", f"aevalsrc=exprs='0.25*sin(2*PI*(60*t+100*t*t)*t)*(t/1.5)':s={r}:d=1.5",
            "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.5:d=1.5:r={r}:seed=15",
            "-filter_complex",
            "[1:a]highpass=f=700,volume='pow(t/1.5,2)':eval=frame[n];[0:a][n]amix=inputs=2:normalize=0,"
            "aecho=0.8:0.6:90:0.3,afade=t=out:st=1.35:d=0.15[out]",
        ]
    # whoosh: ruído em banda larga que sobe até 0,35 s e desce
    return [
        "-f", "lavfi", "-i", f"anoisesrc=color=pink:amplitude=0.7:d=0.7:r={r}:seed=14",
        "-filter_complex", "[0:a]bandpass=f=1400:width_type=o:w=2.5,afade=t=in:d=0.35:curve=exp,afade=t=out:st=0.35:d=0.35[out]",
    ]


# --- efeitos de evento (numpy, semente fixa) ------------------------------------------------------------------------
# Nenhum tem tom afinado acima de 1 kHz (guarda de apito, regra 13) nem nota sustentada: ruído filtrado, varreduras
# graves e estalos. O `sfx_library.tonal_metrics` confere (testes).


def _t(dur: float) -> np.ndarray:
    return np.arange(int(round(dur * SAMPLE_RATE))) / SAMPLE_RATE


def _sweep(f_start: float, f_end: float, dur: float, curve: float = 6.0) -> np.ndarray:
    """Seno que desliza de `f_start` a `f_end` (aproximação exponencial, rápida no começo)."""
    t = _t(dur)
    freq = f_end + (f_start - f_end) * np.exp(-curve * t / dur)
    return np.sin(2 * np.pi * np.cumsum(freq) / SAMPLE_RATE)


def _band_noise(rng: np.random.Generator, dur: float, center: Any, width: float = 0.6, block: int = 1024) -> np.ndarray:
    """Ruído branco filtrado por FFT em blocos (overlap-add com Hann), com o centro da banda (`center(t)`, Hz)
    variando por bloco: a "varredura de filtro" do scratch e do swish, sem nota (a banda tem centenas de Hz)."""
    n = int(round(dur * SAMPLE_RATE))
    hop = block // 2
    x = rng.standard_normal(n + block)
    out = np.zeros(n + block)
    window = np.hanning(block)
    freqs = np.fft.rfftfreq(block, 1 / SAMPLE_RATE)
    for i in range(0, n, hop):
        c = float(center(i / SAMPLE_RATE))
        sigma = max(120.0, c * width)
        mask = np.exp(-0.5 * ((freqs - c) / sigma) ** 2)
        out[i:i + block] += np.fft.irfft(np.fft.rfft(x[i:i + block] * window) * mask, block)
    return out[:n]


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1 / SAMPLE_RATE)
    spec *= 1.0 / np.sqrt(1.0 + (freqs / cutoff) ** 4)
    return np.fft.irfft(spec, x.size)


def _highpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(x.size, 1 / SAMPLE_RATE)
    spec *= 1.0 / np.sqrt(1.0 + (cutoff / np.maximum(freqs, 1.0)) ** 4)
    return np.fft.irfft(spec, x.size)


def _decay(dur: float, rate: float) -> np.ndarray:
    return np.exp(-rate * _t(dur))


def _fade_out(x: np.ndarray, dur: float) -> np.ndarray:
    n = min(x.size, int(dur * SAMPLE_RATE))
    if n > 0:
        x = x.copy()
        x[-n:] *= np.linspace(1.0, 0.0, n)
    return x


def _mix(*parts: np.ndarray) -> np.ndarray:
    n = max(p.size for p in parts)
    out = np.zeros(n)
    for p in parts:
        out[: p.size] += p
    return out


def _sfx_pop(rng: np.random.Generator) -> np.ndarray:
    # "Ploc" de bolha: estalo de ruído médio (sem nota) sobre um corpo grave curto que desce de 420 a 140 Hz.
    body = 0.5 * _sweep(420, 140, 0.09, curve=8) * _decay(0.09, 45)
    snap = 0.6 * _lowpass(rng.standard_normal(int(0.03 * SAMPLE_RATE)), 2200) * _decay(0.03, 160)
    return _fade_out(_mix(body, snap), 0.01)


def _sfx_tick(rng: np.random.Generator) -> np.ndarray:
    # Tique de relógio: ruído agudo de 6 ms, sem corpo.
    burst = _highpass(rng.standard_normal(int(0.05 * SAMPLE_RATE)), 2500) * _decay(0.05, 700)
    return _fade_out(burst, 0.005)


def _sfx_click(rng: np.random.Generator) -> np.ndarray:
    # Clique de mouse: dois estalos (aperta e solta) de ruído filtrado.
    one = _band_noise(rng, 0.03, lambda _t: 3200, width=0.5, block=256) * _decay(0.03, 380)
    two = 0.6 * _band_noise(rng, 0.03, lambda _t: 2600, width=0.5, block=256) * _decay(0.03, 420)
    out = np.zeros(int(0.09 * SAMPLE_RATE))
    out[: one.size] += one
    gap = int(0.05 * SAMPLE_RATE)
    out[gap:gap + two.size] += two[: out.size - gap]
    return _fade_out(out, 0.005)


def _sfx_thump(rng: np.random.Generator) -> np.ndarray:
    # Baque surdo: grave de 95 a 48 Hz e ruído abafado.
    body = 0.8 * _sweep(95, 48, 0.4, curve=5) * _decay(0.4, 11)
    knock = 0.5 * _lowpass(rng.standard_normal(int(0.12 * SAMPLE_RATE)), 600) * _decay(0.12, 40)
    return _fade_out(_mix(body, knock), 0.05)


def _sfx_boom(rng: np.random.Generator) -> np.ndarray:
    # "Vine boom" sem tom: varredura senoidal grave (110 → 32 Hz) com cauda longa de ronco, um golpe de ruído médio
    # no início (o que o celular toca) e saturação leve. Saturar mais cria harmônicos que a trava tonal acusa.
    dur = 1.6
    body = _sweep(110, 32, dur, curve=6) * _decay(dur, 2.6)
    hit = 0.6 * _band_noise(rng, 0.25, lambda _t: 700, width=0.9) * _decay(0.25, 18)
    rumble = 0.35 * _lowpass(rng.standard_normal(int(dur * SAMPLE_RATE)), 140) * _decay(dur, 3.0)
    x = np.tanh(1.2 * _mix(0.8 * body, hit, rumble))
    return _fade_out(x, 0.25)


def _sfx_scratch(rng: np.random.Generator) -> np.ndarray:
    # Arranhão de disco: ruído numa banda que varre para cima e para baixo duas vezes (vai e volta da agulha).
    dur = 0.42
    sweep = lambda t: 350 + 1900 * abs(np.sin(np.pi * t / 0.21))  # noqa: E731 - centro da banda no tempo
    x = _band_noise(rng, dur, sweep, width=0.45, block=512)
    t = _t(dur)
    env = np.clip(np.sin(np.pi * t / 0.21), 0, None) ** 0.5 * (0.6 + 0.4 * np.cos(2 * np.pi * t / dur))
    return _fade_out(x * env, 0.02)


def _sfx_shutter(rng: np.random.Generator) -> np.ndarray:
    # Obturador: dois estalos mecânicos (abre e fecha) e um ruído curto de mola entre eles.
    def snap(amp: float) -> np.ndarray:
        return amp * _highpass(rng.standard_normal(int(0.03 * SAMPLE_RATE)), 1500) * _decay(0.03, 260)

    out = np.zeros(int(0.16 * SAMPLE_RATE))
    for start, amp in ((0.0, 1.0), (0.075, 0.8)):
        s = snap(amp)
        a = int(start * SAMPLE_RATE)
        out[a:a + s.size] += s[: out.size - a]
    spring = 0.15 * _band_noise(rng, 0.07, lambda _t: 900, width=0.6, block=256) * np.hanning(int(0.07 * SAMPLE_RATE))
    a = int(0.01 * SAMPLE_RATE)
    out[a:a + spring.size] += spring
    return _fade_out(out, 0.01)


def _sfx_swish(rng: np.random.Generator) -> np.ndarray:
    # Whoosh curto (passagem rápida): banda que sobe de 600 a 2400 Hz, pico em 0,12 s.
    dur = 0.26
    x = _band_noise(rng, dur, lambda t: 600 + 1800 * t / dur, width=0.7, block=512)
    t = _t(dur)
    env = np.where(t < 0.12, (t / 0.12) ** 2, np.exp(-(t - 0.12) * 22))
    return _fade_out(x * env, 0.02)


def _sfx_bass_drop(rng: np.random.Generator) -> np.ndarray:
    # Queda de grave: varredura de 160 a 30 Hz saturada, com ruído grave no golpe.
    dur = 1.3
    body = np.tanh(2.5 * _sweep(160, 30, dur, curve=3) * _decay(dur, 2.2))
    rumble = 0.3 * _lowpass(rng.standard_normal(int(0.4 * SAMPLE_RATE)), 250) * _decay(0.4, 8)
    return _fade_out(_mix(0.9 * body, rumble), 0.2)


def _sfx_riser_short(rng: np.random.Generator) -> np.ndarray:
    # Subida curta até o corte (0,6 s): ruído com banda que sobe de 400 a 3000 Hz e volume crescente.
    dur = 0.62
    x = _band_noise(rng, dur, lambda t: 400 + 2600 * (t / dur) ** 2, width=0.8, block=512)
    t = _t(dur)
    env = np.minimum(1.0, (t / 0.6) ** 2.2)
    return _fade_out(x * env, 0.02)


_EVENT_SFX: dict[str, tuple[Any, int]] = {
    "pop": (_sfx_pop, 21), "tick": (_sfx_tick, 22), "click": (_sfx_click, 23), "thump": (_sfx_thump, 24),
    "boom": (_sfx_boom, 25), "scratch": (_sfx_scratch, 26), "shutter": (_sfx_shutter, 27), "swish": (_sfx_swish, 28),
    "bass_drop": (_sfx_bass_drop, 29), "riser_short": (_sfx_riser_short, 30),
}
"""Tipo → (gerador, semente): o arquivo sai sempre igual."""


def _event_sfx(kind: str) -> np.ndarray:
    make, seed = _EVENT_SFX[kind]
    return np.asarray(make(np.random.default_rng(seed)), dtype=np.float32)


def generate_sfx(kind: str, out_path: Path) -> Path:
    """Gera o efeito e normaliza a loudness: `audio.sfx.volume_db` vale o mesmo para todos os tipos.

    Com volume 0 dB, o momento mais forte do efeito soa tão alto quanto a narração (`VOICE_LUFS`). Os efeitos de
    transição saem do FFmpeg; os de evento (`EVENT_SFX_KINDS`), do numpy. Tipo desconhecido vira whoosh (como antes;
    quem resolve um nome da biblioteca do usuário é o `sfx_library.resolve`).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if kind in _EVENT_SFX:
        audio = _event_sfx(kind)
    else:
        raw = out_path.with_suffix(".f32")
        run_ffmpeg([*_sfx_args(kind), "-map", "[out]", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", str(raw)])
        try:
            audio = np.fromfile(raw, dtype="<f4")
        finally:
            raw.unlink(missing_ok=True)
    return write_wav(out_path, normalize_sfx(audio, VOICE_LUFS + SFX_TRIM_DB.get(kind, 0.0)))


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


# Trilha sob a voz: passa-altas antes do ganho (subir a trilha sobe a melodia, não o bumbo) e ducking leve, de uns
# 4 dB, para a música continuar audível durante a fala.
MUSIC_HIGHPASS_HZ = 100
# Climas cujo corpo é o grave (drones, batida de coração, pancadas): só tiram o ronco abaixo das notas.
_DEEP_MOODS = frozenset({"drone", "horror", "tension", "mystery", "pulse", "epic"})
_DEEP_HIGHPASS_HZ = 30
_DUCK = "sidechaincompress=threshold=0.1:ratio=3:attack=15:release=250:makeup=1"
MUSIC_FADES = {"short": (0.4, 0.3), "long": (2.0, 3.0)}
"""(entrada, saída) da trilha em s, contra o início e o fim reais do vídeo. No short a trilha já está alta no gancho
e só some no último instante, então a emenda do loop não fica muda."""
END_FADE_S = (0.15, 0.05)
"""(fade, silêncio) no fim do master, depois do limitador: o mix nunca termina num corte seco."""
TRUE_PEAK_DB = -1.0
"""Teto de pico real do mix final (dBTP)."""
_LIMIT = 10 ** (-1.5 / 20)  # teto do limitador sobreamostrado; a folga até -1 dBTP cobre o AAC
_SILENT_LUFS = -70.0
_EBUR128 = "ebur128=framelog=verbose"  # sem o log de cada 100 ms; o resumo sai no fim
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def music_highpass(mood: str | None) -> int:
    """Corte de graves da trilha no mix, conforme o clima."""
    return _DEEP_HIGHPASS_HZ if mood_key(mood) in _DEEP_MOODS else MUSIC_HIGHPASS_HZ


def _ebur128(args: list[str]) -> dict[str, float]:
    """Roda o FFmpeg (com um filtro `ebur128` no fim) e lê o resumo: loudness integrada, LRA e pico real (se pedido)."""
    cmd = [get_settings().ffmpeg, "-hide_banner", "-nostats", "-loglevel", "info", *args, "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
                          creationflags=_NO_WINDOW)
    summary = proc.stderr[proc.stderr.rfind("Summary:"):]
    lufs = re.search(r"I:\s+(-?inf|-?[\d.]+) LUFS", summary)
    if proc.returncode != 0 or not lufs:
        raise FFmpegError(f"ffmpeg não mediu a loudness ({proc.returncode}): {proc.stderr.strip()[-2000:]}")
    lra = re.search(r"LRA:\s+(-?[\d.]+) LU", summary)
    peak = re.search(r"True peak:\s+Peak:\s+(-?inf|-?[\d.]+)", summary)
    return {
        "lufs": float(lufs.group(1)),
        "lra": float(lra.group(1)) if lra else math.nan,
        "true_peak": float(peak.group(1)) if peak else math.nan,
    }


def measure_loudness(path: Path) -> dict[str, float]:
    """Loudness integrada (LUFS), faixa de loudness (LU) e pico real (dBTP) de um arquivo de áudio."""
    return _ebur128(["-i", str(path), "-af", f"{_EBUR128}:peak=true"])


def _gain_to(target: float, measured: float) -> float:
    """Ganho (dB) que leva `measured` a `target`; 0 para silêncio."""
    return target - measured if math.isfinite(measured) and measured > _SILENT_LUFS else 0.0


def _music_chain(highpass: float) -> str:
    """Filtros da trilha antes do ganho, iguais na medida e no mix: mono (o mix é mono até o master; a trilha da
    biblioteca vem em estéreo) e o passa-altas. O downmix é explícito (média dos dois canais): o automático do FFmpeg
    muda de coeficiente conforme os filtros que vêm depois, e a medida e o mix chegavam a diferir 3 dB. Trilha mono
    passa igual (vira estéreo com os dois canais iguais e volta)."""
    return f"aformat=channel_layouts=stereo,pan=mono|c0=0.5*c0+0.5*c1,highpass=f={highpass:g}"


def voice_gain_db(voice: Path) -> float:
    """Ganho (dB) que o mix aplica à voz posicionada para levá-la a `VOICE_LUFS` (com ou sem trilha)."""
    return _gain_to(VOICE_LUFS, _ebur128(["-i", str(voice), "-af", _EBUR128])["lufs"])


def _music_gain(music: Path, music_db: float, highpass: float, total: float | None = None,
                ref_s: float | None = None) -> float:
    """Ganho (dB) que leva a trilha, já em mono e com o passa-altas, a `VOICE_LUFS + music_db + 10` LUFS.

    O arquivo vem em -16 LUFS na banda toda, mas o passa-altas tira o bumbo e o baixo, e com eles uns 5 LU nos climas
    animados. Medida depois do mesmo corte, a trilha fica onde `volume_db` diz em qualquer clima: com -16, 6 LU abaixo
    da voz (antes do ducking). Com `total`, mede só o trecho que toca (o arquivo passa do fim do vídeo e termina em
    fade). Com `ref_s` (várias faixas), mede só os primeiros `ref_s` s, a 1ª cama: ela fica no `volume_db` dela e as
    outras, que já vêm com o ganho relativo, ficam no delas (medir o arquivo todo puxaria a 1ª pelo volume das outras).
    """
    chain = f"{_music_chain(highpass)},{_EBUR128}"
    measured = math.nan
    if ref_s:
        window = min(ref_s, total) if total else ref_s
        measured = _ebur128(["-t", f"{window:.3f}", "-i", str(music), "-af", chain])["lufs"]
    if not (math.isfinite(measured) and measured > _SILENT_LUFS):
        head = ["-t", f"{total:.3f}"] if total else []
        measured = _ebur128([*head, "-i", str(music), "-af", chain])["lufs"]
    return _gain_to(VOICE_LUFS + music_db + 10.0, measured)


MUSIC_DROP_S = (0.3, 0.6)
"""Duração mínima e máxima de uma queda da trilha (`timeline.music_drops`): o silêncio antes do reveal."""
MUSIC_DROP_FADE_S = 0.03
"""Rampa de cada lado da queda (sem estalo)."""


def clamp_drops(drops: list[dict[str, Any]] | None, total: float) -> list[tuple[float, float]]:
    """`[{at, dur}]` da timeline → [(início, fim)] em s, com a duração presa em `MUSIC_DROP_S`, dentro do vídeo e
    sem sobreposição (as que se tocam viram uma)."""
    lo, hi = MUSIC_DROP_S
    spans: list[tuple[float, float]] = []
    for d in sorted(drops or [], key=lambda d: float(d.get("at", 0.0))):
        try:
            at, dur = float(d["at"]), float(d.get("dur") or lo)
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(at) and math.isfinite(dur)) or at >= total:
            continue
        a = max(0.0, at)
        b = min(total, a + min(hi, max(lo, dur)))
        if spans and a <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], b))
        else:
            spans.append((a, b))
    return [(round(a, 3), round(b, 3)) for a, b in spans]


def _drop_filter(spans: list[tuple[float, float]]) -> str:
    """Filtro de volume que zera a trilha em cada trecho, com rampa de `MUSIC_DROP_FADE_S` de cada lado (avaliado
    por quadro de áudio: vale o produto dos portões, 1 fora das quedas e 0 dentro)."""
    f = MUSIC_DROP_FADE_S
    gates = [f"clip(max(({a:.3f}-t)/{f},(t-{b:.3f})/{f}),0,1)" for a, b in spans]
    # quadros de 256 amostras (~5 ms): o volume é avaliado uma vez por quadro, e o quadro do demuxer chega a 85 ms
    return f"asetnsamples=n=256:p=0,volume='{'*'.join(gates)}':eval=frame"


def _premix(
    voice: Path,
    music: Path | None,
    music_gain: float,
    ducking: bool,
    sfx: Path | None,
    total: float,
    short: bool,
    highpass: float = MUSIC_HIGHPASS_HZ,
    voice_gain: float = 0.0,
    drops: list[tuple[float, float]] | None = None,
) -> tuple[list[str], list[str]]:
    """Entradas e grafo do FFmpeg até [mx]: voz (com `voice_gain` dB) + trilha (passa-altas, `music_gain` dB e
    ducking) + SFX, sem o master. `drops` (início, fim em s): a trilha some nesses trechos (−∞, com rampa curta),
    antes do sidechain; sem quedas o grafo é o de antes.

    Toda entrada segue em silêncio depois do fim (apad) e só o atrim depois do amix encerra o grafo: [mx] tem
    exatamente `total` s. Sem isso, o sidechaincompress acabava junto com a voz (o sidechain) e jogava fora a trilha
    que ainda estava no buffer, um pedaço diferente a cada execução (o FFmpeg decodifica as entradas em threads): o
    fim do vídeo saía num corte seco, às vezes logo depois da narração.
    """
    inputs = ["-i", str(voice)]
    graph: list[str] = [f"[0:a]volume={voice_gain:.2f}dB,apad[vn]"]
    mix_inputs = ["[vout]"]
    if music is not None:
        inputs += ["-i", str(music)]
        fade_in, fade_out = (min(d, total / 4) for d in MUSIC_FADES["short" if short else "long"])
        drop = f"{_drop_filter(drops)}," if drops else ""
        graph.append(
            f"[1:a]{_music_chain(highpass)},volume={music_gain:.2f}dB,afade=t=in:d={fade_in:.2f},"
            f"afade=t=out:st={total - fade_out:.3f}:d={fade_out:.2f},{drop}apad[m]"
        )
        if ducking:
            graph.append("[vn]asplit=2[vout][vsc]")
            graph.append(f"[m][vsc]{_DUCK}[md]")
            mix_inputs.append("[md]")
        else:
            graph.append("[vn]anull[vout]")
            mix_inputs.append("[m]")
    else:
        graph.append("[vn]anull[vout]")
    if sfx is not None:
        idx = 1 + (1 if music is not None else 0)
        inputs += ["-i", str(sfx)]
        graph.append(f"[{idx}:a]apad[sx]")
        mix_inputs.append("[sx]")
    graph.append(
        f"{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:normalize=0,atrim=end_sample={round(total * SAMPLE_RATE)}[mx]"
    )
    return inputs, graph


def mix_tracks(
    voice: Path,
    out_path: Path,
    *,
    music: Path | None = None,
    music_db: float = -22.0,
    ducking: bool = True,
    sfx: Path | None = None,
    loudness: float = -14.0,
    total: float | None = None,
    short: bool = False,
    music_highpass: float = MUSIC_HIGHPASS_HZ,
    music_ref_s: float | None = None,
    music_drops: list[dict[str, Any]] | None = None,
) -> Path:
    """Mix final: voz + música (com ducking) + SFX, em `loudness` LUFS e pico real até -1 dBTP. Sai em AAC estéreo (ou WAV, se `out_path` termina em .wav).

    A trilha passa pelo passa-altas `music_highpass` (ver `music_highpass(clima)`) e, medida depois dele, vai a
    `VOICE_LUFS + music_db + 10` LUFS antes do ducking (com -16, 6 LU abaixo da voz). `total` é a duração do vídeo
    (padrão: a da voz posicionada): o arquivo dura exatamente isso, a trilha sai em fade contra ele e o master termina
    com o fade curto de `END_FADE_S`. `short` usa os fades curtos de `MUSIC_FADES`.

    Só ganhos medidos (o loudnorm dinâmico de antes ficava ~1 LU abaixo do alvo): a voz vai a `VOICE_LUFS`, a
    referência da trilha e dos SFX; o mix é medido e o master aplica o ganho exato até `loudness`, com um limitador
    sobreamostrado (pega os picos entre amostras). Se o limitador comer loudness, mede de novo e compensa.
    `music_ref_s` (várias faixas): o ganho da trilha é medido só na 1ª cama (ver `_music_gain`).
    `music_drops` (`timeline.music_drops`, `[{at, dur}]` em s): a trilha some de 0,3 a 0,6 s em cada um (ver
    `clamp_drops`), por cima do ducking. O ganho da trilha é medido sem as quedas.
    """
    total = wav_duration(voice) if total is None else total
    voice_gain = voice_gain_db(voice)
    music_gain = _music_gain(music, music_db, music_highpass, total, music_ref_s) if music is not None else 0.0
    drops = clamp_drops(music_drops, total) if music is not None else []
    inputs, graph = _premix(voice, music, music_gain, ducking, sfx, total, short, music_highpass, voice_gain, drops)
    # Mede e limita já em estéreo (o mono vira dois canais a -3 dB): é o que o arquivo final tem.
    stereo = "[mx]aformat=channel_layouts=stereo"
    # Depois do limitador e da volta a 48 kHz: duração exata de novo e um fade curto que acaba em silêncio antes do fim.
    fade, silence = END_FADE_S
    end = (f"apad,atrim=end_sample={round(total * SAMPLE_RATE)},"
           f"afade=t=out:st={max(0.0, total - fade - silence):.3f}:d={fade:.3f}")

    def master(gain: float, out: str) -> str:
        return ";".join([*graph, (
            f"{stereo},volume={gain:.2f}dB,aresample={SAMPLE_RATE * 4},"
            f"alimiter=limit={_LIMIT:.4f}:attack=5:release=50:level=false:latency=true,aresample={SAMPLE_RATE},"
            f"{end}{out}"
        )])

    measured = _ebur128([*inputs, "-filter_complex", ";".join([*graph, f"{stereo},{_EBUR128}[lu]"]), "-map", "[lu]"])["lufs"]
    gain = _gain_to(loudness, measured)
    if gain:
        for _ in range(2):
            limited = _ebur128([*inputs, "-filter_complex", master(gain, f",{_EBUR128}[lu]"), "-map", "[lu]"])["lufs"]
            if not math.isfinite(limited) or abs(loudness - limited) < 0.1:
                break
            gain += loudness - limited
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # .wav: PCM 24 bits sem perda (faixa de idioma do Modo B para o YouTube Studio); o resto sai em AAC.
    codec = ["-c:a", "pcm_s24le"] if out_path.suffix.lower() == ".wav" else ["-c:a", "aac", "-b:a", "192k"]
    run_ffmpeg([*inputs, "-filter_complex", master(gain, "[out]"), "-map", "[out]", "-ac", "2", "-ar", str(SAMPLE_RATE),
                *codec, str(out_path)])
    return out_path


def write_stereo_wav(path: Path, audio: np.ndarray, rate: int = SAMPLE_RATE) -> Path:
    """Grava float (n, canais) ou (n,) em WAV PCM16."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return pcm16_to_wav(data, rate, path, channels=audio.shape[1] if audio.ndim > 1 else 1)


def music_stem(
    voice: Path,
    music: Path,
    out_path: Path,
    *,
    music_db: float = -22.0,
    ducking: bool = True,
    total: float | None = None,
    short: bool = False,
    music_highpass: float = MUSIC_HIGHPASS_HZ,
    music_ref_s: float | None = None,
    music_drops: list[dict[str, Any]] | None = None,
) -> tuple[Path, float]:
    """Só a trilha como ela entra no mix (passa-altas, ganho e ducking pela voz), com o mesmo grafo do `_premix`, em WAV
    mono de 48 kHz. Devolve (arquivo, ganho da voz em dB): a conferência de áudio põe a voz no mesmo nível do mix.

    O master aplica o mesmo ganho em tudo, então a distância entre voz e trilha medida aqui é a do arquivo final."""
    total = wav_duration(voice) if total is None else total
    voice_gain = voice_gain_db(voice)
    music_gain = _music_gain(music, music_db, music_highpass, total, music_ref_s)
    inputs, graph = _premix(voice, music, music_gain, ducking, None, total, short, music_highpass, voice_gain,
                            clamp_drops(music_drops, total))
    branch = "[md]" if ducking else "[m]"
    end = round(total * SAMPLE_RATE)
    # a voz segue em silêncio (apad) para sempre: sem o atrim, o anullsink nunca deixa o grafo terminar
    stem = [*graph[:-1], f"[vout]atrim=end_sample={end},anullsink", f"{branch}atrim=end_sample={end}[ms]"]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([*inputs, "-filter_complex", ";".join(stem), "-map", "[ms]", "-ac", "1", "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le", str(out_path)])
    return out_path, voice_gain


# --------------------------------------------------------------------------------------------
# Várias faixas por vídeo: uma "cama" por troca de trilha (= music_beds do canais, ADR 0003 bloco G)
# --------------------------------------------------------------------------------------------

BED_SEAM_S = 0.08
"""Crossfade seno/cosseno na emenda do loop (o loop tem um número inteiro de compassos e o fim foi afinado na onda)."""
BED_TAIL_S = 0.6
"""Sobra de cada cama para cruzar com a seguinte."""
BED_FADE_IN_S, BED_FADE_OUT_S = 0.4, 1.4
BED_LUFS = -21.0
"""Loudness de cada loop: todas as camas no mesmo volume (regra 12: "no mesmo volume o vídeo todo")."""
VOICE_BAND_REF_DB = -18.0
"""Banda da voz (1 a 4 kHz) relativa ao total; acima disso, a cama corta metade da diferença (até 3 dB)."""


def bed_trim_db(voz_db: float | None, manual: float | None = None) -> float:
    """Corte da cama pela banda da voz da faixa: `-min(3, max(0, (voz_db + 18) × 0,5))`; o ajuste manual vence."""
    if manual is not None:
        return float(manual)
    if voz_db is None:
        return 0.0
    return -min(3.0, max(0.0, (float(voz_db) - VOICE_BAND_REF_DB) * 0.5))


def bed_durations(starts: list[float], total: float) -> list[float]:
    """Duração de cada cama: até o início da seguinte mais `BED_TAIL_S`; a última vai até o fim do vídeo."""
    out = []
    for i, start in enumerate(starts):
        end = starts[i + 1] + BED_TAIL_S if i + 1 < len(starts) else total
        out.append(max(0.0, min(total, end) - start))
    return out


def _loop_bed(x: np.ndarray, loop: tuple[float, float], need: int, intro: bool) -> np.ndarray:
    """Repete `x[início:início+tamanho]` até `need` amostras; a emenda cruza o que vem depois do fim do loop com o
    começo do loop (seno/cosseno em `BED_SEAM_S`). Com `intro`, toca a faixa desde o começo antes do loop."""
    s = int(round(loop[0] * SAMPLE_RATE))
    e = min(x.shape[0], s + int(round(loop[1] * SAMPLE_RATE)))
    xf = int(BED_SEAM_S * SAMPLE_RATE)
    if e - s <= 2 * xf:
        raise ValueError("loop curto demais para emendar")
    # depois do fim do loop precisa haver `xf` amostras para a emenda; sem isso, o fim do próprio loop cruza
    tail = x[e:e + xf]
    if tail.shape[0] < xf:
        tail = np.concatenate([tail, x[s:s + xf - tail.shape[0]]])
    body = np.concatenate([x[s:e], tail])  # loop + o que vem depois (para cruzar)
    loop_audio = x[s:e]
    up = np.sin(np.linspace(0, np.pi / 2, xf, dtype=np.float32))[:, None]
    down = np.cos(np.linspace(0, np.pi / 2, xf, dtype=np.float32))[:, None]
    head = np.concatenate([x[:s], body]) if intro else body.copy()
    parts = [head[:-xf]]
    last_tail = head[-xf:]
    size = head.shape[0] - xf
    nxt = body[xf:]  # o loop a partir de xf, com a sobra depois do fim
    while size < need:
        parts.append(last_tail * down + loop_audio[:xf] * up)
        parts.append(nxt[:-xf])
        last_tail = nxt[-xf:]
        size += xf + nxt.shape[0] - xf
    parts.append(last_tail)
    return np.concatenate(parts)[:need]


def build_beds(beds: list[dict[str, Any]], total: float, out_path: Path) -> Path:
    """Monta a trilha do vídeo inteiro, uma cama por faixa, em WAV estéreo de 48 kHz com exatamente `total` s.

    Cada cama: `src` (arquivo), `start_s`, `dur_s`, `loop` ((início, tamanho) em s ou None = a faixa desde o começo,
    repetida inteira se faltar), `intro`, `gain_db` (ajuste extra de volume da faixa), `voz_db` e `trim_db` (manual,
    vence o corte automático). Cada cama vai a `BED_LUFS` (medido no loop, ou na própria cama sem loop) com o corte da
    banda da voz; entra em fade de 0,4 s (sem intro) e sai em fade de 1,4 s (até 1/3 da cama)."""
    # import tardio: audio_check importa este módulo
    from studio.media.audio_check import decode, integrated_lufs

    n_total = int(round(total * SAMPLE_RATE))
    mixbuf = np.zeros((n_total, 2), dtype=np.float32)
    cache: dict[str, np.ndarray] = {}
    for bed in beds:
        a = int(round(float(bed["start_s"]) * SAMPLE_RATE))
        if a >= n_total:
            continue
        need = min(n_total - a, int(round(float(bed["dur_s"]) * SAMPLE_RATE)))
        if need <= 0:
            continue
        key = str(bed["src"])
        if key not in cache:
            cache[key] = decode(Path(bed["src"]), SAMPLE_RATE, 2).astype(np.float32)
        x = cache[key]
        if x.shape[0] == 0:
            continue
        loop = bed.get("loop")
        intro = bool(bed.get("intro"))
        if loop:
            y = _loop_bed(x, (float(loop[0]), float(loop[1])), need, intro)
            s = int(round(float(loop[0]) * SAMPLE_RATE))
            ref = integrated_lufs(x[s:s + int(round(float(loop[1]) * SAMPLE_RATE))])
        else:
            y = x[:need] if x.shape[0] >= need else _loop_bed(x, (0.0, x.shape[0] / SAMPLE_RATE), need, True)
            ref = integrated_lufs(y)
        trim = bed_trim_db(bed.get("voz_db"), bed.get("trim_db")) if loop else float(bed.get("trim_db") or 0.0)
        gain = (BED_LUFS - ref if math.isfinite(ref) else 0.0) + trim + float(bed.get("gain_db") or 0.0)
        y = y * np.float32(10 ** (gain / 20))
        if not intro or not loop:
            fi = min(int(BED_FADE_IN_S * SAMPLE_RATE), y.shape[0])
            y[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
        fo = min(int(BED_FADE_OUT_S * SAMPLE_RATE), y.shape[0] // 3)
        if fo > 0:
            y[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None]
        mixbuf[a:a + y.shape[0]] += y
    return write_stereo_wav(out_path, mixbuf)
