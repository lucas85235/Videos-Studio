"""Rotas da conferência de áudio e da curadoria das trilhas (ADR 0003, bloco G).

- `GET /api/library/music/curation`: curadoria de cada faixa da biblioteca (trecho de loop, bpm, banda da voz).
- `POST /api/library/music/curate`: job `music_curate` (`{tracks?: [...], force?}`; vazio = todas).
- `GET /api/library/music/spectro?track=&dur=100`: espectrograma PNG da faixa (batida × nota sustentada).
- `GET /api/sfx/guard`: guarda de apito dos efeitos procedurais.
- `GET /api/projects/{id}/audio-check?lang=`: último relatório; `POST` (`{lang?, source?}`): job `audio_check`.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.core.hashing import hash_obj
from studio.core.project import service
from studio.media import music_library
from studio.workers.jobs import get_runner

router = APIRouter(prefix="/api", tags=["audio"])


class CurateBody(BaseModel):
    tracks: list[str] = Field(default_factory=list, max_length=500)
    force: bool = False


class AudioCheckBody(BaseModel):
    lang: str | None = Field(default=None, max_length=20)
    source: Literal["preview", "final"] = "preview"


@router.get("/library/music/curation")
def music_curation() -> dict[str, Any]:
    return music_library.curation_map()


@router.post("/library/music/curate")
def music_curate(body: CurateBody) -> dict[str, Any]:
    return get_runner().submit("music_curate", {"tracks": body.tracks, "force": body.force})


@router.get("/library/music/spectro")
def music_spectro(track: str, dur: float = 100.0) -> FileResponse:
    from studio.media import music_curate as curate_mod

    dur = max(5.0, min(600.0, dur))
    key = music_library.curation_key(track)
    full = music_library.resolve(track)
    if key is None or full is None:
        raise HTTPException(status_code=404, detail="Faixa não encontrada na biblioteca")
    cur = music_library.get_curation(track) or {}
    loop = (cur["loop_start_s"], cur["loop_s"]) if cur.get("ok") else None
    out = get_settings().cache_dir / "music_spectro" / f"{hash_obj([key, dur, loop])[:24]}.png"
    if not out.exists():
        try:
            curate_mod.spectro_png(full, out, dur=dur, loop=loop)
        except Exception as exc:  # noqa: BLE001 - arquivo que o FFmpeg não lê
            raise HTTPException(status_code=422, detail=f"Não consegui desenhar a faixa: {exc}") from exc
    return FileResponse(out, media_type="image/png")


@router.get("/sfx/guard")
def sfx_guard() -> dict[str, Any]:
    from studio.workers.audiocheck.report import sfx_guard as guard

    return guard()


def _project(project_id: str) -> dict[str, Any]:
    try:
        return service.get_project_row(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc


@router.get("/projects/{project_id}/audio-check")
def get_audio_check(project_id: str, lang: str | None = None) -> dict[str, Any]:
    from studio.workers.audiocheck.report import latest_report

    _project(project_id)
    return {"report": latest_report(project_id, lang)}


@router.post("/projects/{project_id}/audio-check")
def post_audio_check(project_id: str, body: AudioCheckBody) -> dict[str, Any]:
    row = _project(project_id)
    settings = row.get("settings") or {}
    if not ((settings.get(f"last_{body.source}") or {}).get("langs")):
        what = "a prévia" if body.source == "preview" else "o vídeo final"
        raise HTTPException(status_code=409, detail=f"Gere {what} antes de conferir o áudio.")
    return get_runner().submit("audio_check", body.model_dump(), project_id)
