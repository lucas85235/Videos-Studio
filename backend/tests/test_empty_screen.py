"""Foto de banco com a tela vazia (`media/empty_screen.py`): o monitor de chroma crua e o monitor branco apagado que
saíram no vídeo real, e os parecidos que não podem cair (céu, foto lisa, painel escuro de app com texto)."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from studio.media import empty_screen as es

W, H = 1920, 1080


def _textured(seed: int = 1, base=(150, 120, 110), spread: float = 28.0) -> np.ndarray:
    """Fundo de foto: cor com textura (nunca liso)."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, spread, (H // 8, W // 8, 3))
    img = np.asarray(base, dtype=np.float32) + np.kron(noise, np.ones((8, 8, 1)))
    return np.clip(img, 0, 255)


def _jitter(img: np.ndarray, seed: int, spread: float = 1.5) -> Image.Image:
    rng = np.random.default_rng(seed)
    return Image.fromarray(np.clip(img + rng.normal(0, spread, img.shape), 0, 255).astype(np.uint8))


def chroma_monitor(screen=(45, 168, 115)) -> Image.Image:
    """O iMac de banco: moldura preta, tela de chroma verde com leve gradiente, sobre parede texturizada."""
    img = _textured(2)
    img[70:740, 205:1330] = (20, 20, 22)  # moldura
    ys = np.linspace(-6, 6, 700 - 115)[:, None, None]
    img[115:700, 250:1285] = np.asarray(screen, dtype=np.float32) + ys  # a tela, com a luz caindo
    img[740:850, 205:1330] = (225, 226, 230)  # queixo do monitor
    return _jitter(img, 3)


def blank_crt() -> Image.Image:
    """O CRT branco de banco: parede clara texturizada, gabinete branco, tela clara e lisa no meio."""
    img = _textured(4, base=(212, 217, 226), spread=10.0)
    img[165:900, 530:1395] = (246, 246, 248)  # gabinete
    img[250:767, 613:1312] = (200, 200, 205)  # o chanfro em volta da tela
    img[262:755, 625:1300] = (237, 239, 243)  # a tela
    return _jitter(img, 5)


def test_chroma_monitor_is_an_empty_screen():
    assert es.empty_screen(chroma_monitor()) == es.CHROMA
    assert es.empty_screen(chroma_monitor(screen=(20, 70, 200))) == es.CHROMA  # chroma azul


def test_blank_white_monitor_is_an_empty_screen():
    assert es.empty_screen(blank_crt()) == es.BLANK


def test_switched_off_screen_is_an_empty_screen():
    img = _textured(6)
    img[200:860, 300:1600] = (12, 12, 14)
    assert es.empty_screen(_jitter(img, 7)) == es.BLANK


def test_real_photos_are_not_empty_screens():
    assert es.empty_screen(_jitter(_textured(8), 9)) is None  # foto comum
    assert es.empty_screen(Image.new("RGB", (W, H), (30, 90, 160))) is None  # lisa de ponta a ponta: não é uma tela
    sky = _textured(10, base=(90, 120, 80))
    sky[:450] = (40, 110, 210)  # céu azul liso: toca a borda de cima
    assert es.empty_screen(_jitter(sky, 11)) is None
    small = _textured(12)
    small[400:560, 800:1100] = (45, 168, 115)  # verde liso, mas pequeno (2% da foto)
    assert es.empty_screen(_jitter(small, 13)) is None


def test_dark_app_panel_with_text_is_not_an_empty_screen():
    """Print de app no modo escuro (painel preto com linhas de texto no meio de um fundo): tem conteúdo."""
    img = Image.fromarray(_textured(14, base=(90, 70, 90)).astype(np.uint8))
    draw = ImageDraw.Draw(img)
    draw.rectangle((300, 150, 1620, 930), fill=(0, 0, 0))
    for n, y in enumerate(range(200, 900, 70)):
        draw.rectangle((340, y, 340 + 300 + (n * 137) % 900, y + 22), fill=(230, 230, 230))
    assert es.empty_screen(img) is None


def test_description_of_mockups():
    assert es.describes_empty_screen("Clean and minimalist setup with a computer and green screen on a white desk.")
    assert es.describes_empty_screen("A minimalist white computer setup including monitor, keyboard, and mouse.",
                                     "https://www.pexels.com/photo/white-computer-and-blank-screen-monitor-5185166/")
    assert es.describes_empty_screen("Laptop mockup on a wooden table")
    assert es.describes_empty_screen("Celular com tela verde para chroma key")
    assert es.describes_empty_screen("Smartphone with an empty phone screen")
    assert not es.describes_empty_screen("Blue screen of death on an office laptop")  # tela azul tem conteúdo
    assert not es.describes_empty_screen("High-tech gaming setup featuring a curved monitor, RGB keyboard.",
                                         "https://www.pexels.com/photo/computer-on-the-table-7858742/")
    assert not es.describes_empty_screen("Chromatic aberration on a city street at night")
    assert not es.describes_empty_screen("Razer Chroma RGB keyboard on a gaming desk")
