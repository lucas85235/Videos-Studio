"""Impressões, CTR e receita do YouTube na coleta (Reporting API + escopo opcional da receita), tudo simulado."""

from __future__ import annotations

from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from studio.db import session_scope
from studio.models import Channel, Project, Publication, RevenueEntry
from studio.providers import credentials
from studio.workers.analytics import collect as col
from studio.workers.analytics import metrics, reach, yt_revenue
from studio.workers.publish import youtube as yt
from studio.workers.visual import thumbs

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
YT_CHANNEL = "UCcanalreceita1234567890"
JOBS = f"{reach.REPORTING_API}/jobs"
MEDIA = "https://youtubereporting.googleapis.com/v1/media/rel"


def _setup(revenue: bool = False) -> None:
    with session_scope() as s:
        s.add(Channel(id="ch_a", name="Canal A", settings={"youtube": {"channel_id": YT_CHANNEL, "source": "oauth"}}))
        s.add(Project(id="prj_1", title="P1", channel_id="ch_a", style_id="misterios-short", settings={}))
        s.add(Publication(id="pub_1", project_id="prj_1", platform="youtube", language="pt-BR",
                          driver="youtube_native", status="uploaded", external_id="vPub"))
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    yt.save_channel_token(YT_CHANNEL, "rt", "Canal")
    if revenue:
        yt.set_revenue_scope(YT_CHANNEL, True)


VIDEOS = {"items": [{"id": "vPub", "snippet": {"title": "Publicado", "publishedAt": "2026-11-10T12:00:00Z"},
                     "contentDetails": {"duration": "PT8M"}, "status": {"privacyStatus": "public"},
                     "statistics": {"viewCount": "2000"}}]}
ANALYTICS_OK = {"columnHeaders": [{"name": "video"}, {"name": "views"}, {"name": "averageViewDuration"},
                                  {"name": "averageViewPercentage"}, {"name": "subscribersGained"}],
                "rows": [["vPub", 1900, 200, 41.0, 3]]}
CSV_DAY1 = ("date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
            f"20261111,{YT_CHANNEL},vPub,1000,0.05\n20261111,{YT_CHANNEL},vOutro,10,0.1\n")
CSV_DAY2 = ("date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
            f"20261112,{YT_CHANNEL},vPub,3000,0.03\n")


class Fake:
    """Google simulado: token, Data API, Analytics (retenção e receita) e Reporting API."""

    def __init__(self, reports: list[dict] | None = None, csvs: dict[str, str] | None = None,
                 jobs: list[dict] | None = None, revenue_status: int = 200) -> None:
        self.reports = reports or []
        self.csvs = csvs or {}
        self.jobs = jobs or []
        self.revenue_status = revenue_status
        self.created: list[dict] = []
        self.analytics: list[dict] = []
        self.report_queries: list[dict] = []
        self.downloads: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        q = parse_qs(urlparse(url).query)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if url.startswith(f"{yt.API}/videos"):
            return httpx.Response(200, json=VIDEOS)
        if url.startswith(yt.ANALYTICS_API):
            self.analytics.append(q)
            metrics_asked = q["metrics"][0]
            if "estimatedRevenue" in metrics_asked:
                if self.revenue_status != 200:
                    return httpx.Response(self.revenue_status, json={"error": {"errors": [{"reason": "forbidden"}]}})
                if q["dimensions"] == ["video"]:
                    return httpx.Response(200, json={"columnHeaders": [{"name": "video"}, {"name": "views"},
                                                                       {"name": "estimatedRevenue"}],
                                                     "rows": [["vPub", 2000, 3.5]]})
                return httpx.Response(200, json={"columnHeaders": [{"name": "day"}, {"name": "estimatedRevenue"}],
                                                 "rows": [["2026-10-30", 1.25], ["2026-10-31", 0.75],
                                                          ["2026-11-01", 2.0]]})
            return httpx.Response(200, json=ANALYTICS_OK)
        if url == JOBS and request.method == "GET":
            return httpx.Response(200, json={"jobs": self.jobs})
        if url == JOBS and request.method == "POST":
            import json

            self.created.append(json.loads(request.content))
            return httpx.Response(200, json={"id": "job1", "reportTypeId": reach.REPORT_TYPE})
        if url.startswith(f"{JOBS}/job1/reports"):
            self.report_queries.append(q)
            after = (q.get("createdAfter") or [""])[0]
            return httpx.Response(200, json={"reports": [r for r in self.reports if r["createTime"] > after]})
        if url.startswith(MEDIA):
            self.downloads.append(url)
            assert request.headers["Authorization"] == "Bearer at"
            return httpx.Response(200, text=self.csvs[url])
        return httpx.Response(404)


