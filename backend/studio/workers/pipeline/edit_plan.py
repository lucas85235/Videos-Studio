"""Planejador de edição determinístico (ADR 0004 §3.6): subplanos e eventos presos a palavras.

Roda depois do storyboard (e do anexo do acervo) e pelo botão "Reeditar" (`POST /projects/{id}/reedit`), sem LLM e
sem gastar cota: a mesma semente (`doc.seed` + id da cena) dá a mesma edição. Escreve só `Scene.shots`, `Scene.fx` e
`Scene.mark` (este só quando falta). O que está `locked` ou é do usuário (`origin="user"`) fica; cena travada
(`Scene.locked`) não muda nada; `grammar.editor == "off"` não faz nada.

Passos:
1. **Tempo das palavras** estimado pelo texto (palavra longa demora mais, vírgula e ponto pausam) e pela taxa da voz
   do estilo (`words_per_minute` × `voice.speed`), com os mesmos cortes de cena da timeline (`scene_word_ranges`).
2. **Marca da batida** (`BeatMark`) completada quando falta: papel pelo segmento e pelo texto, ênfase (número e palavra
   forte), cena séria pelos termos do avatar.
3. **Ritmo**: durações de uma lognormal (mediana `shot_median_s`, σ = √ln(1+CV²)); o corte cai na palavra mais perto do
   alvo, preferindo ênfase > número > depois de vírgula > substantivo. Rajada de 2 a 4 planos curtos no gancho, na piada
   e na lista (`burst_prob`); respiro longo e sem câmera no clipe de batida `breath`; corte seco na ênfase do `reveal`.
4. **Fonte do subplano**: próximo trecho do mesmo clipe > outro item do acervo com a mesma etiqueta (cota de `switch`)
   > o mesmo asset reenquadrado (jump cut 1,0 → 1,25 em outro ponto, no máximo 2 por asset, contando o `zoom_to` das
   cenas que reaproveitam a imagem). Sem fonte, o plano não é cortado.
5. **Câmera** por plano (static 35%, push 25%, drift 15%, punch seco 15%, pan 10% só em plano aberto), força ±40% e
   curva por semente; clipe sem câmera, captura parada; `slow_zoom_share` e `static_share_min` cumpridos no fim.
6. **Eventos** com cota por minuto (janela própria no gancho), escalada por `intensity` (0→0,5×, 1→0,75×, 2→1×,
   3→1,4×): primeiro os estruturais (revelação, piada, contador da lista, marca-texto da captura, plano parado demais),
   depois o guloso por pontuação (ênfase > número > início de capítulo > piada > maior lacuna) com 1,2 s entre eventos
   do mesmo tipo.

Parâmetros que o planejador escreve em `FxEvent.params` (lidos por `timeline-fx` e pelo renderer):
- `punch`: `scale`; `shake`: `strength`; `freeze`: nada (a duração em `dur_ms`);
- `text_pop`: `text` (1 a 3 palavras); `counter`: `text` ("2/5");
- `sfx`: `kind` (tipo procedural, sempre presente) e `lib_id` (som da biblioteca, quando escolhido pela paleta);
  `purpose` (cut, punch, text_pop, reveal, gag, accent) só informativo;
- `meme`: `lib_id`, `tag` e `variant` ("corner"); `highlight` e `zoom_rect`: `rect` ([x, y, w, h] de 0 a 1);
- `music_drop`: nada (a duração em `dur_ms`; cai na palavra ANTES da revelação).
"""

from __future__ import annotations

import logging
import math
import random
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from studio.core.hashing import seed_from
from studio.core.numbers import notable_number_runs
from studio.core.project.document import (
    CARD_KINDS,
    PRESENTER_AT_RE,
    BeatMark,
    FxEvent,
    MediaRef,
    ProjectDoc,
    Scene,
    ShotFraming,
    SubShot,
)
from studio.core.project.pool import Pool, PoolItem
from studio.core.project.presenter import fold, trigger_index, word_key
from studio.core.sources import prov_of_scene
from studio.core.styles.schema import EditGrammar, StyleSpec, grammar_for
from studio.core.text import _GOOD_CUT_BEFORE, _NO_CUT_AFTER
from studio.core.text import words as split_words
from studio.core.timeline import scene_word_ranges
from studio.providers.base import Word

log = logging.getLogger(__name__)

INTENSITY_SCALE: dict[int, float] = {0: 0.5, 1: 0.75, 2: 1.0, 3: 1.4}
MIN_SHOT_S = 0.35
"""Plano mínimo (meme e flash são eventos, não planos: ficam de fora)."""
TAIL_S = 0.5
"""Nada nos 0,5 s finais da cena (nem corte nem evento; a queda da trilha é a exceção)."""
SPACING_S = 1.2
"""Espaçamento mínimo entre eventos do mesmo tipo."""
MEME_GAP_S = {"long": 20.0, "short": 8.0}
HISTORY_VIDEOS = 5
MAX_REFRAMES = 2
SIMILAR = 0.15
"""Dois planos com duração a ±15% um do outro são "iguais": no máximo 2 seguidos."""
MAX_SAME_PROV = 3
HOOK_BOOST = 2.0
"""Sem `grammar.hook`, a janela do gancho tem o dobro da densidade de punch, texto e SFX."""
BREATH_S = (5.0, 9.0)
BURST_S = (0.5, 1.5)
REFRAME_SCALE = 1.25
PUNCH_CAMERA = (1.15, 1.30)

CAMERA_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("static", 0.35), ("push", 0.25), ("drift", 0.15), ("punch", 0.15), ("pan", 0.10),
)
SLOW_MOTIONS = frozenset({"push", "drift", "ken_burns_in", "ken_burns_out"})
STILL_MOTIONS = frozenset({"static", "none"})
OPEN_SHOTS = frozenset({"wide", "aerial", "top_down"})
EASINGS: tuple[tuple[str, float], ...] = (("inOutQuad", 0.4), ("outCubic", 0.4), ("linear", 0.2))

BUDGETED = ("punch", "shake", "text_pop", "sfx", "meme", "annot")
ANNOT_TYPES = frozenset({"highlight", "arrow", "circle"})
COMIC_FX = frozenset({"meme", "freeze", "shake"})
COMIC_SFX = frozenset({"boom", "scratch"})
TONAL_SFX = frozenset({"riser", "riser_short", "bass_drop"})
# Tipos de SFX do planejador. Os que o `media.audio` ainda não gera caem no parecido (`_available_kind`).
SFX_FALLBACK: dict[str, str] = {
    "pop": "impact", "tick": "impact", "click": "impact", "thump": "impact", "boom": "impact", "scratch": "glitch",
    "shutter": "impact", "swish": "whoosh", "bass_drop": "impact", "riser_short": "riser",
}
PROCEDURAL_SFX = frozenset({"whoosh", "impact", "glitch", "static", "riser", *SFX_FALLBACK})
"""Tipos procedurais; chave da paleta fora daqui é id da biblioteca de SFX do usuário (`lib_id`)."""
DEFAULT_SFX: dict[str, dict[str, float]] = {
    "cut": {"swish": 0.5, "whoosh": 0.3, "click": 0.2},
    "punch": {"thump": 0.6, "impact": 0.4},
    "text_pop": {"pop": 0.7, "click": 0.3},
    "highlight": {"tick": 0.6, "shutter": 0.4},
    "accent": {"pop": 0.4, "tick": 0.3, "click": 0.3},
    "reveal": {"impact": 1.0},
    "gag": {"boom": 0.5, "scratch": 0.5},
}
_SENTENCE_END = (".", "!", "?", "…")
_CLAUSE_END = (",", ";", ":", "—", "–")
_CLOSERS = "\"'”’»)]}"
_FUNCTION_WORDS = frozenset(fold(w) for group in _NO_CUT_AFTER.values() for w in group)
_GOOD_BEFORE = frozenset(fold(w) for group in _GOOD_CUT_BEFORE.values() for w in group)
_AT_CLEAN = re.compile(r"[^\w\-'’]", re.UNICODE)


