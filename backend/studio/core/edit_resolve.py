"""Camada de edição resolvida em quadros (ADR 0004 §3.7).

O documento guarda a edição presa à fala: subplanos (`Scene.shots`) e eventos (`Scene.fx`) com gatilho em palavra
("palavra", "palavra#2") ou em milissegundos ("@1200"). Aqui ela vira o contrato do renderer (`renderer/src/types.ts`):
quadros relativos ao início da cena, o nível (`prov`) de cada plano, os SFX de evento (segundos no vídeo) e as quedas
da trilha. Tudo determinístico pela semente do projeto e da cena.

Só funções puras (o acervo e as imagens chegam prontos). A timeline (`core/timeline.py`) chama `resolve` por cena,
e só nos projetos com a camada de edição (`edit_active`): o projeto antigo gera a mesma timeline de antes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, NamedTuple

from studio.core.hashing import seed_from
from studio.core.project.media_edit import scene_label
from studio.core.project.presenter import parse_at, trigger_index, word_key
from studio.core.sources import REAL_TIERS, prov_of_media, prov_of_scene
from studio.core.styles.library import pick_weighted
from studio.media.audio import SFX_KINDS

if TYPE_CHECKING:
    from studio.core.project.document import FxEvent, MediaRef, ProjectDoc, Scene, SubShot
    from studio.core.project.pool import Pool, PoolItem
    from studio.core.styles.schema import EditGrammar

# Câmeras de zoom lento (a assinatura do slideshow): a gramática limita a fração delas (`slow_zoom_share`).
SLOW_ZOOMS = ("ken_burns_in", "ken_burns_out", "zoom_to")
# Níveis sem grão nem vinheta quando os efeitos são por nível (`effects_policy="per_tier"`): captura de página,
# gráfico desenhado pelo app e material próprio ficam limpos; o resto (arquivo, oficial, IA) leva a textura.
CLEAN_TIERS = frozenset({"capture", "graphic", "own"})
# Duração padrão de cada evento (ms) quando `dur_ms` não vem.
FX_DEFAULT_MS: dict[str, int] = {
    "punch": 600, "shake": 400, "zoom_rect": 1500, "freeze": 700, "flash": 150, "text_pop": 1100, "meme": 1000,
    "sfx": 500, "highlight": 1400, "arrow": 1400, "circle": 1400, "counter": 1500, "music_drop": 450, "speed": 1000,
    "whip": 250, "pip": 2000,
}  # fmt: skip
# Nome de cada evento nos avisos (o usuário não sabe o que é "text_pop").
FX_NAMES: dict[str, str] = {
    "punch": "soco de zoom", "shake": "tremida", "zoom_rect": "zoom no trecho", "freeze": "congelamento",
    "flash": "flash", "text_pop": "texto que pula", "meme": "meme", "sfx": "efeito sonoro", "highlight": "marca-texto",
    "arrow": "seta", "circle": "círculo", "counter": "contador", "music_drop": "queda da trilha",
    "speed": "mudança de velocidade", "whip": "chicote", "pip": "imagem na imagem",
}  # fmt: skip
# Eventos só de áudio: viram SFX ou queda da trilha, nada para o renderer desenhar.
AUDIO_ONLY_FX = frozenset({"sfx", "music_drop"})
# Eventos que apontam um trecho da imagem (`params.rect`, x/y/w/h de 0 a 1): sem retângulo não têm o que mostrar.
RECT_FX = frozenset({"highlight", "zoom_rect"})
SHOT_EASINGS = ("linear", "outCubic", "inOutQuad")
# Planos parados por natureza: o vídeo já se mexe, e a captura com anotação não pode sair do lugar.
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")
# Quanto antes do corte (s) um SFX automático de corte some quando um SFX de evento cai em cima dele.
SFX_STACK_S = 0.25
# Plano mínimo (s, ADR 0004 §3.6.1): plano mais curto é um piscar; ele se funde no vizinho.
MIN_SHOT_S = 0.35
# Tipo procedural de reserva quando o som pedido não existe (ou só existe na biblioteca, `lib_id`).
SFX_FALLBACK = "impact"
# Câmera do estilo que o subplano não usa (precisa das camadas de profundidade, que só a cena tem).
PARALLAX = "parallax"
PARALLAX_FALLBACK = "ken_burns_in"
DEFAULT_FRAMING: dict[str, float] = {"scale": 1.0, "x": 0.5, "y": 0.5}


def edit_active(doc: ProjectDoc) -> bool:
    """O projeto tem a camada de edição (alguma cena com subplanos, eventos ou marcas da batida)? Sem ela, a timeline
    sai exatamente como antes (mesmo hash de bloco)."""
    return any(s.shots or s.fx or s.mark is not None for s in doc.scenes)


def frames_of(ms: float, fps: int) -> int:
    return int(math.floor(ms / 1000 * fps + 0.5))


def min_shot_frames(fps: int) -> int:
    return math.ceil(MIN_SHOT_S * fps)


def fx_name(fx_type: str) -> str:
    return FX_NAMES.get(fx_type, fx_type)


def _seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}".replace(".", ",") + " s"


def _past_end(at: str | None, what: str) -> str:
    _word, _nth, ms = parse_at(at)
    where = f"em {_seconds(ms)}" if ms is not None else f'em "{at}"'
    return f"{what} {where} passa do fim da cena; ficou de fora"


# --- câmera -------------------------------------------------------------------------------------------------------


def grammar_motion(camera: dict[str, float], grammar: EditGrammar, seed: int) -> str:
    """Câmera sorteada respeitando a gramática em média: `static_share_min` das vezes parada, até `slow_zoom_share`
    das vezes um zoom lento (entre os pesos do estilo), e o resto entre os outros movimentos do estilo (sem eles,
    `push`). Para a cota garantida num conjunto de planos, `allocate_motions`."""
    rng = random.Random(seed)
    roll = rng.random()
    if roll < grammar.static_share_min:
        return "static"
    slow = {k: w for k, w in camera.items() if k in SLOW_ZOOMS and w > 0}
    if slow and roll < grammar.static_share_min + grammar.slow_zoom_share:
        return pick_weighted(slow, rng.randrange(2**31))
    rest = {k: w for k, w in camera.items() if k not in SLOW_ZOOMS and k != "static" and w > 0}
    return pick_weighted(rest, rng.randrange(2**31)) if rest else "push"


def allocate_motions(
    units: list[tuple[tuple[Any, ...], dict[str, float]]], grammar: EditGrammar, seed: int
) -> list[str]:
    """Câmera de cada unidade (cena ou plano) com a cota da gramática garantida no conjunto, não só em média.

    `units`: (chave da semente, pesos de câmera daquela unidade). As unidades são ordenadas por semente; as primeiras
    `ceil(static_share_min·n)` ficam paradas, e o resto sorteia com no máximo `floor(slow_zoom_share·n)` zooms lentos
    no total (esgotada a cota, só os outros movimentos). Determinístico: mesma entrada, mesma saída."""
    n = len(units)
    if not n:
        return []
    order = sorted(range(n), key=lambda u: (seed_from(seed, *units[u][0], "grammar"), u))
    n_static = math.ceil(grammar.static_share_min * n - 1e-9)
    slow_left = math.floor(grammar.slow_zoom_share * n + 1e-9)
    # Fora as paradas, a chance do zoom lento é a fração dele no que sobra (a mesma conta de `grammar_motion`).
    free = max(1e-9, 1.0 - grammar.static_share_min)
    p_slow = min(1.0, grammar.slow_zoom_share / free)
    out = [""] * n
    for rank, u in enumerate(order):
        key, camera = units[u]
        if rank < n_static:
            out[u] = "static"
            continue
        rng = random.Random(seed_from(seed, *key, "grammar", "pick"))
        slow = {k: w for k, w in camera.items() if k in SLOW_ZOOMS and w > 0}
        rest = {k: w for k, w in camera.items() if k not in SLOW_ZOOMS and k != "static" and w > 0}
        roll = rng.random()
        if slow and slow_left > 0 and (roll < p_slow or not rest):
            out[u] = pick_weighted(slow, rng.randrange(2**31))
            slow_left -= 1
        else:
            out[u] = pick_weighted(rest, rng.randrange(2**31)) if rest else "push"
    return out


def _easing(motion: str, seed: int) -> str | None:
    if motion in ("static", "none"):
        return None
    return SHOT_EASINGS[random.Random(seed).randrange(len(SHOT_EASINGS))]


# --- gatilhos -----------------------------------------------------------------------------------------------------


class Trigger(NamedTuple):
    """Onde o gatilho caiu: o quadro relativo à cena (None: não bate), a palavra dita ali (o texto que a legenda
    mostra, com o número fundido "2013" quando é ele) e se o quadro veio do mapeamento por posição no vídeo de outro
    idioma (aí a palavra daquela posição é um palpite e não serve de texto)."""

    frame: int | None
    text: str | None = None
    by_position: bool = False


def _clean_word(text: str) -> str:
    return text.strip().strip(".,;:!?…\"'“”«»()[]")


def _is_number(at: str | None) -> bool:
    word = parse_at(at)[0]
    return bool(word) and any(ch.isdigit() for ch in word)


def trigger_frame(
    at: str | None,
    words: list[dict[str, Any]],
    fps: int,
    *,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
    length: int | None = None,
) -> Trigger:
    """Quadro (relativo à cena) do gatilho e a palavra que bateu. `words`: as palavras da cena em quadros
    ({text, from}); `alt_words`: as mesmas com os números fundidos (legenda com algarismos, "2013" de "dois mil e
    treze"); `script`: as palavras da cena no idioma principal, para achar a posição proporcional quando o idioma do
    vídeo é outro (o gatilho é escrito na língua do roteiro).

    No vídeo de outro idioma, a posição vem antes da busca nas palavras traduzidas (o gatilho em português poderia
    bater num falso cognato pelo prefixo), exceto no número, que é igual nas duas línguas."""
    _word, _nth, ms = parse_at(at)
    if ms is not None:
        return Trigger(frames_of(ms, fps))
    if not (at or "").strip():
        return Trigger(0)

    def direct() -> Trigger | None:
        idx = trigger_index([w["text"] for w in words], at)
        if idx is not None:
            return Trigger(int(words[idx]["from"]), _clean_word(str(words[idx]["text"])) or None)
        if alt_words:
            j = trigger_index([w["text"] for w in alt_words], at)
            if j is not None:
                return Trigger(int(alt_words[j]["from"]), _clean_word(str(alt_words[j]["text"])) or None)
        return None

    def by_position() -> Trigger | None:
        if not script:
            return None
        j = trigger_index(script, at)
        if j is None:
            return None
        if words:
            k = min(len(words) - 1, int(j / len(script) * len(words)))
            return Trigger(int(words[k]["from"]), None, True)
        if length:
            return Trigger(int(j / len(script) * length), None, True)
        return None

    steps = (direct, by_position) if not script or _is_number(at) else (by_position, direct)
    for step in steps:
        found = step()
        if found is not None:
            return found
    return Trigger(None)


# --- planos -------------------------------------------------------------------------------------------------------


@dataclass
class _Material:
    """O que o plano mostra: as chaves do contrato ({prov, src?, video?, clipIn?, hold?}), os retângulos da imagem
    (para o marca-texto) e quanto de vídeo útil há a partir do `clipIn` (s; None: sem limite conhecido)."""

    out: dict[str, Any]
    rects: list[list[float]] = field(default_factory=list)
    useful_s: float | None = None
    is_self: bool = False


def _useful(start_s: float, dur_s: float | None, clip_in: float) -> float | None:
    if dur_s is None:
        return None
    return max(0.0, start_s + dur_s - clip_in)


def _shot_material(
    shot: SubShot,
    scene: Scene,
    doc: ProjectDoc | None,
    pool: Pool | None,
    images: dict[str, str | None],
    own: _Material,
    files: dict[str, str | None] | None,
) -> tuple[_Material, str | None]:
    """Origem do plano e um aviso. Origem que falta (ou arquivo que sumiu) cai no visual da cena (`own`).

    `files`: arquivo de origem → o que o renderer recebe (a imagem já recortada e gradada pelo build; None: o
    arquivo sumiu). Sem o mapa, os caminhos vão como estão; caminho fora do mapa também."""

    def mapped(path: str | None) -> tuple[str | None, bool]:
        if not path or files is None or path not in files:
            return path, False
        return files[path], files[path] is None

    if shot.source == "media":
        media = shot.media
        if media is None or not (media.asset or media.video):
            return own, "subplano de mídia sem arquivo; ficou o visual da cena"
        src, src_gone = mapped(media.asset)
        video, video_gone = mapped(media.video)
        if video_gone or (src_gone and not video):
            return own, "o arquivo do subplano sumiu; ficou o visual da cena"
        out: dict[str, Any] = {"prov": prov_of_media(media)}
        if src:
            out["src"] = src
        mat = _Material(out, [list(r) for r in media.rects])
        if video:
            out["video"] = video
            clip_in = shot.clip_in if shot.clip_in is not None else media.start_s
            if clip_in:
                out["clipIn"] = round(float(clip_in), 3)
            mat.useful_s = _useful(media.start_s, media.dur_s, float(clip_in or 0.0))
            if mat.useful_s is not None:  # segundos úteis a partir do clipIn (TimelineShot.seconds)
                out["seconds"] = round(mat.useful_s, 3)
        if shot.hold or media.hold:
            out["hold"] = True
        return mat, None
    if shot.source == "pool":
        item = pool.item(shot.pool_id) if pool is not None else None
        if item is None:
            return own, f"o item {shot.pool_id or '?'} do acervo não existe; ficou o visual da cena"
        if not (item.path or item.poster):
            return own, f"o item {item.id} do acervo ainda não foi baixado; ficou o visual da cena"
        out = {"prov": item.tier}
        mat = _Material(out, [list(r) for r in item.rects])
        is_video = bool(item.path) and (item.media == "clip" or item.path.lower().endswith(VIDEO_EXTS))
        if is_video:
            video, video_gone = mapped(item.path)
            if video_gone:
                return own, "o arquivo do subplano sumiu; ficou o visual da cena"
            out["video"] = video
            poster, _gone = mapped(item.poster)
            if poster:
                out["src"] = poster
            clip_in = shot.clip_in if shot.clip_in is not None else item.start_s
            if clip_in:
                out["clipIn"] = round(float(clip_in), 3)
            mat.useful_s = _useful(item.start_s, item.dur_s, float(clip_in or 0.0))
            if mat.useful_s is not None:  # segundos úteis a partir do clipIn (TimelineShot.seconds)
                out["seconds"] = round(mat.useful_s, 3)
        else:
            src, gone = mapped(item.path or item.poster)
            if gone:
                return own, "o arquivo do subplano sumiu; ficou o visual da cena"
            out["src"] = src
        if shot.hold:
            out["hold"] = True
        return mat, None
    if shot.source == "reuse":
        target = next((s for s in doc.scenes if s.id == shot.reuse_of), None) if doc is not None else None
        src = images.get(shot.reuse_of or "")
        if target is None or doc is None:
            return own, "a cena reaproveitada no subplano não existe; ficou o visual da cena"
        if not src:
            return own, f"a {scene_label(doc, target.id)} reaproveitada no subplano não tem imagem; ficou o visual da cena"
        media = doc.shown_media(target) if doc is not None else None
        rects = [list(r) for r in media.rects] if media is not None else []
        return _Material({"prov": prov_of_scene(target, doc), "src": src}, rects), None
    if shot.source == "ai":
        if own.out["prov"] == "ai":
            return _Material({"prov": "ai"}, is_self=True), None
        if scene.visual.asset and scene.visual.type in ("ai_image", "ai_video"):
            return _Material({"prov": "ai", "src": scene.visual.asset}), None
        return own, "a imagem de IA da cena não está disponível no subplano; ficou o visual da cena"
    return own, None


@dataclass
class _Plan:
    frm: int
    end: int
    key: tuple[Any, ...]
    shot: SubShot | None
    mat: _Material
    cap: int | None = None

    @property
    def dur(self) -> int:
        return self.end - self.frm


def _merge_short(plans: list[_Plan], min_frames: int) -> list[_Plan]:
    """Planos com menos de `min_frames` se fundem no vizinho: o seguinte começa antes (o último da lista ganha, a
    regra do corte no mesmo quadro); o último plano curto alonga o anterior. Clipe com limite (`cap`) não passa dele;
    sem vizinho que caiba, o plano curto fica."""
    plans = list(plans)
    k = 0
    while k < len(plans) and len(plans) > 1:
        cur = plans[k]
        if cur.dur >= min_frames:
            k += 1
            continue
        nxt = plans[k + 1] if k + 1 < len(plans) else None
        prev = plans[k - 1] if k > 0 else None
        if nxt is not None and (nxt.cap is None or nxt.end - cur.frm <= nxt.cap):
            nxt.frm = cur.frm
            plans.pop(k)
            k = max(0, k - 1)
        elif prev is not None and (prev.cap is None or cur.end - prev.frm <= prev.cap):
            prev.end = cur.end
            plans.pop(k)
            k = max(0, k - 1)
        else:
            k += 1
    return plans


@dataclass
class ShotsResult:
    """Subplanos resolvidos: `shots` (contrato do renderer), `warnings`, e por plano os retângulos da imagem
    (`rects`, para os eventos que apontam um trecho) e o limite de duração do clipe em quadros (`caps`, None: sem)."""

    shots: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rects: list[list[list[float]]] = field(default_factory=list)
    caps: list[int | None] = field(default_factory=list)


def scene_rects(scene: Scene, doc: ProjectDoc | None) -> list[list[float]]:
    media = doc.shown_media(scene) if doc is not None else scene.visual.media
    return [list(r) for r in media.rects] if media is not None else []


def resolve_shots(
    scene: Scene,
    words: list[dict[str, Any]],
    fps: int,
    length: int,
    *,
    doc: ProjectDoc | None = None,
    pool: Pool | None = None,
    images: dict[str, str | None] | None = None,
    camera: dict[str, float] | None = None,
    real_camera: dict[str, float] | None = None,
    grammar: EditGrammar | None = None,
    seed: int = 0,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
    scene_is_video: bool = False,
    files: dict[str, str | None] | None = None,
    official_max_clip_s: float | None = None,
) -> ShotsResult:
    """Subplanos em quadros: cortes nos gatilhos, cada plano até o próximo (o último até o fim da cena). Antes do
    primeiro corte, o visual da própria cena; dois cortes no mesmo quadro: vale o último da lista.

    - Corte depois do fim da cena sai, com aviso; plano mais curto que `MIN_SHOT_S` se funde no vizinho.
    - Clipe com duração útil conhecida (`dur_s`, na velocidade do plano) não passa dela: sem `hold`, a cena volta ao
      próprio visual quando o trecho acaba. Material oficial fica em até `official_max_clip_s` (limite de direitos).
    - Câmera: a escolhida no subplano; vídeo `none`; captura `static`; o plano da própria cena herda o `scene.motion`
      escolhido; o resto pela cota da gramática (`allocate_motions`), com as câmeras calmas (`real_camera`) quando o
      material do plano é real e as do estilo (`camera`) quando não é. `scene_is_video`: o visual da cena é vídeo."""
    result = ShotsResult()
    if not scene.shots or length <= 0:
        return result
    warnings = result.warnings
    own = _Material({"prov": prov_of_scene(scene, doc)}, scene_rects(scene, doc), is_self=True)
    starts: list[tuple[int, int, SubShot]] = []
    for i, shot in enumerate(scene.shots):
        frm = trigger_frame(shot.at, words, fps, alt_words=alt_words, script=script, length=length).frame
        if frm is None:
            warnings.append(f"o corte \"{shot.at}\" não está na fala da cena; ficou no início")
            frm = 0
        if frm >= length:
            warnings.append(_past_end(shot.at, "o corte"))
            continue
        starts.append((max(0, frm), i, shot))
    if not starts:
        return result
    starts.sort(key=lambda t: (t[0], t[1]))
    by_frame: dict[int, tuple[int, SubShot]] = {}
    for frm, i, shot in starts:
        by_frame[frm] = (i, shot)  # o último da lista no mesmo quadro vence
    ordered: list[tuple[int, int, SubShot | None]] = [(frm, i, shot) for frm, (i, shot) in sorted(by_frame.items())]
    if ordered[0][0] > 0:
        ordered.insert(0, (0, -1, None))
    plans: list[_Plan] = []
    for n, (frm, i, shot) in enumerate(ordered):
        end = ordered[n + 1][0] if n + 1 < len(ordered) else length
        if shot is None:
            mat = own
        else:
            mat, warning = _shot_material(shot, scene, doc, pool, images or {}, own, files)
            if warning:
                warnings.append(warning)
        plans.append(_Plan(frm, end, (i,), shot, mat))

    # Clipe que acaba antes do plano: congela (`hold`) ou volta ao visual da cena; oficial: limite de direitos.
    capped: list[_Plan] = []
    for plan in plans:
        capped.append(plan)
        if "video" not in plan.mat.out:
            continue
        speed = plan.shot.speed if plan.shot is not None else 1.0
        cap = math.floor(plan.mat.useful_s / speed * fps) if plan.mat.useful_s is not None else None
        rights = None
        if plan.mat.out["prov"] == "official" and official_max_clip_s:
            rights = math.floor(official_max_clip_s * fps)
        limit = min(c for c in (cap, rights) if c is not None) if cap is not None or rights is not None else None
        if limit is None:
            continue
        limit = max(1, limit)
        plan.cap = limit
        if plan.dur <= limit:
            continue
        by_rights = rights is not None and (cap is None or rights <= cap)
        if plan.mat.out.get("hold") and not by_rights:
            plan.cap = None  # congela no último quadro útil: o plano pode ir até o fim
            continue
        if by_rights:
            warnings.append(f"trecho oficial cortado em {_seconds(int(official_max_clip_s * 1000))} "
                            "(limite de direitos do estilo)")
        back = _Plan(plan.frm + limit, plan.end, ("back", *plan.key), None, own)
        plan.end = plan.frm + limit
        capped.append(back)
    plans = _merge_short(capped, min_shot_frames(fps))

    # Câmera de cada plano.
    style_camera = {k: w for k, w in (camera or {"static": 1.0, "push": 1.0}).items() if k != PARALLAX}
    calm = {k: w for k, w in (real_camera or style_camera).items() if k != PARALLAX}
    motions: list[str | None] = []
    pending: list[tuple[tuple[Any, ...], dict[str, float]]] = []
    for plan in plans:
        motion = plan.shot.motion if plan.shot is not None else None
        if motion is None:
            if "video" in plan.mat.out or (plan.mat.is_self and scene_is_video):
                motion = "none"
            elif plan.mat.out["prov"] == "capture":
                motion = "static"
            elif plan.mat.is_self and scene.motion:
                motion = PARALLAX_FALLBACK if scene.motion == PARALLAX else scene.motion
        if motion is None:
            pool_ = calm if plan.mat.out["prov"] in REAL_TIERS else style_camera
            pending.append(((scene.id, "shot", *plan.key), pool_ or {"push": 1.0}))
        motions.append(motion)
    if grammar is not None:
        picked = iter(allocate_motions(pending, grammar, seed))
    else:
        picked = iter(pick_weighted(cam, seed_from(seed, *key)) for key, cam in pending)
    for plan, motion in zip(plans, motions, strict=True):
        motion = motion or next(picked)
        entry: dict[str, Any] = {"from": plan.frm, "dur": plan.dur, **plan.mat.out}
        framing = plan.shot.framing.model_dump() if plan.shot is not None and plan.shot.framing is not None \
            else dict(DEFAULT_FRAMING)
        entry["framing"] = {k: round(float(v), 4) for k, v in framing.items()}
        entry["motion"] = motion
        easing = _easing(motion, seed_from(seed, scene.id, "easing", *plan.key))
        if easing:
            entry["easing"] = easing
        if plan.shot is not None and plan.shot.speed != 1.0:
            entry["speed"] = round(float(plan.shot.speed), 3)
        result.shots.append(entry)
        result.rects.append(plan.mat.rects)
        result.caps.append(plan.cap)
    return result


# --- eventos ------------------------------------------------------------------------------------------------------


def _sound_of(event: FxEvent) -> tuple[str, str | None, float | None, str | None] | None:
    """O som do evento: (tipo procedural, id da biblioteca, ganho, aviso). Evento `sfx` sempre soa (`params.kind`,
    padrão impacto); os outros só com `params.sfx` (tipo) ou `params.sfx_id` (som da biblioteca). Nenhum evento ganha
    som sozinho: a cota de SFX por minuto é do planejador.

    O tipo só vale entre os procedurais (`audio.SFX_KINDS`): ele vira nome de arquivo no build, e o texto vem do
    LLM. Tipo que não existe vira impacto; com `lib_id` é só a reserva (o som é o da biblioteca), sem aviso."""
    params = event.params
    kind = params.get("kind") if event.type == "sfx" else params.get("sfx")
    lib_id = params.get("lib_id") if event.type == "sfx" else params.get("sfx_id")
    kind = kind.strip() if isinstance(kind, str) and kind.strip() else None
    lib_id = lib_id.strip() if isinstance(lib_id, str) and lib_id.strip() else None
    if event.type != "sfx" and kind is None and lib_id is None:
        return None
    gain = params.get("gain_db")
    gain_db = float(gain) if isinstance(gain, (int, float)) and not isinstance(gain, bool) else None
    warning = None
    if kind is not None and kind not in SFX_KINDS:
        if lib_id is None:
            shown = kind if len(kind) <= 24 else kind[:24] + "…"
            warning = f"o som '{shown}' não existe; usei o impacto"
        kind = None
    return kind or SFX_FALLBACK, lib_id, gain_db, warning


def _rect_at(frm: int, shots: ShotsResult | None, fallback: list[list[float]]) -> list[float] | None:
    """O retângulo do material que está na tela no quadro `frm`: o do plano em que ele cai (com subplanos) ou o da
    mídia da cena."""
    rects = fallback
    if shots is not None and shots.shots:
        for shot, shot_rects in zip(shots.shots, shots.rects, strict=True):
            if shot["from"] <= frm < shot["from"] + shot["dur"]:
                rects = shot_rects
                break
    return list(rects[0]) if rects else None


def resolve_fx(
    scene: Scene,
    words: list[dict[str, Any]],
    fps: int,
    length: int,
    *,
    f0: int = 0,
    doc: ProjectDoc | None = None,
    grammar: EditGrammar | None = None,
    seed: int = 0,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
    sfx_lead: dict[str, float] | None = None,
    sfx_lead_default: float = 0.12,
    shots: ShotsResult | None = None,
    auto_sfx: bool = True,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Eventos da cena em quadros, mais os SFX de evento e as quedas da trilha (segundos no vídeo). Devolve
    (fx, sfx, music_drops, avisos). `f0`: quadro do início da cena no vídeo; `shots`: os subplanos já resolvidos (o
    retângulo do marca-texto vem do plano na tela); `auto_sfx`: False quando o estilo desligou os efeitos
    automáticos (só soa o que o usuário pôs ou travou).

    - Gatilho depois do fim da cena: o evento sai, com aviso.
    - `music_drop`: o gatilho é o FIM da queda (a batida do reveal): a trilha some `dur` antes e volta na palavra.
    - `text_pop` sem `params.text` usa a palavra dita no gatilho; sem ela (gatilho "@ms", ou palavra só achada pela
      posição no vídeo traduzido), sai com aviso.
    - `highlight`/`zoom_rect` sem retângulo (nem no evento, nem no material na tela) saem com aviso."""
    fx: list[dict[str, Any]] = []
    sfx: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not scene.fx or length <= 0:
        return fx, sfx, drops, warnings
    fallback_rects = scene_rects(scene, doc)
    for i, event in enumerate(scene.fx):
        name = fx_name(event.type)
        trig = trigger_frame(event.at, words, fps, alt_words=alt_words, script=script, length=length)
        frm = trig.frame
        if frm is None:
            warnings.append(f"o gatilho \"{event.at}\" do efeito \"{name}\" não está na fala da cena; ficou no início")
            frm = 0
        if frm >= length:
            warnings.append(_past_end(event.at, f"o efeito \"{name}\""))
            continue
        frm = max(0, frm)
        at_s = (f0 + frm) / fps
        ms = event.dur_ms if event.dur_ms is not None else FX_DEFAULT_MS.get(event.type, 800)
        if event.type == "music_drop":
            start = max(0.0, at_s - ms / 1000)
            if at_s - start > 0:
                drops.append({"at": round(start, 3), "dur": round(at_s - start, 3)})
            continue
        params: dict[str, Any] = dict(event.params)
        if event.type == "punch" and "scale" not in params and grammar is not None:
            rng = random.Random(seed_from(seed, scene.id, "punch", i))
            params["scale"] = round(rng.uniform(grammar.punch_scale.min, grammar.punch_scale.max), 3)
        if event.type == "text_pop" and not params.get("text"):
            if not trig.text:
                why = "não foi traduzido" if trig.by_position else "não tem texto"
                warnings.append(f"o \"{name}\" em \"{event.at}\" {why}; ficou de fora")
                continue  # nem o som dele: som sem o que se vê fica solto
            params["text"] = trig.text
        if event.type in RECT_FX and "rect" not in params:
            rect = _rect_at(frm, shots, fallback_rects)
            if rect is None:
                warnings.append(f"o \"{name}\" em \"{event.at}\" não tem trecho marcado na imagem; ficou de fora")
                continue
            params["rect"] = rect
        sound = _sound_of(event) if auto_sfx or event.origin == "user" or event.locked else None
        if sound is not None:
            kind, lib_id, gain_db, warning = sound
            if warning:
                warnings.append(warning)
            lead = (sfx_lead or {}).get(kind, sfx_lead_default)
            entry: dict[str, Any] = {"type": kind, "start": round(max(0.0, at_s - lead), 3), "scene_id": scene.id,
                                     "at": round(at_s, 3), "scene": scene.id}
            if lib_id:
                entry["lib_id"] = lib_id
            if gain_db is not None:
                entry["gain_db"] = round(gain_db, 2)
            sfx.append(entry)
        if event.type in AUDIO_ONLY_FX:
            continue
        dur = max(1, min(frames_of(ms, fps), length - frm))
        fx.append({"type": event.type, "from": frm, "dur": dur, "params": params})
    fx.sort(key=lambda e: e["from"])
    sfx.sort(key=lambda e: e["start"])
    return fx, sfx, drops, warnings


def resolve(
    scene: Scene,
    words: list[dict[str, Any]],
    fps: int,
    *,
    length: int,
    f0: int = 0,
    doc: ProjectDoc | None = None,
    pool: Pool | None = None,
    images: dict[str, str | None] | None = None,
    camera: dict[str, float] | None = None,
    real_camera: dict[str, float] | None = None,
    grammar: EditGrammar | None = None,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
    sfx_lead: dict[str, float] | None = None,
    scene_is_video: bool = False,
    files: dict[str, str | None] | None = None,
    official_max_clip_s: float | None = None,
    auto_sfx: bool = True,
) -> dict[str, Any]:
    """A edição da cena resolvida: {shots, fx, sfx, music_drops, warnings, caps}. `words`: palavras da cena em
    quadros relativos a ela ({text, from, to}); `length`: duração da cena em quadros; `f0`: quadro em que ela começa.
    `caps`: o limite de cada plano em quadros (clipe com fim conhecido; None: sem), para o acerto final
    (`fit_to_length`). Os subplanos saem antes dos eventos: o marca-texto aponta o material do plano na tela."""
    seed = doc.seed if doc is not None else 0
    shots = resolve_shots(scene, words, fps, length, doc=doc, pool=pool, images=images, camera=camera,
                          real_camera=real_camera, grammar=grammar, seed=seed, alt_words=alt_words, script=script,
                          scene_is_video=scene_is_video, files=files, official_max_clip_s=official_max_clip_s)
    fx, sfx, drops, fx_warnings = resolve_fx(scene, words, fps, length, f0=f0, doc=doc, grammar=grammar, seed=seed,
                                             alt_words=alt_words, script=script, sfx_lead=sfx_lead, shots=shots,
                                             auto_sfx=auto_sfx)
    return {"shots": shots.shots, "fx": fx, "sfx": sfx, "music_drops": drops, "warnings": shots.warnings + fx_warnings,
            "caps": shots.caps}


CAPS_KEY = "_shot_caps"
"""Chave interna da cena resolvida com os limites dos planos (`resolve`); `fit_to_length` a tira."""


def fit_to_length(resolved: dict[str, Any], fps: int | None = None) -> None:
    """Depois do acerto final das durações (a timeline arredonda e encosta cada cena na próxima): o último plano vai
    até o fim da cena, e plano ou evento que passaria do fim encolhe (o que começaria depois some). Plano que ficou
    curto demais se funde no vizinho; clipe que passaria do trecho útil congela no último quadro (`hold`)."""
    length = int(resolved["durationInFrames"])
    caps = resolved.pop(CAPS_KEY, None)
    shots = resolved.get("shots")
    if shots:
        caps = list(caps) if caps and len(caps) == len(shots) else [None] * len(shots)
        plans = [_Plan(s["from"], 0, (k,), None, _Material(s), cap)
                 for k, (s, cap) in enumerate(zip(shots, caps, strict=True)) if s["from"] < length]
        for cur, nxt in zip(plans, plans[1:], strict=False):
            cur.end = nxt.frm
        if plans:
            plans[-1].end = length
        if fps:
            plans = _merge_short(plans, min_shot_frames(fps))
        kept = []
        for plan in plans:
            shot = plan.mat.out
            shot["from"], shot["dur"] = plan.frm, plan.dur
            if plan.cap is not None and plan.dur > plan.cap and "video" in shot:
                shot["hold"] = True
            kept.append(shot)
        if kept:
            resolved["shots"] = kept
        else:
            resolved.pop("shots")
    fx = resolved.get("fx")
    if fx:
        kept_fx = [e for e in fx if e["from"] < length]
        for e in kept_fx:
            e["dur"] = max(1, min(e["dur"], length - e["from"]))
        if kept_fx:
            resolved["fx"] = kept_fx
        else:
            resolved.pop("fx")


# --- legenda ------------------------------------------------------------------------------------------------------


def emphasize_captions(
    lines: list[dict[str, Any]],
    emphasis: list[str],
    mode: str,
    fx: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
) -> list[str]:
    """Marca `emph` nas palavras da legenda que são as de `mark.emphasis` (a primeira ocorrência de cada uma; a
    expressão de várias palavras marca todas). `mode` "emoji" leva o emoji do `text_pop` daquela palavra (sem ele,
    vira "pop"). Muda `lines` no lugar.

    `script`: as palavras da cena no idioma principal, no vídeo de outro idioma (a ênfase é escrita no do roteiro):
    a expressão é achada no roteiro e marcada na posição proporcional da legenda traduzida (o número, igual nas duas
    línguas, é procurado direto). Devolve as expressões que não acharam lugar."""
    lost: list[str] = []
    if mode == "none" or not emphasis:
        return lost
    flat = [w for line in lines for w in line.get("words", [])]
    keys = [word_key(w["text"]) for w in flat]
    script_keys = [word_key(t) for t in script] if script else None
    emojis = {e["from"]: str(e["params"]["emoji"]) for e in (fx or [])
              if e["type"] == "text_pop" and isinstance(e["params"].get("emoji"), str) and e["params"]["emoji"]}
    for phrase in emphasis:
        tokens = [word_key(t) for t in phrase.split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            continue
        span = len(tokens)
        if script_keys is None or any(ch.isdigit() for t in tokens for ch in t):
            hit = _find_run(keys, tokens)
        else:
            j = _find_run(script_keys, tokens)
            hit = None
            if j is not None and flat:
                hit = min(len(flat) - 1, int(j / len(script_keys) * len(flat)))
                span = max(1, min(span, len(flat) - hit))
        if hit is None and script_keys is not None and not any(ch.isdigit() for t in tokens for ch in t):
            lost.append(phrase)
            continue
        if hit is None:
            continue
        for k in range(hit, hit + span):
            word = flat[k]
            if mode == "emoji":
                emoji = emojis.get(word["from"])
                if emoji:
                    word["emph"], word["emoji"] = "emoji", emoji
                else:
                    word["emph"] = "pop"
            else:
                word["emph"] = mode
    return lost


def _find_run(keys: list[str], tokens: list[str]) -> int | None:
    n = len(tokens)
    for exact in (True, False):
        for i in range(len(keys) - n + 1):
            window = keys[i:i + n]
            if exact and window == tokens:
                return i
            if not exact and all(len(t) >= 4 and k.startswith(t) for k, t in zip(window, tokens, strict=True)):
                return i
    return None


def drop_stacked_sfx(
    cut: list[dict[str, Any]],
    events: list[dict[str, Any]],
    scene: Scene,
    sfx_lead: dict[str, float] | None = None,
    sfx_lead_default: float = 0.12,
) -> list[dict[str, Any]]:
    """SFX automáticos do corte cujo ponto forte cairia a menos de `SFX_STACK_S` do de um SFX de evento saem: dois sons
    empilhados viram ruído. O som escolhido na cena (`scene.sfx`) fica."""
    if not events:
        return cut
    hits = [e["at"] for e in events]
    leads = sfx_lead or {}

    def keep(c: dict[str, Any]) -> bool:
        if scene.sfx and c["type"] == scene.sfx:
            return True
        hit = c["start"] + leads.get(c["type"], sfx_lead_default)
        return all(abs(hit - h) >= SFX_STACK_S for h in hits)

    return [c for c in cut if keep(c)]


# --- material de terceiros ----------------------------------------------------------------------------------------


def uses_pool(doc: ProjectDoc) -> bool:
    """Algum subplano vem do acervo (`pool.json` precisa ser lido)?"""
    return any(sh.source == "pool" for s in doc.scenes for sh in s.shots)


def shown_materials(doc: ProjectDoc, pool: Pool | None = None) -> list[MediaRef | PoolItem]:
    """Todo material real que aparece no vídeo, na ordem das cenas: a mídia da cena (a dela ou a reaproveitada) e a
    dos subplanos (`MediaRef` do subplano e item do acervo). Créditos e aviso editorial leem daqui, para o B-roll de
    terceiros não sair sem atribuição. Pode repetir: quem lista tira as repetições."""
    out: list[MediaRef | PoolItem] = []
    for scene in doc.ordered_scenes():
        media = doc.shown_media(scene)
        if media is not None:
            out.append(media)
        for shot in scene.shots:
            if shot.source == "media" and shot.media is not None and (shot.media.asset or shot.media.video):
                out.append(shot.media)
            elif shot.source == "pool" and pool is not None:
                item = pool.item(shot.pool_id)
                if item is not None:
                    out.append(item)
    return out


def shot_sources(doc: ProjectDoc, pool: Pool | None = None) -> list[tuple[str, MediaRef | PoolItem]]:
    """Arquivos que os subplanos de mídia e de acervo mostram, com o material dono (para o build recortar e gradar a
    imagem como faz com a da cena). Sem repetir, na ordem das cenas."""
    seen: set[str] = set()
    out: list[tuple[str, MediaRef | PoolItem]] = []

    def add(path: str | None, owner: MediaRef | PoolItem) -> None:
        if path and path not in seen:
            seen.add(path)
            out.append((path, owner))

    for scene in doc.ordered_scenes():
        for shot in scene.shots:
            if shot.source == "media" and shot.media is not None:
                add(shot.media.asset, shot.media)
                add(shot.media.video, shot.media)
            elif shot.source == "pool" and pool is not None:
                item = pool.item(shot.pool_id)
                if item is not None:
                    add(item.path, item)
                    add(item.poster, item)
    return out
