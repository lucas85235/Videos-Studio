"""Reenquadramento das imagens 16:9 do longo no Short vertical: foco sugerido, fundo desfocado e imagem vertical."""

from __future__ import annotations

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import MediaRef, ProjectDoc, Scene, SceneFrame, Segment
from studio.core.styles.library import get_style
from studio.core.timeline import resolve_scene, scene_frame
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline.build import plan
from studio.workers.shorts import derive, reframe

LONG_STYLE = "curiosidades-longo"


def _synthetic(cx: float, cy: float, size: tuple[int, int] = (640, 360)) -> Image.Image:
    """Fundo cinza com um pouco de ruído e um disco vermelho em (cx, cy)."""
    w, h = size
    rng = np.random.default_rng(7)
    base = np.clip(110 + rng.normal(0, 6, (h, w, 3)), 0, 255).astype(np.uint8)
    im = Image.fromarray(base, "RGB")
    r = h * 0.12
    ImageDraw.Draw(im).ellipse((cx * w - r, cy * h - r, cx * w + r, cy * h + r), fill=(220, 40, 30))
    return im


def test_suggested_focus_finds_off_center_object():
    x, y = reframe.suggest_focus(_synthetic(0.8, 0.35))
    assert abs(x - 0.8) < 0.06 and abs(y - 0.35) < 0.08
    x, y = reframe.suggest_focus(_synthetic(0.18, 0.7))
    assert abs(x - 0.18) < 0.06 and abs(y - 0.7) < 0.08
    assert reframe.suggest_focus(Image.new("RGB", (320, 180), (90, 90, 90))) == (0.5, 0.5)  # lisa: o meio


def test_suggest_frame_reads_file(tmp_path):
    path = tmp_path / "img.png"
    _synthetic(0.75, 0.5, (800, 450)).save(path)
    frame = reframe.suggest_frame(path)
    assert frame.auto and abs(frame.aspect - 16 / 9) < 0.01 and abs(frame.x - 0.75) < 0.06
    missing = reframe.suggest_frame(tmp_path / "sumiu.png", 1.5)
    assert (missing.x, missing.y, missing.aspect) == (0.5, 0.5, 1.5)


def _short_style():
    parent = ProjectDoc(id="prj_l", title="l", style_id=LONG_STYLE)
    return get_style(LONG_STYLE, derive.short_overrides(parent, 20.0, 35.0))


def _doc(**kw) -> ProjectDoc:
    scenes = [
        Scene(id="c1", segment_id="s1", visual={"prompt": "farol", "asset": "cache/image/a.png"},
              frame=SceneFrame(x=0.8, y=0.3)),
        Scene(id="c2", segment_id="s1", part=1, visual={"prompt": "mar", "asset": "cache/image/b.png"},
              frame=SceneFrame(x=0.2, y=0.6, mode="blur")),
        Scene(id="c3", segment_id="s1", part=2, visual={"prompt": "ilha", "asset": "cache/image/c.png"},
              frame=SceneFrame(aspect=9 / 16)),  # já vertical
        Scene(id="c4", segment_id="s1", part=3, visual={"prompt": "barco"}, frame=SceneFrame()),  # sem imagem fixada
        Scene(id="c5", segment_id="s1", part=4,
              visual={"asset": "cache/image/d.png", "media": MediaRef(asset="uploads/p/media/x.jpg")},
              frame=SceneFrame()),  # mídia real: o build enquadra
        Scene(id="c6", segment_id="s1", part=5, visual={"prompt": "céu", "asset": "cache/image/e.png"}),
    ]
    return ProjectDoc(id="prj_s", title="s", style_id=LONG_STYLE, segments=[Segment(id="s1", text={"pt-BR": "Oi."})],
                      scenes=scenes, **kw)


def test_frame_in_timeline():
    style = _short_style()
    doc = _doc()
    by_id = {s.id: s for s in doc.scenes}
    out = resolve_scene(doc, style, by_id["c1"], "pt-BR", is_first=True)
    assert out["frame"] == {"mode": "crop", "x": 0.8, "y": 0.3, "aspect": round(16 / 9, 4)}
    for sid in ("c3", "c4", "c5", "c6"):
        assert "frame" not in resolve_scene(doc, style, by_id[sid], "pt-BR", is_first=False), sid
    # No longo (16:9) a imagem 16:9 não precisa de reenquadramento.
    assert scene_frame(doc, get_style(LONG_STYLE), by_id["c1"]) is None


