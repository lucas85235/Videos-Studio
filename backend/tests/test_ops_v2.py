"""Operações de edição com as cenas da curadoria visual: spans, reaproveitamento de imagem e troca de estilo."""

from __future__ import annotations

import pytest

from studio.core.agent.editor import _OP_CLASSES, flat_to_ops
from studio.core.project.document import ArtDirection, ProjectDoc, Scene, SceneCard, Segment, Visual
from studio.core.project.ops import OpError, apply_ops, parse_ops
from studio.workers.pipeline.schemas import EDIT_OPS, FLAT_OP_FIELDS

DIRECTED = Visual(prompt="a neon city at sunset", language="key_art", shot="wide", composition="center", seed=7)


def _doc(**extra) -> ProjectDoc:
    seg_a = Segment(id="seg_a", text={"pt-BR": "Primeira parte do roteiro com várias palavras aqui."})
    seg_b = Segment(id="seg_b", text={"pt-BR": "Segunda parte do roteiro."})
    scenes = [
        Scene(id="scn_root", segment_id="seg_a", part=0, parts=2, visual=DIRECTED, span=5),
        Scene(id="scn_stat", segment_id="seg_a", part=1, parts=2, kind="stat", visual=Visual(type="none"),
              card=SceneCard(value={"pt-BR": "475 MI"}), motion="static", span=3),
        Scene(id="scn_r1", segment_id="seg_b", part=0, parts=3, visual=Visual(reuse_of="scn_root"), focus="top", span=2),
        Scene(id="scn_r2", segment_id="seg_b", part=1, parts=3, visual=Visual(reuse_of="scn_root"), span=1),
        Scene(id="scn_r3", segment_id="seg_b", part=2, parts=3, visual=Visual(reuse_of="scn_r1"), span=1),  # cadeia
    ]
    data = {"id": "prj_ops", "title": "Ops", "style_id": "misterios-short", "segments": [seg_a, seg_b],
            "scenes": scenes, "art": ArtDirection(genre="games", languages=["key_art", "render_3d"]),
            "storyboard_approved": True}
    return ProjectDoc(**{**data, **extra})


def _apply(doc: ProjectDoc, *ops: dict) -> ProjectDoc:
    return apply_ops(doc, parse_ops(list(ops)))


def test_split_merge_keep_spans():
    doc = _doc()
    split = _apply(doc, {"op": "split_scene", "scene_id": "scn_root"})
    first, second = split.scenes_of("seg_a")[:2]
    assert first.id == "scn_root" and (first.span, second.span) == (3, 2)
    assert sum(s.span for s in split.scenes_of("seg_a")) == 8  # o segmento continua coberto inteiro
    # a parte nova reaproveita a imagem (nenhuma imagem nova) com outro foco
    assert second.visual.reuse_of == "scn_root" and not second.visual.prompt and second.focus == "top"

    merged = _apply(split, {"op": "merge_scene_with_next", "scene_id": "scn_root"})
    assert [s.span for s in merged.scenes_of("seg_a")] == [5, 3]
    assert merged.scene("scn_root").visual == DIRECTED

    no_span = split.model_copy(deep=True)
    no_span.scenes_of("seg_a")[1].span = None
    joined = _apply(no_span, {"op": "merge_scene_with_next", "scene_id": "scn_root"})
    assert joined.scene("scn_root").span is None  # falta um span: volta à divisão antiga


def test_split_with_prompt_and_split_of_cards():
    doc = _doc()
    with_prompt = _apply(doc, {"op": "split_scene", "scene_id": "scn_root", "prompt": "a crowd with phones"})
    new = with_prompt.scenes_of("seg_a")[1]
    assert new.visual.reuse_of is None and new.visual.prompt == "a crowd with phones"
    assert new.visual.language == "key_art" and new.visual.shot != "wide"  # dirigida, com outro plano

    card = _apply(doc, {"op": "split_scene", "scene_id": "scn_stat"}).scenes_of("seg_a")
    assert [s.kind for s in card] == ["image", "stat", "text"]  # cartão sem imagem: a segunda parte mostra a fala
    assert card[2].visual.type == "none" and card[2].visual.reuse_of is None and card[2].card is None

    reused_bg = doc.model_copy(deep=True)
    reused_bg.scene("scn_stat").visual = Visual(reuse_of="scn_root")
    parts = _apply(reused_bg, {"op": "split_scene", "scene_id": "scn_stat"}).scenes_of("seg_a")
    assert parts[2].kind == "image" and parts[2].visual.reuse_of == "scn_root"

    # dividir uma cena que reaproveita aponta direto para a raiz
    chain = _apply(doc, {"op": "split_scene", "scene_id": "scn_r3"}).scenes_of("seg_b")
    assert chain[-1].visual.reuse_of == "scn_root"


def test_delete_segment_promotes_reuse_root():
    doc = _doc()
    out = _apply(doc, {"op": "delete_segment", "segment_id": "seg_a"})
    r1, r2, r3 = out.scenes_of("seg_b")
    # a primeira que reaproveitava herda o Visual da raiz (mesma chave de cache: nenhuma imagem refeita)
    assert r1.visual == DIRECTED and r1.focus == "top"
    assert r2.visual.reuse_of == "scn_r1" and r3.visual.reuse_of == "scn_r1"
    assert all(out.reuse_root(s) is r1 for s in (r1, r2, r3))


