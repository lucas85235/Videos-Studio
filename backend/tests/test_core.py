from __future__ import annotations

import pytest
from pydantic import ValidationError

from studio.core.hashing import hash_obj, seed_from
from studio.core.project import service
from studio.core.project.document import ProjectDoc, Scene, Segment, Visual
from studio.core.project.ops import OpError, apply_ops, parse_ops
from studio.core.styles.library import (
    deep_merge,
    get_style,
    load_presets,
    pick_weighted,
    save_user_style,
    set_path,
)
from studio.core.styles.schema import StyleSpec, policy_warnings
from studio.core.text import split_word_indices, word_count


def test_presets_are_valid():
    presets = load_presets()
    assert {"misterios-short", "documentario-sombrio", "terror-analog", "true-crime", "top10-listas", "estoico-short"} <= set(presets)
    for style in presets.values():
        assert style.format.width > 0 and style.format.height > 0


def test_hash_is_order_independent():
    assert hash_obj({"a": 1, "b": [1, 2]}) == hash_obj({"b": [1, 2], "a": 1})
    assert hash_obj({"a": 1}) != hash_obj({"a": 2})
    assert seed_from("x", 1) == seed_from("x", 1)


def test_pick_weighted_is_deterministic_and_respects_exclude():
    pool = {"a": 1, "b": 1, "c": 1}
    assert pick_weighted(pool, 42) == pick_weighted(pool, 42)
    for seed in range(50):
        assert pick_weighted(pool, seed, exclude={"a", "b"}) == "c"


def test_style_overrides_and_validation():
    base = get_style("misterios-short")
    style = get_style("misterios-short", {"edit": {"captions": {"size": 100}}})
    assert style.edit.captions.size == 100
    assert style.edit.camera == base.edit.camera
    with pytest.raises(ValidationError):
        StyleSpec.model_validate(deep_merge(base.model_dump(mode="json"), {"edit": {"camera": {"voar": 1}}}))
    assert set_path({}, "a.b.c", 1) == {"a": {"b": {"c": 1}}}


def test_user_style_crud():
    style = get_style("misterios-short").model_copy(update={"id": "meu-estilo", "name": "Meu estilo"})
    assert save_user_style(style) == 1
    assert save_user_style(style) == 2
    assert get_style("meu-estilo").name == "Meu estilo"
    with pytest.raises(ValueError):
        save_user_style(get_style("misterios-short"))


def test_policy_warnings_flag_blocked_tags():
    style = get_style("misterios-short").model_copy(update={"tags": ["reddit-gameplay"]})
    assert any("Bloqueado" in w for w in policy_warnings(style))


def test_text_helpers():
    assert word_count("Olá, mundo! — isto é um teste.") == 6
    ranges = split_word_indices(10, 3)
    assert ranges[0][0] == 0 and ranges[-1][1] == 10
    assert all(a <= b for a, b in ranges)


def _doc() -> ProjectDoc:
    seg1 = Segment(id="seg_a", text={"pt-BR": "Primeiro segmento com algumas palavras."})
    seg2 = Segment(id="seg_b", text={"pt-BR": "Segundo segmento."})
    return ProjectDoc(
        id="prj_test",
        title="Teste",
        style_id="misterios-short",
        segments=[seg1, seg2],
        scenes=[
            Scene(id="scn_1", segment_id="seg_a", visual=Visual(prompt="uma porta antiga")),
            Scene(id="scn_2", segment_id="seg_b", visual=Visual(prompt="um corredor")),
        ],
    )


def test_ops_apply_and_validate():
    doc = _doc()
    ops = parse_ops(
        [
            {"op": "update_scene_prompt", "scene_id": "scn_1", "prompt": "porta com névoa"},
            {"op": "regenerate_scene", "scene_id": "scn_2"},
            {"op": "set_scene_look", "scene_id": "scn_1", "brightness": -0.3},
            {"op": "set_style", "path": "edit.captions.size", "value": 96},
            {"op": "split_scene", "scene_id": "scn_1"},
        ]
    )
    new_doc = apply_ops(doc, ops, get_style("misterios-short"))
    assert new_doc.scene("scn_1").visual.prompt == "porta com névoa"
    assert new_doc.scene("scn_2").visual.variation == 1
    assert new_doc.scene("scn_1").look.brightness == -0.3
    assert new_doc.style_overrides == {"edit": {"captions": {"size": 96}}}
    assert len(new_doc.scenes_of("seg_a")) == 2
    assert [s.part for s in new_doc.scenes_of("seg_a")] == [0, 1]
    # o original não muda
    assert doc.scene("scn_1").visual.prompt == "uma porta antiga"


def test_ops_respect_locks_and_bad_values():
    doc = _doc()
    locked = apply_ops(doc, parse_ops([{"op": "set_lock", "ids": ["scn_1"], "locked": True}]))
    with pytest.raises(OpError):
        apply_ops(locked, parse_ops([{"op": "regenerate_scene", "scene_id": "scn_1"}]))
    with pytest.raises(OpError):
        apply_ops(doc, parse_ops([{"op": "set_scene_motion", "scene_id": "scn_1", "motion": "voar"}]))
    with pytest.raises(OpError):
        apply_ops(doc, parse_ops([{"op": "set_style", "path": "edit.captions.size", "value": 9999}]), get_style("misterios-short"))


def test_project_versions_roundtrip():
    doc = service.create_project(title="Mistério", style_id="misterios-short", topic="o farol abandonado")
    assert service.get_project_row(doc.id)["current_version"] == 1
    doc2 = doc.model_copy(update={"title": "Mistério 2"})
    assert service.commit(doc.id, doc2, "renomeado") == 2
    assert service.get_doc(doc.id).title == "Mistério 2"
    assert service.checkout(doc.id, 1) == 3
    assert service.get_doc(doc.id).title == "Mistério"
    assert [v["number"] for v in service.list_versions(doc.id)] == [3, 2, 1]
