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

O cartão também só fica quando mostra uma ideia inteira (`_whole_idea`): a frase própria do cartão ou a fala da cena
começando e terminando numa pausa, com pelo menos `TYPO_MIN_WORDS` palavras (nunca "e" sozinho nem "até com a").

O cartão que não cabe nas regras e não achou nada acima mostra o tema geral mesmo voltando antes de
`ITEM_REUSE_GAP_S` ou, sem nenhum, a imagem real da cena mais perto (`_forced`). Os tetos nunca são furados: o de
material oficial (risco de Content ID), o de IA (média e trecho), as aparições do mesmo item (`ITEM_MAX_USES`), o tempo
seguido na mesma imagem ou no mesmo vídeo de origem (`same_image_seconds`) e o tempo seguido no mesmo nível de material
(`SAME_TIER_RUN_S`). Tudo conta a cena junto das que reaproveitam a imagem dela (`_group`). Sem nada que caiba, o
cartão fica fora das regras e o aviso diz por quê. A cena travada não muda."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from studio.core import sources as tiers
from studio.core.project.document import MediaRef, ProjectDoc, Scene, Visual
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.schema import StyleSpec
from studio.media.lookalike import LookIndex
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
TYPO_MIN_WORDS = 3
"""Menos palavras que isto não é ideia inteira: "e", "que", "Então vamos" viram quadro de uma ou duas palavras."""
SAME_TIER_RUN_S = storyboard_check.SAME_TIER_RUN_S
"""O mesmo nível de material seguido (o trailer emendado no trailer): o preenchimento não passa disto."""
PROMPT_MIN_WORDS = 6
PROMPT_MAX_WORDS = 90
"""O cenário de IA tem de 1 a 3 frases (sujeito, ação, lugar, hora e luz)."""
SCENARIO_OVERLAP = 0.5
"""Prompt com esta parte das palavras ditas na fala é a própria fala (a reserva antiga), não um cenário: a imagem de
IA feita dele sai genérica."""
_CLAUSE_END = re.compile(r"[.!?…:;,][\"'”’»)\]]*$")
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
    provs: dict[str, str] = field(default_factory=dict)
    total: float = 0.0
    kept: list[str] = field(default_factory=list)
    """Cartões tipográficos que ficam (na ordem do vídeo)."""
    prompts: dict[str, str] = field(default_factory=dict)
    """Cenário de IA escrito para a cena que não tinha um (`write_prompts`)."""
    look: LookIndex = field(default_factory=LookIndex)
    """A mesma imagem noutra fonte conta como o mesmo material (`media.lookalike`)."""

    def refresh(self) -> None:
        self.order = self.doc.ordered_scenes()
        self.spans, self.provs, t = {}, {}, 0.0
        for scene, prov, seconds in storyboard_check.screen_seconds(self.doc, self.style):
            self.spans[scene.id] = (t, t + seconds)
            self.provs[scene.id] = prov
            t += seconds
        self.total = t

    def seconds(self, scene: Scene) -> float:
        a, b = self.spans.get(scene.id, (0.0, 0.0))
        return b - a

    def index(self, scene: Scene) -> int:
        return next((k for k, s in enumerate(self.order) if s.id == scene.id), -1)


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


def _whole_idea(state: _State, scene: Scene) -> bool:
    """O cartão mostra uma ideia inteira: a frase própria dele (`overlay_text`) ou a fala da cena, quando ela começa
    e termina numa pausa (a cena anterior fecha com pontuação, a fala da cena também) e tem pelo menos
    `TYPO_MIN_WORDS` palavras. A cena que corta a frase no meio daria "dependendo do site até com a" na tela."""
    from studio.core.project.ops import scene_text

    if any(t.strip() for t in scene.overlay_text.values()):
        return True
    said = scene_text(state.doc, scene).strip()
    if len(said.split()) < TYPO_MIN_WORDS or not _CLAUSE_END.search(said):
        return False
    at = state.index(scene)
    before = scene_text(state.doc, state.order[at - 1]).strip() if at > 0 else ""
    return not before or bool(_CLAUSE_END.search(before))


