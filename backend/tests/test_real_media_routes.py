"""Rotas da mídia real com a rede falsa: buscar, anexar (candidato, link, arquivo), editar, tirar e créditos.

`media_search.new_client` e `safe_http.new_client` devolvem clientes com `MockTransport`; o DNS resolve para um IP
público fixo. Nada sai da máquina.
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import RealHint, Scene, Segment, Source, Visual
from studio.media import safe_http
from studio.media.real_media import EDITORIAL_WARNING
from studio.providers import media_search

FIXTURES = Path(__file__).parent / "fixtures" / "media"


def jpeg(width: int = 1600, height: int = 1000, color=(30, 90, 160), fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, fmt)
    return buf.getvalue()


def fixture(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    data.pop("_nota", None)
    return data


NEWS_HTML = """<html><head><meta property="og:type" content="article">
<meta property="og:title" content="Trailer 2 de GTA 6 bate recorde | Notícias Exemplo">
<meta property="og:site_name" content="Notícias Exemplo"><meta property="og:image" content="/img/capa.jpg">
<meta property="article:published_time" content="2025-05-06T10:00:00-03:00"></head><body></body></html>"""
OFFICIAL_HTML = """<html><head><meta property="og:title" content="Lucia"><meta property="og:site_name" content="Rockstar Games">
<meta property="og:image" content="https://cdn.example.com/lucia.jpg"></head></html>"""


class Net:
    """A internet de mentira dos testes."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "commons.wikimedia.org":
            return httpx.Response(200, json=fixture("commons_lookup.json" if "titles" in request.url.params
                                                    else "commons_search.json"))
        if host == "api.openverse.org":
            return httpx.Response(504, text="error code: 504")
        if host == "thumb.wikimedia.org":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=jpeg(3840, 2400))
        if host == "noticias.example.com" and path == "/gta":
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=NEWS_HTML)
        if host == "noticias.example.com" and path == "/img/capa.jpg":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=jpeg(1200, 630))
        if host == "www.rockstargames.com":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=OFFICIAL_HTML)
        if host == "cdn.example.com":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=jpeg(1080, 1350, (200, 40, 40)))
        if host == "fora.example.com":
            return httpx.Response(503)
        return httpx.Response(404)

    def hosts(self) -> list[str]:
        return [r.url.host for r in self.requests]


@pytest.fixture
def net(monkeypatch, isolated_settings):
    isolated_settings.offline = False
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    fake = Net()
    monkeypatch.setattr(media_search, "new_client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(safe_http, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    return fake


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def project(**scene_extra) -> tuple[str, list[str]]:
    doc = service.create_project(title="Mídia real", style_id="misterios-short")
    seg = Segment(id="seg_1", text={"pt-BR": "Miami de noite. O trailer bateu recorde. Uma foto da Lucia."})
    scenes = [
        Scene(id="scn_a", segment_id="seg_1", part=0, parts=3, visual=Visual(prompt="miami at night", seed=1), span=3,
              real=RealHint(kind="photo", query="Miami Beach skyline", note="Foto real de Miami")),
        Scene(id="scn_b", segment_id="seg_1", part=1, parts=3, visual=Visual(prompt="trailer", seed=2), span=4,
              real=RealHint(kind="news", source_ref=1), **scene_extra),
        Scene(id="scn_c", segment_id="seg_1", part=2, parts=3, kind="stat", visual=Visual(reuse_of="scn_a"), span=4),
    ]
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes,
                                 "sources": [Source(title="Notícia", url="https://noticias.example.com/gta")]})
    service.commit(doc.id, doc, "cenas")
    return doc.id, [s.id for s in scenes]


def base(pid: str, sid: str) -> str:
    return f"/api/projects/{pid}/scenes/{sid}"


def test_candidates_use_the_suggestion_query(net, client):
    pid, (a, b, c) = project()
    res = client.get(f"{base(pid, a)}/media/candidates")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["query"] == "Miami Beach skyline" and data["cached"] is False
    assert data["sources"] == {"commons": "ok", "openverse": "erro", "pexels": "sem_chave"}
    assert data["candidates"] and all("download_url" not in cand for cand in data["candidates"])
    again = client.get(f"{base(pid, a)}/media/candidates").json()
    assert again["cached"] is True and net.hosts().count("commons.wikimedia.org") == 1
    other = client.get(f"{base(pid, a)}/media/candidates", params={"q": "  lighthouse  ", "refresh": "true"}).json()
    assert other["query"] == "lighthouse"
    assert client.get(f"{base(pid, c)}/media/candidates").json() == {"query": "", "candidates": [], "sources": {},
                                                                     "cached": False}
    assert client.get(f"{base(pid, 'scn_x')}/media/candidates").status_code == 404
    assert client.get("/api/projects/prj_nada/scenes/scn_a/media/candidates").status_code == 404


