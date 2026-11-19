"""Níveis de fonte do material na tela (ADR 0004 §3.0). Só funções puras.

A hierarquia vai do mais real ao gerado: material próprio (`own`), oficial (trailer, arte, gameplay), captura de
página/notícia, foto livre, banco de imagens, gráfico desenhado pelo app, meme e IA. `prov_of_scene` é a fonte única de
verdade do nível de cada cena: a timeline (`clean`, efeitos por nível) e as métricas (tempo de tela de IA) leem daqui.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from studio.core import verdict
from studio.core.numbers import number_runs, text_numbers
from studio.core.project.document import CARD_KINDS, DATA_KINDS, PROVS, MediaRef, Prov, Visual
from studio.core.text import normalize_token, span_ranges, split_word_indices, words

if TYPE_CHECKING:
    from studio.core.project.document import ProjectDoc, Scene, Source
    from studio.core.project.pool import PoolItem
    from studio.media.lookalike import LookIndex

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
CARD_BACKDROP_KINDS = frozenset({"text", "title", "quote", "stat", "compare", "chart"})
"""Cartões em que a imagem é só o fundo (escurecido e desfocado atrás do texto ou do painel, `DIM`/`BLUR` no
renderer): sem uma que caiba (outro assunto, repetida, escura demais), o fundo do estilo serve (cartão tipográfico). O
contador do Top 10 (mostra a imagem do item), o mapa (não usa fundo) e o documento ficam fora."""

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


# Texto na tela do plano que a visão leu (`clips.describe`: 'texto "RIP Rudi - C U in heaven, cuz."').
_ON_SCREEN_RE = re.compile(r'\btexto\s+"([^"]+)"?')
OVERLAY_MAX_CAPS_WORDS = 4
"""Letreiro em caixa alta ou em título ("THRILLBILLY MUD CLUB", "Grand Theft Auto VI") é cenário; acima disso é aviso,
faixa de notícia ou slate de classificação ("MAY CONTAIN CONTENT INAPPROPRIATE FOR CHILDREN")."""


def on_screen_text(desc: str | None) -> str:
    """O texto que a visão leu no quadro do plano (vazio quando não há)."""
    found = _ON_SCREEN_RE.search(desc or "")
    return found.group(1).strip() if found else ""


# O que outro criador cola por cima do plano (`clips.describe`: 'sobreposição "logo"'): logo de canal, mascote, meme,
# print colado ou a câmera do rosto do streamer.
_OVERLAY_MARK_RE = re.compile(r'\bsobreposi[cç][aã]o\s+"([^"]+)"')


def foreign_overlay(desc: str | None, *texts: object, marks: bool = True) -> bool:
    """O plano mostra texto de outra pessoa por cima da imagem: post de rede social, legenda de usuário, nome de
    canal, faixa de notícia ou slate ("YoMammaJammer… RIP Rudi - C U in heaven, cuz." no trailer, QA do
    prj_gq4krrs11w, 4:57). Na tela ele parece vídeo reaproveitado de outro criador e briga com a legenda. Conta como
    alheio o texto de 3 palavras ou mais em frase (com minúscula: "Stay Alert VC! Remember to check"), o de mais de
    `OVERLAY_MAX_CAPS_WORDS` palavras, e o que tem @, # ou endereço; letreiro curto em caixa alta ou em título é
    cenário. Vale quando a fala (`texts`) não divide uma palavra de 4 letras ou mais com ele (a cena que fala daquele
    post pode mostrá-lo). A sobreposição que a visão viu (logo de canal, mascote, meme, print colado, câmera do rosto
    do streamer) conta sempre (`marks`).
    Quem decide para que item isto vale é `overlay_blocks` (o material do usuário e o oficial ficam)."""
    if marks and _OVERLAY_MARK_RE.search(desc or ""):
        return True
    text = on_screen_text(desc)
    if not text:
        return False
    words = [w for w in re.findall(r"[^\W\d_][\w'’]*", text)]
    handle = bool(re.search(r"[@#]|https?:|www\.", text))
    sentence = len(words) >= 3 and any(w[:1].islower() for w in words)
    if not (handle or sentence or len(words) > OVERLAY_MAX_CAPS_WORDS):
        return False
    mine = {normalize_token(w) for w in words if len(w) >= 4}
    spoken = {normalize_token(w) for t in texts if isinstance(t, str) for w in re.findall(r"[^\W\d_][\w'’]*", t)
              if len(w) >= 4}
    return not mine & spoken


def overlay_blocks(item: PoolItem, *texts: object, cutaway: bool = False) -> bool:
    """O item do acervo fica fora da cena pelo texto ou pela sobreposição alheia (`foreign_overlay`)? Nunca o do
    usuário (o vídeo que ele gravou ou pôs no acervo e no kit: a tela do tutorial com "Press E to enter the vehicle"
    é o material dele). O oficial é a própria obra (o post do jogo dentro do trailer é do trailer): como plano da cena
    ele fica; como subplano de corte (`cutaway`, escolha livre do planejador) só entra onde a fala é sobre o texto (o
    logo do dono na obra oficial não conta)."""
    if item.tier == "own" or item.source_id.startswith(OWN_SOURCE_PREFIXES):
        return False
    if item.tier == "official":
        return cutaway and foreign_overlay(item.desc, *texts, marks=False)
    return foreign_overlay(item.desc, *texts)


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


def is_typographic(scene: Scene, doc: ProjectDoc | None = None) -> bool:
    """Cartão tipográfico: cena de texto (`kind` "text") sem imagem nenhuma na tela, só a fala sobre o fundo do estilo
    (nem mídia real, nem imagem de IA, nem a imagem de outra cena de fundo). É o último recurso da cena que fica sem
    material (`workers.pipeline.typographic`): o avatar entra nele (`presenter.assign`) e o renderer o desenha como
    frase inteira sobre o fundo com textura."""
    return scene.kind == "text" and visual_prov(scene, doc) == "graphic"


TEXT_CARD_KINDS = frozenset({"text", "title", "quote", "count"})
"""Cartões que põem uma frase grande no meio da tela (sem painel de dado nem documento)."""


def is_bare_card(scene: Scene, doc: ProjectDoc | None = None) -> bool:
    """Cartão de frase sem imagem nenhuma atrás (o cartão tipográfico e o título, a citação ou a contagem sobre o
    fundo do estilo): o que `workers.pipeline.typographic` tenta trocar por material. O QA do prj_gq4krrs11w (r4)
    contou o título "REMOTE PLAY" no fundo liso como cartão tipográfico colado no outro."""
    return scene.kind in TEXT_CARD_KINDS and visual_prov(scene, doc) == "graphic"


def is_text_card(scene: Scene, doc: ProjectDoc | None = None) -> bool:
    """O que o espectador lê como cartão tipográfico: a fala (ou a frase da cena) grande no meio da tela. É a cena de
    texto com QUALQUER fundo (o fundo da imagem reaproveitada fica escurecido e desfocado atrás da frase: o QA da r4
    contou "Qualquer tabela de requisitos" sobre a captura borrada colado no cartão liso) e o cartão de frase sem
    imagem (`is_bare_card`). O título sobre uma imagem à vista e o cartão de dado não contam. É a conta da regra "nunca
    dois seguidos", do intervalo e da parte do vídeo (`typographic`)."""
    return scene.kind == "text" or is_bare_card(scene, doc)


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