# --- estruturas internas --------------------------------------------------------------------------------------------


@dataclass
class _W:
    """Palavra falada da batida, com tempo estimado (s, contínuo no vídeo) e o gatilho (`at`) que a acha."""

    text: str
    key: str
    start: float
    end: float
    index: int
    at: str = ""
    comma_before: bool = False
    number: bool = False
    emph: bool = False
    content: bool = False
    chapter: bool = False
    gag: bool = False

    @property
    def dur(self) -> float:
        return self.end - self.start


@dataclass
class _Shot:
    t: float
    word: _W | None
    prov: str
    source: str = "self"
    pool_id: str | None = None
    framing: ShotFraming | None = None
    motion: str | None = None
    easing: str | None = None
    strength: float | None = None
    clip_in: float | None = None
    hold: bool = False
    end: float = 0.0

    @property
    def dur(self) -> float:
        return self.end - self.t


@dataclass
class _Beat:
    scene: Scene
    order: int
    words: list[_W]
    start: float
    end: float
    prov: str
    mark: BeatMark
    editable: bool
    seg_role: str
    media: MediaRef | None
    item: PoolItem | None
    asset: str
    tags: set[str]
    keep_shots: bool
    shots: list[_Shot] = field(default_factory=list)
    pos: int = 0
    """Posição entre as batidas com fala (`_Planner.beats`)."""

    @property
    def dur(self) -> float:
        return self.end - self.start

    @property
    def card(self) -> bool:
        return self.scene.kind in CARD_KINDS

    @property
    def framed(self) -> bool:
        """Cena de imagem (IA, banco, mídia real): pode ganhar subplanos e câmera."""
        return self.scene.kind == "image" and (self.scene.visual.type != "none" or self.media is not None)

    @property
    def clip(self) -> bool:
        return self.media is not None and bool(self.media.video)

    @property
    def capture(self) -> bool:
        return self.media is not None and bool(self.media.rects)

    def word_at(self, at: str | None) -> _W | None:
        index = trigger_index([w.text for w in self.words], at) if at else None
        return self.words[index] if index is not None else None

    def latest_start(self) -> float:
        """O último instante em que um evento ou corte pode começar (fora dos 0,5 s finais)."""
        return self.end - TAIL_S


@dataclass
class _Event:
    type: str
    t: float
    beat: _Beat
    fx: FxEvent
    kept: bool = False


class _Budget:
    """Cotas por tipo: uma para a janela do gancho e outra para o resto do vídeo, sem empréstimo entre elas."""

    def __init__(self, grammar: EditGrammar, scale: float, total_s: float) -> None:
        hook = grammar.hook
        self.window = min(total_s, hook.window_s if hook is not None else 5.0)
        body_min = max(0.0, total_s - self.window) / 60.0
        hook_min = self.window / 60.0
        rates = {"punch": grammar.punch_per_min, "shake": grammar.shake_per_min, "text_pop": grammar.text_pop_per_min,
                 "sfx": grammar.sfx_per_min, "meme": grammar.meme_per_min, "annot": grammar.annot_per_min}
        if grammar.humor == "none":
            rates["meme"] = 0.0
        hook_rates = dict(rates)
        for name in ("punch", "text_pop", "sfx"):
            override = getattr(hook, f"{name}_per_min") if hook is not None else None
            hook_rates[name] = override if override is not None else rates[name] * HOOK_BOOST
        # O corpo arredonda para baixo (a escala da intensidade fica proporcional); o gancho, para o mais perto.
        self.left: dict[str, list[int]] = {
            name: [int(math.floor(hook_rates[name] * scale * hook_min + 0.5)),
                   int(math.floor(rates[name] * scale * body_min + 1e-9))]
            for name in BUDGETED
        }
        # A piada marcada no storyboard é intenção do roteiro: o vídeo tem lugar para pelo menos uma (fora do calmo).
        if rates["meme"] > 0 and scale >= 0.75 and body_min > 0:
            self.left["meme"][1] = max(1, self.left["meme"][1])

    def slot(self, t: float) -> int:
        return 0 if t < self.window else 1

    def has(self, kind: str, t: float) -> bool:
        return self.left[kind][self.slot(t)] > 0

    def take(self, kind: str, t: float) -> bool:
        slot = self.slot(t)
        if self.left[kind][slot] <= 0:
            return False
        self.left[kind][slot] -= 1
        return True

    def spend(self, kind: str, t: float) -> None:
        """Gasta sem checar (evento preservado do usuário: conta na cota, nunca sai)."""
        slot = self.slot(t)
        self.left[kind][slot] = max(0, self.left[kind][slot] - 1)

    def total(self, kind: str) -> int:
        return sum(self.left[kind])


def _budget_kind(fx_type: str) -> str | None:
    if fx_type in ANNOT_TYPES:
        return "annot"
    return fx_type if fx_type in BUDGETED else None


# --- entrada ----------------------------------------------------------------------------------------------------------


def plan(doc: ProjectDoc, style: StyleSpec, pool: Pool, *, intensity: int | None = None,
         recent: set[str] | None = None) -> None:
    """Planeja (em `doc`, no lugar) os subplanos e eventos das cenas pela gramática do estilo (`grammar_for`).

    `intensity` troca a do estilo (o "Reeditar"); `recent` são os `lib_id` de meme e SFX usados nos últimos vídeos do
    canal (None: lidos do banco)."""
    grammar = grammar_for(style)
    if intensity is not None:
        grammar = grammar.model_copy(update={"intensity": max(0, min(3, int(intensity)))})
    if grammar.editor == "off":
        return None
    if recent is None:
        recent = recent_lib_ids(doc)
    _Planner(doc, style, grammar, pool, recent).run()
    return None


def summary(doc: ProjectDoc) -> dict[str, Any]:
    """Contagem do que o editor deixou: cenas com subplanos, planos e eventos por tipo."""
    fx = Counter(e.type for s in doc.scenes for e in s.fx)
    return {
        "scenes_with_shots": sum(1 for s in doc.scenes if len(s.shots) > 1),
        "shots": sum(len(s.shots) for s in doc.scenes),
        "fx": dict(sorted(fx.items())),
        "fx_total": sum(fx.values()),
    }


def timed_shots(doc: ProjectDoc, style: StyleSpec, pool: Pool | None = None) -> list[tuple[str, float, float, str]]:
    """Os planos do vídeo pelo tempo estimado das palavras: `(cena, início_s, duração_s, nível)`. Cena sem `shots` é
    um plano só. Para conferir o ritmo antes da narração (testes e métricas)."""
    pool = pool or Pool()
    planner = _Planner(doc, style, grammar_for(style), pool, set())
    out: list[tuple[str, float, float, str]] = []
    for beat in planner.beats:
        shots = beat.scene.shots
        if not shots:
            out.append((beat.scene.id, beat.start, beat.dur, beat.prov))
            continue
        starts = [planner._time_of(beat, s.at) if s.at else beat.start for s in shots]
        ends = [*starts[1:], beat.end]
        for shot, a, b in zip(shots, starts, ends, strict=True):
            item = pool.item(shot.pool_id) if shot.source == "pool" else None
            out.append((beat.scene.id, a, b - a, item.tier if item is not None else beat.prov))
    return out


