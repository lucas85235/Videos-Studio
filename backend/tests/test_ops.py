"""Operação do canal (ADR 0002, bloco C): cópia do banco, pendências, estoque, rotinas, tempo por etapa e créditos."""

from __future__ import annotations

import time as _time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.db import session_scope
from studio.ids import new_id
from studio.models import (
    AffiliateOffer,
    Channel,
    CostEntry,
    Job,
    PendingTask,
    Project,
    Publication,
    RevenueEntry,
)
from studio.workers import jobs
from studio.workers.ops import pending, routines, stock, velocity
from studio.workers.ops.backup import run_backup
from studio.workers.ops.estimate import credits_for

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


def _channel(name: str = "Plantão", **fields) -> str:
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name=name, settings=fields.pop("settings", {}), **fields))
    return cid


def _project(channel_id: str | None, style_id: str = "misterios-short", status: str = "draft",
             title: str = "Vídeo") -> str:
    doc = service.create_project(title=title, style_id=style_id, topic="tema", channel_id=channel_id)
    service.set_status(doc.id, status)
    return doc.id


def _publication(project_id: str, status: str, scheduled_at: datetime | None = None, **details) -> str:
    pid = new_id("pub")
    with session_scope() as s:
        s.add(Publication(id=pid, project_id=project_id, platform="youtube", language="pt-BR", driver="youtube",
                          status=status, scheduled_at=scheduled_at, details=details))
    return pid


def _open_pending() -> list[dict]:
    return pending.list_pending("aberta")


# ----------------------------------------------------------------------------- cópia do banco


def test_backup_keeps_14(isolated_settings):
    first = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    for i in range(16):
        path = run_backup(now=first + timedelta(days=i))
        assert path.is_file() and path.stat().st_size > 0
    names = sorted(p.name for p in (isolated_settings.data_dir / "backups").glob("studio-*.db"))
    assert len(names) == 14
    assert names[0] == "studio-2026-10-03.db" and names[-1] == "studio-2026-10-16.db"
    # mesmo dia de novo: sobrescreve, não acumula
    run_backup(now=first + timedelta(days=15, hours=1))
    assert len(list((isolated_settings.data_dir / "backups").glob("studio-*.db"))) == 14


# ----------------------------------------------------------------------------- pendências


def test_ensure_auto_never_duplicates_nor_reopens(isolated_settings):
    cid = _channel()
    manual = pending.create_pending("Gravar o teaser", "/projetos", cid)
    first = pending.ensure_auto("estoque:x:2026-11-15", "Estoque baixo", "/projetos", cid)
    assert first and first["kind"] == "auto" and first["order"] == manual["order"] + 1
    assert pending.ensure_auto("estoque:x:2026-11-15", "Estoque baixo", "/projetos", cid) is None
    pending.set_status(first["id"], "feita")
    assert pending.ensure_auto("estoque:x:2026-11-15", "Estoque baixo", "/projetos", cid) is None
    with session_scope() as s:
        assert s.query(PendingTask).filter(PendingTask.auto_key == "estoque:x:2026-11-15").count() == 1
    assert [p["text"] for p in _open_pending()] == ["Gravar o teaser"]
    # canal que não existe mais: a pendência entra sem canal
    assert pending.ensure_auto("k2", "Sem canal", "", "chn_sumiu")["channel_id"] is None


# ----------------------------------------------------------------------------- estoque


