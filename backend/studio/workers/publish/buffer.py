"""Publicação no TikTok e no Instagram (Reels) pela API pública do Buffer (GraphQL), conferida em 15/11/2026 em
developers.buffer.com:

- Endpoint único `POST https://api.buffer.com`, chave de API em `Authorization: Bearer <chave>` (Settings → API na
  conta do Buffer; o plano grátis tem 1 chave e 150 pedidos a cada 15 min, 600 por dia e 1.000 por canal a cada 30
  dias, somando o MCP).
- `account { organizations { id name } }` e `channels(input: {organizationId})` listam os perfis conectados.
- `createPost(input: CreatePostInput!)` com `channelId`, `text`, `schedulingType: automatic`, `mode` (`addToQueue`,
  `shareNow`, `shareNext`, `customScheduled` + `dueAt` em ISO 8601 UTC) e `assets: [{video: {url}}]`. Metadados por
  rede: `metadata.instagram {type: reel, shouldShareToFeed, isAiGenerated}` e `metadata.tiktok {isAiGenerated}`.
  A resposta é a união `PostActionPayload` (`PostActionSuccess { post }` ou um erro com `message`).
- `post(input: {id})` traz `status` (`draft`, `needs_approval`, `scheduled`, `sending`, `sent`, `error`),
  `externalLink` e `error { message }`; `deletePost(input: {id})` apaga um post que ainda não saiu.
- Erros de sistema vêm com HTTP 200 e a lista `errors` (`extensions.code`: UNAUTHORIZED, FORBIDDEN, NOT_FOUND,
  UNEXPECTED, RATE_LIMIT_EXCEEDED); acima do limite, HTTP 429 com `Retry-After`.
- Cada resposta traz os cabeçalhos `RateLimit` (`r` = pedidos que sobram, `t` = segundos até zerar), um por janela
  (15 min, 24 h e 30 dias; guia "API Limits"). Guardamos o menor saldo para a conferência de fundo parar antes de
  gastar o que o `createPost` vai precisar.
- O Buffer baixa o vídeo **na hora de publicar**: a URL (aqui, do Cloudflare R2) precisa ficar no ar até lá.

Ainda não testado com uma conta real (README §10.2: confirmar se o TikTok está liberado no plano grátis e se o Reel
sai sozinho). Os testes simulam a API.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from studio.providers import credentials
from studio.providers.base import Provider, ProviderInfo

log = logging.getLogger(__name__)

API_URL = "https://api.buffer.com"
PROVIDER_ID = "buffer"
ENV_VARS = ("BUFFER_API_KEY",)
PLATFORMS = ("tiktok", "instagram")  # serviços do Buffer que este driver publica
MODES = {"queue": "addToQueue", "now": "shareNow", "next": "shareNext", "scheduled": "customScheduled"}
# Status do post no Buffer que encerram o acompanhamento
FINAL_STATUSES = ("sent", "error")

_ORGS_QUERY = "query Organizations { account { organizations { id name } } }"
_CHANNELS_QUERY = """
query Channels($input: ChannelsInput!) {
  channels(input: $input) { id name displayName service avatar isDisconnected isLocked isQueuePaused timezone }
}"""
_CREATE_MUTATION = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id status dueAt externalLink error { message } } }
    ... on MutationError { message }
  }
}"""
_POST_QUERY = """
query Post($input: PostInput!) {
  post(input: $input) { id status dueAt sentAt externalLink error { message } }
}"""
_DELETE_MUTATION = """
mutation DeletePost($input: DeletePostInput!) {
  deletePost(input: $input) {
    ... on DeletePostSuccess { id }
    ... on MutationError { message }
  }
}"""


# Erros em que não dá para saber se o Buffer chegou a fazer o pedido (o post pode ter sido criado).
UNCERTAIN_CODES = ("NETWORK", "SERVER", "UNEXPECTED")