def _report(rid: str, day: str, created: str, host: str = MEDIA) -> dict:
    return {"id": rid, "jobId": "job1", "startTime": f"{day}T08:00:00Z", "endTime": f"{day}T08:00:00Z",
            "createTime": created, "downloadUrl": f"{host}/{rid}"}


def _run(fake: Fake, now: datetime = NOW) -> dict:
    return col.collect("ch_a", http=httpx.Client(transport=httpx.MockTransport(fake)), now=now)["channels"][0]


def _settings() -> dict:
    with session_scope() as s:
        return dict(s.get(Channel, "ch_a").settings)


# --------------------------------------------------------------------------------------------
# Impressões e CTR (Reporting API)
# --------------------------------------------------------------------------------------------


def test_first_collect_creates_reach_job_and_waits():
    _setup()
    fake = Fake()
    summary = _run(fake)
    assert summary["state"] == "ok" and summary["reach"] == "esperando"
    assert fake.created == [{"reportTypeId": "channel_reach_basic_a1", "name": reach.JOB_NAME}]
    assert _settings()["analytics_reach"]["job_id"] == "job1"
    latest = metrics.latest_metrics()["vPub"]
    assert latest["impressions"] is None and latest["ctr"] is None and latest["pct_watched"] == 41.0
    # Receita não foi escolhida ao vincular: nem é chamada.
    assert summary["revenue"] == "desligada"
    assert all("estimatedRevenue" not in q["metrics"][0] for q in fake.analytics)


def test_reach_reports_fill_impressions_and_ctr_without_double_count():
    _setup()
    reports = [_report("r1", "2026-11-11", "2026-11-13T10:00:00Z"), _report("r2", "2026-11-12", "2026-11-14T10:00:00Z")]
    csvs = {f"{MEDIA}/r1": CSV_DAY1, f"{MEDIA}/r2": CSV_DAY2}
    fake = Fake(reports=reports, csvs=csvs, jobs=[{"id": "job1", "reportTypeId": "channel_reach_basic_a1"}])
    summary = _run(fake)
    assert summary["reach"] == "ok", summary["reach_error"]
    assert not fake.created  # job que já existia é reaproveitado
    latest = metrics.latest_metrics()["vPub"]
    # 1000 × 5% + 3000 × 3% = 140 cliques em 4000 impressões = 3,5%
    assert latest["impressions"] == 4000 and latest["ctr"] == 3.5
    assert "vOutro" not in metrics.latest_metrics()  # vídeo fora da lista do canal não vira linha

    # Coleta de novo: só relatórios novos; o Google refez o dia 12 (id novo) → substitui, não soma.
    fake.reports.append(_report("r2b", "2026-11-12", "2026-11-15T10:00:00Z"))
    fake.csvs[f"{MEDIA}/r2b"] = CSV_DAY2.replace("3000,0.03", "2000,0.04")
    fake.downloads.clear()
    _run(fake)
    assert fake.report_queries[-1]["createdAfter"] == ["2026-11-14T10:00:00Z"]
    assert fake.downloads == [f"{MEDIA}/r2b"]
    latest = metrics.latest_metrics()["vPub"]
    assert latest["impressions"] == 3000 and latest["ctr"] == pytest.approx(130 / 3000 * 100, abs=0.01)


def test_reach_ctr_in_percent_and_foreign_download_url_ignored():
    _setup()
    csv_pct = CSV_DAY1.replace("1000,0.05", "1000,5.0").replace("10,0.1", "10,10")
    reports = [_report("r1", "2026-11-11", "2026-11-13T10:00:00Z"),
               _report("evil", "2026-11-12", "2026-11-14T10:00:00Z", host="https://example.com/x")]
    fake = Fake(reports=reports, csvs={f"{MEDIA}/r1": csv_pct}, jobs=[{"id": "job1", "reportTypeId": reach.REPORT_TYPE}])
    _run(fake)
    assert fake.downloads == [f"{MEDIA}/r1"]  # o token nunca vai para outro host
    latest = metrics.latest_metrics()["vPub"]
    assert latest["impressions"] == 1000 and latest["ctr"] == 5.0