_SOCIAL_HOST_RE = re.compile(
    r"(?:^|\.)(?:twitter\.com|x\.com|instagram\.com|tiktok\.com|facebook\.com|fb\.com|threads\.net|bsky\.app|"
    r"reddit\.com|tumblr\.com|linkedin\.com|mastodon\.\w+|kwai\.com)$"
)


def is_social_post(media: MediaRef | None) -> bool:
    """A mídia é um post de rede social (a página ou a imagem vêm de um perfil): só ela cabe na moldura de post do
    feed. Trailer, arte e matéria não são post."""
    if media is None:
        return False
    for url in (media.page_url, media.url):
        host = re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/", 1)[0].split(":", 1)[0]
        if host and _SOCIAL_HOST_RE.search(host):
            return True
    return False


def media_tokens(media: MediaRef | None, look: LookIndex | None = None) -> frozenset[str]:
    """O que identifica a mídia na tela, para contar as aparições do mesmo material: o item do acervo, o arquivo
    anexado e o endereço da imagem. Dois itens do acervo que o anexo baixa no mesmo arquivo (a imagem de capa da mesma
    matéria) são a mesma imagem. O endereço de vídeo não entra: trechos do mesmo trailer são planos diferentes.
    Com `look`, também a marca da imagem parecida (`media.lookalike`: a mesma arte noutra fonte, dentro da captura de
    uma página ou no meio do fundo desfocado), que junta itens diferentes do acervo com a mesma imagem."""
    if media is None:
        return frozenset()
    if look is not None:
        return media_tokens(media) | look.media(media)
    out = {f"pool:{media.pool_id}"} if media.pool_id else set()
    if media.video:
        out.add(f"video:{media.video}#{media.start_s:g}")
    else:
        if media.asset:
            out.add(f"file:{media.asset}")
        if media.url and media.kind != "clip":
            out.add(f"url:{media.url}")
    return frozenset(out)


