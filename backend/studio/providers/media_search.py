"""Busca de fotos de licença livre para a mídia real: Wikimedia Commons, Openverse e Pexels (só com chave).

Nada é aplicado sozinho: a busca devolve até `MAX_CANDIDATES` candidatos e o usuário escolhe. Os resultados ficam no
cache do banco (`Artifact` kind "media_search", sem arquivos) por `SEARCH_TTL`. Uma fonte fora do ar (o Openverse
anônimo cai com frequência) vira status "erro" e a busca segue com as outras, sem levantar exceção.
"""

from __future__ import annotations

import html
import logging
import math
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Literal

import httpx

from studio.config import get_settings
from studio.core.graph.store import ArtifactStore
from studio.core.hashing import hash_obj
from studio.core.project.document import ProjectDoc
from studio.core.styles.schema import StyleSpec
from studio.media.real_media import MediaError, default_credit
from studio.media.safe_http import (
    MEDIA_USER_AGENT,
    NO_CONTACT_MESSAGE,
    WIKIMEDIA_CONTACT_ENV,
    WIKIMEDIA_PROVIDER,
    FetchError,
    wikimedia_contact,
    wikimedia_user_agent,
)
from studio.providers import credentials
from studio.providers.base import Provider, ProviderInfo

log = logging.getLogger(__name__)

COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
PEXELS_API = "https://api.pexels.com/v1/"
MAX_CANDIDATES = 6
MIN_LONG_SIDE = 900
SEARCH_TTL = timedelta(days=7)
ERROR_TTL = timedelta(hours=1)
"""Busca em que alguma fonte não respondeu vale menos tempo: a próxima tenta de novo."""
TIMEOUT = httpx.Timeout(12.0, connect=5.0)
PREFETCH_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
CACHE_KIND = "media_search"
SOURCES = ("commons", "openverse", "pexels")
COMMONS_MIMES = {"image/jpeg", "image/png", "image/webp", "image/tiff"}
COMMONS_META = "LicenseShortName|LicenseUrl|Artist|Credit|AttributionRequired|ObjectName|Restrictions"
OPENVERSE_LICENSES = "cc0,pdm,by,by-sa"
PEXELS_LICENSE_URL = "https://www.pexels.com/license/"
RESTRICTED_WARNING = "Pode ter direito de imagem de pessoa ou marca."
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_PROVIDER_LABELS = {"flickr": "Flickr", "smithsonian": "Smithsonian", "nasa": "NASA", "met": "The Met",
                    "rawpixel": "rawpixel", "stocksnap": "StockSnap", "nappy": "nappy"}
_ORIENTATION_RATIO = {"portrait": 9 / 16, "landscape": 16 / 9, "square": 1.0}

Orientation = Literal["portrait", "landscape", "square"]


@dataclass
class Candidate:
    id: str
    source: Literal["commons", "openverse", "pexels"]
    title: str
    author: str
    license: str
    license_url: str
    page_url: str
    thumb: str
    width: int
    height: int
    credit: str
    attribution_required: bool
    warning: str = ""
    download_url: str = ""
    """De onde a imagem é baixada ao anexar (não vai para a web)."""

    def to_dict(self) -> dict[str, Any]:
        """Formato de `MediaCandidate` (api.ts), sem `download_url`."""
        data = asdict(self)
        data.pop("download_url", None)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Candidate:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in names})


