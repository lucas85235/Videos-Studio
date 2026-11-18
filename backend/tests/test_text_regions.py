"""Letras da imagem (`media/text_regions.py`): a manchete da captura e o logo viram caixas na timeline, para o carimbo,
a faixa do topo e a tarja da legenda levarem em conta (QA do prj_gq4krrs11w: "BOATO" sobre o "C" de "PC",
"JANEIRO t auto" sobre o logo do GTA VI)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from studio.config import REPO_ROOT
from studio.media import text_regions

FONT = REPO_ROOT / "assets" / "fonts" / "Anton-Regular.ttf"


def _covers(rects: list[list[float]], box: tuple[float, float, float, float], share: float = 0.8) -> bool:
    """A união das caixas cobre `share` da área de `box` ([x0, y0, x1, y1] em fração)."""
    grid = np.zeros((200, 200), dtype=bool)
    for x, y, w, h in rects:
        grid[int(y * 200):int((y + h) * 200) + 1, int(x * 200):int((x + w) * 200) + 1] = True
    x0, y0, x1, y1 = (int(v * 200) for v in box)
    return grid[y0:y1, x0:x1].mean() >= share


def _page() -> Image.Image:
    """Captura de matéria: fundo branco, manchete grande em duas linhas e uma foto lisa embaixo."""
    img = Image.new("RGB", (1920, 1080), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 96)
    draw.text((120, 180), "Grand Theft Auto VI's PC", font=font, fill=(20, 20, 20))
    draw.text((120, 310), "release is slated for 2027", font=font, fill=(20, 20, 20))
    draw.rectangle([120, 600, 1100, 1000], fill=(60, 110, 160))
    return img


def test_headline_of_a_capture_becomes_a_box():
    rects = text_regions.detect(_page())
    assert rects and all(len(r) == 4 and all(0 <= v <= 1 for v in r) for r in rects)
    # As duas linhas da manchete (120-1300 px, 180-430 px) ficam cobertas; a foto lisa não vira texto.
    assert _covers(rects, (130 / 1920, 200 / 1080, 1000 / 1920, 420 / 1080))
    assert not _covers(rects, (200 / 1920, 650 / 1080, 1000 / 1920, 950 / 1080), share=0.2)


def test_big_logo_letters_on_dark_background_are_found():
    img = Image.new("RGB", (1920, 1080), (10, 10, 14))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 300)
    draw.text((900, 150), "grand", font=font, fill=(240, 240, 240))
    draw.text((900, 520), "theft", font=font, fill=(240, 240, 240))
    rects = text_regions.detect(img)
    assert _covers(rects, (960 / 1920, 300 / 1080, 1500 / 1920, 850 / 1080), share=0.6)
    # A metade esquerda (sem letra) fica livre: é para lá que o carimbo vai.
    assert not _covers(rects, (0.05, 0.1, 0.4, 0.9), share=0.05)


def test_smooth_photo_has_no_text():
    x = np.linspace(0, 255, 1920, dtype=np.float32)
    y = np.linspace(0, 120, 1080, dtype=np.float32)[:, None]
    arr = np.stack([np.broadcast_to(x, (1080, 1920)), np.broadcast_to(y + 60, (1080, 1920)),
                    np.full((1080, 1920), 140.0)], axis=-1).astype(np.uint8)
    assert text_regions.detect(Image.fromarray(arr)) == []


def test_radial_texture_is_not_text_and_body_text_is_found():
    """Fogos de artifício (raios em todas as direções) não viram caixa (pesavam como logo e empurravam carimbo e faixa);
    o corpo de matéria em letra miúda vira caixa por linha (a escala de 1280)."""
    img = Image.new("RGB", (1920, 1080), (8, 8, 20))
    draw = ImageDraw.Draw(img)
    rng = np.random.default_rng(3)
    for cx, cy in ((500, 400), (1300, 380)):
        for k in range(90):
            a = k * 2 * np.pi / 90 + rng.uniform(-0.02, 0.02)
            r = rng.uniform(150, 260)
            draw.line([cx, cy, cx + r * np.cos(a), cy + r * np.sin(a)], fill=(255, 200, 120), width=3)
    assert not _covers(text_regions.detect(img), (300 / 1920, 200 / 1080, 1500 / 1920, 600 / 1080), share=0.1)

    page = Image.new("RGB", (1920, 1080), (250, 250, 250))
    draw = ImageDraw.Draw(page)
    font = ImageFont.truetype(str(FONT), 30)
    for i in range(3):
        draw.text((160, 640 + i * 52), "Cibercriminosos exploram a expectativa pelo lançamento com downloads", font=font,
                  fill=(60, 60, 60))
    assert _covers(text_regions.detect(page), (200 / 1920, 650 / 1080, 900 / 1920, 780 / 1080), share=0.5)


def test_small_text_on_dark_page_is_found():
    """QA r3: o "VERSÃO" cinético cobria os resultados da busca do Google em modo escuro (letra de 12-16 px, azul e
    cinza sobre quase preto): a escala de 1280 suavizada não via nada e o texto que pula achava a página livre. A passada
    na resolução do arquivo pega as linhas (e o parágrafo de duas linhas coladas)."""
    img = Image.new("RGB", (1920, 1080), (32, 33, 36))
    draw = ImageDraw.Draw(img)
    font = ImageFont.truetype(str(FONT), 18)
    small = ImageFont.truetype(str(FONT), 13)
    for i in range(5):
        y = 140 + i * 170
        draw.text((540, y), "Rockstar Games: Grand Theft Auto VI demo download for PC", font=font, fill=(138, 180, 248))
        draw.text((540, y + 30), "Grand Theft Auto VI launches November 19, 2026 on PlayStation 5 and Xbox Series", font=small,
                  fill=(189, 193, 198))
        draw.text((540, y + 46), "Standard Edition. Grand Theft Auto VI; Vintage Vice City Pack; Free Month of access", font=small,
                  fill=(189, 193, 198))
    rects = text_regions.detect(img)
    assert _covers(rects, (560 / 1920, 140 / 1080, 1000 / 1920, 860 / 1080), share=0.35)
    assert not _covers(rects, (0.0, 0.0, 0.25, 1.0), share=0.02)  # a margem lisa fica livre


def test_main_block_becomes_keep_when_the_image_has_none(tmp_path: Path):
    """QA r3: a página de pré-venda (oficial, sem manchete guardada) saía "PRE-ORDEI" no punch e no plano 1,25x. O maior
    bloco de letras vira o `keep` da cena e do plano; a manchete da captura (`keep` que já veio) fica como está; letreiro
    pequeno não vira `keep`. Revisão r4: só material oficial ou captura (`prov` do plano ou da cena); foto de banco,
    do canal e imagem gerada ficam sem `keep` (a detecção por bordas confunde tela de neon, mão e teclado com letra),
    e o bloco que toma a imagem inteira também não vira `keep` (travaria o zoom perto de 1x)."""
    page = tmp_path / "cache" / "media" / "aa" / "image.jpg"
    page.parent.mkdir(parents=True)
    _page().save(page, quality=92)
    src = "cache/media/aa/image.jpg"
    timeline = {"scenes": [
        {"id": "a", "src": src, "prov": "official"},
        {"id": "b", "src": src, "prov": "capture", "keep": [0.1, 0.1, 0.5, 0.2]},
        {"id": "c", "src": None, "prov": "official", "shots": [{"from": 0, "src": src}]},
        {"id": "d", "src": src, "prov": "stock", "shots": [{"from": 0, "src": src, "prov": "capture"}]},
        {"id": "e", "src": src, "prov": "own"},
        {"id": "f", "src": src, "prov": "ai", "shots": [{"from": 0, "src": src, "prov": "free"}]},
        {"id": "g", "src": src},
    ]}
    text_regions.annotate_timeline(timeline, lambda rel: tmp_path / rel)
    a, b, c, d, e, f, g = timeline["scenes"]
    assert a["keep"] == text_regions.main_block(a["text_rects"])
    x, y, w, h = a["keep"]
    assert x <= 130 / 1920 and y <= 200 / 1080 and x + w >= 1000 / 1920 and y + h >= 420 / 1080
    assert b["keep"] == [0.1, 0.1, 0.5, 0.2]
    assert c["shots"][0]["keep"] == a["keep"] and "keep" not in c
    # Foto, imagem gerada e timeline antiga (sem `prov`): as letras ficam como obstáculo macio, sem `keep`.
    assert "keep" not in d and d["shots"][0]["keep"] == a["keep"]
    for item in (e, f, f["shots"][0], g):
        assert item["text_rects"] and "keep" not in item
    assert text_regions.main_block([[0.4, 0.4, 0.1, 0.05]]) is None
    assert text_regions.main_block([[0.09, 0.15, 0.82, 0.65]]) is None  # a imagem inteira, não uma manchete
    assert text_regions.main_block([[0.16, 0.14, 0.68, 0.36]]) == [0.16, 0.14, 0.68, 0.36]
    assert text_regions.main_block([]) is None


def test_merge_joins_touching_boxes_only():
    merged = text_regions.merge([[0.1, 0.1, 0.2, 0.05], [0.305, 0.1, 0.1, 0.05], [0.7, 0.7, 0.1, 0.1]])
    got = sorted([round(v, 6) for v in r] for r in merged)
    assert got == sorted([[0.1, 0.1, 0.305, 0.05], [0.7, 0.7, 0.1, 0.1]])


def test_annotate_timeline_writes_rects_and_caches_next_to_the_file(tmp_path: Path):
    page = tmp_path / "cache" / "media" / "aa" / "image.jpg"
    page.parent.mkdir(parents=True)
    _page().save(page, quality=92)
    timeline = {"scenes": [
        {"id": "a", "src": "cache/media/aa/image.jpg"},
        {"id": "b", "src": None},
        {"id": "c", "src": "cache/media/aa/image.jpg", "shots": [
            {"from": 0, "src": "cache/media/aa/image.jpg"}, {"from": 10, "video": "x.mp4", "src": "x.mp4"}]},
        {"id": "d", "src": "cache/media/missing.jpg"},
    ]}
    calls: list[str] = []

    def resolve(rel: str) -> Path:
        calls.append(rel)
        return tmp_path / rel

    text_regions.annotate_timeline(timeline, resolve)
    a, b, c, d = timeline["scenes"]
    assert a["text_rects"] and a["text_rects"] == c["text_rects"] == c["shots"][0]["text_rects"]
    assert "text_rects" not in b and "text_rects" not in d and "text_rects" not in c["shots"][1]
    assert calls.count("cache/media/aa/image.jpg") == 1  # uma detecção por arquivo
    cache = json.loads((page.parent / "image.jpg.text.json").read_text(encoding="utf-8"))
    assert cache["rects"] == a["text_rects"] and cache["key"]["v"] == text_regions.VERSION
    # Segunda vez: lê do cache (a detecção não roda).
    original = text_regions.detect
    text_regions.detect = lambda img: (_ for _ in ()).throw(AssertionError("não devia detectar de novo"))
    try:
        assert text_regions.rects_for(page) == a["text_rects"]
    finally:
        text_regions.detect = original


def test_build_annotates_the_timeline_with_image_text():
    src = (REPO_ROOT / "backend" / "studio" / "workers" / "pipeline" / "build.py").read_text(encoding="utf-8")
    assert src.count("self._lut_news_photos(timeline)\n") == 2
    assert src.count("text_regions.annotate_timeline(timeline, self.store.abs)") == 2
