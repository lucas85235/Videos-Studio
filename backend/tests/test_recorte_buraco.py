"""Recorte com buraco no corpo e trecho reto da figura espelhada: a falta de dentro da figura (a pessoa da frente
tirada com `minus`, a roupa que o rembg comeu) tira o recorte da escolha automática, e o degradê do trecho reto
espelhado fica na linha certa (o queixo do Jason da capa sumia com o fundo aparecendo por dentro). Só ativos
sintéticos: nada de modelo, canais reais ou LLM."""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from studio.core.project.document import BgLayer, CutoutLayer, ThumbLayers
from studio.media import thumb_assets as A
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.media import thumb_variety as V
from studio.workers import tasks
from studio.workers.pipeline import authoring

pytest.importorskip("scipy")

W, H = T.CANVAS


# --- Silhuetas sintéticas ------------------------------------------------------------------------------------------------
def _bust(size: tuple[int, int] = (400, 600)) -> Image.Image:
    """Busto (cabeça, pescoço, tronco cortado embaixo) em RGBA azul."""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    d.ellipse((130, 20, 270, 190), fill=255)
    d.rectangle((165, 160, 235, 260), fill=255)
    d.ellipse((40, 230, 360, 720), fill=255)
    im = Image.new("RGBA", size, (40, 90, 200, 0))
    im.putalpha(m)
    return im


def _holed() -> Image.Image:
    """Busto com uma falta redonda no peito: a cabeça da pessoa da frente tirada com `minus`."""
    im = _bust()
    ImageDraw.Draw(im).ellipse((150, 330, 250, 430), fill=(0, 0, 0, 0))
    return im


def _hair_and_arms() -> Image.Image:
    """Silhueta normal: braços abertos, mão na cintura (vão fechado entre o braço e o tronco), fios de cabelo soltos
    (frestas finas) e o furo do brinco."""
    m = Image.new("L", (400, 600), 0)
    d = ImageDraw.Draw(m)
    d.ellipse((150, 10, 250, 120), fill=255)
    d.rectangle((140, 120, 260, 600), fill=255)
    d.line([(250, 140), (340, 260), (255, 360)], fill=255, width=45)  # mão na cintura
    d.line([(150, 140), (40, 250), (10, 330)], fill=255, width=30)  # braço aberto
    for k in range(5):
        d.line([(160 + k * 18, 15), (155 + k * 20, 90)], fill=0, width=2)  # fios de cabelo
    d.ellipse((150, 80, 158, 88), fill=0)  # brinco
    im = Image.new("RGBA", m.size, (40, 90, 200, 0))
    im.putalpha(m)
    return im


def _straight_figure() -> Image.Image:
    """Elipse com uma fatia reta tirada à esquerda (o queixo do Jason da capa, sem a cabeça da Lucia)."""
    im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((8, 0, 392, 600), fill=(40, 90, 200, 255))
    ImageDraw.Draw(im).rectangle((0, 240, 159, 420), fill=(0, 0, 0, 0))
    return im


def _kit(tmp_path: Path, cutouts: dict[str, tuple[Image.Image, list[str]]]) -> tuple[Path, list[dict]]:
    kit = tmp_path / "kit"
    (kit / "cutouts").mkdir(parents=True, exist_ok=True)
    m = A.empty_manifest("chn_teste")
    tags = {"jason-buraco": ["personagem", "jason", "rosto"], "jason-bom": ["personagem", "jason", "rosto"],
            "lucia-buraco": ["personagem", "lucia", "rosto"]}
    for cid, (im, edges) in cutouts.items():
        im.save(kit / "cutouts" / f"{cid}.png")
        A.upsert_item(m, {"id": cid, "kind": "cutout", "file": f"cutouts/{cid}.png", "cut_edges": edges,
                          "tags": tags.get(cid, ["personagem"])})
    A.save_manifest(kit, m)
    return kit, m["items"]


def _hero(asset: str, x: float = 0.28, flip: bool = False) -> ThumbLayers:
    return ThumbLayers(template="heroi-palavra", logo="none",
                       background=BgLayer(kind="gradient", ref="#808080,#808080"),
                       characters=[CutoutLayer(asset=asset, x=x, y=1.0, h=0.95, flip=flip)])


# --- Detecção ------------------------------------------------------------------------------------------------------------
def test_buraco_no_corpo_e_detectado():
    holes = A.detect_holes(_holed().getchannel("A"))
    assert len(holes) == 1
    x0, y0, x1, y1 = holes[0]["box"]
    assert 0.3 < x0 < x1 < 0.7 and 0.45 < y0 < y1 < 0.8  # no peito, em fração da figura aparada
    assert holes[0]["area"] > A.HOLE_MIN_AREA


