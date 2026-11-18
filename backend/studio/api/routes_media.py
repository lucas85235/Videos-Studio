"""Rotas da mídia real: buscar fotos livres, anexar (candidato, link ou arquivo), editar, tirar e créditos.

Todas são `def` (rodam no threadpool: a busca e o download usam a rede). O documento é lido de novo logo antes do
commit, para não desfazer uma edição feita enquanto a imagem baixava.
"""

from __future__ import annotations

import os
import re
import unicodedata
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

from studio.api.routes_projects import _rebuild
from studio.config import get_settings
from studio.core.project import media_edit, service
from studio.core.project.document import MediaRef, ProjectDoc, RealHint, RealKind, Scene
from studio.core.project.ops import OpError
from studio.core.project.pool import load_pool
from studio.core.styles.library import StyleNotFound, get_style
from studio.media import clips, real_media
from studio.media.clips import ClipError
from studio.media.ffmpeg import FFmpegError
from studio.media.real_media import SOURCE_LABELS, MediaError
from studio.media.safe_http import BlockedUrl, FetchError, TooLarge
from studio.providers import media_search
from studio.workers.jobs import get_runner
from studio.workers.pipeline import media_pool
from studio.workers.publish.credits import media_credits

router = APIRouter(prefix="/api/projects", tags=["mídia real"])

NEWS_ONLY_IMAGE = "O cartão de notícia só aparece em cena do tipo Imagem."
LOCKED = "A cena está travada: destrave para mudar a mídia real."


class FoundBody(BaseModel):
    """O achado da busca na web escolhido (`FoundMedia`): o veículo vira o crédito e a página fica como fonte."""

    image: str = Field(default="", max_length=2000)
    page_url: str = Field(default="", max_length=2000)
    title: str = Field(default="", max_length=600)
    outlet: str = Field(default="", max_length=300)


class AttachBody(BaseModel):
    via: Literal["candidate", "link", "pool"]
    candidate_id: str | None = Field(default=None, max_length=400)
    pool_id: str | None = Field(default=None, max_length=40)
    """`via="pool"`: o item do acervo do projeto (`pool.json`) que a cena passa a mostrar (trocar candidato)."""
    url: str | None = Field(default=None, max_length=2000)
    kind: RealKind | None = None
    found: FoundBody | None = None
    rebuild: bool = False


class MediaPatchBody(BaseModel):
    kind: RealKind | None = None
    credit: str | None = Field(default=None, max_length=500)
    editorial: bool | None = None
    focus: str | None = Field(default=None, max_length=40)
    fit: Literal["cover", "contain", "auto"] | None = None
    outlet: str | None = Field(default=None, max_length=300)
    published: str | None = Field(default=None, max_length=60)
    headline: str | None = Field(default=None, max_length=600)
    lang: str | None = Field(default=None, max_length=20)
    rebuild: bool = False


class RealBody(BaseModel):
    real: RealHint | None = None


def _load(project_id: str, scene_id: str) -> tuple[ProjectDoc, Scene]:
    try:
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    try:
        return doc, doc.scene(scene_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="cena não encontrada") from exc


def _editable(scene: Scene) -> None:
    if scene.locked:
        raise HTTPException(status_code=409, detail=LOCKED)


@contextmanager
def _errors() -> Iterator[None]:
    """Erros da mídia em respostas HTTP com a mensagem em português."""
    try:
        yield
    except (BlockedUrl, TooLarge) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except FetchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except MediaError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OpError as exc:
        message = str(exc)
        if message.startswith("cena não encontrada"):
            raise HTTPException(status_code=404, detail="cena não encontrada") from exc
        if "travada" in message:
            raise HTTPException(status_code=409, detail=LOCKED) from exc
        raise HTTPException(status_code=422, detail=message) from exc


