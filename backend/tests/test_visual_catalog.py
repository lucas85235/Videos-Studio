"""Base da curadoria visual: catálogo, linguagens dos estilos, documento novo compatível com o antigo."""

from __future__ import annotations

import pytest
import yaml
from pydantic import ValidationError

from studio.config import REPO_ROOT
from studio.core.project.document import CARD_KINDS, ArtDirection, CardItem, ProjectDoc, SceneCard
from studio.core.styles import visual
from studio.core.styles.library import load_presets
from studio.core.styles.schema import CAMERA_MOVES, GRADE_PRESETS, SCENE_KINDS, ArtSpec, EditSpec, StyleSpec

PRESETS_DIR = REPO_ROOT / "presets" / "styles"


def test_every_preset_declares_languages_from_the_catalog():
    files = sorted(PRESETS_DIR.glob("*.yaml"))
    assert len(files) >= 15
    for path in files:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        art = data.get("art") or {}
        langs = art.get("languages")
        assert langs, f"{path.name}: falta art.languages"
        assert len(langs) == len(set(langs)) <= 4, path.name
        for lang in langs:
            assert lang in visual.VISUAL_LANGUAGES, f"{path.name}: linguagem desconhecida {lang}"
            if visual.VISUAL_LANGUAGES[lang].photoreal:
                assert art.get("photoreal"), f"{path.name}: {lang} exige art.photoreal"
    presets = load_presets()
    for style in presets.values():
        # o validador não descartou nada: o que está no YAML é o que vale
        assert style.art.languages
        assert 0.1 <= style.edit.new_image_ratio <= 1.0


def test_games_preset_follows_the_plan():
    style = load_presets()["games-cultura-pop-short"]
    assert style.format.type == "short" and style.format.aspect == "9:16"
    assert style.script.cta == "Siga para mais novidades de games."
    assert "datas e números" in style.script.tone
    assert style.art.languages == ["key_art", "render_3d", "ui_mockup", "editorial"]
    assert "interface de jogo" in style.art.banned and "logotipos" in style.art.banned
    assert "vela" not in style.art.bible and "grão" in style.art.bible  # "sem grão"
    assert (style.edit.shot_seconds.min, style.edit.shot_seconds.max) == (1.8, 3.0)
    effects = {e.type: e for e in style.edit.effects}
    assert effects["grade"].preset == "vivid" and effects["grade"].amount == 0.35
    assert effects["vignette"].amount == 0.12
    assert "dust" not in effects and "grain" not in effects
    assert style.audio.music.mood == "upbeat"
    assert style.tags == ["games", "noticias", "cultura-pop", "shorts"]


def test_art_spec_languages_validation():
    assert ArtSpec().languages == []
    assert ArtSpec(languages=["Key-Art", "render_3d", "key_art"]).languages == ["key_art", "render_3d"]
    with pytest.raises(ValidationError):
        ArtSpec(languages=["aquarela"])
    # sem fotorrealismo a foto é ignorada (um ajuste antigo não derruba o estilo)
    assert ArtSpec(languages=["photo", "editorial"]).languages == ["editorial"]
    assert ArtSpec(languages=["photo", "editorial"], photoreal=True).languages == ["photo", "editorial"]
    assert EditSpec().new_image_ratio == 0.5
    with pytest.raises(ValidationError):
        EditSpec(new_image_ratio=0.05)


