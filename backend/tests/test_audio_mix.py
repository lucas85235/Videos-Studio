"""Áudio: SFX normalizados e audíveis no celular, trilha audível sob a voz, fades contra o fim real e master em -14 LUFS."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from studio.config import get_settings
from studio.media import audio
from studio.media.ffmpeg import SAMPLE_RATE, decode_mono, read_wav, run_ffmpeg, write_wav

SR = SAMPLE_RATE


def _ebur128(path: Path, af: str = "") -> dict[str, float]:
    """Loudness integrada e pico real medidos pelo filtro ebur128 do FFmpeg (independente do código testado)."""
    chain = f"{af}," if af else ""
    proc = subprocess.run(
        [get_settings().ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-af", f"{chain}ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    summary = proc.stderr[proc.stderr.rfind("Summary:"):]
    lufs = float(re.search(r"I:\s+(-?[\d.]+)", summary).group(1))
    peak = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf)", summary)
    return {"lufs": lufs, "true_peak": float(peak.group(1)) if peak else float("-inf")}


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(x.astype(np.float64) ** 2)) + 1e-9))


def _speech_like(path: Path, total: float, lead: float = 0.3, tail: float = 0.7, seed: int = 3) -> Path:
    """Narração sintética: sílabas harmônicas (110–170 Hz, até ~3 kHz), palavras coladas e pausas de frase."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(np.ceil(total * SR)) + 1, dtype=np.float32)
    t = lead
    while t < total - tail - 0.3:
        dur = rng.uniform(0.12, 0.25)
        n, a = int(dur * SR), int(t * SR)
        tt = np.arange(n) / SR
        f0 = rng.uniform(110, 170)
        tone = sum(np.sin(2 * np.pi * f0 * k * tt) / k for k in range(1, 20))
        out[a:a + n] += (0.3 * tone * np.sin(np.pi * tt / dur) ** 2).astype(np.float32)
        t += dur + (rng.uniform(0.25, 0.45) if rng.random() < 0.15 else rng.uniform(0.01, 0.04))
    return write_wav(path, out)


def _phone(x: np.ndarray) -> np.ndarray:
    """Alto-falante de celular, grosseiramente: nada abaixo de 200 Hz."""
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(x.size, 1 / SR) < 200] = 0
    return np.fft.irfft(spec, x.size)


# --- medida de loudness ----------------------------------------------------------------------


def test_peak_momentary_lufs_matches_bs1770():
    t = np.arange(SR) / SR
    sine = 0.1 * np.sin(2 * np.pi * 997 * t)  # seno de 997 Hz a 0 dBFS = -3,01 LUFS
    assert audio.peak_momentary_lufs(sine) == pytest.approx(-23.01, abs=0.15)
    assert audio.peak_momentary_lufs(np.zeros(100)) == float("-inf")


# --- SFX -------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", audio.SFX_KINDS)
def test_sfx_are_loudness_normalized(tmp_path, kind):
    samples, rate = read_wav(audio.generate_sfx(kind, tmp_path / f"{kind}.wav"))
    assert rate == SR
    target = audio.VOICE_LUFS + audio.SFX_TRIM_DB[kind]
    assert audio.peak_momentary_lufs(samples) == pytest.approx(target, abs=0.6)
    assert np.abs(samples).max() <= 10 ** (-0.95 / 20)  # pico até -1 dBFS
    assert not (tmp_path / f"{kind}.f32").exists()


def test_impact_reaches_phone_speakers(tmp_path):
    x, _ = read_wav(audio.generate_sfx("impact", tmp_path / "impact.wav"))
    spec = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(x.size, 1 / SR)
    assert 10 * np.log10(spec[freqs >= 300].sum() / spec.sum()) > -12  # o antigo ficava 28 dB abaixo
    assert audio.peak_momentary_lufs(x) - audio.peak_momentary_lufs(_phone(x)) < 9


