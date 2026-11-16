"""YouTube: playlists, medidor de cota (dia do Pacífico) e várias contas. Google sempre simulado (MockTransport)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import Metadata
from studio.db import session_scope
from studio.models import Channel, Publication
from studio.providers import credentials
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import quota
from studio.workers.publish import youtube as yt
from studio.workers.publish import youtube_channels as ytc

YT_ID = "UCcuriosidades1234567890"
OTHER_ID = "UCoutrocanal_abcdefghijk"


def _configure() -> None:
    credentials.set_credential("youtube", {"client_id": "cid.apps.googleusercontent.com", "client_secret": "segredo"})


def _global(channel_id: str = OTHER_ID, title: str = "Global") -> None:
    credentials.set_credential(yt.ACCOUNT_KEY, {"refresh_token": "rt-global", "channel_title": title, "channel_id": channel_id})


def _linked_channel(name: str = "Curiosidades", youtube_id: str = YT_ID, token: bool = True) -> str:
    """Canal do Vulto ligado a um canal do YouTube (com token próprio, como depois do login)."""
    channel_id = f"chn_{youtube_id[-6:]}"
    with session_scope() as s:
        s.add(Channel(id=channel_id, name=name, settings={"youtube": {"channel_id": youtube_id, "title": name,
                                                                       "source": "oauth"}}))
    if token:
        yt.save_channel_token(youtube_id, "rt-" + youtube_id[-4:], name)
    return channel_id


def _plain_channel(name: str = "Sem vínculo") -> str:
    with session_scope() as s:
        s.add(Channel(id="chn_plain", name=name, settings={}))
    return "chn_plain"


def _error(status: int, reason: str, message: str = "erro") -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": status, "message": message, "errors": [{"reason": reason}]}})


class FakeYouTube:
    """Token, playlists.list (duas páginas), playlists.insert e playlistItems.insert."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.item_error: httpx.Response | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if request.method == "GET" and url.startswith(f"{yt.API}/playlists"):
            params = request.url.params
            assert params["mine"] == "true" and params["maxResults"] == "50"
            assert "snippet" in params["part"] and "contentDetails" in params["part"]
            if params.get("pageToken") == "p2":
                return httpx.Response(200, json={"items": [
                    {"id": "PLb", "snippet": {"title": "beta"}, "status": {"privacyStatus": "unlisted"},
                     "contentDetails": {"itemCount": 3}}]})
            return httpx.Response(200, json={"nextPageToken": "p2", "items": [
                {"id": "PLc", "snippet": {"title": "Casos reais"}, "status": {"privacyStatus": "public"},
                 "contentDetails": {"itemCount": "12"}},
                {"id": "PLa", "snippet": {"title": "Arquivo"}, "status": {"privacyStatus": "private"}}]})
        if request.method == "POST" and url.startswith(f"{yt.API}/playlists"):
            body = json.loads(request.content)
            assert request.url.params["part"].startswith("snippet,status")
            return httpx.Response(200, json={"id": "PLnova", "snippet": body["snippet"], "status": body["status"],
                                             "contentDetails": {"itemCount": 0}})
        if request.method == "POST" and url.startswith(f"{yt.API}/playlistItems"):
            if self.item_error is not None:
                return self.item_error
            body = json.loads(request.content)
            assert request.url.params["part"] == "snippet"
            return httpx.Response(200, json={"id": "ITEM1", "snippet": body["snippet"]})
        return httpx.Response(404, text="não simulado")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def api_calls(self) -> list[tuple[str, str]]:
        return [(r.method, r.url.path) for r in self.requests if not str(r.url).startswith(yt.TOKEN_URL)]


@pytest.fixture()
def fake(monkeypatch) -> FakeYouTube:
    _configure()
    google = FakeYouTube()
    monkeypatch.setattr(yt, "new_client", lambda timeout=60: google.client())
    return google


# --------------------------------------------------------------------------------------------
# Cota
# --------------------------------------------------------------------------------------------


def test_quota_day_follows_pacific_time_with_daylight_saving():
    # inverno (PST, UTC-8): 07:59 UTC ainda é o dia anterior no Pacífico
    assert quota.pacific_day(datetime(2026, 11, 15, 7, 59, tzinfo=UTC)) == "2026-11-14"
    assert quota.pacific_day(datetime(2026, 11, 15, 8, 0, tzinfo=UTC)) == "2026-11-15"
    # verão (PDT, UTC-7)
    assert quota.pacific_day(datetime(2026, 7, 1, 6, 59, tzinfo=UTC)) == "2026-06-30"
    assert quota.pacific_day(datetime(2026, 7, 1, 7, 0, tzinfo=UTC)) == "2026-07-01"
    assert quota.resets_at(datetime(2026, 11, 15, 12, 0, tzinfo=UTC)) == datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    assert quota.resets_at(datetime(2026, 7, 1, 12, 0, tzinfo=UTC)) == datetime(2026, 7, 2, 7, 0, tzinfo=UTC)


