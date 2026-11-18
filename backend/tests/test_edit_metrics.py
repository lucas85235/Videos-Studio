"""Métricas de "cara de IA" (ADR 0004 §3.10 e §6): impressão digital da timeline, índice, aderência ao perfil,
itens `perfil.*` da conferência, rota `edit-metrics` e o CLI só de leitura. Sem rede e sem FFmpeg."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from compat_fixture import FIXTURE, _clips
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.editing import metrics
from studio.core.editing.profile import EditFingerprint, EditProfile, FieldMeta, PaceBlock
from studio.core.project import service
from studio.core.project.document import ProjectDoc, Scene, Segment, Visual
from studio.core.styles.library import get_style
from studio.db import readonly_engine, reset_engine, session_scope
from studio.models import QualityReport
from studio.workers.finalcheck import profile_check
from studio.workers.finalcheck.quick import EDIT_CODES, edit_items, load_context

LANG = "pt-BR"
FPS = 30


def _scene(sid: str, frm: float, dur: float, *, prov: str, motion: str = "static", kind: str = "image",
           src: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"id": sid, "kind": kind, "from": round(frm * FPS), "durationInFrames": round(dur * FPS), "motion": motion,
            "transition": extra.pop("transition", "cut"), "prov": prov, "src": src or f"cache/images/{sid}.jpg",
            "text": "", "captions": [], "words": [], **extra}


def _timeline(scenes: list[dict[str, Any]], sfx: list[dict[str, Any]] | None = None, **extra: Any) -> dict[str, Any]:
    for a, b in zip(scenes, scenes[1:], strict=False):  # cenas contínuas, como a timeline garante
        a["durationInFrames"] = b["from"] - a["from"]
    total = scenes[-1]["from"] + scenes[-1]["durationInFrames"]
    return {"version": 1, "lang": LANG, "fps": FPS, "width": 1920, "height": 1080, "durationInFrames": total,
            "duration": total / FPS, "scenes": scenes, "sfx": sfx or [], **extra}


def slideshow_timeline() -> dict[str, Any]:
    """60 s de imagem de IA, uma por frase de 3 s, Ken Burns em tudo e só o whoosh das transições."""
    scenes, sfx = [], []
    for i in range(20):
        transition = "flash" if i and i % 4 == 0 else "cut"
        scenes.append(_scene(f"scn_{i:02d}", i * 3.0, 3.0, prov="ai",
                             motion="ken_burns_in" if i % 2 else "ken_burns_out", transition=transition))
        if transition == "flash":
            sfx.append({"type": "whoosh", "start": round(i * 3.0 - 0.35, 3), "scene_id": f"scn_{i:02d}"})
    return _timeline(scenes, sfx)


REAL_DURS = [0.6, 2.4, 1.1, 5.2, 0.9, 3.8, 1.6, 6.4, 1.2, 2.9, 0.7, 4.6, 1.8, 2.2, 7.0, 1.0, 3.1, 1.4, 5.8, 2.0,
             0.8, 4.0, 1.3]
REAL_PROVS = ["official", "capture", "free", "official", "graphic", "capture", "ai", "official", "free", "stock",
              "capture", "official", "own", "graphic", "official", "capture", "ai", "free", "official", "capture",
              "graphic", "official", "free"]


def real_first_timeline() -> dict[str, Any]:
    """Real primeiro: planos de 0,6 a 7 s (CV ~0,7), troca de fonte a cada plano, punch, texto e SFX de evento."""
    scenes, sfx, t = [], [], 0.0
    for i, (dur, prov) in enumerate(zip(REAL_DURS, REAL_PROVS, strict=True)):
        fx = [{"type": "punch", "from": 6, "dur": 8, "params": {"scale": 1.2}}] if dur > 1.5 else []
        if dur > 3:
            fx.append({"type": "text_pop", "from": round(dur * FPS / 2), "dur": 20, "params": {"text": "X"}})
        scenes.append(_scene(f"scn_{i:02d}", t, dur, prov=prov, motion="push" if i % 3 == 0 else "static",
                             kind="title" if prov == "graphic" else "image", fx=fx))
        if i % 5 == 1:
            sfx.append({"type": "impact", "at": round(t + 0.2, 3), "scene": f"scn_{i:02d}"})
        t += dur
    return _timeline(scenes, sfx)


@pytest.fixture
def longo():
    return get_style("curiosidades-longo")


def test_slideshow_of_ai_scores_above_70(longo):
    m = metrics.measure_timeline(slideshow_timeline())
    fp = m.fp
    assert fp.materials.prov_mix == {"ai": 1.0}
    assert fp.pace.shots == 20 and fp.pace.cv == 0 and fp.camera.kb_share == 1.0
    assert fp.audio.sfx_per_min == 0  # só os da transição: não contam
    assert fp.hook_prov == "ai" and fp.materials.switch_per_min == 0
    index = metrics.ai_look_index(fp, longo)
    assert index.score > 70 and index.band == "vermelho"
    assert sum(p.weight for p in index.parts) == 100
    parts = {p.key: p for p in index.parts}
    assert parts["ai_screen_share"].penalty == 1 and parts["kb_share"].penalty == 1


def test_real_first_with_cv_07_scores_below_30(longo):
    fp = metrics.measure_timeline(real_first_timeline()).fp
    assert 0.6 <= fp.pace.cv <= 0.8
    assert fp.materials.prov_mix["ai"] <= 0.2
    assert fp.hook_prov == "capture"  # 0,6 s de oficial e 2,4 s de captura nos 3 s iniciais
    assert fp.camera.punch_per_min > 0 and fp.overlays.text_pop_per_min > 0
    assert 2 <= fp.audio.sfx_per_min <= 5
    index = metrics.ai_look_index(fp, longo)
    assert index.score < 30 and index.band == "verde"


def test_adherence_is_100_for_its_own_profile():
    fp = metrics.measure_timeline(real_first_timeline()).fp
    profile = EditProfile(pace=fp.pace, camera=fp.camera, materials=fp.materials, overlays=fp.overlays, audio=fp.audio)
    result = metrics.adherence(fp, profile)
    assert result.score == 100
    assert {p.field for p in result.parts} >= {"pace.median_s", "pace.cv", "materials.prov_mix"}
    # Perfil com `meta`: entra só o que foi medido.
    measured = profile.model_copy(update={"meta": {"pace.median_s": FieldMeta(confidence=0.9, origin="medido")}})
    assert [p.field for p in metrics.adherence(fp, measured).parts] == ["pace.median_s"]
    # Longe do perfil, a nota cai.
    slide = metrics.measure_timeline(slideshow_timeline()).fp
    assert metrics.adherence(slide, profile).score < 50
    assert metrics.adherence(fp, EditProfile()).score is None


def test_profile_targets_replace_grammar(longo):
    profile = EditProfile(pace=PaceBlock(median_s=1.4, cv=0.9, events_per_min=30.0))
    t = metrics.targets_for(longo, profile)
    assert t.shot_median_s == 1.4 and t.events_min == 30.0
    assert set(t.from_profile) == {"pace.median_s", "pace.cv", "pace.events_per_min"}
    short = metrics.targets_for(get_style("misterios-short"))
    assert short.short and short.ai_max_share == 0.30 and short.switch_min == 4.0 and short.max_still_s == 3.0


def test_new_contract_shots_fx_and_sfx():
    """Subplanos dividem a cena; fx visuais quebram o trecho parado; SFX no formato novo (`at`, `scene`)."""
    scene = _scene("scn_a", 0, 10.0, prov="ai", motion="ken_burns_in", shots=[
        {"from": 0, "dur": 60, "prov": "official", "video": "cache/clip.mp4", "framing": {"scale": 1, "x": .5, "y": .5},
         "motion": "none"},
        {"from": 60, "dur": 90, "prov": "capture", "src": "cache/cap.jpg", "framing": {"scale": 1.2, "x": .4, "y": .5},
         "motion": "static"},
        {"from": 150, "dur": 150, "prov": "ai", "framing": {"scale": 1, "x": .5, "y": .5}, "motion": "push"},
    ], fx=[{"type": "punch", "from": 200, "dur": 8, "params": {"scale": 1.25}},
           {"type": "sfx", "from": 200, "dur": 10, "params": {}}])
    tl = _timeline([scene], [{"type": "boom", "at": 2.0, "scene": "scn_a", "lib_id": "boom-01"}],
                   music_drops=[{"at": 6.0, "dur": 0.4}])
    m = metrics.measure_timeline(tl)
    assert [(s.prov, round(s.dur, 2)) for s in m.shots] == [("official", 2.0), ("capture", 3.0), ("ai", 5.0)]
    fp = m.fp
    assert fp.pace.shots == 3 and fp.materials.switch_per_min == 12.0
    assert fp.camera.punch_per_min == 6.0 and fp.camera.punch_scale == 0.25
    assert fp.pace.events_per_min == 18.0  # 2 cortes + 1 punch (o fx de som não é evento visual)
    assert fp.pace.max_still_s == pytest.approx(10 - 200 / FPS, abs=0.01)  # do punch (6,67 s) ao fim
    assert fp.audio.sfx_per_min == 6.0 and fp.audio.sfx_on_cut_share == 1.0 and fp.audio.sfx_classes == {"impact": 1.0}
    assert fp.audio.drops_per_min == 6.0
    assert fp.hook_prov == "official"  # nos 3 s iniciais: 2 s de oficial, 1 s de captura
    strip = metrics.prov_strip(tl)
    assert [s["prov"] for s in strip] == ["official", "capture", "ai"]


def test_legacy_timeline_takes_prov_from_doc():
    """Timeline antiga (sem `prov`): o nível sai de `sources.prov_of_scene` no documento."""
    from studio.core.timeline import build_timeline

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc = ProjectDoc.model_validate(data["doc"])
    style = get_style("curiosidades-longo")
    images = {s.id: f"cache/images/{s.id}.jpg" for s in doc.scenes if s.kind in ("image", "video")}
    tl = build_timeline(doc, style, doc.primary_language, _clips(doc), images)
    assert not any("prov" in s for s in tl["scenes"])
    fp = metrics.fingerprint_from_timeline(tl, doc)
    assert fp.pace.shots == len(doc.scenes)
    mix = fp.materials.prov_mix
    assert {"ai", "free", "capture", "official", "graphic"} <= set(mix)
    assert sum(mix.values()) == pytest.approx(1.0, abs=0.01)
    assert fp.hook_prov == "ai"
    flat = metrics.flat_metrics(fp)
    assert flat["hook_real"] is False and 0 < flat["real_share"] < 1


def _project_with_storyboard(isolated_settings, style_id: str = "curiosidades-longo") -> ProjectDoc:
    doc = service.create_project(title="GTA", style_id=style_id)
    texts = ["Em 2013 um jogo vendeu milhões de cópias e ninguém esperava isso.",
             "A cidade inteira foi desenhada a partir de fotos reais de Los Angeles."]
    segments = [Segment(id=f"seg_{i}", text={LANG: t}) for i, t in enumerate(texts)]
    scenes = [Scene(id=f"scn_{i}{k}", segment_id=f"seg_{i}", part=k, parts=2, visual=Visual(prompt="cena"))
              for i in range(2) for k in range(2)]
    doc = doc.model_copy(update={"segments": segments, "scenes": scenes})
    service.commit(doc.id, doc, "storyboard")
    return service.get_doc(doc.id)


def test_route_estimates_without_timeline_and_uses_matching_preview(isolated_settings):
    doc = _project_with_storyboard(isolated_settings)
    client = TestClient(create_app())
    res = client.get(f"/api/projects/{doc.id}/edit-metrics", params={"lang": LANG})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["source"] == "estimate" and body["metrics"]["ai_screen_share"] == 1.0
    assert body["index"]["score"] > 55 and body["strip"][0]["prov"] == "ai"
    assert {i["code"] for i in body["items"]} == set(EDIT_CODES)
    assert body["report"] is None and body["adherence"] is None
    # A prévia que bate com as cenas do documento vale mais que a estimativa.
    tl = profile_check.estimate_timeline(doc, get_style("curiosidades-longo"), LANG)
    out = isolated_settings.outputs_dir / doc.id / LANG
    out.mkdir(parents=True)
    (out / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    assert client.get(f"/api/projects/{doc.id}/edit-metrics").json()["source"] == "preview"
    assert client.get(f"/api/projects/{doc.id}/edit-metrics", params={"source": "final"}).status_code == 409
    assert client.get("/api/projects/nada/edit-metrics").status_code == 404


def test_edit_items_point_to_time_and_scene(longo):
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc = ProjectDoc.model_validate(data["doc"])
    tl = slideshow_timeline()
    items = edit_items(tl, doc, longo)
    by_code: dict[str, list[dict[str, Any]]] = {}
    for it in items:
        by_code.setdefault(it["code"], []).append(it)
    assert set(by_code) == set(EDIT_CODES)
    ia = by_code["perfil.ia"][0]
    assert ia["level"] == "aviso" and ia["at_s"] == 0.0 and ia["scene_id"] == "scn_00"
    assert any(i["title"].startswith("O gancho") for i in by_code["perfil.material"])
    assert by_code["perfil.ritmo"][0]["level"] == "aviso"
    assert all(i["level"] in ("ok", "aviso") for i in items)  # edição nunca trava o envio
    ok = edit_items(real_first_timeline(), doc, longo)
    assert {i["code"]: i["level"] for i in ok}["perfil.ia"] == "ok"


def test_run_edit_check_writes_edit_report_with_video_audit(isolated_settings, monkeypatch):
    doc = _project_with_storyboard(isolated_settings)
    tl = profile_check.estimate_timeline(doc, get_style("curiosidades-longo"), LANG)
    out = isolated_settings.outputs_dir / doc.id / LANG
    out.mkdir(parents=True)
    (out / "final.mp4").write_bytes(b"mp4 falso")
    (out / "timeline-final.json").write_text(json.dumps(tl), encoding="utf-8")
    import os
    import time
    os.utime(out / "final.mp4", (time.time() + 5, time.time() + 5))
    rel = f"outputs/{doc.id}/{LANG}"
    service.update_settings(doc.id, {"last_final": {"langs": {LANG: {
        "video": f"{rel}/final.mp4", "timeline": f"{rel}/timeline-final.json"}}}})
    audited = EditFingerprint(source="video", pace=PaceBlock(shots=9, median_s=1.2, cv=0.8, max_still_s=2.0))
    seen: list[Path] = []
    monkeypatch.setattr(profile_check, "measure_render", lambda path: seen.append(path) or audited)
    report = profile_check.run_for_project(doc.id, LANG)
    assert report is not None and report["kind"] == "edit"
    assert seen and seen[0].name == "final.mp4"
    assert report["data"]["video_measured"] is True
    assert report["data"]["fingerprint"]["pace"]["median_s"] == 1.2  # o ritmo vem do MP4
    assert report["data"]["fingerprint"]["materials"]["prov_mix"] == {"ai": 1.0}  # a fonte vem da timeline
    codes = {i["code"] for i in report["items"]}
    assert codes == set(EDIT_CODES)
    with session_scope() as s:
        assert s.query(QualityReport).filter_by(project_id=doc.id, kind="edit").count() == 1
    body = TestClient(create_app()).get(f"/api/projects/{doc.id}/edit-metrics").json()
    assert body["source"] == "final" and body["report"]["id"] == report["id"]


def test_measure_render_without_refprofile_is_none(tmp_path):
    """Até o merge de `ref-perfil` não há extrator: a conferência fica só com a timeline."""
    try:
        import studio.media.refprofile  # noqa: F401
    except ImportError:
        assert profile_check.measure_render(tmp_path / "x.mp4") is None
    else:
        pytest.skip("refprofile já existe")


def _digest(data_dir: Path) -> dict[str, str]:
    out = {}
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and not path.name.endswith("-shm"):
            out[path.relative_to(data_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_cli_reads_without_writing(isolated_settings, capsys):
    from studio.cli import main

    doc = _project_with_storyboard(isolated_settings)
    tl = profile_check.estimate_timeline(doc, get_style("curiosidades-longo"), LANG)
    out = isolated_settings.outputs_dir / doc.id / LANG
    out.mkdir(parents=True)
    (out / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    reset_engine()  # fecha as conexões (checkpoint do WAL) antes de tirar a foto dos arquivos
    before = _digest(isolated_settings.data_dir)
    main(["edit-metrics", "--all"])
    text = capsys.readouterr().out
    assert doc.id in text and "Cara de IA:" in text and "última prévia" in text
    main(["edit-metrics", doc.id, "--json", "--fonte", "estimate"])
    rows = json.loads(capsys.readouterr().out)
    assert rows[0]["source"] == "estimate" and rows[0]["index"]["score"] > 55
    reset_engine()
    assert _digest(isolated_settings.data_dir) == before
    with pytest.raises(SystemExit):
        main(["edit-metrics", "prj_nada"])


def test_readonly_engine_refuses_writes(isolated_settings):
    doc = _project_with_storyboard(isolated_settings)
    with readonly_engine(), pytest.raises(Exception):  # noqa: B017 - o SQLite recusa com OperationalError
        service.update_settings(doc.id, {"x": 1})
    assert service.update_settings(doc.id, {"x": 1})["x"] == 1  # o engine normal volta depois do bloco


def test_load_context_used_by_metrics_is_the_final_one(isolated_settings):
    doc = _project_with_storyboard(isolated_settings)
    ctx = load_context(doc.id, LANG)
    timeline, source = profile_check.metrics_timeline(ctx)
    assert source == "estimate" and timeline["scenes"]
