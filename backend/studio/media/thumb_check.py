"""Checagem de legibilidade das thumbs (seção B.6). Nunca bloqueia: devolve métricas e `warnings` para a tela.

    score = check(img, layers, is_long, title="GTA 6: ...", others=[outra_img_ou_hash, ...])

A imagem deve vir do `thumb_compose.render` (que grava as caixas em `img.info["layout"]` e a máscara do texto em
`img.info["text_mask"]`); sem isso, as métricas que dependem das caixas ficam `None`. O hash perceptual (dHash de
64 bits) é feito à mão com Pillow: sem dependência nova.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from studio.core.project.document import ThumbLayers
from studio.media import thumb_templates as T

LIMITS = {
    "words_long": 3,
    "words_short": 3,
    "words_total": 3,
    "text_h": 0.18,
    "text_h_single": 0.25,
    "stroke_px": 2.0,
    "contrast": 4.5,
    "face_h": 0.25,
    "face_h_heroi": 0.30,
    "luminance": 0.40,
    "focus": 3,
    "repeat_title": 1,
    "dup": 10,
}
SMALL = (168, 94)
FIGURE_TEMPLATES = {"heroi-palavra", "avatar-reage", "numero-lista"}
_STOP = {"de", "da", "do", "das", "dos", "a", "o", "as", "os", "e", "em", "no", "na", "nos", "nas", "um", "uma",
         "pra", "para", "com", "sem", "que", "the", "of", "gta"}


# --- Hash perceptual ---------------------------------------------------------------------------------------------------
def dhash(img: Image.Image) -> int:
    """dHash de 64 bits: cinza 9×8, cada bit diz se o pixel é mais claro que o vizinho da direita."""
    small = img.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    px = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            left, right = px[row * 9 + col], px[row * 9 + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a: int, b: int) -> int:
    return bin((a ^ b) & ((1 << 64) - 1)).count("1")


def _as_hash(value: Image.Image | int | str) -> int:
    if isinstance(value, Image.Image):
        return dhash(value)
    if isinstance(value, str):
        return int(value, 16)
    return int(value)


# --- Cor --------------------------------------------------------------------------------------------------------------
def _srgb_lin(c: float) -> float:
    c = c / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def rel_luminance(rgb: Sequence[float]) -> float:
    r, g, b = (_srgb_lin(v) for v in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: Sequence[float], b: Sequence[float]) -> float:
    la, lb = rel_luminance(a), rel_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mean_lightness(img: Image.Image) -> float:
    """Média do L* (CIELAB) do quadro inteiro, de 0 a 1."""
    small = img.convert("RGB").resize((96, 54), Image.Resampling.BOX)
    total = 0.0
    n = 0
    raw = small.tobytes()
    for i in range(0, len(raw), 3):
        y = rel_luminance(raw[i:i + 3])
        lstar = 116 * (y ** (1 / 3)) - 16 if y > 216 / 24389 else y * 24389 / 27
        total += lstar
        n += 1
    return total / max(1, n) / 100


def _mean_rgb(img: Image.Image, mask: Image.Image) -> tuple[float, float, float] | None:
    hist_n = mask.histogram()[255]
    if not hist_n:
        return None
    out = []
    for band in img.convert("RGB").split():
        masked = ImageChops.multiply(band, mask)
        total = sum(i * c for i, c in enumerate(masked.histogram()))
        out.append(total / hist_n)
    return out[0], out[1], out[2]


# --- Traço a 168 px -----------------------------------------------------------------------------------------------------
def _runs(mask: np.ndarray) -> np.ndarray:
    """Comprimentos dos trechos contínuos de `True`, nas linhas e nas colunas (numpy, sem laço por pixel)."""
    out = []
    for arr in (mask, mask.T):
        padded = np.pad(arr.astype(np.int8), ((0, 0), (1, 1)))
        d = np.diff(padded, axis=1)
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        out.append(ends - starts)
    return np.concatenate(out) if out else np.zeros(0, dtype=np.int64)


def stroke_px_small(text_mask: Image.Image, small_w: int = SMALL[0]) -> float:
    """Espessura do traço do texto como fica na thumb do feed (168 px de largura): mede o traço no tamanho real
    (máscara binarizada; percentil 25 dos trechos contínuos horizontais e verticais = hastes e barras das letras) e
    escala para 168 px. Letra fina ou pequena demais dá < 2 px e vira borrão no celular."""
    box = text_mask.getbbox()
    if not box:
        return 0.0
    arr = np.asarray(text_mask.crop(box)) >= 128
    runs = _runs(arr)
    if runs.size == 0:
        return 0.0
    stem = float(np.percentile(runs, 25))
    return round(stem * small_w / text_mask.width, 2)


# --- Checagem -----------------------------------------------------------------------------------------------------------
def _norm_words(text: str) -> list[str]:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return [w for w in re.findall(r"[a-z0-9]+", s) if w not in _STOP]


def _intersects(box: Sequence[float], zone: Sequence[float]) -> bool:
    return box[0] < zone[2] and box[2] > zone[0] and box[1] < zone[3] and box[3] > zone[1]


def _overlap(box: Sequence[float], face: Sequence[float]) -> float:
    """Fração da área do rosto coberta por `box`."""
    w = min(box[2], face[2]) - max(box[0], face[0])
    h = min(box[3], face[3]) - max(box[1], face[1])
    area = max(1.0, (face[2] - face[0]) * (face[3] - face[1]))
    return max(0.0, w) * max(0.0, h) / area


def _ring_rgb(img: Image.Image, text_mask: Image.Image, H: int) -> tuple[float, float, float]:
    """Sem a medida do compositor: a cor de um anel fora do contorno (≈ 12 px a 720p além do preenchimento)."""
    r = max(2, round(H * 0.012))
    near = text_mask
    for _ in range(max(1, r // 2) + 2):
        near = near.filter(ImageFilter.MaxFilter(5))
    far = near
    for _ in range(max(1, r // 2)):
        far = far.filter(ImageFilter.MaxFilter(5))
    ring = ImageChops.subtract(far, near).point(lambda v: 255 if v > 128 else 0)
    return _mean_rgb(img, ring) or (0, 0, 0)


def visible_words(layers: ThumbLayers) -> int:
    """Texto total visível da thumb: palavra grande + pílula + rótulos dos cartões."""
    parts = [layers.text.words if layers.text else "", layers.pill or "", *(c.label for c in layers.cards)]
    return sum(len(str(p or "").split()) for p in parts)


def check(img: Image.Image, layers: ThumbLayers, is_long: bool = True, *, title: str | None = None,
          others: Sequence[Image.Image | int | str] | None = None, layout: dict[str, Any] | None = None,
          text_mask: Image.Image | None = None, title_max: int | None = None) -> dict[str, Any]:
    """Métricas e avisos (B.6): words, words_total (palavra + pílula + cartões), text_h, legib_168 {stroke_px,
    contrast, ok}, face_h, luminance, forbidden, focus, repeat_title, title_len, dup, dhash. Com `title_max` (o alvo
    do título gerado), avisa o título acima dele. Grava-se em `PackageCandidate.score`."""
    layout = layout if layout is not None else dict(img.info.get("layout") or {})
    text_mask = text_mask if text_mask is not None else img.info.get("text_mask")
    W, H = img.size
    warnings: list[str] = []
    words_txt = layers.text.words if layers.text else ""
    words = len(words_txt.split())
    limit_words = LIMITS["words_long"] if is_long else LIMITS["words_short"]
    if words > limit_words:
        warnings.append(f"texto com {words} palavras: no máximo {limit_words} (ideal 1 ou 2)")
    words_total = visible_words(layers)
    if words_total > LIMITS["words_total"] and words <= limit_words:
        warnings.append(f"texto total visível com {words_total} palavras (palavra + pílula + cartões): no máximo "
                        f"{LIMITS['words_total']} (ideal 1 ou 2)")
    title_len = len(title) if title is not None else None
    if title_len is not None and title_max and title_len > title_max:
        warnings.append(f"título com {title_len} caracteres: o alvo é até {title_max} (curto e com punch)")

    # Altura do texto
    text_h = None
    tb = layout.get("text_bbox")
    if tb:
        text_h = round((tb[3] - tb[1]) / H, 3)
        need = LIMITS["text_h_single"] if words == 1 else LIMITS["text_h"]
        if is_long and text_h < need:
            warnings.append(f"texto baixo demais: {text_h:.0%} da altura (mínimo {need:.0%})")

    # Legibilidade a 168 px
    legib: dict[str, Any] | None = None
    if text_mask is not None and text_mask.getbbox():
        stroke = stroke_px_small(text_mask, SMALL[0] if W >= H else SMALL[1])
        inner = text_mask.filter(ImageFilter.MinFilter(3)) if min(W, H) >= 300 else text_mask
        fill_rgb = _mean_rgb(img, inner.point(lambda v: 255 if v > 200 else 0)) or (255, 255, 255)
        if layout.get("text_bg"):
            # O fundo atrás do texto antes do contorno (o compositor mede): o anel em volta da máscara cairia no
            # próprio contorno escuro e o contraste sairia sempre alto.
            bg_rgb = tuple(layout["text_bg"])
        else:
            bg_rgb = _ring_rgb(img, text_mask, H)
        contrast = round(contrast_ratio(fill_rgb, bg_rgb), 2)
        ok = stroke >= LIMITS["stroke_px"] and contrast >= LIMITS["contrast"]
        legib = {"stroke_px": stroke, "contrast": contrast, "ok": ok}
        if stroke < LIMITS["stroke_px"]:
            warnings.append(f"a 168 px o traço do texto fica com {stroke:.0f} px: aumente o texto")
        if contrast < LIMITS["contrast"]:
            warnings.append(f"contraste do texto {contrast:.1f}:1 (mínimo 4,5:1)")
    elif words:
        legib = {"stroke_px": 0.0, "contrast": 0.0, "ok": False}
        warnings.append("texto sem caixa conhecida (render fora do compositor)")

    # Rosto
    figures = [f for f in layout.get("figures") or [] if f.get("kind") in ("presenter", "cutout", "frame")]
    face_h = max((float(f.get("face_h") or 0) for f in figures), default=0.0)
    if layers.template in FIGURE_TEMPLATES and is_long:
        need = LIMITS["face_h_heroi"] if layers.template == "heroi-palavra" else LIMITS["face_h"]
        if face_h < need:
            warnings.append(f"rosto pequeno: {face_h:.0%} da altura (mínimo {need:.0%})")

    # Luminância
    luminance = round(mean_lightness(img), 3)
    if luminance < LIMITS["luminance"]:
        warnings.append(f"quadro escuro: L* médio {luminance:.0%} (mínimo {LIMITS['luminance']:.0%})")

    # Zonas proibidas
    zones = T.forbidden_zones((W, H))
    forbidden = 0
    hits: list[str] = []
    boxes = [("texto", tb)] if tb else []
    if layout.get("pill_bbox"):
        boxes.append(("pílula", layout["pill_bbox"]))
    boxes += [("cartão", b) for b in layout.get("cards_bbox") or []]
    for name, box in boxes:
        for zname, z in zones.items():
            zb = (z[0] * W, z[1] * H, z[2] * W, z[3] * H)
            if _intersects(box, zb):
                forbidden += 1
                hits.append(f"{name}×{zname}")
    badge = zones.get("selo")
    if badge:
        zb = (badge[0] * W, badge[1] * H, badge[2] * W, badge[3] * H)
        for f in figures:
            if f.get("face_box") and _intersects(f["face_box"], zb):
                forbidden += 1
                hits.append("rosto×selo")
    for f in figures:
        fb = f.get("face_box")
        for zname, z in zones.items():
            zb = (z[0] * W, z[1] * H, z[2] * W, z[3] * H)
            if zname != "selo" and fb and not is_long and _intersects(fb, zb):
                forbidden += 1  # Short: rosto embaixo da interface (topo ou base)
                hits.append(f"rosto×{zname}")
            if zname == "selo" and f.get("kind") == "presenter" and f.get("crop") == "meio" and f.get("bbox")                     and _intersects(f["bbox"], zb):
                forbidden += 1  # avatar pequeno de corpo inteiro no canto do selo (o busto vaza de propósito)
                hits.append("figura×selo")
    if forbidden:
        warnings.append("elemento na zona proibida: " + ", ".join(dict.fromkeys(hits)))

    # Sobreposições: texto em cima de rosto, logo em cima de texto ou de rosto
    overlaps: list[str] = []
    faces = [f["face_box"] for f in figures if f.get("face_box")]
    text_boxes = [b for _, b in boxes]
    logos = [f["bbox"] for f in layout.get("figures") or [] if f.get("kind") == "logo" and f.get("bbox")]
    for fb in faces:
        if any(_overlap(b, fb) > 0.15 for b in text_boxes):
            overlaps.append("texto×rosto")
    for lb in logos:
        if any(_intersects(lb, b) for b in text_boxes):
            overlaps.append("logo×texto")
        if any(_intersects(lb, fb) for fb in faces):
            overlaps.append("logo×rosto")
    if overlaps:
        warnings.append("elementos um em cima do outro: " + ", ".join(dict.fromkeys(overlaps)))

    # Focos
    focus = sum(1 for f in figures if f.get("kind") in ("presenter", "cutout"))
    focus += 1 if any(f.get("kind") == "frame" for f in figures) else 0
    focus += int(layout.get("objects") or 0)
    if focus > LIMITS["focus"]:
        warnings.append(f"{focus} focos na thumb (máximo {LIMITS['focus']})")

    # Repetição do título
    repeat = None
    if title is not None and words_txt:
        tw = set(_norm_words(title))
        repeat = sum(1 for w in _norm_words(words_txt) if w in tw)
        if repeat > LIMITS["repeat_title"]:
            warnings.append(f"o texto repete {repeat} palavras do título: complemente, não repita")

    # Variantes iguais
    h = dhash(img)
    dup = None
    if others:
        dup = min(hamming(h, _as_hash(o)) for o in others)
        if dup < LIMITS["dup"]:
            warnings.append(f"igual a outra variante (dHash {dup} < {LIMITS['dup']}): troque fundo, pose ou palavra")

    for w in layout.get("warnings") or []:
        warnings.append(w)
    return {
        "overlaps": list(dict.fromkeys(overlaps)),
        "words": words, "words_total": words_total, "title_len": title_len, "text_h": text_h, "legib_168": legib, "face_h": round(face_h, 3), "luminance": luminance,
        "forbidden": forbidden, "focus": focus, "repeat_title": repeat, "dup": dup, "dhash": f"{h:016x}",
        "warnings": list(dict.fromkeys(warnings)),
    }