def track(doc: ProjectDoc, style: StyleSpec, pool: Pool | None = None) -> list[dict[str, Any]]:
    """A trilha de edição por cena (tela "Edição" da prévia): palavras faladas com o gatilho (`at`) que acha cada uma
    e o tempo estimado, e os subplanos e eventos com o instante relativo ao início da cena. Só leitura; os tempos são
    os do planejador (estimados pelo texto), a timeline mede com a narração real."""
    pool = pool or Pool()
    planner = _Planner(doc, style, grammar_for(style), pool, set())
    out: list[dict[str, Any]] = []
    for beat in planner.beats:
        scene = beat.scene

        def rel(at: str | None, beat: _Beat = beat) -> float:
            return round(max(0.0, planner._time_of(beat, at) - beat.start), 3)

        shots = []
        for i, shot in enumerate(scene.shots):
            item = pool.item(shot.pool_id) if shot.source == "pool" and shot.pool_id else None
            shots.append({"i": i, "t": rel(shot.at) if shot.at else 0.0, "prov": item.tier if item else beat.prov,
                          "desc": item.desc if item else "", **shot.model_dump(mode="json")})
        fx = [{"i": i, "t": rel(event.at), **event.model_dump(mode="json")} for i, event in enumerate(scene.fx)]
        out.append({
            "scene_id": scene.id, "order": beat.order, "start_s": round(beat.start, 3), "dur_s": round(beat.dur, 3),
            "prov": beat.prov, "locked": scene.locked, "kind": scene.kind,
            "mark": beat.mark.model_dump(mode="json"),
            "words": [{"text": w.text, "at": w.at, "t": round(w.start - beat.start, 3)} for w in beat.words],
            "shots": shots, "fx": fx,
        })
    return out


def scene_triggers(doc: ProjectDoc, style: StyleSpec, scene_id: str) -> list[str] | None:
    """As palavras faladas da cena (para conferir um gatilho movido pelo usuário). None: cena sem fala."""
    planner = _Planner(doc, style, grammar_for(style), Pool(), set())
    beat = next((b for b in planner.beats if b.scene.id == scene_id), None)
    return [w.text for w in beat.words] if beat is not None else None


def recent_lib_ids(doc: ProjectDoc, limit: int = HISTORY_VIDEOS) -> set[str]:
    """Os `lib_id` de meme e SFX dos últimos `limit` projetos do mesmo canal (pelo `Scene.fx` da versão atual). Sem
    canal ou sem banco: vazio."""
    if not doc.channel_id:
        return set()
    try:
        from studio.core.project import service
        from studio.db import session_scope
        from studio.models import Project

        with session_scope() as s:
            rows = (s.query(Project.id).filter(Project.channel_id == doc.channel_id, Project.id != doc.id)
                    .order_by(Project.updated_at.desc()).limit(limit).all())
        used: set[str] = set()
        for (project_id,) in rows:
            try:
                other = service.get_doc(project_id)
            except Exception:  # noqa: BLE001 - projeto sem versão legível não entra na memória
                continue
            for scene in other.scenes:
                for event in scene.fx:
                    lib_id = event.params.get("lib_id")
                    if event.type in ("meme", "sfx") and isinstance(lib_id, str) and lib_id:
                        used.add(lib_id)
        return used
    except Exception as exc:  # noqa: BLE001 - sem memória, o planejador segue
        log.warning("memória de memes e SFX indisponível: %s", exc)
        return set()


def _pick_meme(tag: str, seed: int, exclude: set[str], presenter: Any | None = None) -> Any | None:
    """Meme da biblioteca do usuário com a etiqueta, ou uma pose do avatar do canal como reação
    (`media.meme_library.pick`)."""
    from studio.media import meme_library

    try:
        return meme_library.pick(tag, seed, exclude, presenter=presenter)
    except Exception as exc:  # noqa: BLE001 - biblioteca com problema: sai o "meme sem meme"
        log.warning("a biblioteca de memes falhou: %s", exc)
        return None


def _available_kind(kind: str) -> str:
    """O tipo de SFX que o mixer gera: o pedido, ou o parecido quando ele ainda não existe."""
    try:
        from studio.media.audio import SFX_KINDS
    except Exception:  # noqa: BLE001
        return kind
    if kind in SFX_KINDS:
        return kind
    fallback = SFX_FALLBACK.get(kind, "impact")
    return fallback if fallback in SFX_KINDS else SFX_KINDS[0]


def _weighted(rng: random.Random, weights: Iterable[tuple[str, float]]) -> str | None:
    items = [(k, w) for k, w in weights if w > 0]
    if not items:
        return None
    total = sum(w for _, w in items)
    roll = rng.random() * total
    for key, weight in items:
        roll -= weight
        if roll <= 0:
            return key
    return items[-1][0]


# --- o planejador -----------------------------------------------------------------------------------------------------


