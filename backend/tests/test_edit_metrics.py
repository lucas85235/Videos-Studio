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
    assert 0.6 <= fp.pace.cv <= 0.9  # CV robusto (lognormal, `cuts.robust_cv`): ~0,85 nestes planos
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


def _project_with_storyboard(isolated_settings, style_id: str = "curiosidades-longo",
                             overrides: dict[str, Any] | None = None) -> ProjectDoc:
    doc = service.create_project(title="GTA", style_id=style_id)
    texts = ["Em 2013 um jogo vendeu milhões de cópias e ninguém esperava isso.",
             "A cidade inteira foi desenhada a partir de fotos reais de Los Angeles."]
    segments = [Segment(id=f"seg_{i}", text={LANG: t}) for i, t in enumerate(texts)]
    scenes = [Scene(id=f"scn_{i}{k}", segment_id=f"seg_{i}", part=k, parts=2, visual=Visual(prompt="cena"))
              for i in range(2) for k in range(2)]
    doc = doc.model_copy(update={"segments": segments, "scenes": scenes, "style_overrides": overrides or {}})
    service.commit(doc.id, doc, "storyboard")
    return service.get_doc(doc.id)


def _current(project_id: str) -> int:
    return int(service.get_project_row(project_id)["current_version"])


def _write_preview(settings, doc: ProjectDoc, tl: dict[str, Any] | None = None) -> dict[str, Any]:
    """A prévia como o build grava: `timeline.json` com a versão do projeto."""
    tl = tl or profile_check.estimate_timeline(doc, get_style("curiosidades-longo"), LANG)
    tl = {**tl, "version": _current(doc.id)}
    out = settings.outputs_dir / doc.id / LANG
    out.mkdir(parents=True, exist_ok=True)
    (out / "timeline.json").write_text(json.dumps(tl), encoding="utf-8")
    return tl


def _write_final(settings, doc: ProjectDoc, tl: dict[str, Any] | None = None, *, video_age_s: float = 0.0) -> None:
    """O vídeo final como o build grava: MP4 + `timeline-final.json` com a versão, e o `last_final`. `video_age_s`
    negativo deixa o MP4 mais novo que a timeline (o normal); positivo, a timeline mais nova (a prévia regravou)."""
    import os
    import time

    tl = tl or profile_check.estimate_timeline(doc, get_style("curiosidades-longo"), LANG)
    tl = {**tl, "version": _current(doc.id)}
    out = settings.outputs_dir / doc.id / LANG
    out.mkdir(parents=True, exist_ok=True)
    (out / "final.mp4").write_bytes(b"mp4 falso")
    (out / "timeline-final.json").write_text(json.dumps(tl), encoding="utf-8")
    stamp = time.time() - video_age_s
    os.utime(out / "final.mp4", (stamp, stamp))
    rel = f"outputs/{doc.id}/{LANG}"
    service.update_settings(doc.id, {"last_final": {"langs": {LANG: {
        "video": f"{rel}/final.mp4", "timeline": f"{rel}/timeline-final.json"}}}})


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
    assert body["source_text"] == "Estimado pelo roteiro" and body["outdated"] is False
    assert body["ai_scene_ids"] == [s.id for s in doc.ordered_scenes()]
    # A prévia que bate com as cenas do documento vale mais que a estimativa.
    _write_preview(isolated_settings, doc)
    preview = client.get(f"/api/projects/{doc.id}/edit-metrics").json()
    assert preview["source"] == "preview" and preview["source_text"] == "Medido na última prévia"
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


def _final_render(isolated_settings, overrides: dict[str, Any] | None = None) -> ProjectDoc:
    doc = _project_with_storyboard(isolated_settings, overrides=overrides)
    _write_final(isolated_settings, doc)
    return doc


