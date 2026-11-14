"""Cliente do servidor de render (Node + Remotion), mantido vivo entre os blocos."""

from __future__ import annotations

import atexit
import json
import logging
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any

from studio.config import REPO_ROOT, get_settings
from studio.core.hashing import hash_obj, hash_tree

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class RenderError(RuntimeError):
    pass


@lru_cache(maxsize=1)
def renderer_version() -> str:
    """Versão do renderer = hash do código das composições (mudou o código, os blocos são refeitos)."""
    renderer = get_settings().renderer_dir
    parts = {
        "src": hash_tree(renderer / "src"),
        "package": (renderer / "package.json").read_text(encoding="utf-8") if (renderer / "package.json").exists() else "",
        "fonts": (REPO_ROOT / "assets" / "fonts" / "fonts.json").read_text(encoding="utf-8")
        if (REPO_ROOT / "assets" / "fonts" / "fonts.json").exists()
        else "",
    }
    return hash_obj(parts)[:16]


class RenderClient:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._lock = threading.Lock()
        self._waiters: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._ready = threading.Event()
        self._fatal: str | None = None
        self.asset_base_url: str | None = None
        self._stderr_tail: list[str] = []

    def _start(self) -> None:
        settings = get_settings()
        script = settings.renderer_dir / "scripts" / "render-server.mjs"
        if not script.exists():
            raise RenderError(f"servidor de render não encontrado: {script}")
        bundle_dir = settings.data_dir / "renderer-bundle" / renderer_version()
        cmd = [settings.node, str(script), "--data-dir", str(settings.data_dir), "--bundle-dir", str(bundle_dir)]
        log.info("iniciando servidor de render: %s", " ".join(cmd))
        self._ready.clear()
        self._fatal = None
        self._proc = subprocess.Popen(
            cmd,
            cwd=str(settings.renderer_dir),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=_NO_WINDOW,
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        if not self._ready.wait(timeout=900):
            raise RenderError("o servidor de render não ficou pronto em 15 min (bundle/navegador)")
        if self._fatal:
            raise RenderError(f"servidor de render falhou: {self._fatal}")

    def _read_stdout(self) -> None:
        assert self._proc and self._proc.stdout
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                log.debug("render-server: %s", line)
                continue
            kind = msg.get("type")
            if kind == "ready":
                self.asset_base_url = msg.get("assetBaseUrl")
                self._ready.set()
            elif kind == "fatal":
                self._fatal = msg.get("message", "erro fatal")
                self._ready.set()
            elif msg.get("id") in self._waiters:
                self._waiters[msg["id"]].put(msg)
        # Processo terminou: acorda todos
        self._fatal = self._fatal or "o servidor de render encerrou"
        self._ready.set()
        for waiter in list(self._waiters.values()):
            waiter.put({"type": "error", "message": self._fatal + ": " + " | ".join(self._stderr_tail[-5:])})

    def _read_stderr(self) -> None:
        assert self._proc and self._proc.stderr
        for line in self._proc.stderr:
            line = line.rstrip()
            if line:
                self._stderr_tail = (self._stderr_tail + [line])[-40:]
                log.debug("render-server stderr: %s", line)

    def _ensure(self) -> None:
        if self._proc is None or self._proc.poll() is not None:
            self._start()

    def render(
        self,
        props: dict[str, Any],
        output: Path,
        *,
        on_progress: Callable[[float], None] | None = None,
        concurrency: int | None = None,
        hardware_acceleration: str = "if-possible",
        timeout: float = 3600,
    ) -> Path:
        with self._lock:
            self._ensure()
            request_id = uuid.uuid4().hex
            waiter: queue.Queue[dict[str, Any]] = queue.Queue()
            self._waiters[request_id] = waiter
            message = {
                "id": request_id,
                "type": "render",
                "props": props,
                "output": str(output),
                "concurrency": concurrency or get_settings().render_concurrency,
                "hardwareAcceleration": hardware_acceleration,
            }
            assert self._proc and self._proc.stdin
            self._proc.stdin.write(json.dumps(message) + "\n")
            self._proc.stdin.flush()
            try:
                while True:
                    try:
                        msg = waiter.get(timeout=timeout)
                    except queue.Empty as exc:
                        raise RenderError("tempo esgotado no render") from exc
                    if msg["type"] == "progress" and on_progress:
                        on_progress(float(msg.get("progress", 0)))
                    elif msg["type"] == "done":
                        return output
                    elif msg["type"] == "error":
                        raise RenderError(msg.get("message", "erro no render")[-3000:])
            finally:
                self._waiters.pop(request_id, None)

    def shutdown(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                assert proc.stdin
                proc.stdin.write(json.dumps({"type": "shutdown"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=20)
            except Exception:  # noqa: BLE001
                proc.kill()
        self._proc = None


_client: RenderClient | None = None
_client_lock = threading.Lock()


def get_render_client() -> RenderClient:
    global _client
    with _client_lock:
        if _client is None:
            _client = RenderClient()
            atexit.register(_client.shutdown)
        return _client
