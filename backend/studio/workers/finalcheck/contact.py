"""Folha de contato com 12 quadros reais do vídeo final (origem: canais `check_render.py` e `frames.py`).

Quadros em `t = dur × (k + 0,5) / 12`; horizontal em 3 colunas × 4 linhas com quadros de 640 px de largura, vertical em
6 colunas × 2 linhas com quadros de 480 px de altura; rótulo "m:ss" em (6, 4), vermelho (200, 0, 0); JPG qualidade 86.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from studio.media.ffmpeg import duration_of, probe, run_ffmpeg

FRAMES = 12
TILE_WIDTH = 640  # horizontal
TILE_HEIGHT = 480  # vertical
LABEL_AT = (6, 4)
LABEL_COLOR = (200, 0, 0)
QUALITY = 86
SHEET_NAME = "conferencia-quadros.jpg"


def frame_times(duration: float, n: int = FRAMES) -> list[float]:
    return [round(duration * (k + 0.5) / n, 3) for k in range(n)]


def _size(video: Path) -> tuple[int, int]:
    for stream in probe(video).get("streams", []):
        if stream.get("codec_type") == "video" and stream.get("width"):
            return int(stream["width"]), int(stream["height"])
    raise ValueError("o arquivo não tem imagem de vídeo")


def layout(width: int, height: int) -> tuple[int, int, int, int]:
    """(colunas, linhas, largura do quadro, altura do quadro)."""
    if width >= height:
        return 3, 4, TILE_WIDTH, max(1, round(TILE_WIDTH * height / width))
    return 6, 2, max(1, round(TILE_HEIGHT * width / height)), TILE_HEIGHT


def contact_sheet(video: Path, out_path: Path, duration: float | None = None) -> dict[str, Any]:
    """Monta a folha em `out_path`. Devolve {"path", "tiles", "cols", "rows", "tile": [w, h], "size": [w, h]}."""
    dur = duration if duration and duration > 0 else duration_of(video)
    if dur <= 0:
        raise ValueError("o vídeo não tem duração")
    cols, rows, tw, th = layout(*_size(video))
    sheet = Image.new("RGB", (tw * cols, th * rows), "white")
    draw = ImageDraw.Draw(sheet)
    tmp = Path(tempfile.mkdtemp(prefix="vulto-quadros-"))
    try:
        times = frame_times(dur)
        for i, t in enumerate(times):
            png = tmp / f"q{i:02d}.png"
            run_ffmpeg(["-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1", str(png)], timeout=120)
            with Image.open(png) as im:
                tile = im.convert("RGB").resize((tw, th))
            x, y = (i % cols) * tw, (i // cols) * th
            sheet.paste(tile, (x, y))
            s = int(t)
            draw.text((x + LABEL_AT[0], y + LABEL_AT[1]), f"{s // 60}:{s % 60:02d}", fill=LABEL_COLOR)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out_path, "JPEG", quality=QUALITY)
    return {"path": str(out_path), "tiles": len(times), "cols": cols, "rows": rows, "tile": [tw, th],
            "size": list(sheet.size)}
