"""Rotas do pacote (título e thumb antes do roteiro) e do upload da biblioteca de thumbs do canal."""

from __future__ import annotations

import json
import re
from typing import Annotated, Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, ValidationError

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import (
    CHANNEL_THUMBS_KEY,
    EDITORIAL_NOTICE,
    PACKAGE_TITLE_MAX,
    ChannelThumbs,
    PackageCandidate,
    ProjectDoc,
    ThumbLayers,
)
from studio.core.styles.library import StyleNotFound, get_style
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel
from studio.workers import tasks
from studio.workers.jobs import get_runner
from studio.workers.pipeline import authoring

router = APIRouter(prefix="/api", tags=["pacote"])

UNLOCK_NOTICE = ("O roteiro foi escrito para o título e a thumb anteriores: confira se o gancho ainda cumpre a promessa "
                 "do novo par (ou reescreva o gancho).")
LOCKED_DETAIL = "o pacote está aprovado: destrave para mudar título, texto ou escolha"
THUMBKIT_KINDS = ("frame", "cutout", "logo", "object")
THUMBKIT_MAX_BYTES = 25 * 1024 * 1024
_SAFE_ID = re.compile(r"[^a-z0-9_\-]+")


class GenerateBody(BaseModel):
    n_titles: int | None = Field(default=None, ge=1, le=8)
    n_thumbs: int | None = Field(default=None, ge=1, le=4)
    keep_manual: bool = True
    append: bool = False
    """True: soma os candidatos novos aos que já existem ("Gerar mais")."""


class CandidateEdit(BaseModel):
    title: str | None = Field(default=None, max_length=PACKAGE_TITLE_MAX)
    thumb_text: str | None = Field(default=None, max_length=60)
    promise: str | None = Field(default=None, max_length=400)
    concept: str | None = Field(default=None, max_length=600)
    layers: ThumbLayers | None = None
    approved: bool | None = None


class CandidateCreate(BaseModel):
    from_id: str | None = None
    """Duplica este candidato (os campos abaixo, se vierem, substituem os dele)."""
    title: str | None = Field(default=None, max_length=PACKAGE_TITLE_MAX)
    thumb_text: str | None = Field(default=None, max_length=60)
    promise: str | None = Field(default=None, max_length=400)
    concept: str | None = Field(default=None, max_length=600)
    layers: ThumbLayers | None = None


class ThumbsBody(BaseModel):
    candidate_ids: list[str] | None = None


class ChoiceBody(BaseModel):
    chosen_id: str | None = None
    approved_ids: list[str] = []


class ApproveBody(BaseModel):
    chosen_id: str | None = None
    approved_ids: list[str] | None = None
    continue_pipeline: bool = True


# --- apoio -------------------------------------------------------------------------------------------------------
def _doc(project_id: str) -> ProjectDoc:
    try:
        return service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _candidate(doc: ProjectDoc, candidate_id: str) -> PackageCandidate:
    cand = doc.package.candidate(candidate_id)
    if cand is None:
        raise HTTPException(status_code=404, detail=f"candidato não encontrado: {candidate_id}")
    return cand


def _bible(channel_id: str | None) -> dict[str, Any]:
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return dict(row.style_bible or {}) if row is not None else {}


def _validated(doc: ProjectDoc) -> ProjectDoc:
    try:
        return ProjectDoc.model_validate(doc.model_dump())
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _queue_thumbs(project_id: str, candidate_ids: list[str] | None = None) -> dict[str, Any]:
    return get_runner().submit("package_thumbs", {"candidate_ids": candidate_ids} if candidate_ids else {}, project_id)


def _resolve_refs(ids: list[str], channel_id: str | None, topic: str) -> list[dict[str, Any]]:
    """As referências gravadas no pacote, com título, multiplicador e a thumb do YouTube (para a tela comparar)."""
    known = {r["id"]: r for r in authoring.package_references(channel_id, topic, limit=60)}
    out = []
    for vid in ids:
        ref = known.get(vid) or {"id": vid, "title": "", "channel": "", "views": None, "mult": None, "source": ""}
        out.append({**ref, "thumbnail_url": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                    "url": f"https://www.youtube.com/watch?v={vid}"})
    return out


