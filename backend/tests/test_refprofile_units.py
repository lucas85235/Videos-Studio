"""Peças da análise da referência (ADR 0004 §3.2) sem vídeo: cortes, punch, sobreposição, fala, visão, agregação."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from studio.core.editing.profile import EditProfile
from studio.media.refprofile import aggregate, cuts, motion, recurrence, speech
from studio.media.refprofile import vision as rvision
from studio.providers import vision as pvision
from studio.providers.llm import VISION_TASK, ClaudeCodeLLM

# --- cortes -------------------------------------------------------------------------------------------------------


def test_scan_parse_reads_mafd_and_freezes():
    text = "\n".join([
        "[Parsed_metadata_3 @ 0x1] frame:0 pts:0 pts_time:0",
        "[Parsed_metadata_3 @ 0x1] lavfi.scd.mafd=0.000",
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 0.5",
        "[Parsed_metadata_3 @ 0x1] frame:1 pts:512 pts_time:1.0",
        "[Parsed_metadata_3 @ 0x1] lavfi.scd.mafd=25.5",
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_duration: 0.9",
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_end: 1.4",
        "[freezedetect @ 0x2] lavfi.freezedetect.freeze_start: 1.6",
    ])
    scan = cuts.parse_scan(text)
    assert scan.times == [0.0, 1.0] and scan.mafd == [0.0, 25.5]
    assert scan.freezes == [(0.5, 1.4)]  # o congelado sem fim passa do último quadro: fica de fora


def test_candidates_hard_after_static_gap_and_soft_over_motion():
    # 30 fps contínuo com movimento de fundo ~6 níveis, um corte duro em 1,0 s, um trecho parado (sem quadros, como
    # sai do mpdecimate) de 1,0 a 2,0 s, outro corte logo depois (2,0) e um corte suave em 3,0 s sobre fundo alto
    times, mafd = [], []
    t = 0.0
    while t < 4.0 - 1e-9:
        if 1.0 < t < 2.0:
            t += 1 / 30
            continue
        times.append(round(t, 4))
        if abs(t - 1.0) < 1e-6 or abs(t - 2.0) < 1e-6:
            mafd.append(28.0)  # ~0,72 na escala do select
        elif abs(t - 3.0) < 1e-6:
            mafd.append(16.0)  # ~0,41: suave sobre fundo de ~0,2
        elif t > 2.5:
            mafd.append(8.0)
        else:
            mafd.append(2.0)
        t += 1 / 30
    found = cuts.candidates(times, mafd)
    kinds = {round(c.t, 2): c.kind for c in found}
    assert kinds.get(1.0) == "hard" and kinds.get(2.0) == "hard"
    assert kinds.get(3.0) == "soft"
    assert len(found) == 3


def test_repeated_frames_do_not_look_like_cuts():
    # 60 fps com quadro repetido: sem o mpdecimate a diferença alterna 0 e alto. Os quadros que restam (30 fps) dão
    # movimento contínuo, e movimento contínuo não é corte.
    times = [i / 30 for i in range(90)]
    mafd = [12.0 + (i % 3) for i in range(90)]
    assert cuts.candidates(times, mafd) == []


def test_bursts_become_one_micro_shot():
    cands = [cuts.Candidate(t, 0.5, "hard") for t in (1.0, 1.033, 1.1, 1.2, 1.25, 3.0)]
    merged = cuts.merge_bursts(cands)
    assert [c.t for c in merged] == [1.0, 1.25, 3.0]
    shots = cuts.shots_from_cuts([c.t for c in merged], 5.0)
    assert [s["micro"] for s in shots] == [False, True, False, False]


def test_pace_regular_shots_micro_and_windows():
    shots = cuts.shots_from_cuts([2.0, 2.2, 4.0, 10.0, 31.0, 40.0], 60.0)
    pace = cuts.pace(shots, 60.0)
    assert pace["micro_count"] == 1 and pace["shots"] == 6
    assert pace["median_s"] == pytest.approx(np.median([2.0, 1.8, 6.0, 21.0, 9.0, 20.0]))
    # cortes regulares: 2,2 4 10 31 40 (o corte em 2,0 abre o micro-plano)
    assert pace["cuts_per_min"] == 5.0
    assert pace["cuts_per_min_windows"] == [6.0, 4.0]
    assert pace["hook_cuts_per_min"] == 6.0


def test_old_shot_list_keeps_the_micro_shots_aside():
    from studio.media import reference_video

    micro: list[dict] = []
    shots = reference_video.shots_from([0.1, 1.0, 1.2, 2.0], 3.0, micro)
    assert [s["k"] for s in shots] == [1, 2, 3]  # a folha e o ritmo antigo continuam iguais
    assert micro == [{"a": 1.0, "dur": 0.2}]


def test_freeze_effect_is_inside_a_running_shot():
    shots = cuts.shots_from_cuts([5.0], 10.0)
    freezes = [(0.0, 3.0), (2.0, 3.5), (5.05, 9.0)]
    assert cuts.freezes_inside_shots(freezes, shots) == [(2.0, 3.5)]


# --- movimento ----------------------------------------------------------------------------------------------------


def _texture(seed: int = 3, size: tuple[int, int] = (90, 160)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 255, (size[0] // 6, size[1] // 6)).astype(np.uint8)
    return np.asarray(Image.fromarray(small).resize((size[1], size[0]), Image.Resampling.BICUBIC))


def test_relate_measures_scale_and_shift():
    a = _texture()
    zoomed = motion._warp(a, 1.2, 0, 0).astype(np.uint8)
    rel = motion.relate(a, zoomed)
    assert rel.ok and rel.scale == pytest.approx(1.2, abs=0.02)
    moved = motion._warp(a, 1.0, 4, -6).astype(np.uint8)
    rel = motion.relate(a, moved)
    assert rel.ok and abs(rel.scale - 1) < 0.01
    assert rel.dx == pytest.approx(-6 / 160, abs=0.01) and rel.dy == pytest.approx(4 / 90, abs=0.01)
    other = _texture(seed=11)
    assert not motion.relate(a, other).ok


def test_verify_cuts_turns_a_zoom_cut_into_a_punch():
    a = _texture()
    frames = np.stack([a, a, motion._warp(a, 1.2, 0, 0).astype(np.uint8), _texture(seed=9), _texture(seed=9)])
    times = np.arange(len(frames)) / motion.FPS
    cands = [cuts.Candidate(0.35, 0.4, "hard"), cuts.Candidate(0.55, 0.6, "hard")]
    kept, punches = motion.verify_cuts(cands, frames, times)
    assert kept == [0.55]
    assert len(punches) == 1 and punches[0]["scale"] == pytest.approx(0.2, abs=0.03)


def test_find_punches_separates_jump_from_ramp():
    step = float(np.log(1.12))
    jump = [0.0, 0.0, step, 0.0, 0.0, 0.0]
    assert motion.find_punches(jump) == [(2, 1, pytest.approx(step))]
    ramp = [float(np.log(1.03))] * 10  # 15%/s: zoom lento, não punch
    assert motion.find_punches(ramp) == []


def test_overlay_box_found_over_static_background():
    bg = _texture()
    a = bg.copy()
    b = bg.copy()
    b[20:60, 100:150] = 255 - b[20:60, 100:150]  # algo aparece num canto
    box = motion.overlay_box(a, b)
    assert box is not None
    x, y, w, h = box
    assert 0.55 <= x <= 0.7 and 0.15 <= y <= 0.3 and 0.25 <= w <= 0.4 and 0.35 <= h <= 0.55
    assert motion.overlay_box(a, a) is None
    assert motion.overlay_box(a, _texture(seed=5)) is None  # mudou tudo: é corte, não sobreposição


def test_analyze_finds_slow_zoom_and_overlay():
    a = _texture()
    zoom = [motion._warp(a, 1.0 + 0.01 * i, 0, 0).astype(np.uint8) for i in range(10)]  # 5%/s
    overlay = []
    for i in range(6):
        f = a.copy()
        f[20:60, 100:150] = (np.roll(a, 7 * (i + 1), axis=1))[20:60, 100:150]
        overlay.append(f)
    frames = np.stack([*zoom, *overlay])
    times = np.arange(len(frames)) / motion.FPS
    shots = [{"k": 1, "a": 0.0, "b": 2.0, "dur": 2.0, "micro": False},
             {"k": 2, "a": 2.0, "b": 3.2, "dur": 1.2, "micro": False}]
    res = motion.analyze(frames, times, shots)
    assert len(res.ramps) == 1 and res.ramps[0]["rate_pct_s"] == pytest.approx(5.0, abs=1.0)
    assert not res.punches
    assert len(res.overlays) == 1


# --- fala ---------------------------------------------------------------------------------------------------------


WORDS = [("Você", 0.0, 0.3), ("já", 0.3, 0.5), ("viu", 0.5, 0.8), ("isso?", 0.8, 1.2), ("A", 2.0, 2.1),
         ("gente", 2.1, 2.4), ("vai", 2.4, 2.6), ("zerar", 2.6, 3.0), ("o", 3.0, 3.1), ("jogo", 3.1, 3.5),
         ("hoje", 3.5, 3.9)]


def test_speech_measure():
    out = speech.measure(WORDS, 60.0)
    assert out["wpm"] == pytest.approx(11 / (3.9 / 60), rel=0.01)
    assert out["pauses_per_min"] == 1.0 and out["questions_per_min"] == 1.0
    assert out["person"] == "2"


def test_humor_offline_mock_and_no_copied_phrases():
    out = speech.humor(WORDS, 120.0)  # cadeia offline: MockLLM → mock_reference_humor
    assert out["tone_tags"] and out["structure"][0]["role"] == "gancho"
    assert out["gag_per_min"] > 0 and out["patterns"]

    def copier(system, prompt, context):
        assert "nunca copie frases" in system
        return {"structure": [], "tone_tags": ["irônico"], "gag_count": 2,
                "patterns": ["a gente vai zerar o jogo hoje", "exagero depois de dado sério"]}

    out = speech.humor(WORDS, 60.0, copier)
    assert out["patterns"] == ["exagero depois de dado sério"]


def test_speech_unavailable_offline():
    ok, reason = speech.available()
    assert not ok and "offline" in reason


# --- visão --------------------------------------------------------------------------------------------------------


def _strip(tmp_path: Path, name: str, color: tuple[int, int, int]) -> Path:
    path = tmp_path / name
    Image.new("RGB", (96, 32), color).save(path, "JPEG")
    return path


class CountingVision:
    id = "contador"

    def __init__(self) -> None:
        self.calls = 0

    def classify_frames(self, sheets, schema):
        self.calls += 1
        return [{"material": "official_trailer", "on_screen_text": "MAS VAI SER LEGAL", "text_case": "upper",
                 "text_pos": "bottom", "font_desc": "display pesada com contorno (estilo GTA)", "has_overlay": False,
                 "overlay_kind": None, "is_meme": False, "mood": "dark"} for _ in sheets]


def test_vision_classify_batches_and_cache(tmp_path):
    strips = [_strip(tmp_path, f"s{i}.jpg", (i * 10, 50, 90)) for i in range(14)]
    vis = CountingVision()
    answers, used = rvision.classify(strips, vis)
    assert vis.calls == 2 and used == "contador"  # 12 + 2
    assert answers[0]["material"] == "official_trailer"
    again, used = rvision.classify(strips, vis)
    assert vis.calls == 2 and again == answers and used == "contador"


def test_mock_vision_is_never_cached(tmp_path):
    strips = [_strip(tmp_path, "s.jpg", (200, 200, 200))]
    rvision.classify(strips, pvision.MockVision())
    assert not list(rvision._cache_dir().glob("*.json"))


def test_vision_normalize_rejects_unknown_material():
    out = rvision.normalize({"material": "Cinema", "text_case": "SHOUT", "is_meme": 1})
    assert out["material"] == "other" and out["text_case"] == "" and out["is_meme"] is True


def test_vision_blocks_mix_text_and_font_never_pricedown():
    shots = [
        {"a": 0.0, "dur": 6.0, "material": "official_trailer", "on_screen_text": "MAS VAI", "text_case": "upper",
         "text_pos": "bottom", "font_desc": "Pricedown, a fonte do GTA", "has_overlay": False, "overlay_kind": "",
         "is_meme": False},
        {"a": 6.0, "dur": 2.0, "material": "meme_reaction", "on_screen_text": "", "text_case": "", "text_pos": "",
         "font_desc": "", "has_overlay": True, "overlay_kind": "arrow", "is_meme": True},
        {"a": 8.0, "dur": 2.0, "material": "official_trailer", "on_screen_text": "SER LEGAL", "text_case": "upper",
         "text_pos": "bottom", "font_desc": "Pricedown, a fonte do GTA", "has_overlay": False,
         "overlay_kind": "", "is_meme": False},
    ]
    out = aggregate.vision_blocks(shots, 60.0)
    assert out["materials"]["mix"] == {"official_trailer": 0.8, "meme_reaction": 0.2}
    assert out["materials"]["prov_mix"] == {"official": 0.8, "meme": 0.2}
    assert out["materials"]["switch_per_min"] == 2.0 and out["materials"]["max_same_prov_run"] == 1
    assert out["overlays"]["meme_per_min"] == 1.0 and out["overlays"]["annot_per_min"] == 1.0
    assert out["overlays"]["text_pop_per_min"] == 2.0
    assert out["captions"]["style"] == "word" and out["captions"]["uppercase"] is True
    assert out["captions"]["font_match"] == "Anton"
    assert "pricedown" not in json.dumps(out["captions"]["font_match"]).lower()


def test_claude_vision_task_reads_only_its_workdir(tmp_path, monkeypatch):
    llm = ClaudeCodeLLM()
    cmd = llm._command(VISION_TASK, {"type": "object"}, tmp_path / "system.txt")
    i = cmd.index("--tools")
    assert cmd[i + 1] == "Read" and cmd[cmd.index("--allowedTools") + 1] == "Read(./**)"
    assert llm._command("style", {}, tmp_path / "s.txt")[llm._command("style", {}, tmp_path / "s.txt").index(
        "--tools") + 1] == ""
    seen = {}

    def fake_run(cmd, *, input=None, cwd=None, env=None, timeout=None):
        seen["files"] = sorted(p.name for p in Path(cwd).iterdir())
        out = {"result": "", "structured_output": {"items": [{"i": 0, "material": "gameplay"}]}, "usage": {}}
        return SimpleNamespace(stdout=json.dumps(out), stderr="", returncode=0)

    monkeypatch.setattr("studio.providers.llm.run_tree", fake_run)
    monkeypatch.setattr(llm, "_claude", lambda: "claude")
    strip = _strip(tmp_path, "tira.jpg", (1, 2, 3))
    result = llm.complete_json(task=VISION_TASK, system="s", prompt="p", schema={},
                               context={"images": [str(strip)], "names": ["img00.jpg"]})
    assert seen["files"] == ["img00.jpg", "system.txt"]
    assert pvision._by_index(result.data, 1, pvision.SHOT_SCHEMA)[0]["material"] == "gameplay"


def test_chain_vision_falls_back_and_reports_unavailable(tmp_path):
    from studio.providers.base import TransientError

    class Broken:
        id = "quebrado"

        def availability(self):
            return True, "ok"

        def classify_frames(self, sheets, schema):
            raise TransientError("limite")

    class Off:
        id = "desligado"

        def availability(self):
            return False, "sem login"

    good = CountingVision()
    good.availability = lambda: (True, "ok")  # type: ignore[attr-defined]
    chain = pvision.ChainVision([Broken(), good])
    out = chain.classify_frames([_strip(tmp_path, "a.jpg", (5, 5, 5))], pvision.SHOT_SCHEMA)
    assert out[0]["material"] == "official_trailer" and chain.used == "contador"
    with pytest.raises(pvision.VisionUnavailable):
        pvision.ChainVision([Off()]).classify_frames([], pvision.SHOT_SCHEMA)


def test_codex_vision_reads_the_last_message(tmp_path):
    def runner(cmd, *, cwd=None, env=None, timeout=None):
        assert cmd[cmd.index("--sandbox") + 1] == "read-only" and "-i" in cmd and "--output-schema" in cmd
        last = Path(cmd[cmd.index("-o") + 1])
        last.write_text('ok {"items": [{"i": 0, "material": "news", "is_meme": false}]}', encoding="utf-8")
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    out = pvision.CodexVision(runner).classify_frames([_strip(tmp_path, "c.jpg", (9, 9, 9))], pvision.SHOT_SCHEMA)
    assert out[0]["material"] == "news" and out[0]["has_overlay"] is False


def test_get_vision_offline_is_the_mock():
    assert isinstance(pvision.get_vision(), pvision.MockVision)


# --- recorrentes e agregação --------------------------------------------------------------------------------------


def test_recurrence_groups_five_or_more():
    a = _texture()
    items = [{"hash": recurrence.crop_hash(a), "t": float(i * 10), "source": "overlay", "box": None}
             for i in range(5)]
    items += [{"hash": recurrence.crop_hash(_texture(seed=40 + i)), "t": 100.0 + i, "source": "shot", "box": None}
              for i in range(4)]
    found = recurrence.recurring(items, kind_of=lambda members: "mascot")
    assert len(found) == 1 and found[0]["count"] == 5 and found[0]["kind"] == "mascot"
    assert found[0]["first_s"] == 0.0


def test_merge_profiles_takes_the_median():
    p1 = EditProfile(refs=["a"], duration_s=100, pace={"median_s": 1.0, "shots": 10},
                     materials={"mix": {"gameplay": 1.0}})
    p2 = EditProfile(refs=["b"], duration_s=200, pace={"median_s": 2.0, "shots": 20},
                     materials={"mix": {"gameplay": 0.5, "news": 0.5}})
    p3 = EditProfile(refs=["c"], duration_s=300, pace={"median_s": 4.0, "shots": 40},
                     materials={"mix": {"news": 1.0}})
    merged = aggregate.merge_profiles([p1, p2, p3])
    assert merged.refs == ["a", "b", "c"]
    assert merged.pace.median_s == 2.0 and merged.pace.shots == 20 and merged.duration_s == 200
    assert merged.materials.mix == {"gameplay": 0.5, "news": 0.5}


def test_max_still_counts_every_visual_event():
    base = {"info": {"dur": 20.0},
            "shots": cuts.shots_from_cuts([5.0, 5.2, 12.0], 20.0),
            "punches": [{"t": 8.0, "scale": 0.2}], "overlays": [{"start": 15.0}], "freezes": []}
    events = aggregate.visual_events(base)
    assert events == [5.0, 5.2, 8.0, 12.0, 15.0]
    assert aggregate.max_still(events, 20.0) == 5.0
