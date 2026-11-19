"""Captura de página que é a tela de bloqueio do site (verificação anti-robô, captcha, "Access denied", erro).

QA r4 do prj_gq4krrs11w: a "prova" da GamesBeat no vídeo era a página do Cloudflare ("gamesbeat.com / Executando
verificação de segurança / Confirme que é humano", Ray ID), 12 s sob o chip "GAMESBEAT" e o carimbo BOATO. A recaptura
da rodada 4 (busca do trecho na versão 2) trocou a matéria boa da rodada 3 pela tela de desafio, e nada a recusava.
Tudo offline: as páginas são falsas (`open_page` injetado) e os textos são os das páginas de desafio reais.
"""

from __future__ import annotations

import io
import json
from contextlib import contextmanager

import pytest
from PIL import Image

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import MediaRef, SceneCard, Segment, Source, Visual
from studio.core.project.document import Scene as DocScene
from studio.core.styles.library import get_style
from studio.media import page_capture, safe_http
from studio.media.blocked_page import blocked_reason
from studio.workers.pipeline import media_pool
from studio.workers.visual import proofs

# O texto visível (innerText) da página que entrou no acervo do prj_gq4krrs11w (pool-f00d976cb23f585e.txt).
CLOUDFLARE_PT = """gamesbeat.com
Executando verificação de segurança

Este site utiliza um serviço de segurança para proteção contra bots maliciosos. Esta página é exibida enquanto o site verifica se você não é um bot.

Ray ID: a5cb3340ca80784b
Desempenho e segurança da Cloudflare
Privacidade"""
CLOUDFLARE_EN = """www.example-news.com
Verifying you are human. This may take a few seconds.
www.example-news.com needs to review the security of your connection before proceeding.
Verification successful
Waiting for www.example-news.com to respond...
Ray ID: 8f1c2a3b4d5e6f70
Performance & security by Cloudflare"""
CLOUDFLARE_ES = """noticias.example.es
Verifica que eres humano completando la acción a continuación.
noticias.example.es necesita revisar la seguridad de su conexión antes de continuar.
Ray ID: 8f1c2a3b4d5e6f71
Rendimiento y seguridad de Cloudflare"""
CLOUDFLARE_BLOCKED = """Sorry, you have been blocked
You are unable to access example.com
Why have I been blocked?
This website is using a security service to protect itself from online attacks.
Cloudflare Ray ID: 8f1c2a3b4d5e6f72 • Your IP: Click to reveal • Performance & security by Cloudflare"""
GOOGLE_CAPTCHA = """About this page
Our systems have detected unusual traffic from your computer network. This page checks to see if it's really you
sending the requests, and not a robot.
I'm not a robot
reCAPTCHA"""
AKAMAI = """Access Denied
You don't have permission to access "http://www.example.com/news/" on this server.
Reference #18.6f2c1402.1731960000.1a2b3c4d"""
PERIMETERX = """Before we continue...
Press & Hold to confirm you are
a human (and not a bot).
Reference ID 1a2b3c4d-1234-11ef-9a6b-123456789abc"""
DATADOME = """Please enable JS and disable any ad blocker
DataDome captcha"""
RATE_LIMIT = """429 Too Many Requests
nginx"""
CONSENT = """Before you continue to Example
We use cookies and data to deliver and maintain services
Reject all
Accept all"""
PAYWALL = """Assine para continuar lendo
Você atingiu o limite de matérias gratuitas.
Assine já"""

HEADLINE = "GamesBeat – Grand Theft Auto VI's PC release is slated for Q1 2027"
ARTICLE_ABOUT_CLOUDFLARE = ("Cloudflare outage takes down part of the web\n" + (
    "A Cloudflare configuration error on Tuesday left sites showing the challenge page with a Ray ID and the "
    "message 'Verify you are human' for hours; visitors saw 'Just a moment...' instead of the news. ") * 25)
SHORT_ARTICLE = ("Grand Theft Auto VI's PC release is slated for Q1 2027\nBy Dean Takahashi\n"
                 "Fake 'verify you are human' captcha pages are being used to spread malware, sources said. Ray ID "
                 "logs were shared with us.")
OFFICIAL_SHORT = ("DISPONÍVEL EM 19 DE NOVEMBRO DE 2026\nReserve agora\nPlayStation 5\nXbox Series X|S\n"
                  "Inscreva-se para receber novidades\nThis site is protected by reCAPTCHA and the Google Privacy "
                  "Policy and Terms of Service apply.")


