"""Publicação nativa no YouTube (Data API v3): OAuth com PKCE, upload resumível, agendamento, thumbnail e legendas.

Pré-requisitos (README §10.1): projeto seu no Google Cloud, cliente OAuth do tipo "App para computador",
tela de consentimento "In production" (em "Testing" o refresh token expira em 7 dias) e canal verificado
por telefone (uploads > 15 min e thumbnails personalizadas).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from studio.providers import credentials
from studio.providers.base import Provider, ProviderInfo

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl"
CHUNK = 8 * 1024 * 1024
ACCOUNT_KEY = "youtube_account"


class YouTubeError(RuntimeError):
    pass


class YouTubePublisher(Provider):
    info = ProviderInfo(
        id="youtube",
        kind="publish",  # type: ignore[arg-type]
        name="YouTube (API oficial)",
        commercial_use=True,
        free=True,
        credential_fields=("client_id", "client_secret"),
        notes="Crie um cliente OAuth 'App para computador' no seu projeto do Google Cloud, cole aqui e conecte o canal "
        "na aba de publicação. Tela de consentimento em 'In production' (não 'Testing').",
        cost_note="Grátis: 100 uploads/dia por projeto do Google Cloud",
    )

    def availability(self) -> tuple[bool, str]:
        cid = credentials.resolve(self.info.id, "client_id")
        if not cid:
            return False, "configure o client_id e o client_secret do OAuth"
        if not credentials.get_credential(ACCOUNT_KEY).get("refresh_token"):
            return False, "cliente configurado; falta conectar o canal (aba Vídeo e publicação)"
        return True, "ok"


_pending: dict[str, dict[str, str]] = {}
_lock = threading.Lock()


def _client_creds() -> tuple[str, str]:
    cid = credentials.resolve("youtube", "client_id")
    secret = credentials.resolve("youtube", "client_secret")
    if not cid or not secret:
        raise YouTubeError("configure o client_id e o client_secret do YouTube em Configurações")
    return cid, secret


def start_oauth(redirect_uri: str) -> str:
    cid, _ = _client_creds()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    with _lock:
        _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "created": str(time.time())}
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def finish_oauth(code: str, state: str, http: httpx.Client | None = None) -> dict[str, Any]:
    with _lock:
        pending = _pending.pop(state, None)
    if pending is None:
        raise YouTubeError("pedido de autorização desconhecido ou expirado; tente conectar de novo")
    cid, secret = _client_creds()
    client = http or httpx.Client(timeout=60)
    resp = client.post(
        TOKEN_URL,
        data={
            "client_id": cid,
            "client_secret": secret,
            "code": code,
            "code_verifier": pending["verifier"],
            "grant_type": "authorization_code",
            "redirect_uri": pending["redirect_uri"],
        },
    )
    if resp.status_code != 200:
        raise YouTubeError(f"troca do código falhou ({resp.status_code}): {resp.text[:300]}")
    tokens = resp.json()
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise YouTubeError("o Google não devolveu refresh_token; remova o acesso do app na sua conta Google e conecte de novo")
    channel = _get(client, f"{API}/channels", tokens["access_token"], params={"part": "snippet", "mine": "true"})
    items = channel.get("items") or []
    title = items[0]["snippet"]["title"] if items else ""
    channel_id = items[0]["id"] if items else ""
    credentials.set_credential(ACCOUNT_KEY, {"refresh_token": refresh, "channel_title": title, "channel_id": channel_id})
    return {"channel_title": title, "channel_id": channel_id}


def account() -> dict[str, str]:
    data = credentials.get_credential(ACCOUNT_KEY)
    return {"channel_title": data.get("channel_title", ""), "channel_id": data.get("channel_id", ""), "connected": bool(data.get("refresh_token"))}


def disconnect() -> None:
    credentials.delete_credential(ACCOUNT_KEY)


def access_token(http: httpx.Client) -> str:
    cid, secret = _client_creds()
    refresh = credentials.get_credential(ACCOUNT_KEY).get("refresh_token")
    if not refresh:
        raise YouTubeError("canal do YouTube não conectado")
    resp = http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "refresh_token": refresh, "grant_type": "refresh_token"})
    if resp.status_code != 200:
        raise YouTubeError(
            f"não foi possível renovar o acesso ({resp.status_code}). Se a tela de consentimento estiver em 'Testing', "
            "o token expira em 7 dias: publique-a como 'In production' e conecte de novo."
        )
    return resp.json()["access_token"]


def _get(http: httpx.Client, url: str, token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
    resp = http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        raise YouTubeError(f"erro do YouTube ({resp.status_code}): {resp.text[:400]}")
    return resp.json()


def _explain(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        reasons = ", ".join(e.get("reason", "") for e in err.get("errors", []))
        message = err.get("message", resp.text[:300])
    except (ValueError, AttributeError):
        reasons, message = "", resp.text[:300]
    hint = ""
    if "quotaExceeded" in reasons or "uploadLimitExceeded" in reasons:
        hint = " (cota de uploads do dia esgotada; tente amanhã)"
    elif "forbidden" in reasons and "thumbnail" in message.lower():
        hint = " (thumbnail personalizada exige canal verificado por telefone)"
    return f"{resp.status_code} {message}{hint}"


def upload_video(
    video: Path,
    *,
    snippet: dict[str, Any],
    status: dict[str, Any],
    localizations: dict[str, dict[str, str]] | None = None,
    http: httpx.Client | None = None,
    on_progress=None,
) -> dict[str, Any]:
    client = http or httpx.Client(timeout=httpx.Timeout(120, read=600, write=600))
    token = access_token(client)
    parts = ["snippet", "status"] + (["localizations"] if localizations else [])
    body: dict[str, Any] = {"snippet": snippet, "status": status}
    if localizations:
        body["localizations"] = localizations
    size = video.stat().st_size
    init = client.post(
        f"{UPLOAD_API}/videos",
        params={"uploadType": "resumable", "part": ",".join(parts)},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        content=json.dumps(body).encode("utf-8"),
    )
    if init.status_code != 200 or "location" not in {k.lower() for k in init.headers}:
        raise YouTubeError(f"não foi possível iniciar o upload: {_explain(init)}")
    session_url = init.headers["location"]
    sent = 0
    result: dict[str, Any] | None = None
    with open(video, "rb") as fh:
        while sent < size:
            chunk = fh.read(CHUNK)
            end = sent + len(chunk) - 1
            for attempt in range(5):
                resp = client.put(
                    session_url,
                    headers={"Authorization": f"Bearer {token}", "Content-Length": str(len(chunk)), "Content-Range": f"bytes {sent}-{end}/{size}"},
                    content=chunk,
                )
                if resp.status_code in (200, 201):
                    result = resp.json()
                    break
                if resp.status_code == 308:
                    break
                if resp.status_code >= 500 and attempt < 4:
                    time.sleep(2**attempt)
                    continue
                raise YouTubeError(f"falha no envio do vídeo: {_explain(resp)}")
            sent = end + 1
            if on_progress:
                on_progress(min(1.0, sent / size))
    if result is None:
        raise YouTubeError("o upload terminou sem resposta final do YouTube")
    return result


def set_thumbnail(video_id: str, image: Path, http: httpx.Client) -> None:
    token = access_token(http)
    resp = http.post(
        f"{UPLOAD_API}/thumbnails/set",
        params={"videoId": video_id, "uploadType": "media"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
        content=image.read_bytes(),
    )
    if resp.status_code != 200:
        raise YouTubeError(f"thumbnail não enviada: {_explain(resp)}")


def upload_captions(video_id: str, srt: Path, language: str, http: httpx.Client) -> None:
    token = access_token(http)
    boundary = "vstudio" + secrets.token_hex(8)
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": language, "isDraft": False}})
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
        f"--{boundary}\r\nContent-Type: application/x-subrip\r\n\r\n"
    ).encode("utf-8") + srt.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    resp = http.post(
        f"{UPLOAD_API}/captions",
        params={"uploadType": "multipart", "part": "snippet"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"},
        content=body,
    )
    if resp.status_code not in (200, 201):
        raise YouTubeError(f"legendas não enviadas: {_explain(resp)}")
