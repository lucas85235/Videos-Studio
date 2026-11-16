"""Família de dados e geografia: gráficos (barra, linha, pizza, rosca) e mapas no documento, na timeline e no storyboard."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from test_timeline import _clips, _doc

from studio.core.project.document import CardItem, ProjectDoc, Scene, SceneCard, Segment
from studio.core.regions import find_regions, region_code, region_name, regions
from studio.core.styles.library import get_style, load_presets
from studio.core.styles.schema import FAMILIES_V1, Family, policy_warnings
from studio.core.timeline import build_timeline
from studio.providers.base import LLMResult, Usage
from studio.workers.pipeline import authoring, mock_content
from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA

ROOT = Path(__file__).resolve().parents[2]
PT = "pt-BR"


def _items(*pairs: tuple[str, str], region: list[str] | None = None) -> list[CardItem]:
    regions_ = region or [None] * len(pairs)  # type: ignore[list-item]
    return [CardItem(label={PT: label} if label else {}, value={PT: value} if value else {}, region=code)
            for (label, value), code in zip(pairs, regions_, strict=True)]


# --- regiões ----------------------------------------------------------------------------------


def test_region_code_accepts_codes_and_names_in_three_languages():
    assert region_code("BRA") == region_code("bra") == region_code("br") == "BRA"
    assert region_code("Brasil") == region_code("Brazil") == "BRA"
    assert region_code("Irã") == region_code("Iran") == region_code("Irão") == "IRN"
    assert region_code("EUA") == region_code("Estados Unidos") == region_code("United States") == "USA"
    assert region_code("us-tx") == region_code("Texas") == "US-TX"
    assert region_code("Nova York") == region_code("New York") == "US-NY"
    # o Congo vinha trocado no pacote de nomes: os dois países ficam com o nome certo
    assert region_name("COD", PT) == "República Democrática do Congo"
    assert region_name("COG", PT) == "República do Congo"
    for bad in ("", "  ", "XX", "Atlântida", None, 3):
        assert region_code(bad) is None


def test_region_name_by_language_with_english_fallback():
    assert region_name("DEU", PT) == "Alemanha"
    assert region_name("DEU", "en-US") == "Germany"
    assert region_name("DEU", "es") == "Alemania"
    assert region_name("DEU", "ja-JP") == "Germany"
    assert region_name("US-CA", PT) == "Califórnia"
    assert region_name("ZZZ", PT) == "ZZZ"


def test_find_regions_in_narration_keeps_order_without_repeating():
    text = "O Pantanal fica no Brasil, na Bolívia e no Paraguai; o Brasil tem a maior parte. Já o Texas..."
    assert find_regions(text) == ["BRA", "BOL", "PRY", "US-TX"]
    assert find_regions("Nada de lugares nesta frase.") == []


def test_names_table_matches_the_renderer_maps():
    """Todo código do backend existe nos mapas embutidos do renderer (e vice-versa), senão o mapa sai sem destaque."""
    geo = ROOT / "renderer" / "src" / "geo"
    drawn = {r["id"] for name in ("world.json", "us-states.json")
             for r in json.loads((geo / name).read_text(encoding="utf-8"))["regions"] if r["id"]}
    assert drawn == set(regions())
    assert len([c for c in drawn if not c.startswith("US-")]) >= 170
    assert {"BRA", "USA", "CHN", "RUS", "FJI", "US-TX", "US-AK", "US-HI"} <= drawn
    for name in ("world.json", "us-states.json"):
        assert (geo / name).stat().st_size < 400_000  # mapa simplificado, de tamanho razoável


# --- documento e timeline -----------------------------------------------------------------------


def test_old_cards_resolve_exactly_as_before():
    card = SceneCard(label={PT: "Trailers"}, items=_items(("Trailer 1", "90 MI"), ("Trailer 2", "475 MI")), highlight=1)
    assert card.resolved(PT) == {"label": "Trailers", "highlight": 1,
                                 "items": [{"label": "Trailer 1", "value": "90 MI"},
                                           {"label": "Trailer 2", "value": "475 MI"}]}


def _data_doc() -> ProjectDoc:
    doc = _doc(n_segments=1, scenes_per_segment=4)
    chart, mapa, legacy = doc.scenes[1], doc.scenes[2], doc.scenes[3]
    chart.kind = "chart"
    chart.card = SceneCard(label={PT: "Exportações"}, chart="line", highlight=2,
                           items=_items(("2000", "11 MI"), ("2010", "29 MI"), ("2023", "1,5 BI")))
    mapa.kind = "map"
    mapa.card = SceneCard(label={PT: "Onde fica"}, highlight=2, items=_items(
        ("", "62%"), ("Atlântida", ""), ("", "20%"), ("Paraguai", ""), region=["bra", None, "BOL", None]))
    legacy.kind = "compare"
    legacy.card = SceneCard(items=_items(("A", "1"), ("B", "2")))
    return doc


def test_timeline_resolves_chart_and_map_cards():
    doc = _data_doc()
    timeline = build_timeline(doc, get_style("dados-e-mapas"), PT, _clips(doc, 1.0), {})
    scenes = {s["id"]: s for s in timeline["scenes"]}

    chart = scenes["scn_0_1"]
    assert chart["kind"] == "chart"
    assert chart["card"]["chart"] == "line" and chart["card"]["highlight"] == 2
    assert [(i["label"], i["value"], i["amount"]) for i in chart["card"]["items"]] == [
        ("2000", "11 MI", 11e6), ("2010", "29 MI", 29e6), ("2023", "1,5 BI", 1.5e9)]
    assert all("region" not in i for i in chart["card"]["items"])

    mapa = scenes["scn_0_2"]["card"]
    # região desconhecida sai; sem rótulo, o nome no idioma do vídeo; nome solto vira código; o destaque acompanha
    assert [(i["region"], i["label"]) for i in mapa["items"]] == [("BRA", "Brasil"), ("BOL", "Bolívia"),
                                                                   ("PRY", "Paraguai")]
    assert mapa["items"][0]["amount"] == 62 and "amount" not in mapa["items"][2]
    assert mapa["highlight"] == 1

    english = build_timeline(doc, get_style("dados-e-mapas"), "en-US", _clips(doc, 1.0), {})
    labels = [i["label"] for i in english["scenes"][2]["card"]["items"]]
    assert labels[:2] == ["Brazil", "Bolivia"]

    # o compare continua como antes (sem amount nem region)
    assert scenes["scn_0_3"]["card"]["items"] == [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}]
    # o gráfico entra com impacto, como os outros cartões de dado
    sfx = {e["scene_id"]: e["type"] for e in timeline["sfx"]}
    assert sfx.get("scn_0_1") == "impact"


def test_map_scene_never_spends_an_image():
    from studio.workers.pipeline.build import scene_needs_image

    doc = _doc(n_segments=1, scenes_per_segment=1)
    scene = doc.scenes[0]
    assert scene_needs_image(scene)
    scene.kind = "map"
    assert not scene_needs_image(scene)  # o prompt fica de reserva para quando a cena voltar a ser imagem
    scene.kind = "chart"
    assert scene_needs_image(scene)  # o gráfico fica sobre a imagem desfocada


def test_map_without_known_region_drops_items():
    doc = _doc(n_segments=1, scenes_per_segment=2)
    doc.scenes[1].kind = "map"
    doc.scenes[1].card = SceneCard(label={PT: "Lugar"}, items=_items(("Atlântida", "")), highlight=0)
    timeline = build_timeline(doc, get_style("dados-e-mapas"), PT, _clips(doc, 1.0), {})
    card = timeline["scenes"][1]["card"]
    assert card == {"label": "Lugar"}  # o renderer desenha outro tipo no lugar do mapa vazio


# --- storyboard ---------------------------------------------------------------------------------


def _card(data: dict[str, Any], kind: str) -> tuple[str, SceneCard | None]:
    return authoring._card_from({"label": "Título", **data}, kind, PT)


def test_card_from_chart_types_and_fallbacks():
    years = [{"label": y, "value": v, "region": None} for y, v in (("2000", "11 MI"), ("2010", "29 MI"), ("2023", "101 MI"))]
    kind, card = _card({"items": years, "chart": None}, "chart")
    assert kind == "chart" and card is not None and card.chart == "line" and len(card.items) == 3

    cats = [{"label": n, "value": v, "region": None} for n, v in (("Brasil", "101"), ("EUA", "46"), ("Paraguai", "7"))]
    assert _card({"items": cats, "chart": None}, "chart")[1].chart == "bar"  # type: ignore[union-attr]
    assert _card({"items": cats, "chart": "donut"}, "chart")[1].chart == "donut"  # type: ignore[union-attr]
    assert _card({"items": cats, "chart": "pizza"}, "chart")[1].chart == "bar"  # type: ignore[union-attr]
    negative = [*cats[:2], {"label": "Saldo", "value": "-3", "region": None}]
    assert _card({"items": negative, "chart": "pie"}, "chart")[1].chart == "bar"  # type: ignore[union-attr]

    many = [{"label": str(2000 + i), "value": str(10 + i), "region": None} for i in range(12)]
    assert len(_card({"items": many, "chart": "line"}, "chart")[1].items) == 8  # type: ignore[union-attr]

    # sem 3 números, o gráfico vira comparação (e com 1, número)
    two = [{"label": "A", "value": "10", "region": None}, {"label": "B", "value": "sem número", "region": None},
           {"label": "C", "value": "30", "region": None}]
    kind, card = _card({"items": two, "chart": "bar"}, "chart")
    assert kind == "compare" and card is not None and card.chart is None
    assert [i.label[PT] for i in card.items] == ["A", "C"]
    one = [{"label": "A", "value": "10", "region": None}]
    assert _card({"items": one, "chart": "bar"}, "chart")[0] == "stat"


def test_card_from_map_regions():
    items = [{"label": "", "value": "62%", "region": "BRA"}, {"label": "Bolívia", "value": "", "region": None},
             {"label": "Lá", "value": "", "region": "Atlântida"}, {"label": "Brasil", "value": "", "region": "br"},
             {"label": "Paraguai e vizinhos", "value": "18%", "region": "PRY"}]
    kind, card = _card({"items": items, "highlight": 0}, "map")
    assert kind == "map" and card is not None
    assert [(i.region, i.label.get(PT, ""), i.value.get(PT, "")) for i in card.items] == [
        ("BRA", "", "62%"), ("BOL", "", ""), ("PRY", "Paraguai e vizinhos", "18%")]
    assert card.label == {PT: "Título"} and card.highlight == 0

    assert _card({"items": [{"label": "Atlântida", "value": "", "region": None}]}, "map")[0] == "text"
    assert _card({"items": [], "value": "214 MI"}, "map")[0] == "stat"


DATA_SEGMENTS = [
    ("hook", "Por que o Brasil virou o maior exportador de soja do mundo?"),
    ("body", "As exportações saltaram: 11 milhões, 29 milhões e 101 milhões de toneladas."),
    ("body", "Tudo começou longe do mar, no Pantanal, entre a Bolívia e o Paraguai."),
    ("body", "E a China compra quase tudo isso."),
    ("cta", "Inscreva-se para entender o mundo pelos números."),
]


def _soja_doc(style_id: str) -> ProjectDoc:
    segments = [Segment(id=f"seg_soja{i}", role=role, text={PT: text}) for i, (role, text) in enumerate(DATA_SEGMENTS)]
    return ProjectDoc(id="prj_soja", title="Soja", topic="a soja brasileira", style_id=style_id, seed=7, segments=segments)


class MockLLM:
    """O storyboard offline (mock_content), passando pelo mesmo caminho do LLM de verdade."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, task, system, prompt, schema, context, project_id, job_id):
        self.calls.append({"system": system, "prompt": prompt, "context": context})
        data = mock_content.mock_storyboard(copy.deepcopy(context))
        return LLMResult(data=data, text="", usage=Usage()), "mock"


