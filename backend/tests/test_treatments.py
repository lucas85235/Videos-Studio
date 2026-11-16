"""Família ilustrado 2D, colagem e interface de tela: tratamento das cenas de imagem na timeline."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.core.project.document import ProjectDoc, Scene, Segment, Visual
from studio.core.styles.library import get_style, load_presets
from studio.core.styles.schema import (
    FAMILIES_V1,
    TREATMENTS,
    EditSpec,
    Family,
    policy_warnings,
    treatment_pool,
)
from studio.core.styles.visual import VISUAL_LANGUAGES
from studio.core.timeline import NarrationClip, build_timeline, chunk_timeline, scene_treatment
from studio.providers.base import Word

LEGACY_SCENE_KEYS = {"id", "kind", "motion", "transition", "seed", "look", "text", "number", "from", "durationInFrames",
                     "src", "captions", "words"}


def _doc(style_id: str, n: int = 24, kinds: dict[int, str] | None = None) -> ProjectDoc:
    seg = Segment(id="seg_0", text={"pt-BR": " ".join(f"palavra{j}" for j in range(n * 3)) + "."})
    scenes = [
        Scene(id=f"scn_{k}", segment_id=seg.id, part=k, parts=n, kind=(kinds or {}).get(k, "image"), visual=Visual(prompt="x"))
        for k in range(n)
    ]
    return ProjectDoc(id="prj_t", title="t", style_id=style_id, segments=[seg], scenes=scenes, seed=7)


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    seg = doc.segments[0]
    words = seg.text["pt-BR"].split()
    step = 0.3
    return {seg.id: NarrationClip(segment_id=seg.id, path="x.wav", duration=len(words) * step,
                                  words=[Word(w, i * step, (i + 0.9) * step) for i, w in enumerate(words)])}


def _timeline(style_id: str, overrides: dict | None = None, **kw):
    doc = _doc(style_id, **kw)
    style = get_style(style_id, overrides)
    return doc, style, build_timeline(doc, style, "pt-BR", _clips(doc), {})


def test_presets_of_the_three_families():
    presets = load_presets()
    expected = {"whiteboard-explicador": (Family.illustrated_2d, "whiteboard"),
                "colagem-artesanal": (Family.handmade, "collage"),
                "interface-tela": (Family.screen_ui, "screen")}
    for style_id, (family, treatment) in expected.items():
        style = presets[style_id]
        assert style.family == family
        assert treatment in treatment_pool(style)
        # a família agora é desenhada: sem o aviso de "usarão o modo imagens paradas"
        assert not any("ainda não implementadas" in w for w in policy_warnings(style)), style_id
    assert presets["whiteboard-explicador"].art.languages[0] == "line_art"
    assert "line_art" in VISUAL_LANGUAGES and not VISUAL_LANGUAGES["line_art"].photoreal
    assert {Family.illustrated_2d, Family.handmade, Family.screen_ui} <= FAMILIES_V1


def test_treatment_pool_follows_family_when_style_has_none():
    base = get_style("curiosidades-short")
    assert treatment_pool(base) == {"plain": 2.0}  # motion_graphics + still_cinema: sem variação
    mixed = get_style("curiosidades-short", {"family": "handmade", "families_mix": ["handmade", "screen_ui", "still_cinema"]})
    assert treatment_pool(mixed) == {"collage": 1.0, "screen": 1.0, "plain": 1.0}
    explicit = get_style("curiosidades-short", {"edit": {"treatments": {"whiteboard": 2, "plain": 0}}})
    assert treatment_pool(explicit) == {"whiteboard": 2}


def test_treatments_are_validated():
    assert EditSpec(treatments={}).treatments == {}
    assert EditSpec(treatments={t: 1 for t in TREATMENTS}).treatments
    with pytest.raises(ValidationError):
        EditSpec(treatments={"origami": 1})
    with pytest.raises(ValidationError):
        EditSpec(treatments={"collage": -1})
    with pytest.raises(ValidationError):
        EditSpec(treatments={"collage": 0})


def test_whiteboard_preset_draws_every_image_scene_and_skips_cards():
    kinds = {3: "stat", 5: "title", 8: "text"}
    doc, style, timeline = _timeline("whiteboard-explicador", kinds=kinds)
    for scene in timeline["scenes"]:
        if scene["kind"] == "image":
            assert scene["treatment"] == "whiteboard", scene["id"]
        else:
            assert "treatment" not in scene, scene["id"]


def test_mixed_pool_is_deterministic_and_uses_both_options():
    _, _, first = _timeline("colagem-artesanal")
    _, _, again = _timeline("colagem-artesanal")
    picks = [s.get("treatment", "plain") for s in first["scenes"]]
    assert picks == [s.get("treatment", "plain") for s in again["scenes"]]
    assert set(picks) == {"collage", "plain"}
    assert picks.count("collage") > picks.count("plain")  # pesos 4 × 1
    # "plain" não grava chave: a cena fica igual à de antes
    for scene in first["scenes"]:
        assert scene.get("treatment") in (None, "collage")


def test_screen_treatment_changes_block_key_but_plain_styles_keep_old_scenes():
    _, _, base = _timeline("misterios-short")
    assert all(set(s) == LEGACY_SCENE_KEYS for s in base["scenes"])  # estilos antigos: mesma timeline de antes
    _, _, screen = _timeline("misterios-short", {"family": "screen_ui", "families_mix": [], "edit": {"treatments": {"screen": 1}}})
    assert all(s["treatment"] == "screen" for s in screen["scenes"])
    assert [c["key"] for c in chunk_timeline(screen, "v")] != [c["key"] for c in chunk_timeline(base, "v")]


def test_scene_with_news_card_keeps_plain():
    doc = _doc("interface-tela", n=2)
    style = get_style("interface-tela", {"edit": {"treatments": {"screen": 1, "plain": 0}}})  # mescla com os pesos do preset
    scene = doc.scenes[0]
    assert scene_treatment(doc, style, scene) == "screen"
    assert scene_treatment(doc, style, scene, has_news=True) == "plain"
