"""Acesso do radar à YouTube Data API v3 (origem: classe `Api` e `chave_api` de estudio/radar.py do canais).

Ordem: a chave de API do cofre (provedor `youtube_data_api`, campo `api_key`, ou a variável `YOUTUBE_API_KEY`); sem
ela, o token (Bearer) do primeiro canal do YouTube vinculado em Canais. Sem nenhum dos dois, `RadarAccessError` com a
explicação de como resolver. Todo HTTP passa por `youtube.new_client()` (os testes trocam por um transporte simulado).
"""

from __future__ import annotations

import logging
import urllib.parse
from dataclasses import dataclass
from typing import Any

import httpx

from studio.providers import credentials
from studio.workers.publish import youtube

log = logging.getLogger(__name__)

PROVIDER = "youtube_data_api"
FIELD = "api_key"
ENV_VARS = ("YOUTUBE_API_KEY",)
API = "https://www.googleapis.com/youtube/v3/"
KEY_SOURCE = "chave própria (YOUTUBE_API_KEY)"
NO_ACCESS = (
    "Sem acesso à YouTube Data API: cole uma chave de API em Radar → Acesso (Google Cloud → APIs e serviços → "
    "Credenciais → Criar chave de API, com a YouTube Data API v3 ativada) ou vincule um canal do YouTube em Canais."
)
QUOTA_TEXT = "a cota do dia acabou: tente amanhã ou diminua o orçamento"


class RadarAccessError(RuntimeError):
    """Sem acesso à API, ou a API recusou todos os pedidos de um lote."""


@dataclass(frozen=True)
class Access:
    source: str
    key: str | None = None  # chave de API
    account: str | None = None  # credencial do canal vinculado (token renovado na hora)


def api_key() -> str | None:
    return credentials.resolve(PROVIDER, FIELD, ENV_VARS)


def resolve_access() -> Access:
    """Chave própria ou o primeiro canal vinculado com token. Não chama a rede."""
    key = api_key()
    if key:
        return Access(source=KEY_SOURCE, key=key)
    for acc in youtube.linked_accounts():
        title = acc.get("channel_title") or acc.get("channel_id") or ""
        return Access(
            source=f"token do canal {title}".strip(), account=youtube.channel_key(acc["channel_id"])
        )
    raise RadarAccessError(NO_ACCESS)


def access_state() -> dict[str, Any]:
    """Estado do acesso para a tela: {ok, source, message, key_masked}."""
    key = api_key()
    masked = credentials.mask(key)
    try:
        acc = resolve_access()
    except RadarAccessError as exc:
        return {"ok": False, "source": "", "message": str(exc), "key_masked": masked}
    return {"ok": True, "source": acc.source, "message": "", "key_masked": masked}


def has_access() -> bool:
    try:
        resolve_access()
    except RadarAccessError:
        return False
    return True


def _explain(resp: httpx.Response) -> str:
    """Motivo de uma recusa da API (cota do dia acabou vira texto simples)."""
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        err = {}
    reasons = [e.get("reason") for e in err.get("errors") or [] if isinstance(e, dict)]
    if "quotaExceeded" in reasons or "dailyLimitExceeded" in reasons:
        return QUOTA_TEXT
    message = err.get("message") or resp.text[:200]
    return f"{resp.status_code}: {str(message)[:200]}"


def _meter(caminho: str, status: int, quota_exceeded: bool) -> None:
    """Soma a chamada no medidor da cota (`publish/quota.py`). Nunca derruba o pedido."""
    from studio.workers.publish import quota

    method = caminho.split("?", 1)[0] + ".list"
    try:
        quota.spend(method, failed=status >= 400)
        if quota_exceeded:
            quota.mark_exhausted(quota.bucket(method))
    except Exception as exc:  # noqa: BLE001 - o medidor é um extra
        log.debug("medidor da cota: %s não contado: %s", method, exc)


class Api:
    """Pedidos à YouTube Data API contando a cota (busca = `custo_busca` unidades, o resto = 1).

    O radar ainda conta a busca como 100 unidades (`custo_busca=100`); a pesquisa de vídeos usa o modelo do medidor
    (`publish/quota.py`): `search.list` tem balde próprio de 100 chamadas por dia e não pesa na cota geral
    (`custo_busca=0`, as buscas ficam em `buscas`). Com `medir` (padrão: acesso pelo token de um canal vinculado, o
    mesmo projeto do Google Cloud dos envios), cada chamada entra no medidor da cota e um quotaExceeded marca o balde
    como esgotado no dia."""

    def __init__(self, access: Access, orcamento: int, http: httpx.Client | None = None, *, custo_busca: int = 100,
                 medir: bool | None = None):
        self.access, self.orcamento = access, orcamento
        self.custo_busca = custo_busca
        self.medir = access.account is not None if medir is None else medir
        self.unidades, self.pedidos, self.buscas = 0, 0, 0
        self.http = http or youtube.new_client()
        self.fonte = access.source
        self.erros: list[str] = []
        self._token: str | None = None

    @staticmethod
    def custo(caminho: str) -> int:
        return 100 if caminho.startswith("search?") else 1

    def _custo(self, caminho: str) -> int:
        return self.custo_busca if caminho.startswith("search?") else 1

    def cabe(self, unidades: int) -> bool:
        return self.unidades + unidades <= self.orcamento

    def _headers(self) -> dict[str, str]:
        if self.access.key:
            return {}
        if self._token is None:
            try:
                self._token = youtube.access_token(self.http, self.access.account)
            except youtube.YouTubeError as exc:
                raise RadarAccessError(f"não deu para usar o {self.fonte}: {exc}") from exc
        return {"Authorization": f"Bearer {self._token}"}

    def _url(self, caminho: str) -> str:
        url = API + caminho
        if self.access.key:
            url += "&key=" + urllib.parse.quote(self.access.key)
        return url

    def _get(self, caminho: str) -> dict[str, Any] | None:
        try:
            resp = self.http.get(self._url(caminho), headers=self._headers())
        except httpx.HTTPError as exc:
            self.erros.append(f"sem resposta do YouTube ({type(exc).__name__})")
            return None
        motivo = _explain(resp) if resp.status_code != 200 else ""
        if self.medir:
            _meter(caminho, resp.status_code, motivo == QUOTA_TEXT)
        if resp.status_code != 200:
            self.erros.append(motivo)
            return None
        try:
            return resp.json()
        except ValueError:
            self.erros.append("resposta inválida do YouTube")
            return None

    def lote(self, caminhos: list[str], strict: bool = True) -> list[dict[str, Any] | None]:
        """Faz os pedidos (na ordem); pedido que falha volta como None. Conta a cota gasta.
        Com `strict` (buscas, vídeos e canais), se todos falham, `RadarAccessError("a API recusou tudo: …")`.
        Sem `strict` (recentes dos candidatos), só a falta de token levanta o erro; o resto volta como None."""
        if not caminhos:
            return []
        self.unidades += sum(self._custo(c) for c in caminhos)
        self.pedidos += len(caminhos)
        self.buscas += sum(1 for c in caminhos if c.startswith("search?"))
        antes = len(self.erros)
        out = [self._get(c) for c in caminhos]
        if strict and all(x is None for x in out):
            novos = self.erros[antes:]
            raise RadarAccessError(f"a API recusou tudo: {novos[0] if novos else 'sem resposta'}")
        return out
