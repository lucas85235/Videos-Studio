"""Publicação nativa no Instagram (Instagram API com login do Instagram): Reels por URL pública.

Documentação oficial conferida em 15/11/2026 (developers.facebook.com/docs/instagram-platform):
- Login: `https://www.instagram.com/oauth/authorize` com `instagram_business_basic` e
  `instagram_business_content_publish`; o código vale 1 h, uma vez, e pode vir com `#_` no fim (ignorar).
- Token curto: `POST https://api.instagram.com/oauth/access_token`; token longo (60 dias):
  `GET https://graph.instagram.com/access_token?grant_type=ig_exchange_token`; renovação:
  `GET https://graph.instagram.com/refresh_access_token?grant_type=ig_refresh_token` (token com 24 h ou mais e
  ainda válido; sem renovar em 60 dias, expira de vez). O token gerado no painel do app (Instagram > API setup
  with Instagram business login > Generate token) já é longo.
- Reel: `POST /<IG_ID>/media` com `media_type=REELS`, `video_url` pública, `caption` (até 2.200 caracteres,
  30 hashtags), `share_to_feed`, `cover_url` ou `thumb_offset` e `is_ai_generated` (rótulo de IA).
  Depois `GET /<CONTAINER_ID>?fields=status_code` (EXPIRED, ERROR, FINISHED, IN_PROGRESS, PUBLISHED; consultar
  uma vez por minuto, por até 5 minutos) e `POST /<IG_ID>/media_publish` com `creation_id`.
- Limite: `GET /<IG_ID>/content_publishing_limit?fields=quota_usage,config` (hoje 50 posts por 24 h, conferido
  na hora de publicar). Vídeo: MP4/MOV H.264 ou HEVC, 3 s a 15 min, até 300 MB; capa JPEG até 8 MB.

O Instagram baixa o vídeo de uma URL pública (o app roda só neste computador). O repositório ainda não hospeda
arquivos (Cloudflare R2 está no backlog), então a URL é informada na hora de publicar ou montada com o
"endereço público" das opções + o caminho do vídeo dentro de `outputs/`. O endereço público espelha SÓ a pasta
`outputs/` (os vídeos e capas finais), nunca a pasta de dados inteira: ela guarda `studio.db` (o cofre) e, sem
keyring, `.master.key`.

Contas: cada conta profissional conectada tem o próprio token no cofre (`instagram_account:<user_id>`). Um canal
do Vulto pode escolher a conta e o jeito de publicar (`Channel.settings["instagram"]`: user_id, username, driver).
"""

from __future__ import annotations

import logging
import re
import secrets
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlparse

import httpx

from studio.db import session_scope
from studio.models import Channel, ProviderCredential
from studio.providers import credentials
from studio.providers.base import Provider, ProviderInfo

PROVIDER_ID = "instagram"
OPTIONS_KEY = "instagram_options"  # redirect_uri e public_base_url (não são segredo, mas ficam juntos no cofre)
ACCOUNT_PREFIX = "instagram_account:"

API_VERSION = "v27.0"
GRAPH = "https://graph.instagram.com"
API = f"{GRAPH}/{API_VERSION}"
AUTH_URL = "https://www.instagram.com/oauth/authorize"
TOKEN_URL = "https://api.instagram.com/oauth/access_token"
SCOPES = "instagram_business_basic,instagram_business_content_publish"

LONG_LIVED_S = 60 * 24 * 3600
REFRESH_MIN_AGE = timedelta(hours=24)  # a Meta só renova tokens com 24 h ou mais
REFRESH_WHEN_LEFT = timedelta(days=45)  # renova ao usar, quando faltar menos que isso para expirar
PENDING_TTL_S = 30 * 60

POLL_INTERVAL_S = 60  # a documentação pede uma consulta por minuto...
POLL_TIMEOUT_S = 5 * 60  # ...por no máximo 5 minutos