def _commit_media(project_id: str, scene_id: str, media: MediaRef, warnings: list[str], rebuild: bool) -> dict[str, Any]:
    doc, _ = _load(project_id, scene_id)  # de novo: o download pode ter demorado
    with _errors():
        new_doc = media_edit.attach_media(doc, scene_id, media)
    if media.kind == "news" and new_doc.scene(scene_id).kind != "image":
        warnings = [*warnings, NEWS_ONLY_IMAGE]
    label = media_edit.scene_label(new_doc, scene_id)
    version = service.commit(project_id, new_doc, f"{label}: mídia real anexada ({SOURCE_LABELS.get(media.source, media.source)})")
    return {"version": version, "media": media.model_dump(mode="json"), "warnings": list(dict.fromkeys(warnings)),
            **_rebuild(project_id, new_doc, rebuild)}


@router.get("/{project_id}/scenes/{scene_id}/media/candidates")
def media_candidates(project_id: str, scene_id: str, q: str = "", refresh: bool = False) -> dict[str, Any]:
    """Fotos livres para a cena: a busca `q` (em inglês) ou a da sugestão do storyboard."""
    doc, scene = _load(project_id, scene_id)
    query = " ".join(q.split())[:120] or (" ".join(scene.real.query.split()) if scene.real is not None else "")
    if not query:
        return media_search.SearchResult(query="").to_dict()
    style = get_style(doc.style_id, doc.style_overrides)
    orientation = media_search.orientation_for(style.format.width, style.format.height)
    return media_search.search(query, orientation=orientation, refresh=refresh).to_dict()


@router.post("/{project_id}/scenes/{scene_id}/media")
def attach_media(project_id: str, scene_id: str, body: AttachBody) -> dict[str, Any]:
    """Anexa um candidato da busca ou o que um link aponta (página ou imagem)."""
    doc, scene = _load(project_id, scene_id)
    _editable(scene)
    if body.via == "pool":
        return _attach_pool(project_id, scene_id, doc, (body.pool_id or "").strip(), body.rebuild)
    with _errors():
        if body.via == "candidate":
            if not (body.candidate_id or "").strip():
                raise MediaError("Escolha uma foto.")
            media, warnings = real_media.media_from_candidate(body.candidate_id.strip(), project_id,  # type: ignore[union-attr]
                                                              kind=body.kind or "photo")
        else:
            url = (body.url or "").strip()
            if not url:
                raise MediaError("Cole um link.")
            kind = body.kind or (scene.real.kind if scene.real is not None else None)
            media, warnings = real_media.media_from_link(url, project_id, kind=kind, lang=doc.primary_language,
                                                         found=body.found.model_dump() if body.found else None)
    return _commit_media(project_id, scene_id, media, warnings, body.rebuild)


def _attach_pool(project_id: str, scene_id: str, doc: ProjectDoc, pool_id: str, rebuild: bool) -> dict[str, Any]:
    """Anexa um item do acervo à cena (baixa ou copia como o anexo automático, com `tier` e `pool_id`)."""
    if not pool_id:
        raise HTTPException(status_code=422, detail="Escolha um item do acervo.")
    item = load_pool(project_id).item(pool_id)
    if item is None:
        raise HTTPException(status_code=404, detail=f"O item {pool_id} não está no acervo: colete de novo.")
    try:
        style = get_style(doc.style_id, doc.style_overrides)
    except StyleNotFound as exc:
        raise HTTPException(status_code=422, detail=f"estilo não encontrado: {doc.style_id}") from exc
    try:
        media = media_pool.materialize(item, doc, style, base=doc.scene(scene_id).visual.media)
    except (OSError, ValueError, MediaError, FetchError, BlockedUrl, TooLarge, ClipError, FFmpegError) as exc:
        raise HTTPException(status_code=422, detail=f"O item {pool_id} não deu para anexar: {exc}") from exc
    warnings = ["Material oficial: pode gerar reivindicação de Content ID."] if media.editorial else []
    return _commit_media(project_id, scene_id, media, warnings, rebuild)