def item_tokens(item: PoolItem, look: LookIndex | None = None) -> frozenset[str]:
    """Como `media_tokens`, para o item do acervo (antes do anexo, ou no subplano do planejador)."""
    if look is not None:
        return item_tokens(item) | look.item(item)
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


# --- "Fonte:" sobre título ---------------------------------------------------------------------

# Índice de lista no começo do título ("2. TAMANHO QUE NÃO BATE", "3) PIX", "#4 —"): numeração do vídeo, não dado.
_LIST_INDEX_RE = re.compile(r"^\s*#?\d{1,2}\s*[.)ºª°:\-–—]\s*")
# Palavras de 4+ letras que não dizem de onde veio o texto.
_FILLER = frozenset({
    "para", "pelo", "pela", "pelos", "pelas", "como", "mais", "menos", "muito", "sobre", "entre", "sempre", "nunca",
    "isso", "esse", "essa", "este", "esta", "aqui", "agora", "ainda", "cada", "todo", "toda", "todos", "todas",
    "voce", "seus", "suas", "quem", "qual", "quando", "onde", "porque", "with", "from", "that", "this", "your",
    "what", "when", "will", "have", "about", "they", "their", "pero",
})  # fmt: skip


def _compact_name(text: str) -> str:
    return "".join(ch for ch in normalize_token(text) if ch.isalnum())


def _content_words(text: str) -> set[str]:
    out = set()
    for raw in re.split(r"[^\w]+", text or ""):
        token = normalize_token(raw)
        if len(token) >= 4 and not token.isdigit() and token not in _FILLER:
            out.add(token)
    return out


def _same_word(a: str, b: str) -> bool:
    """A mesma palavra com outra flexão ("golpe"/"golpes", "falso"/"falsos", "roubadas"/"roubando"): radical comum
    de 4+ letras (a palavra menor inteira, ou 5+ letras faltando no máximo 3 da menor)."""
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    common = 0
    for x, y in zip(short, long_, strict=False):
        if x != y:
            break
        common += 1
    if common == len(short):
        return len(long_) - len(short) <= 3
    return common >= 5 and len(short) - common <= 3


# Segundo nível de domínio de país ("canaltech.com.br", "bbc.co.uk"): o nome do site é o rótulo antes dele.
_SECOND_LEVEL = frozenset({"com", "co", "org", "net", "gov", "edu", "ac", "gob", "nom", "art", "blog"})


