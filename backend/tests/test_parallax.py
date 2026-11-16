"""Parallax 2.5D: mapa de profundidade, camadas, download do modelo (simulado) e o caminho no build.

Nada aqui baixa o modelo nem roda o Depth Anything: o estimador é trocado por um mapa sintético.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import numpy as np
import pytest
from PIL import Image

from studio.core.project import service
from studio.core.project.document import ProjectDoc, Scene, Segment, Visual
from studio.core.project.ops import apply_ops, parse_ops
from studio.core.styles.library import get_style
from studio.core.styles.schema import CAMERA_MOVES
from studio.core.timeline import NarrationClip, build_timeline
from studio.media import depth as depth_mod
from studio.providers.base import Word
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline.build import plan


def _disk_image(w: int = 320, h: int = 180) -> tuple[Image.Image, np.ndarray]:
    """Fundo azul com um disco vermelho no meio; profundidade: o disco perto (1), o fundo longe (0 → 0,3 em rampa)."""
    yy, xx = np.mgrid[0:h, 0:w]
    disk = ((xx - w / 2) ** 2 + (yy - h / 2) ** 2) < (min(w, h) * 0.25) ** 2
    rgb = np.zeros((h, w, 3), dtype=np.uint8)
    rgb[...] = (30, 60, 200)
    rgb[disk] = (220, 30, 30)
    depth = (yy / h * 0.3).astype(np.float32)
    depth[disk] = 1.0
    return Image.fromarray(rgb), depth


def _synthetic_estimator(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        w, h = im.size
    yy, xx = np.mgrid[0:h, 0:w]
    return np.clip(yy / h * 0.6 + 0.4 * (np.abs(xx - w / 2) < w / 6), 0, 1).astype(np.float32)


@pytest.fixture
def synthetic_depth(monkeypatch):
    depth_mod.set_estimator(_synthetic_estimator)
    monkeypatch.setattr(depth_mod, "availability", lambda: (True, "ok"))
    yield
    depth_mod.set_estimator(None)


# ---------------------------------------------------------------------------------------------
# Pré e pós-processamento
# ---------------------------------------------------------------------------------------------

def test_input_size_keeps_aspect_and_multiple_of_14():
    w, h = depth_mod.input_size(1920, 1080)
    assert h == 518 and w % 14 == 0 and abs(w / h - 16 / 9) < 0.03
    w, h = depth_mod.input_size(1080, 1920)
    assert w == 518 and h % 14 == 0


def test_preprocess_shape_and_normalization():
    arr = depth_mod.preprocess(Image.new("RGB", (640, 360), (124, 116, 104)))
    assert arr.shape == (1, 3, 518, 924) and arr.dtype == np.float32
    assert np.abs(arr).max() < 0.05  # a cor média do ImageNet vira ~0


def test_normalize_depth_stretches_and_resizes():
    raw = np.linspace(5, 50, 37 * 66, dtype=np.float32).reshape(1, 37, 66)
    depth = depth_mod.normalize_depth(raw, (320, 180))
    assert depth.shape == (180, 320)
    assert depth.min() == pytest.approx(0, abs=0.02) and depth.max() == pytest.approx(1, abs=0.02)
    flat = depth_mod.normalize_depth(np.full((1, 14, 14), 3.0), (20, 10))
    assert flat.shape == (10, 20) and flat.max() == 0


def test_depth_png_round_trip(tmp_path):
    _, depth = _disk_image()
    out = depth_mod.save_depth(depth, tmp_path / "d.png")
    back = depth_mod.load_depth(out)
    assert np.abs(back - depth).max() < 1e-3


# ---------------------------------------------------------------------------------------------
# Camadas
# ---------------------------------------------------------------------------------------------

def test_fill_holes_uses_surrounding_colors():
    rgb = np.zeros((90, 160, 3), dtype=np.float32)
    rgb[...] = (0, 200, 0)
    rgb[30:60, 60:100] = (255, 0, 0)  # "sujeito" que vai virar buraco
    known = np.ones((90, 160), dtype=np.float32)
    known[30:60, 60:100] = 0
    filled = depth_mod.fill_holes(rgb, known)
    center = filled[45, 80]
    assert center[1] > 150 and center[0] < 60  # verde em volta, não o vermelho do sujeito
    assert np.allclose(filled[5, 5], (0, 200, 0))


def test_make_layers_from_synthetic_map(tmp_path):
    image, depth = _disk_image()
    paths = depth_mod.make_layers(image, depth, tmp_path)
    assert [p.name for p in paths] == ["layer0.jpg", "layer1.webp", "layer2.webp"]
    with Image.open(paths[0]) as back:
        assert back.mode == "RGB" and back.size == image.size
        # No fundo, o disco da frente foi preenchido com o azul em volta (sem "fantasma" do sujeito).
        r, g, b = back.getpixel((160, 90))
        assert b > r
    with Image.open(paths[-1]) as front:
        assert front.mode == "RGBA" and front.size == image.size
        assert front.getpixel((160, 90))[3] > 240  # o disco está na frente
        assert front.getpixel((3, 3))[3] < 10  # o canto de cima (longe) não
        assert front.getpixel((160, 90))[0] > 180  # e a cor é a do disco


def test_layer_thresholds_on_flat_map_stay_increasing():
    cuts = depth_mod.layer_thresholds(np.zeros((10, 10), dtype=np.float32), 3)
    assert len(cuts) == 2 and cuts[0] < cuts[1]


def test_flat_map_gives_a_single_opaque_layer(tmp_path):
    # Mapa constante (imagem uniforme ou saída constante do modelo): nada de cópias meio transparentes.
    image, _ = _disk_image()
    paths = depth_mod.make_layers(image, np.zeros((180, 320), dtype=np.float32), tmp_path)
    assert [p.name for p in paths] == ["layer0.jpg"]
    with Image.open(paths[0]) as back:
        diff = np.abs(np.asarray(back, dtype=np.float32) - np.asarray(image, dtype=np.float32)).mean()
    assert diff < 5  # é a imagem original, não a cor média


def test_far_plateau_is_never_half_transparent_in_front_layers(tmp_path):
    # Metade da imagem no ponto mais longe (o percentil cortado zera o céu): o primeiro corte cairia em 0 e o céu
    # ficaria com alfa 0,5 nas camadas da frente.
    image, _ = _disk_image()
    depth = np.zeros((180, 320), dtype=np.float32)
    depth[:, 160:] = np.linspace(0, 1, 160, dtype=np.float32)[None, :]
    paths = depth_mod.make_layers(image, depth, tmp_path)
    assert len(paths) == 3
    for path in paths[1:]:
        with Image.open(path) as layer:
            alpha = np.asarray(layer)[..., 3]
        assert alpha[:, :140].max() < 5


# ---------------------------------------------------------------------------------------------
# Download do modelo (simulado)
# ---------------------------------------------------------------------------------------------

def _mock_stream(payload: bytes, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "huggingface.co"
        assert depth_mod.MODEL_REVISION in request.url.path
        return httpx.Response(status, content=payload)

    def stream(method, url, **kwargs):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        return client.stream(method, url)

    return stream


def test_download_checks_size_and_hash(isolated_settings, monkeypatch):
    monkeypatch.setattr(isolated_settings, "offline", False)
    payload = b"modelo-falso" * 100
    monkeypatch.setattr(depth_mod, "MODEL_SIZE", len(payload))
    monkeypatch.setattr(depth_mod, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(depth_mod.httpx, "stream", _mock_stream(payload))
    assert not depth_mod.model_ready()
    path = depth_mod.ensure_model()
    assert path.read_bytes() == payload and depth_mod.model_ready()
    assert depth_mod.availability() == (True, "ok")


def test_download_rejects_corrupted_file(isolated_settings, monkeypatch):
    monkeypatch.setattr(isolated_settings, "offline", False)
    monkeypatch.setattr(depth_mod.httpx, "stream", _mock_stream(b"outra coisa"))
    with pytest.raises(depth_mod.DepthUnavailable, match="corrompido"):
        depth_mod.ensure_model()
    assert not depth_mod.model_ready()
    assert not list(depth_mod.models_dir().glob("*.part"))


def test_download_disk_error_cleans_up_and_is_unavailable(isolated_settings, monkeypatch):
    monkeypatch.setattr(isolated_settings, "offline", False)
    payload = b"x" * 3000
    monkeypatch.setattr(depth_mod, "MODEL_SIZE", len(payload))
    monkeypatch.setattr(depth_mod, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(depth_mod.httpx, "stream", _mock_stream(payload))

    def full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(depth_mod.shutil, "move", full_disk)
    with pytest.raises(depth_mod.DepthUnavailable, match="gravar"):
        depth_mod.ensure_model()
    assert not list(depth_mod.models_dir().glob("*.part"))


def test_download_can_be_cancelled_between_chunks(isolated_settings, monkeypatch):
    monkeypatch.setattr(isolated_settings, "offline", False)
    payload = b"y" * (3 << 20)
    monkeypatch.setattr(depth_mod, "MODEL_SIZE", len(payload))
    monkeypatch.setattr(depth_mod, "MODEL_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(depth_mod.httpx, "stream", _mock_stream(payload))
    seen: list[int] = []

    class Stop(Exception):
        pass

    def on_chunk(done: int) -> None:
        seen.append(done)
        raise Stop

    with pytest.raises(Stop):
        depth_mod.ensure_model(on_chunk=on_chunk)
    assert len(seen) == 1 and not depth_mod.model_ready()
    assert not list(depth_mod.models_dir().glob("*.part"))


def test_offline_never_downloads(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("não pode baixar no modo offline")

    monkeypatch.setattr(depth_mod.httpx, "stream", boom)
    ok, reason = depth_mod.availability()
    assert not ok and "offline" in reason
    with pytest.raises(depth_mod.DepthUnavailable):
        depth_mod.ensure_model()


# ---------------------------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------------------------

def _doc(motion: str | None) -> ProjectDoc:
    seg = Segment(id="seg_0", text={"pt-BR": "uma frase curta para a cena."})
    scenes = [Scene(id=f"scn_{k}", segment_id=seg.id, part=k, parts=2, visual=Visual(prompt="x"), motion=motion)
              for k in range(2)]
    return ProjectDoc(id="prj_p", title="t", style_id="misterios-short", segments=[seg], scenes=scenes, seed=1)


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    words = doc.segments[0].text["pt-BR"].split()
    return {"seg_0": NarrationClip(segment_id="seg_0", path="x.wav", duration=4.0,
                                   words=[Word(w, i * 0.6, i * 0.6 + 0.5) for i, w in enumerate(words)])}


def test_parallax_is_a_camera_move():
    assert "parallax" in CAMERA_MOVES


def test_llms_are_not_offered_parallax():
    # Storyboard e estilo gerado não escolhem parallax sozinhos (download de 99 MB e CPU por imagem).
    from studio.core.styles.schema import AUTO_CAMERA_MOVES
    from studio.workers.pipeline import authoring

    assert "parallax" not in AUTO_CAMERA_MOVES and "parallax" not in authoring._MOTIONS


def test_timeline_with_a_single_layer_falls_back_to_ken_burns():
    doc = _doc("parallax")
    timeline = build_timeline(doc, get_style("misterios-short"), "pt-BR", _clips(doc),
                              {"scn_0": "a.jpg", "scn_1": "b.jpg"}, {"scn_0": ["cache/l0.jpg"]})
    assert timeline["scenes"][0]["motion"] == "ken_burns_in" and "parallax" not in timeline["scenes"][0]


def test_timeline_carries_layers_or_falls_back_to_ken_burns():
    doc = _doc("parallax")
    style = get_style("misterios-short")
    images = {"scn_0": "cache/a.jpg", "scn_1": "cache/b.jpg"}
    layers = {"scn_0": ["cache/l0.jpg", "cache/l1.webp", "cache/l2.webp"]}
    timeline = build_timeline(doc, style, "pt-BR", _clips(doc), images, layers)
    first, second = timeline["scenes"]
    assert first["motion"] == "parallax" and first["parallax"] == {"layers": layers["scn_0"]}
    assert second["motion"] == "ken_burns_in" and "parallax" not in second


def test_timeline_without_parallax_is_unchanged():
    doc = _doc("drift")
    style = get_style("misterios-short")
    images = {"scn_0": "a.jpg", "scn_1": "b.jpg"}
    a = build_timeline(doc, style, "pt-BR", _clips(doc), images)
    b = build_timeline(doc, style, "pt-BR", _clips(doc), images, {"scn_0": ["x"]})
    assert a == b


# ---------------------------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------------------------

def _run(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _project_with_parallax() -> tuple[str, list[str]]:
    doc = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado da ilha")
    _run("script", doc.id)
    _run("storyboard", doc.id)
    doc = service.get_doc(doc.id)
    targets = [s.id for s in doc.ordered_scenes() if s.kind == "image"][:2]
    assert targets
    ops = parse_ops([{"op": "set_scene_motion", "scene_id": sid, "motion": "parallax"} for sid in targets])
    service.commit(doc.id, apply_ops(doc, ops, get_style(doc.style_id)), "parallax")
    return doc.id, targets


def _timeline(settings, preview: dict) -> dict:
    entry = preview["langs"]["pt-BR"]
    return json.loads((settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))


def test_build_makes_layers_and_reuses_cache(isolated_settings, synthetic_depth):
    project_id, targets = _project_with_parallax()
    plan_before = plan(project_id, mode="preview")
    assert set(targets) <= set(plan_before["parallax_scenes"])
    assert plan_before["depth_model_mb"] == depth_mod.MODEL_MB
    preview = _run("preview", project_id)
    scenes = {s["id"]: s for s in _timeline(isolated_settings, preview)["scenes"]}
    for sid in targets:
        scene = scenes[sid]
        assert scene["motion"] == "parallax"
        layers = scene["parallax"]["layers"]
        assert len(layers) == depth_mod.LAYERS
        assert all((isolated_settings.data_dir / rel).is_file() for rel in layers)
    assert preview["generated"].get("depth", 0) >= 1 and preview["generated"].get("parallax", 0) >= 1
    assert any(a.startswith("parallax:") for a in preview["artifacts"])
    assert not any("Parallax" in w for w in preview["warnings"])

    again = _run("preview", project_id)
    assert again["generated"].get("depth", 0) == 0 and again["generated"].get("parallax", 0) == 0
    assert again["reused"].get("parallax", 0) >= 1


def test_plan_says_why_parallax_is_unavailable(isolated_settings):
    # Modo offline sem o modelo: o plano não promete download; diz que vira Ken Burns.
    project_id, _ = _project_with_parallax()
    result = plan(project_id, mode="preview")
    assert "depth_model_mb" not in result and "offline" in result["parallax_unavailable"]


def test_cached_layers_survive_without_model(isolated_settings, synthetic_depth, monkeypatch):
    project_id, targets = _project_with_parallax()
    _run("preview", project_id)
    # Depois: o modelo some (pacote quebrado, arquivo apagado). As camadas guardadas continuam valendo.
    depth_mod.set_estimator(None)
    monkeypatch.setattr(depth_mod, "availability", lambda: (False, "pacote onnxruntime não instalado"))
    doc = service.get_doc(project_id)
    service.commit(project_id, doc, "de novo")
    preview = _run("preview", project_id)
    scenes = {s["id"]: s for s in _timeline(isolated_settings, preview)["scenes"]}
    for sid in targets:
        assert scenes[sid]["motion"] == "parallax" and len(scenes[sid]["parallax"]["layers"]) == depth_mod.LAYERS
    assert not any("Parallax 2.5D" in w for w in preview["warnings"])
    assert preview["reused"].get("parallax", 0) >= 1


def test_build_without_model_falls_back_with_warning(isolated_settings):
    # Modo offline e modelo ausente (o padrão nos testes): nada é baixado, a cena fica com o Ken Burns.
    project_id, targets = _project_with_parallax()
    preview = _run("preview", project_id)
    scenes = {s["id"]: s for s in _timeline(isolated_settings, preview)["scenes"]}
    for sid in targets:
        assert scenes[sid]["motion"] == "ken_burns_in" and "parallax" not in scenes[sid]
    warning = next(w for w in preview["warnings"] if "Parallax 2.5D" in w)
    assert "Ken Burns" in warning and "offline" in warning