def card_problem(state: _State, scene: Scene) -> str | None:
    """Por que o cartão tipográfico não pode ficar nesta cena (None: pode). Conta com os cartões que já ficaram
    (`state.kept`) e com os vizinhos como estão agora."""
    if _end_zone(state, scene):
        return "fim do vídeo"
    if not _whole_idea(state, scene):
        return "a fala da cena é um pedaço de frase"
    seconds = state.seconds(scene)
    if seconds > TYPO_MAX_S + 1e-6:
        return f"mais de {TYPO_MAX_S:.0f} s"
    at = state.index(scene)
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


def _group(state: _State, scene: Scene) -> list[Scene]:
    """A cena e as que reaproveitam a imagem dela (`reuse_of` sem mídia própria): o que a cena passar a mostrar, elas
    mostram também. Todo teto conta o grupo inteiro."""
    out = []
    for s in state.order:
        if s.id == scene.id:
            out.append(s)
        elif s.visual.media is None and s.visual.reuse_of and not s.visual.asset:
            root = state.doc.reuse_root(s)
            if root is not None and root.id == scene.id:
                out.append(s)
    return out


def _spans_of(state: _State, scenes: list[Scene]) -> list[tuple[float, float]]:
    return [state.spans[s.id] for s in scenes if s.id in state.spans]


def _source_of_media(media: MediaRef | None) -> str | None:
    """O vídeo de origem do trecho (o trailer): trechos dele são planos diferentes para as aparições
    (`media_tokens`), mas emendados viram o mesmo vídeo na tela (o `imagem_seguida` do storyboard)."""
    if media is None or not media.url or not (media.kind == "clip" or media.video):
        return None
    return media.url.lower()


def _source_of_item(item: PoolItem) -> str | None:
    return item.url.lower() if item.media == "clip" and item.url else None


def _shown(state: _State) -> list[tuple[str, frozenset[str], str | None, tuple[float, float]]]:
    """O que cada cena mostra agora (o item do acervo, o arquivo, o vídeo de origem), e onde: para contar as
    aparições."""
    out = []
    for scene in state.order:
        media = state.doc.shown_media(scene)
        tokens = tiers.media_tokens(media, state.look)
        if tokens and scene.id in state.spans:
            out.append((scene.id, tokens, _source_of_media(media), state.spans[scene.id]))
    return out


def _uses(state: _State, item: PoolItem) -> int:
    tokens = tiers.item_tokens(item, state.look)
    return sum(1 for _id, shown, _src, _span in _shown(state) if shown & tokens)


def _placement_ok(state: _State, scene: Scene, tokens: frozenset[str], source: str | None, *,
                  gap: bool = True) -> bool:
    """O material (`tokens`, do vídeo de origem `source`) cabe onde a cena e o grupo dela estão: as aparições e a
    distância entre elas (`sources.reuse_problem`; com `gap=False`, a do cartão que não pode ficar, só a distância
    mínima cai) e o tempo seguido na mesma imagem ou no mesmo vídeo de origem (`same_image_seconds`)."""
    group = _group(state, scene)
    ids = {s.id for s in group}
    mine = _spans_of(state, group)
    shown = [x for x in _shown(state) if x[0] not in ids]
    limit = storyboard_check.same_image_seconds(state.style)
    others = [span for _id, toks, _src, span in shown if toks & tokens]
    if tiers.reuse_problem(others, mine, gap_s=tiers.ITEM_REUSE_GAP_S if gap else 0.0, max_run_s=limit) is not None:
        return False
    if source is None:
        return True
    same = [span for _id, _toks, src, span in shown if src == source]
    return tiers.reuse_problem(same, mine, max_uses=10**6, gap_s=0.0, max_run_s=limit) is None


def _spread_ok(state: _State, scene: Scene, item: PoolItem, *, gap: bool = True) -> bool:
    return _placement_ok(state, scene, tiers.item_tokens(item, state.look), _source_of_item(item), gap=gap)


def _tier_run_ok(state: _State, scene: Scene, prov: str) -> bool:
    """Com a cena (e o grupo dela) mostrando material oficial, o trecho seguido de material oficial não passa de
    `SAME_TIER_RUN_S` (o trailer emendado no trailer por 45 s). Os outros níveis passam: o material próprio seguido é
    o melhor caso, e a captura e a foto já têm o teto de aparições."""
    if prov != "official":
        return True
    ids = {s.id for s in _group(state, scene)}
    at = state.index(scene)
    if at < 0:
        return True

    def prov_at(k: int) -> str:
        s = state.order[k]
        return prov if s.id in ids else state.provs.get(s.id, "")

    total = state.seconds(scene)
    for step in (-1, 1):
        k = at + step
        while 0 <= k < len(state.order) and prov_at(k) == prov:
            total += state.seconds(state.order[k])
            k += step
    return total <= SAME_TIER_RUN_S + 1e-6


