"""Armazenamento de artefatos endereçados pelo hash das entradas (cache do grafo)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.models import Artifact


class ArtifactStore:
    def __init__(self) -> None:
        self.settings = get_settings()

    def dir_for(self, kind: str, input_hash: str) -> Path:
        path = self.settings.cache_dir / kind / input_hash[:2] / input_hash
        path.mkdir(parents=True, exist_ok=True)
        return path

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.settings.data_dir.resolve()).as_posix()

    def abs(self, rel: str) -> Path:
        return (self.settings.data_dir / rel).resolve()

    def lookup(self, kind: str, input_hash: str) -> dict[str, Any] | None:
        with session_scope() as s:
            row = s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).one_or_none()
            if row is None or row.status != "done":
                return None
            output = dict(row.output or {})
        for rel in output.get("files", []):
            if not self.abs(rel).exists():
                return None
        return output

    def save(self, kind: str, input_hash: str, output: dict[str, Any], provider: str | None = None, cost: float = 0.0) -> None:
        with session_scope() as s:
            row = s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).one_or_none()
            if row is None:
                row = Artifact(kind=kind, input_hash=input_hash)
                s.add(row)
            row.status = "done"
            row.output = output
            row.provider = provider
            row.cost_usd = cost
            row.error = None

    def fail(self, kind: str, input_hash: str, error: str) -> None:
        with session_scope() as s:
            row = s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).one_or_none()
            if row is None:
                row = Artifact(kind=kind, input_hash=input_hash)
                s.add(row)
            row.status = "failed"
            row.error = error[:4000]

    def forget(self, kind: str, input_hash: str) -> None:
        with session_scope() as s:
            s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).delete()
        shutil.rmtree(self.settings.cache_dir / kind / input_hash[:2] / input_hash, ignore_errors=True)
