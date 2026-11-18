"""Rotas do visual (ADR 0003, bloco I): provas na tela, vídeos de referência e miniaturas para o teste A/B.

- `GET /api/visual/capabilities`: navegador (captura) e yt-dlp (download por link).
- Provas: `POST /api/projects/{id}/scenes/{scene_id}/proof` (job `page_capture`), `GET /api/projects/{id}/captures`,
  `GET /api/captures/{id}`, `PUT /api/captures/{id}/phrases/{i}/view`.
- Referências: `GET|POST /api/references`, `POST /api/references/link`, `GET|PATCH|DELETE /api/references/{id}`,
  `POST /api/references/{id}/analyze` (job `reference_shots`), `POST /api/references/{id}/style` (cria um estilo do
  usuário a partir das medidas), `POST|GET /api/references/{id}/profile` (perfil de edição, ADR 0004 §3.2),
  `POST /api/references/{id}/project` (estilo + projeto novo de uma vez) e `GET /api/references/{id}/adherence`
  (aderência ao perfil dos projetos que usam um estilo feito dela, ADR 0004 §3.11).
- Miniaturas: `GET /api/projects/{id}/thumbs`, `PUT .../thumbs/spec`, `POST .../thumbs` (job `thumbs_variants`),
  `PUT .../thumbs/chosen`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator

from studio.core.project import service
from studio.core.styles import from_reference
from studio.media import page_capture, reference_video
from studio.media.safe_http import BlockedUrl
from studio.workers.jobs import get_runner
from studio.workers.visual import proofs, references, thumbs
from studio.workers.visual.tasks import submit_profile

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


class RenameBody(BaseModel):
    title: str = Field(min_length=1, max_length=300)


class RefStyleBody(BaseModel):
    name: str | None = Field(default=None, max_length=120)  # = maxLength do nome no editor de estilos
    use_llm: bool = True


class ProfileBody(BaseModel):
    deep: bool = False


class RefProjectBody(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    """O tema do vídeo novo (obrigatório): sem ele, o roteiro sairia sobre o título da referência (um remake)."""
    title: str = Field(default="", max_length=200)
    """Vazio: o tema. O título da referência mandado de volta sem mudar vira o título limpo (sem o id do downloader)."""
    style_name: str | None = Field(default=None, max_length=120)
    style_id: str | None = Field(default=None, max_length=80)
    """Um estilo já feito desta referência (`grammar.profile_refs`): usa ele em vez de criar outro igual."""
    channel_id: str | None = Field(default=None, max_length=40)
    use_llm: bool = True

    @field_validator("topic")
    @classmethod
    def _topic(cls, value: str) -> str:
        clean = " ".join(value.split())
        if not clean:
            raise ValueError("Diga o tema do vídeo novo: da referência só vem a fórmula.")
        return clean


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


@router.patch("/references/{ref_id}")
def rename_reference(ref_id: str, body: RenameBody) -> dict[str, Any]:
    """Renomeia a referência (só o título)."""
    try:
        return references.rename_reference(ref_id, body.title)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="referência não encontrada") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


@router.post("/references/{ref_id}/profile")
def measure_profile(ref_id: str, body: ProfileBody | None = None) -> dict[str, Any]:
    """Mede o perfil de edição (job `reference_profile`): rápido (cortes, movimento, áudio) ou profundo (mais fala e
    visão)."""
    body = body or ProfileBody()
    ref = references.get_reference(ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    if ref["status"] != "pronto":
        raise HTTPException(status_code=409, detail="Analise o vídeo antes de medir o perfil.")
    try:
        job_row, _created = submit_profile(ref_id, body.deep)  # um job ativo da mesma referência é reaproveitado
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return job_row


@router.get("/references/{ref_id}/profile")
def get_profile(ref_id: str) -> dict[str, Any]:
    """O perfil medido (`profile: null` enquanto não houver) e o que ficou de fora na medição (`notes`)."""
    ref = references.get_reference(ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    return {"reference_id": ref_id, "profile": ref["stats"].get("profile"),
            "notes": ref["stats"].get("profile_notes") or {}}


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


def _ready_reference(ref_id: str, action: str) -> dict[str, Any]:
    ref = references.get_reference(ref_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    if ref["status"] != "pronto":
        raise HTTPException(status_code=409, detail=f"Analise o vídeo antes de {action}.")
    return ref


NO_PROFILE = "Meça o perfil de edição antes de criar o projeto: sem ele, o estilo sai sem a gramática da referência."


def reference_styles(ref_id: str) -> dict[str, str]:
    """Os estilos do usuário feitos desta referência (`grammar.profile_refs`): {id: nome}."""
    from studio.core.styles.library import StyleNotFound, get_style
    from studio.core.styles.schema import grammar_for
    from studio.db import session_scope
    from studio.models import Style

    with session_scope() as s:
        rows = [(r.id, r.name) for r in s.query(Style.id, Style.name).all()]
    out: dict[str, str] = {}
    for style_id, name in rows:
        try:
            if ref_id in grammar_for(get_style(style_id)).profile_refs:
                out[style_id] = name
        except (StyleNotFound, ValueError):
            continue
    return out


@router.post("/references/{ref_id}/project")
def project_from_reference(ref_id: str, body: RefProjectBody) -> dict[str, Any]:
    """"Novo projeto a partir desta referência": um projeto novo, com o tema do usuário, no estilo feito dela. Com
    `style_id` (um estilo já feito desta referência), usa esse; sem ele, cria o estilo (como `POST .../style`).
    409 sem o perfil de edição medido: o estilo sairia sem a gramática e as fontes da referência."""
    ref = _ready_reference(ref_id, "criar o projeto")
    if from_reference.profile_of(ref.get("stats") or {}) is None:
        raise HTTPException(status_code=409, detail=NO_PROFILE)
    suggestions: list[Any] = []
    if body.style_id:
        styles = reference_styles(ref_id)
        if body.style_id not in styles:
            raise HTTPException(status_code=422, detail="Esse estilo não foi feito desta referência.")
        style_id, style_name = body.style_id, styles[body.style_id]
    else:
        try:
            made = from_reference.create_from_reference(ref, use_llm=body.use_llm,
                                                        name=(body.style_name or "").strip() or None)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        style_id, style_name = made["style"]["id"], made["style"]["name"]
        suggestions = made.get("suggestions", [])
    title = " ".join(body.title.split())
    if title and title == " ".join(str(ref.get("title") or "").split()):
        title = from_reference.reference_title(ref)  # o nome cru do downloader (com o id do vídeo) sai limpo
    title = (title or body.topic)[:200].strip()
    try:
        doc = service.create_project(title=title, style_id=style_id, topic=body.topic, channel_id=body.channel_id or None)
    except Exception as exc:  # noqa: BLE001 - o estilo fica em Meus estilos; o projeto se cria à mão
        raise HTTPException(status_code=422,
                            detail=f"O estilo “{style_name}” está pronto, mas o projeto não foi criado: {exc}") from exc
    return {"project": {"id": doc.id, "title": doc.title, "style_id": style_id},
            "style": {"id": style_id, "name": style_name}, "suggestions": suggestions}


MAX_ADHERENCE_PROJECTS = 12


@router.get("/references/{ref_id}/adherence")
def reference_adherence(ref_id: str) -> dict[str, Any]:
    """Os projetos que usam um estilo feito desta referência (`grammar.profile_refs`) com a aderência ao perfil e o
    índice "cara de IA" (a medida de `GET /projects/{id}/edit-metrics`, só leitura). Os mais recentes primeiro."""
    from studio.db import session_scope
    from studio.models import Project
    from studio.workers.finalcheck.profile_check import project_metrics

    if references.get_reference(ref_id) is None:
        raise HTTPException(status_code=404, detail="referência não encontrada")
    with session_scope() as s:
        projects = [(p.id, p.title, p.style_id) for p in s.query(Project.id, Project.title, Project.style_id)
                    .order_by(Project.updated_at.desc()).all()]
    styles = reference_styles(ref_id)
    out: list[dict[str, Any]] = []
    for project_id, title, style_id in projects:
        if style_id not in styles:
            continue
        if len(out) >= MAX_ADHERENCE_PROJECTS:
            break
        entry: dict[str, Any] = {"id": project_id, "title": title, "style_id": style_id,
                                 "style_name": styles[style_id], "adherence": None, "index": None, "source": None}
        try:
            data = project_metrics(project_id)
            adherence = data.get("adherence") or {}
            entry.update({"adherence": adherence.get("score"), "index": data["index"]["score"],
                          "band": data["index"]["band"], "source": data["source"]})
        except Exception as exc:  # noqa: BLE001 - projeto sem cenas ainda: fica sem nota
            entry["error"] = str(exc)[:200]
        out.append(entry)
    return {"reference_id": ref_id, "styles": [{"id": k, "name": v} for k, v in styles.items()], "projects": out}


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
