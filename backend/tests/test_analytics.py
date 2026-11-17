"""Bloco A (ADR 0002): métricas, variáveis, experimentos, sugestões, coleta simulada e rotas de desempenho."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.db import session_scope
from studio.models import Channel, Idea, Project, Publication, VideoMetric
from studio.providers import credentials
from studio.workers.analytics import collect as col
from studio.workers.analytics import experiments as ex
from studio.workers.analytics import metrics
from studio.workers.analytics import variables as var
from studio.workers.publish import youtube as yt

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
YT_CHANNEL = "UCcanalmetricas123456789"


def _channel(cid: str = "ch_a", tz: str = "America/Sao_Paulo", **settings) -> str:
    with session_scope() as s:
        s.add(Channel(id=cid, name=f"Canal {cid}", settings={"posting": {"timezone": tz, "slots": []}, **settings}))
    return cid


def _project(pid: str, channel_id: str | None = "ch_a", style: str = "misterios-short", duration: float | None = None,
             parent_id: str | None = None, tipo: str | None = None) -> str:
    settings: dict = {}
    if duration is not None:
        settings["last_final"] = {"langs": {"pt-BR": {"video": "x.mp4", "duration": duration}}}
    if tipo:
        settings["short_of"] = {"tipo": tipo}
    with session_scope() as s:
        s.add(Project(id=pid, title=f"Projeto {pid}", channel_id=channel_id, style_id=style, settings=settings,
                      parent_id=parent_id))
    return pid


def _publish(pid: str, youtube_id: str, scheduled_at: datetime | None = None) -> str:
    pub_id = f"pub_{youtube_id}"
    with session_scope() as s:
        s.add(Publication(id=pub_id, project_id=pid, platform="youtube", language="pt-BR", driver="youtube_native",
                          status="uploaded", external_id=youtube_id, scheduled_at=scheduled_at))
    return pub_id


# --------------------------------------------------------------------------------------------
# Métricas
# --------------------------------------------------------------------------------------------


def test_upsert_coalesce_and_link_with_publication():
    _channel()
    _project("prj_1")
    pub_id = _publish("prj_1", "vid1")
    first = metrics.upsert_metric("vid1", "2026-11-14", "youtube_api", views=100, likes=5, pct_watched=None)
    assert first["publication_id"] == pub_id and first["project_id"] == "prj_1" and first["channel_id"] == "ch_a"
    # A coleta nunca apaga número com vazio; um número novo entra.
    again = metrics.upsert_metric("vid1", "2026-11-14", "youtube_api", views=150, likes=None, pct_watched=None)
    assert again["views"] == 150 and again["likes"] == 5 and again["pct_watched"] is None
    # À mão dá para preencher o que faltava.
    manual = metrics.upsert_metric("vid1", "2026-11-14", "manual", pct_watched=61.5, ctr=4.2)
    assert manual["views"] == 150 and manual["pct_watched"] == 61.5 and manual["source"] == "manual"
    with session_scope() as s:
        assert s.query(VideoMetric).count() == 1
    metrics.upsert_metric("vid1", "2026-11-15", "youtube_api", views=180)
    latest = metrics.latest_metrics()
    assert latest["vid1"]["day"] == "2026-11-15" and latest["vid1"]["views"] == 180
    assert metrics.latest_metrics("ch_a").keys() == {"vid1"}
    assert metrics.latest_metrics("outro") == {}


def test_age_hours_from_published_and_unknown_field():
    row = metrics.upsert_metric("solto", None, "manual", published_at=datetime(2026, 11, 12, 12, tzinfo=UTC),
                                now=NOW, views=10)
    assert row["age_hours"] == 72.0 and row["day"] == "2026-11-15" and row["project_id"] is None
    with pytest.raises(ValueError):
        metrics.upsert_metric("solto", None, "manual", bogus=1)


# --------------------------------------------------------------------------------------------
# Variáveis
# --------------------------------------------------------------------------------------------


def test_bands_same_cuts_as_canais():
    assert var.duration_band(34.9) == "até 35 s"
    assert var.duration_band(35) == "35 a 45 s" and var.duration_band(45) == "35 a 45 s"
    assert var.duration_band(45.1) == "mais de 45 s"
    assert var.score_band(7.4) == "nota abaixo de 7,5"
    assert var.score_band(7.5) == "nota 7,5 a 8" and var.score_band(7.99) == "nota 7,5 a 8"
    assert var.score_band(8) == "nota 8+"
    assert var.parse_iso_duration("PT1M5S") == 65 and var.parse_iso_duration("PT2H") == 7200
    assert var.parse_iso_duration("") is None
    assert var.video_format("long", 60) == "short" and var.video_format("long", 181) == "long"
    assert var.video_format("short", None) == "short" and var.video_format(None, None) == "desconhecido"


def test_hour_and_day_in_channel_timezone():
    # 02:00 UTC de segunda = 23:00 de domingo em São Paulo: noite e fim de semana.
    when = datetime(2026, 11, 16, 2, 0, tzinfo=UTC)
    sp = var.video_variables(published_at=when, timezone="America/Sao_Paulo")
    assert sp["hora"] == "noite (18-24h)" and sp["dia"] == "fim de semana"
    tokyo = var.video_variables(published_at=when, timezone="Asia/Tokyo")  # 11:00 de segunda
    assert tokyo["hora"] == "manhã (6-12h)" and tokyo["dia"] == "dia útil"
    assert var.video_variables(published_at=datetime(2026, 11, 16, 8, tzinfo=UTC),
                               timezone="America/Sao_Paulo")["hora"] == "madrugada (0-6h)"
    assert var.video_variables(published_at=datetime(2026, 11, 16, 18, tzinfo=UTC),
                               timezone="America/Sao_Paulo")["hora"] == "tarde (12-18h)"


def test_load_videos_reads_duration_score_type_and_origin():
    _channel(tz="America/Sao_Paulo")
    _project("prj_longo", style="curiosidades-longo", duration=600)
    _project("prj_s", duration=40, parent_id="prj_longo", tipo="lista")
    _publish("prj_s", "vidS", scheduled_at=datetime(2026, 11, 10, 21, 0, tzinfo=UTC))  # 18:00 em SP, terça
    _publish("prj_longo", "vidL")
    with session_scope() as s:
        s.add(Idea(id="ide_1", title="Ideia", score=7.6, project_id="prj_s"))
    metrics.upsert_metric("vidS", "2026-11-15", "youtube_api", views=500, age_hours=100)
    videos = {v["youtube_id"]: v for v in var.load_videos("ch_a")}
    s_vars = videos["vidS"]["variables"]
    assert s_vars == {"formato": "short", "hora": "noite (18-24h)", "dia": "dia útil", "faixa_duracao": "35 a 45 s",
                      "faixa_nota": "nota 7,5 a 8", "tipo": "lista", "origem": "corte do longo", "estilo": "misterios-short"}
    assert videos["vidS"]["metric"]["views"] == 500
    # tipo, origem e faixa de duração são só de Short: o longo fica fora dessas variáveis.
    assert videos["vidL"]["format"] == "long" and "origem" not in videos["vidL"]["variables"]
    assert "tipo" not in videos["vidL"]["variables"] and "faixa_nota" not in videos["vidL"]["variables"]
    assert "faixa_duracao" not in videos["vidL"]["variables"]
    assert var.load_videos("outro") == []


# --------------------------------------------------------------------------------------------
# Experimentos
# --------------------------------------------------------------------------------------------


def _v(yid: str, value: str, views: int | None, age: float = 100, fmt: str = "short", score: float | None = None,
       **extra) -> dict:
    metric = None if views is None else {"views": views, "age_hours": age, "pct_watched": None, "ctr": None,
                                         "avg_view_s": None, "likes": None}
    return {"youtube_id": yid, "project_id": extra.get("project_id"), "format": fmt, "score": score, "title": yid,
            "variables": {"hora": value, **extra.get("vars", {})}, "metric": metric}


EXP = {"id": "exp_1", "variable": "hora", "variants": ["manhã (6-12h)", "noite (18-24h)"], "metric": "views",
       "project_ids": []}


def test_evaluate_early_with_two_videos_in_a_variant():
    videos = [_v(f"m{i}", "manhã (6-12h)", 1000) for i in range(3)] + [_v(f"n{i}", "noite (18-24h)", 100) for i in range(2)]
    ev = ex.evaluate(EXP, videos=videos)
    assert ev["text"] == "cedo: precisa de 3+ vídeos por variante na idade mínima (faltam: noite (18-24h) (2))"
    assert ev["winner"] is None and ev["young"] == 0


def test_evaluate_winner_with_13x_or_more():
    videos = [_v(f"m{i}", "manhã (6-12h)", 1400) for i in range(3)] + [_v(f"n{i}", "noite (18-24h)", 1000) for i in range(3)]
    ev = ex.evaluate(EXP, videos=videos)
    assert ev["text"] == "manhã (6-12h) na frente: 1,4x a mediana de noite (18-24h)"
    assert ev["winner"] == "manhã (6-12h)"
    assert ev["variants"][0] == {"variant": "manhã (6-12h)", "n": 3, "median": 1400.0}


def test_evaluate_no_clear_difference_below_13x():
    videos = [_v(f"m{i}", "manhã (6-12h)", 1200) for i in range(3)] + [_v(f"n{i}", "noite (18-24h)", 1000) for i in range(3)]
    ev = ex.evaluate(EXP, videos=videos)
    assert ev["text"] == "sem diferença clara (menos de 1,3x)" and ev["winner"] is None


def test_evaluate_young_videos_stay_out():
    videos = [_v(f"m{i}", "manhã (6-12h)", 5000) for i in range(3)] + [_v(f"n{i}", "noite (18-24h)", 1000) for i in range(3)]
    videos += [_v("jovem", "noite (18-24h)", 99999, age=50), _v("sem", "noite (18-24h)", None),
               _v("longo", "noite (18-24h)", 99999, age=100, fmt="long"),  # longo precisa de 168 h
               _v("fora", "tarde (12-18h)", 1)]  # valor fora das variantes nem conta
    ev = ex.evaluate(EXP, videos=videos)
    assert ev["young"] == 3
    assert {g["variant"]: g["n"] for g in ev["variants"]} == {"manhã (6-12h)": 3, "noite (18-24h)": 3}
    assert ev["winner"] == "manhã (6-12h)" and "5,0x" in ev["text"]


def test_evaluate_views_per_day_and_project_filter():
    exp = {**EXP, "metric": "views_dia", "project_ids": ["p1", "p2", "p3", "p4", "p5", "p6"]}
    videos = [_v(f"m{i}", "manhã (6-12h)", 2400, age=240, project_id=f"p{i + 1}") for i in range(3)]  # 240/dia
    videos += [_v(f"n{i}", "noite (18-24h)", 960, age=96, project_id=f"p{i + 4}") for i in range(3)]  # 240/dia
    videos += [_v("x", "noite (18-24h)", 1, project_id="outro")]
    ev = ex.evaluate(exp, videos=videos)
    assert ev["text"] == "sem diferença clara (menos de 1,3x)"
    assert all(g["n"] == 3 and g["median"] == 240 for g in ev["variants"])


def test_spearman_like_canais():
    assert ex.spearman([1, 2, 3, 4], [1, 2, 3, 4]) is None
    assert ex.spearman([1, 2, 3, 4, 5], [10, 20, 30, 40, 50]) == 1.0
    assert ex.spearman([1, 2, 3, 4, 5], [5, 4, 3, 2, 1]) == -1.0


def test_results_and_suggestions_with_8_shorts():
    videos = [_v(f"m{i}", "manhã (6-12h)", 3000, score=7 + i * 0.3) for i in range(3)]
    videos += [_v(f"t{i}", "tarde (12-18h)", 1000, score=9 - i * 0.3) for i in range(3)]
    videos += [_v(f"n{i}", "noite (18-24h)", 200, score=8) for i in range(3)]
    videos += [_v("jovem", "noite (18-24h)", 1, age=10), _v("L", "x", 50000, age=500, fmt="long")]
    res = ex.results(videos=videos)
    assert res["shorts_n"] == 9 and res["overall_median"] == 1000
    hora = next(v for v in res["variables"] if v["variable"] == "hora")
    assert [g["value"] for g in hora["groups"]] == ["manhã (6-12h)", "tarde (12-18h)", "noite (18-24h)"]
    assert res["longs"] == [{"youtube_id": "L", "title": "L", "views": 50000, "days": 21}]
    sug = ex.suggestions(res, [{"id": "exp_9", "variable": "hora"}])
    texts = [s["text"] for s in sug]
    assert texts[0] == "Resultado: Shorts com hora = manhã (6-12h) têm 3,0x a mediana de views (n=3). O experimento exp_9 já acompanha."
    assert texts[1].startswith("Alerta: Shorts com hora = noite (18-24h) ficaram em 200 views de mediana, contra 1 mil no geral (n=3).")
    assert len(sug) <= 4
    # Com menos de 8 Shorts não há sugestão de resultado.
    assert ex.suggestions(ex.results(videos=videos[:6])) == []


def test_suggestion_calibration_and_cut():
    res = {"overall_median": 100, "shorts_n": 3, "variables": [],
           "calibration": {"n": 9, "rho": 0.1, "min": 8.2, "max": 9.1, "cut": 8.0}}
    (only,) = ex.suggestions(res)
    assert "o corte não dá para avaliar" in only["text"] and "correlação 0,1" in only["text"]
    res["calibration"]["cut"] = None
    assert ex.suggestions(res)[0]["text"].endswith("revise os critérios ou o corte.")
    res["calibration"]["n"] = 7
    assert ex.suggestions(res) == []


# --------------------------------------------------------------------------------------------
# Coleta simulada (Data API ok, Analytics 403)
# --------------------------------------------------------------------------------------------


def _link_channel(cid: str = "ch_a") -> None:
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    yt.save_channel_token(YT_CHANNEL, "rt", "Canal")
    with session_scope() as s:
        row = s.get(Channel, cid)
        row.settings = {**row.settings, "youtube": {"channel_id": YT_CHANNEL, "source": "oauth",
                                                    "recent_videos": [{"video_id": "vRec", "title": "Recente"}]}}


def _items() -> dict:
    return {"items": [
        {"id": "vPub", "snippet": {"title": "Publicado", "publishedAt": "2026-11-10T12:00:00Z"},
         "contentDetails": {"duration": "PT40S"}, "status": {"privacyStatus": "public"},
         "statistics": {"viewCount": "1234", "likeCount": "50", "commentCount": "3"}},
        {"id": "vPriv", "snippet": {"title": "Agendado", "publishedAt": "2026-11-14T12:00:00Z"},
         "contentDetails": {"duration": "PT30S"}, "status": {"privacyStatus": "private", "publishAt": "2026-11-20T21:00:00Z"},
         "statistics": {"viewCount": "0"}},
        {"id": "vRec", "snippet": {"title": "Recente", "publishedAt": "2026-11-13T12:00:00Z"},
         "contentDetails": {"duration": "PT5M"}, "status": {"privacyStatus": "public"},
         "statistics": {"viewCount": "77"}},
    ]}


def test_collect_data_api_ok_analytics_403(monkeypatch: pytest.MonkeyPatch):
    _channel()
    _project("prj_1")
    _publish("prj_1", "vPub")
    _project("prj_2")
    _publish("prj_2", "vPriv")
    _link_channel()
    seen: dict[str, list] = {"videos": [], "analytics": []}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if url.startswith(f"{yt.API}/videos"):
            q = parse_qs(urlparse(url).query)
            seen["videos"].append(q)
            return httpx.Response(200, json=_items())
        if url.startswith(yt.ANALYTICS_API):
            seen["analytics"].append(parse_qs(urlparse(url).query))
            return httpx.Response(403, json={"error": {
                "code": 403, "message": "Request had insufficient authentication scopes.", "status": "PERMISSION_DENIED",
                "errors": [{"message": "Insufficient Permission", "reason": "insufficientPermissions"}]}})
        return httpx.Response(404)

    monkeypatch.setattr(yt, "new_client", lambda timeout=60: httpx.Client(transport=httpx.MockTransport(handler)))
    out = col.collect("ch_a", now=NOW)
    (summary,) = out["channels"]
    assert summary["state"] == "sem_escopo" and summary["scope"] == "missing"
    assert summary["saved"] == 2 and summary["skipped"] == 1
    q = seen["videos"][0]
    assert q["part"] == ["statistics,snippet,contentDetails,status"]
    assert set(q["id"][0].split(",")) == {"vPub", "vPriv", "vRec"}
    a = seen["analytics"][0]
    assert a["ids"] == ["channel==MINE"] and a["dimensions"] == ["video"] and a["startDate"] == ["2026-11-10"]
    assert a["metrics"] == ["views,averageViewDuration,averageViewPercentage,subscribersGained"]
    # O relatório por vídeo exige sort e maxResults (até 200).
    assert a["sort"] == ["-views"] and a["maxResults"] == ["200"]
    latest = metrics.latest_metrics()
    assert "vPriv" not in latest  # privado/agendado sem views fica fora (lição #205)
    pub = latest["vPub"]
    assert pub["views"] == 1234 and pub["likes"] == 50 and pub["comments"] == 3 and pub["age_hours"] == 120.0
    assert pub["pct_watched"] is None and pub["avg_view_s"] is None  # vazio, nunca 0 (lição #202)
    assert pub["source"] == "youtube_api" and pub["project_id"] == "prj_1" and pub["channel_id"] == "ch_a"
    assert latest["vRec"]["channel_id"] == "ch_a" and latest["vRec"]["project_id"] is None
    with session_scope() as s:
        analytics = s.get(Channel, "ch_a").settings["analytics"]
    assert analytics["scope"] == "missing" and analytics["last_run"] == NOW.isoformat()
    assert analytics["videos"]["vPub"]["duration_s"] == 40
    videos = {v["youtube_id"]: v for v in var.load_videos("ch_a")}
    assert videos["vRec"]["format"] == "long" and videos["vRec"]["title"] == "Recente"


def test_collect_with_analytics_scope_fills_retention(monkeypatch: pytest.MonkeyPatch):
    _channel()
    _project("prj_1")
    _publish("prj_1", "vPub")
    _link_channel()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if url.startswith(f"{yt.API}/videos"):
            return httpx.Response(200, json=_items())
        if url.startswith(yt.ANALYTICS_API):
            return httpx.Response(200, json={
                "columnHeaders": [{"name": "video"}, {"name": "views"}, {"name": "averageViewDuration"},
                                  {"name": "averageViewPercentage"}, {"name": "subscribersGained"}],
                "rows": [["vPub", 1200, 31.5, 78.2, 4], ["vRec", 0, 0, 0, 0]],
            })
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = col.collect("ch_a", http=client, now=NOW)
    assert out["channels"][0]["state"] == "ok" and out["channels"][0]["scope"] == "ok"
    latest = metrics.latest_metrics()
    assert latest["vPub"]["pct_watched"] == 78.2 and latest["vPub"]["avg_view_s"] == 31.5
    assert latest["vPub"]["subscribers_gained"] == 4
    assert latest["vRec"]["pct_watched"] is None  # Analytics com 0 views ainda não chegou


def test_collect_without_token_or_public_only():
    _channel("ch_pub", youtube={"channel_id": YT_CHANNEL, "source": "public"})
    _channel("ch_none")
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    out = {c["channel_id"]: c for c in col.collect(http=http, now=NOW)["channels"]}
    assert out["ch_pub"]["state"] == "publico" and out["ch_none"]["state"] == "sem_token"


def test_scopes_include_analytics_but_not_readonly_on_global():
    assert yt.ANALYTICS_SCOPE in yt.SCOPES.split() and yt.ANALYTICS_SCOPE in yt.LINK_SCOPES.split()
    assert "youtube.readonly" not in yt.SCOPES


# --------------------------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------------------------


def test_routes_metrics_experiment_close_and_lesson():
    _channel()
    _project("prj_1")
    _publish("prj_1", "vid1")
    client = TestClient(create_app())

    r = client.post("/api/analytics/metrics", json={"youtube_id": "vid1", "day": "2026-11-14", "views": 300, "pct_watched": 55})
    assert r.status_code == 200 and r.json()["source"] == "manual" and r.json()["project_id"] == "prj_1"
    assert client.post("/api/analytics/metrics", json={"youtube_id": "vid1", "ctr": 120}).status_code == 422
    assert client.post("/api/analytics/metrics", json={"youtube_id": "vid1", "views": -1}).status_code == 422
    assert client.post("/api/analytics/metrics", json={"youtube_id": "vid1", "day": "14/11/2026"}).status_code == 422
    hist = client.get("/api/analytics/videos/vid1").json()
    assert hist["history"][0]["views"] == 300 and hist["video"]["project_id"] == "prj_1"
    assert client.get("/api/analytics/videos/nada").status_code == 404

    assert {v["id"] for v in client.get("/api/analytics/variables").json()} >= {"hora", "dia", "faixa_duracao", "tipo"}
    bad = client.post("/api/analytics/experiments", json={"hypothesis": "teste", "variable": "cor", "variants": ["a", "b"]})
    assert bad.status_code == 422
    assert client.post("/api/analytics/experiments", json={"hypothesis": "teste", "variable": "hora", "variants": ["a"]}).status_code == 422
    assert client.post("/api/analytics/experiments", json={"hypothesis": "teste", "variable": "hora", "variants": ["a", "b"],
                                                           "metric": "cliques"}).status_code == 422
    r = client.post("/api/analytics/experiments", json={"channel_id": "ch_a", "hypothesis": "manhã rende mais",
                                                        "variable": "hora", "variants": ["manhã (6-12h)", "noite (18-24h)"]})
    assert r.status_code == 200
    exp = r.json()
    assert exp["status"] == "aberto" and exp["evaluation"]["text"].startswith("cedo:")
    assert client.get(f"/api/analytics/experiments/{exp['id']}").json()["evaluation"] is not None
    upd = client.put(f"/api/analytics/experiments/{exp['id']}", json={"metric": "views_dia"}).json()
    assert upd["metric"] == "views_dia" and upd["variable"] == "hora"
    assert [e["id"] for e in client.get("/api/analytics/experiments?channel_id=ch_a").json()] == [exp["id"]]
    ov = client.get("/api/analytics/overview?channel_id=ch_a").json()
    assert ov["experiments"][0]["id"] == exp["id"] and ov["videos"][0]["youtube_id"] == "vid1"
    assert ov["collect"][0]["state"] == "nunca"
    assert client.get("/api/analytics/overview?channel_id=nada").status_code == 404

    closed = client.post(f"/api/analytics/experiments/{exp['id']}/close",
                         json={"result": "sem diferença", "learning": "hora não muda", "abandon": False}).json()
    assert closed["status"] == "concluido" and closed["closed_at"] and closed["learning"] == "hora não muda"
    assert client.post(f"/api/analytics/experiments/{exp['id']}/close", json={}).status_code == 409
    other = client.post("/api/analytics/experiments", json={"hypothesis": "outro", "variable": "dia",
                                                            "variants": ["dia útil", "fim de semana"]}).json()
    assert client.post(f"/api/analytics/experiments/{other['id']}/close", json={"abandon": True}).json()["status"] == "abandonado"
    assert client.get("/api/analytics/experiments/nada").status_code == 404

    assert client.post("/api/analytics/lessons", json={"scope": "planeta:x", "text": "abc"}).status_code == 422
    assert client.post("/api/analytics/lessons", json={"scope": "formato:medio", "text": "abc"}).status_code == 422
    r = client.post("/api/analytics/lessons", json={"scope": "canal:ch_a", "text": "hora não muda as views",
                                                    "origin": "experimento", "experiment_id": exp["id"]})
    assert r.status_code == 200
    lesson = r.json()
    assert lesson["status"] == "proposta"
    client.post("/api/analytics/lessons", json={"scope": "formato:short", "text": "gancho nos 2 primeiros segundos"})
    assert [x["id"] for x in client.get("/api/analytics/lessons?scope=canal:").json()] == [lesson["id"]]
    assert len(client.get("/api/analytics/lessons?status=proposta").json()) == 2
    dec = client.post(f"/api/analytics/lessons/{lesson['id']}/decide",
                      json={"status": "aplicada", "applied_where": "Agenda do canal"}).json()
    assert dec["status"] == "aplicada" and dec["applied_where"] == "Agenda do canal" and dec["decided_at"]
    assert client.post(f"/api/analytics/lessons/{lesson['id']}/decide", json={"status": "proposta"}).status_code == 422
    assert client.post("/api/analytics/lessons/nada/decide", json={"status": "descartada"}).status_code == 404


def test_collect_route_submits_job(monkeypatch: pytest.MonkeyPatch):
    _channel()
    calls = []
    monkeypatch.setattr("studio.workers.analytics.tasks.collect", lambda cid=None, progress=None: calls.append(cid) or {})
    from studio.workers.jobs import _REGISTRY

    assert "metrics_collect" in _REGISTRY
    client = TestClient(create_app())
    r = client.post("/api/analytics/collect", json={"channel_id": "ch_a"})
    assert r.status_code == 200 and r.json()["kind"] == "metrics_collect"
    assert client.post("/api/analytics/collect", json={"channel_id": "nada"}).status_code == 404


# --------------------------------------------------------------------------------------------
# Correções da revisão
# --------------------------------------------------------------------------------------------


def test_old_long_video_stays_out_of_short_only_experiment():
    """Experimento sobre `tipo` (só de Short): um longo antigo com muitas views não entra na variante "original"."""
    _channel()
    _project("prj_longo", style="curiosidades-longo", duration=600)
    _publish("prj_longo", "vidL")
    for i in range(3):
        _project(f"prj_o{i}", duration=40)
        _publish(f"prj_o{i}", f"vO{i}")
        metrics.upsert_metric(f"vO{i}", "2026-11-15", "youtube_api", views=1000, age_hours=200)
        _project(f"prj_l{i}", duration=40, tipo="lista")
        _publish(f"prj_l{i}", f"vL{i}")
        metrics.upsert_metric(f"vL{i}", "2026-11-15", "youtube_api", views=1000, age_hours=200)
    metrics.upsert_metric("vidL", "2026-11-15", "youtube_api", views=900000, age_hours=500)
    exp = {"id": "exp_t", "variable": "tipo", "variants": ["original", "lista"], "metric": "views", "project_ids": [],
           "channel_id": "ch_a"}
    ev = ex.evaluate(exp)
    assert {g["variant"]: (g["n"], g["median"]) for g in ev["variants"]} == {"original": (3, 1000.0), "lista": (3, 1000.0)}
    assert ev["winner"] is None and ev["young"] == 0


def test_manual_metric_gets_age_from_known_publication_and_counts():
    """Short com métrica só lançada à mão (canal sem token): a idade sai da publicação e o vídeo entra na conta."""
    _channel()
    client = TestClient(create_app())
    published = datetime.now(UTC) - timedelta(days=5)
    for i in range(3):
        _project(f"prj_m{i}", duration=40)
        _publish(f"prj_m{i}", f"vm{i}", scheduled_at=published)
        r = client.post("/api/analytics/metrics", json={"youtube_id": f"vm{i}", "views": 500})
        assert r.status_code == 200
        assert 119 <= r.json()["age_hours"] <= 121
    # Dia passado: a idade é medida até o fim daquele dia, não até agora.
    past = (published + timedelta(days=3)).astimezone().date()
    row = client.post("/api/analytics/metrics", json={"youtube_id": "vm0", "day": past.isoformat(), "likes": 3}).json()
    assert row["age_hours"] < 96
    res = ex.results("ch_a")
    assert res["shorts_n"] == 3 and res["overall_median"] == 500
    exp = {"id": "exp_m", "variable": "formato", "variants": ["short", "long"], "metric": "views", "project_ids": [],
           "channel_id": "ch_a"}
    assert ex.evaluate(exp)["young"] == 0
    # Vídeo sem publicação no Vulto: herda o canal da tela e aceita a idade digitada.
    solto = client.post("/api/analytics/metrics", json={"youtube_id": "fora1", "views": 10, "age_hours": 80,
                                                        "channel_id": "ch_a"}).json()
    assert solto["channel_id"] == "ch_a" and solto["age_hours"] == 80
    assert "fora1" in metrics.latest_metrics("ch_a")
    assert client.post("/api/analytics/metrics", json={"youtube_id": "fora2", "views": 1,
                                                       "channel_id": "nada"}).status_code == 404


def test_manual_ctr_survives_next_day_collect():
    metrics.upsert_metric("vc", "2026-11-14", "manual", ctr=5.5, impressions=2000, views=100)
    metrics.upsert_metric("vc", "2026-11-15", "youtube_api", views=180, age_hours=50)
    latest = metrics.latest_metrics()["vc"]
    assert latest["day"] == "2026-11-15" and latest["views"] == 180 and latest["ctr"] == 5.5
    assert latest["impressions"] == 2000 and latest["source"] == "youtube_api"


def test_collect_and_manual_use_the_same_local_day():
    # 23:30 em São Paulo de 14/11 = 02:30 UTC de 15/11: o dia gravado é o da hora local (mesma base do navegador).
    late = datetime(2026, 11, 15, 2, 30, tzinfo=UTC)
    row = metrics.upsert_metric("vd", None, "youtube_api", now=late, views=1)
    assert row["day"] == late.astimezone().date().isoformat() == metrics.day_of(late)


def _collect_with_analytics(analytics: httpx.Response, items: dict | None = None) -> dict:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if url.startswith(f"{yt.API}/videos"):
            return httpx.Response(200, json=items if items is not None else _items())
        if url.startswith(yt.ANALYTICS_API):
            return analytics
        return httpx.Response(404)

    return col.collect("ch_a", http=httpx.Client(transport=httpx.MockTransport(handler)), now=NOW)["channels"][0]


def test_collect_analytics_403_not_scope_is_visible_error():
    _channel(analytics={"scope": "ok", "state": "ok"})
    _project("prj_1")
    _publish("prj_1", "vPub")
    _link_channel()
    off = httpx.Response(403, json={"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "disabled",
                                              "errors": [{"reason": "accessNotConfigured"}]}})
    out = _collect_with_analytics(off)
    assert out["state"] == "erro" and out["scope"] == "ok"
    assert "ative a YouTube Analytics API" in out["error"]
    assert metrics.latest_metrics()["vPub"]["views"] == 1234  # a Data API grava mesmo assim
    quota = httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})
    assert "cota" in _collect_with_analytics(quota)["error"]
    broken = httpx.Response(400, json={"error": {"message": "The query is not supported."}})
    out = _collect_with_analytics(broken)
    assert out["state"] == "erro" and "Analytics falhou" in out["error"]
    ok = httpx.Response(200, json={"columnHeaders": [], "rows": []})
    assert _collect_with_analytics(ok)["state"] == "ok"


def test_collect_keeps_missing_scope_when_analytics_not_called():
    _channel(analytics={"scope": "missing", "state": "sem_escopo"})
    _project("prj_2")
    _publish("prj_2", "vPriv")
    _link_channel()
    only_private = {"items": [_items()["items"][1]]}
    out = _collect_with_analytics(httpx.Response(500), items=only_private)
    assert out["state"] == "sem_escopo" and out["scope"] == "missing"


def test_collect_prefers_youtube_dates_over_local_schedule():
    _channel()
    _project("prj_1")
    # Agendado no Vulto para depois, mas publicado à mão antes no YouTube.
    _publish("prj_1", "vPub", scheduled_at=datetime(2026, 11, 20, 12, tzinfo=UTC))
    _link_channel()
    _collect_with_analytics(httpx.Response(200, json={"columnHeaders": [], "rows": []}))
    assert metrics.latest_metrics()["vPub"]["age_hours"] == 120.0


def test_close_experiment_with_lesson_in_one_step_and_null_channel_in_overview():
    _channel()
    client = TestClient(create_app())
    todos = client.post("/api/analytics/experiments", json={"hypothesis": "vale para todos", "variable": "dia",
                                                            "variants": ["dia útil", "fim de semana"]}).json()
    ov = client.get("/api/analytics/overview?channel_id=ch_a").json()
    assert [e["id"] for e in ov["experiments"]] == [todos["id"]]
    assert [e["id"] for e in client.get("/api/analytics/experiments?channel_id=ch_a").json()] == [todos["id"]]
    closed = client.post(f"/api/analytics/experiments/{todos['id']}/close",
                         json={"result": "empate", "learning": "dia não importa", "save_lesson": True}).json()
    assert closed["status"] == "concluido"
    (lesson,) = client.get("/api/analytics/lessons").json()
    assert lesson["experiment_id"] == todos["id"] and lesson["scope"] == "global" and lesson["origin"] == "experimento"
    assert lesson["text"] == "dia não importa" and lesson["evidence"] == "vale para todos — empate"