def _host_labels(url: str) -> list[str]:
    host = re.sub(r"^[a-z]+://", "", (url or "").strip().lower()).split("/", 1)[0].split(":", 1)[0]
    return [p for p in host.split(".") if p and p != "www"]


def _domain_name(url: str) -> str:
    """O nome do site no endereço: "welivesecurity" de "www.welivesecurity.com/pt/...", "canaltech" de
    "canaltech.com.br" (e não "com")."""
    parts = _host_labels(url)
    if len(parts) >= 3 and len(parts[-1]) == 2 and parts[-2] in _SECOND_LEVEL:
        parts = parts[:-1]
    return parts[-2] if len(parts) >= 2 else (parts[0] if parts else "")


def cites_source(source: Source, cited: str) -> bool:
    """A fonte do roteiro é a que o "Fonte: X" cita: pelo nome no título ou pelo domínio do endereço."""
    key = _compact_name(cited)
    if not key:
        return False
    domain = _compact_name(_domain_name(source.url or ""))
    return key in _compact_name(source.title or "") or (len(domain) >= 3 and (domain in key or key in domain))


def _source_text(source: Source) -> str:
    slug = re.sub(r"^[a-z]+://[^/]+", "", (source.url or "").lower())
    return f"{source.title}. {source.note}. {re.sub(r'[^a-z0-9]+', ' ', slug)}"


def title_backs_source(title: str, cited: str, sources: Iterable[Source], lang: str) -> bool:
    """O título da cena diz algo da fonte que o "Fonte: X" cita? Diz quando traz um número dela (fora o índice de
    lista: "2." numera o vídeo) ou uma palavra do título, da nota ou do endereço dela, com qualquer flexão ("GOLPE DO
    DOWNLOAD FALSO" com "golpes com downloads falsos"). Sem isso ("2. TAMANHO QUE NÃO BATE" com "Fonte: Malwarebytes",
    numa conclusão que junta o 1,1 MB da Malwarebytes e a ISO da ESET), a fonte vira crédito de uma ideia que não é
    dela. `title` e `lang` são do idioma principal (o das fontes). Fonte fora do roteiro (não dá para conferir) passa."""
    key = _compact_name(cited)
    if not key:
        return True
    own = [s for s in sources if cites_source(s, cited)]
    if not own:
        return True
    body = _LIST_INDEX_RE.sub("", title or "")
    if not body.strip():
        return False
    refs = " ".join(_source_text(s) for s in own)
    numbers = text_numbers(body, lang, names=True)
    if numbers and {float(v) for v in numbers} & {float(v) for v in text_numbers(refs, lang, names=True)}:
        return True
    ref_words = _content_words(refs)
    return any(_same_word(w, r) for w in _content_words(body) for r in ref_words)


def _is_lone_title(scene: Scene) -> bool:
    card = scene.card
    return scene.kind == "title" and not (card and (any(card.headline.values()) or any(card.stamp.values())))


def title_keeps_source(doc: ProjectDoc, scene: Scene, cited: str) -> bool:
    """O título sem faixa nem carimbo mantém o "Fonte: X"? Mantém quando diz algo da fonte (`title_backs_source`) ou
    quando o mesmo trecho da narração já credita X em outra cena ("ATIVE A VERIFICAÇÃO EM 2 ETAPAS" no meio das
    orientações da Malwarebytes): o mesmo assunto fica com o mesmo crédito. Sai quando nada liga o título à fonte
    ("2. TAMANHO QUE NÃO BATE", que junta o arquivo da Malwarebytes e a ISO da ESET, num trecho que só cita outra)."""
    if not _is_lone_title(scene):
        return True
    lang = doc.primary_language
    title = scene.overlay_text.get(lang) or next((t for t in scene.overlay_text.values() if t), "")
    if title_backs_source(title, cited, doc.sources, lang):
        return True
    key = _compact_name(cited)
    # A outra cena só empresta o crédito quando ela mesma diz algo da fonte: o "GOLPE" com "Fonte: Malwarebytes" sobre
    # a busca do Google não faz de "2. O TAMANHO NÃO BATE" (1 MB da Malwarebytes e ISO da ESET) um dado da Malwarebytes.
    return any(
        other.id != scene.id and other.card is not None and _compact_name(other.card.source or "") == key
        and not _is_lone_title(other) and scene_claims_source(doc, other, cited)
        for other in doc.scenes_of(scene.segment_id)
    )