@router.post("/{project_id}/scenes/{scene_id}/media/upload")
def upload_media(
    project_id: str,
    scene_id: str,
    file: Annotated[UploadFile, File()],
    kind: Annotated[RealKind, Form()] = "photo",
    credit: Annotated[str, Form(max_length=500)] = "",
    editorial: Annotated[bool | None, Form()] = None,
    rebuild: Annotated[bool, Form()] = False,
) -> dict[str, Any]:
    """Anexa um arquivo enviado (JPG, PNG, WEBP...)."""
    _, scene = _load(project_id, scene_id)
    _editable(scene)
    data = file.file.read(real_media.UPLOAD_MAX_BYTES + 1)
    with _errors():
        media, warnings = real_media.media_from_upload(data, project_id, kind=kind, credit=credit, editorial=editorial)
    return _commit_media(project_id, scene_id, media, warnings, rebuild)


@router.patch("/{project_id}/scenes/{scene_id}/media")
def patch_media(project_id: str, scene_id: str, body: MediaPatchBody) -> dict[str, Any]:
    """Crédito, tipo, enquadramento, foco e campos da notícia (só os enviados mudam)."""
    doc, _ = _load(project_id, scene_id)
    patch = body.model_dump(exclude_unset=True)
    rebuild = bool(patch.pop("rebuild", False))
    with _errors():
        new_doc = media_edit.update_media(doc, scene_id, patch)
    media = new_doc.scene(scene_id).visual.media
    assert media is not None
    version = service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: mídia real editada")
    return {"version": version, "media": media.model_dump(mode="json"), "warnings": [],
            **_rebuild(project_id, new_doc, rebuild)}


@router.delete("/{project_id}/scenes/{scene_id}/media")
def delete_media(project_id: str, scene_id: str, rebuild: bool = False) -> dict[str, Any]:
    """Tira a mídia real (a cena volta à imagem de reserva; o arquivo fica guardado)."""
    doc, _ = _load(project_id, scene_id)
    with _errors():
        new_doc = media_edit.remove_media(doc, scene_id)
    version = service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: mídia real removida")
    return {"version": version, **_rebuild(project_id, new_doc, rebuild)}


@router.put("/{project_id}/scenes/{scene_id}/real")
def put_real(project_id: str, scene_id: str, body: RealBody) -> dict[str, Any]:
    """Troca a sugestão de mídia real do storyboard (null dispensa)."""
    doc, _ = _load(project_id, scene_id)
    with _errors():
        new_doc = media_edit.set_real(doc, scene_id, body.real)
    action = "dispensada" if body.real is None else "editada"
    version = service.commit(project_id, new_doc, f"{media_edit.scene_label(new_doc, scene_id)}: sugestão de mídia real {action}")
    return {"version": version}


class AiReasonBody(BaseModel):
    reason: str | None = Field(default=None, max_length=300)
    """Por que a cena é imagem de IA (hipótese, futuro, conceito). Vazio/None: sem motivo declarado."""


@router.put("/{project_id}/scenes/{scene_id}/ai_reason")
def put_ai_reason(project_id: str, scene_id: str, body: AiReasonBody) -> dict[str, Any]:
    """O motivo de a cena ser imagem de IA (o achado `faltou_real` some quando a cena diz o motivo)."""
    doc, scene = _load(project_id, scene_id)
    _editable(scene)
    reason = " ".join((body.reason or "").split()) or None
    scene.visual.ai_reason = reason
    action = "motivo da imagem de IA " + ("registrado" if reason else "removido")
    version = service.commit(project_id, doc, f"{media_edit.scene_label(doc, scene_id)}: {action}")
    return {"version": version, "ai_reason": reason}


# --- acervo de material real (ADR 0004 §3.4) -----------------------------------------------------------------------


class PoolLinkBody(BaseModel):
    url: str = Field(min_length=8, max_length=2000)
    label: str = Field(default="", max_length=120)
    tier: Literal["official", "own"] = "official"
    """Material oficial (trailer, gameplay, vídeo da empresa) ou do próprio canal."""


class PoolLinksBody(BaseModel):
    links: list[PoolLinkBody] = Field(default_factory=list, max_length=media_pool.MAX_LINKS)
    replace: bool = False
    """True troca a lista inteira (tirar um link); False junta aos que já existem."""


