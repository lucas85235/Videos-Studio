"""Onde há letras na imagem: manchete e corpo de matéria numa captura, logo, letreiro, tela com texto.

O renderer desenha carimbo (BOATO, CONFIRMADO), faixa do topo ("GAMESBEAT · 13/10/2026") e legenda por cima da
imagem. Sem saber onde estão as letras DA imagem, eles caíam em cima da manchete da captura ("BOATO" tampando o "C" de
"PC") e do logo do jogo ("JANEIRO t auto"). Esta detecção é só numpy/Pillow (sem OCR nem modelo): letra é borda forte
e densa, em fileira, com contraste alto dentro de uma faixa de altura de linha. Funciona em duas escalas (texto de
corpo na fina; logo e título gigante na grossa, onde as letras grandes viram "texto pequeno") e devolve as caixas
[x, y, w, h] em fração da imagem, mescladas, maiores primeiro.

Falso positivo (folhagem, grade de prédio) não quebra nada: o renderer trata essas caixas como obstáculo "macio" — o
carimbo e a faixa só desviam delas quando há lugar livre; sem lugar, ficam no de menor sobreposição.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageFilter

log = logging.getLogger(__name__)

# Versão da detecção: muda o cache em disco (arquivo `<imagem>.text.json`) quando a conta muda.
VERSION = 1
# Larguras de trabalho: a fina pega texto de corpo e manchete; a grossa, logo e título gigante.
SCALES = (640, 200)
# Gradiente (0-255) que conta como borda de letra.
EDGE = 48
# Tamanho da célula (px na largura de trabalho) e fração de borda que marca a célula como "tem letra".
CELL = 8
DENSITY = 0.16
# Contraste mínimo (máx - mín de cinza) dentro da célula: letra tem fundo e traço bem diferentes.
CONTRAST = 70
# Componente aceito: no mínimo 2 células de largura e no máximo esta fração da área da imagem (textura enorme não é
# texto; o logo grande cabe na escala grossa).
MAX_AREA = 0.45
MIN_CELLS = 3
# Caixas mais perto que isto (fração) viram uma só.
MERGE_GAP = 0.012
# Teto de caixas por imagem (a timeline não precisa de mais).
MAX_RECTS = 12

Rect = list[float]


def _cells(gray: np.ndarray) -> np.ndarray:
    """Grade booleana: célula com borda densa e contraste alto (candidata a letra)."""
    g = gray.astype(np.int16)
    gx = np.zeros_like(g)
    gy = np.zeros_like(g)
    gx[:, 1:] = np.abs(g[:, 1:] - g[:, :-1])
    gy[1:, :] = np.abs(g[1:, :] - g[:-1, :])
    edges = (np.maximum(gx, gy) >= EDGE).astype(np.float32)
    h, w = g.shape
    rows, cols = h // CELL, w // CELL
    if rows == 0 or cols == 0:
        return np.zeros((0, 0), dtype=bool)
    e = edges[: rows * CELL, : cols * CELL].reshape(rows, CELL, cols, CELL)
    density = e.mean(axis=(1, 3))
    block = g[: rows * CELL, : cols * CELL].reshape(rows, CELL, cols, CELL)
    contrast = block.max(axis=(1, 3)) - block.min(axis=(1, 3))
    # Traço de letra: borda nas duas direções (só horizontal é linha de tabela, só vertical é grade).
    hx = (gx[: rows * CELL, : cols * CELL] >= EDGE).reshape(rows, CELL, cols, CELL).any(axis=(1, 3))
    hy = (gy[: rows * CELL, : cols * CELL] >= EDGE).reshape(rows, CELL, cols, CELL).any(axis=(1, 3))
    return (density >= DENSITY) & (contrast >= CONTRAST) & hx & hy


def _close_rows(mask: np.ndarray, gap: int = 2) -> np.ndarray:
    """Fecha buracos de até `gap` células na horizontal (espaço entre palavras e entre letras grandes)."""
    out = mask.copy()
    rows, cols = mask.shape
    for r in range(rows):
        idx = np.flatnonzero(mask[r])
        for a, b in zip(idx, idx[1:], strict=False):
            if 1 < b - a <= gap + 1:
                out[r, a:b] = True
    return out


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Componentes 4-conexos: (linha0, col0, linha1, col1, células) com fim exclusivo."""
    rows, cols = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    out: list[tuple[int, int, int, int, int]] = []
    for r0 in range(rows):
        for c0 in range(cols):
            if not mask[r0, c0] or seen[r0, c0]:
                continue
            stack = [(r0, c0)]
            seen[r0, c0] = True
            r_lo, r_hi, c_lo, c_hi, n = r0, r0, c0, c0, 0
            while stack:
                r, c = stack.pop()
                n += 1
                r_lo, r_hi, c_lo, c_hi = min(r_lo, r), max(r_hi, r), min(c_lo, c), max(c_hi, c)
                for rr, cc in ((r + 1, c), (r - 1, c), (r, c + 1), (r, c - 1)):
                    if 0 <= rr < rows and 0 <= cc < cols and mask[rr, cc] and not seen[rr, cc]:
                        seen[rr, cc] = True
                        stack.append((rr, cc))
            out.append((r_lo, c_lo, r_hi + 1, c_hi + 1, n))
    return out


