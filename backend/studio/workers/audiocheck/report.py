"""Conferência de áudio sem renderizar o vídeo (ADR 0003, bloco G; = `mix_preview.py` + `sfx_audit.py` do canais).

Lê a timeline e o mix da última prévia (ou do vídeo final), recoloca a voz e os efeitos como no build, pega a trilha já
montada (`Builder.music_track`), passa a trilha pelo mesmo grafo do mix (passa-altas, ganho e ducking pela voz) e mede:

- por capítulo, a voz sob a fala, a trilha sob a fala e nas pausas e a distância (regra 12: de 14 a 19 dB);
- os efeitos mais altos comparados com a voz e o tom puro de cada um;
- a guarda de apito de cada tipo de efeito usado (regra 13).

Grava um `QualityReport(kind="audio")`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import numpy as np

from studio.config import get_settings
from studio.core.graph.store import ArtifactStore
from studio.core.project import service
from studio.db import session_scope
from studio.ids import new_id
from studio.media import audio as audio_mod
from studio.media import audio_check, sfx_library
from studio.media.ffmpeg import read_wav
from studio.models import QualityReport

SOURCES = ("preview", "final")
TOP_SFX = 15


class AudioCheckError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------------
# Guarda de apito dos efeitos procedurais
# --------------------------------------------------------------------------------------------


def sfx_path(store: ArtifactStore, kind: str) -> Path:
    """Arquivo do efeito procedural no cache (a mesma chave do `Builder.sfx_file`)."""
    return sfx_library.procedural_path(kind, store)


def sfx_guard_item(store: ArtifactStore, kind: str, path: Path | None = None,
                   banned: bool | None = None) -> dict[str, Any]:
    """Relatório de apito de um tipo de efeito (ou de um efeito da biblioteca, com `path` e `banned`): medida do
    `tone_report`, fração tonal e se é banido."""
    sound, _rate = read_wav(path or sfx_path(store, kind))
    report = audio_check.tone_report(sound)
    frac, hz = audio_check.tonal_fraction(sound)
    banned = kind in audio_check.SFX_BANNED if banned is None else banned
    return {"kind": kind, **report, "tonal_fraction": round(frac, 2), "tonal_hz": int(round(hz)), "banned": banned,
            "blocked": bool(banned or report["apito"])}


def sfx_guard(kinds: list[str] | None = None) -> dict[str, Any]:
    """Guarda de apito dos efeitos procedurais (todos, ou os `kinds`)."""
    store = ArtifactStore()
    items = [sfx_guard_item(store, k) for k in (kinds or list(audio_mod.SFX_KINDS))]
    return {"items": items, "ok": not any(i["blocked"] for i in items)}


# --------------------------------------------------------------------------------------------
# Conferência
# --------------------------------------------------------------------------------------------


def _entry(settings: dict[str, Any], source: str, lang: str | None) -> tuple[str, dict[str, Any]]:
    langs = ((settings.get(f"last_{source}") or {}).get("langs")) or {}
    if not langs:
        what = "a prévia" if source == "preview" else "o vídeo final"
        raise AudioCheckError(f"Gere {what} antes de conferir o áudio.")
    if lang is None:
        lang = next(iter(langs))
    entry = langs.get(lang)
    if not entry or not entry.get("timeline") or not entry.get("mix"):
        raise AudioCheckError(f"Não há {'prévia' if source == 'preview' else 'vídeo final'} em {lang}.")
    return lang, entry


def stale_sfx_item(store: ArtifactStore, mix_rel: str, kinds: set[str], source: str) -> dict[str, Any] | None:
    """Item `erro` quando o mix foi montado com um efeito de antes da `SFX_VERSION` atual (glitch e riser antigos
    tinham apito). A conferência mede os efeitos atuais; o mix guardado pode ter os de antes. A chave do mix guardada no
    caminho (`cache/mix/xx/<hash>/mix.m4a`) acha o registro, que diz com que versão ele foi feito (`sfx_v`)."""
    retuned = sorted(kinds & audio_mod.SFX_RETUNED)
    if not retuned:
        return None
    saved = store.lookup("mix", Path(mix_rel).parent.name) or {}
    if int(saved.get("sfx_v") or 0) >= audio_mod.SFX_RETUNED_VERSION:
        return None
    what = "a prévia" if source == "preview" else "o vídeo final"
    names = ", ".join(f"'{k}'" for k in retuned)
    return audio_check.item("sfx_stale", "erro", "efeito com apito: versão antiga do efeito",
                            f"{what.capitalize()} foi montad{'a' if source == 'preview' else 'o'} com a versão antiga "
                            f"de {names}, que tinha apito. Gere {what} de novo e confira outra vez.")


def _scene_at(timeline: dict[str, Any], t: float) -> str | None:
    fps = float(timeline.get("fps") or 30)
    current = None
    for scene in timeline.get("scenes", []):
        if scene.get("from", 0) / fps <= t:
            current = scene.get("id")
        else:
            break
    return current


def _kind_at(events: list[dict[str, Any]], t: float) -> str | None:
    """Tipo do efeito que tocava em `t` (o último que começou até ali)."""
    kind = None
    for e in events:
        if e["start"] <= t + 0.05:
            kind = e["type"]
    return kind


def measure(project_id: str, lang: str | None = None, source: str = "preview",
            progress: Any = None) -> dict[str, Any]:
    """Mede o áudio da prévia/vídeo final sem renderizar. Devolve `lang`, `version`, `source` (mix), `items` e `data`."""
    # import tardio: o build importa muita coisa
    from studio.workers.pipeline.build import Builder, music_ref_s

    if source not in SOURCES:
        raise AudioCheckError(f"origem inválida: {source}")
    tick = progress or (lambda _f, _m="": None)
    row = service.get_project_row(project_id)
    lang, entry = _entry(row.get("settings") or {}, source, lang)
    settings = get_settings()
    timeline = json.loads((settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    total = float(timeline["duration"])
    version = timeline.get("version") or row["current_version"]
    builder = Builder(project_id, mode="preview", langs=[lang], version=version)
    style = builder.style
    work = settings.tmp_dir / f"audiocheck-{new_id('tmp', 8)}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        tick(0.1, "Recolocando a voz")
        voice_path = audio_mod.place_clips(
            [(builder.store.abs(n["path"]), n["start"], 1.0) for n in timeline["narration"]], total, work / "voice.wav")
        # efeitos: guarda de apito por tipo ou id da biblioteca (como no mix) e a trilha dos que ficaram. Evento do
        # ADR 0004 (`at`, `lib_id`) vira início e arquivo como no `Builder.sfx_plan`.
        tonal_ok = builder.sfx_tonal_ok
        events_all = []
        for e in timeline.get("sfx", []):
            ref, explicit = sfx_library.event_ref(e)
            events_all.append({**e, "ref": ref, "explicit": explicit, "gain_db": float(e.get("gain_db") or 0.0),
                               "start": sfx_library.event_start(e, tonal_ok=tonal_ok)})
        files = []
        blocked: set[tuple[str, bool]] = set()
        for ref, explicit in sorted({(e["ref"], e["explicit"]) for e in events_all}):
            item = sfx_library.lookup(ref, tonal_ok=tonal_ok, explicit=explicit)
            if item is None or item.procedural:
                guard = sfx_guard_item(builder.store, item.kind if item else ref)
            else:
                guard = {**sfx_guard_item(builder.store, ref, builder.sfx_source(ref, explicit), item.banned),
                         "kind": ref}
            if guard["blocked"]:
                blocked.add((ref, explicit))
            if guard not in files:
                files.append(guard)
        events_tl = [e for e in events_all if (e["ref"], e["explicit"]) not in blocked]
        sfx_gain_db = style.audio.sfx.volume_db
        tick(0.25, "Montando a trilha")
        found = builder.music_track(timeline)
        short = style.format.type == "short"
        music = None
        if found is not None:
            music_path, info = found
            stem, voice_gain = audio_mod.music_stem(
                voice_path, music_path, work / "music.wav", music_db=info["volume_db"],
                ducking=style.audio.music.ducking, total=total, short=short,
                music_highpass=audio_mod.music_highpass(info.get("mood")), music_ref_s=music_ref_s(info),
                music_drops=timeline.get("music_drops"))
            music, _ = read_wav(stem)
        else:
            # sem trilha o mix também leva a voz a VOICE_LUFS (a referência dos efeitos): sem isso o `vs_voz` saía
            # deslocado pelo ganho que faltou
            voice_gain = audio_mod.voice_gain_db(voice_path)
        tick(0.6, "Medindo voz, trilha e efeitos")
        voice, _ = read_wav(voice_path)
        voice = voice * np.float32(10 ** (voice_gain / 20))
        n = voice.size
        mask = audio_check.speech_mask(timeline, n)
        data: dict[str, Any] = {"chapters": [], "episode": None, "sfx_events": [], "sfx_files": files,
                                "music": None, "voice_gain_db": round(voice_gain, 2)}
        items: list[dict[str, Any]] = []
        if music is not None:
            music = np.pad(music, (0, max(0, n - music.size)))[:n]
            levels = audio_check.chapter_levels(voice, music, mask, timeline)
            data["chapters"], data["episode"] = levels["chapters"], levels["episode"]
            for row_level in levels["chapters"]:
                it = audio_check.distance_item(row_level)
                if it:
                    items.append(it)
            data["music"] = {k: found[1].get(k) for k in ("source", "track", "mood", "volume_db", "beds")} if found else None
        else:
            items.append(audio_check.item("music_none", "ok", "vídeo sem trilha", "Nada a conferir na música."))
        events: list[dict[str, Any]] = []
        if events_tl:
            sfx_path_all = audio_mod.place_clips([(builder.sfx_source(e["ref"], e["explicit"]), e["start"],
                                                   10 ** ((sfx_gain_db + e["gain_db"]) / 20)) for e in events_tl],
                                                 total, work / "sfx.wav")
            sfx, _ = read_wav(sfx_path_all)
            events = audio_check.sfx_events(sfx, voice)
            for e in events:
                e["kind"] = _kind_at(events_tl, e["t"])
                e["scene_id"] = _scene_at(timeline, e["t"])
        data["sfx_events"] = sorted(events, key=lambda e: -(e["vs_voz"] if e["vs_voz"] is not None else -999))[:TOP_SFX]
        # Contrato com o bloco H (erro = bloqueia a publicação): `erro` só para apito que ESTÁ no áudio. O tipo com apito
        # ou banido já foi tirado do mix pela guarda (`Builder.mix`), então aqui é `aviso` ("ficou fora do mix") e a
        # checagem por arquivo do `sfx_items` não roda (lista vazia). O apito que chega ao mix é `erro`: o tom puro de
        # cada evento (`sfx_items`) e o mix montado com efeitos de antes da `SFX_VERSION` atual (`stale_sfx_item`).
        for f in files:
            if f["blocked"]:
                why = "está na lista dos banidos" if f["banned"] else f"tem apito ({f['hz']} Hz por {f['tonal_ms']} ms)"
                items.append(audio_check.item("sfx_guard", "aviso", f"efeito sonoro '{f['kind']}' ficou fora do mix",
                                              f"O efeito {why}. Padrão do estúdio: sem apito."))
        stale = stale_sfx_item(builder.store, entry["mix"], {e["type"] for e in events_tl}, source)
        if stale:
            items.append(stale)
        items += audio_check.sfx_items(events, [])
        if not items:
            items.append(audio_check.item("audio_ok", "ok", "áudio conferido", "Nada fora do padrão."))
        tick(0.95, "Gravando o relatório")
        return {"lang": lang, "version": int(version or 0), "source": entry["mix"], "items": items, "data": data,
                "status": audio_check.worst(items)}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def report_dict(row: QualityReport) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_id": row.project_id,
        "kind": row.kind,
        "lang": row.lang,
        "version": row.version,
        "source": row.source,
        "status": row.status,
        "items": row.items or [],
        "data": row.data or {},
        "contact_sheet": row.contact_sheet or "",
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def run_audio_check(project_id: str, lang: str | None = None, source: str = "preview",
                    progress: Any = None) -> dict[str, Any]:
    """Mede e grava o `QualityReport(kind="audio")`; devolve o relatório."""
    result = measure(project_id, lang, source, progress)
    with session_scope() as s:
        row = QualityReport(project_id=project_id, kind="audio", lang=result["lang"], version=result["version"],
                            source=result["source"], status=result["status"], items=result["items"],
                            data=result["data"])
        s.add(row)
        s.flush()
        return report_dict(row)


def latest_report(project_id: str, lang: str | None = None) -> dict[str, Any] | None:
    with session_scope() as s:
        query = s.query(QualityReport).filter(QualityReport.project_id == project_id, QualityReport.kind == "audio")
        if lang:
            query = query.filter(QualityReport.lang == lang)
        row = query.order_by(QualityReport.id.desc()).first()
        return report_dict(row) if row else None
