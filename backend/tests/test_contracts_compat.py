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
    AUTO_CAMERA_MOVES,
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
from studio.workers.pipeline import authoring, edit_plan, media_pool, mock_content, typographic
from studio.workers.pipeline.schemas import (
    STORYBOARD_EDIT_DEFAULTS,
    STORYBOARD_EDIT_FIELDS,
    STORYBOARD_SCHEMA,
    STORYBOARD_SOURCES,
)
from studio.workers.pipeline.storyboard_check import AVISO, Issue, review_counts

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
    assert SourcePolicy().mix == DEFAULT_SOURCE_MIX  # o padrão já soma 1: fica exatamente igual
    copied = ArtSpec.model_validate({"sources_mix": {"stock": 0.5, "ai_image": 0.5, "official": 0.3}}).sources
    weights = {**DEFAULT_SOURCE_MIX, "stock": 0.5, "official": 0.3}  # soma 1,45: lido como pesos e normalizado
    assert copied.mix == pytest.approx({k: v / 1.45 for k, v in weights.items()}, abs=1e-6)
    assert sum(copied.mix.values()) == pytest.approx(1.0, abs=1e-5)
    only_official = ArtSpec.model_validate({"sources_mix": {"official": 1.0}}).sources
    assert sum(only_official.mix.values()) == pytest.approx(1.0, abs=1e-5)
    assert only_official.share("official") == pytest.approx(1.0 / 1.75, abs=1e-6)
    explicit = ArtSpec.model_validate({"sources_mix": {"stock": 1.0}, "sources": {"mix": {"ai": 0.5}}}).sources
    assert explicit.mix == {"ai": 1.0}  # `sources` declarado vale mais que o campo obsoleto
    assert explicit.share("ai") == 1.0 and explicit.share("stock") == 0.0  # nível ausente vale 0
    halves = SourcePolicy(mix={"stock": 1, "ai": 1})
    assert halves.mix == {"stock": 0.5, "ai": 0.5}
    assert SourcePolicy.model_validate(halves.model_dump()) == halves  # idempotente
    with pytest.raises(ValidationError, match="maior que zero"):
        SourcePolicy(mix={"ai": 0.0})
    with pytest.raises(ValidationError, match="maior que zero"):
        SourcePolicy(mix={})
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
    assert "push" in CAMERA_MOVES  # escolha manual
    # O `renderer-fx` desenha o push (aproximação reta): entra no automático; o parallax continua só manual.
    assert "push" in AUTO_CAMERA_MOVES and "parallax" not in AUTO_CAMERA_MOVES
    assert "push" in authoring._MOTIONS
    # Padrão conservador: nada de humor nem meme por padrão (true crime, mistério); o preset ou o perfil liga.
    assert (EditGrammar().humor, EditGrammar().meme_per_min) == ("none", 0.0)
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
    # O LLM pode mandar mais de 3 (a saída estrita não aceita maxItems): repetidas e vazias saem ANTES do corte.
    assert BeatMark(emphasis=["a", "a", "b", "c"]).emphasis == ["a", "b", "c"]
    assert BeatMark(emphasis=["a", " ", "b", "c", "d", "e"]).emphasis == ["a", "b", "c"]
    assert BeatMark.model_validate({"emphasis": None}).emphasis == []
    with pytest.raises(ValidationError):
        BeatMark(emphasis=["a", 3])  # type: ignore[list-item]
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


