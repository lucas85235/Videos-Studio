"""Fila de jobs dentro do processo (ADR 0001): persistida no banco, com progresso por eventos."""

from __future__ import annotations

import logging
import threading
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

from studio.db import session_scope
from studio.events import bus
from studio.ids import new_id
from studio.models import Job

log = logging.getLogger(__name__)

JobFn = Callable[["JobContext", dict[str, Any]], dict[str, Any] | None]
_REGISTRY: dict[str, JobFn] = {}

ACTIVE_STATUSES = ("queued", "running")


def job(kind: str) -> Callable[[JobFn], JobFn]:
    def decorator(fn: JobFn) -> JobFn:
        _REGISTRY[kind] = fn
        return fn

    return decorator


def job_dict(row: Job) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "project_id": row.project_id,
        "status": row.status,
        "progress": round(row.progress or 0.0, 4),
        "message": row.message,
        "payload": row.payload,
        "result": row.result,
        "error": row.error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
    }


class JobContext:
    def __init__(self, job_id: str, project_id: str | None, cancel: threading.Event) -> None:
        self._job_id = job_id
        self.project_id = project_id
        self._cancel = cancel
        self._last_saved = 0.0

    @property
    def job_id(self) -> str:
        return self._job_id

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def progress(self, fraction: float, message: str = "") -> None:
        fraction = max(0.0, min(1.0, fraction))
        with session_scope() as s:
            row = s.get(Job, self._job_id)
            if row is None:
                return
            row.progress = fraction
            if message:
                row.message = message
            data = job_dict(row)
        bus.publish({"type": "job", "job": data})


class JobRunner:
    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._cancel: dict[str, threading.Event] = {}
        self._lock = threading.Lock()

    def recover(self) -> None:
        """Jobs que estavam rodando quando o app fechou são marcados como interrompidos."""
        with session_scope() as s:
            for row in s.query(Job).filter(Job.status.in_(ACTIVE_STATUSES)).all():
                row.status = "failed"
                row.error = "interrompido (o app foi fechado durante a execução)"
                row.finished_at = datetime.now(UTC)

    def submit(self, kind: str, payload: dict[str, Any] | None = None, project_id: str | None = None,
               *, run_inline: bool = False) -> dict[str, Any]:
        if kind not in _REGISTRY:
            raise ValueError(f"tipo de job desconhecido: {kind}")
        payload = payload or {}
        job_id = new_id("job")
        with session_scope() as s:
            row = Job(id=job_id, kind=kind, project_id=project_id, status="queued", payload=payload, message="Na fila")
            s.add(row)
            s.flush()
            data = job_dict(row)
        bus.publish({"type": "job", "job": data})
        cancel = threading.Event()
        with self._lock:
            self._cancel[job_id] = cancel
        if run_inline:
            self._run(job_id, kind, payload, project_id, cancel)
        else:
            self._pool.submit(self._run, job_id, kind, payload, project_id, cancel)
        return data

    def cancel(self, job_id: str) -> bool:
        with self._lock:
            event = self._cancel.get(job_id)
        if event is None:
            return False
        event.set()
        with session_scope() as s:
            row = s.get(Job, job_id)
            if row and row.status == "queued":
                row.status = "canceled"
                row.finished_at = datetime.now(UTC)
        return True

    def _run(self, job_id: str, kind: str, payload: dict[str, Any], project_id: str | None, cancel: threading.Event) -> None:
        if cancel.is_set():
            return
        with session_scope() as s:
            row = s.get(Job, job_id)
            assert row is not None
            row.status = "running"
            row.started_at = datetime.now(UTC)
            row.message = "Executando"
            data = job_dict(row)
        bus.publish({"type": "job", "job": data})
        ctx = JobContext(job_id, project_id, cancel)
        status, result, error = "done", None, None
        try:
            result = _REGISTRY[kind](ctx, payload) or {}
            if cancel.is_set():
                status = "canceled"
        except Exception as exc:  # noqa: BLE001 - erro vai para o job
            from studio.workers.pipeline.build import BuildCancelled

            if isinstance(exc, BuildCancelled):
                status = "canceled"
            else:
                status = "failed"
                error = str(exc) or exc.__class__.__name__
                log.error("job %s (%s) falhou: %s\n%s", job_id, kind, exc, traceback.format_exc())
        with session_scope() as s:
            row = s.get(Job, job_id)
            assert row is not None
            row.status = status
            row.result = result or {}
            row.error = error
            row.finished_at = datetime.now(UTC)
            if status == "done":
                row.progress = 1.0
                row.message = "Concluído"
            elif status == "canceled":
                row.message = "Cancelado"
            else:
                row.message = "Falhou"
            data = job_dict(row)
        with self._lock:
            self._cancel.pop(job_id, None)
        bus.publish({"type": "job", "job": data})
        if project_id:
            bus.publish({"type": "project", "project_id": project_id, "reason": f"job:{kind}:{status}"})


_runner: JobRunner | None = None


def get_runner() -> JobRunner:
    global _runner
    if _runner is None:
        _runner = JobRunner()
    return _runner


def list_jobs(project_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with session_scope() as s:
        query = s.query(Job)
        if project_id:
            query = query.filter(Job.project_id == project_id)
        return [job_dict(r) for r in query.order_by(Job.created_at.desc()).limit(limit).all()]


def get_job(job_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.get(Job, job_id)
        return job_dict(row) if row else None


# Registra os jobs do pipeline
from studio.workers import tasks  # noqa: E402,F401
