"""renderer-fx (ADR 0004, 3.9): primitivas novas do Remotion para planos, câmera por eventos e eventos de tela.

Três camadas de conferência:
- contrato estático entre o `types.ts` resolvido e os componentes (quem lê o quê e em que ordem a cena empilha);
- as contas puras de `components/fx/math.ts` (e `frame.ts`) rodando no Node com entradas no formato do backend
  (`dur` sempre preenchido, `rect` no espaço do arquivo): punch que segura até o corte, zoom no retângulo, tremor,
  whip, tempo, anotação no relógio real depois de um congelamento, recorte de captura alta, teto do clipe oficial e
  meme fora da legenda. Sem navegador; pula quando não há Node com remoção de tipos (22.6+);
- quadros renderizados de verdade (`renderer/scripts/fx-stills.mjs`), só com VS_RENDER_STILLS=1: usa o
  chrome-headless-shell já baixado (`node_modules/.remotion`, ou VS_BROWSER) e pula sem ele, nunca baixa nada. O punch
  no quadro N tem que sair com escala maior que 1,1.
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
FRAME = RD / "frame.ts"


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


def test_source_capture_stays_readable() -> None:
    """A prova da fonte: o fundo do cartão de dado que entra depois do corte fica nítido até o cartão chegar (a
    Malwarebytes só aparecia desfocada atrás dos cartões), e a captura na moldura de tela é sempre janela (o celular
    mostrava uma faixa do meio da página, com a manchete cortada dos dois lados)."""
    scene = _read(COMP / "SceneView.tsx")
    assert "const backdrop = cardFrom > 0 ? interpolate(frame, [cardFrom," in scene
    assert "(DIM[kind] ?? 0) * backdrop" in scene and "(BLUR[kind] ?? 0) * backdrop" in scene
    treatments = _read(COMP / "Treatments.tsx")
    assert "if (page) return 'window';" in treatments
    assert "screenVariant(layoutSeed(scene), width, height, scene.prov === 'capture')" in treatments


def test_cliplayer_plays_any_video_with_trim_rate_hold_muted() -> None:
    src = _read(COMP / "ClipLayer.tsx")
    for token in ("OffthreadVideo", "muted", "trimBefore={trim}", "playbackRate={rate}", "hold", "<Freeze"):
        assert token in src, token
    shots = _read(COMP / "ShotStack.tsx")
    for field in ("clipIn", "speed", "hold", "framing", "easing", "motion"):
        assert re.search(rf"shot\?\.{field}\b", shots), field
    assert "seconds={shotSeconds(shot)}" in shots  # `seconds` do backend, ou o teto do oficial


def test_scene_clip_plays_the_ai_video_and_the_real_clip() -> None:
    """`scene.video` é o vídeo IA da cena-chave ou o clipe real da cena (proxy já cortado na entrada pelo build, que o
    manda na cena e não em `shots`): os dois tocam, também no plano `self`."""
    src = _read(COMP / "SceneView.tsx")
    assert "const realClip = scene.prov !== undefined && scene.prov !== 'ai';" in src
    assert "const clipUrl = scene.kind === 'video' || realClip ? assetUrl(scene.video, assetBaseUrl) : null;" in src
    assert "selfBackground={otherBackground()}" in src


def test_self_shots_and_anchored_overlay_use_the_right_clock() -> None:
    """Plano `self` passa pelo TimeWarp; a anotação presa à imagem usa o quadro real da cena, não o do fundo."""
    shots = _read(COMP / "ShotStack.tsx")
    assert shots.count("<TimeWarp fx={fx} frames={frames} cuts={cuts}") == 2  # plano self e planos com material
    fx = _read(COMP / "fx" / "FxLayer.tsx")
    anchored = fx[fx.index("export const FxAnchored"):fx.index("/** Marca-texto varrendo")]
    assert "useCurrentFrame" not in anchored and "<Sequence" not in anchored
    assert "anchoredAt(fx, frame, ctx)" in anchored
    for name in ("const Highlight", "const Annot"):
        body = fx[fx.index(name):]
        body = body[:body.index("\n};\n")]
        assert "useCurrentFrame" not in body, name
    scene = _read(COMP / "SceneView.tsx")
    assert "const frame = useCurrentFrame();" in scene
    assert "<FxAnchored fx={fx} frame={frame}" in scene
    assert "<CameraRig fx={camFx}" in scene  # zoom_rect convertido para a tela


def test_meme_without_media_warns_and_corner_avoids_captions() -> None:
    fx = _read(COMP / "fx" / "FxLayer.tsx")
    assert "warnMemeWithoutMedia(params, where);" in fx
    assert "lib_id" in fx[:fx.index("const clampOpts")]  # contrato de chaves registrado no topo
    assert "cornerStickerBox(height > width, caption)" in fx
    assert "captionHalf={captionHalf}" in _read(COMP / "SceneView.tsx")


def test_karaoke_emphasis_waits_for_the_word() -> None:
    src = _read(COMP / "Captions.tsx")
    assert "color={style.style !== 'karaoke' || spoken ? style.highlight : dimmed ? dimColor : style.color}" in src


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
    """`fx/math.ts` e `frame.ts` rodam no Node (teste abaixo): nada de import em tempo de execução."""
    for path in (MATH, FRAME):
        imports = [line for line in _read(path).splitlines() if line.startswith("import ")]
        assert imports and all(line.startswith("import type ") for line in imports), path.name


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
const fr = await import(pathToFileURL(process.argv[3]).href);
const fps = 30;
const ctx = { fps, frames: 120, cuts: [60], width: 1920, height: 1080, seed: 7 };
// Entradas como o backend manda: `dur` sempre preenchido (FX_DEFAULT_MS a 30 fps), nunca 0.
const punch = { type: 'punch', from: 20, dur: 18, params: { scale: 1.2 } };
const out = {};
out.defaults = m.FX_DEFAULT_SECONDS;
out.punch = [19, 20, 21, 22, 23, 40, 59, 60].map((f) => m.punchScale(punch, f, ctx));
const far = { fps, frames: 200, cuts: [150], width: 1920, height: 1080, seed: 7 };
out.punchFar = [37, 38, 39, 41, 42].map((f) => m.punchScale(punch, f, far));
out.spanPunchZero = m.fxSpan({ ...punch, dur: 0 }, far);
out.spanPunchFar = m.fxSpan(punch, far);
out.camPunch = m.cameraAt(23, [punch], ctx);
out.camNone = m.cameraAt(23, [], ctx);
out.camAudio = m.cameraAt(23, [{ type: 'sfx', from: 20, dur: 15, params: {} }], ctx);
const zr = (rect, f) => m.cameraAt(f, [{ type: 'zoom_rect', from: 10, dur: 45, params: { rect } }], ctx);
out.zoomCenter = zr([0.4, 0.4, 0.2, 0.2], 40);
out.zoomCorner = zr([0.85, 0.85, 0.1, 0.1], 40);
out.zoomHeld = zr([0.4, 0.4, 0.2, 0.2], 58);
out.zoomAfterCut = zr([0.4, 0.4, 0.2, 0.2], 60);
const shake = (params) => ({ type: 'shake', from: 0, dur: 30, params });
out.shake = Array.from({ length: 30 }, (_, f) => m.cameraAt(f, [shake({ strength: 1 })], ctx));
const peak = (params) => Math.max(...Array.from({ length: 30 }, (_, f) => {
  const c = m.cameraAt(f, [shake(params)], ctx);
  return Math.abs(c.rot);
}));
out.shakeWeak = peak({ strength: 0.6 });
out.shakeStrong = peak({ strength: 1.2 });
out.shakeLegacy = peak({ amount: 1.2 });
const whip = { type: 'whip', from: 60, dur: 8, params: {} };
out.whip = [50, 57, 59, 60, 62, 70].map((f) => m.cameraAt(f, [whip], ctx));
const segs = m.timeSegments([{ type: 'freeze', from: 10, dur: 10, params: {} }, { type: 'speed', from: 40, dur: 20, params: { rate: 0.5 } }], ctx);
out.remap = [5, 10, 15, 19, 20, 25, 40, 50, 60, 70].map((f) => m.remapFrame(f, segs));
out.memeSegs = m.timeSegments([{ type: 'meme', from: 5, dur: 10, params: { variant: 'freeze_zoom' } }, { type: 'meme', from: 30, dur: 10, params: {} }], ctx);
// Anotação depois de um congelamento: entra no quadro do gatilho (relógio real), não 24 quadros depois.
const noCut = { fps, frames: 120, cuts: [] };
const frozen = [{ type: 'freeze', from: 30, dur: 24, params: {} }, { type: 'highlight', from: 60, dur: 42, params: { rect: [0.1, 0.1, 0.5, 0.1] } }];
out.anchored59 = m.anchoredAt(frozen, 59, noCut).length;
out.anchored60 = m.anchoredAt(frozen, 60, noCut).map((a) => ({ type: a.fx.type, f: a.f, len: a.len, last: a.last }));
out.remapped60 = m.remapFrame(60, m.timeSegments(frozen, noCut));
out.slots = m.shotSlots([{ from: 30, dur: 20 }, { from: 10, dur: 5 }, { from: 30, dur: 9, tag: 'last' }], 120);
out.slotsEmpty = m.shotSlots(undefined, 120);
out.cuts = m.cutsOf(out.slots);
out.spanTextPop = m.fxSpan({ type: 'text_pop', from: 10, dur: 33, params: {} }, ctx);
out.spanTextPopZero = m.fxSpan({ type: 'text_pop', from: 10, dur: 0, params: {} }, ctx);
out.spanHighlight = m.fxSpan({ type: 'highlight', from: 10, dur: 42, params: {} }, ctx);
out.spanHighlightLong = m.fxSpan({ type: 'highlight', from: 10, dur: 90, params: {} }, ctx);
out.spanHighlightFar = m.fxSpan({ type: 'highlight', from: 10, dur: 42, params: {} }, { fps, frames: 300, cuts: [] });
out.spanShake = m.fxSpan({ type: 'shake', from: 30, dur: 12, params: {} }, ctx);
out.spanCounter = m.fxSpan({ type: 'counter', from: 10, dur: 45, params: {} }, ctx);
out.spanLate = m.fxSpan({ type: 'flash', from: 500, dur: 6, params: {} }, ctx);
out.framingFull = m.framingTransform({ scale: 1, x: 0.5, y: 0.5 });
out.framingBox = m.framingBox({ scale: 2, x: 1, y: 0 });
out.rects = m.pRects({ rects: [0.1, 0.1, 0.2, 0.2, 0.5, 0.5, 0.9, 0.9] });
out.rectsNested = m.pRects({ rect: [[0.1, 0.2, 0.3, 0.4]] });
out.snap = [0, 0.11, 0.22, 0.5, 1].map((t) => m.EASE.snap(t));
// Captura de página alta (1:2) num vídeo 16:9: o `rect` vem no espaço do arquivo.
const video = 1920 / 1080;
const tallTop = { aspect: 0.5, pos: [0.5, 0] };
const tallMid = { aspect: 0.5, pos: [0.5, 0.5] };
out.rectTop = m.fxRects({ rect: [0.1, 0.05, 0.6, 0.02] }, tallTop, video);
out.rectMidOut = m.fxRects({ rect: [0.1, 0.05, 0.6, 0.02] }, tallMid, video);
out.rectFrame = m.fxRects({ rect: [0.1, 0.05, 0.6, 0.02], space: 'frame' }, tallMid, video);
out.rectUnknown = m.fxRects({ rect: [0.1, 0.05, 0.6, 0.02] }, null, video);
out.boxCrop = fr.sceneImageBox({ mode: 'crop', x: 0.5, y: 0.1, aspect: 0.5 }, 1920, 1080);
out.boxBlur = fr.sceneImageBox({ mode: 'blur', x: 0.5, y: 0.5, aspect: 0.5, anchor: 0.5 }, 1920, 1080);
out.boxNone = fr.sceneImageBox(undefined, 1920, 1080);
out.rectBlur = m.fxRects({ rect: [0, 0, 1, 1] }, out.boxBlur, video);
const tallShots = m.shotSlots([
  { from: 0, dur: 60, prov: 'capture', src: 'cap.png', aspect: 0.5, framing: { scale: 1, x: 0.5, y: 0.5 }, motion: 'static' },
  { from: 60, dur: 60, prov: 'capture', src: 'cap.png', aspect: 0.5, framing: { scale: 2, x: 0.5, y: 0.5 }, motion: 'static' },
], 120);
const camFx = (from) => m.cameraFx([{ type: 'zoom_rect', from, dur: 45, params: { rect: [0.1, 0.4, 0.8, 0.1] } }], (at) => m.spaceAt(tallShots, at, null), video)[0];
out.camFxPlain = camFx(10).params;
out.camFxFramed = camFx(70).params;
out.camFxSame = m.cameraFx([punch], () => ({ box: null, framing: null }), video)[0] === punch;
// Clipe do plano: `seconds` do backend; sem ele, o oficial para em 4 s.
out.secOfficial = m.shotSeconds({ prov: 'official', video: 'a.mp4' });
out.secOfficialGiven = m.shotSeconds({ prov: 'official', video: 'a.mp4', seconds: 2.5 });
out.secFree = m.shotSeconds({ prov: 'free', video: 'a.mp4' }) ?? null;
out.timingHold = m.shotClipTiming(4, 210, fps, 1, true);
out.timingHoldNone = m.shotClipTiming(undefined, 210, fps, 1, true);
out.timingCover = m.shotClipTiming(4, 210, fps, 1, false);
out.timingFast = m.shotClipTiming(4, 210, fps, 1.5, true);
// Meme de canto e a faixa da legenda (fração da altura).
out.cornerFree = m.cornerStickerBox(false, null);
out.cornerBottomCaption = m.cornerStickerBox(false, [0.78, 0.94]);
out.cornerMidCaption = m.cornerStickerBox(false, [0.42, 0.58]);
out.cornerTopCaption = m.cornerStickerBox(false, [0.06, 0.2]);
out.cornerPortrait = m.cornerStickerBox(true, [0.62, 0.78]);
// Carimbo solto ("CONFIRMADO" num 16:9): o lugar de sempre quando livre; desvia do texto conhecido.
const geom = { cx: { left: 0.2, right: 0.8 }, halfW: 0.14, halfH: 0.093 };
const right = [{ side: 'right', y: 0.3 }];
const caption = [0, 0.7, 1, 0.2];
out.stampGeom = geom;
out.stampSafe = m.STAMP_SAFE;
out.stampFree = m.placeStamp(right, geom, [caption]);
// Título de 2 linhas no meio ("SEM PC, DEMO OU / BETA"): o carimbo sobe para cima dele, do mesmo lado.
const title = [[0.14, 0.2875, 0.72, 0.19], [0.4, 0.4775, 0.2, 0.19], [0.3, 0.6675, 0.4, 0.045]];
out.stampTitle = m.placeStamp(right, geom, [...title, caption]);
out.stampTitleObstacles = [...title, caption];
// Trecho marcado da captura no lado do carimbo: ele troca de lado na mesma altura.
const marked = [[0.6, 0.25, 0.3, 0.1]];
out.stampMarked = m.placeStamp(right, geom, [...marked, caption]);
out.stampMarkedObstacles = [...marked, caption];
// Sem lugar livre: o de menor sobreposição, dentro da área segura.
const wall = [[0, 0.1, 1, 0.55], caption];
out.stampWall = m.placeStamp(right, geom, wall);
out.stampWallObstacles = wall;
out.stampWallPreferredRect = m.stampRect({ side: 'right', y: 0.3 }, geom);
out.rectTitle = m.stampRect(out.stampTitle, geom);
out.rectMarked = m.stampRect(out.stampMarked, geom);
out.rectWall = m.stampRect(out.stampWall, geom);
// Preferido fora da área segura (carimbo embaixo, colado na borda): entra na área.
out.stampClamped = m.placeStamp([{ side: 'left', y: 0.97 }], geom, []);
// Letras da IMAGEM (manchete da captura, logo): o carimbo desvia quando há lugar; sem lugar livre de letra, nunca
// troca a letra da imagem pela legenda ou pelo título da tela.
const logo = [[0.55, 0.05, 0.4, 0.7]];
out.stampLogo = m.placeStamp(right, geom, [caption], logo);
out.rectLogo = m.stampRect(out.stampLogo, geom);
out.stampLogoSoft = logo;
const page = [[0.0, 0.05, 1.0, 0.62]];
out.stampPage = m.placeStamp(right, geom, [caption], page);
out.rectPage = m.stampRect(out.stampPage, geom);
// Entrada do carimbo (2,2 → 1): no lugar perto da borda, só o que cabe; longe da borda, a de sempre.
out.entryLow = m.stampEntryScale({ side: 'right', y: 1 - m.STAMP_SAFE - geom.halfH }, geom);
out.entryMid = m.stampEntryScale({ side: 'right', y: 0.5 }, geom);
// Faixa do topo sobre a manchete da captura (topo 0,11-0,47): desce para a altura livre mais perto, acima da legenda.
out.bandFree = m.placeBand(0.1, 0.5, 0.035, [caption], [[0.0, 0.3, 1, 0.1]], 0.085, 0.65);
out.bandMoved = m.placeBand(0.1, 0.5, 0.035, [caption], [[0.02, 0.03, 0.1, 0.03], [0.02, 0.11, 0.8, 0.36]], 0.085, 0.65);
out.bandStuck = m.placeBand(0.1, 0.5, 0.035, [caption], [[0, 0, 1, 0.7]], 0.085, 0.65);
// Texto que pula ("JANEIRO") sobre o logo do meio da tela (0,1-0,55): sai para baixo dele, acima da legenda (0,8).
out.popFree = m.textPopY(0.42, 600, 120, 1920, 1080, [[0.3, 0.6, 0.1, 0.05]], 0.8, 0.06);
out.popLogo = m.textPopY(0.42, 600, 120, 1920, 1080, [[0.25, 0.1, 0.5, 0.45]], 0.8, 0.06);
out.popPop = { half: (120 * 0.62) / 1080 };
// Letras da imagem na tela: parada, com pan para baixo (o envelope cobre todo o caminho) e no plano ampliado.
const head = [[0.1, 0.2, 0.6, 0.1]];
out.textStatic = m.textOnScreen([{ rects: head, box: null, framing: null, motion: 'none' }], 0.14, [0.5, 0.5], 16 / 9);
out.textPan = m.textOnScreen([{ rects: head, box: null, framing: null, motion: 'pan_down' }], 0.14, [0.5, 0.5], 16 / 9);
out.textFramed = m.textOnScreen(
  [{ rects: head, box: null, framing: { scale: 2, x: 0.25, y: 0.25 }, motion: 'none' }], 0.14, [0.5, 0.5], 16 / 9);
// Tratamento "tela" (janela da captura): a manchete fica dentro da janela, não onde estaria a tela cheia.
const win = m.screenFrame('window', 1920, 1080);
out.screenMedia = win.media;
out.textScreen = m.textInScreen(head, 16 / 9, win.media, [0.5, 0.5], 1, 1920, 1080);
out.textScreenZoom = m.textInScreen(head, 16 / 9, win.media, [0.5, 0.5], m.screenInnerMax('ken_burns_in', 0.14, false), 1920, 1080);
out.bandOverlap = [m.bandOverlap(0.7, 0.9, [[0, 0.8, 1, 0.05]]), m.bandOverlap(0.7, 0.9, [[0, 0.2, 1, 0.1]])];
out.flow = m.flowRows([300, 200, 900, 100, 100], 40, 1000);
out.entry = [9, 12, 30, 60, 0].map((f) => m.entryFrames(f, 30));
out.entryMax = m.ENTRY_MAX_S;
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
    proc = subprocess.run([*node, "--no-warnings", str(script), str(MATH), str(FRAME)], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_punch_rises_in_three_frames_holds_and_releases_at_cut(fx_math: dict) -> None:
    """`dur` de 18 quadros (os 600 ms do backend) com o corte 22 quadros depois: segura até o corte e solta seco."""
    before, f0, f1, f2, f3, mid, held, at_cut = fx_math["punch"]
    assert before == 1
    assert 1.1 < f0 < 1.2 and f0 < f1 < f2  # sobe em 2 a 3 quadros
    assert f2 == pytest.approx(1.2) and f3 == pytest.approx(1.2)
    assert mid == pytest.approx(1.2) and held == pytest.approx(1.2)  # passou do `dur` e continua segurando
    assert at_cut == 1  # solta seco no corte


def test_punch_releases_after_dur_when_the_cut_is_far(fx_math: dict) -> None:
    """Corte longe (mais de HOLD_TO_CUT_S depois): solta no fim do `dur`, numa rampa de 4 quadros."""
    assert fx_math["spanPunchFar"] == {"from": 20, "to": 38}
    assert fx_math["spanPunchZero"] == fx_math["spanPunchFar"]  # sem `dur`: o padrão do tipo (600 ms)
    last, *release = fx_math["punchFar"]
    assert last == pytest.approx(1.2)
    assert release[0] < 1.2 and release[1] < release[0] and release[-1] == 1


def test_fx_defaults_match_backend(fx_math: dict) -> None:
    """A tabela do renderer espelha o `FX_DEFAULT_MS` do timeline-fx (fonte de verdade: o backend sempre preenche)."""
    defaults = fx_math["defaults"]
    assert set(defaults) == set(FX_TYPES)
    edit_resolve = pytest.importorskip("studio.core.edit_resolve")
    for fx_type, ms in edit_resolve.FX_DEFAULT_MS.items():
        assert defaults[fx_type] == pytest.approx(ms / 1000), fx_type


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
    assert fx_math["zoomHeld"]["scale"] == pytest.approx(center["scale"], rel=1e-3)  # segura até o corte
    assert fx_math["zoomAfterCut"]["scale"] == 1  # o zoom solta no corte


def test_shake_decays_and_covers_the_frame(fx_math: dict) -> None:
    shakes = fx_math["shake"]
    offset = [abs(c["x"] - 0.5 * (1 - c["scale"])) for c in shakes]
    assert max(offset[:8]) > max(offset[-8:]) * 3
    assert max(offset) > 0.002
    # A intensidade do planejador (`strength`) vale; `amount` continua como nome antigo.
    assert fx_math["shakeStrong"] > fx_math["shakeWeak"] * 1.8
    assert fx_math["shakeLegacy"] == pytest.approx(fx_math["shakeStrong"])
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


def test_anchored_annotation_after_freeze_enters_on_its_trigger(fx_math: dict) -> None:
    """Freeze de 24 quadros no 30 e marca-texto no 60: aparece no quadro real 60 (o fundo está no 36)."""
    assert fx_math["anchored59"] == 0
    assert fx_math["anchored60"] == [{"type": "highlight", "f": 0, "len": 60, "last": True}]
    assert fx_math["remapped60"] == 36  # o relógio do fundo, que atrasaria a anotação


def test_shot_slots_are_contiguous(fx_math: dict) -> None:
    slots = fx_math["slots"]
    assert [(s["from"], s["to"]) for s in slots] == [(0, 10), (10, 30), (30, 120)]
    assert slots[0]["shot"] is None  # antes do 1º plano: o visual da cena
    assert slots[2]["shot"]["tag"] == "last"  # mesmo `from`: vale o último
    assert fx_math["slotsEmpty"] == []
    assert fx_math["cuts"] == [10, 30]


def test_fx_spans_with_backend_durations(fx_math: dict) -> None:
    assert fx_math["spanTextPop"] == {"from": 10, "to": 43}  # 1,1 s a 30 fps
    assert fx_math["spanTextPopZero"] == fx_math["spanTextPop"]  # sem `dur`: o padrão do tipo
    assert fx_math["spanHighlight"] == {"from": 10, "to": 60}  # corte logo depois: segura até ele
    assert fx_math["spanHighlightLong"] == {"from": 10, "to": 60}  # preso à imagem: nunca passa do corte
    assert fx_math["spanHighlightFar"] == {"from": 10, "to": 52}  # sem corte por perto: o `dur`
    assert fx_math["spanShake"] == {"from": 30, "to": 42}  # o tremor não estica até o corte
    assert fx_math["spanCounter"] == {"from": 10, "to": 55}  # o `dur` do planejador (1,5 s)
    assert fx_math["spanLate"] == {"from": 119, "to": 120}  # gatilho além da cena cai no último quadro


def test_framing_and_rect_params(fx_math: dict) -> None:
    assert fx_math["framingFull"] is None
    assert fx_math["framingBox"] == {"scale": 2, "left": 0.5, "top": 0}
    assert fx_math["rects"] == [[0.1, 0.1, 0.2, 0.2], [0.5, 0.5, 0.5, 0.5]]  # o 2º é cortado na borda
    assert fx_math["rectsNested"] == [[0.1, 0.2, 0.3, 0.4]]
    snap = fx_math["snap"]
    assert snap[0] == 0 and snap[2] == pytest.approx(1) and snap[3] == 1 and snap[1] > 0.8


def test_rect_from_tall_capture_lands_on_the_crop(fx_math: dict) -> None:
    """Captura 1:2 num vídeo 16:9: o `rect` do arquivo vira o recorte visível (faixa de 28% da altura da página)."""
    v = 0.5 / (1920 / 1080)
    (x, y, w, h), = fx_math["rectTop"]
    assert (x, w) == pytest.approx((0.1, 0.6))
    assert y == pytest.approx(0.05 / v) and h == pytest.approx(0.02 / v)
    assert fx_math["rectMidOut"] == []  # recorte no meio da página: o trecho marcado está fora da tela
    assert fx_math["rectFrame"] == [pytest.approx([0.1, 0.05, 0.6, 0.02])]  # `space: 'frame'`: como está
    assert fx_math["rectUnknown"] == [pytest.approx([0.1, 0.05, 0.6, 0.02])]  # proporção desconhecida
    assert fx_math["boxCrop"]["pos"] == pytest.approx([0.5, 0]) and fx_math["boxCrop"]["front"] is None
    assert fx_math["boxNone"] is None
    # Modo desfocado: a imagem inteira numa caixa no meio; o retângulo cai na caixa, não no contêiner todo.
    assert fx_math["rectBlur"] == [pytest.approx([(1 - v) / 2, 0, v, 1])]


def test_zoom_rect_goes_to_screen_through_crop_and_framing(fx_math: dict) -> None:
    v = 0.5 / (1920 / 1080)
    top = (1 - v) * 0.5
    plain = fx_math["camFxPlain"]
    assert plain["space"] == "frame"
    assert plain["rect"] == pytest.approx([0.1, (0.4 - top) / v, 0.8, 0.1 / v])
    # Plano com enquadramento 2x no centro: janela de 0,25 a 0,75.
    y0, y1 = (0.4 - top) / v, (0.5 - top) / v
    expected = [max(0.0, (0.1 - 0.25) * 2), max(0.0, (y0 - 0.25) * 2)]
    expected += [min(1.0, (0.9 - 0.25) * 2) - expected[0], min(1.0, (y1 - 0.25) * 2) - expected[1]]
    assert fx_math["camFxFramed"]["rect"] == pytest.approx(expected)  # cortado na borda da janela
    assert expected == pytest.approx([0, 0, 1, 0.5])
    assert fx_math["camFxSame"] is True  # sem zoom_rect: a mesma lista


def test_shot_clip_respects_seconds_and_official_cap(fx_math: dict) -> None:
    assert fx_math["secOfficial"] == 4.0  # sem `seconds`: o teto do oficial
    assert fx_math["secOfficialGiven"] == 2.5
    assert fx_math["secFree"] is None  # desconhecido: toca até o fim do plano
    hold = fx_math["timingHold"]
    assert hold["rate"] == 1 and hold["playFrames"] < 120  # congela antes dos 4 s
    assert fx_math["timingHoldNone"]["playFrames"] == 210
    cover = fx_math["timingCover"]
    assert cover["playFrames"] * cover["rate"] <= 4 * 30  # desacelera, mas não passa do trecho
    fast = fx_math["timingFast"]
    assert fast["rate"] == 1.5 and fast["playFrames"] * fast["rate"] <= 4 * 30


def test_corner_meme_stays_off_the_caption(fx_math: dict) -> None:
    assert fx_math["cornerFree"] == {"bottom": 0.06, "height": 0.42}

    def span(box: dict) -> tuple[float, float]:
        return 1 - box["bottom"] - box["height"], 1 - box["bottom"]

    for key, band in (("cornerBottomCaption", (0.78, 0.94)), ("cornerMidCaption", (0.42, 0.58))):
        top, bottom = span(fx_math[key])
        assert bottom <= band[0] + 1e-9 or top >= band[1] - 1e-9, key
        assert fx_math[key]["height"] > 0.2, key
    assert fx_math["cornerTopCaption"] == {"bottom": 0.06, "height": 0.42}  # legenda em cima: canto de sempre
    top, bottom = span(fx_math["cornerPortrait"])
    assert bottom <= 0.62 + 1e-9


def _overlap(a: list[float], b: list[float]) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return w * h if w > 1e-6 and h > 1e-6 else 0.0


def test_stamp_keeps_its_place_when_free_and_dodges_known_text(fx_math: dict) -> None:
    """QA do prj_gq4krrs11w: "CONFIRMADO" tampava "DEMO OU" no título ("SEM PC, DEMU UU BETA"). Livre, o carimbo fica
    onde sempre ficou (o quadro não muda); com texto no caminho, vai para o lugar livre mais perto, na área segura."""
    assert fx_math["stampFree"] == {"side": "right", "y": 0.3}
    safe = fx_math["stampSafe"]
    assert safe == pytest.approx(0.05)
    title = fx_math["rectTitle"]
    assert fx_math["stampTitle"]["side"] == "right"  # mesmo lado, logo acima do título
    assert all(_overlap(title, o) == 0 for o in fx_math["stampTitleObstacles"])
    assert title[1] >= safe - 1e-9 and title[1] + title[3] <= 0.2875 + 1e-9
    marked = fx_math["rectMarked"]
    assert fx_math["stampMarked"] == {"side": "left", "y": 0.3}  # troca de lado na mesma altura
    assert all(_overlap(marked, o) == 0 for o in fx_math["stampMarkedObstacles"])
    # Sem lugar livre: dentro da área segura e com menos sobreposição que o lugar de sempre.
    wall = fx_math["rectWall"]
    assert wall[1] >= safe - 1e-9 and wall[1] + wall[3] <= 1 - safe + 1e-9
    cost = lambda r: sum(_overlap(r, o) for o in fx_math["stampWallObstacles"])  # noqa: E731
    assert cost(wall) < cost(fx_math["stampWallPreferredRect"])
    clamped = fx_math["stampClamped"]
    assert clamped["side"] == "left" and clamped["y"] == pytest.approx(1 - safe - fx_math["stampGeom"]["halfH"])


def test_stamp_dodges_image_letters_but_never_trades_them_for_screen_text(fx_math: dict) -> None:
    """QA do prj_gq4krrs11w: "NÃO CONFIRMADO" sobre as letras do logo GTA VI, "BOATO" sobre o "C" de "PC". Com lugar
    livre das letras da imagem, o carimbo vai para lá; sem lugar (a página inteira é texto), ele fica sobre a imagem,
    nunca sobre a legenda."""
    assert fx_math["stampLogo"] == {"side": "left", "y": 0.3}
    assert all(_overlap(fx_math["rectLogo"], o) == 0 for o in fx_math["stampLogoSoft"])
    page = fx_math["rectPage"]
    assert _overlap(page, [0, 0.7, 1, 0.2]) == 0
    assert page[1] >= fx_math["stampSafe"] - 1e-9


def test_stamp_entry_never_overflows_the_screen(fx_math: dict) -> None:
    """QA: "CONFIRMADO" cortado na borda de baixo (1:57): ele entrava em escala 2,2 colado na área segura."""
    low, mid = fx_math["entryLow"], fx_math["entryMid"]
    geom = fx_math["stampGeom"]
    # No meio da altura, só a borda do lado limita (a caixa grande não passa da direita da tela).
    assert mid == pytest.approx((1 - geom["cx"]["right"]) / geom["halfW"])
    assert 0.8 + geom["halfW"] * mid <= 1 + 1e-9
    assert 1 <= low <= mid
    y = 1 - fx_math["stampSafe"] - geom["halfH"]
    assert y + geom["halfH"] * low <= 1 + 1e-9


def test_headline_band_leaves_the_capture_headline(fx_math: dict) -> None:
    """QA: "PRÓXIMOS GOLPES, SEGUNDO ESET" e "ALERTA ESET" sobre o H1 da captura. Livre, a faixa fica no topo; sobre a
    manchete, desce para logo abaixo dela (acima da legenda); sem lugar livre, fica no topo de sempre."""
    assert fx_math["bandFree"] == pytest.approx(0.1)
    assert fx_math["bandMoved"] == pytest.approx(0.47 + 0.035 + 0.015)
    assert fx_math["bandStuck"] == pytest.approx(0.1)


def test_text_pop_leaves_the_logo_letters(fx_math: dict) -> None:
    """QA: "JANEIRO" (texto que pula) sobre as letras do logo do GTA VI. Livre, fica na altura pedida; sobre o logo,
    vai para a altura livre mais perto, sem encostar na legenda."""
    assert fx_math["popFree"] == pytest.approx(0.42)
    half = fx_math["popPop"]["half"]
    y = fx_math["popLogo"]
    assert y - half >= 0.55 - 1e-9 and y + half <= 0.8 - 0.06 + 1e-9


def test_image_letters_follow_camera_framing_and_screen_window(fx_math: dict) -> None:
    """As caixas do backend (fração do arquivo) vão para a tela pelo mesmo caminho da imagem."""
    (static,) = fx_math["textStatic"]
    assert static == pytest.approx([0.1, 0.2, 0.6, 0.1])
    (pan,) = fx_math["textPan"]
    # O pan para baixo mexe a imagem na vertical: a caixa cobre o caminho todo (mais alta, contém a parada).
    assert pan[1] < 0.2 and pan[1] + pan[3] > 0.3 and pan[3] > 0.1
    (framed,) = fx_math["textFramed"]
    # Plano 2x na janela do canto de cima à esquerda: (0,1-0)·2 = 0,2; (0,2-0)·2 = 0,4; o resto sai do quadro.
    assert framed[0] == pytest.approx(0.2) and framed[1] == pytest.approx(0.4)
    assert framed[0] + framed[2] == pytest.approx(1.0)
    media = fx_math["screenMedia"]
    (inside,) = fx_math["textScreen"]
    x0, y0 = media["x"] / 1920, media["y"] / 1080
    assert inside[0] >= x0 - 1e-6 and inside[1] >= y0 - 1e-6
    assert inside[0] + inside[2] <= (media["x"] + media["w"]) / 1920 + 1e-6
    assert inside[2] < 0.6  # a janela é menor que a tela
    (zoomed,) = fx_math["textScreenZoom"]
    assert zoomed[2] > inside[2]  # o zoom lento e o do clique aumentam a caixa
    hit, miss = fx_math["bandOverlap"]
    assert hit == pytest.approx(0.05) and miss == 0


def test_sceneview_feeds_image_letters_to_stamp_band_and_scrim() -> None:
    """A cena leva as letras da imagem (`text_rects`) para o carimbo, a faixa do topo e a tarja; a fonte solta e o
    avatar entram como texto da tela; a legenda deixa a faixa do avatar livre."""
    src = _read(COMP / "SceneView.tsx")
    assert "const imageText = imageTextOnScreen(scene, {" in src
    assert "placeStamp(stampPreferred(scene.card?.stamp_at, stampLowY, stampSideY), geom, obstacles, imageText)" in src
    assert "placeBand(top[1] + half, top[2], half, hard, imageText, lo, hi)" in src
    assert "<Headline text={headline} style={cards} lang={lang} y={headY} />" in src
    assert "entryScale={stampScale}" in src and "alpha={scrimAlpha}" in src
    assert "reserve={captionReserve}" in src
    assert "imageText={imageText}" in src  # o texto que pula também desvia
    assert "textPopY(y, label.length" in _read(COMP / "fx" / "FxLayer.tsx")
    assert "obstacles.push([0.06, sourceY - chipHalf, chipW, chipHalf * 2]);" in src
    captions = _read(COMP / "Captions.tsx")
    assert "const side = captionSides(height > width ? SIDE : SIDE_WIDE, reserve, line.from, line.to);" in captions
    assert "export const SCRIM_ALPHA_TEXT = 0.85;" in captions
    # A moldura do tratamento "tela" é a mesma conta da que leva as letras para a tela.
    assert "screenFrame(variant, width, height)" in _read(COMP / "Treatments.tsx")
    types = _read(RD / "types.ts")
    assert types.count("text_rects?: number[][];") == 2


def test_flow_rows_wrap_like_flex(fx_math: dict) -> None:
    assert fx_math["flow"] == [{"width": 540, "start": 0}, {"width": 900, "start": 2}, {"width": 240, "start": 3}]


def test_entry_transition_is_capped(fx_math: dict) -> None:
    """Transição de entrada (desfoque, flash, fade) de no máximo 0,4 s: o estilo aceita até 2 s, que viravam quadro morto."""
    assert fx_math["entryMax"] == pytest.approx(0.4)
    assert fx_math["entry"] == [9, 12, 12, 12, 1]


def test_sceneview_places_stamp_scrim_and_transition_under_captions() -> None:
    """O carimbo solto passa pelo `placeStamp` com o texto conhecido da tela; a tarja fica sob a legenda no fundo de
    imagem em brilho cheio; a transição de entrada usa o teto e fica ABAIXO da legenda (o flash lavava a fala)."""
    src = _read(COMP / "SceneView.tsx")
    assert "const entry = entryFrames(style.transitionFrames, fps);" in src
    assert "style.transitionFrames}" not in src  # nenhuma peça usa a duração sem teto
    assert "textCardRects(kind, scene, cards, style.captions.uppercase, width, height)" in src
    assert "headlineRect(headline, cards, lang, width, height)" in src
    assert "placeStamp(stampPreferred(scene.card?.stamp_at, stampLowY, stampSideY)" in src
    assert "const scrim = captionsShown && !style.captions.background && scrimOpacity > 0 && Boolean(url || clipUrl);" in src
    # Cartão de dado que entra depois do corte: o fundo fica nítido até ele (BACKDROP_RAMP_S) e a tarja da legenda
    # acompanha, sumindo junto com o escurecimento em vez de sair de uma vez no quadro em que o cartão chega.
    assert "const scrimOpacity = news ? 0 : (DIM[kind] ?? 0) === 0 ? 1 : 1 - backdrop;" in src
    assert "opacity={scrimOpacity}" in src
    i_scrim = src.index("<CaptionScrim")
    i_stamp = src.index("<Stamp text={stamp}")
    i_trans = src.index("<TransitionIn type={scene.transition} frames={entry}")
    i_caps = src.index("<Captions lines=")
    assert i_scrim < i_stamp < i_trans < i_caps
    # O aparelho do tratamento "tela" não entra transparente (os primeiros quadros ficavam só no fundo desfocado).
    screen = _read(COMP / "Treatments.tsx")
    device = screen[screen.index("const deviceStyle"):screen.index("// Coordenadas do conteúdo")]
    assert "opacity" not in device


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
    if result.get("skipped"):
        pytest.skip(f"fx-stills: {result['skipped']}")
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
