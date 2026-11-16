"""Limpeza do cache de artefatos (marcar e varrer).

Marca o que está protegido:

- o que a versão atual de cada projeto usa: narrações, alinhamentos e imagens calculados pelo documento (prévia e
  vídeo final), com ou sem build salvo — um render que gravou as narrações pagas e falhou depois não perde nada;
- o mesmo para a cabeça de cada ramo A/B aberto (ramo fechado não conta: o promovido já passou o resultado para a
  principal; mesclado e descartado não voltam);
- o que as últimas prévias/renders de cada projeto e de cada ramo aberto usam (manifesto `artifacts` do build; para
  builds antigos sem manifesto, o que a timeline salva cita e os blocos recalculados a partir dela);
- o que cada publicação usou (manifesto gravado em `Publication.details["artifacts"]`); publicação de antes do
  manifesto protege as narrações e imagens criadas até ela (o vídeo só pode ter usado essas);
- tudo o que foi criado ou usado nas últimas 24 h (protege builds em andamento).

E varre, em ordem:

1. blocos de render, vídeos finais, mixagens, narrações aceleradas (Modo B), camadas de parallax e imagens gradadas
   por LUT que nada usa mais (refazê-los é só tempo de máquina; cada troca de LUT ou de intensidade gera uma cópia
   nova de todas as imagens);
2. registros com falha, registros cujos arquivos sumiram e pastas sem registro;
3. bundles de versões antigas do renderer e temporários;
4. (opcional, `old_versions`) narrações, alinhamentos e imagens de versões antigas que nada protegido usa —
   refazê-las custa cota ou dinheiro, por isso só com a opção ligada;
5. (opcional, `limit_bytes`) se o cache ainda passa do limite, apaga pelo último uso (LRU) o que não está
   protegido: primeiro o que é barato refazer (`lru`), e só depois narrações e imagens (`lru_costly`).

Narrações e imagens (clipes de vídeo IA incluídos) só saem nos passos 4 e 5. Se a versão atual de algum projeto (ou
ramo aberto) não dá para ler, não dá para saber quais narrações e imagens ele usa: nesse caso elas não são apagadas
(o relatório avisa).

Na limpeza de verdade, a fase de apagar segura `store.CACHE_LOCK` (os builds deste processo esperam) e confere de
novo no banco o que foi usado depois do retrato inicial: isso fica.

A política (limite de espaço, versões antigas, limpeza automática) fica em `AppSetting["cache_policy"]`.
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.core.graph.store import CACHE_LOCK
from studio.db import session_scope
from studio.models import AppSetting, Artifact, Project, ProjectBranch, Publication

log = logging.getLogger(__name__)

SWEEP_KINDS = ("chunk", "final", "mix", "dub", "parallax", "lut")
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
    "dub": "narrações aceleradas sem uso (faixas de idioma)",
    "parallax": "camadas de parallax sem uso",
    "lut": "imagens com LUT de cor sem uso",
    "failed": "artefatos com falha",
    "missing": "registros sem arquivo",
    "orphan": "pastas sem registro",
    "bundles": "versões antigas do renderer",
    "tmp": "temporários",
    "old_voice": "narrações de versões antigas",
    "old_image": "imagens de versões antigas",
    "lru": "usados há mais tempo (limite de espaço)",
    "lru_costly": "narrações e imagens usadas há mais tempo (limite de espaço)",
}
# Categorias que apagam narrações e imagens (refazer gasta cota): a interface avisa antes de confirmar.
COSTLY_CATEGORIES = ("old_voice", "old_image", "lru_costly")


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
    unreadable_projects: list[str] = field(default_factory=list)
    legacy_publications: list[str] = field(default_factory=list)
    legacy_until: datetime | None = None
    aborted: bool = False

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
            "costly_categories": list(COSTLY_CATEGORIES),
            "old_versions": self.old_versions,
            "limit": self.limit,
            "total_after": self.total_after,
            "over_limit": self.limit is not None and self.total_after > self.limit,
            "unreadable_projects": self.unreadable_projects,
            "legacy_publications": self.legacy_publications,
            "legacy_until": self.legacy_until.isoformat() if self.legacy_until else None,
            "aborted": self.aborted,
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


def _cache_paths(value: Any, used: set[tuple[str, str]]) -> None:
    """Junta todo caminho do cache (cache/<tipo>/<hh>/<hash>/...) citado num JSON (timeline de build antigo)."""
    if isinstance(value, str):
        found = _artifact_of(value) if value.startswith("cache/") else None
        if found:
            used.add(found)
    elif isinstance(value, dict):
        for item in value.values():
            _cache_paths(item, used)
    elif isinstance(value, list):
        for item in value:
            _cache_paths(item, used)


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


@dataclass
class _Protection:
    used: set[tuple[str, str]] = field(default_factory=set)
    # projetos cuja versão atual não deu para ler: sem saber o que usam, nenhuma narração ou imagem sai
    unreadable: list[str] = field(default_factory=list)
    # publicações de antes do manifesto: narrações e imagens criadas até a mais recente delas ficam
    legacy_publications: list[str] = field(default_factory=list)
    costly_before: datetime | None = None


def _protected() -> _Protection:
    """O que não pode sair: o que a versão atual de cada projeto e a cabeça de cada ramo A/B aberto usam (calculado
    pelo documento, com ou sem build salvo), o que as últimas prévias/renders usaram (manifesto; build antigo sem manifesto: recalculado pela
    timeline salva) e o que cada publicação usou."""
    from studio.core.project.branches import branch_doc
    from studio.core.timeline import chunk_timeline
    from studio.media.render import renderer_version
    from studio.workers.pipeline.build import current_artifact_keys

    settings = get_settings()
    prot = _Protection()
    with session_scope() as s:
        projects = [(p.id, p.title, p.settings or {}) for p in s.query(Project).all()]
        branches = [(b.project_id, b.name, {"last_preview": b.last_preview, "last_final": b.last_final})
                    for b in s.query(ProjectBranch).filter(ProjectBranch.status == "aberto").all()]
        pubs = [(pub.project_id, pub.platform, _aware(pub.created_at), (pub.details or {}).get("artifacts"))
                for pub in s.query(Publication).all()]
    titles = {pid: title for pid, title, _ in projects}
    # Principal de cada projeto e cabeça de cada ramo A/B aberto (ramo fechado não conta: o promovido já passou o
    # resultado para a principal; mesclado e descartado não voltam).
    lines = [(pid, None, title, st) for pid, title, st in projects]
    lines += [(pid, name, f"{titles.get(pid, pid)} (ramo {name})", st) for pid, name, st in branches]
    for pid, branch, title, project_settings in lines:
        try:
            prot.used |= current_artifact_keys(pid, None if branch is None else branch_doc(pid, branch))
        except Exception as exc:  # noqa: BLE001 - na dúvida, não apaga narração nem imagem
            log.warning("não deu para calcular o que a versão atual de %s usa: %s", title, exc)
            prot.unreadable.append(title)
        for key in ("last_preview", "last_final"):
            result = project_settings.get(key)
            if not isinstance(result, dict):
                continue
            manifest = result.get("artifacts")
            if isinstance(manifest, list):
                _parse_manifest(manifest, prot.used)
                continue
            # Build antigo, sem manifesto: o que a timeline salva cita (narrações, imagens) e os blocos dela.
            for entry in (result.get("langs") or {}).values():
                if not isinstance(entry, dict):
                    continue
                found = _artifact_of(entry.get("mix"))
                if found:
                    prot.used.add(found)
                timeline_path = settings.data_dir / str(entry.get("timeline") or "")
                if timeline_path.is_file():
                    try:
                        timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
                        _cache_paths(timeline, prot.used)
                        for chunk in chunk_timeline(timeline, renderer_version()):
                            prot.used.add(("chunk", chunk["key"]))
                    except (ValueError, KeyError, TypeError) as exc:
                        log.warning("timeline ilegível em %s: %s", timeline_path, exc)
    for pid, platform, created, manifest in pubs:
        if isinstance(manifest, list):
            _parse_manifest(manifest, prot.used)
            continue
        # Publicação de antes do manifesto: não dá para saber o que o vídeo usou, mas foi tudo criado antes dela.
        prot.legacy_publications.append(f"{titles.get(pid, pid)} ({platform})")
        prot.costly_before = max(prot.costly_before or created, created)
    return prot


def in_use() -> set[tuple[str, str]]:
    """Artefatos usados pelas versões atuais, pelas últimas prévias e renders e pelas publicações."""
    return _protected().used


# --------------------------------------------------------------------------------------------
# Limpeza
# --------------------------------------------------------------------------------------------


@dataclass
class _Row:
    id: int
    kind: str
    folder: Path
    last_used: datetime


@dataclass
class _Doomed:
    category: str
    size: int
    path: Path
    row_id: int | None = None


def collect(dry_run: bool = True, now: datetime | None = None, *, old_versions: bool = False,
            limit_bytes: int | None = None, abort: Callable[[], bool] | None = None) -> CacheReport:
    """Calcula (dry_run) ou executa a limpeza. Retorna o relatório por categoria.

    `old_versions` também apaga narrações e imagens que nada protegido usa; `limit_bytes` apaga pelo último
    uso até o cache caber no limite. `abort` (só na limpeza de verdade) é chamado logo antes de apagar, com a
    trava do cache: se devolver True, nada é apagado (ex.: um build entrou na fila enquanto a limpeza calculava).
    """
    from studio.media.render import renderer_version

    settings = get_settings()
    snapshot = datetime.now(UTC)  # o que for usado depois deste instante não sai (build que começou no meio)
    now = now or snapshot
    cutoff = now - GRACE
    prot = _protected()
    used = prot.used
    sizes = cache_sizes()
    report = CacheReport(dry_run=dry_run, sizes=sizes, old_versions=old_versions, limit=limit_bytes,
                         unreadable_projects=prot.unreadable, legacy_publications=prot.legacy_publications,
                         legacy_until=prot.costly_before)

    def costly_kept(kind: str, created: datetime) -> bool:
        if kind not in COSTLY_KINDS:
            return False
        if prot.unreadable:
            return True
        return prot.costly_before is not None and created <= prot.costly_before

    doomed: list[_Doomed] = []
    survivors: list[_Row] = []

    # 1) registros do banco
    with session_scope() as s:
        rows = s.query(Artifact).all()
        known = {(r.kind, r.input_hash) for r in rows}
        for row in rows:
            key = (row.kind, row.input_hash)
            folder = settings.cache_dir / row.kind / row.input_hash[:2] / row.input_hash
            last_used = max(_aware(row.updated_at), _aware(row.last_used_at))
            kept = costly_kept(row.kind, _aware(row.created_at))
            recent = last_used > cutoff
            files = (row.output or {}).get("files", []) if row.status == "done" else []
            category: str | None = None
            if row.status == "failed" and not recent:
                category = "failed"
            elif row.status == "done" and any(not (settings.data_dir / f).exists() for f in files):
                category = "missing"
            elif row.kind in SWEEP_KINDS and row.status == "done" and key not in used and not recent:
                category = row.kind
            elif (old_versions and row.kind in OLD_VERSION_KINDS and row.status == "done" and key not in used
                  and not recent and not kept):
                category = COSTLY_KINDS[row.kind]
            if category is None:
                if key in used:
                    report.kept_in_use += 1
                elif row.status == "done" and not recent and not kept:
                    survivors.append(_Row(row.id, row.kind, folder, last_used))
                continue
            doomed.append(_Doomed(category, _dir_size(folder), folder, row.id))

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
                    doomed.append(_Doomed("orphan", _dir_size(folder), folder))

    # 3) bundles de versões antigas do renderer
    bundles = settings.data_dir / "renderer-bundle"
    if bundles.is_dir():
        current = renderer_version()
        for folder in bundles.iterdir():
            if folder.name == current or _mtime(folder) > cutoff:
                continue
            doomed.append(_Doomed("bundles", _dir_size(folder), folder))

    # 4) temporários
    if settings.tmp_dir.is_dir():
        for item in settings.tmp_dir.iterdir():
            if _mtime(item) > cutoff:
                continue
            doomed.append(_Doomed("tmp", _dir_size(item), item))

    # 5) limite de espaço: pelo último uso, o barato de refazer primeiro
    total_after = sum(sizes.values()) - sum(d.size for d in doomed)
    if limit_bytes is not None and total_after > limit_bytes:
        survivors.sort(key=lambda r: (r.kind in COSTLY_KINDS, r.last_used))
        for cand in survivors:
            if total_after <= limit_bytes:
                break
            size = _dir_size(cand.folder)
            total_after -= size
            category = "lru_costly" if cand.kind in COSTLY_KINDS else "lru"
            doomed.append(_Doomed(category, size, cand.folder, cand.id))

    if dry_run:
        for item in doomed:
            report.add(item.category, item.size)
        report.total_after = max(0, total_after)
        return report

    _remove(doomed, snapshot, report, abort)
    report.sizes = cache_sizes()
    report.total_after = sum(report.sizes.values())
    if not report.aborted:
        log.info("limpeza do cache: %s", dict(report.freed))
    return report


def _remove(doomed: list[_Doomed], snapshot: datetime, report: CacheReport,
            abort: Callable[[], bool] | None) -> None:
    """Apaga com a trava do cache (nenhum build deste processo acha nem grava artefato no meio), conferindo de novo
    no banco: o que um build (ou outro processo, como o `videos-studio clean`) usou depois do retrato fica."""
    with CACHE_LOCK:
        if abort is not None and abort():
            report.aborted = True
            return
        row_ids = [d.row_id for d in doomed if d.row_id is not None]
        touched: set[int] = set()
        with session_scope() as s:
            for start in range(0, len(row_ids), 500):
                for row in s.query(Artifact).filter(Artifact.id.in_(row_ids[start:start + 500])):
                    if max(_aware(row.updated_at), _aware(row.last_used_at)) > snapshot:
                        touched.add(row.id)
            known_now = set(s.query(Artifact.kind, Artifact.input_hash).all())
        gone: list[int] = []
        for item in doomed:
            if item.row_id is not None and item.row_id in touched:
                continue  # usado durante a limpeza
            if item.row_id is None and item.path.exists():
                if _mtime(item.path) > snapshot:
                    continue  # pasta mexida durante a limpeza
                if item.category == "orphan" and (item.path.parent.parent.name, item.path.name) in known_now:
                    continue  # ganhou registro durante a limpeza
            if item.path.is_dir():
                shutil.rmtree(item.path, ignore_errors=True)
            else:
                item.path.unlink(missing_ok=True)
            if item.row_id is not None:
                gone.append(item.row_id)
            report.add(item.category, item.size)
        if gone:
            with session_scope() as s:
                for start in range(0, len(gone), 500):
                    s.query(Artifact).filter(Artifact.id.in_(gone[start:start + 500])).delete(
                        synchronize_session=False)
    if touched:
        log.info("limpeza do cache: %d artefatos usados durante a limpeza ficaram", len(touched))


def busy_jobs(exclude: str | None = None) -> bool:
    from studio.workers.jobs import list_jobs

    return any(j["status"] in ("queued", "running") and j["id"] != exclude for j in list_jobs(limit=200))


def auto_clean(exclude_job: str | None = None) -> CacheReport | None:
    """Limpeza automática (depois de cada build e ao abrir o app): só com a opção ligada, só quando o cache passa
    do limite e só sem outras tarefas na fila (conferido antes de calcular e de novo logo antes de apagar). Nunca
    levanta erro (é uma rotina de fundo)."""
    try:
        policy = get_policy()
        limit = policy.limit_bytes
        if not policy.auto or limit is None:
            return None
        if sum(cache_sizes().values()) <= limit or busy_jobs(exclude=exclude_job):
            return None
        report = collect(dry_run=False, old_versions=policy.old_versions, limit_bytes=limit,
                         abort=lambda: busy_jobs(exclude=exclude_job))
        if report.aborted:
            log.info("limpeza automática do cache adiada: uma tarefa entrou na fila enquanto ela calculava")
            return None
        log.info("limpeza automática do cache: %d bytes liberados", sum(report.freed.values()))
        return report
    except Exception:  # noqa: BLE001 - rotina de fundo: registra e segue
        log.exception("falha na limpeza automática do cache")
        return None
