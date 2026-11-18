"""Captura de página com marca-texto (prova na tela). Origem: `motor/passos/shot_helper.py` e `shot_views.py` do canais.

- `capture`: abre a página num navegador headless (Playwright, opcional), limpa banners, rola até o trecho de foco
  (a 42% da altura), mede os retângulos de cada trecho e fotografa a janela (1100 x 680, DPR 2).
- Funções puras (sem navegador): `ink_mask`, `is_dark`, `view_for` (a janela que mostra as linhas inteiras), `mark`
  (o marca-texto amarelo) e `crop_to_view` (o recorte na proporção do vídeo, sem cortar a janela do trecho).

O Playwright não entra no `pyproject.toml` (ADR 0003, decisão 4): é detectado na hora e usa o Edge do Windows.
"""

from __future__ import annotations

import importlib.util
import json
from collections.abc import Callable, Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import urlsplit

import numpy as np
from PIL import Image

from studio.media import safe_http

WIDTH, HEIGHT, DPR = 1100, 680, 2
SETTLE_S = 2.5
FOCUS_POS = 0.42  # altura do trecho de foco na tela (`__SHOT_POS`)
MAX_PHRASES = 6
HIGHLIGHT = (0xFF, 0xE1, 0x4D)  # #FFE14D
DARK_ALPHA = 0.45  # página escura: camada de 45% (multiply some no fundo escuro)
HIGHLIGHT_PAD = 0.15  # folga do marca-texto: 15% da altura da linha
BROWSER_MISSING = ("Para capturar páginas, instale o Playwright no ambiente do Vulto: `cd backend` e "
                   "`uv pip install playwright` (usa o Edge do Windows, sem baixar navegador).")
BROWSER_FAILED = ("O Playwright está instalado, mas nenhum navegador abriu (Edge, Chrome ou o Chromium do Playwright). "
                  "Instale o Edge ou rode `uv run playwright install chromium` na pasta backend.")