def test_quota_costs_match_the_official_table():
    assert quota.cost("channels.list") == 1 and quota.cost("playlists.list") == 1
    assert quota.cost("playlists.insert") == 50 and quota.cost("playlistItems.insert") == 50
    assert quota.cost("thumbnails.set") == 50 and quota.cost("captions.insert") == 400
    assert quota.COSTS["videos.insert"] == (1, quota.UPLOADS)  # cota própria: 100 envios por dia
    assert quota.publish_estimate() == 450
    assert quota.publish_estimate(captions=False, playlist=True) == 100


def test_quota_sums_per_day_and_warns_near_the_limit():
    day1 = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    quota.spend("captions.insert", now=day1)
    quota.spend("thumbnails.set", now=day1)
    quota.spend("videos.insert", now=day1)
    info = quota.status(day1)
    assert info["used"] == 450 and info["uploads"] == 1 and info["remaining"] == 9550
    assert info["level"] == "ok" and info["message"] is None and info["day"] == "2026-11-15"
    assert {r["method"]: r["units"] for r in info["by_method"]}["captions.insert"] == 400

    quota.spend("captions.insert", calls=19, now=day1)  # 450 + 7600 = 8050: passou de 80%
    info = quota.status(day1)
    assert info["used"] == 8050 and info["level"] == "near" and "8.050 de 10.000" in info["message"]
    assert quota.check_publish(450, day1) == []
    quota.spend("captions.insert", calls=4, now=day1)  # 9650
    assert quota.check_publish(450, day1) and "restam 350" in quota.check_publish(450, day1)[0]
    quota.spend("captions.insert", now=day1)  # 10050
    assert quota.status(day1)["level"] == "over"
    with pytest.raises(quota.QuotaExhausted):
        quota.check_publish(50, day1)

    # depois da meia-noite do Pacífico começa do zero
    next_day = datetime(2026, 11, 16, 8, 0, tzinfo=UTC)
    assert quota.status(next_day)["used"] == 0 and quota.status(next_day)["level"] == "ok"

    # limite maior liberado pelo Google
    assert quota.set_daily_limit(50_000) == 50_000
    info = quota.status(day1)
    assert info["level"] == "ok" and info["custom_limit"] is True
    assert quota.set_daily_limit(None) == quota.DAILY_LIMIT


def test_quota_upload_limit_counts_calls_not_units():
    now = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)
    quota.spend("videos.insert", calls=90, now=now)
    info = quota.status(now)
    assert info["used"] == 0 and info["uploads"] == 90 and info["level"] == "near"
    quota.spend("videos.insert", calls=10, now=now)
    info = quota.status(now)
    assert info["level"] == "over" and "100 envios" in info["message"]


def test_upload_thumbnail_and_captions_are_counted(tmp_path: Path):
    _configure()
    _global()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 1000)
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"jpg")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nOi\n", encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if request.method == "POST" and "/upload/youtube/v3/videos" in url:
            return httpx.Response(200, headers={"Location": "https://upload.example/s"})
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "VID"})
        if "/thumbnails/set" in url:
            return httpx.Response(200, json={})
        if "/captions" in url:
            return _error(403, "quotaExceeded", "The request cannot be completed because you have exceeded your quota.")
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    yt.upload_video(video, snippet={"title": "T"}, status={"privacyStatus": "private"}, http=client)
    yt.set_thumbnail("VID", thumb, client)
    with pytest.raises(yt.YouTubeError, match="meia-noite do horário do Pacífico"):
        yt.upload_captions("VID", srt, "pt-BR", client)
    info = quota.status()
    assert info["uploads"] == 1 and info["used"] == 450  # a chamada recusada também conta
    assert info["exhausted"] is True and info["level"] == "over" and "Google avisou" in info["message"]


# --------------------------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------------------------


def test_list_create_and_add_to_playlist(fake: FakeYouTube):
    client = fake.client()
    items = yt.list_playlists(client, "at")
    assert [p["id"] for p in items] == ["PLa", "PLb", "PLc"]  # duas páginas, por nome
    assert items[2] == {"id": "PLc", "title": "Casos reais", "privacy": "public", "count": 12}
    assert items[0]["count"] is None
    created = yt.create_playlist(client, "at", "  Mistérios   do mar ", "unlisted")
    assert created["id"] == "PLnova" and created["title"] == "Mistérios do mar" and created["privacy"] == "unlisted"
    post = next(r for r in fake.requests if r.method == "POST" and r.url.path.endswith("/playlists"))
    assert json.loads(post.content)["status"] == {"privacyStatus": "unlisted"}
    assert yt.add_to_playlist(client, "at", "PLc", "VID") == "ITEM1"
    item = json.loads(fake.requests[-1].content)
    assert item["snippet"] == {"playlistId": "PLc", "resourceId": {"kind": "youtube#video", "videoId": "VID"}}
    # 2 páginas (2) + criar (50) + adicionar (50)
    assert quota.status()["used"] == 102

    with pytest.raises(yt.YouTubeError, match="nome"):
        yt.create_playlist(client, "at", "   ")
    with pytest.raises(yt.YouTubeError, match="visibilidade"):
        yt.create_playlist(client, "at", "X", "secreta")
    fake.item_error = _error(404, "playlistNotFound", "Playlist not found.")
    with pytest.raises(yt.YouTubeError, match="não existe mais"):
        yt.add_to_playlist(client, "at", "PLsumiu", "VID")