def test_stock_below_minimum(isolated_settings):
    cid = _channel(min_stock=3, stock_format="short")
    ready = _project(cid, status="rendered")
    _project(cid, status="rendered", style_id="curiosidades-longo")  # longo: não conta no estoque de Shorts
    sent = _project(cid, status="rendered")
    _publication(sent, "uploaded")  # já enviado: saiu da prateleira
    later = _project(cid, status="rendered")
    _publication(later, "scheduled", NOW + timedelta(days=1))  # agendado no futuro: conta
    _project(cid, status="preview_ready")  # ainda não está pronto
    _channel("Sem mínimo")  # canal sem mínimo fica fora
    [item] = stock.stock(NOW)
    assert item["channel_id"] == cid and item["total"] == 2 and item["ready"] == 1 and item["scheduled"] == 1
    assert item["below"] and item["min_stock"] == 3
    assert ready  # o projeto pronto é o que conta como "pronto"

    lines = routines.step_stock(NOW)
    assert lines == ["Estoque de Shorts do Plantão: 2 prontos ou agendados, mínimo 3"]
    assert routines.step_stock(NOW) == []  # mesma chave do dia: não duplica
    [p] = _open_pending()
    assert p["where"] == "/" and p["auto_key"].startswith(f"estoque:{cid}:")

    # outro dia ainda abaixo: a pendência do dia anterior é descartada (uma aberta por canal)
    assert routines.step_stock(NOW + timedelta(hours=20))  # dia seguinte; o agendado ainda está no futuro
    [p2] = _open_pending()
    assert p2["id"] != p["id"] and pending.get_pending(p["id"])["status"] == "descartada"

    with session_scope() as s:
        s.get(Channel, cid).stock_format = "any"
    assert stock.stock(NOW)[0]["total"] == 3 and not stock.stock(NOW)[0]["below"]
    # estoque voltou ao mínimo: o alerta aberto fecha sozinho
    assert routines.step_stock(NOW) == []
    assert _open_pending() == [] and pending.get_pending(p2["id"])["status"] == "descartada"


def test_stock_private_upload_stays_on_shelf(isolated_settings):
    cid = _channel(min_stock=1, stock_format="short")
    private = _project(cid, status="rendered")
    _publication(private, "uploaded", privacy="private")  # envio privado: o vídeo não saiu
    item = stock.stock(NOW)[0]
    assert item["ready"] == 1 and item["total"] == 1 and not item["below"]
    _publication(private, "uploaded", privacy="public")  # agora saiu
    assert stock.stock(NOW)[0]["total"] == 0


# ----------------------------------------------------------------------------- marcos


def test_milestones_only_inside_72h_window(isolated_settings):
    cid = _channel()
    cases = {
        "dois dias": NOW - timedelta(hours=50),  # 48 h atingido há 2 h
        "fora da janela": NOW - timedelta(hours=48 + 80),  # 48 h há 80 h e 7 dias ainda não
        "sete dias": NOW - timedelta(days=8),  # 7 dias atingido há 24 h
        "antigo": NOW - timedelta(days=60),  # 28 dias há 32 dias: nada retroativo
        "recente": NOW - timedelta(hours=10),  # ainda sem marco
    }
    for title, when in cases.items():
        pid = _project(cid, style_id="curiosidades-longo", status="rendered", title=title)
        _publication(pid, "scheduled", when)
    short = _project(cid, status="rendered", title="short")
    _publication(short, "scheduled", NOW - timedelta(hours=50))  # Short: marcos são só dos longos
    private = _project(cid, style_id="curiosidades-longo", status="rendered", title="privado")
    pub = _publication(private, "uploaded", privacy="private", published_at=(NOW - timedelta(hours=50)).isoformat())
    assert pub

    lines = routines.step_milestones(NOW)
    assert sorted(lines) == ['Ver como foi "dois dias" com 48 h no ar', 'Ver como foi "sete dias" com 7 dias no ar']
    assert routines.step_milestones(NOW) == []
    keys = sorted(p["auto_key"].rsplit(":", 1)[1] for p in _open_pending())
    assert keys == ["168h", "48h"]
    assert all(p["where"] == "/desempenho" for p in _open_pending())
    assert routines.milestone(24 * 28 + 71) == (24 * 28, "28 dias") and routines.milestone(24 * 28 + 72) is None


# ----------------------------------------------------------------------------- receita


