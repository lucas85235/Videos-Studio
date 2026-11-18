"""Várias faixas por vídeo e curadoria das trilhas (ADR 0003, bloco G)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from studio.core.hashing import hash_obj, seed_from
from studio.core.project import service
from studio.core.project.ops import apply_ops, parse_ops
from studio.core.styles.library import get_style
from studio.media import audio, music_curate, music_library, sfx_library
from studio.media.audio_check import decode
from studio.media.ffmpeg import read_wav
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline.build import Builder

SR = audio.SAMPLE_RATE


def _beat_track(seconds: float = 90.0, voice: tuple[float, float] | None = (30.0, 50.0), bpm: float = 120.0,
                seed: int = 1) -> np.ndarray:
    """Faixa sintética: bumbo em cada batida, chimbal no contratempo, baixo contínuo e, em `voice`, uma "voz" (tons
    modulados de 1 a 4 kHz)."""
    n = int(seconds * SR)
    t = np.arange(n) / SR
    rng = np.random.default_rng(seed)
    x = 0.05 * np.sin(2 * np.pi * 110 * t)
    beat = 60.0 / bpm
    kick_len = int(0.3 * SR)
    tk = np.arange(kick_len) / SR
    kick = 0.6 * np.sin(2 * np.pi * (55 + 60 * np.exp(-30 * tk)) * tk) * np.exp(-12 * tk)
    hat_len = int(0.05 * SR)
    for b in np.arange(0, seconds, beat):
        i = int(round(b * SR))
        x[i:i + kick_len] += kick[: n - i]
        j = int(round((b + beat / 2) * SR))
        if j < n:
            x[j:j + hat_len] += (0.08 * rng.standard_normal(hat_len) * np.exp(-60 * np.arange(hat_len) / SR))[: n - j]
    if voice:
        on = (t >= voice[0]) & (t < voice[1])
        tones = sum(np.sin(2 * np.pi * f * t + rng.uniform(0, 6)) for f in (1200, 1700, 2300, 3100))
        x += on * 0.15 * tones * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))
    x = 0.5 * x
    return np.stack([x, x], axis=1).astype(np.float32)


def test_curate_avoids_voice_and_loops_on_the_bar(isolated_settings, tmp_path):
    path = audio.write_stereo_wav(tmp_path / "batida.wav", _beat_track())
    cur = music_curate.curate(path, loop_out=tmp_path / "loop.wav")
    assert cur["ok"] and abs(cur["bpm"] - 120) < 2
    a, b = cur["loop_start_s"], cur["loop_start_s"] + cur["loop_s"]
    assert b <= 30.0 or a >= 50.0, cur  # o loop fica fora do trecho com "voz"
    bar = 4 * 60 / 120
    bars = cur["loop_s"] / bar
    assert cur["loop_bars"] in (4, 8)
    assert abs(bars - round(bars)) / round(bars) <= 0.02, cur  # múltiplo do compasso em até 2%
    assert cur["emenda_batida"] > 0.5
    for key in ("bpm", "loop_start_s", "loop_s", "loop_bars", "emenda_batida", "emenda_onda", "voz_db",
                "voz_vs_pico_db", "variacao_db", "comeco_ok_s", "textura", "intro"):
        assert key in cur
    loop, rate = read_wav(Path(cur["loop"]))
    assert rate == SR and abs(loop.size / SR - cur["loop_s"]) < 0.01
    # faixa curta demais: sem trecho, o build volta ao modo antigo
    short = audio.write_stereo_wav(tmp_path / "curta.wav", _beat_track(8.0, None))
    assert music_curate.curate(short) == {"ok": False, "reason": music_curate.SHORT_REASON, "dur_s": 8.0}
    # o desenho da faixa (opcional)
    png = music_curate.spectro_png(path, tmp_path / "s.png", dur=30, loop=(a, cur["loop_s"]))
    assert png.exists() and png.stat().st_size > 1000


def test_curation_cache_and_job(isolated_settings):
    root = music_library.music_root()
    audio.write_stereo_wav(root / "tensao" / "batida.wav", _beat_track(60.0, None))
    job = get_runner().submit("music_curate", {}, None, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    assert final["result"]["ok"] == 1
    cmap = music_library.curation_map()
    assert set(cmap) == {"tensao/batida.wav"} and cmap["tensao/batida.wav"]["ok"]
    key = music_library.curation_key("tensao/batida.wav")
    assert key and key.startswith("tensao/batida.wav|")
    # segunda vez: vem do cache (mesma chave caminho|tamanho|mtime)
    again = music_library.curate_track("tensao/batida.wav")
    assert again == cmap["tensao/batida.wav"]


def test_build_beds_exact_length_and_smooth_seam(isolated_settings, tmp_path):
    t = np.arange(10 * SR) / SR
    x = (0.3 * np.sin(2 * np.pi * 100 * t)).astype(np.float32)
    src = audio.write_stereo_wav(tmp_path / "seno.wav", np.stack([x, x], axis=1))
    other = audio.write_stereo_wav(tmp_path / "outro.wav", _beat_track(20.0, None))
    total = 23.456
    # loop de 2,013 s: fora de fase de propósito (a emenda tem de cruzar sem estalo)
    loop = (1.0, 2.013)
    starts = [0.0, 12.0]
    durs = audio.bed_durations(starts, total)
    assert abs(durs[0] - 12.6) < 1e-9 and abs(durs[1] - (total - 12.0)) < 1e-9
    out = audio.build_beds([
        {"src": src, "start_s": 0.0, "dur_s": durs[0], "loop": loop, "intro": False, "gain_db": 0.0, "voz_db": -30.0},
        {"src": other, "start_s": 12.0, "dur_s": durs[1], "loop": None, "intro": False, "gain_db": 0.0},
    ], total, tmp_path / "camas.wav")
    y = decode(out, SR, 2)
    assert y.shape[0] == round(total * SR)
    mono = y[: 11 * SR].mean(axis=1)
    # sem estalo: a maior variação entre amostras não passa de 1,5x a de um seno de 100 Hz no mesmo nível
    level = np.abs(mono[2 * SR: 10 * SR]).max()
    smooth_step = level * 2 * np.pi * 100 / SR
    assert np.abs(np.diff(mono[SR: 10 * SR])).max() <= 1.5 * smooth_step
    # cada loop a -21 LUFS (estéreo)
    from studio.media.audio_check import integrated_lufs

    assert abs(integrated_lufs(y[2 * SR: 10 * SR]) - audio.BED_LUFS) < 0.5
    # o corte pela banda da voz e o ajuste manual
    assert audio.bed_trim_db(-10.0) == -3.0 and audio.bed_trim_db(-16.0) == -1.0 and audio.bed_trim_db(-25.0) == 0.0
    assert audio.bed_trim_db(-10.0, manual=1.5) == 1.5


def _run(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _commit(project_id: str, ops: list[dict]) -> None:
    doc = service.get_doc(project_id)
    service.commit(project_id, apply_ops(doc, parse_ops(ops), get_style(doc.style_id)), "teste")


def _project() -> str:
    project_id = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado").id
    _run("script", project_id)
    _run("storyboard", project_id)
    return project_id


def test_one_cue_keeps_v3_mix_and_two_cues_switch_beds(isolated_settings):
    project_id = _project()
    preview = _run("preview", project_id)
    entry = preview["langs"]["pt-BR"]
    timeline = json.loads((isolated_settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    cue = next(c for c in doc.music if c.enabled)
    mood = cue.mood or style.audio.music.mood
    total = timeline["duration"]
    music_hash = hash_obj({"mood": audio.mood_key(mood), "duration": float(int(total // 5 + 1) * 5),
                           "seed": seed_from(doc.seed, "music", mood), "v": 2})
    volume = cue.volume_db if cue.volume_db is not None else style.audio.music.volume_db
    builder = Builder(project_id, mode="preview")
    sfx_events = builder.sfx_plan(timeline)
    # a mesma chave do mix de antes (v3): projeto antigo com uma faixa não refaz o áudio (glitch/riser levam a versão
    # dos efeitos: o som deles mudou e o mix antigo, com apito, é refeito)
    key = {
        "voice": [(Path(n["path"]).parent.name, n["start"]) for n in timeline["narration"]],
        "music": (music_hash, volume, style.audio.music.ducking, audio.music_highpass(mood)),
        # os efeitos como o mix os lê (`Builder.sfx_plan`: o do ADR 0004 cai na palavra e leva lib_id e ganho)
        "sfx": [(e["type"], e["start"], e["lib_id"], e["gain_db"]) if e["extra"] else (e["type"], e["start"])
                for e in sfx_events],
        "sfx_db": style.audio.sfx.volume_db,
        "loudness": style.audio.loudness_lufs,
        "total": total,
        "short": style.format.type == "short",
        "v": 3,
    }
    # o storyboard novo passa pelo planejador (ADR 0004): efeito de evento (thump, swish...) leva a versão atual dos
    # efeitos; sem eles, a de glitch/riser; sem nenhum dos dois, a chave da v3 fica como era
    if {e["played"] for e in sfx_events} & set(audio.EVENT_SFX_KINDS):
        key["sfx_v"] = audio.SFX_VERSION
    elif any(e["type"] in audio.SFX_RETUNED for e in sfx_events):
        key["sfx_v"] = audio.SFX_RETUNED_VERSION
    lib_keys = [(ref, explicit, k) for ref, explicit in sorted({(e["ref"], e["explicit"]) for e in sfx_events})
                if (k := sfx_library.mix_key(ref, tonal_ok=builder.sfx_tonal_ok, explicit=explicit)) is not None]
    if lib_keys:
        key["sfx_lib"] = lib_keys
    drops = audio.clamp_drops(timeline.get("music_drops"), total)
    if drops:
        key["music_drops"] = drops
    expected = hash_obj(key)
    assert expected in entry["mix"]
    assert preview["music"]["beds"][0]["start_s"] == 0.0 and len(preview["music"]["beds"]) == 1
    assert not any("apito" in w for w in preview["warnings"])

    # 2ª faixa a partir do 3º segmento: duas camas, a 2ª no início da narração desse segmento
    seg = doc.segments[2].id
    _commit(project_id, [{"op": "set_music", "start_segment": seg, "mood": "calma", "volume_db": -24}])
    doc = service.get_doc(project_id)
    assert len(doc.music) == 2 and doc.music[1].start_segment == seg and doc.music[1].loop is False
    two = _run("preview", project_id)
    beds = two["music"]["beds"]
    seg_start = next(n["start"] for n in timeline["narration"] if n["segment_id"] == seg)
    assert [b["cue_id"] for b in beds] == [doc.music[0].id, doc.music[1].id]
    assert beds[0]["start_s"] == 0.0 and abs(beds[1]["start_s"] - seg_start) < 1e-6
    assert abs(beds[0]["dur_s"] - (seg_start + audio.BED_TAIL_S)) < 1e-3
    new_entry = two["langs"]["pt-BR"]
    assert new_entry["mix"] != entry["mix"] and two["generated"].get("mix") == 1
    assert two["generated"].get("tts", 0) == 0
    # a trilha montada (para a conferência) troca de clima no segmento marcado
    track, info = Builder(project_id).music_track(timeline)
    music = decode(track, SR, 1)
    assert abs(music.size / SR - total) < 0.01 and info["beds"] == beds
    # a 2ª faixa é editável: muda de segmento e liga o loop
    _commit(project_id, [{"op": "set_music", "cue_id": doc.music[1].id, "start_segment": doc.segments[1].id,
                          "loop": True}])
    doc = service.get_doc(project_id)
    assert doc.music[1].start_segment == doc.segments[1].id and doc.music[1].loop is True


def test_whistling_sfx_leaves_the_mix(isolated_settings, monkeypatch):
    project_id = _project()

    def whistle(kind: str, out_path: Path) -> Path:
        from studio.media.ffmpeg import write_wav

        t = np.arange(int(0.4 * SR)) / SR
        return write_wav(out_path, (0.3 * np.sin(2 * np.pi * 3000 * t)).astype(np.float32))

    monkeypatch.setattr(audio, "generate_sfx", whistle)
    preview = _run("preview", project_id)
    timeline = json.loads((isolated_settings.data_dir / preview["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    kinds = sorted({e["type"] for e in timeline["sfx"]})
    assert kinds, "o estilo de teste precisa ter efeitos"
    for kind in kinds:
        assert (f"efeito sonoro '{kind}' tem apito: ficou fora do mix (padrão do estúdio: sem apito)"
                in preview["warnings"])


def test_lost_start_segment_does_not_replace_the_opening(isolated_settings):
    project_id = _project()
    doc = service.get_doc(project_id)
    seg = doc.segments[2].id
    _commit(project_id, [{"op": "set_music", "start_segment": seg, "mood": "calma"}])
    doc = service.get_doc(project_id)
    opening, other = doc.music[0].id, doc.music[1].id
    # trecho sumido (roteiro refeito com ids novos): a faixa fica fora, a de abertura continua no começo
    doc.music[1].start_segment = "seg_que_nao_existe"
    service.commit(project_id, doc, "teste")
    preview = _run("preview", project_id)
    beds = preview["music"]["beds"]
    assert [b["cue_id"] for b in beds] == [opening] and beds[0]["start_s"] == 0.0
    assert any("não está mais no vídeo" in w for w in preview["warnings"])
    assert other not in {b["cue_id"] for b in beds}


def test_delete_segment_moves_or_disables_the_cue(isolated_settings):
    project_id = _project()
    doc = service.get_doc(project_id)
    seg, after, last = doc.segments[1].id, doc.segments[2].id, doc.segments[-1].id
    _commit(project_id, [{"op": "set_music", "start_segment": seg},
                         {"op": "set_music", "start_segment": last}])
    _commit(project_id, [{"op": "delete_segment", "segment_id": seg}])
    doc = service.get_doc(project_id)
    moved = doc.music[1]
    assert moved.start_segment == after and moved.enabled
    # apagar o último trecho: não há seguinte, a faixa é desligada
    _commit(project_id, [{"op": "delete_segment", "segment_id": last}])
    doc = service.get_doc(project_id)
    assert doc.music[2].start_segment is None and not doc.music[2].enabled


def test_music_gain_measures_only_the_first_bed(isolated_settings, tmp_path):
    t = np.arange(6 * SR) / SR
    quiet = 0.05 * np.sin(2 * np.pi * 440 * t)
    loud = 0.4 * np.sin(2 * np.pi * 440 * t)
    x = np.concatenate([quiet, loud]).astype(np.float32)
    both = audio.write_stereo_wav(tmp_path / "camas.wav", np.stack([x, x], axis=1))
    first = audio.write_stereo_wav(tmp_path / "primeira.wav", np.stack([quiet, quiet], axis=1).astype(np.float32))
    only_first = audio._music_gain(first, -16, 100, 6.0)
    assert abs(audio._music_gain(both, -16, 100, 12.0, 6.0) - only_first) < 0.3
    assert audio._music_gain(both, -16, 100, 12.0) < only_first - 6  # o arquivo todo puxaria a 1ª para baixo


def test_mix_key_carries_sfx_version_and_stale_mix_is_flagged(isolated_settings):
    from studio.core.graph.store import ArtifactStore
    from studio.workers.audiocheck.report import stale_sfx_item

    store = ArtifactStore()
    old = "a" * 64
    rel = store.rel(store.dir_for("mix", old) / "mix.m4a")
    (store.dir_for("mix", old) / "mix.m4a").write_bytes(b"x")
    store.save("mix", old, {"path": rel, "files": [rel]}, "ffmpeg")  # mix de antes, sem `sfx_v`
    item = stale_sfx_item(store, rel, {"glitch", "whoosh"}, "preview")
    assert item and item["level"] == "erro" and "'glitch'" in item["detail"]
    assert stale_sfx_item(store, rel, {"whoosh", "impact"}, "preview") is None  # efeitos que não mudaram
    store.save("mix", old, {"path": rel, "files": [rel], "sfx_v": audio.SFX_VERSION}, "ffmpeg")
    assert stale_sfx_item(store, rel, {"glitch"}, "preview") is None
