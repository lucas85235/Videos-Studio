"""Mídia real guardada e enquadrada: normalização do original, recorte com foco, imagem inteira com fundo e
créditos padrão."""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from PIL import Image

from studio.core.project.document import MediaRef
from studio.media import real_media, safe_http
from studio.media.real_media import (
    EDITORIAL_WARNING,
    MediaError,
    anchor_for,
    commons_title,
    default_credit,
    fit_media,
    focus_centering,
    media_from_upload,
    resolve_fit,
    store_image,
)

PID = "prj_media"
FIXTURES = Path(__file__).parent / "fixtures" / "media"


def image_bytes(width: int, height: int, fmt: str = "JPEG", mode: str = "RGB", color=(200, 120, 40), **save) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, (width, height), color).save(buf, fmt, **save)
    return buf.getvalue()


def halves(width: int, height: int) -> Image.Image:
    """Foto deitada: metade esquerda vermelha, direita azul."""
    im = Image.new("RGB", (width, height), (0, 0, 255))
    im.paste((255, 0, 0), (0, 0, width // 2, height))
    return im


def test_store_png_with_transparency_as_jpeg(isolated_settings):
    data = image_bytes(1200, 900, "PNG", "RGBA", (255, 0, 0, 0))
    stored = store_image(data, PID)
    assert stored.rel.startswith(f"uploads/{PID}/media/") and stored.rel.endswith(".jpg")
    assert len(Path(stored.rel).stem) == 16 and stored.warnings == []
    path = isolated_settings.data_dir / stored.rel
    with Image.open(path) as im:
        assert im.format == "JPEG" and im.mode == "RGB" and im.size == (1200, 900)
        r, g, b = im.getpixel((10, 10))
        assert max(abs(r - 17), abs(g - 17), abs(b - 17)) <= 3  # transparência sobre #111111


def test_store_turns_by_exif_and_reuses_the_same_bytes(isolated_settings):
    buf = io.BytesIO()
    im = Image.new("RGB", (400, 600), (10, 200, 10))
    exif = im.getexif()
    exif[0x0112] = 6  # girada 90°
    im.save(buf, "JPEG", exif=exif)
    data = buf.getvalue()
    first = store_image(data, PID)
    assert (first.width, first.height) == (600, 400)
    path = isolated_settings.data_dir / first.rel
    mtime = path.stat().st_mtime_ns
    again = store_image(data, PID)
    assert again.rel == first.rel and path.stat().st_mtime_ns == mtime
    assert len(list(path.parent.glob("*.jpg"))) == 1
    assert first.warnings == ["A imagem é pequena (400 px) e pode sair borrada."]


def test_store_refuses_small_and_unreadable(isolated_settings):
    with pytest.raises(MediaError, match="mínimo 320 px"):
        store_image(image_bytes(300, 900), PID)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900"><rect width="9" height="9"/></svg>'
    for data in (svg, b"qualquer coisa", b""):
        with pytest.raises(MediaError, match="Não é uma imagem que o app lê"):
            store_image(data, PID)
    with pytest.raises(MediaError):
        store_image(image_bytes(900, 900), "../fora")
    assert not (isolated_settings.data_dir / "uploads" / PID / "media").exists()


def test_store_reduces_huge_images_and_reads_gif_and_webp(isolated_settings):
    huge = store_image(image_bytes(4000, 1000, "PNG"), PID)
    assert (huge.width, huge.height) == (3840, 960)
    gif = store_image(image_bytes(900, 900, "GIF", "P", 3), PID)
    webp = store_image(image_bytes(1000, 800, "WEBP"), PID)
    assert (gif.width, webp.width) == (900, 1000)


def test_fit_media_cover_follows_the_focus(tmp_path):
    src = tmp_path / "src.jpg"
    halves(1600, 900).save(src)
    left = fit_media(src, tmp_path / "left.jpg", 360, 640, fit="cover", focus="left")
    right = fit_media(src, tmp_path / "right.jpg", 360, 640, fit="cover", focus="right")
    with Image.open(left) as a, Image.open(right) as b:
        assert a.size == b.size == (360, 640)
        assert a.getpixel((180, 320))[0] > 200 and b.getpixel((180, 320))[2] > 200  # vermelho × azul no meio


def test_fit_media_contain_has_the_whole_photo_on_a_blurred_background(tmp_path):
    src = tmp_path / "src.jpg"
    halves(1600, 900).save(src)
    out = fit_media(src, tmp_path / "contain.jpg", 360, 640, fit="contain", anchor_y=0.4)
    with Image.open(out) as im:
        assert im.size == (360, 640)
        # a foto inteira (360 × ~203) centrada em 40% da altura: as duas metades aparecem
        y = round(640 * 0.4)
        assert im.getpixel((40, y))[0] > 200 and im.getpixel((320, y))[2] > 200
        top = im.getpixel((180, 10))
        assert max(top) < 160  # fundo escurecido


def test_resolve_fit_and_helpers():
    def media(**kw):
        return MediaRef(asset="uploads/p/media/x.jpg", **kw)

    assert resolve_fit(media(width=1920, height=1080), 1080, 1920) == "contain"  # 16:9 num vídeo 9:16
    assert resolve_fit(media(width=1200, height=1600), 1080, 1920) == "cover"  # 3:4 num 9:16
    assert resolve_fit(media(width=1920, height=1080), 1920, 1080) == "cover"
    assert resolve_fit(media(width=1920, height=1080, fit="cover"), 1080, 1920) == "cover"  # manual vale
    assert resolve_fit(media(width=1200, height=1600, fit="contain"), 1080, 1920) == "contain"
    assert resolve_fit(media(kind="news", width=1920, height=1080), 1080, 1920) == "cover"
    assert resolve_fit(media(), 1080, 1920) == "cover"  # sem tamanho
    assert focus_centering("left") == (0.22, 0.5) and focus_centering(None) == (0.5, 0.5)
    assert focus_centering("bottom_right") == (0.78, 0.78) and focus_centering("x") == (0.5, 0.5)
    assert anchor_for(1080, 1920, "center") == 0.40 and anchor_for(1080, 1920, "bottom") == 0.40
    assert anchor_for(1080, 1920, "top") == 0.60 and anchor_for(1920, 1080, "top") == 0.5


def test_default_credit_per_source():
    assert default_credit("commons", author="Fulano", license="CC BY-SA 4.0") == "Fulano / Wikimedia Commons · CC BY-SA 4.0"
    assert default_credit("commons", license="CC0") == "Wikimedia Commons · CC0"
    assert default_credit("openverse", author="Bia", license="CC BY 2.0", provider="Flickr") == "Bia / Flickr · CC BY 2.0"
    assert default_credit("openverse", author="Bia", license="CC0") == "Bia / Openverse · CC0"
    assert default_credit("pexels", author="Ana") == "Ana / Pexels"
    assert default_credit("link", site="rockstargames.com") == "rockstargames.com"
    assert default_credit("upload") == ""
    long = default_credit("commons", author="Nome Muito Comprido " * 10, license="CC BY-SA 4.0")
    assert len(long) <= 90 and long.endswith("/ Wikimedia Commons · CC BY-SA 4.0") and "…" in long


def test_upload_rules(isolated_settings):
    with pytest.raises(MediaError, match="25 MB"):
        media_from_upload(b"0" * (real_media.UPLOAD_MAX_BYTES + 1), PID, kind="photo")
    photo, warnings = media_from_upload(image_bytes(1200, 900), PID, kind="photo")
    assert photo.source == "upload" and photo.editorial is False and warnings == []
    official, warnings = media_from_upload(image_bytes(1200, 901), PID, kind="official")
    assert official.editorial is True and official.credit == ""
    assert warnings == ["Escreva o crédito (ex.: Rockstar Games).", EDITORIAL_WARNING]
    credited, warnings = media_from_upload(image_bytes(1200, 902), PID, kind="official", credit="  Rockstar   Games ",
                                           editorial=False)
    assert credited.credit == "Rockstar Games" and credited.editorial is False and warnings == []


def test_commons_title_from_links():
    assert commons_title("https://commons.wikimedia.org/wiki/File:Miami_skyline_from_the_ocean.jpg") == \
        "File:Miami skyline from the ocean.jpg"
    assert commons_title("https://upload.wikimedia.org/wikipedia/commons/c/c6/Miami_skyline.jpg?x=1") == \
        "File:Miami skyline.jpg"
    assert commons_title("https://thumb.wikimedia.org/wikipedia/commons/thumb/9/95/A%C3%A7a%C3%AD.jpg/500px-A.jpg") == \
        "File:Açaí.jpg"
    assert commons_title("https://upload.wikimedia.org/wikipedia/en/a/ab/Poster.jpg") is None  # uso justo da wiki
    assert commons_title("https://commons.wikimedia.org/wiki/Category:Miami") is None
    assert commons_title("https://example.com/File:x.jpg") is None
    # Wikipedia e Commons: visualizador de mídia, páginas de arquivo noutros idiomas e Special:FilePath
    for url in ("https://en.wikipedia.org/wiki/Lighthouse#/media/File:Other.jpg",
                "https://pt.wikipedia.org/wiki/Ficheiro:Other.jpg",
                "https://pt.wikipedia.org/wiki/Farol#/media/Ficheiro:Other.jpg",
                "https://commons.wikimedia.org/wiki/Special:FilePath/Other.jpg",
                "https://commons.wikimedia.org/wiki/Category:X#/media/File:Other.jpg",
                "https://en.wikipedia.org/wiki/Image:Other.jpg"):
        assert commons_title(url) == "File:Other.jpg", url
    assert commons_title("https://en.m.wikipedia.org/wiki/File:A%C3%A7a%C3%AD.jpg") == "File:Açaí.jpg"
    assert commons_title("https://en.wikipedia.org/wiki/Lighthouse") is None  # artigo: não é um arquivo
    assert commons_title("https://en.wikipedia.org/wiki/Lighthouse#Hist%C3%B3ria") is None
    assert commons_title("https://en.wikipedia.org.example.com/wiki/File:x.jpg") is None


# --- links da Wikipedia e do Commons no anexo por link (rede de mentira) ------------------------------------------

SKYLINE = "Miami_skyline_from_the_ocean.jpg"
COMMONS_FILE = f"https://upload.wikimedia.org/wikipedia/commons/c/c6/{SKYLINE}"
ARTICLE_HTML = f"""<html><head><meta property="og:title" content="Miami - Wikipedia">
<meta property="og:image" content="https://upload.wikimedia.org/wikipedia/commons/thumb/c/c6/{SKYLINE}/1200px-{SKYLINE}">
<script type="application/ld+json">{{"@type": "Article", "name": "Miami", "publisher": {{"name": "Wikimedia Foundation"}}}}</script>
</head><body></body></html>"""
POSTER_HTML = """<html><head><meta property="og:title" content="File:Poster.jpg - Wikipedia">
<meta property="og:image" content="https://upload.wikimedia.org/wikipedia/en/a/ab/Poster.jpg"></head></html>"""


class WikiNet:
    """A Wikipedia, o Commons e um encurtador de mentira."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "commons.wikimedia.org" and path == "/w/api.php":
            if request.url.params["titles"] == "File:Miami skyline from the ocean.jpg":
                data = json.loads((FIXTURES / "commons_lookup.json").read_text(encoding="utf-8"))
                return httpx.Response(200, json=data)
            title = request.url.params["titles"]
            return httpx.Response(200, json={"query": {"pages": [{"ns": 6, "title": title, "missing": True}]}})
        if host in ("upload.wikimedia.org", "thumb.wikimedia.org"):
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=image_bytes(1600, 1000))
        if host == "curto.example.com":
            return httpx.Response(302, headers={"location": COMMONS_FILE})
        if host == "en.wikipedia.org" and path == "/wiki/Miami":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=ARTICLE_HTML)
        if host == "en.wikipedia.org" and path == "/wiki/File:Poster.jpg":
            return httpx.Response(200, headers={"content-type": "text/html"}, text=POSTER_HTML)
        return httpx.Response(404)

    def paths(self) -> list[str]:
        return [f"{r.url.host}{r.url.path}" for r in self.requests]


@pytest.fixture
def wiki(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    fake = WikiNet()
    return fake, httpx.Client(transport=httpx.MockTransport(fake))


def _attach(link: str, http: httpx.Client, kind=None):
    return real_media.media_from_link(link, PID, kind=kind, lang="pt-BR", http=http)


def test_media_viewer_link_attaches_the_chosen_commons_file(wiki):
    net, http = wiki
    media, _ = _attach(f"https://en.wikipedia.org/wiki/Lighthouse#/media/File:{SKYLINE}", http)
    assert media.source == "commons" and media.license == "CC BY-SA 4.0" and media.editorial is False
    assert media.author and "Wikimedia Commons" in media.credit
    assert "en.wikipedia.org/wiki/Lighthouse" not in net.paths()  # a página do artigo nem é aberta


def test_link_that_redirects_to_a_commons_image_gets_the_license(wiki):
    net, http = wiki
    media, warnings = _attach("https://curto.example.com/abc", http)
    assert media.source == "commons" and media.license == "CC BY-SA 4.0" and media.editorial is False
    assert EDITORIAL_WARNING not in warnings


def test_wikipedia_article_link_uses_the_lead_commons_image(wiki):
    _, http = wiki
    media, _ = _attach("https://en.wikipedia.org/wiki/Miami", http)
    assert media.kind == "photo" and media.source == "commons" and media.license == "CC BY-SA 4.0"
    news, _ = _attach("https://en.wikipedia.org/wiki/Miami", http, kind="news")  # pediu notícia: fica notícia
    assert news.kind == "news" and news.source == "link"


def test_wikipedia_only_file_is_read_from_its_file_page(wiki):
    net, http = wiki
    media, warnings = _attach("https://en.wikipedia.org/wiki/Cinema#/media/File:Poster.jpg", http)
    assert media.source == "link" and media.editorial is True and media.kind == "official"
    assert media.url == "https://upload.wikimedia.org/wikipedia/en/a/ab/Poster.jpg"
    assert "en.wikipedia.org/wiki/File:Poster.jpg" in net.paths() and "en.wikipedia.org/wiki/Cinema" not in net.paths()
    assert EDITORIAL_WARNING in warnings
    with pytest.raises(MediaError, match="Não achei"):
        _attach("https://commons.wikimedia.org/wiki/File:Poster.jpg", http)  # no Commons: não acha, sem plano B