def _official_room(state: _State, scene: Scene) -> bool:
    """A cena cabe no teto de material oficial (`official_max_share`, o risco de Content ID). Conta junto o tempo das
    cenas que reaproveitam a imagem dela (o cartão sobre o fundo dela passa a mostrar o mesmo material)."""
    mine = {s.id for s in _group(state, scene)}
    official = sum(state.seconds(s) for s in state.order
                   if s.id not in mine and tiers.prov_of_scene(s, state.doc) == "official")
    cap = state.style.art.sources.official_max_share * state.total
    return official + sum(state.seconds(s) for s in state.order if s.id in mine) <= cap + 1e-6


def _usable(item: PoolItem) -> bool:
    return tiers.is_real(item.tier) and item.media != "meme" and not media_pool.is_synthetic(item)


def _plain_texts(texts: tuple[str, ...]) -> tuple[str, ...]:
    """Os textos da cena sem o prompt de reserva (cenário em inglês) e sem a frase inteira (só a captura usa)."""
    return tuple(t for t in texts if not isinstance(t, media_pool.ScenePrompt | media_pool.Sentence))


def _subject_item(state: _State, scene: Scene) -> PoolItem | None:
    """O material real do acervo do mesmo assunto da cena, de qualquer nível (`_subject_fits`, para a cena e as que
    reaproveitam a imagem dela: o trailer não é "do assunto" de uma fala sobre a verificação em duas etapas). Cabe na
    distribuição e, se oficial, no teto de material oficial; o menos usado e o que divide mais palavras."""
    group = _group(state, scene)
    texts = [media_pool._scene_texts(state.doc, s) for s in group]
    said = media_pool.content_terms(*_plain_texts(texts[0])) - state.common
    rank = {t: k for k, t in enumerate(SUBJECT_TIERS)}
    room = _official_room(state, scene)
    best: tuple[Any, ...] | None = None
    chosen: PoolItem | None = None
    for index, item in enumerate(state.pool.items):
        if not _usable(item) or item.tier not in rank or (item.tier == "official" and not room):
            continue
        if not all(_subject_fits(state, item, t) for t in texts) or not _spread_ok(state, scene, item):
            continue
        shared = media_pool.content_terms(item.desc, *item.tags, *item.entities) & said
        key = (_uses(state, item), -len(shared), rank[item.tier], -item.score, index)
        if best is None or key < best:
            best, chosen = key, item
    return chosen


def _subject_fits(state: _State, item: PoolItem, texts: tuple[str, ...]) -> bool:
    """O item é do assunto destes textos: passa em `media_pool.fits_scene` e, nos níveis que sempre passam (próprio,
    oficial, foto livre), divide com a fala uma palavra de conteúdo que não é do assunto do vídeo inteiro."""
    if not media_pool.fits_scene(item, *texts, common=state.common):
        return False
    if item.tier in media_pool.KEYWORD_TIERS or media_pool.is_source_capture(item):
        return True
    said = media_pool.content_terms(*_plain_texts(texts)) - state.common
    return bool(media_pool.content_terms(item.desc, *item.tags, *item.entities) & said)


def _shows_text(item: PoolItem) -> bool:
    """O coletor anotou letras no quadro (`· texto "..."`: o aviso de classificação, um letreiro)."""
    return 'texto "' in (item.desc or "") or bool(item.rects)


def _theme_item(state: _State, scene: Scene, *, gap: bool = True) -> PoolItem | None:
    """Trecho oficial (ou próprio) do tema geral do vídeo: o vídeo antes da imagem, sem letras no quadro antes do com
    letras, o menos usado. Dentro do teto de material oficial, das aparições do item, do tempo seguido no mesmo vídeo
    de origem e no mesmo nível de material. Com `gap=False` (o cartão que não pode ficar), só a distância mínima entre
    as aparições do mesmo item cai: nenhum teto é furado."""
    room = _official_room(state, scene)
    best: tuple[Any, ...] | None = None
    chosen: PoolItem | None = None
    for index, item in enumerate(state.pool.items):
        if not _usable(item) or item.tier not in THEME_TIERS or media_pool.is_source_capture(item):
            continue
        if item.media not in ("clip", "image", "video") or (item.tier == "official" and not room):
            continue
        if not _spread_ok(state, scene, item, gap=gap) or not _tier_run_ok(state, scene, tiers.prov_of_item(item)):
            continue
        key = (item.media == "image", _shows_text(item), _uses(state, item), THEME_TIERS.index(item.tier),
               -item.score, index)
        if best is None or key < best:
            best, chosen = key, item
    return chosen