def test_manual_ctr_kept_when_reach_has_nothing():
    _setup()
    day = metrics.day_of(NOW)
    metrics.upsert_metric("vPub", day, "manual", impressions=900, ctr=6.1)
    _run(Fake())  # relatório ainda não chegou
    latest = metrics.latest_metrics()["vPub"]
    assert latest["impressions"] == 900 and latest["ctr"] == 6.1


def test_reach_disabled_api_is_error_but_views_still_saved():
    _setup()

    class Off(Fake):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(reach.REPORTING_API):
                return httpx.Response(403, json={"error": {"errors": [{"reason": "accessNotConfigured"}]}})
            return super().__call__(request)

    summary = _run(Off())
    assert summary["state"] == "ok" and summary["reach"] == "erro"
    assert "Reporting API" in summary["reach_error"]
    assert metrics.latest_metrics()["vPub"]["views"] == 2000


def test_reach_fold_old_days_keeps_totals():
    data = {"days": {"20260101": {"v": [100.0, 5.0]}, "20261110": {"v": [100.0, 10.0]}}}
    reach._fold(data, NOW)
    assert data["folded_until"] == "20260101" and "20260101" not in data["days"]
    assert reach.totals(data) == {"v": {"impressions": 200, "ctr": 7.5}}


# --------------------------------------------------------------------------------------------
# Receita do YouTube (escopo opcional)
# --------------------------------------------------------------------------------------------


def test_revenue_with_scope_fills_video_and_month_without_duplicating():
    _setup(revenue=True)
    fake = Fake()
    summary = _run(fake)
    assert summary["revenue"] == "ok"
    latest = metrics.latest_metrics()["vPub"]
    assert latest["revenue_usd"] == 3.5 and latest["rpm"] == 1.75
    day_q = next(q for q in fake.analytics if q["metrics"] == ["estimatedRevenue"])
    assert day_q["dimensions"] == ["day"] and day_q["startDate"] == ["2026-09-01"]
    _run(fake)  # de novo: substitui, não soma
    with session_scope() as s:
        rows = {r.period: r for r in s.query(RevenueEntry).filter_by(channel_id="ch_a", store="youtube").all()}
        assert set(rows) == {"2026-10", "2026-11"}
        assert rows["2026-10"].commission == 2.0 and rows["2026-11"].commission == 2.0
        assert rows["2026-10"].currency == "USD" and rows["2026-10"].note == yt_revenue.AUTO_NOTE


def test_revenue_manual_month_wins():
    _setup(revenue=True)
    with session_scope() as s:
        s.add(RevenueEntry(channel_id="ch_a", store="youtube", period="2026-10", tracking_id="", commission=11.0,
                           currency="BRL", note="lançado à mão"))
    summary = _run(Fake())
    assert summary["revenue"] == "ok" and "à mão" in summary["revenue_error"]
    with session_scope() as s:
        oct_row = s.query(RevenueEntry).filter_by(channel_id="ch_a", store="youtube", period="2026-10").one()
        assert oct_row.commission == 11.0 and oct_row.currency == "BRL"


def test_revenue_refused_is_visible_and_nothing_breaks():
    _setup(revenue=True)
    summary = _run(Fake(revenue_status=403))
    assert summary["state"] == "ok" and summary["revenue"] == "erro"
    assert metrics.latest_metrics()["vPub"]["pct_watched"] == 41.0
    with session_scope() as s:
        assert s.query(RevenueEntry).count() == 0


# --------------------------------------------------------------------------------------------
# Escopo opcional no vínculo
# --------------------------------------------------------------------------------------------


def test_link_asks_revenue_scope_only_when_chosen():
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    plain = yt.start_link("http://localhost/cb", None)
    asked = yt.start_link("http://localhost/cb", None, revenue=True)
    assert yt.MONETARY_SCOPE not in parse_qs(urlparse(plain["auth_url"]).query)["scope"][0]
    assert yt.MONETARY_SCOPE in parse_qs(urlparse(asked["auth_url"]).query)["scope"][0]


