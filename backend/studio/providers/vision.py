"""Visão: classificar quadros (folhas/tiras de planos) com um esquema JSON (ADR 0004 §3.0).

Interface única `classify_frames(sheets, schema) -> list[dict]`: uma resposta por imagem, com as chaves do esquema.
O item `ref-perfil` liga a implementação real (`claude -p` com leitura de arquivo restrita ao diretório das folhas,
com `codex exec -i` de reserva). Aqui ficam o contrato e o `MockVision`, determinístico e offline: decide pela
saturação e pela densidade de bordas da imagem, sem rede e sem modelo.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

import numpy as np
from PIL import Image

from studio.config import get_settings

# Esquema por plano usado na análise da referência (`media/refprofile/vision.py`).
SHOT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["material", "on_screen_text", "text_case", "text_pos", "font_desc", "has_overlay", "overlay_kind",
                 "is_meme", "mood"],
    "properties": {
        "material": {"type": "string"},
        "on_screen_text": {"type": ["string", "null"]},
        "text_case": {"type": ["string", "null"]},
        "text_pos": {"type": ["string", "null"]},
        "font_desc": {"type": ["string", "null"]},
        "has_overlay": {"type": "boolean"},
        "overlay_kind": {"type": ["string", "null"]},
        "is_meme": {"type": "boolean"},
        "mood": {"type": ["string", "null"]},
    },
}


class VisionProvider(Protocol):
    id: str

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        """Uma resposta por imagem de `sheets`, na mesma ordem, com as chaves de `schema["properties"]`."""
        ...


def _measure(path: Path) -> tuple[float, float, float]:
    """Saturação média (0 a 1), densidade de bordas (0 a 1) e brilho médio (0 a 1), numa versão de 160 px."""
    with Image.open(path) as img:
        small = img.convert("RGB")
        small.thumbnail((160, 160))
        hsv = np.asarray(small.convert("HSV"), dtype=np.float32) / 255.0
        gray = np.asarray(small.convert("L"), dtype=np.float32) / 255.0
    saturation = float(hsv[..., 1].mean())
    brightness = float(gray.mean())
    gx = np.abs(np.diff(gray, axis=1))
    gy = np.abs(np.diff(gray, axis=0))
    edges = float(((gx > 0.12).mean() + (gy > 0.12).mean()) / 2)
    return saturation, edges, brightness


def _empty(prop: dict[str, Any]) -> Any:
    kind = prop.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    if "null" in kinds:
        return None
    if "boolean" in kinds:
        return False
    if "number" in kinds or "integer" in kinds:
        return 0
    if "array" in kinds:
        return []
    if "object" in kinds:
        return {}
    return ""


class MockVision:
    """Classificação determinística por saturação e bordas (testes e `VS_OFFLINE=1`):

    - muitas bordas e pouca cor: `page_capture` (texto de página, notícia);
    - muitas bordas e muita cor: `gameplay`;
    - poucas bordas e pouca cor: `text_card`;
    - muita cor e poucas bordas: `ai_illustration` (superfícies lisas e saturadas);
    - o resto: `photo_real`.
    """

    id = "mock_vision"

    def describe(self, path: Path) -> dict[str, Any]:
        saturation, edges, brightness = _measure(path)
        if edges >= 0.18:
            material = "page_capture" if saturation < 0.18 else "gameplay"
        elif saturation < 0.12:
            material = "text_card"
        elif saturation >= 0.45 and edges < 0.06:
            material = "ai_illustration"
        else:
            material = "photo_real"
        texty = material in ("page_capture", "text_card")
        return {
            "material": material,
            "on_screen_text": "TEXTO" if texty else None,
            "text_case": "upper" if texty else None,
            "text_pos": "center" if texty else None,
            "font_desc": "sans bold" if texty else None,
            "has_overlay": False,
            "overlay_kind": None,
            "is_meme": False,
            "mood": "dark" if brightness < 0.3 else ("bright" if brightness > 0.65 else "neutral"),
            "saturation": round(saturation, 4),
            "edges": round(edges, 4),
        }

    def classify_frames(self, sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
        props: dict[str, Any] = (schema or {}).get("properties") or {}
        out: list[dict[str, Any]] = []
        for sheet in sheets:
            facts = self.describe(Path(sheet))
            if not props:
                out.append(facts)
                continue
            out.append({key: facts[key] if key in facts else _empty(prop) for key, prop in props.items()})
        return out


def get_vision() -> VisionProvider:
    """O provedor de visão em uso. Offline sempre o `MockVision`; online também, até o `ref-perfil` ligar o real."""
    if get_settings().offline:
        return MockVision()
    return MockVision()  # encaixe: o `ref-perfil` troca pelo provedor real (claude -p / codex exec -i)


def classify_frames(sheets: list[Path], schema: dict[str, Any]) -> list[dict[str, Any]]:
    """Classifica cada imagem com o provedor em uso (ver `VisionProvider.classify_frames`)."""
    return get_vision().classify_frames(sheets, schema)
