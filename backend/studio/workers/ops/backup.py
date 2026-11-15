"""Cópia diária do banco (origem: `backup` de estudio/rotinas.py do canais).

Uma cópia por dia em `<pasta de dados>/backups/studio-AAAA-MM-DD.db`, feita pela API de backup do SQLite (segura com
o banco aberto e em modo WAL). Guarda as 14 mais novas e apaga as mais antigas.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.db import get_engine

KEEP = 14  # cópias guardadas (as mais antigas são apagadas)
PATTERN = "studio-*.db"


def backups_dir() -> Path:
    return get_settings().data_dir / "backups"


def run_backup(now: datetime | None = None, keep: int = KEEP) -> Path:
    """Copia o banco para `backups/studio-AAAA-MM-DD.db` (data local) e devolve o caminho da cópia."""
    if not get_settings().db_url.startswith("sqlite"):
        raise ValueError("a cópia do banco só funciona com SQLite")
    now = (now or datetime.now(UTC)).astimezone()
    dest = backups_dir()
    dest.mkdir(parents=True, exist_ok=True)
    target = dest / f"studio-{now:%Y-%m-%d}.db"
    raw = get_engine().raw_connection()
    try:
        source = raw.driver_connection
        assert isinstance(source, sqlite3.Connection)
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    finally:
        raw.close()
    # o nome tem a data: a ordem alfabética é a ordem do tempo
    for old in sorted(dest.glob(PATTERN))[:-keep]:
        old.unlink(missing_ok=True)
    return target


def list_backups() -> list[dict[str, Any]]:
    """Cópias guardadas, da mais nova para a mais antiga."""
    dest = backups_dir()
    if not dest.is_dir():
        return []
    out = []
    for path in sorted(dest.glob(PATTERN), reverse=True):
        stat = path.stat()
        out.append({
            "name": path.name,
            "size": stat.st_size,
            "created_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
        })
    return out
