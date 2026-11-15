"""Cache das imagens: trocar a cadeia de provedores não pode esconder imagens já geradas, e cancelar para de gerar."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from studio.config import get_settings, set_settings
from studio.core.hashing import hash_obj, seed_from
from studio.core.project import service
from studio.core.project.document import ArtDirection, Entity, ProjectDoc, Scene, Segment, Visual
from studio.core.project.ops import apply_ops, parse_ops
from studio.providers import registry
from studio.providers.base import ImageProvider, ImageResult, ProviderInfo, Usage
from studio.workers.pipeline.build import (
    _LEGACY_IMAGE_CHAINS,
    BuildCancelled,
    Builder,
    BuildError,
    compose_directed_prompt,
    compose_image_prompt,
    image_key,
    legacy_directed_prompt,
    plan,
    resolve_reused_images,
    scene_needs_image,
)


class FakeImages(ImageProvider):
    def __init__(self, provider_id: str, commercial: bool = True) -> None:
        self.info = ProviderInfo(id=provider_id, kind="image", name=provider_id, commercial_use=commercial,
                                 deterministic=True)
        self.calls = 0

    def availability(self):
        return True, "ok"

    def generate(self, *, prompt, width, height, seed, out_dir, references=None):
        self.calls += 1
        path = Path(out_dir) / "image.jpg"
        Image.new("RGB", (8, 8)).save(path)
        return ImageResult(path=path, usage=Usage(units=1, unit_name="imagem"), model=self.info.id)


class Cancelled:
    job_id = None

    def progress(self, fraction, message=""):
        pass

    def cancelled(self):
        return True


@pytest.fixture
def images(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    codex = FakeImages("codex_cli")
    test = FakeImages("placeholder", commercial=False)
    monkeypatch.setitem(registry._PROVIDERS, "codex_cli", codex)
    monkeypatch.setitem(registry._PROVIDERS, "placeholder", test)
    return codex, test


def _project():
    doc = service.create_project(title="Imagens", style_id="misterios-short")
    doc = doc.model_copy(update={
        "segments": [Segment(id="seg_1", text={"pt-BR": "Um texto."})],
        "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=Visual(prompt="um farol à noite"))],
        "style_overrides": {"art": {"image_chain": ["codex_cli"]}},
    })
    service.commit(doc.id, doc, "teste")
    return doc


def _save(builder: Builder, key: str, provider: FakeImages) -> None:
    out = builder.store.dir_for("image", key) / "image.jpg"
    Image.new("RGB", (8, 8)).save(out)
    rel = builder.store.rel(out)
    builder.store.save("image", key, {"path": rel, "files": [rel], "provider": provider.info.id,
                                      "commercial": provider.info.commercial_use}, provider.info.id)


def test_image_saved_with_the_old_chain_key_is_reused(images):
    codex, _ = images
    doc = _project()
    builder = Builder(doc.id, mode="preview")
    scene = builder.doc.scenes[0]
    inputs = builder.image_inputs(scene)
    # imagem guardada antes, quando a cadeia ainda tinha os provedores pagos
    _save(builder, hash_obj({**inputs, "chain": _LEGACY_IMAGE_CHAINS[0]}), codex)

    assert plan(doc.id, mode="preview")["images_missing"] == []
    assert builder.image(scene) and codex.calls == 0


def test_image_key_does_not_depend_on_the_chain(images):
    codex, _ = images
    doc = _project()
    builder = Builder(doc.id, mode="preview")
    scene = builder.doc.scenes[0]
    first = builder.image(scene)
    assert codex.calls == 1

    builder.style.art.image_chain = ["codex_cli", "outro_gerador"]
    inputs = builder.image_inputs(scene)
    assert image_key(inputs) == image_key({**inputs, "chain": ["codex_cli"]})
    assert builder.image(scene) == first and codex.calls == 1


def test_test_image_counts_as_missing_when_a_real_generator_is_available(images):
    codex, test = images
    doc = _project()
    builder = Builder(doc.id, mode="preview")
    scene = builder.doc.scenes[0]
    _save(builder, image_key(builder.image_inputs(scene)), test)

    # a prévia vai trocar a imagem de teste pela do Codex: a estimativa tem que contar isso
    assert plan(doc.id, mode="preview")["images_missing"] == [scene.id]
    builder.image(scene)
    assert codex.calls == 1
    assert plan(doc.id, mode="preview")["images_missing"] == []


def test_good_image_wins_over_a_test_image_saved_later(images):
    codex, test = images
    doc = _project()
    builder = Builder(doc.id, mode="preview")
    scene = builder.doc.scenes[0]
    inputs = builder.image_inputs(scene)
    _save(builder, hash_obj({**inputs, "chain": _LEGACY_IMAGE_CHAINS[0]}), codex)
    _save(builder, image_key(inputs), test)  # o Codex falhou numa prévia depois e caiu no placeholder

    _, cached = builder.cached_image(inputs)
    assert cached["provider"] == "codex_cli"


def test_cancelled_build_does_not_start_new_images(images):
    codex, _ = images
    doc = _project()
    builder = Builder(doc.id, mode="preview", sink=Cancelled())
    with pytest.raises(BuildCancelled):
        builder.image(builder.doc.scenes[0])
    assert codex.calls == 0


# --- curadoria visual: prompt dirigido, reaproveitamento e imagem fixada ----------------------------

ART = ArtDirection(
    genre="games",
    world="sunny neon coastal metropolis, 2020s",
    treatment="saturated sunset light, clean contrast",
    palette=["hot pink", "teal", "sunset orange"],
    languages=["key_art", "render_3d"],
    entities=[Entity(id="lucia", name="Lucia", look="original woman, 30s, dark curly hair, denim jacket")],
    avoid=["candles", "hourglass"],
)


def _directed(**update) -> Visual:
    data = {"prompt": "a woman walking down a neon street at dusk", "language": "key_art", "shot": "low_angle",
            "composition": "left_third", "entities": ["lucia"], "seed": 11}
    return Visual(**{**data, **update})


def _builder_for(doc_update: dict) -> Builder:
    doc = service.create_project(title="Dirigida", style_id="misterios-short")
    doc = doc.model_copy(update={"segments": [Segment(id="seg_1", text={"pt-BR": "Um texto."})],
                                 "style_overrides": {"art": {"image_chain": ["codex_cli"]}}, **doc_update})
    service.commit(doc.id, doc, "teste")
    return Builder(doc.id, mode="preview")


def test_legacy_scene_keeps_prompt_and_cache_key(images):
    # Documento sem direção de arte (e cena dirigida sem direção, e cena sem linguagem num documento com direção):
    # o prompt e a chave de sempre.
    cases = [
        ({}, Visual(prompt="um farol à noite", seed=5)),
        ({}, _directed()),
        ({"art": ART}, Visual(prompt="um farol à noite", seed=5)),
    ]
    for doc_update, visual in cases:
        builder = _builder_for({**doc_update, "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=visual)]})
        scene = builder.doc.scenes[0]
        style = builder.style
        expected = {
            "prompt": compose_image_prompt(style, visual.prompt),
            "width": style.format.width,
            "height": style.format.height,
            "seed": seed_from(visual.seed, visual.variation),
            "variation": visual.variation,
            "type": visual.type,
            "chain": registry.resolve_chain("image", style.art.image_chain),
        }
        assert builder.image_inputs(scene) == expected
        assert image_key(builder.image_inputs(scene)) == image_key(expected)
        # a bíblia do estilo mudou (sem "luz de vela"), mas a cena antiga segue com o texto de antes: mesma chave
        assert expected["prompt"].startswith(
            f"{visual.prompt}. Estilo visual: pintura a óleo sombria e cinematográfica, paleta azul petróleo e dourado "
            "envelhecido, luz de vela, contraste dramático, grão de tela. Sem nenhum texto")
        assert "luz de vela" not in style.art.bible


def test_legacy_bibles_cover_the_presets_that_changed():
    from studio.core.styles.library import load_presets
    from studio.workers.pipeline.build import _LEGACY_BIBLES

    presets = load_presets()
    changed = {"games-cultura-pop-short", "estoico-short", "historia-documental", "biografias", "misterios-short",
               "terror-analog", "true-crime"}
    for style_id in changed:
        assert presets[style_id].art.bible in _LEGACY_BIBLES, style_id
    # nenhuma bíblia fixa hora do dia, céu ou luz de uma fonte só
    for style_id, style in presets.items():
        for word in ("pôr do sol", "fim de tarde", "amanhecer", "luz do dia", "noturna", "luz de vela", "céu aberto",
                     "luz de luminária", "de janela"):
            assert word not in style.art.bible, (style_id, word)


def test_directed_prompt_starts_with_shot_and_has_no_bible(images):
    builder = _builder_for({"art": ART, "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=_directed())]})
    style = builder.style
    prompt = builder.image_inputs(builder.doc.scenes[0])["prompt"]
    assert prompt == compose_directed_prompt(style, ART, _directed())
    assert prompt.startswith("Low-angle shot looking up, subject on the left third. illustrated key-art poster")
    assert "a woman walking down a neon street at dusk" in prompt
    assert "dark curly hair" in prompt and "Lucia" not in prompt  # só a aparência, nunca o nome
    # a cena diz a própria hora ("at dusk"): a hora, o clima e a luz da direção saem; a paleta vira cor de destaque
    assert "World: neon coastal metropolis, 2020s" in prompt and "Accent colors: hot pink, teal, orange" in prompt
    assert "Treatment: clean contrast" in prompt and "sunset" not in prompt and "sunny" not in prompt
    assert "Palette:" not in prompt and "Original designs only" in prompt
    avoid = prompt.split("Avoid: ", 1)[1]
    assert all(term in avoid for term in (*style.art.banned, "candles", "hourglass"))
    assert style.art.bible not in prompt and "Estilo visual" not in prompt
    # cena sem hora nem luz: a direção entra inteira
    plain = compose_directed_prompt(style, ART, _directed(prompt="a woman walking down a palm-lined street"))
    assert "World: sunny neon coastal metropolis, 2020s" in plain
    assert "Accent colors: hot pink, teal, sunset orange" in plain and "Treatment: saturated sunset light" in plain
    # direção definida pelo usuário fica como ele escreveu
    user = compose_directed_prompt(style, ART.model_copy(update={"source": "user"}), _directed())
    assert "World: sunny neon coastal metropolis" in user and "Treatment: saturated sunset light" in user
    # partes vazias somem (sem plano conhecido, sem mundo nem paleta)
    bare = compose_directed_prompt(style, ArtDirection(), Visual(prompt="a lighthouse", language="editorial",
                                                                 shot="voo"))
    assert bare.startswith("modern editorial illustration, clean shapes. a lighthouse. Original designs only")


def test_regenerate_rotates_shot_on_directed_scene(images):
    builder = _builder_for({"art": ART, "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=_directed())]})
    doc = builder.doc
    first = builder.image_inputs(doc.scenes[0])
    once = apply_ops(doc, parse_ops([{"op": "regenerate_scene", "scene_id": "scn_1"}]))
    builder.doc = once
    second = builder.image_inputs(once.scenes[0])
    assert once.scenes[0].visual.variation == 1
    assert second["prompt"].startswith("Medium shot, subject on the right third. illustrated key-art poster")
    assert image_key(second) != image_key(first)
    twice = apply_ops(once, parse_ops([{"op": "regenerate_scene", "scene_id": "scn_1"}]))
    builder.doc = twice
    third = builder.image_inputs(twice.scenes[0])["prompt"]
    assert "stylized 3D render" in third and "key-art" not in third  # da 2ª em diante alterna a linguagem


def test_pinned_asset_used_and_skipped_by_plan(images):
    codex, _ = images
    pinned = Visual(prompt="um farol", asset="uploads/fixa.jpg")
    builder = _builder_for({"scenes": [
        Scene(id="scn_1", segment_id="seg_1", part=0, visual=pinned),
        Scene(id="scn_2", segment_id="seg_1", part=1, visual=Visual(prompt="um barco")),
    ]})
    assert builder.image(builder.doc.scene("scn_1")) == "uploads/fixa.jpg"
    assert codex.calls == 0
    assert plan(builder.project_id, mode="preview")["images_missing"] == ["scn_2"]


def test_reuse_scene_needs_no_image_and_inherits_src():
    scenes = [
        Scene(id="scn_a", segment_id="seg_1", part=0, visual=Visual(prompt="um farol à noite")),
        Scene(id="scn_b", segment_id="seg_1", part=1, visual=Visual(reuse_of="scn_a"), focus="top"),
        Scene(id="scn_c", segment_id="seg_1", part=2, kind="stat", visual=Visual(reuse_of="scn_b")),  # cadeia
        Scene(id="scn_d", segment_id="seg_1", part=3, visual=Visual(reuse_of="scn_apagada")),  # referência solta
    ]
    doc = service.create_project(title="Reaproveita", style_id="misterios-short")
    doc = doc.model_copy(update={
        "segments": [Segment(id="seg_1", text={"pt-BR": "Um farol antigo na costa. Ele ainda acende toda noite. "
                                                         "Ninguém sabe quem cuida dele hoje."})],
        "scenes": scenes,
    })
    service.commit(doc.id, doc, "teste")
    assert [scene_needs_image(s) for s in scenes] == [True, False, False, False]
    assert plan(doc.id, mode="preview")["images_missing"] == ["scn_a"]

    result = Builder(doc.id, mode="preview").run()
    assert result["generated"].get("image") == 1
    timeline = json.loads((get_settings().data_dir / result["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    src = {s["id"]: s["src"] for s in timeline["scenes"]}
    assert src["scn_a"] and src["scn_b"] == src["scn_a"] and src["scn_c"] == src["scn_a"]
    assert src["scn_d"] is None
    assert {s["id"]: s.get("focus") for s in timeline["scenes"]}["scn_b"] == "top"


def test_resolve_reused_images_keeps_pinned_asset():
    doc = ProjectDoc(id="prj_x", title="x", style_id="misterios-short", segments=[Segment(id="seg_1")], scenes=[
        Scene(id="scn_a", segment_id="seg_1", part=0, visual=Visual(prompt="a")),
        Scene(id="scn_b", segment_id="seg_1", part=1, visual=Visual(reuse_of="scn_a", asset="uploads/b.jpg")),
        Scene(id="scn_c", segment_id="seg_1", part=2, visual=Visual(reuse_of="scn_c")),  # ciclo
    ])
    images = {"scn_a": "cache/a.jpg", "scn_b": "uploads/b.jpg", "scn_c": None}
    resolve_reused_images(doc, images)
    assert images == {"scn_a": "cache/a.jpg", "scn_b": "uploads/b.jpg", "scn_c": None}


def test_directed_scene_made_before_accent_colors_keeps_its_image(images):
    # Imagem gerada com o prompt dirigido de antes ("Palette:", luz inteira): achada no cache, nada é gerado de novo.
    codex, _ = images
    scenes = [Scene(id="scn_1", segment_id="seg_1", part=0, visual=_directed()),
              Scene(id="scn_2", segment_id="seg_1", part=1, visual=_directed(prompt="a woman on a palm-lined street"))]
    builder = _builder_for({"art": ART, "scenes": scenes})
    for scene in builder.doc.scenes:
        inputs = builder.image_inputs(scene)
        old = {**inputs, "prompt": legacy_directed_prompt(builder.style, ART, scene.visual)}
        assert "Palette: hot pink, teal, sunset orange" in old["prompt"] and old["prompt"] != inputs["prompt"]
        _save(builder, image_key(old), codex)
    assert plan(builder.project_id, mode="preview")["images_missing"] == []
    assert all(builder.image(s) for s in builder.doc.scenes) and codex.calls == 0
    # "Outra imagem" gera com o prompt novo
    builder.doc = apply_ops(builder.doc, parse_ops([{"op": "regenerate_scene", "scene_id": "scn_1"}]))
    assert "Accent colors" in builder.image_inputs(builder.doc.scene("scn_1"))["prompt"]
    assert plan(builder.project_id, mode="preview", doc=builder.doc)["images_missing"] == ["scn_1"]


def test_legacy_directed_prompt_matches_the_old_format():
    style = _builder_for({"art": ART, "scenes": []}).style
    old = legacy_directed_prompt(style, ART, _directed())
    assert old.startswith("Low-angle shot looking up, subject on the left third. illustrated key-art poster")
    assert ("World: sunny neon coastal metropolis, 2020s. Palette: hot pink, teal, sunset orange. "
            "Treatment: saturated sunset light, clean contrast. Original designs only") in old
    # sem paleta e sem luz da cena, o prompt de antes e o de agora são iguais: nada a procurar a mais
    bare = ArtDirection(world="a port city", treatment="clean contrast")
    plain = _directed(prompt="a woman on a palm-lined street")
    assert legacy_directed_prompt(style, bare, plain) == compose_directed_prompt(style, bare, plain)


def test_directed_scenes_without_direction_are_not_generated(images):
    # O estilo foi trocado (a direção sumiu) e as cenas não foram refeitas: nada de imagem com o prompt antigo.
    codex, _ = images
    builder = _builder_for({"scenes": [
        Scene(id="scn_1", segment_id="seg_1", part=0, visual=_directed()),
        Scene(id="scn_2", segment_id="seg_1", part=1, visual=Visual(prompt="um barco")),
    ]})
    estimate = plan(builder.project_id, mode="preview")
    assert estimate["direction_missing"] == ["scn_1"] and estimate["images_missing"] == ["scn_2"]
    with pytest.raises(BuildError, match="refaça as cenas"):
        builder.run()
    assert codex.calls == 0
    directed = _builder_for({"art": ART, "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=_directed())]})
    assert plan(directed.project_id, mode="preview")["direction_missing"] == []
