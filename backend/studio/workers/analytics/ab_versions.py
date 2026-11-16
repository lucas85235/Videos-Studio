"""Ramos A/B publicados viram experimento de Desempenho (variável `versao`).

Cada publicação de um projeto com ramos leva `details["versao"]` ("A" para a principal, "B", "C"... para os ramos) e
`details["branch"]`. Quando a principal e um ramo têm vídeo no YouTube, o ramo abre um experimento (um por ramo) com
`variable="versao"`, variantes `["A", "<letra>"]` e `project_ids=[projeto]`: a leitura de `experiments.evaluate` usa as
mesmas regras dos outros experimentos (idade mínima, 3+ vídeos por variante, 1,3x).

Como um projeto costuma ter 1 vídeo por versão, `reading` também mostra a comparação direta (indicativa) dos números
de cada lado.
"""

from __future__ import annotations

from typing import Any

from studio.core.project import branches as br
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Experiment, Project, ProjectBranch, Publication
from studio.workers.analytics.experiments import IDADE_MIN_H, evaluate, experiment_dict
from studio.workers.analytics.metrics import latest_metrics

VARIABLE = "versao"


def publication_tag(project_id: str, branch: str | None) -> dict[str, Any]:
    """O que vai em `Publication.details` para ligar a publicação à versão (vazio: projeto sem A/B)."""
    if branch and branch != br.MAIN:
        info = br.get_branch(project_id, branch)
        return {"versao": info["letter"], "branch": info["name"], "version": info["head"]}
    if br.has_ab(project_id):
        return {"versao": "A", "branch": br.MAIN, "version": br.head_of(project_id, br.MAIN)}
    return {}


def _published(project_id: str) -> dict[str, list[dict[str, Any]]]:
    """Vídeos do YouTube do projeto por versão ("A", "B"...), só os que já têm id do vídeo."""
    out: dict[str, list[dict[str, Any]]] = {}
    with session_scope() as s:
        rows = (
            s.query(Publication)
            .filter(Publication.project_id == project_id, Publication.platform == "youtube",
                    Publication.external_id.isnot(None))
            .order_by(Publication.created_at)
            .all()
        )
        for r in rows:
            versao = (r.details or {}).get("versao")
            if versao:
                out.setdefault(str(versao), []).append(
                    {"publication_id": r.id, "youtube_id": r.external_id, "url": r.url, "language": r.language})
    return out


def sync_experiments(project_id: str) -> list[str]:
    """Abre o experimento de cada ramo que já tem vídeo publicado junto com a versão A. Devolve os ids criados."""
    published = _published(project_id)
    if "A" not in published:
        return []
    created: list[str] = []
    with session_scope() as s:
        project = s.get(Project, project_id)
        if project is None:
            return []
        rows = (
            s.query(ProjectBranch)
            .filter(ProjectBranch.project_id == project_id, ProjectBranch.status != "descartado")
            .all()
        )
        for row in rows:
            if row.letter not in published:
                continue
            if row.experiment_id and s.get(Experiment, row.experiment_id) is not None:
                continue
            note = f" ({row.note})" if row.note else ""
            exp = Experiment(
                id=new_id("exp"),
                channel_id=project.channel_id,
                hypothesis=f"{project.title}: a versão {row.letter}{note} rende mais que a versão A?"[:2000],
                variable=VARIABLE,
                variants=["A", row.letter],
                metric="views",
                project_ids=[project_id],
                status="aberto",
            )
            s.add(exp)
            row.experiment_id = exp.id
            created.append(exp.id)
    return created


def _side(videos: list[dict[str, Any]], metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = []
    for v in videos:
        m = metrics.get(v["youtube_id"] or "") or {}
        items.append({**v, "views": m.get("views"), "ctr": m.get("ctr"), "pct_watched": m.get("pct_watched"),
                      "age_hours": m.get("age_hours")})
    views = [i["views"] for i in items if isinstance(i["views"], int | float)]
    return {"videos": items, "views": sum(views) if views else None}


def reading(project_id: str) -> list[dict[str, Any]]:
    """Comparação de cada ramo publicado com a versão A: vídeos, views e a leitura do experimento."""
    published = _published(project_id)
    metrics = latest_metrics()
    out = []
    for b in br.list_branches(project_id):
        if b["status"] == "descartado" or (b["letter"] not in published and not b["experiment_id"]):
            continue
        a_side = _side(published.get("A", []), metrics)
        b_side = _side(published.get(b["letter"], []), metrics)
        exp_data = None
        if b["experiment_id"]:
            with session_scope() as s:
                row = s.get(Experiment, b["experiment_id"])
                if row is not None:
                    exp_data = {**experiment_dict(row), "evaluation": evaluate(row)}
        direct = None
        if a_side["views"] is not None and b_side["views"] is not None:
            young = any((v.get("age_hours") or 0) < IDADE_MIN_H["short"]
                        for v in a_side["videos"] + b_side["videos"])
            lead = "B" if b_side["views"] > a_side["views"] else "A" if a_side["views"] > b_side["views"] else None
            leader = b["letter"] if lead == "B" else "A" if lead == "A" else None
            direct = {
                "leader": leader,
                "young": young,
                "text": (f"Versão {leader} na frente em views" if leader else "Empate em views")
                + (" (ainda cedo: os números sobem nos primeiros dias)" if young else ""),
            }
        out.append({"branch": b["name"], "letter": b["letter"], "note": b["note"], "a": a_side, "b": b_side,
                    "experiment": exp_data, "direct": direct})
    return out
