"""Lições aplicadas que entram nos pedidos ao LLM (roteiro e metadados).

Só entram as lições com status "aplicada" cujo escopo vale para o projeto: `global`, `canal:<canal do projeto>`,
`estilo:<estilo do projeto>` e `formato:<short|long do estilo>`. As mais recentes primeiro, até `MAX_LESSONS` e
`MAX_CHARS` caracteres no total, para não inflar o pedido. A lista usada fica gravada no projeto
(`settings["lessons_used"][<etapa>]`) para conferir depois se a lição ajudou.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_

from studio.db import session_scope
from studio.models import Lesson

MAX_LESSONS = 8
MAX_CHARS = 1500
# Uma lição muito longa é cortada aqui (com "…") para não ocupar o orçamento sozinha.
MAX_LESSON_CHARS = 400


def project_scopes(channel_id: str | None, style_id: str | None, format_type: str | None) -> list[str]:
    """Escopos que valem para o projeto, do mais geral ao mais específico."""
    scopes = ["global"]
    if channel_id:
        scopes.append(f"canal:{channel_id}")
    if style_id:
        scopes.append(f"estilo:{style_id}")
    if format_type in ("short", "long"):
        scopes.append(f"formato:{format_type}")
    return scopes


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_LESSON_CHARS else text[: MAX_LESSON_CHARS - 1].rstrip() + "…"


def applied_lessons(channel_id: str | None, style_id: str | None, format_type: str | None, *,
                    limit: int = MAX_LESSONS, max_chars: int = MAX_CHARS) -> list[dict[str, Any]]:
    """Lições aplicadas do projeto, mais recentes primeiro (pela decisão), dentro do limite e do orçamento.

    Uma lição que estoura o orçamento é pulada e a seguinte, se couber, entra.
    """
    scopes = project_scopes(channel_id, style_id, format_type)
    with session_scope() as s:
        rows = (
            s.query(Lesson)
            .filter(Lesson.status == "aplicada", or_(*(Lesson.scope == sc for sc in scopes)))
            .all()
        )
        items = [
            {"id": r.id, "scope": r.scope, "text": _clip(r.text or ""),
             "_when": r.decided_at or r.created_at or datetime.min.replace(tzinfo=UTC)}
            for r in rows if (r.text or "").strip()
        ]
    items.sort(key=lambda i: (i["_when"], i["id"]), reverse=True)
    chosen: list[dict[str, Any]] = []
    used = 0
    for item in items:
        if len(chosen) >= limit:
            break
        size = len(item["text"])
        if used + size > max_chars:
            continue
        used += size
        chosen.append({"id": item["id"], "scope": item["scope"], "text": item["text"]})
    return chosen


def lessons_key(lessons: list[dict[str, Any]]) -> str:
    """Impressão digital da lista (ids e textos): muda quando a lista muda, e o pedido ao LLM também."""
    if not lessons:
        return ""
    raw = json.dumps([[item["id"], item["text"]] for item in lessons], ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def lessons_block(lessons: list[dict[str, Any]], *, stage: str = "roteiro") -> str:
    """Trecho do pedido com as lições (vazio sem lição)."""
    if not lessons:
        return ""
    lines = "\n".join(f"- {item['text']}" for item in lessons)
    return (f"Lições aprendidas com vídeos anteriores do canal (siga as que valem para o {stage}; "
            f"ignore as que não se aplicam a ele):\n{lines}")


def usage_record(lessons: list[dict[str, Any]], *, version: int | None = None) -> dict[str, Any]:
    """O que fica gravado no projeto sobre as lições usadas numa etapa."""
    return {
        "ids": [item["id"] for item in lessons],
        "items": [dict(item) for item in lessons],
        "key": lessons_key(lessons),
        "version": version,
        "at": datetime.now(UTC).isoformat(),
    }