def test_attach_a_candidate(net, client, isolated_settings):
    pid, (a, b, c) = project()
    before = service.get_project_row(pid)["current_version"]
    res = client.post(f"{base(pid, a)}/media", json={"via": "candidate",
                                                      "candidate_id": "commons:File:Miami skyline from the ocean.jpg"})
    assert res.status_code == 200, res.text
    body = res.json()
    media = body["media"]
    assert body["version"] == before + 1 and body["job"] is None and body["notice"] is None
    assert media["source"] == "commons" and media["kind"] == "photo" and media["editorial"] is False
    assert media["asset"].startswith(f"uploads/{pid}/media/") and (isolated_settings.data_dir / media["asset"]).is_file()
    assert media["credit"] == "Matthew T Rader / Wikimedia Commons · CC BY-SA 4.0"
    assert media["candidate_id"] == "commons:File:Miami skyline from the ocean.jpg" and media["width"] == 3840
    assert "/3840px-" in media["url"]
    doc = service.get_doc(pid)
    assert doc.scene(a).visual.media.asset == media["asset"] and doc.scene(a).visual.prompt == "miami at night"
    assert service.list_versions(pid)[0]["message"] == "Cena 1: mídia real anexada (Wikimedia Commons)"
    credits = client.get(f"/api/projects/{pid}/media/credits").json()
    assert credits["lang"] == "pt-BR" and credits["heading"] == "Créditos das imagens" and credits["editorial"] is False
    assert credits["lines"] == ["Miami skyline from the ocean — Matthew T Rader — CC BY-SA 4.0 "
                                "(https://creativecommons.org/licenses/by-sa/4.0) — "
                                "https://commons.wikimedia.org/wiki/File:Miami_skyline_from_the_ocean.jpg"]
    assert credits["text"].startswith("Créditos das imagens:\n- Miami skyline")
    assert client.get(f"/api/projects/{pid}/media/credits", params={"lang": "en-US"}).json()["heading"] == "Image credits"


def test_attach_a_news_page_link(net, client):
    pid, (a, b, c) = project()
    res = client.post(f"{base(pid, b)}/media", json={"via": "link", "url": "https://noticias.example.com/gta"})
    assert res.status_code == 200, res.text
    body = res.json()
    media = body["media"]
    assert media["kind"] == "news" and media["source"] == "link" and media["editorial"] is True
    assert media["outlet"] == "Notícias Exemplo" and media["published"] == "2025-05-06"
    assert media["headline"] == {"pt-BR": "Trailer 2 de GTA 6 bate recorde"} and media["asset"]
    assert media["page_url"] == "https://noticias.example.com/gta"
    assert media["url"] == "https://noticias.example.com/img/capa.jpg" and media["credit"] == "Notícias Exemplo"
    assert EDITORIAL_WARNING in body["warnings"]
    assert [str(r.url) for r in net.requests] == ["https://noticias.example.com/gta",
                                                  "https://noticias.example.com/img/capa.jpg"]
    assert service.list_versions(pid)[0]["message"] == "Cena 2: mídia real anexada (link)"
    line = client.get(f"/api/projects/{pid}/media/credits").json()["lines"][0]
    assert line == "Notícias Exemplo, 6 mai. 2025 — “Trailer 2 de GTA 6 bate recorde” — https://noticias.example.com/gta"


def test_news_on_a_card_scene_warns(net, client):
    pid, (a, b, c) = project()
    res = client.post(f"{base(pid, c)}/media", json={"via": "link", "url": "https://noticias.example.com/gta",
                                                      "kind": "news"})
    assert res.status_code == 200, res.text
    assert "O cartão de notícia só aparece em cena do tipo Imagem." in res.json()["warnings"]


def test_attach_direct_image_and_official_page(net, client):
    pid, (a, b, c) = project()
    res = client.post(f"{base(pid, a)}/media", json={"via": "link", "url": "https://cdn.example.com/arte.jpg",
                                                      "kind": None})
    assert res.status_code == 200, res.text
    body = res.json()
    # a cena sugere foto: o tipo do pedido (null) cai na sugestão
    assert body["media"]["kind"] == "photo" and body["media"]["editorial"] is True
    assert body["media"]["credit"] == "cdn.example.com"
    assert "Confira o crédito: escreva o dono do material (ex.: Rockstar Games)." in body["warnings"]
    page = client.post(f"{base(pid, c)}/media", json={"via": "link", "url": "https://www.rockstargames.com/lucia"})
    assert page.status_code == 200, page.text
    media = page.json()["media"]
    assert media["kind"] == "official" and media["credit"] == "Rockstar Games" and media["title"] == "Lucia"