# Acha os trechos na página (texto normalizado: aspas, traços e espaços), esconde barras fixas e banners e rola até o
# trecho de foco ficar a `__SHOT_POS` da altura. Devolve, para cada trecho, se ele foi achado.
# `headline` (captura das fontes do roteiro): o trecho é o título da fonte, que quase nunca está igual na página
# ("GamesBeat – Título", "Cuidado: ..." contra "Cuidado! ..."). Sem o texto exato, vale a manchete da página (o `h1`) e,
# sem ela, a parte mais longa do título sem o nome do veículo; sem nenhum retângulo, o enquadramento não sabe onde está
# a manchete e o zoom da edição a corta.
FIND_JS = r"""
((phrases, focus, scale, headline) => {
  const norm = s => s.replace(/[‘’ʼ]/g, "'").replace(/[“”]/g, '"').replace(/[‐-―−]/g, '-').replace(/\s/g, ' ');
  // limpa barras fixas, banners de cookie e overlays
  const BODY0 = document.body || document.getElementsByTagName('body')[0];
  for (const el of document.querySelectorAll('body *')) {
    const cs = getComputedStyle(el);
    if (cs.position === 'fixed' || cs.position === 'sticky') el.style.setProperty('display', 'none', 'important');
  }
  for (const sel of ['[id*=cookie]','[class*=cookie]','[id*=consent]','[class*=consent]','[class*=paywall]','[id*=gateway]','[class*=gateway]','[aria-modal=true]','dialog']) {
    document.querySelectorAll(sel).forEach(e => { if (e !== BODY0 && !e.contains(document.querySelector('h1')) && !phrases.some(p => norm(e.innerText || '').replace(/ +/g, ' ').toLowerCase().includes(norm(p).replace(/ +/g, ' ').toLowerCase()))) e.style.setProperty('display', 'none', 'important'); });
  }
  document.documentElement.style.setProperty('overflow', 'auto', 'important');
  const BODY = document.body || document.getElementsByTagName('body')[0] || document.documentElement;
  BODY.style.setProperty('overflow', 'visible', 'important');
  if (scale && scale !== 1) BODY.style.zoom = scale;
  const walker = document.createTreeWalker(BODY, NodeFilter.SHOW_TEXT, {acceptNode: n => {
    const p = n.parentElement; if (!p || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE)$/.test(p.tagName)) return NodeFilter.FILTER_REJECT;
    return p.getClientRects().length ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_REJECT; }});
  let text = '', map = [], prev = false;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const s = n.nodeValue;
    for (let i = 0; i < s.length; i++) {
      const c = norm(s[i]);
      if (c === ' ') { if (prev) continue; prev = true; } else prev = false;
      text += c.toLowerCase(); map.push([n, i]);
    }
  }
  const key = s => norm(s).replace(/ +/g, ' ').trim().toLowerCase();
  const rangeOf = q => {
    const i = q ? text.indexOf(q) : -1;
    if (i < 0) return null;
    const r = document.createRange();
    r.setStart(map[i][0], map[i][1]); r.setEnd(map[i + q.length - 1][0], map[i + q.length - 1][1] + 1);
    return r;
  };
  // pedaço do título achado fora do rodapé, do menu e de formulário: "Rockstar Games" do título casava com "muito mais
  // da Rockstar Games" na caixa de newsletter do rodapé, e a captura do site oficial virava o rodapé
  const BOILER = 'footer, nav, form, aside, [role=contentinfo], [role=navigation], [class*=footer i], [id*=footer i], ' +
    '[class*=newsletter i], [id*=newsletter i], [class*=subscribe i], [class*=signup i]';
  const rangeOutside = q => {
    for (let i = q ? text.indexOf(q) : -1; i >= 0; i = text.indexOf(q, i + 1)) {
      const el = map[i][0].parentElement;
      if (el && el.closest(BOILER)) continue;
      const r = document.createRange();
      r.setStart(map[i][0], map[i][1]); r.setEnd(map[i + q.length - 1][0], map[i + q.length - 1][1] + 1);
      return r;
    }
    return null;
  };
  // manchete: o primeiro h1 visível com texto de título (h1 curto costuma ser o logo do site)
  const head = [...document.querySelectorAll('h1, [itemprop=headline]')].find(e =>
    e.getClientRects().length && (e.innerText || '').trim().length >= 15 && !e.closest(BOILER));
  window.__ranges = phrases.map(p => {
    const q = key(p);
    const exact = rangeOf(q);
    if (exact || !headline) return exact;
    if (head) { const r = document.createRange(); r.selectNodeContents(head); return r; }
    const parts = q.split(/\s+[-|·]\s+|:\s+/).map(s => s.trim()).filter(s => s.length >= 12)
      .sort((a, b) => b.length - a.length);
    for (const part of parts) { const r = rangeOutside(part); if (r) return r; }
    return null;
  });
  const f = window.__ranges[focus] || window.__ranges.find(Boolean);
  if (f) {
    document.documentElement.style.setProperty('scroll-behavior', 'auto', 'important');
    BODY.style.setProperty('scroll-behavior', 'auto', 'important');
    const mk = document.createElement('span'); const c = f.cloneRange(); c.collapse(true); c.insertNode(mk);
    mk.scrollIntoView({block: 'center', inline: 'nearest', behavior: 'instant'}); mk.remove();
    const b = f.getBoundingClientRect();
    const target = Math.max(0, b.top + window.scrollY - innerHeight * (window.__SHOT_POS ?? 0.42));  // altura do trecho na tela
    (document.scrollingElement || document.documentElement).scrollTop = target;
    window.scrollTo({top: target, behavior: 'instant'});
  }
  return window.__ranges.map(r => !!r);
})(%s, %d, %s, %s)
"""

# Retângulos de cada trecho (uma caixa por linha, juntando as caixas da mesma linha), de 0 a 1 em relação à janela.
RECTS_JS = r"""
(() => (window.__ranges || []).map(r => {
  if (!r) return null;
  const out = [];
  for (const b of r.getClientRects()) {
    if (b.width < 2 || b.height < 4) continue;
    const last = out[out.length - 1];
    if (last && Math.abs(last[1] - b.top) < b.height * 0.5) { const x2 = Math.max(last[0] + last[2], b.right); last[0] = Math.min(last[0], b.left); last[2] = x2 - last[0]; last[3] = Math.max(last[3], b.height); }
    else out.push([b.left, b.top, b.width, b.height]);
  }
  return out.map(([x, y, w, h]) => [x / innerWidth, y / innerHeight, w / innerWidth, h / innerHeight]);
}))()
"""

