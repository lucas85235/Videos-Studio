"""Vincular canal do YouTube já existente: login do Google (multi-conta), link público, IA e publicação por canal."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.db import session_scope
from studio.models import Channel, Project
from studio.providers import credentials
from studio.providers.base import LLMResult, Usage
from studio.workers.publish import youtube as yt
from studio.workers.publish import youtube_channels as ytc

YT_ID = "UCcuriosidades1234567890"
OTHER_ID = "UCoutrocanal_abcdefghijk"
AVATAR = "https://yt3.ggpht.com/foto-do-canal=s800-c-k"
REDIRECT = "http://127.0.0.1:8765/api/youtube/callback"


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (400, 400), (200, 40, 40)).save(buf, "PNG")
    return buf.getvalue()


def _channel_item(youtube_id: str = YT_ID, subscribers: str = "12345") -> dict:
    return {
        "id": youtube_id,
        "snippet": {
            "title": "Curiosidades Ocultas",
            "description": "Fatos estranhos da ciência e da história, toda semana.",
            "customUrl": "@curiosidadesocultas",
            "defaultLanguage": "pt",
            "country": "BR",
            "thumbnails": {"default": {"url": "https://yt3.ggpht.com/p=s88"}, "high": {"url": AVATAR}},
        },
        "brandingSettings": {"channel": {"keywords": 'curiosidades "fatos estranhos" ciência', "country": "BR"}},
        "statistics": {"subscriberCount": subscribers, "videoCount": "42", "viewCount": "999", "hiddenSubscriberCount": False},
        "contentDetails": {"relatedPlaylists": {"uploads": "UU" + youtube_id[2:]}},
    }


def _uploads() -> dict:
    return {
        "items": [
            {"snippet": {"title": "Por que o céu é azul?", "resourceId": {"videoId": "v1"}, "publishedAt": "2026-11-10T12:00:00Z"}},
            {"snippet": {"title": "Private video", "resourceId": {"videoId": "v2"}}},
            {"snippet": {"title": "O mistério do farol", "resourceId": {"videoId": "v3"}, "publishedAt": "2026-11-01T12:00:00Z"}},
        ]
    }


class FakeGoogle:
    """Google/YouTube simulado (token, channels.list, playlistItems, foto, página do canal e feed RSS)."""

    def __init__(self, youtube_id: str = YT_ID) -> None:
        self.youtube_id = youtube_id
        self.requests: list[httpx.Request] = []
        self.subscribers = "12345"
        self.refresh_ok = True

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            form = parse_qs(request.content.decode())
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(200, json={"access_token": "at-link", "refresh_token": "rt-canal"})
            if not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "at-" + form["refresh_token"][0]})
        if url.startswith(f"{yt.API}/channels"):
            params = request.url.params
            assert "brandingSettings" in params["part"] or params["part"] == "snippet"
            return httpx.Response(200, json={"items": [_channel_item(self.youtube_id, self.subscribers)]})
        if url.startswith(f"{yt.API}/playlistItems"):
            assert request.url.params["maxResults"] == "15"
            return httpx.Response(200, json=_uploads())
        if url.startswith("https://yt3."):
            return httpx.Response(200, content=_png(), headers={"Content-Type": "image/png"})
        if url.startswith(yt.FEED_URL):
            return httpx.Response(200, content=_feed(request.url.params["channel_id"]).encode("utf-8"))
        if url.startswith(f"{yt.SITE}/@curiosidadesocultas"):
            return httpx.Response(200, text=_page(self.youtube_id))
        return httpx.Response(404, text="não simulado")

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def urls(self) -> list[str]:
        return [str(r.url) for r in self.requests]


def _feed(youtube_id: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <link rel="self" href="http://www.youtube.com/feeds/videos.xml?channel_id={youtube_id}"/>
 <id>yt:channel:{youtube_id[2:]}</id>
 <title>Curiosidades Ocultas</title>
 <author><name>Curiosidades Ocultas</name><uri>https://www.youtube.com/channel/{youtube_id}</uri></author>
 <entry>
  <id>yt:video:v1</id><yt:videoId>v1</yt:videoId><yt:channelId>{youtube_id}</yt:channelId>
  <title>Por que o céu é azul?</title>
  <link rel="alternate" href="https://www.youtube.com/shorts/v1"/>
  <published>2026-11-10T12:00:00+00:00</published>
  <media:group><media:title>Por que o céu é azul?</media:title></media:group>
 </entry>
 <entry>
  <id>yt:video:v3</id><yt:videoId>v3</yt:videoId><yt:channelId>{youtube_id}</yt:channelId>
  <title>O mistério do farol</title>
  <published>2026-11-01T12:00:00+00:00</published>
 </entry>
</feed>"""