@dataclass
class SearchResult:
    query: str
    candidates: list[Candidate] = field(default_factory=list)
    sources: dict[str, str] = field(default_factory=dict)
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Formato de `MediaCandidates` (api.ts)."""
        return {"query": self.query, "candidates": [c.to_dict() for c in self.candidates],
                "sources": dict(self.sources), "cached": self.cached}


def new_client(timeout: httpx.Timeout = TIMEOUT) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": MEDIA_USER_AGENT}, follow_redirects=True)


def orientation_for(width: int, height: int) -> Orientation:
    return "portrait" if height > width else "landscape" if width > height else "square"


# --- licenças ----------------------------------------------------------------------------------


def is_free_license(name: str) -> bool:
    """Licença que deixa usar a foto no vídeo (inclusive monetizado): CC0, domínio público, CC BY e CC BY-SA,
    "No restrictions", "Copyrighted free use", "Attribution" e a licença do Pexels. Nunca NC, ND, GFDL sozinha ou
    uso justo ("Fair use")."""
    text = " ".join((name or "").lower().replace("-", " ").replace("_", " ").split())
    if not text:
        return False
    words = set(re.split(r"[\s,;/+()]+", text))
    if words & {"nc", "nd", "noncommercial", "noderivs", "noderivatives"} or "non commercial" in text:
        return False
    if "fair use" in text or "all rights reserved" in text:
        return False
    if text.startswith(("cc0", "cc zero", "public domain", "pd", "no restrictions", "copyrighted free use",
                        "attribution", "pexels license")):
        return True
    return "cc by" in text


def _openverse_license(code: str, version: str) -> str:
    code = (code or "").lower().strip()
    if code == "cc0":
        return "CC0"
    if code == "pdm":
        return "Public Domain Mark"
    if not code:
        return ""
    return f"CC {code.upper()} {version}".strip()


# --- respostas das fontes ----------------------------------------------------------------------


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _plain(value: object, limit: int = 160) -> str:
    """Texto sem as tags (o Artist do Commons vem em HTML)."""
    if not isinstance(value, str) or not value.strip():
        return ""
    parser = _Text()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001
        text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html.unescape(text).split())[:limit].strip()


def _int(value: object) -> int:
    try:
        return max(0, int(value))  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return 0


def _stem(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _commons_page(page: dict[str, Any], *, big: bool = False) -> Candidate | None:
    """Candidato de uma página de arquivo do Commons (None: formato, licença ou dados que não servem)."""
    infos = page.get("imageinfo") or []
    if page.get("missing") or not infos or not isinstance(infos[0], dict):
        return None
    info = infos[0]
    if (info.get("mime") or "").lower() not in COMMONS_MIMES:
        return None
    meta = info.get("extmetadata") or {}

    def value(key: str, limit: int = 160) -> str:
        entry = meta.get(key)
        return _plain(entry.get("value") if isinstance(entry, dict) else None, limit)

    license_name = value("LicenseShortName", 60)
    if not is_free_license(license_name):
        return None
    title = str(page.get("title") or "")
    if not title.startswith("File:"):
        return None
    author = value("Artist", 120)
    attribution = value("AttributionRequired").lower()
    restrictions = value("Restrictions").lower()
    original = str(info.get("url") or "")
    thumb = str(info.get("thumburl") or original)
    return Candidate(
        id=f"commons:{title}",
        source="commons",
        title=value("ObjectName", 200) or _stem(title[5:]),
        author=author,
        license=license_name,
        license_url=value("LicenseUrl", 300),
        page_url=str(info.get("descriptionurl") or ""),
        thumb=thumb,
        width=_int(info.get("width")),
        height=_int(info.get("height")),
        credit=default_credit("commons", author=author, license=license_name),
        attribution_required=attribution == "true" if attribution in ("true", "false") else "by" in license_name.lower(),
        warning=RESTRICTED_WARNING if "personality" in restrictions or "trademark" in restrictions else "",
        download_url=(thumb if big else "") or original,
    )


def parse_commons(data: dict[str, Any], *, big: bool = False) -> list[Candidate]:
    """Candidatos de uma resposta da API do Commons (`prop=imageinfo`), na ordem da busca. Saem SVG, GIF, foto
    pequena e licença que não é livre. Com `big`, o download é a miniatura grande (`iiurlwidth=3840`)."""
    pages = ((data or {}).get("query") or {}).get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    pages = sorted((p for p in pages if isinstance(p, dict)), key=lambda p: _int(p.get("index")) or 10**6)
    out = []
    for page in pages:
        candidate = _commons_page(page, big=big)
        if candidate is not None and max(candidate.width, candidate.height) >= MIN_LONG_SIDE:
            out.append(candidate)
    return out


def _provider_label(value: object) -> str:
    key = str(value or "").strip().lower()
    if not key:
        return ""
    return _PROVIDER_LABELS.get(key, key.replace("_", " ").title())


def _openverse_item(item: dict[str, Any]) -> Candidate | None:
    if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
        return None
    if str(item.get("filetype") or "").lower() in ("svg", "gif"):
        return None
    license_name = _openverse_license(str(item.get("license") or ""), str(item.get("license_version") or ""))
    if not is_free_license(license_name):
        return None
    author = " ".join(str(item.get("creator") or "").split())[:120]
    code = str(item.get("license") or "").lower()
    return Candidate(
        id=f"openverse:{item['id']}",
        source="openverse",
        title=" ".join(str(item.get("title") or "").split())[:200] or "Sem título",
        author=author,
        license=license_name,
        license_url=str(item.get("license_url") or ""),
        page_url=str(item.get("foreign_landing_url") or ""),
        thumb=str(item.get("thumbnail") or item.get("url") or ""),
        width=_int(item.get("width")),
        height=_int(item.get("height")),
        credit=default_credit("openverse", author=author, license=license_name,
                              provider=_provider_label(item.get("source") or item.get("provider"))),
        attribution_required=code not in ("cc0", "pdm"),
        download_url=str(item.get("url") or ""),
    )


def parse_openverse(data: dict[str, Any]) -> list[Candidate]:
    """Candidatos de uma busca do Openverse. Os do Wikimedia saem (o Commons já vem direto)."""
    out = []
    for item in (data or {}).get("results") or []:
        if not isinstance(item, dict) or "wikimedia" in str(item.get("source") or item.get("provider") or "").lower():
            continue
        candidate = _openverse_item(item)
        if candidate is not None and not (candidate.width and candidate.height
                                          and max(candidate.width, candidate.height) < MIN_LONG_SIDE):
            out.append(candidate)
    return out


def _pexels_item(photo: dict[str, Any]) -> Candidate | None:
    if not isinstance(photo, dict) or photo.get("id") is None:
        return None
    src = photo.get("src") or {}
    download = str(src.get("large2x") or src.get("original") or "")
    if not download:
        return None
    author = " ".join(str(photo.get("photographer") or "").split())[:120]
    return Candidate(
        id=f"pexels:{photo['id']}",
        source="pexels",
        title=" ".join(str(photo.get("alt") or "").split())[:200] or f"Foto {photo['id']}",
        author=author,
        license="Pexels License",
        license_url=PEXELS_LICENSE_URL,
        page_url=str(photo.get("url") or ""),
        thumb=str(src.get("medium") or src.get("small") or download),
        width=_int(photo.get("width")),
        height=_int(photo.get("height")),
        credit=default_credit("pexels", author=author),
        attribution_required=False,
        download_url=download,
    )


def parse_pexels(data: dict[str, Any]) -> list[Candidate]:
    out = []
    for photo in (data or {}).get("photos") or []:
        candidate = _pexels_item(photo)
        if candidate is not None:
            out.append(candidate)
    return out


# --- busca ---------------------------------------------------------------------------------------


def _json(resp: httpx.Response) -> dict[str, Any] | None:
    if resp.status_code != 200:
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _status(found: list[Candidate]) -> str:
    return "ok" if found else "vazio"


class WikimediaCommonsSource(Provider):
    """Só para a tela de Configurações: guarda o contato que o Commons exige (não gera imagem nem entra em cadeia)."""

    info = ProviderInfo(
        id=WIKIMEDIA_PROVIDER,
        kind="image",
        name="Wikimedia Commons (mídia real)",
        commercial_use=True,
        free=True,
        credential_fields=("contact",),
        env_vars=WIKIMEDIA_CONTACT_ENV,
        notes="Fotos de licença livre sugeridas nas cenas (mídia real); nada é aplicado sozinho. A política de robôs "
        "do Wikimedia pede um contato de quem usa a API: informe um site ou e-mail seu. Ele vai só para os sites do "
        "Wikimedia, no cabeçalho do pedido. Sem contato, a busca de fotos usa só o Openverse.",
        cost_note="Grátis, sem chave.",
    )

    def availability(self) -> tuple[bool, str]:
        if wikimedia_contact():
            return True, "ok"
        return False, "sem contato: o Commons pede um site ou e-mail seu"


def _search_commons(client: httpx.Client, query: str) -> tuple[list[Candidate], str]:
    agent = wikimedia_user_agent()
    if agent is None:
        return [], "sem_contato"
    params = {
        "action": "query", "format": "json", "formatversion": "2", "generator": "search", "gsrnamespace": "6",
        "gsrlimit": "10", "gsrsearch": f"{query} filetype:bitmap", "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "500", "iiextmetadatafilter": COMMONS_META,
    }  # fmt: skip
    try:
        data = _json(client.get(COMMONS_API, params=params, headers={"User-Agent": agent}))
    except httpx.HTTPError as exc:
        log.warning("busca no Commons falhou: %s", exc)
        return [], "erro"
    if data is None:
        return [], "erro"
    found = parse_commons(data)
    return found, _status(found)


def _search_openverse(client: httpx.Client, query: str) -> tuple[list[Candidate], str]:
    params = {"q": query, "license": OPENVERSE_LICENSES, "page_size": "10", "mature": "false",
              "excluded_source": "wikimedia"}
    try:
        resp = client.get(OPENVERSE_API, params=params)
        if resp.status_code == 400:  # filtro de fonte não aceito: repete sem ele
            params.pop("excluded_source")
            resp = client.get(OPENVERSE_API, params=params)
        data = _json(resp)
    except httpx.HTTPError as exc:
        log.warning("busca no Openverse falhou: %s", exc)
        return [], "erro"
    if data is None:
        return [], "erro"
    found = parse_openverse(data)
    return found, _status(found)


def _search_pexels(client: httpx.Client, query: str, orientation: Orientation, key: str) -> tuple[list[Candidate], str]:
    try:
        data = _json(client.get(PEXELS_API + "search", params={"query": query, "orientation": orientation,
                                                               "per_page": "6"}, headers={"Authorization": key}))
    except httpx.HTTPError as exc:
        log.warning("busca no Pexels falhou: %s", exc)
        return [], "erro"
    if data is None:
        return [], "erro"
    found = parse_pexels(data)
    return found, _status(found)


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if ch.isalnum() and not unicodedata.combining(ch))


def merge(found: dict[str, list[Candidate]], orientation: Orientation) -> list[Candidate]:
    """Junta as fontes: sem repetidos (página ou título), sem foto pequena nem licença que não é livre; cada fonte
    ordenada pela proporção mais perto da do vídeo e depois pela resolução; alterna as fontes (Commons primeiro)."""
    target = _ORIENTATION_RATIO.get(orientation, 1.0)
    pages: set[str] = set()
    titles: set[str] = set()
    queues: dict[str, list[Candidate]] = {}

    def distance(c: Candidate) -> float:
        return abs(math.log((c.width / c.height) / target)) if c.width and c.height else 10.0

    for source in SOURCES:
        kept = []
        for c in found.get(source) or []:
            if c.width and c.height and max(c.width, c.height) < MIN_LONG_SIDE:
                continue
            if not is_free_license(c.license):
                continue
            page = c.page_url.lower().rstrip("/")
            title = _fold(c.title)
            if (page and page in pages) or (len(title) >= 8 and title in titles):
                continue
            if page:
                pages.add(page)
            if len(title) >= 8:
                titles.add(title)
            kept.append(c)
        kept.sort(key=lambda c: (round(distance(c), 2), -(c.width * c.height)))
        queues[source] = kept
    out: list[Candidate] = []
    while len(out) < MAX_CANDIDATES and any(queues.values()):
        for source in SOURCES:
            if queues.get(source) and len(out) < MAX_CANDIDATES:
                out.append(queues[source].pop(0))
    return out


def _cache_key(query: str, orientation: str, pexels: bool, commons: bool = True) -> str:
    return hash_obj({"q": query.strip().lower(), "orientation": orientation, "pexels": pexels, "commons": commons,
                     "v": 1})


def _age(cached: dict[str, Any]) -> timedelta:
    try:
        when = datetime.fromisoformat(str(cached.get("fetched_at")))
    except ValueError:
        return timedelta.max
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return datetime.now(UTC) - when


def _fresh(cached: dict[str, Any]) -> bool:
    age = _age(cached)
    failed = "erro" in (cached.get("sources") or {}).values()
    return age < (ERROR_TTL if failed else SEARCH_TTL)


def _from_cache(query: str, cached: dict[str, Any]) -> SearchResult:
    candidates = [Candidate.from_dict(c) for c in cached.get("candidates") or [] if isinstance(c, dict)]
    return SearchResult(query=query, candidates=candidates, sources=dict(cached.get("sources") or {}), cached=True)


def _pexels_key() -> str | None:
    return credentials.resolve("pexels", "api_key", ("PEXELS_API_KEY",))


def search(query: str, *, orientation: Orientation, refresh: bool = False,
           http: httpx.Client | None = None) -> SearchResult:
    """Candidatos para a busca (em inglês). Offline (sem `http`), não faz rede."""
    text = " ".join((query or "").split())[:120]
    if not text:
        return SearchResult(query="")
    if http is None and get_settings().offline:
        return SearchResult(query=text, sources={"commons": "offline", "openverse": "offline"})
    key = _pexels_key()
    store = ArtifactStore()
    input_hash = _cache_key(text, orientation, bool(key), wikimedia_contact() is not None)
    cached = store.lookup(CACHE_KIND, input_hash)
    if cached and not refresh and _fresh(cached):
        return _from_cache(text, cached)
    client = http or new_client()
    try:
        found: dict[str, list[Candidate]] = {}
        sources: dict[str, str] = {}
        found["commons"], sources["commons"] = _search_commons(client, text)
        found["openverse"], sources["openverse"] = _search_openverse(client, text)
        if key:
            found["pexels"], sources["pexels"] = _search_pexels(client, text, orientation, key)
        else:
            sources["pexels"] = "sem_chave"
    finally:
        if http is None:
            client.close()
    asked = [status for status in sources.values() if status not in ("sem_chave", "sem_contato")]
    if all(status == "erro" for status in asked):
        if cached:
            return _from_cache(text, cached)  # a rede caiu: o resultado vencido ainda serve
        return SearchResult(query=text, sources=sources)
    candidates = merge(found, orientation)
    store.save(CACHE_KIND, input_hash, {
        "query": text, "candidates": [asdict(c) for c in candidates], "sources": sources,
        "fetched_at": datetime.now(UTC).isoformat(), "files": [],
    }, "media_search")  # fmt: skip
    return SearchResult(query=text, candidates=candidates, sources=sources, cached=False)


# --- um candidato ------------------------------------------------------------------------------


def _get(client: httpx.Client, url: str, unavailable: str, **kwargs: Any) -> httpx.Response:
    try:
        return client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise FetchError(unavailable) from exc


def lookup(candidate_id: str, *, http: httpx.Client | None = None) -> Candidate:
    """O candidato direto na fonte (1 GET), com a licença conferida de novo. `MediaError` se não achar ou se a
    licença não for livre; `FetchError` se a fonte não responder."""
    source, _, ident = (candidate_id or "").partition(":")
    ident = ident.strip()
    client = http or new_client()
    try:
        if source == "commons" and ident.startswith("File:") and len(ident) > 5:
            agent = wikimedia_user_agent()
            if agent is None:
                raise MediaError(NO_CONTACT_MESSAGE)
            params = {"action": "query", "format": "json", "formatversion": "2", "titles": ident,
                      "prop": "imageinfo", "iiprop": "url|size|mime|extmetadata", "iiurlwidth": "3840",
                      "iiextmetadatafilter": COMMONS_META}
            resp = _get(client, COMMONS_API, "O Wikimedia Commons não respondeu agora.", params=params,
                        headers={"User-Agent": agent})
            data = _json(resp)
            if data is None:
                raise FetchError("O Wikimedia Commons não respondeu agora.")
            pages = (data.get("query") or {}).get("pages") or []
            page = next((p for p in pages if isinstance(p, dict) and not p.get("missing") and p.get("imageinfo")), None)
            if page is None:
                raise MediaError("Não achei esta foto no Wikimedia Commons.")
            candidate = _commons_page(page, big=True)
            if candidate is None:
                raise MediaError("A licença ou o formato desta imagem não deixa usar no vídeo.")
            return candidate
        if source == "openverse" and _UUID_RE.match(ident):
            resp = _get(client, f"{OPENVERSE_API}{ident}/", "O Openverse não respondeu agora.")
            if resp.status_code == 404:
                raise MediaError("Não achei esta foto no Openverse.")
            data = _json(resp)
            if data is None:
                raise FetchError("O Openverse não respondeu agora.")
            candidate = _openverse_item(data)
            if candidate is None:
                raise MediaError("A licença ou o formato desta imagem não deixa usar no vídeo.")
            return candidate
        if source == "pexels" and ident.isdigit():
            key = _pexels_key()
            if not key:
                raise MediaError("Configure a chave do Pexels para usar esta foto.")
            resp = _get(client, f"{PEXELS_API}photos/{ident}", "O Pexels não respondeu agora.",
                        headers={"Authorization": key})
            if resp.status_code == 404:
                raise MediaError("Não achei esta foto no Pexels.")
            data = _json(resp)
            candidate = _pexels_item(data) if data is not None else None
            if candidate is None:
                raise FetchError("O Pexels não respondeu agora.")
            return candidate
        raise MediaError("Foto não encontrada.")
    finally:
        if http is None:
            client.close()


# --- pré-busca do job das cenas ----------------------------------------------------------------


def prefetch(doc: ProjectDoc, style: StyleSpec, *, http: httpx.Client | None = None, budget_s: float = 25) -> int:
    """Busca de antemão as sugestões de foto do storyboard (cache quente quando o usuário abrir a cena), até
    `budget_s` segundos no total. Devolve quantas buscas foram feitas. Offline: nada."""
    if http is None and get_settings().offline:
        return 0
    queries = list(dict.fromkeys(
        " ".join(scene.real.query.split()) for scene in doc.ordered_scenes()
        if scene.real is not None and scene.real.kind == "photo" and scene.real.query.strip()
        and scene.visual.media is None))
    if not queries:
        return 0
    orientation = orientation_for(style.format.width, style.format.height)
    deadline = time.monotonic() + budget_s
    client = http or new_client(PREFETCH_TIMEOUT)
    done = 0
    try:
        for query in queries:
            if time.monotonic() >= deadline:
                break
            try:
                search(query, orientation=orientation, http=client)
                done += 1
            except Exception as exc:  # noqa: BLE001 - a pré-busca nunca derruba o job
                log.warning("pré-busca de fotos falhou para %r: %s", query, exc)
    finally:
        if http is None:
            client.close()
    return done
