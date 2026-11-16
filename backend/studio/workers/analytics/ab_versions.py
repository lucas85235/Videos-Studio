"""Ramos A/B publicados viram experimento de Desempenho (variável `versao`).

Cada publicação de um projeto com ramos leva `details["versao"]` ("A" para a principal, "B", "C"... para os ramos) e
`details["branch"]`. Publicação da principal sem a marca (a A saiu antes de a B existir) também conta como "A".

Quando a A e um ramo têm vídeo **público** no YouTube (não privado), o ramo abre um experimento (um por ramo) com
`variable="versao"`, variantes `["A", "<letra>"]` e `project_ids=[projeto]`. Um vídeo privado não ganha views: medir
contra ele daria a vitória à A sem motivo.

Como cada versão costuma ter 1 vídeo, a regra de `experiments.evaluate` (3+ vídeos por variante) nunca fecharia esse
experimento. A leitura aqui é 1×1: compara as views no mesmo idioma dos dois lados, só depois da idade mínima do
formato (`IDADE_MIN_H`), e pede 1,3x de vantagem (`VANTAGEM`) para apontar a vencedora. Passada a idade mínima, o
experimento fecha como concluído com o resultado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from studio.core.project import branches as br
from studio.core.project import service
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Experiment, Project, ProjectBranch, Publication
from studio.workers.analytics.experiments import IDADE_MIN_H, VANTAGEM, experiment_dict, vezes
from studio.workers.analytics.metrics import latest_metrics

VARIABLE = "versao"


def publication_tag(project_id: str, branch: str | None, lang: str | None = None) -> dict[str, Any]:
    """O que vai em `Publication.details` para ligar a publicação à versão (vazio: projeto sem A/B).

    Para um ramo, guarda também o estilo e a duração do vídeo do ramo: as variáveis de Desempenho (`estilo`,
    `faixa_duracao`, `formato`) não podem vir da principal.
    """
    if branch and branch != br.MAIN:
        info = br.get_branch(project_id, branch)
        tag: dict[str, Any] = {"versao": info["letter"], "branch": info["name"], "version": info["head"]}
        try:
            tag["style_id"] = service.get_doc(project_id, info["head"]).style_id
        except LookupError:
            pass
        langs = (info["last_final"] or {}).get("langs") or {}
        entry = langs.get(lang) if lang else None
        if isinstance(entry, dict) and isinstance(entry.get("duration"), int | float):
            tag["duration"] = float(entry["duration"])
        return tag
    if br.has_ab(project_id):
        return {"versao": "A", "branch": br.MAIN, "version": br.head_of(project_id, br.MAIN)}
    return {}


def _is_public(details: dict[str, Any], metric: dict[str, Any] | None) -> bool:
    """O vídeo está no ar para o público? A coleta só guarda número de vídeo público (ou privado que já teve views);
    sem leitura, vale a privacidade do envio. Vídeo ligado à mão ou pelo pacote (sem privacidade) conta como público."""
    if metric is not None:
        return True
    return details.get("privacy") != "private"


def published_videos(project_id: str,
                     metrics: dict[str, dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:
    """Vídeos do YouTube do projeto por versão ("A", "B"...), só os que já têm id do vídeo."""
    metrics = latest_metrics() if metrics is None else metrics
    out: dict[str, list[dict[str, Any]]] = {}
    with session_scope() as s:
        project = s.get(Project, project_id)
        default_style = project.style_id if project else None
        rows = (
            s.query(Publication)
            .filter(Publication.project_id == project_id, Publication.platform == "youtube",
                    Publication.external_id.isnot(None))
            .order_by(Publication.created_at)
            .all()
        )
        for r in rows:
            details = r.details or {}
            versao = details.get("versao")
            if not versao and details.get("branch") in (None, br.MAIN):
                versao = "A"  # a A saiu antes de existir a B: a publicação da principal não levou a marca
            if not versao:
                continue
            out.setdefault(str(versao), []).append({
                "publication_id": r.id, "youtube_id": r.external_id, "url": r.url, "language": r.language,
                "privacy": details.get("privacy"), "public": _is_public(details, metrics.get(r.external_id or "")),
                "style_id": details.get("style_id") or default_style,
            })
    return out


def _any_public(videos: list[dict[str, Any]]) -> bool:
    return any(v["public"] for v in videos)


def missing_reason(published: dict[str, list[dict[str, Any]]], letter: str) -> str | None:
    """Por que o experimento da versão `letter` ainda não abriu (None: já dá para abrir)."""
    a, b = published.get("A", []), published.get(letter, [])
    if not a:
        return ("Falta o vídeo da versão A no YouTube: publique a A pela aba Vídeo e publicação ou use "
                "“Ligar vídeo já publicado”.")
    if not b:
        return f"Falta o vídeo da versão {letter} no YouTube."
    if not _any_public(a):
        return "A versão A está privada no YouTube: deixe-a pública para comparar as views."
    if not _any_public(b):
        return (f"A versão {letter} está privada no YouTube: um vídeo privado não ganha views. Deixe-a pública "
                "(ou não listada) no YouTube Studio ou envie de novo como pública para começar a medir.")
    return None


def sync_experiments(project_id: str, metrics: dict[str, dict[str, Any]] | None = None) -> list[str]:
    """Abre o experimento de cada ramo que já tem vídeo público junto com um vídeo público da A. Devolve os ids."""
    published = published_videos(project_id, metrics)
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
            if missing_reason(published, row.letter):
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


def _format_of(style_id: str | None) -> str:
    from studio.workers.analytics.variables import _style_format

    return "short" if _style_format(style_id, {}) == "short" else "long"


def _side(videos: list[dict[str, Any]], metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = []
    for v in videos:
        m = metrics.get(v["youtube_id"] or "") or {}
        items.append({**v, "views": m.get("views"), "ctr": m.get("ctr"), "pct_watched": m.get("pct_watched"),
                      "age_hours": m.get("age_hours"), "min_age_h": IDADE_MIN_H[_format_of(v.get("style_id"))]})
    views = [i["views"] for i in items if isinstance(i["views"], int | float)]
    return {"videos": items, "views": sum(views) if views else None}


def direct_reading(a_side: dict[str, Any], b_side: dict[str, Any], letter: str) -> dict[str, Any] | None:
    """Comparação 1×1 dos vídeos públicos, só nos idiomas que os dois lados têm.

    `mature`: todos os vídeos comparados passaram da idade mínima do formato. `leader` só com 1,3x de vantagem.
    """
    def by_lang(side: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
        out: dict[str, list[dict[str, Any]]] = {}
        for v in side["videos"]:
            if v["public"] and isinstance(v.get("views"), int | float):
                out.setdefault(v["language"], []).append(v)
        return out

    a_langs, b_langs = by_lang(a_side), by_lang(b_side)
    common = sorted(set(a_langs) & set(b_langs))
    if not common:
        if a_side["views"] is None or b_side["views"] is None:
            return None
        return {"leader": None, "young": True, "mature": False, "langs": [],
                "text": f"A e {letter} ainda não têm vídeo público com números no mesmo idioma"}
    a_vids = [v for lang in common for v in a_langs[lang]]
    b_vids = [v for lang in common for v in b_langs[lang]]
    a_views = sum(v["views"] for v in a_vids)
    b_views = sum(v["views"] for v in b_vids)
    young = any((v.get("age_hours") or 0) < v["min_age_h"] for v in a_vids + b_vids)
    high, low = max(a_views, b_views), min(a_views, b_views)
    ahead = None if a_views == b_views else (letter if b_views > a_views else "A")
    decided = ahead is not None and (low == 0 or high / low >= VANTAGEM)
    leader = ahead if decided else None
    if ahead is None:
        text = "Empate em views"
    elif decided:
        text = f"Versão {ahead} na frente em views" + (f" ({vezes(high / low)})" if low else "")
    else:
        text = f"Versão {ahead} um pouco na frente em views (menos de {vezes(VANTAGEM)}: empate técnico)"
    if young:
        text += " (ainda cedo: os números sobem nos primeiros dias)"
    return {"leader": leader, "young": young, "mature": not young, "langs": common, "a_views": a_views,
            "b_views": b_views, "text": text}


def _close_if_mature(experiment_id: str | None, direct: dict[str, Any] | None) -> None:
    """Fecha o experimento 1×1 quando os vídeos passaram da idade mínima (a regra de 3+ vídeos nunca fecharia)."""
    if not experiment_id or not direct or not direct.get("mature"):
        return
    with session_scope() as s:
        row = s.get(Experiment, experiment_id)
        if row is None or row.status != "aberto":
            return
        row.status = "concluido"
        row.result = f"{direct['text']} (comparação 1×1, idiomas: {', '.join(direct['langs'])})"
        row.closed_at = datetime.now(UTC)


def reading(project_id: str, *, update: bool = True) -> list[dict[str, Any]]:
    """Comparação de cada ramo publicado com a versão A: vídeos, views e a leitura do experimento.

    Com `update`, abre o experimento que já pode abrir (a B pode ter ficado pública no YouTube depois do envio) e
    fecha o que já passou da idade mínima.
    """
    metrics = latest_metrics()
    if update:
        sync_experiments(project_id, metrics)
    published = published_videos(project_id, metrics)
    out = []
    for b in br.list_branches(project_id):
        if b["status"] == "descartado" or (b["letter"] not in published and not b["experiment_id"]):
            continue
        a_side = _side(published.get("A", []), metrics)
        b_side = _side(published.get(b["letter"], []), metrics)
        direct = direct_reading(a_side, b_side, b["letter"])
        if update:
            _close_if_mature(b["experiment_id"], direct)
        exp_data = None
        if b["experiment_id"]:
            with session_scope() as s:
                row = s.get(Experiment, b["experiment_id"])
                if row is not None:
                    text = row.result if row.status == "concluido" and row.result else (
                        direct["text"] if direct else "esperando os números da coleta")
                    exp_data = {**experiment_dict(row),
                                "evaluation": {"text": text, "winner": direct["leader"] if direct else None}}
        out.append({"branch": b["name"], "letter": b["letter"], "note": b["note"], "a": a_side, "b": b_side,
                    "experiment": exp_data, "direct": direct, "notice": missing_reason(published, b["letter"])})
    return out
