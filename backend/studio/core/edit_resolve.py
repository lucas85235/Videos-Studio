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
from typing import TYPE_CHECKING, Any

from studio.core.hashing import seed_from
from studio.core.project.presenter import parse_at, trigger_index, word_key
from studio.core.sources import prov_of_media, prov_of_scene
from studio.core.styles.library import pick_weighted

if TYPE_CHECKING:
    from studio.core.project.document import FxEvent, ProjectDoc, Scene, SubShot
    from studio.core.project.pool import Pool
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
# Eventos só de áudio: viram SFX ou queda da trilha, nada para o renderer desenhar.
AUDIO_ONLY_FX = frozenset({"sfx", "music_drop"})
SHOT_EASINGS = ("linear", "outCubic", "inOutQuad")
# Planos parados por natureza: o vídeo já se mexe, e a captura com anotação não pode sair do lugar.
VIDEO_EXTS = (".mp4", ".webm", ".mov", ".m4v")
# Quanto antes do corte (s) um SFX automático de corte some quando um SFX de evento cai em cima dele.
SFX_STACK_S = 0.25
DEFAULT_FRAMING: dict[str, float] = {"scale": 1.0, "x": 0.5, "y": 0.5}


def edit_active(doc: ProjectDoc) -> bool:
    """O projeto tem a camada de edição (alguma cena com subplanos, eventos ou marcas da batida)? Sem ela, a timeline
    sai exatamente como antes (mesmo hash de bloco)."""
    return any(s.shots or s.fx or s.mark is not None for s in doc.scenes)


def frames_of(ms: float, fps: int) -> int:
    return int(math.floor(ms / 1000 * fps + 0.5))


# --- câmera -------------------------------------------------------------------------------------------------------


def grammar_motion(camera: dict[str, float], grammar: EditGrammar, seed: int) -> str:
    """Câmera sorteada respeitando a gramática: `static_share_min` das vezes parada, até `slow_zoom_share` das vezes
    um zoom lento (entre os pesos do estilo), e o resto entre os outros movimentos do estilo (sem eles, `push`)."""
    rng = random.Random(seed)
    roll = rng.random()
    if roll < grammar.static_share_min:
        return "static"
    slow = {k: w for k, w in camera.items() if k in SLOW_ZOOMS and w > 0}
    if slow and roll < grammar.static_share_min + grammar.slow_zoom_share:
        return pick_weighted(slow, rng.randrange(2**31))
    rest = {k: w for k, w in camera.items() if k not in SLOW_ZOOMS and k != "static" and w > 0}
    return pick_weighted(rest, rng.randrange(2**31)) if rest else "push"


def _easing(motion: str, seed: int) -> str | None:
    if motion in ("static", "none"):
        return None
    return SHOT_EASINGS[random.Random(seed).randrange(len(SHOT_EASINGS))]


# --- gatilhos -----------------------------------------------------------------------------------------------------


def trigger_frame(
    at: str | None,
    words: list[dict[str, Any]],
    fps: int,
    *,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
    length: int | None = None,
) -> tuple[int | None, int | None]:
    """Quadro (relativo à cena) do gatilho e o índice da palavra em `words` (None no "@ms"). `words`: as palavras da
    cena em quadros ({text, from}); `alt_words`: as mesmas com os números fundidos (legenda com algarismos, "2013" de
    "dois mil e treze"); `script`: as palavras da cena no idioma principal, para achar a posição proporcional quando
    o idioma do vídeo é outro (o gatilho é escrito na língua do roteiro). (None, None): gatilho que não bate."""
    _word, _nth, ms = parse_at(at)
    if ms is not None:
        return frames_of(ms, fps), None
    if not (at or "").strip():
        return 0, None
    idx = trigger_index([w["text"] for w in words], at)
    if idx is not None:
        return int(words[idx]["from"]), idx
    if alt_words:
        j = trigger_index([w["text"] for w in alt_words], at)
        if j is not None:
            return int(alt_words[j]["from"]), None
    if script:
        j = trigger_index(script, at)
        if j is not None:
            if words:
                k = min(len(words) - 1, int(j / len(script) * len(words)))
                return int(words[k]["from"]), k
            if length:
                return int(j / len(script) * length), None
    return None, None