# --- "Fonte:" só onde a cena diz algo da fonte -------------------------------------------------

SEARCH_SITES = frozenset({"google", "bing", "duckduckgo", "yahoo", "yandex", "baidu", "ecosia", "startpage"})
"""Busca não é veículo: a captura da página de resultados não credita ninguém."""

_RESULTS = r"(results?|resultados?|busca|buscas|search|searches|pesquisa|pesquisas|serp)"
_SEARCH_FIGURE_RE = re.compile(
    rf"\b({'|'.join(sorted(SEARCH_SITES))})\b[^.;|]{{0,40}}\b{_RESULTS}\b|\b{_RESULTS}\b[^.;|]{{0,40}}\b"
    rf"({'|'.join(sorted(SEARCH_SITES))})\b"
)


def shows_search_page(*texts: str) -> bool:
    """A imagem reproduz uma página de busca ("gta-6-leak-google-results.png", "Site falso nos resultados do
    Google"): mesmo vindo da matéria da fonte, o que está na tela é a busca, não dado da fonte. Olha o nome do
    arquivo e a descrição (hífen e sublinhado viram espaço)."""
    text = " ".join(re.sub(r"[-_+]", " ", t or "") for t in texts).lower()
    return bool(_SEARCH_FIGURE_RE.search(text))

KNOWN_OUTLETS: dict[str, str] = {
    "welivesecurity": "WeLiveSecurity (ESET)",
    "malwarebytes": "Malwarebytes",
    "rockstargames": "Rockstar Games",
}
"""Nome do veículo pelo site, quando o roteiro não tem fonte desse site para dar o nome (o blog da ESET é a
WeLiveSecurity: o domínio sozinho não diz "ESET")."""

_SENTENCE_END = (".", "!", "?", "…", ";")
# Número falado até este valor, sem casa decimal, só liga a cena à fonte quando conta a mesma coisa que a nota ("três
# modelos" ~ "3 modelos") ou é porcentagem: "duas etapas", "cinco sinais" e o "6" de "GTA 6" aparecem em qualquer nota
# ("PS5", "1º trimestre").
_BARE_NUMBER_MAX = 12


def source_label(source: Source) -> str:
    """Nome curto da fonte para o cartão: o título até o travessão ou os dois pontos, com até 40 caracteres
    ("Guinness World Records — recorde do trailer" → "Guinness World Records")."""
    title = re.split(r"\s+[—–|]\s+|\s+-\s+|:\s+", (source.title or "").strip(), maxsplit=1)[0].strip()
    if len(title) > 40:
        head = title[:41]
        title = (head.rsplit(" ", 1)[0] if " " in head else head[:40]).rstrip(" ,;:-")
    return title


def outlet_for_url(url: str, sources: Iterable[Source]) -> str | None:
    """Quem assina a página deste endereço, para o "Fonte:" da captura: a fonte do roteiro do mesmo site (o nome dela
    no cartão), o veículo conhecido do site ("welivesecurity.com" → "WeLiveSecurity (ESET)") ou "" para a página de
    busca ("google.com": não credita ninguém). None quando não dá para saber."""
    site = _domain_name(url)
    if not site:
        return None
    if site in SEARCH_SITES:
        return ""
    for source in sources:
        if _domain_name(source.url or "") == site and (label := source_label(source)):
            return label
    return KNOWN_OUTLETS.get(site)


