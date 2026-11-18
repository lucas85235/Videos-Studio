"""Métricas de edição e de "cara de IA" (ADR 0004 §6). Só funções puras: nada lê banco nem disco.

- `fingerprint_from_timeline(timeline, doc)`: a impressão digital EXATA do vídeo a partir da timeline resolvida, antes
  do render (os mesmos blocos e unidades do `EditProfile` que a análise da referência mede).
- `ai_look_index(fp, style)`: o índice "Cara de IA", de 0 a 100 (mais alto = mais cara de IA), com o detalhamento.
- `adherence(fp, profile)`: o quanto a edição segue o perfil medido da referência, de 0 a 100.
- `prov_strip(timeline, doc)`: a faixa da timeline colorida por nível de fonte (onde está a IA no tempo).

A timeline pode ser antiga (sem `prov`, `shots` nem `fx`): o nível de cada cena sai do documento
(`sources.prov_of_scene`) e cada cena conta como um plano só, como ela é desenhada.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel

from studio.core.editing.profile import (
    AudioBlock,
    CameraBlock,
    EditFingerprint,
    EditProfile,
    MaterialsBlock,
    OverlaysBlock,
    PaceBlock,
)
from studio.core.project.document import CARD_KINDS, ProjectDoc
from studio.core.sources import REAL_TIERS, prov_of_scene
from studio.core.styles.schema import EditGrammar, StyleSpec, grammar_for, sources_for

# --- Constantes das medidas -------------------------------------------------------------------------------------------

MICRO_S = 0.4
"""Plano mais curto que isto é micro-plano (a referência conta à parte)."""
HOOK_S = 3.0
"""Janela do gancho para o nível dominante (`hook_prov`)."""
HOOK_CUTS_S = 30.0
"""Janela dos cortes do gancho (`hook_cuts_per_min`)."""
WINDOW_S = 30.0
"""Janelas de cortes por minuto (`cuts_per_min_windows`)."""
ON_CUT_S = 0.12
"""SFX a até isto de um corte conta como "no corte"."""
KB_MOTIONS = frozenset({"ken_burns_in", "ken_burns_out", "pan_left", "pan_right", "pan_up", "pan_down", "parallax"})
"""Movimentos de slideshow: Ken Burns e pan (o parallax cai no Ken Burns sem camadas)."""
SLOW_ZOOM_MOTIONS = frozenset({"ken_burns_in", "ken_burns_out", "push", "zoom_to", "parallax"})
"""Rampas de zoom lento."""
STATIC_MOTIONS = frozenset({"static", "none"})
PUNCH_MOTIONS = frozenset({"punch_zoom"})
AUDIO_FX = frozenset({"sfx", "music_drop"})
"""Tipos de fx que não são evento visual (o som vai para `timeline.sfx` e `timeline.music_drops`)."""
ANNOT_FX = frozenset({"arrow", "circle", "highlight"})
OVERLAY_FX = frozenset({"meme", "pip", "text_pop", "counter"}) | ANNOT_FX
SFX_CLASS_OF: dict[str, str] = {
    "impact": "impact", "boom": "impact", "thump": "impact", "bass_drop": "impact",
    "whoosh": "whoosh", "swish": "whoosh", "whip": "whoosh",
    "pop": "pop", "click": "pop", "tick": "pop", "shutter": "pop",
    "riser": "riser", "riser_short": "riser",
    "scratch": "scratch",
}
MAX_APPEARANCES = 3
"""Aparições livres do mesmo asset (o original e até 2 reenquadramentos); cada uma a mais é uma repetição."""

# --- Alvos do índice e das conferências (ADR 0004 §6) ------------------------------------------------------------------

CV_MIN = 0.45
REAL_SHARE_MIN = 0.5
SHORT_SWITCH_MIN = 4.0
MAX_RUN_SHOTS = 3
MAX_RUN_S = 20.0
SFX_BAND = {"long": (0.5, 1.25), "short": (2 / 3, 5 / 3)}
"""Faixa aceitável de SFX/min em múltiplos do alvo (longo 4 → 2 a 5; Short 6 → 4 a 10)."""
INTENSITY_SCALE = {0: 0.5, 1: 0.75, 2: 1.0, 3: 1.4}
INDEX_WEIGHTS: dict[str, int] = {
    "ai_screen_share": 30,
    "shot_cv": 15,
    "kb_share": 15,
    "events_per_min": 15,
    "sfx_per_min": 10,
    "switch_per_min": 10,
    "repeat_hits": 5,
}
INDEX_LABELS: dict[str, str] = {
    "ai_screen_share": "Imagem de IA na tela",
    "shot_cv": "Variação da duração dos planos",
    "kb_share": "Ken Burns e pan",
    "events_per_min": "Eventos por minuto",
    "sfx_per_min": "Efeitos sonoros por minuto",
    "switch_per_min": "Trocas de tipo de mídia por minuto",
    "repeat_hits": "Repetições",
}
BAND_GREEN_MAX = 30
BAND_YELLOW_MAX = 55
REPEAT_FULL = 3
"""Repetições que valem o componente inteiro."""
ADHERENCE_EPS = 0.05
"""Folga no logaritmo da aderência (alvo 0 com valor 0 dá 1, sem dividir por zero)."""

Band = Literal["verde", "amarelo", "vermelho"]


# --- Planos da timeline ---------------------------------------------------------------------------------------------


@dataclass
class Shot:
    """Um plano da timeline (em segundos no vídeo)."""

    start: float
    dur: float
    prov: str
    motion: str
    scene_id: str
    kind: str
    image: bool
    """Imagem parada (conta no tempo de imagem do Ken Burns): não é cartão nem clipe."""
    key: str | None
    """Identidade do que aparece (imagem ou vídeo), para as repetições."""

    @property
    def end(self) -> float:
        return self.start + self.dur


def _fps(timeline: dict[str, Any]) -> float:
    try:
        fps = float(timeline.get("fps") or 30)
    except (TypeError, ValueError):
        fps = 30.0
    return fps if fps > 0 else 30.0


def timeline_duration(timeline: dict[str, Any]) -> float:
    fps = _fps(timeline)
    if timeline.get("durationInFrames"):
        return float(timeline["durationInFrames"]) / fps
    if timeline.get("duration"):
        return float(timeline["duration"])
    scenes = timeline.get("scenes") or []
    if scenes:
        last = scenes[-1]
        return (float(last.get("from") or 0) + float(last.get("durationInFrames") or 0)) / fps
    return 0.0


def _scene_prov(scene: dict[str, Any], provs: dict[str, str]) -> str:
    """O nível da cena: o da timeline (`prov`), o do documento ou, cena que o documento não tem mais, pelo que a
    timeline mostra."""
    if scene.get("prov"):
        return str(scene["prov"])
    sid = str(scene.get("id") or "")
    if sid in provs:
        return provs[sid]
    if scene.get("kind") in CARD_KINDS:
        return "graphic"
    if scene.get("news"):
        return "capture"
    return "ai" if scene.get("src") or scene.get("video") else "graphic"


def _doc_provs(doc: ProjectDoc | None) -> dict[str, str]:
    if doc is None:
        return {}
    out: dict[str, str] = {}
    for scene in doc.scenes:
        try:
            out[scene.id] = prov_of_scene(scene, doc)
        except Exception:  # noqa: BLE001 - cena estranha não derruba a medida: cai na regra da timeline
            continue
    return out


def timeline_shots(timeline: dict[str, Any], doc: ProjectDoc | None = None) -> list[Shot]:
    """Os planos da timeline, em ordem. Cena sem `shots` é um plano só; com `shots`, cada subplano vai de onde começa
    até o próximo (ou o fim da cena), e o trecho antes do primeiro é o visual da própria cena."""
    fps = _fps(timeline)
    provs = _doc_provs(doc)
    out: list[Shot] = []
    for scene in timeline.get("scenes") or []:
        f0 = float(scene.get("from") or 0)
        frames = float(scene.get("durationInFrames") or 0)
        if frames <= 0:
            continue
        sid = str(scene.get("id") or "")
        kind = str(scene.get("kind") or "image")
        prov = _scene_prov(scene, provs)
        is_card = kind in CARD_KINDS
        own_video = bool(scene.get("video")) or kind == "video"
        own_key = None if is_card else (scene.get("video") or scene.get("src"))
        base = dict(prov=prov, motion=str(scene.get("motion") or ""), scene_id=sid, kind=kind,
                    image=not is_card and not own_video, key=own_key)
        subs = sorted((s for s in scene.get("shots") or [] if isinstance(s, dict)), key=lambda s: float(s.get("from") or 0))
        subs = [s for s in subs if 0 <= float(s.get("from") or 0) < frames]
        if not subs:
            out.append(Shot(start=f0 / fps, dur=frames / fps, **base))  # type: ignore[arg-type]
            continue
        starts = [float(s.get("from") or 0) for s in subs]
        if starts[0] > 0:
            out.append(Shot(start=f0 / fps, dur=starts[0] / fps, **base))  # type: ignore[arg-type]
        for i, sub in enumerate(subs):
            a = starts[i]
            b = starts[i + 1] if i + 1 < len(subs) else frames
            if b <= a:
                continue
            video = sub.get("video")
            key = video or sub.get("src") or own_key
            out.append(Shot(
                start=(f0 + a) / fps, dur=(b - a) / fps, prov=str(sub.get("prov") or prov),
                motion=str(sub.get("motion") or ""), scene_id=sid, kind=kind,
                image=not is_card and not video and (bool(sub.get("src")) or not own_video), key=key,
            ))
    return out


@dataclass
class _Fx:
    type: str
    at: float
    scene_id: str
    params: dict[str, Any]


def _timeline_fx(timeline: dict[str, Any]) -> list[_Fx]:
    fps = _fps(timeline)
    out: list[_Fx] = []
    for scene in timeline.get("scenes") or []:
        f0 = float(scene.get("from") or 0)
        for fx in scene.get("fx") or []:
            if not isinstance(fx, dict) or not fx.get("type"):
                continue
            params = fx.get("params") if isinstance(fx.get("params"), dict) else {}
            out.append(_Fx(str(fx["type"]), (f0 + float(fx.get("from") or 0)) / fps, str(scene.get("id") or ""),
                           params))
    return sorted(out, key=lambda f: f.at)


@dataclass
class _Sfx:
    type: str
    at: float
    scene_id: str
    transition: bool
    on_cut: bool
    lib_id: str | None


def _timeline_sfx(timeline: dict[str, Any], cuts: list[float]) -> list[_Sfx]:
    """Os SFX da timeline com o instante do ponto forte. Formato antigo `{type, start, scene_id}` (o `start` já vem
    adiantado pelo `SFX_LEAD`) e o do contrato novo `{type, at, scene, lib_id?, gain_db?}`. É de transição o som da
    transição da cena, no corte dela."""
    from studio.core.timeline import SFX_FOR_TRANSITION, SFX_LEAD, SFX_LEAD_DEFAULT

    fps = _fps(timeline)
    scene_start: dict[str, tuple[float, str]] = {}
    for scene in timeline.get("scenes") or []:
        scene_start[str(scene.get("id") or "")] = (float(scene.get("from") or 0) / fps, str(scene.get("transition") or ""))
    out: list[_Sfx] = []
    for event in timeline.get("sfx") or []:
        if not isinstance(event, dict) or not event.get("type"):
            continue
        kind = str(event["type"])
        if event.get("at") is not None:
            at = float(event["at"])
        else:
            at = float(event.get("start") or 0) + SFX_LEAD.get(kind, SFX_LEAD_DEFAULT)
        sid = str(event.get("scene") or event.get("scene_id") or "")
        start, transition = scene_start.get(sid, (None, ""))
        is_transition = (start is not None and abs(at - start) <= ON_CUT_S and kind == SFX_FOR_TRANSITION.get(transition)
                         and start > 0)
        on_cut = any(abs(at - c) <= ON_CUT_S for c in cuts)
        lib = event.get("lib_id")
        out.append(_Sfx(kind, at, sid, is_transition, on_cut, str(lib) if lib else None))
    return sorted(out, key=lambda s: s.at)


# --- Estatística ----------------------------------------------------------------------------------------------------


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _per_min(count: float, minutes: float) -> float:
    return count / minutes if minutes > 0 else 0.0


def _r(value: float, digits: int = 3) -> float:
    return round(float(value), digits)


def _share(value: float) -> float:
    return _r(min(1.0, max(0.0, value)), 4)


# --- Impressão digital ----------------------------------------------------------------------------------------------


@dataclass
class TimelineMeasure:
    """O que a medida da timeline encontra, além da impressão (para os itens da conferência com tempo e cena)."""

    fp: EditFingerprint
    shots: list[Shot]
    cuts: list[float]
    still: tuple[float, float]
    """Maior trecho sem evento visual: (início, duração)."""
    repeats: list[tuple[float, str]]
    """Cada repetição: (instante, cena)."""
    duration: float


def measure_timeline(timeline: dict[str, Any], doc: ProjectDoc | None = None) -> TimelineMeasure:
    duration = timeline_duration(timeline)
    shots = timeline_shots(timeline, doc)
    if duration <= 0 or not shots:
        return TimelineMeasure(EditFingerprint(source="timeline"), shots, [], (0.0, 0.0), [], duration)
    minutes = duration / 60.0
    durs = [s.dur for s in shots]
    cuts = [s.start for s in shots[1:]]
    mean = statistics.fmean(durs)
    cv = statistics.pstdev(durs) / mean if mean > 0 and len(durs) > 1 else 0.0
    windows: list[float] = []
    w0 = 0.0
    while w0 < duration - 1e-6:
        w1 = min(duration, w0 + WINDOW_S)
        n = sum(1 for c in cuts if w0 <= c < w1)
        windows.append(_r(_per_min(n, (w1 - w0) / 60.0), 2))
        w0 = w1
    hook_end = min(duration, HOOK_CUTS_S)
    micro = sum(1 for d in durs if d < MICRO_S)

    fxs = _timeline_fx(timeline)
    visual_fx = [f for f in fxs if f.type not in AUDIO_FX]
    punch_shots = [s for s in shots if s.motion in PUNCH_MOTIONS]
    events = sorted([*cuts, *(f.at for f in visual_fx)])
    points = [0.0, *[e for e in events if 0 < e < duration], duration]
    still_start, still_len = 0.0, 0.0
    for a, b in zip(points, points[1:], strict=False):
        if b - a > still_len:
            still_start, still_len = a, b - a

    count = Counter(f.type for f in visual_fx)
    punches = count["punch"] + len(punch_shots)
    scales = [float(f.params["scale"]) - 1.0 for f in visual_fx
              if f.type == "punch" and isinstance(f.params.get("scale"), int | float) and float(f.params["scale"]) > 1]
    image_time = sum(s.dur for s in shots if s.image)
    kb_time = sum(s.dur for s in shots if s.image and s.motion in KB_MOTIONS)
    static_time = sum(s.dur for s in shots if s.image and s.motion in STATIC_MOTIONS)
    slow_time = sum(s.dur for s in shots if s.motion in SLOW_ZOOM_MOTIONS)

    prov_time: dict[str, float] = {}
    for s in shots:
        prov_time[s.prov] = prov_time.get(s.prov, 0.0) + s.dur
    total = sum(prov_time.values()) or 1.0
    switches = sum(1 for a, b in zip(shots, shots[1:], strict=False) if a.prov != b.prov)
    run, run_s, best, best_s = 0, 0.0, 0, 0.0
    prev: str | None = None
    for s in shots:
        if s.prov == prev:
            run, run_s = run + 1, run_s + s.dur
        else:
            run, run_s, prev = 1, s.dur, s.prov
        best, best_s = max(best, run), max(best_s, run_s)
    hook: dict[str, float] = {}
    for s in shots:
        overlap = min(s.end, HOOK_S) - max(s.start, 0.0)
        if overlap > 0:
            hook[s.prov] = hook.get(s.prov, 0.0) + overlap
    hook_prov = max(hook.items(), key=lambda kv: kv[1])[0] if hook else None

    text_time = sum(float(sc.get("durationInFrames") or 0) for sc in timeline.get("scenes") or []
                    if sc.get("text") or sc.get("kind") in CARD_KINDS) / _fps(timeline)
    memes = count["meme"] + sum(1 for s in shots if s.prov == "meme")
    overlays = sum(count[t] for t in OVERLAY_FX)

    sfx = _timeline_sfx(timeline, cuts)
    accents = [e for e in sfx if not e.transition]
    classes = Counter(SFX_CLASS_OF.get(e.type, "other") for e in accents)
    drops = timeline.get("music_drops") or []

    # Repetições: o mesmo asset em mais cenas do que o livre, e o mesmo meme da biblioteca mais de uma vez.
    repeats: list[tuple[float, str]] = []
    seen_scenes: dict[str, list[str]] = {}
    for s in shots:
        if not s.key or s.kind in CARD_KINDS:
            continue
        scenes_with = seen_scenes.setdefault(str(s.key), [])
        if s.scene_id in scenes_with:
            continue
        scenes_with.append(s.scene_id)
        if len(scenes_with) > MAX_APPEARANCES:
            repeats.append((s.start, s.scene_id))
    memes_seen: set[str] = set()
    for f in fxs:
        lib = f.params.get("lib_id")
        if f.type == "meme" and lib:
            if str(lib) in memes_seen:
                repeats.append((f.at, f.scene_id))
            memes_seen.add(str(lib))
    repeats.sort()

    fp = EditFingerprint(
        source="timeline",
        duration_s=_r(duration),
        pace=PaceBlock(
            shots=len(shots), median_s=_r(statistics.median(durs)), mean_s=_r(mean), p10_s=_r(_quantile(durs, 0.1)),
            p90_s=_r(_quantile(durs, 0.9)), cv=_r(cv), max_shot_s=_r(max(durs)),
            cuts_per_min=_r(_per_min(len(cuts), minutes), 2), cuts_per_min_windows=windows,
            hook_cuts_per_min=_r(_per_min(sum(1 for c in cuts if c < hook_end), hook_end / 60.0), 2),
            micro_count=micro, micro_per_min=_r(_per_min(micro, minutes), 2), max_still_s=_r(still_len),
            events_per_min=_r(_per_min(len(cuts) + len(visual_fx) + len(punch_shots), minutes), 2),
        ),
        camera=CameraBlock(
            punch_per_min=_r(_per_min(punches, minutes), 2),
            punch_scale=_r(statistics.fmean(scales)) if scales else 0.0,
            shake_per_min=_r(_per_min(count["shake"], minutes), 2),
            slow_zoom_share=_share(slow_time / duration),
            kb_share=_share(kb_time / image_time) if image_time > 0 else 0.0,
            static_share=_share(static_time / image_time) if image_time > 0 else 0.0,
            freeze_per_min=_r(_per_min(count["freeze"], minutes), 2),
        ),
        materials=MaterialsBlock(
            prov_mix={k: _share(v / total) for k, v in sorted(prov_time.items()) if v > 0},
            switch_per_min=_r(_per_min(switches, minutes), 2),
            max_same_prov_run=best, max_same_prov_run_s=_r(best_s),
        ),
        overlays=OverlaysBlock(
            text_pop_per_min=_r(_per_min(count["text_pop"], minutes), 2),
            meme_per_min=_r(_per_min(memes, minutes), 2),
            annot_per_min=_r(_per_min(sum(count[t] for t in ANNOT_FX), minutes), 2),
            overlay_per_min=_r(_per_min(overlays, minutes), 2),
            on_screen_text_share=_share(text_time / duration),
        ),
        audio=AudioBlock(
            sfx_per_min=_r(_per_min(len(accents), minutes), 2),
            sfx_on_cut_share=_share(sum(1 for e in accents if e.on_cut) / len(accents)) if accents else 0.0,
            sfx_classes={k: _share(v / len(accents)) for k, v in sorted(classes.items())} if accents else {},
            drops_per_min=_r(_per_min(len(drops), minutes), 2),
        ),
        hook_prov=hook_prov,
        repeat_hits=len(repeats),
    )
    return TimelineMeasure(fp, shots, cuts, (still_start, still_len), repeats, duration)


def fingerprint_from_timeline(timeline: dict[str, Any], doc: ProjectDoc) -> EditFingerprint:
    """A impressão digital da edição a partir da timeline resolvida: exata e anterior ao render."""
    return measure_timeline(timeline, doc).fp


def prov_strip(timeline: dict[str, Any], doc: ProjectDoc | None = None) -> list[dict[str, Any]]:
    """Trechos seguidos com o mesmo nível de fonte: `[{start, end, prov, scene_id}]` (segundos), para a faixa colorida.
    `scene_id` é a cena onde o trecho começa."""
    out: list[dict[str, Any]] = []
    for shot in timeline_shots(timeline, doc):
        if out and out[-1]["prov"] == shot.prov and abs(out[-1]["end"] - shot.start) < 1e-3:
            out[-1]["end"] = _r(shot.end)
        else:
            out.append({"start": _r(shot.start), "end": _r(shot.end), "prov": shot.prov, "scene_id": shot.scene_id})
    return out


# --- Métricas planas da tabela §6 ---------------------------------------------------------------------------------


def ai_screen_share(fp: EditFingerprint) -> float:
    return float(fp.materials.prov_mix.get("ai", 0.0))


def real_share(fp: EditFingerprint) -> float:
    return sum(v for k, v in fp.materials.prov_mix.items() if k in REAL_TIERS)


def flat_metrics(fp: EditFingerprint) -> dict[str, Any]:
    """As métricas da tabela do ADR 0004 §6 com os nomes de lá."""
    return {
        "ai_screen_share": _share(ai_screen_share(fp)),
        "hook_real": fp.hook_prov is not None and fp.hook_prov != "ai",
        "hook_prov": fp.hook_prov,
        "real_share": _share(real_share(fp)),
        "official_share": _share(fp.materials.prov_mix.get("official", 0.0)),
        "shot_median": fp.pace.median_s,
        "p10": fp.pace.p10_s,
        "p90": fp.pace.p90_s,
        "shot_cv": fp.pace.cv,
        "kb_share": fp.camera.kb_share,
        "events_per_min": fp.pace.events_per_min,
        "sfx_per_min": fp.audio.sfx_per_min,
        "switch_per_min": fp.materials.switch_per_min,
        "max_same_prov_run": fp.materials.max_same_prov_run,
        "max_same_prov_run_s": fp.materials.max_same_prov_run_s,
        "max_still_s": fp.pace.max_still_s,
        "repeat_hits": fp.repeat_hits,
    }


# --- Alvos ----------------------------------------------------------------------------------------------------------


class Targets(BaseModel):
    """Os alvos de um estilo (e, quando há, do perfil da referência) para o índice e a conferência."""

    short: bool = False
    ai_max_share: float = 0.20
    official_max_share: float = 0.35
    hook_tiers: list[str] = []
    real_share_min: float = REAL_SHARE_MIN
    shot_median_s: float = 2.6
    shot_median_band: tuple[float, float] = (1.9, 3.5)
    cv_min: float = CV_MIN
    kb_max: float = 0.30
    events_min: float = 0.0
    sfx_band: tuple[float, float] = (2.0, 5.0)
    switch_min: float = 2.0
    max_still_s: float = 6.0
    max_run_shots: int = MAX_RUN_SHOTS
    max_run_s: float = MAX_RUN_S
    tolerance: float = 0.35
    from_profile: list[str] = []
    """Campos cujo alvo veio do perfil medido da referência."""


def expected_events_per_min(grammar: EditGrammar, median_s: float | None = None, cv: float | None = None) -> float:
    """Eventos por minuto que a gramática pede: cortes (pela média da lognormal com a mediana e o CV) + punch, texto,
    meme, anotação e tremida, escalados pela intensidade."""
    median = median_s or grammar.shot_median_s
    var = cv if cv is not None else grammar.shot_cv
    mean = median * math.sqrt(1 + var * var)
    scale = INTENSITY_SCALE.get(grammar.intensity, 1.0)
    inserts = (grammar.punch_per_min + grammar.text_pop_per_min + grammar.meme_per_min + grammar.annot_per_min
               + grammar.shake_per_min) * scale
    return 60.0 / mean + inserts


def targets_for(style: StyleSpec, profile: EditProfile | None = None) -> Targets:
    grammar = grammar_for(style)
    sources = sources_for(style)
    short = style.format.type == "short"
    tol = grammar.tolerance
    used: list[str] = []

    def measured(field: str, value: float) -> bool:
        return profile is not None and value > 0 and (profile.confidence(field) > 0 or not profile.meta)

    median = grammar.shot_median_s
    cv = grammar.shot_cv
    if profile is not None and measured("pace.median_s", profile.pace.median_s):
        median = profile.pace.median_s
        used.append("pace.median_s")
    if profile is not None and measured("pace.cv", profile.pace.cv):
        cv = profile.pace.cv
        used.append("pace.cv")
    events = expected_events_per_min(grammar, median, cv)
    if profile is not None and measured("pace.events_per_min", profile.pace.events_per_min):
        events = profile.pace.events_per_min
        used.append("pace.events_per_min")
    sfx_target = grammar.sfx_per_min
    if profile is not None and measured("audio.sfx_per_min", profile.audio.sfx_per_min):
        sfx_target = profile.audio.sfx_per_min
        used.append("audio.sfx_per_min")
    lo, hi = SFX_BAND["short" if short else "long"]
    switch = grammar.switch_per_min
    if short and switch == EditGrammar.model_fields["switch_per_min"].default:
        switch = SHORT_SWITCH_MIN
    return Targets(
        short=short,
        ai_max_share=sources.ai_max_share,
        official_max_share=sources.official_max_share,
        hook_tiers=list(sources.hook),
        shot_median_s=_r(median),
        shot_median_band=(_r(median / (1 + tol)), _r(median * (1 + tol))),
        cv_min=min(CV_MIN, cv) if "pace.cv" in used else CV_MIN,
        kb_max=grammar.slow_zoom_share,
        events_min=_r(events, 2),
        sfx_band=(_r(sfx_target * lo, 2), _r(sfx_target * hi, 2)),
        switch_min=switch,
        max_still_s=grammar.max_still_s,
        tolerance=tol,
        from_profile=used,
    )


# --- Índice "Cara de IA" --------------------------------------------------------------------------------------------


class IndexPart(BaseModel):
    key: str
    label: str
    value: float
    target: str
    """O alvo em texto ("≤ 0.2", "≥ 0.45", "2–5")."""
    penalty: float
    """0 (no alvo) a 1 (o pior)."""
    weight: int
    points: float


class AiLookIndex(BaseModel):
    """Índice "Cara de IA" de 0 a 100 (mais alto = mais cara de IA) e o detalhamento por componente."""

    score: int
    band: Band
    parts: list[IndexPart]


def _over(value: float, cap: float) -> float:
    if value <= cap:
        return 0.0
    return min(1.0, (value - cap) / max(1e-9, 1.0 - cap))


def _under(value: float, floor: float) -> float:
    if floor <= 0 or value >= floor:
        return 0.0
    return min(1.0, (floor - value) / floor)


def _outside(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return _under(value, lo)
    if value > hi > 0:
        return min(1.0, (value - hi) / hi)
    return 0.0


def band_of(score: float) -> Band:
    if score <= BAND_GREEN_MAX:
        return "verde"
    return "amarelo" if score <= BAND_YELLOW_MAX else "vermelho"


def ai_look_index(fp: EditFingerprint, style: StyleSpec, profile: EditProfile | None = None) -> AiLookIndex:
    """O índice pelos pesos do ADR 0004 §6: cada componente vale de 0 a 1 conforme a distância até o alvo."""
    t = targets_for(style, profile)
    ai = ai_screen_share(fp)
    lo, hi = t.sfx_band
    raw: dict[str, tuple[float, str, float]] = {
        "ai_screen_share": (ai, f"≤ {t.ai_max_share:g}", _over(ai, t.ai_max_share)),
        "shot_cv": (fp.pace.cv, f"≥ {t.cv_min:g}", _under(fp.pace.cv, t.cv_min)),
        "kb_share": (fp.camera.kb_share, f"≤ {t.kb_max:g}", _over(fp.camera.kb_share, t.kb_max)),
        "events_per_min": (fp.pace.events_per_min, f"≥ {t.events_min:g}", _under(fp.pace.events_per_min, t.events_min)),
        "sfx_per_min": (fp.audio.sfx_per_min, f"{lo:g}–{hi:g}", _outside(fp.audio.sfx_per_min, lo, hi)),
        "switch_per_min": (fp.materials.switch_per_min, f"≥ {t.switch_min:g}",
                           _under(fp.materials.switch_per_min, t.switch_min)),
        "repeat_hits": (float(fp.repeat_hits), "0", min(1.0, fp.repeat_hits / REPEAT_FULL)),
    }
    parts = [
        IndexPart(key=key, label=INDEX_LABELS[key], value=_r(value), target=target, penalty=_r(penalty),
                  weight=INDEX_WEIGHTS[key], points=_r(INDEX_WEIGHTS[key] * penalty, 2))
        for key, (value, target, penalty) in raw.items()
    ]
    score = int(round(sum(p.weight * p.penalty for p in parts)))
    return AiLookIndex(score=score, band=band_of(score), parts=parts)


# --- Aderência ao perfil --------------------------------------------------------------------------------------------


class AdherencePart(BaseModel):
    field: str
    value: float
    target: float
    score: float


class Adherence(BaseModel):
    """Aderência ao perfil (0 a 100): 100 × a média de `s_i` sobre os campos que o perfil mediu."""

    score: int | None
    """None: o perfil não tem nenhum campo medido."""
    tolerance: float
    parts: list[AdherencePart]


ADHERENCE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("pace.median_s", "pace", "median_s"),
    ("pace.cv", "pace", "cv"),
    ("pace.cuts_per_min", "pace", "cuts_per_min"),
    ("pace.micro_per_min", "pace", "micro_per_min"),
    ("camera.punch_per_min", "camera", "punch_per_min"),
    ("audio.sfx_per_min", "audio", "sfx_per_min"),
    ("overlays.text_pop_per_min", "overlays", "text_pop_per_min"),
    ("overlays.meme_per_min", "overlays", "meme_per_min"),
)


def _s(value: float, target: float, tol: float) -> float:
    ratio = (value + ADHERENCE_EPS) / (target + ADHERENCE_EPS)
    return max(0.0, 1.0 - abs(math.log(ratio)) / math.log(1.0 + tol))


def _profile_prov_mix(profile: EditProfile) -> dict[str, float]:
    if profile.materials.prov_mix:
        return dict(profile.materials.prov_mix)
    from studio.core.editing.profile import prov_mix_from_materials

    return prov_mix_from_materials(profile.materials.mix)


def adherence(fp: EditFingerprint, profile: EditProfile, tolerance: float = 0.35) -> Adherence:
    """`s_i = max(0, 1 − |ln(v/alvo)| / ln(1+tol))` sobre ritmo, CV, cortes/min, micro/min, punch/min, SFX/min,
    texto/min e meme/min, e `1 − L1/(2·tol)` sobre a mistura por nível de fonte. Entra o campo que o perfil mediu
    (confiança > 0 no `meta`) ou, perfil sem `meta`, o que tem valor."""
    parts: list[AdherencePart] = []

    def counts(field: str, target: float) -> bool:
        if profile.meta:
            return profile.confidence(field) > 0
        return target > 0

    for field, block, attr in ADHERENCE_FIELDS:
        target = float(getattr(getattr(profile, block), attr))
        if not counts(field, target):
            continue
        value = float(getattr(getattr(fp, block), attr))
        parts.append(AdherencePart(field=field, value=_r(value), target=_r(target), score=_r(_s(value, target, tolerance))))
    mix = _profile_prov_mix(profile)
    if mix and (not profile.meta or profile.confidence("materials.mix") > 0 or profile.confidence("materials.prov_mix") > 0):
        mine = fp.materials.prov_mix
        l1 = sum(abs(mine.get(k, 0.0) - mix.get(k, 0.0)) for k in set(mine) | set(mix))
        parts.append(AdherencePart(field="materials.prov_mix", value=_r(l1), target=0.0,
                                   score=_r(max(0.0, 1.0 - l1 / (2.0 * tolerance)))))
    score = int(round(100 * statistics.fmean(p.score for p in parts))) if parts else None
    return Adherence(score=score, tolerance=tolerance, parts=parts)