# --- planos -------------------------------------------------------------------------------------------------------


def _shot_material(
    shot: SubShot,
    scene: Scene,
    doc: ProjectDoc | None,
    pool: Pool | None,
    images: dict[str, str | None],
    scene_prov: str,
) -> tuple[dict[str, Any], str | None]:
    """Origem do plano: ({prov, src?, video?, clipIn?, seconds?, hold?}, aviso). Origem que falta cai no visual da cena."""
    own: dict[str, Any] = {"prov": scene_prov}
    if shot.source == "media":
        media = shot.media
        if media is None or not (media.asset or media.video):
            return own, "subplano de mídia sem arquivo; ficou o visual da cena"
        out: dict[str, Any] = {"prov": prov_of_media(media)}
        if media.asset:
            out["src"] = media.asset
        if media.video:
            out["video"] = media.video
            clip_in = shot.clip_in if shot.clip_in is not None else media.start_s
            if clip_in:
                out["clipIn"] = round(float(clip_in), 3)
            if media.dur_s:
                out["seconds"] = round(float(media.dur_s), 3)
        if shot.hold or media.hold:
            out["hold"] = True
        return out, None
    if shot.source == "pool":
        item = pool.item(shot.pool_id) if pool is not None else None
        if item is None:
            return own, f"o item {shot.pool_id or '?'} do acervo não existe; ficou o visual da cena"
        path = item.path or item.poster
        if not path:
            return own, f"o item {item.id} do acervo ainda não foi baixado; ficou o visual da cena"
        out = {"prov": item.tier}
        is_video = bool(item.path) and (item.media == "clip" or item.path.lower().endswith(VIDEO_EXTS))
        if is_video:
            out["video"] = item.path
            if item.poster:
                out["src"] = item.poster
            clip_in = shot.clip_in if shot.clip_in is not None else item.start_s
            if clip_in:
                out["clipIn"] = round(float(clip_in), 3)
            if item.dur_s:
                out["seconds"] = round(float(item.dur_s), 3)
        else:
            out["src"] = path
        if shot.hold:
            out["hold"] = True
        return out, None
    if shot.source == "reuse":
        target = next((s for s in doc.scenes if s.id == shot.reuse_of), None) if doc is not None else None
        src = images.get(shot.reuse_of or "")
        if target is None or not src:
            return own, f"a cena {shot.reuse_of or '?'} reaproveitada no subplano não tem imagem; ficou o visual da cena"
        return {"prov": prov_of_scene(target, doc), "src": src}, None
    if shot.source == "ai":
        if scene_prov == "ai":
            return {"prov": "ai"}, None
        if scene.visual.asset and scene.visual.type in ("ai_image", "ai_video"):
            return {"prov": "ai", "src": scene.visual.asset}, None
        return own, "a imagem de IA da cena não está disponível no subplano; ficou o visual da cena"
    return own, None


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
    grammar: EditGrammar | None = None,
    seed: int = 0,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Subplanos em quadros: cortes nos gatilhos, cada plano até o próximo (o último até o fim da cena). Antes do
    primeiro corte, o visual da própria cena; dois cortes no mesmo quadro: vale o último da lista."""
    if not scene.shots or length <= 0:
        return [], []
    warnings: list[str] = []
    scene_prov = prov_of_scene(scene, doc)
    starts: list[tuple[int, int, SubShot]] = []
    for i, shot in enumerate(scene.shots):
        frm, _idx = trigger_frame(shot.at, words, fps, alt_words=alt_words, script=script, length=length)
        if frm is None:
            warnings.append(f"o corte \"{shot.at}\" não está na fala da cena; ficou no início")
            frm = 0
        starts.append((min(max(0, frm), length - 1), i, shot))
    starts.sort(key=lambda t: (t[0], t[1]))
    by_frame: dict[int, tuple[int, SubShot]] = {}
    for frm, i, shot in starts:
        by_frame[frm] = (i, shot)  # o último da lista no mesmo quadro vence
    ordered = sorted(by_frame.items())
    if ordered[0][0] > 0:
        ordered.insert(0, (0, (-1, None)))  # type: ignore[arg-type]
    pool_camera = {k: w for k, w in (camera or {"static": 1.0, "push": 1.0}).items() if k != "parallax"}
    out: list[dict[str, Any]] = []
    for n, (frm, (i, shot)) in enumerate(ordered):
        end = ordered[n + 1][0] if n + 1 < len(ordered) else length
        entry: dict[str, Any] = {"from": frm, "dur": end - frm}
        if shot is None:
            material: dict[str, Any] = {"prov": scene_prov}
            framing = dict(DEFAULT_FRAMING)
            motion = None
        else:
            material, warning = _shot_material(shot, scene, doc, pool, images or {}, scene_prov)
            if warning:
                warnings.append(warning)
            framing = shot.framing.model_dump() if shot.framing is not None else dict(DEFAULT_FRAMING)
            motion = shot.motion
        entry.update(material)
        entry["framing"] = {k: round(float(v), 4) for k, v in framing.items()}
        if motion is None:
            if "video" in material:
                motion = "none"
            elif material["prov"] == "capture":
                motion = "static"
            else:
                shot_seed = seed_from(seed, scene.id, "shot", i)
                motion = grammar_motion(pool_camera, grammar, shot_seed) if grammar is not None else \
                    pick_weighted(pool_camera or {"push": 1.0}, shot_seed)
        entry["motion"] = motion
        easing = _easing(motion, seed_from(seed, scene.id, "easing", i))
        if easing:
            entry["easing"] = easing
        if shot is not None and shot.speed != 1.0:
            entry["speed"] = round(float(shot.speed), 3)
        out.append(entry)
    return out, warnings


# --- eventos ------------------------------------------------------------------------------------------------------


def _sound_of(event: FxEvent) -> tuple[str, str | None, float | None] | None:
    """O som do evento: (tipo procedural, id da biblioteca, ganho). Evento `sfx` sempre soa (`params.kind`, padrão
    impacto); os outros só com `params.sfx` (tipo) ou `params.sfx_id` (som da biblioteca). Nenhum evento ganha som
    sozinho: a cota de SFX por minuto é do planejador."""
    params = event.params
    kind = params.get("kind") if event.type == "sfx" else params.get("sfx")
    lib_id = params.get("lib_id") if event.type == "sfx" else params.get("sfx_id")
    kind = kind if isinstance(kind, str) and kind.strip() else None
    lib_id = lib_id if isinstance(lib_id, str) and lib_id.strip() else None
    if event.type != "sfx" and kind is None and lib_id is None:
        return None
    gain = params.get("gain_db")
    gain_db = float(gain) if isinstance(gain, (int, float)) and not isinstance(gain, bool) else None
    return (kind or "impact").strip(), lib_id, gain_db


def _clean_word(text: str) -> str:
    return text.strip().strip(".,;:!?…\"'“”«»()[]")


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
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Eventos da cena em quadros, mais os SFX de evento e as quedas da trilha (segundos no vídeo). Devolve
    (fx, sfx, music_drops, avisos). `f0`: quadro do início da cena no vídeo."""
    fx: list[dict[str, Any]] = []
    sfx: list[dict[str, Any]] = []
    drops: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not scene.fx or length <= 0:
        return fx, sfx, drops, warnings
    media = doc.shown_media(scene) if doc is not None else scene.visual.media
    for i, event in enumerate(scene.fx):
        frm, idx = trigger_frame(event.at, words, fps, alt_words=alt_words, script=script, length=length)
        if frm is None:
            warnings.append(f"o gatilho \"{event.at}\" do evento {event.type} não está na fala da cena; ficou no início")
            frm = 0
        frm = min(max(0, frm), length - 1)
        at_s = (f0 + frm) / fps
        ms = event.dur_ms if event.dur_ms is not None else FX_DEFAULT_MS.get(event.type, 800)
        if event.type == "music_drop":
            drops.append({"at": round(at_s, 3), "dur": round(ms / 1000, 3)})
            continue
        sound = _sound_of(event)
        if sound is not None:
            kind, lib_id, gain_db = sound
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
        params: dict[str, Any] = dict(event.params)
        if event.type == "punch" and "scale" not in params and grammar is not None:
            rng = random.Random(seed_from(seed, scene.id, "punch", i))
            params["scale"] = round(rng.uniform(grammar.punch_scale.min, grammar.punch_scale.max), 3)
        if event.type == "text_pop" and not params.get("text") and idx is not None:
            text = _clean_word(str(words[idx]["text"]))
            if text:
                params["text"] = text
        if event.type in ("zoom_rect", "highlight") and "rect" not in params and media is not None and media.rects:
            params["rect"] = list(media.rects[0])
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
    grammar: EditGrammar | None = None,
    alt_words: list[dict[str, Any]] | None = None,
    script: list[str] | None = None,
    sfx_lead: dict[str, float] | None = None,
) -> dict[str, Any]:
    """A edição da cena resolvida: {shots, fx, sfx, music_drops, warnings}. `words`: palavras da cena em quadros
    relativos a ela ({text, from, to}); `length`: duração da cena em quadros; `f0`: quadro em que ela começa."""
    seed = doc.seed if doc is not None else 0
    shots, shot_warnings = resolve_shots(scene, words, fps, length, doc=doc, pool=pool, images=images, camera=camera,
                                         grammar=grammar, seed=seed, alt_words=alt_words, script=script)
    fx, sfx, drops, fx_warnings = resolve_fx(scene, words, fps, length, f0=f0, doc=doc, grammar=grammar, seed=seed,
                                             alt_words=alt_words, script=script, sfx_lead=sfx_lead)
    return {"shots": shots, "fx": fx, "sfx": sfx, "music_drops": drops, "warnings": shot_warnings + fx_warnings}