def test_sfx_are_deterministic_and_keep_their_hit_point(tmp_path):
    a, _ = read_wav(audio.generate_sfx("impact", tmp_path / "a.wav"))
    b, _ = read_wav(audio.generate_sfx("impact", tmp_path / "b.wav"))
    assert np.array_equal(a, b)
    assert _rms_db(a[: int(0.01 * SR)]) > -30  # o golpe começa no início do arquivo
    whoosh, _ = read_wav(audio.generate_sfx("whoosh", tmp_path / "w.wav"))
    win = int(0.05 * SR)
    levels = [_rms_db(whoosh[i:i + win]) for i in range(0, whoosh.size - win, win)]
    assert abs((int(np.argmax(levels)) + 0.5) * 0.05 - audio.SFX_HIT_S["whoosh"]) <= 0.1


# --- trilha ----------------------------------------------------------------------------------


def test_music_highpass_depends_on_mood():
    assert audio.music_highpass("upbeat") == audio.MUSIC_HIGHPASS_HZ == 100
    assert audio.music_highpass(None) == 100
    assert audio.music_highpass("dark ambient drone") == 30
    assert audio.music_highpass("tensão") == 30


def test_generate_music_has_no_long_fade_in(tmp_path):
    x, _ = read_wav(audio.generate_music("upbeat", 12.0, 5, tmp_path / "m.wav"))
    assert abs(x[0]) < 0.01
    assert _rms_db(x[int(0.1 * SR):int(0.6 * SR)]) > _rms_db(x[4 * SR:8 * SR]) - 6


def _music_stem(tmp_path: Path, voice: Path, music: Path, music_db: float, ducking: bool, total: float,
                mood: str = "upbeat") -> Path:
    """A trilha depois do ducking, exatamente como entra no mix (o grafo de `_premix` sem o amix)."""
    voice_gain = audio._gain_to(audio.VOICE_LUFS, audio.measure_loudness(voice)["lufs"])
    highpass = audio.music_highpass(mood)
    inputs, graph = audio._premix(voice, music, audio._music_gain(music, music_db, highpass), ducking, None, total, True,
                                  highpass, voice_gain)
    label = "[md]" if ducking else "[m]"
    # Os ramos seguem em silêncio sem fim (apad): o atrim do amix vai para os dois, senão o FFmpeg não termina.
    trim = f"atrim=end_sample={round(total * SR)}"
    body = ";".join([*graph[:-1], f"[vout]{trim},anullsink", f"[st]{trim}[stem]"])
    out = tmp_path / f"stem_{int(ducking)}.wav"
    run_ffmpeg([*inputs, "-filter_complex", body.replace(label, "[st]"), "-map", "[stem]", "-ar", str(SR),
                "-c:a", "pcm_s16le", str(out)])
    return out


def test_music_bed_stays_audible_under_voice(tmp_path):
    total = 12.0
    voice = _speech_like(tmp_path / "voice.wav", total)
    music = audio.generate_music("upbeat", 15.0, 7, tmp_path / "music.wav")
    ducked = _ebur128(_music_stem(tmp_path, voice, music, -16, True, total))["lufs"]
    plain = _ebur128(_music_stem(tmp_path, voice, music, -16, False, total))["lufs"]
    # Antes: ~25 LU abaixo da voz (ducking de ~17 dB); depois, ~15 LU (o passa-altas comia ~5 LU da trilha). Agora
    # a trilha fica ~6 LU abaixo da voz, como `volume_db` -16 diz, e o ducking é leve.
    assert 5 <= audio.VOICE_LUFS - plain <= 7.5
    assert 8 <= audio.VOICE_LUFS - ducked <= 13
    assert 1.5 <= plain - ducked <= 7


@pytest.mark.parametrize(("mood", "music_db"), [("upbeat", -16.0), ("lofi", -22.0), ("drone", -19.0)])
def test_music_bed_level_counts_the_highpass(tmp_path, mood, music_db):
    """A trilha, já sem os graves, fica em VOICE_LUFS + volume_db + 10 (antes ficava ~5 LU abaixo nos climas animados)."""
    total = 12.0
    voice = _speech_like(tmp_path / "voice.wav", total)
    music = audio.generate_music(mood, 15.0, 7, tmp_path / "music.wav")
    bed = _ebur128(_music_stem(tmp_path, voice, music, music_db, False, total, mood), "atrim=1:11")["lufs"]
    assert bed == pytest.approx(audio.VOICE_LUFS + music_db + 10, abs=0.5)


