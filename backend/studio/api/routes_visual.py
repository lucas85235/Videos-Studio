"""Rotas do visual (ADR 0003, bloco I): provas na tela, vídeos de referência e miniaturas para o teste A/B.

- `GET /api/visual/capabilities`: navegador (captura) e yt-dlp (download por link).
- Provas: `POST /api/projects/{id}/scenes/{scene_id}/proof` (job `page_capture`), `GET /api/projects/{id}/captures`,
  `GET /api/captures/{id}`, `PUT /api/captures/{id}/phrases/{i}/view`.
- Referências: `GET|POST /api/references`, `POST /api/references/link`, `GET|DELETE /api/references/{id}`,
  `POST /api/references/{id}/analyze` (job `reference_shots`), `POST /api/references/{id}/style` (cria um estilo do
  usuário a partir das medidas).
- Miniaturas: `GET /api/projects/{id}/thumbs`, `PUT .../thumbs/spec`, `POST .../thumbs` (job `thumbs_variants`),
  `PUT .../thumbs/chosen`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from studio.core.project import service
from studio.core.styles import from_reference
from studio.media import page_capture, reference_video
from studio.media.safe_http import BlockedUrl
from studio.workers.jobs import get_runner
from studio.workers.visual import proofs, references, thumbs

router = APIRouter(prefix="/api", tags=["visual"])


class ProofBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    phrases: list[str] = Field(min_length=1, max_length=page_capture.MAX_PHRASES)
    focus: int = Field(default=0, ge=0, le=page_capture.MAX_PHRASES - 1)


class ViewBody(BaseModel):
    view: list[float] | None = Field(default=None, min_length=4, max_length=4)
    lock: bool = True


class LinkBody(BaseModel):
    url: str = Field(min_length=1, max_length=2000)
    title: str | None = Field(default=None, max_length=300)


class RefStyleBody(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    use_llm: bool = True


class ThumbSpecItem(BaseModel):
    scene_id: str | None = Field(default=None, max_length=32)
    text: str = Field(default="", max_length=200)
    template: str = Field(default="texto-grande-esquerda", max_length=40)


class SpecBody(BaseModel):
    lang: str | None = Field(default=None, max_length=20)
    variants: list[ThumbSpecItem] = Field(min_length=1, max_length=3)


class LangBody(BaseModel):
    lang: str | None = Field(default=None, max_length=20)


class ChosenBody(BaseModel):
    lang: str | None = Field(default=None, max_length=20)
    n: int = Field(ge=1, le=3)


def _project(project_id: str) -> dict[str, Any]:
    try:
        return service.get_project_row(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="projeto não encontrado") from exc


@router.get("/visual/capabilities")
def capabilities() -> dict[str, Any]:
    return {"browser": page_capture.browser_status(), "ytdlp": reference_video.ytdlp_status()}


# --------------------------------------------------------------------------------------------
# Provas na tela
# --------------------------------------------------------------------------------------------


@router.post("/projects/{project_id}/scenes/{scene_id}/proof")
def capture_proof(project_id: str, scene_id: str, body: ProofBody) -> dict[str, Any]:
    _project(project_id)
    status = page_capture.browser_status()
    if not status["ok"]:
        raise HTTPException(status_code=400, detail=status["message"])
    doc = service.get_doc(project_id)
    try:
        scene = doc.scene(scene_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="cena não encontrada") from exc
    if scene.locked:
        raise HTTPException(status_code=409, detail="A cena está travada: destrave para mudar a mídia real.")
    phrases = [" ".join(p.split()) for p in body.phrases if p.strip()]
    if not phrases:
        raise HTTPException(status_code=422, detail="Escreva pelo menos um trecho para marcar.")
    try:
        url = page_capture.safe_http.check_url(body.url)
    except BlockedUrl as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    payload = {"scene_id": scene_id, "url": url, "phrases": phrases, "focus": min(body.focus, len(phrases) - 1)}
    return get_runner().submit("page_capture", payload, project_id)


@router.get("/projects/{project_id}/captures")
def project_captures(project_id: str) -> list[dict[str, Any]]:
    _project(project_id)
    return proofs.list_captures(project_id)


@router.get("/captures/{capture_id}")
def get_capture(capture_id: str) -> dict[str, Any]:
    out = proofs.get_capture(capture_id)
    if out is None:
        raise HTTPException(status_code=404, detail="captura não encontrada")
    return out


@router.put("/captures/{capture_id}/phrases/{i}/view")
def set_view(capture_id: str, i: int, body: ViewBody) -> dict[str, Any]:
    try:
        return proofs.rebake(capture_id, i, body.view, body.lock)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="captura não encontrada") from exc
    except proofs.ProofError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------------
# Vídeos de referência
# --------------------------------------------------------------------------------------------


@router.get("/references")
def list_references() -> list[dict[str, Any]]:
    return references.list_references()


@router.post("/references")
def upload_reference(file: Annotated[UploadFile, File()],
                     title: Annotated[str, Form(max_length=300)] = "") -> dict[str, Any]:
    """Envio do arquivo; a análise começa sozinha (o job volta em `job`)."""
    try:
        ref = references.save_upload(file.file, file.filename or "", title)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = get_runner().submit("reference_shots", {"reference_id": ref["id"]})
    return {**ref, "job": job}


@router.post("/references/link")
def reference_from_link(body: LinkBody) -> dict[str, Any]:
    """Baixa pelo yt-dlp e analisa (job `reference_shots`)."""
    try:
        ref = references.create_from_link(body.url.strip(), body.title or "")
    except BlockedUrl as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return get_runner().submit("reference_shots", {"reference_id": ref["id"]})


@router.get("/references/{ref_id}")
def get_reference(ref_id: str) -> dict[str, Any]:
    out = references.get_reference(ref_id)
    if out is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    return out


@router.delete("/references/{ref_id}")
def delete_reference(ref_id: str) -> dict[str, Any]:
    if not references.delete_reference(ref_id):
        raise HTTPException(status_code=404, detail="referência não encontrada")
    return {"ok": True}


@router.post("/references/{ref_id}/analyze")
def analyze_reference(ref_id: str) -> dict[str, Any]:
    if references.get_reference(ref_id) is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    return get_runner().submit("reference_shots", {"reference_id": ref_id})


@router.post("/references/{ref_id}/style")
def style_from_reference(ref_id: str, body: RefStyleBody | None = None) -> dict[str, Any]:
    """Cria um estilo do usuário a partir das medidas (preset mais próximo + medidas + IA, se houver)."""
    body = body or RefStyleBody()
    ref = references.get_reference(ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    if ref["status"] != "pronto":
        raise HTTPException(status_code=409, detail="Analise o vídeo antes de criar o estilo.")
    try:
        return from_reference.create_from_reference(ref, use_llm=body.use_llm, name=(body.name or "").strip() or None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --------------------------------------------------------------------------------------------
# Miniaturas (teste A/B)
# --------------------------------------------------------------------------------------------


@router.get("/projects/{project_id}/thumbs")
def get_thumbs(project_id: str, lang: str | None = None) -> dict[str, Any]:
    _project(project_id)
    return thumbs.get_state(project_id, lang)


@router.put("/projects/{project_id}/thumbs/spec")
def put_spec(project_id: str, body: SpecBody) -> dict[str, Any]:
    _project(project_id)
    try:
        return thumbs.set_spec(project_id, body.lang, [v.model_dump() for v in body.variants])
    except thumbs.ThumbsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/projects/{project_id}/thumbs")
def make_thumbs(project_id: str, body: LangBody) -> dict[str, Any]:
    _project(project_id)
    lang = body.lang or service.get_doc(project_id).primary_language
    return get_runner().submit("thumbs_variants", {"lang": lang}, project_id)


@router.put("/projects/{project_id}/thumbs/chosen")
def put_chosen(project_id: str, body: ChosenBody) -> dict[str, Any]:
    _project(project_id)
    try:
        return thumbs.choose(project_id, body.lang, body.n)
    except thumbs.ThumbsError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
