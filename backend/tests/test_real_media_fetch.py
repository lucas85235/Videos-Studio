"""Link colado pelo usuário: anti-SSRF básico, redirecionamentos, limites de tamanho e metadados da página.

Nada de rede: o resolvedor de DNS é trocado por monkeypatch e o httpx usa `MockTransport`.
"""

from __future__ import annotations

import httpx
import pytest

from studio.media import link_preview, safe_http
from studio.media.link_preview import clean_headline, iso_date, parse_meta, read_link, site_label
from studio.media.safe_http import MEDIA_USER_AGENT, BlockedUrl, FetchError, TooLarge, check_url, fetch

PUBLIC_IP = "93.184.216.34"


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    """DNS falso: tudo resolve para um IP público, menos os nomes da tabela."""
    table: dict[str, list[str]] = {"interno.example.com": ["10.0.0.5"], "misto.example.com": [PUBLIC_IP, "127.0.0.1"]}
    monkeypatch.setattr(safe_http, "resolve", lambda host: table.get(host, [PUBLIC_IP]))
    return table


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://example.com/x.jpg", "data:image/png;base64,AAAA", "javascript:alert(1)", "example.com",
    "http://127.0.0.1/", "http://127.1.2.3:80/x", "http://[::1]/", "http://10.1.2.3/", "http://172.16.0.1/",
    "http://172.31.255.1/", "http://192.168.0.10/", "http://169.254.169.254/latest/meta-data/", "http://0.0.0.0/",
    "http://localhost/", "http://LOCALHOST./", "http://painel.localhost/", "http://impressora.local/",
    "http://nas.lan/", "http://api.internal/", "http://roteador.home.arpa/", "http://[::ffff:127.0.0.1]/",
    "http://[::ffff:8.8.8.8]/", "http://[fe80::1]/", "http://[fc00::1]/", "http://224.0.0.1/",
    "http://user:pw@example.com/", "http://user@example.com/", "http://example.com:8080/", "https://example.com:8443/",
    "http://interno.example.com/", "http://misto.example.com/", "http://", "https:///caminho",
])  # fmt: skip
def test_check_url_refuses(url):
    with pytest.raises(BlockedUrl) as info:
        check_url(url)
    assert str(info.value) == "Link não aceito: use um endereço http ou https de um site público."


def test_check_url_accepts_public_sites():
    assert check_url(" https://www.example.com/a b?x=1#topo ") == "https://www.example.com/a%20b?x=1"
    assert check_url("HTTP://example.com:80/") == "http://example.com:80/"
    assert check_url("https://8.8.8.8/img.jpg") == "https://8.8.8.8/img.jpg"


def test_unresolvable_host_is_a_fetch_error(monkeypatch):
    def fail(host):
        raise OSError("sem DNS")

    monkeypatch.setattr(safe_http, "resolve", fail)
    with pytest.raises(FetchError, match="Não consegui abrir o link"):
        check_url("https://nao-existe.example.com/")


def test_fetch_sends_the_generic_user_agent_and_no_personal_headers():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html; charset=UTF-8", "set-cookie": "a=1; Path=/"},
                              text="<html></html>")

    http = safe_http.new_client()
    http._transport = httpx.MockTransport(handler)  # o cliente de verdade, sem rede
    fetch("https://example.com/", max_bytes=1000, accept=safe_http.ACCEPT_HTML, http=http)
    fetched = fetch("https://example.com/2", max_bytes=1000, accept=safe_http.ACCEPT_HTML, http=http)
    http.close()
    assert fetched.content_type == "text/html" and fetched.charset == "utf-8"
    for request in seen:
        assert request.headers["user-agent"] == MEDIA_USER_AGENT
        request.headers["user-agent"].encode("ascii")
        assert request.headers["accept"] == safe_http.ACCEPT_HTML
        assert "accept-language" not in request.headers
    assert "cookie" not in seen[1].headers  # nenhum cookie guardado


def test_redirect_to_a_private_ip_is_blocked_at_the_hop():
    seen: list[str] = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    with pytest.raises(BlockedUrl):
        fetch("https://example.com/foto", max_bytes=1000, accept=safe_http.ACCEPT_IMAGE, http=client(handler))
    assert seen == ["https://example.com/foto"]  # o destino interno nunca é pedido


def test_relative_redirects_are_followed_and_capped():
    def handler(request):
        if request.url.path == "/a":
            return httpx.Response(301, headers={"location": "/b?x=1"})
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")

    fetched = fetch("https://example.com/a", max_bytes=1000, accept=safe_http.ACCEPT_IMAGE, http=client(handler))
    assert fetched.url == "https://example.com/b?x=1" and fetched.content == b"png"

    loop = client(lambda request: httpx.Response(302, headers={"location": "/de-novo"}))
    with pytest.raises(FetchError, match="redireciona demais"):
        fetch("https://example.com/", max_bytes=1000, accept=safe_http.ACCEPT_HTML, http=loop)