def outlet_names(sources: Iterable[Source]) -> list[str]:
    """Os nomes de veículo do roteiro: o nome curto de cada fonte (`source_label`) só quando ele é o do site dela
    ("Windows Central" de windowscentral.com, "WeLiveSecurity (ESET)" de welivesecurity.com; um título sem o nome do
    veículo, "GTA 6 release date", não vale), o nome do site no endereço e os veículos conhecidos (`KNOWN_OUTLETS`)."""
    out: list[str] = []
    for source in sources:
        site = _domain_name(source.url or "")
        if len(site) >= 3:
            out.append(site)
        label = source_label(source)
        key = _compact_name(label)
        if site and len(key) >= 3 and len(site) >= 3 and (site in key or key in site):
            out.append(label)
    out += list(KNOWN_OUTLETS.values())
    return list(dict.fromkeys(out))


def scene_sentence(doc: ProjectDoc, scene: Scene) -> str:
    """As frases da narração que a cena cobre, inteiras (idioma principal): a fala da cena estendida até o começo e o
    fim das frases dela ("Em agosto, a Malwarebytes encontrou sites | com nomes tipo GTA 6 demo" vale nas duas)."""
    try:
        text = doc.segment(scene.segment_id).text_for(doc.primary_language)
    except KeyError:
        return ""
    tokens = words(text)
    if not tokens:
        return ""
    siblings = doc.scenes_of(scene.segment_id)
    index = next((i for i, s in enumerate(siblings) if s.id == scene.id), 0)
    if siblings and all(s.span is not None for s in siblings):
        ranges = span_ranges([s.span or 0 for s in siblings], len(tokens))
    else:
        ranges = split_word_indices(len(tokens), len(siblings) or 1)
    start, end = ranges[index] if index < len(ranges) else (0, len(tokens))
    if start >= end:
        return ""
    while start > 0 and not tokens[start - 1].endswith(_SENTENCE_END):
        start -= 1
    while end < len(tokens) and not tokens[end - 1].endswith(_SENTENCE_END):
        end += 1
    return " ".join(tokens[start:end])


def _name_keys(cited: str, own: list[Source]) -> set[str]:
    """Como o texto nomeia a fonte: o nome inteiro e cada parte ("WeLiveSecurity (ESET)" → "welivesecurity",
    "eset"), a primeira palavra longa de um nome composto ("Rockstar Games" → "rockstar") e o nome do site."""
    keys: set[str] = set()
    for part in (cited, *re.split(r"[()\[\]·|,/]", cited)):
        key = _compact_name(part)
        if len(key) >= 3:
            keys.add(key)
        head = part.split()
        first = _compact_name(head[0]) if head else ""
        if len(first) >= 6:
            keys.add(first)
    keys |= {name for s in own if len(name := _compact_name(_domain_name(s.url or ""))) >= 4}
    return keys


def names_source(text: str, cited: str, own: list[Source] | None = None) -> bool:
    """O texto cita a fonte pelo nome ("a ESET descreve", "segundo a GamesBeat", "a Malwarebytes encontrou")."""
    keys = _name_keys(cited, own or [])
    tokens = [t for t in (_compact_name(raw) for raw in re.split(r"[^\w]+", text or "")) if t]
    grams = set(tokens) | {a + b for a, b in zip(tokens, tokens[1:], strict=False)}
    return bool(keys & grams)


