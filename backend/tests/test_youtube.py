from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from studio.core.styles.library import get_style
from studio.providers import credentials
from studio.workers.publish import youtube as yt
from studio.workers.publish_tasks import youtube_status_body


def _configure() -> None:
    credentials.set_credential("youtube", {"client_id": "cid.apps.googleusercontent.com", "client_secret": "segredo"})
    credentials.set_credential(yt.ACCOUNT_KEY, {"refresh_token": "rt", "channel_title": "Canal", "channel_id": "UC1"})


def test_oauth_url_uses_pkce_and_offline_access():
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    url = yt.start_oauth("http://127.0.0.1:8765/api/youtube/callback")
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"]
    assert query["code_challenge_method"] == ["S256"]
    assert "youtube.upload" in query["scope"][0]


def test_resumable_upload_thumbnail_and_captions(tmp_path: Path):
    _configure()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * (yt.CHUNK + 1000))
    thumb = tmp_path / "t.jpg"
    thumb.write_bytes(b"jpg")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nOi\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []
    received = {"bytes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if request.method == "POST" and "/upload/youtube/v3/videos" in url:
            body = json.loads(request.content)
            assert body["status"]["containsSyntheticMedia"] is False
            assert body["snippet"]["defaultAudioLanguage"] == "pt-BR"
            return httpx.Response(200, headers={"Location": "https://upload.example/session/1"})
        if request.method == "PUT" and url == "https://upload.example/session/1":
            received["bytes"] += len(request.content)
            if received["bytes"] < video.stat().st_size:
                return httpx.Response(308)
            return httpx.Response(200, json={"id": "VID123"})
        if "/thumbnails/set" in url:
            assert request.headers["Content-Type"] == "image/jpeg"
            return httpx.Response(200, json={})
        if "/upload/youtube/v3/captions" in url:
            assert "multipart/related" in request.headers["Content-Type"]
            assert b"application/x-subrip" in request.content
            return httpx.Response(200, json={"id": "CAP1"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    style = get_style("misterios-short")
    status = youtube_status_body(style, "private", None)
    result = yt.upload_video(video, snippet={"title": "T", "defaultAudioLanguage": "pt-BR"}, status=status, http=client)
    assert result["id"] == "VID123"
    assert received["bytes"] == video.stat().st_size
    yt.set_thumbnail("VID123", thumb, client)
    yt.upload_captions("VID123", srt, "pt-BR", client)
    puts = [c for c in calls if c[0] == "PUT"]
    assert len(puts) == 2


def test_localizations_use_other_languages(isolated_settings):
    from studio.core.project import service
    from studio.core.project.document import Metadata
    from studio.workers.publish_tasks import youtube_localizations

    doc = service.create_project(title="T", style_id="misterios-short", topic="farol", languages=["pt-BR", "en-US", "es-ES"])
    doc.metadata = {
        "pt-BR": Metadata(title="O farol"),
        "en-US": Metadata(title="The lighthouse", description="Desc", hashtags=["#mystery"]),
        "es-ES": Metadata(title="   "),
    }
    assert youtube_localizations(doc, "pt-BR") == {"en-US": {"title": "The lighthouse", "description": "Desc\n\n#mystery"}}
    assert set(youtube_localizations(doc, "en-US")) == {"pt-BR"}


def test_upload_sends_localizations(tmp_path: Path):
    _configure()
    video = tmp_path / "v.mp4"
    video.write_bytes(b"\x00" * 1000)
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "at"})
        if request.method == "POST" and "/upload/youtube/v3/videos" in url:
            seen["part"] = request.url.params["part"]
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, headers={"Location": "https://upload.example/session/2"})
        if request.method == "PUT":
            return httpx.Response(200, json={"id": "VID9"})
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    yt.upload_video(video, snippet={"title": "T", "defaultLanguage": "pt-BR"}, status={"privacyStatus": "private"},
                    localizations={"en-US": {"title": "The T", "description": ""}}, http=client)
    assert "localizations" in seen["part"].split(",")
    assert seen["body"]["localizations"]["en-US"]["title"] == "The T"
    assert seen["body"]["snippet"]["defaultLanguage"] == "pt-BR"


def test_schedule_requires_future_and_sets_private():
    style = get_style("misterios-short")
    with pytest.raises(ValueError):
        youtube_status_body(style, "scheduled", "2020-01-01T10:00:00Z")
    status = youtube_status_body(style, "scheduled", "2099-01-01T10:00:00-03:00")
    assert status["privacyStatus"] == "private"
    assert status["publishAt"] == "2099-01-01T13:00:00Z"
    photoreal = style.model_copy(update={"art": style.art.model_copy(update={"photoreal": True})})
    assert youtube_status_body(photoreal, "public", None)["containsSyntheticMedia"] is True
