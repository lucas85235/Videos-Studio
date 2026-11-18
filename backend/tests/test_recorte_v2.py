"""Recorte v2: lados cortados (`cut_edges`), ancoragem da figura na borda da thumb, contorno que não desenha no lado
cortado e recusa do recorte que não encaixa. Só ativos sintéticos e rembg falso: nada de modelo, canais ou banco."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from studio.core.project.document import BgLayer, CutoutLayer, PresenterThumbLayer, ThumbLayers
from studio.media import thumb_assets as A
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T

W, H = T.CANVAS


# --- Ativos sintéticos ---------------------------------------------------------------------------------------------------
def _figure(size: tuple[int, int], box: tuple[float, float, float, float], *, color=(40, 90, 200)) -> Image.Image:
    """PNG RGBA com uma elipse opaca em `box` (fração; passar de 0..1 corta a figura na borda da imagem)."""
    w, h = size
    im = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((box[0] * w, box[1] * h, box[2] * w, box[3] * h), fill=(*color, 255))
    return im


def _kit(tmp_path: Path, cutouts: dict[str, tuple[Image.Image, list[str] | None]]) -> A.AssetResolver:
    kit = tmp_path / "kit"
    (kit / "cutouts").mkdir(parents=True)
    m = A.empty_manifest("chn_teste")
    for cid, (im, edges) in cutouts.items():
        im.save(kit / "cutouts" / f"{cid}.png")
        item = {"id": cid, "kind": "cutout", "file": f"cutouts/{cid}.png"}
        if edges is not None:
            item["cut_edges"] = edges
        A.upsert_item(m, item)
    A.save_manifest(kit, m)
    return A.AssetResolver(kit, allow_editorial=True, auto_cutout=False)


def _hero(asset: str, x: float = 0.72, y: float = 0.85, h: float = 0.6) -> ThumbLayers:
    return ThumbLayers(template="heroi-palavra", logo="none", characters=[CutoutLayer(asset=asset, x=x, y=y, h=h)])


# --- Detecção ------------------------------------------------------------------------------------------------------------
def test_detecta_lados_cortados_com_margem():
    # figura com margem transparente, encostando à direita e embaixo (o cotovelo da pose, o corpo cortado)
    alpha = _figure((400, 600), (0.3, 0.2, 1.2, 1.3)).getchannel("A")
    assert A.detect_cut_edges(alpha) == ["bottom", "right"]
    # contorno natural com margem: nada cortado
    assert A.detect_cut_edges(_figure((400, 600), (0.1, 0.1, 0.9, 0.9)).getchannel("A")) == []


def test_imagem_aparada_so_conta_corte_longo():
    # aparada (o alfa toca os 4 lados): o contorno natural encosta em todos; só a base cortada é longa
    im = _figure((400, 600), (0.0, 0.0, 0.9975, 1.1))  # 0,9975: o PIL pinta 1 px além
    assert A.detect_cut_edges(im.getchannel("A")) == ["bottom"]
    assert A.detect_cut_edges(_figure((400, 400), (0, 0, 0.9975, 0.9975)).getchannel("A")) == []


def test_cut_edges_prefere_manifesto_e_png(tmp_path):
    p = tmp_path / "a.png"
    _figure((300, 300), (0.2, 0.2, 0.8, 1.3)).save(p)
    assert A.cut_edges_of(p) == ["bottom"]
    assert A.cut_edges_of(p, {"cut_edges": ["left"]}) == ["left"]  # o manifesto manda
    assert A.cut_edges_of(p, {"cut_edges": []}) == []
    from PIL.PngImagePlugin import PngInfo

    info = PngInfo()
    info.add_text(A.CUT_TEXT_KEY, "top,bottom")
    q = tmp_path / "b.png"
    _figure((300, 300), (0.2, 0.2, 0.8, 0.8)).save(q, pnginfo=info)
    assert A.cut_edges_of(q) == ["top", "bottom"]


def test_anchorable_e_qualidade():
    assert A.anchorable(["bottom", "right"]) and A.anchorable(["top", "bottom", "left"]) and A.anchorable([])
    assert not A.anchorable(["left", "right"]) and not A.anchorable(["top"])
    assert A.cutout_quality({"cut_edges": ["bottom"]}) == "ok"
    assert A.cutout_quality({"cut_edges": ["top"]}) == A.QUALITY_LOW
    assert A.cutout_quality({"cut_edges": ["bottom"], "quality": "baixa"}) == A.QUALITY_LOW


def test_resolver_esconde_recorte_de_qualidade_baixa(tmp_path):
    r = _kit(tmp_path, {"bom": (_figure((200, 300), (0.1, 0.1, 0.9, 1.3)), ["bottom"]),
                        "ruim": (_figure((200, 300), (-0.2, 0.1, 1.2, 0.9)), ["left", "right"])})
    assert [i["id"] for i in r.items("cutout")] == ["bom"]
    assert {i["id"] for i in r.items("cutout", include_low=True)} == {"bom", "ruim"}


# --- Recorte com rembg falso -----------------------------------------------------------------------------------------------
class _Fake:
    def __init__(self, box: tuple[float, float, float, float]) -> None:
        self.box = box

    def __call__(self, im: Image.Image, **kw) -> Image.Image:
        w, h = im.size
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).ellipse((self.box[0] * w, self.box[1] * h, self.box[2] * w, self.box[3] * h), fill=255)
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out.paste(im.convert("RGBA"), (0, 0), mask)
        return out


def _fake(monkeypatch, box) -> None:
    monkeypatch.setattr(A, "HAS_REMBG", True)
    monkeypatch.setattr(A, "_rembg_remove", _Fake(box))
    monkeypatch.setattr(A, "_rembg_session", lambda model: f"sessao:{model}")


def _src_kit(tmp_path: Path) -> tuple[Path, dict]:
    kit = tmp_path / "kit"
    (kit / "frames").mkdir(parents=True)
    Image.new("RGB", (600, 400), (230, 200, 60)).save(kit / "frames" / "q.png")
    m = A.empty_manifest("chn_teste")
    A.upsert_item(m, {"id": "q", "kind": "frame", "file": "frames/q.png", "editorial": True})
    return kit, m


def test_make_cutouts_grava_cut_edges_qualidade_e_rosto(tmp_path, monkeypatch):
    _fake(monkeypatch, (0.3, 0.2, 0.8, 1.4))  # cortada embaixo
    kit, m = _src_kit(tmp_path)
    s = A.make_cutouts(kit, m, [{"id": "fig", "from": "q", "face_src": [0.3, 0.2, 0.8, 0.5]},
                                {"id": "carro", "from": "q", "occluded": ["left"]}])
    assert s["cutouts"] == 2
    fig = next(i for i in m["items"] if i["id"] == "fig")
    assert fig["cut_edges"] == ["bottom"] and fig["quality"] == "ok"
    assert fig["source"]["v"] == A.CUTOUT_VERSION and "face_src" not in fig
    # face_src (fração do original) virou face_box no recorte aparado: a figura começa em x=0,3 e y=0,2
    assert fig["face_box"][0] == pytest.approx(0.0, abs=0.02) and fig["face_box"][1] == pytest.approx(0.0, abs=0.02)
    assert Image.open(kit / fig["file"]).info.get(A.CUT_TEXT_KEY) == "bottom"
    carro = next(i for i in m["items"] if i["id"] == "carro")
    assert carro["cut_edges"] == ["bottom", "left"] and "occluded" not in carro


def test_make_cutouts_recorte_que_nao_encaixa_vira_qualidade_baixa(tmp_path, monkeypatch):
    _fake(monkeypatch, (-0.2, 0.2, 1.2, 0.8))  # cortada nas duas laterais
    kit, m = _src_kit(tmp_path)
    s = A.make_cutouts(kit, m, [{"id": "faixa", "from": "q"}])
    it = next(i for i in m["items"] if i["id"] == "faixa")
    assert it["cut_edges"] == ["left", "right"] and it["quality"] == A.QUALITY_LOW and it["quality_note"]
    assert any("qualidade baixa" in w for w in s["warnings"])


def test_versao_antiga_refaz_o_recorte(tmp_path, monkeypatch):
    _fake(monkeypatch, (0.3, 0.2, 0.8, 1.4))
    kit, m = _src_kit(tmp_path)
    calls = []
    real = A.cutout
    monkeypatch.setattr(A, "cutout", lambda *a, **kw: calls.append(kw["force"]) or real(*a, **kw))
    A.make_cutouts(kit, m, [{"id": "fig", "from": "q"}])
    A.make_cutouts(kit, m, [{"id": "fig", "from": "q"}])
    assert calls == [True, False]  # mesmo algoritmo e parâmetros: cache
    next(i for i in m["items"] if i["id"] == "fig")["source"].pop("v")  # item gravado pela v1
    A.make_cutouts(kit, m, [{"id": "fig", "from": "q"}])
    assert calls[-1] is True
    key = A.cutout_cache_key(kit / "frames" / "q.png")
    monkeypatch.setattr(A, "CUTOUT_VERSION", A.CUTOUT_VERSION + 1)
    assert A.cutout_cache_key(kit / "frames" / "q.png") != key


# --- Ancoragem -------------------------------------------------------------------------------------------------------------
def test_figura_cortada_embaixo_termina_rente_a_base(tmp_path):
    r = _kit(tmp_path, {"fig": (_figure((300, 500), (0.0, 0.0, 1.0, 1.5)), ["bottom"])})
    lay = C.render(_hero("fig", y=0.8, h=0.5), T.CANVAS, r).info["layout"]
    f = lay["figures"][0]
    assert f["bbox"][3] == H and f["cut_edges"] == ["bottom"]
    # o topo fica onde o candidato pôs (cresce para baixo em vez de descer o rosto)
    assert f["bbox"][1] == round(0.8 * H - 0.5 * H)


def test_corte_lateral_encosta_na_lateral_e_espelha_se_preciso(tmp_path):
    im = _figure((300, 500), (0.0, 0.0, 1.5, 1.5))
    r = _kit(tmp_path, {"fig": (im, ["bottom", "right"])})
    f = C.render(_hero("fig", x=0.75), T.CANVAS, r).info["layout"]["figures"][0]
    assert f["bbox"][2] == W and f["bbox"][3] == H and f["flip"] is False
    # do lado esquerdo da thumb: espelha e encosta na esquerda
    lay = C.render(_hero("fig", x=0.25), T.CANVAS, r).info["layout"]
    f = lay["figures"][0]
    assert f["bbox"][0] == 0 and f["flip"] is True and f["cut_edges"] == ["bottom", "left"]
    assert any("espelhada" in w for w in lay["warnings"])


def test_cortada_em_cima_e_embaixo_ocupa_a_altura_toda(tmp_path):
    r = _kit(tmp_path, {"fig": (_figure((300, 500), (0.0, -0.5, 1.5, 1.5)), ["top", "bottom", "right"])})
    f = C.render(_hero("fig", h=0.7), T.CANVAS, r).info["layout"]["figures"][0]
    assert f["bbox"][1] == 0 and f["bbox"][3] == H and f["bbox"][2] == W


@pytest.mark.parametrize(("edges", "anchored", "faded"), [
    (["top"], [], ["top"]),
    (["left", "right"], ["right"], ["left"]),
    (["bottom", "left", "right"], ["bottom", "right"], ["left"]),
])
def test_recorte_que_nao_encaixa_sai_com_degrade(tmp_path, edges, anchored, faded):
    # figura retangular azul: o lado cortado que não dá para encostar na borda some num degradê (nunca uma reta)
    im = Image.new("RGBA", (300, 500), (40, 90, 200, 255))
    ImageDraw.Draw(im).rectangle((290, 0, 299, 9), fill=(0, 0, 0, 0))  # um canto vazio: é recorte, não foto
    r = _kit(tmp_path, {"fig": (im, edges)})
    layers = _hero("fig").model_copy(update={"background": BgLayer(kind="gradient", ref="#808080,#808080")})
    img = C.render(layers, T.CANVAS, r)
    lay = img.info["layout"]
    f = lay["figures"][0]
    assert f["cut_edges"] == anchored and f["faded"] == faded
    assert any("saiu com degradê" in w for w in lay["warnings"])
    x0, y0, x1, y1 = f["bbox"]
    mid = ((x0 + x1) // 2, (y0 + y1) // 2)
    edge = {"left": (x0 + 1, mid[1]), "top": (mid[0], y0 + 1)}[faded[0]]
    inside = {"left": (x0 + 60, mid[1]), "top": (mid[0], y0 + 60)}[faded[0]]
    px, core = img.getpixel(edge), img.getpixel(inside)
    assert core[2] > 150 and core[0] < 90  # o miolo segue azul
    assert px[2] < 175 and abs(px[0] - 128) < 50  # na borda reta, quase só o fundo (cinza): sem corte nem contorno


def test_contorno_nao_desenha_no_lado_cortado():
    fig = _figure((200, 300), (0.0, 0.0, 1.0, 1.5))
    for cut, expect_white in (((), True), (("bottom",), False)):
        st, pad = C._sticker(fig, H, outline=8, rim=None, ink=(0, 0, 0), shadow=False, cut=cut)
        # faixa logo abaixo da figura, no meio: contorno branco só quando o lado não é cortado
        strip = st.crop((pad + 60, pad + 300 + 2, pad + 140, pad + 300 + 8)).convert("RGBA")
        px = list(strip.getdata())
        white = sum(1 for p in px if p[3] > 200 and min(p[:3]) > 230)
        assert (white > len(px) * 0.5) is expect_white
        if cut:  # a figura continua pela margem (azul), sem branco
            assert all(p[2] > 150 and p[0] < 100 for p in px)


def test_contorno_antisserrilhado():
    fig = _figure((200, 300), (0.1, 0.1, 0.9, 0.9))
    st, _ = C._sticker(fig, H, outline=8, rim=None, ink=(0, 0, 0), shadow=False)
    alphas = list(st.getchannel("A").getdata())
    partial = sum(1 for a in alphas if 0 < a < 255)
    # a borda do contorno tem rampa (não só 0/255) em boa parte do perímetro (~800 px)
    assert partial > 400


def test_pose_com_cotovelo_na_lateral_encosta_na_borda(tmp_path):
    poses = tmp_path / "poses"
    poses.mkdir()
    # pose com margem, cabeça no meio e o braço encostando na lateral direita do PNG
    im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((150, 20, 250, 120), fill=(200, 150, 120, 255))
    d.rectangle((100, 120, 300, 600), fill=(30, 30, 30, 255))
    d.rectangle((300, 300, 400, 340), fill=(200, 150, 120, 255))  # braço cortado pela lateral
    im.save(poses / "braco.png")
    assert A.cut_edges_of(poses / "braco.png") == ["bottom", "right"]
    r = A.AssetResolver(None, pose_dirs=[poses])
    layers = ThumbLayers(template="avatar-reage", logo="none",
                         presenter=PresenterThumbLayer(pose="braco", side="right", crop="busto", h=1.0))
    f = C.render(layers, T.CANVAS, r).info["layout"]["figures"][0]
    assert f["bbox"][2] == W and "right" in f["cut_edges"]
    # do lado esquerdo: o corte ficaria para dentro; espelha e encosta na esquerda
    left = layers.model_copy(update={"presenter": layers.presenter.model_copy(update={"side": "left"})})
    f = C.render(left, T.CANVAS, r).info["layout"]["figures"][0]
    assert f["bbox"][0] == 0 and f["flip"] is True


# --- Correções da revisão ------------------------------------------------------------------------------------------------
def test_borda_cortada_e_suavizada_ainda_e_detectada():
    # a máscara suave chega na borda do quadro com rampa de alfa nos últimos 3 px (220, 140, 60): na última coluna
    # nada passa de 128, mas a figura foi cortada ali
    a = Image.new("L", (300, 400), 0)
    ImageDraw.Draw(a).rectangle((60, 40, 296, 400), fill=255)
    for k, v in enumerate((220, 140, 60)):
        ImageDraw.Draw(a).rectangle((297 + k, 40, 297 + k, 399), fill=v)
    assert "right" not in A.detect_cut_edges(a, trimmed=False)
    assert A.detect_cut_edges(a, trimmed=False, band=A._cut_band(a.size)) == ["bottom", "right"]
    sq = A._square_cut(a, ["bottom", "right"], A._cut_band(a.size))
    assert sq.getpixel((299, 200)) == 255 and sq.getpixel((299, 20)) == 0  # chega opaca na borda, só onde há figura


def test_recorte_com_rampa_na_borda_grava_o_lado(tmp_path, monkeypatch):
    class _Ramp(_Fake):
        def __call__(self, im, **kw):
            out = super().__call__(im, **kw)
            a = out.getchannel("A")
            w, h = a.size
            for k, v in enumerate((200, 110, 40)):  # rampa nas 3 últimas colunas (à direita)
                col = a.crop((w - 4, 0, w - 3, h)).point(lambda p, v=v: min(p, v))
                a.paste(col, (w - 3 + k, 0))
            out.putalpha(a)
            return out

    monkeypatch.setattr(A, "HAS_REMBG", True)
    monkeypatch.setattr(A, "_rembg_remove", _Ramp((0.3, 0.2, 1.4, 1.4)))
    monkeypatch.setattr(A, "_rembg_session", lambda model: model)
    kit, m = _src_kit(tmp_path)
    A.make_cutouts(kit, m, [{"id": "fig", "from": "q"}])
    fig = next(i for i in m["items"] if i["id"] == "fig")
    assert fig["cut_edges"] == ["bottom", "right"]
    png = Image.open(kit / fig["file"]).getchannel("A")
    assert png.getpixel((png.width - 1, png.height // 2)) >= 200


def test_origem_mais_nova_refaz_o_recorte_com_minus(tmp_path, monkeypatch):
    _fake(monkeypatch, (0.1, 0.1, 0.9, 1.4))
    kit, m = _src_kit(tmp_path)
    seen: list[tuple[bool, int]] = []
    real = A.cutout

    def spy(*a, **kw):
        seen.append((kw["force"], len(kw.get("minus") or [])))
        return real(*a, **kw)

    monkeypatch.setattr(A, "cutout", spy)
    entries = [{"id": "jason", "from": "q", "crop": [0.4, 0, 1, 1], "minus": ["lucia"]},
               {"id": "lucia", "from": "q", "crop": [0, 0.1, 0.6, 1]}]
    A.make_cutouts(kit, m, entries)
    assert seen[0] == (True, 1)
    A.make_cutouts(kit, m, entries)
    assert seen[2] == (False, 0)  # nada mudou: cache, sem calcular a máscara
    # a capa foi reimportada (quadro de origem mais novo que o recorte): refaz, e com o minus
    import os
    import time

    src = kit / "frames" / "q.png"
    later = time.time() + 60
    os.utime(src, (later, later))
    A.make_cutouts(kit, m, entries)
    assert seen[4] == (True, 1)


def test_qualidade_mede_o_png_quando_o_item_nao_tem_cut_edges(tmp_path):
    r = _kit(tmp_path, {"bom": (_figure((200, 300), (0.1, 0.1, 0.9, 1.3)), None),
                        "faixa": (_figure((300, 200), (-0.3, 0.1, 1.3, 0.9)), None)})
    assert A.cutout_quality({"file": "cutouts/faixa.png"}) == "ok"  # sem kit: não dá para medir
    assert A.cutout_quality({"file": "cutouts/faixa.png"}, r.kit_dir) == A.QUALITY_LOW
    assert [i["id"] for i in r.items("cutout")] == ["bom"]


def test_figura_escondida_atras_do_avatar_sai(tmp_path):
    r = _kit(tmp_path, {"faixa": (_figure((300, 500), (0.1, 0.1, 0.9, 0.9)), [])})
    poses = tmp_path / "poses"
    poses.mkdir()
    pose = Image.new("RGBA", (600, 900), (0, 0, 0, 0))
    ImageDraw.Draw(pose).rectangle((0, 0, 600, 900), fill=(30, 30, 30, 255))
    pose.save(poses / "parede.png")
    r.pose_dirs.append(poses)
    # personagem quase todo atrás do busto do avatar: sai (sobrava um pedaço de camiseta saindo do lado)
    layers = ThumbLayers(template="numero-lista", logo="none",
                         characters=[CutoutLayer(asset="faixa", x=0.85, y=1.0, h=0.6)],
                         presenter=PresenterThumbLayer(pose="parede", side="right", crop="busto", h=1.5))
    lay = C.render(layers, T.CANVAS, r).info["layout"]
    assert not [f for f in lay["figures"] if f["kind"] == "cutout"]
    assert any("escondida atrás do avatar" in w for w in lay["warnings"])
    # do outro lado (à mostra), fica
    side = layers.model_copy(update={"characters": [CutoutLayer(asset="faixa", x=0.2, y=1.0, h=0.6)]})
    assert [f for f in C.render(side, T.CANVAS, r).info["layout"]["figures"] if f["kind"] == "cutout"]


def test_vi_gigante_segue_a_figura_desenhada(tmp_path, monkeypatch):
    r = _kit(tmp_path, {"fig": (_figure((300, 500), (0.0, 0.0, 1.5, 1.5)), ["bottom", "right"])})
    seen: list = []
    real = C._big_vi

    def spy(canvas, layers, resolver, pal, layout, tpl, text_box):
        seen.append(C._vi_figure(layers, layout))  # o VI decide a posição depois das figuras desenhadas
        return real(canvas, layers, resolver, pal, layout, tpl, text_box)

    monkeypatch.setattr(C, "_big_vi", spy)
    lay = C.render(_hero("fig", x=0.72).model_copy(update={"big_vi": True}), T.CANVAS, r).info["layout"]
    assert seen[-1] is not None and seen[-1]["bbox"] == lay["figures"][0]["bbox"]


def test_rosto_sai_da_zona_do_selo(tmp_path):
    # figura cortada embaixo e à direita, rosto no canto de baixo à direita do recorte (a Lucia da capa)
    im = _figure((300, 300), (0.0, 0.0, 1.5, 1.5))
    kit_r = _kit(tmp_path, {"fig": (im, ["bottom", "right"])})
    item = kit_r.item("fig")
    item["face_box"] = [0.4, 0.35, 0.98, 0.8]
    lay = C.render(_hero("fig", x=0.8, y=1.0, h=0.7), T.CANVAS, kit_r).info["layout"]
    f = lay["figures"][0]
    zone = T.forbidden_zones((W, H))["selo"]
    fb = f["face_box"]
    assert not (fb[2] > zone[0] * W and fb[3] > zone[1] * H)
    assert f["bbox"][2] == W and f["bbox"][3] == H  # continua ancorada
    assert any("selo" in w for w in lay["warnings"])


def test_logo_sai_com_o_vi_gigante_ou_espremido(tmp_path):
    kit = tmp_path / "kit"
    (kit / "logos").mkdir(parents=True)
    (kit / "cutouts").mkdir(parents=True)
    Image.new("RGBA", (300, 120), (255, 255, 255, 255)).save(kit / "logos" / "lockup.png")
    _figure((300, 500), (0.0, 0.0, 1.5, 1.5)).save(kit / "cutouts" / "fig.png")
    m = A.empty_manifest("chn_teste")
    A.upsert_item(m, {"id": "lockup", "kind": "logo", "file": "logos/lockup.png", "tags": ["lockup"]})
    A.upsert_item(m, {"id": "fig", "kind": "cutout", "file": "cutouts/fig.png", "cut_edges": ["bottom", "right"]})
    A.save_manifest(kit, m)
    r = A.AssetResolver(kit, allow_editorial=True, auto_cutout=False)
    base = ThumbLayers(template="heroi-palavra", logo="corner",
                       characters=[CutoutLayer(asset="cutout:fig", x=0.72, y=1.0, h=0.9)])
    no_vi = C.render(base, T.CANVAS, r).info["layout"]
    assert any(f["kind"] == "logo" for f in no_vi["figures"])
    with_vi = C.render(base.model_copy(update={"big_vi": True}), T.CANVAS, r).info["layout"]
    assert not any(f["kind"] == "logo" for f in with_vi["figures"])
    assert any("marca já aparece grande" in w for w in with_vi["warnings"])
    layout = C.Layout((W, H), "numero-lista")
    layout.text_bbox = (40, 60, 500, 600)
    layout.figures.append({"kind": "cutout", "bbox": [760, 0, W, H]})
    assert C._logo_gap(layout, (W, H), 520, 30, 150, 60) == 260  # 2×150 + 2×32 não cabe: sai


# --- Trechos retos, acabamento e manifesto -------------------------------------------------------------------------------
def _straight_figure() -> Image.Image:
    """Elipse com uma fatia tirada à esquerda no meio (linha reta vertical que não encosta na borda da imagem): o que
    sobra quando uma figura da frente foi tirada com `minus` ou o quadro já vinha cortado."""
    im = _figure((400, 600), (0.02, 0.0, 0.98, 1.0))
    ImageDraw.Draw(im).rectangle((0, 240, 159, 420), fill=(0, 0, 0, 0))
    return im


def test_detecta_trecho_reto_de_dentro_e_ignora_contorno_natural():
    segs = A.detect_straight_cuts(_straight_figure().getchannel("A"))
    assert len(segs) == 1
    seg = segs[0]
    assert seg["side"] == "left" and abs(seg["pos"] - 160 / 399) < 0.01
    assert seg["start"] < 0.42 and seg["end"] > 0.68
    # elipse inteira (contorno de verdade) e retângulo encostado na borda (é `cut_edges`) não contam
    assert A.detect_straight_cuts(_figure((400, 600), (0.05, 0.05, 0.95, 0.95)).getchannel("A")) == []
    rect = Image.new("L", (300, 400), 0)
    ImageDraw.Draw(rect).rectangle((0, 50, 200, 399), fill=255)
    assert [s["side"] for s in A.detect_straight_cuts(rect)] == ["top", "right"]


def test_trecho_reto_some_no_degrade_sem_contorno(tmp_path):
    r = _kit(tmp_path, {"fig": (_straight_figure(), [])})
    layers = _hero("fig", x=0.6, y=0.95, h=0.9).model_copy(
        update={"background": BgLayer(kind="gradient", ref="#808080,#808080")})
    img = C.render(layers, T.CANVAS, r)
    f = img.info["layout"]["figures"][0]
    assert f["straight"] == 1
    # bem na linha reta (meio do trecho): nem contorno branco nem azul cheio
    fw = f["bbox"][2] - f["bbox"][0]
    x = round(f["bbox"][0] + fw * 160 / 399)
    y = round(f["bbox"][1] + (f["bbox"][3] - f["bbox"][1]) * 0.55)
    line = img.getpixel((x, y))
    assert min(line) < 200  # sem branco do contorno
    assert line[2] < 170  # e sem a figura cheia na linha
    assert img.getpixel((x + 70, y))[2] > 170  # mais para dentro a figura aparece


def test_acabamento_tira_franja_e_preserva_lado_ancorado():
    im = Image.new("RGBA", (200, 300), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 20, 179, 299), fill=(250, 240, 240, 120))  # franja clara de 1-2 px
    d.rectangle((0, 22, 177, 299), fill=(40, 90, 200, 255))
    out = C._refine(im, H, keep=["left", "bottom"])
    a = out.getchannel("A")
    assert a.getpixel((179, 150)) < 60  # a franja de fora quase some
    assert a.getpixel((0, 150)) == 255 and a.getpixel((100, 299)) == 255  # lado ancorado intacto
    assert a.getpixel((100, 150)) == 255


def test_manifesto_grava_trechos_retos_e_le_sem_manifesto(tmp_path):
    path = tmp_path / "fig.png"
    _straight_figure().save(path)
    segs = A.straight_cuts_of(path)  # recorte antigo: medido na hora
    assert segs and segs[0]["side"] == "left"
    assert A.straight_cuts_of(path, {"straight_cuts": []}) == []  # o do manifesto vale mais


# --- revisão: reta colada na base, moldura, folha de regressão e orçamento de tempo -----------------------------------
def _car_door_figure() -> Image.Image:
    """Dois "personagens" que terminam numa reta levemente inclinada (a porta do carro) a ~6% da base, com margem
    transparente embaixo: o que o casal-carro da biblioteca tem."""
    im = Image.new("RGBA", (900, 500), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((60, 20, 380, 520), fill=(200, 150, 120, 255))
    d.ellipse((480, 60, 860, 560), fill=(60, 60, 70, 255))
    # tudo abaixo da porta (de y=462 à esquerda a y=472 à direita) sai: reta quase horizontal
    mask = Image.new("L", im.size, 255)
    ImageDraw.Draw(mask).polygon([(0, 462), (900, 472), (900, 500), (0, 500)], fill=0)
    im.putalpha(ImageChops.multiply(im.getchannel("A"), mask))
    im.putpixel((5, 499), (0, 0, 0, 0))
    return im


def test_reta_colada_na_base_vira_lado_cortado(tmp_path):
    im = _car_door_figure()
    path = tmp_path / "porta.png"
    im.save(path)
    fig, edges, straight, _ = A.prepared_cutout(path, {"cut_edges": []})
    assert "bottom" in edges
    a = fig.getchannel("A")
    row = [a.getpixel((x, a.height - 1)) for x in range(0, a.width, 10)]
    assert sum(v > 128 for v in row) / len(row) > 0.6  # a base aparada é a figura, não a margem
    assert not any(s["side"] == "bottom" for s in straight)
    # no quadro: a base encosta na borda (nenhuma reta com contorno flutuando acima dela)
    r = _kit(tmp_path, {"porta": (im, [])})
    lay = C.render(_hero("porta", x=0.72, y=1.0, h=0.95), T.CANVAS, r).info["layout"]
    f = lay["figures"][0]
    assert "bottom" in f["cut_edges"] and f["bbox"][3] == T.CANVAS[1]


def test_lado_cortado_perde_a_moldura(tmp_path):
    im = _figure((400, 600), (0.1, 0.1, 0.9, 1.4))
    ImageDraw.Draw(im).rectangle((0, 597, 399, 599), fill=(53, 20, 37, 255))  # a moldura roxa da keyart
    path = tmp_path / "moldura.png"
    im.save(path)
    fig, edges, _, _ = A.prepared_cutout(path, {"cut_edges": ["bottom"]})
    assert edges == ["bottom"]
    last = fig.convert("RGB").getpixel((fig.width // 2, fig.height - 1))
    assert last != (53, 20, 37)


def test_trecho_inclinado_e_detectado_e_some_no_degrade():
    im = Image.new("L", (400, 600), 0)
    ImageDraw.Draw(im).polygon([(150, 100), (390, 100), (390, 580), (165, 580)], fill=255)  # lado esquerdo ~1,8°
    segs = [s for s in A.detect_straight_cuts(im) if s["side"] == "left"]
    assert segs and abs(segs[0]["pos_a"] - segs[0]["pos_b"]) > 0.02
    body, line = C._fade_mask((460, 660), 30, 400, 600, [], segs, 720)
    # o contorno some antes do corpo: onde o corpo ainda aparece pela metade, nada de branco por baixo
    import numpy as np
    b, o = np.asarray(body, dtype=np.int16), np.asarray(line, dtype=np.int16)
    assert (o <= b).all() and ((b > 60) & (b < 200) & (o > 10)).sum() == 0


@pytest.mark.parametrize("x", [0.72, 0.28])
def test_folha_de_regressao_dos_recortes(tmp_path, x):
    """Cada tipo de recorte da biblioteca (lado cortado, dois lados, reta de dentro, reta perto da base) no
    heroi-palavra dos dois lados: lado cortado sempre encostado na borda, e nada flutuando acima da base."""
    figs = {
        "base": (_figure((400, 600), (0.1, 0.1, 0.9, 1.4)), ["bottom"]),
        "base-lado": (_figure((400, 600), (0.1, 0.1, 1.4, 1.4)), ["bottom", "right"]),
        "reta": (_straight_figure(), []),
        "porta": (_car_door_figure(), []),
    }
    r = _kit(tmp_path, figs)
    W, H = T.CANVAS
    for cid in figs:
        img = C.render(_hero(cid, x=x, y=1.0, h=0.95), T.CANVAS, r)
        f = img.info["layout"]["figures"][0]
        if "bottom" in f["cut_edges"]:
            assert f["bbox"][3] == H
            alpha_row = [img.getpixel((xx, H - 1)) for xx in range(f["bbox"][0], f["bbox"][2], 8)]
            assert alpha_row  # a figura chega na base
        side = "left" if x < 0.5 else "right"
        if {"left", "right"} & set(f["cut_edges"]):
            assert side in f["cut_edges"] and (f["bbox"][0] == 0 if side == "left" else f["bbox"][2] == W)


def test_render_de_uma_figura_cabe_no_orcamento(tmp_path):
    import time

    big = _figure((1400, 1500), (0.05, 0.05, 0.95, 1.3))
    r = _kit(tmp_path, {"grande": (big, ["bottom"])})
    layers = _hero("grande", x=0.72, y=1.0, h=0.95)
    C.render(layers, T.CANVAS, r)  # 1ª: mede e guarda o recorte preparado
    t0 = time.perf_counter()
    C.render(layers, T.CANVAS, r)
    assert time.perf_counter() - t0 < 1.5
