"""Afirmações da checagem: leitura, correção aplicada no roteiro, resumo e a tabela fala → fonte.

A correção passa pela operação de sempre (`UpdateSegmentText` + `apply_ops` + `service.commit`): vira uma versão nova
do roteiro, com desfazer. Nada de corrigir "só a lista": a regra da skill `estudio-checagem` é corrigir o roteiro.
"""

from __future__ import annotations

from typing import Any

from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.project.ops import OpError, UpdateSegmentText, apply_ops
from studio.db import session_scope
from studio.models import FactClaim, Project
from studio.workers.factcheck.claims import VERDICTS, FactcheckError, segment_hash

OPEN_VERDICTS = ("corrigir", "sem_fonte", "boato")
VERDICT_LABELS = {"ok": "ok", "corrigir": "corrigir", "sem_fonte": "sem fonte", "boato": "boato"}


def _iso(value: Any) -> str | None:
    return value.isoformat() if value else None


def claim_dict(row: FactClaim, doc: ProjectDoc | None = None) -> dict[str, Any]:
    out = {
        "id": row.id, "project_id": row.project_id, "run_id": row.run_id, "segment_id": row.segment_id,
        "scene_id": row.scene_id, "lang": row.lang, "segment_hash": row.segment_hash, "excerpt": row.excerpt,
        "claim": row.claim, "verdict": row.verdict, "source_title": row.source_title, "url": row.url,
        "quote": row.quote, "quote_found": row.quote_found, "check_note": row.check_note,
        "correction": row.correction, "note": row.note, "status": row.status, "checked_at": _iso(row.checked_at),
        "created_at": _iso(row.created_at), "updated_at": _iso(row.updated_at),
    }  # fmt: skip
    if doc is not None:
        out["stale"] = is_stale(row, doc)
    return out


def _segment_text(doc: ProjectDoc, segment_id: str, lang: str) -> str | None:
    seg = next((s for s in doc.segments if s.id == segment_id), None)
    return None if seg is None else seg.text.get(lang, "")


def is_stale(row: FactClaim, doc: ProjectDoc) -> bool:
    """O texto do segmento mudou desde a checagem (ou o segmento foi apagado)."""
    text = _segment_text(doc, row.segment_id, row.lang)
    return text is None or segment_hash(text) != row.segment_hash


def _order(doc: ProjectDoc) -> dict[str, int]:
    return {s.id: i for i, s in enumerate(doc.segments)}


def list_claims(project_id: str, lang: str, doc: ProjectDoc | None = None) -> list[dict[str, Any]]:
    doc = doc or service.get_doc(project_id)
    order = _order(doc)
    with session_scope() as s:
        rows = s.query(FactClaim).filter_by(project_id=project_id, lang=lang).order_by(FactClaim.created_at).all()
        out = [claim_dict(r, doc) for r in rows]
    return sorted(out, key=lambda c: order.get(c["segment_id"], len(order)))


def get_claim(project_id: str, claim_id: str) -> FactClaim:
    """Linha solta da sessão (para ler); 404 se não é deste projeto."""
    with session_scope() as s:
        row = s.get(FactClaim, claim_id)
        if row is None or row.project_id != project_id:
            raise FactcheckError(404, "Afirmação não encontrada")
        s.expunge(row)
        return row


def summary(project_id: str, lang: str, doc: ProjectDoc | None = None) -> dict[str, Any]:
    """Contagem por veredito, abertas, desatualizadas, citações não achadas e segmentos sem checagem."""
    doc = doc or service.get_doc(project_id)
    with session_scope() as s:
        rows = s.query(FactClaim).filter_by(project_id=project_id, lang=lang).all()
        project = s.get(Project, project_id)
        checked = dict((((project.settings or {}).get("factcheck") or {}).get(lang) or {}).get("checked") or {}) \
            if project else {}
        active = [r for r in rows if r.status != "ignorada"]
        out: dict[str, Any] = {v: sum(1 for r in active if r.verdict == v) for v in VERDICTS}
        out["open"] = sum(1 for r in rows if r.status == "aberta" and r.verdict in OPEN_VERDICTS)
        out["stale"] = sum(1 for r in active if is_stale(r, doc))
        out["quote_missing"] = sum(1 for r in active if r.quote_found is False)
        out["applied"] = sum(1 for r in rows if r.status == "aplicada")
        out["ignored"] = len(rows) - len(active)
        open_by_segment: dict[str, int] = {}
        for r in rows:
            if r.status == "aberta" and r.verdict in OPEN_VERDICTS:
                open_by_segment[r.segment_id] = open_by_segment.get(r.segment_id, 0) + 1
    segs = [seg for seg in doc.segments if seg.text.get(lang, "").strip()]
    unchecked = [seg.id for seg in segs if checked.get(seg.id) != segment_hash(seg.text.get(lang, ""))]
    out["unchecked"] = len(unchecked)
    out["unchecked_ids"] = unchecked
    out["segments"] = len(segs)
    out["open_by_segment"] = open_by_segment
    return out


