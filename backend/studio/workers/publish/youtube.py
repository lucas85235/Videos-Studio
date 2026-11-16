"""Publicação nativa no YouTube (Data API v3): OAuth com PKCE, upload resumível, agendamento, thumbnail e legendas.

Pré-requisitos (README §10.1): projeto seu no Google Cloud, cliente OAuth do tipo "App para computador",
tela de consentimento "In production" (em "Testing" o refresh token expira em 7 dias) e canal verificado
por telefone (uploads > 15 min e thumbnails personalizadas).

Contas: cada canal do Vulto vinculado pelo login do Google tem o próprio token (`youtube_account:<id no YouTube>`),
usado ao publicar os projetos dele. A conta global (`ACCOUNT_KEY`, conectada na aba de publicação) continua valendo
para projetos sem canal vinculado.

Também lê dados públicos de um canal a partir do link colado (sem login e sem cota): a página do canal, uma vez,
para achar o id, e o feed RSS oficial para o nome e os vídeos recentes.
"""

from __future__ import annotations

import base64
import hashlib
import html
import json
import logging
import re
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlencode, urlparse

import httpx

from studio.db import session_scope
from studio.models import Channel, ProviderCredential
from studio.providers import credentials
from studio.providers.base import Provider, ProviderInfo
from studio.workers.publish import quota

log = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_API = "https://www.googleapis.com/upload/youtube/v3"
# Métricas (ADR 0002): retenção e duração média vêm do YouTube Analytics. Tokens antigos sem este escopo continuam
# publicando; a coleta segue só com a Data API e a tela pede para vincular de novo.
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
SCOPES = ("https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.force-ssl "
          f"{ANALYTICS_SCOPE}")
READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
# Receita do YouTube (opcional, escolhido ao vincular o canal): sem ele nada quebra, a receita segue à mão.
MONETARY_SCOPE = "https://www.googleapis.com/auth/yt-analytics-monetary.readonly"
LINK_SCOPES = f"{SCOPES} {READONLY_SCOPE}"
CHUNK = 8 * 1024 * 1024
ACCOUNT_KEY = "youtube_account"

# Vínculo de canal: alvo do login ("new" cria um canal do Vulto; senão é o id de um canal existente).
NEW_CHANNEL = "new"
PENDING_TTL_S = 30 * 60
RECENT_VIDEOS = 15

# Leituras públicas (link colado): só URLs montadas aqui, com cabeçalho de navegador genérico (nada do usuário).
SITE = "https://www.youtube.com"
FEED_URL = f"{SITE}/feeds/videos.xml"
PUBLIC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
}
CHANNEL_ID_RE = re.compile(r"UC[0-9A-Za-z_-]{22}")
_HANDLE_RE = re.compile(r"@[\w.\-·]{1,100}")
_YOUTUBE_HOSTS = ("youtube.com", "www.youtube.com", "m.youtube.com")
_FEED_NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/"}


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
        notes="Crie um cliente OAuth 'App para computador' no seu projeto do Google Cloud, cole aqui e vincule o canal "
        "em Canais (ou conecte na aba de publicação). Tela de consentimento em 'In production' (não 'Testing').",
        cost_note="Grátis: 100 uploads/dia por projeto do Google Cloud",
    )

    def availability(self) -> tuple[bool, str]:
        cid = credentials.resolve(self.info.id, "client_id")
        if not cid:
            return False, "configure o client_id e o client_secret do OAuth"
        if not any_connected():
            return False, "cliente configurado; falta vincular o canal (Canais ou aba Vídeo e publicação)"
        return True, "ok"


def new_client(timeout: float | httpx.Timeout = 60) -> httpx.Client:
    """Cliente HTTP das chamadas ao Google (os testes trocam por um cliente com transporte simulado)."""
    return httpx.Client(timeout=timeout)


# --------------------------------------------------------------------------------------------
# OAuth (PKCE): conta global ou um canal do Vulto
# --------------------------------------------------------------------------------------------

