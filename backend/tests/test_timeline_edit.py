"""Camada de edição na timeline (ADR 0004 §3.7): subplanos e eventos resolvidos em quadros, SFX de evento, quedas
da trilha, efeitos por nível (`clean`), ênfase na legenda e câmera pela gramática. Sem a camada, nada muda."""

from __future__ import annotations

import json

import pytest

from studio.core import edit_resolve
from studio.core.hashing import hash_obj
from studio.core.project.document import (
    PRESENTER_AT_RE,
    BeatMark,
    FxEvent,
    MediaRef,
    ProjectDoc,
    Scene,
    SceneCard,
    Segment,
    ShotFraming,
    SubShot,
    Visual,
)
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.core.styles.library import get_style
from studio.core.styles.schema import EditGrammar
from studio.core.timeline import (
    SFX_LEAD,
    NarrationClip,
    build_timeline,
    chunk_timeline,
    lead_in,
    scene_motion,
)
from studio.providers.base import Word

TEXT = "isso aqui é chocante pessoal e de novo chocante no fim da frase toda mesmo"
TEXT_EN = "this here is shocking folks and once again shocking at the end of the whole sentence"
STEP = 0.5
STYLE = get_style("curiosidades-longo")
FPS = STYLE.format.fps


def _doc(*scenes: Scene, extra: dict | None = None) -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", text={"pt-BR": TEXT, "en": TEXT_EN}) for i in range(len(scenes))]
    return ProjectDoc(id="prj_tl", title="t", style_id="curiosidades-longo", segments=segments,
                      scenes=list(scenes), seed=3, languages=["pt-BR", "en"], **(extra or {}))


def _scene(i: int, **kwargs) -> Scene:
    kwargs.setdefault("visual", Visual(prompt="x"))
    return Scene(id=f"scn_{i}", segment_id=f"seg_{i}", **kwargs)


def _clips(doc: ProjectDoc, lang: str = "pt-BR") -> dict[str, NarrationClip]:
    toks = (TEXT if lang == "pt-BR" else TEXT_EN).split()
    return {seg.id: NarrationClip(seg.id, "x.wav", len(toks) * STEP + 0.2,
                                  [Word(t, k * STEP, k * STEP + 0.4) for k, t in enumerate(toks)])
            for seg in doc.segments}


def _timeline(doc: ProjectDoc, style=STYLE, lang: str = "pt-BR", **kwargs):
    images = {s.id: f"cache/images/{s.id}.jpg" for s in doc.scenes}
    return build_timeline(doc, style, lang, _clips(doc, lang), images, **kwargs)


def _word_frame(k: int) -> int:
    """Quadro (relativo à 1ª cena, que começa no 0) da k-ésima palavra do 1º segmento."""
    return int((lead_in(STYLE) + k * STEP) * FPS + 0.5)


# --- compatibilidade ----------------------------------------------------------------------------------------------


def test_old_project_keeps_the_same_timeline_and_chunk_hashes(isolated_settings):
    from compat_fixture import FIXTURE, timeline_hashes

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for style_id, expected in data["expected"].items():
        assert timeline_hashes(data["doc"], style_id) == expected


def test_without_edit_fields_no_new_key_appears():
    doc = _doc(_scene(0), _scene(1, kind="title", visual=Visual(type="none"), overlay_text={"pt-BR": "X"}))
    assert not edit_resolve.edit_active(doc)
    timeline = _timeline(doc)
    assert "music_drops" not in timeline
    for scene in timeline["scenes"]:
        assert not {"prov", "clean", "shots", "fx"} & set(scene)
        assert all("emph" not in w for line in scene["captions"] for w in line["words"])
    assert all(set(e) == {"type", "start", "scene_id"} for e in timeline["sfx"])


def test_scene_without_edit_fields_in_an_old_doc_keeps_the_chunk_hash():
    """Só o projeto com a camada muda: o mesmo documento sem ela gera os mesmos blocos de antes."""
    plain = _doc(_scene(0), _scene(1))
    again = _doc(_scene(0, shots=[], fx=[], mark=None), _scene(1))
    a = chunk_timeline(_timeline(plain), "v")
    b = chunk_timeline(_timeline(again), "v")
    assert [c["key"] for c in a] == [c["key"] for c in b]


