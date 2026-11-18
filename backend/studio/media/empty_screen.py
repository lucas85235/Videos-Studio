"""Foto de banco com a tela vazia: monitor, celular ou TV com a tela de chroma crua, branca ou desligada.

Banco de imagens (Pexels, Commons, Openverse) é cheio de "mockups": o aparelho com a tela de chroma ou em branco para
quem compra pôr a própria imagem dentro. No vídeo isso sai como placeholder esquecido (o verde cru com texto por
cima) e, desfocado e escurecido atrás do texto cinético, vira um quadro cinza liso só com a palavra da vez.

Dois filtros baratos, para o acervo usar só no banco de imagens (material próprio, oficial e captura nunca passam
por aqui: página e app têm muito retângulo liso de propósito):

- `describes_empty_screen`: a descrição ou o endereço da página fala em "green screen", "chroma key", "blank
  screen", "mockup"... (na coleta, antes de baixar);
- `empty_screen`: a imagem tem uma tela vazia. Numa grade de células de 4 px da miniatura de 192 px de largura,
  procura uma região de células lisas (desvio de cada canal < 5 na célula; < 10 no chroma, que tem ruído de
  iluminação) e da mesma cor (vizinhas a menos de 8 níveis, todas a menos de 40 da primeira) que
  * não toca a borda da foto: é um retângulo DENTRO da cena, como a tela de um aparelho (céu, mar, parede e fundo
    de estúdio tocam a borda);
  * cobre pelo menos 8% da foto;
  * tem cor de chroma (verde de 85° a 165° ou azul de 200° a 250°, saturação ≥ 0,45, brilho ≥ 0,3) ou cor extrema
    (quase branca e sem cor, ou quase preta);
  * é cheia por dentro: o miolo da caixa dela (tirando 15% de cada lado) está ao menos 85% coberto (chroma) ou 97%
    (branco/preto: um painel escuro de app, com texto, tem buracos e fica de fora).
"""

from __future__ import annotations

import re
import unicodedata
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image

GRID_WIDTH = 192
CELL = 4
FLAT_STD = 5.0
CHROMA_STD = 10.0
STEP = 8.0
SEED = 40.0
MIN_AREA = 0.08
MARGIN = 0.15
MIN_CORE_CHROMA = 0.85
MIN_CORE_BLANK = 0.97

CHROMA = "tela de chroma"
BLANK = "tela vazia"

# Texto já dobrado (minúsculas, sem acento, só letras, números e espaços). "chroma" sozinho não: é a linha de
# teclados e periféricos RGB (Razer Chroma), assunto comum de vídeo de jogo. "blue screen" também não: é a tela azul
# de erro do Windows, que tem conteúdo.
_WORDS = re.compile(
    r"\b(green ?screen|chroma ?key|mock ?ups?"
    r"|(blank|empty)( \w+)? (screen|display|monitor)s?"
    r"|tela (verde|vazia|em branco)|fundo verde)\b"
)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text if not unicodedata.combining(ch)).split())


def describes_empty_screen(*texts: str | None) -> bool:
    """A descrição ou o endereço da página diz que a foto é de tela de chroma, tela em branco ou mockup."""
    return bool(_WORDS.search(" ".join(_fold(t or "") for t in texts)))


def empty_screen(image: Image.Image) -> str | None:
    """`CHROMA` ou `BLANK` quando a imagem tem uma tela vazia (ver o critério no topo do módulo); senão None."""
    rgb = image.convert("RGB")
    rows = max(3, round(GRID_WIDTH * rgb.height / max(1, rgb.width) / CELL))
    pixels = np.asarray(rgb.resize((GRID_WIDTH, rows * CELL), Image.Resampling.BOX), dtype=np.float32)
    gw = GRID_WIDTH // CELL
    cells = pixels.reshape(rows, CELL, gw, CELL, 3).transpose(0, 2, 1, 3, 4).reshape(rows, gw, CELL * CELL, 3)
    mean = cells.mean(axis=2)
    std = cells.std(axis=2).max(axis=2)
    top, low = mean.max(axis=2), mean.min(axis=2)
    value = top / 255
    sat = (top - low) / np.maximum(top, 1e-6)
    r, g, b = mean[..., 0], mean[..., 1], mean[..., 2]
    span = np.maximum(top - low, 1e-6)
    hue = np.where(top == r, ((g - b) / span) % 6, np.where(top == g, (b - r) / span + 2, (r - g) / span + 4)) * 60
    chroma = (sat >= 0.45) & (value >= 0.3) & (((hue >= 85) & (hue <= 165)) | ((hue >= 200) & (hue <= 250)))
    extreme = ((value >= 0.85) & (sat <= 0.15)) | (value <= 0.12)
    candidate = ((std < FLAT_STD) & extreme) | ((std < CHROMA_STD) & chroma)
    seen = np.zeros_like(candidate)
    for y in range(rows):
        for x in range(gw):
            if not candidate[y, x] or seen[y, x]:
                continue
            seed = mean[y, x]
            seen[y, x] = True
            queue = deque([(y, x)])
            region: set[tuple[int, int]] = set()
            while queue:
                cy, cx = queue.popleft()
                region.add((cy, cx))
                for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                    if (0 <= ny < rows and 0 <= nx < gw and candidate[ny, nx] and not seen[ny, nx]
                            and float(np.abs(mean[ny, nx] - mean[cy, cx]).max()) < STEP
                            and float(np.abs(mean[ny, nx] - seed).max()) < SEED):
                        seen[ny, nx] = True
                        queue.append((ny, nx))
            if len(region) < MIN_AREA * rows * gw:
                continue
            ys = [p[0] for p in region]
            xs = [p[1] for p in region]
            y0, y1, x0, x1 = min(ys), max(ys), min(xs), max(xs)
            if y0 == 0 or x0 == 0 or y1 == rows - 1 or x1 == gw - 1:
                continue  # toca a borda: céu, parede, fundo de estúdio
            my, mx = (y1 - y0 + 1) * MARGIN, (x1 - x0 + 1) * MARGIN
            core = [(py, px) for py in range(y0, y1 + 1) for px in range(x0, x1 + 1)
                    if y0 + my <= py <= y1 - my and x0 + mx <= px <= x1 - mx]
            filled = sum(1 for p in core if p in region) / len(core) if core else 0.0
            is_chroma = bool(chroma[y, x])
            if filled >= (MIN_CORE_CHROMA if is_chroma else MIN_CORE_BLANK):
                return CHROMA if is_chroma else BLANK
    return None


def empty_screen_file(path: Path) -> str | None:
    """`empty_screen` de um arquivo; arquivo ilegível não é tela vazia (quem anexa já recusa por outro motivo)."""
    try:
        with Image.open(path) as im:
            return empty_screen(im)
    except Exception:  # noqa: BLE001 - imagem ruim ou gigante: sem veredito
        return None
