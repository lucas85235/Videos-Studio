"""Banco de ideias de vídeo (bloco D, ADR 0002). Origem: `cmd_ideia_add`, `cmd_ideia_list` e `decidir_ideias` do
`estudio/estudio.py` do canais.

Regras trazidas de lá:
- nota de 0 a 10 (ou vazia);
- título repetido no mesmo canal (comparado pela forma normalizada: minúsculas, sem acento, só letras e números) não
  cria outra ideia, a não ser com `force`;
- lista em ordem de nota (sem nota por último) e, na mesma nota, a mais nova primeiro;
- decidir em lote grava status, motivo (só quando vem) e a hora da decisão.
No Vulto a ideia vira projeto (`to_project`) e fica "feita" quando o projeto tem publicação enviada, agendada ou postada.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any

from studio.core.project import service
from studio.core.styles.library import DEFAULT_STYLE_ID, StyleNotFound, get_style, list_styles
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Idea, Project, Publication, utcnow

STATUSES = ("nova", "aprovada", "rejeitada", "em_producao", "feita", "descartada")
FORMATS = ("", "short", "long")
DONE_PUBLICATION = ("uploaded", "scheduled", "published")
EDITABLE = ("channel_id", "title", "angle", "format", "origin", "evidence", "search", "score", "status", "reason")


class IdeaError(ValueError):
    """Pedido inválido (vira 400/422 na API)."""


class IdeaNotFound(LookupError):
    pass


class DuplicateIdea(Exception):
    """Já existe ideia com o mesmo título no canal (vira 409 com o id da existente)."""

    def __init__(self, idea: dict[str, Any]):
        super().__init__(f'já existe a ideia "{idea["title"]}" ({idea["status"]}) neste canal')
        self.idea = idea


def norm(text: str) -> str:
    """Forma de comparar títulos (igual ao `norm` do canais): minúsculas, sem acento, só letras e números."""
    s = unicodedata.normalize("NFD", str(text).lower())
    return re.sub(r"[^a-z0-9]+", " ", "".join(c for c in s if unicodedata.category(c) != "Mn")).strip()


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def idea_dict(row: Idea) -> dict[str, Any]:
    return {
        "id": row.id,
        "channel_id": row.channel_id,
        "title": row.title,
        "angle": row.angle or "",
        "format": row.format or "",
        "origin": row.origin or "manual",
        "evidence": row.evidence or "",
        "search": row.search or "",
        "score": row.score,
        "status": row.status,
        "reason": row.reason or "",
        "project_id": row.project_id,
        "created_at": _iso(row.created_at),
        "decided_at": _iso(row.decided_at),
    }


def _check_score(score: float | None) -> None:
    if score is not None and not 0 <= score <= 10:
        raise IdeaError("nota vai de 0 a 10")


def _check_fields(data: dict[str, Any]) -> None:
    if "score" in data:
        _check_score(data["score"])
    if "status" in data and data["status"] not in STATUSES:
        raise IdeaError(f"status inválido: {data['status']} (use {', '.join(STATUSES)})")
    if "format" in data and (data["format"] or "") not in FORMATS:
        raise IdeaError("formato inválido (use short, long ou vazio)")
    if "title" in data and not str(data["title"] or "").strip():
        raise IdeaError("a ideia precisa de um título")


def _check_channel(s, channel_id: str | None) -> None:
    if channel_id and s.get(Channel, channel_id) is None:
        raise IdeaError(f"canal não encontrado: {channel_id}")


def _duplicate(s, channel_id: str | None, title: str, skip_id: str | None = None) -> Idea | None:
    key = norm(title)
    q = s.query(Idea).filter(Idea.channel_id.is_(None) if channel_id is None else Idea.channel_id == channel_id)
    for row in q.all():
        if row.id != skip_id and norm(row.title) == key:
            return row
    return None


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    """Só os campos editáveis; canal vazio ("") vira "sem canal" (senão violaria a chave estrangeira)."""
    data = {k: v for k, v in data.items() if k in EDITABLE}
    if "channel_id" in data and not (data["channel_id"] or "").strip():
        data["channel_id"] = None
    return data


def create(data: dict[str, Any], force: bool = False) -> dict[str, Any]:
    data = _clean(data)
    data.setdefault("status", "nova")
    _check_fields({"title": data.get("title", ""), **data})
    title = str(data["title"]).strip()
    with session_scope() as s:
        _check_channel(s, data.get("channel_id"))
        if not force and (dup := _duplicate(s, data.get("channel_id"), title)):
            raise DuplicateIdea(idea_dict(dup))
        row = Idea(id=new_id("ide"), **{**data, "title": title, "format": data.get("format") or ""})
        s.add(row)
        s.flush()
        return idea_dict(row)


def _refresh_done(s, rows: list[Idea]) -> None:
    """Ideia em produção cujo projeto já tem publicação enviada, agendada ou postada passa a "feita". Ideia cujo
    projeto foi apagado volta a "aprovada" e sem projeto, para poder virar projeto de novo."""
    linked = [r for r in rows if r.project_id]
    if linked:
        alive = {pid for (pid,) in s.query(Project.id).filter(Project.id.in_({r.project_id for r in linked}))}
        for r in linked:
            if r.project_id not in alive:
                r.project_id = None
                if r.status == "em_producao":
                    r.status = "aprovada"
                    r.decided_at = utcnow()
    waiting = {r.project_id: r for r in rows if r.status == "em_producao" and r.project_id}
    if not waiting:
        return
    published = {
        pid for (pid,) in s.query(Publication.project_id)
        .filter(Publication.project_id.in_(list(waiting)), Publication.status.in_(DONE_PUBLICATION)).distinct()
    }
    for pid in published:
        waiting[pid].status = "feita"
        waiting[pid].decided_at = waiting[pid].decided_at or utcnow()


def list_ideas(channel_id: str | None = None, status: str | None = None, min_score: float | None = None,
               limit: int = 500) -> list[dict[str, Any]]:
    with session_scope() as s:
        _refresh_done(s, s.query(Idea).filter(Idea.project_id.is_not(None)).all())
        q = s.query(Idea)
        if channel_id:
            q = q.filter(Idea.channel_id == channel_id)
        if status:
            q = q.filter(Idea.status == status)
        if min_score is not None:
            q = q.filter(Idea.score >= min_score)
        rows = q.all()
        # Nota maior primeiro (sem nota por último, como COALESCE(nota, -1) DESC); empate: a mais nova primeiro.
        rows.sort(key=lambda r: (-1 if r.score is None else r.score, r.created_at), reverse=True)
        return [idea_dict(r) for r in rows[:limit]]


def get(idea_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Idea, idea_id)
        if row is None:
            raise IdeaNotFound(idea_id)
        _refresh_done(s, [row])
        return idea_dict(row)


def update(idea_id: str, data: dict[str, Any], force: bool = False) -> dict[str, Any]:
    data = _clean(data)
    _check_fields(data)
    with session_scope() as s:
        row = s.get(Idea, idea_id)
        if row is None:
            raise IdeaNotFound(idea_id)
        if "channel_id" in data:
            _check_channel(s, data["channel_id"])
        if "title" in data:
            data["title"] = str(data["title"]).strip()
        channel_id = data.get("channel_id", row.channel_id)
        title = data.get("title", row.title)
        if ("title" in data or "channel_id" in data) and not force and (dup := _duplicate(s, channel_id, title, row.id)):
            raise DuplicateIdea(idea_dict(dup))
        if "status" in data and data["status"] != row.status:
            row.decided_at = utcnow()
        for key, value in data.items():
            setattr(row, key, value if value is not None or key in ("score", "channel_id") else "")
        return idea_dict(row)


def delete(idea_id: str) -> None:
    with session_scope() as s:
        row = s.get(Idea, idea_id)
        if row is None:
            raise IdeaNotFound(idea_id)
        s.delete(row)


def decide(ids: list[str], status: str, reason: str | None = None) -> list[dict[str, Any]]:
    """Decide várias ideias de uma vez (aprovar, rejeitar, descartar...). Motivo vazio mantém o anterior."""
    if status not in STATUSES:
        raise IdeaError(f"status inválido: {status} (use {', '.join(STATUSES)})")
    if not ids:
        raise IdeaError("diga quais ideias")
    now = utcnow()
    with session_scope() as s:
        rows = []
        for idea_id in ids:
            row = s.get(Idea, idea_id)
            if row is None:
                raise IdeaNotFound(idea_id)
            rows.append(row)
        for row in rows:
            row.status = status
            if reason:
                row.reason = reason
            row.decided_at = now
        return [idea_dict(r) for r in rows]


def _style_format(style_id: str) -> str | None:
    try:
        return get_style(style_id).format.type
    except (StyleNotFound, ValueError):
        return None


def _default_style(channel_id: str | None, fmt: str = "") -> str:
    """Estilo do projeto quando a ideia vira projeto sem estilo escolhido: o padrão do canal se o formato da ideia
    (short/long) bate com ele; senão o primeiro estilo da biblioteca desse formato; senão o padrão de sempre."""
    channel_style = None
    if channel_id:
        with session_scope() as s:
            ch = s.get(Channel, channel_id)
            if ch is not None and ch.default_style_id:
                channel_style = ch.default_style_id
    fallback = channel_style or DEFAULT_STYLE_ID
    if not fmt or _style_format(fallback) == fmt:
        return fallback
    for item in list_styles():
        if (item["style"].get("format") or {}).get("type", "short") == fmt:
            return item["style"]["id"]
    return fallback


def to_project(idea_id: str, style_id: str | None = None, languages: list[str] | None = None,
               voice: dict[str, str] | None = None) -> dict[str, Any]:
    """Cria o projeto da ideia (título e tema = título; briefing = ângulo + evidência) e marca a ideia em produção."""
    idea = get(idea_id)
    if idea["project_id"]:
        try:
            service.get_project_row(idea["project_id"])
            raise IdeaError("esta ideia já virou projeto")
        except service.ProjectNotFound:
            pass  # o projeto foi apagado: pode virar projeto de novo
    brief = "\n\n".join(p for p in (
        idea["angle"].strip() and f"Ângulo: {idea['angle'].strip()}",
        idea["evidence"].strip() and f"Evidência: {idea['evidence'].strip()}",
    ) if p)
    doc = service.create_project(
        title=idea["title"][:200],
        topic=idea["title"],
        brief=brief,
        channel_id=idea["channel_id"],
        style_id=style_id or _default_style(idea["channel_id"], idea["format"]),
        languages=languages or None,
        voice=voice,
    )
    with session_scope() as s:
        row = s.get(Idea, idea_id)
        assert row is not None
        row.status = "em_producao"
        row.project_id = doc.id
        row.decided_at = utcnow()
        out = idea_dict(row)
    return {"idea": out, "project": service.get_project_row(doc.id)}
