"""IA na tela: todo pixel de IA conta na métrica e no teto da distribuição (QA do prj_gq4krrs11w, rodada 3).

O painel contou ~60% de IA no minuto 3, e a medida dizia 12% na média e 32% no pior trecho: a arte de fã e de IA da
web entrava pelo nível do acervo (foto livre, oficial), e o fundo de IA atrás do cartão de dado não contava.
"""

from __future__ import annotations

from typing import Any

import pytest

from studio.core import sources
from studio.core.editing import metrics
from studio.core.project.document import MediaRef, ProjectDoc, Scene, Segment, Visual
from studio.core.project.pool import PoolItem
from studio.workers.pipeline import authoring, media_find, media_pool, storyboard_check

FPS = 30


def _scene(sid: str, frm: float, dur: float, *, prov: str, kind: str = "image", src: str | None = None,
           **extra: Any) -> dict[str, Any]:
    return {"id": sid, "kind": kind, "from": round(frm * FPS), "durationInFrames": round(dur * FPS),
            "motion": "static", "transition": "cut", "prov": prov, "src": src or f"cache/media/{sid}.jpg",
            "text": "", "captions": [], "words": [], **extra}


def _timeline(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    total = scenes[-1]["from"] + scenes[-1]["durationInFrames"]
    return {"version": 1, "lang": "pt-BR", "fps": FPS, "width": 1920, "height": 1080, "durationInFrames": total,
            "duration": total / FPS, "scenes": scenes, "sfx": []}


FAN_COVER = MediaRef(kind="photo", source="openverse", asset="uploads/p/media/a.jpg", title="GTA 6 Fan Cover",
                     url="https://live.staticflickr.com/4157/34386102316_0bf525ca8a_b.jpg",
                     page_url="https://www.flickr.com/photos/31954002@N08/34386102316",
                     credit="Davmi Pics / Flickr · CC BY-SA 2.0", license="CC BY-SA 2.0", tier="free")


@pytest.mark.parametrize("text, synthetic", [
    ("GTA 6 Fan Cover", True), ("fan art of the heroine", True), ("Fan-made poster", True), ("fanart", True),
    ("AI-generated city at night", True), ("made with Midjourney", True), ("imagem gerada por IA", True),
    ("DALL-E render", True), ("Rockstar fan", False), ("fans at the concert", False), ("Fanatec wheel", False),
    ("AI chip on a motherboard", False), ("Grand Theft Auto VI hype leads to malware", False),
])
def test_source_text_tells_fan_and_ai_art(text, synthetic):
    assert sources.looks_synthetic(text) is synthetic


def test_fan_or_ai_art_from_the_web_is_ai_on_screen_whatever_the_pool_tier():
    assert sources.prov_of_media(FAN_COVER) == "ai"
    # o arquivo do usuário e a biblioteca do canal ficam com o nível deles
    own = FAN_COVER.model_copy(update={"source": "library", "tier": "official"})
    assert sources.prov_of_media(own) == "official"
    real = FAN_COVER.model_copy(update={"title": "Golden Gate Bridge at dusk"})
    assert sources.prov_of_media(real) == "free"


def test_ai_behind_a_data_card_counts_with_weight_in_the_metric():
    scenes = [
        _scene("scn_a", 0, 10, prov="official"),
        _scene("scn_b", 10, 10, prov="graphic", kind="stat", bg_prov="ai", src="cache/image/b.jpg"),
        _scene("scn_c", 20, 10, prov="graphic", kind="stat", src="cache/image/c.jpg"),  # timeline antiga
        _scene("scn_d", 30, 10, prov="graphic", kind="stat"),  # fundo real
        _scene("scn_e", 40, 10, prov="ai", kind="text"),  # cartão de texto sobre IA: inteiro
    ]
    m = metrics.measure_timeline(_timeline(scenes))
    by_scene = {s.scene_id: s.ai for s in m.shots}
    assert by_scene == {"scn_a": 0.0, "scn_b": sources.PANEL_AI_WEIGHT, "scn_c": sources.PANEL_AI_WEIGHT,
                        "scn_d": 0.0, "scn_e": 1.0}
    assert metrics.ai_screen_share(m.fp) == pytest.approx((5 + 5 + 10) / 50)
    assert m.fp.materials.prov_mix["ai"] == pytest.approx(0.2)  # o nível continua o nível
    assert m.fp.ai_window_share == pytest.approx(20 / 45, abs=0.01)
    assert m.ai_scene_ids == ["scn_b", "scn_c", "scn_e"]


def test_ai_subshot_inside_a_real_scene_counts_and_real_subshot_of_ai_scene_does_not():
    real = _scene("scn_r", 0, 10, prov="official",
                  shots=[{"from": 0, "prov": "official"}, {"from": 150, "prov": "ai", "src": "cache/image/x.jpg"}])
    ai = _scene("scn_i", 10, 10, prov="ai",
                shots=[{"from": 0, "prov": "ai"}, {"from": 150, "prov": "capture", "src": "cache/media/y.jpg"}])
    m = metrics.measure_timeline(_timeline([real, ai]))
    assert [(round(s.start), s.ai) for s in m.shots] == [(0, 0.0), (5, 1.0), (10, 1.0), (15, 0.0)]
    assert metrics.ai_screen_share(m.fp) == pytest.approx(0.5)


def test_document_saying_ai_wins_over_an_old_tier_in_the_timeline():
    """A timeline gravou a capa de fã pelo nível do acervo (`free`); com o documento a medida vê a IA."""
    seg = Segment(id="seg_0", text={"pt-BR": "uma frase"})
    doc = ProjectDoc(id="prj_x", title="t", style_id="misterios-short", segments=[seg], scenes=[
        Scene(id="scn_a", segment_id="seg_0", kind="image", visual=Visual(type="stock", media=FAN_COVER)),
        Scene(id="scn_b", segment_id="seg_0", part=1, parts=2, kind="image", visual=Visual(type="stock")),
    ])
    tl = _timeline([_scene("scn_a", 0, 10, prov="free"), _scene("scn_b", 10, 10, prov="stock")])
    assert metrics.ai_screen_share(metrics.measure_timeline(tl).fp) == 0.0
    assert metrics.ai_screen_share(metrics.measure_timeline(tl, doc).fp) == pytest.approx(0.5)


def test_storyboard_cap_weighs_the_ai_behind_a_data_card():
    seg = Segment(id="seg_0", text={"pt-BR": "uma frase"})
    stat = Scene(id="scn_s", segment_id="seg_0", kind="stat", visual=Visual(prompt="a city at night"))
    text = Scene(id="scn_t", segment_id="seg_0", kind="text", visual=Visual(prompt="a city at night"))
    bare = Scene(id="scn_n", segment_id="seg_0", kind="stat", visual=Visual(type="none"))
    fan = Scene(id="scn_f", segment_id="seg_0", kind="image", visual=Visual(type="stock", media=FAN_COVER))
    doc = ProjectDoc(id="prj_x", title="t", style_id="misterios-short", segments=[seg], scenes=[stat, text, bare, fan])
    assert sources.prov_of_scene(stat, doc) == "graphic"  # o nível do cartão de dado não muda (teto de oficial)
    assert [sources.ai_weight(s, doc) for s in (stat, text, bare, fan)] == [sources.PANEL_AI_WEIGHT, 1.0, 0.0, 1.0]
    # o trecho com o cartão sobre IA entra pesado na janela da distribuição
    timed = [(stat, "graphic", 20.0), (text, "ai", 10.0), (bare, "graphic", 30.0)]
    weights = [sources.ai_weight(s, doc) for s, _p, _x in timed]
    issue = storyboard_check._ai_window_issue(timed, {}, 0.2, weights)
    assert issue and issue[0].extra["share"] == pytest.approx((10 + 10) / 45, abs=0.01)
    assert issue[0].scene_id == "scn_s"
    assert not storyboard_check._ai_window_issue(timed, {}, 0.2, [0.0, 1.0, 0.0])


def test_fan_and_ai_art_never_enter_the_pool_nor_a_subshot():
    fan = {"tier": "free", "media": "image", "desc": "GTA 6 Fan Cover", "source_id": "openverse:1",
           "url": "https://live.staticflickr.com/x.jpg", "page_url": "https://www.flickr.com/photos/x"}
    fan_of = {**fan, "desc": "Rockstar fan in the crowd", "source_id": "openverse:2"}
    mine = {**fan, "source_id": "kit:capa", "tier": "official"}
    assert media_pool.is_synthetic(fan) and not media_pool.is_synthetic(fan_of) and not media_pool.is_synthetic(mine)
    item = PoolItem(id="p1", **{k: v for k, v in fan.items()})
    assert media_pool.is_synthetic(item) and sources.item_is_synthetic(item)
    assert not authoring.usable_item(item)  # acervo coletado antes do filtro também não serve
    assert authoring.usable_item(item.model_copy(update={"desc": "Golden Gate Bridge"}))


def test_web_search_drops_illustrations_and_fan_art():
    data = {"scenes": [{"scene_id": "s1", "candidates": [
        {"image": "https://a.com/hero.jpg", "page_url": "https://a.com/x", "title": "Hype leads to malware",
         "outlet": "Blog", "why": "imagem principal", "made": "illustration"},
        {"image": "https://b.com/fan.jpg", "page_url": "https://b.com/y", "title": "GTA 6 fan art poster",
         "outlet": "B", "why": "capa"},
        {"image": "https://c.com/shot.jpg", "page_url": "https://c.com/z", "title": "Fake demo page",
         "outlet": "C", "why": "print", "made": "screenshot"},
    ]}]}
    assert [c["image"] for c in media_find.parse_candidates(data, {"s1"})["s1"]] == ["https://c.com/shot.jpg"]
    made = media_find.SCHEMA["properties"]["scenes"]["items"]["properties"]["candidates"]["items"]["properties"]["made"]
    assert "illustration" in made["enum"]


@pytest.mark.parametrize("prompt, entities, expected", [
    ("A neon sign with the game logo over a wet street", [], "logo"),
    ("The box art of the game on a shelf", [], "box art"),
    ("A woman driving across a bridge at dusk", ["lucia"], "Lucia"),
    ("Lucia driving across a bridge at dusk", [], "Lucia"),
    ("A cardboard parcel on a desk at night, no logos", [], None),
    ("A hand pulls the network cable out of the PC", ["pc"], None),
])
def test_brand_request_finds_logo_and_official_character(prompt, entities, expected):
    characters = {"lucia": ["Lucia"]}
    assert storyboard_check.brand_request(prompt, entities, characters) == expected
