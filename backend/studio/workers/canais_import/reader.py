"""Leitura do projeto `canais` SÓ PARA LEITURA.

Nada é escrito na pasta do canais: o `estudio/estudio.db` (e o `-wal` ou `-journal`, se houver) é copiado para uma pasta
temporária e lido de lá. Abrir o banco original, mesmo em modo leitura, pode criar os arquivos `-shm`/`-wal` ao lado dele.
O canais pode estar gravando durante a cópia: se o banco ou o `-wal` mudarem no meio, a cópia é refeita (até
`COPY_TRIES` vezes) e, se ainda assim mudarem, o relatório avisa que a leitura pode estar incompleta.
Os `canal.toml` são lidos com o `tomllib` da biblioteca padrão.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.config import get_settings

DB_RELATIVE = Path("estudio") / "estudio.db"

# Tabelas lidas do estudio.db. `execucoes` só é contada (não tem destino no Vulto).
TABLES = ("canais", "ideias", "metricas", "experimentos", "licoes", "pendencias", "receita", "videos")
COUNTED = ("execucoes", "videos")
COPY_TRIES = 3
SIDE_FILES = ("-wal", "-journal")


class CanaisError(ValueError):
    """Pasta do canais inválida (sem banco e sem canal.toml)."""


@dataclass
class CanaisChannel:
    slug: str  # id do canal no canais, ex. "plantao-vice"
    name: str
    db: dict[str, Any] = field(default_factory=dict)  # linha da tabela `canais` (se houver)
    toml: dict[str, Any] = field(default_factory=dict)  # canal.toml lido (se houver)
    toml_path: str = ""


@dataclass
class CanaisSource:
    root: Path
    db_path: Path | None
    rows: dict[str, list[dict[str, Any]]]
    counts: dict[str, int]
    channels: dict[str, CanaisChannel]
    warnings: list[str]


def default_root() -> Path:
    return get_settings().canais_dir or (Path.home() / "canais")


def _state(paths: list[Path]) -> list[tuple[int, int] | None]:
    """Tamanho e hora da última mudança de cada arquivo (None = não existe)."""
    out: list[tuple[int, int] | None] = []
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            out.append(None)
            continue
        out.append((st.st_size, st.st_mtime_ns))
    return out


def _copy_db(db_path: Path, dest: Path) -> bool:
    """Copia o banco e os arquivos ao lado dele (`-wal`, `-journal`) para `dest`. Devolve False se algum deles mudou
    durante a cópia em todas as tentativas (o canais estava gravando)."""
    sources = [db_path] + [db_path.with_name(db_path.name + suffix) for suffix in SIDE_FILES]
    for _ in range(COPY_TRIES):
        for suffix in SIDE_FILES:
            dest.with_name(dest.name + suffix).unlink(missing_ok=True)
        before = _state(sources)
        shutil.copy2(db_path, dest)
        for src, suffix in zip(sources[1:], SIDE_FILES, strict=True):
            if src.is_file() and src.stat().st_size > 0:
                shutil.copy2(src, dest.with_name(dest.name + suffix))
        if _state(sources) == before:
            return True
    return False


def _read_db(db_path: Path) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int], list[str]]:
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    counts: dict[str, int] = {t: 0 for t in COUNTED}
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="vulto-canais-") as tmp:
        copy = Path(tmp) / "estudio.db"
        if not _copy_db(db_path, copy):
            warnings.append("O canais estava gravando no banco durante a leitura: alguma mudança recente pode ter "
                            "ficado de fora. Rode a prévia de novo com o canais parado.")
        con = sqlite3.connect(copy)
        try:
            con.row_factory = sqlite3.Row
            present = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            for table in TABLES:
                if table in present:
                    rows[table] = [dict(r) for r in con.execute(f'SELECT * FROM "{table}"')]
            for table in COUNTED:
                if table in present:
                    counts[table] = int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        finally:
            con.close()
    return rows, counts, warnings


def _toml_paths(root: Path, db_rows: list[dict[str, Any]]) -> list[Path]:
    """`canal.toml` de cada canal: um nível abaixo da raiz e os caminhos da tabela `canais` que ficam dentro dela."""
    found = {p.resolve() for p in root.glob("*/canal.toml") if p.is_file()}
    base = root.resolve()
    for row in db_rows:
        raw = (row.get("toml") or "").strip()
        if not raw:
            continue
        path = Path(raw)
        try:
            path.resolve().relative_to(base)
        except ValueError:
            continue  # fora da pasta do canais: não lê
        if path.is_file():
            found.add(path.resolve())
    return sorted(found)


def read_source(root: str | Path | None = None) -> CanaisSource:
    """Lê o banco e os canal.toml do canais em `root` (padrão: `~/canais` ou `VS_CANAIS_DIR`)."""
    root = Path(root) if root else default_root()
    if not root.is_dir():
        raise CanaisError(f"pasta não encontrada: {root}")
    warnings: list[str] = []
    db_path = root / DB_RELATIVE
    rows: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
    counts: dict[str, int] = {t: 0 for t in COUNTED}
    if db_path.is_file():
        try:
            rows, counts, db_warnings = _read_db(db_path)
            warnings += db_warnings
        except sqlite3.DatabaseError as exc:
            raise CanaisError(f"não foi possível ler {db_path}: {exc}") from exc
    else:
        warnings.append(f"Sem banco em {db_path}: só os canal.toml entram.")

    channels: dict[str, CanaisChannel] = {}
    for row in rows["canais"]:
        slug = str(row.get("id") or "").strip()
        if slug:
            channels[slug] = CanaisChannel(slug=slug, name=str(row.get("nome") or slug), db=row)
    for path in _toml_paths(root, rows["canais"]):
        try:
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            warnings.append(f"canal.toml ilegível ({path}): {exc}")
            continue
        slug = str(data.get("id") or path.parent.name).strip()
        ch = channels.get(slug) or CanaisChannel(slug=slug, name=str(data.get("nome") or slug))
        ch.toml, ch.toml_path = data, str(path)
        if data.get("nome"):
            ch.name = str(data["nome"])
        channels[slug] = ch

    if db_path.is_file() is False and not channels:
        raise CanaisError(f"nada para importar em {root}: falta {DB_RELATIVE} e nenhum */canal.toml")
    return CanaisSource(root=root, db_path=db_path if db_path.is_file() else None, rows=rows, counts=counts,
                        channels=channels, warnings=warnings)
