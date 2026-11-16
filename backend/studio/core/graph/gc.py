"""Limpeza do cache de artefatos (marcar e varrer).

Marca o que está protegido:

- o que as últimas prévias/renders de cada projeto usam (manifesto `artifacts` do build; para builds antigos sem
  manifesto, recalcula os blocos a partir da timeline salva) — são as versões atuais;
- o que cada publicação usou (manifesto gravado em `Publication.details["artifacts"]`);
- tudo o que foi criado ou usado nas últimas 24 h (protege builds em andamento).

E varre, em ordem:

1. blocos de render, vídeos finais e mixagens que nada usa mais (refazê-los é só tempo de máquina);
2. registros com falha, registros cujos arquivos sumiram e pastas sem registro;
3. bundles de versões antigas do renderer e temporários;
4. (opcional, `old_versions`) narrações, alinhamentos e imagens de versões antigas que nada protegido usa —
   refazê-las custa cota ou dinheiro, por isso só com a opção ligada;
5. (opcional, `limit_bytes`) se o cache ainda passa do limite, apaga pelo último uso (LRU) o que não está
   protegido: primeiro o que é barato refazer, e só depois narrações e imagens.

Narrações e imagens só saem nos passos 4 e 5. Se algum projeto tem build antigo sem manifesto, não dá para saber
quais narrações e imagens ele usa: nesse caso elas não são apagadas (o relatório avisa).

A política (limite de espaço, versões antigas, limpeza automática) fica em `AppSetting["cache_policy"]`.
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
from studio.models import AppSetting, Artifact, Project, Publication

log = logging.getLogger(__name__)

SWEEP_KINDS = ("chunk", "final", "mix")
# Refazer custa cota ou dinheiro: só sai com a opção de versões antigas ou, por último, pelo limite de espaço.
COSTLY_KINDS = {"tts": "old_voice", "align": "old_voice", "image": "old_image", "media_search": "old_image"}
OLD_VERSION_KINDS = ("tts", "align", "image")
GRACE = timedelta(hours=24)
GB = 1024**3
POLICY_KEY = "cache_policy"

CATEGORY_LABELS = {
    "chunk": "blocos de render sem uso",
    "final": "vídeos finais sem uso (cópias do cache)",
    "mix": "mixagens sem uso",
    "failed": "artefatos com falha",
    "missing": "registros sem arquivo",
    "orphan": "pastas sem registro",
    "bundles": "versões antigas do renderer",
    "tmp": "temporários",
    "old_voice": "narrações de versões antigas",
    "old_image": "imagens de versões antigas",
    "lru": "usados há mais tempo (limite de espaço)",
}


# --------------------------------------------------------------------------------------------
# Política
# --------------------------------------------------------------------------------------------


@dataclass
class CachePolicy:
    limit_gb: float = 0.0  # 0 = sem limite
    old_versions: bool = False  # a limpeza automática também apaga narrações/imagens de versões antigas
    auto: bool = False  # limpa sozinho depois de cada build e ao abrir o app, quando passa do limite

    @property
    def limit_bytes(self) -> int | None:
        return int(self.limit_gb * GB) if self.limit_gb > 0 else None

    def to_dict(self) -> dict[str, Any]:
        return {"limit_gb": self.limit_gb, "old_versions": self.old_versions, "auto": self.auto}


def get_policy() -> CachePolicy:
    with session_scope() as s:
        row = s.get(AppSetting, POLICY_KEY)
        value = row.value if row and isinstance(row.value, dict) else {}
    try:
        limit = max(0.0, float(value.get("limit_gb") or 0))
    except (TypeError, ValueError):
        limit = 0.0
    return CachePolicy(limit_gb=limit, old_versions=bool(value.get("old_versions")), auto=bool(value.get("auto")))


def set_policy(policy: CachePolicy) -> CachePolicy:
    with session_scope() as s:
        row = s.get(AppSetting, POLICY_KEY)
        if row is None:
            s.add(AppSetting(key=POLICY_KEY, value=policy.to_dict()))
        else:
            row.value = policy.to_dict()
    return policy


# --------------------------------------------------------------------------------------------
# Relatório
# --------------------------------------------------------------------------------------------


@dataclass
class CacheReport:
    dry_run: bool
    sizes: dict[str, int]
    removed: dict[str, int] = field(default_factory=dict)
    freed: dict[str, int] = field(default_factory=dict)
    kept_in_use: int = 0
    old_versions: bool = False
    limit: int | None = None
    total_after: int = 0
    legacy_projects: list[str] = field(default_factory=list)

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
            "old_versions": self.old_versions,
            "limit": self.limit,
            "total_after": self.total_after,
            "over_limit": self.limit is not None and self.total_after > self.limit,
            "legacy_projects": self.legacy_projects,
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


def _parse_manifest(manifest: Any, used: set[tuple[str, str]]) -> None:
    for item in manifest or []:
        kind, _, h = str(item).partition(":")
        if h:
            used.add((kind, h))


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


def _protected() -> tuple[set[tuple[str, str]], list[str]]:
    """Artefatos usados pelas versões atuais (últimas prévias/renders) e pelas publicações, e os projetos cujo
    último build não tem manifesto (não dá para saber que narrações e imagens eles usam)."""
    from studio.core.timeline import chunk_timeline
    from studio.media.render import renderer_version

    settings = get_settings()
    used: set[tuple[str, str]] = set()
    legacy: list[str] = []
    with session_scope() as s:
        results = [
            (p.title, (p.settings or {}).get(key))
            for p in s.query(Project).all()
            for key in ("last_preview", "last_final")
        ]
        for pub in s.query(Publication).all():
            _parse_manifest((pub.details or {}).get("artifacts"), used)
    for title, result in results:
        if not isinstance(result, dict):
            continue
        manifest = result.get("artifacts")
        if isinstance(manifest, list):
            _parse_manifest(manifest, used)
            continue
        if title not in legacy:
            legacy.append(title)
        # Build antigo, sem manifesto: recalcula blocos e mix pela timeline salva.
        for entry in (result.get("langs") or {}).values():
            if not isinstance(entry, dict):
                continue
            found = _artifact_of(entry.get("mix"))
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
    return used, legacy


def in_use() -> set[tuple[str, str]]:
    """Artefatos usados pelas últimas prévias e renders de todos os projetos e pelas publicações."""
    return _protected()[0]


# --------------------------------------------------------------------------------------------
# Limpeza
# --------------------------------------------------------------------------------------------


@dataclass
class _Row:
    id: int
    kind: str
    input_hash: str
    folder: Path
    last_used: datetime
    size: int = 0


def collect(dry_run: bool = True, now: datetime | None = None, *, old_versions: bool = False,
            limit_bytes: int | None = None) -> CacheReport:
    """Calcula (dry_run) ou executa a limpeza. Retorna o relatório por categoria.

    `old_versions` também apaga narrações e imagens que nada protegido usa; `limit_bytes` apaga pelo último
    uso até o cache caber no limite.
    """
    from studio.media.render import renderer_version

    settings = get_settings()
    now = now or datetime.now(UTC)
    cutoff = now - GRACE
    used, legacy = _protected()
    sizes = cache_sizes()
    report = CacheReport(dry_run=dry_run, sizes=sizes, old_versions=old_versions, limit=limit_bytes,
                         legacy_projects=legacy)
    costly_locked = bool(legacy)
    doomed_rows: list[int] = []
    doomed_folders: list[Path] = []
    survivors: list[_Row] = []

    # 1) registros do banco
    with session_scope() as s:
        rows = s.query(Artifact).all()
        known = {(r.kind, r.input_hash) for r in rows}
        for row in rows:
            key = (row.kind, row.input_hash)
            folder = settings.cache_dir / row.kind / row.input_hash[:2] / row.input_hash
            last_used = max(_aware(row.updated_at), _aware(row.last_used_at))
            recent = last_used > cutoff
            files = (row.output or {}).get("files", []) if row.status == "done" else []
            category: str | None = None
            if row.status == "failed" and not recent:
                category = "failed"
            elif row.status == "done" and any(not (settings.data_dir / f).exists() for f in files):
                category = "missing"
            elif row.kind in SWEEP_KINDS and row.status == "done" and key not in used and not recent:
                category = row.kind
            elif (old_versions and not costly_locked and row.kind in OLD_VERSION_KINDS and row.status == "done"
                  and key not in used and not recent):
                category = COSTLY_KINDS[row.kind]
            if category is None:
                if key in used:
                    report.kept_in_use += 1
                elif row.status == "done" and not recent:
                    survivors.append(_Row(row.id, row.kind, row.input_hash, folder, last_used))
                continue
            report.add(category, _dir_size(folder))
            doomed_rows.append(row.id)
            doomed_folders.append(folder)

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
                    doomed_folders.append(folder)

    # 3) bundles de versões antigas do renderer
    bundles = settings.data_dir / "renderer-bundle"
    if bundles.is_dir():
        current = renderer_version()
        for folder in bundles.iterdir():
            if folder.name == current or _mtime(folder) > cutoff:
                continue
            report.add("bundles", _dir_size(folder))
            doomed_folders.append(folder)

    # 4) temporários
    if settings.tmp_dir.is_dir():
        for item in settings.tmp_dir.iterdir():
            if _mtime(item) > cutoff:
                continue
            report.add("tmp", _dir_size(item))
            doomed_folders.append(item)

    # 5) limite de espaço: pelo último uso, o barato de refazer primeiro
    total_after = sum(sizes.values()) - sum(report.freed.values())
    if limit_bytes is not None and total_after > limit_bytes:
        candidates = [r for r in survivors if not (costly_locked and r.kind in COSTLY_KINDS)]
        candidates.sort(key=lambda r: (r.kind in COSTLY_KINDS, r.last_used))
        for cand in candidates:
            if total_after <= limit_bytes:
                break
            cand.size = _dir_size(cand.folder)
            report.add("lru", cand.size)
            total_after -= cand.size
            doomed_rows.append(cand.id)
            doomed_folders.append(cand.folder)
    report.total_after = max(0, total_after)

    if not dry_run:
        for path in doomed_folders:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        if doomed_rows:
            with session_scope() as s:
                for start in range(0, len(doomed_rows), 500):
                    s.query(Artifact).filter(Artifact.id.in_(doomed_rows[start:start + 500])).delete(
                        synchronize_session=False)
        report.sizes = cache_sizes()
        report.total_after = sum(report.sizes.values())
        log.info("limpeza do cache: %s", {k: v for k, v in report.freed.items()})
    return report


def busy_jobs(exclude: str | None = None) -> bool:
    from studio.workers.jobs import list_jobs

    return any(j["status"] in ("queued", "running") and j["id"] != exclude for j in list_jobs(limit=200))


def auto_clean(exclude_job: str | None = None) -> CacheReport | None:
    """Limpeza automática (depois de cada build e ao abrir o app): só com a opção ligada, só quando o cache passa
    do limite e só sem outras tarefas na fila. Nunca levanta erro (é uma rotina de fundo)."""
    try:
        policy = get_policy()
        limit = policy.limit_bytes
        if not policy.auto or limit is None:
            return None
        if sum(cache_sizes().values()) <= limit or busy_jobs(exclude=exclude_job):
            return None
        report = collect(dry_run=False, old_versions=policy.old_versions, limit_bytes=limit)
        log.info("limpeza automática do cache: %d bytes liberados", sum(report.freed.values()))
        return report
    except Exception:  # noqa: BLE001 - rotina de fundo: registra e segue
        log.exception("falha na limpeza automática do cache")
        return None
