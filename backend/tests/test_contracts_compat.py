"""Contratos da fundação do ADR 0004 (§3.0): esquemas novos, compatibilidade e encaixes vazios."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pytest
import yaml
from compat_fixture import FIXTURE, STYLES, timeline_hashes
from PIL import Image
from pydantic import ValidationError

from studio.config import REPO_ROOT
from studio.core import sources
from studio.core.editing import metrics, profile
from studio.core.project import pool as pool_mod
from studio.core.project import service
from studio.core.project.document import (
    FX_TYPES,
    PROVS,
    BeatMark,
    FxEvent,
    MediaRef,
    ProjectDoc,
    Scene,
    SubShot,
    Visual,
)
from studio.core.styles.schema import (
    CAMERA_MOVES,
    DEFAULT_SOURCE_MIX,
    ArtSpec,
    EditGrammar,
    SourcePolicy,
    StyleSpec,
    grammar_for,
    sources_for,
)
from studio.providers import vision
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import edit_plan, media_pool, mock_content
from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA, STORYBOARD_SOURCES

FIXTURES = Path(__file__).parent / "fixtures"
PRESETS = sorted((REPO_ROOT / "presets" / "styles").glob("*.yaml"))


# --- estilos ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PRESETS, ids=lambda p: p.stem)
def test_presets_validate_with_the_new_policy_and_grammar(path):
    style = StyleSpec.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    # Os presets declaram `sources` e `grammar` por família (perfil-estilo, ADR 0004 §3.3): a mistura soma 1.
    assert sum(style.art.sources.mix.values()) == pytest.approx(1.0)
    grammar, policy = grammar_for(style), sources_for(style)
    if style.format.type == "short":
        # No Short, só o que está no valor padrão troca.
        own, defaults = style.edit.grammar, EditGrammar()
        for key, short_value in (("shot_median_s", 1.6), ("max_still_s", 3.0), ("sfx_per_min", 6.0)):
            expected = short_value if getattr(own, key) == getattr(defaults, key) else getattr(own, key)
            assert getattr(grammar, key) == expected, key
        own_ai = style.art.sources.ai_max_share
        assert policy.ai_max_share == (0.30 if own_ai == SourcePolicy().ai_max_share else own_ai)
    else:
        assert grammar is style.edit.grammar and policy is style.art.sources


def test_there_are_presets():
    assert len(PRESETS) >= 19


def test_saved_reference_style_validates_and_gets_the_default_policy():
    data = json.loads((FIXTURES / "refstyle_legado.json").read_text(encoding="utf-8"))
    assert "sources" not in data["art"] and data["art"]["sources_mix"] == {"ai_image": 1.0}
    style = StyleSpec.model_validate(data)
    assert style.art.sources == SourcePolicy()
    assert style.art.sources.ai_max_share == 0.20 and style.art.sources.hook == ["own", "official", "capture", "free"]
    # o estilo gravado de novo leva a política junto e volta igual
    again = StyleSpec.model_validate(style.model_dump(mode="json"))
    assert again == style


def test_legacy_sources_mix():
    assert ArtSpec.model_validate({"sources_mix": {"ai_image": 1}}).sources == SourcePolicy()
    assert ArtSpec().sources == SourcePolicy()
    copied = ArtSpec.model_validate({"sources_mix": {"stock": 0.5, "ai_image": 0.5, "official": 0.3}}).sources
    assert copied.mix == {**DEFAULT_SOURCE_MIX, "stock": 0.5, "official": 0.3}
    explicit = ArtSpec.model_validate({"sources_mix": {"stock": 1.0}, "sources": {"mix": {"ai": 0.5}}}).sources
    assert explicit.mix == {"ai": 0.5}  # `sources` declarado vale mais que o campo obsoleto
    with pytest.raises(ValidationError, match="nível de fonte desconhecido"):
        ArtSpec.model_validate({"sources": {"mix": {"ai_image": 1.0}}})
    with pytest.raises(ValidationError):
        ArtSpec.model_validate({"sources": {"hook": ["ai", "tiktok"]}})


def test_short_keeps_chosen_values():
    data = yaml.safe_load((REPO_ROOT / "presets" / "styles" / "misterios-short.yaml").read_text(encoding="utf-8"))
    data.setdefault("edit", {})["grammar"] = {"shot_median_s": 2.0, "sfx_per_min": 4.5}
    data.setdefault("art", {})["sources"] = {"ai_max_share": 0.1}
    style = StyleSpec.model_validate(data)
    grammar = grammar_for(style)
    assert (grammar.shot_median_s, grammar.max_still_s, grammar.sfx_per_min) == (2.0, 3.0, 4.5)
    assert sources_for(style).ai_max_share == 0.1


def test_grammar_bounds_and_push_motion():
    assert "push" in CAMERA_MOVES
    assert EditGrammar.model_validate({"intensity": 3, "editor": "off"}).intensity == 3
    for bad in ({"intensity": 4}, {"humor": "pesado"}, {"punch_scale": {"min": 1.3, "max": 1.1}}, {"x": 1},
                {"sfx_palette": {"pop": -1}}):
        with pytest.raises(ValidationError):
            EditGrammar.model_validate(bad)


# --- documento ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("at", ["", "  ", "duas palavras", "#2", "palavra#", "@", "@12345678", "x#1234", "@-1"])
def test_fx_event_rejects_bad_trigger(at):
    with pytest.raises(ValidationError):
        FxEvent(type="punch", at=at)


def test_fx_event_and_shots_accept_the_presenter_syntax():
    for at in ("palavra", "palavra#2", "@1200", "475", "GTA6"):
        assert FxEvent(type="text_pop", at=f" {at} ", params={"text": "UAU", "scale": 1.3}).at == at
    event = FxEvent(type="highlight", at="recorde", params={"rect": [0.1, 0.2, 0.3, 0.1], "on": True})
    assert event.params == {"rect": [0.1, 0.2, 0.3, 0.1], "on": True} and event.origin == "planner"
    with pytest.raises(ValidationError):
        FxEvent(type="explosao", at="x")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        FxEvent(type="punch", at="x", dur_ms=10)
    assert SubShot().at is None and SubShot(at="").at is None and SubShot(at="cidade#3").at == "cidade#3"
    with pytest.raises(ValidationError):
        SubShot(at="duas palavras")
    with pytest.raises(ValidationError):
        SubShot(framing={"scale": 2.5})
    mark = BeatMark(role="joke", emphasis=["Miami", " Miami ", "vice"], gag={"at": "ué", "payoff": "meme"})
    assert mark.emphasis == ["Miami", "vice"] and mark.gag is not None and mark.gag.at == "ué"
    with pytest.raises(ValidationError):
        BeatMark(emphasis=["a", "b", "c", "d"])
    with pytest.raises(ValidationError):
        BeatMark(gag={"at": "duas palavras"})


def test_media_ref_new_fields():
    media = MediaRef(kind="clip", source="youtube", asset="uploads/p/media/a.jpg", video="uploads\\p\\clips\\a.mp4",
                     start_s=12.5, dur_s=3.2, rects=[[0.1, 0.2, 0.5, 0.1]], tier="official", pool_id="p3")
    assert media.video == "uploads/p/clips/a.mp4" and media.mute and not media.hold
    for kind, source in (("capture", "capture"), ("meme", "library")):
        MediaRef(kind=kind, source=source)
    for bad in ({"rects": [[0.9, 0.1, 0.3, 0.1]]}, {"rects": [[0.1, 0.1, 0.1]]}, {"video": "../x.mp4"},
                {"tier": "tiktok"}, {"kind": "gif"}, {"start_s": -1}):
        with pytest.raises(ValidationError):
            MediaRef.model_validate(bad)


def test_old_scene_dump_is_unchanged_but_new_fields_round_trip():
    scene = Scene(segment_id="s", shots=[SubShot(source="pool", pool_id="p1", motion="push")],
                  fx=[FxEvent(type="sfx", at="@300", params={"lib_id": "boom"})], mark=BeatMark(role="reveal"))
    again = Scene.model_validate(json.loads(scene.model_dump_json()))
    assert again == scene


# --- compatibilidade: o projeto antigo gera a MESMA timeline ----------------------------------------------------


def test_old_project_generates_identical_timeline_and_chunk_hashes(isolated_settings):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert not any(k in s for s in data["doc"]["scenes"] for k in ("shots", "fx", "mark"))  # gravado antes da fundação
    for style_id in STYLES:
        assert timeline_hashes(data["doc"], style_id) == data["expected"][style_id], style_id


def test_old_project_with_the_new_fields_empty_keeps_the_hashes(isolated_settings):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc = ProjectDoc.model_validate(data["doc"]).model_dump(mode="json")
    assert all(s["shots"] == [] and s["fx"] == [] and s["mark"] is None for s in doc["scenes"])
    for style_id in STYLES:
        assert timeline_hashes(doc, style_id) == data["expected"][style_id], style_id


# --- níveis de fonte ----------------------------------------------------------------------------------------------


def _scene(kind: str = "image", **visual) -> Scene:
    return Scene(id=f"scn_{kind}", segment_id="seg", kind=kind, visual=Visual(**visual))  # type: ignore[arg-type]


@pytest.mark.parametrize(("visual", "expected"), [
    ({"type": "ai_image", "prompt": "x"}, "ai"),
    ({"type": "ai_video", "prompt": "x"}, "ai"),
    ({"type": "stock", "query": "x"}, "stock"),
    ({"type": "upload", "asset": "uploads/p/a.jpg"}, "own"),
    ({"type": "none"}, "graphic"),
])
def test_prov_of_each_visual_type(visual, expected):
    assert sources.prov_of_scene(_scene(**visual)) == expected


@pytest.mark.parametrize(("media", "expected"), [
    ({"kind": "photo", "source": "commons"}, "free"),
    ({"kind": "photo", "source": "openverse"}, "free"),
    ({"kind": "photo", "source": "pexels"}, "stock"),
    ({"kind": "photo", "source": "upload"}, "own"),
    ({"kind": "photo", "source": "library"}, "own"),
    ({"kind": "news", "source": "link"}, "capture"),
    ({"kind": "capture", "source": "capture"}, "capture"),
    ({"kind": "official", "source": "link"}, "official"),
    ({"kind": "clip", "source": "youtube"}, "official"),
    ({"kind": "meme", "source": "library"}, "meme"),
    ({"kind": "photo", "source": "upload", "tier": "stock"}, "stock"),  # `tier` vale mais
])
def test_prov_of_media(media, expected):
    assert sources.prov_of_scene(_scene(prompt="x", media=MediaRef(**media))) == expected


def test_prov_of_cards_and_reuse():
    assert sources.prov_of_scene(_scene("stat")) == "graphic"  # cartão sem visual
    assert sources.prov_of_scene(_scene("map", type="none")) == "graphic"
    photo = Scene(id="scn_a", segment_id="seg", visual=Visual(prompt="x", media=MediaRef(kind="news", source="link")))
    reuse = Scene(id="scn_b", segment_id="seg", visual=Visual(reuse_of="scn_a"))
    ai = Scene(id="scn_c", segment_id="seg", visual=Visual(prompt="y"))
    card_on_ai = Scene(id="scn_d", segment_id="seg", kind="stat", visual=Visual(reuse_of="scn_c"))
    doc = ProjectDoc(id="prj_x", title="x", style_id="misterios-short", segments=[{"id": "seg"}],
                     scenes=[photo, reuse, ai, card_on_ai])
    assert sources.prov_of_scene(reuse, doc) == "capture"  # mostra a notícia da cena dona
    assert sources.prov_of_scene(reuse) == "ai"  # sem o documento, não segue o reaproveitamento
    assert sources.prov_of_scene(card_on_ai, doc) == "ai"  # cartão sobre a imagem de IA desfocada
    assert sources.TIERS == PROVS and set(sources.FALLBACK) <= set(PROVS)
    assert sources.is_real("official") and not sources.is_real("graphic")


# --- acervo -------------------------------------------------------------------------------------------------------


def test_pool_round_trip(isolated_settings):
    assert pool_mod.load_pool("prj_vazio") == pool_mod.Pool()
    item = pool_mod.PoolItem(id="p12", tier="capture", media="capture", desc="manchete", tags=["vendas"],
                             path="uploads\\prj_a\\captures\\c1.png", rects=[[0.1, 0.1, 0.5, 0.2]], w=1280, h=720,
                             editorial=True)
    pool = pool_mod.Pool(items=[item], gathered_at="2026-11-17T00:00:00Z", warnings=["sem Pexels"])
    path = pool_mod.save_pool("prj_a", pool)
    assert path == isolated_settings.uploads_dir / "prj_a" / "pool.json"
    loaded = pool_mod.load_pool("prj_a")
    assert loaded == pool and loaded.item("p12") == item and loaded.item("p99") is None
    assert loaded.items[0].path == "uploads/prj_a/captures/c1.png"
    path.write_text("{quebrado", encoding="utf-8")
    assert pool_mod.load_pool("prj_a").items == []
    for bad in ({"id": "12", "tier": "ai"}, {"id": "p1", "tier": "tiktok"}, {"id": "p1", "tier": "ai", "path": "../x"}):
        with pytest.raises(ValidationError):
            pool_mod.PoolItem.model_validate(bad)
    with pytest.raises(ValidationError):
        pool_mod.Pool(items=[item, item])
    with pytest.raises(ValueError):
        pool_mod.pool_path("../fora")


# --- perfil e impressão digital -----------------------------------------------------------------------------------


def test_edit_profile_contract():
    assert set(profile.MATERIAL_TO_PROV) | profile.PRESENTER_MATERIALS | {"other"} == set(profile.MATERIALS)
    assert profile.MATERIAL_TO_PROV["official_trailer"] == "official" == profile.MATERIAL_TO_PROV["gameplay"]
    assert profile.MATERIAL_TO_PROV["page_capture"] == "capture" == profile.MATERIAL_TO_PROV["news"]
    assert profile.MATERIAL_TO_PROV["ai_illustration"] == "ai" and profile.MATERIAL_TO_PROV["meme_reaction"] == "meme"
    mix = {"official_trailer": 0.6, "gameplay": 0.1, "meme_reaction": 0.1, "facecam": 0.2}
    assert profile.prov_mix_from_materials(mix) == pytest.approx({"official": 0.875, "meme": 0.125})
    assert profile.presenter_share(mix) == pytest.approx(0.2)
    prof = profile.EditProfile.model_validate({
        "pace": {"median_s": 1.43, "cuts_per_min": 21.0, "micro_per_min": 6.0},
        "materials": {"mix": mix},
        "meta": {"pace.median_s": {"confidence": 0.9, "origin": "medido"}},
        "speech": {"structure": [{"role": "hook", "start_s": 0, "end_s": 30}]},
        "recurring": [{"id": "r1", "kind": "mascot", "count": 7}],
    })
    assert prof.confidence("pace.median_s") == 0.9 and prof.confidence("speech.wpm") == 0.0
    assert profile.EditProfile.model_validate(json.loads(prof.model_dump_json())) == prof
    with pytest.raises(ValidationError):
        profile.MaterialsBlock(mix={"trailer": 1.0})
    with pytest.raises(ValidationError):
        profile.FieldMeta(confidence=1.5)
    fp = profile.EditFingerprint()
    assert set(type(fp).model_fields) >= {"pace", "camera", "materials", "overlays", "audio"}


# --- visão --------------------------------------------------------------------------------------------------------


def _img(path: Path, pixels: np.ndarray) -> Path:
    Image.fromarray(pixels.astype(np.uint8), "RGB").save(path)
    return path


def test_mock_vision_is_deterministic_and_follows_the_schema(tmp_path):
    rng = np.random.default_rng(1)
    flat_gray = _img(tmp_path / "card.png", np.full((90, 160, 3), 40))
    noisy_color = _img(tmp_path / "game.png", rng.integers(0, 256, (90, 160, 3)))
    gray_lines = np.full((90, 160, 3), 235)
    gray_lines[::3] = 20
    page = _img(tmp_path / "page.png", gray_lines)
    smooth = np.zeros((90, 160, 3))
    smooth[..., 0] = np.linspace(150, 255, 160)
    smooth[..., 2] = 40
    painted = _img(tmp_path / "ai.png", smooth)
    sheets = [flat_gray, noisy_color, page, painted]
    first = vision.classify_frames(sheets, vision.SHOT_SCHEMA)
    assert first == vision.MockVision().classify_frames(sheets, vision.SHOT_SCHEMA)
    assert [r["material"] for r in first] == ["text_card", "gameplay", "page_capture", "ai_illustration"]
    assert all(set(r) == set(vision.SHOT_SCHEMA["properties"]) for r in first)
    custom = vision.classify_frames([page], {"properties": {"material": {"type": "string"}, "x": {"type": ["string", "null"]}}})
    assert custom == [{"material": "page_capture", "x": None}]


# --- esquema do storyboard ----------------------------------------------------------------------------------------


def test_storyboard_schema_has_the_flat_edit_fields():
    item = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]
    props = item["properties"]
    assert "pool" in STORYBOARD_SOURCES and props["source"]["enum"] == STORYBOARD_SOURCES
    for name in ("pool_id", "ai_reason", "gag_payoff", "gag_line", "gag_at"):
        assert props[name] == {"type": ["string", "null"]}
    assert props["emphasis"] == {"type": ["array", "null"], "items": {"type": "string"}}
    assert props["serious"] == {"type": "boolean"} and props["role"]["type"] == "string"
    assert "hook" in props["role"]["enum"] and "breath" in props["role"]["enum"]
    assert {"real_kind", "real_query", "real_note"} <= set(props)  # reserva
    assert item["required"] == list(props)
    scene = mock_content._mock_scene("b0")
    assert set(scene) == set(props) and scene["role"] == "breath" and scene["serious"] is False


# --- encaixes vazios e ligação no job ------------------------------------------------------------------------------


def test_empty_hooks_change_nothing(isolated_settings):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc = ProjectDoc.model_validate(data["doc"])
    before = doc.model_dump(mode="json")
    from studio.core.styles.library import get_style

    style = get_style("misterios-short")
    pool = media_pool.gather(doc, style)
    # acervo-real: a mídia anexada à mão vira item do acervo, mas sem `pool_id` pendente nada é anexado nem trocado
    # (só sai o aviso `faltou_real` das cenas de IA antigas, que não declaram motivo).
    assert pool.items and all(i.source_id.startswith("file:") for i in pool.items)
    assert all(w.startswith("faltou_real") for w in media_pool.attach_chosen(doc, pool))
    # o planejador já não é encaixe vazio (editor-planejador): com o editor desligado, nada muda
    off = style.model_copy(update={"edit": style.edit.model_copy(
        update={"grammar": style.edit.grammar.model_copy(update={"editor": "off"})})})
    assert edit_plan.plan(doc, off, pool) is None
    assert doc.model_dump(mode="json") == before
    fp = metrics.fingerprint_from_timeline({"scenes": []}, doc)
    assert fp == profile.EditFingerprint(source="timeline")


def _run_job(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def test_storyboard_job_calls_the_hooks_in_order(isolated_settings, monkeypatch):
    calls: list[str] = []
    real_storyboard = __import__("studio.workers.pipeline.authoring", fromlist=["storyboard"]).storyboard

    def gather(doc, style):
        calls.append("gather")
        return pool_mod.Pool(items=[pool_mod.PoolItem(id="p1", tier="official", desc="trailer")])

    def storyboard(*args, **kwargs):
        calls.append("storyboard")
        assert pool_mod.load_pool(args[0].id).item("p1") is not None  # gravado antes do storyboard
        return real_storyboard(*args, **kwargs)

    def attach(doc, pool):
        calls.append("attach")
        assert pool.item("p1") is not None and doc.scenes
        return ["aviso do anexo"]

    def plan(doc, style, pool):
        calls.append("plan")
        doc.scenes[0].fx = [FxEvent(type="punch", at="@100")]

    from studio.workers.pipeline import authoring

    monkeypatch.setattr(media_pool, "gather", gather)
    monkeypatch.setattr(media_pool, "attach_chosen", attach)
    monkeypatch.setattr(edit_plan, "plan", plan)
    monkeypatch.setattr(authoring, "storyboard", storyboard)
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado da ilha")
    _run_job("script", doc.id)
    result = _run_job("storyboard", doc.id)
    assert calls == ["gather", "storyboard", "attach", "plan"]
    assert "aviso do anexo" in result["warnings"]
    saved = service.get_doc(doc.id)
    assert [e.type for s in saved.scenes for e in s.fx] == ["punch"]  # o que o planejador escreve é gravado


def test_storyboard_job_without_pool_writes_no_pool_file(isolated_settings):
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado da ilha")
    _run_job("script", doc.id)
    result = _run_job("storyboard", doc.id)
    assert "warnings" not in result or all("acervo" not in w for w in result["warnings"])
    assert not (isolated_settings.uploads_dir / doc.id / "pool.json").exists()


def test_failing_gather_becomes_a_warning(isolated_settings, monkeypatch):
    def boom(doc, style):
        raise RuntimeError("sem rede")

    monkeypatch.setattr(media_pool, "gather", boom)
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado da ilha")
    _run_job("script", doc.id)
    result = _run_job("storyboard", doc.id)
    assert any("acervo" in w and "sem rede" in w for w in result["warnings"])
    assert service.get_doc(doc.id).scenes


# --- espelho no renderer ------------------------------------------------------------------------------------------


def _ts_union(source: str, name: str) -> list[str]:
    match = re.search(rf"export type {name} =([^;]+);", source)
    assert match, name
    return re.findall(r"'([a-z_]+)'", match.group(1))


def test_renderer_types_mirror_the_contract():
    source = (REPO_ROOT / "renderer" / "src" / "types.ts").read_text(encoding="utf-8")
    assert tuple(_ts_union(source, "Prov")) == PROVS
    assert tuple(_ts_union(source, "FxType")) == FX_TYPES
    scene_block = source[source.index("export type TimelineScene"):]
    for key in ("prov?: Prov", "clean?: true", "shots?: TimelineShot[]", "fx?: TimelineFx[]"):
        assert key in scene_block