@pytest.mark.parametrize(("title", "text"), [
    ("Um momento…", CLOUDFLARE_PT),
    ("Just a moment...", CLOUDFLARE_EN),
    ("Un momento…", CLOUDFLARE_ES),
    ("Attention Required! | Cloudflare", CLOUDFLARE_BLOCKED),
    ("https://www.google.com/search?q=gta", GOOGLE_CAPTCHA),
    ("Access Denied", AKAMAI),
    ("example.com", PERIMETERX),
    ("example.com", DATADOME),
    ("429 Too Many Requests", RATE_LIMIT),
    ("Before you continue to Example", CONSENT),
    ("Folha", PAYWALL),
    ("Just a moment...", ""),  # o desafio sem texto legível ainda: o título basta
])
def test_challenge_block_and_error_pages_are_detected(title, text):
    assert blocked_reason(title, text, expected=[HEADLINE]) is not None


def test_status_and_blank_page():
    assert "HTTP 403" in blocked_reason("Example", "Bem-vindo", 403)
    assert "HTTP 503" in blocked_reason("Example", "x", 503)
    assert blocked_reason("Example", "texto qualquer da matéria", 200) is None
    assert blocked_reason("", "   ", blank=True) is not None
    assert blocked_reason("", "   ") is None  # captura antiga sem o texto guardado: o texto vazio não conta


def test_real_pages_that_only_mention_the_words_stay():
    """A matéria que só cita "Cloudflare", "Ray ID" ou o captcha falso não é a tela de desafio."""
    assert blocked_reason("Cloudflare outage takes down part of the web - The Verge", ARTICLE_ABOUT_CLOUDFLARE) is None
    assert blocked_reason("GamesBeat", SHORT_ARTICLE, expected=[HEADLINE]) is None  # traz a manchete esperada
    assert blocked_reason("Grand Theft Auto VI – Rockstar Games", OFFICIAL_SHORT) is None  # rodapé com reCAPTCHA
    long_title = "Access Denied: how a Cloudflare bug locked gamers out - Dexerto"
    assert blocked_reason(long_title, "Access denied: o texto da matéria " * 100) is None
    assert blocked_reason("Página não encontrada? Não: matéria sobre 404", "texto " * 500) is None


# --------------------------------------------------------------------------------------------
# Captura ao vivo (página falsa)
# --------------------------------------------------------------------------------------------


def page_png(dark: bool = False) -> bytes:
    img = Image.new("RGB", (2200, 1360), (20, 20, 20) if dark else "white")
    img.paste((0, 0, 0) if not dark else (240, 240, 240), (200, 300, 1500, 380))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class ChallengePage:
    """O desafio do Cloudflare que passa (ou não) depois de `clears_after` leituras, ou depois de recarregar."""

    def __init__(self, clears_after: int | None, *, clears_on_reload: bool = False, status: int | None = None):
        self.reads = 0
        self.clears_after = clears_after
        self.clears_on_reload = clears_on_reload
        self.reloaded = False
        self.shots = 0
        self._status = status

    @property
    def cleared(self) -> bool:
        return (self.clears_after is not None and self.reads > self.clears_after) or (
            self.clears_on_reload and self.reloaded)

    def js(self, expression: str):
        if expression == page_capture.TEXT_JS:
            self.reads += 1
            return ("Grand Theft Auto VI's PC release is slated for Q1 2027\n" + "Texto da matéria. " * 200
                    if self.cleared else CLOUDFLARE_PT)
        if "createTreeWalker" in expression:
            return [True]
        if "getClientRects" in expression:
            return [[[0.1, 0.4, 0.6, 0.05]]]
        if expression in (page_capture.IMAGES_JS, page_capture.BROKEN_JS, page_capture.BOILER_JS):
            return 0
        return True

    def sleep(self, seconds: float) -> None:
        pass

    def screenshot(self) -> bytes:
        self.shots += 1
        return page_png()

    def title(self) -> str:
        return "GamesBeat – GTA VI PC" if self.cleared else "Um momento…"

    def status(self) -> int | None:
        return self._status if not self.cleared else 200

    def reload(self) -> None:
        self.reloaded = True


def opener(page):
    @contextmanager
    def open_page(url, width, height, dpr):
        yield page

    return open_page


@pytest.fixture
def public_dns(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])


URL = "https://gamesbeat.com/grand-theft-auto-vis-pc-release-is-slated-for-q1-2027/"


def test_capture_waits_for_the_challenge_to_pass(public_dns):
    page = ChallengePage(clears_after=2)
    shot = page_capture.capture(URL, [HEADLINE], open_page=opener(page), headline=True)
    assert "Texto da matéria" in shot.text and shot.title.startswith("GamesBeat") and not page.reloaded


