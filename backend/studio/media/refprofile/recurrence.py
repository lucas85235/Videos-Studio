"""Elementos recorrentes da referência (ADR 0004 §3.2, `recurrence.py`): mascote, logo ou meme que volta muitas vezes.

O dHash de `media/novelty.py` (64 bits) é tirado de cada caixa de sobreposição (o recorte do quadro do meio) e do
quadro do meio de cada plano. Agrupamento guloso por distância de Hamming (até 10 bits, `novelty.SIMILAR_BITS`); grupo
com 5 aparições ou mais vira `recurring`, com miniatura colorida quando há onde gravar.

Só para medir a fórmula ("o mascote volta a cada 40 s"): a miniatura é referência visual na tela da referência e
**nunca** vira material reutilizável (ADR 0004 §4).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from studio.media import novelty

MIN_COUNT = 5
THUMB_SIDE = 240


def crop_hash(frame: np.ndarray, box: list[float] | tuple[float, ...] | None = None) -> str | None:
    """dHash do quadro em cinza inteiro ou do recorte `box` (x, y, w, h de 0 a 1)."""
    h, w = frame.shape[:2]
    if box:
        x0, y0 = int(box[0] * w), int(box[1] * h)
        x1, y1 = max(x0 + 2, int((box[0] + box[2]) * w)), max(y0 + 2, int((box[1] + box[3]) * h))
        frame = frame[y0:y1, x0:x1]
    if frame.size == 0:
        return None
    return novelty.dhash(Image.fromarray(np.ascontiguousarray(frame)))


def group(items: list[dict[str, Any]], bits: int = novelty.SIMILAR_BITS) -> list[list[dict[str, Any]]]:
    """Grupos de itens com hash parecido (`{"hash", "t", "source", ...}`), do maior para o menor."""
    groups: list[list[dict[str, Any]]] = []
    centers: list[str] = []
    for item in sorted((i for i in items if i.get("hash")), key=lambda i: i["t"]):
        for n, center in enumerate(centers):
            if novelty.distance(center, item["hash"]) <= bits:
                groups[n].append(item)
                break
        else:
            centers.append(item["hash"])
            groups.append([item])
    return sorted(groups, key=len, reverse=True)


def recurring(items: list[dict[str, Any]], *, kind_of: Callable[[list[dict[str, Any]]], str] | None = None,
              thumb: Callable[[int, dict[str, Any]], str | None] | None = None,
              min_count: int = MIN_COUNT) -> list[dict[str, Any]]:
    """Os grupos com `min_count` aparições ou mais, no formato de `RecurringItem`."""
    out: list[dict[str, Any]] = []
    for n, members in enumerate(g for g in group(items) if len(g) >= min_count):
        first = members[0]
        kind = kind_of(members) if kind_of else "other"
        out.append({"id": f"rec{n + 1}", "kind": kind if kind in ("mascot", "logo", "meme") else "other",
                    "count": len(members), "thumb": thumb(n + 1, first) if thumb else None,
                    "first_s": round(float(first["t"]), 2)})
        if len(out) >= 10:
            break
    return out


def save_thumb(image: Image.Image, box: list[float] | None, target: Path) -> Path:
    """Recorte (ou quadro inteiro) reduzido a 240 px, em JPG."""
    w, h = image.size
    if box:
        image = image.crop((int(box[0] * w), int(box[1] * h), int((box[0] + box[2]) * w), int((box[1] + box[3]) * h)))
    image = image.convert("RGB")
    image.thumbnail((THUMB_SIDE, THUMB_SIDE))
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, "JPEG", quality=85)
    return target
