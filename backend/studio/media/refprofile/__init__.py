"""Análise profunda da referência (ADR 0004 §3.2): do vídeo para um `EditProfile` medido, com confiança por campo.

Etapas (cada uma em cache em `cache/refprofile/<chave do arquivo>/`):
1. **base** (modo rápido): uma passada do ffmpeg para cortes e movimento (`decode`), cortes com dois limiares e
   micro-planos (`cuts`), movimento dentro do plano (`motion`), ataques, SFX, trilha e drops (`audio_events`) e os
   hashes para os recorrentes;
2. **fala** (modo profundo): transcrição local com a língua detectada, ritmo e pessoa (`speech-<língua>.json`) e a
   tarefa `reference_humor` (`humor-<língua>.json`, cache à parte: a falha do LLM não fica gravada e é tentada de novo
   na próxima vez, sem transcrever outra vez);
3. **visão** (modo profundo): tiras de 3 quadros por plano classificadas em lotes (`vision`).

Custo: o modo rápido decodifica o vídeo uma vez (cortes e movimento juntos) e o áudio uma vez. O cancelamento do job é
conferido entre as etapas, durante a leitura do vídeo e entre os lotes da visão (`Cancelled`): cancelado, nada é
gravado.

`measure_video(path)` roda só a base e devolve a `EditFingerprint` do vídeo: o mesmo extrator mede o render final
(`metricas-cara-ia`), para comparar de igual para igual.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from studio.config import get_settings
from studio.core.editing.profile import EditFingerprint, EditProfile
from studio.media import reference_video as rv
from studio.media.refprofile import aggregate, audio_events, cuts, decode, motion, recurrence, speech, vision
from studio.media.refprofile.aggregate import merge_profiles
from studio.media.refprofile.decode import Cancel, Cancelled, check

__all__ = ["Cancelled", "analyze", "cache_dir_for", "file_key", "measure_video", "merge_profiles"]

VERSION = 2
PUNCH_DEDUPE_S = 0.3

Progress = Callable[[float, str], None]


def cache_dir_for(path: Path) -> Path:
    """Pasta de cache da análise deste arquivo (caminho, tamanho, data e versão do extrator)."""
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}|v{VERSION}".encode()).hexdigest()[:20]
    return get_settings().cache_dir / "refprofile" / key


def file_key(path: Path) -> str:
    """A chave do arquivo (nome da pasta de cache): muda quando o arquivo muda (baixado de novo) ou o extrator muda.
    Vai para as notas do perfil, para saber se o perfil gravado é deste arquivo."""
    return cache_dir_for(path).name


def _read(file: Path) -> dict[str, Any] | None:
    try:
        return json.loads(file.read_text(encoding="utf-8")) if file.is_file() else None
    except (OSError, ValueError):
        return None


def _write(file: Path, data: dict[str, Any]) -> None:
    """Grava num temporário de nome único na mesma pasta e troca: dois jobs na mesma referência não se atropelam."""
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=file.stem + "-", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False)
        os.replace(tmp, file)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


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


def measure_base(path: Path, progress: Progress | None = None, cancelled: Cancel | None = None) -> dict[str, Any]:
    """Cortes, movimento e áudio, sem cache (ver `base`)."""
    say = progress or (lambda f, m: None)
    say(0.0, "Lendo o vídeo")
    info = rv.probe(path)
    offset = decode.audio_offset(path)
    say(0.05, "Medindo os cortes e o movimento")
    scan, frames, times = decode.scan_and_gray(path, info["w"], info["h"], float(info["dur"]) or None,
                                               cancelled=cancelled)
    cands = cuts.merge_bursts(cuts.candidates(scan.times, scan.mafd))
    check(cancelled)
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
    check(cancelled)
    say(0.85, "Ouvindo os efeitos sonoros")
    audio = audio_events.analyze(audio_events.load(path), cut_times, offset=offset)
    check(cancelled)
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
        "static_s": round(mot.static_s, 3), "analyzed_s": round(mot.analyzed_s, 3),
        "image_s": round(mot.image_s, 3), "kb_s": round(mot.kb_s, 3),
        "audio_offset": offset, "audio": audio.to_dict(),
    }


def base(path: Path, progress: Progress | None = None, *, use_cache: bool = True,
         cancelled: Cancel | None = None) -> dict[str, Any]:
    """A etapa base, do cache quando o arquivo não mudou."""
    file = cache_dir_for(path) / "base.json"
    if use_cache and (cached := _read(file)) and cached.get("version") == VERSION:
        return cached
    data = measure_base(path, progress, cancelled)
    _write(file, data)
    return data


def measure_video(path: Path, quick: bool = True) -> EditFingerprint:
    """Impressão digital de um vídeo (MP4 final ou referência) pelo extrator do perfil. Só o modo rápido existe para a
    impressão digital: materiais saem da timeline, não da visão."""
    return aggregate.fingerprint(base(Path(path)))


# --------------------------------------------------------------------------------------------
# Etapas profundas
# --------------------------------------------------------------------------------------------


def _unpack_transcript(result: Any, lang: str | None) -> tuple[list[tuple[str, float, float]], str]:
    """O transcritor devolve as palavras ou (palavras, língua)."""
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], str):
        words, used = result
    else:
        words, used = result, lang or ""
    return [tuple(w) for w in words], used  # type: ignore[misc]


def speech_stage(path: Path, dur: float, *, lang: str | None = None,
                 transcriber: Callable[[Path, str | None], Any] | None = None,
                 humor_llm: Callable[..., dict[str, Any]] | None = None, use_cache: bool = True,
                 cancelled: Cancel | None = None) -> dict[str, Any]:
    """Transcrição, medidas da fala e humor (pulado sem faster-whisper ou offline). `lang` None: língua detectada.

    A transcrição (`speech-<língua>.json`) e o humor (`humor-<língua>.json`) têm caches separados: se o LLM falhar
    (limite do plano), a falha não fica gravada e a próxima análise tenta só o humor de novo."""
    folder = cache_dir_for(path)
    tag = lang or "auto"
    file = folder / f"speech-{tag}.json"
    cached = _read(file) if use_cache else None
    if cached and cached.get("words"):
        data: dict[str, Any] = {k: cached[k] for k in ("words", "measure", "lang") if k in cached}
        words = [tuple(w) for w in data["words"]]
    else:
        if transcriber is None:
            ok, reason = speech.available()
            if not ok:
                return {"skipped": reason}
        check(cancelled)
        try:
            words, used = _unpack_transcript((transcriber or speech.transcribe)(path, lang), lang)
        except Exception as exc:  # noqa: BLE001 - a fala é opcional: o resto do perfil segue
            return {"skipped": f"transcrição falhou: {exc}"}
        data = {"words": [list(w) for w in words], "measure": speech.measure(words, dur),  # type: ignore[arg-type]
                "lang": used}
        if words:
            _write(file, data)
    if not words:
        return data
    hfile = folder / f"humor-{tag}.json"
    hcached = _read(hfile) if use_cache else None
    if hcached and isinstance(hcached.get("humor"), dict):
        data["humor"] = hcached["humor"]
        return data
    check(cancelled)
    try:
        data["humor"] = speech.humor(words, dur, humor_llm)  # type: ignore[arg-type]
    except Exception as exc:  # noqa: BLE001 - LLM indisponível: fica só a medida (e tenta de novo na próxima)
        data["humor_error"] = str(exc)[:300]
    else:
        _write(hfile, {"humor": data["humor"]})
    return data


def vision_stage(path: Path, base_data: dict[str, Any], *, provider: Any | None = None,
                 progress: Progress | None = None, cancelled: Cancel | None = None) -> dict[str, Any]:
    """Classificação dos planos pela visão (tiras em cache, respostas em cache pelo sha da tira)."""
    from studio.providers.vision import MockVision, VisionUnavailable

    say = progress or (lambda f, m: None)
    shots = vision.pick_shots(base_data["shots"])
    if not shots:
        return {"skipped": "nenhum plano para classificar"}
    strips = vision.build_strips(path, shots, base_data["info"], cache_dir_for(path) / "strips",
                                 _scaled(say, 0.0, 0.3), cancelled=cancelled)
    try:
        answers, used = vision.classify(strips, provider, _scaled(say, 0.3, 1.0), cancelled=cancelled)
    except VisionUnavailable as exc:
        return {"skipped": f"sem visão: {exc}"}
    mock = used == MockVision.id  # só se alguma resposta veio do mock (as reais em cache valem também offline)
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


def analyze(path: Path, *, quick: bool = True, progress: Progress | None = None, lang: str | None = None,
            vision_provider: Any | None = None, transcriber: Callable[[Path, str | None], Any] | None = None,
            humor_llm: Callable[..., dict[str, Any]] | None = None, thumbs_dir: Path | None = None,
            thumb_prefix: str = "ref", use_cache: bool = True,
            cancelled: Cancel | None = None) -> tuple[EditProfile, dict[str, Any]]:
    """O perfil do vídeo e um resumo das etapas (`{"skipped": {...}, "vision_provider": ..., "file_key": ...,
    "speech_lang": ...}`).

    Modo rápido: cortes, movimento e áudio. Modo profundo: mais fala (faster-whisper + `reference_humor`) e visão.
    `lang` None: a língua da narração é detectada. `cancelled`: conferido entre as etapas (levanta `Cancelled`)."""
    path = Path(path)
    say = progress or (lambda f, m: None)
    check(cancelled)
    base_data = base(path, _scaled(say, 0.0, 0.55 if not quick else 0.9), use_cache=use_cache, cancelled=cancelled)
    dur = float(base_data["info"]["dur"])
    notes: dict[str, Any] = {"skipped": {}, "file_key": file_key(path)}
    speech_data: dict[str, Any] | None = None
    vision_data: dict[str, Any] | None = None
    audio_data: dict[str, Any] | None = None
    if not quick:
        say(0.55, "Transcrevendo a narração")
        speech_data = speech_stage(path, dur, lang=lang, transcriber=transcriber, humor_llm=humor_llm,
                                   use_cache=use_cache, cancelled=cancelled)
        if speech_data.get("skipped"):
            notes["skipped"]["speech"] = speech_data["skipped"]
        elif speech_data.get("words"):
            if speech_data.get("lang"):
                notes["speech_lang"] = speech_data["lang"]
            words = [(float(w[1]), float(w[2])) for w in speech_data["words"]]
            check(cancelled)
            audio_data = audio_events.analyze(audio_events.load(path), base_data["cuts"], words,
                                              offset=float(base_data.get("audio_offset") or 0.0)).to_dict()
        if speech_data.get("humor_error"):
            notes["skipped"]["humor"] = speech_data["humor_error"]
        check(cancelled)
        say(0.7, "Olhando os planos")
        vision_data = vision_stage(path, base_data, provider=vision_provider, progress=_scaled(say, 0.7, 0.95),
                                   cancelled=cancelled)
        if vision_data.get("skipped"):
            notes["skipped"]["vision"] = vision_data["skipped"]
        else:
            notes["vision_provider"] = vision_data.get("provider")
    else:
        notes["skipped"] = {"speech": "modo rápido", "vision": "modo rápido"}
    check(cancelled)
    say(0.95, "Procurando elementos recorrentes")
    rec = _recurring(path, base_data, vision_data, thumbs_dir, thumb_prefix)
    profile = aggregate.build(base_data, quick=quick, speech=speech_data, vision=vision_data, recurring=rec,
                              audio=audio_data)
    say(1.0, "Perfil pronto")
    return profile, notes