def test_revenue_request_days_1_to_10_and_only_without_last_month(isolated_settings):
    cid = _channel()
    other = _channel("Money")
    with session_scope() as s:
        s.add(AffiliateOffer(id=new_id("ofe"), channel_id=cid, slug="a", name="Oferta A", status="ativa"))
        s.add(AffiliateOffer(id=new_id("ofe"), channel_id=other, slug="b", name="Oferta B", status="ativa"))
        s.add(AffiliateOffer(id=new_id("ofe"), channel_id=_channel("Pausado"), slug="c", name="C", status="pausada"))
        s.add(RevenueEntry(channel_id=other, store="amazon", period="2026-10", commission=10.0))
    assert routines.step_revenue(datetime(2026, 11, 11, 12, tzinfo=UTC)) == []  # dia 11: fora
    lines = routines.step_revenue(datetime(2026, 11, 3, 12, tzinfo=UTC))
    assert lines == ["Lançar os números de outubro de 2026 das lojas de afiliado (Plantão)"]
    assert routines.step_revenue(datetime(2026, 11, 4, 12, tzinfo=UTC)) == []  # mesmo mês: não duplica
    [p] = _open_pending()
    assert p["auto_key"] == f"receita:{cid}:2026-10" and p["where"] == "/dinheiro" and p["channel_id"] == cid
    # janeiro pede dezembro do ano anterior
    assert routines.previous_month(datetime(2027, 1, 5).date()) == "2026-12"


# ----------------------------------------------------------------------------- manhã


def test_morning_submits_metrics_collect_only_when_registered(isolated_settings, monkeypatch):
    calls: list[dict] = []
    _channel(settings={"youtube": {"channel_id": "UC123"}})
    monkeypatch.delitem(jobs._REGISTRY, "metrics_collect", raising=False)
    routines.morning(NOW)
    with session_scope() as s:
        assert s.query(Job).filter(Job.kind == "metrics_collect").count() == 0

    def fake(ctx, payload):
        calls.append(payload)
        return {}

    monkeypatch.setitem(jobs._REGISTRY, "metrics_collect", fake)
    lines = routines.morning(NOW)
    assert any("coleta de métricas" in line for line in lines)
    with session_scope() as s:
        assert s.query(Job).filter(Job.kind == "metrics_collect").count() == 1
    # desligada nas rotinas: não pede
    routines.update_state(metrics=False)
    routines.morning(NOW)
    with session_scope() as s:
        assert s.query(Job).filter(Job.kind == "metrics_collect").count() == 1
    for _ in range(50):  # o job fake roda no pool de threads
        if calls:
            break
        _time.sleep(0.05)
    assert calls == [{}]


def test_tick_runs_backup_and_morning_once_a_day(isolated_settings):
    submitted: list[str] = []

    def submit(kind, payload):
        submitted.append(kind)
        return {"id": f"job_{len(submitted)}"}

    early = datetime(2026, 11, 15, 7, 0).astimezone()  # hora local antes das 08:00
    assert routines.tick(early, submit) == ["backup"]
    assert routines.tick(early + timedelta(hours=2), submit) == ["routine_morning"]
    assert routines.tick(early + timedelta(hours=3), submit) == []
    assert routines.tick(early + timedelta(days=1, hours=2), submit) == ["backup", "routine_morning"]
    routines.update_state(backup=False)
    assert routines.tick(early + timedelta(days=2, hours=2), submit) == ["routine_morning"]


def test_morning_job_records_state(isolated_settings):
    result = jobs.get_runner().submit("routine_morning", {}, run_inline=True)
    final = jobs.get_job(result["id"])
    assert final and final["status"] == "done"
    state = routines.public_state()
    assert state["last_morning"] and state["last_morning_job"] == result["id"]
    backup = jobs.get_runner().submit("backup", {}, run_inline=True)
    assert jobs.get_job(backup["id"])["status"] == "done"
    assert routines.public_state()["last_backup"]


# ----------------------------------------------------------------------------- tempo por etapa


def _job(kind: str, seconds: float, start: datetime, project_id: str | None = None, status: str = "done",
         cost: float = 0.0) -> None:
    jid = new_id("job")
    with session_scope() as s:
        s.add(Job(id=jid, kind=kind, project_id=project_id, status=status, started_at=start,
                  finished_at=start + timedelta(seconds=seconds)))
        if cost:
            s.add(CostEntry(job_id=jid, project_id=project_id, provider="x", kind="tts", cost_usd=cost))