def _ai_fits(state: _State, scene: Scene) -> bool:
    """A cena pode virar imagem de IA sem passar do teto de IA (média do vídeo e trecho de `AI_WINDOW_S`). As cenas
    que reaproveitam a imagem dela passam a mostrar a IA também: contam inteiras."""
    policy = state.style.art.sources
    if state.total <= 0 or policy.ai_max_share <= 0:
        return False
    ids = {s.id for s in _group(state, scene)}
    spans = []
    ai = 0.0
    for s in state.order:
        weight = 1.0 if s.id in ids else tiers.ai_weight(s, state.doc)
        if weight > 0 and s.id in state.spans:
            a, b = state.spans[s.id]
            spans.append((a, b, weight))
            ai += (b - a) * weight
    if ai > policy.ai_max_share * state.total + 1e-6:
        return False
    share, _at = tiers.window_share(spans, state.total)
    return share <= tiers.ai_window_cap(policy.ai_max_share) + 1e-6


def is_scenario(doc: ProjectDoc, scene: Scene, prompt: str, said: str = "") -> bool:
    """`prompt` é um cenário de IA que pode ir para a cena: de `PROMPT_MIN_WORDS` a `PROMPT_MAX_WORDS` palavras, sem
    estilo, marca, interface nem o que a direção manda evitar (`storyboard_check`), e não é a própria fala (`said`;
    a reserva antiga punha "Então vamos ao golpe." no prompt: a imagem feita dela sai genérica, com cara de IA)."""
    text = (prompt or "").strip()
    count = len(text.split())
    if not PROMPT_MIN_WORDS <= count <= PROMPT_MAX_WORDS:
        return False
    avoid = list(doc.art.avoid) if doc.art else []
    if storyboard_check.find_terms(text, storyboard_check.STYLE_TERMS) or storyboard_check.find_terms(
            text, storyboard_check.BRAND_TERMS) or storyboard_check.find_terms(text, avoid):
        return False
    if storyboard_check.cliche_metaphor(text, said):
        return False  # mão de luva, chave na fechadura, hacker de capuz: enfeite de banco, não a cena da fala
    words = {w for w in media_pool.fold(text).split() if len(w) >= 3}
    spoken = {w for w in media_pool.fold(said).split() if len(w) >= 3}
    return bool(words) and len(words & spoken) < SCENARIO_OVERLAP * len(words)


def _said(state: _State, scene: Scene) -> str:
    from studio.core.project.ops import scene_text

    texts = [scene_text(state.doc, s) for s in _group(state, scene)]
    return " ".join([*texts, *scene.overlay_text.values()])


def _scenario_prompt(state: _State, scene: Scene) -> str | None:
    """O cenário de IA da cena: o prompt dela, quando é um cenário de verdade (`is_scenario`), ou o que
    `write_prompts` escreveu. None: sem cenário, a cena não vira IA aqui."""
    said = _said(state, scene)
    for prompt in (scene.visual.prompt, state.prompts.get(scene.id, "")):
        if prompt and is_scenario(state.doc, scene, prompt, said):
            return prompt.strip()
    return None


PROMPTS_SYSTEM = """Você escreve o prompt da imagem de IA de cenas de um vídeo do YouTube que ficaram sem material real.
Para cada cena, 1 a 3 frases em inglês com sujeito, ação, lugar, época, hora e luz, que mostram a IDEIA da fala (não
repita as palavras dela nem escreva texto na imagem). Varie o sujeito, a hora e a luz entre as cenas. NÃO escreva
estilo, técnica, paleta, "no text" nem "vertical": o app acrescenta. Nada de logotipo, personagem oficial, tipografia
de marca, interface de jogo, "screenshot", "gameplay", "leak" ou cena de trailer; nome de personagem de ficção, marca,
jogo, produto ou pessoa viva não entra: descreva o que se vê. Tela dentro da imagem (monitor, celular) sem letras
legíveis. Mostre a cena concreta da fala (quem faz o quê e onde: a pessoa trocando a senha no celular, a tela do banco
aberta, a fila da loja), nunca a metáfora-clichê de banco de imagem: mão de luva, chave na fechadura, cadeado, hacker de
capuz, lupa, correntes, código binário, mão segurando cabo, celular de tela vazia, caixa misteriosa. Respeite o cenário
(world) e o que evitar (avoid) da direção de arte."""

PROMPTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"prompts": {"type": "array", "items": {
        "type": "object", "properties": {"id": {"type": "string"}, "prompt": {"type": "string"}},
        "required": ["id", "prompt"]}}},
    "required": ["prompts"],
}


def write_prompts(doc: ProjectDoc, scenes: list[Scene], *, project_id: str | None = None,
                  job_id: str | None = None) -> dict[str, str]:
    """Pede ao LLM o cenário de IA (em inglês) das cenas que ficariam em cartão tipográfico e não têm um: uma
    chamada para todas. `settle` só usa o que passa em `is_scenario`."""
    from studio.core.project.ops import scene_text
    from studio.workers.pipeline.authoring import _llm

    if not scenes:
        return {}
    ordered = doc.ordered_scenes()
    texts = [scene_text(doc, s) for s in ordered]
    items = []
    for scene in scenes:
        at = next((k for k, s in enumerate(ordered) if s.id == scene.id), -1)
        items.append({"id": scene.id, "text": texts[at] if at >= 0 else "",
                      "context": media_pool.sentence_context(texts, at) if at >= 0 else ""})
    art = doc.art
    context = {"title": doc.title, "world": art.world if art else "", "avoid": list(art.avoid) if art else [],
               "scenes": items}
    lines = [f"Vídeo: {doc.title}", f"Cenário (world): {context['world'] or '-'}",
             f"Evitar (avoid): {', '.join(context['avoid']) or '-'}", "Cenas:"]
    lines += [f'- {i["id"]}: "{i["text"]}" (frase inteira: "{i["context"]}")' for i in items]
    result, _provider = _llm("scene_prompts", PROMPTS_SYSTEM, "\n".join(lines), PROMPTS_SCHEMA, context, project_id,
                             job_id)
    wanted = {s.id for s in scenes}
    out: dict[str, str] = {}
    for entry in (result.data or {}).get("prompts", []) or []:
        if isinstance(entry, dict) and entry.get("id") in wanted and isinstance(entry.get("prompt"), str):
            out[entry["id"]] = entry["prompt"].strip()
    return out


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


def _show_ai(state: _State, scene: Scene, prompt: str) -> None:
    language = next((s.visual.language for s in state.order if s.visual.language and s.id != scene.id
                     and tiers.prov_of_scene(s, state.doc) == "ai"), None) or scene.visual.language
    scene.visual = Visual(prompt=prompt, seed=scene.visual.seed, language=language, ai_reason=AI_REASON)
    _to_image(scene)


def _nearest_real(state: _State, scene: Scene) -> Scene | None:
    """A cena que empresta a imagem real ao cartão que não pode ficar sem nada: a que mostra material do mesmo assunto
    (o item do acervo dela é do assunto desta cena e de todas as que reaproveitam a imagem dela) ou do tema geral
    (próprio ou oficial, sem captura); nunca a foto de banco ou a captura de outro assunto (papel de parede). Só dentro
    dos tetos (`_placement_ok` sem a distância mínima, `_tier_run_ok`, teto oficial): a menos emprestada e a mais
    perto."""
    at = state.index(scene)
    group = _group(state, scene)
    ids = {s.id for s in group}
    texts = [media_pool._scene_texts(state.doc, s) for s in group]
    lent: dict[str, int] = {}
    for other in state.order:
        root = state.doc.reuse_root(other) if other.visual.reuse_of and other.visual.media is None else None
        if root is not None and root.id != other.id:
            lent[root.id] = lent.get(root.id, 0) + 1
    options = []
    for k, other in enumerate(state.order):
        media = other.visual.media
        if other.id in ids or media is None or media_pool.needs_attach(media, state.doc.id) or not (
                media.asset or media.video):
            continue
        prov = tiers.prov_of_media(media)
        if prov == "official" and not _official_room(state, scene):
            continue
        item = state.pool.item(media.pool_id) if media.pool_id else None
        subject = item is not None and all(_subject_fits(state, item, t) for t in texts)
        theme = prov in THEME_TIERS and media.tier != "capture" and media.kind != "capture"
        if not (subject or theme):
            continue
        if not _placement_ok(state, scene, tiers.media_tokens(media, state.look), _source_of_media(media), gap=False):
            continue
        if not _tier_run_ok(state, scene, prov):
            continue
        options.append((not subject, lent.get(other.id, 0), abs(k - at), k > at, k, other))
    return min(options, key=lambda o: o[:5])[5] if options else None


