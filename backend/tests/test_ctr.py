"""Fase 9 (CTR): a coleta cria o job do relatório de alcance; a Reporting API desligada vira mensagem clara.

Tudo com transporte HTTP falso (nenhuma chamada ao Google) e banco temporário (conftest).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx

from studio.db import session_scope
from studio.models import Channel, Project, Publication
from studio.providers import credentials
from studio.workers.analytics import collect as col
from studio.workers.analytics import metrics, reach
from studio.workers.publish import youtube as yt

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
YT_CHANNEL = "UCcanalctr12345678901234"
JOBS = f"{reach.REPORTING_API}/jobs"
PROJECT = "1039537801474"
ACTIVATION = f"https://console.developers.google.com/apis/api/youtubereporting.googleapis.com/overview?project={PROJECT}"
# Corpo real (formato) do 403 do Google com a API desligada: a mensagem passa de 300 caracteres e o motivo vem depois.
DISABLED_BODY = {"error": {
    "code": 403,
    "message": (f"YouTube Reporting API has not been used in project {PROJECT} before or it is disabled. Enable it by "
                f"visiting {ACTIVATION} then retry. If you enabled this API recently, wait a few minutes for the "
                "action to propagate to our systems and retry."),
    "errors": [{"message": "...", "domain": "usageLimits", "reason": "accessNotConfigured",
                "extendedHelp": "https://console.developers.google.com"}],
    "status": "PERMISSION_DENIED",
    "details": [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "SERVICE_DISABLED",
                 "domain": "googleapis.com",
                 "metadata": {"service": "youtubereporting.googleapis.com", "consumer": f"projects/{PROJECT}",
                              "activationUrl": ACTIVATION}}],
}}


def _videos(privacy: str = "public", views: str = "500") -> dict:
    return {"items": [{"id": "vPub", "snippet": {"title": "V", "publishedAt": "2026-11-10T12:00:00Z"},
                       "contentDetails": {"duration": "PT8M"}, "status": {"privacyStatus": privacy},
                       "statistics": {"viewCount": views}}]}


class Google:
    """Google simulado: token, Data API, Analytics e Reporting API (desligada até `enabled=True`)."""

    def __init__(self, enabled: bool = True, privacy: str = "public", views: str = "500") -> None:
        self.enabled = enabled
        self.videos = _videos(privacy, views)
        self.created: list[dict] = []
        self.reporting_calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if url.startswith(f"{yt.API}/videos"):
            return httpx.Response(200, json=self.videos)
        if url.startswith(yt.ANALYTICS_API):
            return httpx.Response(200, json={"columnHeaders": [{"name": "video"}, {"name": "views"}], "rows": []})
        if url.startswith(reach.REPORTING_API):
            self.reporting_calls += 1
            if not self.enabled:
                return httpx.Response(403, json=DISABLED_BODY)
            if url == JOBS and request.method == "GET":
                return httpx.Response(200, json={})  # conta sem nenhum job: a API devolve objeto vazio
            if url == JOBS and request.method == "POST":
                self.created.append(json.loads(request.content))
                return httpx.Response(200, json={"id": "jobCtr", "reportTypeId": reach.REPORT_TYPE})
            if url.startswith(f"{JOBS}/jobCtr/reports"):
                return httpx.Response(200, json={})
        return httpx.Response(404)


def _setup() -> None:
    with session_scope() as s:
        s.add(Channel(id="ch_v", name="Canal V", settings={"youtube": {"channel_id": YT_CHANNEL, "source": "oauth"}}))
        s.add(Project(id="prj_v", title="P", channel_id="ch_v", style_id="misterios-short", settings={}))
        s.add(Publication(id="pub_v", project_id="prj_v", platform="youtube", language="pt-BR",
                          driver="youtube_native", status="uploaded", external_id="vPub"))
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    yt.save_channel_token(YT_CHANNEL, "rt", "Canal")


def _run(google: Google) -> dict:
    return col.collect("ch_v", http=httpx.Client(transport=httpx.MockTransport(google)), now=NOW)["channels"][0]


def _reach_state() -> dict:
    with session_scope() as s:
        return dict((s.get(Channel, "ch_v").settings or {}).get(reach.SETTINGS_KEY) or {})


def test_disabled_reporting_api_is_recognized_with_activation_link():
    """O caso real do canal: 403 SERVICE_DISABLED com mensagem longa. Antes virava "erro da YouTube Reporting API
    (403): {..." cortado; agora diz o que fazer e com qual link."""
    _setup()
    google = Google(enabled=False)
    summary = _run(google)
    assert summary["state"] == "ok" and summary["reach"] == "erro"
    msg = summary["reach_error"]
    assert msg.startswith("ative a YouTube Reporting API no Google Cloud (projeto 1039537801474)")
    assert ACTIVATION in msg
    assert "(403)" not in msg
    assert _reach_state() == {"format": reach.FORMAT}  # sem job ainda (o estado que o canal real tinha)
    assert metrics.latest_metrics()["vPub"]["views"] == 500  # views gravadas mesmo assim


def test_job_is_created_on_next_collect_once_api_is_enabled():
    """Depois de ativar a API, a próxima coleta (rotina ou botão) cria o job sozinha, uma vez só."""
    _setup()
    _run(Google(enabled=False))
    google = Google(enabled=True)
    summary = _run(google)
    assert summary["reach"] == "esperando"
    assert "criado agora" in summary["reach_error"]
    assert google.created == [{"reportTypeId": "channel_reach_basic_a1", "name": reach.JOB_NAME}]
    assert _reach_state()["job_id"] == "jobCtr"
    # Coleta seguinte: reaproveita o job guardado, não cria outro nem lista os jobs de novo.
    again = Google(enabled=True)
    assert _run(again)["reach"] == "esperando"
    assert again.created == []


def test_job_created_even_without_public_video():
    """Canal só com vídeo agendado/privado (sem views): o job nasce logo, para o histórico de 30 dias valer."""
    _setup()
    google = Google(enabled=True, privacy="private", views="0")
    summary = _run(google)
    assert summary["saved"] == 0 and summary["skipped"] == 1
    assert summary["reach"] == "esperando"
    assert len(google.created) == 1


def test_error_classification():
    def resp(status: int, body: object) -> httpx.Response:
        return httpx.Response(status, json=body)

    off = reach._error(resp(403, DISABLED_BODY))
    assert isinstance(off, reach.ReachApiDisabled) and isinstance(off, yt.YouTubeError)
    # Só o texto (sem `details`): o número do projeto sai da mensagem.
    plain = reach._error(httpx.Response(403, text=DISABLED_BODY["error"]["message"]))
    assert isinstance(plain, reach.ReachApiDisabled) and f"projeto {PROJECT}" in str(plain)
    scope = reach._error(resp(403, {"error": {"status": "PERMISSION_DENIED",
                                              "details": [{"reason": "ACCESS_TOKEN_SCOPE_INSUFFICIENT"}]}}))
    assert isinstance(scope, reach.ReachScopeMissing)
    assert isinstance(reach._error(resp(401, {"error": {}})), reach.ReachScopeMissing)
    other = reach._error(resp(500, {"error": {"status": "INTERNAL"}}))
    assert type(other) is yt.YouTubeError and "(500)" in str(other)
    assert len(reach.disabled_message(PROJECT).split(" Link:")[0]) <= 160  # o essencial cabe no corte da tela