@pytest.mark.parametrize(("media", "editorial"), [
    ({"kind": "clip", "source": "youtube"}, True),  # trecho de trailer/gameplay oficial
    ({"kind": "clip", "source": "link"}, True),
    ({"kind": "official", "source": "link"}, True),
    ({"kind": "photo", "source": "youtube"}, True),
    ({"kind": "photo", "source": "commons", "tier": "official"}, True),
    # Arquivo do próprio usuário: ele pode declarar que tem os direitos (`media_from_upload(editorial=False)`).
    ({"kind": "official", "source": "upload"}, False),
    ({"kind": "photo", "source": "upload", "tier": "official"}, False),
    ({"kind": "photo", "source": "link"}, True),  # link sem licença livre: terceiro com direitos
    ({"kind": "clip", "source": "pexels"}, False),  # vídeo de banco: licença do Pexels
    ({"kind": "photo", "source": "link", "license": "CC BY 4.0"}, False),
    ({"kind": "photo", "source": "commons", "license": "CC BY-SA 4.0"}, False),
    ({"kind": "photo", "source": "upload"}, False),
    ({"kind": "meme", "source": "library"}, False),
])
def test_third_party_media_is_always_editorial(media, editorial):
    ref = MediaRef(**media, editorial=False)
    assert ref.editorial is editorial
    assert MediaRef(**media, editorial=True).editorial  # marcar à mão continua valendo


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
    ({"kind": "clip", "source": "pexels"}, "stock"),
    ({"kind": "meme", "source": "library"}, "meme"),
    ({"kind": "photo", "source": "link"}, "official"),  # sem licença declarada: terceiro com direitos, não "livre"
    ({"kind": "photo", "source": "link", "license": "CC BY-SA 4.0"}, "free"),
    ({"kind": "photo", "source": "link", "license": "Public domain"}, "free"),
    ({"kind": "photo", "source": "link", "license": "Todos os direitos reservados"}, "official"),
    ({"kind": "photo", "source": "upload", "tier": "stock"}, "stock"),  # `tier` vale mais
])
def test_prov_of_media(media, expected):
    assert sources.prov_of_scene(_scene(prompt="x", media=MediaRef(**media))) == expected


@pytest.mark.parametrize(("name", "free"), [
    ("CC0", True), ("CC BY 4.0", True), ("cc-by-sa", True), ("Creative Commons", True), ("Public domain", True),
    ("Domínio público", True), ("Pexels License", True), ("", False), ("Rockstar Games", False), ("ccTLD", False),
])
def test_free_license(name, free):
    assert sources.is_free_license(name) is free


def test_prov_of_cards_and_reuse():
    assert sources.prov_of_scene(_scene("stat")) == "graphic"  # cartão sem visual
    assert sources.prov_of_scene(_scene("map", type="none")) == "graphic"
    photo = Scene(id="scn_a", segment_id="seg", visual=Visual(prompt="x", media=MediaRef(kind="news", source="link")))
    reuse = Scene(id="scn_b", segment_id="seg", visual=Visual(reuse_of="scn_a"))
    ai = Scene(id="scn_c", segment_id="seg", visual=Visual(prompt="y"))
    card_on_ai = Scene(id="scn_d", segment_id="seg", kind="stat", visual=Visual(reuse_of="scn_c"))
    text_on_ai = Scene(id="scn_e", segment_id="seg", kind="text", visual=Visual(reuse_of="scn_c"))
    card_on_news = Scene(id="scn_f", segment_id="seg", kind="compare", visual=Visual(reuse_of="scn_a"))
    doc = ProjectDoc(id="prj_x", title="x", style_id="misterios-short", segments=[{"id": "seg"}],
                     scenes=[photo, reuse, ai, card_on_ai, text_on_ai, card_on_news])
    assert sources.prov_of_scene(reuse, doc) == "capture"  # mostra a notícia da cena dona
    assert sources.prov_of_scene(reuse) == "ai"  # sem o documento, não segue o reaproveitamento
    # Cartão de dado: o painel cobre o fundo desfocado, conta como gráfico (nem IA, nem teto de material oficial).
    assert sources.prov_of_scene(card_on_ai, doc) == "graphic"
    assert sources.prov_of_scene(card_on_news, doc) == "graphic"
    assert sources.prov_of_scene(text_on_ai, doc) == "ai"  # cartão de texto: o fundo fica à vista
    assert sources.TIERS == PROVS and set(sources.FALLBACK) <= set(PROVS)
    assert sources.is_real("official") and not sources.is_real("graphic")


def test_prov_follows_the_reuse_root_visual_type():
    upload = Scene(id="scn_up", segment_id="seg", visual=Visual(type="upload", asset="uploads/p/a.jpg"))
    stock = Scene(id="scn_st", segment_id="seg", visual=Visual(type="stock", query="city"))
    bare_card = Scene(id="scn_card", segment_id="seg", kind="title")
    on_upload = Scene(id="scn_1", segment_id="seg", visual=Visual(reuse_of="scn_up"))
    on_stock = Scene(id="scn_2", segment_id="seg", visual=Visual(reuse_of="scn_st"))
    loose = Scene(id="scn_3", segment_id="seg", visual=Visual(reuse_of="scn_apagada"))
    chain = Scene(id="scn_4", segment_id="seg", visual=Visual(reuse_of="scn_1"))  # reaproveita quem reaproveita
    on_card = Scene(id="scn_5", segment_id="seg", visual=Visual(reuse_of="scn_card"))
    pinned = Scene(id="scn_6", segment_id="seg", visual=Visual(reuse_of="scn_up", asset="cache/images/x.jpg"))
    doc = ProjectDoc(id="prj_x", title="x", style_id="misterios-short", segments=[{"id": "seg"}],
                     scenes=[upload, stock, bare_card, on_upload, on_stock, loose, chain, on_card, pinned])
    assert sources.prov_of_scene(on_upload, doc) == "own"
    assert sources.prov_of_scene(on_stock, doc) == "stock"
    assert sources.prov_of_scene(loose, doc) == "graphic"  # referência solta: o renderer mostra o fundo animado
    assert sources.prov_of_scene(chain, doc) == "own"
    assert sources.prov_of_scene(on_card, doc) == "graphic"
    assert sources.prov_of_scene(pinned, doc) == "ai"  # imagem fixada própria vale mais que a raiz


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


