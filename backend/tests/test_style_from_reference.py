"""Estilo a partir do vídeo de referência: medidas sintéticas → StyleSpec válido, com e sem LLM (simulado)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.styles import from_reference as fr
from studio.core.styles.library import get_style, list_styles, load_presets
from studio.core.styles.schema import StyleSpec
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


def test_aspect_change_and_text_presence():
    ref = {**FAST_VERTICAL, "info": {"w": 1080, "h": 1080, "fps": 60}, "stats": {**FAST_VERTICAL["stats"], "text_ratio": 0.6}}
    style = StyleSpec.model_validate(fr.build_style(ref, use_llm=False)["style"])
    assert style.format.aspect == "1:1" and style.format.resolution == "1080x1080" and style.format.fps == 60
    assert style.edit.captions.style == "karaoke"
    ref["stats"] = {**ref["stats"], "text_ratio": 0.02}
    assert fr.build_style(ref, use_llm=False)["style"]["edit"]["captions"]["style"] == "line"


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