# --- gatilhos -----------------------------------------------------------------------------------------------------


def test_word_with_occurrence_resolves_the_right_frame():
    doc = _doc(_scene(0, fx=[FxEvent(type="punch", at="chocante#2"), FxEvent(type="flash", at="chocante")]), _scene(1))
    fx = _timeline(doc)["scenes"][0]["fx"]
    by_type = {e["type"]: e for e in fx}
    assert by_type["flash"]["from"] == _word_frame(3)
    assert by_type["punch"]["from"] == _word_frame(8)
    assert [e["from"] for e in fx] == sorted(e["from"] for e in fx)


def test_at_milliseconds_resolves_in_ms():
    doc = _doc(_scene(0, fx=[FxEvent(type="shake", at="@1200", dur_ms=400)]), _scene(1))
    event = _timeline(doc)["scenes"][0]["fx"][0]
    assert event["from"] == round(1.2 * FPS)
    assert event["dur"] == round(0.4 * FPS)


def test_trigger_that_does_not_match_falls_to_the_start_with_a_warning():
    doc = _doc(_scene(0, fx=[FxEvent(type="text_pop", at="inexistente")]), _scene(1))
    timeline = _timeline(doc)
    assert timeline["scenes"][0]["fx"][0]["from"] == 0
    assert any("inexistente" in w and "Cena 1" in w for w in timeline["warnings"])


def test_translated_video_maps_the_trigger_by_position_without_warning():
    doc = _doc(_scene(0, fx=[FxEvent(type="punch", at="chocante#2")]), _scene(1))
    timeline = _timeline(doc, lang="en")
    assert "warnings" not in timeline
    event = timeline["scenes"][0]["fx"][0]
    words = timeline["scenes"][0]["words"]
    # "chocante#2" é a 9ª de 15 palavras do pt: no inglês (16 palavras), a palavra na mesma posição.
    assert event["from"] == words[int(8 / 15 * len(words))]["from"]


def test_all_events_fit_the_scene_and_keep_the_renderer_shape():
    doc = _doc(_scene(0, fx=[FxEvent(type=t, at="mesmo", dur_ms=8000) for t in ("pip", "counter", "highlight")]),
               _scene(1))
    timeline = _timeline(doc)
    scene = timeline["scenes"][0]
    for event in scene["fx"]:
        assert set(event) == {"type", "from", "dur", "params"}
        assert event["dur"] >= 1
        assert event["from"] + event["dur"] <= scene["durationInFrames"]


# --- SFX e trilha -------------------------------------------------------------------------------------------------


def test_fx_event_generates_sfx():
    doc = _doc(_scene(0, fx=[
        FxEvent(type="sfx", at="chocante", params={"kind": "impact", "lib_id": "mixkit-123", "gain_db": -3.0}),
        FxEvent(type="text_pop", at="pessoal", params={"sfx": "whoosh"}),
        FxEvent(type="punch", at="novo"),
    ]), _scene(1))
    timeline = _timeline(doc)
    events = [e for e in timeline["sfx"] if "at" in e]
    assert len(events) == 2  # o punch sem som não ganha som sozinho
    impact = next(e for e in events if e["type"] == "impact")
    at = _word_frame(3) / FPS
    assert impact == {"type": "impact", "at": round(at, 3), "start": round(at - SFX_LEAD["impact"], 3),
                      "scene": "scn_0", "scene_id": "scn_0", "lib_id": "mixkit-123", "gain_db": -3.0}
    whoosh = next(e for e in events if e["type"] == "whoosh")
    assert whoosh["start"] == round(_word_frame(4) / FPS - SFX_LEAD["whoosh"], 3)
    # O evento só de áudio não vai para o renderer; os outros vão.
    assert [e["type"] for e in timeline["scenes"][0]["fx"]] == ["text_pop", "punch"]
    assert timeline["sfx"] == sorted(timeline["sfx"], key=lambda e: e["start"])