def _project(project_id: str) -> None:
    try:
        service.get_project_row(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


def _http_link(url: str) -> str:
    text = " ".join(url.split())
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=422, detail=f"Link inválido: {text[:120]} (use http ou https).")
    return text


@router.get("/{project_id}/pool")
def get_pool(project_id: str) -> dict[str, Any]:
    """O acervo gravado (`pool.json`), os links oficiais, os arquivos próprios, o resumo por nível, o espaço das
    fontes de vídeo baixadas (com teto: as menos usadas saem) e o job ativo que mexe no acervo."""
    _project(project_id)
    pool = load_pool(project_id)
    return {"pool": pool.model_dump(mode="json"), "links": media_pool.pool_links(project_id),
            "summary": media_pool.summary(pool), "files": _own_files(project_id),
            "sources_bytes": clips.sources_bytes(), "job": media_pool.active_job(project_id)}


def _auto_refresh(project_id: str) -> dict[str, Any] | None:
    """Coleta completa logo depois de o usuário dar links ou arquivos (baixa e corta já, não no storyboard). Com outro
    job do acervo ativo (coleta, storyboard, piloto), não começa outra: devolve a coleta ativa ou None."""
    active = media_pool.active_job(project_id)
    if active is not None:
        return active if active["kind"] == "media_pool" else None
    return get_runner().submit("media_pool", {}, project_id)


@router.post("/{project_id}/pool/links")
def post_pool_links(project_id: str, body: PoolLinksBody) -> dict[str, Any]:
    """Links oficiais dados pelo usuário (trailer, gameplay, vídeo do canal). Link novo dispara a coleta completa
    (baixa e corta os planos); `job` é ela (ou a que já estava rodando)."""
    _project(project_id)
    before = {link["url"] for link in media_pool.pool_links(project_id)}
    current = [] if body.replace else media_pool.pool_links(project_id)
    merged: dict[str, dict[str, str]] = {link["url"]: link for link in current}
    for link in body.links:
        clean = media_pool.normalize_link({**link.model_dump(), "url": _http_link(link.url)})
        merged[clean["url"]] = clean
    links = list(merged.values())
    if len(links) > media_pool.MAX_LINKS:
        raise HTTPException(status_code=422, detail=f"No máximo {media_pool.MAX_LINKS} links por projeto.")
    service.update_settings(project_id, {media_pool.LINKS_KEY: links})
    job = _auto_refresh(project_id) if any(link["url"] not in before for link in links) else None
    return {"links": links, "job": job}


@router.post("/{project_id}/pool/refresh")
def refresh_pool(project_id: str) -> dict[str, Any]:
    """Coleta o acervo de novo (job `media_pool`): baixa os links novos, captura as fontes e busca por tópico. Com
    uma coleta já rodando, devolve ela; com o storyboard ou o piloto rodando (eles leem e gravam o mesmo acervo),
    409."""
    _project(project_id)
    active = media_pool.active_job(project_id)
    if active is not None:
        if active["kind"] == "media_pool":
            return active
        raise HTTPException(status_code=409, detail="O planejamento das cenas está rodando e usa o acervo; espere "
                                                    "terminar para atualizar.")
    return get_runner().submit("media_pool", {}, project_id)


# Arquivos próprios do acervo: `uploads/<projeto>/acervo/` (o nível `own`, o mais real que existe).

OWN_FILES_PER_REQUEST = 20
_CHUNK = 1 << 20


def _own_dir(project_id: str) -> Path:
    return get_settings().uploads_dir / project_id / media_pool.OWN_DIR


def _own_exts() -> frozenset[str]:
    return media_pool.OWN_IMAGE_EXTS | media_pool.OWN_VIDEO_EXTS


def _own_file_name(raw: str | None, folder: Path) -> str:
    """Nome saneado, com a extensão aceita (imagem ou vídeo) e sem pisar num arquivo que já existe."""
    name = Path(unicodedata.normalize("NFKC", raw or "").replace("\\", "/")).name
    stem, suffix = Path(name).stem, Path(name).suffix.lower()
    if suffix not in _own_exts():
        allowed = ", ".join(sorted(_own_exts()))
        raise HTTPException(status_code=422, detail=f"Arquivo {name[:80] or '(sem nome)'} não aceito: use {allowed}.")
    stem = re.sub(r"[^\w\- ]+", "_", stem).strip(" ._-")[:80] or "arquivo"
    candidate, n = f"{stem}{suffix}", 2
    while (folder / candidate).exists():
        candidate, n = f"{stem}-{n}{suffix}", n + 1
    return candidate


