"""Hospedagem temporária dos vídeos no Cloudflare R2 (API compatível com S3), para as URLs públicas que o Buffer exige.

O Buffer baixa o vídeo **na hora de publicar** (pode ser dias depois), então o arquivo precisa ficar numa URL pública,
direta, em HTTPS e que não expira até o post sair (developers.buffer.com, guia "Hosting Media"). Por isso a URL vem do
domínio público do bucket (domínio próprio ou o subdomínio r2.dev), e não de um link assinado (que expira). Depois que
o post sai (ou falha, ou é cancelado), o arquivo é apagado.

Assinatura AWS Signature Version 4 feita aqui mesmo com httpx (sem boto3). Endpoint
`https://<ID da conta>.r2.cloudflarestorage.com`, região `auto` (developers.cloudflare.com/r2/api/s3/api/).
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from studio.providers import credentials
from studio.providers.base import Provider, ProviderInfo

PROVIDER_ID = "cloudflare_r2"
FIELDS = ("account_id", "access_key_id", "secret_access_key", "bucket", "public_url")
REGION = "auto"
SERVICE = "s3"
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
MAX_SINGLE_PUT = 5 * 1024**3  # limite de um PutObject (5 GiB); vídeos do Buffer ficam bem abaixo (TikTok ≤ 1 GB)
READ_CHUNK = 1024 * 1024
KEY_PREFIX = "vulto"


class R2Error(RuntimeError):
    pass


class R2Storage(Provider):
    info = ProviderInfo(
        id=PROVIDER_ID,
        kind="publish",  # type: ignore[arg-type]
        name="Cloudflare R2 (URLs públicas dos vídeos)",
        commercial_use=True,
        free=True,
        credential_fields=FIELDS,
        notes="Guarda o vídeo numa URL pública até o Buffer publicar e apaga depois. No painel da Cloudflare: crie um "
        "bucket, ative o acesso público (domínio próprio ou o endereço r2.dev, que tem limite de uso e é para testes) e "
        "crie um token de API do R2 com permissão de leitura e escrita de objetos nesse bucket. Cole aqui o ID da conta, "
        "o par de chaves do token, o nome do bucket e o endereço público (ex.: https://pub-xxxx.r2.dev).",
        cost_note="Grátis até ~10 GB guardados e sem custo de saída; pode pedir cartão cadastrado na Cloudflare",
    )

    def availability(self) -> tuple[bool, str]:
        missing = [f for f in FIELDS if not credentials.resolve(PROVIDER_ID, f)]
        if missing:
            return False, f"falta preencher: {', '.join(missing)}"
        try:
            _public_base()
        except R2Error as exc:
            return False, str(exc)
        return True, "ok"


def configured() -> bool:
    return R2Storage().availability()[0]


def _cfg() -> dict[str, str]:
    values = {f: (credentials.resolve(PROVIDER_ID, f) or "").strip() for f in FIELDS}
    missing = [f for f, v in values.items() if not v]
    if missing:
        raise R2Error("configure o Cloudflare R2 em Configurações (falta: " + ", ".join(missing) + ")")
    return values


def _public_base(value: str | None = None) -> str:
    raw = (value if value is not None else credentials.resolve(PROVIDER_ID, "public_url") or "").strip().rstrip("/")
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        raise R2Error("o endereço público do bucket precisa começar com https:// (domínio próprio ou r2.dev)")
    return raw


def endpoint(account_id: str) -> str:
    return f"https://{account_id}.r2.cloudflarestorage.com"


def public_url(key: str) -> str:
    return f"{_public_base()}/{quote(key, safe='/')}"


def new_key(project_id: str, publication_hint: str, suffix: str = ".mp4") -> str:
    """Nome do objeto: pasta do projeto e um trecho aleatório (a URL pública não fica adivinhável)."""
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in publication_hint)[:40] or "video"
    return f"{KEY_PREFIX}/{project_id}/{safe}-{secrets.token_urlsafe(12)}{suffix}"


# --------------------------------------------------------------------------------------------
# AWS Signature Version 4
# --------------------------------------------------------------------------------------------


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _canonical_uri(path: str) -> str:
    # S3: cada caractere fora dos "não reservados" é codificado uma vez; as barras ficam.
    return quote(path or "/", safe="/-_.~")


def sign(
    method: str,
    url: str,
    *,
    access_key: str,
    secret_key: str,
    payload_hash: str,
    headers: dict[str, str] | None = None,
    region: str = REGION,
    now: datetime | None = None,
) -> dict[str, str]:
    """Cabeçalhos assinados (Authorization, x-amz-date, x-amz-content-sha256) de um pedido S3 sem query string."""
    parsed = urlparse(url)
    if parsed.query:
        raise ValueError("pedidos com query string não são usados aqui")
    stamp = (now or datetime.now(UTC)).astimezone(UTC)
    amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
    day = stamp.strftime("%Y%m%d")
    host = parsed.netloc
    all_headers = {"host": host, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    for name, value in (headers or {}).items():
        all_headers[name.lower()] = " ".join(str(value).strip().split())
    names = sorted(all_headers)
    canonical_headers = "".join(f"{n}:{all_headers[n]}\n" for n in names)
    signed = ";".join(names)
    canonical = "\n".join([method.upper(), _canonical_uri(parsed.path), "", canonical_headers, signed, payload_hash])
    scope = f"{day}/{region}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    key = _hmac(_hmac(_hmac(_hmac(f"AWS4{secret_key}".encode(), day), region), SERVICE), "aws4_request")
    signature = hmac.new(key, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    out = {k: v for k, v in all_headers.items() if k != "host"}
    out["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
    )
    return out


# --------------------------------------------------------------------------------------------
# Envio e remoção
# --------------------------------------------------------------------------------------------


def new_client() -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60, read=600, write=600))


def _object_url(cfg: dict[str, str], key: str) -> str:
    return f"{endpoint(cfg['account_id'])}/{cfg['bucket']}/{key}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _explain(resp: httpx.Response) -> str:
    text = resp.text[:300]
    hint = ""
    if resp.status_code in (401, 403):
        hint = " (confira as chaves do token do R2 e se ele tem permissão de escrita neste bucket)"
    elif resp.status_code == 404:
        hint = " (o bucket não existe nessa conta; confira o nome e o ID da conta)"
    return f"{resp.status_code} {text}{hint}"


def upload(path: Path, key: str, *, http: httpx.Client | None = None, content_type: str = "video/mp4",
           on_progress=None) -> str:
    """Envia o arquivo ao bucket e devolve a URL pública dele."""
    cfg = _cfg()
    base = _public_base(cfg["public_url"])
    size = path.stat().st_size
    if size > MAX_SINGLE_PUT:
        raise R2Error("o vídeo passa de 5 GB, acima do limite de um envio ao R2")
    payload_hash = _file_sha256(path)
    url = _object_url(cfg, key)
    own = http is None
    client = http or new_client()

    def body() -> Iterator[bytes]:
        sent = 0
        with open(path, "rb") as fh:
            while chunk := fh.read(READ_CHUNK):
                sent += len(chunk)
                if on_progress and size:
                    on_progress(min(1.0, sent / size))
                yield chunk

    try:
        for attempt in range(3):
            headers = sign("PUT", url, access_key=cfg["access_key_id"], secret_key=cfg["secret_access_key"],
                           payload_hash=payload_hash, headers={"content-type": content_type})
            headers["content-length"] = str(size)
            try:
                resp = client.put(url, headers=headers, content=body())
            except httpx.HTTPError as exc:
                if attempt < 2:
                    time.sleep(2**attempt)
                    continue
                raise R2Error(f"sem resposta do R2 ao enviar o vídeo: {exc}") from exc
            if resp.status_code in (200, 201):
                return f"{base}/{quote(key, safe='/')}"
            if resp.status_code >= 500 and attempt < 2:
                time.sleep(2**attempt)
                continue
            raise R2Error(f"o R2 recusou o vídeo: {_explain(resp)}")
        raise R2Error("o R2 não aceitou o vídeo depois de 3 tentativas")
    finally:
        if own:
            client.close()


def delete(key: str, *, http: httpx.Client | None = None) -> None:
    """Apaga o objeto (o S3 responde 204 mesmo se ele já não existir)."""
    cfg = _cfg()
    url = _object_url(cfg, key)
    own = http is None
    client = http or new_client()
    try:
        headers = sign("DELETE", url, access_key=cfg["access_key_id"], secret_key=cfg["secret_access_key"],
                       payload_hash=EMPTY_SHA256)
        try:
            resp = client.delete(url, headers=headers)
        except httpx.HTTPError as exc:
            raise R2Error(f"sem resposta do R2 ao apagar o vídeo: {exc}") from exc
        if resp.status_code not in (200, 204, 404):
            raise R2Error(f"não foi possível apagar o vídeo do R2: {_explain(resp)}")
    finally:
        if own:
            client.close()