def test_music_drop_goes_to_the_timeline_only_when_present():
    doc = _doc(_scene(0, fx=[FxEvent(type="music_drop", at="chocante", dur_ms=500)]), _scene(1))
    timeline = _timeline(doc)
    assert timeline["music_drops"] == [{"at": round(_word_frame(3) / FPS, 3), "dur": 0.5}]
    assert "fx" not in timeline["scenes"][0]


def test_event_sfx_on_the_cut_replaces_the_automatic_cut_sound():
    plain = _doc(_scene(0), _scene(1, transition_in="flash", mark=BeatMark()))
    cut = [e for e in _timeline(plain)["sfx"] if e["scene_id"] == "scn_1"]
    assert [e["type"] for e in cut] == ["whoosh"]
    stacked = _doc(_scene(0), _scene(1, transition_in="flash", fx=[FxEvent(type="sfx", at="@0")]))
    events = [e for e in _timeline(stacked)["sfx"] if e["scene_id"] == "scn_1"]
    assert [e["type"] for e in events] == ["impact"]


# --- efeitos por nível --------------------------------------------------------------------------------------------


def _tiers_doc() -> ProjectDoc:
    news = MediaRef(kind="news", source="link", asset="uploads/prj_tl/media/0123456789abcdef.jpg", outlet="X",
                    headline={"pt-BR": "Manchete"})
    official = MediaRef(kind="official", source="link", asset="uploads/prj_tl/media/fedcba9876543210.jpg")
    own = MediaRef(kind="photo", source="upload", asset="uploads/prj_tl/media/00112233445566aa.jpg")
    return _doc(
        _scene(0, mark=BeatMark(role="hook")),
        _scene(1, visual=Visual(prompt="x", media=news)),
        _scene(2, visual=Visual(prompt="x", media=official)),
        _scene(3, visual=Visual(prompt="x", media=own)),
        _scene(4, kind="stat", visual=Visual(type="none"), card=SceneCard(value={"pt-BR": "12"})),
    )


def test_clean_appears_by_tier():
    scenes = _timeline(_tiers_doc())["scenes"]
    assert [s["prov"] for s in scenes] == ["ai", "capture", "official", "own", "graphic"]
    assert [s.get("clean") for s in scenes] == [None, True, None, True, True]


def test_global_effects_policy_has_no_clean():
    style = STYLE.model_copy(deep=True)
    style.edit.grammar = EditGrammar(effects_policy="global")
    scenes = _timeline(_tiers_doc(), style=style)["scenes"]
    assert all("clean" not in s for s in scenes)
    assert all("prov" in s for s in scenes)


# --- legenda ------------------------------------------------------------------------------------------------------


def _emph(timeline) -> list[tuple[str, str, int]]:
    return [(w["text"], w["emph"], w["from"]) for line in timeline["scenes"][0]["captions"] for w in line["words"]
            if "emph" in w]


def test_emph_falls_on_the_right_words():
    doc = _doc(_scene(0, mark=BeatMark(role="reveal", emphasis=["chocante", "fim da frase"])), _scene(1))
    marked = _emph(_timeline(doc))
    assert [(t, e) for t, e, _ in marked] == [("chocante", "pop"), ("fim", "pop"), ("da", "pop"), ("frase", "pop")]
    assert marked[0][2] == _word_frame(3)  # a 1ª ocorrência, não a 2ª


def test_emph_follows_the_caption_emphasis_of_the_grammar():
    doc = _doc(_scene(0, mark=BeatMark(emphasis=["pessoal"]), fx=[
        FxEvent(type="text_pop", at="pessoal", params={"emoji": "😱"})]), _scene(1))
    for mode, expected in (("color", "color"), ("none", None)):
        style = STYLE.model_copy(deep=True)
        style.edit.grammar = EditGrammar(caption_emphasis=mode)
        marked = _emph(_timeline(doc, style=style))
        assert (marked[0][1] if marked else None) == expected
    style = STYLE.model_copy(deep=True)
    style.edit.grammar = EditGrammar(caption_emphasis="emoji")
    words = [w for line in _timeline(doc, style=style)["scenes"][0]["captions"] for w in line["words"]]
    assert next(w for w in words if w["text"] == "pessoal")["emoji"] == "😱"


