"""Estilo a partir do vídeo de referência: medidas sintéticas → StyleSpec válido, com e sem LLM (simulado)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.config import REPO_ROOT
from studio.core.editing import grammar as gr
from studio.core.editing.profile import EditProfile
from studio.core.styles import from_reference as fr
from studio.core.styles.library import get_style, list_styles, load_presets
from studio.core.styles.schema import EditGrammar, SourcePolicy, StyleSpec, grammar_for, sources_for
from studio.db import session_scope
from studio.models import ReferenceVideo


def shots_of(durs: list[float]) -> list[dict]:
    out, t = [], 0.0
    for i, d in enumerate(durs, 1):
        out.append({"k": i, "a": round(t, 2), "b": round(t + d, 2), "dur": d})
        t += d
    return out


# Vertical, cortes rápidos, paleta escura e fria.
FAST_VERTICAL = {
    "title": "Trailer Sombrio",
    "info": {"w": 1080, "h": 1920, "fps": 29.97, "dur": 30.0},
    "shots": shots_of([1.0, 1.2, 1.4, 1.5, 1.6, 1.8, 2.0, 1.1]),
    "stats": {"shots": 8, "median_s": 1.45, "mean_s": 1.45, "cuts_per_min": 14.0,
              "palette": ["#0b1418", "#14262c", "#1f3b44", "#2a8c9a", "#08090c"]},
}
# Horizontal, planos longos, paleta quente e pouco saturada.
SLOW_HORIZONTAL = {
    "title": "Documentário Antigo",
    "info": {"w": 1920, "h": 1080, "fps": 24.0, "dur": 300.0},
    "shots": shots_of([4.5, 5.0, 5.5, 6.0, 6.5, 7.0]),
    "stats": {"shots": 6, "median_s": 5.75, "mean_s": 5.75, "cuts_per_min": 1.0,
              "palette": ["#7a6a55", "#544636", "#b09e86", "#3a3025", "#96846c"]},
}


def no_llm(*_a, **_k):
    raise AssertionError("o LLM não deveria ser chamado")


def test_fast_vertical_measures_become_style():
    out = fr.build_style(FAST_VERTICAL, use_llm=False)
    style = StyleSpec.model_validate(out["style"])
    assert out["base_style_id"] in load_presets()
    assert load_presets()[out["base_style_id"]].format.aspect == "9:16"
    assert style.format.aspect == "9:16" and style.format.resolution == "1080x1920"
    assert style.format.type == "short" and style.format.fps == 30
    # Faixa do 1º ao 3º quartil dos planos.
    assert style.edit.shot_seconds.min == pytest.approx(1.2, abs=0.11)
    assert style.edit.shot_seconds.max == pytest.approx(1.65, abs=0.11)
    # Ritmo rápido: 85% de cortes secos e transições curtas.
    total = sum(style.edit.transitions.values())
    assert style.edit.transitions["cut"] / total == pytest.approx(0.85, abs=0.02)
    assert style.edit.transition_seconds == 0.2
    grade = [e for e in style.edit.effects if e.type == "grade"]
    assert len(grade) == 1 and grade[0].preset == "cold-teal-dark"
    assert style.edit.text_cards.background == "#08090C"
    assert style.edit.captions.highlight == "#2A8C9A"
    assert style.id.startswith("ref-trailer-sombrio")
    assert "referencia" in style.tags
    assert out["llm"]["used"] is False


def test_slow_horizontal_measures_become_style():
    out = fr.build_style(SLOW_HORIZONTAL, use_llm=False)
    style = StyleSpec.model_validate(out["style"])
    assert style.format.aspect == "16:9" and style.format.type == "long" and style.format.fps == 24
    assert load_presets()[out["base_style_id"]].format.aspect == "16:9"
    assert 4.5 <= style.edit.shot_seconds.min <= 5.5 and 6.0 <= style.edit.shot_seconds.max <= 7.0
    assert "fade_black" in style.edit.transitions
    assert style.edit.transition_seconds == 0.6
    assert next(e.preset for e in style.edit.effects if e.type == "grade") == "warm-sepia"
    sources = {o["path"]: o["source"] for o in out["origins"]}
    assert sources["edit.shot_seconds"] == "ritmo"
    assert sources["edit.effects"] == "paleta"
    assert sources["format.aspect"] == "proporcao"
    assert sources["base"] == "preset"


def test_aspect_change_and_dead_text_ratio_is_gone():
    ref = {**FAST_VERTICAL, "info": {"w": 1080, "h": 1080, "fps": 60}, "stats": {**FAST_VERTICAL["stats"], "text_ratio": 0.6}}
    out = fr.build_style(ref, use_llm=False)
    style = StyleSpec.model_validate(out["style"])
    assert style.format.aspect == "1:1" and style.format.resolution == "1080x1080" and style.format.fps == 60
    # `text_ratio` nunca foi medido (ramo morto, ADR 0004 §3.3): a legenda fica a do preset.
    assert style.edit.captions.style == load_presets()[out["base_style_id"]].edit.captions.style
    assert not [o for o in out["origins"] if o["path"].startswith("edit.captions.style")]


def test_grade_from_palette():
    assert fr.grade_for(fr.palette_traits(["#101010", "#202020", "#303030"]))[0] == "noir"
    assert fr.grade_for(fr.palette_traits(["#ff2020", "#20ff40", "#2040ff"]))[0] == "vivid"
    assert fr.grade_for(fr.palette_traits(["#300808", "#4a0c0c", "#200404"]))[0] == "blood-red"
    assert fr.palette_traits(["xyz"]) is None


def test_without_measures_fails():
    with pytest.raises(ValueError):
        fr.build_style({"title": "x", "info": {}, "stats": {}, "shots": []}, use_llm=False)


def test_llm_fills_only_unmeasured_fields():
    calls = []

    def fake(system, prompt, context):
        calls.append(prompt)
        return {"summary": "arte e câmera", "changes": [
            {"path": "art.bible", "value_json": json.dumps("filme de suspense, contraste alto"), "reason": "clima"},
            {"path": "edit.camera", "value_json": json.dumps({"punch_zoom": 2, "drift": 1}), "reason": "tensão"},
            {"path": "edit.shot_seconds", "value_json": json.dumps({"min": 5, "max": 9}), "reason": "lento"},
            {"path": "edit", "value_json": "{}", "reason": "tudo"},
            {"path": "edit.effects", "value_json": json.dumps([{"type": "grain", "amount": 0.3},
                                                               {"type": "grade", "preset": "vivid"}]), "reason": "grão"},
            {"path": "edit.zoom_strength", "value_json": "9", "reason": "inválido"},
        ]}, "fake_llm"

    out = fr.build_style(FAST_VERTICAL, llm=fake)
    style = StyleSpec.model_validate(out["style"])
    assert calls and "1.45" in calls[0] and "edit.shot_seconds" in calls[0]
    assert out["llm"] == {"used": True, "provider": "fake_llm", "summary": "arte e câmera", "message": ""}
    assert style.art.bible.startswith("filme de suspense")
    assert style.edit.camera == {"punch_zoom": 2, "drift": 1}
    # Campo medido não muda; a gradação medida volta mesmo que a IA troque os efeitos.
    assert style.edit.shot_seconds.min < 2
    assert [e.type for e in style.edit.effects] == ["grain", "grade"]
    assert style.edit.effects[1].preset == "cold-teal-dark"
    rejected = {r["path"] for r in out["rejected"]}
    assert rejected == {"edit.shot_seconds", "edit", "edit.zoom_strength"}
    assert {o["path"] for o in out["origins"] if o["source"] == "llm"} == {"art.bible", "edit.camera", "edit.effects"}


def test_llm_failure_falls_back_to_measures():
    def broken(*_a):
        raise RuntimeError("nenhum provedor de llm funcionou")

    out = fr.build_style(FAST_VERTICAL, llm=broken)
    assert out["llm"]["used"] is False and "Sem IA" in out["llm"]["message"]
    StyleSpec.model_validate(out["style"])
    assert fr.build_style(FAST_VERTICAL, llm=no_llm, use_llm=False)["llm"]["used"] is False


def test_offline_chain_works():
    # Nos testes a cadeia é o LLM offline (regras): não quebra e não mexe nas medidas.
    out = fr.build_style(FAST_VERTICAL)
    style = StyleSpec.model_validate(out["style"])
    assert out["llm"]["used"] is True and out["llm"]["provider"] == "mock_llm"
    assert style.edit.transition_seconds == 0.2


def test_create_saves_user_style_with_parent():
    first = fr.create_from_reference(FAST_VERTICAL, use_llm=False)
    second = fr.create_from_reference(FAST_VERTICAL, use_llm=False, name="Meu sombrio")
    assert first["style"]["id"] == "ref-trailer-sombrio" and second["style"]["id"] == "ref-trailer-sombrio-2"
    assert second["style"]["name"] == "Meu sombrio"
    item = next(i for i in list_styles() if i["style"]["id"] == "ref-trailer-sombrio")
    assert item["source"] == "user" and item["parent_id"] == first["base_style_id"]
    assert get_style("ref-trailer-sombrio").format.aspect == "9:16"


def test_route_creates_style():
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_pronto", title="Trailer Sombrio", info=FAST_VERTICAL["info"],
                             shots=FAST_VERTICAL["shots"], stats=FAST_VERTICAL["stats"], status="pronto"))
        s.add(ReferenceVideo(id="ref_novo", title="Novo", status="novo"))
    with TestClient(create_app()) as client:
        r = client.post("/api/references/ref_pronto/style", json={"use_llm": False})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["style"]["id"] == "ref-trailer-sombrio" and body["version"] == 1
        assert any(o["source"] == "ritmo" for o in body["origins"])
        assert client.get("/api/styles/ref-trailer-sombrio").status_code == 200
        assert client.post("/api/references/ref_novo/style", json={}).status_code == 409
        assert client.post("/api/references/nao_existe/style").status_code == 404


def test_llm_cannot_change_format_when_preset_already_matches():
    def fake(system, prompt, context):
        return {"summary": "virar Short", "changes": [
            {"path": "format.type", "value_json": json.dumps("short"), "reason": "x"},
            {"path": "format.duration", "value_json": json.dumps({"target_s": 30}), "reason": "x"},
        ]}, "fake_llm"

    out = fr.build_style(SLOW_HORIZONTAL, llm=fake)
    style = StyleSpec.model_validate(out["style"])
    assert style.format.aspect == "16:9" and style.format.type == "long"
    assert {r["path"] for r in out["rejected"]} == {"format.type", "format.duration"}
    # Nada da IA entrou: a tela avisa em vez de mostrar o resumo como se valesse.
    assert not [o for o in out["origins"] if o["source"] == "llm"]
    assert "não mudou nada" in out["llm"]["message"]


def test_fps_50_stays_50():
    ref = {**SLOW_HORIZONTAL, "info": {**SLOW_HORIZONTAL["info"], "fps": 50.0}}
    assert fr.build_style(ref, use_llm=False)["style"]["format"]["fps"] == 50
    ref = {**SLOW_HORIZONTAL, "info": {**SLOW_HORIZONTAL["info"], "fps": 59.94}}
    assert fr.build_style(ref, use_llm=False)["style"]["format"]["fps"] == 60


def test_cut_share_detail_matches_transitions_and_progress_color():
    presets = load_presets()
    base = next(p for p in presets.values() if p.format.aspect == "9:16")
    only_cut = base.model_copy(update={"edit": base.edit.model_copy(update={"transitions": {"cut": 1.0},
                                                                             "progress_bar": False})})
    out = fr.build_style(FAST_VERTICAL, presets={only_cut.id: only_cut}, use_llm=False)
    trans = next(o for o in out["origins"] if o["path"] == "edit.transitions")
    assert trans["value"] == {"cut": 1.0} and "100% de cortes secos" in trans["detail"]
    paths = {o["path"] for o in out["origins"]}
    assert "edit.progress_color" not in paths and "edit.text_cards.accent" in paths
    with_bar = only_cut.model_copy(update={"edit": only_cut.edit.model_copy(update={"progress_bar": True})})
    out = fr.build_style(FAST_VERTICAL, presets={with_bar.id: with_bar}, use_llm=False)
    assert "edit.progress_color" in {o["path"] for o in out["origins"]}


def test_create_does_not_overwrite_style_saved_meanwhile():
    from studio.core.styles.library import save_user_style

    def slow_llm(system, prompt, context):
        # Enquanto a IA "pensa", outro pedido salva um estilo com o mesmo id.
        other = fr.build_style(FAST_VERTICAL, use_llm=False, new_id="ref-trailer-sombrio", name="Outro")
        save_user_style(StyleSpec.model_validate(other["style"]))
        return {"summary": "", "changes": []}, "fake_llm"

    out = fr.create_from_reference(FAST_VERTICAL, llm=slow_llm)
    assert out["style"]["id"] == "ref-trailer-sombrio-2" and out["version"] == 1
    assert get_style("ref-trailer-sombrio").name == "Outro"


def test_probe_swaps_size_of_rotated_phone_video(monkeypatch):
    from pathlib import Path

    from studio.media import reference_video as rv

    def fake_probe(_path):
        return {"format": {"duration": "12.0"}, "streams": [{
            "codec_type": "video", "width": 1920, "height": 1080, "r_frame_rate": "30/1", "codec_name": "h264",
            "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}]}]}

    monkeypatch.setattr(rv, "ff_probe", fake_probe)
    info = rv.probe(Path("x.mp4"))
    assert (info["w"], info["h"]) == (1080, 1920)
    monkeypatch.setattr(rv, "ff_probe", lambda _p: {"format": {}, "streams": [{
        "codec_type": "video", "width": 1920, "height": 1080, "tags": {"rotate": "180"}}]})
    assert rv.probe(Path("x.mp4"))["w"] == 1920


# --- perfil de edição (ADR 0004 §3.3) ------------------------------------------------------------------------------

PROFILE = json.loads((Path(__file__).parent / "fixtures" / "perfil_gta.json").read_text(encoding="utf-8"))
# A referência GTA 6 (horizontal, 21 cortes/min) com o perfil medido no modo profundo.
GTA = {
    "id": "ref_x1l83ap549",
    "title": "Você vai zerar GTA 6",
    "info": {"w": 1920, "h": 1080, "fps": 60, "dur": 804.5},
    "shots": [],
    "stats": {"shots": 283, "median_s": 1.43, "mean_s": 2.73, "cuts_per_min": 21.0,
              "palette": ["#0d090a", "#392b29", "#685758", "#b0927c", "#dad5ce"], "profile": PROFILE},
}


def _profile(**over) -> EditProfile:
    return EditProfile.model_validate({**PROFILE, **over})


def test_profile_becomes_policy_without_ai_and_measured_grammar():
    out = fr.build_style(GTA, use_llm=False)
    style = StyleSpec.model_validate(out["style"])
    base = load_presets()[out["base_style_id"]]
    # Política sem IA (a referência não tem nenhuma) e a gramática medida.
    assert style.art.sources.mix["ai"] <= 0.05 and style.art.sources.ai_max_share <= 0.05
    assert style.art.sources.mix["official"] <= style.art.sources.official_max_share  # teto de Content ID
    assert sum(style.art.sources.mix.values()) == pytest.approx(1.0, abs=0.01)
    g = style.edit.grammar
    assert g.shot_median_s == pytest.approx(1.43)
    assert g.profile_refs == ["ref_x1l83ap549"]
    assert g.micro_per_min == 6.0 and g.punch_per_min == 4.2 and g.sfx_per_min == 7.5
    assert g.humor == "meme" and g.caption_emphasis == "pop"
    assert g.punch_scale.min == pytest.approx(1.15) and g.punch_scale.max == pytest.approx(1.25)
    assert g.hook is not None and g.hook.shot_median_s < g.shot_median_s  # gancho mais rápido que o vídeo
    assert g.sfx_palette == {"impact": 0.3, "whoosh": 0.35, "pop": 0.25, "riser": 0.1}
    # Campo marcado como padrão no meta (shake) não vale: fica o do preset.
    assert g.shake_per_min == base.edit.grammar.shake_per_min
    # Legenda: a fonte livre mais próxima (Pricedown nunca), palavra a palavra, caixa alta.
    caps = style.edit.captions
    assert caps.font == "Anton" and "pricedown" not in json.dumps(out["style"]).lower()
    assert caps.style == "word" and caps.uppercase is True and caps.max_words_per_line == 2
    assert caps.weight == 400  # o peso que a Anton tem
    assert style.audio.sfx.auto is True
    assert style.script.words_per_minute[style.languages.primary] == 172
    origins = {o["path"]: o for o in out["origins"]}
    assert origins["edit.grammar"]["source"] == "perfil" and "1.43" in origins["edit.grammar"]["detail"]
    assert origins["art.sources"]["source"] == "perfil" and "IA em 0%" in origins["art.sources"]["detail"]
    assert "perfil" in fr.SOURCE_LABELS and "texto" not in fr.SOURCE_LABELS
    # facecam + mascote = 15% da referência: sugere ligar o apresentador.
    assert len(out["suggestions"]) == 1 and "apresentador" in out["suggestions"][0]


def test_without_profile_keeps_current_behavior():
    out = fr.build_style(FAST_VERTICAL, use_llm=False)
    style = StyleSpec.model_validate(out["style"])
    base = load_presets()[out["base_style_id"]]
    assert style.edit.grammar == base.edit.grammar and style.art.sources == base.art.sources
    assert style.edit.grammar.profile_refs == []
    assert not [o for o in out["origins"] if o["source"] == "perfil"] and out["suggestions"] == []
    # Perfil inválido é ignorado (não derruba a criação do estilo).
    broken = {**FAST_VERTICAL, "stats": {**FAST_VERTICAL["stats"], "profile": {"pace": {"median_s": -1}}}}
    assert fr.build_style(broken, use_llm=False)["style"]["edit"]["grammar"] == style.edit.grammar.model_dump(
        mode="json")


def test_quick_profile_keeps_policy_and_only_measures_pace():
    quick = {k: v for k, v in PROFILE.items() if k not in ("meta",)}
    quick = {**quick, "quick": True}
    ref = {**GTA, "stats": {**GTA["stats"], "profile": quick}}
    out = fr.build_style(ref, use_llm=False)
    style = StyleSpec.model_validate(out["style"])
    base = load_presets()[out["base_style_id"]]
    # Sem visão, a mistura de materiais não vale: a política fica a do preset.
    assert style.art.sources == base.art.sources
    assert "art.sources" not in {o["path"] for o in out["origins"]}
    # O ritmo, a câmera e o áudio valem (modo rápido mede), com confiança assumida 0,5: tolerância maior.
    g = style.edit.grammar
    assert g.shot_median_s == pytest.approx(1.43) and g.punch_per_min == 4.2
    assert g.text_pop_per_min == base.edit.grammar.text_pop_per_min  # sobreposição é visão
    assert g.tolerance > 0.35
    assert style.edit.captions.font == base.edit.captions.font
    assert style.script.words_per_minute == base.script.words_per_minute  # fala não medida
    assert out["suggestions"] == []


def test_tolerance_grows_with_low_confidence():
    high = {"pace": {"confidence": 1.0, "origin": "medido"}}
    low = {"pace": {"confidence": 0.2, "origin": "medido"}}
    pace = {"shots": 10, "median_s": 2.0}
    g_high = gr.grammar_from_profile(EditProfile.model_validate({"pace": pace, "meta": high})).grammar
    g_low = gr.grammar_from_profile(EditProfile.model_validate({"pace": pace, "meta": low})).grammar
    assert g_high.tolerance == pytest.approx(EditGrammar().tolerance)
    assert g_low.tolerance > g_high.tolerance
    # Perfil vazio não mexe em nada (nem na tolerância).
    empty = gr.grammar_from_profile(EditProfile())
    assert empty.grammar == EditGrammar() and empty.sources == SourcePolicy() and not empty.grammar_fields
    assert empty.captions == {} and empty.sfx == {}


def test_ai_floor_and_material_mapping():
    p = _profile(materials={"mix": {"ai_illustration": 0.3, "stock_video": 0.5, "text_card": 0.2}})
    pg = gr.grammar_from_profile(p)
    assert pg.sources.mix == pytest.approx({"ai": 0.3, "stock": 0.5, "graphic": 0.2})
    assert pg.sources.ai_max_share == pytest.approx(0.3)
    assert not pg.suggest_presenter
    # Sem IA na referência, o teto fica no piso de 5%.
    none = gr.grammar_from_profile(_profile(materials={"mix": {"photo_real": 1.0}}))
    assert none.sources.ai_max_share == 0.05 and none.sources.mix == {"free": 1.0, "ai": 0.0}


def test_match_font_is_free_and_never_pricedown():
    assert gr.match_font("Pricedown") == "Anton"
    assert gr.match_font("serifada clássica, romana") == "Cinzel"
    assert gr.match_font("máquina de escrever") == "Special Elite"
    assert gr.match_font("pixel retrô de terminal") == "VT323"
    assert gr.match_font("Montserrat") == "Montserrat"
    assert gr.match_font("") is None and gr.match_font("xyzzy") is None
    catalog = gr.font_catalog()
    assert catalog and "Pricedown" not in catalog
    assert all(str(info["license"]).startswith(("OFL", "Apache")) for info in catalog.values())


def test_llm_sees_profile_script_and_voice_but_not_measured_fields():
    seen = {}

    def fake(system, prompt, context):
        seen["prompt"], seen["system"] = prompt, system
        return {"summary": "tom", "changes": [
            {"path": "script.tone", "value_json": json.dumps("conversado, irônico e empolgado"), "reason": "perfil"},
            {"path": "edit.grammar.punch_per_min", "value_json": "1", "reason": "x"},
            {"path": "art.sources", "value_json": json.dumps({"ai_max_share": 1.0}), "reason": "x"},
            {"path": "edit.captions.font", "value_json": json.dumps("Cinzel"), "reason": "x"},
            {"path": "script.words_per_minute", "value_json": json.dumps({"pt-BR": 120}), "reason": "x"},
        ]}, "fake_llm"

    out = fr.build_style(GTA, llm=fake)
    style = StyleSpec.model_validate(out["style"])
    prompt = seen["prompt"]
    assert "Perfil de edição medido" in prompt and "empolgado" in prompt and "172" in prompt
    assert '"script"' in prompt and '"voice"' in prompt
    assert "quinto padrão" not in prompt  # só 4 padrões de humor
    assert "nunca copie frases" in seen["system"]
    assert style.script.tone == "conversado, irônico e empolgado"
    assert style.edit.grammar.punch_per_min == 4.2 and style.art.sources.ai_max_share <= 0.05
    assert style.edit.captions.font == "Anton" and style.script.words_per_minute["pt-BR"] == 172
    assert {r["path"] for r in out["rejected"]} == {"edit.grammar.punch_per_min", "art.sources",
                                                    "edit.captions.font", "script.words_per_minute"}


def test_route_with_profile_points_grammar_to_the_reference():
    stats = GTA["stats"]
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_gta", title="GTA 6", info=GTA["info"], shots=[], stats=stats, status="pronto"))
    with TestClient(create_app()) as client:
        r = client.post("/api/references/ref_gta/style", json={"use_llm": False})
        assert r.status_code == 200, r.text
        body = r.json()
    assert body["style"]["edit"]["grammar"]["profile_refs"] == ["ref_gta"]
    assert body["suggestions"] and any(o["source"] == "perfil" for o in body["origins"])


# --- presets ------------------------------------------------------------------------------------------------------

PRESET_FILES = sorted((REPO_ROOT / "presets" / "styles").glob("*.yaml"))


@pytest.mark.parametrize("path", PRESET_FILES, ids=lambda p: p.stem)
def test_presets_declare_sources_and_grammar_by_family(path):
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert "sources" in data["art"] and "grammar" in data["edit"]
    style = StyleSpec.model_validate(data)
    policy = style.art.sources
    assert sum(policy.mix.values()) == pytest.approx(1.0)
    assert policy.mix["ai"] <= policy.ai_max_share + 1e-9
    assert policy.mix.get("official", 0) <= policy.official_max_share
    assert style.edit.grammar.profile_refs == []
    expected_ai = {"colagem-artesanal": 0.5, "whiteboard-explicador": 0.5, "explicador-vetorial": 0.5,
                   "games-cultura-pop-short": 0.10, "dados-e-mapas": 0.15}
    assert policy.ai_max_share == expected_ai.get(style.id, 0.20)
    # Grão e vinheta só no material de arquivo; o terror analógico é a exceção (a textura é o estilo).
    assert style.edit.grammar.effects_policy == ("global" if style.id == "terror-analog" else "per_tier")


def test_family_specific_presets():
    presets = load_presets()
    games = presets["games-cultura-pop-short"]
    assert games.edit.grammar.humor == "meme" and sources_for(games).ai_max_share == 0.10
    assert grammar_for(games).shot_median_s == 1.4  # valor escolhido: o Short não troca
    dados = presets["dados-e-mapas"].art.sources.mix
    assert max(dados, key=dados.get) == "graphic"
    # Short com o teto padrão (0,20) continua virando 0,30.
    assert sources_for(presets["misterios-short"]).ai_max_share == 0.30


# --- roteiro ------------------------------------------------------------------------------------------------------


def test_script_prompt_has_reference_block_only_with_profile(monkeypatch):
    from studio.core.project import service
    from studio.core.styles.library import save_user_style
    from studio.providers.base import LLMResult, Usage
    from studio.workers.pipeline import authoring

    calls = []

    def fake_llm(task, system, prompt, schema, context, project_id, job_id):
        calls.append({"prompt": prompt, "context": context})
        data = {"title": "GTA", "sources": [], "segments": [
            {"role": "hook", "speaker": "narrator", "chapter": None, "number": None, "text": "Olha isso."}]}
        return LLMResult(data=data, text="", usage=Usage()), "fake_llm"

    monkeypatch.setattr(authoring, "_llm", fake_llm)
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_x1l83ap549", title="GTA 6", info=GTA["info"], shots=[], stats=GTA["stats"],
                             status="pronto"))
    style = StyleSpec.model_validate(fr.build_style(GTA, use_llm=False, new_id="ref-gta")["style"])
    save_user_style(style)
    doc = service.create_project(title="GTA", style_id="ref-gta", topic="o trailer do GTA 6")

    authoring.generate_script(doc, style)
    prompt = calls[-1]["prompt"]
    assert "Voz e estrutura da referência" in prompt
    assert "172 palavras por minuto" in prompt and "primeira pessoa" in prompt
    assert "empolgado, conversado, irônico" in prompt
    assert "hook (0–4%" in prompt and "fechamento" in prompt
    assert "autoironia sobre a espera" in prompt and "quinto padrão" not in prompt
    assert "nenhuma frase da referência" in prompt
    assert calls[-1]["context"]["reference_voice"]

    # Estilo de preset (sem profile_refs): nenhum bloco.
    authoring.generate_script(doc, get_style("curiosidades-longo"))
    assert "Voz e estrutura da referência" not in calls[-1]["prompt"]
    assert "reference_voice" not in calls[-1]["context"]
    # profile_refs apontando para referência sem perfil (ou apagada): nenhum bloco, sem erro.
    orphan = style.model_copy(update={"edit": style.edit.model_copy(update={
        "grammar": style.edit.grammar.model_copy(update={"profile_refs": ["ref_sumiu"]})})})
    authoring.generate_script(doc, orphan)
    assert "Voz e estrutura da referência" not in calls[-1]["prompt"]


def test_quick_profile_gives_no_script_block():
    quick = EditProfile.model_validate({"quick": True, "duration_s": 60, "pace": {"shots": 20, "median_s": 2.0}})
    assert gr.script_reference_block([quick]) == ""
    assert gr.script_reference_block([]) == ""
    block = gr.script_reference_block([_profile()])
    assert block.count("\n  - ") == 4  # 4 padrões de humor, no máximo