def test_capture_reloads_once_and_then_refuses_the_challenge(public_dns):
    page = ChallengePage(clears_after=None, clears_on_reload=True)
    shot = page_capture.capture(URL, [HEADLINE], open_page=opener(page), headline=True)
    assert page.reloaded and "Texto da matéria" in shot.text

    stuck = ChallengePage(clears_after=None, status=403)
    with pytest.raises(page_capture.BlockedPage) as err:
        page_capture.capture(URL, [HEADLINE], open_page=opener(stuck), headline=True)
    assert stuck.reloaded and stuck.shots == 0  # nunca fotografa a tela de bloqueio
    assert "gamesbeat.com" in str(err.value) and "HTTP 403" in err.value.reason


def test_manual_proof_of_a_blocked_page_is_refused(public_dns):
    doc = service.create_project(title="Prova", style_id="curiosidades-longo")
    seg = Segment(id="seg_1", role="hook", text={"pt-BR": "A GamesBeat publicou."})
    doc = doc.model_copy(update={"segments": [seg], "scenes": [
        DocScene(id="scn_0", segment_id="seg_1", part=0, parts=1, visual=Visual(prompt="x"))]})
    service.commit(doc.id, doc, "pronto")
    with pytest.raises(page_capture.BlockedPage):
        proofs.capture_proof(doc.id, "scn_0", URL, ["GTA VI PC"], 0, open_page=opener(ChallengePage(None)))
    assert service.get_doc(doc.id).scene("scn_0").visual.media is None
    rows = proofs.list_captures(doc.id)
    assert [r["status"] for r in rows] == ["falhou"] and "verificação anti-robô" in rows[0]["error"]


# --------------------------------------------------------------------------------------------
# Cache das capturas do acervo
# --------------------------------------------------------------------------------------------


def _write_cached(pid: str, url: str, texts: list[str], entry: dict, text: str | None) -> None:
    done = proofs._pool_capture_file(pid, url, texts, 16 / 9)
    done.parent.mkdir(parents=True, exist_ok=True)
    data_dir = get_settings().data_dir
    (data_dir / entry["asset"]).parent.mkdir(parents=True, exist_ok=True)
    (data_dir / entry["asset"]).write_bytes(page_png())
    done.write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
    if text is not None:
        done.with_suffix(".txt").write_text(text, encoding="utf-8")


def test_blocked_capture_never_enters_the_cache_and_warns(public_dns):
    pid, texts = "prj_bloqueio", [HEADLINE]
    with pytest.raises(proofs.BlockedCapture, match="anti-robô"):
        proofs.capture_clean(pid, URL, texts, 16 / 9, open_page=opener(ChallengePage(None)))
    assert proofs.cached_capture_clean(pid, URL, texts, 16 / 9) is None
    assert "anti-robô" in proofs.capture_rejection(pid, URL, texts, 16 / 9)
    assert URL in proofs.blocked_capture_urls(pid)
    assert not list((get_settings().data_dir / "uploads" / pid / "captures").glob("pool-*.png"))
    # a próxima coleta completa tenta de novo; a página passou: a captura entra e a recusa sai
    entry = proofs.capture_clean(pid, URL, texts, 16 / 9, open_page=opener(ChallengePage(clears_after=0)))
    assert entry["title"].startswith("GamesBeat") and proofs.capture_rejection(pid, URL, texts, 16 / 9) is None
    assert proofs.blocked_capture_urls(pid) == {}


def test_good_capture_of_before_survives_a_blocked_recapture(public_dns):
    """A recaptura da rodada 4 (busca da versão 2) trocou a matéria boa pela tela do Cloudflare: agora a boa fica."""
    pid, texts = "prj_boa", [HEADLINE]
    good = {"asset": f"uploads/{pid}/media/boa.png", "rects": [[0.1, 0.4, 0.6, 0.05]], "headline": True, "find": 1,
            "url": URL, "domain": "gamesbeat.com", "title": "GTA VI PC slated for Q1 2027 | GamesBeat",
            "text": HEADLINE}
    _write_cached(pid, URL, texts, good, "Grand Theft Auto VI's PC release is slated for Q1 2027\n" + "x " * 900)
    assert proofs.capture_clean(pid, URL, texts, 16 / 9, open_page=opener(ChallengePage(None))) == good
    assert proofs.cached_capture_clean(pid, URL, texts, 16 / 9) == good


def test_blocked_capture_already_in_the_cache_is_invalidated(public_dns):
    """O cache do prj_gq4krrs11w: título "Um momento…" e o texto do Cloudflare, gravados antes desta checagem."""
    pid, texts = "prj_velho", [HEADLINE]
    stale = {"asset": f"uploads/{pid}/media/cf.jpg", "rects": [], "width": 2418, "height": 1360, "url": URL,
             "domain": "gamesbeat.com", "title": "Um momento…", "text": HEADLINE, "headline": True,
             "find": page_capture.FIND_VERSION}
    _write_cached(pid, URL, texts, stale, CLOUDFLARE_PT)
    assert proofs.cached_capture_clean(pid, URL, texts, 16 / 9) is None
    assert proofs.blocked_capture_urls(pid) == {URL: proofs.capture_rejection(pid, URL, texts, 16 / 9)}
    page = ChallengePage(clears_after=0)
    entry = proofs.capture_clean(pid, URL, texts, 16 / 9, open_page=opener(page))
    assert page.shots == 1 and entry["asset"] != stale["asset"]