def package_view(project_id: str) -> dict[str, Any]:
    """O pacote do projeto e o que a tela precisa para editá-lo (contrato do GET)."""
    doc = _doc(project_id)
    row = service.get_project_row(project_id)
    gates = (row.get("settings") or {}).get("gates", {})
    try:
        style = get_style(doc.style_id, doc.style_overrides)
    except StyleNotFound as exc:
        raise HTTPException(status_code=400, detail=f"estilo não encontrado: {exc}") from exc
    assets = authoring.package_assets(doc, style, _bible(doc.channel_id))
    is_long = style.format.type != "short"
    templates = list(style.thumbnail.templates) if is_long else ["livre"]
    if not assets.poses:
        templates = [t for t in templates if t != "avatar-reage"]
    data = doc.package.model_dump(mode="json")
    for cand in data["candidates"]:
        cand["image_url"] = f"/files/{cand['image']}" if cand.get("image") else None
    return {
        **data,
        "references_resolved": _resolve_refs(doc.package.references, doc.channel_id, doc.topic or doc.title),
        "gate": tasks.package_gate_on(gates, style),
        "status": row["status"],
        "format": "long" if is_long else "short",
        "size": list(tasks.package_size(style)),
        "has_script": bool(doc.segments),
        "notice": UNLOCK_NOTICE if doc.segments and not doc.package.locked and doc.package.candidates else None,
        "templates": templates,
        "title_candidates": style.thumbnail.title_candidates,
        "thumb_candidates": style.thumbnail.thumb_candidates if is_long else 1,
        "allow_editorial": assets.allow_editorial,
        "editorial_notice": EDITORIAL_NOTICE if assets.allow_editorial else None,
        "presenter": {"enabled": bool(assets.poses), "poses": assets.poses},
        "assets": {"frames": assets.frames, "cutouts": assets.cutouts, "logos": assets.logos,
                   "objects": assets.objects},
    }


# --- rotas do pacote ---------------------------------------------------------------------------------------------
@router.get("/projects/{project_id}/package")
def get_package(project_id: str) -> dict[str, Any]:
    return package_view(project_id)


@router.post("/projects/{project_id}/package")
def generate(project_id: str, body: GenerateBody | None = None) -> dict[str, Any]:
    doc = _doc(project_id)
    if doc.package.locked:
        raise HTTPException(status_code=409, detail=LOCKED_DETAIL)
    payload = (body or GenerateBody()).model_dump(exclude_none=True)
    return {"job": get_runner().submit("package", payload, project_id)}


@router.put("/projects/{project_id}/package/candidates/{candidate_id}")
def edit_candidate(project_id: str, candidate_id: str, body: CandidateEdit) -> dict[str, Any]:
    doc = _doc(project_id)
    cand = _candidate(doc, candidate_id)
    changes = body.model_dump(exclude_unset=True)
    visual = {k for k in changes if k in ("title", "thumb_text", "layers")}
    if doc.package.locked and cand.id == doc.package.chosen_id:
        # Travado: título, texto e promessa ficam (o roteiro foi escrito para eles). As camadas podem mudar desde que
        # o texto da thumb continue o mesmo (ex.: trocar o fundo por um frame do próprio vídeo depois do render).
        old_words = cand.layers.text.words if cand.layers.text else ""
        new_words = (body.layers.text.words if body.layers.text else "") if body.layers is not None else old_words
        if {"title", "thumb_text", "promise"} & set(changes) or new_words != old_words:
            raise HTTPException(status_code=409, detail=LOCKED_DETAIL)
    for key in ("title", "thumb_text", "promise", "concept"):
        if changes.get(key) is not None:
            setattr(cand, key, " ".join(str(changes[key]).split()))
    if body.layers is not None:
        cand.layers = body.layers
    if "thumb_text" in changes and body.layers is None and cand.layers.text is not None:
        cand.layers.text.words = cand.thumb_text  # o texto da thumb é o da camada de texto
    if body.approved is not None:
        cand.approved = body.approved
    if changes.keys() - {"approved"}:
        cand.source = "manual"
    if visual:
        cand.score = {}
    version = service.commit(project_id, _validated(doc), f"Pacote: candidato {candidate_id} editado")
    job = _queue_thumbs(project_id, [candidate_id]) if visual else None
    return {"version": version, "job": job, "candidate": cand.model_dump(mode="json")}