def fit_to_length(resolved: dict[str, Any]) -> None:
    """Depois do acerto final das durações (a timeline arredonda e encosta cada cena na próxima): o último plano vai
    até o fim da cena, e plano ou evento que passaria do fim encolhe (o que começaria depois some)."""
    length = int(resolved["durationInFrames"])
    shots = resolved.get("shots")
    if shots:
        kept = [s for s in shots if s["from"] < length]
        for cur, nxt in zip(kept, kept[1:], strict=False):
            cur["dur"] = nxt["from"] - cur["from"]
        kept[-1]["dur"] = length - kept[-1]["from"]
        resolved["shots"] = kept
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
    lines: list[dict[str, Any]], emphasis: list[str], mode: str, fx: list[dict[str, Any]] | None = None
) -> None:
    """Marca `emph` nas palavras da legenda que são as de `mark.emphasis` (a primeira ocorrência de cada uma; a
    expressão de várias palavras marca todas). `mode` "emoji" leva o emoji do `text_pop` daquela palavra (sem ele,
    vira "pop"). Muda `lines` no lugar."""
    if mode == "none" or not emphasis:
        return
    flat = [w for line in lines for w in line.get("words", [])]
    keys = [word_key(w["text"]) for w in flat]
    emojis = {e["from"]: str(e["params"]["emoji"]) for e in (fx or [])
              if e["type"] == "text_pop" and isinstance(e["params"].get("emoji"), str) and e["params"]["emoji"]}
    for phrase in emphasis:
        tokens = [word_key(t) for t in phrase.split()]
        tokens = [t for t in tokens if t]
        if not tokens:
            continue
        hit = _find_run(keys, tokens)
        if hit is None:
            continue
        for k in range(hit, hit + len(tokens)):
            word = flat[k]
            if mode == "emoji":
                emoji = emojis.get(word["from"])
                if emoji:
                    word["emph"], word["emoji"] = "emoji", emoji
                else:
                    word["emph"] = "pop"
            else:
                word["emph"] = mode


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