def test_data_style_storyboard_makes_charts_and_maps(monkeypatch):
    fake = MockLLM()
    monkeypatch.setattr(authoring, "_llm", fake)
    style = get_style("dados-e-mapas")
    new_doc, _provider, _review = authoring.storyboard(_soja_doc("dados-e-mapas"), style, styles={})
    assert authoring.STORYBOARD_DATA_RULES in fake.calls[0]["prompt"]
    assert fake.calls[0]["context"]["data_geo"] is True

    by_kind: dict[str, list[Scene]] = {}
    for scene in new_doc.ordered_scenes():
        by_kind.setdefault(scene.kind, []).append(scene)
    chart = by_kind["chart"][0]
    assert chart.card is not None and chart.card.chart == "bar" and len(chart.card.items) == 3
    maps = by_kind["map"]
    assert [i.region for i in maps[0].card.items] == ["BOL", "PRY"]  # type: ignore[union-attr]
    assert all(m.visual.type == "none" and m.visual.reuse_of is None for m in maps)  # o mapa não usa fundo


def test_other_styles_keep_stat_and_compare(monkeypatch):
    fake = MockLLM()
    monkeypatch.setattr(authoring, "_llm", fake)
    new_doc, _provider, _review = authoring.storyboard(_soja_doc("curiosidades-short"), get_style("curiosidades-short"),
                                                       styles={})
    assert authoring.STORYBOARD_DATA_RULES not in fake.calls[0]["prompt"]
    kinds = {s.kind for s in new_doc.scenes}
    assert not kinds & {"chart", "map"}
    assert "compare" in kinds


def test_mock_data_storyboard_follows_the_strict_schema():
    style = get_style("dados-e-mapas")
    doc = _soja_doc("dados-e-mapas")
    beats = authoring.plan_beats(doc, style)
    data = mock_content.mock_storyboard(authoring._storyboard_context(doc, style, beats, authoring.image_budget(len(beats), style)))
    scene_schema = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]
    item_keys = set(scene_schema["properties"]["items"]["items"]["properties"])
    assert {"chart", "map"} <= {s["kind"] for s in data["scenes"]}
    for scene in data["scenes"]:
        assert set(scene) == set(scene_schema["properties"])
        for item in scene["items"] or []:
            assert set(item) == item_keys


# --- estilo ---------------------------------------------------------------------------------------


def test_data_preset_uses_the_data_family_without_warnings():
    style = load_presets()["dados-e-mapas"]
    assert style.family == Family.data_geo and Family.data_geo in FAMILIES_V1
    assert authoring.is_data_style(style)
    assert policy_warnings(style) == []
    assert style.format.aspect == "16:9"
    assert not authoring.is_data_style(get_style("financas-negocios"))