def test_default_playlist_belongs_to_the_linked_youtube_channel():
    _configure()
    plain = _plain_channel()
    with pytest.raises(yt.YouTubeError, match="ligue o canal"):
        ytc.set_default_playlist(plain, "PL1", "Casos")
    linked = _linked_channel()
    assert ytc.set_default_playlist(linked, "PL1", "Casos") == {"id": "PL1", "title": "Casos"}
    with TestClient(create_app()) as client:
        channel = next(c for c in client.get("/api/channels").json() if c["id"] == linked)
        assert channel["youtube_playlist"] == {"id": "PL1", "title": "Casos"}
        cleared = client.put(f"/api/channels/{linked}/youtube/playlist", json={"id": None}).json()["channel"]
        assert cleared["youtube_playlist"] is None
        again = client.put(f"/api/channels/{linked}/youtube/playlist", json={"id": "PL2", "title": "Arquivo"}).json()
        assert again["channel"]["youtube_playlist"]["id"] == "PL2"
        assert client.put("/api/channels/chn_nao/youtube/playlist", json={"id": "PL"}).status_code == 404
    # o canal do Vulto passou a ser de outro canal do YouTube: a playlist antiga não vale mais
    with session_scope() as s:
        row = s.get(Channel, linked)
        row.settings = {**row.settings, "youtube": {"channel_id": OTHER_ID, "title": "Outro"}}
    with session_scope() as s:
        assert ytc.default_playlist(s.get(Channel, linked).settings) is None


def test_playlist_routes_use_the_project_account(fake: FakeYouTube):
    linked = _linked_channel()
    doc = service.create_project(title="T", style_id="misterios-short", topic="t", channel_id=linked)
    with TestClient(create_app()) as client:
        data = client.get("/api/youtube/playlists", params={"project_id": doc.id}).json()
        assert data["account_title"] == "Curiosidades" and len(data["playlists"]) == 3
        token_form = next(r for r in fake.requests if str(r.url).startswith(yt.TOKEN_URL)).content.decode()
        assert "refresh_token=rt-7890" in token_form  # token do canal do projeto, não a global
        created = client.post("/api/youtube/playlists", json={"channel_id": linked, "title": "Nova", "privacy": "public"})
        assert created.status_code == 200 and created.json()["id"] == "PLnova"
        bad = client.post("/api/youtube/playlists", json={"channel_id": linked, "title": "X", "privacy": "secreta"})
        assert bad.status_code == 400
        none = client.get("/api/youtube/playlists")
        assert none.status_code == 400 and "conecte" in none.json()["detail"]


# --------------------------------------------------------------------------------------------
# Várias contas
# --------------------------------------------------------------------------------------------


def test_accounts_list_and_choosing_an_account_for_an_unlinked_project():
    _configure()
    linked = _linked_channel()
    plain = _plain_channel()
    _global()
    accounts = yt.accounts()
    assert {a["channel_id"]: a["global"] for a in accounts} == {OTHER_ID: True, YT_ID: False}
    assert next(a for a in accounts if a["channel_id"] == YT_ID)["vulto_channels"] == ["Curiosidades"]

    # canal sem vínculo: pode enviar pela conta de outro canal conectado
    assert yt.account_for_channel(plain, YT_ID) == yt.channel_key(YT_ID)
    assert yt.account_for_channel(None, OTHER_ID) == yt.ACCOUNT_KEY
    with pytest.raises(yt.YouTubeError, match="não está mais conectada"):
        yt.account_for_channel(plain, "UCdesconhecido_abcdefghi")
    # canal vinculado nunca publica em outro canal do YouTube
    with pytest.raises(yt.YouTubeError, match="só no canal do YouTube vinculado"):
        yt.account_for_channel(linked, OTHER_ID)
    assert yt.account_for_channel(linked, YT_ID) == yt.channel_key(YT_ID)

    # global e canal vinculado sendo o mesmo canal do YouTube aparecem uma vez só
    _global(YT_ID, "Curiosidades (global)")
    assert [a["channel_id"] for a in yt.accounts()] == [YT_ID]


