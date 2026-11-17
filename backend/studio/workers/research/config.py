"""Configuração da pesquisa de vídeos, por canal, em `Channel.settings["research"]` (JSON, sem migração).

Campos editáveis:
- `competitors`: concorrentes `[{id, name, handle}]` (link colado e resolvido sem cota);
- `keywords`: até 8 termos de busca (um por busca, sem "|"); vazio = as 5 primeiras palavras-chave do canal no YouTube;
- `keywords_us`: até 8 termos em inglês para o mercado "us" (vazio = os mesmos `keywords`);
- `markets`: onde buscar, entre "br" e "us" (padrão ["br"]); o "us" acha ângulos de fora que ainda não existem em PT;
- `formats`: formatos que o canal produz ("long", "short"); vazio = deduzido dos estilos e do estoque do canal;
- `budget`: teto da cota geral da YouTube Data API por rodada (cada leitura = 1 unidade; as buscas têm balde próprio
  de 100 por dia, contado à parte);
- `days`: janela dos vídeos olhados;
- `auto`: rodar toda semana na rotina da manhã.
Estado (gravado pelo job e pela rotina): `last_week`, `last_week_submitted`, `last_week_tries` (tentativas da rotina
naquela semana), `last_run_id`, `last_job`.
"""

from __future__ import annotations

import copy
import threading
from typing import Any

import httpx

from studio.core.styles.library import StyleNotFound, get_style
from studio.db import session_scope
from studio.models import Channel, Project
from studio.workers.publish import youtube as yt
from studio.workers.radar.catalog import OR_ERROR

KEY = "research"
MARKETS = ("br", "us")
FORMATS = ("long", "short")
MAX_KEYWORDS = 8
MAX_COMPETITORS = 20
DEFAULT_KEYWORDS = 5  # palavras-chave do canal usadas quando a pesquisa não tem as suas
BUDGET_RANGE = (100, 3000)
DAYS_RANGE = (14, 365)

DEFAULTS: dict[str, Any] = {
    "competitors": [],
    "keywords": [],
    "keywords_us": [],
    "markets": ["br"],
    "formats": [],
    "budget": 600,
    "days": 90,
    "auto": False,
}
STATE_FIELDS = ("last_week", "last_week_submitted", "last_week_tries", "last_run_id", "last_job")
EDITABLE = tuple(DEFAULTS)
_WRITE_LOCK = threading.Lock()  # tela, job e rotina gravam no mesmo JSON: ler e gravar juntos, sem perder campo


class ResearchError(ValueError):
    """Pedido inválido para a pesquisa (vira 422 na API)."""


class ChannelNotFound(LookupError):
    pass


def _channel_keywords(settings: dict[str, Any]) -> list[str]:
    words = ((settings.get("youtube") or {}).get("keywords")) or []
    return [str(w) for w in words if str(w).strip() and "|" not in str(w)][:DEFAULT_KEYWORDS]


def get_config(channel_id: str) -> dict[str, Any]:
    """A config do canal com os padrões. `keywords_default` diz se os termos vieram das palavras-chave do YouTube."""
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            raise ChannelNotFound(f"canal não encontrado: {channel_id}")
        settings = dict(channel.settings or {})
    stored = dict(settings.get(KEY) or {})
    cfg = copy.deepcopy(DEFAULTS)
    cfg.update({k: v for k, v in stored.items() if k in DEFAULTS and v is not None})
    for field in STATE_FIELDS:
        cfg[field] = stored.get(field)
    cfg["keywords_default"] = not cfg["keywords"]
    if not cfg["keywords"]:
        cfg["keywords"] = _channel_keywords(settings)
    return cfg


def _terms(value: Any, name: str) -> list[str]:
    if not isinstance(value, list):
        raise ResearchError(f"{name}: mande uma lista de termos")
    out: list[str] = []
    for raw in value:
        term = " ".join(str(raw or "").split())[:80]
        if not term:
            continue
        if "|" in term:
            raise ResearchError(OR_ERROR)
        if term.lower() not in {t.lower() for t in out}:
            out.append(term)
    if len(out) > MAX_KEYWORDS:
        raise ResearchError(f"{name}: no máximo {MAX_KEYWORDS} termos (cada um é uma busca por mercado, do limite "
                            "de 100 buscas por dia)")
    return out