def _scale_rects(img: Image.Image, width: int) -> list[Rect]:
    w0, h0 = img.size
    if w0 <= 0 or h0 <= 0:
        return []
    width = min(width, w0)
    height = max(CELL, round(h0 * width / w0))
    gray = np.asarray(img.convert("L").resize((width, height), Image.Resampling.BILINEAR).filter(ImageFilter.SMOOTH))
    mask = _close_rows(_cells(gray))
    rows, cols = mask.shape
    if not rows or not cols:
        return []
    out: list[Rect] = []
    for r0, c0, r1, c1, n in _components(mask):
        bw, bh = c1 - c0, r1 - r0
        if n < MIN_CELLS or bw < 2:
            continue
        area = (bw * bh) / (rows * cols)
        if area > MAX_AREA:
            continue
        # Bloco de texto é cheio (fileiras de letras); textura solta (galhos, ruído) deixa a caixa vazia.
        if n / (bw * bh) < 0.35:
            continue
        out.append([c0 / cols, r0 / rows, bw / cols, bh / rows])
    return out


def _touch(a: Rect, b: Rect, gap: float) -> bool:
    return (a[0] - gap < b[0] + b[2] and b[0] - gap < a[0] + a[2]
            and a[1] - gap < b[1] + b[3] and b[1] - gap < a[1] + a[3])


def merge(rects: Iterable[Rect], gap: float = MERGE_GAP) -> list[Rect]:
    """Une caixas que se tocam (com `gap` de folga) até não sobrar par encostado."""
    boxes = [list(r) for r in rects]
    changed = True
    while changed:
        changed = False
        out: list[Rect] = []
        for box in boxes:
            for other in out:
                if _touch(box, other, gap):
                    x0, y0 = min(box[0], other[0]), min(box[1], other[1])
                    x1 = max(box[0] + box[2], other[0] + other[2])
                    y1 = max(box[1] + box[3], other[1] + other[3])
                    other[:] = [x0, y0, x1 - x0, y1 - y0]
                    changed = True
                    break
            else:
                out.append(box)
        boxes = out
    return boxes


def detect(img: Image.Image) -> list[Rect]:
    """Caixas [x, y, w, h] (0 a 1 da imagem) onde há letras, maiores primeiro (no máximo `MAX_RECTS`)."""
    found: list[Rect] = []
    for width in SCALES:
        found += _scale_rects(img, width)
    boxes = merge(found)
    boxes = [b for b in boxes if b[2] * b[3] <= MAX_AREA * 1.2]
    boxes.sort(key=lambda b: b[2] * b[3], reverse=True)
    return [[round(max(0.0, v), 4) for v in b] for b in boxes[:MAX_RECTS]]


def _cache_path(path: Path) -> Path:
    return path.with_name(path.name + ".text.json")


def rects_for(path: Path) -> list[Rect]:
    """Caixas de texto do arquivo, com cache ao lado dele (`<arquivo>.text.json`, invalidado pela versão e pelo
    tamanho/data do arquivo). Arquivo ilegível ou vídeo: lista vazia."""
    try:
        stat = path.stat()
    except OSError:
        return []
    key = {"v": VERSION, "size": stat.st_size, "mtime": int(stat.st_mtime)}
    cache = _cache_path(path)
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        if data.get("key") == key:
            return [list(map(float, r)) for r in data.get("rects", [])]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    try:
        with Image.open(path) as img:
            img.load()
            rects = detect(img)
    except Exception as exc:  # noqa: BLE001 - imagem ilegível: sem caixas, o vídeo segue
        log.debug("texto da imagem não detectado em %s: %s", path, exc)
        return []
    try:
        cache.write_text(json.dumps({"key": key, "rects": rects}), encoding="utf-8")
    except OSError:
        pass
    return rects


IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def annotate_timeline(timeline: dict[str, Any], resolve: Callable[[str], Path]) -> None:
    """Grava `text_rects` (caixas de letras no arquivo que o renderer recebe) em cada cena e em cada subplano com
    imagem própria. Cena sem imagem (ou com vídeo) fica como está."""
    memo: dict[str, list[Rect]] = {}

    def of(rel: Any) -> list[Rect]:
        if not isinstance(rel, str) or not rel.lower().endswith(IMAGE_EXTS):
            return []
        if rel not in memo:
            memo[rel] = rects_for(resolve(rel))
        return memo[rel]

    for scene in timeline.get("scenes", []):
        rects = of(scene.get("src"))
        if rects:
            scene["text_rects"] = rects
        for shot in scene.get("shots") or []:
            if isinstance(shot, dict) and shot.get("src") and not shot.get("video"):
                own = of(shot.get("src"))
                if own:
                    shot["text_rects"] = own
