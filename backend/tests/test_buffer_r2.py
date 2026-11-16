"""Driver do Buffer (TikTok e Instagram) e hospedagem no Cloudflare R2, com as APIs simuladas (nada de rede)."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.db import session_scope
from studio.models import Channel, Project, Publication
from studio.providers import credentials
from studio.workers.publish import buffer, buffer_flow, r2

ACCOUNT = "abc123"
BUCKET = "videos"
R2_HOST = f"{ACCOUNT}.r2.cloudflarestorage.com"
PUBLIC = "https://pub-teste.r2.dev"


def _configure(r2_too: bool = True) -> None:
    credentials.set_credential("buffer", {"api_key": "chave-buffer"})
    if r2_too:
        credentials.set_credential(r2.PROVIDER_ID, {
            "account_id": ACCOUNT, "access_key_id": "AKID", "secret_access_key": "segredo",
            "bucket": BUCKET, "public_url": PUBLIC + "/",
        })


class FakeCloud:
    """Simula o R2 (PUT/DELETE de objetos) e a API GraphQL do Buffer."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []
        self.created: list[dict] = []
        self.deleted_posts: list[str] = []
        self.posts: dict[str, dict] = {}
        self.create_error: str | None = None
        self.graphql_calls: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == R2_HOST:
            key = request.url.path.removeprefix(f"/{BUCKET}/")
            assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKID/")
            assert "/auto/s3/aws4_request" in request.headers["authorization"]
            if request.method == "PUT":
                body = request.read()
                assert request.headers["x-amz-content-sha256"] == hashlib.sha256(body).hexdigest()
                assert request.headers["content-length"] == str(len(body))
                self.objects[key] = body
                return httpx.Response(200)
            if request.method == "DELETE":
                self.deleted.append(key)
                self.objects.pop(key, None)
                return httpx.Response(204)
        if str(request.url).rstrip("/") == buffer.API_URL:
            assert request.headers["authorization"] == "Bearer chave-buffer"
            body = json.loads(request.content)
            query, variables = body["query"], body.get("variables") or {}
            self.graphql_calls.append(query)
            if "account" in query and "organizations" in query:
                return httpx.Response(200, json={"data": {"account": {"organizations": [{"id": "org1", "name": "Minha"}]}}})
            if "channels(" in query:
                assert variables["input"]["organizationId"] == "org1"
                return httpx.Response(200, json={"data": {"channels": [
                    {"id": "ch-tt", "name": "tt", "displayName": "Meu TikTok", "service": "tiktok", "avatar": "",
                     "isDisconnected": False, "isLocked": False, "isQueuePaused": False, "timezone": "America/Sao_Paulo"},
                    {"id": "ch-ig", "name": "ig", "displayName": "Meu Insta", "service": "instagram", "avatar": "",
                     "isDisconnected": False, "isLocked": False, "isQueuePaused": False, "timezone": "America/Sao_Paulo"},
                    {"id": "ch-x", "name": "x", "displayName": "X", "service": "twitter", "avatar": "",
                     "isDisconnected": False, "isLocked": False, "isQueuePaused": False, "timezone": "UTC"},
                ]}})
            if "createPost" in query:
                data = variables["input"]
                self.created.append(data)
                if self.create_error:
                    return httpx.Response(200, json={"data": {"createPost": {"message": self.create_error}}})
                post = {"id": f"post{len(self.created)}", "status": "scheduled",
                        "dueAt": data.get("dueAt") or "2030-01-02T21:00:00.000Z", "externalLink": None}
                self.posts[post["id"]] = dict(post)
                return httpx.Response(200, json={"data": {"createPost": {"post": post}}})
            if "deletePost" in query:
                post_id = variables["input"]["id"]
                self.deleted_posts.append(post_id)
                self.posts.pop(post_id, None)
                return httpx.Response(200, json={"data": {"deletePost": {"id": post_id}}})
            if "post(" in query:
                post = self.posts.get(variables["input"]["id"])
                if post is None:
                    return httpx.Response(200, json={"data": None, "errors": [
                        {"message": "Post not found", "extensions": {"code": "NOT_FOUND"}}]})
                return httpx.Response(200, json={"data": {"post": post}})
        return httpx.Response(404, text="rota desconhecida")