def test_run_edit_check_skips_the_mp4_audit_without_reference_profile(isolated_settings, monkeypatch):
    """Sem perfil de referência no estilo, a conferência mede pela timeline do render: o scan do MP4 (minutos num
    vídeo longo) não roda."""
    doc = _final_render(isolated_settings)
    seen: list[Path] = []
    monkeypatch.setattr(profile_check, "measure_render", lambda path: seen.append(path))
    report = profile_check.run_for_project(doc.id, LANG)
    assert report is not None and report["kind"] == "edit"
    assert seen == [] and report["data"]["video_measured"] is False


def test_run_edit_check_writes_edit_report_with_video_audit(isolated_settings, monkeypatch):
    doc = _project_with_storyboard(isolated_settings,
                                   overrides={"edit": {"grammar": {"profile_refs": ["ref_inexistente"]}}})
    _write_final(isolated_settings, doc)
    audited = EditFingerprint(source="video", pace=PaceBlock(shots=9, median_s=1.2, cv=0.8, max_still_s=2.0))
    seen: list[Path] = []
    monkeypatch.setattr(profile_check, "measure_render", lambda path: seen.append(path) or audited)
    report = profile_check.run_for_project(doc.id, LANG)
    assert report is not None and report["kind"] == "edit"
    assert seen and seen[0].name == "final.mp4"
    assert report["data"]["video_measured"] is True
    assert report["data"]["fingerprint"]["pace"]["median_s"] == 1.2  # o ritmo vem do MP4
    assert report["data"]["fingerprint"]["materials"]["prov_mix"] == {"ai": 1.0}  # a fonte vem da timeline
    # O áudio fica o da timeline (exato, sem a transição); o MP4 só confere o ritmo (`perfil.render`).
    assert report["data"]["fingerprint"]["audio"] == report["data"]["timeline_fingerprint"]["audio"]
    codes = {i["code"] for i in report["items"]}
    assert codes == set(EDIT_CODES) | {profile_check.RENDER_CODE}
    with session_scope() as s:
        assert s.query(QualityReport).filter_by(project_id=doc.id, kind="edit").count() == 1
    body = TestClient(create_app()).get(f"/api/projects/{doc.id}/edit-metrics").json()
    assert body["source"] == "final" and body["report"]["id"] == report["id"]
    # Vídeo final em dia com a auditoria: os números mostrados são os dela.
    assert body["measured_from"] == "render" and body["index"] == report["data"]["index"]
    assert body["fingerprint"]["pace"]["median_s"] == 1.2 and body["source_text"] == "Medido no vídeo final"


def test_measure_render_uses_the_reference_extractor(isolated_settings, tmp_path):
    """O MP4 final é medido pelo mesmo extrator da referência (`refprofile.measure_video`); arquivo que não mede
    deixa a conferência só com a timeline."""
    from studio.media.ffmpeg import run_ffmpeg

    assert profile_check.measure_render(tmp_path / "nao-existe.mp4") is None
    video = tmp_path / "final.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=2",
                "-f", "lavfi", "-i", "color=c=0x2060c0:size=320x180:rate=30:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(video)], timeout=120)
    fp = profile_check.measure_render(video)
    assert isinstance(fp, EditFingerprint) and fp.source == "video"
    assert fp.pace.shots == 2 and fp.duration_s == pytest.approx(4.0, abs=0.2)


