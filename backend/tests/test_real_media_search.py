"""Busca de fotos livres (Commons, Openverse, Pexels): respostas gravadas, licenças, cache e fontes fora do ar.

Nada de rede: o httpx usa `MockTransport`. `fixtures/media/commons_*.json` são respostas reais do Commons
(15/11/2026); a do Openverse segue o formato documentado (a API estava fora do ar no dia).
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from studio.core.graph.store import ArtifactStore
from studio.core.project.document import ProjectDoc, RealHint, Scene, Segment, Visual
from studio.core.styles.library import get_style
from studio.media.real_media import MediaError
from studio.media.safe_http import MEDIA_USER_AGENT, FetchError, user_agent_for, wikimedia_contact
from studio.providers import media_search as ms

FIXTURES = Path(__file__).parent / "fixtures" / "media"
QUERY = "Miami Beach skyline"


def load(name: str) -> dict[str, Any]:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    data.pop("_nota", None)
    return data


class Api:
    """Transporte falso por host: guarda os pedidos e responde o que cada teste mandar."""

    def __init__(self, *, commons: Any = None, lookup: Any = None, openverse: Any = None, pexels: Any = None) -> None:
        self.commons = load("commons_search.json") if commons is None else commons
        self.lookup = load("commons_lookup.json") if lookup is None else lookup
        self.openverse = load("openverse_search.json") if openverse is None else openverse
        self.pexels = pexels
        self.requests: list[httpx.Request] = []

    @staticmethod
    def _answer(value: Any) -> httpx.Response:
        if isinstance(value, httpx.Response):
            return value
        if isinstance(value, int):
            return httpx.Response(value, text="error code: 504")
        if isinstance(value, Exception):
            raise value
        return httpx.Response(200, json=value)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "commons.wikimedia.org":
            return self._answer(self.lookup if "titles" in request.url.params else self.commons)
        if host == "api.openverse.org":
            value = self.openverse
            return self._answer(value(request) if callable(value) else value)
        if host == "api.pexels.com":
            return self._answer(self.pexels if self.pexels is not None else 401)
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


@pytest.fixture(autouse=True)
def no_pexels_key(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)


def _page(**changes: Any) -> dict[str, Any]:
    """Uma página da resposta gravada do Commons, com mudanças."""
    page = copy.deepcopy(load("commons_search.json")["query"]["pages"][3])  # "Miami skyline from the ocean"
    info = page["imageinfo"][0]
    for key, value in changes.items():
        if key in ("width", "height", "mime"):
            info[key] = value
        elif key == "title":
            page["title"] = value
        else:
            info["extmetadata"][key] = {"value": value}
    return page


# --- respostas -----------------------------------------------------------------------------------


def test_parse_commons_reads_the_recorded_answer():
    found = ms.parse_commons(load("commons_search.json"))
    assert [c.title for c in found] == [  # na ordem da busca (`index`), não na da lista
        "Miami skyline from the ocean",
        "Miami skyline from Miami Beach 2006 by Averette101",
        "Skyline over Miami beach USA",
        "Miami Beach and Port of Miami Skyline (2720195951)",
        "Marina and city skyline at night, Miami Beach, Florida",
    ]
    first = found[0]
    assert first.id == "commons:File:Miami skyline from the ocean.jpg" and first.source == "commons"
    assert first.author == "Matthew T Rader"  # o Artist vem em HTML
    assert (first.license, first.license_url) == ("CC BY-SA 4.0", "https://creativecommons.org/licenses/by-sa/4.0")
    assert first.credit == "Matthew T Rader / Wikimedia Commons · CC BY-SA 4.0"
    assert first.attribution_required is True and first.warning == ""
    assert first.page_url == "https://commons.wikimedia.org/wiki/File:Miami_skyline_from_the_ocean.jpg"
    assert first.thumb.startswith("https://thumb.wikimedia.org/") and "/500px-" in first.thumb
    assert (first.width, first.height) == (3881, 2426)
    assert first.download_url.startswith("https://upload.wikimedia.org/")  # na busca: o original
    by_title = {c.title: c for c in found}
    pd = by_title["Miami skyline from Miami Beach 2006 by Averette101"]
    assert pd.license == "Public domain" and pd.attribution_required is False
    assert pd.credit == "Marc Averette / Wikimedia Commons · Public domain"
    flickr = by_title["Miami Beach and Port of Miami Skyline (2720195951)"]
    assert flickr.author == "joiseyshowaa from Freehold, NJ, USA"
    assert by_title["Marina and city skyline at night, Miami Beach, Florida"].license == "CC0"


def test_parse_commons_drops_what_cannot_be_used():
    pages = [
        _page(title="File:Mapa.svg", mime="image/svg+xml"),
        _page(title="File:Anim.gif", mime="image/gif"),
        _page(title="File:Pequena.jpg", width=640, height=480),
        _page(title="File:NC.jpg", LicenseShortName="CC BY-NC-SA 4.0"),
        _page(title="File:ND.jpg", LicenseShortName="CC BY-ND 2.0"),
        _page(title="File:Uso justo.jpg", LicenseShortName="Fair use"),
        _page(title="File:GFDL.jpg", LicenseShortName="GFDL"),
        _page(title="File:Pessoa.jpg", Restrictions="personality|trademarked"),
        {"title": "File:Sumiu.jpg", "missing": True},
    ]
    found = ms.parse_commons({"query": {"pages": pages}})
    assert [c.id for c in found] == ["commons:File:Pessoa.jpg"]
    assert found[0].warning == "Pode ter direito de imagem de pessoa ou marca."


@pytest.mark.parametrize("name,free", [
    ("CC0", True), ("CC BY 4.0", True), ("CC BY-SA 3.0", True), ("Cc-by-sa-4.0", True), ("CC BY-SA 3.0 de", True),
    ("Public domain", True), ("PD-USGov", True), ("Public Domain Mark", True), ("No restrictions", True),
    ("Copyrighted free use", True), ("Attribution", True), ("Pexels License", True), ("GFDL, CC BY-SA 3.0", True),
    ("CC BY-NC 2.0", False), ("CC BY-NC-SA 4.0", False), ("CC BY-ND 4.0", False), ("GFDL", False), ("GFDL 1.2", False),
    ("Fair use", False), ("", False), ("All rights reserved", False),
])  # fmt: skip
def test_free_licenses(name, free):
    assert ms.is_free_license(name) is free


def test_parse_openverse_licenses_and_exclusions():
    found = ms.parse_openverse(load("openverse_search.json"))
    assert [c.title for c in found] == ["Miami Beach skyline at dusk", "Ocean Drive"]  # sem NC, wikimedia, pequena, svg
    flickr, cc0 = found
    assert flickr.id == "openverse:5f2a6c1e-8a7b-4c3d-9e10-1a2b3c4d5e6f" and flickr.license == "CC BY-SA 2.0"
    assert flickr.credit == "Exemplo Fotografia / Flickr · CC BY-SA 2.0" and flickr.attribution_required
    assert flickr.download_url == "https://live.staticflickr.com/65535/1234567890_abcdef1234_b.jpg"
    assert flickr.thumb.endswith("/thumb/") and flickr.page_url.startswith("https://www.flickr.com/")
    assert cc0.license == "CC0" and cc0.credit == "StockSnap · CC0" and cc0.attribution_required is False


def test_parse_pexels():
    data = {"photos": [{"id": 123, "width": 4000, "height": 6000, "url": "https://www.pexels.com/photo/praia-123/",
                        "photographer": "Ana Souza", "alt": "Praia ao entardecer",
                        "src": {"original": "https://images.pexels.com/photos/123/o.jpeg",
                                "large2x": "https://images.pexels.com/photos/123/l2.jpeg",
                                "medium": "https://images.pexels.com/photos/123/m.jpeg"}}]}
    (photo,) = ms.parse_pexels(data)
    assert photo.id == "pexels:123" and photo.license == "Pexels License"
    assert photo.license_url == "https://www.pexels.com/license/" and photo.credit == "Ana Souza / Pexels"
    assert photo.download_url.endswith("/l2.jpeg") and photo.thumb.endswith("/m.jpeg")


# --- busca --------------------------------------------------------------------------------------


def test_openverse_down_keeps_commons_and_pexels_needs_a_key():
    api = Api(openverse=504)
    result = ms.search(QUERY, orientation="portrait", http=api.client())
    assert result.sources == {"commons": "ok", "openverse": "erro", "pexels": "sem_chave"}
    assert result.cached is False and 0 < len(result.candidates) <= ms.MAX_CANDIDATES
    assert all(c.source == "commons" for c in result.candidates)
    assert "api.pexels.com" not in api.hosts()  # sem chave: nenhuma chamada
    commons = next(r for r in api.requests if r.url.host == "commons.wikimedia.org")
    params = commons.url.params
    assert params["gsrsearch"] == f"{QUERY} filetype:bitmap" and params["gsrnamespace"] == "6"
    assert params["iiurlwidth"] == "500" and "LicenseShortName" in params["iiextmetadatafilter"]
    openverse = next(r for r in api.requests if r.url.host == "api.openverse.org")
    assert openverse.url.params["license"] == "cc0,pdm,by,by-sa"
    assert openverse.url.params["excluded_source"] == "wikimedia"


def test_openverse_without_the_source_filter_when_it_is_refused():
    calls: list[httpx.Request] = []

    def openverse(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "excluded_source" in request.url.params:
            return httpx.Response(400, json={"detail": "excluded_source"})
        return httpx.Response(200, json=load("openverse_search.json"))

    result = ms.search(QUERY, orientation="landscape", http=Api(openverse=openverse).client())
    assert len(calls) == 2 and "excluded_source" not in calls[1].url.params
    assert result.sources["openverse"] == "ok"


def test_merge_alternates_sources_and_prefers_the_video_shape():
    api = Api()
    result = ms.search(QUERY, orientation="portrait", http=api.client())
    sources = [c.source for c in result.candidates]
    assert sources[:4] == ["commons", "openverse", "commons", "openverse"]
    openverse = [c for c in result.candidates if c.source == "openverse"]
    assert openverse[0].title == "Ocean Drive"  # em pé: mais perto do 9:16
    assert len(result.candidates) == ms.MAX_CANDIDATES
    assert len({c.id for c in result.candidates}) == len(result.candidates)


def test_search_cache_refresh_and_stale_cache_when_the_network_fails():
    api = Api()
    first = ms.search(QUERY, orientation="portrait", http=api.client())
    assert first.cached is False and len(api.requests) == 2

    again = ms.search("  miami   beach SKYLINE ", orientation="portrait", http=api.client())
    assert again.cached is True and len(api.requests) == 2  # 2ª chamada sem HTTP
    assert [c.id for c in again.candidates] == [c.id for c in first.candidates]
    assert again.candidates[0].download_url  # o cache guarda o endereço de download

    ms.search(QUERY, orientation="portrait", refresh=True, http=api.client())
    assert len(api.requests) == 4  # refresh refaz

    # vencido e a rede caiu: devolve o vencido
    store = ArtifactStore()
    key = ms._cache_key(QUERY, "portrait", False)
    saved = store.lookup(ms.CACHE_KIND, key)
    old = (datetime.now(UTC) - ms.SEARCH_TTL - timedelta(days=1)).isoformat()
    store.save(ms.CACHE_KIND, key, {**saved, "fetched_at": old}, "media_search")
    down = Api(commons=httpx.ConnectError("sem rede"), openverse=httpx.ConnectError("sem rede"))
    stale = ms.search(QUERY, orientation="portrait", http=down.client())
    assert stale.cached is True and [c.id for c in stale.candidates] == [c.id for c in first.candidates]

    # sem cache e sem rede: lista vazia com as fontes em erro, sem exceção
    empty = ms.search("outra busca", orientation="portrait", http=down.client())
    assert empty.candidates == [] and empty.sources == {"commons": "erro", "openverse": "erro", "pexels": "sem_chave"}


def test_search_with_a_source_down_is_retried_sooner():
    api = Api(openverse=504)
    ms.search(QUERY, orientation="portrait", http=api.client())
    store = ArtifactStore()
    key = ms._cache_key(QUERY, "portrait", False)
    saved = store.lookup(ms.CACHE_KIND, key)
    store.save(ms.CACHE_KIND, key, {**saved, "fetched_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat()},
               "media_search")
    ms.search(QUERY, orientation="portrait", http=api.client())
    assert len(api.requests) == 4  # a fonte que falhou não fica 7 dias sem tentar de novo


def test_offline_search_makes_no_request(monkeypatch):
    monkeypatch.setattr(ms, "new_client", lambda *a, **k: pytest.fail("não pode usar a rede offline"))
    result = ms.search(QUERY, orientation="portrait")
    assert result.sources == {"commons": "offline", "openverse": "offline"} and result.candidates == []
    assert ms.search("   ", orientation="portrait").to_dict() == {"query": "", "candidates": [], "sources": {},
                                                                 "cached": False}


def test_pexels_with_a_key(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "chave-de-teste")
    photos = {"photos": [{"id": 9, "width": 3000, "height": 5000, "url": "https://www.pexels.com/photo/9/",
                          "photographer": "Bia", "src": {"large2x": "https://images.pexels.com/9/l.jpeg"}}]}
    api = Api(openverse=504, pexels=photos)
    result = ms.search(QUERY, orientation="portrait", http=api.client())
    assert result.sources["pexels"] == "ok" and any(c.id == "pexels:9" for c in result.candidates)
    pexels = next(r for r in api.requests if r.url.host == "api.pexels.com")
    assert pexels.headers["Authorization"] == "chave-de-teste"
    assert pexels.url.params["orientation"] == "portrait" and pexels.url.params["per_page"] == "6"


def test_to_dict_has_no_download_url():
    result = ms.search(QUERY, orientation="portrait", http=Api(openverse=504).client())
    data = result.to_dict()
    assert set(data) == {"query", "candidates", "sources", "cached"}
    assert set(data["candidates"][0]) == {"id", "source", "title", "author", "license", "license_url", "page_url",
                                          "thumb", "width", "height", "credit", "attribution_required", "warning"}
    json.dumps(data)


def test_requests_use_the_generic_user_agent():
    client = ms.new_client()
    assert client.headers["User-Agent"] == MEDIA_USER_AGENT
    MEDIA_USER_AGENT.encode("ascii")
    client.close()


def test_commons_needs_the_contact_the_user_typed(monkeypatch):
    monkeypatch.delenv("VULTO_WIKIMEDIA_CONTACT")
    api = Api()
    result = ms.search(QUERY, orientation="portrait", http=api.client())
    assert result.sources["commons"] == "sem_contato" and "commons.wikimedia.org" not in api.hosts()
    assert result.candidates and all(c.source == "openverse" for c in result.candidates)
    with pytest.raises(MediaError, match="contato"):
        ms.lookup("commons:File:Miami skyline from the ocean.jpg", http=api.client())
    assert ms.WikimediaCommonsSource().availability()[0] is False


def test_commons_requests_carry_the_contact_only_to_wikimedia():
    api = Api()
    ms.search(QUERY, orientation="portrait", http=api.client())
    commons = next(r for r in api.requests if r.url.host == "commons.wikimedia.org")
    openverse = next(r for r in api.requests if r.url.host == "api.openverse.org")
    assert commons.headers["User-Agent"] == "Vulto/0.1 (teste@example.org)"
    assert "teste@example.org" not in openverse.headers.get("User-Agent", "")
    assert user_agent_for("https://upload.wikimedia.org/x.jpg") == "Vulto/0.1 (teste@example.org)"
    assert user_agent_for("https://pt.wikipedia.org/wiki/X") == "Vulto/0.1 (teste@example.org)"
    assert user_agent_for("https://example.com/x.jpg") == MEDIA_USER_AGENT
    assert user_agent_for("https://evilwikimedia.org/x") == MEDIA_USER_AGENT
    assert ms.WikimediaCommonsSource().availability() == (True, "ok")


@pytest.mark.parametrize("value", ["ab", "nome sobrenome", "x" * 200, "a(b)@c.d", "joão@exemplo.com", "semponto"])
def test_bad_contact_is_ignored(monkeypatch, value):
    monkeypatch.setenv("VULTO_WIKIMEDIA_CONTACT", value)
    assert wikimedia_contact() is None


# --- um candidato ------------------------------------------------------------------------------


def test_lookup_commons_downloads_the_big_thumbnail():
    api = Api()
    candidate = ms.lookup("commons:File:Miami skyline from the ocean.jpg", http=api.client())
    (request,) = api.requests
    assert request.url.params["titles"] == "File:Miami skyline from the ocean.jpg"
    assert request.url.params["iiurlwidth"] == "3840"
    assert candidate.download_url.startswith("https://thumb.wikimedia.org/") and "/3840px-" in candidate.download_url
    assert candidate.license == "CC BY-SA 4.0" and candidate.credit.startswith("Matthew T Rader /")


def test_lookup_refuses_missing_or_not_free():
    missing = {"batchcomplete": True, "query": {"pages": [{"ns": 6, "title": "File:X.jpg", "missing": True}]}}
    with pytest.raises(MediaError, match="Não achei"):
        ms.lookup("commons:File:X.jpg", http=Api(lookup=missing).client())
    nc = {"query": {"pages": [_page(title="File:NC.jpg", LicenseShortName="CC BY-NC 4.0")]}}
    with pytest.raises(MediaError, match="licença"):
        ms.lookup("commons:File:NC.jpg", http=Api(lookup=nc).client())
    with pytest.raises(MediaError):
        ms.lookup("openverse:../../etc", http=Api().client())
    with pytest.raises(FetchError, match="não respondeu"):
        ms.lookup("commons:File:X.jpg", http=Api(lookup=httpx.ConnectError("sem rede")).client())


def test_lookup_openverse_and_pexels(monkeypatch):
    item = load("openverse_search.json")["results"][0]

    def openverse(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/images/{item['id']}/"
        return httpx.Response(200, json=item)

    candidate = ms.lookup(f"openverse:{item['id']}", http=Api(openverse=openverse).client())
    assert candidate.download_url == item["url"] and candidate.license == "CC BY-SA 2.0"
    with pytest.raises(MediaError, match="chave do Pexels"):
        ms.lookup("pexels:9", http=Api().client())


# --- pré-busca ----------------------------------------------------------------------------------


def test_prefetch_searches_the_photo_suggestions_once():
    seg = Segment(id="seg_1", text={"pt-BR": "Um texto qualquer."})
    scenes = [
        Scene(id="scn_a", segment_id="seg_1", part=0, parts=3, visual=Visual(prompt="x"),
              real=RealHint(kind="photo", query=QUERY)),
        Scene(id="scn_b", segment_id="seg_1", part=1, parts=3, visual=Visual(prompt="y"),
              real=RealHint(kind="photo", query=QUERY.lower())),
        Scene(id="scn_c", segment_id="seg_1", part=2, parts=3, visual=Visual(prompt="z"),
              real=RealHint(kind="official", query="Lucia official art")),
    ]
    doc = ProjectDoc(id="prj_pre", title="t", style_id="misterios-short", segments=[seg], scenes=scenes)
    api = Api(openverse=504)
    assert ms.prefetch(doc, get_style("misterios-short"), http=api.client()) == 2
    assert api.hosts().count("commons.wikimedia.org") == 1  # a 2ª busca igual sai do cache
    assert ms.prefetch(doc, get_style("misterios-short")) == 0  # offline (sem http): nada
