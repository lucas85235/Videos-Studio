"""Jobs do bloco G (ADR 0003): `audio_check` (conferência de áudio sem renderizar) e `music_curate` (curadoria das
trilhas da biblioteca). Importado por `studio.workers.jobs`."""

from __future__ import annotations

from typing import Any

from studio.media import music_library
from studio.workers.audiocheck.report import run_audio_check
from studio.workers.jobs import JobContext, job


@job("audio_check")
def run_audio_check_job(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """`{lang?, source?: "preview" | "final"}` → grava o `QualityReport(kind="audio")`."""
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.02, "Conferindo o áudio")
    report = run_audio_check(project_id, payload.get("lang"), payload.get("source") or "preview", ctx.progress)
    bad = [it for it in report["items"] if it["level"] != "ok"]
    return {"report_id": report["id"], "lang": report["lang"], "status": report["status"], "problems": len(bad)}


@job("music_curate")
def run_music_curate(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """`{tracks?: [caminhos], force?: bool}` (vazio = todas da biblioteca). Cache em `AppSetting["music_curation"]` pela
    chave `caminho|tamanho|mtime`: faixa já curada e sem mudança no disco não é analisada de novo."""
    wanted = [str(t) for t in payload.get("tracks") or []]
    tracks = wanted or [t.path for t in music_library.list_tracks()]
    force = bool(payload.get("force"))
    done: dict[str, dict[str, Any]] = {}
    for index, rel in enumerate(tracks):
        if ctx.cancelled():
            break
        ctx.progress(index / max(1, len(tracks)), f"Curando {rel}")
        result = music_library.curate_track(rel, force=force)
        done[rel] = {k: result.get(k) for k in ("ok", "reason", "bpm", "loop_start_s", "loop_s", "loop_bars")}
    ok = sum(1 for r in done.values() if r.get("ok"))
    return {"tracks": len(done), "ok": ok, "results": done}