def test_silhueta_normal_nao_tem_buraco():
    assert A.detect_holes(_bust().getchannel("A")) == []
    # braço aberto, mão na cintura, fios de cabelo e brinco: contorno natural
    assert A.detect_holes(_hair_and_arms().getchannel("A")) == []
    # a fatia reta tirada do lado não é buraco (é contorno: `detect_straight_cuts` cuida dela)
    assert A.detect_holes(_straight_figure().getchannel("A")) == []


def test_buraco_tira_o_recorte_da_escolha_e_tem_motivo(tmp_path):
    kit, items = _kit(tmp_path, {"jason-buraco": (_holed(), ["bottom"]), "jason-bom": (_bust(), ["bottom"])})
    by_id = {i["id"]: i for i in items}
    assert A.cutout_defect(by_id["jason-buraco"], kit) == A.HOLE_NOTE
    assert A.cutout_quality(by_id["jason-buraco"], kit) == A.QUALITY_LOW
    assert A.cutout_quality(by_id["jason-bom"], kit) == "ok"
    # o gravado no manifesto vale sem abrir o arquivo
    assert A.cutout_quality({"cut_edges": ["bottom"], "holes": [{"box": [0, 0, 1, 1], "area": 0.1}]}) == A.QUALITY_LOW
    assert A.cutout_quality({"cut_edges": ["bottom"], "holes": []}, kit) == "ok"
    r = A.AssetResolver(kit, allow_editorial=True, auto_cutout=False)
    assert [i["id"] for i in r.items("cutout")] == ["jason-bom"]


def test_make_cutouts_grava_o_buraco(tmp_path, monkeypatch):
    kit = tmp_path / "kit"
    (kit / "frames").mkdir(parents=True)
    Image.new("RGB", (400, 600), (230, 200, 60)).save(kit / "frames" / "q.png")
    m = A.empty_manifest("chn_teste")
    A.upsert_item(m, {"id": "q", "kind": "frame", "file": "frames/q.png", "editorial": True})

    def fake_cutout(src, dest, **kw):
        dest.parent.mkdir(parents=True, exist_ok=True)
        _holed().save(dest)
        return dest

    monkeypatch.setattr(A, "rembg_available", lambda: True)
    monkeypatch.setattr(A, "cutout", fake_cutout)
    summary = A.make_cutouts(kit, m, [{"id": "furado", "from": "q", "tags": ["personagem"]}])
    item = next(i for i in m["items"] if i["id"] == "furado")
    assert item["holes"] and item["quality"] == A.QUALITY_LOW and item["quality_note"] == A.HOLE_NOTE
    assert any("buraco no corpo" in w for w in summary["warnings"])


# --- Compositor ----------------------------------------------------------------------------------------------------------
def test_recorte_com_buraco_posto_a_mao_desenha_com_aviso(tmp_path):
    kit, _ = _kit(tmp_path, {"jason-buraco": (_holed(), ["bottom"])})
    r = A.AssetResolver(kit, allow_editorial=True, auto_cutout=False)
    lay = C.render(_hero("jason-buraco", x=0.7), T.CANVAS, r).info["layout"]
    assert lay["figures"] and lay["figures"][0]["asset"] == "jason-buraco"  # o do dono fica
    assert any("buraco no corpo" in w for w in lay["warnings"])


def test_trecho_reto_da_figura_espelhada_fica_na_linha_certa(tmp_path):
    """O Jason da capa (fatia reta no queixo, à esquerda) posto à esquerda da thumb sai espelhado: o degradê do
    trecho tem de ir junto com a linha. Antes, o `pos_a`/`pos_b` ficavam no x de antes do espelho e o degradê,
    virado para o outro lado, apagava a faixa inteira da figura (o fundo aparecia pelo queixo e pescoço)."""
    kit, _ = _kit(tmp_path, {"reta": (_straight_figure(), [])})
    r = A.AssetResolver(kit, allow_editorial=True, auto_cutout=False)
    for flip in (False, True):
        img = C.render(_hero("reta", x=0.28, flip=flip), T.CANVAS, r)
        f = img.info["layout"]["figures"][0]
        x0, y0, x1, y1 = f["bbox"]
        fw, fh = x1 - x0, y1 - y0
        y = round(y0 + fh * 0.55)  # no meio do trecho reto
        line_x = (160 - 8) / 384  # a linha, na figura aparada
        line_x = line_x if not flip else 1 - line_x
        for depth in (0.15, 0.35):  # para dentro do corpo: perto e longe da linha
            inner = line_x + depth if not flip else line_x - depth
            assert img.getpixel((round(x0 + fw * inner), y))[2] > 170, f"corpo apagado com flip={flip}"
        assert img.getpixel((round(x0 + fw * line_x), y))[2] < 170  # na linha: some no degradê


