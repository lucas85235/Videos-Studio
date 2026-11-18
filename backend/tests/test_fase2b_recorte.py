"""Fase 2b: recorte de personagem com o rembg (opcional e cacheado). O rembg é sempre trocado por um falso: nenhum
modelo é baixado nem executado, nada do canais real nem do banco real."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from studio.core.project.document import CutoutLayer, TextLayer, ThumbLayers
from studio.media import thumb_assets as A
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T


# --- rembg falso ------------------------------------------------------------------------------------------------------
class FakeRembg:
    """Imita o `rembg.remove`: devolve RGBA do mesmo tamanho com o RGB zerado fora da máscara. A máscara tem uma
    figura (elipse) com um buraco pequeno dentro e uma ilha pequena solta, para testar a limpeza."""

    def __init__(self, empty: bool = False) -> None:
        self.calls = 0
        self.kwargs: list[dict] = []
        self.empty = empty

    def __call__(self, im: Image.Image, **kw) -> Image.Image:
        self.calls += 1
        self.kwargs.append(kw)
        w, h = im.size
        mask = Image.new("L", (w, h), 0)
        if not self.empty:
            d = ImageDraw.Draw(mask)
            d.ellipse((w * 0.2, h * 0.1, w * 0.8, h * 1.3), fill=255)
            d.ellipse((w * 0.45, h * 0.5, w * 0.5, h * 0.55), fill=0)  # buraco (regata clara)
            d.rectangle((w * 0.02, h * 0.02, w * 0.04, h * 0.04), fill=255)  # ilha
        out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        out.paste(im.convert("RGBA"), (0, 0), mask)
        return out


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeRembg:
    f = FakeRembg()
    monkeypatch.setattr(A, "HAS_REMBG", True)
    monkeypatch.setattr(A, "_rembg_remove", f)
    monkeypatch.setattr(A, "_rembg_session", lambda model: f"sessao:{model}")
    return f


@pytest.fixture()
def no_rembg(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(A, "HAS_REMBG", False)
    monkeypatch.setattr(A, "_rembg_remove", None)


def _photo(path: Path, size: tuple[int, int] = (800, 600), color: tuple[int, int, int] = (230, 200, 60)) -> Path:
    im = Image.new("RGB", size, color)
    ImageDraw.Draw(im).rectangle((0, 0, size[0] // 3, size[1]), fill=(40, 90, 200))
    im.save(path)
    return path


def _kit(tmp_path: Path) -> Path:
    kit = tmp_path / "kit"
    (kit / "frames").mkdir(parents=True)
    (kit / "logos").mkdir()
    m = A.empty_manifest("chn_teste")
    _photo(kit / "frames" / "capa.jpg")
    A.upsert_item(m, {"id": "capa", "kind": "frame", "file": "frames/capa.jpg", "editorial": True,
                      "credit": "Rockstar Games (capa oficial)"})
    _photo(kit / "frames" / "fundo.jpg", color=(255, 120, 80))
    A.upsert_item(m, {"id": "fundo", "kind": "frame", "file": "frames/fundo.jpg", "editorial": True})
    vi = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(vi).rectangle((20, 20, 380, 280), fill=(0, 255, 0, 255))
    vi.save(kit / "logos" / "vi.png")
    A.upsert_item(m, {"id": "vi", "kind": "logo", "file": "logos/vi.png", "tags": ["vi"], "editorial": True})
    A.save_manifest(kit, m)
    return kit


# --- cutout ------------------------------------------------------------------------------------------------------------
def test_sem_rembg_nao_recorta(tmp_path, no_rembg):
    src = _photo(tmp_path / "a.jpg")
    assert A.rembg_available() is False
    assert A.cutout(src, tmp_path / "a.png") is None
    assert A.auto_cutout(src, tmp_path / "cache") is None
    assert not (tmp_path / "a.png").exists()


def test_cutout_alfa_limpo_cor_original_e_aparado(tmp_path, fake):
    src = _photo(tmp_path / "src.png")  # PNG: cor exata (JPG mexe 1 nível)
    out = A.cutout(src, tmp_path / "r" / "a.png", model="u2net_human_seg")
    assert out is not None and out.is_file()
    assert fake.kwargs[0]["session"] == "sessao:u2net_human_seg" and fake.kwargs[0]["post_process_mask"] is False  # v2: limpeza suave nossa
    im = Image.open(out)
    assert im.mode == "RGBA"
    # aparado na figura: a ilha do canto saiu, então a largura é a da elipse (60% de 800) e não a imagem inteira
    assert abs(im.width - 480) <= 4 and im.height < 600
    # o buraco pequeno foi tapado com a cor ORIGINAL (o rembg zera o RGB; preto seria o erro)
    hx, hy = round(800 * 0.475) - round(800 * 0.2), round(600 * 0.525) - round(600 * 0.1)
    px = im.getpixel((hx, hy))
    assert px[3] == 255 and px[:3] == (230, 200, 60)
    # cache: arquivo mais novo que a fonte não roda de novo; force roda
    A.cutout(src, out)
    assert fake.calls == 1
    A.cutout(src, out, force=True)
    assert fake.calls == 2


def test_cutout_com_crop_e_lado_maximo(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(A, "CUTOUT_MAX_SIDE", 300)
    src = _photo(tmp_path / "src.png", size=(1200, 600))
    out = A.cutout(src, tmp_path / "a.png", crop=[0.5, 0, 1, 1])
    assert out is not None
    # o corte (600x600) é reduzido para 300x300 antes do rembg; a figura sai do lado direito (amarelo)
    im = Image.open(out)
    assert max(im.size) <= 300
    assert im.getpixel((im.width // 2, im.height // 2))[:3] == (230, 200, 60)


def test_cutout_sem_figura_devolve_none(tmp_path, monkeypatch):
    f = FakeRembg(empty=True)
    monkeypatch.setattr(A, "HAS_REMBG", True)
    monkeypatch.setattr(A, "_rembg_remove", f)
    monkeypatch.setattr(A, "_rembg_session", lambda model: None)
    src = _photo(tmp_path / "a.jpg")
    assert A.cutout(src, tmp_path / "a.png") is None
    # cache negativo: o automático não roda o rembg de novo para o mesmo conteúdo
    assert A.auto_cutout(src, tmp_path / "cache") is None
    assert A.auto_cutout(src, tmp_path / "cache") is None
    assert f.calls == 2


def test_auto_cutout_cache_pelo_conteudo(tmp_path, fake):
    a = _photo(tmp_path / "a.jpg")
    b = tmp_path / "b.jpg"
    b.write_bytes(a.read_bytes())  # mesmo conteúdo, outro nome: mesmo recorte
    cache = tmp_path / "cache"
    p1 = A.auto_cutout(a, cache)
    p2 = A.auto_cutout(b, cache)
    assert p1 == p2 and fake.calls == 1
    assert A.cutout_cache_key(a) != A.cutout_cache_key(a, model="u2net_human_seg")
    assert A.cutout_cache_key(a) != A.cutout_cache_key(a, crop=[0, 0, 0.5, 1])
    _photo(b, color=(10, 10, 10))  # conteúdo novo: recorta de novo
    assert A.auto_cutout(b, cache) != p1 and fake.calls == 2


def test_auto_cutout_falha_vira_none(tmp_path, monkeypatch):
    def boom(im, **kw):
        raise RuntimeError("onnx quebrado")

    monkeypatch.setattr(A, "HAS_REMBG", True)
    monkeypatch.setattr(A, "_rembg_remove", boom)
    monkeypatch.setattr(A, "_rembg_session", lambda model: None)
    assert A.auto_cutout(_photo(tmp_path / "a.jpg"), tmp_path / "c") is None


# --- make_cutouts / import_kit -----------------------------------------------------------------------------------------
def test_make_cutouts_gera_itens(tmp_path, fake):
    kit = _kit(tmp_path)
    m = A.load_manifest(kit)
    entries = [
        {"id": "jason-capa", "from": "capa", "crop": [0.4, 0, 1, 1], "tags": ["jason"], "face_box": [0.1, 0, 0.7, 0.5]},
        {"id": "lucia-x", "from": "nao-existe"},
        {"id": "fundo", "from": "capa"},  # id de um quadro: não sobrescreve
    ]
    s = A.make_cutouts(kit, m, entries)
    assert s["cutouts"] == 1
    assert any("nao-existe" in k for k in s["skipped"])
    assert any("fundo" in w and "frame" in w for w in s["warnings"])
    it = next(i for i in m["items"] if i["id"] == "jason-capa")
    assert it["kind"] == "cutout" and it["file"] == "cutouts/jason-capa.png"
    assert it["editorial"] is True and it["credit"] == "Rockstar Games (capa oficial)"  # herdado da capa
    assert it["source"] == {"from": "capa", "crop": [0.4, 0, 1, 1], "model": A.CUTOUT_MODEL, "v": A.CUTOUT_VERSION}
    assert it["face_box"] == [0.1, 0, 0.7, 0.5] and "crop" not in it and "from" not in it
    assert next(i for i in m["items"] if i["id"] == "fundo")["kind"] == "frame"
    assert A.has_alpha(kit / it["file"])
    A.make_cutouts(kit, m, entries[:1])  # idempotente e cacheado
    assert fake.calls == 1


def test_make_cutouts_sem_rembg_avisa(tmp_path, no_rembg):
    kit = _kit(tmp_path)
    m = A.load_manifest(kit)
    s = A.make_cutouts(kit, m, [{"id": "jason-capa", "from": "capa"}])
    assert s["cutouts"] == 0 and any("rembg" in w for w in s["warnings"])
    assert not any(i["kind"] == "cutout" for i in m["items"])


def test_import_kit_com_recortes(tmp_path, fake):
    canais = tmp_path / "canais"
    (canais / "v").mkdir(parents=True)
    _photo(canais / "v" / "capa.jpg")
    seed = {"videos": {}, "files": [{"id": "capa", "kind": "frame", "src": "v/capa.jpg", "editorial": True}],
            "frames": [], "cutouts": [{"id": "casal-capa", "from": "capa", "tags": ["casal"]}]}
    kit = tmp_path / "kit"
    s = A.import_kit("chn_teste", canais, seed=seed, kit_dir=kit)
    assert s["cutouts"] == 1 and s["total"] == 2
    r = A.AssetResolver(kit, allow_editorial=True)
    asset = r.resolve("cutout:casal-capa")
    assert asset.kind == "cutout" and asset.editorial is True
    s2 = A.import_kit("chn_teste", canais, seed=seed, kit_dir=kit, cutouts=False)
    assert s2["cutouts"] == 0 and s2["total"] == 2  # o recorte já feito continua no manifesto


def test_seed_real_recortes_validos():
    seed = A.load_seed()
    origins = {e["id"] for e in [*seed["files"], *seed["frames"]]}
    cut_ids = [c["id"] for c in seed.get("cutouts") or []]
    assert {"jason-capa", "lucia-capa", "casal-capa"} <= set(cut_ids)
    assert len(cut_ids) == len(set(cut_ids)) and not set(cut_ids) & origins
    for c in seed["cutouts"]:
        assert A.ID_RE.match(c["id"]) and c["from"] in origins
        for key in ("crop", "face_box"):
            if c.get(key):
                x0, y0, x1, y1 = c[key]
                assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
        assert c.get("model") in (None, *A.CUTOUT_MODELS)
        assert c.get("editorial", True) is True  # recorte de personagem da Rockstar é material oficial


# --- Compositor ----------------------------------------------------------------------------------------------------------
def _hero(asset: str, **kw) -> ThumbLayers:
    return ThumbLayers(template="heroi-palavra", characters=[CutoutLayer(asset=asset, x=kw.get("x", 0.72), y=1.0,
                                                                         h=kw.get("h", 0.95))],
                       big_vi=kw.get("big_vi", False), logo="none",
                       text=TextLayer(words="SEMANA 1", fill="yellow", pos="left", size=0.3))


def test_compositor_recorta_figura_sem_alfa(tmp_path, fake):
    kit = _kit(tmp_path)
    r = A.AssetResolver(kit, allow_editorial=True)
    img = C.render(_hero("frame:capa"), T.CANVAS, r)
    lay = img.info["layout"]
    assert any("recorte automático" in w for w in lay["warnings"])
    assert not any("precisa de recorte" in w for w in lay["warnings"])
    assert [f["kind"] for f in lay["figures"]] == ["cutout"]
    assert list((kit / "cutouts" / "_auto").glob("*.png"))  # cache dentro do kit
    C.render(_hero("frame:capa"), T.CANVAS, A.AssetResolver(kit, allow_editorial=True))
    assert fake.calls == 1  # segunda thumb usa o cache


def test_compositor_sem_rembg_ou_desligado_pede_recorte(tmp_path, fake, monkeypatch):
    kit = _kit(tmp_path)
    off = A.AssetResolver(kit, auto_cutout=False, allow_editorial=True)
    lay = C.render(_hero("frame:capa"), T.CANVAS, off).info["layout"]
    assert any("precisa de recorte" in w for w in lay["warnings"]) and not lay["figures"]
    monkeypatch.setattr(A, "HAS_REMBG", False)
    lay = C.render(_hero("frame:capa"), T.CANVAS, A.AssetResolver(kit, allow_editorial=True)).info["layout"]
    assert any("precisa de recorte" in w for w in lay["warnings"])
    assert fake.calls == 0


def test_compositor_falha_no_recorte_nao_derruba(tmp_path, fake, monkeypatch):
    kit = _kit(tmp_path)
    r = A.AssetResolver(kit, allow_editorial=True)
    monkeypatch.setattr(r, "cutout_for", lambda asset: (_ for _ in ()).throw(RuntimeError("x")))
    lay = C.render(_hero("frame:capa"), T.CANVAS, r).info["layout"]
    assert any("precisa de recorte" in w for w in lay["warnings"])


def test_rosto_anotado_fica_dentro_do_quadro(tmp_path, fake):
    kit = _kit(tmp_path)
    m = A.load_manifest(kit)
    A.make_cutouts(kit, m, [{"id": "lucia-capa", "from": "capa", "face_box": [0.5, 0.2, 1.0, 0.7]}])
    A.save_manifest(kit, m)
    r = A.AssetResolver(kit, allow_editorial=True)
    W, H = T.CANVAS
    lay = C.render(_hero("lucia-capa", x=0.95, h=1.0), T.CANVAS, r).info["layout"]
    face = lay["figures"][0]["face_box"]
    assert face[2] <= W - round(W * 0.02) + 1 and face[0] >= 0
    assert lay["figures"][0]["face_h"] == pytest.approx(0.5, abs=0.02)  # do face_box, não da estimativa
    flipped = ThumbLayers(template="heroi-palavra", logo="none",
                          characters=[CutoutLayer(asset="lucia-capa", x=0.02, y=1.0, h=1.0, flip=True)])
    face = C.render(flipped, T.CANVAS, r).info["layout"]["figures"][0]["face_box"]
    assert face[0] >= round(W * 0.02) - 1


def test_vi_gigante_atras_da_figura(tmp_path, fake):
    kit = _kit(tmp_path)
    m = A.load_manifest(kit)
    A.make_cutouts(kit, m, [{"id": "jason-capa", "from": "capa"}])
    A.save_manifest(kit, m)
    r = A.AssetResolver(kit, allow_editorial=True)
    lay = _hero("jason-capa", big_vi=True)
    drawn = C.Layout(T.CANVAS, "heroi-palavra")  # o VI segue a figura que foi desenhada de verdade
    drawn.figures.append({"kind": "cutout", "asset": "cutout:jason-capa", "bbox": [700, 0, 1280, 720]})
    assert C._vi_figure(lay, drawn) is not None
    assert C._vi_figure(lay, C.Layout(T.CANVAS, "heroi-palavra")) is None
    assert C._vi_figure(lay.model_copy(update={"template": "lugar-mapa"}), drawn) is None
    img = C.render(lay.model_copy(update={"text": None}), T.CANVAS, r)
    W, H = img.size

    def green(x0: float, x1: float) -> int:
        box = img.crop((round(x0 * W), round(0.05 * H), round(x1 * W), round(0.25 * H))).convert("RGB")
        data = box.tobytes()
        return sum(1 for i in range(0, len(data), 3) if data[i + 1] > 200 and data[i] < 80 and data[i + 2] < 80)

    # o VI (verde no teste) acompanha a figura à direita, não fica atrás do texto à esquerda
    assert green(0.5, 1.0) > green(0.0, 0.3)
    assert green(0.0, 0.3) == 0