CAPTION_MAX = 2200
HASHTAGS_MAX = 30
MIN_DURATION_S = 3.0
MAX_DURATION_S = 15 * 60.0
MAX_VIDEO_BYTES = 300 * 1024 * 1024

DRIVERS = ("native", "manual")
# Códigos de erro da Graph API que significam limite de chamadas ou de posts.
RATE_LIMIT_CODES = {4, 17, 32, 613}
RATE_LIMIT_SUBCODES = {2207042}


class InstagramError(RuntimeError):
    def __init__(self, message: str, *, code: int | None = None, subcode: int | None = None,
                 api_message: str = "") -> None:
        super().__init__(message)
        self.code = code  # código da Graph API (100 = parâmetro inválido)
        self.subcode = subcode
        self.api_message = api_message  # error.message (técnica), além do texto traduzido para o usuário


class RateLimited(InstagramError):
    """Limite de chamadas ou de posts da conta (tente mais tarde)."""


class Cancelled(InstagramError):
    """O job foi cancelado durante a espera; o envio continua válido e pode ser retomado."""


class ContainerPending(InstagramError):
    """O Instagram ainda está processando o vídeo depois do tempo de espera; dá para retomar com o container."""

    def __init__(self, container_id: str) -> None:
        super().__init__(
            "o Instagram ainda está processando o vídeo. Tente publicar de novo em alguns minutos "
            "(o app retoma de onde parou; o envio vale por 24 h)."
        )
        self.container_id = container_id


class InstagramPublisher(Provider):
    info = ProviderInfo(
        id=PROVIDER_ID,
        kind="publish",  # type: ignore[arg-type]
        name="Instagram (API oficial)",
        commercial_use=True,
        free=True,
        credential_fields=("app_id", "app_secret"),
        notes="Crie um app no painel da Meta com o produto Instagram (login do Instagram) e cole o ID e o segredo do "
        "app do Instagram. A conta precisa ser profissional (Empresa ou Criador de conteúdo). Com o app em modo de "
        "desenvolvimento, os posts só aparecem para quem tem papel no app.",
        cost_note="Grátis: até 50 posts por conta a cada 24 h",
    )

    def availability(self) -> tuple[bool, str]:
        if linked_accounts():
            return True, "ok"
        if app_configured():
            return False, "app configurado; falta conectar a conta do Instagram"
        return False, "configure o app do Instagram ou cole um token gerado no painel da Meta"


# O httpx registra em INFO a URL inteira de cada chamada. A troca e a renovação de token levam o segredo do app e
# o token na query string (é como a Meta pede), então mascaramos esses campos em qualquer log do httpx.
_SECRET_PARAMS = re.compile(r"(?<![\w])((?:client_secret|access_token|refresh_token|fb_exchange_token|code)=)[^&\s\"']+")


class _MaskSecrets(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - log malformado: deixa passar como está
            return True
        masked = _SECRET_PARAMS.sub(r"\1***", text)
        if masked != text:
            record.msg, record.args = masked, ()
        return True


def _install_log_mask() -> None:
    for name in ("httpx", "httpcore"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, _MaskSecrets) for f in logger.filters):
            logger.addFilter(_MaskSecrets())


_install_log_mask()


def pause(seconds: float) -> None:
    """Espera entre consultas do processamento (os testes trocam por uma que não espera)."""
    time.sleep(seconds)


def new_client(timeout: float = 60) -> httpx.Client:
    """Cliente HTTP das chamadas à Meta (os testes trocam por um cliente com transporte simulado)."""
    return httpx.Client(timeout=timeout)


def _now() -> datetime:
    return datetime.now(UTC)


# --------------------------------------------------------------------------------------------
# Opções e app
# --------------------------------------------------------------------------------------------


def app_configured() -> bool:
    return bool(credentials.resolve(PROVIDER_ID, "app_id") and credentials.resolve(PROVIDER_ID, "app_secret"))