# Antes de fotografar: esconde barra do Wayback, banners de cookies/consentimento e anúncios (no canais, o banner de
# cookies e os anúncios apareciam no quadro e o enquadramento abria a página inteira por causa deles).
CLEAN_JS = r"""(() => {
  const st = document.createElement('style');
  st.textContent = `
    #wm-ipp-base, #wm-ipp, #donato, #onetrust-consent-sdk, #onetrust-banner-sdk, #CybotCookiebotDialog, #usercentrics-root,
    .fc-consent-root, .qc-cmp2-container, #truste-consent-track, .osano-cm-window, #hs-eu-cookie-confirmation, #cookiescript_injected,
    body [id*="cookie" i]:not(html):not(body), body [class*="cookie-banner" i], body [class*="cookieBanner" i], body [class*="cookie-consent" i],
    body [class*="consent-banner" i], body [aria-label*="cookie" i], body [aria-label*="consent" i],
    iframe, ins.adsbygoogle, [id^="google_ads"], [id*="div-gpt" i], [class*="advert" i], [class*="ad-slot" i], [class*="adslot" i],
    [class*="ad-container" i], [class*="ads-container" i], [class*="dfp" i], [id*="dfp" i], [class*="sticky-ad" i]
    { display: none !important; }
    html, body { filter: none !important; opacity: 1 !important; overflow: auto !important; }`;
  document.head.appendChild(st);
  document.querySelectorAll('body *').forEach((el) => {
    const cs = getComputedStyle(el);
    if ((cs.position === 'fixed' || cs.position === 'sticky') && el.getBoundingClientRect().height > 0) {
      const t = (el.innerText || '').toLowerCase();
      if (/cookie|consent|privacy|aceptar|accept/.test(t) && t.length < 1500) el.style.setProperty('display', 'none', 'important');
    }
  });
  return true; })()"""

# Espaço de anúncio que o CSS não pega (no Vulto, a Canaltech saía com um bloco cinza "PUBLICIDADE" em cima da
# manchete): a caixa que só tem o rótulo some inteira, e os marcadores comuns de anúncio também, desde que não levem a
# matéria junto (h1, article, main) nem texto de verdade. Roda de novo depois da rolagem (anúncio que entra depois).
ADS_JS = r"""(() => {
  const LABEL = /^(publicidade|publicidad|publicit[ée]|an[úu]ncios?|advertisements?|advertising|ads?|sponsored|patrocinado|conte[úu]do patrocinado|anzeige|werbung)$/i;
  const keep = (el) => el === document.body || el === document.documentElement ||
    el.matches('h1, article, main') || el.querySelector('h1, article, main, [itemprop=articleBody]');
  const hide = (el) => el.style.setProperty('display', 'none', 'important');
  let n = 0;
  for (const el of document.querySelectorAll('body *')) {
    if (el.childElementCount || !LABEL.test((el.textContent || '').trim()) || keep(el)) continue;
    let box = el;
    while (box.parentElement && !keep(box.parentElement) && LABEL.test((box.parentElement.innerText || '').trim())) box = box.parentElement;
    hide(box); n++;
  }
  const marked = '[data-ad], [data-ad-slot], [data-adunit], [data-ad-unit], [data-google-query-id], [id^="ad-" i], ' +
    '[id^="ad_" i], [class^="ad-" i], [class*=" ad-" i], [class*="publicidade" i], [id*="publicidade" i], ' +
    '[class*="ad-wrapper" i], [class*="ad-banner" i], [class*="banner-ad" i]';
  for (const el of document.querySelectorAll(marked)) {
    if (keep(el) || (el.innerText || '').trim().length > 300) continue;
    hide(el); n++;
  }
  return n; })()"""