def test_revenue_scope_follows_what_google_granted():
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    yt.save_channel_token(YT_CHANNEL, "rt", "Canal")
    info: dict = {"channel_id": YT_CHANNEL, "warnings": []}
    yt._note_revenue_scope(info, f"{yt.ANALYTICS_SCOPE} {yt.MONETARY_SCOPE}", True)
    assert yt.has_revenue_scope(yt.channel_key(YT_CHANNEL)) and not info["warnings"]
    yt._note_revenue_scope(info, yt.ANALYTICS_SCOPE, True)  # desmarcou na tela do Google
    assert not yt.has_revenue_scope(yt.channel_key(YT_CHANNEL)) and info["warnings"]


# --------------------------------------------------------------------------------------------
# Miniaturas A/B usam o CTR coletado
# --------------------------------------------------------------------------------------------


def test_thumb_ctr_check_verdicts():
    _setup()
    assert thumbs.ctr_check("prj_1")["verdict"] == "sem_dado"
    metrics.upsert_metric("vPub", "2026-11-11", "youtube_api", age_hours=24, impressions=500, ctr=2.0)
    assert thumbs.ctr_check("prj_1")["verdict"] == "cedo"
    metrics.upsert_metric("vPub", "2026-11-12", "youtube_api", age_hours=50, impressions=1500, ctr=3.2)
    low = thumbs.ctr_check("prj_1", chosen=1)
    assert low["verdict"] == "trocar" and low["swap"] == 2 and "2ª" in low["text"] and "3,2%" in low["text"]
    assert thumbs.ctr_check("prj_1", eligible=True)["verdict"] == "ab"
    metrics.upsert_metric("vPub", "2026-11-13", "manual", ctr=5.0)
    assert thumbs.ctr_check("prj_1")["verdict"] == "manter"
    assert thumbs.ctr_check("sem_publicacao") is None


# --------------------------------------------------------------------------------------------
# Correções da revisão
# --------------------------------------------------------------------------------------------


def test_reach_ctr_scale_is_per_channel_not_per_file():
    """Relatório de canal pequeno com todos os CTRs abaixo de 1 (escala 0–100) não vira 100× mais cliques."""
    _setup()
    head = "date,channel_id,video_id,video_thumbnail_impressions,video_thumbnail_impressions_ctr\n"
    csvs = {f"{MEDIA}/r1": head + f"20261111,{YT_CHANNEL},vPub,500,0.8\n",
            f"{MEDIA}/r2": head + f"20261112,{YT_CHANNEL},vPub,500,5.0\n"}
    reports = [_report("r1", "2026-11-11", "2026-11-13T10:00:00Z")]
    fake = Fake(reports=reports, csvs=csvs, jobs=[{"id": "job1", "reportTypeId": reach.REPORT_TYPE}])
    _run(fake)
    assert metrics.latest_metrics()["vPub"]["ctr"] == 80.0  # sozinho, 0,8 é fração (definição do Google)
    fake.reports.append(_report("r2", "2026-11-12", "2026-11-14T10:00:00Z"))
    _run(fake)
    # Um 5,0 mostra que o canal manda 0–100: o 0,8 do dia 11 passa a ser 0,8% → (4 + 25) cliques / 1000 = 2,9%
    assert metrics.latest_metrics()["vPub"]["ctr"] == pytest.approx(2.9, abs=0.01)
    assert _settings()["analytics_reach"]["ctr_percent"] is True
    # Dia novo só com CTR baixo continua lido como 0–100
    fake.reports.append(_report("r3", "2026-11-13", "2026-11-15T10:00:00Z"))
    fake.csvs[f"{MEDIA}/r3"] = head + f"20261113,{YT_CHANNEL},vPub,1000,0.5\n"
    _run(fake)
    assert metrics.latest_metrics()["vPub"]["ctr"] == pytest.approx((4 + 25 + 5) / 2000 * 100, abs=0.01)


def test_reach_old_format_is_downloaded_again():
    data = {"job_id": "job1", "after": "2026-11-14T10:00:00Z", "days": {"20261111": {"v": [10.0, 1.0]}}}
    _setup()
    with session_scope() as s:
        ch = s.get(Channel, "ch_a")
        ch.settings = {**ch.settings, reach.SETTINGS_KEY: data}
    assert reach._load("ch_a") == {"job_id": "job1", "format": reach.FORMAT}