class _Planner:
    def __init__(self, doc: ProjectDoc, style: StyleSpec, grammar: EditGrammar, pool: Pool, recent: set[str]) -> None:
        self.doc = doc
        self.style = style
        self.g = grammar
        self.pool = pool
        self.recent = set(recent)
        self.scale = INTENSITY_SCALE.get(grammar.intensity, 1.0)
        self.short = style.format.type == "short"
        self.events: list[_Event] = []
        self.used_lib: set[str] = set()
        self.reframes: Counter[str] = Counter()
        self.pool_uses: Counter[str] = Counter()
        self.beats = self._beats()
        self.total = max((b.end for b in self.beats), default=0.0)
        self.budget = _Budget(grammar, self.scale, self.total)
        self.switch_left = int(math.floor(grammar.switch_per_min * self.scale * self.total / 60.0 + 1e-9))

    def _presenter(self) -> Any | None:
        """O avatar do canal (as poses viram reações), lido uma vez. None: sem canal, desligado ou sem banco."""
        if not hasattr(self, "_presenter_cache"):
            found = None
            if self.doc.channel_id:
                try:
                    from studio.core.project.presenter import channel_presenter

                    found = channel_presenter(self.doc.channel_id)
                except Exception as exc:  # noqa: BLE001 - sem avatar, só a biblioteca de memes
                    log.warning("avatar do canal indisponível para reações: %s", exc)
            self._presenter_cache = found if found is not None and found.enabled else None
        return self._presenter_cache

    def _rng(self, beat: _Beat, *purpose: Any) -> random.Random:
        return random.Random(seed_from(self.doc.seed, beat.scene.id, "edit", *purpose))

    # --- 1 e 2: tempo das palavras e marcas ---------------------------------------------------------------------------

    def _beats(self) -> list[_Beat]:
        doc = self.doc
        lang = doc.primary_language
        wpm = self.style.script.words_per_minute.get(lang, 150)
        step = 60.0 / max(60.0, wpm * (self.style.voice.speed or 1.0))
        gap = self.style.voice.gap_s
        timed: dict[str, tuple[list[Word], list[float]]] = {}
        t = 0.0
        for seg in doc.segments:
            tokens = split_words(seg.text_for(lang, lang))
            seg_words: list[Word] = []
            pauses: list[float] = []
            if tokens:
                lens = [max(1, len(word_key(tok))) for tok in tokens]
                mean = sum(lens) / len(lens)
                weights = [0.55 + 0.45 * n / mean for n in lens]
                norm = len(tokens) / sum(weights)
                for tok, weight in zip(tokens, weights, strict=True):
                    dur = step * weight * norm
                    seg_words.append(Word(tok, t, t + dur))
                    tail = tok.rstrip(_CLOSERS)
                    pause = 0.32 if tail.endswith(_SENTENCE_END) else 0.16 if tail.endswith(_CLAUSE_END) else 0.0
                    pauses.append(pause)
                    t += dur + pause
            scenes = doc.scenes_of(seg.id)
            if scenes:
                ranges = scene_word_ranges(scenes, seg_words)
                for k, scene in enumerate(scenes):
                    a, b = ranges[k] if k < len(ranges) else (len(seg_words), len(seg_words))
                    timed[scene.id] = (seg_words[a:b], pauses[a:b])
            t += gap
        roles = {seg.id: seg.role for seg in doc.segments}
        first_of_segment: set[str] = set()
        for seg in doc.segments:
            scenes = doc.scenes_of(seg.id)
            if scenes:
                first_of_segment.add(scenes[0].id)
        beats: list[_Beat] = []
        list_runs = self._list_flags(doc)
        for order, scene in enumerate(doc.ordered_scenes()):
            words, pauses = timed.get(scene.id, ([], []))
            if not words:
                continue
            seg_role = roles.get(scene.segment_id, "body")
            media = doc.shown_media(scene)
            item = self.pool.item(media.pool_id) if media is not None and media.pool_id else None
            mark = scene.mark or self._infer_mark(scene, [w.text for w in words], seg_role, order, list_runs)
            ws = self._word_rows(words, mark, seg_role == "chapter" and scene.id in first_of_segment)
            root = doc.reuse_root(scene)
            asset = (media.asset or media.pool_id or media.video or "") if media is not None else ""
            asset = asset or (scene.visual.asset or "") or (root.id if root is not None else scene.id)
            tags = _tagset([*(item.tags + item.entities if item is not None else []), *scene.visual.entities])
            tags |= {w.key for w in ws if w.content}
            keep_shots = any(s.locked or s.origin == "user" for s in scene.shots)
            beats.append(_Beat(
                scene=scene, order=order, words=ws, start=words[0].start, end=words[-1].end + (pauses[-1] or 0.12),
                prov=prov_of_scene(scene, doc), mark=mark, editable=not scene.locked, seg_role=seg_role, media=media,
                item=item, asset=asset, tags={t for t in tags if t}, keep_shots=keep_shots, pos=len(beats),
            ))
        # zoom_to já usado nas cenas que reaproveitam a imagem conta no limite de reenquadramento
        for beat in beats:
            if beat.scene.motion == "zoom_to":
                self.reframes[_reframe_key(beat)] += 1
        return beats

    @staticmethod
    def _list_flags(doc: ProjectDoc) -> set[str]:
        """Cenas que formam lista: duas ou mais seguidas com `count` ou com o papel `list`."""
        return {s.id for s in doc.ordered_scenes()
                if s.kind == "count" or (s.mark is not None and s.mark.role == "list")}

    def _infer_mark(self, scene: Scene, texts: list[str], seg_role: str, order: int, lists: set[str]) -> BeatMark:
        from studio.workers.pipeline.presenter import _serious

        text = " ".join(texts)
        lang = self.doc.primary_language
        tail = text.rstrip(_CLOSERS)
        numbers = notable_number_runs(texts, lang)
        if seg_role == "hook" or (order == 0 and seg_role != "cta"):
            role = "hook"
        elif seg_role == "cta":
            role = "cta"
        elif scene.id in lists:
            role = "list"
        elif numbers:
            role = "number"
        elif tail.endswith("?"):
            role = "tension"
        elif tail.endswith("!"):
            role = "reveal"
        else:
            role = "breath"
        emphasis: list[str] = []
        if numbers:
            emphasis.append(_at_text(texts[numbers[0][0]]))
        contents = [(i, t) for i, t in enumerate(texts) if _is_content(word_key(t))]
        proper = [t for i, t in contents if i > 0 and t[:1].isupper()]
        strong = sorted((t for _, t in contents if len(word_key(t)) >= 7), key=lambda t: -len(word_key(t)))
        for cand in (*proper, *strong):
            clean = _at_text(cand)
            if clean and clean not in emphasis and len(emphasis) < 2:
                emphasis.append(clean)
        energy = {"hook": 3, "reveal": 2, "tension": 2, "list": 2, "number": 2}.get(role, 1)
        return BeatMark(role=role, emphasis=[e for e in emphasis if e], energy=energy,
                        serious=_serious(text) is not None)

    def _word_rows(self, words: list[Word], mark: BeatMark, chapter: bool) -> list[_W]:
        texts = [w.text for w in words]
        keys = [word_key(t) for t in texts]
        emph = {word_key(e) for e in mark.emphasis}
        numbers: set[int] = set()
        for i0, i1, _value in notable_number_runs(texts, self.doc.primary_language):
            numbers.update(range(i0, i1))
        gag_index = trigger_index(texts, mark.gag.at) if mark.gag is not None and mark.gag.at else None
        rows: list[_W] = []
        seen: Counter[str] = Counter()
        for i, (word, key) in enumerate(zip(words, keys, strict=True)):
            seen[key] += 1
            row = _W(text=word.text, key=key, start=word.start, end=word.end, index=i,
                     comma_before=i > 0 and texts[i - 1].rstrip(_CLOSERS).endswith(_CLAUSE_END + _SENTENCE_END),
                     number=i in numbers, emph=bool(key) and key in emph, content=_is_content(key),
                     chapter=chapter and i == 0, gag=gag_index == i)
            row.at = self._at_for(texts, i, seen[key], word.start - words[0].start)
            rows.append(row)
        return rows

    @staticmethod
    def _at_for(texts: list[str], index: int, occurrence: int, offset_s: float) -> str:
        clean = _at_text(texts[index])
        if clean:
            at = clean if occurrence == 1 else f"{clean}#{occurrence}"
            if PRESENTER_AT_RE.match(at) and trigger_index(texts, at) == index:
                return at
        return f"@{max(0, round(offset_s * 1000))}"

    # --- execução ---------------------------------------------------------------------------------------------------

    def run(self) -> None:
        for beat in self.beats:
            if beat.editable and beat.scene.mark is None:
                beat.scene.mark = beat.mark  # só complementa: marca que veio do storyboard fica
        self._register_kept()
        recent_durs: list[float] = []
        prov_run: list[str] = []
        for beat in self.beats:
            if beat.editable and not beat.keep_shots and beat.framed:
                self._plan_shots(beat, recent_durs, prov_run)
            else:
                recent_durs.append(beat.dur)
                prov_run.append(beat.prov)
        self._camera_shares()
        self._structural()
        self._max_still()
        self._fill("punch")
        self._fill("text_pop")
        self._fill("shake")
        self._fill_sfx()
        self._write()

    def _register_kept(self) -> None:
        for beat in self.beats:
            for event in beat.scene.fx:
                if beat.editable and not (event.locked or event.origin == "user"):
                    continue
                t = self._time_of(beat, event.at)
                self.events.append(_Event(event.type, t, beat, event, kept=True))
                kind = _budget_kind(event.type)
                if kind is not None:
                    self.budget.spend(kind, t)
                lib_id = event.params.get("lib_id")
                if isinstance(lib_id, str):
                    self.used_lib.add(lib_id)

    @staticmethod
    def _time_of(beat: _Beat, at: str | None) -> float:
        if at and at.startswith("@"):
            try:
                return beat.start + int(at[1:]) / 1000.0
            except ValueError:
                return beat.start
        word = beat.word_at(at)
        return word.start if word is not None else beat.start

    # --- 3 e 4: ritmo e fonte dos planos ----------------------------------------------------------------------------

    def _median(self, t: float) -> float:
        hook = self.g.hook
        if hook is not None and hook.shot_median_s is not None and t < self.budget.window:
            return hook.shot_median_s
        return self.g.shot_median_s

    def _sample(self, rng: random.Random, t: float) -> float:
        sigma = math.sqrt(math.log(1.0 + self.g.shot_cv ** 2))
        value = math.exp(math.log(self._median(t)) + sigma * rng.gauss(0.0, 1.0))
        return max(MIN_SHOT_S, min(self.g.shot_max_s, value))

    def _plan_shots(self, beat: _Beat, recent_durs: list[float], prov_run: list[str]) -> None:
        rng = self._rng(beat, "shots")
        role = beat.mark.role
        shots = [_Shot(t=beat.start, word=None, prov=beat.prov)]
        breath_clip = role == "breath" and beat.clip
        forced: _W | None = self._reveal_word(beat) if role == "reveal" else None
        burst = 0
        if role in ("hook", "joke", "list") and rng.random() < self.g.burst_prob:
            burst = rng.randint(2, 4) - 1  # cortes da rajada (planos - 1)
        t = beat.start
        guard = 0
        while guard < 40:
            guard += 1
            if forced is not None and forced.start > t + MIN_SHOT_S and forced.index > 0:
                target_word: _W | None = forced
                forced = None
            else:
                if burst > 0:
                    d = rng.uniform(*BURST_S)
                    burst -= 1
                elif breath_clip:
                    d = rng.uniform(*BREATH_S)
                else:
                    d = self._sample(rng, t)
                target_word = self._cut_word(beat, t, t + d, recent_durs + [s.dur for s in shots[:-1]])
            if target_word is None:
                break
            shot = self._source_for(beat, shots, target_word, rng, prov_run)
            if shot is None:
                t = target_word.start  # sem fonte: o plano segue (não corta), mas o ritmo anda
                continue
            shots[-1].end = target_word.start
            shots.append(shot)
            t = target_word.start
        shots[-1].end = beat.end
        # rajada/corte que deixou um plano curto demais no fim: junta com o anterior
        while len(shots) > 1 and shots[-1].dur < MIN_SHOT_S:
            shots.pop()
            shots[-1].end = beat.end
        for shot in shots:
            recent_durs.append(shot.dur)
            prov_run.append(shot.prov)
        if breath_clip:
            for shot in shots:
                shot.motion = "none"
        if len(shots) > 1 or breath_clip:
            for shot in shots:
                if shot.motion is None:
                    self._camera(beat, shot, rng)
            beat.shots = shots

    def _reveal_word(self, beat: _Beat) -> _W | None:
        for test in (lambda w: w.emph, lambda w: w.number, lambda w: w.content):
            word = next((w for w in beat.words if test(w) and w.start <= beat.latest_start()), None)
            if word is not None:
                return word
        return None

    def _cut_word(self, beat: _Beat, shot_start: float, target: float, durs: list[float]) -> _W | None:
        """A palavra do corte: a mais perto do alvo, com preferência de ênfase > número > vírgula > substantivo."""
        if target >= beat.end - MIN_SHOT_S:
            return None  # o alvo passa do fim da cena: o plano vai até o fim
        limit = min(beat.latest_start(), beat.end - MIN_SHOT_S)
        best: tuple[float, int] | None = None
        best_word: _W | None = None
        for word in beat.words[1:]:
            if word.start < shot_start + MIN_SHOT_S or word.start > limit:
                continue
            score = -abs(word.start - target) / 0.6
            score += 1.0 if word.emph else 0.8 if word.number else 0.6 if word.comma_before else 0.3 if word.content else 0
            prev = beat.words[word.index - 1]
            if prev.key in _FUNCTION_WORDS and not word.comma_before:
                score -= 0.8
            if word.key in _FUNCTION_WORDS and word.key not in _GOOD_BEFORE and not word.comma_before:
                score -= 0.6  # "parte | do farol": o corte não separa a preposição do que ela liga
            piece = word.start - shot_start
            if len(durs) >= 2 and _similar(durs[-1], durs[-2]) and _similar(piece, durs[-1]):
                score -= 3.0
            if durs and _similar(piece, durs[-1]):
                score -= 0.5
            if abs(piece - (beat.end - word.start)) <= 0.25 * max(piece, beat.end - word.start):
                score -= 0.7  # cena partida ao meio: dois planos iguais (o corte assimétrico varia mais)
            if best is None or (score, -word.index) > best:
                best = (score, -word.index)
                best_word = word
        return best_word

    def _source_for(self, beat: _Beat, shots: list[_Shot], word: _W, rng: random.Random,
                    prov_run: list[str]) -> _Shot | None:
        """Fonte do novo plano: próximo trecho do clipe > acervo com a mesma etiqueta > reenquadramento. Com 3 planos
        seguidos do mesmo nível, o acervo de outro nível passa na frente."""
        own = next(s for s in reversed(shots) if s.source == "self")  # o primeiro plano é sempre o da cena
        run = [*prov_run, *(s.prov for s in shots)]
        tired = len(run) >= MAX_SAME_PROV and len(set(run[-MAX_SAME_PROV:])) == 1
        avoid = run[-1] if tired else None
        if tired:
            probe = self._pool_shot(beat, shots, word, rng, avoid=avoid, dry=True)
            if probe is not None and probe.prov != avoid:
                return self._pool_shot(beat, shots, word, rng, avoid=avoid)
        return (self._clip_next(beat, own, word, rng)
                or self._pool_shot(beat, shots, word, rng, avoid=avoid)
                or self._reframe(beat, own, word, rng))

    def _clip_next(self, beat: _Beat, prev: _Shot, word: _W, rng: random.Random) -> _Shot | None:
        """O trecho seguinte do mesmo clipe (pula 0,4 a 1,5 s do material), dentro do `dur_s` do trecho."""
        media = beat.media
        if media is None or not media.video:
            return None
        start = prev.clip_in if prev.clip_in is not None else media.start_s
        clip_in = start + (word.start - prev.t) + rng.uniform(0.4, 1.5)
        if media.dur_s is not None and clip_in + 0.5 > media.start_s + media.dur_s:
            return None
        return _Shot(t=word.start, word=word, prov=beat.prov, clip_in=round(clip_in, 2), motion="none",
                     hold=media.dur_s is None)

    def _pool_shot(self, beat: _Beat, shots: list[_Shot], word: _W, rng: random.Random, avoid: str | None,
                   *, dry: bool = False) -> _Shot | None:
        """Outro item do acervo com etiqueta em comum (cota de `switch_per_min`); `dry` só consulta."""
        if self.switch_left <= 0 or not beat.tags:
            return None
        recent_ids = {s.pool_id for b in self.beats[max(0, beat.pos - 2):beat.pos] for s in b.shots if s.pool_id}
        recent_ids |= {s.pool_id for s in shots if s.pool_id}
        if dry:  # a consulta não mexe no sorteio de quem decide
            rng = random.Random(seed_from(self.doc.seed, beat.scene.id, "edit", "probe", word.index))
        best: tuple[float, float] | None = None
        chosen: PoolItem | None = None
        for item in self.pool.items:
            if item.media == "meme" or not (item.path or item.poster):
                continue
            if beat.item is not None and item.id == beat.item.id:
                continue
            if item.id in recent_ids or self.pool_uses[item.id] >= 2:
                continue
            overlap = len(beat.tags & _tagset(item.tags + item.entities))
            if overlap == 0:
                continue
            score = overlap + 0.1 * item.score + (2.0 if avoid is not None and item.tier != avoid else 0.0)
            if avoid is not None and item.tier == avoid:
                score -= 2.0
            key = (score, rng.random())
            if best is None or key > best:
                best, chosen = key, item
        if chosen is None:
            return None
        if dry:
            return _Shot(t=word.start, word=word, prov=chosen.tier, source="pool", pool_id=chosen.id)
        self.switch_left -= 1
        self.pool_uses[chosen.id] += 1
        clip_in = None
        if chosen.media == "clip":  # o segundo uso do mesmo clipe entra noutro ponto
            clip_in = chosen.start_s + 2.0 * (self.pool_uses[chosen.id] - 1)
            if chosen.dur_s is not None and clip_in + 1.0 > chosen.start_s + chosen.dur_s:
                clip_in = chosen.start_s
        motion = "none" if chosen.media == "clip" else "static" if chosen.rects else None
        return _Shot(t=word.start, word=word, prov=chosen.tier, source="pool", pool_id=chosen.id, clip_in=clip_in,
                     motion=motion)

    def _reframe(self, beat: _Beat, prev: _Shot, word: _W, rng: random.Random) -> _Shot | None:
        """O mesmo asset noutro enquadramento (jump cut 1,0 ↔ 1,25 noutro ponto), até 2 vezes por asset."""
        key = _reframe_key(beat)
        if beat.clip or self.reframes[key] >= MAX_REFRAMES:
            return None
        self.reframes[key] += 1
        wide = prev.framing is None or prev.framing.scale < 1.1
        scale = REFRAME_SCALE if wide else 1.0
        x = round(rng.uniform(0.35, 0.65), 3)
        y = round(rng.uniform(0.38, 0.62), 3)
        return _Shot(t=word.start, word=word, prov=beat.prov, framing=ShotFraming(scale=scale, x=x, y=y))

    # --- 5: câmera ----------------------------------------------------------------------------------------------------

    def _camera(self, beat: _Beat, shot: _Shot, rng: random.Random) -> None:
        if shot.source == "pool" and shot.motion is not None:
            return
        if beat.clip and shot.source == "self":
            shot.motion = "none"
            return
        if beat.capture and shot.source == "self":
            shot.motion = "static"
            return
        open_shot = (shot.source == "self" and beat.scene.visual.shot in OPEN_SHOTS
                     and (shot.framing is None or shot.framing.scale < 1.1))
        choice = _weighted(rng, [(k, w) for k, w in CAMERA_WEIGHTS if k != "pan" or open_shot]) or "static"
        if choice == "pan":
            shot.motion = "pan_left" if rng.random() < 0.5 else "pan_right"
        elif choice == "punch":
            shot.motion = "static"
            shot.easing = "snap"
            base = shot.framing or ShotFraming()
            shot.framing = ShotFraming(scale=round(min(1.8, max(base.scale, rng.uniform(*PUNCH_CAMERA))), 3),
                                       x=base.x, y=base.y)
            return
        else:
            shot.motion = choice
        if shot.motion != "static":
            shot.strength = round(rng.uniform(0.6, 1.4), 3)
            shot.easing = _weighted(rng, EASINGS)

    def _camera_shares(self) -> None:
        """`slow_zoom_share` (teto de zoom lento) e `static_share_min` (piso de plano parado) nos planos do editor."""
        free = [(b, s) for b in self.beats for s in b.shots if s.motion is not None and s.motion != "none"
                and not (b.clip and s.source == "self")]
        if not free:
            return
        n = len(free)
        ranked = sorted(free, key=lambda bs: seed_from(self.doc.seed, bs[0].scene.id, "edit", "cam", bs[1].t))
        slow = [bs for bs in ranked if bs[1].motion in SLOW_MOTIONS]
        excess = len(slow) - int(math.floor(self.g.slow_zoom_share * n + 1e-9))
        for _beat, shot in slow[:max(0, excess)]:
            shot.motion, shot.easing, shot.strength = "static", None, None
        static = sum(1 for _b, s in free if s.motion == "static")
        need = int(math.ceil(self.g.static_share_min * n - 1e-9)) - static
        for _beat, shot in ranked:
            if need <= 0:
                break
            if shot.motion != "static":
                shot.motion, shot.easing, shot.strength = "static", None, None
                need -= 1

    # --- 6: eventos ---------------------------------------------------------------------------------------------------

    def _free(self, beat: _Beat, word: _W, fx_type: str, spacing: float = SPACING_S) -> bool:
        if not beat.editable or word.start > beat.latest_start() or word.start > self.total - TAIL_S:
            return False
        if beat.mark.serious and fx_type in COMIC_FX:
            return False
        group = {fx_type} if fx_type not in ANNOT_TYPES else set(ANNOT_TYPES)
        return all(abs(e.t - word.start) >= spacing for e in self.events if e.type in group)

    def _add(self, beat: _Beat, word: _W | None, fx_type: str, *, dur_ms: int | None = None,
             params: dict[str, Any] | None = None, at: str | None = None, t: float | None = None) -> FxEvent:
        event = FxEvent(type=fx_type, at=at or (word.at if word is not None else "@0"), dur_ms=dur_ms,
                        params=params or {})
        when = t if t is not None else (word.start if word is not None else beat.start)
        self.events.append(_Event(fx_type, when, beat, event))
        return event

    def _try(self, kind: str, beat: _Beat, word: _W, fx_type: str | None = None, **kw: Any) -> bool:
        """Põe o evento se há cota (`kind`) e espaço; devolve se entrou."""
        fx_type = fx_type or kind
        if not self._free(beat, word, fx_type) or not self.budget.has(kind, word.start):
            return False
        self.budget.take(kind, word.start)
        self._add(beat, word, fx_type, **kw)
        return True

    def _structural(self) -> None:
        lists = self._list_runs()
        meme_gap = MEME_GAP_S["short" if self.short else "long"]
        for beat in self.beats:
            if not beat.editable:
                continue
            role = beat.mark.role
            if role == "reveal":
                self._reveal(beat)
            if beat.mark.gag is not None and not beat.mark.serious and self.g.humor != "none":
                self._gag(beat, meme_gap)
            if beat.scene.id in lists and self.g.intensity > 0:
                k, n = lists[beat.scene.id]
                word = beat.words[0]
                if self._free(beat, word, "counter", spacing=0.5):
                    self._add(beat, word, "counter", params={"text": f"{k}/{n}"}, dur_ms=1500)
            if beat.capture and beat.media is not None:
                self._highlight(beat)

    def _list_runs(self) -> dict[str, tuple[int, int]]:
        runs: list[list[_Beat]] = []
        for beat in self.beats:
            is_list = beat.mark.role == "list" or beat.scene.kind == "count"
            if is_list and runs and runs[-1] and runs[-1][-1].order == beat.order - 1:
                runs[-1].append(beat)
            elif is_list:
                runs.append([beat])
        out: dict[str, tuple[int, int]] = {}
        for run in runs:
            if len(run) >= 2:
                for k, beat in enumerate(run, 1):
                    out[beat.scene.id] = (k, len(run))
        return out

    def _reveal(self, beat: _Beat) -> None:
        word = self._reveal_word(beat)
        if word is None:
            return
        rng = self._rng(beat, "reveal")
        lo, hi = self.g.punch_scale.min, self.g.punch_scale.max
        if self._try("punch", beat, word, params={"scale": round(rng.uniform((lo + hi) / 2, hi), 3)}):
            self._sfx(beat, word, "reveal", rng)
        if self.g.music_drop_before_reveal and self.g.intensity > 0:
            prev_beat, prev_word = self._word_before(beat, word)
            if prev_word is not None and prev_beat is not None and prev_beat.editable:
                dur = int(min(600, max(300, round((word.start - prev_word.start) * 1000))))
                self._add(prev_beat, prev_word, "music_drop", dur_ms=dur)

    def _word_before(self, beat: _Beat, word: _W) -> tuple[_Beat | None, _W | None]:
        if word.index > 0:
            return beat, beat.words[word.index - 1]
        if beat.pos == 0:
            return None, None
        prev = self.beats[beat.pos - 1]
        return prev, prev.words[-1]

    def _gag_word(self, beat: _Beat) -> _W | None:
        word = next((w for w in beat.words if w.gag), None)
        if word is None:
            word = next((w for w in beat.words if w.emph), None)
        if word is None:
            contents = [w for w in beat.words if w.content and w.start <= beat.latest_start()]
            word = contents[-1] if contents else None
        if word is not None and word.start > beat.latest_start():
            fitting = [w for w in beat.words if w.start <= beat.latest_start()]
            word = fitting[-1] if fitting else None
        return word

    def _gag(self, beat: _Beat, meme_gap: float) -> None:
        gag = beat.mark.gag
        assert gag is not None
        word = self._gag_word(beat)
        if word is None or not self._free(beat, word, "meme") or \
                any(e.type in ("meme", "freeze") and abs(e.t - word.start) < meme_gap for e in self.events):
            return
        if not self.budget.take("meme", word.start):
            return
        rng = self._rng(beat, "gag")
        item = None
        if gag.payoff == "meme" or gag.tag:
            pick_args: tuple[Any, ...] = (gag.tag or gag.payoff, seed_from(self.doc.seed, beat.scene.id, "edit", "meme"),
                                          self.recent | self.used_lib)
            presenter = self._presenter()
            item = _pick_meme(*pick_args, presenter) if presenter is not None else _pick_meme(*pick_args)
        if item is not None:
            lib_id = str(getattr(item, "id", "") or "")
            max_ms = getattr(item, "max_ms", None) or 900
            params: dict[str, Any] = {"lib_id": lib_id, "tag": gag.tag, "variant": "corner"}
            file = getattr(item, "file", None)
            if isinstance(file, str) and file:
                # o arquivo vai junto (relativo à pasta de dados): o renderer desenha `src` (png) ou `video` (webm)
                params["video" if getattr(item, "media", "png") == "webm" else "src"] = file
            self._add(beat, word, "meme", dur_ms=int(min(1500, max(400, max_ms))), params=params)
            self.used_lib.add(lib_id)
            sfx_id = getattr(item, "sfx_id", None)
            self._sfx(beat, word, "gag", rng, lib_id=str(sfx_id) if sfx_id else None)
            return
        # "meme sem meme": congela + punch forte + SFX cômico + a frase da piada
        payoff = gag.payoff if gag.payoff != "meme" else "freeze"
        if payoff == "freeze":
            self._add(beat, word, "freeze", dur_ms=int(rng.uniform(600, 900)))
        if payoff in ("freeze", "zoom"):
            self._try("punch", beat, word, params={"scale": 1.3})
        if payoff in ("freeze", "text") and (gag.line or payoff == "text"):
            self._try("text_pop", beat, word, params={"text": _short_text(gag.line) or _at_text(word.text)})
        self._sfx(beat, word, "gag" if payoff != "text" else "text_pop", rng)

    def _highlight(self, beat: _Beat) -> None:
        media = beat.media
        assert media is not None
        candidates = [w for w in beat.words if w.start <= beat.latest_start()]
        word = next((w for w in candidates if w.emph), None) or next((w for w in candidates if w.number), None) or \
            next((w for w in candidates if w.content and w.index > 0), None) or (candidates[0] if candidates else None)
        if word is None:
            return
        rect = [round(v, 4) for v in media.rects[0]]
        if self._try("annot", beat, word, "highlight", params={"rect": rect}, dur_ms=1200):
            self._add(beat, word, "zoom_rect", params={"rect": rect})
            self._sfx(beat, word, "highlight", self._rng(beat, "highlight"))

    def _max_still(self) -> None:
        """Nenhum plano parado passa de `max_still_s`: punch no meio (ou o corte já resolve)."""
        limit = self.g.max_still_s
        for beat in self.beats:
            if not beat.editable or beat.card or not beat.framed or beat.clip:
                continue
            # todo plano de imagem conta: um push lento de 20 s também é "parado" para quem assiste
            spans = ([(s.t, s.end) for s in beat.shots if s.motion != "none"] if beat.shots
                     else [(beat.start, beat.end)])
            for start, end in spans:
                if end - start <= limit:
                    continue
                marks = sorted([start, end, *(e.t for e in self.events if e.beat is beat
                                                and e.type in ("punch", "zoom_rect", "shake"))])
                for a, b in zip(marks, marks[1:], strict=False):
                    pieces = math.ceil((b - a) / (limit * 0.8) - 1e-9)  # folga: a palavra fica perto do alvo
                    for k in range(1, pieces):
                        target = a + (b - a) * k / pieces
                        word = min((w for w in beat.words if a + MIN_SHOT_S < w.start < min(b, beat.latest_start())),
                                   key=lambda w, x=target: abs(w.start - x) - (1.0 if w.emph else 0.6 if w.content
                                                                                else 0.0), default=None)
                        if word is None or not self._free(beat, word, "punch"):
                            continue
                        self.budget.take("punch", word.start)  # sem cota entra igual: é trava
                        self._add(beat, word, "punch", params={"scale": round(self.g.punch_scale.min, 3)})

    def _candidates(self, fx_type: str) -> list[tuple[_Beat, _W, float]]:
        out: list[tuple[_Beat, _W, float]] = []
        for beat in self.beats:
            if not beat.editable or beat.card:
                continue
            if fx_type == "shake" and beat.mark.serious:
                continue
            if fx_type == "text_pop" and (beat.scene.overlay_text.get(self.doc.primary_language) or beat.capture):
                continue
            jitter = self._rng(beat, "fill", fx_type)
            for word in beat.words:
                noise = jitter.random() * 0.3
                if word.start > beat.latest_start() or word.start > self.total - TAIL_S:
                    continue
                if fx_type == "text_pop" and not (word.number or word.emph or (word.content and len(word.key) >= 6)):
                    continue
                base = 5.0 if word.emph else 4.0 if word.number else 3.0 if word.chapter else 2.0 if word.gag \
                    else 1.0 if word.content else 0.0
                if fx_type == "punch" and word.index == 0:
                    base -= 0.5  # o corte da cena já marca o começo
                out.append((beat, word, base + noise))
        return out

    def _fill(self, fx_type: str) -> None:
        if self.budget.total(fx_type) <= 0:
            return
        cands = [c for c in self._candidates(fx_type) if self._free(c[0], c[1], fx_type)]
        same = [e.t for e in self.events if e.type == fx_type]
        gaps = [min((abs(w.start - t) for t in same), default=10.0) for _b, w, _s in cands]
        used: set[int] = set()
        rng_scale = self.g.punch_scale
        while self.budget.total(fx_type) > 0:
            best: tuple[float, int] | None = None
            for i, (_beat, word, score) in enumerate(cands):
                if i in used or gaps[i] < SPACING_S or not self.budget.has(fx_type, word.start):
                    continue
                key = (score + min(2.0, gaps[i] / 10.0), -i)
                if best is None or key > best:
                    best = key
            if best is None:
                break
            i = -best[1]
            beat, word, _score = cands[i]
            used.add(i)
            self.budget.take(fx_type, word.start)
            rng = self._rng(beat, fx_type, word.index)
            if fx_type == "punch":
                self._add(beat, word, "punch", params={"scale": round(rng.uniform(rng_scale.min, rng_scale.max), 3)})
            elif fx_type == "text_pop":
                self._add(beat, word, "text_pop", params={"text": self._pop_text(beat, word)}, dur_ms=900)
            elif fx_type == "shake":
                self._add(beat, word, "shake", params={"strength": round(rng.uniform(0.6, 1.2), 3)},
                          dur_ms=int(rng.uniform(300, 500)))
            for j, (_b, other, _s) in enumerate(cands):
                gaps[j] = min(gaps[j], abs(other.start - word.start))

    def _pop_text(self, beat: _Beat, word: _W) -> str:
        if word.number:
            run = []
            for w in beat.words[word.index:]:
                if not w.number or len(run) >= 3:
                    break
                run.append(_at_text(w.text))
            return " ".join(r for r in run if r) or _at_text(word.text)
        return _at_text(word.text)

    # --- SFX ------------------------------------------------------------------------------------------------------------

    def _sfx(self, beat: _Beat, word: _W, purpose: str, rng: random.Random, *, lib_id: str | None = None) -> bool:
        if not self._free(beat, word, "sfx") or not self.budget.take("sfx", word.start):
            return False
        params: dict[str, Any] = {"purpose": purpose}
        if lib_id:
            params["kind"] = _available_kind("boom")
            params["lib_id"] = lib_id
            self.used_lib.add(lib_id)
        else:
            choice = self._sfx_choice(purpose, rng, serious=beat.mark.serious)
            if choice is None:
                self.budget.left["sfx"][self.budget.slot(word.start)] += 1
                return False
            kind, chosen_lib = choice
            params["kind"] = kind
            if chosen_lib:
                params["lib_id"] = chosen_lib
                self.used_lib.add(chosen_lib)
        self._add(beat, word, "sfx", params=params)
        return True

    def _sfx_choice(self, purpose: str, rng: random.Random, *, serious: bool) -> tuple[str, str | None] | None:
        palette = DEFAULT_SFX.get(purpose, DEFAULT_SFX["accent"])
        if self.g.sfx_palette and purpose not in ("reveal", "gag"):
            palette = self.g.sfx_palette
        weights: list[tuple[str, float]] = []
        for key, weight in palette.items():
            if not self.g.sfx_tonal_ok and key in TONAL_SFX:
                continue
            if serious and key in COMIC_SFX:
                continue
            if key in self.recent:
                weight *= 0.25
            weights.append((key, weight))
        choice = _weighted(rng, weights)
        if choice is None:
            return None
        if choice in PROCEDURAL_SFX:
            return _available_kind(choice), None
        return _available_kind("impact"), choice  # id da biblioteca do usuário

    def _fill_sfx(self) -> None:
        if self.budget.total("sfx") <= 0:
            return
        total = sum(self.budget.left["sfx"]) + sum(1 for e in self.events if e.type == "sfx")
        on_cut = int(round(self.g.sfx_on_cut_share * total)) - sum(
            1 for e in self.events if e.type == "sfx" and e.fx.params.get("purpose") == "cut")
        cuts = sorted(((b, s.word) for b in self.beats if b.editable for s in b.shots[1:] if s.word is not None),
                      key=lambda bw: seed_from(self.doc.seed, bw[0].scene.id, "edit", "cut", bw[1].index))
        for beat, word in cuts:
            if on_cut <= 0 or self.budget.total("sfx") <= 0:
                break
            if self._sfx(beat, word, "cut", self._rng(beat, "sfx-cut", word.index)):
                on_cut -= 1
        # nos eventos visuais que ainda não têm som
        visual = sorted((e for e in self.events if e.type in ("punch", "text_pop") and not e.kept),
                        key=lambda e: (e.t, e.type))
        for event in visual:
            if self.budget.total("sfx") <= 0:
                break
            word = event.beat.word_at(event.fx.at)
            if word is not None:
                self._sfx(event.beat, word, event.type, self._rng(event.beat, "sfx", event.type, word.index))
        # o resto, nas palavras fortes
        if self.budget.total("sfx") > 0:
            for beat, word, _score in sorted(self._candidates("sfx"), key=lambda c: (-c[2], c[1].start)):
                if self.budget.total("sfx") <= 0:
                    break
                if word.index == 0 or _score < 1.0:
                    continue
                self._sfx(beat, word, "accent", self._rng(beat, "sfx-accent", word.index))

    # --- escrita ------------------------------------------------------------------------------------------------------

    def _write(self) -> None:
        by_beat: dict[str, list[_Event]] = {}
        for event in self.events:
            by_beat.setdefault(event.beat.scene.id, []).append(event)
        for beat in self.beats:
            scene = beat.scene
            if not beat.editable:
                continue
            if not beat.keep_shots:
                scene.shots = [self._subshot(s) for s in beat.shots]
            events = sorted(by_beat.get(scene.id, []), key=lambda e: (e.t, e.type, e.fx.at))
            seen: set[tuple[str, str]] = set()
            out: list[FxEvent] = []
            for event in events:
                key = (event.fx.type, event.fx.at)
                if key in seen:
                    continue
                seen.add(key)
                out.append(event.fx)
            scene.fx = out
        # cena sem palavras (fora dos beats) e não travada: só o que é do usuário fica
        planned = {b.scene.id for b in self.beats}
        for scene in self.doc.scenes:
            if scene.id not in planned and not scene.locked:
                scene.fx = [e for e in scene.fx if e.locked or e.origin == "user"]
                if not any(s.locked or s.origin == "user" for s in scene.shots):
                    scene.shots = []

    @staticmethod
    def _subshot(shot: _Shot) -> SubShot:
        return SubShot(
            at=shot.word.at if shot.word is not None else None, source=shot.source, pool_id=shot.pool_id,
            framing=shot.framing, motion=shot.motion, easing=shot.easing, strength=shot.strength,  # type: ignore[arg-type]
            clip_in=shot.clip_in, hold=shot.hold,
        )


