"""Séries com piloto automático: cadastro, episódios da lista ou das ideias, estoque, portão de revisão, rotina e
página Hoje."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Idea, Job, PendingTask, Project, Publication, Series, SeriesEpisode
from studio.workers import jobs
from studio.workers.ops import routines
from studio.workers.ops.today import summary
from studio.workers.series import core

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


class FakeSubmit:
    """Fila falsa: só anota os jobs pedidos (o piloto de verdade roda no teste de ponta a ponta)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    def __call__(self, kind: str, payload: dict[str, Any] | None = None, project_id: str | None = None,
                 **_: Any) -> dict[str, Any]:
        self.calls.append((kind, dict(payload or {}), project_id))
        return {"id": f"job_fake{len(self.calls)}"}


@pytest.fixture
def inline_runner():
    """Troca a fila por uma que roda o job na hora (como o comando `rotinas`)."""

    class InlineRunner(jobs.JobRunner):
        def submit(self, kind, payload=None, project_id=None, *, run_inline=False):
            return super().submit(kind, payload, project_id, run_inline=True)

    previous = jobs._runner
    jobs._runner = InlineRunner(workers=1)
    yield jobs._runner
    jobs._runner = previous


def _channel(name: str = "Mistérios", **fields) -> str:
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name=name, default_style_id="misterios-short", **fields))
    return cid


def _series(channel_id: str, **fields) -> dict[str, Any]:
    data = {"channel_id": channel_id, "name": "Faróis malditos", "theme": "faróis abandonados do Brasil",
            "stock_target": 2, "max_per_run": 2, "topics": ["O farol da ilha Rasa", "O farol de Santa Marta",
                                                            "O farol do Calcanhar"], **fields}
    return core.create_series(data)


def _idea(channel_id: str, title: str, status: str = "aprovada", score: float | None = None, fmt: str = "",
          angle: str = "") -> str:
    iid = new_id("ide")
    with session_scope() as s:
        s.add(Idea(id=iid, channel_id=channel_id, title=title, status=status, score=score, format=fmt, angle=angle))
    return iid


# ----------------------------------------------------------------------------- cadastro


def test_create_validates(isolated_settings):
    cid = _channel()
    with pytest.raises(core.SeriesError):
        core.create_series({"channel_id": "chn_naoexiste", "name": "X"})
    with pytest.raises(core.SeriesError):
        core.create_series({"channel_id": cid, "name": "  "})
    with pytest.raises(core.SeriesError):
        core.create_series({"channel_id": cid, "name": "X", "style_id": "estilo-que-nao-existe"})
    with pytest.raises(core.SeriesError):
        core.create_series({"channel_id": cid, "name": "X", "stock_target": 99})
    with pytest.raises(core.SeriesError):
        core.create_series({"channel_id": cid, "name": "X", "source": "rss"})
    item = core.create_series({"channel_id": cid, "name": " Faróis ", "topics": "  um   tema \n\n dois\n"})
    assert item["name"] == "Faróis"
    assert item["topics"] == ["um tema", "dois"]
    assert item["autopilot"] is False and item["active"] is True
    assert item["channel_name"] == "Mistérios"
    assert item["counts"]["open"] == 0 and item["next_number"] == 1


def test_list_topics_fill_stock_and_stop_at_target(isolated_settings):
    cid = _channel()
    item = _series(cid)
    submit = FakeSubmit()
    result = core.run_series(item["id"], manual=True, now=NOW, submit=submit)
    assert [e["number"] for e in result["created"]] == [1, 2]
    assert [e["topic"] for e in result["created"]] == ["O farol da ilha Rasa", "O farol de Santa Marta"]
    assert "2 episódios novos" in result["message"]
    # cada episódio vira um projeto com o briefing da série e o job do piloto SEM pular portão
    assert [k for k, _, _ in submit.calls] == ["autopilot", "autopilot"]
    assert all("skip_gates" not in payload for _, payload, _ in submit.calls)
    first = service.get_project_row(result["created"][0]["project_id"])
    assert first["settings"]["gates"] == {"script": True, "storyboard": True, "preview": True, "publish": True}
    assert first["settings"]["series"] == {"id": item["id"], "name": "Faróis malditos", "number": 1}
    assert first["channel_id"] == cid and first["style_id"] == "misterios-short"
    doc = service.get_doc(first["id"])
    assert doc.topic == "O farol da ilha Rasa"
    assert 'Episódio 1 da série "Faróis malditos"' in doc.brief and "faróis abandonados" in doc.brief

    after = core.get_series(item["id"])
    assert after["topics"] == ["O farol do Calcanhar"]  # os temas usados saem da fila
    assert after["counts"]["open"] == 2 and after["below"] is False
    assert after["coverage_days"] == 14  # 2 episódios, um por semana
    assert [e["number"] for e in after["episodes"]] == [2, 1]

    again = core.run_series(item["id"], manual=True, now=NOW, submit=submit)
    assert again["created"] == [] and again["message"] == "estoque em dia (2 de 2)"
    assert len(submit.calls) == 2


