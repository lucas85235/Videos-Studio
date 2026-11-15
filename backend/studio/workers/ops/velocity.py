"""Onde vai o tempo (origem: `velocidade` e `sugestoes` de estudio/melhoria.py do canais).

Sem tabela de execuções: o tempo sai dos jobs (`Job.finished_at - Job.started_at`, só os que têm as duas datas) e o
custo, de `CostEntry.job_id`. Por etapa (tipo de job): quantas vezes rodou, tempo total e médio, parte do tempo do
período, falhas, projetos distintos, execuções por projeto, tempo no período anterior (mesmo tamanho) e custo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func

from studio.db import session_scope
from studio.models import CostEntry, Job

REWORK = 1.5  # execuções por projeto a partir das quais uma etapa conta como retrabalho (REFAZ)
REWORK_MIN_PROJECTS = 3  # ... em pelo menos 3 projetos
BOTTLENECK = 0.3  # a etapa que mais leva tempo vira gargalo com 30% ou mais do período

KIND_LABELS = {
    "script": "Roteiro",
    "storyboard": "Storyboard",
    "translate": "Tradução",
    "metadata": "Metadados",
    "preview": "Prévia",
    "render": "Render final",
    "autopilot": "Piloto automático",
    "publish_youtube": "Envio ao YouTube",
    "metrics_collect": "Coleta de métricas",
    "shorts_suggest": "Sugestão de Shorts",
    "routine_morning": "Rotina da manhã",
    "backup": "Cópia do banco",
}


def label(kind: str) -> str:
    return KIND_LABELS.get(kind, kind)


def dur(seconds: float) -> str:
    """Duração curta em português: "45 s", "3 min 20 s", "1 h 05 min"."""
    s = int(round(seconds or 0))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        m, r = divmod(s, 60)
        return f"{m} min {r} s" if r else f"{m} min"
    h, r = divmod(s, 3600)
    return f"{h} h {r // 60:02d} min"


def _comma(value: float) -> str:
    return str(value).replace(".", ",")


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def report(days: int = 7, now: datetime | None = None) -> dict[str, Any]:
    now = _aware(now or datetime.now(UTC))
    start, before = now - timedelta(days=days), now - timedelta(days=2 * days)
    with session_scope() as s:
        rows = (
            s.query(Job.id, Job.kind, Job.project_id, Job.status, Job.started_at, Job.finished_at)
            .filter(Job.started_at.isnot(None), Job.finished_at.isnot(None), Job.started_at >= before)
            .all()
        )
        ids = [r.id for r in rows]
        costs: dict[str, float] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            for job_id, total in (
                s.query(CostEntry.job_id, func.sum(CostEntry.cost_usd))
                .filter(CostEntry.job_id.in_(chunk))
                .group_by(CostEntry.job_id)
                .all()
            ):
                costs[job_id] = float(total or 0)
    runs = []
    for r in rows:
        started, finished = _aware(r.started_at), _aware(r.finished_at)
        runs.append({"id": r.id, "kind": r.kind, "project_id": r.project_id, "status": r.status, "t": started,
                     "seconds": max((finished - started).total_seconds(), 0.0)})
    cur = [r for r in runs if start <= r["t"] <= now]
    prev = [r for r in runs if before <= r["t"] < start]
    total = sum(r["seconds"] for r in cur)
    total_prev = sum(r["seconds"] for r in prev)
    by_prev: dict[str, float] = {}
    for r in prev:
        by_prev[r["kind"]] = by_prev.get(r["kind"], 0.0) + r["seconds"]
    steps: dict[str, dict[str, Any]] = {}
    for r in cur:
        e = steps.setdefault(r["kind"], {"kind": r["kind"], "label": label(r["kind"]), "n": 0, "total_s": 0.0,
                                         "failures": 0, "cost_usd": 0.0, "_projects": set()})
        e["n"] += 1
        e["total_s"] += r["seconds"]
        e["failures"] += r["status"] == "failed"
        e["cost_usd"] += costs.get(r["id"], 0.0)
        if r["project_id"]:
            e["_projects"].add(r["project_id"])
    out = []
    for e in sorted(steps.values(), key=lambda x: -x["total_s"]):
        projects = len(e.pop("_projects"))
        out.append({
            **e,
            "total_s": round(e["total_s"], 1),
            "avg_s": round(e["total_s"] / e["n"], 1),
            "pct": round(e["total_s"] / total, 4) if total else 0.0,
            "projects": projects,
            "per_project": round(e["n"] / projects, 2) if projects else None,
            "before_s": round(by_prev.get(e["kind"], 0.0), 1),
            "cost_usd": round(e["cost_usd"], 4),
        })
    rework = [e for e in out if (e["per_project"] or 0) >= REWORK and e["projects"] >= REWORK_MIN_PROJECTS]
    result = {
        "days": days,
        "since": start.isoformat(),
        "until": now.isoformat(),
        "jobs": len(cur),
        "total_s": round(total, 1),
        "total_prev_s": round(total_prev, 1),
        "failures": sum(e["failures"] for e in out),
        "cost_usd": round(sum(e["cost_usd"] for e in out), 4),
        "steps": out,
        "rework": [{"kind": e["kind"], "label": e["label"], "per_project": e["per_project"],
                    "projects": e["projects"]} for e in rework],
    }
    result["suggestions"] = suggestions(result)
    return result


def suggestions(rep: dict[str, Any]) -> list[str]:
    """Gargalo (1ª etapa com 30%+ do tempo) e retrabalho (1,5+ execuções por projeto em 3+ projetos)."""
    out: list[str] = []
    steps = rep["steps"]
    if steps and steps[0]["pct"] >= BOTTLENECK:
        e = steps[0]
        out.append(f"Gargalo: {e['label']} leva {round(e['pct'] * 100)}% do tempo do período "
                   f"({dur(e['avg_s'])} por vez, {e['n']} vezes). Meça uma mudança por vez nele.")
    redo = [e for e in steps if (e["per_project"] or 0) >= REWORK and e["projects"] >= REWORK_MIN_PROJECTS]
    if redo:
        e = max(redo, key=lambda x: x["per_project"] * x["total_s"])
        out.append(f"Retrabalho: {e['label']} roda {_comma(e['per_project'])} vezes por projeto "
                   f"({e['projects']} projetos). Descubra o que faz refazer.")
    return out