def test_storyboard_schema_has_the_frozen_edit_fields():
    # O storyboard real primeiro sempre explica os campos de edição no pedido: estão no esquema de produção.
    item = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]
    props = item["properties"]
    assert set(STORYBOARD_EDIT_FIELDS) <= set(props)
    assert props["source"]["enum"] == STORYBOARD_SOURCES == ["new", "reuse", "none", "pool"]
    assert authoring.storyboard_schema(False) is STORYBOARD_SCHEMA
    assert set(mock_content._mock_scene("b0")) == set(props)
    for name in ("pool_id", "ai_reason", "gag_payoff", "gag_line", "gag_at"):
        assert props[name] == {"type": ["string", "null"]}
    assert props["emphasis"] == {"type": ["array", "null"], "items": {"type": "string"}}
    assert props["serious"] == {"type": "boolean"} and props["role"]["type"] == "string"
    assert "hook" in props["role"]["enum"] and "breath" in props["role"]["enum"]
    assert {"real_kind", "real_query", "real_note"} <= set(props)  # reserva
    assert item["required"] == list(props)
    assert set(STORYBOARD_EDIT_DEFAULTS) <= set(STORYBOARD_EDIT_FIELDS)
    both = authoring.storyboard_schema(True)["properties"]["scenes"]["items"]
    assert {"presenter_pose", "pool_id"} <= set(both["properties"]) and both["required"] == list(both["properties"])


# --- encaixes vazios e ligação no job ------------------------------------------------------------------------------


def test_empty_hooks_change_nothing(isolated_settings):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc = ProjectDoc.model_validate(data["doc"])
    before = doc.model_dump(mode="json")
    from studio.core.styles.library import get_style

    style = get_style("misterios-short")
    pool = media_pool.gather(doc, style)
    # acervo-real: a mídia anexada à mão vira item do acervo, mas sem `pool_id` nada é anexado nem trocado (o achado
    # `faltou_real` é da conferência do storyboard, não do anexo).
    assert pool.items and all(i.source_id.startswith("file:") for i in pool.items)
    assert media_pool.attach_chosen(doc, pool) == []
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


def _farol() -> ProjectDoc:
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado da ilha")
    _run_job("script", doc.id)
    return doc


def _review(project_id: str) -> dict:
    return service.get_project_row(project_id)["settings"]["storyboard_review"]


