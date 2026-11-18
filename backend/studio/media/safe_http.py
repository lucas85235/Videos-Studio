"""Busca de links colados pelo usuário (página ou imagem), com anti-SSRF básico.

Só http/https de site público: nada de `localhost`, rede local, IP privado, porta diferente de 80/443 ou usuário e
senha no link. Os redirecionamentos são seguidos à mão (até `MAX_REDIRECTS`) e cada destino passa pela mesma
checagem. O corpo é lido aos pedaços até um limite de tamanho, com teto de tempo no fetch inteiro.

Limitação conhecida (decidida para um app pessoal): o IP conferido não fica fixado na conexão. Um DNS que troca de
resposta entre a checagem e a conexão (DNS rebinding) passa por esta proteção.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import time
from dataclasses import dataclass
from http.cookiejar import CookieJar, DefaultCookiePolicy
from urllib.parse import urljoin, urlsplit

import httpx

# Só ASCII: o httpx recusa acento no cabeçalho (UnicodeEncodeError). Nenhum dado do usuário aqui.
MEDIA_USER_AGENT = "Vulto/0.1 (ferramenta pessoal de video)"
WIKIMEDIA_PROVIDER = "wikimedia_commons"
WIKIMEDIA_CONTACT_ENV = ("VULTO_WIKIMEDIA_CONTACT",)
WIKIMEDIA_HOSTS = ("wikimedia.org", "wikipedia.org", "wikidata.org")
NO_CONTACT_MESSAGE = ("O Wikimedia Commons pede um contato (site ou e-mail) de quem usa a API. Informe o seu em "
                      "Configurações, no cartão Wikimedia Commons.")
_CONTACT_RE = re.compile(r"^[A-Za-z0-9._%+\-@:/~?=&#]{3,120}$")
HTML_MAX_BYTES = 1_500_000
IMAGE_MAX_BYTES = 20_000_000
# A conexão inclui o aperto de mão TLS: CDNs de notícia (futurecdn: PC Gamer, GamesRadar) chegam a levar ~10 s nele.
TIMEOUT = httpx.Timeout(10.0, connect=12.0)
WALL_CLOCK_S = 20.0
"""Teto de relógio do fetch inteiro (redirecionamentos e leitura do corpo)."""
MAX_REDIRECTS = 4
ACCEPT_HTML = "text/html,application/xhtml+xml;q=0.9,image/*;q=0.8,*/*;q=0.5"
ACCEPT_IMAGE = "image/avif,image/webp,image/jpeg,image/png,image/*;q=0.8"

BLOCKED_MESSAGE = "Link não aceito: use um endereço http ou https de um site público."
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home.arpa")
_REDIRECTS = {301, 302, 303, 307, 308}


class FetchError(Exception):
    """Falha ao abrir o link (mensagem em português em `args[0]`)."""


class BlockedUrl(FetchError):
    def __init__(self, message: str = BLOCKED_MESSAGE) -> None:
        super().__init__(message)


class TooLarge(FetchError):
    pass


@dataclass
class Fetched:
    url: str
    """Endereço final, depois dos redirecionamentos."""
    status: int
    content_type: str
    """Minúsculo, sem parâmetros ("text/html", "image/jpeg")."""
    charset: str | None
    content: bytes


def wikimedia_contact() -> str | None:
    """Contato que o próprio usuário informou em Configurações (site ou e-mail), exigido pela política de robôs do
    Wikimedia (sem ele, a API do Commons responde 403). Só vai para os sites do Wikimedia, no User-Agent."""
    from studio.providers import credentials  # aqui: o cofre abre o banco

    value = (credentials.resolve(WIKIMEDIA_PROVIDER, "contact", WIKIMEDIA_CONTACT_ENV) or "").strip()
    return value if _CONTACT_RE.match(value) and ("@" in value or "." in value) else None


def wikimedia_user_agent() -> str | None:
    contact = wikimedia_contact()
    return f"Vulto/0.1 ({contact})" if contact else None


def is_wikimedia(url: str) -> bool:
    try:
        host = (urlsplit(url).hostname or "").rstrip(".").lower()
    except ValueError:
        return False
    return any(host == h or host.endswith("." + h) for h in WIKIMEDIA_HOSTS)


def user_agent_for(url: str) -> str:
    """User-Agent do pedido: o com contato nos sites do Wikimedia (quando há), o genérico nos outros."""
    return (wikimedia_user_agent() if is_wikimedia(url) else None) or MEDIA_USER_AGENT


def resolve(host: str) -> list[str]:
    """IPs do host (A e AAAA). Os testes trocam esta função por uma que não usa a rede."""
    infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(str(info[4][0]) for info in infos))


def _public_ip(text: str) -> bool:
    """IP global (internet), nunca rede local, loopback, link-local, multicast ou IPv4 mapeado em IPv6."""
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped is not None or ip.sixtofour is not None):
        return False
    return ip.is_global and not ip.is_multicast


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def _resolved(host: str) -> list[str]:
    try:
        addresses = resolve(host)
    except (OSError, UnicodeError) as exc:
        raise FetchError("Não consegui abrir o link.") from exc
    if not addresses:
        raise FetchError("Não consegui abrir o link.")
    return addresses


def check_url(url: str) -> str:
    """A URL limpa (sem o fragmento) quando ela aponta para um site público; senão `BlockedUrl`."""
    text = (url or "").strip().replace(" ", "%20")
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError as exc:
        raise BlockedUrl() from exc
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").rstrip(".").lower()
    if scheme not in ("http", "https") or not host:
        raise BlockedUrl()
    if parts.username is not None or parts.password is not None:
        raise BlockedUrl()
    if port is not None and port not in (80, 443):
        raise BlockedUrl()
    if host == "localhost" or host.endswith(_BLOCKED_SUFFIXES):
        raise BlockedUrl()
    addresses = [host] if _is_ip(host) else _resolved(host)
    if not all(_public_ip(a) for a in addresses):
        raise BlockedUrl()
    return parts._replace(scheme=scheme, fragment="").geturl()


def new_client() -> httpx.Client:
    """Cliente sem redirecionamento automático, sem proxy do ambiente e sem guardar cookies."""
    return httpx.Client(
        follow_redirects=False,
        trust_env=False,
        timeout=TIMEOUT,
        headers={"User-Agent": MEDIA_USER_AGENT},
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    )


def _content_type(header: str) -> tuple[str, str | None]:
    main, *params = (header or "").split(";")
    charset = None
    for param in params:
        key, _, value = param.partition("=")
        if key.strip().lower() == "charset" and value.strip():
            charset = value.strip().strip("\"'").lower()
    return main.strip().lower(), charset


def _mb(limit: int) -> str:
    return f"{limit / 1_000_000:g}"


def fetch(url: str, *, max_bytes: int, accept: str, http: httpx.Client | None = None, truncate: bool = False,
          image_max_bytes: int | None = None, wall_clock_s: float = WALL_CLOCK_S) -> Fetched:
    """GET com a checagem de `check_url` em cada salto e leitura até `max_bytes`.

    `truncate=True` corta o corpo no limite sem erro (página: os metadados ficam no começo); senão, passar do limite
    é `TooLarge`. Com `image_max_bytes`, uma resposta `image/*` usa esse limite (sem cortar): o mesmo GET serve para
    link de página e de imagem. `wall_clock_s` é o teto de tempo do pedido inteiro (um vídeo pede mais que uma
    página).
    """
    deadline = time.monotonic() + wall_clock_s
    client = http or new_client()
    try:
        current = check_url(url)
        for _hop in range(MAX_REDIRECTS + 1):
            if time.monotonic() > deadline:
                raise FetchError("O site demorou demais para responder.")
            try:
                # Corpo sem compressão: o limite de tamanho é conferido depois de descomprimir cada pedaço.
                headers = {"Accept": accept, "Accept-Encoding": "identity", "User-Agent": user_agent_for(current)}
                with client.stream("GET", current, headers=headers, follow_redirects=False) as resp:
                    if resp.status_code in _REDIRECTS:
                        location = resp.headers.get("location")
                        if not location:
                            raise FetchError(f"O site respondeu {resp.status_code}.")
                        current = check_url(urljoin(current, location))
                        continue
                    if resp.status_code >= 400:
                        raise FetchError(f"O site respondeu {resp.status_code}.")
                    # Compressão empilhada ("gzip, gzip") multiplica a expansão: poucos bytes viram GB na memória
                    # antes da checagem de tamanho. Uma só (servidor que ignora "identity") fica limitada por pedaço.
                    codings = [c.strip().lower() for c in resp.headers.get("content-encoding", "").split(",")]
                    if len([c for c in codings if c not in ("", "identity")]) > 1:
                        raise FetchError("Não consegui abrir o link.")
                    content_type, charset = _content_type(resp.headers.get("content-type", ""))
                    limit, cut = max_bytes, truncate
                    if image_max_bytes is not None and content_type.startswith("image/"):
                        limit, cut = image_max_bytes, False
                    declared = resp.headers.get("content-length", "")
                    if not cut and declared.isdigit() and int(declared) > limit:
                        raise TooLarge(f"O arquivo passa de {_mb(limit)} MB.")
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in resp.iter_bytes():
                        if time.monotonic() > deadline:
                            raise FetchError("O site demorou demais para responder.")
                        if size + len(chunk) > limit:
                            if not cut:
                                raise TooLarge(f"O arquivo passa de {_mb(limit)} MB.")
                            chunks.append(chunk[: limit - size])
                            break
                        chunks.append(chunk)
                        size += len(chunk)
                    return Fetched(url=current, status=resp.status_code, content_type=content_type, charset=charset,
                                   content=b"".join(chunks))
            except httpx.TimeoutException as exc:
                raise FetchError("O site demorou demais para responder.") from exc
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                raise FetchError("Não consegui abrir o link.") from exc
        raise FetchError("O link redireciona demais.")
    finally:
        if http is None:
            client.close()