def _digest(data_dir: Path) -> dict[str, str]:
    out = {}
    for path in sorted(data_dir.rglob("*")):
        if path.is_file() and not path.name.endswith("-shm"):
            out[path.relative_to(data_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def test_cli_reads_without_writing(isolated_settings, capsys):
    from studio.cli import main

    doc = _project_with_storyboard(isolated_settings)
    _write_preview(isolated_settings, doc)
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


@pytest.mark.parametrize("module", ["studio.workers.finalcheck.profile_check", "studio.workers.factcheck.claims"])
def test_cli_modules_import_in_a_fresh_interpreter(module):
    """O `studio edit-metrics` importa `profile_check` antes de `workers.jobs`; num processo novo isso não pode cair no
    ciclo claims → authoring → media_pool → jobs → factcheck.tasks → claims (pela metade)."""
    import subprocess
    import sys

    done = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True,
                          cwd=Path(__file__).resolve().parents[1], timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]


def test_readonly_engine_refuses_writes(isolated_settings):
    doc = _project_with_storyboard(isolated_settings)
    with readonly_engine(), pytest.raises(Exception):  # noqa: B017 - o SQLite recusa com OperationalError
        service.update_settings(doc.id, {"x": 1})
    assert service.update_settings(doc.id, {"x": 1})["x"] == 1  # o engine normal volta depois do bloco


def test_load_context_used_by_metrics_is_the_final_one(isolated_settings):
    doc = _project_with_storyboard(isolated_settings)
    ctx = load_context(doc.id, LANG)
    measured = profile_check.metrics_timeline(ctx)
    assert measured.source == "estimate" and measured.timeline["scenes"] and not measured.outdated


# --- Revisão: timeline que não descreve mais o projeto, cenas de IA, gancho, repetições, licenças -------------------


def _attach_official(doc: ProjectDoc, scene_id: str) -> ProjectDoc:
    from studio.core.project.document import MediaRef

    scenes = []
    for scene in doc.scenes:
        if scene.id == scene_id:
            media = MediaRef(kind="official", source="link", asset=f"uploads/{doc.id}/media/a.jpg",
                             page_url="https://example.org/arte")
            scene = scene.model_copy(update={"visual": scene.visual.model_copy(update={"media": media})})
        scenes.append(scene)
    return doc.model_copy(update={"scenes": scenes})


def test_auto_drops_final_after_new_version_and_final_uses_rendered_doc(isolated_settings):
    """Depois do render, um commit novo (mídia real anexada, sem renderizar): o `auto` não mede o vídeo antigo; o
    `final` explícito mede, avisa que está desatualizado e tira o nível de fonte do documento renderizado."""
    doc = _project_with_storyboard(isolated_settings)
    _write_final(isolated_settings, doc)
    client = TestClient(create_app())
    body = client.get(f"/api/projects/{doc.id}/edit-metrics").json()
    assert body["source"] == "final" and body["outdated"] is False
    rendered = _current(doc.id)
    service.commit(doc.id, _attach_official(doc, "scn_00"), "anexa arte oficial")
    auto = client.get(f"/api/projects/{doc.id}/edit-metrics").json()
    assert auto["source"] == "estimate" and auto["outdated"] is False
    assert auto["metrics"]["ai_screen_share"] < 1.0  # o plano de agora já tem a arte oficial
    final = client.get(f"/api/projects/{doc.id}/edit-metrics", params={"source": "final"}).json()
    assert final["outdated"] is True and final["version"] == rendered and final["current_version"] == rendered + 1
    assert final["metrics"]["ai_screen_share"] == 1.0  # o vídeo renderizado era todo de IA
    assert "o projeto mudou" in final["source_text"]
    # A conferência do render também mede com o documento da versão renderizada.
    report = profile_check.run_for_project(doc.id, LANG)
    assert report is not None and report["data"]["metrics"]["ai_screen_share"] == 1.0


def test_auto_skips_preview_of_other_version(isolated_settings):
    doc = _project_with_storyboard(isolated_settings)
    _write_preview(isolated_settings, doc)
    service.commit(doc.id, _attach_official(doc, "scn_00"), "anexa")
    ctx = load_context(doc.id, LANG)
    assert profile_check.metrics_timeline(ctx).source == "estimate"
    measured = profile_check.metrics_timeline(ctx, "preview")
    assert measured.source == "preview" and measured.outdated


def test_ai_scene_ids_cover_every_ai_scene_in_a_run():
    """Três cenas de IA seguidas viram uma faixa só, mas o "Trocar IA por real" recebe as três."""
    scenes = [_scene("scn_a", 0, 3, prov="official"), _scene("scn_b", 3, 3, prov="ai"),
              _scene("scn_c", 6, 3, prov="ai"), _scene("scn_d", 9, 3, prov="ai"), _scene("scn_e", 12, 3, prov="free")]
    tl = _timeline(scenes)
    strip = metrics.prov_strip(tl)
    assert [s["prov"] for s in strip] == ["official", "ai", "free"]
    assert strip[1]["scene_id"] == "scn_b" and strip[1]["scene_ids"] == ["scn_b", "scn_c", "scn_d"]
    assert metrics.measure_timeline(tl).ai_scene_ids == ["scn_b", "scn_c", "scn_d"]
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    doc = ProjectDoc.model_validate(data["doc"])
    out = profile_check.compute(tl, doc, get_style("curiosidades-longo"))
    assert out["ai_scene_ids"] == ["scn_b", "scn_c", "scn_d"]


def test_hook_follows_style_policy(longo):
    """Gancho de cartão não é real: a política do estilo (own/official/capture/free) decide."""
    scenes = [_scene("scn_t", 0, 3.2, prov="graphic", kind="title"), *[
        _scene(f"scn_{i}", 3.2 + i * 2.0, 2.0, prov=p) for i, p in enumerate(["official", "capture", "free", "own"])]]
    tl = _timeline(scenes)
    fp = metrics.measure_timeline(tl).fp
    assert fp.hook_prov == "graphic"
    t = metrics.targets_for(longo)
    assert metrics.flat_metrics(fp, t)["hook_real"] is False
    assert metrics.flat_metrics(fp)["hook_real"] is False  # sem estilo: só material real
    doc = ProjectDoc.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["doc"])
    hook = [i for i in edit_items(tl, doc, longo) if i["title"].startswith("O gancho")]
    assert hook and hook[0]["title"] == "O gancho abre com gráfico" and hook[0]["scene_id"] == "scn_t"


