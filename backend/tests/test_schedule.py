from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.db import session_scope
from studio.models import Publication
from studio.workers.publish.schedule import PostingPlan, Slot, next_free_slots

MONDAY_NOON_UTC = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # 09:00 de segunda em São Paulo


def _days_and_times(slots: list[datetime]) -> list[tuple[int, str]]:
    return [(s.weekday(), s.strftime("%H:%M")) for s in slots]


def test_next_free_slots_weekdays_lead_conflicts_and_timezone():
    plan = PostingPlan(timezone="America/Sao_Paulo", slots=[Slot(weekdays=[0, 2, 4], time="18:00"), Slot(weekdays=[5], time="10:30")])
    slots = next_free_slots(plan, [], after=MONDAY_NOON_UTC, count=4)
    assert _days_and_times(slots) == [(0, "18:00"), (2, "18:00"), (4, "18:00"), (5, "10:30")]
    assert slots[0].astimezone(UTC).hour == 21  # São Paulo = UTC-3
    # menos de 20 min de antecedência: o horário de hoje fica de fora
    late = datetime(2026, 11, 16, 20, 50, tzinfo=UTC)  # 17:50 em São Paulo
    assert next_free_slots(plan, [], after=late, count=1)[0].weekday() == 2
    # outra publicação do canal a 10 min do horário: pula
    taken = [datetime(2026, 11, 16, 21, 10, tzinfo=UTC)]
    assert next_free_slots(plan, taken, after=MONDAY_NOON_UTC, count=1)[0].weekday() == 2
    # outro fuso (Nova York em novembro = UTC-5)
    ny = PostingPlan(timezone="America/New_York", slots=[Slot(weekdays=[0], time="18:00")])
    assert next_free_slots(ny, [], after=MONDAY_NOON_UTC, count=1)[0].astimezone(UTC).hour == 23
    assert next_free_slots(PostingPlan(), [], after=MONDAY_NOON_UTC) == []


def test_datetimes_round_trip_in_utc(isolated_settings):
    from zoneinfo import ZoneInfo

    doc = service.create_project(title="T", style_id="misterios-short", topic="t")
    sp = datetime(2026, 11, 20, 18, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
    with session_scope() as s:
        s.add(Publication(id="pub_tz", project_id=doc.id, platform="youtube", language="pt-BR", driver="x",
                          status="scheduled", scheduled_at=sp))
    with session_scope() as s:
        back = s.get(Publication, "pub_tz").scheduled_at
    assert back == sp and back.utcoffset() == timedelta(0) and back.hour == 21
    # a API devolve horários com fuso (a interface não os lê como hora local)
    assert service.get_project_row(doc.id)["created_at"].endswith("+00:00")


def test_youtube_batch_uses_next_free_slots_per_channel(isolated_settings):
    from studio.models import Channel
    from studio.workers.publish.schedule import plan_youtube_batch, ready_for_youtube

    with session_scope() as s:
        s.add(Channel(id="chn_q", name="Fila", settings={"posting": {"timezone": "America/Sao_Paulo",
                                                                     "slots": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "18:00"}]}}))
        s.add(Channel(id="chn_vazio", name="Sem horários", settings={}))
    final = {"langs": {"pt-BR": {"video": "outputs/x/pt-BR/video.mp4", "duration": 60}}, "preview_only_providers": []}
    ids = {}
    for name, channel, extra in (("a", "chn_q", {}), ("b", "chn_q", {}), ("sem_canal", None, {}),
                                 ("previa", "chn_q", {"preview_only_providers": ["mock_tts"]}), ("vazio", "chn_vazio", {}),
                                 ("enviado", "chn_q", {})):
        doc = service.create_project(title=f"Vídeo {name}", style_id="misterios-short", topic="t", channel_id=channel)
        service.update_settings(doc.id, {"last_final": {**final, **extra}})
        ids[name] = doc.id
    with session_scope() as s:
        s.add(Publication(id="pub_env", project_id=ids["enviado"], platform="youtube", language="pt-BR",
                          driver="youtube_native", status="uploaded"))

    ready = {r["project_id"] for r in ready_for_youtube()}
    assert ids["enviado"] not in ready and ids["a"] in ready

    calls: list[tuple[str, dict, str]] = []

    def fake_submit(kind: str, payload: dict, project_id: str) -> dict:
        calls.append((kind, payload, project_id))
        return {"id": f"job_{len(calls)}"}

    items = [{"project_id": ids[n], "lang": "pt-BR"} for n in ("a", "b", "sem_canal", "previa", "vazio", "enviado")]
    results = plan_youtube_batch(items, fake_submit)
    by_project = {r["project_id"]: r for r in results}
    assert [c[2] for c in calls] == [ids["a"], ids["b"]]
    first, second = (datetime.fromisoformat(c[1]["publish_at"].replace("Z", "+00:00")) for c in calls)
    assert second - first == timedelta(days=1)  # um por dia, no horário do canal
    assert all(c[1]["privacy"] == "scheduled" for c in calls)
    assert "canal" in by_project[ids["sem_canal"]]["error"]
    assert "prévia" in by_project[ids["previa"]]["error"]
    assert "horários de postagem" in by_project[ids["vazio"]]["error"]
    assert "já enviado" in by_project[ids["enviado"]]["error"]


