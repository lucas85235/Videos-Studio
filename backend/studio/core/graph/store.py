"""Armazenamento de artefatos endereçados pelo hash das entradas (cache do grafo)."""

from __future__ import annotations

import shutil
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.models import Artifact

# Gravar o último uso a cada leitura custaria uma escrita por narração/imagem; basta a precisão de minutos.
TOUCH_EVERY = timedelta(minutes=10)

# Trava entre a limpeza do cache e os builds deste processo: enquanto a limpeza confere e apaga, nenhum build acha
# (ou grava) um artefato que está saindo. Ver `core/graph/gc.py`.
CACHE_LOCK = threading.RLock()


def _stale(last: datetime | None, now: datetime) -> bool:
    if last is None:
        return True
    last = last if last.tzinfo else last.replace(tzinfo=UTC)
    return now - last > TOUCH_EVERY


class ArtifactStore:
    def __init__(self) -> None:
        self.settings = get_settings()
        # (tipo, hash) de tudo o que foi lido ou gravado: vira o manifesto do build (usado na limpeza do cache).
        self.touched: set[tuple[str, str]] = set()

    def dir_for(self, kind: str, input_hash: str) -> Path:
        path = self.settings.cache_dir / kind / input_hash[:2] / input_hash
        path.mkdir(parents=True, exist_ok=True)
        return path

    def rel(self, path: Path) -> str:
        return path.resolve().relative_to(self.settings.data_dir.resolve()).as_posix()

    def abs(self, rel: str) -> Path:
        return (self.settings.data_dir / rel).resolve()

    def peek(self, kind: str, input_hash: str) -> dict[str, Any] | None:
        """A saída guardada, sem marcar uso nem conferir os arquivos (só para contas, como a da limpeza)."""
        with session_scope() as s:
            row = s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).one_or_none()
            return dict(row.output or {}) if row is not None and row.status == "done" else None

    def lookup(self, kind: str, input_hash: str) -> dict[str, Any] | None:
        with CACHE_LOCK, session_scope() as s:
            row = s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).one_or_none()
            if row is None or row.status != "done":
                return None
            output = dict(row.output or {})
            missing = any(not self.abs(rel).exists() for rel in output.get("files", []))
            now = datetime.now(UTC)
            if not missing and _stale(row.last_used_at, now):
                # Só o último uso: `updated_at` continua dizendo quando o conteúdo mudou.
                s.query(Artifact).filter_by(id=row.id).update(
                    {"last_used_at": now, "updated_at": row.updated_at}, synchronize_session=False)
        if missing:
            return None
        self.touched.add((kind, input_hash))
        return output

    def save(self, kind: str, input_hash: str, output: dict[str, Any], provider: str | None = None, cost: float = 0.0) -> None:
        with CACHE_LOCK, session_scope() as s:
            row = s.query(Artifact).filter_by(kind=kind, input_hash=input_hash).one_or_none()
            if row is None:
                row = Artifact(kind=kind, input_hash=input_hash)
                s.add(row)
            row.status = "done"
            row.output = output
            row.provider = provider
            row.cost_usd = cost
            row.error = None
            row.last_used_at = datetime.now(UTC)
        self.touched.add((kind, input_hash))

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