def _own_files(project_id: str) -> list[dict[str, Any]]:
    folder = _own_dir(project_id)
    if not folder.is_dir():
        return []
    out = []
    for path in sorted(folder.iterdir()):
        suffix = path.suffix.lower()
        if path.is_file() and not path.name.startswith(".") and suffix in _own_exts():
            out.append({"name": path.name, "size": path.stat().st_size,
                        "kind": "video" if suffix in media_pool.OWN_VIDEO_EXTS else "image",
                        "path": f"uploads/{project_id}/{media_pool.OWN_DIR}/{path.name}"})
    return out


@router.get("/{project_id}/pool/files")
def get_pool_files(project_id: str) -> dict[str, Any]:
    """Os arquivos próprios do acervo (fotos e vídeos do usuário)."""
    _project(project_id)
    return {"files": _own_files(project_id)}


@router.post("/{project_id}/pool/files")
def post_pool_files(project_id: str, files: Annotated[list[UploadFile], File()]) -> dict[str, Any]:
    """Envia fotos e vídeos próprios para o acervo (`uploads/<projeto>/acervo/`) e dispara a coleta completa (o vídeo
    é cortado em planos). Imagem até 25 MB, vídeo até 2 GB."""
    _project(project_id)
    if not files:
        raise HTTPException(status_code=422, detail="Nenhum arquivo enviado.")
    if len(files) > OWN_FILES_PER_REQUEST:
        raise HTTPException(status_code=422, detail=f"No máximo {OWN_FILES_PER_REQUEST} arquivos por envio.")
    folder = _own_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for upload in files:
        name = _own_file_name(upload.filename, folder)
        video = Path(name).suffix.lower() in media_pool.OWN_VIDEO_EXTS
        limit = media_pool.OWN_VIDEO_MAX_BYTES if video else real_media.UPLOAD_MAX_BYTES
        tmp = folder / f".{uuid.uuid4().hex[:10]}.upload"
        size = 0
        try:
            with open(tmp, "wb") as fh:
                while chunk := upload.file.read(_CHUNK):
                    size += len(chunk)
                    if size > limit:
                        raise HTTPException(status_code=413, detail=f"{name}: passa de {limit // 1_000_000} MB.")
                    fh.write(chunk)
            if size == 0:
                raise HTTPException(status_code=422, detail=f"{name}: arquivo vazio.")
            if not video:
                try:
                    with Image.open(tmp) as im:
                        im.verify()
                except Exception as exc:  # noqa: BLE001 - não é imagem
                    raise HTTPException(status_code=422, detail=f"{name}: não é uma imagem válida.") from exc
            os.replace(tmp, folder / name)
        finally:
            tmp.unlink(missing_ok=True)
        saved.append(name)
    return {"saved": saved, "files": _own_files(project_id), "job": _auto_refresh(project_id)}


@router.delete("/{project_id}/pool/files/{name}")
def delete_pool_file(project_id: str, name: str) -> dict[str, Any]:
    """Tira um arquivo próprio do acervo (sai na próxima coleta; a cena que já o usa tem a cópia dela)."""
    _project(project_id)
    path = _own_dir(project_id) / Path(name).name
    if Path(name).name != name or name.startswith(".") or not path.is_file():
        raise HTTPException(status_code=404, detail="arquivo não encontrado no acervo")
    path.unlink()
    return {"files": _own_files(project_id)}


@router.get("/{project_id}/media/credits")
def get_media_credits(project_id: str, lang: str | None = None) -> dict[str, Any]:
    """O que vai no fim da descrição ao publicar ("Créditos das imagens")."""
    try:
        doc = service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc
    return media_credits(doc, lang or doc.primary_language)