def _page(youtube_id: str) -> str:
    return f"""<html><head><title>Curiosidades Ocultas - YouTube</title>
<meta property="og:title" content="Curiosidades Ocultas">
<meta property="og:description" content="Fatos estranhos &amp; histórias reais.">
<meta property="og:image" content="https://yt3.googleusercontent.com/foto=s900-c-k">
<link rel="canonical" href="https://www.youtube.com/channel/{youtube_id}">
</head><body><script>var x = {{"channelId":"UCnaoeesteeee_abcdefghi"}};</script></body></html>"""


def _configure_client() -> None:
    credentials.set_credential("youtube", {"client_id": "cid.apps.googleusercontent.com", "client_secret": "segredo"})


@pytest.fixture()
def google(monkeypatch) -> FakeGoogle:
    fake = FakeGoogle()
    monkeypatch.setattr(yt, "new_client", lambda timeout=60: fake.client())
    return fake


def _link(target: str | None = None) -> tuple[str, dict]:
    _configure_client()
    started = yt.start_link(REDIRECT, target)
    return started["state"], started


# --------------------------------------------------------------------------------------------
# Login do Google (OAuth)
# --------------------------------------------------------------------------------------------


def test_link_oauth_creates_channel_with_real_data(google: FakeGoogle, isolated_settings):
    state, started = _link()
    query = parse_qs(urlparse(started["auth_url"]).query)
    assert "youtube.readonly" in query["scope"][0] and "youtube.upload" in query["scope"][0]
    assert query["code_challenge_method"] == ["S256"] and query["state"] == [state]
    assert yt.link_status(state) == {"status": "pending"}

    info = yt.finish_oauth("codigo", state)
    assert info["channel_id"] == YT_ID and info["created"] is True
    assert not any("/search" in u for u in google.urls())  # nada de search.list (100 unidades)

    with session_scope() as s:
        row = s.get(Channel, info["vulto_channel_id"])
        assert row.name == "Curiosidades Ocultas"
        assert row.languages == ["pt-BR"]
        data = row.settings["youtube"]
    assert data["handle"] == "@curiosidadesocultas"
    assert data["url"] == "https://www.youtube.com/@curiosidadesocultas"
    assert data["subscribers"] == 12345 and data["videos"] == 42
    assert data["keywords"] == ["curiosidades", "fatos estranhos", "ciência"]
    assert [v["title"] for v in data["recent_videos"]] == ["Por que o céu é azul?", "O mistério do farol"]
    assert data["source"] == "oauth" and data["country"] == "BR"
    avatar = isolated_settings.data_dir / data["avatar"]
    assert avatar.is_file() and Image.open(avatar).size == (256, 256)

    # um token por canal; a conta global continua vazia
    assert credentials.get_credential(yt.channel_key(YT_ID))["refresh_token"] == "rt-canal"
    assert not yt.account()["connected"]
    assert yt.any_connected()
    done = yt.link_status(state)
    assert done["status"] == "done" and done["vulto_channel_id"] == info["vulto_channel_id"]

    # vincular de novo o mesmo canal do YouTube atualiza o canal do Vulto, não cria outro
    google.subscribers = "20000"
    again = yt.finish_oauth("codigo", _link()[0])
    assert again["vulto_channel_id"] == info["vulto_channel_id"] and again["created"] is False
    with session_scope() as s:
        assert s.query(Channel).count() == 1
        assert s.get(Channel, info["vulto_channel_id"]).settings["youtube"]["subscribers"] == 20000