@pytest.fixture()
def cloud(monkeypatch: pytest.MonkeyPatch) -> FakeCloud:
    fake = FakeCloud()
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda *a, **kw: real_client(transport=httpx.MockTransport(fake.handler)))
    return fake


def _rendered_project(settings, *, preview_only: bool = False, channel: bool = True) -> str:
    from studio.core.project import service

    channel_id = None
    if channel:
        channel_id = "chan_teste"
        with session_scope() as s:
            s.add(Channel(id=channel_id, name="Canal", settings={}))
    doc = service.create_project(title="Farol assombrado", style_id="misterios-short", topic="farol", channel_id=channel_id)
    video = settings.data_dir / "outputs" / "v.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"\x01\x02" * 3000)
    with session_scope() as s:
        row = s.get(Project, doc.id)
        last = {"langs": {"pt-BR": {"video": "outputs/v.mp4"}}}
        if preview_only:
            last["preview_only_providers"] = ["mock_tts"]
        row.settings = {**(row.settings or {}), "last_final": last}
    return doc.id


def _run_job(project_id: str, payload: dict):
    from studio.workers.jobs import get_job, get_runner

    job = get_runner().submit("publish_buffer", payload, project_id, run_inline=True)
    return get_job(job["id"])


# --------------------------------------------------------------------------------------------
# R2
# --------------------------------------------------------------------------------------------


def test_sigv4_matches_the_official_aws_example():
    """Exemplo "GET Object" da documentação da AWS (Signature Version 4, cabeçalho Authorization)."""
    headers = r2.sign(
        "GET", "https://examplebucket.s3.amazonaws.com/test.txt",
        access_key="AKIAIOSFODNN7EXAMPLE", secret_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        payload_hash=r2.EMPTY_SHA256, headers={"Range": "bytes=0-9"}, region="us-east-1",
        now=datetime(2013, 5, 24, tzinfo=UTC),
    )
    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 Credential=AKIAIOSFODNN7EXAMPLE/20130524/us-east-1/s3/aws4_request, "
        "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date, "
        "Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"
    )
    assert headers["x-amz-date"] == "20130524T000000Z"


def test_r2_availability_needs_every_field_and_https():
    ok, reason = r2.R2Storage().availability()
    assert not ok and "account_id" in reason
    _configure()
    assert r2.R2Storage().availability() == (True, "ok")
    credentials.set_credential(r2.PROVIDER_ID, {**credentials.get_credential(r2.PROVIDER_ID), "public_url": "http://x.dev"})
    ok, reason = r2.R2Storage().availability()
    assert not ok and "https://" in reason


def test_r2_upload_and_delete(tmp_path, cloud: FakeCloud):
    _configure()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"abc" * (r2.READ_CHUNK // 2))  # mais de um pedaço de leitura
    progress: list[float] = []
    key = r2.new_key("proj_1", "tiktok-pt-BR")
    assert key.startswith("vulto/proj_1/tiktok-pt-BR-") and key.endswith(".mp4")
    url = r2.upload(video, key, on_progress=progress.append)
    assert url == f"{PUBLIC}/{key}"
    assert cloud.objects[key] == video.read_bytes()
    assert progress[-1] == 1.0
    r2.delete(key)
    assert cloud.deleted == [key] and key not in cloud.objects


def test_r2_explains_refused_credentials(tmp_path, monkeypatch):
    _configure()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(403, text="<Error>AccessDenied</Error>")))
    with pytest.raises(r2.R2Error, match="permissão de escrita"):
        r2.upload(video, "vulto/k.mp4", http=client)


# --------------------------------------------------------------------------------------------
# Buffer
# --------------------------------------------------------------------------------------------