class BufferError(RuntimeError):
    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code

    @property
    def uncertain(self) -> bool:
        """Verdadeiro quando o pedido pode ter dado certo do lado do Buffer (queda de rede, erro 5xx, erro interno)."""
        return self.code in UNCERTAIN_CODES


# --------------------------------------------------------------------------------------------
# Saldo de pedidos (cabeçalhos RateLimit)
# --------------------------------------------------------------------------------------------

LOW_QUOTA = 25  # abaixo disso a conferência de fundo espera (sobra para criar posts e listar perfis)
_quota_lock = threading.Lock()
_quota: dict[str, Any] = {"remaining": None, "reset_at": None}
_ITEM = re.compile(r"(?:^|;)\s*(r|t)\s*=\s*(\d+)")


def _parse_ratelimit(values: list[str]) -> list[tuple[int, int]]:
    """(restante, segundos até zerar) de cada janela do cabeçalho `RateLimit` (`"15m";r=149;t=900, ...`)."""
    out: list[tuple[int, int]] = []
    for item in ",".join(values).split(","):
        fields = dict(_ITEM.findall(item))
        if "r" in fields:
            out.append((int(fields["r"]), int(fields.get("t") or 0)))
    return out


def _note_quota(resp: httpx.Response) -> None:
    windows = _parse_ratelimit(resp.headers.get_list("RateLimit"))
    now = datetime.now(UTC)
    if resp.status_code == 429:
        try:
            wait = int(resp.headers.get("Retry-After") or 0)
        except ValueError:
            wait = 0
        windows.append((0, wait or 15 * 60))
    if not windows:
        return
    remaining, reset = min(windows)
    with _quota_lock:
        _quota["remaining"] = remaining
        _quota["reset_at"] = now + timedelta(seconds=reset)


def quota_low(threshold: int = LOW_QUOTA) -> bool:
    """Se o último saldo visto está baixo e a janela dele ainda não zerou."""
    with _quota_lock:
        remaining, reset_at = _quota["remaining"], _quota["reset_at"]
        if remaining is None:
            return False
        if reset_at is not None and reset_at <= datetime.now(UTC):
            _quota["remaining"] = _quota["reset_at"] = None
            return False
        return remaining <= threshold


def reset_quota() -> None:
    with _quota_lock:
        _quota["remaining"] = _quota["reset_at"] = None


class BufferPublisher(Provider):
    info = ProviderInfo(
        id=PROVIDER_ID,
        kind="publish",  # type: ignore[arg-type]
        name="Buffer (TikTok e Instagram)",
        commercial_use=True,
        free=True,
        credential_fields=("api_key",),
        env_vars=ENV_VARS,
        notes="Conecte o TikTok e o Instagram (conta Business ou Creator) no Buffer, crie uma chave em "
        "Settings → API na sua conta do Buffer e cole aqui. Também precisa do Cloudflare R2 configurado: o Buffer "
        "baixa o vídeo de uma URL pública na hora de publicar.",
        cost_note="Plano grátis: 3 canais, 10 posts na fila por canal, 1.000 chamadas da API por canal a cada 30 dias",
    )

    def availability(self) -> tuple[bool, str]:
        if not api_key():
            return False, "cole a chave de API do Buffer"
        from studio.workers.publish import r2

        if not r2.configured():
            return False, "chave do Buffer salva; falta configurar o Cloudflare R2 (URL pública dos vídeos)"
        return True, "ok"


def api_key() -> str | None:
    return credentials.resolve(PROVIDER_ID, "api_key", ENV_VARS)


def new_client(timeout: float = 60) -> httpx.Client:
    return httpx.Client(timeout=timeout)


_CODE_HINTS = {
    "UNAUTHORIZED": "a chave de API do Buffer foi recusada; crie outra em Settings → API e cole em Configurações",
    "FORBIDDEN": "a chave do Buffer não tem acesso a esse canal",
    "NOT_FOUND": "não encontrado no Buffer",
    "RATE_LIMIT_EXCEEDED": "limite de chamadas da API do Buffer atingido; tente mais tarde",
}