def test_storyboard_job_calls_the_hooks_in_order(isolated_settings, monkeypatch):
    calls: list[str] = []
    real_storyboard = authoring.storyboard
    attached: list[str] = []

    def gather(doc, style, *, progress=None, cancelled=None):
        calls.append("gather")
        assert progress is not None and cancelled is not None and cancelled() is False
        progress(0.5, "Baixando os clipes")
        return pool_mod.Pool(items=[pool_mod.PoolItem(id="p1", tier="official", desc="trailer")])

    def storyboard(*args, **kwargs):
        calls.append("storyboard")
        assert pool_mod.load_pool(args[0].id).item("p1") is not None  # gravado antes do storyboard
        assert kwargs["pool"].item("p1") is not None  # e o mesmo acervo vai junto, explícito
        return real_storyboard(*args, **kwargs)

    def attach(doc, pool):
        calls.append("attach")
        assert pool.item("p1") is not None and doc.scenes
        for scene in doc.scenes:  # o acervo anexa mídia em todas as cenas que gerariam imagem
            if scene.kind == "image" and not scene.visual.reuse_of:
                scene.visual.media = MediaRef(kind="clip", source="youtube", tier="official", pool_id="p1",
                                              asset=f"uploads/{doc.id}/media/{scene.id}.jpg")
                attached.append(scene.id)
        return [Issue("faltou_real", AVISO, "A cena ficou em IA sem motivo.", scene_id=doc.scenes[-1].id)]

    def plan(doc, style, pool):
        calls.append("plan")
        doc.scenes[0].fx = [FxEvent(type="punch", at="@100")]
        return []

    monkeypatch.setattr(media_pool, "gather", gather)
    monkeypatch.setattr(media_pool, "attach_chosen", attach)
    monkeypatch.setattr(edit_plan, "plan", plan)
    monkeypatch.setattr(authoring, "storyboard", storyboard)
    # a hierarquia do cartão tipográfico roda entre o anexo e o planejador (`typographic.settle`)
    monkeypatch.setattr(typographic, "settle", lambda doc, style, pool: calls.append("typographic") or [])
    doc = _farol()
    result = _run_job("storyboard", doc.id)
    assert calls == ["gather", "storyboard", "attach", "typographic", "plan"]
    assert attached and "A cena ficou em IA sem motivo." in result["warnings"]
    saved = service.get_doc(doc.id)
    assert [e.type for s in saved.scenes for e in s.fx] == ["punch"]  # o que o planejador escreve é gravado
    assert all(s.visual.media is not None and s.visual.media.editorial for s in saved.scenes if s.id in attached)
    # Contagens, mensagem do commit e revisão saem do documento FINAL (depois do anexo), com o achado por cena.
    review = _review(doc.id)
    expected_new = review_counts(saved, review["counts"]["budget"])["new_images"]
    assert expected_new <= len(saved.scenes) - len(attached)
    assert review["counts"]["new_images"] == result["new_images"] == expected_new
    assert f"{expected_new} imagens novas" in service.list_versions(doc.id)[0]["message"]
    assert {"code": "faltou_real", "severity": AVISO, "scene_key": None, "scene_id": saved.scenes[-1].id,
            "message": "A cena ficou em IA sem motivo."} in review["issues"]


def test_media_find_is_skipped_when_the_pool_already_searched(isolated_settings, monkeypatch):
    from studio.workers.pipeline import media_find

    def gather(doc, style, **_):
        return pool_mod.Pool(searched=True)

    def never(*args, **kwargs):
        raise AssertionError("media_find rodou duas vezes")

    monkeypatch.setattr(media_pool, "gather", gather)
    monkeypatch.setattr(media_find, "queue_find", never)
    monkeypatch.setattr(media_find, "auto_find", never)
    doc = _farol()
    result = _run_job("storyboard", doc.id)
    assert "media_find_job" not in result and "media_find" not in result


def test_storyboard_job_always_writes_the_gathered_pool(isolated_settings, monkeypatch):
    """Uma fonte só: o `pool.json` é sempre o acervo desta rodada. Uma rodada com acervo e depois uma sem: a segunda
    não vê os itens da primeira (nem no arquivo, nem no que o storyboard e o anexo recebem)."""
    rounds = [pool_mod.Pool(items=[pool_mod.PoolItem(id="p1", tier="official", desc="trailer")]), pool_mod.Pool()]
    seen: list[tuple[int, int, int]] = []
    real_storyboard = authoring.storyboard

    def gather(doc, style, **_):
        return rounds.pop(0)

    def storyboard(*args, **kwargs):
        seen.append((len(pool_mod.load_pool(args[0].id).items), len(kwargs["pool"].items), -1))
        return real_storyboard(*args, **kwargs)

    def attach(doc, pool):
        seen[-1] = (*seen[-1][:2], len(pool.items))
        return []

    monkeypatch.setattr(media_pool, "gather", gather)
    monkeypatch.setattr(media_pool, "attach_chosen", attach)
    monkeypatch.setattr(authoring, "storyboard", storyboard)
    doc = _farol()
    _run_job("storyboard", doc.id)
    path = isolated_settings.uploads_dir / doc.id / "pool.json"
    assert path.is_file() and pool_mod.load_pool(doc.id).item("p1") is not None
    _run_job("storyboard", doc.id)
    assert seen == [(1, 1, 1), (0, 0, 0)]
    again = pool_mod.load_pool(doc.id)
    assert again.items == [] and again.gathered_at  # o vazio também é gravado, com a hora da coleta