@router.post("/projects/{project_id}/package/candidates")
def create_candidate(project_id: str, body: CandidateCreate) -> dict[str, Any]:
    doc = _doc(project_id)
    base = _candidate(doc, body.from_id) if body.from_id else None
    data = base.model_dump(mode="json") if base is not None else {}
    data.update({"id": new_id("pkc", 8), "image": None, "score": {}, "approved": False, "source": "manual"})
    for key in ("title", "thumb_text", "promise", "concept"):
        value = getattr(body, key)
        if value is not None:
            data[key] = value
    if body.layers is not None:
        data["layers"] = body.layers.model_dump(mode="json")
    elif base is None and data.get("thumb_text"):
        data["layers"] = ThumbLayers(text={"words": data["thumb_text"]}).model_dump(mode="json")  # type: ignore[arg-type]
    try:
        cand = PackageCandidate.model_validate(data)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    doc.package.candidates.append(cand)
    version = service.commit(project_id, _validated(doc), "Pacote: candidato manual")
    return {"version": version, "job": _queue_thumbs(project_id, [cand.id]), "candidate": cand.model_dump(mode="json")}


@router.delete("/projects/{project_id}/package/candidates/{candidate_id}")
def delete_candidate(project_id: str, candidate_id: str) -> dict[str, Any]:
    doc = _doc(project_id)
    _candidate(doc, candidate_id)
    if doc.package.locked and candidate_id == doc.package.chosen_id:
        raise HTTPException(status_code=409, detail=LOCKED_DETAIL)
    doc.package.candidates = [c for c in doc.package.candidates if c.id != candidate_id]
    version = service.commit(project_id, _validated(doc), f"Pacote: candidato {candidate_id} removido")
    for old in (get_settings().outputs_dir / project_id / "package").glob(f"{candidate_id}-*.jpg"):
        old.unlink(missing_ok=True)
    return {"version": version}


@router.post("/projects/{project_id}/package/thumbs")
def rerender(project_id: str, body: ThumbsBody | None = None) -> dict[str, Any]:
    doc = _doc(project_id)
    ids = (body.candidate_ids if body else None) or None
    if ids:
        for cid in ids:
            _candidate(doc, cid)
    elif not doc.package.candidates:
        raise HTTPException(status_code=400, detail="o pacote não tem candidatos")
    return {"job": _queue_thumbs(project_id, ids)}


@router.put("/projects/{project_id}/package/choice")
def choose(project_id: str, body: ChoiceBody) -> dict[str, Any]:
    doc = _doc(project_id)
    if doc.package.locked and body.chosen_id != doc.package.chosen_id:
        raise HTTPException(status_code=409, detail=LOCKED_DETAIL)
    if body.chosen_id is not None:
        _candidate(doc, body.chosen_id)
    for cid in body.approved_ids:
        _candidate(doc, cid)
    doc.package.chosen_id = body.chosen_id
    wanted = set(body.approved_ids) | ({body.chosen_id} if body.chosen_id else set())
    for cand in doc.package.candidates:
        cand.approved = cand.id in wanted
    version = service.commit(project_id, _validated(doc), "Pacote: escolha")
    return {"version": version, "chosen_id": doc.package.chosen_id,
            "approved_ids": [c.id for c in doc.package.approved()]}