def test_merge_promotes_reuse_root_of_the_removed_scene():
    seg = Segment(id="seg_a", text={"pt-BR": "a b c d"})
    doc = ProjectDoc(id="prj_m", title="m", style_id="misterios-short", segments=[seg], scenes=[
        Scene(id="scn_1", segment_id="seg_a", part=0, visual=Visual(prompt="um farol")),
        Scene(id="scn_2", segment_id="seg_a", part=1, visual=Visual(prompt="um barco", seed=3)),
        Scene(id="scn_3", segment_id="seg_a", part=2, visual=Visual(reuse_of="scn_2")),
        Scene(id="scn_4", segment_id="seg_a", part=3, visual=Visual(reuse_of="scn_sumida")),
    ])
    out = _apply(doc, {"op": "merge_scene_with_next", "scene_id": "scn_1"})
    assert [s.id for s in out.scenes_of("seg_a")] == ["scn_1", "scn_3", "scn_4"]
    assert out.scene("scn_3").visual == Visual(prompt="um barco", seed=3)
    assert out.scene("scn_4").visual.reuse_of == "scn_sumida"  # referência solta continua solta


def test_regenerate_and_new_prompt_give_reused_scene_its_own_image():
    doc = _doc()
    regen = _apply(doc, {"op": "regenerate_scene", "scene_id": "scn_r2"}).scene("scn_r2")
    assert regen.visual.reuse_of is None and regen.visual.prompt == DIRECTED.prompt
    assert regen.visual.variation == DIRECTED.variation + 1 and regen.visual.language == "key_art"

    prompt = _apply(doc, {"op": "update_scene_prompt", "scene_id": "scn_r3", "prompt": "a sports car"}).scene("scn_r3")
    assert prompt.visual.reuse_of is None and prompt.visual.prompt == "a sports car"
    assert prompt.visual.language == "key_art"

    loose = doc.model_copy(deep=True)
    loose.scene("scn_r2").visual = Visual(reuse_of="scn_sumida")
    with pytest.raises(OpError, match="não tem imagem"):
        _apply(loose, {"op": "regenerate_scene", "scene_id": "scn_r2"})


def test_set_style_id_validates_and_clears_art():
    doc = _doc(style_overrides={"voice": {"speed": 1.1}})
    out = _apply(doc, {"op": "set_style_id", "style_id": "games-cultura-pop-short"})
    assert out.style_id == "games-cultura-pop-short"
    assert out.art is None and out.storyboard_approved is False
    assert out.style_overrides == {"voice": {"speed": 1.1}}  # os ajustes (voz escolhida) ficam
    assert [s.model_dump() for s in out.scenes] == [s.model_dump() for s in doc.scenes]

    same = _apply(doc, {"op": "set_style_id", "style_id": "misterios-short"})
    assert same.art == doc.art and same.storyboard_approved is True  # o mesmo estilo não muda nada

    with pytest.raises(OpError, match="estilo não encontrado: nao-existe"):
        _apply(doc, {"op": "set_style_id", "style_id": "nao-existe"})
    bad = _doc(style_overrides={"edit": {"captions": {"size": 9999}}})
    with pytest.raises(OpError, match="os ajustes do projeto não valem no estilo games-cultura-pop-short"):
        _apply(bad, {"op": "set_style_id", "style_id": "games-cultura-pop-short"})


def test_set_style_id_reaches_the_edit_agent():
    flat = {key: None for key in FLAT_OP_FIELDS}
    flat.update({"op": "set_style_id", "reason": "pedido", "style_id": "games-cultura-pop-short"})
    ops = flat_to_ops([flat])
    assert ops[0].op == "set_style_id" and ops[0].style_id == "games-cultura-pop-short"
    # toda operação tem nome no esquema do agente
    assert set(_OP_CLASSES) == set(EDIT_OPS)


def test_zoom_to_is_a_valid_scene_motion():
    out = _apply(_doc(), {"op": "set_scene_motion", "scene_id": "scn_r1", "motion": "zoom_to"})
    assert out.scene("scn_r1").motion == "zoom_to"


def test_reused_scene_with_its_own_image_gets_its_own_seed():
    doc = _doc()
    once = _apply(doc, {"op": "regenerate_scene", "scene_id": "scn_r2"})
    both = _apply(once, {"op": "regenerate_scene", "scene_id": "scn_root"})
    r2, root = both.scene("scn_r2").visual, both.scene("scn_root").visual
    assert r2.variation == root.variation == 1
    assert r2.seed != root.seed  # "Outra imagem" na cena e depois na raiz não dá a mesma imagem nas duas


def test_card_without_background_turned_image_asks_for_an_image():
    doc = _doc()
    image = _apply(doc, {"op": "set_scene_kind", "scene_id": "scn_stat", "kind": "image"}).scene("scn_stat")
    assert image.kind == "image" and image.visual.type == "ai_image" and image.motion is None
    assert image.visual.prompt == "várias palavras aqui."  # a fala da cena (span 5..8)
    assert image.visual.language == "key_art" and image.visual.seed
    # continuar como cartão não muda o fundo
    text = _apply(doc, {"op": "set_scene_kind", "scene_id": "scn_stat", "kind": "text"}).scene("scn_stat")
    assert text.visual.type == "none"
    prompt = _apply(doc, {"op": "update_scene_prompt", "scene_id": "scn_stat", "prompt": "a crowd"}).scene("scn_stat")
    assert prompt.visual.type == "ai_image" and prompt.visual.prompt == "a crowd"
