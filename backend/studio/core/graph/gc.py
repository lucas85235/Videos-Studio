"""Limpeza do cache de artefatos (marcar e varrer).

Marca o que as últimas prévias/renders de cada projeto usam (manifesto `artifacts` do build; para builds
antigos sem manifesto, recalcula os blocos a partir da timeline salva) e varre:

- blocos de render, vídeos finais, mixagens e narrações aceleradas (Modo B) que nenhum projeto usa mais (refazê-los é só tempo de máquina);
- registros com falha, registros cujos arquivos sumiram e pastas sem registro;
- bundles de versões antigas do renderer e temporários.

Narrações, imagens, alinhamentos, trilhas e thumbnails nunca são apagados aqui: custam cota ou dinheiro para
refazer, e versões antigas do projeto podem precisar deles. Tudo o que foi criado nas últimas 24 h é preservado
(protege builds em andamento).
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.models import Artifact, Project

log = logging.getLogger(__name__)

SWEEP_KINDS = ("chunk", "final", "mix", "dub")
GRACE = timedelta(hours=24)

CATEGORY_LABELS = {
    "chunk": "blocos de render sem uso",
    "final": "vídeos finais sem uso (cópias do cache)",
    "mix": "mixagens sem uso",
    "dub": "narrações aceleradas sem uso (faixas de idioma)",
    "failed": "artefatos com falha",
    "missing": "registros sem arquivo",
    "orphan": "pastas sem registro",
    "bundles": "versões antigas do renderer",
    "tmp": "temporários",
}


@dataclass
class CacheReport:
    dry_run: bool
    sizes: dict[str, int]
    removed: dict[str, int] = field(default_factory=dict)
    freed: dict[str, int] = field(default_factory=dict)
    kept_in_use: int = 0

    def add(self, category: str, size: int) -> None:
        self.removed[category] = self.removed.get(category, 0) + 1
        self.freed[category] = self.freed.get(category, 0) + size

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "sizes": self.sizes,
            "total": sum(self.sizes.values()),
            "removed": self.removed,
            "freed": self.freed,
            "freed_total": sum(self.freed.values()),
            "kept_in_use": self.kept_in_use,
            "labels": CATEGORY_LABELS,
        }


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def _aware(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=UTC)
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)


def _artifact_of(rel: str | None) -> tuple[str, str] | None:
    """(tipo, hash) a partir de um caminho relativo do cache: cache/<tipo>/<hh>/<hash>/arquivo."""
    if not rel:
        return None
    parts = Path(rel).parts
    if len(parts) >= 4 and parts[0] == "cache":
        return parts[1], parts[3]
    return None


def cache_sizes() -> dict[str, int]:
    settings = get_settings()
    sizes: dict[str, int] = {}
    if settings.cache_dir.is_dir():
        for kind_dir in sorted(settings.cache_dir.iterdir()):
            if kind_dir.is_dir():
                sizes[kind_dir.name] = _dir_size(kind_dir)
    sizes["bundles"] = _dir_size(settings.data_dir / "renderer-bundle")
    sizes["tmp"] = _dir_size(settings.tmp_dir)
    return sizes


def in_use() -> set[tuple[str, str]]:
    """Artefatos usados pelas últimas prévias e renders de todos os projetos."""
    from studio.core.timeline import chunk_timeline
    from studio.media.render import renderer_version

    settings = get_settings()
    used: set[tuple[str, str]] = set()
    with session_scope() as s:
        results = [
            (p.settings or {}).get(key)
            for p in s.query(Project).all()
            for key in ("last_preview", "last_final")
        ]
    for result in results:
        if not isinstance(result, dict):
            continue
        manifest = result.get("artifacts")
        if isinstance(manifest, list):
            for item in manifest:
                kind, _, h = str(item).partition(":")
                if h:
                    used.add((kind, h))
            continue
        # Build antigo, sem manifesto: recalcula blocos e mix pela timeline salva.
        for entry in (result.get("langs") or {}).values():
            if not isinstance(entry, dict):
                continue
            for rel in (entry.get("mix"),):
                found = _artifact_of(rel)
                if found:
                    used.add(found)
            timeline_path = settings.data_dir / str(entry.get("timeline") or "")
            if timeline_path.is_file():
                try:
                    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
                    for chunk in chunk_timeline(timeline, renderer_version()):
                        used.add(("chunk", chunk["key"]))
                except (ValueError, KeyError, TypeError) as exc:
                    log.warning("timeline ilegível em %s: %s", timeline_path, exc)
    return used


def collect(dry_run: bool = True, now: datetime | None = None) -> CacheReport:
    """Calcula (dry_run) ou executa a limpeza. Retorna o relatório por categoria."""
    from studio.media.render import renderer_version

    settings = get_settings()
    now = now or datetime.now(UTC)
    cutoff = now - GRACE
    used = in_use()
    report = CacheReport(dry_run=dry_run, sizes=cache_sizes())

    # 1) registros do banco
    with session_scope() as s:
        rows = s.query(Artifact).all()
        known = {(r.kind, r.input_hash) for r in rows}
        for row in rows:
            key = (row.kind, row.input_hash)
            folder = settings.cache_dir / row.kind / row.input_hash[:2] / row.input_hash
            recent = _aware(row.updated_at) > cutoff
            files = (row.output or {}).get("files", []) if row.status == "done" else []
            category: str | None = None
            if row.status == "failed" and not recent:
                category = "failed"
            elif row.status == "done" and any(not (settings.data_dir / f).exists() for f in files):
                category = "missing"
            elif row.kind in SWEEP_KINDS and row.status == "done" and key not in used and not recent:
                category = row.kind
            if category is None:
                if key in used:
                    report.kept_in_use += 1
                continue
            report.add(category, _dir_size(folder))
            if not dry_run:
                shutil.rmtree(folder, ignore_errors=True)
                s.delete(row)

    # 2) pastas do cache sem registro no banco
    if settings.cache_dir.is_dir():
        for kind_dir in settings.cache_dir.iterdir():
            if not kind_dir.is_dir():
                continue
            for bucket in kind_dir.iterdir():
                if not bucket.is_dir():
                    continue
                for folder in bucket.iterdir():
                    if (kind_dir.name, folder.name) in known or _mtime(folder) > cutoff:
                        continue
                    report.add("orphan", _dir_size(folder))
                    if not dry_run:
                        shutil.rmtree(folder, ignore_errors=True)

    # 3) bundles de versões antigas do renderer
    bundles = settings.data_dir / "renderer-bundle"
    if bundles.is_dir():
        current = renderer_version()
        for folder in bundles.iterdir():
            if folder.name == current or _mtime(folder) > cutoff:
                continue
            report.add("bundles", _dir_size(folder))
            if not dry_run:
                shutil.rmtree(folder, ignore_errors=True)

    # 4) temporários
    if settings.tmp_dir.is_dir():
        for item in settings.tmp_dir.iterdir():
            if _mtime(item) > cutoff:
                continue
            report.add("tmp", _dir_size(item))
            if not dry_run:
                if item.is_dir():
                    shutil.rmtree(item, ignore_errors=True)
                else:
                    item.unlink(missing_ok=True)

    if not dry_run:
        report.sizes = cache_sizes()
        log.info("limpeza do cache: %s", {k: v for k, v in report.freed.items()})
    return report
