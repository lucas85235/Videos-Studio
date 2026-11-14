"""Thumbnails: imagem base sem texto + texto desenhado por código (vale para todos os idiomas)."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from studio.config import REPO_ROOT

FONTS_DIR = REPO_ROOT / "assets" / "fonts"


@lru_cache(maxsize=1)
def _font_manifest() -> dict:
    path = FONTS_DIR / "fonts.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load_font(family: str, size: int, weight: int = 800) -> ImageFont.FreeTypeFont:
    info = _font_manifest().get(family) or _font_manifest().get("Anton") or {}
    path = FONTS_DIR / info.get("file", "")
    if not path.is_file():
        return ImageFont.load_default(size=size)
    font = ImageFont.truetype(str(path), size=size)
    lo, hi = info.get("weights", [400, 400])
    if lo != hi:
        try:
            font.set_variation_by_axes([max(lo, min(hi, weight))])
        except (OSError, ValueError):
            pass
    return font


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit(draw: ImageDraw.ImageDraw, text: str, family: str, box_w: int, box_h: int, max_lines: int = 3) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    size = int(box_h / 1.6)
    while size > 18:
        font = load_font(family, size)
        lines = _wrap(draw, text, font, box_w)
        line_h = size * 1.05
        if len(lines) <= max_lines and line_h * len(lines) <= box_h:
            return font, lines
        size = int(size * 0.9)
    font = load_font(family, 18)
    return font, _wrap(draw, text, font, box_w)[:max_lines]


def make_thumbnail(
    base_image: Path | None,
    out_path: Path,
    *,
    text: str,
    template: str,
    width: int,
    height: int,
    font: str = "Anton",
    color: str = "#FFFFFF",
    accent: str = "#FFD400",
) -> Path:
    if base_image and base_image.exists():
        with Image.open(base_image) as im:
            img = ImageOps.fit(im.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
    else:
        img = Image.new("RGB", (width, height), (8, 8, 12))
    img = ImageEnhance.Contrast(img).enhance(1.12)
    img = ImageEnhance.Color(img).enhance(1.15)
    text = (text or "").strip().upper()
    if template == "sem-texto" or not text:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(out_path, quality=92)
        return out_path

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if template == "texto-grande-esquerda":
        for x in range(width // 2 + width // 6):
            alpha = int(215 * max(0.0, 1 - x / (width * 0.66)))
            od.line([(x, 0), (x, height)], fill=(0, 0, 0, alpha))
        box = (int(width * 0.05), int(height * 0.12), int(width * 0.5), int(height * 0.76))
    elif template == "texto-baixo":
        for y in range(height // 2, height):
            alpha = int(225 * (y - height / 2) / (height / 2))
            od.line([(0, y), (width, y)], fill=(0, 0, 0, alpha))
        box = (int(width * 0.06), int(height * 0.62), int(width * 0.88), int(height * 0.3))
    else:  # texto-centro
        od.rectangle([0, int(height * 0.3), width, int(height * 0.7)], fill=(0, 0, 0, 150))
        box = (int(width * 0.08), int(height * 0.32), int(width * 0.84), int(height * 0.36))
    img = Image.alpha_composite(img.convert("RGBA"), overlay.filter(ImageFilter.GaussianBlur(2)))

    draw = ImageDraw.Draw(img)
    x0, y0, bw, bh = box
    fnt, lines = _fit(draw, text, font, bw, bh)
    size = fnt.size
    line_h = int(size * 1.05)
    total_h = line_h * len(lines)
    y = y0 + (bh - total_h) // 2
    stroke = max(2, size // 14)
    for i, line in enumerate(lines):
        line_w = draw.textlength(line, font=fnt)
        x = x0 if template == "texto-grande-esquerda" else x0 + (bw - line_w) / 2
        fill = accent if i == len(lines) - 1 and len(lines) > 1 else color
        draw.text((x + size * 0.04, y + size * 0.05), line, font=fnt, fill=(0, 0, 0, 180))
        draw.text((x, y), line, font=fnt, fill=fill, stroke_width=stroke, stroke_fill="#000000")
        y += line_h
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out_path, quality=92)
    return out_path
