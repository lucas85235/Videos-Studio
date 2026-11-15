"""`run_tree`: stdin fechado e a árvore inteira morta no tempo esgotado (o Codex travava o job para sempre)."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

from studio.core.subproc import run_tree

PY = sys.executable


def test_child_does_not_wait_for_the_servers_stdin():
    # Com o stdin herdado de um pipe que nunca fecha, `codex exec` lia o stdin para sempre. Aqui ele vem fechado.
    started = time.monotonic()
    proc = run_tree([PY, "-c", "import sys; data = sys.stdin.read(); print(len(data))"], timeout=20)
    assert proc.returncode == 0 and proc.stdout.strip() == "0"
    assert time.monotonic() - started < 15


def test_input_is_sent_and_text_is_utf8():
    proc = run_tree([PY, "-c", "import sys; sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8'); print(sys.stdin.read().upper())"],
                    input="ação", timeout=20)
    assert proc.stdout.strip() == "AÇÃO"


def test_timeout_kills_grandchildren_that_hold_the_output():
    # O shim (.cmd → node → codex.exe) deixava o neto vivo segurando a saída: o `run` esperava para sempre.
    grandchild = "import time; time.sleep(60)"
    child = f"import subprocess, sys, time; subprocess.Popen([sys.executable, '-c', {grandchild!r}]); time.sleep(60)"
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        run_tree([PY, "-c", child], timeout=2)
    assert time.monotonic() - started < 25