def test_velocity_bottleneck_and_rework(isolated_settings):
    t = NOW - timedelta(days=1)
    for p in ("p1", "p2", "p3"):
        _job("render", 600, t, p, cost=0.5)
        _job("preview", 60, t, p)
        _job("preview", 60, t, p)
    _job("preview", 60, t, "p1", status="failed")
    _job("render", 100, NOW - timedelta(days=10), "p9")  # período anterior
    _job("render", 100, NOW - timedelta(days=30), "p9")  # fora dos dois períodos
    rep = velocity.report(days=7, now=NOW)
    render, preview = rep["steps"]
    assert render["kind"] == "render" and render["label"] == "Render final"
    assert render["n"] == 3 and render["total_s"] == 1800 and render["avg_s"] == 600
    assert render["before_s"] == 100 and render["cost_usd"] == 1.5 and render["per_project"] == 1.0
    assert preview["n"] == 7 and preview["failures"] == 1 and preview["projects"] == 3
    assert preview["per_project"] == 2.33
    assert rep["total_s"] == 1800 + 420 and rep["failures"] == 1
    assert rep["rework"] == [{"kind": "preview", "label": "Prévia", "per_project": 2.33, "projects": 3}]
    assert rep["suggestions"] == [
        "Gargalo: Render final leva 81% do tempo do período (10 min por vez, 3 vezes). Meça uma mudança por vez nele.",
        "Retrabalho: Prévia roda 2,33 vezes por projeto (3 projetos). Descubra o que faz refazer.",
    ]


def test_velocity_without_bottleneck_or_rework(isolated_settings):
    t = NOW - timedelta(hours=5)
    for kind in ("script", "storyboard", "translate", "metadata", "render"):
        _job(kind, 300, t, "p1")
    for p in ("p1", "p1", "p2"):  # 1,5 por projeto, mas só 2 projetos
        _job("preview", 100, t, p)
    rep = velocity.report(days=7, now=NOW)
    assert rep["steps"][0]["pct"] < 0.3 and rep["rework"] == [] and rep["suggestions"] == []


# ----------------------------------------------------------------------------- créditos


def test_credits_for():
    assert credits_for(15558, 0.44) == {"credits": 6846, "per_char": 0.44, "usd": None}
    assert credits_for(1000, 0.44, 0.0003) == {"credits": 440, "per_char": 0.44, "usd": 0.13}
    assert credits_for(0, 0.44) is None and credits_for(100, 0) is None


def test_plan_has_credits_estimate(isolated_settings, monkeypatch):
    from studio.workers.pipeline import build
    from studio.workers.pipeline.build import plan

    cid = _channel(credits_per_char=0.44, credit_price_usd=0.0003)
    pid = _project(cid)
    jobs.get_runner().submit("script", {}, pid, run_inline=True)
    # voz final local e grátis (kokoro_local): não gasta créditos
    monkeypatch.setattr(build, "_first_available", lambda chain: "kokoro_local")
    local_final = plan(pid)
    assert local_final["tts_provider"] == "kokoro_local" and not local_final["tts_local"]
    assert local_final["credits_estimate"] is None
    # voz final paga (ElevenLabs): estimativa pelos créditos por caractere do canal
    monkeypatch.setattr(build, "_first_available", lambda chain: "elevenlabs")
    final = plan(pid)
    assert final["tts_chars"] > 0 and not final["tts_local"]
    est = final["credits_estimate"]
    assert est["credits"] == round(final["tts_chars"] * 0.44) and est["per_char"] == 0.44
    assert est["usd"] == round(est["credits"] * 0.0003, 2)
    # canal sem créditos por caractere, ou projeto sem canal: sem estimativa
    with session_scope() as s:
        s.get(Channel, cid).credits_per_char = 0.0
    assert plan(pid)["credits_estimate"] is None
    with session_scope() as s:
        s.get(Project, pid).channel_id = None
    assert plan(pid)["credits_estimate"] is None


