"""Avatar do apresentador no vídeo: o pedido ao storyboard e a distribuição determinística das poses pelas cenas.

`assign` roda depois da narração (precisa do tempo das palavras) e também pelo botão "Redistribuir". Ordem das regras
(as do `plantao-vice/playbook.md`, seção "Avatar nos vídeos longos"):
  1. o manual vence (`source="manual"`, inclusive o "sem avatar" da cena);
  2. nunca em cena séria (vítima, menor de idade, morte, crise, racismo, maus-tratos);
  3. nunca nos tipos de `hide_on_kinds` (documento, citação, gráfico, mapa, notícia) nem na 1ª cena;
  4. uma aparição a cada 60 a 90 s (`video.every_s`): a sugestão perto demais da anterior sai, o buraco longo ganha
     uma aparição pela fala da cena;
  5. nunca em cenas adjacentes (`video.min_gap_scenes`, no mínimo 2 de distância);
  6. nunca a mesma pose duas vezes seguidas;
  7. pose inexistente vira a mais próxima pela emoção e depois a `default_pose`;
  8. fixas: abertura `oculos`, CTA (e pedido final) `feliz`.
Os avisos saem no formato do `storyboard_check` (`Issue` com AVISO/CORRIGIDO).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from studio.core.editing.serious import (  # noqa: F401 - os termos continuam importáveis daqui
    SERIOUS_NOT,
    SERIOUS_PREFIXES,
    SERIOUS_TERMS,
    serious_term,
)
from studio.core.project.document import Presenter, ProjectDoc, Scene, ScenePresenter
from studio.core.project.presenter import fold, pose_exists, resolve_pose, trigger_index, word_key
from studio.workers.pipeline.storyboard_check import AVISO, CORRIGIDO, Issue

OPENING_POSE = "oculos"
CTA_POSE = "feliz"
EXPLAIN_POSES = {"apontando"}
OPENING_WINDOW_S = 30.0
# Rodízio das poses no preenchimento sem pista na fala (das mais neutras às mais fortes).
ROTATION = ("apontando", "pensativo", "surpreso", "rindo", "lupa", "preocupado", "celular", "comemorando", "base")

# Cena séria: o avatar nunca entra. Os termos moram em `core.editing.serious` (a mesma regra do editor-planejador).
CTA_TERMS = ("inscrev", "se inscreve", "deixa o like", "deixe o like", "deixa seu like", "sininho", "ativa o sino",
             "manda pra um amigo", "mil inscritos")
# Pista da fala → pose (pela emoção do playbook). A pose que o canal não tem cai na mais próxima (`resolve_pose`).
CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dinheiro", ("preco", "precos", "custo", "custa", "dolar", "dolares", "reais", "vendeu", "vendas", "dinheiro",
                  "caro", "bilhao", "bilhoes", "milhoes")),
    ("segredo", ("rumor", "rumores", "boato", "boatos", "vazament", "vazou", "segredo", "spoiler")),
    ("lupa", ("detalhe", "detalhes", "escondid", "repara", "reparou", "pista", "pistas", "investig")),
    ("surpreso", ("chocante", "inacredit", "ninguem esperava", "surpresa", "surpreend", "impression")),
    ("mao-na-testa", ("erro", "erros", "bug", "bugs", "falha", "falhou", "vergonha")),
    ("bravo", ("atraso", "atrasou", "adiad", "adiament", "abuso", "absurdo")),
    ("comemorando", ("lancamento", "chegou", "finalmente", "confirmad", "oficial")),
    ("controle", ("gameplay", "jogar", "jogabilidade", "missao", "missoes", "controle")),
    ("celular", ("noticia", "noticias", "post", "postou", "anuncio", "anunciou", "tweet")),
    ("suando", ("espaco", "gigabytes", "nervos", "tenso")),
    ("preocupado", ("risco", "cuidado", "perigo", "perigoso")),
    ("rindo", ("piada", "engracad", "ridicul", "meme")),
    ("pensativo", ("sera", "pergunta", "duvida", "talvez")),
)


class WordLike(Protocol):
    text: str
    start: float
    end: float


@dataclass
class _Slot:
    index: int
    scene: Scene
    start: float
    end: float
    text: str
    words: list[str]
    blocked: str | None
    cta: bool


def prompt_lines(presenter: Presenter | None) -> list[str]:
    """Bloco do pedido do storyboard com as poses do avatar (vazio sem avatar ligado)."""
    if presenter is None or not presenter.enabled or not presenter.poses:
        return []
    lo, hi = presenter.video.every_s
    lines = ["", f"Avatar do apresentador (opcional): só onde houver uma reação clara na fala, no máximo uma a cada "
                 f"{lo} a {hi} s e nunca em cenas seguidas, preencha presenter_hint {{pose, at, mode}} com o id de "
                 "uma das poses abaixo. at = a palavra exata da narração que dispara a reação; mode = reacao (1,5 a "
                 "2,5 s) ou explica (fica a cena, para mostrar a prova). Nunca em cena séria (vítima, menor, morte, "
                 "crise), nem em documento, citação, gráfico, mapa ou notícia, nem na primeira cena. Sem balão de "
                 "fala nem texto."]
    for pose in presenter.poses:
        meaning = ", ".join([*pose.emotions, *pose.aliases]) or "neutro"
        lines.append(f"- {pose.id}: {meaning}")
    return lines


def ordered_scenes(doc: ProjectDoc) -> list[Scene]:
    return [scene for seg in doc.segments for scene in doc.scenes_of(seg.id)]


def _has(text: str, terms: Iterable[str]) -> str | None:
    """O primeiro termo (sem acento, começo de palavra) que aparece no texto."""
    folded = " " + " ".join(fold(text).replace("-", " ").split()) + " "
    for term in terms:
        t = " ".join(fold(term).replace("-", " ").split())
        if re.search(r"(?<![a-z0-9])" + re.escape(t), folded):
            return term
    return None


_serious = serious_term
"""O termo de cena séria que aparece no texto (`core.editing.serious.serious_term`)."""


def _cue(presenter: Presenter, slot: _Slot) -> tuple[str, str | None]:
    """Pose pela pista da fala (e a palavra que dispara); sem pista, ("", None)."""
    for pose_name, terms in CUES:
        single = [word_key(t) for t in terms if " " not in t]
        for word in slot.words:
            key = word_key(word)
            if any(key == t or (len(t) >= 4 and key.startswith(t)) for t in single):
                return resolve_pose(presenter, pose_name).id, word  # type: ignore[union-attr]
        if _has(slot.text, [t for t in terms if " " in t]):
            return resolve_pose(presenter, pose_name).id, None  # type: ignore[union-attr]
    return "", None


def estimate_words(doc: ProjectDoc, lang: str | None = None, wpm: int = 150, gap_s: float = 0.4) -> dict[str, list[Any]]:
    """Tempos aproximados das palavras por cena, sem narração (pela velocidade da fala): para redistribuir antes
    da narração existir. Os mesmos cortes de cena da timeline (`scene_word_ranges`)."""
    from studio.core.text import words as split_words
    from studio.core.timeline import scene_word_ranges
    from studio.providers.base import Word

    lang = lang or doc.primary_language
    step = 60.0 / max(60, wpm)
    t = 0.0
    out: dict[str, list[Any]] = {}
    for seg in doc.segments:
        tokens = split_words(seg.text_for(lang, doc.primary_language))
        seg_words = [Word(tok, t + i * step, t + (i + 1) * step) for i, tok in enumerate(tokens)]
        scenes = doc.scenes_of(seg.id)
        if scenes:
            ranges = scene_word_ranges(scenes, seg_words)
            for k, scene in enumerate(scenes):
                a, b = ranges[k] if k < len(ranges) else (len(seg_words), len(seg_words))
                out[scene.id] = seg_words[a:b]
        t += len(tokens) * step + gap_s
    return out


def _slots(doc: ProjectDoc, presenter: Presenter, words_by_scene: dict[str, Sequence[WordLike]],
           scene_times: dict[str, tuple[float, float]] | None, serious: set[str]) -> list[_Slot]:
    roles = {seg.id: seg.role for seg in doc.segments}
    hide = set(presenter.video.hide_on_kinds)
    slots: list[_Slot] = []
    clock = 0.0
    for i, scene in enumerate(ordered_scenes(doc)):
        words = list(words_by_scene.get(scene.id) or [])
        if scene_times and scene.id in scene_times:
            start, end = scene_times[scene.id]
        elif words:
            start, end = words[0].start, words[-1].end
        else:
            start = end = clock
        clock = max(clock, end)
        texts = [w.text for w in words]
        overlay = scene.overlay_text.get(doc.primary_language) or ""
        text = " ".join(texts) + " " + overlay
        blocked: str | None = None
        media = doc.shown_media(scene)
        if scene.id in serious or (scene.mark is not None and scene.mark.serious):
            blocked = "cena séria"
        elif term := _serious(text):
            blocked = f"cena séria ({term})"
        elif scene.kind in hide or (media is not None and media.kind in hide):
            blocked = f"cena de {media.kind if media is not None and media.kind in hide else scene.kind}"
        elif i == 0 and presenter.video.skip_first_scene:
            blocked = "primeira cena"
        cta = roles.get(scene.segment_id) == "cta" or _has(text, CTA_TERMS) is not None
        slots.append(_Slot(i, scene, start, end, text, texts, blocked, cta))
    return slots


def assign(
    doc: ProjectDoc,
    presenter: Presenter | None,
    words_by_scene: dict[str, Sequence[WordLike]],
    *,
    scene_times: dict[str, tuple[float, float]] | None = None,
    vertical: bool = False,
    serious: Iterable[str] = (),
) -> list[Issue]:
    """Distribui o avatar pelas cenas (muda `Scene.presenter` em `doc`) e devolve os avisos.

    `presenter`: o efetivo (`core.project.presenter.effective`); None tira todo avatar automático (o manual fica).
    `words_by_scene`: palavras de cada cena com tempo em segundos, contínuo no vídeo (as da timeline, ou
    `estimate_words`). `scene_times`: início/fim de cada cena, quando já se sabe (senão vem das palavras).
    `serious`: ids de cena marcados como sensíveis pelo fact-check."""
    issues: list[Issue] = []
    scenes = ordered_scenes(doc)
    if presenter is None or not presenter.poses or (vertical and presenter.video.hide_on_vertical):
        for scene in scenes:
            if scene.presenter is not None and scene.presenter.source == "auto":
                scene.presenter = None
        return issues

    slots = _slots(doc, presenter, words_by_scene, scene_times, set(serious))
    lo, hi = presenter.video.every_s
    gap = max(2, presenter.video.min_gap_scenes)
    chosen: dict[int, tuple[ScenePresenter, str]] = {}  # índice → (avatar, origem: manual|fixa|sugestao|auto)

    def fits(slot: _Slot, *, timed: bool) -> bool:
        for j in chosen:
            if abs(j - slot.index) < gap:
                return False
            if timed and abs(slots[j].start - slot.start) < lo:
                return False
        return True

    # 1. manual (inclusive "sem avatar") vence; as sugestões automáticas são guardadas e limpas.
    hints: dict[int, ScenePresenter] = {}
    for slot in slots:
        current = slot.scene.presenter
        if current is None:
            continue
        if current.source == "manual":
            if not current.off:
                if not pose_exists(presenter, current.pose) and current.pose:
                    resolved = resolve_pose(presenter, current.pose)
                    issues.append(Issue("avatar_pose", AVISO, f'A pose "{current.pose}" não existe no canal: o vídeo '
                                        f'usa "{resolved.id if resolved else "-"}".', scene_id=slot.scene.id))
                chosen[slot.index] = (current, "manual")
            continue
        if not current.off:
            hints[slot.index] = current
        slot.scene.presenter = None
    manual_off = {s.index for s in slots if s.scene.presenter is not None and s.scene.presenter.off}

    def free(slot: _Slot) -> bool:
        return slot.blocked is None and slot.index not in manual_off and slot.index not in chosen

    # 8. fixas: CTA (feliz), sem a regra do tempo, mas nunca adjacente a outra aparição.
    run_done = False  # uma aparição por trecho de CTA (cenas seguidas de CTA)
    for slot in slots:
        if not slot.cta:
            run_done = False
            continue
        if not run_done and free(slot) and fits(slot, timed=False):
            pose = resolve_pose(presenter, CTA_POSE)
            chosen[slot.index] = (ScenePresenter(pose=pose.id if pose else None, mode="reacao"), "fixa")
            run_done = True
    # 2-5. sugestões do storyboard, na ordem.
    for index, hint in sorted(hints.items()):
        slot = slots[index]
        why = slot.blocked or ("sem avatar nesta cena" if index in manual_off else None)
        if why is None and index in chosen:
            continue  # a fixa da mesma cena vale
        if why is None and not fits(slot, timed=False):
            why = "cena vizinha de outra com avatar"
        if why is None and not fits(slot, timed=True):
            why = f"menos de {lo} s depois da aparição anterior"
        if why is not None:
            issues.append(Issue("avatar_tirado", CORRIGIDO, f"Avatar tirado da cena: {why}.", scene_id=slot.scene.id))
            continue
        fixed = hint.model_copy()
        if not pose_exists(presenter, hint.pose):
            pose = resolve_pose(presenter, hint.pose)
            if hint.pose:
                issues.append(Issue("avatar_pose", CORRIGIDO, f'A pose "{hint.pose}" não existe: virou '
                                    f'"{pose.id if pose else "-"}".', scene_id=slot.scene.id))
            fixed.pose = pose.id if pose else None
        else:
            fixed.pose = resolve_pose(presenter, hint.pose).id  # type: ignore[union-attr]
        if fixed.at and not fixed.at.startswith("@") and trigger_index(slot.words, fixed.at) is None:
            issues.append(Issue("avatar_gatilho", CORRIGIDO, f'A palavra "{fixed.at}" não está na fala da cena: o '
                                "avatar entra no começo.", scene_id=slot.scene.id))
            fixed.at = None
        chosen[index] = (fixed, "sugestao")

    # 8. abertura (oculos) nos primeiros segundos, quando sobra espaço depois das sugestões.
    opening = next((s for s in slots if s.start <= OPENING_WINDOW_S and free(s) and fits(s, timed=True)), None)
    if opening is not None and pose_exists(presenter, OPENING_POSE):
        chosen[opening.index] = (ScenePresenter(pose=OPENING_POSE, mode="reacao"), "fixa")

    # 4. buracos maiores que o máximo ganham uma aparição pela fala (alvo: o meio do intervalo).
    if slots:
        end_time = max(s.end for s in slots)
        rotation = 0
        guard = 0
        while guard < len(slots):
            guard += 1
            times = sorted(slots[j].start for j in chosen)
            anchors = [0.0, *times]
            added = False
            for k, a in enumerate(anchors):
                b = anchors[k + 1] if k + 1 < len(anchors) else end_time
                if b - a <= hi:
                    continue
                target = a + (lo + hi) / 2
                limit = b - lo if k + 1 < len(anchors) else end_time
                options = [s for s in slots if free(s) and a + (lo if k else 0) <= s.start <= limit
                           and fits(s, timed=True)]
                if not options:
                    continue
                # Dentro da janela de 60 a 90 s, a cena com pista na fala ganha; senão, a mais perto do alvo.
                window = [s for s in options if s.start <= a + hi] or options
                cued = [s for s in window if _cue(presenter, s)[0]]
                slot = min(cued or window, key=lambda s: (abs(s.start - target), s.index))
                pose_id, word = _cue(presenter, slot)
                if not pose_id:
                    names = [n for n in ROTATION if pose_exists(presenter, n)] or [p.id for p in presenter.poses]
                    pose_id = names[rotation % len(names)]
                    rotation += 1
                mode = "explica" if pose_id in EXPLAIN_POSES else "reacao"
                chosen[slot.index] = (ScenePresenter(pose=pose_id, at=_at_of(slot.words, word), mode=mode), "auto")
                added = True
                break
            if not added:
                break

    # 6. a mesma pose duas vezes seguidas: troca a de menor prioridade (manual > fixa > sugestão > automática;
    # empate: a de depois). Duas manuais iguais só geram aviso.
    rank = {"manual": 3, "fixa": 2, "sugestao": 1, "auto": 0}
    order = sorted(chosen)
    for pos in range(1, len(order)):
        prev, cur = order[pos - 1], order[pos]
        p_av, p_src = chosen[prev]
        c_av, c_src = chosen[cur]
        if not p_av.pose or fold(p_av.pose) != fold(c_av.pose):
            continue
        if p_src == c_src == "manual":
            issues.append(Issue("avatar_repetido", AVISO, f'A pose "{c_av.pose}" aparece duas vezes seguidas (as '
                                "duas escolhidas à mão).", scene_id=slots[cur].scene.id))
            continue
        target_pos = pos - 1 if rank[p_src] < rank[c_src] else pos
        target_index = order[target_pos]
        target = chosen[target_index][0]
        neighbours = {fold(chosen[order[q]][0].pose) for q in (target_pos - 1, target_pos + 1) if 0 <= q < len(order)}
        options = [n for n in (*ROTATION, *(p.id for p in presenter.poses))
                   if pose_exists(presenter, n) and fold(n) not in neighbours]
        if not options:
            continue
        old = target.pose
        target.pose = options[0]
        target.mode = "explica" if options[0] in EXPLAIN_POSES else "reacao"
        issues.append(Issue("avatar_repetido", CORRIGIDO, f'Pose "{old}" repetida em seguida: virou "{options[0]}".',
                            scene_id=slots[target_index].scene.id))

    for index, (avatar, src) in chosen.items():
        if src != "manual":
            avatar.source = "auto"
            avatar.off = False
            slots[index].scene.presenter = avatar
    return issues


def _at_of(words: list[str], word: str | None) -> str | None:
    """O gatilho da palavra ("palavra#2" quando ela já apareceu antes na cena)."""
    if not word:
        return None
    key = word_key(word)
    if not key or not re.match(r"^[^\s#@][^\s#]{0,59}$", key):
        return None
    first = next((i for i, w in enumerate(words) if w == word), None)
    if first is None:
        return None
    nth = sum(1 for w in words[: first + 1] if word_key(w) == key)
    return key if nth == 1 else f"{key}#{nth}"


def summary(doc: ProjectDoc) -> dict[str, Any]:
    """Resumo do avatar no projeto: cenas com avatar (pose, origem) e quantas."""
    rows = [{"scene_id": s.id, "pose": s.presenter.pose, "at": s.presenter.at, "mode": s.presenter.mode,
             "source": s.presenter.source}
            for s in ordered_scenes(doc) if s.presenter is not None and not s.presenter.off]
    return {"count": len(rows), "scenes": rows}