_pending: dict[str, dict[str, str]] = {}
_results: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _purge(now: float) -> None:
    """Esquece pedidos de login abandonados (e resultados já lidos há tempo)."""
    for store in (_pending, _results):
        for key in [k for k, v in store.items() if now - float(v.get("created") or now) > PENDING_TTL_S]:
            store.pop(key, None)


def _client_creds() -> tuple[str, str]:
    cid = credentials.resolve("youtube", "client_id")
    secret = credentials.resolve("youtube", "client_secret")
    if not cid or not secret:
        raise YouTubeError("configure o client_id e o client_secret do YouTube em Configurações")
    return cid, secret


def client_configured() -> bool:
    return bool(credentials.resolve("youtube", "client_id") and credentials.resolve("youtube", "client_secret"))


def _start(redirect_uri: str, target: str | None, revenue: bool = False) -> tuple[str, str]:
    cid, _ = _client_creds()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        _purge(now)
        _pending[state] = {"verifier": verifier, "redirect_uri": redirect_uri, "created": str(now), "target": target or "",
                           "revenue": "1" if revenue and target else ""}
    params = {
        "client_id": cid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": (f"{LINK_SCOPES} {MONETARY_SCOPE}" if revenue else LINK_SCOPES) if target else SCOPES,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    return f"{AUTH_URL}?{urlencode(params)}", state


def start_oauth(redirect_uri: str, channel_target: str | None = None) -> str:
    """URL do login do Google. `channel_target`: None (conta global), NEW_CHANNEL (cria um canal do Vulto com os
    dados do canal escolhido) ou o id de um canal do Vulto que passa a ser desse canal do YouTube."""
    return _start(redirect_uri, channel_target)[0]


def start_link(redirect_uri: str, channel_target: str | None, revenue: bool = False) -> dict[str, str]:
    """Login para vincular um canal: devolve a URL e o `state`, que a tela usa para saber quando terminou.
    `revenue` pede também o escopo da receita do YouTube (opcional)."""
    url, state = _start(redirect_uri, channel_target or NEW_CHANNEL, revenue)
    return {"auth_url": url, "state": state}


def link_status(state: str) -> dict[str, Any]:
    """Situação de um login de vínculo: pending, done (com o canal) ou error (com o motivo)."""
    with _lock:
        if state in _pending:
            return {"status": "pending"}
        result = _results.get(state)
    if result is None:
        return {"status": "unknown"}
    return {k: v for k, v in result.items() if k != "created"}


def finish_oauth(code: str, state: str, http: httpx.Client | None = None) -> dict[str, Any]:
    with _lock:
        pending = _pending.pop(state, None)
        if pending is not None and pending.get("target"):
            # A tela segue vendo "pending" enquanto as chamadas ao Google terminam (o estado nunca fica sem nenhum dos
            # dois, o que a tela leria como login expirado); o "done" ou o "error" abaixo sobrescrevem.
            _results[state] = {"status": "pending", "created": time.time()}
    if pending is None:
        raise YouTubeError("pedido de autorização desconhecido ou expirado; tente conectar de novo")
    target = pending.get("target") or None
    try:
        info = _finish(code, pending, http)
    except Exception as exc:
        if target:
            with _lock:
                _results[state] = {"status": "error", "error": str(exc), "created": time.time()}
        raise
    if target:
        with _lock:
            _results[state] = {"status": "done", **info, "created": time.time()}
    return info


def _finish(code: str, pending: dict[str, str], http: httpx.Client | None) -> dict[str, Any]:
    cid, secret = _client_creds()
    own = http is None
    client = http or new_client()
    try:
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
        target = pending.get("target") or None
        if target:
            from studio.workers.publish import youtube_channels

            info = youtube_channels.link_from_oauth(client, tokens["access_token"], refresh, target)
            _note_revenue_scope(info, str(tokens.get("scope") or ""), pending.get("revenue") == "1")
            return info
        channel = _get(client, f"{API}/channels", tokens["access_token"], params={"part": "snippet", "mine": "true"},
                       method="channels.list")
        items = channel.get("items") or []
        title = items[0]["snippet"]["title"] if items else ""
        channel_id = items[0]["id"] if items else ""
        credentials.set_credential(ACCOUNT_KEY, {"refresh_token": refresh, "channel_title": title, "channel_id": channel_id})
        return {"channel_title": title, "channel_id": channel_id}
    finally:
        if own:
            client.close()


def account() -> dict[str, str]:
    data = credentials.get_credential(ACCOUNT_KEY)
    return {"channel_title": data.get("channel_title", ""), "channel_id": data.get("channel_id", ""), "connected": bool(data.get("refresh_token"))}


def disconnect() -> None:
    credentials.delete_credential(ACCOUNT_KEY)


# --------------------------------------------------------------------------------------------
# Um token por canal vinculado
# --------------------------------------------------------------------------------------------


def channel_key(youtube_id: str) -> str:
    """Nome da credencial do token de um canal do YouTube vinculado."""
    return f"{ACCOUNT_KEY}:{youtube_id}"


def has_token(youtube_id: str | None) -> bool:
    return bool(youtube_id) and bool(credentials.get_credential(channel_key(youtube_id or "")).get("refresh_token"))


def save_channel_token(youtube_id: str, refresh_token: str, title: str) -> None:
    credentials.set_credential(channel_key(youtube_id), {"refresh_token": refresh_token, "channel_title": title, "channel_id": youtube_id})


def _note_revenue_scope(info: dict[str, Any], granted: str, asked: bool) -> None:
    """Guarda no token do canal se a receita do YouTube foi autorizada (o Google devolve os escopos aceitos; na tela de
    consentimento a pessoa pode desmarcar um). Vincular de novo sem pedir a receita desliga; pedir e o Google não dar
    fica marcado como negado (Desempenho mostra "sem permissão", não "desligada")."""
    youtube_id = str(info.get("channel_id") or "")
    if not youtube_id:
        return
    ok = MONETARY_SCOPE in granted.split()
    set_revenue_scope(youtube_id, ok, denied=asked and not ok)
    if asked and not ok:
        info.setdefault("warnings", []).append(
            "a receita do YouTube não foi autorizada; ela continua sendo lançada à mão em Dinheiro")


def set_revenue_scope(youtube_id: str, enabled: bool, *, denied: bool = False) -> None:
    data = dict(credentials.get_credential(channel_key(youtube_id)))
    if not data.get("refresh_token"):
        return
    data["revenue_scope"] = "1" if enabled else ("negado" if denied else "")
    credentials.set_credential(channel_key(youtube_id), data)


def has_revenue_scope(account_key: str) -> bool:
    """O token tem o escopo da receita do YouTube (escolhido ao vincular)."""
    return credentials.get_credential(account_key).get("revenue_scope") == "1"


def revenue_scope_denied(account_key: str) -> bool:
    """A receita foi pedida ao vincular, mas o Google não deu o escopo (a pessoa desmarcou no consentimento)."""
    return credentials.get_credential(account_key).get("revenue_scope") == "negado"


def forget_channel_token(youtube_id: str) -> None:
    credentials.delete_credential(channel_key(youtube_id))


def linked_accounts() -> list[dict[str, Any]]:
    """Canais do YouTube com token próprio (vinculados em Canais)."""
    prefix = f"{ACCOUNT_KEY}:"
    with session_scope() as s:
        keys = [k for (k,) in s.query(ProviderCredential.provider).all() if k.startswith(prefix)]
    out: list[dict[str, Any]] = []
    for key in sorted(keys):
        data = credentials.get_credential(key)
        if data.get("refresh_token"):
            out.append({"channel_id": data.get("channel_id") or key[len(prefix):], "channel_title": data.get("channel_title", ""),
                        "revenue": data.get("revenue_scope") == "1"})
    return out


def any_connected() -> bool:
    return bool(account()["connected"]) or bool(linked_accounts())


def _channel_link(channel_id: str | None) -> tuple[str, dict[str, Any]]:
    """Nome e dados do YouTube (`settings["youtube"]`) de um canal do Vulto."""
    if not channel_id:
        return "", {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return "", {}
        return row.name or "", dict((row.settings or {}).get("youtube") or {})


def account_by_youtube_id(youtube_id: str | None) -> str | None:
    """Credencial de um canal do YouTube conectado (token próprio ou a conta global, se for dele)."""
    if not youtube_id:
        return None
    if has_token(youtube_id):
        return channel_key(youtube_id)
    glob = credentials.get_credential(ACCOUNT_KEY)
    if glob.get("refresh_token") and glob.get("channel_id") == youtube_id:
        return ACCOUNT_KEY
    return None


def accounts() -> list[dict[str, Any]]:
    """Todas as contas que podem publicar: a global e os canais vinculados, sem repetir o mesmo canal do YouTube."""
    owners: dict[str, list[str]] = {}
    with session_scope() as s:
        for row in s.query(Channel).order_by(Channel.name).all():
            youtube_id = ((row.settings or {}).get("youtube") or {}).get("channel_id")
            if youtube_id:
                owners.setdefault(youtube_id, []).append(row.name)
    out: dict[str, dict[str, Any]] = {}
    glob = account()
    if glob["connected"]:
        key = glob["channel_id"] or ACCOUNT_KEY
        out[key] = {"channel_id": glob["channel_id"], "channel_title": glob["channel_title"], "global": True}
    for item in linked_accounts():
        entry = out.setdefault(item["channel_id"], {**item, "global": False})
        entry["channel_title"] = entry["channel_title"] or item["channel_title"]
    return [{**v, "vulto_channels": owners.get(v["channel_id"], [])} for v in out.values()]


def account_for_channel(channel_id: str | None, prefer: str | None = None) -> str:
    """Credencial usada para publicar um projeto do canal `channel_id` do Vulto.

    Canal vinculado: o token dele (ou a conta global, se ela for do mesmo canal do YouTube). Nunca cai para outro
    canal do YouTube. Canal sem vínculo, ou projeto sem canal: a conta escolhida no envio (`prefer`, id do canal no
    YouTube) ou a conta global, como antes.
    """
    name, link = _channel_link(channel_id)
    youtube_id = link.get("channel_id")
    glob = credentials.get_credential(ACCOUNT_KEY)
    if youtube_id and prefer and prefer != youtube_id:
        raise YouTubeError(f"o canal “{link.get('title') or name}” publica só no canal do YouTube vinculado a ele")
    if not youtube_id and prefer:
        key = account_by_youtube_id(prefer)
        if key is None:
            raise YouTubeError("a conta do YouTube escolhida não está mais conectada; escolha outra")
        return key
    if youtube_id:
        if has_token(youtube_id):
            return channel_key(youtube_id)
        if glob.get("refresh_token") and glob.get("channel_id") == youtube_id:
            return ACCOUNT_KEY
        label = f"“{link.get('title') or name}”"
        if link.get("source") == "public":
            raise YouTubeError(f"o canal {label} foi ligado só pelo link público, que não publica. "
                               "Vincule com o login do Google em Canais.")
        raise YouTubeError(f"o canal {label} está sem acesso ao YouTube. Vincule de novo em Canais.")
    if glob.get("refresh_token"):
        return ACCOUNT_KEY
    if channel_id:
        raise YouTubeError(f"o canal “{name or channel_id}” não está vinculado ao YouTube. Vincule-o em Canais.")
    raise YouTubeError("conecte o canal do YouTube primeiro (em Canais ou na aba Vídeo e publicação)")


def account_title(account_key: str) -> str:
    return credentials.get_credential(account_key).get("channel_title", "")


def account_channel_id(account_key: str) -> str:
    return credentials.get_credential(account_key).get("channel_id", "")


def access_token(http: httpx.Client, account: str | None = None) -> str:
    cid, secret = _client_creds()
    key = account or ACCOUNT_KEY
    refresh = credentials.get_credential(key).get("refresh_token")
    if not refresh:
        raise YouTubeError("canal do YouTube não conectado" if key == ACCOUNT_KEY
                           else "o canal do YouTube está sem acesso; vincule de novo em Canais")
    try:
        resp = http.post(TOKEN_URL, data={"client_id": cid, "client_secret": secret, "refresh_token": refresh,
                                          "grant_type": "refresh_token"})
    except httpx.HTTPError as exc:
        raise YouTubeError(f"sem resposta do Google ao renovar o acesso: {exc}") from exc
    if resp.status_code != 200:
        raise YouTubeError(
            f"não foi possível renovar o acesso ({resp.status_code}). Se a tela de consentimento estiver em 'Testing', "
            "o token expira em 7 dias: publique-a como 'In production' e conecte de novo."
        )
    return resp.json()["access_token"]


def _track(method: str, resp: httpx.Response | None) -> None:
    """Soma a chamada no medidor de cota e marca o balde como esgotado se o Google disser que a cota acabou.

    O medidor é só uma estimativa: se gravar falhar (ex.: banco ocupado), a chamada ao YouTube, que já aconteceu,
    segue normalmente. Chamada recusada conta 1 unidade, como diz a documentação do Google.
    """
    if resp is None:
        return
    try:
        quota.spend(method, failed=resp.status_code >= 400)
        if resp.status_code in (403, 429) and ("quotaExceeded" in resp.text or "dailyLimitExceeded" in resp.text):
            quota.mark_exhausted(quota.bucket(method))
    except Exception as exc:  # noqa: BLE001 - o contador nunca derruba um envio que já foi feito
        log.warning("medidor de cota do YouTube não gravou %s: %s", method, exc)


def _get(http: httpx.Client, url: str, token: str, params: dict[str, str] | None = None, *, method: str) -> dict[str, Any]:
    try:
        resp = http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise YouTubeError(f"sem resposta do YouTube: {exc}") from exc
    _track(method, resp)
    if resp.status_code != 200:
        raise YouTubeError(f"erro do YouTube: {_explain(resp)}")
    return resp.json()


def _post(http: httpx.Client, url: str, token: str, params: dict[str, str], body: dict[str, Any], *, method: str) -> dict[str, Any]:
    try:
        resp = http.post(url, params=params, headers={"Authorization": f"Bearer {token}",
                                                      "Content-Type": "application/json; charset=UTF-8"},
                         content=json.dumps(body).encode("utf-8"))
    except httpx.HTTPError as exc:
        raise YouTubeError(f"sem resposta do YouTube: {exc}") from exc
    _track(method, resp)
    if resp.status_code not in (200, 201):
        raise YouTubeError(_explain(resp))
    return resp.json()


# --------------------------------------------------------------------------------------------
# Dados do canal pela API (com login)
# --------------------------------------------------------------------------------------------


def fetch_channel(http: httpx.Client, token: str, youtube_id: str | None = None) -> dict[str, Any]:
    """channels.list (1 unidade de cota): o canal da conta escolhida no login, ou o canal `youtube_id`."""
    params = {"part": "snippet,brandingSettings,statistics,contentDetails"}
    params.update({"id": youtube_id} if youtube_id else {"mine": "true"})
    items = _get(http, f"{API}/channels", token, params=params, method="channels.list").get("items") or []
    if not items:
        if youtube_id:
            raise YouTubeError("canal não encontrado no YouTube")
        raise YouTubeError("esta conta do Google não tem canal do YouTube; crie o canal ou escolha outra conta no login")
    return items[0]


def fetch_recent_uploads(http: httpx.Client, token: str, playlist_id: str, limit: int = RECENT_VIDEOS) -> list[dict[str, str]]:
    """Últimos vídeos pela playlist de uploads (playlistItems.list: 1 unidade de cota; search.list custaria 100)."""
    if not playlist_id:
        return []
    data = _get(http, f"{API}/playlistItems", token,
                params={"part": "snippet", "playlistId": playlist_id, "maxResults": str(limit)}, method="playlistItems.list")
    out: list[dict[str, str]] = []
    for item in data.get("items") or []:
        snippet = item.get("snippet") or {}
        title = (snippet.get("title") or "").strip()
        if not title or title in ("Private video", "Deleted video"):
            continue
        out.append({"title": title, "video_id": (snippet.get("resourceId") or {}).get("videoId") or "",
                    "published": snippet.get("publishedAt") or ""})
    return out[:limit]


# --------------------------------------------------------------------------------------------
# Dados públicos do canal (link colado, sem login)
# --------------------------------------------------------------------------------------------


def _clean_handle(raw: str) -> str:
    handle = raw.strip().rstrip("/")
    if not _HANDLE_RE.fullmatch(handle):
        raise YouTubeError("o @ do canal parece inválido; cole o link completo do canal")
    return handle


def parse_channel_link(text: str) -> tuple[str, str]:
    """Do que foi colado: ("id", "UC…"), ("handle", "@nome") ou ("path", "/c/nome")."""
    raw = (text or "").strip()
    if not raw:
        raise YouTubeError("cole o link do canal")
    if CHANNEL_ID_RE.fullmatch(raw):
        return "id", raw
    if raw.startswith("@"):
        return "handle", _clean_handle(raw.split("/")[0])
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").lower()
    path = unquote(parsed.path or "")
    if host == "youtu.be" or (host in _YOUTUBE_HOSTS and path.startswith(("/watch", "/shorts/", "/live/", "/playlist"))):
        raise YouTubeError("esse é o link de um vídeo; cole o link do canal (ex.: youtube.com/@seucanal)")
    if host not in _YOUTUBE_HOSTS:
        raise YouTubeError("cole um link do YouTube (ex.: youtube.com/@seucanal ou youtube.com/channel/UC…)")
    parts = [p for p in path.split("/") if p]
    if parts and parts[0] == "channel" and len(parts) > 1 and CHANNEL_ID_RE.fullmatch(parts[1]):
        return "id", parts[1]
    if parts and parts[0].startswith("@"):
        return "handle", _clean_handle(parts[0])
    if len(parts) > 1 and parts[0] in ("c", "user"):
        return "path", f"/{parts[0]}/{quote(parts[1])}"
    raise YouTubeError("não reconheci o link do canal; use youtube.com/@seucanal ou youtube.com/channel/UC…")


_PAGE_ID_PATTERNS = (
    re.compile(r'<link\s+rel="canonical"\s+href="https?://(?:www\.|m\.)?youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'<meta\s+property="og:url"\s+content="https?://(?:www\.|m\.)?youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'"externalId"\s*:\s*"(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'<meta\s+itemprop="(?:identifier|channelId)"\s+content="(UC[0-9A-Za-z_-]{22})"'),
)


def _page_meta(page: str, prop: str) -> str:
    match = re.search(rf'<meta\s+property="{re.escape(prop)}"\s+content="([^"]*)"', page)
    return html.unescape(match.group(1)).strip() if match else ""


def page_path(kind: str, value: str) -> str:
    return f"/{quote(value, safe='@')}" if kind == "handle" else value


def read_channel_page(http: httpx.Client, path: str) -> dict[str, str]:
    """Lê a página pública do canal uma vez para achar o id (e, de quebra, nome, descrição e foto das metatags)."""
    try:
        resp = http.get(f"{SITE}{path}", headers=PUBLIC_HEADERS, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"não consegui abrir a página do canal: {exc}") from exc
    if resp.status_code == 404:
        raise YouTubeError("canal não encontrado no YouTube; confira o link")
    if resp.status_code != 200:
        raise YouTubeError(f"o YouTube não abriu a página do canal ({resp.status_code}); tente de novo mais tarde")
    page = resp.text
    channel_id = next((m.group(1) for p in _PAGE_ID_PATTERNS if (m := p.search(page))), "")
    if not channel_id:
        raise YouTubeError("não achei o id do canal na página; cole o link no formato youtube.com/channel/UC…")
    return {"channel_id": channel_id, "title": _page_meta(page, "og:title"),
            "description": _page_meta(page, "og:description"), "avatar_url": _page_meta(page, "og:image")}


def read_feed(http: httpx.Client, youtube_id: str) -> dict[str, Any]:
    """Feed RSS oficial do canal (sem login, sem cota): nome e os ~15 vídeos mais recentes."""
    try:
        resp = http.get(FEED_URL, params={"channel_id": youtube_id}, headers=PUBLIC_HEADERS)
    except httpx.HTTPError as exc:
        raise YouTubeError(f"não consegui ler o feed do canal: {exc}") from exc
    if resp.status_code == 404:
        raise YouTubeError("canal não encontrado no YouTube (ou sem vídeos públicos)")
    if resp.status_code != 200:
        raise YouTubeError(f"o feed do canal não abriu ({resp.status_code}); tente de novo mais tarde")
    try:
        root = ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise YouTubeError("o feed do canal veio num formato inesperado") from exc
    title = (root.findtext("a:title", default="", namespaces=_FEED_NS)
             or root.findtext("a:author/a:name", default="", namespaces=_FEED_NS)).strip()
    videos: list[dict[str, str]] = []
    for entry in root.findall("a:entry", _FEED_NS):
        name = (entry.findtext("a:title", default="", namespaces=_FEED_NS)
                or entry.findtext("media:group/media:title", default="", namespaces=_FEED_NS)).strip()
        if not name:
            continue
        videos.append({"title": name, "video_id": entry.findtext("yt:videoId", default="", namespaces=_FEED_NS).strip(),
                       "published": entry.findtext("a:published", default="", namespaces=_FEED_NS).strip()})
    return {"channel_id": youtube_id, "title": title, "videos": videos[:RECENT_VIDEOS]}


# --------------------------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------------------------


def _explain(resp: httpx.Response) -> str:
    try:
        err = resp.json().get("error", {})
        reasons = ", ".join(e.get("reason", "") for e in err.get("errors", []))
        message = err.get("message", resp.text[:300])
    except (ValueError, AttributeError):
        reasons, message = "", resp.text[:300]
    hint = ""
    if "quotaExceeded" in reasons or "uploadLimitExceeded" in reasons:
        hint = " (cota do YouTube do dia esgotada; ela volta à meia-noite do horário do Pacífico)"
    elif "playlistNotFound" in reasons:
        hint = " (a playlist não existe mais ou é de outro canal; escolha outra)"
    elif "playlistContainsMaximumNumberOfVideos" in reasons:
        hint = " (a playlist chegou ao máximo de vídeos)"
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
    account: str | None = None,
    paid_placement: bool = False,
) -> dict[str, Any]:
    client = http or httpx.Client(timeout=httpx.Timeout(120, read=600, write=600))
    token = access_token(client, account)
    parts = ["snippet", "status"] + (["localizations"] if localizations else [])
    body: dict[str, Any] = {"snippet": snippet, "status": status}
    if localizations:
        body["localizations"] = localizations
    if paid_placement:  # selo "Inclui promoção paga" (links de afiliado na descrição)
        parts.append("paidProductPlacementDetails")
        body["paidProductPlacementDetails"] = {"hasPaidProductPlacement": True}
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
    _track("videos.insert", init)
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


def set_thumbnail(video_id: str, image: Path, http: httpx.Client, account: str | None = None) -> None:
    token = access_token(http, account)
    resp = http.post(
        f"{UPLOAD_API}/thumbnails/set",
        params={"videoId": video_id, "uploadType": "media"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": "image/jpeg"},
        content=image.read_bytes(),
    )
    _track("thumbnails.set", resp)
    if resp.status_code != 200:
        raise YouTubeError(f"thumbnail não enviada: {_explain(resp)}")


def upload_captions(video_id: str, srt: Path, language: str, http: httpx.Client, account: str | None = None) -> None:
    token = access_token(http, account)
    boundary = "vstudio" + secrets.token_hex(8)
    meta = json.dumps({"snippet": {"videoId": video_id, "language": language, "name": language, "isDraft": False}})
    body = (
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{meta}\r\n"
        f"--{boundary}\r\nContent-Type: application/x-subrip\r\n\r\n"
    ).encode() + srt.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    resp = http.post(
        f"{UPLOAD_API}/captions",
        params={"uploadType": "multipart", "part": "snippet"},
        headers={"Authorization": f"Bearer {token}", "Content-Type": f"multipart/related; boundary={boundary}"},
        content=body,
    )
    _track("captions.insert", resp)
    if resp.status_code not in (200, 201):
        raise YouTubeError(f"legendas não enviadas: {_explain(resp)}")


# --------------------------------------------------------------------------------------------
# Playlists
# --------------------------------------------------------------------------------------------

PLAYLIST_PRIVACY = ("public", "unlisted", "private")
PLAYLIST_PAGES = 4  # até 200 playlists (50 por página, 1 unidade de cota por página)
PLAYLIST_TITLE_MAX = 150


def _playlist(item: dict[str, Any]) -> dict[str, Any]:
    snippet = item.get("snippet") or {}
    return {
        "id": item.get("id") or "",
        "title": (snippet.get("title") or "").strip(),
        "privacy": (item.get("status") or {}).get("privacyStatus") or "",
        "count": _count((item.get("contentDetails") or {}).get("itemCount")),
    }


def _count(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def list_playlists_page(http: httpx.Client, token: str) -> tuple[list[dict[str, Any]], bool]:
    """Playlists do canal da conta (playlists.list com mine=true; 1 unidade por página de 50).

    Lê no máximo PLAYLIST_PAGES páginas; o segundo valor diz se ficaram playlists de fora (a lista foi cortada).
    """
    out: list[dict[str, Any]] = []
    page: str | None = None
    for _ in range(PLAYLIST_PAGES):
        params = {"part": "snippet,status,contentDetails", "mine": "true", "maxResults": "50"}
        if page:
            params["pageToken"] = page
        data = _get(http, f"{API}/playlists", token, params=params, method="playlists.list")
        out.extend(p for p in map(_playlist, data.get("items") or []) if p["id"])
        page = data.get("nextPageToken")
        if not page:
            break
    return sorted(out, key=lambda p: p["title"].lower()), bool(page)


def list_playlists(http: httpx.Client, token: str) -> list[dict[str, Any]]:
    return list_playlists_page(http, token)[0]


def create_playlist(http: httpx.Client, token: str, title: str, privacy: str = "public", description: str = "") -> dict[str, Any]:
    """Cria uma playlist no canal da conta (playlists.insert: 50 unidades)."""
    title = " ".join((title or "").split())[:PLAYLIST_TITLE_MAX]
    if not title:
        raise YouTubeError("dê um nome à playlist")
    if privacy not in PLAYLIST_PRIVACY:
        raise YouTubeError("visibilidade da playlist inválida (use public, unlisted ou private)")
    body = {"snippet": {"title": title, "description": (description or "")[:5000]}, "status": {"privacyStatus": privacy}}
    try:
        data = _post(http, f"{API}/playlists", token, {"part": "snippet,status,contentDetails"}, body, method="playlists.insert")
    except YouTubeError as exc:
        raise YouTubeError(f"playlist não criada: {exc}") from exc
    return _playlist(data)


def add_to_playlist(http: httpx.Client, token: str, playlist_id: str, video_id: str) -> str:
    """Põe o vídeo no fim da playlist (playlistItems.insert: 50 unidades). Devolve o id do item."""
    body = {"snippet": {"playlistId": playlist_id, "resourceId": {"kind": "youtube#video", "videoId": video_id}}}
    try:
        data = _post(http, f"{API}/playlistItems", token, {"part": "snippet"}, body, method="playlistItems.insert")
    except YouTubeError as exc:
        raise YouTubeError(f"vídeo não entrou na playlist: {exc}") from exc
    return data.get("id") or ""


def playlists_for(account_key: str, http: httpx.Client | None = None) -> tuple[list[dict[str, Any]], bool]:
    """(playlists, cortada): cortada quando o canal tem mais playlists do que o app lê."""
    own = http is None
    client = http or new_client(timeout=30)
    try:
        return list_playlists_page(client, access_token(client, account_key))
    finally:
        if own:
            client.close()


def new_playlist(account_key: str, title: str, privacy: str = "public", http: httpx.Client | None = None) -> dict[str, Any]:
    own = http is None
    client = http or new_client(timeout=30)
    try:
        return create_playlist(client, access_token(client, account_key), title, privacy)
    finally:
        if own:
            client.close()
