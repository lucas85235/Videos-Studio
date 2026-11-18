"""Biblioteca de efeitos sonoros e de memes do usuário, com licença registrada (ADR 0004 §3.8).

SFX:
- `GET /api/library/sfx`: embutidos (procedurais) e do usuário, com `auto` e o aviso de cada um (arquivos soltos na
  pasta entram como licença desconhecida);
- `POST /api/library/sfx` (multipart `file`, `kind`, `license`, `tags`, `author`, `source_url`): envia um efeito;
- `PATCH /api/library/sfx/{id}`: declara licença, tipo, tags ou autor;
- `DELETE /api/library/sfx/{id}`;
- `GET /api/library/sfx/{id}/audio`: o arquivo como entra no mix (prévia);
- `POST /api/library/sfx/fetch-freesound` (`{sound, kind, tags}`): só CC0, com a chave do usuário;
- `POST /api/library/sfx/voice` (`{voice, lang}`): "ué", "pera", "oxe", "hã?" com a voz do canal (Kokoro local);
- `POST /api/library/sfx/import-canais` (`{dry_run, replace}`): os .wav do canais (só leitura lá).

Memes:
- `GET /api/library/memes?channel_id=`: os do usuário, as poses do avatar do canal e os templates "meme sem meme";
- `POST /api/library/memes` (multipart `file`, `license` obrigatória, `tags`, `risk`, `max_ms`, `sfx_id`...);
- `DELETE /api/library/memes/{id}`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from studio.config import get_settings
from studio.media import audio as audio_mod
from studio.media import meme_library, sfx_library

router = APIRouter(prefix="/api/library", tags=["biblioteca"])

SFX_MAX_BYTES = 30_000_000


def _tags(raw: str | None) -> list[str]:
    return [t.strip() for t in (raw or "").replace(";", ",").split(",") if t.strip()]


def _save_upload(file: UploadFile, limit: int, folder: Path) -> Path:
    name = Path(file.filename or "arquivo").name
    data = file.file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(status_code=413, detail=f"arquivo grande demais (até {limit // 1_000_000} MB)")
    if not data:
        raise HTTPException(status_code=422, detail="arquivo vazio")
    path = folder / name
    path.write_bytes(data)
    return path


# --------------------------------------------------------------------------------------------
# SFX
# --------------------------------------------------------------------------------------------


@router.get("/sfx")
def list_sfx(tonal_ok: bool = True) -> dict[str, Any]:
    new = sfx_library.scan()
    items = sfx_library.all_items()
    return {
        "dir": str(sfx_library.user_root()),
        "items": [i.public(tonal_ok) for i in items],
        "warnings": sfx_library.warnings(items),
        "scanned": [i.id for i in new],
        "licenses": [{"id": k, "label": v} for k, v in sfx_library.LICENSE_LABELS.items()],
        "procedural": list(audio_mod.SFX_KINDS),
    }


@router.post("/sfx")
def upload_sfx(
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form(min_length=1, max_length=40)],
    license: Annotated[str, Form(max_length=20)] = sfx_library.UNKNOWN_LICENSE,
    tags: Annotated[str, Form(max_length=500)] = "",
    author: Annotated[str, Form(max_length=200)] = "",
    source_url: Annotated[str, Form(max_length=500)] = "",
    lib_id: Annotated[str | None, Form(max_length=64)] = None,
) -> dict[str, Any]:
    """Envia um efeito. Sem licença declarada ele entra como "desconhecida" (fora do automático, com aviso)."""
    with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
        src = _save_upload(file, SFX_MAX_BYTES, Path(tmp))
        try:
            item = sfx_library.import_file(src, kind=kind, license=license, tags=_tags(tags), author=author,
                                           source_url=source_url, lib_id=lib_id, imported_from="upload")
        except sfx_library.SfxLibraryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return item.public()


class SfxPatch(BaseModel):
    license: str | None = Field(default=None, max_length=20)
    kind: str | None = Field(default=None, min_length=1, max_length=40)
    tags: list[str] | None = Field(default=None, max_length=30)
    author: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=500)
    note: str | None = Field(default=None, max_length=500)


@router.patch("/sfx/{lib_id}")
def patch_sfx(lib_id: str, body: SfxPatch) -> dict[str, Any]:
    try:
        item = sfx_library.update(lib_id, **body.model_dump())
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="efeito não encontrado na biblioteca do usuário") from exc
    except (sfx_library.SfxLibraryError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return item.public()


@router.delete("/sfx/{lib_id}")
def delete_sfx(lib_id: str) -> dict[str, Any]:
    try:
        removed = sfx_library.delete(lib_id)
    except sfx_library.SfxLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail="efeito não encontrado")
    return {"deleted": lib_id}


@router.get("/sfx/{lib_id}/audio")
def sfx_audio(lib_id: str) -> FileResponse:
    item = sfx_library.get(lib_id)
    if item is None:
        raise HTTPException(status_code=404, detail="efeito não encontrado")
    try:
        path = sfx_library.prepared_path(item)
    except sfx_library.SfxLibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(path, media_type="audio/wav")


class FreesoundBody(BaseModel):
    sound: str = Field(min_length=1, max_length=300)
    """Id ou link do som."""
    kind: str = Field(min_length=1, max_length=40)
    tags: list[str] = Field(default_factory=list, max_length=30)


@router.post("/sfx/fetch-freesound")
def fetch_freesound(body: FreesoundBody) -> dict[str, Any]:
    if get_settings().offline:
        raise HTTPException(status_code=503, detail="modo offline: o Freesound precisa de internet")
    try:
        item = sfx_library.fetch_freesound(body.sound, kind=body.kind, tags=body.tags)
    except sfx_library.SfxLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return item.public()


class VoiceBody(BaseModel):
    voice: str | None = Field(default=None, max_length=60)
    lang: str = Field(default="pt-BR", max_length=12)


@router.post("/sfx/voice")
def voice_sfx(body: VoiceBody) -> dict[str, Any]:
    """Gera (uma vez) as interjeições com a voz local do canal; ficam em cache na biblioteca, licença própria."""
    try:
        items = sfx_library.ensure_voice_sfx(body.voice, body.lang)
    except sfx_library.SfxLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"items": [i.public() for i in items]}


class CanaisSfxBody(BaseModel):
    dry_run: bool = False
    replace: bool = False


@router.post("/sfx/import-canais")
def import_canais_sfx(body: CanaisSfxBody) -> dict[str, Any]:
    """Importa os efeitos do canais (lidos lá, copiados para a biblioteca do usuário; nada vai para o repositório)."""
    from studio.workers.canais_import import sfx as canais_sfx

    return canais_sfx.import_sfx(dry_run=body.dry_run, replace=body.replace).to_dict()


# --------------------------------------------------------------------------------------------
# Memes
# --------------------------------------------------------------------------------------------


def _presenter(channel_id: str | None) -> Any:
    if not channel_id:
        return None
    from studio.core.project.presenter import channel_presenter

    return channel_presenter(channel_id)


@router.get("/memes")
def list_memes(channel_id: str | None = None) -> dict[str, Any]:
    items = meme_library.list_items(_presenter(channel_id))
    return {
        "dir": str(meme_library.memes_root()),
        "items": [{**i.public(), "url": f"/files/{i.file}"} for i in items],
        "templates": [t.model_dump(mode="json") for t in meme_library.TEMPLATES.values()],
        "licenses": [{"id": k, "label": v} for k, v in meme_library.LICENSE_LABELS.items()],
        "risks": list(meme_library.RISKS),
    }


@router.post("/memes")
def upload_meme(
    file: Annotated[UploadFile, File()],
    license: Annotated[str, Form(min_length=1, max_length=20)],
    tags: Annotated[str, Form(max_length=500)] = "",
    risk: Annotated[str, Form(max_length=10)] = "low",
    max_ms: Annotated[int, Form(ge=200, le=4000)] = 1200,
    sfx_id: Annotated[str | None, Form(max_length=64)] = None,
    author: Annotated[str, Form(max_length=200)] = "",
    source_url: Annotated[str, Form(max_length=500)] = "",
    lib_id: Annotated[str | None, Form(max_length=64)] = None,
) -> dict[str, Any]:
    """Envia um meme ou reação (PNG ou WebM com alfa). A licença é obrigatória."""
    with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
        src = _save_upload(file, meme_library.MAX_UPLOAD_BYTES, Path(tmp))
        try:
            item = meme_library.add(src, license=license, tags=_tags(tags), risk=risk, max_ms=max_ms, sfx_id=sfx_id,
                                    author=author, source_url=source_url, lib_id=lib_id)
        except meme_library.MemeLibraryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**item.public(), "url": f"/files/{item.file}"}


@router.delete("/memes/{lib_id}")
def delete_meme(lib_id: str) -> dict[str, Any]:
    if not meme_library.delete(lib_id):
        raise HTTPException(status_code=404, detail="meme não encontrado")
    return {"deleted": lib_id}