def test_repeats_count_library_sfx_and_channel_memes():
    scenes = [_scene(f"scn_{i}", i * 6.0, 6.0, prov="official" if i % 2 else "capture") for i in range(10)]
    scenes[3]["fx"] = [{"type": "meme", "from": 30, "dur": 30, "params": {"lib_id": "meme-ja-usado"}}]
    sfx = [{"type": "boom", "at": i * 6.0 + 1.0, "scene": f"scn_{i}", "lib_id": "boom-01"} for i in range(5)]
    tl = _timeline(scenes, sfx)
    m = metrics.measure_timeline(tl, channel_memes={"meme-ja-usado"})
    # 60 s: o mesmo som da biblioteca toca até 2 vezes de graça; as 3 a mais são repetições. O meme já foi ao ar.
    assert sorted(what for _, _, what in m.repeats) == ["meme do canal", "som", "som", "som"]
    assert m.fp.repeat_hits == 4
    assert metrics.measure_timeline(tl).fp.repeat_hits == 3  # sem o histórico do canal
    assert metrics.timeline_memes(tl) == {"meme-ja-usado"}


def test_legacy_sfx_at_scene_start_is_transition_and_inner_sfx_is_event():
    """Formato antigo (sem `at`): o som no início da cena (transição, escolhido, impacto do cartão) é de corte; o
    carimbo pousando dentro da cena é evento."""
    scenes = [_scene(f"scn_{i}", i * 6.0, 6.0, prov="ai", kind="title" if i % 2 else "image") for i in range(10)]
    sfx = []
    for i in range(1, 10):
        sfx.append({"type": "impact", "start": i * 6.0, "scene_id": f"scn_{i}"})  # impacto do cartão no corte
    sfx.append({"type": "whoosh", "start": 0.0, "scene_id": "scn_0"})  # escolhido na 1ª cena
    sfx.append({"type": "impact", "start": 6.0 + 0.3, "scene_id": "scn_1"})  # carimbo pousando
    fp = metrics.measure_timeline(_timeline(scenes, sfx)).fp
    assert fp.audio.sfx_per_min == 1.0


