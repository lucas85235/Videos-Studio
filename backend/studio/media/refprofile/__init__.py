"""Análise profunda da referência (ADR 0004 §3.2): do vídeo para um `EditProfile` medido, com confiança por campo.

Etapas (cada uma em cache em `cache/refprofile/<chave do arquivo>/`):
1. **base** (modo rápido): cortes com dois limiares e micro-planos (`cuts`), movimento dentro do plano (`motion`),
   ataques, SFX, trilha e drops (`audio_events`) e os hashes para os recorrentes;
2. **fala** (modo profundo): transcrição local, ritmo, pessoa e a tarefa `reference_humor` (`speech`);
3. **visão** (modo profundo): tiras de 3 quadros por plano classificadas em lotes (`vision`).

`measure_video(path)` roda só a base e devolve a `EditFingerprint` do vídeo: o mesmo extrator mede o render final
(`metricas-cara-ia`), para comparar de igual para igual.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from studio.config import get_settings
from studio.core.editing.profile import EditFingerprint, EditProfile
from studio.media import reference_video as rv
from studio.media.refprofile import aggregate, audio_events, cuts, motion, recurrence, speech, vision
from studio.media.refprofile.aggregate import merge_profiles

__all__ = ["analyze", "measure_video", "merge_profiles", "cache_dir_for"]

VERSION = 1
PUNCH_DEDUPE_S = 0.3

Progress = Callable[[float, str], None]


def cache_dir_for(path: Path) -> Path:
    """Pasta de cache da análise deste arquivo (caminho, tamanho, data e versão do extrator)."""
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|v{VERSION}".encode()).hexdigest()[:20]
    return get_settings().cache_dir / "refprofile" / key


def _read(file: Path) -> dict[str, Any] | None:
    try:
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
    except (OSError, ValueError):
        return None


def _write(file: Path, data: dict[str, Any]) -> None:
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(file)


def _scaled(say: Progress, lo: float, hi: float) -> Progress:
    return lambda f, m: say(lo + (hi - lo) * max(0.0, min(1.0, f)), m)


# --------------------------------------------------------------------------------------------
# Etapa base (modo rápido)
# --------------------------------------------------------------------------------------------


def _dedupe_punches(punches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in sorted(punches, key=lambda p: p["t"]):
        if out and p["t"] - out[-1]["t"] < PUNCH_DEDUPE_S:
            continue
        out.append(p)
    return out


def measure_base(path: Path, progress: Progress | None = None) -> dict[str, Any]:
    """Cortes, movimento e áudio, sem cache (ver `base`)."""
    say = progress or (lambda f, m: None)
    say(0.0, "Lendo o vídeo")
    info = rv.probe(path)
    say(0.05, "Medindo os cortes")
    scan = cuts.scan(path)
    cands = cuts.merge_bursts(cuts.candidates(scan.times, scan.mafd))
    say(0.35, "Lendo os quadros para o movimento")
    frames, times = motion.decode_gray(path, info["w"], info["h"])
    say(0.5, "Conferindo os cortes")
    cut_times, cut_punches = motion.verify_cuts(cands, frames, times)
    dur = float(info["dur"]) or (scan.times[-1] if scan.times else 0.0)
    shots = cuts.shots_from_cuts(cut_times, dur)
    say(0.55, "Medindo zoom, punch e sobreposições")
    mot = motion.analyze(frames, times, shots)
    for shot in shots:
        mid = (float(shot["a"]) + float(shot["b"])) / 2
        i = int(np.clip(round(mid * motion.FPS), 0, len(frames) - 1)) if len(frames) else -1
        shot["hash"] = recurrence.crop_hash(frames[i]) if i >= 0 and not shot["micro"] else None
    overlays = []
    for ov in mot.overlays:
        overlays.append({**ov, "hash": recurrence.crop_hash(frames[ov["frame"]], ov["box"])})
    say(0.85, "Ouvindo os efeitos sonoros")
    audio = audio_events.analyze(audio_events.load(path), cut_times)
    say(1.0, "Medidas prontas")
    punches = _dedupe_punches([*cut_punches, *mot.punches])
    # imagem parada depois de um punch ou de um zoom é o fim do movimento, não o efeito "freeze"
    moves = [float(p["t"]) for p in punches] + [float(r["end"]) for r in mot.ramps]
    freezes = [list(f) for f in cuts.freezes_inside_shots(scan.freezes, shots)
               if not any(0 <= f[0] - m <= 0.5 for m in moves)]
    return {
        "version": VERSION, "info": info, "shots": shots, "cuts": cut_times,
        "punches": punches, "ramps": mot.ramps, "pans": mot.pans,
        "shakes": mot.shakes, "overlays": overlays, "freezes": freezes,
        "static_s": round(mot.static_s, 3), "analyzed_s": round(mot.analyzed_s, 3), "audio": audio.to_dict(),
    }


def base(path: Path, progress: Progress | None = None, *, use_cache: bool = True) -> dict[str, Any]:
    """A etapa base, do cache quando o arquivo não mudou."""
    file = cache_dir_for(path) / "base.json"
    if use_cache and (cached := _read(file)) and cached.get("version") == VERSION:
        return cached
    data = measure_base(path, progress)
    _write(file, data)
    return data


def measure_video(path: Path, quick: bool = True) -> EditFingerprint:
    """Impressão digital de um vídeo (MP4 final ou referência) pelo extrator do perfil. Só o modo rápido existe para a
    impressão digital: materiais saem da timeline, não da visão."""
    return aggregate.fingerprint(base(Path(path)))


# --------------------------------------------------------------------------------------------
# Etapas profundas
# --------------------------------------------------------------------------------------------


def speech_stage(path: Path, dur: float, *, lang: str = "pt", transcriber: Callable[[Path, str], list] | None = None,
                 humor_llm: Callable[..., dict[str, Any]] | None = None, use_cache: bool = True) -> dict[str, Any]:
    """Transcrição, medidas da fala e humor (pulado sem faster-whisper ou offline)."""
    file = cache_dir_for(path) / f"speech-{lang}.json"
    if use_cache and (cached := _read(file)) and cached.get("words"):
        return cached
    if transcriber is None:
        ok, reason = speech.available()
        if not ok:
            return {"skipped": reason}
    try:
        words = [tuple(w) for w in (transcriber or speech.transcribe)(path, lang)]
    except Exception as exc:  # noqa: BLE001 - a fala é opcional: o resto do perfil segue
        return {"skipped": f"transcrição falhou: {exc}"}
    data: dict[str, Any] = {"words": [list(w) for w in words], "measure": speech.measure(words, dur)}  # type: ignore[arg-type]
    try:
        data["humor"] = speech.humor(words, dur, humor_llm)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - LLM indisponível: fica só a medida
        data["humor_error"] = str(exc)[:300]
    if words:
        _write(file, data)
    return data


def vision_stage(path: Path, base_data: dict[str, Any], *, provider: Any | None = None,
                 progress: Progress | None = None) -> dict[str, Any]:
    """Classificação dos planos pela visão (tiras em cache, respostas em cache pelo sha da tira)."""
    from studio.providers.vision import MockVision, VisionUnavailable

    say = progress or (lambda f, m: None)
    shots = vision.pick_shots(base_data["shots"])
    if not shots:
        return {"skipped": "nenhum plano para classificar"}
    strips = vision.build_strips(path, shots, base_data["info"], cache_dir_for(path) / "strips",
                                 _scaled(say, 0.0, 0.3))
    try:
        answers, used = vision.classify(strips, provider, _scaled(say, 0.3, 1.0))
    except VisionUnavailable as exc:
        return {"skipped": f"sem visão: {exc}"}
    mock = isinstance(provider, MockVision) or used == MockVision.id
    return {"provider": used, "mock": mock,
            "shots": [{"k": s["k"], "a": s["a"], "dur": s["dur"], **ans} for s, ans in zip(shots, answers, strict=False)]}


def _recurring(path: Path, base_data: dict[str, Any], vision_data: dict[str, Any] | None,
               thumbs_dir: Path | None, thumb_prefix: str) -> list[dict[str, Any]]:
    items = [{"hash": s.get("hash"), "t": float(s["a"]), "source": "shot", "box": None}
             for s in base_data["shots"] if s.get("hash")]
    items += [{"hash": o.get("hash"), "t": float(o["start"]), "source": "overlay", "box": o["box"]}
              for o in base_data.get("overlays") or [] if o.get("hash")]
    vshots = (vision_data or {}).get("shots") or []

    def kind_of(members: list[dict[str, Any]]) -> str:
        votes: list[str] = []
        for m in members:
            shot = next((s for s in vshots if s["a"] <= m["t"] < s["a"] + s["dur"]), None)
            if shot is None:
                continue
            if shot.get("overlay_kind") in ("mascot", "logo", "meme"):
                votes.append(shot["overlay_kind"])
            elif shot["material"] == "mascot":
                votes.append("mascot")
            elif shot.get("is_meme") or shot["material"] == "meme_reaction":
                votes.append("meme")
        return aggregate._mode(votes) or "other"

    thumb = None
    if thumbs_dir is not None:
        info = base_data["info"]
        fw, fh = vision.frame_dims(int(info.get("w") or 0), int(info.get("h") or 0))

        def thumb(n: int, first: dict[str, Any]) -> str | None:
            try:
                image = rv.frame_at(path, float(first["t"]) + 0.1, fw, fh)
                target = recurrence.save_thumb(image, first.get("box"), thumbs_dir / f"{thumb_prefix}-rec-{n}.jpg")
                return target.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()
            except (OSError, ValueError):
                return None

    return recurrence.recurring(items, kind_of=kind_of, thumb=thumb)


# --------------------------------------------------------------------------------------------
# Tudo
# --------------------------------------------------------------------------------------------


def analyze(path: Path, *, quick: bool = True, progress: Progress | None = None, lang: str = "pt",
            vision_provider: Any | None = None, transcriber: Callable[[Path, str], list] | None = None,
            humor_llm: Callable[..., dict[str, Any]] | None = None, thumbs_dir: Path | None = None,
            thumb_prefix: str = "ref", use_cache: bool = True) -> tuple[EditProfile, dict[str, Any]]:
    """O perfil do vídeo e um resumo das etapas (`{"skipped": {...}, "vision_provider": ...}`).

    Modo rápido: cortes, movimento e áudio. Modo profundo: mais fala (faster-whisper + `reference_humor`) e visão."""
    path = Path(path)
    say = progress or (lambda f, m: None)
    base_data = base(path, _scaled(say, 0.0, 0.55 if not quick else 0.9), use_cache=use_cache)
    dur = float(base_data["info"]["dur"])
    notes: dict[str, Any] = {"skipped": {}}
    speech_data: dict[str, Any] | None = None
    vision_data: dict[str, Any] | None = None
    audio_data: dict[str, Any] | None = None
    if not quick:
        say(0.55, "Transcrevendo a narração")
        speech_data = speech_stage(path, dur, lang=lang, transcriber=transcriber, humor_llm=humor_llm,
                                   use_cache=use_cache)
        if speech_data.get("skipped"):
            notes["skipped"]["speech"] = speech_data["skipped"]
        elif speech_data.get("words"):
            words = [(float(w[1]), float(w[2])) for w in speech_data["words"]]
            audio_data = audio_events.analyze(audio_events.load(path), base_data["cuts"], words).to_dict()
        if speech_data.get("humor_error"):
            notes["skipped"]["humor"] = speech_data["humor_error"]
        say(0.7, "Olhando os planos")
        vision_data = vision_stage(path, base_data, provider=vision_provider, progress=_scaled(say, 0.7, 0.95))
        if vision_data.get("skipped"):
            notes["skipped"]["vision"] = vision_data["skipped"]
        else:
            notes["vision_provider"] = vision_data.get("provider")
    else:
        notes["skipped"] = {"speech": "modo rápido", "vision": "modo rápido"}
    say(0.95, "Procurando elementos recorrentes")
    rec = _recurring(path, base_data, vision_data, thumbs_dir, thumb_prefix)
    profile = aggregate.build(base_data, quick=quick, speech=speech_data, vision=vision_data, recurring=rec,
                              audio=audio_data)
    say(1.0, "Perfil pronto")
    return profile, notes