def _competitors(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ResearchError("concorrentes: mande uma lista")
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or not yt.CHANNEL_ID_RE.fullmatch(str(item.get("id") or "")):
            raise ResearchError("concorrente sem id de canal válido (UC…): use o botão de resolver o link")
        if any(c["id"] == item["id"] for c in out):
            continue
        out.append({"id": str(item["id"]), "name": str(item.get("name") or "")[:200],
                    "handle": str(item.get("handle") or "")[:100]})
    if len(out) > MAX_COMPETITORS:
        raise ResearchError(f"no máximo {MAX_COMPETITORS} concorrentes")
    return out


def _int_in(value: Any, name: str, bounds: tuple[int, int]) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float) or int(value) != value:
        raise ResearchError(f"{name}: use um número inteiro")
    if not bounds[0] <= int(value) <= bounds[1]:
        raise ResearchError(f"{name}: de {bounds[0]} a {bounds[1]}")
    return int(value)


def validate(changes: dict[str, Any]) -> dict[str, Any]:
    """Confere e normaliza os campos editáveis enviados (os outros são ignorados)."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if key not in EDITABLE or value is None:
            continue
        if key == "competitors":
            out[key] = _competitors(value)
        elif key in ("keywords", "keywords_us"):
            out[key] = _terms(value, "palavras-chave" if key == "keywords" else "palavras-chave em inglês")
        elif key == "markets":
            markets = [m for m in MARKETS if m in (value or [])]
            if not markets or len(markets) != len(set(value)):
                raise ResearchError('mercados: escolha "br", "us" ou os dois')
            out[key] = markets
        elif key == "formats":
            if any(f not in FORMATS for f in value or []):
                raise ResearchError('formatos: use "long", "short" ou deixe vazio')
            out[key] = [f for f in FORMATS if f in value]
        elif key == "budget":
            out[key] = _int_in(value, "orçamento", BUDGET_RANGE)
        elif key == "days":
            out[key] = _int_in(value, "janela em dias", DAYS_RANGE)
        elif key == "auto":
            out[key] = bool(value)
    return out


def _write(channel_id: str, changes: dict[str, Any]) -> None:
    with _WRITE_LOCK, session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            raise ChannelNotFound(f"canal não encontrado: {channel_id}")
        settings = dict(channel.settings or {})
        settings[KEY] = {**dict(settings.get(KEY) or {}), **changes}
        channel.settings = settings  # dict novo: o SQLAlchemy só grava JSON reatribuído


def save_config(channel_id: str, **changes: Any) -> dict[str, Any]:
    """Grava os campos editáveis (validados) e devolve a config completa."""
    _write(channel_id, validate(changes))
    return get_config(channel_id)


def update_state(channel_id: str, **changes: Any) -> None:
    """Estado da rotina e do job (`last_week`, `last_run_id`...): sem validação, só os campos de estado."""
    _write(channel_id, {k: v for k, v in changes.items() if k in STATE_FIELDS})


def _style_format(style_id: str | None) -> str | None:
    if not style_id:
        return None
    try:
        return get_style(style_id).format.type
    except (StyleNotFound, ValueError):
        return None


def channel_formats(channel_id: str, cfg: dict[str, Any] | None = None) -> list[str]:
    """Formatos que o canal produz: os da config; senão os dos estilos do canal (padrão e projetos) e do estoque;
    sem nada disso, os dois."""
    cfg = cfg if cfg is not None else get_config(channel_id)
    if cfg.get("formats"):
        return [f for f in FORMATS if f in cfg["formats"]]
    found: set[str] = set()
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        if channel is None:
            raise ChannelNotFound(f"canal não encontrado: {channel_id}")
        styles = {channel.default_style_id} | {
            p.style_id for p in s.query(Project.style_id).filter(Project.channel_id == channel_id).distinct().all()
        }
        if channel.min_stock and channel.stock_format in FORMATS:  # sem mínimo de estoque, o formato é só o padrão
            found.add(channel.stock_format)
    for style_id in styles:
        fmt = _style_format(style_id)
        if fmt in FORMATS:
            found.add(fmt)
    return [f for f in FORMATS if f in found] or list(FORMATS)


def resolve_competitor(link: str, http: httpx.Client | None = None) -> dict[str, Any]:
    """Do link colado (@handle, /channel/UC…, /c/nome) ao concorrente `{id, name, handle, url, subs}` sem cota (página
    pública e feed RSS). `subs` fica None: o número de inscritos vem na rodada, pela API."""
    from studio.workers.publish.youtube_channels import _public_data

    own = http is None
    client = http or yt.new_client(timeout=30)
    try:
        data = _public_data(client, link)
    except yt.YouTubeError as exc:
        raise ResearchError(str(exc)) from exc
    finally:
        if own:
            client.close()
    return {"id": data["channel_id"], "name": data["title"], "handle": data.get("handle") or "",
            "url": data["url"], "subs": None}
