"""Rotinas com o Vulto fechado: o comando `videos-studio rotinas` e a tarefa agendada do Windows.

`run_once` faz uma volta do laço das rotinas (`routines.tick`) fora do servidor, rodando os jobs na hora (inline),
inclusive os que a manhã pede (coleta de métricas, radar). A trava (`ops/lock.py`) impede rodar junto com o app aberto
ou com outra execução. O que é diário não roda duas vezes no mesmo dia (`backup_day` e `morning_day`, os mesmos do
laço do app). O resultado fica no estado das rotinas (`last_offline_*`), que a página Hoje mostra.

`scheduled_task` diz se a tarefa agendada existe (`schtasks /Query`) e em que horário, com o comando para instalar.
"""

from __future__ import annotations

import logging
import re
import socket
import subprocess
import sys
import time
from datetime import UTC, datetime
from typing import Any

from studio.config import REPO_ROOT
from studio.workers.ops import lock, routines

log = logging.getLogger(__name__)

TASK_NAME = "Vulto Rotinas"
SCRIPT = REPO_ROOT / "scripts" / "agendar-rotinas.ps1"
STATUS_CACHE_S = 60


def _inline_runner() -> Any:
    """Troca a fila de jobs por uma que roda tudo na hora: o processo do comando não fica esperando threads."""
    from studio.workers import jobs

    class InlineRunner(jobs.JobRunner):
        def submit(self, kind: str, payload: dict[str, Any] | None = None, project_id: str | None = None,
                   *, run_inline: bool = False) -> dict[str, Any]:
            return super().submit(kind, payload, project_id, run_inline=True)

    runner = InlineRunner(workers=1)
    jobs._runner = runner
    return runner


def run_once(now: datetime | None = None) -> dict[str, Any]:
    """Roda as rotinas do dia uma vez. Devolve {"status": "ok"|"parcial"|"travada"|"falhou", "ran": [...], "message"}."""
    from studio.workers import jobs

    stamp = datetime.now(UTC).isoformat()
    other = lock.acquire("cli", lock.CLI_TTL)
    if other is not None:
        # campos próprios: a última execução de verdade (ok/falhou) continua na página Hoje
        message = f"não rodou: {lock.describe(other)}; as rotinas ficam por conta dele"
        routines.update_state(last_offline_skipped=stamp, last_offline_skipped_message=message)
        return {"status": "travada", "ran": [], "message": message}
    previous = jobs._runner
    try:
        _recover_stale(stamp)
        runner = _inline_runner()
        ran = routines.tick(now, runner.submit)

        done = _jobs_since(stamp)
        failed = [j for j in done if j["status"] == "failed"]
        broken = _morning_failures(done)
        if failed:
            status = "falhou"
            message = "; ".join([f"{j['kind']}: {(j['error'] or '')[:200]}" for j in failed] + broken)
        elif broken:
            status = "parcial"  # a manhã rodou, mas alguma parte dela quebrou
            message = f"rodou {', '.join(ran)}; " + "; ".join(broken)
        else:
            status = "ok"
            message = f"rodou {', '.join(ran)}" if ran else "nada a fazer hoje (já rodou ou ainda não é o horário)"
        routines.update_state(last_offline_run=stamp, last_offline_status=status, last_offline_message=message)
        return {"status": status, "ran": ran, "message": message}
    except Exception as exc:  # noqa: BLE001 - o resultado fica registrado para a página Hoje
        log.exception("rotinas fora do app falharam")
        message = f"{type(exc).__name__}: {str(exc)[:300]}"
        routines.update_state(last_offline_run=stamp, last_offline_status="falhou", last_offline_message=message)
        return {"status": "falhou", "ran": [], "message": message}
    finally:
        jobs._runner = previous
        lock.release()

def _morning_failures(done: list[dict[str, Any]]) -> list[str]:
    """Partes da manhã que quebraram: `morning()` captura a exceção e devolve "<parte>: falhou (...)"."""
    prefixes = tuple(f"{name}: falhou (" for name, _ in routines.STEPS)
    lines: list[str] = []
    for j in done:
        if j["kind"] == "routine_morning" and j["status"] == "done":
            found = (j.get("result") or {}).get("lines") or []
            lines += [str(line) for line in found if str(line).startswith(prefixes)]
    return lines