# Imagens na tela: a preguiçosa (`loading=lazy`, `data-src`) carrega já. Devolve quantas ainda não terminaram (a
# GamesBeat saía com a caixa da foto vazia e o ícone de imagem quebrada).
IMAGES_JS = r"""(() => {
  let pending = 0;
  for (const img of document.images) {
    const b = img.getBoundingClientRect();
    if (b.width < 4 || b.height < 4 || b.bottom < 0 || b.top > innerHeight || b.right < 0 || b.left > innerWidth) continue;
    if (img.loading === 'lazy') img.loading = 'eager';
    const src = img.getAttribute('src') || '';
    const lazy = img.getAttribute('data-src') || img.getAttribute('data-lazy-src') || img.getAttribute('data-original');
    if (lazy && (!src || src.startsWith('data:'))) img.setAttribute('src', lazy);
    const lazySet = img.getAttribute('data-srcset');
    if (lazySet && !img.getAttribute('srcset')) img.setAttribute('srcset', lazySet);
    if (!img.complete) pending++;
  }
  return pending; })()"""

# Logo antes da foto: a imagem que terminou sem carregar (ícone de imagem quebrada) some com a caixa vazia em volta,
# sem mexer no layout (os retângulos já medidos continuam valendo).
BROKEN_JS = r"""(() => {
  let n = 0;
  for (const img of document.images) {
    const b = img.getBoundingClientRect();
    if (b.width < 4 || b.height < 4 || b.bottom < 0 || b.top > innerHeight) continue;
    if (!img.complete || img.naturalWidth > 0) continue;
    const src = img.currentSrc || img.getAttribute('src') || '';
    if (/\.svg([?#]|$)/i.test(src) || src.startsWith('data:image/svg')) continue;  // SVG sem tamanho próprio não é quebrado
    let box = img;
    while (box.parentElement && box.parentElement !== document.body && !(box.parentElement.innerText || '').trim()
           && box.parentElement.querySelectorAll('img').length === 1) box = box.parentElement;
    box.style.setProperty('visibility', 'hidden', 'important'); n++;
  }
  return n; })()"""

IMAGE_WAIT_TRIES = 8
IMAGE_WAIT_S = 0.5

TEXT_JS = "(() => (document.body && document.body.innerText) || '')()"

FIND_VERSION = 2
"""Versão da busca do trecho (`FIND_JS`). A 2 não marca o título achado no rodapé, no menu ou na caixa de newsletter;
a captura guardada com a 1 é refeita na próxima coleta completa (`proofs.capture_clean`)."""

# Quanto da tela (0 a 1) o rodapé, a caixa de newsletter ou o formulário de inscrição ocupam no quadro fotografado. A
# página que não montou o conteúdo (site em JS pesado) sai só com o rodapé: "Inscreva-se para receber muito mais da
# Rockstar Games" no lugar do site oficial (QA do prj_gq4krrs11w, 0:30 e 1:33).
BOILER_JS = r"""(() => {
  const SEL = 'footer, [role=contentinfo], [class*=footer i], [id*=footer i], [class*=newsletter i], ' +
    '[id*=newsletter i], [class*=subscribe i], [class*=signup i]';
  const W = innerWidth, H = innerHeight;
  let area = 0;
  for (const el of document.querySelectorAll(SEL)) {
    if (el.parentElement && el.parentElement.closest(SEL)) continue;  // só o bloco de fora
    const b = el.getBoundingClientRect();
    const w = Math.max(0, Math.min(W, b.right) - Math.max(0, b.left));
    const h = Math.max(0, Math.min(H, b.bottom) - Math.max(0, b.top));
    area += w * h;
  }
  return Math.min(1, area / Math.max(1, W * H));
})()"""
BOILER_MAX_SHARE = 0.5
"""Acima disto o quadro é o rodapé da página, não a página."""


class CaptureError(RuntimeError):
    """Captura recusada ou falha (navegador ausente, página não abriu): mensagem em português."""