def test_size_limits():
    big = b"x" * 5000
    declared = client(lambda r: httpx.Response(200, headers={"content-type": "image/jpeg"}, content=big))
    with pytest.raises(TooLarge, match="passa de"):
        fetch("https://example.com/a.jpg", max_bytes=1000, accept=safe_http.ACCEPT_IMAGE, http=declared)

    def streamed(request):  # sem content-length: corta na leitura
        return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=iter([b"x" * 600, b"x" * 600]))

    with pytest.raises(TooLarge):
        fetch("https://example.com/a.jpg", max_bytes=1000, accept=safe_http.ACCEPT_IMAGE, http=client(streamed))
    page = client(lambda r: httpx.Response(200, headers={"content-type": "text/html"}, content=b"<p>" + big))
    fetched = fetch("https://example.com/", max_bytes=1000, accept=safe_http.ACCEPT_HTML, http=page, truncate=True)
    assert len(fetched.content) == 1000  # página: corta sem erro
    assert str(TooLarge(f"O arquivo passa de {safe_http._mb(safe_http.IMAGE_MAX_BYTES)} MB.")) == "O arquivo passa de 20 MB."


def test_stacked_compression_is_refused_before_reading():
    # "gzip, gzip": 1 KB vira dezenas de MB na memória antes da checagem de tamanho. Recusa sem ler o corpo (antes,
    # descomprimia tudo e só então dava "O arquivo passa de").
    import gzip

    seen: list[httpx.Request] = []
    bomb = gzip.compress(gzip.compress(b"\0" * 20_000_000))

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "image/jpeg", "content-encoding": "gzip, gzip"},
                              content=bomb)

    with pytest.raises(FetchError, match="Não consegui abrir o link.") as info:
        fetch("https://example.com/a.jpg", max_bytes=1000, accept=safe_http.ACCEPT_IMAGE, http=client(handler))
    assert not isinstance(info.value, TooLarge)
    assert seen[0].headers["accept-encoding"] == "identity"

    # uma compressão só (servidor que ignora "identity") ainda funciona, com o limite de tamanho de sempre
    html = b"<html><head><title>Oi</title></head></html>"
    single = client(lambda r: httpx.Response(200, headers={"content-type": "text/html", "content-encoding": "gzip"},
                                             content=gzip.compress(html)))
    assert fetch("https://example.com/", max_bytes=1000, accept=safe_http.ACCEPT_HTML, http=single).content == html


def test_http_errors_become_messages():
    with pytest.raises(FetchError, match="O site respondeu 404."):
        fetch("https://example.com/", max_bytes=10, accept="*/*", http=client(lambda r: httpx.Response(404)))

    def slow(request):
        raise httpx.ReadTimeout("demorou", request=request)

    with pytest.raises(FetchError, match="demorou demais"):
        fetch("https://example.com/", max_bytes=10, accept="*/*", http=client(slow))

    def down(request):
        raise httpx.ConnectError("recusado", request=request)

    with pytest.raises(FetchError, match="Não consegui abrir o link."):
        fetch("https://example.com/", max_bytes=10, accept="*/*", http=client(down))


# --- metadados da página -------------------------------------------------------------------------

NEWS_PAGE = """<!doctype html><html><head>
<meta charset="utf-8">
<title>Título da aba | IGN Brasil</title>
<meta property="og:type" content="article">
<meta property="og:title" content="“Trailer 2 de GTA 6 bate recorde de visualizações” - IGN Brasil">
<meta property="og:site_name" content="IGN Brasil">
<meta property="og:image" content="/imagens/capa.jpg">
<meta property="article:published_time" content="2025-05-06T14:00:00Z">
<meta property="article:author" content="https://www.facebook.com/autor">
<meta name="description" content="Resumo da notícia.">
</head><body><p>texto</p></body></html>"""


def test_parse_meta_open_graph():
    preview = parse_meta(NEWS_PAGE, "https://br.ign.com/gta-6/123/news/trailer")
    assert preview.kind == "page" and preview.is_article
    assert preview.title == "“Trailer 2 de GTA 6 bate recorde de visualizações” - IGN Brasil"
    assert preview.site_name == "IGN Brasil" and preview.published == "2025-05-06"
    assert preview.image_url == "https://br.ign.com/imagens/capa.jpg"  # relativa vira absoluta
    assert preview.author == "" and preview.description == "Resumo da notícia."


