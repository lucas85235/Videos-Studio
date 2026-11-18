"""Cartão tipográfico (a fala na tela sobre o fundo do estilo, `sources.is_typographic`): o último recurso.

A cena fica sem material por vários caminhos (a captura de outro assunto no anexo, o cartão do storyboard sem real que
caiba de fundo, o teto de IA ou de material oficial, o item que não baixou). Sozinho, cada caminho virava cartão
tipográfico, e o QA do prj_gq4krrs11w achou 27% do vídeo em fundo roxo liso com a legenda palavra a palavra, em blocos
de ~21 s e no fim do vídeo depois do CTA. `settle` roda depois do anexo e decide, cena a cena, na ordem do vídeo:

1. outro material real do MESMO assunto da cena, de qualquer nível (`_subject_item`);
2. com avatar no canal, o cartão que cabe nas regras abaixo fica: o avatar entra nele (`presenter.assign`);
3. trecho oficial (ou próprio) do tema geral do vídeo, sem repetir cedo (`_theme_item`);
4. imagem de IA, só dentro do teto de IA (média e trecho);
5. só então o cartão tipográfico, e só quando cabe: nunca dois seguidos, no máximo `TYPO_MAX_S`, um a cada
   `TYPO_WINDOW_S`, até `TYPO_MAX_SHARE` do vídeo e nunca no fim do vídeo (`END_ZONE_S`, o CTA e a despedida).

O cartão que não cabe nas regras e não achou nada acima mostra o tema geral mesmo repetindo (o item menos usado) ou,
sem nenhum, a imagem real da cena mais perto (`_forced`). A cena travada não muda."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from studio.core import sources as tiers
from studio.core.project.document import ProjectDoc, Scene, Visual
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.schema import StyleSpec
from studio.workers.pipeline import media_pool, storyboard_check

log = logging.getLogger(__name__)

TYPO_MAX_S = 4.0
"""O cartão tipográfico mais longo (tempo estimado pela fala): a frase cabe inteira e não vira quadro morto."""
TYPO_WINDOW_S = 20.0
"""Distância mínima entre o começo de dois cartões tipográficos."""
TYPO_MAX_SHARE = 0.08
"""Parte do vídeo que pode ser cartão tipográfico."""
END_ZONE_S = 20.0
"""Os últimos segundos do vídeo (o CTA e a despedida): nunca cartão tipográfico. O segmento de CTA inteiro também."""
SUBJECT_TIERS = ("own", "official", "capture", "free", "stock")
"""Ordem de preferência do material do mesmo assunto (o próprio e o oficial primeiro)."""
THEME_TIERS = ("own", "official")
"""O material do tema geral do vídeo: o próprio canal e a fonte oficial (trailer, arte). Captura é a prova de um fato e
foto de banco é palavra-chave: nenhuma das duas ilustra o tema solta."""
FORCED_OFFICIAL_SLACK = 0.10
"""Folga no teto de material oficial só para a cena que, sem material, ficaria num cartão tipográfico fora das regras
(colado em outro, longo demais, no fim do vídeo): uns segundos a mais de trailer valem mais que 20 s de fundo liso."""
AI_REASON = "o acervo não tem material real deste trecho"


@dataclass
class _State:
    doc: ProjectDoc
    style: StyleSpec
    pool: Pool
    avatar: bool
    common: frozenset[str]
    order: list[Scene] = field(default_factory=list)
    spans: dict[str, tuple[float, float]] = field(default_factory=dict)
    total: float = 0.0
    kept: list[str] = field(default_factory=list)
    """Cartões tipográficos que ficam (na ordem do vídeo)."""

    def refresh(self) -> None:
        self.order = self.doc.ordered_scenes()
        self.spans, t = {}, 0.0
        for scene, _prov, seconds in storyboard_check.screen_seconds(self.doc, self.style):
            self.spans[scene.id] = (t, t + seconds)
            t += seconds
        self.total = t

    def seconds(self, scene: Scene) -> float:
        a, b = self.spans.get(scene.id, (0.0, 0.0))
        return b - a


def _end_zone(state: _State, scene: Scene) -> bool:
    """A cena é do fim do vídeo: o segmento de CTA ou os últimos `END_ZONE_S` (a despedida depois do CTA)."""
    try:
        role = state.doc.segment(scene.segment_id).role
    except KeyError:
        role = None
    if role == "cta":
        return True
    end = state.spans.get(scene.id, (0.0, 0.0))[1]
    return state.total > 0 and end > state.total - END_ZONE_S + 1e-6


def card_problem(state: _State, scene: Scene) -> str | None:
    """Por que o cartão tipográfico não pode ficar nesta cena (None: pode). Conta com os cartões que já ficaram
    (`state.kept`) e com os vizinhos como estão agora."""
    if _end_zone(state, scene):
        return "fim do vídeo"
    seconds = state.seconds(scene)
    if seconds > TYPO_MAX_S + 1e-6:
        return f"mais de {TYPO_MAX_S:.0f} s"
    at = next((k for k, s in enumerate(state.order) if s.id == scene.id), -1)
    for k in (at - 1, at + 1):
        if 0 <= k < len(state.order) and (state.order[k].id in state.kept or (
                k == at - 1 and tiers.is_typographic(state.order[k], state.doc))):
            return "outro cartão tipográfico colado"
    start = state.spans.get(scene.id, (0.0, 0.0))[0]
    for other in state.kept:
        if abs(state.spans.get(other, (0.0, 0.0))[0] - start) < TYPO_WINDOW_S - 1e-6:
            return f"outro cartão tipográfico a menos de {TYPO_WINDOW_S:.0f} s"
    used = sum(state.seconds(s) for s in state.order if s.id in state.kept)
    if state.total > 0 and used + seconds > TYPO_MAX_SHARE * state.total + 1e-6:
        return f"passaria de {round(TYPO_MAX_SHARE * 100)}% do vídeo em cartão tipográfico"
    return None


def _shown(state: _State) -> list[tuple[frozenset[str], tuple[float, float]]]:
    """O que cada cena mostra agora (o item do acervo, o arquivo), e onde: para contar as aparições."""
    out = []
    for scene in state.order:
        media = state.doc.shown_media(scene)
        tokens = tiers.media_tokens(media)
        if tokens and scene.id in state.spans:
            out.append((tokens, state.spans[scene.id]))
    return out


def _uses(state: _State, item: PoolItem) -> int:
    tokens = tiers.item_tokens(item)
    return sum(1 for shown, _span in _shown(state) if shown & tokens)


def _spread_ok(state: _State, scene: Scene, item: PoolItem, *, gap: bool = True) -> bool:
    """O item cabe onde a cena está (`sources.reuse_problem`). Com `gap=False` (o cartão que não pode ficar), só não
    emenda no mesmo item da cena vizinha."""
    mine = [state.spans[scene.id]] if scene.id in state.spans else []
    tokens = tiers.item_tokens(item)
    others = [span for shown, span in _shown(state) if shown & tokens]
    if not gap:
        a, b = mine[0] if mine else (0.0, 0.0)
        return not any(o_a - b < tiers.TOUCH_S and a - o_b < tiers.TOUCH_S for o_a, o_b in others)
    return tiers.reuse_problem(others, mine, max_run_s=storyboard_check.same_image_seconds(state.style)) is None


def _official_room(state: _State, scene: Scene, slack: float = 0.0) -> bool:
    """A cena cabe no teto de material oficial (`official_max_share`, mais `slack` do vídeo). Conta junto o tempo das
    cenas que reaproveitam a imagem dela (o cartão sobre o fundo dela passa a mostrar o mesmo material)."""
    mine = {s.id for s in state.order if s.id == scene.id or (
        s.visual.media is None and s.visual.reuse_of and (root := state.doc.reuse_root(s)) is not None
        and root.id == scene.id)}
    official = sum(state.seconds(s) for s in state.order
                   if s.id not in mine and tiers.prov_of_scene(s, state.doc) == "official")
    cap = (state.style.art.sources.official_max_share + slack) * state.total
    return official + sum(state.seconds(s) for s in state.order if s.id in mine) <= cap + 1e-6


def _usable(item: PoolItem) -> bool:
    return tiers.is_real(item.tier) and item.media != "meme" and not media_pool.is_synthetic(item)


def _plain_texts(texts: tuple[str, ...]) -> tuple[str, ...]:
    """Os textos da cena sem o prompt de reserva (cenário em inglês) e sem a frase inteira (só a captura usa)."""
    return tuple(t for t in texts if not isinstance(t, media_pool.ScenePrompt | media_pool.Sentence))


def _subject_item(state: _State, scene: Scene) -> PoolItem | None:
    """O material real do acervo do mesmo assunto da cena, de qualquer nível: passa em `media_pool.fits_scene` e,
    nos níveis que sempre passam (próprio, oficial, foto livre), divide com a fala uma palavra de conteúdo que não é do
    assunto do vídeo inteiro (o trailer não é "do assunto" de uma fala sobre a verificação em duas etapas). Cabe na
    distribuição e, se oficial, no teto de material oficial; o menos usado e o que divide mais palavras."""
    texts = media_pool._scene_texts(state.doc, scene)
    said = media_pool.content_terms(*_plain_texts(texts)) - state.common
    rank = {t: k for k, t in enumerate(SUBJECT_TIERS)}
    room = _official_room(state, scene)
    best: tuple[Any, ...] | None = None
    chosen: PoolItem | None = None
    for index, item in enumerate(state.pool.items):
        if not _usable(item) or item.tier not in rank or (item.tier == "official" and not room):
            continue
        if not media_pool.fits_scene(item, *texts, common=state.common):
            continue
        shared = media_pool.content_terms(item.desc, *item.tags, *item.entities) & said
        keyword = item.tier in media_pool.KEYWORD_TIERS or media_pool.is_source_capture(item)
        if not keyword and not shared:
            continue
        if not _spread_ok(state, scene, item):
            continue
        key = (_uses(state, item), -len(shared), rank[item.tier], -item.score, index)
        if best is None or key < best:
            best, chosen = key, item
    return chosen


def _shows_text(item: PoolItem) -> bool:
    """O coletor anotou letras no quadro (`· texto "..."`: o aviso de classificação, um letreiro)."""
    return 'texto "' in (item.desc or "") or bool(item.rects)


def _theme_item(state: _State, scene: Scene, *, gap: bool = True) -> PoolItem | None:
    """Trecho oficial (ou próprio) do tema geral do vídeo: o vídeo antes da imagem, sem letras no quadro antes do com
    letras, o menos usado. Só dentro do teto de material oficial. Com `gap=False` (o cartão que não pode ficar),
    repete o menos usado (só não emenda no mesmo item) e o teto ganha a folga `FORCED_OFFICIAL_SLACK`."""
    if not _official_room(state, scene, 0.0 if gap else FORCED_OFFICIAL_SLACK):
        return None
    best: tuple[Any, ...] | None = None
    chosen: PoolItem | None = None
    for index, item in enumerate(state.pool.items):
        if not _usable(item) or item.tier not in THEME_TIERS or media_pool.is_source_capture(item):
            continue
        if item.media not in ("clip", "image", "video"):
            continue
        if not _spread_ok(state, scene, item, gap=gap):
            continue
        key = (item.media == "image", _shows_text(item), _uses(state, item), THEME_TIERS.index(item.tier),
               -item.score, index)
        if best is None or key < best:
            best, chosen = key, item
    return chosen


def _ai_fits(state: _State, scene: Scene) -> bool:
    """A cena pode virar imagem de IA sem passar do teto de IA (média do vídeo e trecho de `AI_WINDOW_S`)."""
    policy = state.style.art.sources
    if state.total <= 0 or policy.ai_max_share <= 0:
        return False
    spans = []
    ai = 0.0
    for s in state.order:
        weight = 1.0 if s.id == scene.id else tiers.ai_weight(s, state.doc)
        if weight > 0 and s.id in state.spans:
            a, b = state.spans[s.id]
            spans.append((a, b, weight))
            ai += (b - a) * weight
    if ai > policy.ai_max_share * state.total + 1e-6:
        return False
    share, _at = tiers.window_share(spans, state.total)
    return share <= tiers.ai_window_cap(policy.ai_max_share) + 1e-6


def _to_image(scene: Scene) -> None:
    """Sem frase própria na tela (o texto do cartão), a cena vira cena de imagem: a legenda mostra a fala. Com a
    frase, a imagem vira o fundo do cartão."""
    if not any(t.strip() for t in scene.overlay_text.values()):
        scene.kind = "image"
        scene.motion = None if scene.motion == "static" else scene.motion


def _show_item(scene: Scene, item: PoolItem) -> None:
    """A cena passa a mostrar o item do acervo (pendente de anexo; sem prompt de reserva: o que não anexar volta para
    cá na 2ª volta, nunca para a IA sem conferir o teto)."""
    from studio.workers.pipeline.authoring import media_from_pool

    scene.visual = Visual(seed=scene.visual.seed, media=media_from_pool(item))
    _to_image(scene)


def _reuse(scene: Scene, near: Scene) -> None:
    scene.visual = Visual(seed=scene.visual.seed, reuse_of=near.id)
    _to_image(scene)


def _show_ai(state: _State, scene: Scene) -> None:
    from studio.core.project.ops import scene_text

    language = next((s.visual.language for s in state.order if s.visual.language and s.id != scene.id
                     and tiers.prov_of_scene(s, state.doc) == "ai"), None) or scene.visual.language
    prompt = scene.visual.prompt.strip() or scene_text(state.doc, scene)
    scene.visual = Visual(prompt=prompt, seed=scene.visual.seed, language=language, ai_reason=AI_REASON)
    _to_image(scene)


def _nearest_real(state: _State, scene: Scene) -> Scene | None:
    """A cena que empresta a imagem real ao cartão que não pode ficar sem nada: a que mostra material do mesmo assunto
    (o item do acervo dela passa em `fits_scene` com esta cena) ou do tema geral (próprio ou oficial, sem captura);
    nunca a foto de banco ou a captura de outro assunto (papel de parede). A menos emprestada e a mais perto, sem
    emendar na vizinha. Material oficial só dentro do teto de material oficial com a folga `FORCED_OFFICIAL_SLACK`."""
    at = next((k for k, s in enumerate(state.order) if s.id == scene.id), -1)
    room = _official_room(state, scene, FORCED_OFFICIAL_SLACK)
    texts = media_pool._scene_texts(state.doc, scene)
    lent: dict[str, int] = {}
    for other in state.order:
        root = state.doc.reuse_root(other) if other.visual.reuse_of and other.visual.media is None else None
        if root is not None:
            lent[root.id] = lent.get(root.id, 0) + 1
    options = []
    for k, other in enumerate(state.order):
        media = other.visual.media
        if k == at or media is None or media_pool.needs_attach(media, state.doc.id) or not (media.asset or media.video):
            continue
        prov = tiers.prov_of_media(media)
        if not room and prov == "official":
            continue
        item = state.pool.item(media.pool_id) if media.pool_id else None
        subject = item is not None and media_pool.fits_scene(item, *texts, common=state.common) and (
            item.tier in media_pool.KEYWORD_TIERS or media_pool.is_source_capture(item)
            or bool(media_pool.content_terms(item.desc, *item.tags, *item.entities)
                    & (media_pool.content_terms(*_plain_texts(texts)) - state.common)))
        theme = prov in THEME_TIERS and media.tier != "capture" and media.kind != "capture"
        if not (subject or theme):
            continue
        options.append((abs(k - at) <= 1, not subject, lent.get(other.id, 0), abs(k - at), k > at, k, other))
    return min(options, key=lambda o: o[:6])[6] if options else None


def _label(state: _State, scene: Scene) -> str:
    return media_pool._label(state.doc, scene)


def _decide(state: _State, scene: Scene, notes: list[str]) -> bool:
    """A hierarquia numa cena sem material. Devolve True quando deixou mídia pendente de anexo."""
    problem = card_problem(state, scene)
    item = _subject_item(state, scene)
    if item is not None:
        _show_item(scene, item)
        notes.append(f"{_label(state, scene)}: em vez do cartão tipográfico, mostra material real do assunto "
                     f"({item.id}, {item.tier}).")
        return True
    if state.avatar and problem is None:
        state.kept.append(scene.id)
        return False
    theme = _theme_item(state, scene)
    if theme is not None:
        _show_item(scene, theme)
        notes.append(f"{_label(state, scene)}: em vez do cartão tipográfico, mostra material oficial do tema do vídeo "
                     f"({theme.id}).")
        return True
    if _ai_fits(state, scene):
        _show_ai(state, scene)
        notes.append(f"{_label(state, scene)}: em vez do cartão tipográfico, ganha imagem de IA (dentro do teto de IA).")
        return False
    if problem is None:
        state.kept.append(scene.id)
        return False
    return _forced(state, scene, problem, notes)


def _forced(state: _State, scene: Scene, problem: str, notes: list[str]) -> bool:
    """O cartão que não pode ficar (`problem`) e não achou material novo: o tema geral repetindo o menos usado, ou o
    fundo da imagem real mais perto."""
    theme = _theme_item(state, scene, gap=False)
    if theme is not None:
        again = ", mesmo repetido" if _uses(state, theme) else ""
        _show_item(scene, theme)
        notes.append(f"{_label(state, scene)}: o cartão tipográfico não cabia ali ({problem}); mostra material oficial "
                     f"do tema do vídeo ({theme.id}){again}.")
        return True
    near = _nearest_real(state, scene)
    if near is not None:
        _reuse(scene, near)
        notes.append(f"{_label(state, scene)}: o cartão tipográfico não cabia ali ({problem}); repete a imagem real "
                     f"da {_label(state, near)}.")
        return False
    state.kept.append(scene.id)
    notes.append(f"{_label(state, scene)}: ficou cartão tipográfico ({problem}): o acervo não tem material real nem "
                 "oficial para o lugar e o teto de IA acabou.")
    return False


def settle(doc: ProjectDoc, style: StyleSpec | None, pool: Pool, *, avatar: bool | None = None,
           attach: Callable[[ProjectDoc], list[str]] | None = None) -> list[str]:
    """Resolve, em `doc` (no lugar), as cenas que ficariam em cartão tipográfico (ver o módulo). `avatar`: o canal
    tem avatar no vídeo (None: lê do projeto). `attach`: anexa a mídia pendente (padrão: `media_pool.attach_chosen`
    com o acervo). Devolve os avisos."""
    if style is None:
        from studio.core.styles.library import get_style

        try:
            style = get_style(doc.style_id, doc.style_overrides)
        except Exception:  # noqa: BLE001 - sem estilo não há tempo de tela: nada muda
            return []
    if avatar is None:
        avatar = _has_avatar(doc, style)
    attach = attach or (lambda d: media_pool.attach_chosen(d, pool))
    state = _State(doc=doc, style=style, pool=pool, avatar=avatar, common=media_pool.video_terms(doc))
    notes: list[str] = []
    for round_ in range(2):
        state.refresh()
        state.kept = []
        pending = False
        # O fim do vídeo primeiro: a tela final nunca fica sem material porque o meio gastou a folga do teto.
        for scene in sorted(state.order, key=lambda s: not _end_zone(state, s)):
            if scene.locked or not tiers.is_typographic(scene, doc):
                continue
            state.refresh()
            if round_ == 0:
                pending |= _decide(state, scene, notes)
            else:
                # Depois do anexo: o que não baixou (ou caiu de novo em cartão) só tem a reserva sem download.
                problem = card_problem(state, scene)
                if problem is None:
                    state.kept.append(scene.id)
                else:
                    near = _nearest_real(state, scene)
                    if near is not None:
                        _reuse(scene, near)
                        notes.append(f"{_label(state, scene)}: o material não anexou e o cartão tipográfico não "
                                     f"cabia ali ({problem}); repete a imagem real da {_label(state, near)}.")
                    else:
                        state.kept.append(scene.id)
        if not pending:
            break
        try:
            notes += attach(doc)
        except Exception:  # noqa: BLE001 - sem anexo, a 2ª volta usa só o que já está no projeto
            log.warning("o anexo do material dos cartões tipográficos falhou", exc_info=True)
    return notes


def _has_avatar(doc: ProjectDoc, style: StyleSpec) -> bool:
    try:
        from studio.core.project.presenter import project_presenter

        presenter = project_presenter(doc, style)
    except Exception:  # noqa: BLE001 - sem banco ou sem canal: sem avatar
        return False
    return presenter is not None and bool(presenter.poses)