def test_max_per_run_and_explicit_count(isolated_settings):
    cid = _channel()
    item = _series(cid, stock_target=3, max_per_run=1)
    submit = FakeSubmit()
    assert len(core.run_series(item["id"], manual=True, now=NOW, submit=submit)["created"]) == 1
    # "+1 episódio" cria mesmo com o estoque em dia
    core.update_series(item["id"], {"stock_target": 0})
    assert len(core.run_series(item["id"], manual=True, count=1, now=NOW, submit=submit)["created"]) == 1
    assert core.get_series(item["id"])["counts"]["open"] == 2


def test_never_publishes(isolated_settings, inline_runner):
    """De ponta a ponta com o LLM simulado: o episódio para no roteiro esperando revisão e nada é publicado."""
    cid = _channel()
    item = _series(cid, stock_target=1, max_per_run=1, autopilot=True)
    result = core.run_series(item["id"], now=NOW)
    pid = result["created"][0]["project_id"]
    row = service.get_project_row(pid)
    assert row["status"] == "script_review"
    job = jobs.get_job(result["created"][0]["job_id"])
    assert job["status"] == "done" and job["result"]["stopped_at"] == "script"
    with session_scope() as s:
        assert s.query(Publication).count() == 0
    ep = core.get_series(item["id"])["episodes"][0]
    assert ep["state"] == "revisao" and ep["open"] is True
    # a página Hoje mostra a revisão (em "esperando você") e a série
    today = summary(now=NOW)
    assert any(w["kind"] == "review" and w["project_id"] == pid for w in today["waiting"])
    [serie] = today["series"]
    assert serie["review"] == 1 and serie["open"] == 1 and serie["below"] is False and serie["topics_left"] == 2


# ----------------------------------------------------------------------------- ideias


def test_ideas_source_picks_best_approved_idea(isolated_settings):
    cid = _channel()
    other = _channel("Outro")
    _idea(cid, "Ideia nova sem decisão", status="nova", score=10)
    _idea(cid, "Ideia rejeitada", status="rejeitada", score=9)
    _idea(other, "Farol de outro canal", score=9.5)
    _idea(cid, "Farol longo demais", score=9, fmt="long")
    low = _idea(cid, "Farol de nota baixa", score=3)
    best = _idea(cid, "Farol de nota alta", score=8, fmt="short", angle="a luz que acende sozinha")
    _idea(cid, "Castelo assombrado", score=9.9)  # sem a palavra do filtro
    item = _series(cid, source="ideas", format="short", idea_filter="farol", stock_target=3, max_per_run=3)
    submit = FakeSubmit()
    result = core.run_series(item["id"], manual=True, now=NOW, submit=submit)
    assert [e["idea_id"] for e in result["created"]] == [best, low]
    assert "acabaram os temas" in result["message"]
    with session_scope() as s:
        idea = s.get(Idea, best)
        assert idea.status == "em_producao" and idea.project_id == result["created"][0]["project_id"]
    doc = service.get_doc(result["created"][0]["project_id"])
    assert "Ângulo: a luz que acende sozinha" in doc.brief
    # a lista da série não muda com a fonte "ideias"
    assert core.get_series(item["id"])["topics"] == item["topics"]


# ----------------------------------------------------------------------------- sem temas


def _open_no_topic(series_id: str) -> list[PendingTask]:
    with session_scope() as s:
        rows = (s.query(PendingTask).filter(PendingTask.status == "aberta",
                                            PendingTask.auto_key.like(f"serie:{series_id}:%")).all())
        s.expunge_all()
    return rows


def test_out_of_topics_opens_one_pending_and_closes_it(isolated_settings):
    cid = _channel()
    item = _series(cid, topics=[])
    submit = FakeSubmit()
    result = core.run_series(item["id"], manual=True, now=NOW, submit=submit)
    assert result["created"] == [] and result["message"] == "sem temas para o próximo episódio"
    [pend] = _open_no_topic(item["id"])
    assert "Faróis malditos" in pend.text and pend.where_hint == "/series" and pend.channel_id == cid
    # no dia seguinte a pendência do dia substitui a antiga
    core.run_series(item["id"], manual=True, now=NOW + timedelta(days=1), submit=submit)
    assert len(_open_no_topic(item["id"])) == 1
    core.update_series(item["id"], {"topics": ["Tema novo"]})
    assert core.run_series(item["id"], manual=True, now=NOW, submit=submit)["created"]
    assert _open_no_topic(item["id"]) == []


# ----------------------------------------------------------------------------- estoque e situação