# --- subplanos ----------------------------------------------------------------------------------------------------


def _pool() -> Pool:
    return Pool(items=[
        PoolItem(id="p1", tier="official", media="clip", path="uploads/prj_tl/pool/p1.mp4",
                 poster="uploads/prj_tl/pool/p1.jpg", start_s=4.0),
        PoolItem(id="p2", tier="capture", media="capture", path="uploads/prj_tl/pool/p2.jpg",
                 rects=[[0.1, 0.2, 0.3, 0.1]]),
    ])


def _shots_doc() -> ProjectDoc:
    media = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg")
    return _doc(
        _scene(0, shots=[
            SubShot(at="chocante", source="pool", pool_id="p1", speed=1.5),
            SubShot(at="pessoal", source="media", media=media, framing=ShotFraming(scale=1.25, x=0.3, y=0.4),
                    motion="push"),
            SubShot(at="novo", source="pool", pool_id="p2"),
            SubShot(at="chocante#2", source="reuse", reuse_of="scn_1"),
            SubShot(at="fim", source="pool", pool_id="p99"),
        ]),
        _scene(1),
    )


def test_shots_resolve_to_frames_covering_the_scene():
    timeline = _timeline(_shots_doc(), pool=_pool())
    scene = timeline["scenes"][0]
    shots = scene["shots"]
    assert shots[0]["from"] == 0 and "src" not in shots[0] and shots[0]["prov"] == "ai"  # antes do 1º corte: a cena
    assert [s["from"] for s in shots[1:]] == [_word_frame(k) for k in (3, 4, 7, 8, 10)]
    assert sum(s["dur"] for s in shots) == scene["durationInFrames"]
    for a, b in zip(shots, shots[1:], strict=False):
        assert a["from"] + a["dur"] == b["from"]
    clip, photo, capture, reuse, missing = shots[1:]
    assert clip == {**clip, "prov": "official", "video": "uploads/prj_tl/pool/p1.mp4",
                    "src": "uploads/prj_tl/pool/p1.jpg", "clipIn": 4.0, "speed": 1.5, "motion": "none"}
    assert photo["prov"] == "free" and photo["src"].endswith("0123456789abcdef.jpg")
    assert photo["framing"] == {"scale": 1.25, "x": 0.3, "y": 0.4} and photo["motion"] == "push"
    assert photo["easing"] in ("linear", "outCubic", "inOutQuad")
    assert capture["prov"] == "capture" and capture["motion"] == "static" and "easing" not in capture
    assert reuse == {**reuse, "prov": "ai", "src": "cache/images/scn_1.jpg"}
    assert missing["prov"] == "ai" and "src" not in missing
    assert any("p99" in w for w in timeline["warnings"])
    for shot in shots:
        assert {"from", "dur", "prov", "framing", "motion"} <= set(shot)


def test_shot_clip_carries_its_useful_seconds_for_the_renderer():
    # `seconds` (TimelineShot no renderer): com ele o plano com `hold` congela quando o trecho acaba.
    pool = Pool(items=[PoolItem(id="p1", tier="official", media="clip", path="uploads/prj_tl/pool/p1.mp4",
                                start_s=2.0, dur_s=1.25)])
    media = MediaRef(kind="clip", source="upload", asset="uploads/prj_tl/media/a.jpg",
                     video="uploads/prj_tl/media/a.mp4", dur_s=0.8, hold=True)
    doc = _doc(_scene(0, shots=[SubShot(at="chocante", source="pool", pool_id="p1", hold=True),
                                SubShot(at="pessoal", source="media", media=media)]), _scene(1))
    pool_shot, media_shot = _timeline(doc, pool=pool)["scenes"][0]["shots"][1:]
    assert pool_shot["seconds"] == 1.25 and pool_shot["hold"] is True and pool_shot["clipIn"] == 2.0
    assert media_shot["seconds"] == 0.8 and media_shot["hold"] is True
    assert "seconds" not in _timeline(_shots_doc(), pool=_pool())["scenes"][0]["shots"][1]  # sem dur_s: até o fim