class Page(Protocol):
    """O que a captura precisa de uma página aberta (Playwright ou, nos testes, uma página falsa)."""

    def js(self, expression: str) -> Any: ...

    def sleep(self, seconds: float) -> None: ...

    def screenshot(self) -> bytes: ...

    def title(self) -> str: ...


OpenPage = Callable[[str, int, int, int], AbstractContextManager[Page]]


@dataclass
class CaptureResult:
    url: str
    domain: str
    title: str
    png: bytes
    text: str
    found: list[bool] = field(default_factory=list)
    rects: list[list[list[float]] | None] = field(default_factory=list)
    width: int = 0
    height: int = 0
    boiler_share: float = 0.0
    """Quanto do quadro é rodapé ou caixa de newsletter (`BOILER_JS`; 0 quando a página não respondeu)."""

    @property
    def boilerplate_only(self) -> bool:
        """O quadro é o rodapé da página (o conteúdo não montou)."""
        return self.boiler_share > BOILER_MAX_SHARE


# --------------------------------------------------------------------------------------------
# Navegador (opcional)
# --------------------------------------------------------------------------------------------


def _has_playwright() -> bool:
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def browser_status() -> dict[str, Any]:
    """{ok, message}: se a captura de página está disponível neste ambiente."""
    if _has_playwright():
        return {"ok": True, "message": "Captura de página disponível (Playwright com o Edge do Windows)."}
    return {"ok": False, "message": BROWSER_MISSING}


class _PlaywrightPage:
    def __init__(self, page: Any) -> None:
        self._page = page

    def js(self, expression: str) -> Any:
        return self._page.evaluate(expression)

    def sleep(self, seconds: float) -> None:
        self._page.wait_for_timeout(int(seconds * 1000))

    def screenshot(self) -> bytes:
        try:
            self._page.bring_to_front()
        except Exception:  # noqa: BLE001 - só ajuda a pintar a página
            pass
        self._page.wait_for_timeout(400)
        return self._page.screenshot(type="png", timeout=40_000)

    def title(self) -> str:
        return self._page.title() or ""


@contextmanager
def playwright_page(url: str, width: int, height: int, dpr: int) -> Iterator[Page]:
    """Abre a página no Edge do Windows, depois no Chrome, depois no Chromium do Playwright."""
    if not _has_playwright():
        raise CaptureError(BROWSER_MISSING)
    from playwright.sync_api import sync_playwright  # opcional: só existe quando o usuário instalou

    with sync_playwright() as p:
        browser = None
        for channel in ("msedge", "chrome", None):
            try:
                browser = p.chromium.launch(channel=channel) if channel else p.chromium.launch()
                break
            except Exception:  # noqa: BLE001 - tenta o próximo navegador
                continue
        if browser is None:
            raise CaptureError(BROWSER_FAILED)
        try:
            context = browser.new_context(viewport={"width": width, "height": height}, device_scale_factor=dpr)
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45_000)
                try:
                    page.wait_for_load_state("load", timeout=20_000)
                except Exception:  # noqa: BLE001 - página que nunca termina de carregar: segue com o que tem
                    pass
            except Exception as exc:  # noqa: BLE001
                raise CaptureError(f"A página não abriu: {str(exc).splitlines()[0][:200]}") from exc
            yield _PlaywrightPage(page)
        finally:
            browser.close()


def domain_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _wait_images(page: Page) -> None:
    """Espera as imagens da tela terminarem de carregar (no máximo `IMAGE_WAIT_TRIES` × `IMAGE_WAIT_S`)."""
    for _ in range(IMAGE_WAIT_TRIES):
        pending = page.js(IMAGES_JS)
        if isinstance(pending, bool) or not isinstance(pending, (int, float)) or pending <= 0:
            return
        page.sleep(IMAGE_WAIT_S)