def _label(state: _State, scene: Scene) -> str:
    return media_pool._label(state.doc, scene)


def _decide(state: _State, scene: Scene, notes: list[str], *, fill_only: bool = False) -> bool:
    """A hierarquia numa cena sem material. Devolve True quando deixou mídia pendente de anexo. `fill_only`: só os
    degraus de material (assunto, tema, IA); sem nenhum, a cena fica como está para a volta normal."""
    problem = card_problem(state, scene)
    item = _subject_item(state, scene)
    if item is not None:
        _show_item(scene, item)
        notes.append(f"{_label(state, scene)}: em vez do cartão tipográfico, mostra material real do assunto "
                     f"({item.id}, {item.tier}).")
        return True
    if state.avatar and problem is None and not fill_only:
        state.kept.append(scene.id)
        return False
    theme = _theme_item(state, scene)
    if theme is not None:
        _show_item(scene, theme)
        notes.append(f"{_label(state, scene)}: em vez do cartão tipográfico, mostra material oficial do tema do vídeo "
                     f"({theme.id}).")
        return True
    prompt = _scenario_prompt(state, scene)
    if prompt is not None and _ai_fits(state, scene):
        _show_ai(state, scene, prompt)
        notes.append(f"{_label(state, scene)}: em vez do cartão tipográfico, ganha imagem de IA (dentro do teto de IA).")
        return False
    if fill_only:
        return False
    if problem is None:
        state.kept.append(scene.id)
        return False
    return _forced(state, scene, problem, notes)


def _forced(state: _State, scene: Scene, problem: str, notes: list[str]) -> bool:
    """O cartão que não pode ficar (`problem`) e não achou material novo: o tema geral voltando antes da distância
    mínima, ou o fundo da imagem real mais perto; sem nenhum dentro dos tetos, o cartão fica (fora das regras)."""
    theme = _theme_item(state, scene, gap=False)
    if theme is not None:
        again = ", mesmo repetido" if _uses(state, theme) else ""
        _show_item(scene, theme)
        notes.append(f"{_label(state, scene)}: o cartão tipográfico não cabia ali ({problem}); mostra material oficial "
                     f"do tema do vídeo ({theme.id}){again}.")
        return True
    if _near(state, scene, problem, notes, "o cartão tipográfico não cabia ali"):
        return False
    _keep_out_of_rules(state, scene, problem, notes)
    return False


def _near(state: _State, scene: Scene, problem: str, notes: list[str], lead: str) -> bool:
    near = _nearest_real(state, scene)
    if near is None:
        return False
    _reuse(scene, near)
    notes.append(f"{_label(state, scene)}: {lead} ({problem}); repete a imagem real da {_label(state, near)}.")
    return True


def _keep_out_of_rules(state: _State, scene: Scene, problem: str, notes: list[str]) -> None:
    state.kept.append(scene.id)
    ai = ("a IA passaria do teto de IA" if _scenario_prompt(state, scene) is not None
          else "não há cenário de IA para a cena")
    notes.append(f"{_label(state, scene)}: ficou cartão tipográfico ({problem}): o acervo não tem material real que "
                 f"caiba ali sem passar do teto de material oficial ou repetir demais, e {ai}. Mais material do "
                 "assunto no acervo resolve.")


def _revert(scene: Scene, saved: tuple[str, str | None, Visual]) -> None:
    scene.kind, scene.motion, scene.visual = saved[0], saved[1], saved[2].model_copy(deep=True)  # type: ignore[assignment]


def _settled_ok(state: _State, scene: Scene) -> bool:
    """A cena que o `settle` mandou anexar ficou bem depois do anexo: mostra mídia própria, ou a reaproveitada da
    vizinha (o anexo que falhou repete a mídia real perto) dentro dos tetos."""
    if scene.visual.media is not None:
        return True
    media = state.doc.shown_media(scene)
    if media is None:
        return False
    return _placement_ok(state, scene, tiers.media_tokens(media, state.look), _source_of_media(media), gap=False) and \
        _tier_run_ok(state, scene, tiers.prov_of_media(media))