def test_shots_read_the_pool_file_when_not_given(isolated_settings):
    save_pool("prj_tl", _pool())
    shots = _timeline(_shots_doc())["scenes"][0]["shots"]
    assert shots[1]["video"] == "uploads/prj_tl/pool/p1.mp4"


def test_highlight_takes_the_rect_of_the_capture():
    capture = MediaRef(kind="capture", source="capture", asset="uploads/prj_tl/media/0123456789abcdef.jpg",
                       rects=[[0.1, 0.2, 0.5, 0.1]])
    doc = _doc(_scene(0, visual=Visual(prompt="x", media=capture), fx=[
        FxEvent(type="highlight", at="chocante"), FxEvent(type="text_pop", at="pessoal,"),
        FxEvent(type="punch", at="novo")]), _scene(1))
    fx = {e["type"]: e for e in _timeline(doc)["scenes"][0]["fx"]}
    assert fx["highlight"]["params"]["rect"] == [0.1, 0.2, 0.5, 0.1]
    assert fx["text_pop"]["params"]["text"] == "pessoal"
    low, high = STYLE.edit.grammar.punch_scale.min, STYLE.edit.grammar.punch_scale.max
    assert low <= fx["punch"]["params"]["scale"] <= high


def test_same_doc_gives_the_same_timeline():
    a = _timeline(_shots_doc(), pool=_pool())
    b = _timeline(_shots_doc(), pool=_pool())
    assert hash_obj(a) == hash_obj(b)


def test_edit_triggers_validate_with_the_presenter_syntax():
    for scene in _shots_doc().scenes:
        for shot in scene.shots:
            assert shot.at is None or PRESENTER_AT_RE.match(shot.at)


# --- câmera -------------------------------------------------------------------------------------------------------


def test_grammar_motion_respects_the_shares():
    grammar = EditGrammar(static_share_min=0.25, slow_zoom_share=0.30)
    camera = {"ken_burns_in": 3.0, "ken_burns_out": 2.0, "drift": 1.0, "push": 1.0}
    picks = [edit_resolve.grammar_motion(camera, grammar, seed) for seed in range(3000)]
    static = picks.count("static") / len(picks)
    slow = sum(p in edit_resolve.SLOW_ZOOMS for p in picks) / len(picks)
    assert 0.22 <= static <= 0.28
    assert 0.26 <= slow <= 0.34


def test_scene_motion_follows_the_grammar_only_in_edited_projects():
    scenes = [_scene(i) for i in range(60)]
    old = _doc(*scenes)
    new = _doc(*[s.model_copy(update={"mark": BeatMark()}) for s in scenes])
    style = STYLE.model_copy(deep=True)
    style.edit.camera = {"ken_burns_in": 1.0}
    assert {scene_motion(old, style, s) for s in old.scenes} == {"ken_burns_in"}
    motions = [scene_motion(new, style, s) for s in new.scenes]
    assert "static" in motions and "push" in motions
    assert motions.count("ken_burns_in") / len(motions) <= style.edit.grammar.slow_zoom_share + 0.15
    chosen = new.scenes[0].model_copy(update={"motion": "parallax"})
    assert scene_motion(new, style, chosen) == "parallax"


def test_real_media_can_be_static_or_push_only_with_the_edit_layer():
    media = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg")
    old = _doc(*[_scene(i, visual=Visual(prompt="x", media=media)) for i in range(40)])
    assert {scene_motion(old, STYLE, s) for s in old.scenes} <= {"ken_burns_in", "ken_burns_out", "drift"}
    new = _doc(*[_scene(i, visual=Visual(prompt="x", media=media), mark=BeatMark()) for i in range(40)])
    assert "static" in {scene_motion(new, STYLE, s) for s in new.scenes}


@pytest.mark.parametrize("motion", ["static", "push"])
def test_chosen_motion_is_kept(motion):
    doc = _doc(_scene(0, motion=motion, mark=BeatMark()), _scene(1))
    assert _timeline(doc)["scenes"][0]["motion"] == motion