def graphql(query: str, variables: dict[str, Any] | None = None, *, http: httpx.Client | None = None) -> dict[str, Any]:
    """Faz uma chamada GraphQL e devolve `data` (erros viram BufferError com a explicação em português)."""
    key = api_key()
    if not key:
        raise BufferError("configure a chave de API do Buffer em Configurações", "NOT_CONFIGURED")
    own = http is None
    client = http or new_client()
    try:
        try:
            resp = client.post(
                API_URL,
                json={"query": query, "variables": variables or {}},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise BufferError(f"sem resposta do Buffer: {exc}", "NETWORK") from exc
    finally:
        if own:
            client.close()
    _note_quota(resp)
    if resp.status_code == 429:
        wait = resp.headers.get("Retry-After")
        raise BufferError(_CODE_HINTS["RATE_LIMIT_EXCEEDED"] + (f" (em {wait} s)" if wait else ""), "RATE_LIMIT_EXCEEDED")
    if resp.status_code == 401:
        raise BufferError(_CODE_HINTS["UNAUTHORIZED"], "UNAUTHORIZED")
    if resp.status_code != 200:
        code = "SERVER" if resp.status_code >= 500 else "HTTP"
        raise BufferError(f"erro do Buffer ({resp.status_code}): {resp.text[:300]}", code)
    try:
        body = resp.json()
    except ValueError as exc:
        raise BufferError("o Buffer respondeu num formato inesperado", "HTTP") from exc
    errors = body.get("errors") or []
    if errors:
        first = errors[0] or {}
        code = str((first.get("extensions") or {}).get("code") or "")
        hint = _CODE_HINTS.get(code)
        message = str(first.get("message") or "erro desconhecido")
        if not hint:
            text = f"erro do Buffer: {message}"
        elif code in ("UNAUTHORIZED", "RATE_LIMIT_EXCEEDED"):
            text = hint
        else:
            text = f"{hint} ({message})"
        raise BufferError(text, code)
    return body.get("data") or {}


# --------------------------------------------------------------------------------------------
# Canais
# --------------------------------------------------------------------------------------------


def list_channels(http: httpx.Client | None = None) -> list[dict[str, Any]]:
    """Perfis de TikTok e Instagram conectados no Buffer (de todas as organizações da conta)."""
    own = http is None
    client = http or new_client()
    try:
        orgs = (graphql(_ORGS_QUERY, http=client).get("account") or {}).get("organizations") or []
        out: list[dict[str, Any]] = []
        for org in orgs:
            channels = graphql(_CHANNELS_QUERY, {"input": {"organizationId": org["id"]}}, http=client).get("channels") or []
            for ch in channels:
                service = str(ch.get("service") or "").lower()
                if service not in PLATFORMS:
                    continue
                out.append({
                    "id": ch["id"],
                    "service": service,
                    "name": ch.get("displayName") or ch.get("name") or "",
                    "avatar": ch.get("avatar") or "",
                    "organization": org.get("name") or "",
                    "disconnected": bool(ch.get("isDisconnected")),
                    "locked": bool(ch.get("isLocked")),
                    "queue_paused": bool(ch.get("isQueuePaused")),
                    "timezone": ch.get("timezone") or "",
                })
        return out
    finally:
        if own:
            client.close()


# --------------------------------------------------------------------------------------------
# Posts
# --------------------------------------------------------------------------------------------


def post_input(
    *,
    channel_id: str,
    platform: str,
    text: str,
    video_url: str,
    mode: str,
    due_at: datetime | None = None,
    ai_generated: bool = False,
    thumbnail_offset_ms: int | None = None,
) -> dict[str, Any]:
    """CreatePostInput de um vídeo para o TikTok ou um Reel do Instagram."""
    if platform not in PLATFORMS:
        raise BufferError(f"o driver do Buffer publica só no TikTok e no Instagram, não em {platform}")
    if mode not in MODES:
        raise BufferError("modo inválido (use queue, now, next ou scheduled)")
    video: dict[str, Any] = {"url": video_url}
    if thumbnail_offset_ms is not None:
        video["metadata"] = {"thumbnailOffset": max(0, int(thumbnail_offset_ms))}
    data: dict[str, Any] = {
        "channelId": channel_id,
        "text": text,
        "schedulingType": "automatic",
        "mode": MODES[mode],
        "assets": [{"video": video}],
    }
    if mode == "scheduled":
        if due_at is None:
            raise BufferError("informe a data e a hora do agendamento")
        when = due_at if due_at.tzinfo else due_at.astimezone()
        if when <= datetime.now(UTC):
            raise BufferError("o horário do agendamento precisa estar no futuro")
        data["dueAt"] = when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    if platform == "instagram":
        data["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True, "isAiGenerated": bool(ai_generated)}}
    else:
        data["metadata"] = {"tiktok": {"isAiGenerated": bool(ai_generated)}}
    return data


def create_post(data: dict[str, Any], http: httpx.Client | None = None) -> dict[str, Any]:
    result = graphql(_CREATE_MUTATION, {"input": data}, http=http).get("createPost") or {}
    post = result.get("post")
    if not post:
        raise BufferError(f"o Buffer recusou o post: {result.get('message') or 'sem detalhes'}", "MUTATION")
    return post


# Limites dos vídeos que o Buffer publica sozinho (central de ajuda do Buffer: TikTok de 3 s a 10 min e até 1 GB;
# Reel de 5 s a 15 min, até 300 MB, entre 4:5 e 9:16; fora disso o Reel vira só lembrete).
VIDEO_LIMITS = {
    "tiktok": {"min_s": 3, "max_s": 10 * 60, "max_mb": 1024},
    "instagram": {"min_s": 5, "max_s": 15 * 60, "max_mb": 300},
}
VERTICAL_ASPECTS = ("9:16", "4:5")


def video_warnings(platform: str, *, aspect: str | None, duration_s: float | None, size_bytes: int | None) -> list[str]:
    """Avisos de um vídeo fora do que o TikTok ou o Reel aceitam (o Buffer só confere na hora de publicar)."""
    limits = VIDEO_LIMITS.get(platform)
    if not limits:
        return []
    name = "TikTok" if platform == "tiktok" else "Reels do Instagram"
    out: list[str] = []
    if aspect and aspect not in VERTICAL_ASPECTS:
        out.append(f"o vídeo é {aspect}; o {name} espera vídeo vertical (9:16) e pode recusar ou cortar")
    if duration_s:
        if duration_s > limits["max_s"]:
            out.append(f"o vídeo tem {round(duration_s / 60, 1)} min; o {name} aceita até {limits['max_s'] // 60} min "
                       "pelo Buffer e o post deve falhar na hora de sair")
        elif duration_s < limits["min_s"]:
            out.append(f"o vídeo tem menos de {limits['min_s']} s, abaixo do mínimo do {name}")
    if size_bytes and size_bytes > limits["max_mb"] * 1024 * 1024:
        out.append(f"o arquivo passa de {limits['max_mb']} MB, acima do limite do {name}")
    return out


def get_post(post_id: str, http: httpx.Client | None = None) -> dict[str, Any]:
    return graphql(_POST_QUERY, {"input": {"id": post_id}}, http=http).get("post") or {}


def delete_post(post_id: str, http: httpx.Client | None = None) -> None:
    result = graphql(_DELETE_MUTATION, {"input": {"id": post_id}}, http=http).get("deletePost") or {}
    if not result.get("id"):
        raise BufferError(f"o Buffer não apagou o post: {result.get('message') or 'sem detalhes'}", "MUTATION")