def settle(doc: ProjectDoc, style: StyleSpec | None, pool: Pool, *, avatar: bool | None = None,
           attach: Callable[[ProjectDoc], list[str]] | None = None,
           prompts: Callable[[ProjectDoc, list[Scene]], dict[str, str]] | None = None) -> list[str]:
    """Resolve, em `doc` (no lugar), as cenas que ficariam em cartão tipográfico (ver o módulo). `avatar`: o canal
    tem avatar no vídeo (None: lê do projeto). `attach`: anexa a mídia pendente (padrão: `media_pool.attach_chosen`
    com o acervo). `prompts`: escreve o cenário de IA das cenas sem um (`write_prompts`; None: a cena sem cenário
    não vira IA). Devolve os avisos."""
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
    if prompts is not None and style.art.sources.ai_max_share > 0:
        state.refresh()
        missing = [s for s in state.order if not s.locked and tiers.is_typographic(s, doc)
                   and _scenario_prompt(state, s) is None]
        if missing:
            try:
                state.prompts = prompts(doc, missing)
            except Exception:  # noqa: BLE001 - sem cenário escrito, a cena sem um só não vira IA
                log.warning("os cenários de IA dos cartões tipográficos falharam", exc_info=True)
    # O cartão antes do `settle`, para a cena que mandou anexar: se o anexo falhar e a reserva dele não couber,
    # a 2ª volta decide de novo a partir dele.
    saved: dict[str, tuple[str, str | None, Visual]] = {}
    for round_ in range(2):
        state.refresh()
        state.kept = []
        if round_ == 1:
            # Os cartões que a 1ª volta deixou contam como estão; só a cena que mandou anexar decide de novo.
            state.kept = [s.id for s in state.order if s.id not in saved and tiers.is_typographic(s, doc)]
        # O fim do vídeo primeiro: a tela final nunca fica sem material porque o meio gastou o teto.
        ordered = sorted(state.order, key=lambda s: not _end_zone(state, s))
        if round_ == 0:
            # 1º o material (assunto, tema, IA) do cartão que de saída não pode ficar (fim do vídeo, longo, pedaço de
            # frase, colado em outro): a folga dos tetos vai para quem mais precisa. Depois, na ordem, a hierarquia
            # inteira em todos (o cartão que cabe nas regras fica com o que sobrar).
            urgent = [s for s in ordered if not s.locked and tiers.is_typographic(s, doc)
                      and card_problem(state, s) is not None]
            for fill_only, scenes in ((True, urgent), (False, ordered)):
                for scene in scenes:
                    if scene.locked or not tiers.is_typographic(scene, doc):
                        continue
                    state.refresh()
                    before = (scene.kind, scene.motion, scene.visual.model_copy(deep=True))
                    if _decide(state, scene, notes, fill_only=fill_only):
                        saved[scene.id] = before
            if not saved:
                break
            try:
                notes += attach(doc)
            except Exception:  # noqa: BLE001 - sem anexo, a 2ª volta usa só o que já está no projeto
                log.warning("o anexo do material dos cartões tipográficos falhou", exc_info=True)
            continue
        for scene in ordered:
            if scene.locked or scene.id not in saved:
                continue
            state.refresh()
            if not tiers.is_typographic(scene, doc) and not _settled_ok(state, scene):
                # O material não anexou e a reserva do anexo (a mídia vizinha) passa dos tetos: volta a ser cartão.
                _revert(scene, saved[scene.id])
                state.refresh()
            if not tiers.is_typographic(scene, doc):
                continue
            # Depois do anexo: o que não baixou (ou caiu de novo em cartão) só tem o que já está no projeto.
            problem = card_problem(state, scene)
            if problem is None:
                state.kept.append(scene.id)
            elif not _near(state, scene, problem, notes, "o material não anexou e o cartão tipográfico não cabia ali"):
                _keep_out_of_rules(state, scene, problem, notes)
    return notes


def _has_avatar(doc: ProjectDoc, style: StyleSpec) -> bool:
    try:
        from studio.core.project.presenter import project_presenter

        presenter = project_presenter(doc, style)
    except Exception:  # noqa: BLE001 - sem banco ou sem canal: sem avatar
        return False
    return presenter is not None and bool(presenter.poses)