def test_revenue_month_fixed_by_hand_with_empty_note_is_kept():
    """Corrigir em Dinheiro (observação vazia, nota automática fica) basta para a coleta não mexer mais no mês."""
    from studio.workers.business import revenue

    _setup(revenue=True)
    _run(Fake())
    revenue.upsert("ch_a", "youtube", "2026-10", "", commission=9.0, currency="BRL", note=None)
    _run(Fake())
    with session_scope() as s:
        oct_row = s.query(RevenueEntry).filter_by(channel_id="ch_a", store="youtube", period="2026-10").one()
        assert oct_row.commission == 9.0 and oct_row.currency == "BRL"
    # Mesmo em US$, um valor corrigido não volta para o do Google
    revenue.upsert("ch_a", "youtube", "2026-11", "", commission=5.0, currency="USD", note=None)
    summary = _run(Fake())
    assert "à mão" in summary["revenue_error"]
    with session_scope() as s:
        nov_row = s.query(RevenueEntry).filter_by(channel_id="ch_a", store="youtube", period="2026-11").one()
        assert nov_row.commission == 5.0


def test_revenue_strange_answer_does_not_break_collect():
    _setup(revenue=True)

    class Broken(Fake):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if str(request.url).startswith(yt.ANALYTICS_API) and "estimatedRevenue" in str(request.url):
                return httpx.Response(200, text="<html>não é JSON</html>")
            return super().__call__(request)

    summary = _run(Broken())
    assert summary["revenue"] == "erro" and "inesperada" in summary["revenue_error"]
    assert metrics.latest_metrics()["vPub"]["views"] == 2000


def test_revenue_denied_shows_without_permission_and_linked_says_revenue():
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    yt.save_channel_token(YT_CHANNEL, "rt", "Canal")
    key = yt.channel_key(YT_CHANNEL)
    yt._note_revenue_scope({"channel_id": YT_CHANNEL}, yt.ANALYTICS_SCOPE, True)  # pediu e desmarcou no Google
    state, _, msg = yt_revenue.collect_revenue("ch_a", httpx.Client(), "at", key, [], "2026-11-01", NOW)
    assert state == yt_revenue.SEM_ESCOPO and "não foi autorizada" in msg
    assert yt.linked_accounts()[0]["revenue"] is False
    yt._note_revenue_scope({"channel_id": YT_CHANNEL}, yt.ANALYTICS_SCOPE, False)  # não pediu: desligada
    assert yt_revenue.collect_revenue("ch_a", httpx.Client(), "at", key, [], "2026-11-01", NOW)[0] == "desligada"
    yt._note_revenue_scope({"channel_id": YT_CHANNEL}, f"{yt.ANALYTICS_SCOPE} {yt.MONETARY_SCOPE}", True)
    assert yt.linked_accounts()[0]["revenue"] is True


def test_thumb_ctr_needs_enough_impressions():
    _setup()
    metrics.upsert_metric("vPub", "2026-11-12", "youtube_api", age_hours=60, impressions=80, ctr=1.5)
    few = thumbs.ctr_check("prj_1")
    assert few["verdict"] == "cedo" and "poucas impressões" in few["text"]


def test_ab_rule_uses_first_week_and_ignores_old_cumulative_rows():
    from studio.models import VideoMetric

    _setup()
    days = {f"202611{d:02d}": {"vNovo": [400.0, 20.0]} for d in range(1, 9)}
    with session_scope() as s:
        ch = s.get(Channel, "ch_a")
        ch.settings = {**ch.settings,
                       "analytics": {"videos": {"vNovo": {"published_at": "2026-11-01T15:00:00+00:00", "duration_s": 600},
                                                "vVelho": {"published_at": "2025-01-01T15:00:00+00:00", "duration_s": 600}}},
                       reach.SETTINGS_KEY: {"format": reach.FORMAT, "days": days}}
        # vídeo antigo: a coleta gravou semanas de impressões numa linha de idade alta → não é o número de 7 dias
        s.add(VideoMetric(youtube_id="vVelho", day="2026-11-15", channel_id="ch_a", age_hours=7600, impressions=90_000))
        # vídeo novo: linha cumulativa perto de 168 h perde para a soma dos 7 primeiros dias da Reporting API
        s.add(VideoMetric(youtube_id="vNovo", day="2026-11-08", channel_id="ch_a", age_hours=170, impressions=1500))
    rule = thumbs.ab_rule("ch_a")
    assert rule["n"] == 1 and rule["median_impressions"] == 2800 and rule["eligible"] is True
