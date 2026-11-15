"""Rotas da checagem de fatos do roteiro (ADR 0003, bloco F): afirmações, conferência das fontes e correções.

- `GET /api/projects/{id}/factcheck?lang=`: afirmações, resumo, estado da busca web e o último job.
- `POST /api/projects/{id}/factcheck`: job `factcheck` (`{lang?, segment_ids?}`).
- `PATCH /api/projects/{id}/factcheck/{claim_id}`: edição à mão (veredito, fonte, trecho, correção, nota, status).
- `POST /api/projects/{id}/factcheck/{claim_id}/verify`: reconfere o trecho na página agora.
- `POST /api/projects/{id}/factcheck/{claim_id}/apply`: aplica a correção no roteiro (versão nova).
- `GET /api/projects/{id}/factcheck/sources.md?lang=`: tabela fala → fonte em Markdown.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.db import session_scope
from studio.models import FactClaim
from studio.providers.llm import web_search_status
from studio.workers.factcheck.apply import (
    apply_correction,
    claim_dict,
    get_claim,
    list_claims,
    sources_markdown,
    summary,
)
from studio.workers.factcheck.claims import FactcheckError, clean_url
from studio.workers.factcheck.verify import verify
from studio.workers.jobs import ACTIVE_STATUSES, get_runner, list_jobs

router = APIRouter(prefix="/api", tags=["checagem"])

Verdict = Literal["ok", "corrigir", "sem_fonte", "boato"]


class RunBody(BaseModel):
    lang: str | None = Field(default=None, max_length=20)
    segment_ids: list[str] | None = Field(default=None, max_length=500)


class ClaimPatch(BaseModel):
    verdict: Verdict | None = None
    source_title: str | None = Field(default=None, max_length=300)
    url: str | None = Field(default=None, max_length=2000)
    quote: str | None = Field(default=None, max_length=2000)
    correction: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=2000)
    status: Literal["aberta", "ignorada"] | None = None


def _http(exc: FactcheckError) -> HTTPException:
    return HTTPException(status_code=exc.status, detail=exc.message)


def _doc(project_id: str) -> ProjectDoc:
    try:
        return service.get_doc(project_id)
    except service.ProjectNotFound as exc:
        raise HTTPException(status_code=404, detail="Projeto não encontrado") from exc


def _lang(doc: ProjectDoc, lang: str | None) -> str:
    lang = lang or doc.primary_language
    if lang not in doc.languages:
        raise HTTPException(status_code=400, detail=f"o projeto não tem o idioma {lang}")
    return lang


@router.get("/projects/{project_id}/factcheck")
def get_factcheck(project_id: str, lang: str | None = None) -> dict[str, Any]:
    doc = _doc(project_id)
    lang = _lang(doc, lang)
    available, reason = web_search_status()
    jobs = [j for j in list_jobs(project_id, limit=100) if j["kind"] == "factcheck"]
    active = next((j for j in jobs if j["status"] in ACTIVE_STATUSES), None)
    settings = service.get_project_row(project_id).get("settings") or {}
    state = (settings.get("factcheck") or {}).get(lang) or {}
    return {
        "lang": lang,
        "claims": list_claims(project_id, lang, doc),
        "summary": summary(project_id, lang, doc),
        "web": {"available": available, "reason": reason},
        "last_job": state.get("last_job"),
        "checked_at": state.get("at"),
        "last_web": state.get("web"),
        "job": active or (jobs[0] if jobs else None),
    }


@router.post("/projects/{project_id}/factcheck")
def start_factcheck(project_id: str, body: RunBody | None = None) -> dict[str, Any]:
    doc = _doc(project_id)
    body = body or RunBody()
    lang = _lang(doc, body.lang)
    if not any(s.text.get(lang, "").strip() for s in doc.segments):
        raise HTTPException(status_code=409, detail="não há texto do roteiro para checar neste idioma")
    payload: dict[str, Any] = {"lang": lang}
    if body.segment_ids:
        known = {s.id for s in doc.segments}
        payload["segment_ids"] = [i for i in body.segment_ids if i in known]
        if not payload["segment_ids"]:
            raise HTTPException(status_code=400, detail="nenhum dos segmentos pedidos existe")
    return get_runner().submit("factcheck", payload, project_id)


@router.patch("/projects/{project_id}/factcheck/{claim_id}")
def update_claim(project_id: str, claim_id: str, body: ClaimPatch) -> dict[str, Any]:
    doc = _doc(project_id)
    changes = {k: (v.strip() if isinstance(v, str) else v) for k, v in body.model_dump(exclude_none=True).items()}
    if changes.get("url") and not clean_url(changes["url"]):
        raise HTTPException(status_code=400, detail="o link da fonte precisa começar com http:// ou https://")
    with session_scope() as s:
        row = s.get(FactClaim, claim_id)
        if row is None or row.project_id != project_id:
            raise HTTPException(status_code=404, detail="Afirmação não encontrada")
        if row.status == "aplicada" and "status" in changes:
            raise HTTPException(status_code=409, detail="a correção já foi aplicada no roteiro")
        relink = any(k in changes and changes[k] != getattr(row, k) for k in ("url", "quote"))
        for key, value in changes.items():
            setattr(row, key, value)
        if relink:  # trecho ou link novo: a conferência anterior não vale mais
            row.quote_found = None
            row.check_note = "editado: reconfira o trecho"
        s.flush()
        return claim_dict(row, doc)


@router.post("/projects/{project_id}/factcheck/{claim_id}/verify")
def verify_claim(project_id: str, claim_id: str) -> dict[str, Any]:
    doc = _doc(project_id)
    try:
        row = get_claim(project_id, claim_id)
    except FactcheckError as exc:
        raise _http(exc) from exc
    found, note = verify(row.url, row.quote)
    with session_scope() as s:
        target = s.get(FactClaim, claim_id)
        if target is None:
            raise HTTPException(status_code=404, detail="Afirmação não encontrada")
        target.quote_found = found
        target.check_note = note
        target.checked_at = datetime.now(UTC)
        s.flush()
        return claim_dict(target, doc)


@router.post("/projects/{project_id}/factcheck/{claim_id}/apply")
def apply_claim(project_id: str, claim_id: str) -> dict[str, Any]:
    _doc(project_id)
    try:
        return apply_correction(project_id, claim_id)
    except FactcheckError as exc:
        raise _http(exc) from exc


@router.get("/projects/{project_id}/factcheck/sources.md", response_class=PlainTextResponse)
def factcheck_sources(project_id: str, lang: str | None = None) -> PlainTextResponse:
    doc = _doc(project_id)
    lang = _lang(doc, lang)
    return PlainTextResponse(sources_markdown(project_id, lang), media_type="text/markdown; charset=utf-8")
