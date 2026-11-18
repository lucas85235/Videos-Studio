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


def _doc(style_id: str, n: int = 24, kinds: dict[int, str] | None = None, visual: str = "ai_image") -> ProjectDoc:
    seg = Segment(id="seg_0", text={"pt-BR": " ".join(f"palavra{j}" for j in range(n * 3)) + "."})
    scenes = [
        Scene(id=f"scn_{k}", segment_id=seg.id, part=k, parts=n, kind=(kinds or {}).get(k, "image"),
              visual=Visual(type=visual, prompt="x"))  # type: ignore[arg-type]
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
    _, _, real = _timeline("misterios-short", visual="stock")
    _, _, screen = _timeline("misterios-short", {"family": "screen_ui", "families_mix": [], "edit": {"treatments": {"screen": 1}}},
                             visual="stock")
    assert all(s["treatment"] == "screen" for s in screen["scenes"])
    assert [c["key"] for c in chunk_timeline(screen, "v")] != [c["key"] for c in chunk_timeline(real, "v")]


def test_scene_with_news_card_keeps_plain():
    doc = _doc("interface-tela", n=2, visual="stock")
    style = get_style("interface-tela", {"edit": {"treatments": {"screen": 1, "plain": 0}}})  # troca os pesos do preset
    scene = doc.scenes[0]
    assert scene_treatment(doc, style, scene) == "screen"
    assert scene_treatment(doc, style, scene, has_news=True) == "plain"


def test_screen_never_frames_an_ai_image():
    """QA do prj_gq4krrs11w: post de celular com curtida e janela de navegador com cursor sobre imagem de IA simulam uma
    captura real que não existe. A imagem de IA sorteia entre os outros tratamentos; só com a tela, fica sem."""
    only_screen = {"family": "screen_ui", "families_mix": [], "edit": {"treatments": {"screen": 1}}}
    _, _, ai = _timeline("misterios-short", only_screen)
    assert all("treatment" not in s for s in ai["scenes"])
    _, _, mixed = _timeline("misterios-short", {"family": "screen_ui", "families_mix": [],
                                                "edit": {"treatments": {"screen": 3, "collage": 1}}})
    assert {s.get("treatment") for s in mixed["scenes"]} == {"collage"}
    _, _, real = _timeline("misterios-short", only_screen, visual="stock")
    assert all(s["treatment"] == "screen" for s in real["scenes"])


def test_screen_never_frames_an_ai_subshot_inside_a_real_scene():
    """A cena real com subplano de IA (reaproveitamento de cena de IA, item de IA ou arte de fã do acervo, mídia de
    IA) também não ganha a moldura de tela: o post com curtida mostraria a IA como captura."""
    from studio.core.project.document import MediaRef, SubShot
    from studio.core.project.pool import Pool, PoolItem

    style = get_style("interface-tela", {"edit": {"treatments": {"screen": 1, "plain": 0}}})
    doc = _doc("interface-tela", n=3, visual="stock")
    doc.scenes[2].visual = Visual(type="ai_image", prompt="x")
    fan = PoolItem(id="p1", tier="free", media="image", desc="GTA 6 Fan Cover", source_id="openverse:1", path="a.jpg")
    gen = PoolItem(id="p2", tier="ai", media="image", desc="x", source_id="gen:1", path="b.jpg")
    real = PoolItem(id="p3", tier="free", media="image", desc="Golden Gate Bridge", source_id="openverse:3",
                    path="c.jpg")
    pool = Pool(items=[fan, gen, real])
    scene = doc.scenes[0]
    assert scene_treatment(doc, style, scene, pool=pool) == "screen"
    cases = [SubShot(source="reuse", reuse_of="scn_2"), SubShot(source="pool", pool_id="p1"),
             SubShot(source="pool", pool_id="p2"),
             SubShot(source="media", media=MediaRef(kind="photo", source="link", asset="d.jpg", tier="ai"))]
    for shot in cases:
        scene.shots = [SubShot(), shot]
        assert scene_treatment(doc, style, scene, pool=pool) == "plain", shot.source
    scene.shots = [SubShot(), SubShot(source="pool", pool_id="p3"), SubShot(source="reuse", reuse_of="scn_1")]
    assert scene_treatment(doc, style, scene, pool=pool) == "screen"


def test_first_whiteboard_scene_starts_drawn_and_quick():
    _, _, timeline = _timeline("whiteboard-explicador")
    first, *rest = timeline["scenes"]
    assert first["treatment"] == "whiteboard" and first["treatment_quick"] is True
    assert all("treatment_quick" not in s for s in rest)
    # colagem e tela já mostram a imagem logo: sem a marca
    _, _, collage = _timeline("colagem-artesanal", {"edit": {"treatments": {"collage": 1}}})
    assert "treatment_quick" not in collage["scenes"][0]


def test_scene_reusing_previous_image_continues_the_treatment():
    doc = _doc("whiteboard-explicador", n=6)
    # scn_1 e scn_2 reaproveitam a imagem de scn_0 (cadeia); scn_4 reaproveita scn_3
    doc.scenes[1].visual.reuse_of = "scn_0"
    doc.scenes[2].visual.reuse_of = "scn_1"
    doc.scenes[4].visual.reuse_of = "scn_3"
    images = {"scn_0": "a.png", "scn_1": "a.png", "scn_2": "a.png", "scn_3": "b.png", "scn_4": "b.png", "scn_5": "c.png"}
    style = get_style("whiteboard-explicador")
    scenes = build_timeline(doc, style, "pt-BR", _clips(doc), images)["scenes"]
    by_id = {s["id"]: s for s in scenes}
    assert "treatment_continue" not in by_id["scn_0"]
    assert by_id["scn_1"]["treatment_continue"] == by_id["scn_0"]["seed"]
    assert by_id["scn_2"]["treatment_continue"] == by_id["scn_0"]["seed"]  # a semente de quem começou
    assert "treatment_continue" not in by_id["scn_3"]
    assert by_id["scn_4"]["treatment_continue"] == by_id["scn_3"]["seed"]
    assert "treatment_continue" not in by_id["scn_5"]


def test_reused_image_with_other_treatment_starts_fresh():
    doc = _doc("colagem-artesanal", n=2)
    doc.scenes[1].visual.reuse_of = "scn_0"
    images = {"scn_0": "a.png", "scn_1": "a.png"}
    style = get_style("colagem-artesanal", {"edit": {"treatments": {"plain": 1}}})
    scenes = build_timeline(doc, style, "pt-BR", _clips(doc), images)["scenes"]
    assert all("treatment" not in s and "treatment_continue" not in s for s in scenes)


def test_project_override_replaces_treatment_weights():
    # vazio volta ao automático pela família (handmade + still_cinema: 1 × 1), não aos pesos 4 × 1 do preset
    assert treatment_pool(get_style("colagem-artesanal", {"edit": {"treatments": {}}})) == {"collage": 1.0, "plain": 1.0}
    assert treatment_pool(get_style("colagem-artesanal", {"edit": {"treatments": {"screen": 1}}})) == {"screen": 1}
    assert treatment_pool(get_style("whiteboard-explicador", {"edit": {"treatments": {"plain": 1}}})) == {"plain": 1}
    # os outros mapas continuam mesclados
    merged = get_style("colagem-artesanal", {"edit": {"camera": {"static": 9}}})
    assert merged.edit.camera["static"] == 9 and len(merged.edit.camera) > 1


def test_set_style_op_on_one_treatment_weight_keeps_the_others():
    from studio.core.project.ops import OpError, SetStyle, apply_op

    base = get_style("colagem-artesanal")
    doc = _doc("colagem-artesanal", n=1)
    apply_op(doc, SetStyle(path="edit.treatments.collage", value=0), base)
    assert doc.style_overrides["edit"]["treatments"] == {"collage": 0, "plain": 1}
    assert treatment_pool(get_style("colagem-artesanal", doc.style_overrides)) == {"plain": 1}
    apply_op(doc, SetStyle(path="edit.treatments", value={}), base)
    assert treatment_pool(get_style("colagem-artesanal", doc.style_overrides)) == {"collage": 1.0, "plain": 1.0}  # handmade + still_cinema
    # tirar o único tratamento do preset da lousa continua inválido (com mensagem)
    board = _doc("whiteboard-explicador", n=1)
    with pytest.raises(OpError):
        apply_op(board, SetStyle(path="edit.treatments.whiteboard", value=0), get_style("whiteboard-explicador"))
