"""Instagram nativo: login, token no cofre, Reel por URL pública, espera do processamento, limites e rotas.

Tudo com a API simulada (httpx.MockTransport); nada chama a Meta de verdade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import Metadata
from studio.db import session_scope
from studio.models import Channel, Publication
from studio.providers import credentials
from studio.workers import publish_tasks
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import instagram as ig

API = ig.API


class FakeMeta:
    """Graph API simulada: guarda as chamadas e responde conforme o cenário."""

    def __init__(self, statuses: list[str] | None = None, quota_used: int = 0) -> None:
        self.calls: list[httpx.Request] = []
        self.statuses = list(statuses or ["FINISHED"])
        self.quota_used = quota_used
        self.media_bodies: list[dict[str, list[str]]] = []
        self.refuse_ai_label = False
        self.head_status = 200

    def form(self, request: httpx.Request) -> dict[str, list[str]]:
        return parse_qs(request.content.decode())

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        url = str(request.url)
        path = request.url.path
        if request.method == "HEAD":
            return httpx.Response(self.head_status)
        if url.startswith(ig.TOKEN_URL):
            body = self.form(request)
            assert body["grant_type"] == ["authorization_code"]
            assert body["code"] == ["CODE1"]
            return httpx.Response(200, json={"data": [{"access_token": "curto", "user_id": "178",
                                                       "permissions": "instagram_business_basic,instagram_business_content_publish"}]})
        if path == "/access_token":
            if request.url.params.get("access_token") != "curto":
                return httpx.Response(400, json={"error": {"message": "token inválido para troca", "code": 100}})
            return httpx.Response(200, json={"access_token": "longo", "token_type": "bearer", "expires_in": 5183944})
        if path == "/refresh_access_token":
            return httpx.Response(200, json={"access_token": "renovado", "token_type": "bearer", "expires_in": 5183944})
        assert request.headers.get("Authorization", "").startswith("Bearer "), url
        if path.endswith("/me"):
            account_type = "PERSONAL" if request.headers["Authorization"] == "Bearer pessoal" else "BUSINESS"
            return httpx.Response(200, json={"user_id": "178", "username": "vulto.br", "account_type": account_type})
        if path.endswith("/content_publishing_limit"):
            return httpx.Response(200, json={"data": [{"quota_usage": self.quota_used,
                                                       "config": {"quota_total": 50, "quota_duration": 86400}}]})
        if path.endswith("/178/media") and request.method == "POST":
            body = self.form(request)
            self.media_bodies.append(body)
            if self.refuse_ai_label and "is_ai_generated" in body:
                return httpx.Response(400, json={"error": {"message": "Invalid parameter is_ai_generated", "code": 100}})
            return httpx.Response(200, json={"id": "CONT1"})
        if path.endswith("/CONT1"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            return httpx.Response(200, json={"status_code": status, "status": "Error: formato" if status == "ERROR" else ""})
        if path.endswith("/178/media_publish"):
            assert self.form(request)["creation_id"] == ["CONT1"]
            return httpx.Response(200, json={"id": "MEDIA9"})
        if path.endswith("/MEDIA9"):
            return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/abc/"})
        return httpx.Response(404, json={"error": {"message": f"rota inesperada {url}"}})


@pytest.fixture
def meta(monkeypatch: pytest.MonkeyPatch) -> FakeMeta:
    fake = FakeMeta()
    monkeypatch.setattr(ig, "new_client", lambda timeout=60: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(ig, "pause", lambda seconds: None)
    return fake


def _app() -> None:
    credentials.set_credential("instagram", {"app_id": "999", "app_secret": "segredo"})


def _account(user_id: str = "178", username: str = "vulto.br", age: timedelta = timedelta(hours=1),
             left: timedelta = timedelta(days=59)) -> None:
    now = datetime.now(UTC)
    credentials.set_credential(ig.account_key(user_id), {
        "access_token": "longo", "user_id": user_id, "username": username, "account_type": "BUSINESS",
        "refreshed_at": (now - age).isoformat(), "expires_at": (now + left).isoformat(),
    })


def _channel(channel_id: str = "chn_1") -> str:
    with session_scope() as s:
        s.add(Channel(id=channel_id, name="Mistérios"))
    return channel_id


def _rendered(isolated_settings, channel_id: str | None = None, preview_only: list[str] | None = None,
              duration: float = 42.0) -> str:
    doc = service.create_project(title="O farol", style_id="misterios-short", channel_id=channel_id)
    doc = doc.model_copy(update={"metadata": {"pt-BR": Metadata(title="O farol", description="Resumo.\n\nMais.",
                                                                 hashtags=["#misterio", "farol"])}})
    service.commit(doc.id, doc, "pronto")
    out = isolated_settings.outputs_dir / doc.id / "pt-BR"
    out.mkdir(parents=True, exist_ok=True)
    (out / "o farol.mp4").write_bytes(b"mp4")
    service.update_settings(doc.id, {"last_final": {
        "langs": {"pt-BR": {"video": f"outputs/{doc.id}/pt-BR/o farol.mp4", "thumbnail": f"outputs/{doc.id}/pt-BR/capa.jpg",
                            "duration": duration}},
        "preview_only_providers": preview_only or [],
    }})
    return doc.id


def _run(project_id: str, payload: dict) -> dict:
    job = get_runner().submit("publish_instagram", payload, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None
    return final


# ------------------------------------------------------------------------------------------------
# Login e token
# ------------------------------------------------------------------------------------------------


def test_login_url_uses_instagram_scopes_and_local_redirect():
    _app()
    info = ig.start_link("http://127.0.0.1:8765/api/instagram/callback")
    query = parse_qs(urlparse(info["auth_url"]).query)
    assert info["auth_url"].startswith(ig.AUTH_URL)
    assert query["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    assert query["redirect_uri"] == ["http://127.0.0.1:8765/api/instagram/callback"]
    assert query["response_type"] == ["code"] and query["state"] == [info["state"]]
    ig.set_options(redirect_uri="https://exemplo.dev/retorno")
    assert ig.start_link("http://local")["redirect_uri"] == "https://exemplo.dev/retorno"


def test_pasted_url_strips_the_hash_suffix():
    assert ig.parse_pasted("https://exemplo.dev/retorno?code=ABC123&state=xyz#_") == ("ABC123", "xyz")
    assert ig.parse_pasted("ABC123#_") == ("ABC123", None)
    with pytest.raises(ig.InstagramError):
        ig.parse_pasted("https://exemplo.dev/retorno?state=xyz")


def test_finish_login_saves_long_lived_token_and_links_channel(meta: FakeMeta):
    _app()
    channel_id = _channel()
    state = ig.start_link("http://local", channel_id)["state"]
    info = ig.finish_oauth("CODE1#_", state)
    assert info["user_id"] == "178" and info["username"] == "vulto.br"
    saved = ig.get_account("178")
    assert saved["access_token"] == "longo" and not saved.get("expires_estimated")
    assert ig.link_status(state)["status"] == "done"
    assert ig.channel_link(channel_id) == {"user_id": "178", "username": "vulto.br", "driver": "native"}
    with pytest.raises(ig.InstagramError, match="desconhecido"):
        ig.finish_oauth("CODE1", state)


def test_pasted_dashboard_token_is_kept_when_exchange_fails(meta: FakeMeta):
    _app()
    info = ig.connect_with_token("ja-longo")
    assert info["expires_estimated"] is True
    assert ig.get_account("178")["access_token"] == "ja-longo"
    with pytest.raises(ig.InstagramError, match="pessoal"):
        ig.connect_with_token("pessoal")


def test_token_is_refreshed_only_when_due(meta: FakeMeta):
    client = ig.new_client()
    _account(age=timedelta(hours=2), left=timedelta(days=59))
    assert ig.access_token(client, "178") == "longo"
    _account(age=timedelta(days=20), left=timedelta(days=40))
    assert ig.access_token(client, "178") == "renovado"
    assert ig.get_account("178")["access_token"] == "renovado"
    _account(age=timedelta(days=61), left=timedelta(days=-1))
    with pytest.raises(ig.InstagramError, match="expirou"):
        ig.access_token(client, "178")


def test_account_choice_per_channel():
    channel_id = _channel()
    with pytest.raises(ig.InstagramError, match="conecte"):
        ig.account_for_channel(channel_id)
    _account("178", "um")
    assert ig.account_for_channel(channel_id) == "178"
    _account("200", "dois")
    with pytest.raises(ig.InstagramError, match="mais de uma"):
        ig.account_for_channel(channel_id)
    ig.set_channel_link(channel_id, user_id="200", driver="manual")
    assert ig.account_for_channel(channel_id) == "200"
    ig.forget_account("200")
    assert "user_id" not in ig.channel_link(channel_id)
    assert ig.channel_link(channel_id)["driver"] == "manual"


def test_rate_limit_errors_are_recognized():
    resp = httpx.Response(400, json={"error": {"message": "Application request limit reached", "code": 4}})
    with pytest.raises(ig.RateLimited):
        ig._raise_for(resp, "publicar")
    resp = httpx.Response(400, json={"error": {"message": "limite", "code": 9, "error_subcode": 2207042}})
    with pytest.raises(ig.RateLimited):
        ig._raise_for(resp, "publicar")


def test_caption_and_video_limits():
    text = " ".join(f"#t{i}" for i in range(40))
    assert ig.fit_caption(text).count("#") == 30
    assert len(ig.fit_caption("a" * 3000)) == 2200
    with pytest.raises(ig.InstagramError, match="3 s a 15 min"):
        ig.check_video(None, 2.0)
    with pytest.raises(ig.InstagramError, match="3 s a 15 min"):
        ig.check_video(None, 16 * 60)
    ig.check_video(None, 60)


# ------------------------------------------------------------------------------------------------
# Publicação (job)
# ------------------------------------------------------------------------------------------------


def test_publish_reel_with_public_base_url(isolated_settings, meta: FakeMeta, monkeypatch: pytest.MonkeyPatch):
    _account()
    ig.set_options(public_base_url="https://cdn.exemplo.dev/vulto/")
    meta.statuses = ["IN_PROGRESS", "IN_PROGRESS", "FINISHED"]
    monkeypatch.setattr(publish_tasks, "ai_label", lambda style: True)
    pid = _rendered(isolated_settings)
    final = _run(pid, {"lang": "pt-BR", "share_to_feed": False})
    assert final["status"] == "done", final["error"]
    assert final["result"]["url"] == "https://www.instagram.com/reel/abc/"
    body = meta.media_bodies[0]
    assert body["media_type"] == ["REELS"]
    assert body["video_url"] == [f"https://cdn.exemplo.dev/vulto/outputs/{pid}/pt-BR/o%20farol.mp4"]
    assert body["cover_url"] == [f"https://cdn.exemplo.dev/vulto/outputs/{pid}/pt-BR/capa.jpg"]
    assert body["share_to_feed"] == ["false"]
    assert body["is_ai_generated"] == ["true"]
    assert body["caption"] == ["O farol\n\nResumo.\n\n#misterio #farol"]
    polls = [c for c in meta.calls if c.url.path.endswith("/CONT1")]
    assert len(polls) == 3
    with session_scope() as s:
        pub = s.query(Publication).filter_by(project_id=pid, platform="instagram").one()
        assert pub.driver == "instagram_native" and pub.status == "published"
        assert pub.external_id == "MEDIA9" and pub.details["ai_label"] is True


def test_ai_label_refused_retries_without_it(isolated_settings, meta: FakeMeta, monkeypatch: pytest.MonkeyPatch):
    _account()
    meta.refuse_ai_label = True
    monkeypatch.setattr(publish_tasks, "ai_label", lambda style: True)
    pid = _rendered(isolated_settings)
    final = _run(pid, {"video_url": "https://arquivos.exemplo.dev/reel.mp4"})
    assert final["status"] == "done", final["error"]
    assert len(meta.media_bodies) == 2 and "is_ai_generated" not in meta.media_bodies[1]
    assert any("Rótulo de IA" in w for w in final["result"]["warnings"])


def test_publish_needs_a_public_url_and_a_commercial_render(isolated_settings, meta: FakeMeta):
    _account()
    pid = _rendered(isolated_settings)
    final = _run(pid, {})
    assert final["status"] == "failed" and "URL pública" in final["error"]
    final = _run(pid, {"video_url": "http://inseguro.exemplo.dev/v.mp4"})
    assert final["status"] == "failed" and "https://" in final["error"]
    meta.head_status = 403
    final = _run(pid, {"video_url": "https://privado.exemplo.dev/v.mp4"})
    assert final["status"] == "failed" and "403" in final["error"]
    test_pid = _rendered(isolated_settings, preview_only=["windows_sapi"])
    final = _run(test_pid, {"video_url": "https://arquivos.exemplo.dev/reel.mp4"})
    assert final["status"] == "failed" and "prévia" in final["error"]
    assert not meta.media_bodies


def test_quota_used_up_blocks_before_creating(isolated_settings, meta: FakeMeta):
    _account()
    meta.quota_used = 50
    pid = _rendered(isolated_settings)
    final = _run(pid, {"video_url": "https://arquivos.exemplo.dev/reel.mp4"})
    assert final["status"] == "failed" and "50 dos 50" in final["error"]
    assert not meta.media_bodies


def test_container_error_fails_with_reason(isolated_settings, meta: FakeMeta):
    _account()
    meta.statuses = ["ERROR"]
    pid = _rendered(isolated_settings)
    final = _run(pid, {"video_url": "https://arquivos.exemplo.dev/reel.mp4"})
    assert final["status"] == "failed" and "não aceitou o vídeo" in final["error"]


class _InlineRunner:
    """Roda o job da rota na hora (sem a thread do executor)."""

    def submit(self, kind, payload, project_id):
        return get_runner().submit(kind, payload, project_id, run_inline=True)


def test_slow_processing_is_saved_and_resumed_through_the_api(isolated_settings, meta: FakeMeta,
                                                             monkeypatch: pytest.MonkeyPatch):
    from studio.api import routes_instagram

    monkeypatch.setattr(routes_instagram, "get_runner", lambda: _InlineRunner())
    _account()
    meta.statuses = ["IN_PROGRESS"]
    pid = _rendered(isolated_settings)
    final = _run(pid, {"video_url": "https://arquivos.exemplo.dev/reel.mp4"})
    assert final["status"] == "done" and final["result"]["pending"] is True
    pub_id = final["result"]["publication_id"]
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        assert pub.status == "processing" and pub.details["container_id"] == "CONT1"
    meta.statuses = ["FINISHED"]
    created_before = len(meta.media_bodies)
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/projects/{pid}/publish/instagram", json={"publication_id": pub_id})
        assert resp.status_code == 200, resp.text
        job = get_job(resp.json()["id"])
        assert job and job["status"] == "done", job and job["error"]
        assert job["payload"]["container_id"] == "CONT1"
        # Publicação já resolvida não pode ser retomada de novo.
        again = client.post(f"/api/projects/{pid}/publish/instagram", json={"publication_id": pub_id})
        assert again.status_code == 400
    assert len(meta.media_bodies) == created_before  # retomar não cria outro envio
    with session_scope() as s:
        pub = s.get(Publication, pub_id)
        assert pub.status == "published" and pub.url == "https://www.instagram.com/reel/abc/" and pub.error is None
        assert s.query(Publication).filter_by(project_id=pid).count() == 1


# ------------------------------------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------------------------------------


def test_routes_status_options_token_and_channel_driver(isolated_settings, meta: FakeMeta):
    channel_id = _channel()
    pid = _rendered(isolated_settings, channel_id=channel_id)
    with TestClient(create_app()) as client:
        status = client.get(f"/api/instagram/status?project_id={pid}").json()
        assert status["ready"] is False and status["driver"] == "manual" and status["accounts"] == []
        assert client.put("/api/instagram/options", json={"public_base_url": "http://sem-https.dev"}).status_code == 400
        assert client.put("/api/instagram/options", json={"public_base_url": "https://cdn.exemplo.dev"}).json()[
            "public_base_url"] == "https://cdn.exemplo.dev"
        resp = client.post("/api/instagram/token", json={"access_token": "token-do-painel", "channel_id": channel_id})
        assert resp.status_code == 200, resp.text
        status = client.get(f"/api/instagram/status?project_id={pid}").json()
        assert status["ready"] is True and status["driver"] == "native" and status["account"]["username"] == "vulto.br"
        assert "access_token" not in str(status)
        assert client.put(f"/api/channels/{channel_id}/instagram", json={"driver": "x"}).status_code == 400
        resp = client.put(f"/api/channels/{channel_id}/instagram", json={"driver": "manual"})
        assert resp.json()["driver"] == "manual" and resp.json()["user_id"] == "178"
        limit = client.get("/api/instagram/accounts/178").json()["limit"]
        assert limit == {"used": 0, "total": 50, "window_s": 86400}
        assert client.post("/api/instagram/link", json={}).status_code == 400  # app não configurado
        providers = {p["id"]: p for p in client.get("/api/providers").json()}
        assert providers["instagram"]["available"] is True
        assert client.delete("/api/instagram/accounts/178").json() == {"deleted": True}
        assert client.get(f"/api/instagram/status?project_id={pid}").json()["ready"] is False


def test_callback_page_finishes_login(meta: FakeMeta):
    _app()
    state = ig.start_link("http://local")["state"]
    with TestClient(create_app()) as client:
        page = client.get(f"/api/instagram/callback?code=CODE1&state={state}")
        assert page.status_code == 200 and "@vulto.br" in page.text
        denied = client.get("/api/instagram/callback?error=access_denied&error_description=negado")
        assert denied.status_code == 400 and "negado" in denied.text
