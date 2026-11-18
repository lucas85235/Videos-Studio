"""Compositor de thumbs por camadas (Pillow, determinístico). Recebe só o `ThumbLayers` (JSON) e devolve a imagem.

    img = render(layers, (1280, 720), resolver, palette)      # prévia
    img = render(layers, (3840, 2160), resolver, palette)     # exportação (geometria em fração: mesmo desenho)
    img.info["layout"]                                         # caixas do texto, figuras, avisos (para a checagem)

Mesmo `ThumbLayers` + mesmos arquivos = mesmos bytes (sem aleatoriedade, sem relógio). Os números de estilo seguem o
molde que funcionou no canais (`embalagem_v2_miniaturas.py`): Anton com contorno escuro (~7% do corpo), sombra
dura para baixo e halo escuro; fundo com saturação +15% e contraste ×1,08 (sem gradiente preto de meia tela);
avatar com contorno branco de adesivo, rim light rosa e sombra.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps, ImageStat

from studio.core.project.document import CutoutLayer, ThumbLayers
from studio.core.styles.schema import DEFAULT_THUMB_PALETTE
from studio.media import thumb_templates as T
from studio.media.thumb_assets import (
    Asset,
    AssetError,
    AssetResolver,
    anchorable,
    cut_edges_of,
    open_rgba,
    prepared_cutout,
    straight_cuts_of,
)
from studio.media.thumbnail import load_font

log = logging.getLogger(__name__)

VERSION = 2
"""Versão do desenho: muda quando o compositor passa a desenhar diferente (entra na chave do cache da capa)."""
BASE_H = 720
"""Altura de referência: todos os px fixos (contornos, sombras) são a 720p e escalam com o canvas."""
RED = (232, 22, 50)
WHITE = (255, 255, 255)
HIDDEN_MAX = 0.55
"""Personagem com mais que isso atrás do avatar sai da thumb (o que sobra aparecendo vira um borrão no canto)."""
GRADIENTS = {
    "vice": ((43, 16, 85), (255, 46, 136), (255, 138, 31)),
    "noite": ((10, 8, 30), (60, 20, 90), (255, 46, 136)),
    "mar": ((25, 211, 197), (40, 120, 200), (26, 11, 46)),
}


# --- Utilidades ------------------------------------------------------------------------------------------------------
def hex_rgb(value: str | Sequence[int], fallback: tuple[int, int, int] = (255, 212, 0)) -> tuple[int, int, int]:
    if isinstance(value, (tuple, list)) and len(value) >= 3:
        return int(value[0]), int(value[1]), int(value[2])
    s = str(value or "").lstrip("#")
    if len(s) != 6:
        return fallback
    try:
        return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    except ValueError:
        return fallback


def _palette(palette: dict[str, str] | None) -> dict[str, tuple[int, int, int]]:
    merged = {**DEFAULT_THUMB_PALETTE, **(palette or {})}
    return {k: hex_rgb(v) for k, v in merged.items()}


def _px(value: float, H: int) -> int:
    """px a 720p → px no canvas."""
    return max(1, round(value * H / BASE_H))


def _vertical_gradient(size: tuple[int, int], stops: Sequence[tuple[int, int, int]]) -> Image.Image:
    w, h = size
    col = Image.new("RGB", (1, 256))
    n = len(stops) - 1
    for y in range(256):
        t = y / 255 * n
        i = min(int(t), n - 1)
        f = t - i
        a, b = stops[i], stops[i + 1]
        col.putpixel((0, y), tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3)))
    return col.resize((w, h), Image.Resampling.BILINEAR)


def _horizontal_alpha(size: tuple[int, int], a0: float, a1: float) -> Image.Image:
    w, h = size
    row = Image.new("L", (256, 1))
    for x in range(256):
        t = x / 255
        t = t * t * (3 - 2 * t)  # suave nas pontas
        row.putpixel((x, 0), round(255 * (a0 + (a1 - a0) * t)))
    return row.resize((max(1, w), max(1, h)), Image.Resampling.BILINEAR)


def _trim(im: Image.Image) -> Image.Image:
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    return im.crop(box) if box else im


def _morph(alpha: Image.Image, radius: int, *, grow: bool = True) -> Image.Image:
    """Dilatação (ou erosão) arredondada do alfa por `radius` px, rápida em imagem grande: faz o filtro de máximo numa
    cópia reduzida e volta com degradê suave. Determinística."""
    if radius <= 0:
        return alpha.copy()
    src = alpha if grow else ImageOps.invert(alpha)
    w, h = src.size
    step = max(1, radius // 4)
    small = src.resize((max(1, w // step), max(1, h // step)), Image.Resampling.BILINEAR)
    r = max(1, round(radius / step))
    for _ in range(r):
        small = small.filter(ImageFilter.MaxFilter(3))
    small = small.filter(ImageFilter.GaussianBlur(max(0.6, r * 0.35)))
    big = small.resize((w, h), Image.Resampling.BILINEAR)
    big = big.point(lambda v: 0 if v < 70 else 255 if v > 150 else round((v - 70) * 255 / 80))
    out = ImageChops.lighter(big, src)
    return out if grow else ImageOps.invert(out)


def _shift(mask: Image.Image, dx: int, dy: int) -> Image.Image:
    """Desloca a máscara sem dar a volta na borda (o `ImageChops.offset` dá)."""
    out = Image.new(mask.mode, mask.size, 0)
    out.paste(mask, (dx, dy))
    return out


def _stroke_w(size_px: float, H: int) -> int:
    """Contorno do texto: 7% do corpo, entre 5 e 11 px a 720p (o molde do canais usa 9 px em letras de 230-420 px;
    acima disso o contorno vira borrão)."""
    return int(min(max(_px(5, H), round(size_px * 0.07)), _px(11, H)))


def _luma(im: Image.Image) -> float:
    small = im.convert("L").resize((64, 36), Image.Resampling.BILINEAR)
    hist = small.histogram()
    return sum(i * c for i, c in enumerate(hist)) / (255 * max(1, sum(hist)))


def _strip_letterbox(im: Image.Image) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """Tira a tarja preta de cima/baixo (trailers em 2,39:1 dentro do 16:9) e das laterais. Devolve a imagem e a
    caixa usada (no original)."""
    g = im.convert("L")
    w, h = g.size
    small = g.resize((max(1, w // 8), max(1, h // 8)), Image.Resampling.BOX)
    sw, sh = small.size
    px = small.load()

    def dark_row(y: int) -> bool:
        return max(px[x, y] for x in range(sw)) < 24

    def dark_col(x: int) -> bool:
        return max(px[x, y] for y in range(sh)) < 24

    top = 0
    while top < sh // 3 and dark_row(top):
        top += 1
    bottom = sh
    while bottom > sh * 2 // 3 and dark_row(bottom - 1):
        bottom -= 1
    left = 0
    while left < sw // 4 and dark_col(left):
        left += 1
    right = sw
    while right > sw * 3 // 4 and dark_col(right - 1):
        right -= 1
    full = (0, 0, w, h)
    if top <= 1 and bottom >= sh - 1 and left <= 1 and right >= sw - 1:
        return im, full
    pad = 1  # 1 linha a mais para não sobrar borda escura
    box = (min(w, (left + (pad if left else 0)) * 8), min(h, (top + (pad if top else 0)) * 8),
           max(0, (right - (pad if right < sw else 0)) * 8), max(0, (bottom - (pad if bottom < sh else 0)) * 8))
    if box[2] - box[0] < w * 0.5 or box[3] - box[1] < h * 0.5:
        return im, full
    return im.crop(box), box


# --- Layout (o que a checagem precisa saber) ---------------------------------------------------------------------------
class Layout:
    def __init__(self, size: tuple[int, int], template: str) -> None:
        self.size = size
        self.template = template
        self.text_bbox: tuple[int, int, int, int] | None = None
        self.text_lines: list[dict[str, Any]] = []
        self.text_fill: tuple[int, int, int] | None = None
        self.text_mask: Image.Image | None = None
        self.text_bg: tuple[float, float, float] | None = None
        """Cor média do fundo atrás do texto (antes do contorno), para o contraste da checagem."""
        self.pill_bbox: tuple[int, int, int, int] | None = None
        self.cards_bbox: list[tuple[int, int, int, int]] = []
        self.figures: list[dict[str, Any]] = []
        self.objects = 0
        self.warnings: list[str] = []
        self.assets: list[str] = []
        self.editorial = False
        self.big_vi = False
        """O VI gigante entrou: a marca já está grande na thumb e o logo pequeno sai."""
        self.bg_tags: list[str] = []
        """Tags do quadro de fundo (`vi`/`logo`: o fundo já mostra a marca e o logo pequeno sai)."""

    def as_dict(self) -> dict[str, Any]:
        return {
            "size": list(self.size), "template": self.template,
            "text_bbox": list(self.text_bbox) if self.text_bbox else None, "text_lines": self.text_lines,
            "text_fill": list(self.text_fill) if self.text_fill else None,
            "text_bg": [round(v, 1) for v in self.text_bg] if self.text_bg else None,
            "pill_bbox": list(self.pill_bbox) if self.pill_bbox else None,
            "cards_bbox": [list(b) for b in self.cards_bbox], "figures": self.figures, "objects": self.objects,
            "warnings": list(dict.fromkeys(self.warnings)), "assets": self.assets, "editorial": self.editorial,
        }


def _resolve(resolver: AssetResolver | None, ref: str | None, layout: Layout, what: str) -> Asset | None:
    if not ref or resolver is None:
        return None
    try:
        asset = resolver.resolve(ref)
    except AssetError as exc:
        layout.warnings.append(f"{what}: {exc}")
        return None
    except Exception as exc:  # noqa: BLE001 - ffmpeg/arquivo quebrado vira aviso, a thumb sai com o fallback
        layout.warnings.append(f"{what}: falhou ao abrir {ref} ({exc})")
        return None
    layout.assets.append(ref)
    layout.editorial = layout.editorial or asset.editorial
    return asset


# --- Fundo -------------------------------------------------------------------------------------------------------------
def _gradient_bg(name: str | None, size: tuple[int, int], pal: dict[str, tuple[int, int, int]]) -> Image.Image:
    stops: Sequence[tuple[int, int, int]]
    if name and "," in name:
        stops = [hex_rgb(c.strip(), (0, 0, 0)) for c in name.split(",") if c.strip()] or GRADIENTS["vice"]
        if len(stops) == 1:
            stops = [stops[0], stops[0]]
    else:
        stops = GRADIENTS.get(name or "vice", GRADIENTS["vice"])
    im = _vertical_gradient(size, stops).convert("RGBA")
    # brilho do sol: um halo claro no terço de baixo (fundo "pôr do sol" sem quadro)
    w, h = size
    glow = Image.new("L", size, 0)
    ImageDraw.Draw(glow).ellipse((w * 0.35, h * 0.45, w * 0.95, h * 1.25), fill=150)
    glow = glow.filter(ImageFilter.GaussianBlur(h * 0.12))
    im.paste(Image.new("RGBA", size, (*pal.get("yellow", (255, 212, 0)), 255)), (0, 0), glow.point(lambda v: v // 2))
    return im


def _cover(im: Image.Image, size: tuple[int, int], zoom: float, focus: tuple[float, float],
           target: tuple[float, float]) -> tuple[Image.Image, tuple[float, float, float]]:
    """Enquadra `im` no canvas (cover × zoom), pondo o ponto `focus` (fração da imagem) em `target` (fração do
    canvas). Devolve a imagem e a transformação (escala, origem x, origem y) para mapear caixas."""
    W, H = size
    iw, ih = im.size
    scale = max(W / iw, H / ih) * max(1.0, zoom)
    cw, ch = W / scale, H / scale
    left = focus[0] * iw - target[0] * cw
    top = focus[1] * ih - target[1] * ch
    left = min(max(0.0, left), iw - cw)
    top = min(max(0.0, top), ih - ch)
    out = im.resize((W, H), Image.Resampling.LANCZOS, box=(left, top, left + cw, top + ch))
    return out, (scale, left, top)


def _grade(im: Image.Image, blur: int, darken: float, H: int) -> Image.Image:
    rgb = im.convert("RGB")
    rgb = ImageEnhance.Color(rgb).enhance(1.15)
    rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
    lum = _luma(rgb)
    if lum < 0.36 and darken <= 0:
        # quadro muito escuro some no feed: levanta a exposição (no máximo +35%)
        rgb = ImageEnhance.Brightness(rgb).enhance(min(1.35, 0.40 / max(lum, 0.05)))
    if blur:
        rgb = rgb.filter(ImageFilter.GaussianBlur(_px(blur, H)))
    if darken > 0:
        rgb = ImageEnhance.Brightness(rgb).enhance(max(0.0, 1.0 - darken))
    return rgb.convert("RGBA")


def _background(layers: ThumbLayers, size: tuple[int, int], resolver: AssetResolver | None,
                pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any],
                bg_key: str = "bg", ref: str | None = None) -> Image.Image:
    bg = layers.background
    W, H = size
    geo = tpl.get(bg_key) or tpl["bg"]
    ref = ref if ref is not None else bg.ref
    asset = None
    if bg.kind in ("frame", "media", "upload") and ref:
        asset = _resolve(resolver, ref, layout, "fundo")
    elif bg.kind == "ai":
        layout.warnings.append("fundo por IA desligado (sem cota): usei o degradê")
    if asset is None:
        return _gradient_bg(ref if bg.kind == "gradient" else "vice", size, pal)
    if bg_key == "bg":
        layout.bg_tags = [str(t) for t in (asset.meta.get("tags") or [])]
    raw = open_rgba(asset.path).convert("RGB")
    src, (cx0, cy0, _cx1, _cy1) = _strip_letterbox(raw)
    rw, rh = raw.size
    sw, sh = src.size

    def to_src(fx: float, fy: float) -> tuple[float, float]:
        """Fração do quadro original (manifesto) → fração do quadro sem tarja."""
        return (fx * rw - cx0) / sw, (fy * rh - cy0) / sh

    focus = to_src(bg.focus_x, float(asset.meta.get("focus_y", 0.42)))
    face = asset.meta.get("face_box")
    if H > W and sw / sh > 1.2 and tpl.get("band"):
        # Short com quadro deitado: fundo desfocado + o quadro inteiro numa faixa (o rosto não vira borrão)
        framed, _ = _cover(src, size, 1.0, focus, (0.5, 0.5))
        framed = _grade(framed, 24, 0.30, H)
        band_h = round(W * sh / sw)
        band, (scale, ox, oy) = _cover(src, (W, band_h), bg.zoom, focus, (0.5, 0.45))
        band = _grade(band, bg.blur, bg.darken, H)
        by = round(H * tpl["band"])
        framed.alpha_composite(band, (0, by))
        edge = Image.new("RGBA", (W, _px(6, H)), (*WHITE, 255))
        framed.alpha_composite(edge, (0, by - edge.height))
        framed.alpha_composite(edge, (0, by + band_h))
        offset_y = by
    else:
        target_x = geo.get("target_x", 0.5)
        if bg_key == "bg" and _mirrored(layers):
            target_x = 1 - target_x  # o assunto do quadro vai para o lado oposto ao texto
        framed, (scale, ox, oy) = _cover(src, size, bg.zoom, focus, (target_x, geo.get("target_y", 0.42)))
        framed = _grade(framed, bg.blur, bg.darken, H)
        offset_y = 0
    if face and bg_key == "bg":
        (a0, b0), (a1, b1) = to_src(face[0], face[1]), to_src(face[2], face[3])
        fx0, fy0 = (a0 * sw - ox) * scale, (b0 * sh - oy) * scale + offset_y
        fx1, fy1 = (a1 * sw - ox) * scale, (b1 * sh - oy) * scale + offset_y
        box = [round(max(0, fx0)), round(max(0, fy0)), round(min(W, fx1)), round(min(H, fy1))]
        if box[2] > box[0] and box[3] > box[1]:
            layout.figures.append({"kind": "frame", "bbox": box, "face_box": box,
                                   "face_h": round((min(H, fy1) - max(0, fy0)) / H, 3)})
    return framed


def _mirrored(layers: ThumbLayers) -> bool:
    """O molde está espelhado: texto à direita, figura e avatar à esquerda. Só nos templates que espelham
    (`T.MIRRORABLE`): na prova, no versus e no livre o "Direita" não mexe no resto do molde."""
    return bool(layers.text and layers.text.pos == "right" and layers.template in T.MIRRORABLE)


def _shade(canvas: Image.Image, tpl: dict[str, Any], pal: dict[str, tuple[int, int, int]], has_text: bool,
           mirrored: bool = False) -> None:
    sh = tpl.get("shade")
    if not sh or not has_text:
        return
    W, H = canvas.size
    width = round(W * sh["width"])
    alpha = _horizontal_alpha((width, H), sh["alpha"], 0.0)
    side = sh.get("side", "left")
    if mirrored:
        side = _MIRROR.get(side, side)
    if side == "right":
        alpha = ImageOps.mirror(alpha)
    layer = Image.new("RGBA", (width, H), (*pal["ink"], 255))
    x = 0 if side != "right" else W - width
    canvas.alpha_composite(Image.composite(layer, Image.new("RGBA", layer.size, (0, 0, 0, 0)), alpha), (x, 0))


# --- Adesivo (avatar, recortes, objetos) ------------------------------------------------------------------------------
def _grow_aa(alpha: Image.Image, radius: int) -> Image.Image:
    """Dilatação do alfa por `radius` px com borda antisserrilhada: distância euclidiana exata até a figura (scipy)
    e rampa de ~1 px no raio, calculada na resolução final (o `_morph` faz o máximo numa cópia reduzida e volta
    ampliando: a borda do contorno saía em escada). Sem scipy cai no `_morph`."""
    if radius <= 0:
        return alpha.copy()
    try:
        import numpy as np
        from scipy import ndimage  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001 - scipy vem com o rembg; sem ele, o contorno antigo
        return _morph(alpha, radius)
    a = np.asarray(alpha, dtype=np.float32) / 255.0
    inside = a >= 0.5
    if not inside.any():
        return alpha.copy()
    # distância de cada pixel ao centro do pixel de figura mais perto; a borda da figura fica em ~0,5 px
    dist = ndimage.distance_transform_edt(~inside)
    ring = np.clip(radius + 0.5 - dist, 0.0, 1.0)
    out = np.maximum(ring, a)
    return Image.fromarray(np.clip(out * 255 + 0.5, 0, 255).astype(np.uint8), "L")


def _extend_cut(base: Image.Image, pad: int, w: int, h: int, cut: Sequence[str]) -> None:
    """Estica a última linha/coluna da figura para dentro da margem nos lados cortados: o contorno, o rim light e a
    sombra não desenham ali (o lado cortado encosta na borda da thumb e continua "para fora")."""
    if not cut or pad <= 0:
        return
    bw, bh = base.size
    if "top" in cut:
        base.paste(base.crop((pad, pad, pad + w, pad + 1)).resize((w, pad)), (pad, 0))
    if "bottom" in cut:
        base.paste(base.crop((pad, pad + h - 1, pad + w, pad + h)).resize((w, pad)), (pad, pad + h))
    if "left" in cut:  # depois de cima/baixo: a coluna já leva os cantos esticados
        base.paste(base.crop((pad, 0, pad + 1, bh)).resize((pad, bh)), (0, 0))
    if "right" in cut:
        base.paste(base.crop((pad + w - 1, 0, pad + w, bh)).resize((pad, bh)), (pad + w, 0))


def _sticker(fig: Image.Image, H: int, *, outline: int, rim: tuple[int, int, int] | None, ink: tuple[int, int, int],
             shadow: bool = True, cut: Sequence[str] = (),
             fade: Any = None) -> tuple[Image.Image, int]:
    """Figura com contorno branco por dilatação do alfa, rim light na borda de dentro e sombra. Nos lados cortados
    (`cut`) a figura continua pela margem e nada disso aparece. `fade(size, pad)` devolve as máscaras (corpo,
    contorno) do degradê dos lados/trechos retos que somem (`_fade_mask`), ou None: o corpo some suave e o contorno
    branco some antes e sem borrão (senão o branco de baixo aparece pelo corpo semitransparente). Devolve a imagem
    com margem e o tamanho da margem."""
    o = _px(outline, H) if outline else 0
    pad = o + _px(26, H)
    w, h = fig.size
    base = Image.new("RGBA", (w + 2 * pad, h + 2 * pad), (0, 0, 0, 0))
    base.paste(fig, (pad, pad))
    _extend_cut(base, pad, w, h, cut)
    alpha = base.getchannel("A")
    masks = fade(base.size, pad) if fade is not None else None
    out = Image.new("RGBA", base.size, (0, 0, 0, 0))
    grown = _grow_aa(alpha, o) if o else alpha
    shadow_src = grown
    if masks is not None:
        body_mask, line_mask = masks
        shadow_src = ImageChops.multiply(grown, body_mask)
        grown = ImageChops.multiply(grown, line_mask)
        base.putalpha(ImageChops.multiply(alpha, body_mask))
    if shadow:
        sh = shadow_src.filter(ImageFilter.GaussianBlur(_px(12, H))).point(lambda v: v * 6 // 10)
        dy = _px(10, H)
        sh_layer = Image.new("RGBA", base.size, (*ink, 255))
        sh_layer.putalpha(_shift(sh, 0, dy))
        out.alpha_composite(sh_layer)
    if o:
        white = Image.new("RGBA", base.size, (*WHITE, 255))
        white.putalpha(grown)
        out.alpha_composite(white)
    body = base
    if rim:
        r = _px(4, H)
        inner = _morph(alpha, r, grow=False)
        band = ImageChops.subtract(alpha, inner).filter(ImageFilter.GaussianBlur(max(1, r * 0.6)))
        band = band.point(lambda v: v * 8 // 10)
        lit = ImageChops.screen(base.convert("RGB"), Image.new("RGB", base.size, rim))
        rgb = Image.composite(lit, base.convert("RGB"), band)
        body = rgb.convert("RGBA")
        body.putalpha(base.getchannel("A"))
    out.alpha_composite(body)
    return out, pad


_MIRROR = {"left": "right", "right": "left"}


def _flip_edges(cut: Sequence[str], flip: bool) -> set[str]:
    return {_MIRROR.get(e, e) for e in cut} if flip else set(cut)


def _head_center_x(fig: Image.Image) -> float:
    """x do centro da cabeça (fração da largura da figura): média das colunas opacas nas linhas de cima."""
    a = fig.getchannel("A")
    w, h = a.size
    top = a.crop((0, 0, w, max(1, round(h * 0.10)))).point(lambda v: 255 if v > 128 else 0)
    box = top.getbbox()
    if not box:
        return 0.5
    return (box[0] + box[2]) / 2 / w


def _presenter(canvas: Image.Image, layers: ThumbLayers, resolver: AssetResolver | None,
               pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any]) -> None:
    p = layers.presenter
    if p is None:
        return
    if resolver is not None and not getattr(resolver, "presenter_on", True):
        return  # avatar desligado na thumb deste projeto: a camada fica no candidato, mas não aparece
    asset = _resolve(resolver, f"pose:{p.pose}", layout, "avatar")
    if asset is None:
        return
    W, H = canvas.size
    cut = cut_edges_of(asset.path)  # medido no PNG com margem: braço/cotovelo que encosta na lateral da pose
    if not anchorable(cut):
        layout.warnings.append(f"avatar {p.pose}: pose cortada em {', '.join(cut)} não encaixa na borda; saiu")
        return
    src = _trim(open_rgba(asset.path))
    pt = tpl.get("presenter", {})
    h = p.h
    if "h_max" in pt:  # heroi-palavra e prova: avatar pequeno, o foco é outro
        h = min(h, pt["h_max"])
    crop = p.crop
    target = pt.get("head_x", 0.77) if p.side == "right" else 1 - pt.get("head_x", 0.77)

    def place(flip: bool) -> tuple[Image.Image, int, int, float, set[str]]:
        """(figura no tamanho final, x, top, centro da cabeça em px, lados cortados) com o espelho `flip`."""
        fig = ImageOps.mirror(src) if flip else src
        edges = _flip_edges(cut, flip)
        scale = h * H / fig.height
        hx = _head_center_x(fig)
        fw = max(1, round(fig.width * scale))
        if crop == "busto":
            top = round(H * pt.get("top", 0.035))
            # lado cortado da pose do lado da thumb: cresce em volta da cabeça até a borda da pose passar da thumb
            if p.side == "right" and "right" in edges and W * target + (1 - hx) * fw < W and hx < 0.97:
                fw = max(fw, min(round(fw * 1.6), math.ceil((1 - target) * W / (1 - hx))))
            if p.side == "left" and "left" in edges and W * target - hx * fw > 0 and hx > 0.03:
                fw = max(fw, min(round(fw * 1.6), math.ceil(target * W / hx)))
        fh = max(1, round(fig.height * fw / fig.width))
        fig = fig.resize((fw, fh), Image.Resampling.LANCZOS)
        head_x = hx * fw
        if crop == "busto":
            x = round(W * target - head_x)
        else:
            top = H - fh + _px(p.outline_px + 4, H)  # corte da cintura fica para fora do quadro
            margin = round(W * 0.02)
            x = W - fw - margin if p.side == "right" else margin
        # lado cortado encosta na borda da thumb (passa um pouco, nunca fica solto dentro dela)
        if "right" in edges and x + fw < W:
            x = W - fw
        if "left" in edges and x > 0:
            x = 0
        if "bottom" in edges and top + fh < H:
            top = H - fh
        if "top" in edges:
            top = min(top, 0)
        return fig, x, top, head_x, edges

    flip = p.flip
    fig, x, top, head_x, edges = place(flip)
    if edges & {"left", "right"} and abs((x + head_x) / W - target) > 0.12:
        # o lado cortado só encostou porque a figura andou muito (corte do lado de dentro): espelha a pose
        alt = place(not flip)
        if abs((alt[1] + alt[3]) / W - target) < abs((x + head_x) / W - target):
            fig, x, top, head_x, edges = alt
            flip = not flip
            layout.warnings.append(f"avatar {p.pose}: espelhado para o lado cortado da pose ficar na borda")
    sticker, pad = _sticker(fig, H, outline=p.outline_px, rim=pal.get("pink"), ink=pal["ink"], cut=sorted(edges))
    canvas.alpha_composite(_clip_paste(sticker, canvas.size, x - pad, top - pad), (0, 0))
    fw, fh = fig.size
    head = T.HEAD_FRACTION * fh
    face = [max(0, round(x + head_x - head * 0.45)), max(0, top), min(W, round(x + head_x + head * 0.45)),
            min(H, round(top + head))]
    bbox = [max(0, x), max(0, top), min(W, x + fw), min(H, top + fh)]
    layout.figures.append({"kind": "presenter", "pose": p.pose, "bbox": bbox, "face_box": face,
                           "face_h": round(head / H, 3), "crop": crop, "side": p.side,
                           "cut_edges": sorted(edges), "flip": flip})


def _clip_paste(layer: Image.Image, size: tuple[int, int], x: int, y: int) -> Image.Image:
    """Camada do tamanho do canvas com `layer` em (x, y), cortando o que vaza."""
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    out.paste(layer, (x, y))
    return out


def _cutouts(size: tuple[int, int], layers: ThumbLayers, resolver: AssetResolver | None,
             pal: dict[str, tuple[int, int, int]], layout: Layout) -> list[tuple[Image.Image, dict[str, Any] | None]]:
    """Cada figura numa camada própria do tamanho do canvas, com o item que ela pôs em `layout.figures` (None para
    logo/objeto): quem chama confere se o avatar não a esconde antes de juntar."""
    out: list[tuple[Image.Image, dict[str, Any] | None]] = []
    for c in layers.characters:
        asset = _resolve(resolver, c.asset, layout, "figura")
        if asset is None:
            continue
        im = open_rgba(asset.path)
        if im.getchannel("A").getextrema()[0] >= 250:
            # sem alfa: recorte automático (rembg, cacheado); sem rembg a figura sai e a tela pede o recorte
            cut = _auto_cutout(resolver, asset)
            if cut is None:
                layout.warnings.append(f"figura {c.asset}: precisa de recorte (imagem sem transparência)")
                continue
            layout.warnings.append(f"figura {c.asset}: recorte automático (confira as bordas)")
            asset, im = cut, open_rgba(cut.path)
        is_logo = asset.kind in ("logo", "object")
        meta = asset.meta if asset.kind == "cutout" else None
        edges: list[str] = []
        straight: list[dict[str, Any]] = []
        fig = None
        if not is_logo:
            try:
                fig, edges, straight, meta_fixed = prepared_cutout(asset.path, meta)
                meta = meta_fixed if meta is not None else ({"face_box": meta_fixed["face_box"]}
                                                            if meta_fixed.get("face_box") else None)
            except (OSError, ValueError) as exc:  # arquivo sumiu no meio: mede como antes
                log.warning("recorte %s: preparo falhou (%s)", asset.ref, exc)
                edges, straight = cut_edges_of(asset.path, meta), straight_cuts_of(asset.path, meta)
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        before = len(layout.figures)
        if _place_cutout(layer, fig if fig is not None else _trim(im), c, asset.kind, pal, layout,
                         meta if meta is not None else asset.meta, edges, straight):
            out.append((layer, layout.figures[-1] if len(layout.figures) > before else None))
    return out


def _hidden_fraction(fig: Image.Image, cover: Image.Image) -> float:
    """Quanto da figura (alfa) fica atrás de `cover` (alfa do avatar), medido numa cópia pequena."""
    import numpy as np

    small = (320, max(1, round(320 * fig.height / max(1, fig.width))))
    a = np.asarray(fig.getchannel("A").resize(small, Image.Resampling.BOX), dtype=np.float32) / 255
    b = np.asarray(cover.getchannel("A").resize(small, Image.Resampling.BOX), dtype=np.float32) / 255
    total = float(a.sum())
    return float((a * b).sum()) / total if total > 0 else 0.0


def _auto_cutout(resolver: AssetResolver | None, asset: Asset) -> Asset | None:
    fn = getattr(resolver, "cutout_for", None)
    if fn is None:
        return None
    try:
        return fn(asset)
    except Exception as exc:  # noqa: BLE001 - recorte é opcional: falha vira o aviso "precisa de recorte"
        log.warning("recorte automático falhou (%s): %s", asset.ref, exc)
        return None


def _face_px(fb: Sequence[float], flip: bool, x: float, y: float, fw: float, fh: float) -> tuple[float, ...]:
    """Caixa do rosto (fração do recorte aparado, `face_box`) em px da thumb, com a figura em (x, y) e (fw, fh)."""
    fx0, fx1 = (1 - fb[2], 1 - fb[0]) if flip else (fb[0], fb[2])
    return (x + fx0 * fw, y + fb[1] * fh, x + fx1 * fw, y + fb[3] * fh)


def _clear_badge(src: Image.Image, fb: Sequence[float], flip: bool, edges: set[str],
                 size: tuple[int, int], x: int, y: int, fw: int, fh: int) -> tuple[int, int, int, int, bool]:
    """Tira o rosto da zona do selo de duração (canto de baixo à direita da thumb deitada), sem soltar o lado
    cortado da borda: sem corte embaixo a figura sobe; com corte embaixo ela cresce a partir da base (o rosto anda
    para cima e para longe da lateral ancorada, até 1,6×); sem corte lateral, ainda pode andar para a esquerda.
    Devolve (x, y, fw, fh, ok)."""
    W, H = size
    zone = T.forbidden_zones((W, H)).get("selo") if W > H else None
    if not zone:
        return x, y, fw, fh, True
    zx0, zy0 = zone[0] * W, zone[1] * H

    def hit(x_: float, y_: float, w_: float, h_: float) -> bool:
        f = _face_px(fb, flip, x_, y_, w_, h_)
        return f[2] > zx0 and f[3] > zy0

    if not hit(x, y, fw, fh) or "top" in edges:
        return x, y, fw, fh, not hit(x, y, fw, fh)
    if "bottom" not in edges:
        ny = round(zy0 - fb[3] * fh) - 1
        if _face_px(fb, flip, x, ny, fw, fh)[1] >= 0:
            return x, ny, fw, fh, True
    else:
        for k in range(1, 13):
            nfh = round(fh * (1 + 0.05 * k))
            nfw = max(1, round(src.width * nfh / src.height))
            nx = W - nfw if "right" in edges else 0 if "left" in edges else round(x + fw / 2 - nfw / 2)
            ny = H - nfh
            if _face_px(fb, flip, nx, ny, nfw, nfh)[1] < 0:
                break  # o rosto sairia por cima
            if not hit(nx, ny, nfw, nfh):
                return nx, ny, nfw, nfh, True
    if not edges & {"left", "right"}:
        f = _face_px(fb, flip, x, y, fw, fh)
        dx = math.ceil(f[2] - zx0) + 1
        if f[0] - dx >= W * 0.02:
            return x - dx, y, fw, fh, True
    return x, y, fw, fh, False


FADE_SIDE = 0.14
"""Degradê do lado cortado que não encosta na borda da thumb, em fração do lado da figura (entre 24 e 110 px a
720p): a figura some no fundo em vez de terminar numa reta."""
FADE_STRAIGHT = 0.06
"""Degradê de um trecho reto de dentro do recorte (crop, figura tirada da frente), em fração da figura."""


def _refine(fig: Image.Image, H: int, keep: Sequence[str] = ()) -> Image.Image:
    """Acabamento do recorte no tamanho final: erosão leve do alfa (~1 px a 720p, tira a franja do fundo antigo) e
    feather. A cor da borda não é refeita aqui: o PNG já sai descontaminado do recorte (`thumb_assets.cutout`), e
    refazer a cada render custava ~1 s por figura. Os lados em `keep` (cortados e encostados na borda da thumb) não
    perdem nada: a borda da imagem é replicada antes da erosão e a última faixa fica como estava."""
    import numpy as np

    r = max(1, round(H / BASE_H))
    a = np.asarray(fig.getchannel("A"), dtype=np.uint8)
    if a.min() >= 250:
        return fig
    pad = r + 3
    src = Image.fromarray(np.pad(a, pad, mode="edge"), "L")
    eroded = src.filter(ImageFilter.MinFilter(2 * r + 1)).filter(ImageFilter.GaussianBlur(0.5 * r + 0.3))
    soft = np.minimum(np.asarray(eroded, dtype=np.uint8)[pad:-pad, pad:-pad], a)
    band = 2 * r + 2
    if "left" in keep:
        soft[:, :band] = a[:, :band]
    if "right" in keep:
        soft[:, -band:] = a[:, -band:]
    if "top" in keep:
        soft[:band, :] = a[:band, :]
    if "bottom" in keep:
        soft[-band:, :] = a[-band:, :]
    out = fig.copy()
    out.putalpha(Image.fromarray(np.ascontiguousarray(soft), "L"))
    return out


def _smooth(t: Any) -> Any:
    return t * t * (3 - 2 * t)


def _fade_mask(size: tuple[int, int], pad: int, fw: int, fh: int, sides: Sequence[str],
               segments: Sequence[dict[str, Any]], H: int) -> tuple[Image.Image, Image.Image] | None:
    """Máscaras (L, do tamanho do adesivo com margem) que somem com a figura nos lados cortados que ficaram dentro
    do quadro (`sides`: degradê no lado inteiro) e nos trechos retos de dentro (`segments`: degradê só no trecho,
    com as pontas suavizadas; a linha pode ser inclinada, de `pos_a` a `pos_b`). Devolve (corpo, contorno): o corpo
    some em degradê; o contorno branco (e o branco por baixo do corpo) sai inteiro até o fim do degradê, sem rampa,
    e um pouco além das pontas do trecho: o corpo semitransparente não deixa vazar um borrão branco. None quando não
    há o que sumir."""
    import numpy as np

    if not sides and not segments:
        return None
    bw, bh = size
    m = np.ones((bh, bw), dtype=np.float32)
    o = np.ones((bh, bw), dtype=np.float32)
    xs = np.arange(bw, dtype=np.float32)
    ys = np.arange(bh, dtype=np.float32)
    for side in sides:
        horizontal = side in ("left", "right")
        width = min(max(_px(24, H), (fw if horizontal else fh) * FADE_SIDE), _px(110, H))
        # distância para dentro da figura a partir da borda daquele lado (< 0 na margem: contorno e sombra)
        dist = {"left": xs - pad, "right": pad + fw - xs, "top": ys - pad, "bottom": pad + fh - ys}[side]
        body = _smooth(np.clip(dist / width, 0, 1))
        line = np.clip((dist - width) / 2 + 1, 0, 1)  # 0 até o fim do degradê
        if horizontal:
            m, o = np.minimum(m, body[None, :]), np.minimum(o, line[None, :])
        else:
            m, o = np.minimum(m, body[:, None]), np.minimum(o, line[:, None])
    for seg in segments:
        side = seg["side"]
        horizontal = side in ("left", "right")
        length, depth = (fh, fw) if horizontal else (fw, fh)
        width = min(max(_px(10, H), min(fw, fh) * FADE_STRAIGHT), _px(44, H))
        a0, a1 = pad + seg["start"] * length, pad + seg["end"] * length
        along = ys if horizontal else xs
        across = xs if horizontal else ys
        pa, pb = float(seg.get("pos_a", seg["pos"])), float(seg.get("pos_b", seg["pos"]))
        t = np.clip((along - a0) / max(1.0, a1 - a0), 0, 1)
        line_at = pad + (pa + (pb - pa) * t) * (depth - 1)  # onde a linha está, ao longo do trecho
        # ao longo do trecho: peso 1 dentro, cai a 0 em `width` px depois das pontas
        w_along = _smooth(np.clip(1 - np.maximum(a0 - along, along - a1) / width, 0, 1))
        sign = 1.0 if side in ("left", "top") else -1.0
        d = sign * (across[None, :] - line_at[:, None])  # > 0: para dentro da figura
        fade = 1 - w_along[:, None] * (1 - _smooth(np.clip(d / width, 0, 1)))  # (ao longo, através)
        # contorno: some inteiro até o fim do degradê, e um pouco além das pontas (rampa curta, sem borrão)
        reach = np.clip((np.maximum(a0 - along, along - a1) - width) / _px(6, H), 0, 1)
        o_fade = np.maximum(reach[:, None], np.clip((d - width) / 2 + 1, 0, 1))
        m = np.minimum(m, fade if horizontal else fade.T)
        o = np.minimum(o, o_fade if horizontal else o_fade.T)
    o = np.minimum(o, m)

    def img(v: Any) -> Image.Image:
        return Image.fromarray(np.clip(v * 255 + 0.5, 0, 255).astype(np.uint8), "L")

    return img(m), img(o)


def _place_cutout(canvas: Image.Image, fig: Image.Image, c: CutoutLayer, kind: str,
                  pal: dict[str, tuple[int, int, int]], layout: Layout, meta: dict[str, Any] | None = None,
                  cut: Sequence[str] = (), straight: Sequence[dict[str, Any]] = ()) -> bool:
    """Põe a figura na thumb. Lado cortado da figura (`cut`: a borda do quadro original passou por ela) é ancorado
    na borda correspondente da thumb: embaixo encosta na base (crescendo a partir do topo, ou descendo), lateral
    encosta na lateral do lado em que a figura está (espelha se o corte estiver do lado de dentro), em cima + embaixo
    ocupa a altura toda. O lado cortado que não dá para encostar (as duas laterais, só em cima, ou o que a figura
    não alcançou depois de posta) some num degradê, como os trechos retos de dentro do recorte (`straight`): nunca
    sobra uma reta dentro do quadro. Devolve True quando desenhou."""
    W, H = canvas.size
    is_logo = kind in ("logo", "object")
    cut = [] if is_logo else list(cut)
    straight = [] if is_logo else list(straight)
    flip = c.flip
    side = "left" if c.x < 0.5 else "right"
    if set(cut) & {"left", "right"} and side not in _flip_edges(cut, flip) and not {"left", "right"} <= set(cut):
        flip = not flip  # corte do lado de dentro: espelha para ele ficar na borda
        layout.warnings.append(f"figura {c.asset}: espelhada para o lado cortado ficar na borda")
    all_edges = _flip_edges(cut, flip)
    # o que dá para encostar: a lateral do lado da figura, a base, e o topo só junto com a base
    edges = {e for e in all_edges if e == "bottom" or e == side or (e == "top" and "bottom" in all_edges)}
    faded = sorted(all_edges - edges)
    if faded:
        layout.warnings.append(f"figura {c.asset}: lado cortado em {', '.join(faded)} não encosta na borda; "
                               "saiu com degradê")
    if flip:
        fig = ImageOps.mirror(fig)
        straight = [_mirror_segment(s) for s in straight]
    src = fig
    fh = max(1, round(c.h * H))
    fw = max(1, round(fig.width * fh / fig.height))
    if fw > W * 0.95 and not edges:  # logo largo: limita pela largura
        fw = round(W * 0.95)
        fh = max(1, round(fig.height * fw / fig.width))
    top = round(c.y * H - fh)
    if "top" in edges:  # cortada em cima e embaixo: a altura toda da thumb
        top, fh = 0, H
        fw = max(1, round(fig.width * fh / fig.height))
    elif "bottom" in edges and top + fh < H:
        grow = H - top  # mantém o topo (o rosto fica onde o candidato pôs) e cresce até a base
        if grow <= fh * 1.6:
            fh = grow
            fw = max(1, round(fig.width * fh / fig.height))
        else:
            top = H - fh
    fig = fig.resize((fw, fh), Image.Resampling.LANCZOS)
    x = round(c.x * W - fw / 2)
    y = top
    # o lado cortado encosta na borda; passando muito dela (figura larga, como o casal no carro), volta até encostar
    # (senão quem está daquele lado sai com meia cabeça para fora do quadro)
    if "right" in edges and (x + fw < W or x + fw - W > fw * 0.05):
        x = W - fw
    if "left" in edges and (x > 0 or -x > fw * 0.05):
        x = 0
    fb = (meta or {}).get("face_box")
    if not is_logo and fb and len(fb) == 4 and not edges & {"left", "right"}:
        # o corpo pode vazar pela borda, o rosto não: empurra a figura para o rosto caber (margem de 2%)
        fx0, fx1 = (1 - fb[2], 1 - fb[0]) if flip else (fb[0], fb[2])
        margin = round(W * 0.02)
        right, left = x + fx1 * fw, x + fx0 * fw
        if right > W - margin:
            x -= round(right - (W - margin))
        elif left < margin:
            x += round(margin - left)
    if not is_logo and fb and len(fb) == 4:
        x, y, nfw, nfh, ok = _clear_badge(src, fb, flip, edges, (W, H), x, y, fw, fh)
        if (nfw, nfh) != (fw, fh):
            fw, fh = nfw, nfh
            fig = src.resize((fw, fh), Image.Resampling.LANCZOS)
            layout.warnings.append(f"figura {c.asset}: aumentada para o rosto sair da zona do selo de duração")
        if not ok:
            layout.warnings.append(f"figura {c.asset}: rosto na zona do selo de duração (canto de baixo à direita)")
    # depois de posta: o lado ancorado que não chegou na borda (crescer tem limite) também some no degradê
    reach = {"left": x <= 0, "right": x + fw >= W, "top": y <= 0, "bottom": y + fh >= H}
    loose = sorted(e for e in edges if not reach[e])
    if loose:
        layout.warnings.append(f"figura {c.asset}: lado cortado em {', '.join(loose)} não chegou na borda; "
                               "saiu com degradê")
    anchored = sorted(e for e in edges if reach[e])
    if not is_logo:
        fig = _refine(fig, H, anchored)
    fading = [*faded, *loose]

    def fade(size: tuple[int, int], pad: int) -> tuple[Image.Image, Image.Image] | None:
        return _fade_mask(size, pad, fw, fh, fading, straight, H)

    sticker, pad = _sticker(fig, H, outline=0 if is_logo else c.outline_px, rim=None if is_logo else pal.get("pink"),
                            ink=pal["ink"], cut=anchored, fade=None if is_logo else fade)
    canvas.alpha_composite(_clip_paste(sticker, canvas.size, x - pad, y - pad), (0, 0))
    box = [max(0, x), max(0, y), min(W, x + fw), min(H, y + fh)]
    if is_logo:
        layout.objects += 1
        layout.figures.append({"kind": "object", "asset": c.asset, "bbox": box})
        return True
    if fb and len(fb) == 4:  # rosto anotado no manifesto (fração do recorte aparado)
        fx0, fx1 = (1 - fb[2], 1 - fb[0]) if flip else (fb[0], fb[2])
        face = [x + fx0 * fw, y + fb[1] * fh, x + fx1 * fw, y + fb[3] * fh]
        face = [round(max(0, face[0])), round(max(0, face[1])), round(min(W, face[2])), round(min(H, face[3]))]
        head = (fb[3] - fb[1]) * fh
    else:
        head = T.HEAD_FRACTION * 1.4 * fh  # sem anotação: cabeça um pouco maior que a da pose do avatar
        face = [box[0], box[1], box[2], round(box[1] + head)]
    layout.figures.append({"kind": "cutout", "asset": c.asset, "bbox": box, "face_box": face,
                           "face_h": round(head / H, 3), "cut_edges": anchored, "flip": flip,
                           "faded": sorted({*faded, *loose}), "straight": len(straight)})
    return True


def _mirror_segment(seg: dict[str, Any]) -> dict[str, Any]:
    """Trecho reto da figura espelhada na horizontal."""
    side = seg["side"]
    if side in ("left", "right"):
        return {**seg, "side": _MIRROR[side], "pos": 1 - seg["pos"]}
    return {**seg, "start": 1 - seg["end"], "end": 1 - seg["start"]}


def _big_vi(canvas: Image.Image, layers: ThumbLayers, resolver: AssetResolver | None,
            pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any], text_box: tuple) -> None:
    if not layers.big_vi:
        return
    W, H = canvas.size
    conf = tpl.get("big_vi", {"h": 0.86, "alpha": 0.92})
    ref = resolver.big_vi_id() if resolver else None
    asset = _resolve(resolver, f"logo:{ref}", layout, "VI gigante") if ref else None
    cx = (text_box[0] + text_box[2] / 2) * W
    figure = _vi_figure(layers, layout)
    if figure is not None:
        # com recorte de personagem (heroi-palavra), o VI fica atrás da figura e aparece pelo lado do texto; a
        # posição é a da figura desenhada (já ancorada na borda), não a pedida
        fx = (figure["bbox"][0] + figure["bbox"][2]) / 2 / W
        cx = (fx - conf.get("behind_offset", 0.07)) * W
    # atrás da figura o VI sangra pelo topo (e pela direita, quando chega perto dela): com a borda de cima à mostra
    # ele parecia um painel colado no quadro
    bleed = figure is not None
    if asset is not None:
        vi = _trim(open_rgba(asset.path))
        vh = round(H * (max(conf["h"], 1.0) if bleed else conf["h"]))
        vw = round(vi.width * vh / vi.height)
        vi = vi.resize((vw, vh), Image.Resampling.LANCZOS)
    else:
        font = load_font("Anton", round(H * conf["h"] * 1.25))
        box = font.getbbox("VI")
        vi = Image.new("RGBA", (box[2] - box[0] + 4, box[3] - box[1] + 4), (0, 0, 0, 0))
        mask = Image.new("L", vi.size, 0)
        ImageDraw.Draw(mask).text((-box[0] + 2, -box[1] + 2), "VI", font=font, fill=255)
        grad = _vertical_gradient(vi.size, (pal["pink"], pal["orange"])).convert("RGBA")
        vi = Image.composite(grad, vi, mask)
        vw, vh = vi.size
    x = round(min(max(cx - vw / 2, -vw * 0.1), W - vw * 0.9))
    y = round((H - vh) / 2)
    if bleed:
        y = -round(H * 0.07)
        if x + vw > W - vw * 0.12:
            x = W - vw + round(vw * 0.1)
    glow = vi.getchannel("A").filter(ImageFilter.GaussianBlur(_px(18, H))).point(lambda v: v * 7 // 10)
    glow_layer = Image.new("RGBA", vi.size, (*pal["ink"], 255))
    glow_layer.putalpha(glow)
    canvas.alpha_composite(_clip_paste(glow_layer, canvas.size, x, y), (0, 0))
    vi.putalpha(vi.getchannel("A").point(lambda v: round(v * conf.get("alpha", 0.92))))
    canvas.alpha_composite(_clip_paste(vi, canvas.size, x, y), (0, 0))
    layout.objects += 1
    layout.big_vi = True


def _vi_figure(layers: ThumbLayers, layout: Layout) -> dict[str, Any] | None:
    """O recorte de personagem que o VI gigante acompanha (só no heroi-palavra): o 1º que foi desenhado de verdade
    (`layout.figures`); recorte recusado (corte que não encaixa) não puxa o VI para trás de uma figura que não
    existe, e aí o VI fica atrás do texto."""
    if layers.template != "heroi-palavra":
        return None
    return next((f for f in layout.figures if f.get("kind") == "cutout" and f.get("bbox")), None)


# --- Texto -------------------------------------------------------------------------------------------------------------
def _split_lines(words: str, template: str, pos: str, tconf: dict[str, Any]) -> list[str]:
    tokens = words.split()
    if not tokens:
        return []
    if tconf.get("number") and len(tokens) > 1 and any(ch.isdigit() for ch in tokens[0]):
        rest = tokens[1:]
        # coluna estreita: cada palavra longa na sua linha ("SEMANA" / "COMPLETA"); as curtas juntam à seguinte
        if len(rest) > 1 and len(" ".join(rest)) > 8:
            return [tokens[0], *_split_lines(" ".join(rest), "", "left", {})]
        return [tokens[0], " ".join(rest)]
    if tconf.get("box"):
        return [words] if len(tokens) <= 1 else ([" ".join(tokens[:-1]), tokens[-1]] if len(tokens) == 3 else tokens)
    if pos == "top":
        return [words] if len(tokens) <= 2 else [" ".join(tokens[:2]), " ".join(tokens[2:])]
    if len(tokens) <= 1:
        return tokens
    # empilha: palavras curtas ("7", "DE", "NO") se juntam à seguinte
    lines: list[str] = []
    buf = ""
    for tok in tokens:
        if buf and len(buf) <= 2:
            buf = f"{buf} {tok}"
            lines.append(buf)
            buf = ""
        elif buf:
            lines.append(buf)
            buf = tok
        else:
            buf = tok
    if buf:
        if lines and len(buf) <= 2:
            lines[-1] = f"{lines[-1]} {buf}"
        else:
            lines.append(buf)
    return lines


def _emphasis(lines: list[str]) -> int:
    for i, line in enumerate(lines):
        if any(ch.isdigit() for ch in line):
            return i
    return len(lines) - 1


def _ink_box(font: ImageFont.FreeTypeFont, text: str) -> tuple[int, int, int, int]:
    return font.getbbox(text)


def fill_rgb(fill: str, pal: dict[str, tuple[int, int, int]]) -> tuple[int, int, int]:
    """A cor de um `TextLayer.fill` na paleta (o `vice` é o degradê: vale o rosa)."""
    if fill == "white":
        return WHITE
    if fill == "vice":
        return pal["pink"]
    return pal.get(fill) or hex_rgb(DEFAULT_THUMB_PALETTE.get(fill, "#FFD400"))


def _fill_image(size: tuple[int, int], fill: str, emph: bool, pal: dict[str, tuple[int, int, int]]) -> Image.Image:
    if not emph or fill == "white":
        return Image.new("RGBA", size, (*WHITE, 255))
    if fill == "vice":
        return _vertical_gradient(size, (pal["pink"], pal["orange"])).convert("RGBA")
    y = fill_rgb(fill, pal)
    light = tuple(min(255, round(c + (255 - c) * 0.35)) for c in y)
    return _vertical_gradient(size, (light, y, y)).convert("RGBA")  # brilho no topo


def _text(canvas: Image.Image, layers: ThumbLayers, pal: dict[str, tuple[int, int, int]], layout: Layout,
          tpl: dict[str, Any]) -> tuple[float, float, float, float] | None:
    t = layers.text
    tconf = tpl["text"]
    pos = t.pos if t else "left"
    box = tconf.get(pos) or tconf["left"]
    if tconf.get("solo") and not layers.cards:
        # número sem cartões ("7 DIAS"): a coluna cresce para o espaço dos cartões
        box = tconf.get("solo_right", tconf["solo"]) if pos == "right" else tconf["solo"]
        tconf = {**tconf, "sub": tconf.get("solo_sub", tconf.get("sub", 0.62))}
    if t is None or not t.words.strip():
        return box
    W, H = canvas.size
    lines = _split_lines(t.words.upper(), layers.template, pos, tconf)
    emph = _emphasis(lines)
    bx, by, bw, bh = box[0] * W, box[1] * H, box[2] * W, box[3] * H
    size_frac = t.size if t.size > 0 else tconf["size"]
    ref = 200
    f_ref = load_font("Anton", ref)
    dims = []
    for line in lines:
        x0, y0, x1, y1 = _ink_box(f_ref, line)
        dims.append((max(1, x1 - x0), max(1, y1 - y0)))
    sizes: list[float] = []
    for i, (lw, lh) in enumerate(dims):
        cap = size_frac * H * (1.0 if i == emph else tconf.get("sub", 0.62))
        sizes.append(min(ref * bw * 0.94 / lw, ref * cap / lh))
    if tconf.get("number") and len(lines) > 1:
        # número gigante: as outras linhas ficam na largura dele (coluna)
        # Dígito estreito ("1", "1ª"): a coluna tem a largura da caixa como piso, senão a linha de baixo some no feed.
        emph_w = max(sizes[emph] * dims[emph][0] / ref, bw * 0.94)
        sizes = [s if i == emph else min(s, ref * emph_w / dims[i][0]) for i, s in enumerate(sizes)]
    gap_k = 0.10
    for _ in range(4):  # a altura da tinta não escala exatamente com o corpo: confere de novo depois de reduzir
        heights = []
        for line, s in zip(lines, sizes, strict=True):
            bb = _ink_box(load_font("Anton", max(8, round(s))), line)
            heights.append(bb[3] - bb[1])
        total = sum(heights) + gap_k * sum(sizes[:-1]) * 0.5  # o respiro vem depois de cada linha, menos a última
        room = bh - 2 * _stroke_w(max(sizes), H)  # o texto começa um contorno abaixo do topo e o contorno desce junto
        if total <= room or room <= 0:
            break
        k = room / total * 0.99
        sizes = [s * k for s in sizes]

    # Máscaras: preenchimento e contorno, linha a linha
    fill_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    fill_mask_all = Image.new("L", (W, H), 0)
    stroke_mask = Image.new("L", (W, H), 0)
    stroke_max = _stroke_w(max(sizes), H)
    y = by + stroke_max
    x_left = bx + stroke_max
    rows: list[tuple[str, ImageFont.FreeTypeFont, int, int, tuple[int, int, int, int], int]] = []
    for line, s in zip(lines, sizes, strict=True):
        size_px = max(8, round(s))
        font = load_font("Anton", size_px)
        bb = _ink_box(font, line)
        lw, lh = bb[2] - bb[0], bb[3] - bb[1]
        if pos == "center" or tconf.get("box"):
            x = round(bx + (bw - lw) / 2 - bb[0])
        else:
            x = round(x_left - bb[0]) if pos != "right" else round(bx + bw - stroke_max - bb[2])
        yy = round(y - bb[1])
        rows.append((line, font, x, yy, bb, size_px))
        y += lh + gap_k * size_px * 0.5
    for i, (line, font, x, yy, bb, size_px) in enumerate(rows):
        sw = _stroke_w(size_px, H)
        ImageDraw.Draw(stroke_mask).text((x, yy), line, font=font, fill=255, stroke_width=sw, stroke_fill=255)
        m = Image.new("L", (W, H), 0)
        ImageDraw.Draw(m).text((x, yy), line, font=font, fill=255)
        ink = (x + bb[0], yy + bb[1], x + bb[2], yy + bb[3])
        fill_img = _fill_image((max(1, ink[2] - ink[0]), max(1, ink[3] - ink[1])), t.fill, i == emph, pal)
        tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        tile.paste(fill_img, (ink[0], ink[1]))
        fill_layer = Image.composite(tile, fill_layer, m)
        fill_mask_all = ImageChops.lighter(fill_mask_all, m)
        layout.text_lines.append({"text": line, "size": size_px, "bbox": [ink[0] - sw, ink[1] - sw, ink[2] + sw,
                                                                          ink[3] + sw], "emph": i == emph})
    big = max(r[5] for r in rows)
    ink = pal["ink"]
    # o fundo atrás do miolo das letras (sem o traço): é contra ele que a cor do texto precisa de contraste
    behind = fill_mask_all.point(lambda v: 255 if v > 128 else 0)
    if not behind.getbbox():
        behind = stroke_mask.point(lambda v: 255 if v > 128 else 0)
    if behind.getbbox():
        layout.text_bg = tuple(ImageStat.Stat(canvas.convert("RGB"), mask=behind).mean[:3])  # type: ignore[assignment]
    # halo escuro (legibilidade em qualquer fundo) + sombra dura para baixo + contorno + preenchimento
    halo = stroke_mask.filter(ImageFilter.GaussianBlur(min(max(2, big * 0.06), _px(16, H)))).point(
        lambda v: v * 55 // 100)
    hl = Image.new("RGBA", (W, H), (0, 0, 0, 255))
    hl.putalpha(halo)
    canvas.alpha_composite(hl)
    shadow = _shift(stroke_mask, 0, min(max(_px(4, H), round(big * 0.04)), _px(9, H)))
    sl = Image.new("RGBA", (W, H), (*ink, 255))
    sl.putalpha(shadow)
    canvas.alpha_composite(sl)
    st = Image.new("RGBA", (W, H), (*ink, 255))
    st.putalpha(stroke_mask)
    canvas.alpha_composite(st)
    fl = fill_layer.copy()
    fl.putalpha(ImageChops.multiply(fill_layer.getchannel("A"), fill_mask_all))
    canvas.alpha_composite(fl)
    layout.text_bbox = stroke_mask.getbbox()
    layout.text_mask = fill_mask_all
    em_fill = fill_rgb(t.fill, pal)
    layout.text_fill = em_fill if len(lines) == 1 or t.fill != "white" else WHITE
    return box


def _rounded_label(text: str, font: ImageFont.FreeTypeFont, pad: tuple[int, int], fill: tuple[int, int, int],
                   color: tuple[int, int, int], border: tuple[int, int, int], border_w: int) -> Image.Image:
    bb = font.getbbox(text)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    w, h = tw + 2 * pad[0], th + 2 * pad[1]
    im = Image.new("RGBA", (w + 2 * border_w, h + 2 * border_w), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    r = (h + 2 * border_w) // 2
    d.rounded_rectangle((0, 0, im.width - 1, im.height - 1), radius=r, fill=(*border, 255))
    d.rounded_rectangle((border_w, border_w, im.width - 1 - border_w, im.height - 1 - border_w),
                        radius=max(1, r - border_w), fill=(*fill, 255))
    d.text((border_w + pad[0] - bb[0], border_w + pad[1] - bb[1]), text, font=font, fill=(*color, 255))
    return im


def _pill(canvas: Image.Image, layers: ThumbLayers, pal: dict[str, tuple[int, int, int]], layout: Layout,
          box: tuple[float, float, float, float]) -> None:
    if not layers.pill:
        return
    W, H = canvas.size
    vertical = H > W
    size = round(H * (0.030 if vertical else 0.056))
    font = load_font("Montserrat", size, weight=800)
    label = _rounded_label(layers.pill.upper(), font, (round(size * 0.55), round(size * 0.36)), RED, WHITE,
                           WHITE, _px(4, H))
    label = label.rotate(3, resample=Image.Resampling.BICUBIC, expand=True)
    if layout.text_bbox:
        x = layout.text_bbox[0] + _px(4, H)
        if layers.template == "livre" or (layers.text and layers.text.pos == "center"):
            x = round((layout.text_bbox[0] + layout.text_bbox[2] - label.width) / 2)
        y = layout.text_bbox[3] + _px(10, H)
    else:
        x, y = round(box[0] * W), round(box[1] * H)
    limit = H * (0.70 if vertical else 0.94) - label.height
    y = min(y, round(limit))
    tb = layout.text_bbox
    if tb and x < tb[2] and x + label.width > tb[0] and y < tb[3] and y + label.height > tb[1]:
        # Texto alto (número gigante) empurra a pílula para cima dele: tenta acima; sem espaço, omite.
        above = tb[1] - label.height - _px(10, H)
        if above >= round(H * 0.03):
            y = above
        else:
            layout.warnings.append("pílula omitida: cobriria o texto")
            return
    sh = label.getchannel("A").filter(ImageFilter.GaussianBlur(_px(6, H))).point(lambda v: v // 2)
    shl = Image.new("RGBA", label.size, (*pal["ink"], 255))
    shl.putalpha(sh)
    canvas.alpha_composite(_clip_paste(shl, canvas.size, x, y + _px(5, H)), (0, 0))
    canvas.alpha_composite(_clip_paste(label, canvas.size, x, y), (0, 0))
    layout.pill_bbox = (x, y, x + label.width, y + label.height)


def _strike(canvas: Image.Image, layers: ThumbLayers, pal: dict[str, tuple[int, int, int]], layout: Layout) -> None:
    if not layers.strike:
        return
    target = layout.pill_bbox if layers.pill and layout.pill_bbox else layout.text_bbox
    if not target:
        return
    W, H = canvas.size
    x0, y0, x1, y1 = target
    th = max(_px(12, H), round((y1 - y0) * 0.16))
    ss = 2
    layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    pad = (x1 - x0) * 0.04
    p0 = ((x0 - pad) * ss, (y1 - (y1 - y0) * 0.30) * ss)
    p1 = ((x1 + pad) * ss, (y0 + (y1 - y0) * 0.30) * ss)
    d.line([p0, p1], fill=(*pal["ink"], 255), width=(th + _px(8, H)) * ss)
    d.line([p0, p1], fill=(*RED, 255), width=th * ss)
    canvas.alpha_composite(layer.resize((W, H), Image.Resampling.LANCZOS))


def _cards(canvas: Image.Image, layers: ThumbLayers, resolver: AssetResolver | None,
           pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any]) -> None:
    conf = tpl.get("cards")
    if not conf or not layers.cards:
        return
    W, H = canvas.size
    n = len(layers.cards)
    ch = round(conf["h"] * H)
    cw = round(conf["w"] * W)
    span = conf["y1"] * H - conf["y0"] * H
    gap = (span - n * ch) / max(1, n) if n > 1 else 0
    gap = min(gap, ch * 0.45)
    y = conf["y0"] * H + (span - (n * ch + (n - 1) * gap)) / 2
    font_size = max(_px(34, H), round(ch * 0.36))
    border = _px(5, H)
    for k, card in enumerate(layers.cards):
        x = round((1 - conf["x"] - conf["w"] if _mirrored(layers) else conf["x"]) * W)
        im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        r = round(ch * 0.18)
        d.rounded_rectangle((0, 0, cw - 1, ch - 1), radius=r, fill=(*pal["ink"], 255))
        d.rounded_rectangle((border, border, cw - 1 - border, ch - 1 - border), radius=max(1, r - border),
                            fill=(*WHITE, 255))
        inner = ch - 2 * border
        icon_w = inner
        asset = _resolve(resolver, card.asset, layout, "cartão") if card.asset else None
        if asset is not None:
            thumb = ImageOps.fit(open_rgba(asset.path).convert("RGB"), (icon_w - border, inner - border),
                                 Image.Resampling.LANCZOS)
            mask = Image.new("L", thumb.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, thumb.width - 1, thumb.height - 1), radius=max(1, r - border),
                                                   fill=255)
            im.paste(thumb, (border + border // 2, border + border // 2), mask)
        else:
            # selo com o número do item
            d.rounded_rectangle((border, border, border + icon_w, ch - 1 - border), radius=max(1, r - border),
                                fill=(*pal["yellow"], 255))
            nf = load_font("Anton", round(inner * 0.78))
            num = str(k + 1)
            bb = nf.getbbox(num)
            d.text((border + (icon_w - (bb[2] - bb[0])) / 2 - bb[0], border + (inner - (bb[3] - bb[1])) / 2 - bb[1]),
                   num, font=nf, fill=(*pal["ink"], 255))
        label = card.label.upper().strip()
        if label:
            avail = cw - icon_w - 3 * border - round(ch * 0.15)
            size = font_size
            font = load_font("Montserrat", size, weight=850)
            while size > _px(20, H) and font.getlength(label) > avail:
                size = round(size * 0.92)
                font = load_font("Montserrat", size, weight=850)
            bb = font.getbbox(label)
            tx = border + icon_w + round(ch * 0.12)
            d.text((tx - bb[0], (ch - (bb[3] - bb[1])) / 2 - bb[1]), label, font=font, fill=(*pal["ink"], 255))
        im = im.rotate(-2 if k % 2 == 0 else 2, resample=Image.Resampling.BICUBIC, expand=True)
        sh = im.getchannel("A").filter(ImageFilter.GaussianBlur(_px(8, H))).point(lambda v: v * 6 // 10)
        shl = Image.new("RGBA", im.size, (*pal["ink"], 255))
        shl.putalpha(sh)
        yy = round(y)
        canvas.alpha_composite(_clip_paste(shl, canvas.size, x, yy + _px(7, H)), (0, 0))
        canvas.alpha_composite(_clip_paste(im, canvas.size, x, yy), (0, 0))
        layout.cards_bbox.append((x, yy, x + im.width, yy + im.height))
        y += ch + gap
    layout.objects += 1


def _arrow(canvas: Image.Image, layers: ThumbLayers, pal: dict[str, tuple[int, int, int]]) -> None:
    a = layers.arrow
    if a is None:
        return
    W, H = canvas.size
    ss = 2
    color = pal.get(a.color) or hex_rgb(a.color, pal["yellow"])
    x0, y0, x1, y1 = a.from_x * W * ss, a.from_y * H * ss, a.to_x * W * ss, a.to_y * H * ss
    ang = math.atan2(y1 - y0, x1 - x0)
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 4:
        return
    shaft = _px(16, H) * ss
    head_l = min(length * 0.5, _px(60, H) * ss)
    head_w = _px(70, H) * ss
    ux, uy = math.cos(ang), math.sin(ang)
    nx, ny = -uy, ux
    bx, by = x1 - ux * head_l, y1 - uy * head_l
    poly = [
        (x0 + nx * shaft / 2, y0 + ny * shaft / 2), (bx + nx * shaft / 2, by + ny * shaft / 2),
        (bx + nx * head_w / 2, by + ny * head_w / 2), (x1, y1),
        (bx - nx * head_w / 2, by - ny * head_w / 2), (bx - nx * shaft / 2, by - ny * shaft / 2),
        (x0 - nx * shaft / 2, y0 - ny * shaft / 2),
    ]
    layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.polygon(poly, fill=(*pal["ink"], 255))
    d.line([*poly, poly[0]], fill=(*pal["ink"], 255), width=_px(12, H) * ss, joint="curve")
    d.polygon(poly, fill=(*color, 255))
    canvas.alpha_composite(layer.resize((W, H), Image.Resampling.LANCZOS))


def _logo(canvas: Image.Image, layers: ThumbLayers, resolver: AssetResolver | None,
          pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any], logo_asset: str | None) -> None:
    conf = tpl.get("logo", {})
    anchor = conf.get("anchor", "top-right")
    if _mirrored(layers):
        anchor = {"top-right": "top-left", "top-left": "top-right"}.get(anchor, anchor)
        if anchor == "top-mid":
            conf = {**conf, "x": round(1 - conf.get("x", 0.4) - conf["w"], 4)}
    if layers.logo == "none" or anchor == "none" or resolver is None:
        return
    W, H = canvas.size
    if anchor == "versus":
        for side, tag in (("left", "gta"), ("right", "vi")):
            item = next((i for i in resolver.items("logo") if tag in (i.get("tags") or [])), None)
            if item:
                _paste_logo(canvas, resolver, f"logo:{item['id']}", conf["w"] * (0.8 if tag == "vi" else 1.0),
                            side, pal, layout, y=0.075)
        return
    logo_id = resolver.logo_id(logo_asset)
    if not logo_id:
        return
    if layout.big_vi or {"vi", "logo"} & set(layout.bg_tags):
        layout.warnings.append("logo omitido: a marca já aparece grande na thumb (VI gigante ou fundo com logo)")
        return
    if anchor == "top-mid":
        _paste_logo(canvas, resolver, f"logo:{logo_id}", conf["w"], "x", pal, layout, x=conf.get("x", 0.4), y=0.03)
    elif anchor == "short-top":
        _paste_logo(canvas, resolver, f"logo:{logo_id}", conf["w"], "right", pal, layout, y=tpl.get("band", 0.15) + 0.008)
    else:
        side = "left" if anchor == "top-left" else "right"
        _paste_logo(canvas, resolver, f"logo:{logo_id}", conf["w"], side, pal, layout, y=0.07)


def _paste_logo(canvas: Image.Image, resolver: AssetResolver, ref: str, w_frac: float, side: str,
                pal: dict[str, tuple[int, int, int]], layout: Layout, *, y: float, x: float | None = None) -> None:
    asset = _resolve(resolver, ref, layout, "logo")
    if asset is None:
        return
    W, H = canvas.size
    logo = _trim(open_rgba(asset.path))
    lw = round(W * w_frac)
    lh = round(logo.height * lw / logo.width)
    logo = logo.resize((lw, lh), Image.Resampling.LANCZOS)
    margin = round(W * 0.025)
    if side == "left":
        px = margin
    elif side == "center":
        px = (W - lw) // 2
    elif side == "x" and x is not None:
        px = round(W * x)
    else:
        px = W - lw - margin
    py = round(H * y)
    wanted = px
    px = _logo_spot(layout, (W, H), px, py, lw, lh, margin, side)
    if px is None:
        layout.warnings.append("logo omitido: cobriria o rosto ou o texto")
        return
    if (side == "x" or px != wanted) and _logo_gap(layout, (W, H), px, py, lw, lh) < 2 * lw + 2 * margin:
        layout.warnings.append("logo omitido: ficaria espremido entre o texto e a figura")
        return
    glow = logo.getchannel("A").filter(ImageFilter.GaussianBlur(_px(10, H))).point(lambda v: v * 6 // 10)
    gl = Image.new("RGBA", logo.size, (*pal["ink"], 255))
    gl.putalpha(glow)
    canvas.alpha_composite(_clip_paste(gl, canvas.size, px, py + _px(3, H)), (0, 0))
    canvas.alpha_composite(_clip_paste(logo, canvas.size, px, py), (0, 0))
    layout.figures.append({"kind": "logo", "bbox": [px, py, px + lw, py + lh]})


def _logo_gap(layout: Layout, size: tuple[int, int], px: int, py: int, lw: int, lh: int) -> float:
    """Espaço livre na horizontal, na faixa do logo, entre o que está à esquerda dele (texto, pílula, cartões,
    figuras) e o que está à direita: logo no meio do miolo só fica se tiver folga (a thumb com menos coisa)."""
    W, _H = size
    cx = px + lw / 2
    left, right = 0.0, float(W)
    boxes = [b for b in (layout.text_bbox, layout.pill_bbox, *layout.cards_bbox) if b]
    boxes += [f["bbox"] for f in layout.figures if f.get("kind") in ("cutout", "presenter") and f.get("bbox")]
    for b in boxes:
        if b[3] <= py or b[1] >= py + lh:
            continue
        if b[2] <= cx:
            left = max(left, float(b[2]))
        elif b[0] >= cx:
            right = min(right, float(b[0]))
    return right - left


def _logo_obstacles(layout: Layout) -> list[tuple[float, float, float, float]]:
    """O que o logo não pode cobrir: texto, pílula, cartões e os rostos (com folga para cabelo e orelhas)."""
    out: list[tuple[float, float, float, float]] = []
    for box in (layout.text_bbox, layout.pill_bbox, *layout.cards_bbox):
        if box:
            out.append(tuple(box))  # type: ignore[arg-type]
    for f in layout.figures:
        if f.get("kind") in ("card", "object") and f.get("bbox"):
            out.append(tuple(f["bbox"]))  # type: ignore[arg-type]  # a prova e o objeto em close não ficam embaixo do logo
        fb = f.get("face_box")
        if fb and f.get("kind") in ("presenter", "cutout", "frame"):
            pad_x, pad_y = (fb[2] - fb[0]) * 0.25, (fb[3] - fb[1]) * 0.15
            out.append((fb[0] - pad_x, fb[1] - pad_y, fb[2] + pad_x, fb[3] + pad_y))
    return out


def _logo_spot(layout: Layout, size: tuple[int, int], px: int, py: int, lw: int, lh: int, margin: int,
               side: str) -> int | None:
    """O x do logo sem cobrir texto nem rosto: o lugar do template; senão encostado do lado de dentro do que ele
    cobriria; senão None (fica sem logo)."""
    W, _H = size
    obstacles = _logo_obstacles(layout)

    def hits(x: float) -> list[tuple[float, float, float, float]]:
        return [o for o in obstacles if x < o[2] and x + lw > o[0] and py < o[3] and py + lh > o[1]]

    blocked = hits(px)
    if not blocked:
        return px
    tries = [round(min(o[0] for o in blocked) - lw - margin), round(max(o[2] for o in blocked) + margin)]
    if side == "left":
        tries.reverse()
    for x in tries:
        if margin <= x <= W - lw - margin and not hits(x):
            return x
    return None


def _versus(canvas: Image.Image, layers: ThumbLayers, resolver: AssetResolver | None,
            pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any]) -> Image.Image:
    """Fundo do versus: esquerda (cartão 0 ou o próprio fundo) em sépia, direita saturada, corte diagonal."""
    W, H = canvas.size
    right = _background(layers, (W, H), resolver, pal, layout, tpl, "bg")
    left_ref = layers.cards[0].asset if layers.cards and layers.cards[0].asset else layers.background.ref
    left = _background(layers, (W, H), resolver, pal, layout, tpl, "left_bg", ref=left_ref)
    gray = ImageOps.grayscale(left.convert("RGB"))
    sepia = ImageOps.colorize(gray, black=(40, 24, 14), white=(250, 228, 190), mid=(150, 110, 70)).convert("RGBA")
    top_x, bot_x = tpl.get("split", (0.56, 0.44))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).polygon([(0, 0), (top_x * W, 0), (bot_x * W, H), (0, H)], fill=255)
    out = Image.composite(sepia, right, mask)
    ss = 2
    line = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(line)
    d.line([(top_x * W * ss, 0), (bot_x * W * ss, H * ss)], fill=(*pal["ink"], 255), width=_px(22, H) * ss)
    d.line([(top_x * W * ss, 0), (bot_x * W * ss, H * ss)], fill=(*WHITE, 255), width=_px(12, H) * ss)
    out.alpha_composite(line.resize((W, H), Image.Resampling.LANCZOS))
    # selo VS no meio do corte
    r = _px(64, H)
    cx, cy = (top_x + bot_x) / 2 * W, H * 0.55
    badge = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(badge)
    bd.ellipse((cx - r - _px(6, H), cy - r - _px(6, H), cx + r + _px(6, H), cy + r + _px(6, H)), fill=(*pal["ink"], 255))
    bd.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(*pal["yellow"], 255))
    font = load_font("Anton", round(r * 1.05))
    bb = font.getbbox("VS")
    bd.text((cx - (bb[2] - bb[0]) / 2 - bb[0], cy - (bb[3] - bb[1]) / 2 - bb[1]), "VS", font=font,
            fill=(*pal["ink"], 255))
    out.alpha_composite(badge)
    return out


# --- Prova e objeto: cartão com a mídia do projeto e marcação vermelha ---------------------------------------------
def _card_mode(layers: ThumbLayers, tpl: dict[str, Any]) -> bool:
    """O template desenha a imagem do fundo como cartão (prova/objeto com mídia, upload ou quadro)."""
    bg = layers.background
    return bool(tpl.get("card")) and bg.kind in ("media", "upload", "frame") and bool(bg.ref)


def content_window(im: Image.Image, aspect: float, zoom: float = 1.0) -> tuple[int, int, int, int]:
    """A janela (proporção `aspect`) da imagem com mais "conteúdo": bordas e cor. Numa captura de página (muito
    branco em volta) cai no título e na foto da matéria; com `zoom` > 1, num trecho menor (o close do objeto).
    Procura em 3 tamanhos (do `zoom` pedido a 1,7× ele) e prefere a janela maior quando empata."""
    import numpy as np

    rgb = im.convert("RGB")
    w, h = rgb.size
    sw = 192
    sh = max(8, round(sw * h / max(1, w)))
    small = rgb.resize((sw, sh), Image.Resampling.BOX)
    edges = np.asarray(small.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32)
    sat = np.asarray(small.convert("HSV").getchannel("S"), dtype=np.float32)
    energy = edges + 0.35 * sat
    integral = np.pad(energy.cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    if sw / sh > aspect:
        base_h, base_w = sh, sh * aspect
    else:
        base_w, base_h = sw, sw / aspect
    best: tuple[float, tuple[int, int, int, int]] = (-1.0, (0, 0, w, h))
    for k in (1.0, 1.3, 1.7):
        z = max(1.0, zoom) * k
        ww, hh = max(4, int(base_w / z)), max(4, int(base_h / z))
        if ww > sw or hh > sh:
            continue
        for y0 in range(0, sh - hh + 1, max(1, (sh - hh) // 12 or 1)):
            for x0 in range(0, sw - ww + 1, max(1, (sw - ww) // 12 or 1)):
                total = (integral[y0 + hh, x0 + ww] - integral[y0, x0 + ww] - integral[y0 + hh, x0]
                         + integral[y0, x0])
                score = float(total) / (ww * hh) * (1 / k) ** 0.35
                if score > best[0]:
                    fx, fy = w / sw, h / sh
                    best = (score, (round(x0 * fx), round(y0 * fy), round((x0 + ww) * fx), round((y0 + hh) * fy)))
    return best[1]


def _colorful(im: Image.Image) -> bool:
    """Quadro colorido (trailer, foto) e não página clara: vira o fundo desfocado do cartão."""
    small = im.convert("RGB").resize((64, 36), Image.Resampling.BOX)
    sat = ImageStat.Stat(small.convert("HSV").getchannel("S")).mean[0] / 255
    return sat > 0.22 and _luma(small) < 0.85


def _ink(im: Image.Image, width: int = 480, *, color: bool = True) -> Any:
    """Máscara (numpy bool, numa cópia de `width` px) do que é "tinta" numa página: texto escuro (e cor forte, com
    `color`)."""
    import numpy as np

    w, h = im.size
    small = im.convert("RGB").resize((width, max(4, round(width * h / max(1, w)))), Image.Resampling.BOX)
    luma = np.asarray(small.convert("L"), dtype=np.float32)
    if not color:
        return luma < 110
    sat = np.asarray(small.convert("HSV").getchannel("S"), dtype=np.float32)
    return (luma < 120) | (sat > 110)


def page_window(im: Image.Image, aspect: float) -> tuple[int, int, int, int]:
    """A janela (proporção `aspect`) de uma página capturada: com a manchete achada (`headline_band`), da largura
    dela inteira (nunca cortada no meio da palavra), com a manchete no alto e o que vem embaixo (a foto da
    matéria) no resto. Sem manchete: a largura da coluna de texto e, na vertical, onde há mais tinta."""
    import numpy as np

    w, h = im.size
    band = headline_band(im)
    if band is not None:
        bx0, by0, bx1, by1 = band[0] * w, band[1] * h, band[2] * w, band[3] * h
        ww = (bx1 - bx0) * 1.1
        hh = ww / aspect
        if hh <= h and ww <= w:
            x0 = min(max(0.0, (bx0 + bx1) / 2 - ww / 2), w - ww)
            y0 = min(max(0.0, by0 - (hh - (by1 - by0)) * 0.08), h - hh)
            return round(x0), round(y0), round(x0 + ww), round(y0 + hh)
    ink = _ink(im)
    sh, sw = ink.shape
    cols = np.where(ink.sum(axis=0) > max(1, sh * 0.004))[0]
    if len(cols) < 4:
        return content_window(im, aspect)
    weights = ink.sum(axis=0)[cols].astype(np.float64)
    cum = np.cumsum(weights) / weights.sum()
    # 3% de cada lado fora: o selo/aba lateral do site ("Parceiro") não alarga a janela
    x0, x1 = int(cols[np.searchsorted(cum, 0.03)]), int(cols[min(len(cols) - 1, np.searchsorted(cum, 0.97))])
    pad = max(2, round((x1 - x0) * 0.04))
    x0, x1 = max(0, x0 - pad), min(sw, x1 + pad + 1)
    ww, hh = x1 - x0, (x1 - x0) / aspect
    if hh > sh:  # coluna larga numa página baixa: a altura toda, largura pela proporção (centrada na coluna)
        hh, ww = sh, sh * aspect
        cx = (x0 + x1) / 2
        x0 = int(min(max(0, cx - ww / 2), sw - ww))
        x1 = int(x0 + ww)
    rows = np.pad(np.cumsum(ink[:, x0:x1].sum(axis=1)), (1, 0))
    hh_i = max(1, min(sh, round(hh)))
    best = max(range(0, sh - hh_i + 1), key=lambda y: rows[y + hh_i] - rows[y]) if sh > hh_i else 0
    fx, fy = w / sw, h / sh
    return (round(x0 * fx), round(best * fy), round(x1 * fx), round(min(h, (best + hh_i) * fy)))


def headline_band(photo: Image.Image, width: int = 480) -> tuple[float, float, float, float] | None:
    """Onde está a manchete numa página (fração x0, y0, x1, y1): o bloco de linhas de texto escuro com a letra mais
    alta (as linhas de uma manchete de 2 linhas juntas). Fotos e imagens da página (manchas cheias, sem o fundo claro
    entre as letras) saem da conta antes: só sobra a tinta de texto. None quando não há texto que se destaque."""
    import numpy as np

    w, h = photo.size
    small = photo.convert("RGB").resize((width, max(4, round(width * h / max(1, w)))), Image.Resampling.BOX)
    gray = small.convert("L")
    solid = np.asarray(gray, dtype=np.float32) < 225
    # bloco de 8 px (a 480 de largura) quase todo sem fundo claro, em mancha grande, é foto; letra, mesmo grossa,
    # deixa fundo claro em volta (e um bloco cheio solto no cruzamento de traços some na erosão). Os blocos vizinhos
    # também saem (as letras brancas de um logo dentro da foto não viram "texto")
    bs = 8
    gh, gw = -(-solid.shape[0] // bs), -(-solid.shape[1] // bs)
    padded = np.zeros((gh * bs, gw * bs), dtype=np.float32)
    padded[:solid.shape[0], :solid.shape[1]] = solid
    blocks = Image.fromarray(((padded.reshape(gh, bs, gw, bs).mean(axis=(1, 3)) > 0.9) * 255).astype(np.uint8))
    blocks = np.asarray(blocks.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(7))) > 0
    photo_mask = np.kron(blocks, np.ones((bs, bs), dtype=bool))[:solid.shape[0], :solid.shape[1]]
    ink = (np.asarray(gray, dtype=np.float32) < 110) & ~photo_mask
    sh, sw = ink.shape
    density = ink.mean(axis=1)
    rows = density > 0.01
    # cada linha de texto é um trecho de linhas com tinta (sem pular vão: parágrafos não viram um bloco só)
    runs: list[list[int]] = []
    y = 0
    while y < sh:
        if rows[y]:
            y0 = y
            while y < sh and rows[y]:
                y += 1
            runs.append([y0, y])
        y += 1
    # linha de texto tem altura de linha (até 22% da imagem: mais que isso é pedaço de foto) e fundo claro em volta
    light = np.asarray(gray, dtype=np.float32) > 225

    gap = max(2, round(sw * 0.04))

    def main_block(y0: int, y1: int) -> tuple[int, int] | None:
        """As colunas do bloco de palavras com mais tinta nas linhas y0..y1 (vão maior que 4% da largura separa
        outra coisa: o selo lateral do site, os botões do cabeçalho)."""
        counts = ink[y0:y1].sum(axis=0).astype(np.float64)
        cols = np.where(counts > 0)[0]
        if not len(cols):
            return None
        blocks: list[list[int]] = [[int(cols[0]), int(cols[0])]]
        for c in cols[1:]:
            if c - blocks[-1][1] > gap:
                blocks.append([int(c), int(c)])
            else:
                blocks[-1][1] = int(c)
        c0, c1 = max(blocks, key=lambda bl: counts[bl[0]:bl[1] + 1].sum())
        return c0, c1 + 1

    def texty(r: list[int]) -> bool:
        """Linha de manchete: larga (um quarto da página ou mais: o logo do site no cabeçalho não conta) e com
        fundo claro em volta das letras (texto de dentro de uma foto não conta)."""
        blk = main_block(r[0], r[1])
        if blk is None or blk[1] - blk[0] < sw * 0.25:
            return False
        return float(light[r[0]:r[1], blk[0]:blk[1]].mean()) > 0.45

    runs = [r for r in runs if max(3, sh * 0.015) <= r[1] - r[0] <= sh * 0.22 and texty(r)]
    if not runs:
        return None
    # a manchete é a 1ª linha (de cima para baixo) com letra quase do tamanho da maior: texto grande mais embaixo
    # costuma ser de dentro da foto (o logo do jogo) ou de outra matéria
    tall = max(r[1] - r[0] for r in runs)
    first = next(r for r in runs if r[1] - r[0] >= tall * 0.7)
    group = [first]
    for r in runs[runs.index(first) + 1:]:  # as outras linhas da manchete (quase a mesma altura, logo embaixo)
        if r[1] - r[0] >= tall * 0.5 and r[0] - group[-1][1] <= tall:
            group.append(r)
        else:
            break
    y0, y1 = group[0][0], group[-1][1]
    blk = main_block(y0, y1)
    if blk is None:
        return None
    return blk[0] / sw, y0 / sh, blk[1] / sw, y1 / sh


def _card_scene(layers: ThumbLayers, size: tuple[int, int], resolver: AssetResolver | None,
                pal: dict[str, tuple[int, int, int]], layout: Layout, tpl: dict[str, Any],
                ) -> tuple[Image.Image, tuple[int, int, int, int] | None]:
    """Fundo e cartão da prova/objeto. Fundo: o quadro do jogo de `layers.backdrop` escurecido; senão a própria
    imagem desfocada (quando colorida); senão o degradê do template. O cartão mostra a janela da imagem: numa
    página capturada, a coluna de texto inteira (`page_window`: a manchete não sai cortada); numa foto, a janela
    com mais conteúdo (`content_window`). Borda branca, sombra e a inclinação do template. A manchete achada
    (`headline_band`) vai para o item do cartão em `layout.figures` (a marcação fica fora dela). Devolve o canvas e
    a caixa do cartão (None quando a imagem não abriu)."""
    W, H = size
    conf = tpl["card"]
    bg = layers.background
    canvas = None
    if layers.backdrop:
        back = _resolve(resolver, layers.backdrop, layout, "fundo da prova")
        if back is not None:
            src_b, _ = _strip_letterbox(open_rgba(back.path).convert("RGB"))
            canvas, _ = _cover(src_b, size, 1.0, (0.5, 0.45), (0.5, 0.45))
            canvas = _grade(canvas, 3, 0.42, H)
    asset = _resolve(resolver, bg.ref, layout, "prova")
    if asset is None:
        return (canvas if canvas is not None else _gradient_bg(tpl.get("backdrop", "vice"), size, pal)), None
    raw = open_rgba(asset.path).convert("RGB")
    src, _ = _strip_letterbox(raw)
    colorful = _colorful(src)
    if canvas is None and colorful:
        canvas, _ = _cover(src, size, 1.0, (0.5, 0.5), (0.5, 0.5))
        canvas = _grade(canvas, 22, 0.38, H)
    if canvas is None:
        canvas = _gradient_bg(tpl.get("backdrop", "vice"), size, pal)
    bx, by, bw, bh = conf["box"]
    if _mirrored(layers):
        bx = 1 - bx - bw
    cw, ch = round(bw * W), round(bh * H)
    border = _px(conf.get("border", 7), H)
    inner = (max(8, cw - 2 * border), max(8, ch - 2 * border))
    zoom = max(float(conf.get("zoom", 1.0)), bg.zoom)
    page = not colorful
    win = page_window(src, inner[0] / inner[1]) if page else content_window(src, inner[0] / inner[1], zoom)
    photo = src.resize(inner, Image.Resampling.LANCZOS, box=win)
    band = headline_band(photo) if page else None
    photo = ImageEnhance.Contrast(ImageEnhance.Color(photo).enhance(1.1)).enhance(1.05)
    card = Image.new("RGBA", (cw, ch), (*WHITE, 255))
    card.paste(photo, (border, border))
    tilt = float(conf.get("tilt", 0.0))
    if tilt:
        card = card.rotate(tilt, resample=Image.Resampling.BICUBIC, expand=True)
    x = round(bx * W + (cw - card.width) / 2)
    y = round(by * H + (ch - card.height) / 2)
    sh = card.getchannel("A").filter(ImageFilter.GaussianBlur(_px(14, H))).point(lambda v: v * 7 // 10)
    shl = Image.new("RGBA", card.size, (*pal["ink"], 255))
    shl.putalpha(sh)
    canvas.alpha_composite(_clip_paste(shl, size, x + _px(6, H), y + _px(12, H)), (0, 0))
    canvas.alpha_composite(_clip_paste(card, size, x, y), (0, 0))
    box = (max(0, x), max(0, y), min(W, x + card.width), min(H, y + card.height))
    item: dict[str, Any] = {"kind": "card", "asset": bg.ref, "bbox": list(box)}
    if band is not None:
        # a manchete no canvas (sem a inclinação: ±2,5° mexe poucos px)
        ox, oy = bx * W + border, by * H + border
        item["headline"] = [round(ox + band[0] * inner[0]), round(oy + band[1] * inner[1]),
                            round(ox + band[2] * inner[0]), round(oy + band[3] * inner[1])]
    layout.figures.append(item)
    layout.objects += 1
    return canvas, box


def _mark(canvas: Image.Image, kind: str | None, box: tuple[int, int, int, int] | Sequence[int],
          pal: dict[str, tuple[int, int, int]], headline: Sequence[int] | None = None) -> None:
    """Marcação vermelha (com contorno escuro) sobre a prova. Com a manchete conhecida (`headline`), nunca em cima
    dela: o X vai no pedaço do cartão embaixo da manchete (a foto da matéria; sem espaço ali, no de cima), o círculo vira um
    retângulo arredondado em volta da manchete (sem cortar palavras). Sem manchete: X de ponta a ponta ou círculo
    em volta do miolo."""
    if kind not in ("x", "circle"):
        return
    W, H = canvas.size
    x0, y0, x1, y1 = box
    if x1 - x0 < 8 or y1 - y0 < 8:
        return
    ss = 2
    layer = Image.new("RGBA", (W * ss, H * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    ink = (*pal["ink"], 255)
    red = (*RED, 255)
    if headline is not None and kind == "circle":
        hx0, hy0, hx1, hy1 = headline
        m = _px(14, H)
        rect = (max(x0 - m, hx0 - m), hy0 - m, min(x1 + m, hx1 + m), hy1 + m)
        th = _px(11, H)
        r = round((rect[3] - rect[1]) * 0.35)
        for width, color in ((th + _px(8, H), ink), (th, red)):
            d.rounded_rectangle(tuple(v * ss for v in rect), radius=r * ss, outline=color, width=width * ss)
        canvas.alpha_composite(layer.resize((W, H), Image.Resampling.LANCZOS))
        return
    if headline is not None:
        hy0, hy1 = headline[1], headline[3]
        above, below = hy0 - y0, y1 - hy1
        m = _px(10, H)
        if max(above, below) >= (y1 - y0) * 0.25:
            # embaixo da manchete costuma estar a foto da matéria: o X vai nela quando cabe
            y0, y1 = (hy1 + m, y1) if below >= (y1 - y0) * 0.25 else (y0, hy0 - m)
            side = min(x1 - x0, y1 - y0)  # um X quadrado no meio do pedaço livre
            cx = (x0 + x1) / 2
            x0, x1 = round(cx - side * 0.7), round(cx + side * 0.7)
    bw, bh = x1 - x0, y1 - y0
    if kind == "x":
        th = _px(22 if headline is not None else 26, H)
        mx, my = bw * 0.1, bh * 0.1
        lines = [((x0 + mx, y0 + my), (x1 - mx, y1 - my)), ((x1 - mx, y0 + my), (x0 + mx, y1 - my))]
        for width, color in ((th + _px(10, H), ink), (th, red)):
            for a, b in lines:
                d.line([(a[0] * ss, a[1] * ss), (b[0] * ss, b[1] * ss)], fill=color, width=width * ss)
                r = width * ss / 2  # pontas arredondadas
                for px, py in (a, b):
                    d.ellipse((px * ss - r, py * ss - r, px * ss + r, py * ss + r), fill=color)
    else:
        th = _px(13, H)
        mx, my = bw * 0.12, bh * 0.16
        ell = (x0 + mx, y0 + my, x1 - mx, y1 - my)
        d.ellipse(tuple(v * ss for v in ell), outline=ink, width=(th + _px(10, H)) * ss)
        inset = _px(5, H)
        d.ellipse(tuple((v + (inset if i < 2 else -inset)) * ss for i, v in enumerate(ell)), outline=red,
                  width=th * ss)
    canvas.alpha_composite(layer.resize((W, H), Image.Resampling.LANCZOS))


# --- Render ------------------------------------------------------------------------------------------------------------
def render(layers: ThumbLayers, size: tuple[int, int] = T.CANVAS, assets: AssetResolver | None = None,
           palette: dict[str, str] | None = None, *, logo_asset: str | None = None) -> Image.Image:
    """Desenha a thumb (RGB). `img.info["layout"]` traz as caixas e os avisos (dict serializável) e
    `img.info["text_mask"]` a máscara do preenchimento do texto (para a checagem de legibilidade)."""
    W, H = int(size[0]), int(size[1])
    pal = _palette(palette)
    tpl = T.spec(layers.template)
    layout = Layout((W, H), layers.template)
    card = None
    if layers.template == "versus":
        canvas = _versus(Image.new("RGBA", (W, H)), layers, assets, pal, layout, tpl)
    elif _card_mode(layers, tpl):
        canvas, card = _card_scene(layers, (W, H), assets, pal, layout, tpl)
    else:
        canvas = _background(layers, (W, H), assets, pal, layout, tpl)
    has_text = bool(layers.text and layers.text.words.strip())
    _shade(canvas, tpl, pal, has_text, _mirrored(layers))
    if card is not None:
        item = next((f for f in layout.figures if f.get("kind") == "card"), {})
        _mark(canvas, layers.mark, card, pal, item.get("headline"))
    tconf = tpl["text"]
    text_box = tconf.get(layers.text.pos if layers.text else "left") or tconf["left"]
    # as figuras vão para uma camada própria antes do VI gigante: ele fica por baixo delas, mas a posição dele
    # depende de qual figura entrou de verdade (e onde ficou depois de ancorada)
    figures = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    placed = _cutouts((W, H), layers, assets, pal, layout)
    avatar = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    _presenter(avatar, layers, assets, pal, layout, tpl)
    has_avatar = avatar.getchannel("A").getbbox() is not None
    for layer, fig in placed:
        if has_avatar and fig is not None and _hidden_fraction(layer, avatar) > HIDDEN_MAX:
            # personagem quase todo atrás do avatar: sobrava um pedaço de camiseta saindo do lado (um foco só)
            layout.figures.remove(fig)
            layout.warnings.append(f"figura {fig.get('asset')}: saiu (ficava escondida atrás do avatar)")
            continue
        figures.alpha_composite(layer)
    _big_vi(canvas, layers, assets, pal, layout, tpl, text_box)
    canvas.alpha_composite(figures)
    canvas.alpha_composite(avatar)
    if card is None and layers.mark and tpl.get("card"):
        # objeto recortado (sem cartão): a marcação vai em volta dele
        target = next((f["bbox"] for f in layout.figures if f.get("kind") in ("object", "cutout")), None)
        if target:
            _mark(canvas, layers.mark, tuple(target), pal)
    if tconf.get("box") and has_text:
        _text_box_backdrop(canvas, layers, pal, tpl)
    _text(canvas, layers, pal, layout, tpl)
    _pill(canvas, layers, pal, layout, text_box)
    _cards(canvas, layers, assets, pal, layout, tpl)
    _strike(canvas, layers, pal, layout)
    _arrow(canvas, layers, pal)
    _logo(canvas, layers, assets, pal, layout, tpl, logo_asset)
    img = canvas.convert("RGB")
    img.info["layout"] = layout.as_dict()
    if layout.text_mask is not None:
        img.info["text_mask"] = layout.text_mask
    return img


def _text_box_backdrop(canvas: Image.Image, layers: ThumbLayers, pal: dict[str, tuple[int, int, int]],
                       tpl: dict[str, Any]) -> None:
    """Short: faixa escura translúcida atrás da caixa de texto (o quadro vertical costuma ser movimentado)."""
    W, H = canvas.size
    box = tpl["text"].get(layers.text.pos if layers.text else "center") or tpl["text"]["center"]
    x0, y0, x1, y1 = box[0] * W, box[1] * H, (box[0] + box[2]) * W, (box[1] + box[3]) * H
    pad = W * 0.03
    band = Image.new("L", (W, H), 0)
    ImageDraw.Draw(band).rounded_rectangle((x0 - pad, y0 - pad, x1 + pad, y1 + pad), radius=round(W * 0.04), fill=120)
    band = band.filter(ImageFilter.GaussianBlur(W * 0.02))
    layer = Image.new("RGBA", (W, H), (*pal["ink"], 255))
    layer.putalpha(band)
    canvas.alpha_composite(layer)


def export(layers: ThumbLayers, out: Path, *, is_long: bool = True, assets: AssetResolver | None = None,
           palette: dict[str, str] | None = None, logo_asset: str | None = None) -> dict[str, Any]:
    """Exporta no tamanho do YouTube (3840×2160 no longo, 1080×1920 no Short), JPG ≤ 2 MB, e a prévia de 1280 px."""
    from studio.media.thumb_variants import (  # import tardio: thumb_variants importa este
        encode_jpg,
        preview_size,
    )

    size = T.EXPORT_LONG if is_long else T.SHORT_CANVAS
    img = render(layers, size, assets, palette, logo_asset=logo_asset)
    size_bytes, quality = encode_jpg(img, out)
    preview = out.with_name(f"{out.stem}-previa.jpg")
    img.resize(preview_size(*img.size), Image.Resampling.LANCZOS).save(preview, "JPEG", quality=90)
    return {"path": out, "preview": preview, "size": f"{img.width}x{img.height}", "bytes": size_bytes,
            "quality": quality, "layout": img.info.get("layout", {})}


def render_set(candidates: Sequence[ThumbLayers], size: tuple[int, int] = T.CANVAS,
               assets: AssetResolver | None = None, palette: dict[str, str] | None = None, *,
               min_distance: int = 10, max_swaps: int = 4, logo_asset: str | None = None,
               ) -> list[tuple[ThumbLayers, Image.Image]]:
    """Renderiza os candidatos garantindo variantes reais (B.7): se um sair parecido demais com um anterior
    (dHash < `min_distance`), troca o fundo pelo próximo quadro da biblioteca com a mesma tag e tenta de novo.
    Devolve as camadas (possivelmente com o fundo trocado) e a imagem de cada um."""
    from studio.media.thumb_check import dhash, hamming

    done: list[tuple[ThumbLayers, Image.Image]] = []
    hashes: list[int] = []
    for layers in candidates:
        current = layers
        img = render(current, size, assets, palette, logo_asset=logo_asset)
        h = dhash(img)
        tried: set[str] = set()
        swaps = 0
        while hashes and min(hamming(h, o) for o in hashes) < min_distance and swaps < max_swaps and assets:
            if current.background.ref:
                tried.add(current.background.ref.split(":", 1)[-1])
            nxt = assets.next_frame(current.background.ref, skip=tried)
            if not nxt:
                break
            item = assets.item(nxt.split(":", 1)[1]) or {}
            bg = current.background.model_copy(update={"kind": "frame", "ref": nxt,
                                                       "focus_x": float(item.get("focus_x", 0.5))})
            current = current.model_copy(update={"background": bg})
            img = render(current, size, assets, palette, logo_asset=logo_asset)
            h = dhash(img)
            swaps += 1
        if swaps:
            img.info.setdefault("layout", {}).setdefault("warnings", []).append(
                f"fundo trocado {swaps}x para não repetir outra variante")
        hashes.append(h)
        done.append((current, img))
    return done