def test_catalog_constants():
    assert {"stat", "compare"} <= set(SCENE_KINDS)
    assert "zoom_to" in CAMERA_MOVES
    assert CARD_KINDS <= set(SCENE_KINDS) and "image" not in CARD_KINDS
    assert set(visual.VISUAL_LANGUAGES) == {
        "key_art", "editorial", "render_3d", "flat_vector", "isometric", "ui_mockup", "painting", "archival",
        "collage", "photo",
    }  # fmt: skip
    assert [k for k, v in visual.VISUAL_LANGUAGES.items() if v.photoreal] == ["photo"]
    assert "photo" not in visual.allowed_languages(False) and "photo" in visual.allowed_languages(True)
    assert set(visual.SHOTS) == set(visual.SHOT_ROTATION)
    assert set(visual.COMPOSITIONS) == set(visual.COMPOSITION_ROTATION)
    assert set(visual.FOCUS_POINTS) == set(visual.FOCUS_ROTATION)
    for position in ("top", "center", "bottom"):
        assert set(visual.compositions_for_caption(position)) <= set(visual.COMPOSITIONS)
    for items in (visual.SHOTS, visual.COMPOSITIONS, visual.FOCUS_POINTS):
        assert all(item.label and item.text for item in items.values())
    assert "games" in visual.GENRES and "true_crime" in visual.GENRES
    assert visual.language_prefix("render_3d").startswith("stylized 3D render")
    assert visual.language_prefix("nada") == "" and visual.shot_text(None) == ""
    text = visual.catalog_text(photoreal=False)
    assert "key_art" in text and "photo:" not in text


def test_catalog_helpers_normalize_ai_answers():
    assert visual.pick(" Low-Angle ", visual.SHOTS) == "low_angle"
    assert visual.pick("drone", visual.SHOTS, "wide") == "wide"
    assert visual.pick(None, visual.FOCUS_POINTS) is None
    assert visual.is_genre("true-crime") and not visual.is_genre("novela")
    assert visual.clean_languages(["photo", "Editorial", "xyz", "editorial", "key_art", "render_3d", "isometric"],
                                  photoreal=False) == ["editorial", "key_art", "render_3d"]
    assert visual.validate_languages(["painting", "Painting"]) == ["painting"]
    with pytest.raises(ValueError):
        visual.validate_languages(["oleo"])


def test_rotate_variant_changes_shot_and_language_deterministically():
    langs = ["key_art", "render_3d"]
    assert visual.rotate_variant("wide", "center", "key_art", langs, 0) == ("wide", "center", "key_art")
    one = visual.rotate_variant("wide", "center", "key_art", langs, 1)
    assert one == visual.rotate_variant("wide", "center", "key_art", langs, 1)
    assert one[0] != "wide" and one[1] != "center" and one[2] == "key_art"
    two = visual.rotate_variant("wide", "center", "key_art", langs, 2)
    assert two[2] == "render_3d" and two[0] not in ("wide", one[0])
    # sem plano definido, a variação também escolhe um; linguagem única não muda
    assert visual.rotate_variant(None, None, "painting", ["painting"], 3)[2] == "painting"
    assert visual.rotate_variant(None, None, None, [], 1)[0] in visual.SHOTS


OLD_DOC = {
    "schema_version": 1,
    "id": "prj_antigo",
    "title": "Antigo",
    "style_id": "misterios-short",
    "segments": [{"id": "seg_a", "role": "hook", "text": {"pt-BR": "Olá mundo."}}],
    "scenes": [
        {"id": "scn_a", "segment_id": "seg_a", "kind": "image",
         "visual": {"type": "ai_image", "prompt": "a lighthouse", "query": None, "asset": None, "seed": 3, "variation": 0}},
    ],
}  # fmt: skip


def test_old_document_loads_with_defaults_and_keeps_old_fields():
    doc = ProjectDoc.model_validate(OLD_DOC)
    scene = doc.scenes[0]
    assert doc.art is None and doc.storyboard_approved is False
    assert scene.span is None and scene.card is None and scene.focus is None
    assert scene.visual.language is None and scene.visual.reuse_of is None and scene.visual.entities == []
    assert scene.visual.shot is None and scene.visual.composition is None
    assert scene.visual.prompt == "a lighthouse" and scene.visual.seed == 3
    again = ProjectDoc.model_validate(doc.model_dump(mode="json"))
    assert again == doc
    assert doc.reuse_root(scene) is scene