def capture(url: str, phrases: Sequence[str], focus: int = 0, *, open_page: OpenPage | None = None,
            width: int = WIDTH, height: int = HEIGHT, dpr: int = DPR, settle: float = SETTLE_S,
            scale: float = 1, headline: bool = False) -> CaptureResult:
    """Fotografa a janela com o trecho de foco a 42% da altura e mede os retângulos de cada trecho (como o `shoot` do
    canais). `headline`: trecho que não está igual na página vira a manchete dela (o título de uma fonte do roteiro;
    a prova pedida à mão marca só o que o usuário escreveu). `open_page` é injetável (testes)."""
    url = safe_http.check_url(url)
    phrases = [" ".join(p.split()) for p in phrases if p and p.strip()][:MAX_PHRASES]
    if not phrases:
        raise CaptureError("Escreva pelo menos um trecho para marcar.")
    focus = focus if 0 <= focus < len(phrases) else 0
    opener = open_page or playwright_page
    find = FIND_JS % (json.dumps(phrases, ensure_ascii=False), focus, json.dumps(scale), json.dumps(headline))
    with opener(url, width, height, dpr) as page:
        page.sleep(settle)
        page.js(f"window.__SHOT_POS = {FOCUS_POS}")
        page.js(CLEAN_JS)
        page.js(ADS_JS)
        found = page.js(find)
        page.sleep(1.5)  # imagens que carregam depois da rolagem empurram o texto: rola e mede de novo
        _wait_images(page)
        page.js(ADS_JS)  # anúncio que entrou depois da rolagem
        found = page.js(find)
        page.sleep(0.8)
        rects = page.js(RECTS_JS)
        for _ in range(5):  # espera a página parar de mexer (anúncio/imagem entrando): duas medidas iguais seguidas
            page.sleep(0.9)
            again = page.js(RECTS_JS)
            if again == rects:
                break
            found = page.js(find)
            page.sleep(0.8)
            rects = page.js(RECTS_JS)
        page.js(BROKEN_JS)
        share = page.js(BOILER_JS)
        png = page.screenshot()
        title = page.title()
        text = page.js(TEXT_JS) or ""
    rects = list(rects or [])
    rects += [None] * (len(phrases) - len(rects))
    found = [bool(f) for f in (found or [])] + [False] * max(0, len(phrases) - len(found or []))
    return CaptureResult(url=url, domain=domain_of(url), title=" ".join(str(title).split())[:300], png=png,
                         text=str(text), found=found[: len(phrases)], rects=rects[: len(phrases)],
                         width=width * dpr, height=height * dpr,
                         boiler_share=float(share) if isinstance(share, (int, float)) and not isinstance(share, bool)
                         else 0.0)


# --------------------------------------------------------------------------------------------
# Funções puras (shot_views.py)
# --------------------------------------------------------------------------------------------


def ink_mask(img: Image.Image) -> np.ndarray:
    """Onde há texto: escuro numa página clara; claro numa página escura (mediana do cinza < 110)."""
    g = np.asarray(img.convert("L"), dtype=np.int16)
    if np.median(g) < 110:  # página escura (ex.: Yahoo Finance): o texto é o que é claro
        return g > 150
    return g < 150  # texto escuro e links


def is_dark(img: Image.Image) -> bool:
    """Página escura (marca-texto sem multiply): média do cinza em 64 x 40 abaixo de 40%."""
    g = img.convert("L").resize((64, 40))
    return float(np.asarray(g, dtype=np.float64).sum()) / (64 * 40 * 255) < 0.4


def visible(rects: Sequence[Sequence[float]] | None) -> bool:
    """Trecho achado e inteiro dentro da tela capturada."""
    return bool(rects) and all(0 <= r[1] and r[1] + r[3] <= 1 for r in rects or [])


def zoom_for(view: Sequence[float]) -> float:
    vx0, vy0, vx1, vy1 = view
    return max(1.0, min(1.6, min(1 / max(1e-6, vx1 - vx0), 1 / max(1e-6, vy1 - vy0)) * 0.94))


