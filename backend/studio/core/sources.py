"""Níveis de fonte do material na tela (ADR 0004 §3.0). Só funções puras.

A hierarquia vai do mais real ao gerado: material próprio (`own`), oficial (trailer, arte, gameplay), captura de
página/notícia, foto livre, banco de imagens, gráfico desenhado pelo app, meme e IA. `prov_of_scene` é a fonte única de
verdade do nível de cada cena: a timeline (`clean`, efeitos por nível) e as métricas (tempo de tela de IA) leem daqui.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from studio.core.project.document import CARD_KINDS, DATA_KINDS, PROVS, MediaRef, Prov, Visual

if TYPE_CHECKING:
    from studio.core.project.document import ProjectDoc, Scene
    from studio.core.project.pool import PoolItem

TIERS: tuple[str, ...] = PROVS
"""Os níveis, do mais real ao gerado (T0 a T7)."""
FALLBACK: list[str] = ["own", "capture", "official", "free", "stock", "graphic", "ai"]
"""Ordem de reserva quando o item escolhido falta: o próximo nível disponível no mesmo tópico."""
REAL_TIERS = frozenset({"own", "official", "capture", "free", "stock"})
"""Níveis que contam como material real (`real_share`)."""
HOOK_DEFAULT: list[str] = ["own", "official", "capture", "free"]
"""Níveis aceitos no gancho quando o estilo não diz."""
PANEL_PROV_KINDS = DATA_KINDS
"""Cartões de dado (stat, compare, chart, map): o painel ocupa a tela e o fundo fica desfocado atrás (`BLUR` no
renderer), então o NÍVEL da cena é `graphic`, seja qual for o fundo (não entra no teto de material oficial). O fundo
de IA desfocado atrás do painel continua sendo IA na tela: entra no tempo de IA com `PANEL_AI_WEIGHT`
(`ai_weight`). Os cartões de texto (text, title, quote, count, document) deixam o fundo à vista e contam pelo nível
dele."""
PANEL_AI_WEIGHT = 0.5
"""Quanto da imagem de IA fica à vista atrás de um cartão de dado (desfocada e escurecida, o painel cobre o meio):
metade do tempo da cena conta como IA na tela. O fundo do cartão de texto, a imagem na moldura de tela e o subplano
de IA contam inteiros."""

# Arte de fã ou gerada por IA que chega pela web (Flickr, Openverse, capa de matéria): o texto que descreve a imagem
# diz QUEM a fez ("GTA 6 Fan Cover", "fan art", "made with Midjourney", "AI-generated art"). Não é material real: não
# entra no acervo e, já anexada, conta como IA na tela. Só sinal de autoria: o assunto não conta ("Midjourney v7
# launches", "AI images are fooling people", "imagem de IA do papa viraliza" falam de IA, não são imagem de IA), e
# "Rockstar fan" (a foto de um fã) não casa: só arte, capa ou pôster de fã.
_AI_TOOLS = (r"(?:an? |the )?(?:ai|midjourney|stable[\s_-]?diffusion|dall[\s·._-]?e(?:[\s-]?\d)?|leonardo(?:\.ai)?|"
             r"firefly|nightcafe)")
_SYNTHETIC_RE = re.compile(
    r"\b(?:fan[\s_-]?(?:arts?|made|covers?|posters?|concepts?|renders?|edits?|artworks?|designs?|mock-?ups?|"
    r"wallpapers?)|fanarts?|fanmade|"
    r"ai[\s_-]generated|"
    r"(?:midjourney|stable[\s_-]?diffusion|dall[\s·._-]?e(?:[\s-]?\d)?|leonardo\.ai|firefly|nightcafe) "
    r"(?:arts?|artworks?|renders?|illustrations?|creations?)|"
    rf"(?:generated|made|created|rendered|drawn|imagined) (?:with|by|using|in|via) {_AI_TOOLS}|"
    r"(?:gerad|feit|criad)[ao]s? (?:por|com|pela|na) (?:uma )?ia|"
    r"arte de f[aã]|fan ?arte)\b",
    re.IGNORECASE)
_NOT_SYNTHETIC_KINDS = frozenset({"news", "capture"})
"""Notícia e captura de página: o print mostra a página como ela é. A manchete pode falar de arte de fã ou de IA (é o
assunto) e o material continua sendo a captura real."""

# Mídia anexada sem `tier`: o nível pelo tipo e, em foto e clipe, pela origem.
_KIND_TIER: dict[str, Prov] = {"news": "capture", "capture": "capture", "official": "official", "meme": "meme"}
_SOURCE_TIER: dict[str, Prov] = {"upload": "own", "library": "own", "pexels": "stock", "commons": "free",
                                 "openverse": "free", "capture": "capture", "youtube": "official"}
# Licença livre declarada: Creative Commons (CC0, CC BY...), domínio público ou licença de banco gratuito.
_FREE_LICENSE_RE = re.compile(r"^\s*(cc(?:[\s\-_0-9]|$)|creative commons|public domain|dom[ií]nio p[uú]blico|"
                              r"pd(?:m|\b)|pexels|unsplash|pixabay)", re.IGNORECASE)


def is_free_license(license_name: str) -> bool:
    """A licença declarada é livre (CC*, domínio público ou licença de banco gratuito). Vazia: não (direitos
    reservados ou desconhecida)."""
    return bool(_FREE_LICENSE_RE.match(license_name or ""))


def looks_synthetic(*texts: object) -> bool:
    """O texto que descreve a imagem (título, crédito, autor, descrição) diz que ela é arte de fã ou gerada por IA.
    Endereço não entra: o caminho da página é o assunto da matéria, não o autor da imagem."""
    return any(_SYNTHETIC_RE.search(str(t)) for t in texts if t)


def media_is_synthetic(media: MediaRef) -> bool:
    """Mídia da web que a própria fonte diz ser arte de fã ou de IA (`looks_synthetic`). O arquivo enviado, a
    biblioteca do canal (escolha do usuário) e a notícia ou captura de página (`_NOT_SYNTHETIC_KINDS`) ficam com o
    nível deles."""
    if media.source in ("upload", "library") or media.kind in _NOT_SYNTHETIC_KINDS or media.tier == "capture":
        return False
    return looks_synthetic(media.title, media.credit, media.author)


OWN_SOURCE_PREFIXES = ("file:", "acervo:", "kit:", "kitvideo:")
"""Itens do acervo que o usuário pôs (arquivo anexado, pasta do projeto, biblioteca do canal): ficam como estão."""


def item_is_synthetic(item: PoolItem) -> bool:
    """Item do acervo que a própria fonte diz ser arte de fã ou de IA (`looks_synthetic`); o do usuário e a captura de
    página (o print da notícia) ficam."""
    if item.tier in ("own", "capture") or item.source_id.startswith(OWN_SOURCE_PREFIXES):
        return False
    return looks_synthetic(item.desc, item.credit)


def prov_of_item(item: PoolItem) -> Prov:
    """O nível de um item do acervo na tela: o `tier`, ou `ai` quando a fonte diz que é arte de fã ou de IA."""
    return "ai" if item_is_synthetic(item) else item.tier


def prov_of_media(media: MediaRef) -> Prov:
    """O nível de uma mídia anexada: arte de fã ou de IA pela própria fonte (`media_is_synthetic`) é `ai`, seja qual
    for o `tier` do acervo; senão o `tier` quando existe; senão pelo tipo (notícia/captura, oficial, meme) e, em foto
    e clipe, pela origem: arquivo enviado e biblioteca são próprios, Pexels é banco, Commons/Openverse são foto livre,
    YouTube é oficial. Link só é `free` com licença livre declarada; sem ela é material de terceiros com direitos
    (`official`: entra no teto de Content ID e fica `editorial`)."""
    if media_is_synthetic(media):
        return "ai"
    if media.tier is not None:
        return media.tier
    if media.kind in _KIND_TIER:
        return _KIND_TIER[media.kind]
    if media.source in _SOURCE_TIER:
        return _SOURCE_TIER[media.source]
    return "free" if is_free_license(media.license) else "official"


def _prov_of_visual(visual: Visual) -> Prov:
    if visual.type == "none":
        return "graphic"
    if visual.type == "stock":
        return "stock"
    if visual.type == "upload":
        return "own"
    return "ai"  # ai_image, ai_video


def prov_of_scene(scene: Scene, doc: ProjectDoc | None = None) -> Prov:
    """O nível do que a cena mostra. Regras, em ordem:

    1. cartão de dado (`PANEL_PROV_KINDS`) → `graphic` (o painel cobre o fundo desfocado);
    2. mídia real (a da cena ou, com `doc`, a da cena que ela reaproveita): `media.tier`, ou `prov_of_media`;
    3. visual `none`, ou cartão de texto sem visual próprio (sem prompt, imagem fixada nem reaproveitamento) → `graphic`;
    4. com `doc`, cena que reaproveita outra (`reuse_of`, sem imagem fixada própria): vale o visual da cena dona (raiz
       `upload` → `own`, `stock` → `stock`...); referência solta (cena apagada, ciclo) → `graphic`, porque o renderer
       mostra o fundo animado;
    5. o tipo do visual: `ai_image`/`ai_video` → `ai`; `stock` → `stock`; `upload` → `own`.

    Imagem fixada (`visual.asset`) sem mídia continua com o nível do tipo do visual (é a imagem de IA fixada). Sem
    `doc`, o reaproveitamento não é seguido (a cena vale pelo próprio visual)."""
    if scene.kind in PANEL_PROV_KINDS:
        return "graphic"
    return visual_prov(scene, doc)


def visual_prov(scene: Scene, doc: ProjectDoc | None = None) -> Prov:
    """O nível da IMAGEM que a cena mostra (as regras 2 a 5 de `prov_of_scene`, sem a do cartão de dado): o fundo
    atrás do painel de um cartão de dado também é imagem na tela."""
    media = doc.shown_media(scene) if doc is not None else scene.visual.media
    if media is not None:
        return prov_of_media(media)
    if _bare_card(scene):
        return "graphic"
    if doc is not None and scene.visual.reuse_of and not scene.visual.asset:
        root = doc.reuse_root(scene)
        if root is None or _bare_card(root):
            return "graphic"
        return _prov_of_visual(root.visual)
    return _prov_of_visual(scene.visual)


def _bare_card(scene: Scene) -> bool:
    """Sem imagem nenhuma: visual `none`, ou cartão sem prompt, imagem fixada nem reaproveitamento."""
    visual = scene.visual
    return visual.type == "none" or (
        scene.kind in CARD_KINDS and not visual.prompt and not visual.asset and not visual.reuse_of)


def is_real(prov: str) -> bool:
    return prov in REAL_TIERS


def ai_weight(scene: Scene, doc: ProjectDoc | None = None) -> float:
    """Quanto do tempo da cena é imagem de IA na tela, de 0 a 1: 1 quando ela mostra IA (imagem, fundo de cartão de
    texto, moldura de tela ou arte de fã/IA da web); `PANEL_AI_WEIGHT` quando a IA é o fundo desfocado de um cartão
    de dado; 0 no resto. É o peso do teto de IA na distribuição do storyboard e nas métricas."""
    if prov_of_scene(scene, doc) == "ai":
        return 1.0
    if scene.kind in PANEL_PROV_KINDS and visual_prov(scene, doc) == "ai":
        return PANEL_AI_WEIGHT
    return 0.0


# --- Distribuição do material no tempo --------------------------------------------------------------------------------

AI_WINDOW_S = 45.0
"""Janela deslizante do teto de IA: além da média do vídeo (`ai_max_share`), nenhum trecho desta duração passa de
`ai_window_cap`. A média de 20% num vídeo de 7 min esconde um minuto inteiro de IA (o QA do prj_gq4krrs11w achou 46%
de IA num trecho de 45 s com a média em 17%). Vídeo mais curto que a janela: a janela é o vídeo todo."""
AI_WINDOW_SLACK = 1.5
"""Folga do teto por trecho sobre o da média: a batida é a unidade (4 a 7 s no longo), e com o teto do trecho igual
ao da média duas imagens de IA de 5 s nunca caberiam em 45 s (22%), o que baixaria a média possível para ~11%. Com
1,5× (30% no longo: duas batidas de IA por trecho) a IA espalhada passa e o bloco de IA não."""


def ai_window_cap(ai_max_share: float) -> float:
    """O teto de IA em qualquer trecho de `AI_WINDOW_S`, a partir do teto da média."""
    return round(min(1.0, ai_max_share * AI_WINDOW_SLACK), 4)


ITEM_MAX_USES = 2
"""Quantas vezes o mesmo material do acervo aparece no vídeo. Cenas seguidas com a mesma imagem (a foto e o cartão
sobre ela) são uma aparição só."""
ITEM_REUSE_GAP_S = 60.0
"""Distância mínima entre duas aparições do mesmo material: voltar antes disso parece slideshow de poucas imagens. A
retomada de propósito é a que o usuário fixou (cena travada, mídia ou subplano escolhidos à mão), que o anexo e o
planejador contam mas nunca trocam."""
TOUCH_S = 1.0
"""Aparições a menos disto uma da outra são a mesma (cenas seguidas: a pausa da fala entre elas não separa)."""


def window_share(spans: Iterable[tuple[float, ...]], total: float,
                 window_s: float = AI_WINDOW_S) -> tuple[float, float]:
    """O trecho de `window_s` segundos mais coberto por `spans` (`(início, fim)` ou `(início, fim, peso)` em segundos,
    sem sobreposição entre eles; o peso, de 0 a 1, é quanto do trecho conta, como o `ai_weight` do fundo de IA atrás
    do cartão de dado): `(fração coberta, início do trecho)`. Vídeo mais curto que a janela: o vídeo todo."""
    if total <= 0:
        return 0.0, 0.0
    width = min(window_s, total)
    clipped = [(max(0.0, sp[0]), min(total, sp[1]), float(sp[2]) if len(sp) > 2 else 1.0) for sp in spans
               if min(total, sp[1]) > max(0.0, sp[0]) and (len(sp) <= 2 or sp[2] > 0)]
    if not clipped:
        return 0.0, 0.0
    # O máximo cai numa janela que começa no início de um trecho ou termina no fim de um.
    starts = sorted({min(max(0.0, t), total - width) for a, b, _w in clipped for t in (a, b - width)})
    best, at = -1.0, 0.0
    for t in starts:
        covered = sum(max(0.0, min(b, t + width) - max(a, t)) * w for a, b, w in clipped)
        if covered > best + 1e-9:
            best, at = covered, t
    return min(1.0, best / width), at


def media_tokens(media: MediaRef | None) -> frozenset[str]:
    """O que identifica a mídia na tela, para contar as aparições do mesmo material: o item do acervo, o arquivo
    anexado e o endereço da imagem. Dois itens do acervo que o anexo baixa no mesmo arquivo (a imagem de capa da mesma
    matéria) são a mesma imagem. O endereço de vídeo não entra: trechos do mesmo trailer são planos diferentes."""
    if media is None:
        return frozenset()
    out = {f"pool:{media.pool_id}"} if media.pool_id else set()
    if media.video:
        out.add(f"video:{media.video}#{media.start_s:g}")
    else:
        if media.asset:
            out.add(f"file:{media.asset}")
        if media.url and media.kind != "clip":
            out.add(f"url:{media.url}")
    return frozenset(out)


def item_tokens(item: PoolItem) -> frozenset[str]:
    """Como `media_tokens`, para o item do acervo (antes do anexo, ou no subplano do planejador)."""
    out = {f"pool:{item.id}"}
    if item.media != "clip":
        if item.url:
            out.add(f"url:{item.url}")
        if item.path:
            out.add(f"file:{item.path}")
    return frozenset(out)


def reuse_problem(others: Iterable[tuple[float, float]], mine: Iterable[tuple[float, float]], *,
                  max_uses: int = ITEM_MAX_USES, gap_s: float = ITEM_REUSE_GAP_S,
                  max_run_s: float | None = None) -> str | None:
    """Por que pôr o material nos trechos `mine` (a cena e as que reaproveitam a imagem dela) fere a distribuição,
    dadas as aparições dele no resto do vídeo (`others`, `(início, fim)` em segundos); None: cabe. Motivos:

    - `"uses"`: a nova é uma aparição a mais (não a continuação de outra) e passa de `max_uses`;
    - `"gap"`: a aparição da nova fica a menos de `gap_s` de outra (antes ou depois);
    - `"run"`: com `max_run_s`, a nova emenda numa aparição de outra cena e o conjunto passa desse tempo seguido na
      tela (a duração das cenas que só ela ocupa é escolha do storyboard: trocar o item não muda nada).

    O que já estava errado só entre as outras não é culpa da nova."""
    spans = sorted([(a, b, False) for a, b in others] + [(a, b, True) for a, b in mine])
    runs: list[tuple[float, float, bool, bool]] = []  # (início, fim, tem a nova, tem outra)
    for a, b, new in spans:
        if runs and a - runs[-1][1] < TOUCH_S:
            a0, b0, m0, o0 = runs[-1]
            runs[-1] = (a0, max(b0, b), m0 or new, o0 or not new)
        else:
            runs.append((a, b, new, not new))
    if len(runs) > max_uses and any(m and not o for _a, _b, m, o in runs):
        return "uses"
    for a, b, m, o in runs:
        if not m:
            continue
        if max_run_s is not None and o and b - a > max_run_s + 1e-6:
            return "run"  # emenda numa aparição de outra cena e passa do tempo seguido (só a dela não é escolha daqui)
        for c, d, _m2, o2 in runs:
            if (c, d) == (a, b) or not o2:
                continue  # a cena e a que reaproveita a imagem dela longe: é a escolha do storyboard
            if (c - b if c >= b else a - d) < gap_s - 1e-6:
                return "gap"
    return None
