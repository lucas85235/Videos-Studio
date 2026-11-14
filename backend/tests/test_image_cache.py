"""Cache das imagens: trocar a cadeia de provedores não pode esconder imagens já geradas, e cancelar para de gerar."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from studio.config import get_settings, set_settings
from studio.core.hashing import hash_obj
from studio.core.project import service
from studio.core.project.document import Scene, Segment, Visual
from studio.providers import registry
from studio.providers.base import ImageProvider, ImageResult, ProviderInfo, Usage
from studio.workers.pipeline.build import _LEGACY_IMAGE_CHAINS, BuildCancelled, Builder, image_key, plan


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
