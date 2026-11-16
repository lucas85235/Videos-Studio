"""Biblioteca de estilos: presets (YAML no repositório) + estilos do usuário (banco)."""

from __future__ import annotations

import copy
import random
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from studio.config import get_settings
from studio.core.styles.schema import StyleSpec


class StyleNotFound(LookupError):
    pass


# Estilo sugerido ao criar um projeto (o mesmo `DEFAULT_STYLE_ID` de apps/web/src/api.ts).
DEFAULT_STYLE_ID = "curiosidades-short"

# Ordem dos presets na interface: os nichos gerais primeiro, os sombrios por último. Presets fora da lista
# vêm depois, na ordem dos arquivos.
PRESET_ORDER = (
    "curiosidades-short",
    "tecnologia-ia",
    "games-cultura-pop-short",
    "estoico-short",
    "misterios-short",
    "terror-analog",
    "curiosidades-longo",
    "explicador-vetorial",
    "historia-documental",
    "biografias",
    "financas-negocios",
    "historias-narradas",
    "top10-listas",
    "whiteboard-explicador",
    "colagem-artesanal",
    "interface-tela",
    "documentario-sombrio",
    "true-crime",
)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Mescla recursiva: dicionários são mesclados, o resto é substituído."""
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def set_path(data: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    """Define um valor por caminho com pontos (ex.: 'edit.captions.size')."""
    result = copy.deepcopy(data)
    node = result
    parts = path.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    node[parts[-1]] = value
    return result


def presets_dir() -> Path:
    return get_settings().presets_dir / "styles"


def load_presets() -> dict[str, StyleSpec]:
    styles: dict[str, StyleSpec] = {}
    folder = presets_dir()
    if not folder.exists():
        return styles
    for path in sorted(folder.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        try:
            style = StyleSpec.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"preset inválido em {path.name}: {exc}") from exc
        styles[style.id] = style
    rank = {style_id: i for i, style_id in enumerate(PRESET_ORDER)}
    return dict(sorted(styles.items(), key=lambda item: rank.get(item[0], len(rank))))


def list_styles() -> list[dict[str, Any]]:
    """Presets + estilos do usuário, com a origem de cada um."""
    from studio.db import session_scope
    from studio.models import Style

    items: list[dict[str, Any]] = []
    for style in load_presets().values():
        items.append({"source": "preset", "version": 1, "style": style.model_dump(mode="json")})
    with session_scope() as s:
        for row in s.query(Style).order_by(Style.name).all():
            items.append({"source": "user", "version": row.version, "style": row.data, "parent_id": row.parent_id})
    return items


def get_style(style_id: str, overrides: dict[str, Any] | None = None) -> StyleSpec:
    from studio.db import session_scope
    from studio.models import Style

    with session_scope() as s:
        row = s.get(Style, style_id)
        data: dict[str, Any] | None = dict(row.data) if row else None
    if data is None:
        preset = load_presets().get(style_id)
        if preset is None:
            raise StyleNotFound(style_id)
        data = preset.model_dump(mode="json")
    if overrides:
        data = deep_merge(data, overrides)
    return StyleSpec.model_validate(data)


def save_user_style(style: StyleSpec, parent_id: str | None = None) -> int:
    """Cria ou atualiza um estilo do usuário. Retorna a nova versão."""
    from studio.db import session_scope
    from studio.models import Style

    if style.id in load_presets():
        raise ValueError("esse id pertence a um preset; duplique com outro id")
    with session_scope() as s:
        row = s.get(Style, style.id)
        if row is None:
            row = Style(id=style.id, name=style.name, data=style.model_dump(mode="json"), version=1, parent_id=parent_id)
            s.add(row)
            return 1
        row.name = style.name
        row.data = style.model_dump(mode="json")
        row.version += 1
        return row.version


def delete_user_style(style_id: str) -> bool:
    from studio.db import session_scope
    from studio.models import Style

    with session_scope() as s:
        row = s.get(Style, style_id)
        if row is None:
            return False
        s.delete(row)
        return True


def pick_weighted(pool: dict[str, float], seed: int, exclude: set[str] | None = None) -> str:
    """Sorteio ponderado determinístico. `exclude` evita repetir a escolha anterior quando possível."""
    items = [(k, w) for k, w in pool.items() if w > 0]
    if exclude:
        filtered = [(k, w) for k, w in items if k not in exclude]
        if filtered:
            items = filtered
    rng = random.Random(seed)
    total = sum(w for _, w in items)
    roll = rng.uniform(0, total)
    acc = 0.0
    for key, weight in items:
        acc += weight
        if roll <= acc:
            return key
    return items[-1][0]