def _reframe_key(beat: _Beat) -> str:
    """De quem é a cota de `MAX_REFRAMES`. Imagem de IA: do asset no vídeo todo (a mesma IA reenquadrada de novo é o
    enchimento que o ADR 0004 corta). Material real: de cada aparição do asset (a batida), porque o storyboard real
    primeiro volta ao mesmo item do acervo de propósito; com a cota do vídeo todo, um acervo pequeno esgotava os
    cortes logo no começo e o resto do vídeo ficava em planos do tamanho da batida (ritmo uniforme, CV baixo)."""
    return beat.asset if beat.prov == "ai" else f"{beat.asset}#{beat.pos}"


def _similar(a: float, b: float) -> bool:
    return a > 0 and b > 0 and abs(a - b) <= SIMILAR * max(a, b)


def _tagset(values: Iterable[str]) -> set[str]:
    """Etiquetas comparáveis: a forma dobrada ("farol-abandonado") e cada palavra dela."""
    out: set[str] = set()
    for value in values:
        key = fold(value)
        if key:
            out.add(key)
            out.update(part for part in key.split("-") if _is_content(part))
    return out


def _is_content(key: str) -> bool:
    return len(key) >= 4 and key not in _FUNCTION_WORDS and not key.isdigit()


def _at_text(token: str) -> str:
    """A palavra como gatilho: sem pontuação nas pontas, sem espaço nem '#'."""
    clean = _AT_CLEAN.sub("", token).strip("-'’")
    return clean[:60]


def _short_text(text: str, limit: int = 3) -> str:
    return " ".join(text.split()[:limit]).strip(" .,;:!?…")
