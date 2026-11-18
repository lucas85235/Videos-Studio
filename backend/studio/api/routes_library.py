"""Biblioteca de efeitos sonoros e de memes do usuário, com licença registrada (ADR 0004 §3.8).

SFX:
- `GET /api/library/sfx`: embutidos (procedurais) e do usuário, com `auto`, `missing` e o aviso de cada um, os
  arquivos soltos ainda não importados (`loose`) e o estado da chave do Freesound. Não importa nada (sem efeito
  colateral): os soltos entram pelo `POST /sfx/scan`;
- `POST /api/library/sfx` (multipart `file`, `kind`, `license`, `tags`, `author`, `source_url`): envia um efeito;
- `PATCH /api/library/sfx/{id}`: declara licença, tipo, tags, autor ou corrige o ponto forte (`hit_s`);
- `DELETE /api/library/sfx/{id}`;
- `GET /api/library/sfx/{id}/audio`: o arquivo como entra no mix (prévia);
- `POST /api/library/sfx/scan`: job `sfx_scan` (os arquivos soltos na pasta entram como licença desconhecida);
- `POST /api/library/sfx/fetch-freesound` (`{sound, kind, tags}`): só CC0, com a chave do usuário;
- `PUT|DELETE /api/library/freesound-key` (`{api_key}`): a chave do Freesound no cofre;
- `POST /api/library/sfx/voice` (`{channel_id?, style_id?, lang}`): job `sfx_voice`, "ué", "pera", "oxe", "hã?" com a
  voz final do canal (recusa na hora, 422, sem a voz final disponível);
- `POST /api/library/sfx/import-canais` (`{dry_run, replace, license_checked}`): com `dry_run` o relatório na hora;
  sem, o job `sfx_import_canais` (os .wav do canais, só leitura lá).

Memes:
- `GET /api/library/memes?channel_id=`: os do usuário, as poses do avatar do canal e os templates "meme sem meme";
- `POST /api/library/memes` (multipart `file`, `license` obrigatória, `tags`, `risk`, `max_ms`, `sfx_id`...);
- `DELETE /api/library/memes/{id}`.

Campo obrigatório que falta (licença do meme, tipo do efeito) dá 422 com a mensagem em português da biblioteca, não a
do validador do FastAPI.
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
from studio.workers.jobs import get_runner

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
    items = sfx_library.all_items()
    root = sfx_library.user_root()
    return {
        "dir": str(root),
        "items": [i.public(tonal_ok) for i in items],
        "warnings": sfx_library.warnings(items),
        "loose": [p.relative_to(root).as_posix() for p in sfx_library.loose_files(root)],
        "licenses": [{"id": k, "label": v} for k, v in sfx_library.LICENSE_LABELS.items()],
        "procedural": list(audio_mod.SFX_KINDS),
        "freesound": sfx_library.freesound_key_state(),
        "offline": get_settings().offline,
    }


@router.post("/sfx")
def upload_sfx(
    file: Annotated[UploadFile, File()],
    kind: Annotated[str, Form(max_length=40)] = "",
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
            item = sfx_library.import_file(src, kind=kind, license=license or sfx_library.UNKNOWN_LICENSE,
                                           tags=_tags(tags), author=author, source_url=source_url, lib_id=lib_id,
                                           imported_from="upload")
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
    hit_s: float | None = Field(default=None, ge=0, le=sfx_library.MAX_SFX_S)
    """Ponto forte corrigido à mão (s desde o início do efeito preparado)."""


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


@router.post("/sfx/scan")
def scan_sfx() -> dict[str, Any]:
    """Job `sfx_scan`: os arquivos soltos na pasta entram no manifesto (licença desconhecida)."""
    return get_runner().submit("sfx_scan", {})


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


class FreesoundKeyBody(BaseModel):
    api_key: str = Field(min_length=1, max_length=200)


@router.put("/freesound-key")
def set_freesound_key(body: FreesoundKeyBody) -> dict[str, Any]:
    try:
        return sfx_library.set_freesound_key(body.api_key)
    except sfx_library.SfxLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/freesound-key")
def delete_freesound_key() -> dict[str, Any]:
    return sfx_library.delete_freesound_key()


class VoiceBody(BaseModel):
    channel_id: str | None = Field(default=None, max_length=64)
    style_id: str | None = Field(default=None, max_length=100)
    """Sem canal: a voz final do estilo."""
    lang: str = Field(default="pt-BR", max_length=12)


@router.post("/sfx/voice")
def voice_sfx(body: VoiceBody) -> dict[str, Any]:
    """Job `sfx_voice`: grava (uma vez) as interjeições com a voz final do canal; ficam em cache na biblioteca,
    licença própria. Sem a voz final disponível (offline, sem chave, só voz de teste), 422 na hora."""
    try:
        target = sfx_library.voice_target(body.channel_id, body.style_id)
    except sfx_library.SfxLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = get_runner().submit("sfx_voice", body.model_dump())
    return {**job, "voice": target.model_dump()}


class CanaisSfxBody(BaseModel):
    dry_run: bool = False
    replace: bool = False
    license_checked: bool = False
    """O usuário conferiu no Mixkit que os efeitos rastreados não são "Restricted" (entram como `mixkit-free`)."""


@router.post("/sfx/import-canais")
def import_canais_sfx(body: CanaisSfxBody) -> dict[str, Any]:
    """Importa os efeitos do canais (lidos lá, copiados para a biblioteca do usuário; nada vai para o repositório).
    `dry_run`: o relatório na hora (sem decodificar); senão, o job `sfx_import_canais`."""
    from studio.workers.canais_import import sfx as canais_sfx

    if body.dry_run:
        return canais_sfx.import_sfx(dry_run=True, replace=body.replace,
                                     license_checked=body.license_checked).to_dict()
    return get_runner().submit("sfx_import_canais", {"replace": body.replace,
                                                     "license_checked": body.license_checked})


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
        "warnings": meme_library.warnings(),
        "templates": [t.model_dump(mode="json") for t in meme_library.TEMPLATES.values()],
        "licenses": [{"id": k, "label": v} for k, v in meme_library.LICENSE_LABELS.items()],
        "risks": list(meme_library.RISKS),
    }


@router.post("/memes")
def upload_meme(
    file: Annotated[UploadFile, File()],
    license: Annotated[str, Form(max_length=20)] = "",
    tags: Annotated[str, Form(max_length=500)] = "",
    risk: Annotated[str, Form(max_length=10)] = "",
    max_ms: Annotated[int, Form(ge=200, le=4000)] = 1200,
    sfx_id: Annotated[str | None, Form(max_length=64)] = None,
    author: Annotated[str, Form(max_length=200)] = "",
    source_url: Annotated[str, Form(max_length=500)] = "",
    lib_id: Annotated[str | None, Form(max_length=64)] = None,
) -> dict[str, Any]:
    """Envia um meme ou reação (PNG ou WebM com alfa). A licença é obrigatória (422 em português sem ela). Sem risco
    declarado: baixo, ou médio para `fair_comment` (material de terceiros)."""
    with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
        src = _save_upload(file, meme_library.MAX_UPLOAD_BYTES, Path(tmp))
        try:
            item = meme_library.add(src, license=license, tags=_tags(tags), risk=risk or None, max_ms=max_ms,
                                    sfx_id=sfx_id, author=author, source_url=source_url, lib_id=lib_id)
        except meme_library.MemeLibraryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {**item.public(), "url": f"/files/{item.file}"}


@router.delete("/memes/{lib_id}")
def delete_meme(lib_id: str) -> dict[str, Any]:
    if not meme_library.delete(lib_id):
        raise HTTPException(status_code=404, detail="meme não encontrado")
    return {"deleted": lib_id}