def test_material_run_by_seconds_names_the_violating_run(longo):
    scenes = [_scene("scn_a", 0, 2, prov="official"), _scene("scn_b", 2, 2, prov="capture"),
              _scene("scn_c", 4, 2, prov="free"), _scene("scn_d", 6, 2, prov="official"),
              _scene("scn_ia", 8, 25, prov="ai"), _scene("scn_e", 33, 2, prov="capture")]
    doc = ProjectDoc.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["doc"])
    items = edit_items(_timeline(scenes), doc, longo)
    run = [i for i in items if "seguidos" in i["title"]]
    assert run and run[0]["title"] == "25,0 s seguidos de imagem de IA"
    assert run[0]["scene_id"] == "scn_ia" and run[0]["at_s"] == 8.0


def test_ai_block_is_caught_by_the_sliding_window_even_under_the_average(longo):
    """QA do prj_gq4krrs11w: IA em 17% na média (abaixo dos 20%), mas o minuto 3 virou um slideshow de imagens geradas
    (46% de IA num trecho de 45 s). O teto vale também por trecho (`sources.AI_WINDOW_S`)."""
    from studio.core import sources

    provs = ["official", "capture", "free", "stock", "own"]
    scenes = [_scene(f"scn_{i:02d}", i * 6.0, 6.0, prov=provs[i % len(provs)]) for i in range(20)]
    scenes += [_scene(f"scn_ia{i}", 120 + i * 6.0, 6.0, prov="ai") for i in range(5)]  # 30 s seguidos de IA
    scenes += [_scene(f"scn_{i:02d}", 150 + (i - 20) * 6.0, 6.0, prov=provs[i % len(provs)]) for i in range(20, 45)]
    m = metrics.measure_timeline(_timeline(scenes))
    assert metrics.ai_screen_share(m.fp) == pytest.approx(0.10, abs=0.01)
    assert m.fp.ai_window_share == pytest.approx(30 / 45, abs=0.01) and 105 <= m.ai_window_start <= 120
    assert metrics.flat_metrics(m.fp)["ai_window_share"] == m.fp.ai_window_share
    parts = {p.key: p for p in metrics.ai_look_index(m.fp, longo).parts}
    assert parts["ai_screen_share"].penalty == 0
    assert parts["ai_window_share"].penalty > 0 and parts["ai_window_share"].hi == sources.ai_window_cap(0.20) == 0.3
    doc = ProjectDoc.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["doc"])
    ia = next(i for i in edit_items(_timeline(scenes), doc, longo) if i["code"] == "perfil.ia")
    assert ia["level"] == "aviso" and "trecho de 1:" in ia["title"] and "por trecho de 45 s" in ia["title"]
    assert ia["scene_id"] == "scn_ia0" and ia["at_s"] == 120.0
    # IA espalhada (uma imagem de 6 s a cada 30 s: 20% na média) cabe no teto por trecho
    spread = [_scene(f"scn_{i:02d}", i * 6.0, 6.0, prov="ai" if i % 5 == 2 else provs[i % len(provs)])
              for i in range(50)]
    fp = metrics.measure_timeline(_timeline(spread)).fp
    assert fp.ai_window_share <= 0.3 + 1e-9
    assert next(i for i in edit_items(_timeline(spread), doc, longo) if i["code"] == "perfil.ia")["level"] == "ok"
    # vídeo mais curto que a janela: o trecho é o vídeo todo
    assert sources.window_share([(0.0, 10.0)], 30.0) == (pytest.approx(1 / 3), 0.0)
    assert sources.window_share([(0.0, 10.0), (90.0, 100.0)], 100.0)[0] == pytest.approx(10 / 45)


def test_events_index_and_item_use_the_same_band(longo):
    fp = metrics.measure_timeline(real_first_timeline()).fp
    doc = ProjectDoc.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["doc"])
    lo, hi = metrics.targets_for(longo).events_band
    for value in (lo * 0.98, lo * 1.02, (lo + hi) / 2, hi * 0.98, hi * 1.05):
        probe = fp.model_copy(update={"pace": fp.pace.model_copy(update={"events_per_min": value})})
        part = next(p for p in metrics.ai_look_index(probe, longo).parts if p.key == "events_per_min")
        level = next(i["level"] for i in edit_items(real_first_timeline(), doc, longo, fp=probe)
                     if i["code"] == "perfil.eventos")
        assert (part.penalty > 0) == (level == "aviso"), value
    assert part.lo == lo and part.hi == hi and part.unit == "per_min"


