"""Rodar CLIs externas (Codex, Claude Code) sem travar o job.

Dois cuidados que `subprocess.run(..., timeout=...)` não tem no Windows:
- stdin fechado: sem `input`, o filho herda o stdin do servidor. Se ele for um pipe que nunca fecha (app iniciado
  sem console), o `codex exec` espera o stdin terminar para anexar ao pedido e nunca gera a imagem.
- árvore inteira no tempo esgotado: `codex`/`claude` costumam ser shims (.cmd → node → .exe). O `run` mata só o
  primeiro processo; os netos seguem vivos segurando a saída e o `communicate()` depois do kill espera para sempre.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from typing import Any

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Depois de matar a árvore, quanto esperar o fim da saída (os leitores do pipe) antes de desistir.
_DRAIN_S = 10


def kill_tree(proc: subprocess.Popen[Any]) -> None:
    """Mata o processo e todos os filhos (no Windows, `taskkill /T`; fora dele, o grupo do processo)."""
    if proc.poll() is not None and sys.platform != "win32":
        return
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=30,
                       creationflags=_NO_WINDOW, check=False)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def run_tree(cmd: list[str], *, timeout: float, input: str | None = None, cwd: str | None = None,
             env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """`subprocess.run` com texto UTF-8, stdin fechado (ou `input`) e a árvore morta no tempo esgotado.

    Levanta `subprocess.TimeoutExpired` como o `run`, mas só depois de matar a árvore e liberar a saída.
    """
    extra: dict[str, Any] = {"creationflags": _NO_WINDOW}
    if sys.platform != "win32":
        extra = {"start_new_session": True}
    proc = subprocess.Popen(
        cmd, cwd=cwd, env=env, stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", **extra,
    )
    try:
        stdout, stderr = proc.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        kill_tree(proc)
        try:
            exc.stdout, exc.stderr = proc.communicate(timeout=_DRAIN_S)
        except subprocess.TimeoutExpired:
            pass
        raise
    except BaseException:
        kill_tree(proc)
        raise
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)