def test_new_fields_round_trip():
    data = {
        **OLD_DOC,
        "art": {"genre": "games", "languages": ["key_art"], "entities": [{"id": "lucia", "name": "Lucia", "look": "x"}],
                "style_fit": "fraco", "suggested_style": "games-cultura-pop-short", "grade": "vivid"},
        "storyboard_approved": True,
        "scenes": [
            {**OLD_DOC["scenes"][0], "span": 2, "visual": {"prompt": "city", "language": "key_art", "shot": "wide"}},
            {"id": "scn_b", "segment_id": "seg_a", "part": 1, "kind": "stat", "span": 0, "focus": "top_left",
             "visual": {"type": "none", "reuse_of": "scn_a"},
             "card": {"value": {"pt-BR": "475 MI"}, "label": {"pt-BR": "visualizações em 24 h"},
                      "stamp": {"pt-BR": "RECORDE"}, "source": "Rockstar"}},
        ],
    }  # fmt: skip
    doc = ProjectDoc.model_validate(data)
    assert doc.art and doc.art.entity("lucia") and doc.art.entity("nada") is None
    assert doc.art.grade in GRADE_PRESETS
    assert ProjectDoc.model_validate(doc.model_dump(mode="json")) == doc
    with pytest.raises(ValidationError):
        ArtDirection(style_fit="talvez")


def test_scene_card_resolves_by_language_without_empty_keys():
    card = SceneCard(
        value={"pt-BR": "475 MI", "en-US": "475M"},
        label={"pt-BR": "visualizações"},
        items=[
            CardItem(label={"pt-BR": "Trailer 1"}, value={"pt-BR": "90 MI"}),
            CardItem(label={"pt-BR": "Trailer 2"}, value={"pt-BR": "475 MI"}),
            CardItem(),
        ],
        highlight=1,
        headline={"pt-BR": "  "},
        source=" Rockstar ",
    )
    assert card.resolved("pt-BR") == {
        "value": "475 MI",
        "label": "visualizações",
        "items": [{"label": "Trailer 1", "value": "90 MI"}, {"label": "Trailer 2", "value": "475 MI"}],
        "highlight": 1,
        "source": "Rockstar",
    }
    en = card.resolved("en-US", "pt-BR")
    assert en["value"] == "475M" and en["label"] == "visualizações"
    assert card.resolved("en-US") == {"value": "475M", "source": "Rockstar"}
    assert SceneCard(highlight=5, items=[CardItem(label={"pt-BR": "a"})]).resolved("pt-BR") == {
        "items": [{"label": "a", "value": ""}]
    }
    assert SceneCard().resolved("pt-BR") == {}
    assert "90 MI" in card.texts() and "Rockstar" in card.texts()


def test_reuse_root_follows_chain_and_drops_broken_links():
    scenes = [{"id": "scn_0", "segment_id": "seg_a", "visual": {"prompt": "x"}}]
    for i in range(1, 6):
        scenes.append({"id": f"scn_{i}", "segment_id": "seg_a", "part": i, "visual": {"reuse_of": f"scn_{i - 1}"}})
    scenes.append({"id": "scn_solta", "segment_id": "seg_a", "part": 9, "visual": {"reuse_of": "scn_apagada"}})
    scenes.append({"id": "scn_c1", "segment_id": "seg_a", "part": 10, "visual": {"reuse_of": "scn_c2"}})
    scenes.append({"id": "scn_c2", "segment_id": "seg_a", "part": 11, "visual": {"reuse_of": "scn_c1"}})
    doc = ProjectDoc.model_validate({**OLD_DOC, "scenes": scenes})
    root = doc.scene("scn_0")
    assert doc.reuse_root(doc.scene("scn_1")) is root
    assert doc.reuse_root(doc.scene("scn_3")) is root  # 3 saltos
    assert doc.reuse_root(doc.scene("scn_4")) is None  # passou do limite
    assert doc.reuse_root(doc.scene("scn_4"), max_depth=5) is root
    assert doc.reuse_root(doc.scene("scn_solta")) is None
    assert doc.reuse_root(doc.scene("scn_c1")) is None
    assert "reaproveita=scn_0" in doc.outline()


def test_styles_with_languages_survive_overrides():
    base = load_presets()["curiosidades-short"].model_dump(mode="json")
    base["art"]["languages"] = ["photo", "editorial"]
    base["art"]["photoreal"] = True
    style = StyleSpec.model_validate(base)
    assert style.art.languages == ["photo", "editorial"]
    base["art"]["photoreal"] = False
    assert StyleSpec.model_validate(base).art.languages == ["editorial"]