def test_kb_share_matches_planner_grammar(longo):
    """A timeline com a câmera que a gramática pede (25% parada, 30% zoom lento, o resto pan/deriva) não perde ponto
    no `kb_share`; trocar o Ken Burns por zoom_to, push ou drift não engana a medida."""
    motions = ["static"] * 5 + ["ken_burns_in"] * 6 + ["pan_left", "pan_right", "pan_up", "drift"] * 2 + ["pan_left"]
    scenes = [_scene(f"scn_{i:02d}", i * 3.0, 3.0, prov="official", motion=m) for i, m in enumerate(motions)]
    fp = metrics.measure_timeline(_timeline(scenes)).fp
    assert fp.camera.kb_share == 0.75
    kb = next(p for p in metrics.ai_look_index(fp, longo).parts if p.key == "kb_share")
    assert kb.penalty == 0 and metrics.kb_cap(longo) == 0.75
    swapped = [_scene(f"scn_{i:02d}", i * 3.0, 3.0, prov="official",
                      motion={"ken_burns_in": "zoom_to", "pan_left": "push"}.get(m, m)) for i, m in enumerate(motions)]
    assert metrics.measure_timeline(_timeline(swapped)).fp.camera.kb_share == 0.75
    everything = [_scene(f"scn_{i:02d}", i * 3.0, 3.0, prov="official", motion="zoom_to") for i in range(20)]
    kb_all = metrics.measure_timeline(_timeline(everything)).fp
    assert next(p for p in metrics.ai_look_index(kb_all, longo).parts if p.key == "kb_share").penalty == 1


def _manifest(settings, rows: list[dict[str, Any]]) -> None:
    path = settings.library_dir / "sfx" / "manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": rows}), encoding="utf-8")


def test_license_items_check_unknown_library_shots_and_pool(isolated_settings, longo):
    from studio.core.project.document import MediaRef, SubShot
    from studio.core.project.pool import Pool, PoolItem

    _manifest(isolated_settings, [{"id": "boom-ok", "license": "cc0"}, {"id": "fair", "license": "fair_comment"},
                                  {"id": "ccby", "license": "CC-BY 4.0"}, {"id": "risco", "license": "own",
                                                                              "risk": "high"}])
    doc = _project_with_storyboard(isolated_settings)
    meme = MediaRef(kind="meme", source="upload", asset=f"uploads/{doc.id}/media/m.jpg")
    scenes = list(doc.scenes)
    scenes[1] = scenes[1].model_copy(update={"shots": [SubShot(source="media", media=meme)]})
    scenes[2] = scenes[2].model_copy(update={"shots": [SubShot(source="pool", pool_id="p1")]})
    doc = doc.model_copy(update={"scenes": scenes})
    pool = Pool(items=[PoolItem(id="p1", tier="meme")])
    tl = profile_check.estimate_timeline(doc, longo, LANG)
    tl["sfx"] = [*tl.get("sfx", []), *[{"type": "boom", "at": 1.0, "scene": scenes[0].id, "lib_id": lib}
                                       for lib in ("boom-ok", "fair", "ccby", "risco", "fantasma")]]
    lic = [i for i in edit_items(tl, doc, longo, pool=pool) if i["code"] == "perfil.licenca"]
    titles = {i["title"] for i in lic}
    assert all(i["level"] == "aviso" for i in lic)
    assert "Meme sem licença declarada (2 cenas)" in titles
    assert "Inserto \"fantasma\" sem registro na biblioteca" in titles
    assert "Inserto \"fair\" usado como comentário" in titles and "Inserto \"risco\" usado como comentário" in titles
    assert "Inserto \"ccby\" sem crédito" in titles
    assert not any("boom-ok" in t for t in titles)