def apply_correction(project_id: str, claim_id: str) -> dict[str, Any]:
    """Troca o trecho pela correção no roteiro (versão nova) e marca a afirmação como aplicada."""
    row = get_claim(project_id, claim_id)
    if row.status == "aplicada":
        raise FactcheckError(409, "esta correção já foi aplicada")
    correction = row.correction.strip()
    if not correction:
        raise FactcheckError(409, "a afirmação não tem correção para aplicar")
    doc = service.get_doc(project_id)
    seg = next((s for s in doc.segments if s.id == row.segment_id), None)
    if seg is None:
        raise FactcheckError(409, "o segmento foi apagado depois da checagem")
    if seg.locked:
        raise FactcheckError(409, "segmento travado")
    text = seg.text.get(row.lang, "")
    if not row.excerpt or row.excerpt not in text:
        raise FactcheckError(409, "o trecho mudou desde a checagem: edite à mão")
    new_text = text.replace(row.excerpt, correction, 1)
    try:
        new_doc = apply_ops(doc, [UpdateSegmentText(segment_id=seg.id, lang=row.lang, text=new_text)])
    except OpError as exc:
        raise FactcheckError(409, str(exc)) from exc
    version = service.commit(project_id, new_doc, f"Checagem: {row.claim[:60]}", author="factcheck")
    final_text = new_doc.segment(seg.id).text.get(row.lang, "")
    old_hash, new_hash = segment_hash(text), segment_hash(final_text)
    with session_scope() as s:
        target = s.get(FactClaim, claim_id)
        assert target is not None
        target.status = "aplicada"
        # As outras afirmações do segmento continuam valendo se o trecho delas ficou intacto.
        siblings = s.query(FactClaim).filter_by(project_id=project_id, lang=row.lang, segment_id=seg.id).all()
        for other in siblings:
            if other.segment_hash == old_hash and (other.id == claim_id or not other.excerpt
                                                   or other.excerpt in final_text):
                other.segment_hash = new_hash
        project = s.get(Project, project_id)
        if project is not None:
            settings = dict(project.settings or {})
            factcheck = dict(settings.get("factcheck") or {})
            current = dict(factcheck.get(row.lang) or {})
            checked = dict(current.get("checked") or {})
            if checked.get(seg.id) == old_hash:
                checked[seg.id] = new_hash
                current["checked"] = checked
                factcheck[row.lang] = current
                settings["factcheck"] = factcheck
                project.settings = settings
        s.flush()
        data = claim_dict(target, new_doc)
    return {"claim": data, "version": version}


def _cell(text: str) -> str:
    return " ".join((text or "").replace("|", "\\|").split()) or "-"


def sources_markdown(project_id: str, lang: str) -> str:
    """Tabela fala → fonte (como o `roteiro/fontes-<slug>.md` do estúdio antigo)."""
    doc = service.get_doc(project_id)
    claims = [c for c in list_claims(project_id, lang, doc) if c["status"] != "ignorada"]
    index = {s.id: i for i, s in enumerate(doc.segments, 1)}
    lines = [f"# Fontes: {doc.title}", "", f"Idioma: {lang}", ""]
    if claims:
        lines += ["| Trecho | Fala | Afirmação | Veredito | Fonte | Citação |", "|---|---|---|---|---|---|"]
        for c in claims:
            source = c["source_title"] or c["url"] or "-"
            if c["url"]:
                source = f"[{_cell(c['source_title'] or c['url'])}]({c['url']})"
            quote = _cell(c["quote"]) if c["quote"] else "-"
            if c["quote"] and c["quote_found"] is True:
                quote += " (achada)"
            elif c["quote"] and c["quote_found"] is False:
                quote += " (NÃO achada)"
            verdict = VERDICT_LABELS.get(c["verdict"], c["verdict"])
            if c["status"] == "aplicada":
                verdict += " (corrigido)"
            lines.append(f"| {index.get(c['segment_id'], '?')} | {_cell(c['excerpt'])} | {_cell(c['claim'])} | "
                         f"{verdict} | {source} | {quote} |")
    else:
        lines.append("Nenhuma afirmação checada ainda.")
    if doc.sources:
        lines += ["", "## Fontes do roteiro", ""]
        for src in doc.sources:
            title = f"[{src.title}]({src.url})" if src.url else src.title
            lines.append(f"- {title}" + (f" ({src.note})" if src.note else ""))
    return "\n".join(lines) + "\n"