def test_parse_meta_twitter_and_json_ld_graph():
    html = """<html><head><title>Anúncio oficial</title>
    <meta name="twitter:image" content="https://cdn.example.com/t.jpg">
    <script type="application/ld+json">{"@context": "https://schema.org", "@graph": [
      {"@type": "WebSite", "name": "Site"},
      {"@type": ["NewsArticle"], "headline": "A Rockstar mostra o mapa", "datePublished": "2025-12",
       "publisher": {"@type": "Organization", "name": "Newswire"}, "author": [{"name": "Equipe"}],
       "image": [{"url": "https://cdn.example.com/ld.jpg"}]}]}</script>
    </head><body><time datetime="2020-01-01">antigo</time></body></html>"""
    preview = parse_meta(html, "https://www.rockstargames.com/newswire/article/1")
    assert preview.title == "A Rockstar mostra o mapa" and preview.site_name == "Newswire"
    assert preview.published == "2025-12" and preview.author == "Equipe" and preview.is_article
    assert preview.image_url == "https://cdn.example.com/t.jpg"  # twitter:image antes do JSON-LD
    bare = parse_meta('<html><head><title> Só o título </title><link rel="image_src" href="//cdn.x.com/i.png">'
                      '<script type="application/ld+json">[{"@type":"BlogPosting","image":"/ld.png"}]</script>'
                      "</head></html>", "https://blog.example.com/p")
    assert bare.title == "Só o título" and bare.image_url == "https://blog.example.com/ld.png" and bare.is_article


@pytest.mark.parametrize("html", [
    "<a " * 5000,
    "<a '" * 5000,
    '<script type="application/ld+json">[' + "{}," * 200_000 + "{}]</script>",
    '<script type="application/ld+json">' + "[" * 100_000 + "</script>",
])  # fmt: skip
def test_parse_meta_is_fast_on_crafted_pages(html):
    # Página montada para travar o parser (tag aberta sem fim, JSON-LD enorme ou aninhado): termina rápido, sem erro.
    import time

    start = time.perf_counter()
    preview = parse_meta(html, "https://x.test/")
    assert time.perf_counter() - start < 1.0
    assert preview.kind == "page"


def test_parse_meta_still_reads_news_with_json_ld_in_the_body():
    html = NEWS_PAGE.replace("<p>texto</p>", '<script type="application/ld+json">{"@type": "NewsArticle", '
                             '"headline": "Outra", "datePublished": "2024-01-02"}</script><p>texto</p>')
    preview = parse_meta(html, "https://br.ign.com/gta-6/123/news/trailer")
    assert preview.title.startswith("“Trailer 2 de GTA 6") and preview.published == "2025-05-06"
    assert preview.is_article
    body_only = parse_meta('<html><body><script type="application/ld+json">{"@type": "NewsArticle", "headline": '
                           '"Só no corpo", "datePublished": "2024-01-02"}</script><time datetime="2023-05-06">x</time>',
                           "https://x.test/")
    assert body_only.title == "Só no corpo" and body_only.published == "2024-01-02" and body_only.is_article


def test_read_link_page_is_a_single_get_with_charset():
    seen: list[httpx.Request] = []
    body = NEWS_PAGE.replace('charset="utf-8"', 'charset="iso-8859-1"').encode("latin-1", "replace")

    def handler(request):
        seen.append(request)
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body)

    preview = read_link("https://br.ign.com/noticia", http=client(handler))
    assert len(seen) == 1  # a og:image não é baixada aqui
    assert preview.kind == "page" and preview.site_name == "IGN Brasil" and "visualiza" in preview.title
    assert "Título da aba" not in preview.title


def test_read_link_image_and_other_types():
    image = client(lambda r: httpx.Response(200, headers={"content-type": "image/png"}, content=b"\x89PNG..."))
    preview = read_link("https://cdn.example.com/arte.png", http=image)
    assert preview.kind == "image" and preview.image == b"\x89PNG..." and preview.content_type == "image/png"
    pdf = client(lambda r: httpx.Response(200, headers={"content-type": "application/pdf"}, content=b"%PDF"))
    with pytest.raises(FetchError, match="não é página nem imagem"):
        read_link("https://example.com/doc.pdf", http=pdf)


def test_clean_headline_site_label_and_iso_date():
    assert clean_headline("Trailer 2 bate recorde - IGN Brasil", "IGN Brasil") == "Trailer 2 bate recorde"
    assert clean_headline("Rockstar Games | Novo trailer de GTA VI", "rockstargames.com") == "Novo trailer de GTA VI"
    assert clean_headline("“Lucia aparece no trailer” — Folha", "Folha") == "Lucia aparece no trailer"
    assert clean_headline('"Entre aspas"') == "Entre aspas"
    long = " ".join(f"palavra{i}" for i in range(20))
    assert clean_headline(long) == " ".join(f"palavra{i}" for i in range(14)) + "…"
    assert clean_headline("Sem site no fim - Outra Coisa", "IGN") == "Sem site no fim - Outra Coisa"
    assert site_label("https://www.rockstargames.com/newswire") == "rockstargames.com"
    assert iso_date("2025-05-06T14:00:00Z") == "2025-05-06"
    assert iso_date("2025-05-06") == "2025-05-06" and iso_date("2025-05") == "2025-05" and iso_date("2025") == "2025"
    assert iso_date("2025-13-01") == "" and iso_date("2025-02-30") == "" and iso_date("ontem") == ""
    assert iso_date("") == "" and iso_date("12025") == ""
    assert link_preview.LinkPreview(url="x", kind="page").image is None