def test_license_ok_text_depends_on_inserts(isolated_settings, longo):
    doc = _project_with_storyboard(isolated_settings)
    tl = profile_check.estimate_timeline(doc, longo, LANG)
    lic = [i for i in edit_items(tl, doc, longo) if i["code"] == "perfil.licenca"]
    assert [(i["level"], i["title"]) for i in lic] == [("ok", "Nenhum inserto de terceiros")]
    _manifest(isolated_settings, [{"id": "boom-ok", "license": "cc0"}])
    tl["sfx"] = [{"type": "boom", "at": 1.0, "scene": doc.scenes[0].id, "lib_id": "boom-ok"}]
    lic = [i for i in edit_items(tl, doc, longo) if i["code"] == "perfil.licenca"]
    assert [(i["level"], i["title"]) for i in lic] == [("ok", "Insertos com licença registrada")]


def test_pace_title_for_short_shots(longo):
    durs = [0.5, 1.4, 0.7, 2.2, 0.9, 1.1, 0.6, 1.8, 0.8, 1.3]
    scenes, t = [], 0.0
    for i, d in enumerate(durs):
        scenes.append(_scene(f"scn_{i}", t, d, prov="official" if i % 2 else "capture"))
        t += d
    doc = ProjectDoc.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8"))["doc"])
    pace = next(i for i in edit_items(_timeline(scenes), doc, longo) if i["code"] == "perfil.ritmo")
    assert pace["level"] == "aviso" and pace["title"] == "Planos curtos demais"


def test_index_parts_carry_structured_targets(longo):
    parts = {p.key: p for p in metrics.ai_look_index(metrics.measure_timeline(slideshow_timeline()).fp, longo).parts}
    assert parts["ai_screen_share"].unit == "share" and parts["ai_screen_share"].hi == 0.2
    assert parts["ai_screen_share"].lo is None and parts["ai_screen_share"].target == "≤ 0.2"
    assert parts["shot_cv"].unit == "ratio" and parts["shot_cv"].lo == 0.45
    assert parts["sfx_per_min"].unit == "per_min" and parts["sfx_per_min"].lo == 2.0
    assert parts["repeat_hits"].unit == "count" and parts["repeat_hits"].hi == 0


# --- Conferência do vídeo final de ponta a ponta --------------------------------------------------------------------


def _stub_final_check(monkeypatch) -> None:
    from studio.workers import novelty
    from studio.workers.finalcheck import measure, report

    monkeypatch.setattr(report, "duration_of", lambda _p: 12.0)
    monkeypatch.setattr(measure, "loudness", lambda _p: {"lufs": -14.0, "lra": 5.0, "true_peak": -1.5})
    monkeypatch.setattr(measure, "voice_vs_music", lambda _p, _t: ([], []))
    monkeypatch.setattr(measure, "black_segments", lambda _p, _d=None: [])
    monkeypatch.setattr(report, "contact_sheet", lambda *_a, **_k: None)
    monkeypatch.setattr(novelty, "safe_warnings", lambda _pid: [])
    monkeypatch.setattr(profile_check, "measure_render", lambda _p: None)


def test_run_final_check_writes_edit_report(isolated_settings, monkeypatch):
    from studio.workers.finalcheck.report import preflight, run_final_check

    _stub_final_check(monkeypatch)
    doc = _project_with_storyboard(isolated_settings)
    _write_final(isolated_settings, doc)
    final = run_final_check(doc.id, LANG)
    assert final["kind"] == "final"
    with session_scope() as s:
        rows = s.query(QualityReport).filter_by(project_id=doc.id, kind="edit").all()
        assert len(rows) == 1 and rows[0].version == _current(doc.id)
        assert {i["code"] for i in rows[0].items} >= set(EDIT_CODES)
    # Os itens `perfil.*` vão à Conferência final à parte: nunca erro e fora do `status`.
    state = preflight(doc.id, LANG)
    assert state["edit"] is not None and state["edit"]["score"] is not None
    assert {i["code"] for i in state["edit"]["items"]} >= set(EDIT_CODES)
    assert all(i["code"] not in EDIT_CODES for i in state["items"])