def _app() -> tuple[str, str]:
    app_id = credentials.resolve(PROVIDER_ID, "app_id")
    secret = credentials.resolve(PROVIDER_ID, "app_secret")
    if not app_id or not secret:
        raise InstagramError("configure o ID e o segredo do app do Instagram em Configurações")
    return app_id, secret


def options() -> dict[str, str]:
    data = credentials.get_credential(OPTIONS_KEY)
    return {"redirect_uri": data.get("redirect_uri", ""), "public_base_url": data.get("public_base_url", "")}


def _check_url(value: str, what: str, https_only: bool) -> str:
    value = value.strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in (("https",) if https_only else ("https", "http")) or not parsed.netloc:
        raise InstagramError(f"{what} inválido: use um endereço {'https://' if https_only else 'http(s)://'} completo")
    return value


def set_options(redirect_uri: str | None = None, public_base_url: str | None = None) -> dict[str, str]:
    current = options()
    if redirect_uri is not None:
        current["redirect_uri"] = _check_url(redirect_uri, "endereço de retorno", https_only=False)
    if public_base_url is not None:
        current["public_base_url"] = _check_url(public_base_url, "endereço público", https_only=True).rstrip("/")
    credentials.set_credential(OPTIONS_KEY, current)
    return options()


# --------------------------------------------------------------------------------------------
# Erros da Graph API
# --------------------------------------------------------------------------------------------


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_of(resp: httpx.Response) -> tuple[str, int | None, int | None, str]:
    """Mensagem para o usuário, código, subcódigo e a mensagem técnica (error.message)."""
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        return resp.text[:300], None, None, ""
    if not isinstance(err, dict):
        return str(err)[:300], None, None, ""
    technical = str(err.get("message") or "")
    msg = err.get("error_user_msg") or technical or resp.text[:300]
    return str(msg), _int(err.get("code")), _int(err.get("error_subcode")), technical


def _raise_for(resp: httpx.Response, action: str) -> None:
    if resp.status_code < 400:
        return
    msg, code, sub, technical = _error_of(resp)
    extra: dict[str, Any] = {"code": code, "subcode": sub, "api_message": technical}
    if resp.status_code == 429 or code in RATE_LIMIT_CODES or sub in RATE_LIMIT_SUBCODES:
        raise RateLimited(f"limite do Instagram atingido ao {action}; tente de novo mais tarde ({msg})", **extra)
    if code == 190:
        raise InstagramError("o acesso à conta do Instagram expirou ou foi revogado; conecte a conta de novo", **extra)
    raise InstagramError(f"erro do Instagram ao {action} ({resp.status_code}): {msg}", **extra)