def test_storyboard_job_without_pool_has_no_pool_warning(isolated_settings):
    doc = _farol()
    result = _run_job("storyboard", doc.id)
    assert "warnings" not in result or all("acervo" not in w for w in result["warnings"])
    pool = pool_mod.load_pool(doc.id)
    assert pool.items == [] and pool.warnings == [] and pool.gathered_at


def test_failing_gather_becomes_a_portuguese_warning_and_clears_the_old_pool(isolated_settings, monkeypatch):
    doc = _farol()
    pool_mod.save_pool(doc.id, pool_mod.Pool(items=[pool_mod.PoolItem(id="p7", tier="free", desc="velho")]))

    def boom(doc, style, **_):
        raise RuntimeError("sem rede: ConnectTimeout('x')")

    monkeypatch.setattr(media_pool, "gather", boom)
    result = _run_job("storyboard", doc.id)
    assert any("acervo" in w for w in result["warnings"])
    assert not any("sem rede" in w or "ConnectTimeout" in w for w in result["warnings"])  # nada de exceção crua
    assert pool_mod.load_pool(doc.id).items == []  # o acervo velho não fica valendo
    assert any(i["code"] == "acervo_aviso" for i in _review(doc.id)["issues"])
    assert service.get_doc(doc.id).scenes


def test_failing_attach_or_plan_leaves_nothing_half_applied(isolated_settings, monkeypatch):
    def attach(doc, pool):
        doc.scenes[0].visual.media = MediaRef(kind="photo", source="upload", asset="uploads/x/a.jpg")
        raise RuntimeError("KeyError: 'p9'")

    def plan(doc, style, pool):
        doc.scenes[0].fx = [FxEvent(type="punch", at="@100")]
        doc.scenes[1].fx = "quebrado"  # type: ignore[assignment] - o resultado inválido também é descartado
        return []

    monkeypatch.setattr(media_pool, "attach_chosen", attach)
    monkeypatch.setattr(edit_plan, "plan", plan)
    doc = _farol()
    result = _run_job("storyboard", doc.id)
    saved = service.get_doc(doc.id)
    assert all(s.visual.media is None and s.fx == [] for s in saved.scenes)
    assert len([w for w in result["warnings"] if "acervo" in w or "planejador" in w]) == 2
    assert not any("KeyError" in w for w in result["warnings"])
    assert [i["code"] for i in _review(doc.id)["issues"]].count("edicao_falhou") == 2


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


def _ts_object_keys(source: str, name: str) -> dict[str, bool]:
    """As chaves de um `export type X = { ... };` de uma linha: {chave: opcional}."""
    match = re.search(rf"export type {name} = \{{([^}}]+)\}};", source)
    assert match, name
    return {m.group(1): bool(m.group(2)) for m in re.finditer(r"(\w+)(\?)?:", match.group(1))}


def test_sfx_event_contract_mirrors_the_real_timeline_event():
    """O evento de SFX congelado tem as chaves que a timeline JÁ emite e que o mixer (`build.py`) e a conferência de
    áudio leem (`start`, `scene_id`); o SFX de evento (timeline-fx) soma `at` e `scene`, e `lib_id` e `gain_db` são
    opcionais."""
    from studio.core.timeline import _sfx_event

    source = (REPO_ROOT / "renderer" / "src" / "types.ts").read_text(encoding="utf-8")
    keys = _ts_object_keys(source, "TimelineSfxEvent")
    event = _sfx_event("impact", 3.0, "scn_a")
    assert set(event) <= set(keys) and not any(keys[k] for k in event)  # obrigatórias no tipo
    assert {k for k, optional in keys.items() if optional} == {"lib_id", "gain_db"}
    assert set(_ts_object_keys(source, "TimelineMusicDrop")) == {"at", "dur"}


def test_is_serious_joins_the_mark_and_the_presenter_terms():
    from studio.core.editing.serious import is_serious, serious_term
    from studio.workers.pipeline import presenter

    plain = Scene(segment_id="s")
    assert not is_serious(plain) and not is_serious(plain, "o jogo vendeu muito")
    assert is_serious(plain, "a vítima foi encontrada") and serious_term("homicídios na cidade") == "homicid"
    assert not is_serious(plain, "Assassin's Creed chegou")
    assert is_serious(Scene(segment_id="s", mark=BeatMark(serious=True)), "o jogo vendeu muito")
    assert presenter._serious is serious_term and presenter.SERIOUS_TERMS  # o apresentador usa a mesma regra