def _claim_numbers(text: str, lang: str, topic: set[float]) -> list[tuple[float, str]]:
    """Os números do texto que podem ser dado de uma fonte, com o que vem junto: "%" na porcentagem ("5%", "cinco por
    cento"), a palavra seguinte no número pequeno inteiro ("três modelos" → (3, "modelos")) e "" no resto (número
    grande ou com casa decimal vale sozinho). Fora os números do assunto do vídeo (o "6" de "GTA 6")."""
    # "~1,1 MB", "≈19": o sinal de aproximação grudado no número esconde o número do leitor.
    tokens = words(re.sub(r"[~≈±]", " ", text or ""))
    out: list[tuple[float, str]] = []
    for start, end, raw in number_runs(tokens, lang, names=True):
        value = abs(float(raw))
        if value in topic:
            continue
        run = " ".join(tokens[start:end]).lower()
        if "%" in run or run.rstrip(".,;:!?").endswith("cento") or "percent" in run:
            out.append((value, "%"))
        elif value > _BARE_NUMBER_MAX or value != int(value):
            out.append((value, ""))
        elif end < len(tokens) and not tokens[end - 1].endswith((".", ",", ";", ":", "!", "?")):
            noun = normalize_token(tokens[end])
            if len(noun) >= 3 and not noun.isdigit():
                out.append((value, noun))
    return out


def _same_claim(said: tuple[float, str], held: tuple[float, str]) -> bool:
    """O mesmo dado: mesmo valor e, no número pequeno, a mesma coisa contada ("três modelos" ~ "3 modelos"); a
    porcentagem casa com porcentagem. "duas etapas" não casa com "2 milhões" nem com "PS5"."""
    (value, unit), (other, other_unit) = said, held
    if abs(value - other) > 1e-6 * max(1.0, other):
        return False
    if unit == "%" or other_unit == "%":
        return unit == other_unit
    if unit == "" or other_unit == "":
        return value > _BARE_NUMBER_MAX or value != int(value)
    return _same_word(unit, other_unit)


def scene_claims_source(doc: ProjectDoc, scene: Scene, cited: str) -> bool:
    """A cena diz algo da fonte que o "Fonte: X" cita? Diz quando a frase da narração (inteira, `scene_sentence`) ou o
    texto na tela nomeia a fonte ("a ESET descreve"), traz um número dela (título e nota; fora número pequeno sem casa
    decimal que não conta a mesma coisa que a nota, e os do assunto do vídeo, como o "6" de "GTA 6"), quando o carimbo
    é veredito positivo explícito ("CONFIRMADO" com "Fonte: Rockstar Games": quem confirma é ela; `verdict.is_positive`,
    "ATENÇÃO" e "BOATO" não contam) ou, fora o cartão tipográfico, quando o título ou a faixa dizem algo dela (`title_backs_source`; o título sozinho segue `title_keeps_source`).

    Sem nada disso, o "Fonte: X" vira crédito de quem não disse aquilo: o conselho "cartão ou Pix, fala com o seu
    banco" creditado à Malwarebytes, "ou de horas de nuvem" à Windows Central, a busca do Google à matéria que a
    reproduz. O cartão tipográfico é a fala do apresentador: palavra comum com a nota ("nuvem") não basta. Fonte fora
    do roteiro (não dá para conferir) passa."""
    cited = (cited or "").strip()
    if not cited:
        return False
    own = [s for s in doc.sources if cites_source(s, cited)]
    if not own:
        return True
    lang = doc.primary_language
    card = scene.card
    stamp = (card.stamp.get(lang) or "") if card is not None else ""
    if verdict.is_positive(stamp):
        return True
    shown = [scene.overlay_text.get(lang) or "", (card.headline.get(lang) or "") if card is not None else ""]
    said = " ".join([scene_sentence(doc, scene), *shown])
    if names_source(said, cited, own):
        return True
    topic = {abs(float(v)) for v in text_numbers(f"{doc.title}. {doc.topic}", lang, names=True)}
    held = [n for s in own for part in (s.title, s.note) for n in _claim_numbers(part or "", lang, set())]
    if any(_same_claim(v, h) for v in _claim_numbers(said, lang, topic) for h in held):
        return True
    if scene.kind == "text":
        return False
    if _is_lone_title(scene):
        return title_keeps_source(doc, scene, cited)
    return any(title_backs_source(_LIST_INDEX_RE.sub("", t), cited, doc.sources, lang) for t in shown if t.strip())