def test_blur_mode_in_timeline():
    style = _short_style()
    doc = _doc()
    by_id = {s.id: s for s in doc.scenes}
    frame = resolve_scene(doc, style, by_id["c2"], "pt-BR", is_first=False)["frame"]
    assert frame["mode"] == "blur" and (frame["x"], frame["y"]) == (0.2, 0.6)
    expected = 0.6 if style.edit.captions.position == "top" else 0.4
    assert frame["anchor"] == expected
    # Padrão do Short: vale para as cenas sem modo próprio.
    blurred = _doc(frame_mode="blur")
    assert scene_frame(blurred, style, blurred.scenes[0])["mode"] == "blur"
    crop = blurred.scenes[0].model_copy(update={"frame": SceneFrame(x=0.8, y=0.3, mode="crop")})
    assert scene_frame(blurred, style, crop)["mode"] == "crop"


# ---------------------------------------------------------------------------------------------
# Short de verdade (offline)
# ---------------------------------------------------------------------------------------------
def _run(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _short() -> tuple[str, str]:
    doc = service.create_project(title="O farol", style_id=LONG_STYLE, topic="o farol abandonado da ilha")
    _run("script", doc.id)
    _run("storyboard", doc.id)
    _run("preview", doc.id)
    overview = derive.segments_overview(doc.id)
    ids = [s["id"] for s in overview["segments"]]
    result = derive.derive_short(doc.id, [[ids[1], ids[2]]], "O segredo do farol")
    assert derive.FRAME_WARNING in result["warnings"]
    return doc.id, result["project_id"]


def test_short_gets_frames_and_routes(isolated_settings):
    parent_id, child_id = _short()
    child = service.get_doc(child_id)
    framed = [s for s in child.scenes if s.frame is not None]
    assert framed and all(s.visual.asset and s.frame.auto for s in framed)
    assert all(abs(s.frame.aspect - 16 / 9) < 0.05 for s in framed)

    _run("preview", child_id)
    timeline, _ = derive.load_timeline(child_id, "pt-BR", service.get_project_row(child_id)["settings"])
    in_timeline = {sc["id"]: sc for sc in timeline["scenes"] if "frame" in sc}
    assert set(in_timeline) == {s.id for s in framed}
    assert all(sc["frame"]["mode"] == "crop" for sc in in_timeline.values())

    target = next(s for s in framed if s.kind == "image" and s.visual.prompt and not s.visual.reuse_of)
    with TestClient(create_app()) as client:
        assert client.get(f"/api/projects/{parent_id}/shorts/frames").status_code == 409  # o longo não tem
        data = client.get(f"/api/projects/{child_id}/shorts/frames").json()
        assert data["default_mode"] == "crop" and data["width"] == 1080 and data["height"] == 1920
        assert {s["id"] for s in data["scenes"]} == {s.id for s in framed}

        r = client.put(f"/api/projects/{child_id}/shorts/frames",
                       json={"default_mode": "blur", "scenes": [{"id": target.id, "x": 0.9, "y": 0.1, "mode": "crop"}]})
        assert r.status_code == 200, r.text
        edited = next(s for s in r.json()["scenes"] if s["id"] == target.id)["frame"]
        assert (edited["x"], edited["y"], edited["mode"], edited["auto"]) == (0.9, 0.1, "crop", False)
        doc = service.get_doc(child_id)
        assert doc.frame_mode == "blur"
        r = client.put(f"/api/projects/{child_id}/shorts/frames",
                       json={"scenes": [{"id": target.id, "mode": "default", "reset": True}]})
        again = next(s for s in r.json()["scenes"] if s["id"] == target.id)["frame"]
        assert again["mode"] is None and again["auto"] and (again["x"], again["y"]) == (target.frame.x, target.frame.y)
        assert client.put(f"/api/projects/{child_id}/shorts/frames",
                          json={"scenes": [{"id": "scn_nada"}]}).status_code == 422

        # Imagem vertical: vira "Outra imagem" e a próxima prévia gera em 9:16.
        variation = target.visual.variation
        r = client.post(f"/api/projects/{child_id}/shorts/frames/{target.id}/vertical")
        assert r.status_code == 200, r.text
        listed = next(s for s in r.json()["scenes"] if s["id"] == target.id)
        assert listed["vertical"] and listed["src"] is None and listed["frame"]["source"] == target.visual.asset
        assert client.post(f"/api/projects/{child_id}/shorts/frames/{target.id}/vertical").status_code == 422
        # Pedida em 9:16, a cena não aceita foco nem modo.
        assert client.put(f"/api/projects/{child_id}/shorts/frames",
                          json={"scenes": [{"id": target.id, "x": 0.3}]}).status_code == 422
    scene = service.get_doc(child_id).scene(target.id)
    assert scene.visual.asset is None and scene.frame.vertical and scene.locked
    assert scene.visual.variation == variation + 1
    assert target.id in plan(child_id, mode="preview")["images_missing"]
    assert "frame" not in resolve_scene(service.get_doc(child_id), get_style(LONG_STYLE, service.get_doc(child_id).style_overrides),
                                        scene, "pt-BR", is_first=False)

    # Depois da prévia, a tela mostra a imagem vertical que saiu (e não "na próxima prévia" para sempre).
    _run("preview", child_id)
    data = reframe.frames_overview(child_id)
    listed = next(s for s in data["scenes"] if s["id"] == target.id)
    assert listed["vertical"] and listed["src"] and listed["src"] != target.visual.asset

    # Voltar à imagem 16:9: a imagem fixada e o foco de antes.
    data = reframe.update_frames(child_id, None, [{"id": target.id, "restore": True}])
    listed = next(s for s in data["scenes"] if s["id"] == target.id)
    assert not listed["vertical"] and listed["src"] == target.visual.asset
    assert service.get_doc(child_id).scene(target.id).frame.source is None


def _strip_frames(child_id: str) -> ProjectDoc:
    doc = service.get_doc(child_id)
    for scene in doc.scenes:
        scene.frame = None
    service.commit(child_id, doc, "Short antigo")
    return doc


def test_old_short_gets_frames_when_opened(isolated_settings):
    _, child_id = _short()
    old = _strip_frames(child_id)  # Short criado antes do reenquadramento: nenhuma cena com foco
    pinned = {s.id for s in old.scenes if s.visual.asset and s.visual.media is None}
    with TestClient(create_app()) as client:
        data = client.get(f"/api/projects/{child_id}/shorts/frames").json()
    assert pinned and {s["id"] for s in data["scenes"]} == pinned
    assert all(not s["vertical"] and s["frame"]["auto"] for s in data["scenes"])
    assert all(service.get_doc(child_id).scene(sid).frame is not None for sid in pinned)  # gravado


def test_redo_scene_and_lost_image_are_not_listed(isolated_settings):
    _, child_id = _short()
    doc = service.get_doc(child_id)
    framed = [s for s in doc.scenes if s.frame is not None]
    lost = framed[0]
    lost.visual.asset = None  # perdeu a imagem fixada pelo roteiro ("Outra imagem"): o frame ficou para trás
    redo = next((s for s in doc.scenes if s.frame is None), None)
    service.commit(child_id, doc, "teste")
    data = reframe.frames_overview(child_id)
    ids = {s["id"] for s in data["scenes"]}
    assert lost.id not in ids and (redo is None or redo.id not in ids)
    try:
        reframe.update_frames(child_id, None, [{"id": lost.id, "x": 0.2}])
    except derive.ShortsError as exc:
        assert exc.args and "reenquadrar" in str(exc)
    else:
        raise AssertionError("aceitou foco numa cena sem imagem")


def test_vertical_image_carries_reusing_scenes(isolated_settings):
    _, child_id = _short()
    doc = service.get_doc(child_id)
    root = next(s for s in doc.scenes if s.frame is not None and s.visual.prompt and not s.visual.reuse_of)
    # Uma cena do Short reaproveita a imagem da raiz e fixou a mesma 16:9 (como o corte faz).
    other = next(s for s in doc.scenes if s.id != root.id and s.visual.media is None)
    other.visual.reuse_of = root.id
    other.visual.asset = root.visual.asset
    other.frame = SceneFrame(x=0.3)
    service.commit(child_id, doc, "teste")

    data = reframe.vertical_image(child_id, root.id)
    follower = next(s for s in data["scenes"] if s["id"] == other.id)
    assert follower["vertical"] and follower["follows"] == root.id
    saved = service.get_doc(child_id)
    assert saved.scene(other.id).visual.asset is None and saved.reuse_root(saved.scene(other.id)).id == root.id

    reframe.update_frames(child_id, None, [{"id": root.id, "restore": True}])
    saved = service.get_doc(child_id)
    assert saved.scene(root.id).visual.asset == root.visual.asset
    back = saved.scene(other.id)
    assert back.visual.asset == root.visual.asset and not back.frame.vertical and back.frame.x == 0.3
