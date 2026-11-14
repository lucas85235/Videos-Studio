"""Hash canônico de objetos: a base do cache por conteúdo e da edição incremental."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def _default(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump(mode="json")
    if isinstance(obj, Path):
        return obj.as_posix()
    if isinstance(obj, (set, frozenset)):
        return sorted(obj)
    raise TypeError(f"não serializável para hash: {type(obj)!r}")


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=_default)


def hash_obj(obj: Any) -> str:
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def hash_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(chunk_size):
            digest.update(block)
    return digest.hexdigest()


def hash_tree(root: Path, patterns: tuple[str, ...] = ("*",)) -> str:
    """Hash de uma árvore de arquivos (nomes + conteúdo), em ordem estável."""
    digest = hashlib.sha256()
    files: list[Path] = []
    for pattern in patterns:
        files.extend(p for p in root.rglob(pattern) if p.is_file())
    for path in sorted(set(files)):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(hash_file(path).encode("ascii"))
    return digest.hexdigest()


def seed_from(*parts: Any) -> int:
    """Semente determinística (31 bits) a partir de qualquer combinação de valores."""
    return int(hash_obj(list(parts))[:8], 16) & 0x7FFFFFFF
