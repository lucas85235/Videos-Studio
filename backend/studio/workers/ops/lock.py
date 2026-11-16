"""Trava das rotinas: só um processo roda as rotinas do dia por vez (o app aberto ou o comando `rotinas`).

Um arquivo `rotinas.lock` na pasta de dados guarda quem segura a trava (`app` ou `cli`), o PID, quando o processo
foi criado, quando pegou e até quando vale. A trava de outro processo é respeitada enquanto não venceu e o processo
dele está vivo; vencida ou de processo morto (app fechado à força, computador desligado), ela é tomada.

A do app vale enquanto o processo do app estiver vivo, mesmo vencida: o PC pode acordar da suspensão e disparar a
tarefa antes de o laço do app renovar a trava. A hora de criação do processo evita confundir com outro processo que
reaproveitou o mesmo PID; sem ela (sistema sem como saber), vale a validade.

O app renova a trava a cada volta do laço (5 min) com validade de 15 min; o comando de linha a pega por até 2 h.
Ler, decidir e gravar a trava acontece sob uma trava do sistema (`rotinas.lock.guard`): dois processos não saem os
dois achando que pegaram.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from studio.config import get_settings

FILE = "rotinas.lock"
APP_TTL = timedelta(minutes=15)  # o laço do app renova a cada 5 min
CLI_TTL = timedelta(hours=2)


def lock_path() -> Path:
    return get_settings().data_dir / FILE


def pid_alive(pid: int) -> bool:
    """O processo `pid` ainda existe? (no Windows, `os.kill(pid, 0)` mataria o processo: usa a API do sistema)."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_created(pid: int) -> int | None:
    """Quando o processo `pid` foi criado (número do sistema, só para comparar), ou None se não dá para saber."""
    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (ctypes.c_ulonglong() for _ in range(4))
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return int(created.value)
        finally:
            kernel32.CloseHandle(handle)
    try:  # Linux: campo 22 de /proc/<pid>/stat (depois do nome, que fica entre parênteses)
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return int(stat.rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def _same_process(pid: int, created: Any) -> bool | None:
    """O `pid` ainda é o processo que gravou a trava? None se não dá para saber."""
    if created is None:
        return None
    now = process_created(pid)
    if now is None:
        return None
    try:
        return now == int(created)
    except (TypeError, ValueError):
        return None


def read_lock() -> dict[str, Any] | None:
    try:
        data = json.loads(lock_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _expires(data: dict[str, Any]) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(data.get("expires_at")))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def holder(now: datetime | None = None) -> dict[str, Any] | None:
    """Quem segura a trava agora (outro processo, vivo e dentro da validade), ou None se ela está livre."""
    data = read_lock()
    if not data:
        return None
    now = now or datetime.now(UTC)
    try:
        pid = int(data.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    if pid == os.getpid():
        return None  # é nossa
    if not pid_alive(pid):
        return None  # processo que já morreu
    same = _same_process(pid, data.get("created"))
    if same is False:
        return None  # o PID agora é de outro processo
    if same and data.get("owner") == "app":
        return data  # app aberto: vale mesmo vencida (PC acordando da suspensão antes de o laço renovar)
    expires = _expires(data)
    if expires is None or expires <= now:
        return None  # vencida
    return data


@contextmanager
def _guard() -> Iterator[None]:
    """Trava do sistema em volta de ler-decidir-gravar `rotinas.lock` (vale entre processos e entre threads)."""
    path = lock_path().with_name(FILE + ".guard")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a+b") as fh:
        if sys.platform == "win32":
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)  # espera até ~10 s; o trecho guardado leva milissegundos
            try:
                yield
            finally:
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def acquire(owner: str, ttl: timedelta, now: datetime | None = None) -> dict[str, Any] | None:
    """Pega (ou renova) a trava. Devolve None se pegou; se outro processo a segura, devolve os dados dele."""
    now = now or datetime.now(UTC)
    with _guard():
        other = holder(now)
        if other is not None:
            return other
        path = lock_path()
        data: dict[str, Any] = {"owner": owner, "pid": os.getpid(), "created": process_created(os.getpid()),
                                "started_at": now.isoformat(), "expires_at": (now + ttl).isoformat()}
        current = read_lock()
        if current and current.get("pid") == os.getpid() and current.get("started_at"):
            data["started_at"] = current["started_at"]  # renovação: guarda quando pegou
        tmp = path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        os.replace(tmp, path)
    return None


def release() -> None:
    """Solta a trava, só se for deste processo."""
    with _guard():
        data = read_lock()
        if data and data.get("pid") == os.getpid():
            lock_path().unlink(missing_ok=True)


def describe(data: dict[str, Any]) -> str:
    who = "o Vulto está aberto" if data.get("owner") == "app" else "outra execução das rotinas está rodando"
    return f"{who} (processo {data.get('pid')}, desde {data.get('started_at')})"