def test_run_final_check_skips_edit_report_with_stale_timeline(isolated_settings, monkeypatch):
    from studio.workers.finalcheck.report import preflight, run_final_check

    _stub_final_check(monkeypatch)
    doc = _project_with_storyboard(isolated_settings)
    _write_final(isolated_settings, doc, video_age_s=30.0)  # a prévia regravou a timeline depois do MP4
    assert load_context(doc.id, LANG).timeline_stale
    run_final_check(doc.id, LANG)
    with session_scope() as s:
        assert s.query(QualityReport).filter_by(project_id=doc.id, kind="edit").count() == 0
    assert preflight(doc.id, LANG)["edit"] is None


def test_edit_report_of_older_render_is_not_returned(isolated_settings, monkeypatch):
    import os
    import time

    monkeypatch.setattr(profile_check, "measure_render", lambda _p: None)
    doc = _project_with_storyboard(isolated_settings)
    _write_final(isolated_settings, doc)
    assert profile_check.run_for_project(doc.id, LANG) is not None
    video = isolated_settings.outputs_dir / doc.id / LANG / "final.mp4"
    later = time.time() + 120  # render novo no mesmo caminho
    os.utime(video, (later, later))
    body = TestClient(create_app()).get(f"/api/projects/{doc.id}/edit-metrics").json()
    assert body["report"] is None and body["report_outdated"] is True and body["measured_from"] == "timeline"


# --- Banco só leitura ------------------------------------------------------------------------------------------------


def test_readonly_engine_reads_wal_of_open_app_without_writing(isolated_settings):
    """Com o app aberto (conexão de escrita viva e o -wal no disco), o modo só leitura enxerga o que ainda está no
    WAL e não grava nada."""
    from studio.db import get_engine

    doc = _project_with_storyboard(isolated_settings)
    service.update_settings(doc.id, {"marca": "no-wal"})
    engine = get_engine()
    held = engine.connect()  # segura uma conexão de escrita aberta: sem checkpoint final
    try:
        db = isolated_settings.data_dir / "studio.db"
        wal = db.with_name(db.name + "-wal")
        assert wal.is_file()
        before = {p.name: p.read_bytes() for p in (db, wal)}
        with readonly_engine() as ro:
            assert "mode=ro" in str(ro.url)
            assert service.get_project_row(doc.id)["settings"]["marca"] == "no-wal"
            with pytest.raises(Exception):  # noqa: B017 - o SQLite recusa com OperationalError
                service.update_settings(doc.id, {"x": 1})
        assert {p.name: p.read_bytes() for p in (db, wal)} == before
    finally:
        held.close()


def test_readonly_engine_uses_configured_database_url(isolated_settings, tmp_path):
    import shutil

    from studio.config import Settings, set_settings
    from studio.db import sqlite_path

    doc = _project_with_storyboard(isolated_settings)
    reset_engine()
    other = tmp_path / "outro" / "banco.db"
    other.parent.mkdir()
    shutil.copy(isolated_settings.data_dir / "studio.db", other)
    (isolated_settings.data_dir / "studio.db").rename(tmp_path / "fora.db")
    set_settings(Settings(data_dir=isolated_settings.data_dir, presets_dir=isolated_settings.presets_dir,
                          offline=True, workers_enabled=False, database_url=f"sqlite:///{other.as_posix()}"))
    try:
        with readonly_engine():
            assert service.get_project_row(doc.id)["id"] == doc.id
    finally:
        set_settings(isolated_settings)
        reset_engine()
    assert sqlite_path(f"sqlite:///{other.as_posix()}") == other
    with pytest.raises(ValueError):
        sqlite_path("postgresql://u@h/db")
    with pytest.raises(ValueError):
        sqlite_path("sqlite://")