def test_buffer_availability_needs_key_and_storage():
    assert buffer.BufferPublisher().availability() == (False, "cole a chave de API do Buffer")
    _configure(r2_too=False)
    ok, reason = buffer.BufferPublisher().availability()
    assert not ok and "Cloudflare R2" in reason
    _configure()
    assert buffer.BufferPublisher().availability() == (True, "ok")


def test_buffer_env_var_wins(monkeypatch):
    monkeypatch.setenv("BUFFER_API_KEY", "da-variavel")
    assert buffer.api_key() == "da-variavel"


def test_graphql_errors_are_explained():
    _configure()
    unauthorized = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
        "data": None, "errors": [{"message": "Unauthorized", "extensions": {"code": "UNAUTHORIZED"}}]})))
    with pytest.raises(buffer.BufferError, match="chave de API do Buffer foi recusada") as info:
        buffer.graphql("query { account { id } }", http=unauthorized)
    assert info.value.code == "UNAUTHORIZED"
    limited = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "30"})))
    with pytest.raises(buffer.BufferError, match="em 30 s") as info:
        buffer.graphql("query { account { id } }", http=limited)
    assert info.value.code == "RATE_LIMIT_EXCEEDED"


def test_list_channels_keeps_only_tiktok_and_instagram(cloud: FakeCloud):
    _configure()
    channels = buffer.list_channels()
    assert [(c["id"], c["service"], c["name"]) for c in channels] == [
        ("ch-tt", "tiktok", "Meu TikTok"), ("ch-ig", "instagram", "Meu Insta")]