def test_published_deleted_and_failed_episodes(isolated_settings):
    cid = _channel()
    item = _series(cid, stock_target=3, max_per_run=3)
    submit = FakeSubmit()
    created = core.run_series(item["id"], manual=True, now=NOW, submit=submit)["created"]
    published, deleted, failed = (e["project_id"] for e in created)
    with session_scope() as s:
        s.add(Publication(id=new_id("pub"), project_id=published, platform="youtube", language="pt-BR",
                          driver="youtube", status="scheduled", scheduled_at=NOW + timedelta(days=1), details={}))
        s.delete(s.get(Project, deleted))
        s.add(Job(id="job_falhou1", kind="autopilot", project_id=failed, status="failed", payload={},
                  error="LLM sem cota"))
        ep = s.query(SeriesEpisode).filter(SeriesEpisode.project_id == failed).one()
        ep.job_id = "job_falhou1"
    info = core.get_series(item["id"])
    states = {e["project_id"]: e["state"] for e in info["episodes"]}
    assert states == {published: "publicado", deleted: "apagado", failed: "falhou"}
    failed_ep = next(e for e in info["episodes"] if e["state"] == "falhou")
    assert failed_ep["error"] == "LLM sem cota"
    assert info["counts"]["open"] == 1 and info["below"] is True
    # o estoque volta a encher no lugar do que saiu da prateleira (a fila tinha acabado: põe temas novos)
    core.update_series(item["id"], {"topics": ["A", "B", "C"]})
    again = core.run_series(item["id"], manual=True, now=NOW, submit=submit)
    assert len(again["created"]) == 2


# ----------------------------------------------------------------------------- rotina


def test_morning_runs_only_series_with_autopilot(isolated_settings, monkeypatch):
    cid = _channel()
    on = _series(cid, name="Ligada", autopilot=True)
    off = _series(cid, name="Desligada", autopilot=False)
    paused = _series(cid, name="Pausada", autopilot=True, active=False)
    submit = FakeSubmit()
    monkeypatch.setattr(jobs, "_runner", None)
    monkeypatch.setattr(jobs.JobRunner, "submit", lambda self, *a, **k: submit(*a, **k))
    lines = routines.morning(NOW)
    assert any('série "Ligada": 2 episódios novos' in line for line in lines)
    assert core.get_series(on["id"])["counts"]["open"] == 2
    assert core.get_series(off["id"])["counts"]["open"] == 0
    assert core.get_series(paused["id"])["counts"]["open"] == 0
    # a pausada nem com o botão
    assert core.run_series(paused["id"], manual=True, submit=submit)["message"] == "série pausada"
    # rodada seguinte: estoque em dia, a manhã não diz nada
    assert not any("Ligada" in line for line in routines.morning(NOW))
    assert core.get_series(on["id"])["last_run_message"] == "estoque em dia (2 de 2)"


def test_series_step_failure_does_not_break_morning(isolated_settings, monkeypatch):
    def boom(now=None, submit=None):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(core, "run_autopilots", boom)
    lines = routines.morning(NOW)
    assert any(line.startswith("séries: falhou (RuntimeError") for line in lines)


# ----------------------------------------------------------------------------- API


def test_api_crud_and_run(isolated_settings, monkeypatch):
    cid = _channel()
    submit = FakeSubmit()
    monkeypatch.setattr(jobs, "_runner", None)
    monkeypatch.setattr(jobs.JobRunner, "submit", lambda self, *a, **k: submit(*a, **k))
    with TestClient(create_app()) as client:
        assert client.post("/api/series", json={"channel_id": cid, "name": "X", "stock_target": 50}).status_code == 422
        assert client.post("/api/series", json={"channel_id": "nada", "name": "X"}).status_code == 422
        r = client.post("/api/series", json={"channel_id": cid, "name": "Faróis", "topics": ["Um", "Dois"],
                                             "cadence_days": 3, "stock_target": 1})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert client.get("/api/series").json()[0]["id"] == sid
        assert client.get(f"/api/series?channel_id={cid}").json()[0]["cadence_days"] == 3
        r = client.put(f"/api/series/{sid}", json={"autopilot": True, "style_id": None, "name": None})
        assert r.status_code == 200 and r.json()["autopilot"] is True and r.json()["name"] == "Faróis"
        assert client.put(f"/api/series/{sid}", json={"format": "medio"}).status_code == 422
        r = client.post(f"/api/series/{sid}/run", json={})
        assert r.status_code == 200, r.text
        body = r.json()
        assert len(body["created"]) == 1 and body["series"]["counts"]["open"] == 1
        r = client.post(f"/api/series/{sid}/run", json={"count": 1})
        assert len(r.json()["created"]) == 1 and r.json()["series"]["topics"] == []
        assert client.get("/api/today").json()["series"][0]["open"] == 2
        assert client.get("/api/series/ser_nada").status_code == 404
        assert client.delete(f"/api/series/{sid}").json() == {"deleted": True}
        assert client.get(f"/api/series/{sid}").status_code == 404
    # os projetos dos episódios ficam
    with session_scope() as s:
        assert s.query(Project).count() == 2


def test_deleting_channel_removes_its_series(isolated_settings):
    cid = _channel()
    item = _series(cid)
    core.run_series(item["id"], manual=True, now=NOW, submit=FakeSubmit())
    with session_scope() as s:
        s.delete(s.get(Channel, cid))
    with session_scope() as s:
        assert s.query(Series).count() == 0 and s.query(SeriesEpisode).count() == 0