def test_wide_stereo_bed_lands_on_target(tmp_path):
    """Trilha da biblioteca vem em estéreo e o mix é mono: o downmix de um estéreo largo custa uns 3 LU. Medida em mono,
    como toca, ela fica no alvo."""
    total = 10.0
    voice = _speech_like(tmp_path / "voice.wav", total)
    music = tmp_path / "wide.wav"
    run_ffmpeg(["-f", "lavfi", "-i", "anoisesrc=c=pink:seed=1:d=13:a=0.2", "-f", "lavfi", "-i",
                "anoisesrc=c=pink:seed=2:d=13:a=0.2", "-filter_complex", "[0:a][1:a]amerge=inputs=2[s]", "-map", "[s]",
                "-ar", str(SR), "-c:a", "pcm_s16le", str(music)])
    bed = _ebur128(_music_stem(tmp_path, voice, music, -16, False, total), "atrim=1:9")["lufs"]
    assert bed == pytest.approx(audio.VOICE_LUFS - 16 + 10, abs=0.5)


def test_mix_tracks_applies_the_measured_music_gain(tmp_path, monkeypatch):
    """O `mix_tracks` usa o ganho medido da trilha (mono, passa-altas do clima, só o trecho que toca), não o fixo de
    antes (`music_db + 10`)."""
    total = 6.0
    voice = _speech_like(tmp_path / "voice.wav", total)
    music = audio.generate_music("upbeat", 9.0, 7, tmp_path / "music.wav")
    seen: dict[str, float] = {}
    original = audio._premix

    def spy(voice_, music_, music_gain, *args, **kwargs):
        seen["gain"] = music_gain
        return original(voice_, music_, music_gain, *args, **kwargs)

    monkeypatch.setattr(audio, "_premix", spy)
    audio.mix_tracks(voice, tmp_path / "mix.m4a", music=music, music_db=-16, total=total, short=True,
                     music_highpass=100)
    assert seen["gain"] == pytest.approx(audio._music_gain(music, -16, 100, total), abs=0.01)
    assert seen["gain"] > -16 + 10 + 2  # o passa-altas de 100 Hz tira uns 5 LU da trilha animada, e o ganho devolve


@pytest.mark.parametrize("short", [True, False])
def test_music_fades_follow_the_real_end(tmp_path, short):
    total = 10.0
    voice = write_wav(tmp_path / "silence.wav", np.zeros(int(total * SR) + 1, dtype=np.float32))
    music = audio.generate_music("upbeat", 15.0, 7, tmp_path / "music.wav")  # maior que o vídeo, como o balde de 5 s
    out = audio.mix_tracks(voice, tmp_path / "mix.m4a", music=music, music_db=-16, total=total, short=short)
    x = decode_mono(out, SR)
    assert x.size / SR == pytest.approx(total, abs=0.1)
    mid = _rms_db(x[4 * SR:6 * SR])
    start = _rms_db(x[int(0.3 * SR):int(0.5 * SR)])
    end = _rms_db(x[-int(0.05 * SR):])
    assert end < mid - 12  # some de verdade no fim do vídeo, não no fim do arquivo da trilha
    if short:
        assert start > mid - 5  # já está alta no gancho
        assert _rms_db(x[-int(0.6 * SR):-int(0.35 * SR)]) > mid - 4  # e a emenda do loop não fica muda
    else:
        assert start < mid - 8  # entrada lenta no vídeo longo


def _tone_bed(path: Path, duration: float) -> Path:
    """Trilha sintética do FFmpeg: acorde contínuo com grave, sem pausas (qualquer corte aparece no nível)."""
    expr = "0.3*sin(2*PI*55*t)+0.12*sin(2*PI*220*t)+0.1*sin(2*PI*330*t)+0.08*sin(2*PI*440*t)"
    run_ffmpeg(["-f", "lavfi", "-i", f"aevalsrc=exprs='{expr}':s={SR}:d={duration:.2f}", "-ac", "1", "-c:a", "pcm_s16le",
                str(path)])
    return path