def test_espelho_do_trecho_reto_troca_as_pontas():
    seg = {"side": "left", "pos": 0.2, "pos_a": 0.18, "pos_b": 0.22, "start": 0.4, "end": 0.6}
    m = C._mirror_segment(seg)
    assert m["side"] == "right" and m["pos"] == pytest.approx(0.8)
    assert (m["pos_a"], m["pos_b"]) == (pytest.approx(0.82), pytest.approx(0.78))
    top = {"side": "top", "pos": 0.1, "pos_a": 0.08, "pos_b": 0.12, "start": 0.2, "end": 0.5}
    m = C._mirror_segment(top)
    assert (m["start"], m["end"]) == (pytest.approx(0.5), pytest.approx(0.8))
    assert (m["pos_a"], m["pos_b"]) == (0.12, 0.08)  # a ponta que era o fim agora é o começo
    back = C._mirror_segment(C._mirror_segment(seg))
    assert back["side"] == "left" and [back[k] for k in ("pos", "pos_a", "pos_b", "start", "end")] == pytest.approx(
        [seg[k] for k in ("pos", "pos_a", "pos_b", "start", "end")])


# --- Gerador -------------------------------------------------------------------------------------------------------------
GOOD = [{"id": "jason-bom", "tags": ["personagem", "jason", "rosto"]},
        {"id": "jason-rua", "tags": ["personagem", "jason", "corpo"]},
        {"id": "casal-carro", "tags": ["personagem", "jason", "lucia", "casal"]}]
LOW = [{"id": "jason-buraco", "tags": ["personagem", "jason", "rosto"], "defect": A.HOLE_NOTE},
       {"id": "lucia-buraco", "tags": ["personagem", "lucia", "rosto"], "defect": A.HOLE_NOTE}]


def test_prompt_nao_oferece_recorte_com_buraco():
    assets = authoring.PackageAssets(cutouts=GOOD, low_cutouts=LOW, allow_editorial=True)
    lines = assets.prompt_lines()
    assert "jason-bom" in lines and "jason-buraco" not in lines and "lucia-buraco" not in lines


def test_gerador_troca_o_recorte_com_buraco_pelo_do_mesmo_personagem():
    assets = authoring.PackageAssets(cutouts=GOOD, low_cutouts=LOW, allow_editorial=True)
    raw = [{"title": "Título bom do Jason", "thumb_text": "2027?", "template": "heroi-palavra",
            "characters_hint": ["jason-buraco"]},
           {"title": "Título bom da Lucia", "thumb_text": "AGORA", "template": "heroi-palavra",
            "characters_hint": ["lucia-buraco"]}]
    cands = authoring._normalize_candidates(raw, assets=assets, templates=["heroi-palavra"], is_long=True)
    jason, lucia = cands
    assert [c.asset for c in jason.layers.characters] == ["jason-bom"]  # nunca o corpo inteiro nem o casal
    assert any(n.startswith("CORRIGIDO: recorte jason-buraco") and "buraco no corpo" in n and "jason-bom" in n
               for n in jason.score["notes"])
    # sem outro recorte da Lucia: a thumb sai sem figura, com aviso
    assert lucia.layers.characters == []
    assert any("lucia-buraco" in n and "sem a figura" in n for n in lucia.score["notes"])


def test_troca_automatica_nao_escolhe_e_conserta_recorte_com_buraco():
    low = {c["id"]: c for c in LOW}
    layers = ThumbLayers(template="heroi-palavra", characters=[CutoutLayer(asset="jason-buraco", x=0.28, h=0.95)])
    fixed, notes = tasks._fix_low_figures(layers, GOOD, low, lambda v: 0.0)
    assert [c.asset for c in fixed.characters] == ["jason-bom"] and "CORRIGIDO" in notes[0]
    assert fixed.characters[0].x == 0.28 and layers.characters[0].asset == "jason-buraco"  # não mexe no original
    gone, notes = tasks._fix_low_figures(
        layers.model_copy(update={"characters": [CutoutLayer(asset="lucia-buraco")]}), GOOD, low, lambda v: 0.0)
    assert gone.characters == [] and "a figura saiu" in notes[0]
    same, notes = tasks._fix_low_figures(_hero("jason-bom"), GOOD, low, lambda v: 0.0)
    assert notes == [] and same.characters[0].asset == "jason-bom"
    # a troca por semelhança (`_swap_figure`) só anda entre os recortes bons
    swapped = tasks._swap_figure(ThumbLayers(template="heroi-palavra",
                                             characters=[CutoutLayer(asset="jason-bom")]), GOOD, lambda v: 0.0)
    assert swapped is None  # o único outro Jason é corpo inteiro: o com buraco nem está na lista


def test_variedade_nao_traz_o_recorte_com_buraco_de_volta():
    recent = V.Usage()
    recent.add({"character": ["jason-bom"]}, 2.0)
    notes: list[str] = []
    assert authoring._pick_characters(["jason-bom"], GOOD, recent, V.Usage(), notes) == ["jason-bom"]
