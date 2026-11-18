"""Foto de banco que não serve pelo formato (`media/stock_frame.py`): a foto de portfólio quadrada com moldura preta,
título e assinatura ("RETRO ROCKSTAR", que saiu no CTA do vídeo real com as laterais desfocadas), a prévia de banco
pago e a arte de fã; e os parecidos que não podem cair (céu escuro que vai sumindo, foto lisa, "glowing fans")."""

from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from studio.media import stock_frame as sf


def _photo(w: int, h: int, seed: int = 1, base=(60, 170, 90)) -> np.ndarray:
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 30, (-(-h // 8), -(-w // 8), 3))
    img = np.asarray(base, dtype=np.float32) + np.kron(noise, np.ones((8, 8, 1)))[:h, :w]
    return np.clip(img, 0, 255)


def portfolio_frame(size: int = 1024, band: float = 0.19, color=(0, 0, 0)) -> Image.Image:
    """A foto do vídeo real: 1024x1024, a foto deitada no meio, faixa preta em cima e embaixo, com o título no meio
    da faixa de baixo e a assinatura do fotógrafo no canto da foto."""
    img = np.zeros((size, size, 3), dtype=np.float32) + np.asarray(color, dtype=np.float32)
    top = int(size * band)
    img[top:size - top] = _photo(size, size - 2 * top)
    out = Image.fromarray(img.astype(np.uint8))
    draw = ImageDraw.Draw(out)
    draw.text((size * 0.42, size - top * 0.45), "RETRO ROCKSTAR", fill=(200, 160, 60))
    draw.text((size * 0.05, size - top - 40), "CARSTEN LINKE PHOTOGRAPHY", fill=(120, 120, 120))
    return out


def test_portfolio_photo_with_black_bands_and_caption_is_framed():
    assert sf.framed(portfolio_frame()) == sf.FRAMED
    assert sf.framed(portfolio_frame(color=(250, 250, 250))) == sf.FRAMED  # moldura branca


def test_side_bands_are_framed_too():
    img = np.zeros((683, 1024, 3), dtype=np.float32)
    img[:, 150:874] = _photo(724, 683, seed=3)
    assert sf.framed(Image.fromarray(img.astype(np.uint8))) == sf.FRAMED


def test_plain_photo_is_not_framed():
    assert sf.framed(Image.fromarray(_photo(1024, 683).astype(np.uint8))) is None


def test_night_sky_fading_into_the_city_is_not_framed():
    """Céu quase preto em cima que vai clareando: não corta seco, não é moldura (e só tem faixa de um lado)."""
    h, w = 683, 1024
    img = _photo(w, h, seed=5, base=(90, 80, 70))
    fade = np.clip(np.linspace(0, 1.4, h), 0, 1)[:, None, None]
    img = img * fade
    assert sf.framed(Image.fromarray(img.astype(np.uint8))) is None


def test_almost_flat_photo_is_not_framed():
    """Parede lisa ocupando quase tudo (faixas somadas acima de `MAX_BANDS`): não é moldura."""
    img = np.full((683, 1024, 3), 245, dtype=np.float32)
    img[300:330] = _photo(1024, 30, seed=7)
    assert sf.framed(Image.fromarray(img.astype(np.uint8))) is None


@pytest.mark.parametrize(("w", "h", "out"), [
    (1024, 1024, sf.PILLARBOX),  # a foto quadrada do CTA
    (1080, 1920, sf.PILLARBOX),  # vídeo em pé de banco num vídeo deitado
    (1024, 683, None),
    (4096, 3072, None),  # 4:3 recorta sem laterais
    (0, 0, None),  # sem tamanho: o anexo confere pelo arquivo
])
def test_pillarbox_by_proportion(w, h, out):
    assert sf.pillarboxed(w, h, 1920, 1080) == out


@pytest.mark.parametrize(("desc", "urls", "out"), [
    ("Businessman at desk", ("https://www.shutterstock.com/image-photo/x-123",), sf.WATERMARK),
    ("Hacker", ("https://media.istockphoto.com/id/1/photo.jpg",), sf.WATERMARK),
    ("Laptop on a table (watermarked preview)", (), sf.WATERMARK),
    ("GTA 6 Fan Cover", ("https://www.flickr.com/photos/x/34386102316",), sf.FAN_ART),
    ("Vice City fan-art poster", (), sf.FAN_ART),
    ("Sleek gaming desk setup with a gaming PC with glowing fans.", ("https://www.pexels.com/photo/x/",), None),
    ("Fans cheering at a concert", (), None),
])
def test_watermark_and_fan_art_by_description(desc, urls, out):
    assert sf.describes_watermark(desc, *urls) == out