def test_post_input_follows_the_schema():
    future = datetime.now(UTC) + timedelta(days=1)
    reel = buffer.post_input(channel_id="c", platform="instagram", text="oi", video_url="https://v/x.mp4",
                             mode="scheduled", due_at=future, ai_generated=True)
    assert reel["mode"] == "customScheduled" and reel["schedulingType"] == "automatic"
    assert reel["dueAt"].endswith(".000Z")
    assert reel["metadata"] == {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": True}}
    assert reel["assets"] == [{"video": {"url": "https://v/x.mp4"}}]
    tiktok = buffer.post_input(channel_id="c", platform="tiktok", text="oi", video_url="https://v/x.mp4", mode="queue")
    assert tiktok["mode"] == "addToQueue" and "dueAt" not in tiktok
    assert tiktok["metadata"] == {"tiktok": {"isAiGenerated": False}}
    with pytest.raises(buffer.BufferError, match="futuro"):
        buffer.post_input(channel_id="c", platform="tiktok", text="", video_url="u", mode="scheduled",
                          due_at=datetime.now(UTC) - timedelta(minutes=1))
    with pytest.raises(buffer.BufferError, match="só no TikTok e no Instagram"):
        buffer.post_input(channel_id="c", platform="x", text="", video_url="u", mode="queue")


# --------------------------------------------------------------------------------------------
# Job, sincronização, limpeza e cancelamento
# --------------------------------------------------------------------------------------------


def test_job_uploads_to_r2_creates_post_and_remembers_channel(isolated_settings, cloud: FakeCloud):
    _configure()
    pid = _rendered_project(isolated_settings)
    final = _run_job(pid, {"lang": "pt-BR", "platform": "instagram", "buffer_channel_id": "ch-ig",
                           "buffer_channel_name": "Meu Insta", "mode": "queue"})
    assert final["status"] == "done", final["error"]
    [created] = cloud.created
    [key] = cloud.objects
    assert created["assets"][0]["video"]["url"] == f"{PUBLIC}/{key}"
    assert created["channelId"] == "ch-ig" and created["mode"] == "addToQueue"
    assert created["text"].startswith("Farol assombrado")
    with session_scope() as s:
        pub = s.query(Publication).one()
        assert (pub.driver, pub.platform, pub.status, pub.external_id) == ("buffer", "instagram", "scheduled", "post1")
        assert pub.details["r2_key"] == key and pub.details["tracking"] is True
        assert pub.scheduled_at is not None
        assert s.get(Channel, "chan_teste").settings["buffer"] == {"instagram": "ch-ig"}


def test_job_removes_video_when_buffer_refuses(isolated_settings, cloud: FakeCloud):
    _configure()
    cloud.create_error = "Video is too long"
    pid = _rendered_project(isolated_settings)
    final = _run_job(pid, {"lang": "pt-BR", "platform": "tiktok", "buffer_channel_id": "ch-tt", "mode": "now"})
    assert final["status"] == "failed" and "Video is too long" in final["error"]
    assert cloud.objects == {} and len(cloud.deleted) == 1
    with session_scope() as s:
        assert s.query(Publication).count() == 0


def test_job_blocks_preview_render_before_uploading(isolated_settings, cloud: FakeCloud):
    _configure()
    pid = _rendered_project(isolated_settings, preview_only=True)
    final = _run_job(pid, {"lang": "pt-BR", "platform": "tiktok", "buffer_channel_id": "ch-tt", "mode": "queue"})
    assert final["status"] == "failed" and "prévia" in final["error"]
    assert cloud.objects == {} and cloud.created == []


def test_job_checks_schedule_before_uploading(isolated_settings, cloud: FakeCloud):
    _configure()
    pid = _rendered_project(isolated_settings)
    past = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    final = _run_job(pid, {"lang": "pt-BR", "platform": "tiktok", "buffer_channel_id": "ch-tt", "mode": "scheduled",
                           "publish_at": past})
    assert final["status"] == "failed" and "futuro" in final["error"]
    assert cloud.objects == {}


def _published_job(settings, cloud: FakeCloud) -> tuple[str, str]:
    _configure()
    pid = _rendered_project(settings)
    final = _run_job(pid, {"lang": "pt-BR", "platform": "tiktok", "buffer_channel_id": "ch-tt", "mode": "queue"})
    assert final["status"] == "done", final["error"]
    with session_scope() as s:
        pub = s.query(Publication).one()
        return pub.id, pub.details["r2_key"]


def test_sync_marks_sent_posts_and_cleans_r2(isolated_settings, cloud: FakeCloud):
    pub_id, key = _published_job(isolated_settings, cloud)
    assert buffer_flow.sync()["checked"] == 0  # recém-criado: espera o intervalo mínimo
    result = buffer_flow.sync(force=True)
    assert result["items"][0]["status"] == "scheduled" and key in cloud.objects
    cloud.posts["post1"].update(status="sent", externalLink="https://www.tiktok.com/@eu/video/1",
                                sentAt="2030-01-02T21:00:05Z")
    result = buffer_flow.sync(force=True)
    assert result["items"][0] == {"id": pub_id, "status": "published", "url": "https://www.tiktok.com/@eu/video/1",
                                  "error": None, "tracking": False, "r2_deleted": True}
    assert cloud.deleted == [key] and key not in cloud.objects
    calls = len(cloud.graphql_calls)
    assert buffer_flow.sync(force=True)["checked"] == 0  # terminado: não consulta mais
    assert len(cloud.graphql_calls) == calls
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        assert pub.details["published_at"] == "2030-01-02T21:00:05Z"


def test_sync_records_buffer_error_and_cleans(isolated_settings, cloud: FakeCloud):
    pub_id, key = _published_job(isolated_settings, cloud)
    cloud.posts["post1"].update(status="error", error={"message": "TikTok recusou o vídeo"})
    buffer_flow.sync(force=True)
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        assert pub.status == "failed" and pub.error == "TikTok recusou o vídeo"
        assert pub.details["tracking"] is False
    assert cloud.deleted == [key]


def test_sync_post_deleted_in_buffer(isolated_settings, cloud: FakeCloud):
    pub_id, key = _published_job(isolated_settings, cloud)
    cloud.posts.clear()
    buffer_flow.sync(force=True)
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        assert pub.status == "canceled" and "apagado no Buffer" in pub.error
    assert cloud.deleted == [key]


def test_sync_retries_cleanup_when_r2_fails(isolated_settings, cloud: FakeCloud, monkeypatch):
    pub_id, key = _published_job(isolated_settings, cloud)
    cloud.posts["post1"]["status"] = "sent"
    real_delete = r2.delete

    def broken(key, http=None):
        raise r2.R2Error("fora do ar")

    monkeypatch.setattr(r2, "delete", broken)
    buffer_flow.sync(force=True)
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        assert pub.status == "published" and pub.details["tracking"] is True
        assert pub.details["cleanup_error"] == "fora do ar"
    monkeypatch.setattr(r2, "delete", real_delete)
    calls = len(cloud.graphql_calls)
    buffer_flow.sync(force=True)
    assert len(cloud.graphql_calls) == calls  # já publicado: só tenta apagar de novo
    assert cloud.deleted == [key]
    with session_scope() as s:
        assert s.get(Publication, pub_id).details["tracking"] is False


def test_cancel_deletes_post_and_video(isolated_settings, cloud: FakeCloud):
    pub_id, key = _published_job(isolated_settings, cloud)
    result = buffer_flow.cancel(pub_id)
    assert result == {"id": pub_id, "status": "canceled", "r2_deleted": True}
    assert cloud.deleted_posts == ["post1"] and cloud.deleted == [key]


def test_sync_quietly_never_raises(isolated_settings, monkeypatch):
    monkeypatch.setattr(buffer_flow, "sync", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    buffer_flow.sync_quietly()


# --------------------------------------------------------------------------------------------
# Rotas
# --------------------------------------------------------------------------------------------


def test_routes_status_channels_publish_and_cancel(isolated_settings, cloud: FakeCloud):
    with TestClient(create_app()) as client:
        status = client.get("/api/buffer/status").json()
        assert status["ready"] is False and status["api_key"] is False
        resp = client.post("/api/projects/nada/publish/buffer", json={"platform": "tiktok", "buffer_channel_id": "c"})
        assert resp.status_code == 404
        _configure()
        pid = _rendered_project(isolated_settings)
        status = client.get("/api/buffer/status", params={"project_id": pid}).json()
        assert status["ready"] is True and status["remembered"] == {}
        channels = client.get("/api/buffer/channels").json()["channels"]
        assert {c["service"] for c in channels} == {"tiktok", "instagram"}
        bad = client.post(f"/api/projects/{pid}/publish/buffer", json={"platform": "x", "buffer_channel_id": "c"})
        assert bad.status_code == 400
        bad = client.post(f"/api/projects/{pid}/publish/buffer",
                          json={"platform": "tiktok", "buffer_channel_id": "c", "mode": "scheduled"})
        assert bad.status_code == 400 and "data e a hora" in bad.json()["detail"]
        providers = {p["id"]: p for p in client.get("/api/providers").json()}
        assert providers["buffer"]["kind"] == "publish" and providers["buffer"]["available"] is True
        assert providers["cloudflare_r2"]["credentials"]["configured"] is True


def test_cancel_route(isolated_settings, cloud: FakeCloud):
    pub_id, key = _published_job(isolated_settings, cloud)
    with TestClient(create_app()) as client:
        assert client.post("/api/publications/naoexiste/buffer/cancel").status_code == 404
        resp = client.post(f"/api/publications/{pub_id}/buffer/cancel")
        assert resp.status_code == 200 and resp.json()["status"] == "canceled"
        cloud.posts["post1"] = {"id": "post1", "status": "sent"}
        with session_scope() as s:
            s.get(Publication, pub_id).status = "published"
        resp = client.post(f"/api/publications/{pub_id}/buffer/cancel")
        assert resp.status_code == 400 and "já foi publicado" in resp.json()["detail"]
        assert client.post("/api/buffer/sync").status_code == 200
