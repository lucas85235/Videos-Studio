"""Fase 2: compositor de thumbs por camadas (Pillow), templates, biblioteca (thumbkit) e CLI `thumbkit`.
Tudo com ativos sintéticos gerados no teste (nada do canais real, nada do banco real)."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from studio.core.project.document import BgLayer, CutoutLayer, PresenterThumbLayer, TextLayer, ThumbLayers
from studio.core.styles.schema import THUMB_TEMPLATES
from studio.media import thumb_assets as A
from studio.media import thumb_check as K
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T

HAS_FFMPEG = shutil.which("ffmpeg") is not None


# --- Ativos sintéticos ---------------------------------------------------------------------------------------------
def _frame(path: Path, color: tuple[int, int, int], *, letterbox: bool = False, pattern: int = 0) -> Path:
    im = Image.new("RGB", (1600, 900), color)
    d = ImageDraw.Draw(im)
    for k in range(6):
        x = (k * 260 + pattern * 97) % 1500
        d.rectangle((x, 100 + (k * 113 + pattern * 51) % 500, x + 140 + pattern * 30, 800), fill=(
            (color[0] + 90 * (k % 3)) % 256, (color[1] + 60 * k) % 256, (color[2] + 120 * pattern) % 256))
    d.ellipse((900, 150, 1150, 450), fill=(230, 180, 150))  # "rosto"
    if letterbox:
        d.rectangle((0, 0, 1600, 110), fill=(0, 0, 0))
        d.rectangle((0, 790, 1600, 900), fill=(0, 0, 0))
    im.save(path)
    return path


def _rgba_figure(path: Path, size: tuple[int, int] = (400, 600)) -> Path:
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    w, h = size
    d.ellipse((w * 0.32, h * 0.02, w * 0.68, h * 0.20), fill=(220, 170, 140, 255))  # cabeça
    d.rectangle((w * 0.15, h * 0.22, w * 0.85, h), fill=(25, 22, 30, 255))  # camiseta preta
    im.save(path)
    return path


def _logo(path: Path) -> Path:
    im = Image.new("RGBA", (880, 680), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.polygon([(40, 40), (440, 640), (840, 40)], fill=(255, 46, 136, 255))
    im.save(path)
    return path


@pytest.fixture()
def kit(tmp_path: Path) -> tuple[A.AssetResolver, Path]:
    kit_dir = tmp_path / "kit"
    (kit_dir / "frames").mkdir(parents=True)
    (kit_dir / "logos").mkdir()
    poses = tmp_path / "poses"
    poses.mkdir()
    manifest = A.empty_manifest("chn_teste")
    frames = [("sol", (255, 120, 80), False, ["lugar", "claro"]), ("praia", (40, 200, 190), False, ["lugar", "claro"]),
              ("rua", (90, 140, 230), True, ["lugar", "claro"]), ("noite", (12, 10, 25), False, ["noite"])]
    for k, (fid, color, lb, tags) in enumerate(frames):
        _frame(kit_dir / "frames" / f"{fid}.png", color, letterbox=lb, pattern=k)
        A.upsert_item(manifest, {"id": fid, "kind": "frame", "file": f"frames/{fid}.png", "tags": tags,
                                 "editorial": True, "focus_x": 0.64, "focus_y": 0.33,
                                 "face_box": [0.56, 0.16, 0.72, 0.5], "size": [1600, 900]})
    _logo(kit_dir / "logos" / "lockup.png")
    _logo(kit_dir / "logos" / "vi.png")
    A.upsert_item(manifest, {"id": "lockup", "kind": "logo", "file": "logos/lockup.png", "tags": ["lockup"],
                             "editorial": True})
    A.upsert_item(manifest, {"id": "vi", "kind": "logo", "file": "logos/vi.png", "tags": ["vi"], "editorial": True})
    A.save_manifest(kit_dir, manifest)
    for pose in ("surpreso", "apontando", "lupa"):
        _rgba_figure(poses / f"{pose}.png")
    return A.AssetResolver(kit_dir, pose_dirs=[poses], allow_editorial=True), kit_dir


def _layers(template: str, **kw) -> ThumbLayers:
    hints = {"words": "7 DIAS" if template == "numero-lista" else "SEMANA 1", "background": "frame:sol",
             "focus_x": 0.64, "presenter": template in ("avatar-reage", "numero-lista", "lugar-mapa"),
             "pose": "surpreso", "cards": ["PRAZO", "NÃO GASTE", "EXPLORE"], "left": "frame:praia"}
    hints.update(kw)
    return T.default_layers(template, hints)


# --- Templates e camadas padrão -------------------------------------------------------------------------------------
def test_templates_cobrem_os_do_modelo():
    assert set(T.TEMPLATES) == set(THUMB_TEMPLATES)


def test_default_layers_regras():
    lay = T.default_layers("numero-lista", {"words": "7 dias", "presenter": True, "cards": ["A", "B", "C", "D"]})
    assert lay.text and lay.text.words == "7 DIAS"
    assert len(lay.cards) == 3
    # `apontando` à direita vira para apontar para o texto/cartões
    assert lay.presenter and lay.presenter.pose == "apontando" and lay.presenter.flip is True
    heroi = T.default_layers("heroi-palavra", {"words": "SEMANA", "presenter": True, "presenter_h": 1.5})
    assert heroi.presenter and heroi.presenter.h <= 0.30  # avatar pequeno no canto
    reage = T.default_layers("avatar-reage", {"words": "NÃO PROCURE"})
    assert reage.presenter and reage.presenter.crop == "busto" and reage.presenter.h >= 1.4
    assert reage.characters and reage.characters[0].asset == "logo:lockup" and reage.logo == "none"
    assert T.default_layers("lugar-mapa", {"words": "MAPA"}).big_vi is True
    assert T.default_layers("heroi-palavra", {}).background.kind == "gradient"


def test_focus_hints_zoom_pelo_rosto():
    item = {"focus_x": 0.6, "face_box": [0.5, 0.2, 0.6, 0.45], "size": [1920, 1080]}
    hints = T.focus_hints(item, "heroi-palavra")
    assert hints["focus_x"] == 0.6
    assert hints["zoom"] == pytest.approx(2.0)  # rosto de 25% → 50% da altura
    assert "zoom" not in T.focus_hints(item, "lugar-mapa")
    assert T.focus_hints(None) == {}


# --- Render ------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("template", THUMB_TEMPLATES)
def test_cada_template_renderiza(kit, template):
    resolver, _ = kit
    size = T.SHORT_CANVAS if template == "livre" else T.CANVAS
    img = C.render(_layers(template), size, resolver)
    assert img.size == size and img.mode == "RGB"
    layout = img.info["layout"]
    missing = [w for w in layout["warnings"] if "não" in w and "encontr" in w]
    assert not missing, layout["warnings"]
    assert layout["text_bbox"], "o texto tem de ser desenhado"
    if template in ("avatar-reage", "numero-lista"):
        assert any(f["kind"] == "presenter" for f in layout["figures"])


def test_render_deterministico(kit):
    resolver, _ = kit
    lay = _layers("avatar-reage", pill="SEM SPOILER", strike=True)
    a = C.render(lay, T.CANVAS, resolver)
    b = C.render(ThumbLayers.model_validate(json.loads(lay.model_dump_json())), T.CANVAS, resolver)
    assert hashlib.sha256(a.tobytes()).hexdigest() == hashlib.sha256(b.tobytes()).hexdigest()


def test_exporta_4k_ate_2mb(kit, tmp_path):
    resolver, _ = kit
    out = tmp_path / "thumb.jpg"
    info = C.export(_layers("numero-lista"), out, is_long=True, assets=resolver)
    with Image.open(out) as im:
        assert im.size == (3840, 2160)
    assert out.stat().st_size <= 2_000_000
    assert info["preview"].is_file()
    with Image.open(info["preview"]) as pv:
        assert pv.size == (1280, 720)


@pytest.mark.parametrize("template", ["heroi-palavra", "avatar-reage", "lugar-mapa", "numero-lista", "versus"])
def test_zona_proibida_respeitada(kit, template):
    resolver, _ = kit
    img = C.render(_layers(template, pill="ANTES DE JOGAR" if template != "numero-lista" else None), T.CANVAS,
                   resolver)
    W, H = img.size
    layout = img.info["layout"]
    boxes = [layout["text_bbox"], layout.get("pill_bbox"), *layout["cards_bbox"]]
    for box in [b for b in boxes if b]:
        for name, z in T.FORBIDDEN_LONG.items():
            zb = (z[0] * W, z[1] * H, z[2] * W, z[3] * H)
            inter = box[0] < zb[2] and box[2] > zb[0] and box[1] < zb[3] and box[3] > zb[1]
            assert not inter, f"{template}: {box} invade {name}"
    assert K.check(img, _layers(template), True)["forbidden"] == 0


def test_short_texto_no_centro_seguro(kit):
    resolver, _ = kit
    img = C.render(_layers("livre", words="NÃO PROCURE ISSO"), T.SHORT_CANVAS, resolver)
    box = img.info["layout"]["text_bbox"]
    H = img.height
    assert box[1] >= 0.15 * H and box[3] <= 0.70 * H


def test_tarja_preta_removida(kit):
    resolver, _ = kit
    lay = T.default_layers("lugar-mapa", {"background": "frame:rua", "zoom": 1.0, "big_vi": False, "logo": "none"})
    img = C.render(lay, T.CANVAS, resolver)
    top = img.crop((0, 0, img.width, 6)).convert("L")
    assert top.getextrema()[1] > 40, "a tarja preta de cima não pode aparecer"


def test_avatar_contorno_branco(kit):
    resolver, _ = kit
    lay = ThumbLayers(template="avatar-reage", background=BgLayer(kind="gradient", ref="#202020,#202020"),
                      presenter=PresenterThumbLayer(pose="surpreso", h=1.5, outline_px=10), logo="none")
    img = C.render(lay, T.CANVAS, resolver)
    fig = next(f for f in img.info["layout"]["figures"] if f["kind"] == "presenter")
    x0 = fig["bbox"][0]
    y = fig["bbox"][1] + (fig["bbox"][3] - fig["bbox"][1]) // 2
    # logo à esquerda do corpo: anel branco do adesivo sobre o fundo escuro
    ring = [img.getpixel((x, y)) for x in range(x0 - 9, x0 + 1)]
    assert any(min(p) > 230 for p in ring)
    assert fig["face_h"] >= 0.25


def test_editorial_bloqueado_cai_no_fallback(kit):
    _, kit_dir = kit
    blocked = A.AssetResolver(kit_dir, pose_dirs=kit[0].pose_dirs, allow_editorial=False)
    img = C.render(_layers("avatar-reage"), T.CANVAS, blocked)
    layout = img.info["layout"]
    assert layout["editorial"] is False
    assert any("allow_editorial" in w for w in layout["warnings"])
    ok = C.render(_layers("avatar-reage"), T.CANVAS, kit[0]).info["layout"]
    assert ok["editorial"] is True


def test_fundo_ai_e_ref_inexistente_viram_degrade(kit):
    resolver, _ = kit
    ai = ThumbLayers(template="heroi-palavra", background=BgLayer(kind="ai", ref="sunset"),
                     text=TextLayer(words="TUDO"))
    img = C.render(ai, T.CANVAS, resolver)
    assert any("IA" in w for w in img.info["layout"]["warnings"])
    gone = ThumbLayers(template="heroi-palavra", background=BgLayer(kind="frame", ref="frame:nao-existe"),
                       text=TextLayer(words="TUDO"))
    assert any("não está" in w for w in C.render(gone, T.CANVAS, resolver).info["layout"]["warnings"])


def test_recorte_sem_alfa_pede_recorte(kit, tmp_path):
    resolver, _ = kit
    jpg = tmp_path / "casal.jpg"
    Image.new("RGB", (400, 600), (200, 100, 50)).save(jpg)
    lay = ThumbLayers(template="heroi-palavra", characters=[CutoutLayer(asset=f"upload:{jpg}")])
    warnings = C.render(lay, T.CANVAS, resolver).info["layout"]["warnings"]
    assert any("precisa de recorte" in w for w in warnings)


def test_render_set_troca_fundo_de_variante_igual(kit):
    resolver, _ = kit
    a = _layers("heroi-palavra", background="frame:sol")
    b = _layers("heroi-palavra", background="frame:sol")
    out = C.render_set([a, b], T.CANVAS, resolver)
    assert out[1][0].background.ref != "frame:sol"
    assert K.hamming(K.dhash(out[0][1]), K.dhash(out[1][1])) >= 10


# --- Biblioteca -----------------------------------------------------------------------------------------------------
def test_resolver_refs(kit, tmp_path):
    resolver, kit_dir = kit
    assert resolver.resolve("frame:sol").path == kit_dir / "frames" / "sol.png"
    assert resolver.resolve("sol").kind == "frame"
    assert resolver.resolve("pose:lupa").kind == "pose"
    assert resolver.logo_id() == "lockup" and resolver.big_vi_id() == "vi"
    with pytest.raises(A.AssetError):
        resolver.resolve("logo:sol")  # tipo errado
    with pytest.raises(A.AssetError):
        resolver.resolve("pose:nao-tem")
    with pytest.raises(A.AssetError):
        resolver.resolve("frame:Trailer@3")  # vídeo não registrado
    up = tmp_path / "u.png"
    Image.new("RGB", (10, 10)).save(up)
    assert resolver.resolve(f"upload:{up}").path == up
    assert resolver.next_frame("frame:sol") == "frame:praia"


def _video(path: Path) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "testsrc=size=320x180:rate=10:duration=3", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg ausente")
def test_quadro_do_video_extraido_e_cacheado(tmp_path):
    video = _video(tmp_path / "trailer.mp4")
    kit_dir = tmp_path / "kit"
    manifest = A.empty_manifest("chn_x")
    manifest["videos"]["Trailer"] = str(video)
    A.save_manifest(kit_dir, manifest)
    r = A.AssetResolver(kit_dir, allow_editorial=True)
    asset = r.resolve("frame:Trailer@1.5")
    assert asset.path.is_file() and asset.path.name == "Trailer@1.50.jpg"
    mtime = asset.path.stat().st_mtime
    assert A.AssetResolver(kit_dir, allow_editorial=True).resolve("frame:Trailer@1.5").path.stat().st_mtime == mtime
    assert A.AssetResolver(kit_dir, extract=False, allow_editorial=True).resolve("frame:Trailer@1.5").path == asset.path


@pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg ausente")
def test_import_kit(tmp_path):
    canais = tmp_path / "canais"
    (canais / "v").mkdir(parents=True)
    _video(canais / "v" / "T1.mp4")
    _logo(canais / "v" / "lockup.png")
    Image.new("RGB", (300, 200), (10, 10, 10)).save(canais / "v" / "sem-alfa.png")
    _frame(canais / "v" / "capa.png", (200, 100, 200))
    seed = {
        "videos": {"T1": "v/T1.mp4", "Falta": "v/nao.mp4"},
        "files": [
            {"id": "lockup", "kind": "logo", "src": "v/lockup.png", "tags": ["lockup"], "editorial": True},
            {"id": "logo-ruim", "kind": "logo", "src": "v/sem-alfa.png"},
            {"id": "capa", "kind": "frame", "src": "v/capa.png", "crop": [0, 0, 0.5, 1], "tags": ["keyart"]},
        ],
        "frames": [{"id": "t1-a", "video": "T1", "t": 1, "tags": ["lugar"]},
                   {"id": "t1-b", "video": "T1", "t": 2, "crop": [0.25, 0, 0.75, 1]}],
    }
    s = A.import_kit("chn_teste", canais, seed=seed)
    assert s["frames"] == 2 and s["total"] == 4
    assert any("sem alfa" in w for w in s["warnings"]) and any("nao.mp4" in k for k in s["skipped"])
    kit_dir = A.thumbkit_dir("chn_teste")
    manifest = A.load_manifest(kit_dir)
    capa = next(i for i in manifest["items"] if i["id"] == "capa")
    assert capa["size"] == [800, 900] and "crop" not in capa
    t1b = next(i for i in manifest["items"] if i["id"] == "t1-b")
    assert t1b["size"] == [160, 180] and t1b["editorial"] is True and t1b["source"] == {"video": "T1", "t": 2.0}
    again = A.import_kit("chn_teste", canais, seed=seed)  # idempotente
    assert again["total"] == 4
    r = A.AssetResolver.for_channel("chn_teste", allow_editorial=True)
    assert r.resolve("frame:t1-a").path.is_file()
    assert r.resolve("frame:T1@1").path.is_file()  # o mesmo quadro por tempo, já no cache


def test_seed_real_valida():
    seed = A.load_seed()
    ids = [e["id"] for e in [*seed["files"], *seed["frames"]]]
    assert len(ids) == len(set(ids))
    for e in [*seed["files"], *seed["frames"]]:
        assert A.ID_RE.match(e["id"])
        assert e.get("editorial", True) is True  # tudo do seed é material da Rockstar
        if e.get("face_box"):
            x0, y0, x1, y1 = e["face_box"]
            assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert {f["video"] for f in seed["frames"]} <= set(seed["videos"])
    assert "GTAVI_Official_Cover_Art_Landscape" in seed["videos"]


def test_cli_thumbkit(kit, tmp_path, capsys):
    from studio.cli import main

    resolver, kit_dir = kit
    dest = A.thumbkit_dir("chn_cli")
    shutil.copytree(kit_dir, dest)
    main(["thumbkit", "list", "chn_cli"])
    assert "lockup" in capsys.readouterr().out
    layers = tmp_path / "l.json"
    layers.write_text(_layers("avatar-reage").model_dump_json(), encoding="utf-8")
    out = tmp_path / "t.png"
    main(["thumbkit", "render", str(layers), str(out), "--canal", "chn_cli", "--poses", str(resolver.pose_dirs[0]),
          "--titulo", "GTA 6: semana 1"])
    assert out.is_file()
    score = json.loads(capsys.readouterr().out)
    assert "legib_168" in score and "dhash" in score


def test_pilula_nunca_cobre_o_texto_alto(kit):
    """Número gigante (numero-lista) ocupa a coluna inteira: a pílula sobe acima do texto ou é omitida com aviso."""
    resolver, _ = kit
    img = C.render(_layers("numero-lista", pill="SEM SPOILER"), T.CANVAS, resolver)
    layout = img.info["layout"]
    tb, pb = layout["text_bbox"], layout.get("pill_bbox")
    if pb:
        assert not (pb[0] < tb[2] and pb[2] > tb[0] and pb[1] < tb[3] and pb[3] > tb[1])
    else:
        assert "pílula omitida: cobriria o texto" in layout["warnings"]