def view_for(mask: np.ndarray, rects: Sequence[Sequence[float]], W: int, H: int) -> list[float]:
    """Janela [x0, y0, x1, y1] (0 a 1) que mostra a coluna inteira do trecho com 1,4 linha de folga acima e abaixo."""
    xs0 = min(r[0] for r in rects) * W
    xs1 = max(r[0] + r[2] for r in rects) * W
    ys0 = min(r[1] for r in rects) * H
    ys1 = max(r[1] + r[3] for r in rects) * H
    line_h = max(r[3] for r in rects) * H
    band0 = int(max(0, ys0 - 1.6 * line_h))
    band1 = int(min(H, ys1 + 1.6 * line_h))
    vy0 = max(0, ys0 - 1.4 * line_h) / H
    vy1 = min(H, ys1 + 1.4 * line_h) / H
    # expande para os lados enquanto houver texto, tolerando o espaço entre palavras
    gap_tol = int(0.03 * W)

    def grow(cols: np.ndarray, x: int, step: int) -> int:
        gap = 0
        last = x
        while 0 <= x < W:
            if cols[x]:
                last, gap = x, 0
            else:
                gap += 1
                if gap > gap_tol:
                    break
            x += step
        return last

    pad = 0.02 * W
    a, b = int(max(0, xs0)), int(min(W - 1, xs1))
    vx0, vx1 = max(0, a - pad) / W, min(W, b + pad) / W
    # O zoom do vídeo mostra uma janela de 1/z x 1/z da página, bem mais alta que a faixa do trecho. As linhas que
    # aparecem acima e abaixo também não podem ficar cortadas nas laterais: a coluna é medida em toda a altura visível
    # e recalculada até estabilizar (no pior caso, z = 1 e a página aparece inteira).
    for _ in range(8):
        cols = mask[band0:band1].sum(0) > 0
        a, b = grow(cols, a, -1), grow(cols, b, 1)
        vx0 = max(0, a - pad) / W
        vx1 = min(W, b + pad) / W
        z = zoom_for((vx0, vy0, vx1, vy1))
        vis = 1 / z
        top = min(max(0.0, (vy0 + vy1) / 2 - vis / 2), 1 - vis)
        nb0, nb1 = int(top * H), int(min(H, (top + vis) * H))
        if nb0 >= band0 and nb1 <= band1:
            break
        band0, band1 = min(band0, nb0), max(band1, nb1)
    return [round(vx0, 4), round(vy0, 4), round(vx1, 4), round(vy1, 4)]


def mark(img: Image.Image, rects: Sequence[Sequence[float]], dark: bool) -> Image.Image:
    """Marca-texto #FFE14D: multiply na página clara; camada de 45% na escura. Folga de 15% da altura da linha."""
    out = np.asarray(img.convert("RGB"), dtype=np.float32).copy()
    H, W = out.shape[:2]
    color = np.array(HIGHLIGHT, dtype=np.float32)
    for x, y, w, h in rects:
        px, py, pw, ph = x * W, y * H, w * W, h * H
        pad = HIGHLIGHT_PAD * ph
        x0, x1 = int(max(0, px - pad)), int(min(W, px + pw + pad))
        y0, y1 = int(max(0, py - pad)), int(min(H, py + ph + pad))
        if x1 <= x0 or y1 <= y0:
            continue
        region = out[y0:y1, x0:x1]
        if dark:
            out[y0:y1, x0:x1] = region * (1 - DARK_ALPHA) + color * DARK_ALPHA
        else:
            out[y0:y1, x0:x1] = region * (color / 255.0)
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def _background(img: Image.Image) -> tuple[int, int, int]:
    small = np.asarray(img.convert("RGB").resize((32, 20)), dtype=np.int32).reshape(-1, 3)
    return tuple(int(v) for v in np.median(small, axis=0))  # type: ignore[return-value]


