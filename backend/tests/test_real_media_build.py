"""Build com mídia real: a cena usa a mídia enquadrada (cache "media") e não gera imagem de IA."""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image

from studio.core.project import service
from studio.core.project.document import MediaRef, Scene, Segment, Visual
from studio.media.real_media import EDITORIAL_WARNING, store_image
from studio.providers import registry
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline.build import Builder, plan, resolve_reused_images, scene_needs_image


def jpeg(width: int, height: int, color=(30, 90, 160)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "JPEG")
    return buf.getvalue()


@pytest.fixture
def no_ai_images(monkeypatch):
    """Qualquer pedido de imagem a um provedor falha o teste (a narração da prévia continua funcionando)."""
    original = registry.run_chain
    calls: list[str] = []

    def guarded(kind, *args, **kwargs):
        calls.append(kind)
        if kind == "image":
            raise AssertionError("a cena com mídia real não pode gerar imagem de IA")
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(registry, "run_chain", guarded)
    return calls


def project(media: dict[str, MediaRef | None], *, editorial_first: bool = False) -> str:
    doc = service.create_project(title="Build com mídia", style_id="misterios-short")
    seg = Segment(id="seg_1", text={"pt-BR": "Miami vista do mar. A cidade brilha à noite. E depois amanhece."})
    scenes = [
        Scene(id="scn_a", segment_id="seg_1", part=0, parts=3, visual=Visual(prompt="miami from the sea", seed=1),
              span=4),
        Scene(id="scn_b", segment_id="seg_1", part=1, parts=3, visual=Visual(reuse_of="scn_a"), span=5),
        Scene(id="scn_c", segment_id="seg_1", part=2, parts=3, visual=Visual(prompt="sunrise", seed=2), span=3),
    ]
    for scene in scenes:
        scene.visual.media = media.get(scene.id)
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes})
    service.commit(doc.id, doc, "cenas")
    return doc.id


def photo(pid: str, width: int = 1920, height: int = 1080, **extra) -> MediaRef:
    stored = store_image(jpeg(width, height), pid)
    fields = {"kind": "photo", "source": "commons", "asset": stored.rel, "width": stored.width,
              "height": stored.height, "credit": "Fulano / Wikimedia Commons · CC BY-SA 4.0",
              "candidate_id": f"commons:File:{width}.jpg"}
    return MediaRef(**{**fields, **extra})


def attach(pid: str, scene_id: str, media: MediaRef | None) -> None:
    doc = service.get_doc(pid)
    doc.scene(scene_id).visual.media = media
    service.commit(pid, doc, "mídia")


def test_media_scene_needs_no_image_and_is_framed_once(no_ai_images, isolated_settings):
    pid = project({})
    media = photo(pid)  # 16:9 num vídeo 9:16: imagem inteira com fundo desfocado
    attach(pid, "scn_a", media)
    builder = Builder(pid, mode="preview")
    scene = builder.doc.scene("scn_a")
    assert not scene_needs_image(scene)
    rel = builder.image(scene)
    assert rel and rel.startswith("cache/media/") and builder.stats.generated == {"media": 1}
    with Image.open(isolated_settings.data_dir / rel) as im:
        assert im.size == (builder.style.format.width, builder.style.format.height)
    again = Builder(pid, mode="preview")
    assert again.image(again.doc.scene("scn_a")) == rel and again.stats.reused == {"media": 1}

    attach(pid, "scn_a", media.model_copy(update={"focus": "left", "fit": "cover"}))
    other = Builder(pid, mode="preview")
    assert other.image(other.doc.scene("scn_a")) != rel  # foco e enquadramento: outro recorte, sem baixar nada
    assert "image" not in no_ai_images


def test_reuse_gets_the_media_image(no_ai_images):
    pid = project({})
    attach(pid, "scn_a", photo(pid))
    builder = Builder(pid, mode="preview")
    images = {s.id: builder.image(s) for s in builder.doc.scenes if s.id != "scn_c"}
    assert images["scn_b"] is None
    resolve_reused_images(builder.doc, images)
    assert images["scn_b"] == images["scn_a"] and images["scn_a"].startswith("cache/media/")


def test_plan_counts_media_scenes_only_when_there_are_any(isolated_settings):
    pid = project({})
    plain = plan(pid, mode="preview")
    assert "media_scenes" not in plain and "media_missing" not in plain
    assert set(plain["images_missing"]) == {"scn_a", "scn_c"}
    attach(pid, "scn_a", photo(pid))
    with_media = plan(pid, mode="preview")
    assert with_media["images_missing"] == ["scn_c"] and with_media["media_scenes"] == ["scn_a"]
    assert "media_missing" not in with_media
    (isolated_settings.data_dir / service.get_doc(pid).scene("scn_a").visual.media.asset).unlink()
    assert plan(pid, mode="preview")["media_missing"] == ["scn_a"]