def test_link_status_stays_pending_while_the_callback_finishes(google: FakeGoogle):
    # A volta do Google tira o pedido de _pending e só grava o resultado depois das chamadas à API: nesse meio-tempo
    # a tela via "unknown", parava de perguntar e mostrava "O pedido de login expirou" com o canal já vinculado.
    state, _ = _link()
    reached, release = threading.Event(), threading.Event()
    handler = google.handler

    def slow(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(yt.TOKEN_URL):
            reached.set()
            assert release.wait(5)
        return handler(request)

    google.handler = slow  # type: ignore[method-assign]
    worker = threading.Thread(target=yt.finish_oauth, args=("codigo", state))
    worker.start()
    try:
        assert reached.wait(5)
        assert yt.link_status(state) == {"status": "pending"}
    finally:
        release.set()
        worker.join(10)
    assert yt.link_status(state)["status"] == "done"
    with pytest.raises(yt.YouTubeError, match="expirado"):
        yt.finish_oauth("codigo", state)  # a volta repetida não mexe no resultado
    assert yt.link_status(state)["status"] == "done"


def test_status_with_project_reports_the_account_that_uploads(google: FakeGoogle):
    # A conta global é de outro canal do YouTube: o painel de publicação do projeto mostra o canal dele.
    from studio.core.project import service

    info = yt.finish_oauth("c", _link()[0])
    _set_global()
    doc = service.create_project(title="T", style_id="misterios-short", topic="t", channel_id=info["vulto_channel_id"])
    with TestClient(create_app()) as client:
        assert client.get("/api/youtube/status").json()["channel_title"] == "Global"
        status = client.get("/api/youtube/status", params={"project_id": doc.id}).json()
    assert status["connected"] is True and status["channel_title"] == "Curiosidades Ocultas"
    assert status["reason"] is None


def test_link_existing_channel_keeps_persona_and_blocks_duplicates(google: FakeGoogle):
    with TestClient(create_app()) as client:
        mine = client.post("/api/channels", json={"name": "Meu canal", "niche": "ciência", "persona": "narrador calmo",
                                                  "languages": ["es-ES"]}).json()
        other = client.post("/api/channels", json={"name": "Outro"}).json()
        state, _ = _link(mine["id"])
        page = client.get("/api/youtube/callback", params={"code": "c", "state": state})
        assert page.status_code == 200 and "Canal vinculado" in page.text
        assert client.get(f"/api/youtube/link/{state}").json()["status"] == "done"

        linked = next(c for c in client.get("/api/channels").json() if c["id"] == mine["id"])
        assert linked["name"] == "Curiosidades Ocultas"
        assert linked["niche"] == "ciência" and linked["persona"] == "narrador calmo"
        assert linked["languages"] == ["es-ES"]  # canal existente: os idiomas escolhidos ficam
        assert linked["youtube"]["token"] == "ok" and linked["youtube"]["handle"] == "@curiosidadesocultas"

        # o mesmo canal do YouTube não pode ir para outro canal do Vulto
        state2, _ = _link(other["id"])
        page = client.get("/api/youtube/callback", params={"code": "c", "state": state2})
        assert page.status_code == 400 and "já está ligado" in page.text
        failed = client.get(f"/api/youtube/link/{state2}").json()
        assert failed["status"] == "error" and "já está ligado" in failed["error"]

        # pedido de vínculo para um canal que não existe
        assert client.post("/api/youtube/link", json={"channel_id": "chn_nao_existe"}).status_code == 404


def test_link_requires_oauth_client():
    with TestClient(create_app()) as client:
        resp = client.post("/api/youtube/link", json={})
        assert resp.status_code == 400 and "client_id" in resp.json()["detail"]
        status = client.get("/api/youtube/status").json()
        assert status["client_configured"] is False and status["connected"] is False and status["linked"] == []


def test_legacy_connect_still_uses_global_account(google: FakeGoogle):
    _configure_client()
    url = yt.start_oauth(REDIRECT)
    query = parse_qs(urlparse(url).query)
    assert "youtube.readonly" not in query["scope"][0]
    info = yt.finish_oauth("c", query["state"][0])
    assert info == {"channel_title": "Curiosidades Ocultas", "channel_id": YT_ID}
    assert yt.account()["connected"] and yt.linked_accounts() == []
    with session_scope() as s:
        assert s.query(Channel).count() == 0


def test_unlink_refresh_and_delete(google: FakeGoogle, isolated_settings):
    info = yt.finish_oauth("c", _link()[0])
    channel_id = info["vulto_channel_id"]
    with TestClient(create_app()) as client:
        google.subscribers = "777"
        refreshed = client.post(f"/api/channels/{channel_id}/youtube/refresh").json()
        assert refreshed["channel"]["youtube"]["subscribers"] == 777
        assert any(f"id={YT_ID}" in u for u in google.urls() if "/youtube/v3/channels" in u)

        # token recusado pelo Google: a tela mostra "error"
        google.refresh_ok = False
        resp = client.post(f"/api/channels/{channel_id}/youtube/refresh")
        assert resp.status_code == 400
        assert client.get("/api/channels").json()[0]["youtube"]["token"] == "error"
        google.refresh_ok = True

        # desvincular apaga só o token; os dados ficam
        unlinked = client.delete(f"/api/channels/{channel_id}/youtube").json()["channel"]
        assert unlinked["youtube"]["token"] == "missing" and unlinked["youtube"]["title"] == "Curiosidades Ocultas"
        assert credentials.get_credential(yt.channel_key(YT_ID)) == {}
        with pytest.raises(yt.YouTubeError, match="sem acesso"):
            yt.account_for_channel(channel_id)

        # esquecer tira os dados do YouTube e a foto
        avatar = isolated_settings.data_dir / ytc.avatar_rel(channel_id)
        assert avatar.is_file()
        forgotten = client.delete(f"/api/channels/{channel_id}/youtube", params={"forget": "true"}).json()["channel"]
        assert forgotten["youtube"] is None and not avatar.exists()

    # apagar o canal do Vulto apaga o token do canal do YouTube
    again = yt.finish_oauth("c", _link()[0])
    assert yt.has_token(YT_ID)
    with TestClient(create_app()) as client:
        assert client.delete(f"/api/channels/{again['vulto_channel_id']}").json() == {"deleted": True}
    assert not yt.has_token(YT_ID)


# --------------------------------------------------------------------------------------------
# Publicação pelo token do canal do projeto
# --------------------------------------------------------------------------------------------


def _set_global(channel_id: str = OTHER_ID) -> None:
    credentials.set_credential(yt.ACCOUNT_KEY, {"refresh_token": "rt-global", "channel_title": "Global", "channel_id": channel_id})


def test_account_for_channel_rules(google: FakeGoogle):
    from studio.core.project import service

    info = yt.finish_oauth("c", _link()[0])
    linked = info["vulto_channel_id"]
    with TestClient(create_app()) as client:
        plain = client.post("/api/channels", json={"name": "Sem vínculo"}).json()["id"]
    assert yt.account_for_channel(linked) == yt.channel_key(YT_ID)
    with pytest.raises(yt.YouTubeError, match="não está vinculado"):
        yt.account_for_channel(plain)
    with pytest.raises(yt.YouTubeError, match="conecte"):
        yt.account_for_channel(None)
    _set_global()
    assert yt.account_for_channel(plain) == yt.ACCOUNT_KEY  # compatibilidade: sem vínculo usa a global
    assert yt.account_for_channel(None) == yt.ACCOUNT_KEY
    assert yt.account_for_channel(linked) == yt.channel_key(YT_ID)

    # canal vinculado sem token nunca cai para a global de OUTRO canal do YouTube
    ytc.unlink(linked)
    with pytest.raises(yt.YouTubeError, match="sem acesso"):
        yt.account_for_channel(linked)
    _set_global(YT_ID)  # a global é deste mesmo canal: pode usar
    assert yt.account_for_channel(linked) == yt.ACCOUNT_KEY

    doc = service.create_project(title="T", style_id="misterios-short", topic="t", channel_id=plain)
    with TestClient(create_app()) as client:
        status = client.get("/api/youtube/status", params={"project_id": doc.id}).json()
        assert status["connected"] is True and status["channel_title"] == "Global"
        credentials.delete_credential(yt.ACCOUNT_KEY)
        status = client.get("/api/youtube/status", params={"project_id": doc.id}).json()
        assert status["connected"] is False and "não está vinculado" in status["reason"]
        resp = client.post(f"/api/projects/{doc.id}/publish/youtube", json={"privacy": "private"})
        assert resp.status_code == 400 and "Vincule" in resp.json()["detail"]


def test_publish_job_uses_project_channel_token(google: FakeGoogle, monkeypatch, isolated_settings):
    from studio.core.project import service
    from studio.workers.jobs import JobContext
    from studio.workers.publish_tasks import run_publish_youtube

    info = yt.finish_oauth("c", _link()[0])
    _set_global()
    doc = service.create_project(title="Vídeo", style_id="misterios-short", topic="farol", channel_id=info["vulto_channel_id"])
    video = isolated_settings.data_dir / "outputs" / "v.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x00" * 2048)
    with session_scope() as s:
        row = s.get(Project, doc.id)
        row.settings = {**(row.settings or {}), "last_final": {"langs": {"pt-BR": {"video": "outputs/v.mp4"}}}}

    refreshes: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            refreshes.append(parse_qs(request.content.decode())["refresh_token"][0])
            return httpx.Response(200, json={"access_token": "at"})
        if request.method == "POST" and "/upload/youtube/v3/videos" in url:
            return httpx.Response(200, headers={"Location": "https://upload.example/s/1"})
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "VIDX"})
        return httpx.Response(404)

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(handler)))
    result = run_publish_youtube(JobContext("job_teste", doc.id, threading.Event()), {"lang": "pt-BR", "privacy": "private"})
    assert result["video_id"] == "VIDX"
    assert refreshes == ["rt-canal"]  # o token do canal do projeto, não o da conta global
    with session_scope() as s:
        from studio.models import Publication

        pub = s.query(Publication).one()
        assert pub.details["youtube_channel"] == "Curiosidades Ocultas"


