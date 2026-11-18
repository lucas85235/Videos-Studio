"""Fase 5 (avatar no renderer): contrato estático entre o `core/timeline.py` e o Remotion.

O render de verdade é conferido com `tsc --noEmit` e um still de amostra; aqui só garantimos que a camada existe,
entra na cena entre a fonte e as legendas, some no vertical, lê os campos que o backend grava e que o hash do
renderer (versão dos blocos) inclui o arquivo novo.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from studio.config import REPO_ROOT
from studio.core.hashing import hash_tree

RD = REPO_ROOT / "renderer" / "src"
LAYER = RD / "components" / "PresenterLayer.tsx"
SCENE = RD / "components" / "SceneView.tsx"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_layer_exists_and_reads_backend_fields() -> None:
    src = _read(LAYER)
    assert "export const PresenterLayer" in src
    # Campos de `resolved["presenter"]` (C.4): src, from, dur, mode, side, height, outline, punch.
    for field in ("src", "from", "dur", "mode", "side", "height", "outline", "punch"):
        assert re.search(rf"presenter\.{field}\b", src), field


def test_layer_hidden_on_vertical() -> None:
    assert "width <= height" in _read(LAYER)


def test_layer_ports_reacao_motion() -> None:
    src = _read(LAYER)
    assert "damping: 13, stiffness: 170" in src  # mola de entrada
    assert "[9, 1.5]" in src  # inclinação que assenta
    assert "1.07" in src  # soco do surpreso
    assert "0.018" in src  # pulso da fala no modo explica
    assert "BLEED = 0.14" in src  # 14% para fora da borda
    # O contorno de adesivo mora em sticker.ts (os memes do renderer-fx também usam): 4 de contorno + sombra.
    assert "import { stickerFilter } from '../sticker';" in src
    assert _read(RD / "sticker.ts").count("drop-shadow(") >= 5


def test_sceneview_inserts_layer_between_source_and_captions() -> None:
    src = _read(SCENE)
    # o carimbo também importa o retângulo do avatar (`presenterRect`) para desviar dele
    assert re.search(r"import \{ PresenterLayer\b[^}]*\} from './PresenterLayer';", src)
    i_source = src.index("sourceChip\n      )}")
    i_layer = src.index("<PresenterLayer")
    i_caps = src.index("<Captions lines=")
    assert i_source < i_layer < i_caps


def test_timeline_type_has_presenter() -> None:
    assert "presenter?: TimelinePresenter" in _read(RD / "types.ts")


def test_renderer_hash_changes_with_presenter_layer(tmp_path: Path) -> None:
    """A versão do renderer é o hash de `renderer/src`: com a camada nova, todos os blocos são refeitos uma vez."""
    copy = tmp_path / "src"
    shutil.copytree(RD, copy)
    with_layer = hash_tree(copy)
    (copy / "components" / "PresenterLayer.tsx").unlink()
    assert hash_tree(copy) != with_layer