# ----------------------------------------------------------------------------- rotas


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_pending_routes(client):
    cid = _channel()
    created = client.post("/api/pending", json={"text": "Gravar teaser", "where": "/agenda", "channel_id": cid})
    assert created.status_code == 200, created.text
    item = created.json()
    assert item["where"] == "/agenda" and item["status"] == "aberta" and item["kind"] == "manual"
    assert client.post("/api/pending", json={"text": ""}).status_code == 422
    assert client.post("/api/pending", json={"text": "x", "channel_id": "chn_nao"}).status_code == 404
    edited = client.put(f"/api/pending/{item['id']}", json={"text": "Gravar o teaser"}).json()
    assert edited["text"] == "Gravar o teaser" and edited["where"] == "/agenda" and edited["channel_id"] == cid
    assert client.put(f"/api/pending/{item['id']}", json={"channel_id": None}).json()["channel_id"] is None
    assert client.put("/api/pending/pen_nao", json={"text": "a"}).status_code == 404
    done = client.post(f"/api/pending/{item['id']}/status", json={"status": "feita"}).json()
    assert done["status"] == "feita" and done["done_at"]
    assert client.post(f"/api/pending/{item['id']}/status", json={"status": "x"}).status_code == 422
    assert client.get("/api/pending").json() == []
    assert len(client.get("/api/pending?status=todas").json()) == 1


def test_today_route(client):
    cid = _channel(min_stock=2, settings={"analytics": {"scope": "missing"}})
    review = _project(cid, status="script_review", title="Farol")
    manual = _project(cid, status="rendered", title="Pacote")
    _publication(manual, "package_ready")
    with session_scope() as s:
        s.add(AffiliateOffer(id=new_id("ofe"), channel_id=cid, slug="a", name="Fone", status="ativa",
                             checked_on=""))
        s.add(AffiliateOffer(id=new_id("ofe"), channel_id=cid, slug="b", name="Mouse", status="ativa",
                             checked_on=datetime.now().astimezone().date().isoformat()))
    yesterday = datetime.now(UTC) - timedelta(days=1)
    with session_scope() as s:
        s.add(Job(id=new_id("job"), kind="render", status="failed", error="sem ffmpeg", project_id=review,
                  started_at=yesterday.replace(hour=12, minute=0), finished_at=yesterday.replace(hour=12, minute=5)))
    pending.create_pending("Responder comentários")
    data = client.get("/api/today").json()
    kinds = [w["kind"] for w in data["waiting"]]
    assert kinds.count("review") == 1 and "post" in kinds and "analytics" in kinds and kinds.count("offer") == 1
    rv = next(w for w in data["waiting"] if w["kind"] == "review")
    assert rv["where"] == f"/projetos/{review}?aba=roteiro" and "Farol" in rv["text"]
    assert [p["text"] for p in data["pending"]] == ["Responder comentários"]
    assert data["stock"][0]["channel_id"] == cid and data["stock"][0]["below"]
    assert set(data["routines"]) >= {"morning_time", "backup", "metrics", "last_backup", "last_morning", "enabled"}
    assert "steps" in data["time"] and "suggestions" in data["time"]
    assert set(data["yesterday"]) >= {"date", "published", "failed", "jobs"}


def test_routines_velocity_and_backup_routes(client, isolated_settings):
    state = client.get("/api/ops/routines").json()
    assert state["morning_time"] == "08:00" and state["backup"] is True and state["metrics"] is True
    assert state["enabled"] is False  # os testes desligam os workers
    changed = client.put("/api/ops/routines", json={"morning_time": "07:30", "metrics": False}).json()
    assert changed["morning_time"] == "07:30" and changed["metrics"] is False and changed["backup"] is True
    assert client.put("/api/ops/routines", json={"morning_time": "25:00"}).status_code == 422
    assert client.get("/api/ops/velocity?days=7").json()["days"] == 7
    assert client.get("/api/ops/velocity?days=0").status_code == 422
    morning = client.post("/api/ops/morning").json()
    state = routines.get_state()
    assert state["morning_day"] == routines.local_today() and state["last_morning_job"] == morning["id"]
    job = client.post("/api/ops/backup").json()
    assert job["kind"] == "backup"
    for _ in range(100):
        if (jobs.get_job(job["id"]) or {}).get("status") in ("done", "failed"):
            break
        _time.sleep(0.05)
    assert jobs.get_job(job["id"])["status"] == "done"
    assert client.get("/api/ops/backups").json()[0]["name"].startswith("studio-")