# --------------------------------------------------------------------------------------------
# Link público (sem login)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (YT_ID, ("id", YT_ID)),
        (f"https://www.youtube.com/channel/{YT_ID}/videos", ("id", YT_ID)),
        ("youtube.com/@curiosidadesocultas", ("handle", "@curiosidadesocultas")),
        ("https://m.youtube.com/@curiosidadesocultas/shorts?si=x", ("handle", "@curiosidadesocultas")),
        ("@curiosidadesocultas/videos", ("handle", "@curiosidadesocultas")),
        ("https://www.youtube.com/c/CuriosidadesOcultas", ("path", "/c/CuriosidadesOcultas")),
        ("https://www.youtube.com/user/antigo", ("path", "/user/antigo")),
    ],
)
def test_parse_channel_link(text, expected):
    assert yt.parse_channel_link(text) == expected


@pytest.mark.parametrize("text", ["", "https://youtu.be/abc", "https://www.youtube.com/watch?v=abc",
                                  "https://www.youtube.com/shorts/abc", "https://exemplo.com/@canal", "youtube.com/"])
def test_parse_channel_link_rejects(text):
    with pytest.raises(yt.YouTubeError):
        yt.parse_channel_link(text)


def test_public_link_by_handle_reads_page_once_and_feed(google: FakeGoogle, isolated_settings):
    with TestClient(create_app()) as client:
        resp = client.post("/api/channels/from-link", json={"url": "https://www.youtube.com/@curiosidadesocultas/videos"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
    assert body["created"] is True
    channel = body["channel"]
    assert channel["name"] == "Curiosidades Ocultas"
    data = channel["youtube"]
    assert data["channel_id"] == YT_ID  # o canonical, não o primeiro "channelId" da página
    assert data["source"] == "public" and data["token"] == "public"
    assert data["handle"] == "@curiosidadesocultas"
    assert data["description"] == "Fatos estranhos & histórias reais."
    assert [v["title"] for v in data["recent_videos"]] == ["Por que o céu é azul?", "O mistério do farol"]
    assert (isolated_settings.data_dir / data["avatar"]).is_file()
    pages = [r for r in google.requests if str(r.url).startswith(f"{yt.SITE}/@")]
    assert len(pages) == 1
    for request in google.requests:  # leitura pública: cabeçalho genérico, sem login
        assert "authorization" not in request.headers
        if not str(request.url).startswith("https://yt3."):
            assert request.headers["user-agent"] == yt.PUBLIC_HEADERS["User-Agent"]
    with pytest.raises(yt.YouTubeError, match="link público"):
        yt.account_for_channel(channel["id"])


def test_public_link_by_id_uses_only_feed_and_updates_existing(google: FakeGoogle):
    with TestClient(create_app()) as client:
        mine = client.post("/api/channels", json={"name": "Rascunho", "persona": "voz grave"}).json()
        resp = client.post("/api/channels/from-link", json={"url": f"youtube.com/channel/{YT_ID}", "channel_id": mine["id"]})
        assert resp.status_code == 200
        body = resp.json()
        assert body["created"] is False and body["channel"]["id"] == mine["id"]
        assert body["channel"]["persona"] == "voz grave" and body["channel"]["name"] == "Curiosidades Ocultas"
        assert all(not str(r.url).startswith(f"{yt.SITE}/@") for r in google.requests)
        bad = client.post("/api/channels/from-link", json={"url": "https://youtu.be/abc"})
        assert bad.status_code == 400 and "vídeo" in bad.json()["detail"]


def test_public_link_handle_not_found(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    monkeypatch.setattr(yt, "new_client", lambda timeout=60: httpx.Client(transport=httpx.MockTransport(handler)))
    with TestClient(create_app()) as client:
        resp = client.post("/api/channels/from-link", json={"url": "@naoexiste"})
        assert resp.status_code == 400 and "não encontrado" in resp.json()["detail"]


# --------------------------------------------------------------------------------------------
# Preencher com IA (LLM simulado)
# --------------------------------------------------------------------------------------------


def test_suggest_profile_validates_llm_output(google: FakeGoogle, monkeypatch):
    info = yt.finish_oauth("c", _link()[0])
    seen: dict = {}

    def fake_llm(task, system, prompt, schema, context):
        seen.update(task=task, prompt=prompt, schema=schema, context=context)
        data = {"niche": "curiosidades de ciência", "persona": "Narrador curioso e direto.", "languages": ["pt", "xx-YY", "en-GB"],
                "default_style_id": "estilo-que-nao-existe", "identity": "Tons escuros com vermelho.", "reason": "Shorts curtos."}
        return LLMResult(data=data, text=json.dumps(data), usage=Usage(units=0, unit_name="tokens"), model="teste"), "teste_llm"

    monkeypatch.setattr(ytc, "_llm", fake_llm)
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/channels/{info['vulto_channel_id']}/suggest")
        assert resp.status_code == 200, resp.text
        out = resp.json()
    assert out["niche"] == "curiosidades de ciência"
    assert out["languages"] == ["pt-BR", "en-US"]
    assert out["default_style_id"] is None  # id fora da lista de estilos é descartado
    assert out["identity"] == "Tons escuros com vermelho." and out["provider"] == "teste_llm"
    assert seen["task"] == "channel_profile"
    assert "Por que o céu é azul?" in seen["prompt"] and "Fatos estranhos da ciência" in seen["prompt"]
    assert "curiosidades-short" in seen["prompt"]  # lista de estilos vai junto
    schema = seen["schema"]
    assert schema["additionalProperties"] is False and set(schema["required"]) == set(schema["properties"])
    with session_scope() as s:  # nada é salvo sem o usuário revisar
        assert s.get(Channel, info["vulto_channel_id"]).niche == ""


def test_suggest_profile_through_llm_chain(google: FakeGoogle, monkeypatch):
    """Pela cadeia de LLM de verdade (offline: o mock), com a tarefa simulada aqui."""
    from studio.workers.pipeline import mock_content

    info = yt.finish_oauth("c", _link()[0])
    monkeypatch.setattr(mock_content, "mock_channel_profile", lambda ctx: {
        "niche": "ciência", "persona": "Calmo.", "languages": ["pt-BR"], "default_style_id": "curiosidades-short",
        "identity": "", "reason": ""}, raising=False)
    with TestClient(create_app()) as client:
        out = client.post(f"/api/channels/{info['vulto_channel_id']}/suggest").json()
        assert out["default_style_id"] == "curiosidades-short" and out["provider"] == "mock_llm"
        plain = client.post("/api/channels", json={"name": "Manual"}).json()
        assert client.post(f"/api/channels/{plain['id']}/suggest").status_code == 422
        assert client.post("/api/channels/chn_x/suggest").status_code == 404


def test_app_language_mapping():
    assert ytc.app_language("pt") == "pt-BR"
    assert ytc.app_language("pt-PT") == "pt-BR"
    assert ytc.app_language("es-419") == "es-MX"
    assert ytc.app_language("es") == "es-ES"
    assert ytc.app_language("en-GB") == "en-US"
    assert ytc.app_language(None, "BR") == "pt-BR"
    assert ytc.app_language("zz", "ZZ") is None
    assert ytc.parse_keywords('a "b c" a  "d') in (["a", "b c", "d"], ["a", "b", "c", "d"])


def test_status_lists_linked_channels(google: FakeGoogle):
    yt.finish_oauth("c", _link()[0])
    with TestClient(create_app()) as client:
        status = client.get("/api/youtube/status").json()
    assert status["client_configured"] is True
    assert status["connected"] is True and status["global_connected"] is False
    assert status["linked"] == [{"channel_id": YT_ID, "channel_title": "Curiosidades Ocultas"}]
    assert status["channel_title"] == "Curiosidades Ocultas"
    providers = {p["id"]: p for p in TestClient(create_app()).get("/api/providers").json()}
    assert providers["youtube"]["available"] is True


def test_avatar_only_from_google_image_hosts(tmp_path: Path):
    calls: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: calls.append(str(r.url)) or httpx.Response(200, content=_png())))
    assert ytc.save_avatar(client, "https://exemplo.com/foto.png", "chn_1") is None
    assert ytc.save_avatar(client, "http://yt3.ggpht.com/foto", "chn_1") is None
    assert calls == []
    assert ytc.save_avatar(client, "https://yt3.ggpht.com/foto", "chn_1") == "channels/chn_1/avatar.jpg"