def test_missing_media_file_warns_and_never_falls_back_to_ai(no_ai_images, isolated_settings):
    pid = project({})
    media = photo(pid)
    attach(pid, "scn_a", media)
    (isolated_settings.data_dir / media.asset).unlink()
    builder = Builder(pid, mode="preview")
    assert builder.image(builder.doc.scene("scn_a")) is None
    assert builder.warnings == ["A mídia real da Cena 1 sumiu da pasta de dados; a cena ficou sem imagem. Anexe de "
                                "novo ou tire a mídia."]
    news = MediaRef(kind="news", outlet="IGN")
    attach(pid, "scn_a", news)
    builder = Builder(pid, mode="preview")
    assert builder.image(builder.doc.scene("scn_a")) is None and builder.warnings == []  # notícia sem imagem


def test_default_thumbnail_skips_third_party_material():
    pid = project({})
    attach(pid, "scn_a", photo(pid, editorial=True, kind="official"))
    builder = Builder(pid, mode="preview")
    images = {"scn_a": "cache/media/a.jpg", "scn_b": "cache/media/a.jpg", "scn_c": "cache/image/c.jpg"}
    assert builder.default_thumbnail_scene(images) == "scn_c"
    assert builder.default_thumbnail_scene({"scn_a": "cache/media/a.jpg"}) == "scn_a"  # só tem ela
    attach(pid, "scn_a", photo(pid, width=1921))
    assert Builder(pid, mode="preview").default_thumbnail_scene(images) == "scn_a"


def test_same_media_on_two_scenes_is_framed_once_in_parallel(no_ai_images, isolated_settings, monkeypatch):
    # As cenas vão em paralelo: sem a trava, as duas perdiam o cache, recortavam juntas e a segunda gravação falhava
    # no banco (UNIQUE), derrubando a prévia inteira.
    import time

    from studio.workers.pipeline import build as build_mod

    pid = project({})
    media = photo(pid)
    attach(pid, "scn_a", media)
    attach(pid, "scn_c", media)
    real = build_mod.fit_media
    calls: list[int] = []

    def slow_fit(*args, **kwargs):
        calls.append(1)
        time.sleep(0.2)
        return real(*args, **kwargs)

    monkeypatch.setattr(build_mod, "fit_media", slow_fit)
    job = get_runner().submit("preview", {}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    assert len(calls) == 1
    timeline = json.loads((isolated_settings.data_dir / final["result"]["langs"]["pt-BR"]["timeline"])
                          .read_text(encoding="utf-8"))
    src = {s["id"]: s["src"] for s in timeline["scenes"]}
    assert src["scn_a"].startswith("cache/media/") and src["scn_c"] == src["scn_a"]


def test_preview_with_media_has_credit_and_content_id_warning(no_ai_images, isolated_settings):
    pid = project({})
    doc = service.get_doc(pid)
    doc.scenes = [s for s in doc.scenes if s.id != "scn_c"]
    doc.scenes[0].span, doc.scenes[1].span = 6, 6
    doc.scenes[0].parts = doc.scenes[1].parts = 2
    doc.scenes[0].visual.media = photo(pid, kind="official", editorial=True).model_copy(update={"credit": "Rockstar Games"})
    service.commit(pid, doc, "mídia oficial")
    job = get_runner().submit("preview", {}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    result = final["result"]
    assert EDITORIAL_WARNING in result["warnings"] and result["generated"].get("image", 0) == 0
    timeline = json.loads((isolated_settings.data_dir / result["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    first, second = timeline["scenes"]
    assert first["src"].startswith("cache/media/") and second["src"] == first["src"]
    assert first["credit"] == second["credit"] == "Imagem: Rockstar Games"
    assert "image" not in no_ai_images


def test_news_headline_too_long_for_its_scene_warns_in_the_result(no_ai_images, isolated_settings):
    # O aviso da linha do tempo chega ao resultado do job e não fica gravado no timeline.json.
    pid = project({})
    headline = "Rockstar confirma que GTA 6 chega em maio de 2026 com o maior mapa da série"
    attach(pid, "scn_a", photo(pid, kind="news", source="link", editorial=True, outlet="IGN Brasil",
                               title=headline, headline={"pt-BR": headline}, credit="IGN Brasil"))
    attach(pid, "scn_c", photo(pid))  # sem imagem de IA no teste
    job = get_runner().submit("preview", {}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    result = final["result"]
    assert any(w.startswith("Cena 1: a manchete da notícia (") for w in result["warnings"]), result["warnings"]
    timeline = json.loads((isolated_settings.data_dir / result["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    assert "warnings" not in timeline
