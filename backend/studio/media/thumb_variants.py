"""Miniaturas para o teste A/B: render no tamanho do YouTube, limite de 2 MB e folhas de conferência.

Origem: `motor/passos/thumbs.py` do canais. A Ajuda do YouTube recomenda 3840 x 2160 (JPG ou PNG); o limite é 2 MB
pelo celular. As folhas mostram as 3 grandes (1280 x 720) e do jeito que aparecem no YouTube (168, 246 e 360 px de
largura, com o selo da duração no canto).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

from studio.media.thumbnail import make_thumbnail

TEMPLATE_CYCLE = ("texto-grande-esquerda", "texto-centro", "texto-baixo")
TEMPLATES = (*TEMPLATE_CYCLE, "sem-texto")
LONG_SIZE = (3840, 2160)
SHORT_SIZE = (1080, 1920)
MAX_BYTES = 2_000_000
QUALITIES = (92, 88, 84, 80, 76)
PREVIEW_H = 720
LONG_MAX_WORDS = 3
TOO_MANY_WORDS = "miniatura do longo: no máximo 3 palavras"
SMALL_SIZES = ((168, 94), (246, 138), (360, 202))
SMALL_GAP = 40
SMALL_ROW = 230
SMALL_BG = (15, 15, 15)
SMALL_LABEL = (200, 200, 200)
_BOLD_FONTS = ("C:/Windows/Fonts/arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")


def next_template(template: str) -> str:
    """Outro modelo para a 3ª: texto-grande-esquerda → texto-centro → texto-baixo → texto-grande-esquerda."""
    if template not in TEMPLATE_CYCLE:
        return TEMPLATE_CYCLE[0]
    return TEMPLATE_CYCLE[(TEMPLATE_CYCLE.index(template) + 1) % len(TEMPLATE_CYCLE)]


def target_size(is_long: bool) -> tuple[int, int]:
    return LONG_SIZE if is_long else SHORT_SIZE


def mmss(seconds: float | None) -> str:
    total = max(0, int(round(float(seconds or 0))))
    return f"{total // 60}:{total % 60:02d}"


def _bold(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _BOLD_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def text_warnings(text: str, is_long: bool) -> list[str]:
    return [TOO_MANY_WORDS] if is_long and len((text or "").split()) > LONG_MAX_WORDS else []


def encode_jpg(img: Image.Image, out: Path) -> tuple[int, int]:
    """JPG 92; acima de 2 MB, 88, 84, 80 e 76. Devolve (bytes, qualidade usada)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    rgb = img.convert("RGB")
    size, quality = 0, QUALITIES[0]
    for quality in QUALITIES:
        rgb.save(out, "JPEG", quality=quality, optimize=True)
        size = out.stat().st_size
        if size <= MAX_BYTES:
            break
    return size, quality


def preview_size(width: int, height: int) -> tuple[int, int]:
    """Prévia de 1280 x 720 (vídeo deitado) ou com 720 de altura (em pé)."""
    return (round(PREVIEW_H * width / height), PREVIEW_H) if height > width else (1280, 720)


def render_variant(base: Path | None, out: Path, *, text: str, template: str, is_long: bool, font: str = "Anton",
                   color: str = "#FFFFFF", accent: str = "#FFD400") -> dict[str, Any]:
    """Miniatura no tamanho do YouTube + prévia; `errors` (tamanho, 2 MB) e `warnings` (3 palavras no longo)."""
    width, height = target_size(is_long)
    tmp = out.with_suffix(".png")
    make_thumbnail(base, tmp, text=text, template=template, width=width, height=height, font=font, color=color,
                   accent=accent)
    try:
        with Image.open(tmp) as im:
            img = im.convert("RGB")
    finally:
        tmp.unlink(missing_ok=True)
    size_bytes, quality = encode_jpg(img, out)
    with Image.open(out) as done:
        w, h = done.size
        preview = out.with_name(f"{out.stem}-previa.jpg")
        done.convert("RGB").resize(preview_size(w, h), Image.Resampling.LANCZOS).save(preview, "JPEG", quality=90)
    mb = size_bytes / 1e6
    errors = [e for e in (f"{w}x{h}, esperava {width}x{height}" if (w, h) != (width, height) else "",
                          f"{mb:.2f} MB passa de 2 MB (limite pelo celular)" if size_bytes > MAX_BYTES else "") if e]
    return {"path": out, "preview": preview, "size": f"{w}x{h}", "mb": round(mb, 2), "quality": quality,
            "errors": errors, "warnings": text_warnings(text, is_long)}


def sheet_big(previews: Sequence[Path], out: Path) -> Path:
    """As prévias empilhadas (1280 x 720 cada)."""
    images = []
    for p in previews:
        with Image.open(p) as im:
            images.append(im.convert("RGB"))
    width = max((im.width for im in images), default=1280)
    height = sum(im.height for im in images) or PREVIEW_H
    big = Image.new("RGB", (width, height))
    y = 0
    for im in images:
        big.paste(im, ((width - im.width) // 2, y))
        y += im.height
    out.parent.mkdir(parents=True, exist_ok=True)
    big.save(out, "JPEG", quality=88)
    return out


def _badge(im: Image.Image, duration: str) -> None:
    """Selo da duração no canto (como o YouTube): caixa preta arredondada e texto branco."""
    sw, sh = im.size
    di = ImageDraw.Draw(im)
    fs = max(9, round(sh * 0.12))
    font = _bold(fs)
    tw = di.textlength(duration, font=font)
    bx0, by0 = sw - tw - fs * 0.9, sh - fs * 1.7
    di.rounded_rectangle((bx0, by0, sw - fs * 0.3, sh - fs * 0.35), radius=fs * 0.3, fill=(0, 0, 0))
    di.text((bx0 + fs * 0.3, by0 + fs * 0.12), duration, font=font, fill=(255, 255, 255))


def sheet_small(previews: Sequence[Path], labels: Sequence[str], duration: str, out: Path) -> Path:
    """Folha "do tamanho do YouTube": larguras 168, 246 e 360 px (alturas 94, 138, 202), 40 px entre elas, fundo
    (15, 15, 15), 230 px por linha, selo da duração e o rótulo "n. rótulo" em cinza."""
    W = sum(s[0] for s in SMALL_SIZES) + SMALL_GAP * (len(SMALL_SIZES) + 1)
    small = Image.new("RGB", (W, SMALL_GAP + len(previews) * SMALL_ROW), SMALL_BG)
    dr = ImageDraw.Draw(small)
    label_font = _bold(14)
    for k, path in enumerate(previews):
        with Image.open(path) as src:
            base = src.convert("RGB")
        x = SMALL_GAP
        for sw, sh in SMALL_SIZES:
            im = ImageOps.fit(base, (sw, sh), method=Image.Resampling.LANCZOS)
            _badge(im, duration)
            small.paste(im, (x, SMALL_GAP + k * SMALL_ROW))
            x += sw + SMALL_GAP
        label = labels[k] if k < len(labels) else ""
        dr.text((SMALL_GAP, SMALL_GAP + k * SMALL_ROW + 206), f"{k + 1}. {label}", font=label_font, fill=SMALL_LABEL)
    out.parent.mkdir(parents=True, exist_ok=True)
    small.save(out, "JPEG", quality=92)
    return out