def test_plan_validation():
    with pytest.raises(ValueError):
        PostingPlan(timezone="Marte/Base")
    with pytest.raises(ValueError):
        Slot(time="25:00")
    with pytest.raises(ValueError):
        Slot(weekdays=[9])
    assert Slot(time="7:5").time == "07:05"


def test_channel_slots_agenda_and_manual_posts(isolated_settings):
    with TestClient(create_app()) as client:
        posting = {"timezone": "America/Sao_Paulo", "slots": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "18:00"}]}
        channel = client.post("/api/channels", json={"name": "Canal A", "posting": posting}).json()
        assert channel["posting"]["slots"][0]["time"] == "18:00"
        # a tela de canais sem `posting` não apaga os horários
        updated = client.put(f"/api/channels/{channel['id']}", json={"name": "Canal A2", "niche": "mistério"}).json()
        assert updated["name"] == "Canal A2" and updated["posting"]["slots"]
        bad = client.put(f"/api/channels/{channel['id']}", json={"name": "X", "posting": {"timezone": "Marte/Base"}})
        assert bad.status_code == 422

        free = client.get(f"/api/channels/{channel['id']}/slots?count=3").json()
        assert free["configured"] and len(free["slots"]) == 3

        doc = service.create_project(title="Vídeo 1", style_id="misterios-short", topic="t", channel_id=channel["id"])
        first = datetime.fromisoformat(free["slots"][0])
        now = datetime.now(UTC)
        with session_scope() as s:
            s.add(Publication(id="pub_a", project_id=doc.id, platform="youtube", language="pt-BR", driver="youtube_native",
                              status="scheduled", scheduled_at=first, url="https://youtu.be/a"))
            s.add(Publication(id="pub_b", project_id=doc.id, platform="tiktok", language="pt-BR", driver="manual_helper",
                              status="package_ready", details={"caption": "legenda"}))
            s.add(Publication(id="pub_c", project_id=doc.id, platform="youtube", language="pt-BR", driver="youtube_native",
                              status="uploaded", url="https://youtu.be/c"))
            s.add(Publication(id="pub_d", project_id=doc.id, platform="youtube", language="pt-BR", driver="youtube_native",
                              status="scheduled", scheduled_at=now - timedelta(days=2), url="https://youtu.be/d"))

        # o horário agendado deixa de ser oferecido
        again = client.get(f"/api/channels/{channel['id']}/slots?count=3").json()
        assert free["slots"][0] not in again["slots"]

        agenda = client.get("/api/agenda").json()
        assert [i["id"] for i in agenda["upcoming"]] == ["pub_a"]
        assert agenda["upcoming"][0]["channel_name"] == "Canal A2"
        assert [i["id"] for i in agenda["manual"]] == ["pub_b"]
        assert {i["id"] for i in agenda["recent"]} == {"pub_c", "pub_d"}
        assert next(i for i in agenda["recent"] if i["id"] == "pub_d")["status"] == "published"

        assert client.post("/api/publications/pub_b/published", json={"url": "ftp://x"}).status_code == 422
        marked = client.post("/api/publications/pub_b/published", json={"url": "https://www.tiktok.com/@canal/video/1"}).json()
        assert marked["status"] == "published"
        agenda = client.get("/api/agenda").json()
        assert not agenda["manual"]
        assert {i["id"] for i in agenda["recent"]} == {"pub_b", "pub_c", "pub_d"}
        assert client.post("/api/publications/nao-existe/published", json={}).status_code == 404


def test_youtube_batch_respects_the_upload_quota(isolated_settings):
    from studio.models import Channel
    from studio.workers.publish import quota
    from studio.workers.publish.schedule import plan_youtube_batch

    with session_scope() as s:
        s.add(Channel(id="chn_q", name="Fila", settings={"posting": {"timezone": "America/Sao_Paulo",
                                                                     "slots": [{"weekdays": [0, 1, 2, 3, 4, 5, 6], "time": "18:00"}]}}))
    final = {"langs": {"pt-BR": {"video": "outputs/x/pt-BR/video.mp4", "duration": 60}}, "preview_only_providers": []}
    ids = []
    for n in range(3):
        doc = service.create_project(title=f"Vídeo {n}", style_id="misterios-short", topic="t", channel_id="chn_q")
        service.update_settings(doc.id, {"last_final": final})
        ids.append(doc.id)
    items = [{"project_id": i, "lang": "pt-BR"} for i in ids]
    submitted: list[str] = []

    def fake_submit(kind: str, payload: dict, project_id: str) -> dict:
        submitted.append(project_id)
        return {"id": f"job_{len(submitted)}"}

    quota.spend("videos.insert", calls=quota.UPLOAD_LIMIT - 2)  # restam 2 envios hoje
    results = plan_youtube_batch(items, fake_submit)
    assert submitted == ids[:2]
    assert "cota de envios" in results[2]["error"] and "job_id" not in results[2]

    quota.spend("videos.insert", calls=2)  # acabou: nada entra na fila
    submitted.clear()
    results = plan_youtube_batch(items[2:], fake_submit)
    assert submitted == [] and "envios de vídeo" in results[0]["error"]