def test_blocked_link_and_site_down(net, client):
    pid, (a, b, c) = project()
    blocked = client.post(f"{base(pid, a)}/media", json={"via": "link", "url": "http://127.0.0.1:8765/api/health"})
    assert blocked.status_code == 422
    assert blocked.json()["detail"] == "Link não aceito: use um endereço http ou https de um site público."
    down = client.post(f"{base(pid, a)}/media", json={"via": "link", "url": "https://fora.example.com/x"})
    assert down.status_code == 502 and down.json()["detail"] == "O site respondeu 503."
    empty = client.post(f"{base(pid, a)}/media", json={"via": "candidate"})
    assert empty.status_code == 422
    assert service.get_doc(pid).scene(a).visual.media is None


def test_upload_patch_delete_and_dismiss(net, client):
    pid, (a, b, c) = project()
    up = client.post(f"{base(pid, a)}/media/upload", files={"file": ("lucia.png", jpeg(1080, 1920, fmt="PNG"), "image/png")},
                     data={"kind": "official", "credit": "Rockstar Games"})
    assert up.status_code == 200, up.text
    media = up.json()["media"]
    assert media["source"] == "upload" and media["editorial"] is True and media["credit"] == "Rockstar Games"
    assert up.json()["warnings"] == [EDITORIAL_WARNING]

    patch = client.patch(f"{base(pid, a)}/media", json={"credit": "Rockstar Games / Newswire", "fit": "contain",
                                                         "focus": "top", "headline": "Manchete", "lang": "en-US"})
    assert patch.status_code == 200, patch.text
    patched = patch.json()
    assert patched["warnings"] == [] and patched["media"]["fit"] == "contain" and patched["media"]["focus"] == "top"
    assert patched["media"]["credit"] == "Rockstar Games / Newswire" and patched["media"]["headline"] == {"en-US": "Manchete"}
    assert service.list_versions(pid)[0]["message"] == "Cena 1: mídia real editada"
    auto = client.patch(f"{base(pid, a)}/media", json={"fit": "auto"}).json()["media"]
    assert auto["fit"] is None and auto["credit"] == "Rockstar Games / Newswire"
    bad = client.patch(f"{base(pid, a)}/media", json={"published": "ontem"})
    assert bad.status_code == 422 and "data inválida" in bad.json()["detail"]

    removed = client.delete(f"{base(pid, a)}/media")
    assert removed.status_code == 200 and set(removed.json()) == {"version", "job", "notice"}
    assert service.get_doc(pid).scene(a).visual.media is None
    assert service.list_versions(pid)[0]["message"] == "Cena 1: mídia real removida"
    assert client.delete(f"{base(pid, a)}/media").status_code == 422  # já sem mídia

    dismissed = client.put(f"{base(pid, a)}/real", json={"real": None})
    assert dismissed.status_code == 200 and service.get_doc(pid).scene(a).real is None
    assert service.list_versions(pid)[0]["message"] == "Cena 1: sugestão de mídia real dispensada"
    edited = client.put(f"{base(pid, a)}/real", json={"real": {"kind": "photo", "query": "Miami"}})
    assert edited.status_code == 200 and service.get_doc(pid).scene(a).real.query == "Miami"
    assert service.list_versions(pid)[0]["message"] == "Cena 1: sugestão de mídia real editada"


def test_upload_refuses_what_is_not_an_image(net, client):
    pid, (a, b, c) = project()
    res = client.post(f"{base(pid, a)}/media/upload", files={"file": ("x.svg", b"<svg></svg>", "image/svg+xml")})
    assert res.status_code == 422 and res.json()["detail"] == "Não é uma imagem que o app lê (use JPG, PNG ou WEBP)."


def test_locked_scene_is_409(net, client):
    pid, (a, b, c) = project(locked=True)
    assert client.post(f"{base(pid, b)}/media", json={"via": "link", "url": "https://noticias.example.com/gta"}
                       ).status_code == 409
    assert client.post(f"{base(pid, b)}/media/upload", files={"file": ("a.jpg", jpeg(), "image/jpeg")}).status_code == 409
    assert client.patch(f"{base(pid, b)}/media", json={"credit": "x"}).status_code == 409
    assert client.delete(f"{base(pid, b)}/media").status_code == 409
    assert client.put(f"{base(pid, b)}/real", json={"real": None}).status_code == 409
    assert net.requests == []  # travada: nada é baixado


def test_rebuild_respects_the_scenes_gate(net, client):
    pid, (a, b, c) = project()
    res = client.post(f"{base(pid, a)}/media/upload", files={"file": ("a.jpg", jpeg(), "image/jpeg")},
                      data={"rebuild": "true"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["job"] is None and body["notice"] == "Edição salva. Aprove as cenas para gerar as imagens e atualizar a prévia."
    plan = client.get(f"/api/projects/{pid}/plan", params={"mode": "preview"}).json()
    assert plan["media_scenes"] == [a] and a not in plan["images_missing"] and "media_missing" not in plan