def crop_box(W: int, H: int, view: Sequence[float], aspect: float) -> tuple[float, float, float, float]:
    """Caixa (x0, y0, x1, y1) em px, na proporção `aspect`, que contém a janela do trecho; pode passar da imagem
    (o excesso vira fundo) quando a proporção não cabe."""
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    z = zoom_for(view)
    cx, cy = (vx0 + vx1) / 2 * W, (vy0 + vy1) / 2 * H
    # janela de W/z x H/z centrada no trecho, presa à imagem
    ww, wh = W / z, H / z
    x0 = min(max(0.0, cx - ww / 2), W - ww)
    y0 = min(max(0.0, cy - wh / 2), H - wh)
    # a janela do trecho (obrigatória) entra inteira
    must = (vx0 * W, vy0 * H, vx1 * W, vy1 * H)
    bx0, by0 = min(x0, must[0]), min(y0, must[1])
    bx1, by1 = max(x0 + ww, must[2]), max(y0 + wh, must[3])

    def fit(lo: float, hi: float, size: float, limit: float, keep: tuple[float, float]) -> tuple[float, float]:
        """Intervalo de `size` que contém [lo, hi] e `keep`, dentro de [0, limit] quando possível."""
        center = (lo + hi) / 2
        start = center - size / 2
        if size <= limit:
            start = min(max(0.0, start), limit - size)
        else:
            start = (limit - size) / 2
        start = min(start, keep[0])
        start = max(start, keep[1] - size)
        return start, start + size

    bw, bh = bx1 - bx0, by1 - by0
    if bw / bh < aspect:  # alta demais: alarga
        need_w = bh * aspect
        if need_w > W:  # não cabe: encolhe a altura até a janela do trecho (sem cortá-la)
            bh = max(must[3] - must[1], W / aspect)
            by0, by1 = fit(by0, by1, bh, H, (must[1], must[3]))
            need_w = bh * aspect
        bx0, bx1 = fit(bx0, bx1, need_w, W, (must[0], must[2]))
    else:  # larga demais: aumenta a altura
        need_h = bw / aspect
        if need_h > H:  # não cabe: estreita até a janela do trecho (sem cortá-la)
            bw = max(must[2] - must[0], H * aspect)
            bx0, bx1 = fit(bx0, bx1, bw, W, (must[0], must[2]))
            need_h = bw / aspect
        by0, by1 = fit(by0, by1, need_h, H, (must[1], must[3]))
    return bx0, by0, bx1, by1


def crop_to_view(img: Image.Image, view: Sequence[float], aspect: float) -> Image.Image:
    """Recorte na proporção do vídeo (16:9 ou 9:16) em volta da janela do trecho; o que passa da página vira fundo."""
    rgb = img.convert("RGB")
    W, H = rgb.size
    x0, y0, x1, y1 = crop_box(W, H, view, aspect)
    width = max(1, round(x1 - x0))
    height = max(1, round(width / aspect))
    canvas = Image.new("RGB", (width, height), _background(rgb))
    ix0, iy0 = round(x0), round(y0)
    part = rgb.crop((max(0, ix0), max(0, iy0), min(W, ix0 + width), min(H, iy0 + height)))
    canvas.paste(part, (max(0, -ix0), max(0, -iy0)))
    return canvas


def clean_crop(img: Image.Image, view: Sequence[float], rects: Sequence[Sequence[float]],
               aspect: float) -> tuple[Image.Image, list[list[float]]]:
    """O recorte da prova SEM o marca-texto queimado (o PNG limpo do acervo) e os retângulos dos trechos medidos na
    captura, passados para o quadro recortado (`[x, y, w, h]` de 0 a 1): o destaque fica para o renderer animar.
    Trecho fora do recorte sai; o que encosta na borda é aparado."""
    out = crop_to_view(img, view, aspect)
    W, H = img.size
    x0, y0, _x1, _y1 = crop_box(W, H, view, aspect)
    cw, ch = out.size
    ix0, iy0 = round(x0), round(y0)
    moved: list[list[float]] = []
    for rect in rects:
        if len(rect) != 4:
            continue
        x, y, w, h = (float(v) for v in rect)
        a = min(1.0, max(0.0, (x * W - ix0) / cw))
        b = min(1.0, max(0.0, (y * H - iy0) / ch))
        c = min(1.0, max(0.0, ((x + w) * W - ix0) / cw))
        d = min(1.0, max(0.0, ((y + h) * H - iy0) / ch))
        a, b = round(a, 4), round(b, 4)
        rw, rh = round(min(c - a, 1.0 - a), 4), round(min(d - b, 1.0 - b), 4)
        if rw <= 0.001 or rh <= 0.001:
            continue
        moved.append([a, b, rw, rh])
    return out, moved
