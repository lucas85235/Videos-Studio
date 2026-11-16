"""Rotinas com o Vulto fechado: comando `rotinas`, trava e consulta da tarefa agendada (jobs e schtasks simulados)."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from studio import cli
from studio.api.app import create_app
from studio.workers import jobs
from studio.workers.ops import lock, offline, routines

MORNING = datetime(2026, 11, 15, 9, 0).astimezone()  # hora local depois das 08:00
EARLY = datetime(2026, 11, 15, 7, 0).astimezone()


@pytest.fixture(autouse=True)
def app_closed(monkeypatch):
    """Nenhum Vulto respondendo na porta (o de quem roda os testes pode estar aberto)."""
    monkeypatch.setattr(offline, "_app_listening", lambda: False)


@pytest.fixture()
def fake_jobs(monkeypatch):
    """Troca backup e manhã por jobs simulados; a manhã pede a coleta de métricas, como a de verdade."""
    calls: list[str] = []

    def backup(ctx, payload):
        calls.append("backup")
        return {}

    def morning(ctx, payload):
        calls.append("routine_morning")
        jobs.get_runner().submit("metrics_collect", {})
        calls.append("depois do submit")  # a coleta já rodou: tudo inline
        return {}

    def metrics(ctx, payload):
        calls.append("metrics_collect")
        return {}

    monkeypatch.setitem(jobs._REGISTRY, "backup", backup)
    monkeypatch.setitem(jobs._REGISTRY, "routine_morning", morning)
    monkeypatch.setitem(jobs._REGISTRY, "metrics_collect", metrics)
    return calls


def _write_lock(owner: str, pid: int, expires: datetime) -> None:
    lock.lock_path().write_text(json.dumps({"owner": owner, "pid": pid, "started_at": datetime.now(UTC).isoformat(),
                                            "expires_at": expires.isoformat()}), encoding="utf-8")


def test_run_once_runs_jobs_inline_and_records(isolated_settings, fake_jobs):
    before = jobs._runner
    result = offline.run_once(MORNING)
    assert result["status"] == "ok" and result["ran"] == ["backup", "routine_morning"]
    assert fake_jobs == ["backup", "routine_morning", "metrics_collect", "depois do submit"]
    assert jobs._runner is before  # a fila do processo volta a ser a de sempre
    assert not lock.lock_path().exists()  # trava solta no fim
    state = routines.public_state()
    assert state["last_offline_status"] == "ok" and state["last_offline_run"]
    assert "backup" in state["last_offline_message"]
    assert all(j["status"] == "done" for j in jobs.list_jobs())


def test_daily_routines_do_not_run_twice_same_day(isolated_settings, fake_jobs):
    assert offline.run_once(EARLY)["ran"] == ["backup"]  # antes do horário da manhã: só a cópia
    assert offline.run_once(MORNING)["ran"] == ["routine_morning"]
    again = offline.run_once(MORNING + timedelta(hours=3))
    assert again["status"] == "ok" and again["ran"] == []
    assert "nada a fazer" in again["message"]
    assert fake_jobs.count("backup") == 1 and fake_jobs.count("routine_morning") == 1
    assert offline.run_once(MORNING + timedelta(days=1))["ran"] == ["backup", "routine_morning"]


def test_app_ran_today_counts(isolated_settings, fake_jobs):
    """O que o laço do app já fez hoje (mesmos `backup_day`/`morning_day`) não roda de novo pelo comando."""
    today = routines.local_today(MORNING)
    routines.update_state(backup_day=today, morning_day=today)
    assert offline.run_once(MORNING)["ran"] == []
    assert fake_jobs == []


def test_lock_held_by_live_app_blocks(isolated_settings, fake_jobs):
    _write_lock("app", os.getppid(), datetime.now(UTC) + timedelta(minutes=10))
    result = offline.run_once(MORNING)
    assert result["status"] == "travada" and "Vulto está aberto" in result["message"]
    assert fake_jobs == [] and jobs.list_jobs() == []
    state = routines.public_state()
    assert state["last_offline_status"] is None and state["last_offline_run"] is None  # não apaga a última de verdade
    assert state["last_offline_skipped"] and "Vulto está aberto" in state["last_offline_skipped_message"]
    assert json.loads(lock.lock_path().read_text(encoding="utf-8"))["owner"] == "app"  # a do app fica


def test_lock_held_by_other_cli_blocks(isolated_settings, fake_jobs):
    _write_lock("cli", os.getppid(), datetime.now(UTC) + timedelta(hours=1))
    assert "outra execução" in offline.run_once(MORNING)["message"]


def test_expired_or_dead_lock_is_taken(isolated_settings, fake_jobs, monkeypatch):
    _write_lock("app", os.getppid(), datetime.now(UTC) - timedelta(minutes=1))  # vencida
    assert offline.run_once(MORNING)["status"] == "ok"
    monkeypatch.setattr(lock, "pid_alive", lambda pid: False)  # app fechado à força: processo morto
    _write_lock("app", 999999, datetime.now(UTC) + timedelta(minutes=10))
    result = offline.run_once(MORNING + timedelta(days=1))
    assert result["status"] == "ok" and result["ran"] == ["backup", "routine_morning"]


def test_live_app_lock_counts_even_expired(isolated_settings, fake_jobs):
    """PC acordando da suspensão: a tarefa dispara antes de o laço do app renovar; com o app vivo, nada roda."""
    parent = os.getppid()
    created = lock.process_created(parent)
    if created is None:
        pytest.skip("sem como saber quando o processo foi criado neste sistema")
    data = {"owner": "app", "pid": parent, "created": created, "started_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) - timedelta(hours=1)).isoformat()}
    lock.lock_path().write_text(json.dumps(data), encoding="utf-8")
    assert offline.run_once(MORNING)["status"] == "travada"
    assert fake_jobs == []
    data["created"] = created + 1  # o PID agora é de outro processo: a trava é tomada
    lock.lock_path().write_text(json.dumps(data), encoding="utf-8")
    assert offline.run_once(MORNING)["status"] == "ok"


def test_acquire_renews_own_lock_and_release_only_own(isolated_settings):
    assert lock.acquire("app", lock.APP_TTL) is None
    first = lock.read_lock()
    assert first["pid"] == os.getpid() and first["owner"] == "app"
    assert lock.acquire("app", lock.APP_TTL) is None  # renovar a própria
    assert lock.read_lock()["started_at"] == first["started_at"]
    _write_lock("cli", os.getppid(), datetime.now(UTC) + timedelta(hours=1))
    assert lock.acquire("app", lock.APP_TTL)["owner"] == "cli"
    lock.release()  # não é nossa: fica
    assert lock.lock_path().exists()


def test_failed_job_is_reported(isolated_settings, fake_jobs, monkeypatch):
    def broken(ctx, payload):
        raise RuntimeError("disco cheio")

    monkeypatch.setitem(jobs._REGISTRY, "backup", broken)
    result = offline.run_once(MORNING)
    assert result["status"] == "falhou" and "disco cheio" in result["message"]
    assert routines.public_state()["last_offline_status"] == "falhou"
    assert not lock.lock_path().exists()


def test_morning_part_failure_is_partial(isolated_settings, fake_jobs, monkeypatch):
    def morning(ctx, payload):
        return {"lines": ["estoque: 2 pendências", "métricas: falhou (RuntimeError: sem rede)"]}

    monkeypatch.setitem(jobs._REGISTRY, "routine_morning", morning)
    result = offline.run_once(MORNING)
    assert result["status"] == "parcial" and "métricas: falhou" in result["message"]
    assert "estoque" not in result["message"]
    assert routines.public_state()["last_offline_status"] == "parcial"


def test_stale_jobs_are_recovered_by_cli(isolated_settings, fake_jobs, monkeypatch):
    """App morto numa queda de energia com um job na fila: o comando o marca como interrompido."""
    from studio.db import session_scope
    from studio.models import Job

    with session_scope() as s:
        s.add(Job(id="job_velho", kind="radar_collect", status="running", payload={}, message="",
                  created_at=datetime.now(UTC) - timedelta(days=1)))
    offline.run_once(MORNING)
    with session_scope() as s:
        row = s.get(Job, "job_velho")
        assert row.status == "failed" and "interrompido" in row.error

    with session_scope() as s:  # com o app respondendo na porta, os jobs ficam com ele
        s.add(Job(id="job_do_app", kind="render", status="running", payload={}, message="",
                  created_at=datetime.now(UTC) - timedelta(minutes=1)))
    monkeypatch.setattr(offline, "_app_listening", lambda: True)
    offline.run_once(MORNING + timedelta(days=1))
    with session_scope() as s:
        assert s.get(Job, "job_do_app").status == "running"


def test_app_recover_keeps_jobs_of_running_cli(isolated_settings):
    from studio.db import session_scope
    from studio.models import Job

    started = datetime.now(UTC) - timedelta(minutes=5)
    lock.lock_path().write_text(json.dumps({"owner": "cli", "pid": os.getppid(), "started_at": started.isoformat(),
                                            "expires_at": (started + lock.CLI_TTL).isoformat()}), encoding="utf-8")
    with session_scope() as s:
        s.add(Job(id="job_cli", kind="backup", status="running", payload={}, message="",
                  created_at=started + timedelta(minutes=1)))
        s.add(Job(id="job_antigo", kind="render", status="running", payload={}, message="",
                  created_at=started - timedelta(hours=1)))
    jobs.JobRunner(workers=1).recover()
    with session_scope() as s:
        assert s.get(Job, "job_cli").status == "running"
        assert s.get(Job, "job_antigo").status == "failed"


def test_installed_time_from_xml():
    xml = ("<Task><Triggers><LogonTrigger><StartBoundary>2026-01-01T00:00:00</StartBoundary></LogonTrigger>"
           "<CalendarTrigger><StartBoundary>2026-11-15T07:30:00-03:00</StartBoundary></CalendarTrigger></Triggers>")
    assert offline.installed_time(xml) == "07:30"
    assert offline.installed_time("\x00".join(xml)) == "07:30"  # saída em UTF-16 lida como UTF-8
    assert offline.installed_time("<Task/>") is None


def test_cli_command(isolated_settings, fake_jobs, monkeypatch, capsys):
    monkeypatch.setattr(offline, "run_once", lambda: {"status": "ok", "ran": ["backup"], "message": "rodou backup"})
    cli.main(["rotinas"])
    assert "Rotinas (ok): rodou backup" in capsys.readouterr().out
    monkeypatch.setattr(offline, "run_once", lambda: {"status": "falhou", "ran": [], "message": "x"})
    with pytest.raises(SystemExit) as exc:
        cli.main(["rotinas"])
    assert exc.value.code == 1


def test_scheduled_task_status(isolated_settings, monkeypatch):
    from studio.core import subproc

    seen: list[list[str]] = []

    xml = "<CalendarTrigger><StartBoundary>2026-11-15T08:00:00</StartBoundary></CalendarTrigger>"

    def fake_run(cmd, timeout):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if len(seen) == 1 else 1, xml, "")

    monkeypatch.setattr(subproc, "run_tree", fake_run)
    monkeypatch.setattr(offline.sys, "platform", "win32")
    info = offline.scheduled_task(refresh=True)
    assert info["supported"] and info["installed"] and seen[0][:3] == ["schtasks", "/Query", "/TN"]
    assert seen[0][-1] == "/XML" and info["installed_time"] == "08:00" and info["time_mismatch"] is False
    assert "agendar-rotinas.ps1" in info["install_command"] and "-Remover" in info["remove_command"]
    assert info["install_command"].endswith("-Horario 08:00")
    routines.update_state(morning_time="09:15")  # manhã mudou depois de instalar: avisa e o comando acompanha
    info = offline.scheduled_task()
    assert info["installed"] and info["time_mismatch"] and info["morning_time"] == "09:15"  # sem consultar de novo
    assert info["install_command"].endswith("-Horario 09:15")
    assert len(seen) == 1
    assert offline.scheduled_task(refresh=True)["installed"] is False

    monkeypatch.setattr(offline.sys, "platform", "linux")
    info = offline.scheduled_task(refresh=True)
    assert info["supported"] is False and info["installed"] is False and len(seen) == 2


def test_scheduled_task_route(isolated_settings, monkeypatch):
    monkeypatch.setattr(offline.sys, "platform", "linux")  # sem schtasks de verdade
    with TestClient(create_app()) as client:
        body = client.get("/api/ops/scheduled-task?refresh=true").json()
        assert body["name"] == offline.TASK_NAME and body["supported"] is False
        state = client.get("/api/ops/routines").json()
        assert state["last_offline_run"] is None and state["last_offline_message"] == ""