def test_full_gather_leaves_the_blocked_source_out_with_a_warning(public_dns):
    doc = service.create_project(title="GTA 6 no PC", style_id="curiosidades-longo", topic="GTA 6 no PC")
    doc = doc.model_copy(update={"sources": [Source(url=URL, title=HEADLINE)]})
    pool = media_pool.gather(doc, get_style("curiosidades-longo"), full=True, open_page=opener(ChallengePage(None)))
    assert not [i for i in pool.items if i.tier == "capture"]
    assert any("gamesbeat.com ficou sem captura" in w and "Fonte:" in w for w in pool.warnings)
    quick = media_pool.gather(doc, get_style("curiosidades-longo"))  # a coleta rápida do storyboard também avisa
    assert not [i for i in quick.items if i.tier == "capture"]
    assert any("gamesbeat.com ficou sem captura" in w for w in quick.warnings)


# --------------------------------------------------------------------------------------------
# Cena que já tinha a captura de bloqueio
# --------------------------------------------------------------------------------------------


def test_scene_with_the_blocked_capture_becomes_the_data_card_with_the_source(public_dns):
    doc = service.create_project(title="GTA 6 no PC", style_id="curiosidades-longo", topic="GTA 6 no PC")
    seg = Segment(id="seg_1", text={"pt-BR": "No dia 13 de outubro, a GamesBeat publicou que o PC sai no primeiro "
                                             "trimestre de 2027. Se a GamesBeat acertar, o PC chega bem mais rápido. "
                                             "O resto é com a Rockstar."})
    media = MediaRef(kind="capture", source="capture", asset=f"uploads/{doc.id}/media/cf.jpg", url=URL, page_url=URL,
                     title="Um momento… — \"GamesBeat\"", credit="gamesbeat.com", editorial=True, tier="capture",
                     pool_id="p225")
    good = MediaRef(kind="capture", source="capture", asset=f"uploads/{doc.id}/media/ok.jpg",
                    url="https://canaltech.com.br/x", page_url="https://canaltech.com.br/x", title="Canaltech",
                    tier="capture", pool_id="p226")
    card = SceneCard(headline={"pt-BR": "GAMESBEAT · 13/10/2026"}, stamp={"pt-BR": "BOATO"}, source="GamesBeat")
    stat = SceneCard(value={"pt-BR": "T1 2027"}, label={"pt-BR": "janela do PC"}, source="GamesBeat")
    scenes = [
        DocScene(id="scn_0", segment_id="seg_1", part=0, parts=4, kind="image", card=card,
                 visual=Visual(prompt="x", media=media)),
        DocScene(id="scn_1", segment_id="seg_1", part=1, parts=4, kind="stat", card=stat,
                 visual=Visual(reuse_of="scn_0")),
        DocScene(id="scn_2", segment_id="seg_1", part=2, parts=4, kind="image",
                 visual=Visual(prompt="y", media=media.model_copy())),
        DocScene(id="scn_3", segment_id="seg_1", part=3, parts=4, kind="image",
                 visual=Visual(prompt="z", media=good)),
    ]
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes, "sources": [Source(url=URL, title=HEADLINE)]})
    _write_cached(doc.id, URL, [HEADLINE], {"asset": media.asset, "rects": [], "url": URL, "title": "Um momento…",
                                            "text": HEADLINE, "headline": True, "find": 2}, CLOUDFLARE_PT)
    warnings = media_pool.drop_blocked_captures(doc)
    s0, s1, s2, s3 = (doc.scene(f"scn_{i}") for i in range(4))
    assert s0.visual.media is None and s0.kind == "text" and s0.card.source == "GamesBeat"  # cartão com a fonte
    assert s0.card.headline == {"pt-BR": "GAMESBEAT · 13/10/2026"}
    # o cartão do dado não usa a tela de bloqueio de fundo
    assert s1.visual.reuse_of is None and s1.visual.type == "none" and s1.card.source == "GamesBeat"
    assert s2.visual.media is None and s2.kind == "text"
    assert s3.visual.media == good  # a captura boa de outra fonte fica
    assert len(warnings) == 1 and "gamesbeat.com" in warnings[0] and "anti-robô" in warnings[0]
    assert media_pool.drop_blocked_captures(doc) == []
