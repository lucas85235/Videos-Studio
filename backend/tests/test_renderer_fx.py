"""renderer-fx (ADR 0004, 3.9): primitivas novas do Remotion para planos, câmera por eventos e eventos de tela.

Três camadas de conferência:
- contrato estático entre o `types.ts` resolvido e os componentes (quem lê o quê e em que ordem a cena empilha);
- as contas puras de `components/fx/math.ts` rodando no Node (punch, zoom no retângulo, tremor, whip, tempo,
  planos), sem navegador; pula quando não há Node com remoção de tipos (22.6+);
- quadros renderizados de verdade (`renderer/scripts/fx-stills.mjs`), só com VS_RENDER_STILLS=1: precisa do navegador
  do Remotion e leva ~1 min. O punch no quadro N tem que sair com escala maior que 1,1.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from studio.config import REPO_ROOT
from studio.core.project.document import FX_TYPES

RD = REPO_ROOT / "renderer" / "src"
COMP = RD / "components"
MATH = COMP / "fx" / "math.ts"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- contrato estático -------------------------------------------------------------------------------------------


def test_new_components_exist() -> None:
    for rel in ("CameraRig.tsx", "ShotStack.tsx", "fx/FxLayer.tsx", "fx/math.ts"):
        assert (COMP / rel).is_file(), rel
    assert "export const CameraRig" in _read(COMP / "CameraRig.tsx")
    assert "export const TimeWarp" in _read(COMP / "CameraRig.tsx")
    assert "export const ShotStack" in _read(COMP / "ShotStack.tsx")
    fx = _read(COMP / "fx" / "FxLayer.tsx")
    for name in ("FxLayer", "FxAnchored"):
        assert f"export const {name}" in fx
    for name in ("TextPop", "Meme", "Highlight", "Annot", "Counter", "Flash", "Pip"):
        assert f"const {name}: React.FC" in fx, name


def test_sceneview_stacks_rig_effects_fx_card_captions() -> None:
    """O CameraRig envolve só o fundo; o FxLayer fica entre as texturas e o cartão, abaixo das legendas."""
    src = _read(COMP / "SceneView.tsx")
    i_rig = src.index("<CameraRig")
    i_bg = src.index("{background}")
    i_rig_end = src.index("</CameraRig>")
    i_fx = src.index("<EffectsLayer")
    i_layer = src.index("<FxLayer")
    i_card = src.index("{card && cardFrom > 0")
    i_caps = src.index("<Captions lines=")
    assert i_rig < i_bg < i_rig_end < i_fx < i_layer < i_card < i_caps
    assert "clean={scene.clean === true}" in src
    # Cena com planos passa pelo ShotStack; sem planos, o fundo de sempre dentro do TimeWarp.
    assert "slots.length ? (\n    <ShotStack" in src
    assert "<TimeWarp fx={fx}" in src


def test_layers_return_the_old_tree_without_new_fields() -> None:
    """Sem `fx`/`shots`/`clean`, cada peça nova devolve o fundo como está (o quadro antigo não muda)."""
    rig = _read(COMP / "CameraRig.tsx")
    assert "if (!hasCamera(fx)) return <>{children}</>;" in rig
    assert "if (!segs.length) return <>{children}</>;" in rig
    assert "if (!items.length) return null;" in _read(COMP / "fx" / "FxLayer.tsx")
    visual = _read(COMP / "VisualLayer.tsx")
    # A curva antiga continua sendo a padrão; o punch_zoom do quadro 0 continua por compatibilidade.
    assert "easing: Easing.inOut(Easing.quad)" in visual
    assert "case 'punch_zoom':" in visual
    assert "case 'push':" in visual and "case 'none':" in visual
    assert ") : overlay ? (" in visual  # só com camada presa a imagem ganha invólucro


def test_effects_layer_skips_grain_and_vignette_on_clean_scene() -> None:
    src = _read(COMP / "EffectsLayer.tsx")
    assert "const DIRTY = new Set(['grain', 'vignette']);" in src
    assert "if (clean && DIRTY.has(effect.type)) continue;" in src


def test_cliplayer_plays_any_video_with_trim_rate_hold_muted() -> None:
    src = _read(COMP / "ClipLayer.tsx")
    for token in ("OffthreadVideo", "muted", "trimBefore={trim}", "playbackRate={rate}", "hold", "<Freeze"):
        assert token in src, token
    shots = _read(COMP / "ShotStack.tsx")
    for field in ("clipIn", "speed", "hold", "seconds", "framing", "easing", "motion"):
        assert re.search(rf"shot\?\.{field}\b", shots), field


def test_captions_emphasis() -> None:
    src = _read(COMP / "Captions.tsx")
    assert "EMPH_SCALE = 1.3" in src
    assert '"Noto Emoji"' in src
    assert "w.emph ? (" in src  # só a palavra marcada troca o realce de +6%
    assert "translateY(${-0.06 * fontSize * pop}px)" in src  # as outras continuam com ele


def test_sticker_filter_is_shared() -> None:
    assert "export const stickerFilter" in _read(RD / "sticker.ts")
    assert "export { stickerFilter } from './sticker';" in _read(RD / "lib.ts")
    assert "stickerFilter(outline, scale)" in _read(COMP / "fx" / "FxLayer.tsx")


def test_math_module_has_only_type_imports() -> None:
    """`fx/math.ts` roda no Node (teste abaixo): nada de import em tempo de execução."""
    imports = [line for line in _read(MATH).splitlines() if line.startswith("import ")]
    assert imports and all(line.startswith("import type ") for line in imports)


def test_sample_covers_every_event_shot_kind_and_a_clip() -> None:
    src = _read(RD / "sample.ts")
    block = src[src.index("const fxShowcase"):]
    for fx_type in FX_TYPES:
        assert f"type: '{fx_type}'" in block, fx_type
    shots = block[block.index("shots: ["):block.index("fx: [")]
    assert "video: SAMPLE_CLIP" in shots and "clipIn:" in shots and "hold: true" in shots
    assert "src: SAMPLE_PHOTO" in shots  # plano do acervo
    assert "{ from: 0, dur: 50, prov: 'capture'" in shots  # plano da própria cena
    for motion in ("none", "push", "drift"):
        assert f"motion: '{motion}'" in shots
    assert "emph: 'emoji'" in block and "emph: 'pop'" in block and "emph: 'color'" in block
    assert "variant: 'freeze_zoom'" in src
    assert 'id="Edicao"' in _read(RD / "Root.tsx")


def test_renderer_hash_changes_with_fx_components(tmp_path: Path) -> None:
    from studio.core.hashing import hash_tree

    copy = tmp_path / "src"
    shutil.copytree(RD, copy)
    with_fx = hash_tree(copy)
    (copy / "components" / "fx" / "FxLayer.tsx").unlink()
    assert hash_tree(copy) != with_fx


# --- contas puras no Node --------------------------------------------------------------------------------------------

HARNESS = r"""
import { pathToFileURL } from 'node:url';
const m = await import(pathToFileURL(process.argv[2]).href);
const fps = 30;
const ctx = { fps, frames: 120, cuts: [60], width: 1920, height: 1080, seed: 7 };
const punch = { type: 'punch', from: 20, dur: 0, params: { scale: 1.2 } };
const out = {};
out.punch = [19, 20, 21, 22, 23, 59, 60].map((f) => m.punchScale(punch, f, ctx));
out.punchFree = [40, 41, 42, 43, 44].map((f) => m.punchScale({ ...punch, from: 30, dur: 10 }, f, ctx));
out.camPunch = m.cameraAt(23, [punch], ctx);
out.camNone = m.cameraAt(23, [], ctx);
out.camAudio = m.cameraAt(23, [{ type: 'sfx', from: 20, dur: 0, params: {} }], ctx);
const zr = (rect, f) => m.cameraAt(f, [{ type: 'zoom_rect', from: 10, dur: 0, params: { rect } }], ctx);
out.zoomCenter = zr([0.4, 0.4, 0.2, 0.2], 40);
out.zoomCorner = zr([0.85, 0.85, 0.1, 0.1], 40);
out.zoomAfterCut = zr([0.4, 0.4, 0.2, 0.2], 60);
const shake = { type: 'shake', from: 0, dur: 30, params: { amount: 1 } };
out.shake = Array.from({ length: 30 }, (_, f) => m.cameraAt(f, [shake], ctx));
const whip = { type: 'whip', from: 60, dur: 8, params: {} };
out.whip = [50, 57, 59, 60, 62, 70].map((f) => m.cameraAt(f, [whip], ctx));
const segs = m.timeSegments([{ type: 'freeze', from: 10, dur: 10, params: {} }, { type: 'speed', from: 40, dur: 20, params: { rate: 0.5 } }], ctx);
out.remap = [5, 10, 15, 19, 20, 25, 40, 50, 60, 70].map((f) => m.remapFrame(f, segs));
out.memeSegs = m.timeSegments([{ type: 'meme', from: 5, dur: 10, params: { variant: 'freeze_zoom' } }, { type: 'meme', from: 30, dur: 10, params: {} }], ctx);
out.slots = m.shotSlots([{ from: 30, dur: 20 }, { from: 10, dur: 5 }, { from: 30, dur: 9, tag: 'last' }], 120);
out.slotsEmpty = m.shotSlots(undefined, 120);
out.cuts = m.cutsOf(out.slots);
out.spanTextPop = m.fxSpan({ type: 'text_pop', from: 10, dur: 0, params: {} }, ctx);
out.spanHighlight = m.fxSpan({ type: 'highlight', from: 10, dur: 0, params: {} }, ctx);
out.spanHighlightLong = m.fxSpan({ type: 'highlight', from: 10, dur: 90, params: {} }, ctx);
out.spanCounter = m.fxSpan({ type: 'counter', from: 10, dur: 0, params: {} }, ctx);
out.spanLate = m.fxSpan({ type: 'flash', from: 500, dur: 6, params: {} }, ctx);
out.framingFull = m.framingTransform({ scale: 1, x: 0.5, y: 0.5 });
out.framingBox = m.framingBox({ scale: 2, x: 1, y: 0 });
out.rects = m.pRects({ rects: [0.1, 0.1, 0.2, 0.2, 0.5, 0.5, 0.9, 0.9] });
out.rectsNested = m.pRects({ rect: [[0.1, 0.2, 0.3, 0.4]] });
out.snap = [0, 0.11, 0.22, 0.5, 1].map((t) => m.EASE.snap(t));
console.log(JSON.stringify(out));
"""


def _node() -> list[str] | None:
    node = shutil.which("node")
    if not node:
        return None
    try:
        version = subprocess.run([node, "--version"], capture_output=True, text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    match = re.match(r"v(\d+)\.(\d+)", version)
    if not match:
        return None
    major, minor = int(match.group(1)), int(match.group(2))
    if major >= 23:
        return [node]
    if major == 22 and minor >= 6:
        return [node, "--experimental-strip-types"]
    return None


@pytest.fixture(scope="module")
def fx_math(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = _node()
    if node is None:
        pytest.skip("Node 22.6+ (remoção de tipos) indisponível")
    script = tmp_path_factory.mktemp("fxmath") / "harness.mjs"
    script.write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([*node, "--no-warnings", str(script), str(MATH)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_punch_rises_in_three_frames_holds_and_releases_at_cut(fx_math: dict) -> None:
    before, f0, f1, f2, f3, held, at_cut = fx_math["punch"]
    assert before == 1
    assert 1.1 < f0 < 1.2 and f0 < f1 < f2  # sobe em 2 a 3 quadros
    assert f2 == pytest.approx(1.2) and f3 == pytest.approx(1.2) and held == pytest.approx(1.2)
    assert at_cut == 1  # solta seco no corte
    # Sem corte no fim: solta em ~4 quadros.
    end, *release = fx_math["punchFree"]
    assert end == 1 or end < 1.2
    assert release[0] < 1.2 and release[-1] == 1


def test_camera_punch_scale_and_identity(fx_math: dict) -> None:
    cam = fx_math["camPunch"]
    assert cam["scale"] > 1.1
    # Punch no centro: o centro da tela fica no lugar (x = 0,5·(1 − s)).
    assert cam["x"] == pytest.approx(0.5 * (1 - cam["scale"]), abs=1e-6)
    for key in ("camNone", "camAudio"):
        assert fx_math[key] == {"scale": 1, "x": 0, "y": 0, "rot": 0, "blur": 0}


def test_zoom_rect_reaches_target_without_showing_border(fx_math: dict) -> None:
    center = fx_math["zoomCenter"]
    assert center["scale"] == pytest.approx(4.0 * 0.8 if 4.0 * 0.8 <= 2.5 else 2.5, rel=1e-3)
    # O centro do retângulo (0,5) cai no centro da tela.
    assert 0.5 * center["scale"] + center["x"] == pytest.approx(0.5, abs=1e-3)
    corner = fx_math["zoomCorner"]
    assert corner["scale"] > 2
    assert corner["x"] == pytest.approx(1 - corner["scale"], abs=1e-6)  # encosta na borda, não passa
    assert fx_math["zoomAfterCut"]["scale"] == 1  # o zoom solta no corte


def test_shake_decays_and_covers_the_frame(fx_math: dict) -> None:
    shakes = fx_math["shake"]
    offset = [abs(c["x"] - 0.5 * (1 - c["scale"])) for c in shakes]
    assert max(offset[:8]) > max(offset[-8:]) * 3
    assert max(offset) > 0.002
    for cam in shakes:
        # A sobra de escala cobre o deslocamento: a borda nunca entra no quadro.
        assert cam["scale"] >= 1
        assert cam["x"] <= 1e-6 and cam["x"] + cam["scale"] >= 1 - 1e-6
        assert cam["y"] <= 1e-6 and cam["y"] + cam["scale"] >= 1 - 1e-6


def test_whip_blurs_around_the_cut_only(fx_math: dict) -> None:
    far_before, before, last, cut, after, far_after = fx_math["whip"]
    assert far_before["blur"] == 0 and far_after["blur"] == 0
    assert before["blur"] > 0 and last["blur"] > before["blur"]
    assert cut["blur"] >= after["blur"] > 0
    assert last["x"] < 0 < cut["x"] + 0.5 * (cut["scale"] - 1)  # sai para um lado e entra do outro


def test_freeze_and_speed_remap_time(fx_math: dict) -> None:
    assert fx_math["remap"] == [5, 10, 10, 10, 10, 15, 30, 35, 40, 50]
    segs = fx_math["memeSegs"]
    assert [(s["from"], s["to"], s["rate"]) for s in segs] == [(5, 15, 0)]  # só o meme "congela + zoom"


def test_shot_slots_are_contiguous(fx_math: dict) -> None:
    slots = fx_math["slots"]
    assert [(s["from"], s["to"]) for s in slots] == [(0, 10), (10, 30), (30, 120)]
    assert slots[0]["shot"] is None  # antes do 1º plano: o visual da cena
    assert slots[2]["shot"]["tag"] == "last"  # mesmo `from`: vale o último
    assert fx_math["slotsEmpty"] == []
    assert fx_math["cuts"] == [10, 30]


def test_fx_spans_use_type_defaults(fx_math: dict) -> None:
    assert fx_math["spanTextPop"] == {"from": 10, "to": 37}  # 0,9 s a 30 fps
    assert fx_math["spanHighlight"] == {"from": 10, "to": 60}  # até o corte
    assert fx_math["spanHighlightLong"] == {"from": 10, "to": 60}  # preso à imagem: nunca passa do corte
    assert fx_math["spanCounter"] == {"from": 10, "to": 120}  # até o fim da cena
    assert fx_math["spanLate"] == {"from": 119, "to": 120}  # gatilho além da cena cai no último quadro


def test_framing_and_rect_params(fx_math: dict) -> None:
    assert fx_math["framingFull"] is None
    assert fx_math["framingBox"] == {"scale": 2, "left": 0.5, "top": 0}
    assert fx_math["rects"] == [[0.1, 0.1, 0.2, 0.2], [0.5, 0.5, 0.5, 0.5]]  # o 2º é cortado na borda
    assert fx_math["rectsNested"] == [[0.1, 0.2, 0.3, 0.4]]
    snap = fx_math["snap"]
    assert snap[0] == 0 and snap[2] == pytest.approx(1) and snap[3] == 1 and snap[1] > 0.8


# --- quadros renderizados (opt-in) -------------------------------------------------------------------------------------


@pytest.mark.skipif(os.environ.get("VS_RENDER_STILLS") != "1", reason="render de quadros só com VS_RENDER_STILLS=1")
def test_rendered_stills_show_punch(tmp_path: Path) -> None:
    from PIL import Image

    node = _node()
    if node is None:
        pytest.skip("Node 22.6+ indisponível")
    cmd = [*node, "--no-warnings", str(REPO_ROOT / "renderer" / "scripts" / "fx-stills.mjs"), "--out", str(tmp_path)]
    if os.environ.get("VS_BROWSER"):
        cmd += ["--browser", os.environ["VS_BROWSER"]]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=REPO_ROOT / "renderer")
    assert proc.returncode == 0, proc.stderr[-4000:]
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    stills = {int(k): Path(v) for k, v in result["stills"].items()}
    assert all(p.is_file() for p in stills.values())

    def square_width(path: Path) -> int:
        im = Image.open(path).convert("L")
        w, h = im.size
        row = [im.getpixel((x, h // 2)) for x in range(w)]
        lit = [x for x, v in enumerate(row) if v > 128]
        return lit[-1] - lit[0] + 1

    at = result["punch"]["at"]
    ratio = square_width(stills[at + 3]) / square_width(stills[at - 1])
    assert ratio > 1.1
    assert ratio == pytest.approx(result["punch"]["scale"], abs=0.03)
    # Nenhum quadro sai preto (planos, clipe e eventos desenharam algo).
    for frame, path in stills.items():
        extrema = Image.open(path).convert("L").getextrema()
        assert extrema[1] - extrema[0] > 40, frame