@pytest.mark.parametrize("voice_s", [None, 5.0])
def test_mix_lasts_the_video_and_fades_out_the_same_way_every_time(tmp_path, voice_s):
    """O fim do mix não depende de qual entrada o FFmpeg leu primeiro: dura `total`, com um fade curto e não um corte.

    Antes, com ducking, o sidechaincompress acabava junto com a voz e descartava a trilha que estava no buffer: o mix
    caía a seco em silêncio um pedaço antes do fim, diferente a cada execução. Com a voz mais curta que o vídeo, o
    amix (duration=first) terminava o arquivo antes.
    """
    total = 7.37  # não fecha em quadros de AAC (1024 amostras)
    voice = _speech_like(tmp_path / "voice.wav", voice_s or total)  # a voz posicionada tem o tamanho do vídeo
    music = _tone_bed(tmp_path / "music.wav", total + 3)  # o balde da trilha passa do fim do vídeo
    runs = [
        decode_mono(audio.mix_tracks(voice, tmp_path / f"mix{i}.m4a", music=music, music_db=-16, total=total, short=True),
                    SR)
        for i in range(3)
    ]
    for x in runs:
        assert abs(x.size - round(total * SR)) <= 1024  # um quadro de AAC
        assert np.array_equal(x, runs[0])
    x = runs[0]
    win = int(0.025 * SR)
    end = x.size - int(audio.END_FADE_S[1] * SR)
    fade = [_rms_db(x[a:a + win]) for a in range(end - 6 * win, end, win)]  # 25 ms a 25 ms nos 150 ms do fade
    assert fade[0] > -35  # a trilha ainda toca quando o fade começa
    assert np.all(np.diff(fade) < 0)  # desce aos poucos...
    assert fade[-1] > -70  # ...até o fim do fade, sem cair em silêncio antes
    assert 20 * np.log10(np.abs(x[end:]).max() + 1e-9) < -50  # os últimos 50 ms ficam quietos


# --- master ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("loudness", "short"), [(-14.0, True), (-16.0, False)])
def test_mix_reaches_target_loudness_and_true_peak(tmp_path, loudness, short):
    total = 12.0
    voice = _speech_like(tmp_path / "voice.wav", total)
    music = audio.generate_music("upbeat", 15.0, 7, tmp_path / "music.wav")
    impact = audio.generate_sfx("impact", tmp_path / "impact.wav")
    whoosh = audio.generate_sfx("whoosh", tmp_path / "whoosh.wav")
    gain = 10 ** (-8 / 20)
    sfx = audio.place_clips([(impact, 2.0, gain), (whoosh, 5.5, gain), (impact, 8.0, gain)], total, tmp_path / "sfx.wav")
    out = audio.mix_tracks(voice, tmp_path / "mix.m4a", music=music, music_db=-16, sfx=sfx, loudness=loudness,
                           total=total, short=short)
    measured = _ebur128(out)
    assert measured["lufs"] == pytest.approx(loudness, abs=0.5)  # o loudnorm de uma passagem ficava ~1 LU abaixo
    assert measured["true_peak"] <= audio.TRUE_PEAK_DB
    assert audio.measure_loudness(out)["lufs"] == pytest.approx(measured["lufs"], abs=0.2)


def test_mix_without_music_or_sfx(tmp_path):
    voice = _speech_like(tmp_path / "voice.wav", 6.0)
    out = audio.mix_tracks(voice, tmp_path / "mix.m4a")
    measured = _ebur128(out)
    assert measured["lufs"] == pytest.approx(-14.0, abs=0.5)
    assert measured["true_peak"] <= audio.TRUE_PEAK_DB


def test_preview_mix_follows_the_style(isolated_settings):
    """Pipeline offline inteiro: o mix da prévia sai na loudness do estilo, com pico real até -1 dBTP."""
    from studio.core.project import service
    from studio.core.styles.library import get_style
    from studio.workers.jobs import get_job, get_runner

    style_id = "games-cultura-pop-short"
    doc = service.create_project(title="Teste", style_id=style_id, topic="o jogo mais esperado da década")
    for kind in ("script", "storyboard", "preview"):
        job = get_runner().submit(kind, {}, doc.id, run_inline=True)
        final = get_job(job["id"])
        assert final is not None and final["status"] == "done", final and final["error"]
    mix = isolated_settings.data_dir / final["result"]["langs"]["pt-BR"]["mix"]
    measured = _ebur128(mix)
    assert measured["lufs"] == pytest.approx(get_style(style_id).audio.loudness_lufs, abs=0.6)
    assert measured["true_peak"] <= audio.TRUE_PEAK_DB