def _app_listening() -> bool:
    """O servidor do Vulto responde na porta dele? (app aberto sem a trava, por exemplo logo depois de subir)."""
    from studio.config import get_settings

    settings = get_settings()
    host = settings.host if settings.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    try:
        with socket.create_connection((host, int(settings.port)), timeout=0.5):
            return True
    except OSError:
        return False


def _recover_stale(stamp: str) -> None:
    """Com a trava pega e o app fechado, jobs que ficaram na fila ou rodando de antes (app morto numa queda de
    energia, por exemplo) são marcados como interrompidos: senão o radar, que pula quando há coleta ativa, nunca
    mais rodaria com o app fechado."""
    from studio.db import session_scope
    from studio.models import Job
    from studio.workers.jobs import ACTIVE_STATUSES

    if _app_listening():
        return  # app aberto: os jobs dele podem estar rodando de verdade
    since = datetime.fromisoformat(stamp)
    with session_scope() as s:
        for row in s.query(Job).filter(Job.status.in_(ACTIVE_STATUSES), Job.created_at < since).all():
            row.status = "failed"
            row.error = "interrompido (o Vulto foi fechado durante a execução)"
            row.finished_at = datetime.now(UTC)


def _jobs_since(stamp: str) -> list[dict[str, Any]]:
    from studio.db import session_scope
    from studio.models import Job
    from studio.workers.jobs import job_dict

    since = datetime.fromisoformat(stamp)
    with session_scope() as s:
        return [job_dict(r) for r in s.query(Job).filter(Job.created_at >= since).all()]


# ----------------------------------------------------------------------------- tarefa agendada

_cache: tuple[float, dict[str, Any]] | None = None
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
# horário do gatilho diário no XML da tarefa: <CalendarTrigger> ... <StartBoundary>2026-11-15T08:00:00</StartBoundary>
_DAILY = re.compile(r"<CalendarTrigger>.*?<StartBoundary>[^<]*?T(\d{2}:\d{2})", re.S)


def installed_time(xml: str) -> str | None:
    """O horário (HH:MM) do gatilho diário da tarefa, lido do `schtasks /Query /XML`."""
    match = _DAILY.search(xml.replace("\x00", ""))
    return match.group(1) if match else None


def install_command(morning_time: str | None = None) -> str:
    """Comando que instala (ou reinstala) a tarefa, no horário da manhã da página Hoje."""
    extra = f" -Horario {morning_time}" if morning_time and _TIME.match(morning_time) else ""
    return f'powershell -ExecutionPolicy Bypass -File "{SCRIPT}"{extra}'


def remove_command() -> str:
    return f'powershell -ExecutionPolicy Bypass -File "{SCRIPT}" -Remover'


def scheduled_task(refresh: bool = False) -> dict[str, Any]:
    """A tarefa agendada existe e em que horário? Só no Windows; a consulta fica guardada por 1 min.

    O comando de instalar leva o horário da manhã atual; `time_mismatch` avisa quando a tarefa instalada dispara
    em outro horário (antes do horário da manhã, a execução diária só faria a cópia do banco)."""
    global _cache
    supported = sys.platform == "win32"
    if refresh or _cache is None or time.monotonic() - _cache[0] >= STATUS_CACHE_S:
        found: dict[str, Any] = {"installed": False, "installed_time": None, "error": None}
        if supported:
            from studio.core.subproc import run_tree

            try:
                proc = run_tree(["schtasks", "/Query", "/TN", TASK_NAME, "/XML"], timeout=15)
                found["installed"] = proc.returncode == 0
                if found["installed"]:
                    found["installed_time"] = installed_time(proc.stdout or "")
            except (OSError, subprocess.TimeoutExpired) as exc:
                found["error"] = f"não foi possível consultar o Agendador de Tarefas ({type(exc).__name__})"
        _cache = (time.monotonic(), found)
    found = _cache[1]
    morning_time = routines.get_state().get("morning_time") or routines.DEFAULTS["morning_time"]
    installed = bool(found["installed"]) and supported
    when = found["installed_time"] if installed else None
    return {
        "name": TASK_NAME, "supported": supported, "installed": installed, "installed_time": when,
        "morning_time": morning_time, "time_mismatch": bool(when and when != morning_time),
        "install_command": install_command(morning_time), "remove_command": remove_command(),
        "error": found["error"] if supported else None,
    }