def test_status_lets_an_unlinked_project_pick_from_linked_accounts():
    _configure()
    _linked_channel()
    plain = _plain_channel()
    doc = service.create_project(title="T", style_id="misterios-short", topic="t", channel_id=plain)
    with TestClient(create_app()) as client:
        status = client.get("/api/youtube/status", params={"project_id": doc.id}).json()
        assert status["can_choose_account"] is True and status["connected"] is True
        assert status["account_channel_id"] == "" and status["reason"] is None
        assert [a["channel_id"] for a in status["accounts"]] == [YT_ID]


# --------------------------------------------------------------------------------------------
# Envio com playlist e cota
# --------------------------------------------------------------------------------------------


def _rendered_project(settings, channel_id: str | None) -> str:
    doc = service.create_project(title="Vídeo", style_id="misterios-short", topic="t", channel_id=channel_id)
    doc = doc.model_copy(update={"metadata": {"pt-BR": Metadata(title="Vídeo", description="Resumo.")}})
    service.commit(doc.id, doc, "pronto")
    out = settings.outputs_dir / doc.id / "pt-BR"
    out.mkdir(parents=True, exist_ok=True)
    (out / "video.mp4").write_bytes(b"mp4")
    service.update_settings(doc.id, {"last_final": {"langs": {"pt-BR": {"video": f"outputs/{doc.id}/pt-BR/video.mp4"}},
                                                    "preview_only_providers": []}})
    return doc.id


@pytest.fixture()
def sent(monkeypatch) -> dict:
    """Troca o envio do vídeo por um registro (o resto, inclusive playlistItems.insert, vai ao MockTransport)."""
    record: dict = {"accounts": []}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None):
        record["accounts"].append(account)
        return {"id": "VID9"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    return record


def test_publish_job_adds_the_video_to_the_channel_default_playlist(isolated_settings, fake, sent, monkeypatch):
    linked = _linked_channel()
    ytc.set_default_playlist(linked, "PLc", "Casos reais")
    pid = _rendered_project(isolated_settings, linked)

    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private"}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final["status"] == "done", final["error"]
    assert sent["accounts"] == [yt.channel_key(YT_ID)]
    item = next(r for r in fake.requests if r.url.path.endswith("/playlistItems"))
    assert json.loads(item.content)["snippet"]["playlistId"] == "PLc"
    with session_scope() as s:
        details = s.query(Publication).filter_by(project_id=pid).one().details
    assert details["playlist"] == {"id": "PLc", "title": "Casos reais"}

    # "" desliga a playlist padrão; outra playlist escolhida no envio vale mais que a padrão
    fake.requests.clear()
    get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private", "playlist_id": ""}, pid, run_inline=True)
    assert not any(r.url.path.endswith("/playlistItems") for r in fake.requests)
    get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private", "playlist_id": "PLb",
                                            "playlist_title": "beta"}, pid, run_inline=True)
    item = next(r for r in fake.requests if r.url.path.endswith("/playlistItems"))
    assert json.loads(item.content)["snippet"]["playlistId"] == "PLb"

    # falha na playlist vira aviso; o vídeo continua enviado
    fake.item_error = _error(403, "playlistContainsMaximumNumberOfVideos", "Playlist full.")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private", "playlist_id": "PLb"}, pid,
                              run_inline=True)
    final = get_job(job["id"])
    assert final["status"] == "done"
    assert any("máximo de vídeos" in w for w in final["result"]["warnings"])


def test_publish_job_uses_the_chosen_account_for_an_unlinked_project(isolated_settings, fake, sent, monkeypatch):
    _linked_channel()
    plain = _plain_channel()
    pid = _rendered_project(isolated_settings, plain)
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private", "account": YT_ID}, pid,
                              run_inline=True)
    assert get_job(job["id"])["status"] == "done"
    assert sent["accounts"] == [yt.channel_key(YT_ID)]


def test_publish_route_blocks_when_the_quota_is_over(isolated_settings, fake, sent):
    _global()
    pid = _rendered_project(isolated_settings, None)
    quota.mark_exhausted()
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/projects/{pid}/publish/youtube", json={"privacy": "private"})
        assert resp.status_code == 429 and "Pacífico" in resp.json()["detail"]
        info = client.get("/api/youtube/quota").json()
        assert info["level"] == "over" and info["limit"] == 10_000
        assert client.put("/api/youtube/quota", json={"limit": 20_000}).json()["limit"] == 20_000
        assert client.put("/api/youtube/quota", json={"limit": 0}).status_code == 422
        # conta escolhida que não existe: erro claro antes de criar o job
        bad = client.post(f"/api/projects/{pid}/publish/youtube", json={"privacy": "private", "account": "UCnada"})
        assert bad.status_code == 400 and "não está mais conectada" in bad.json()["detail"]