def _call(http: httpx.Client, method: str, url: str, action: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = http.request(method, url, **kwargs)
    except httpx.HTTPError as exc:
        raise InstagramError(f"sem resposta do Instagram ao {action}: {exc}") from exc
    _raise_for(resp, action)
    try:
        return resp.json()
    except ValueError as exc:
        raise InstagramError(f"resposta inesperada do Instagram ao {action}") from exc


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------------------------
# Contas (um token por conta profissional)
# --------------------------------------------------------------------------------------------


def account_key(user_id: str) -> str:
    return f"{ACCOUNT_PREFIX}{user_id}"


def get_account(user_id: str) -> dict[str, str]:
    return credentials.get_credential(account_key(user_id)) if user_id else {}


def _public(data: dict[str, str]) -> dict[str, Any]:
    return {
        "user_id": data.get("user_id", ""),
        "username": data.get("username", ""),
        "account_type": data.get("account_type", ""),
        "expires_at": data.get("expires_at") or None,
        "expires_estimated": data.get("expires_estimated") == "1",
    }


def linked_accounts() -> list[dict[str, Any]]:
    with session_scope() as s:
        keys = [k for (k,) in s.query(ProviderCredential.provider).all() if k.startswith(ACCOUNT_PREFIX)]
    out = []
    for key in sorted(keys):
        data = credentials.get_credential(key)
        if data.get("access_token"):
            out.append(_public(data))
    return out


def save_account(token: str, expires_in: int | None, me: dict[str, Any]) -> dict[str, Any]:
    user_id = str(me.get("user_id") or me.get("id") or "")
    if not user_id:
        raise InstagramError("o Instagram não devolveu o id da conta")
    now = _now()
    data = {
        "access_token": token,
        "user_id": user_id,
        "username": str(me.get("username") or ""),
        "account_type": str(me.get("account_type") or ""),
        "refreshed_at": now.isoformat(),
        "expires_at": (now + timedelta(seconds=expires_in or LONG_LIVED_S)).isoformat(),
        "expires_estimated": "" if expires_in else "1",
    }
    credentials.set_credential(account_key(user_id), data)
    return _public(data)


def forget_account(user_id: str) -> None:
    credentials.delete_credential(account_key(user_id))
    with session_scope() as s:
        for row in s.query(Channel).all():
            link = dict((row.settings or {}).get("instagram") or {})
            if link.get("user_id") == user_id:
                link.pop("user_id", None)
                link.pop("username", None)
                row.settings = {**(row.settings or {}), "instagram": link}


def fetch_me(http: httpx.Client, token: str) -> dict[str, Any]:
    me = _call(http, "GET", f"{API}/me", "ler a conta", params={"fields": "user_id,username,account_type"},
               headers=_auth(token))
    if str(me.get("account_type") or "").upper() == "PERSONAL":
        raise InstagramError("a conta é pessoal: mude para Empresa ou Criador de conteúdo no app do Instagram")
    return me


def exchange_long_lived(http: httpx.Client, short_token: str) -> tuple[str, int | None]:
    _, secret = _app()
    data = _call(http, "GET", f"{GRAPH}/access_token", "gerar o token de longa duração",
                 params={"grant_type": "ig_exchange_token", "client_secret": secret, "access_token": short_token})
    return str(data["access_token"]), int(data.get("expires_in") or LONG_LIVED_S)


def connect_with_token(token: str, http: httpx.Client | None = None, channel_id: str | None = None) -> dict[str, Any]:
    """Conecta com um token colado (o do painel da Meta já é longo). Se o app estiver configurado, tenta trocar por
    um token longo; se a troca falhar (o token já é longo), guarda como veio, com validade estimada em 60 dias."""
    token = token.strip()
    if not token:
        raise InstagramError("cole o token de acesso")
    own = http is None
    client = http or new_client()
    try:
        expires_in: int | None = None
        if app_configured():
            try:
                token, expires_in = exchange_long_lived(client, token)
            except InstagramError:
                expires_in = None
        me = fetch_me(client, token)
        info = save_account(token, expires_in, me)
    finally:
        if own:
            client.close()
    if channel_id:
        set_channel_link(channel_id, user_id=info["user_id"], driver="native")
    return info


def _refresh_due(data: dict[str, str], now: datetime) -> bool:
    try:
        refreshed = datetime.fromisoformat(data.get("refreshed_at") or "")
        expires = datetime.fromisoformat(data.get("expires_at") or "")
    except ValueError:
        return True
    if now - refreshed < REFRESH_MIN_AGE:
        return False
    # Token colado com validade estimada: pode ter sido gerado bem antes da colagem. Renova no primeiro uso
    # depois de 24 h, para a data passar a ser a real.
    if data.get("expires_estimated") == "1":
        return True
    return expires - now <= REFRESH_WHEN_LEFT


def access_token(http: httpx.Client, user_id: str) -> str:
    """Token da conta, renovado quando já passou de 24 h e falta menos de 45 dias para expirar."""
    data = get_account(user_id)
    token = data.get("access_token")
    if not token:
        raise InstagramError("a conta do Instagram não está conectada; conecte em Configurações")
    now = _now()
    expires_at = data.get("expires_at") or ""
    try:
        expired = bool(expires_at) and datetime.fromisoformat(expires_at) <= now
    except ValueError:
        expired = False
    if expired:
        raise InstagramError("o token do Instagram expirou (passou de 60 dias sem uso); conecte a conta de novo")
    if _refresh_due(data, now):
        try:
            fresh = _call(http, "GET", f"{GRAPH}/refresh_access_token", "renovar o acesso",
                          params={"grant_type": "ig_refresh_token", "access_token": token})
        except RateLimited:
            raise
        except InstagramError:
            return token  # ainda válido: tenta de novo na próxima vez
        token = str(fresh.get("access_token") or token)
        data.update(access_token=token, refreshed_at=now.isoformat(), expires_estimated="",
                    expires_at=(now + timedelta(seconds=int(fresh.get("expires_in") or LONG_LIVED_S))).isoformat())
        credentials.set_credential(account_key(user_id), data)
    return token


# --------------------------------------------------------------------------------------------
# Login (OAuth do Instagram)
# --------------------------------------------------------------------------------------------

_pending: dict[str, dict[str, Any]] = {}
_results: dict[str, dict[str, Any]] = {}
_lock = threading.Lock()


def _purge(now: float) -> None:
    for store in (_pending, _results):
        for key in [k for k, v in store.items() if now - float(v.get("created") or now) > PENDING_TTL_S]:
            store.pop(key, None)


def start_link(default_redirect: str, channel_id: str | None = None) -> dict[str, str]:
    """URL do login do Instagram. O endereço de retorno é o das opções (precisa estar cadastrado no app) ou o
    local do Vulto. Se a Meta não aceitar o local, cadastre outro e cole o endereço final na tela."""
    app_id, _ = _app()
    redirect = options()["redirect_uri"] or default_redirect
    state = secrets.token_urlsafe(24)
    now = time.time()
    with _lock:
        _purge(now)
        _pending[state] = {"redirect_uri": redirect, "channel_id": channel_id or "", "created": now}
    params = {"client_id": app_id, "redirect_uri": redirect, "response_type": "code", "scope": SCOPES, "state": state}
    return {"auth_url": f"{AUTH_URL}?{urlencode(params)}", "state": state, "redirect_uri": redirect}


def link_status(state: str) -> dict[str, Any]:
    with _lock:
        if state in _pending:
            return {"status": "pending"}
        result = _results.get(state)
    if result is None:
        return {"status": "unknown"}
    return {k: v for k, v in result.items() if k != "created"}


def parse_pasted(text: str) -> tuple[str, str | None]:
    """Código (e state, se houver) a partir do endereço final colado ou do próprio código."""
    text = text.strip()
    if "://" in text or "?" in text or "code=" in text:
        query = parse_qs(urlparse(text).query) or parse_qs(text.split("?", 1)[-1])
        code = (query.get("code") or [""])[0]
        state = (query.get("state") or [None])[0]
    else:
        code, state = text, None
    code = code.split("#")[0].strip()
    if not code:
        raise InstagramError("não achei o código no endereço colado")
    return code, state


def finish_oauth(code: str, state: str | None, http: httpx.Client | None = None, *,
                 allow_single_pending: bool = False) -> dict[str, Any]:
    """Termina o login. Sem `state`, só aceita o único login pendente quando `allow_single_pending` (código colado
    à mão na tela do Vulto); o retorno pelo navegador sempre exige o state (proteção contra CSRF de login)."""
    with _lock:
        if state is None and allow_single_pending and len(_pending) == 1:
            state = next(iter(_pending))
        pending = _pending.pop(state or "", None)
        if pending is not None:
            _results[state or ""] = {"status": "pending", "created": time.time()}
    if pending is None:
        raise InstagramError("pedido de login desconhecido ou expirado; clique em conectar de novo")
    try:
        info = _finish(code.split("#")[0], pending, http)
    except Exception as exc:
        with _lock:
            _results[state or ""] = {"status": "error", "error": str(exc), "created": time.time()}
        raise
    with _lock:
        _results[state or ""] = {"status": "done", **info, "created": time.time()}
    return info


def _finish(code: str, pending: dict[str, Any], http: httpx.Client | None) -> dict[str, Any]:
    app_id, secret = _app()
    own = http is None
    client = http or new_client()
    try:
        data = _call(client, "POST", TOKEN_URL, "trocar o código de login", data={
            "client_id": app_id, "client_secret": secret, "grant_type": "authorization_code",
            "redirect_uri": pending["redirect_uri"], "code": code,
        })
        # A resposta atual vem em "data": [{access_token, user_id, permissions}]; a antiga vinha solta.
        first = (data.get("data") or [data])[0] if isinstance(data.get("data"), list) else data
        short = first.get("access_token")
        if not short:
            raise InstagramError("o Instagram não devolveu o token de acesso")
        perms = first.get("permissions") or ""
        if isinstance(perms, list):
            perms = ",".join(perms)
        if perms and "instagram_business_content_publish" not in perms:
            raise InstagramError("a permissão de publicar não foi concedida; conecte de novo e aceite todas as permissões")
        token, expires_in = exchange_long_lived(client, short)
        me = fetch_me(client, token)
        info = save_account(token, expires_in, me)
    finally:
        if own:
            client.close()
    if pending.get("channel_id"):
        set_channel_link(pending["channel_id"], user_id=info["user_id"], driver="native")
    return info


# --------------------------------------------------------------------------------------------
# Canal do Vulto: conta e jeito de publicar
# --------------------------------------------------------------------------------------------


def channel_link(channel_id: str | None) -> dict[str, Any]:
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return dict((row.settings or {}).get("instagram") or {}) if row else {}


def set_channel_link(channel_id: str, *, user_id: str | None = None, driver: str | None = None,
                     clear_account: bool = False) -> dict[str, Any]:
    if driver is not None and driver not in DRIVERS:
        raise InstagramError("jeito de publicar inválido (use native ou manual)")
    if user_id and not get_account(user_id).get("access_token"):
        raise InstagramError("conta do Instagram não conectada")
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise InstagramError("canal não encontrado")
        link = dict((row.settings or {}).get("instagram") or {})
        if clear_account:
            link.pop("user_id", None)
            link.pop("username", None)
        if user_id:
            link.update(user_id=user_id, username=get_account(user_id).get("username", ""))
        if driver:
            link["driver"] = driver
        row.settings = {**(row.settings or {}), "instagram": link}
        return link


def account_for_channel(channel_id: str | None) -> str:
    """Conta (user_id) usada por um projeto do canal: a escolhida no canal, ou a única conectada."""
    link = channel_link(channel_id)
    if link.get("user_id"):
        if get_account(link["user_id"]).get("access_token"):
            return str(link["user_id"])
        raise InstagramError(f"a conta @{link.get('username') or link['user_id']} do canal perdeu o acesso; conecte de novo")
    accounts = linked_accounts()
    if len(accounts) == 1:
        return accounts[0]["user_id"]
    if not accounts:
        raise InstagramError("conecte uma conta profissional do Instagram em Configurações")
    raise InstagramError("há mais de uma conta do Instagram: escolha a do canal na publicação")


# --------------------------------------------------------------------------------------------
# Publicação de Reels
# --------------------------------------------------------------------------------------------


def public_url_for(rel_path: str) -> str:
    """URL pública de um arquivo de `outputs/`, pelo endereço público das opções (vazio sem ele).

    O endereço público espelha só a pasta `outputs/`: o caminho usado é o relativo a ela. Arquivos fora de
    `outputs/` (como `studio.db` ou `.master.key`) nunca viram URL."""
    base = options()["public_base_url"]
    if not base or not rel_path:
        return ""
    parts = [p for p in rel_path.replace(chr(92), "/").split("/") if p not in ("", ".")]
    if len(parts) < 2 or parts[0] != "outputs" or ".." in parts:
        return ""
    return f"{base}/{quote('/'.join(parts[1:]))}"


def check_video(path: Path | None, duration: float | None) -> None:
    """Limites do Reel que dá para conferir aqui (o resto o Instagram confere ao processar)."""
    if duration is not None and duration and not (MIN_DURATION_S <= float(duration) <= MAX_DURATION_S):
        raise InstagramError(f"o Reel precisa ter de 3 s a 15 min (este tem {float(duration):.0f} s)")
    if path is not None and path.exists() and path.stat().st_size > MAX_VIDEO_BYTES:
        raise InstagramError("o vídeo passa de 300 MB, o limite do Instagram")


def fit_caption(text: str) -> str:
    """Corta a legenda nos limites da API (2.200 caracteres e 30 hashtags)."""
    words = text.split(" ")
    seen = 0
    kept = []
    for word in words:
        if word.startswith("#"):
            seen += 1
            if seen > HASHTAGS_MAX:
                continue
        kept.append(word)
    return " ".join(kept)[:CAPTION_MAX]


def check_public_url(http: httpx.Client, url: str, what: str = "do vídeo",
                     kinds: tuple[str, ...] = ("video/",)) -> list[str]:
    """Confere se a URL responde de fora antes de mandar ao Instagram (erro mais claro que o da Meta).

    Pede só o primeiro byte com GET (`Range: bytes=0-0`), que é como o Instagram baixa: URLs pré-assinadas de
    S3/R2 são assinadas para GET e recusam HEAD. 403/405/501 viram aviso (o Instagram decide); 404 e outros erros
    bloqueiam. Uma página HTML no lugar do arquivo (link de compartilhamento) também vira aviso."""
    try:
        with http.stream("GET", url, headers={"Range": "bytes=0-0"}, follow_redirects=True) as resp:
            status = resp.status_code
            ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
    except httpx.HTTPError as exc:
        raise InstagramError(f"a URL {what} não respondeu ({exc}); ela precisa estar pública na internet") from exc
    if status in (403, 405, 501):
        return [f"a URL {what} respondeu {status} ao teste do app; o Instagram vai tentar baixar mesmo assim"]
    if status >= 400:
        raise InstagramError(f"a URL {what} respondeu {status}; ela precisa estar pública na internet")
    if ctype and not ctype.startswith(kinds) and ctype not in ("application/octet-stream", "binary/octet-stream"):
        return [f"a URL {what} devolveu '{ctype}' em vez do arquivo; use o link direto (não a página de "
                "compartilhamento), senão o Instagram recusa"]
    return []


def publishing_limit(http: httpx.Client, user_id: str, token: str) -> dict[str, int]:
    data = _call(http, "GET", f"{API}/{user_id}/content_publishing_limit", "ler o limite de posts",
                 params={"fields": "quota_usage,config"}, headers=_auth(token))
    item = (data.get("data") or [{}])[0]
    config = item.get("config") or {}
    return {"used": int(item.get("quota_usage") or 0), "total": int(config.get("quota_total") or 50),
            "window_s": int(config.get("quota_duration") or 86400)}


def create_container(http: httpx.Client, user_id: str, token: str, *, video_url: str, caption: str,
                     share_to_feed: bool = True, cover_url: str = "", thumb_offset_ms: int | None = None,
                     ai_generated: bool | None = None) -> tuple[str, list[str]]:
    """Cria o container do Reel. Se a API recusar o rótulo de IA, tenta sem ele e devolve um aviso."""
    params: dict[str, str] = {"media_type": "REELS", "video_url": video_url, "caption": caption,
                              "share_to_feed": "true" if share_to_feed else "false"}
    if cover_url:
        params["cover_url"] = cover_url
    elif thumb_offset_ms is not None:
        params["thumb_offset"] = str(int(thumb_offset_ms))
    if ai_generated:
        params["is_ai_generated"] = "true"
    warnings: list[str] = []
    try:
        data = _call(http, "POST", f"{API}/{user_id}/media", "criar o Reel", data=params, headers=_auth(token))
    except RateLimited:
        raise
    except InstagramError as exc:
        # A mensagem para o usuário vem traduzida e nem sempre cita o campo: qualquer recusa de parâmetro
        # (code 100) com o rótulo ligado tenta de novo sem ele.
        if not ai_generated or not (exc.code == 100 or "is_ai_generated" in f"{exc} {exc.api_message}"):
            raise
        params.pop("is_ai_generated")
        data = _call(http, "POST", f"{API}/{user_id}/media", "criar o Reel", data=params, headers=_auth(token))
        warnings.append("o Instagram recusou o rótulo de IA pela API; marque 'Rótulo de IA' no app do celular")
    container = str(data.get("id") or "")
    if not container:
        raise InstagramError("o Instagram não devolveu o id do envio")
    return container, warnings


def wait_container(http: httpx.Client, container_id: str, token: str, *, interval_s: float = POLL_INTERVAL_S,
                   timeout_s: float = POLL_TIMEOUT_S, sleep: Callable[[float], None] = time.sleep,
                   on_tick: Callable[[float], None] | None = None,
                   cancelled: Callable[[], bool] | None = None) -> str:
    """Espera o processamento do vídeo. Devolve FINISHED ou PUBLISHED; erro, expirado ou tempo esgotado viram exceção."""
    waited = 0.0
    while True:
        data = _call(http, "GET", f"{API}/{container_id}", "conferir o processamento",
                     params={"fields": "status_code,status"}, headers=_auth(token))
        code = str(data.get("status_code") or "").upper()
        if code in ("FINISHED", "PUBLISHED"):
            return code
        if code == "ERROR":
            detail = data.get("status") or "sem detalhe"
            raise InstagramError(f"o Instagram não aceitou o vídeo: {detail}. Confira formato (MP4 H.264), "
                                 "duração (3 s a 15 min) e se a URL baixa o arquivo direto")
        if code == "EXPIRED":
            raise InstagramError("o envio expirou (passou de 24 h sem publicar); publique de novo")
        if waited >= timeout_s:
            raise ContainerPending(container_id)
        if cancelled and cancelled():
            raise Cancelled("cancelado: o envio continua no Instagram e pode ser retomado por 24 h")
        sleep(interval_s)
        waited += interval_s
        if on_tick:
            on_tick(min(1.0, waited / timeout_s))


def publish_container(http: httpx.Client, user_id: str, token: str, container_id: str) -> str:
    data = _call(http, "POST", f"{API}/{user_id}/media_publish", "publicar o Reel",
                 data={"creation_id": container_id}, headers=_auth(token))
    media_id = str(data.get("id") or "")
    if not media_id:
        raise InstagramError("o Instagram não devolveu o id do post")
    return media_id


def permalink(http: httpx.Client, media_id: str, token: str) -> str:
    try:
        data = _call(http, "GET", f"{API}/{media_id}", "ler o link do post", params={"fields": "permalink"},
                     headers=_auth(token))
    except InstagramError:
        return ""
    return str(data.get("permalink") or "")


def account_status(user_id: str, http: httpx.Client | None = None) -> dict[str, Any]:
    """Conta e uso do limite de posts (para a tela)."""
    own = http is None
    client = http or new_client(20)
    try:
        token = access_token(client, user_id)
        return {**_public(get_account(user_id)), "limit": publishing_limit(client, user_id, token)}
    finally:
        if own:
            client.close()