def approve_package(project_id: str, chosen_id: str | None = None, approved_ids: list[str] | None = None,
                    continue_pipeline: bool = True) -> dict[str, Any]:
    """Trava o pacote (título, texto da thumb) e segue o piloto para o roteiro. Também atende
    `POST /projects/{id}/approve` com `stage="package"`."""
    doc = _doc(project_id)
    try:
        version = tasks.approve_package(project_id, chosen_id, approved_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    job = None
    notice = None
    if doc.segments:
        notice = UNLOCK_NOTICE
        if service.get_project_row(project_id)["status"] == tasks.PACKAGE_STATUS:
            service.set_status(project_id, "draft")  # não fica listado como "Escolher título e thumb"
    elif continue_pipeline:
        service.set_status(project_id, "draft")
        job = get_runner().submit("autopilot", {}, project_id)
    else:
        service.set_status(project_id, "draft")
    return {"version": version, "job": job, "notice": notice}


@router.post("/projects/{project_id}/package/approve")
def approve(project_id: str, body: ApproveBody | None = None) -> dict[str, Any]:
    body = body or ApproveBody()
    return approve_package(project_id, body.chosen_id, body.approved_ids, body.continue_pipeline)


@router.post("/projects/{project_id}/package/unlock")
def unlock(project_id: str) -> dict[str, Any]:
    doc = _doc(project_id)
    if not doc.package.locked:
        return {"version": None, "notice": None}
    doc.package.locked = False
    version = service.commit(project_id, doc, "Pacote destravado")
    return {"version": version, "notice": UNLOCK_NOTICE if doc.segments else None}


# --- biblioteca de thumbs do canal (upload) ----------------------------------------------------------------------
def _manifest_path(channel_id: str) -> Any:
    return get_settings().library_dir / "thumbkit" / channel_id / "thumbkit.json"


@router.post("/channels/{channel_id}/thumbkit")
def upload_thumbkit(channel_id: str, file: Annotated[UploadFile, File()],
                    kind: Annotated[Literal["frame", "cutout", "logo", "object"], Form()] = "frame",
                    tags: Annotated[str, Form()] = "", credit: Annotated[str, Form()] = "",
                    editorial: Annotated[bool, Form()] = False) -> dict[str, Any]:
    """Guarda uma imagem na biblioteca de thumbs do canal e a soma ao manifesto. Recorte e logo precisam de PNG com
    alfa (o recorte "precisa de recorte" sem ele)."""
    from io import BytesIO

    from PIL import Image, UnidentifiedImageError

    with session_scope() as s:
        if s.get(Channel, channel_id) is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
    data = file.file.read(THUMBKIT_MAX_BYTES + 1)
    if len(data) > THUMBKIT_MAX_BYTES:
        raise HTTPException(status_code=413, detail="arquivo grande demais (máximo 25 MB)")
    try:
        with Image.open(BytesIO(data)) as im:
            fmt = (im.format or "").upper()
            has_alpha = im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info)
            alpha_used = has_alpha and im.convert("RGBA").getchannel("A").getextrema()[0] < 255
            width, height = im.size
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=422, detail="não é uma imagem válida") from exc
    if fmt not in ("PNG", "JPEG", "WEBP"):
        raise HTTPException(status_code=422, detail="use PNG, JPG ou WEBP")
    if kind in ("cutout", "logo") and not alpha_used:
        raise HTTPException(status_code=422, detail="recorte e logo precisam de PNG com fundo transparente (alfa)")
    stem = _SAFE_ID.sub("-", (file.filename or "img").rsplit(".", 1)[0].lower()).strip("-")[:40] or "img"
    item_id = f"{kind}-{stem}-{new_id('tk', 4)[3:]}"
    ext = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp"}[fmt]
    folder = _manifest_path(channel_id).parent
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{item_id}.{ext}").write_bytes(data)
    path = _manifest_path(channel_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = {"items": []}
    items = raw.get("items") if isinstance(raw, dict) else raw
    items = [i for i in items or [] if isinstance(i, dict)]
    item = {"id": item_id, "kind": kind, "file": f"{item_id}.{ext}",
            "tags": [t.strip().lower() for t in tags.split(",") if t.strip()][:12], "credit": credit.strip()[:200],
            "editorial": bool(editorial), "width": width, "height": height,
            "source": {"upload": file.filename or item_id}}
    if kind == "cutout":
        # lados cortados e qualidade medidos já no envio: o seletor do pacote e o compositor usam a mesma medida
        from studio.media.thumb_assets import cut_edges_of, cutout_quality

        item["cut_edges"] = cut_edges_of(folder / item["file"])
        item["quality"] = cutout_quality(item)
    items.append(item)
    out = {**raw, "items": items} if isinstance(raw, dict) else {"items": items}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    rel = (folder / item["file"]).relative_to(get_settings().data_dir).as_posix()
    return {**item, "path": rel, "url": f"/files/{rel}"}


class ChannelThumbsBody(BaseModel):
    allow_editorial: bool | None = None


def _thumbs_view(channel_id: str, bible: dict[str, Any]) -> dict[str, Any]:
    prefs = ChannelThumbs.from_bible(bible)
    return {"channel_id": channel_id, "thumbs": prefs.model_dump(mode="json"), "editorial_notice": EDITORIAL_NOTICE}


@router.get("/channels/{channel_id}/thumbs")
def channel_thumbs(channel_id: str) -> dict[str, Any]:
    """Preferências de thumb do canal (`style_bible["thumbs"]`): hoje, a liberação do material oficial."""
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        return _thumbs_view(channel_id, dict(row.style_bible or {}))


@router.put("/channels/{channel_id}/thumbs")
def save_channel_thumbs(channel_id: str, body: ChannelThumbsBody) -> dict[str, Any]:
    """Libera (true), proíbe (false) ou devolve ao estilo (null) os frames e a arte oficial nas thumbs do canal.
    Personagem oficial gerado por IA continua proibido (`ai_official_characters` é sempre false)."""
    prefs = ChannelThumbs(allow_editorial=body.allow_editorial)
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise HTTPException(status_code=404, detail="canal não encontrado")
        row.style_bible = {**(row.style_bible or {}), CHANNEL_THUMBS_KEY: prefs.model_dump(mode="json")}
        bible = dict(row.style_bible)
    return _thumbs_view(channel_id, bible)
