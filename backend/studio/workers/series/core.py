"""Séries de um canal e o piloto automático delas.

Regras:
- a série cria episódios (projetos) até ter `stock_target` episódios abertos (em produção, esperando revisão, prontos
  ou com falha), no máximo `max_per_run` por rodada;
- o tema de cada episódio sai da fila `topics` (o primeiro sai e vai para o episódio) ou, com `source="ideas"`, da
  ideia **aprovada** de nota mais alta do canal, ainda sem projeto, no formato da série, com a palavra do filtro e que
  ainda não virou episódio de nenhuma série (apagar o projeto de um episódio não faz a mesma ideia voltar);
- a situação do episódio sai do status do projeto e do job **mais recente** do projeto (aprovar o roteiro dispara outro
  job, e tentar de novo também): falhou, parado (cancelado ou terminado sem chegar a uma revisão) ou em produção;
- cada episódio roda o job `autopilot` com os portões de roteiro e cenas ligados: para em "Revisar o roteiro" e
  espera você. O piloto nunca pula portão nem publica;
- a rotina da manhã (`ops/routines.step_series`) roda as séries com piloto ligado; o botão "Rodar agora" roda uma
  série mesmo com o piloto desligado (mas não pausada);
- sem tema na fila nem ideia aprovada: pendência na página Hoje (uma aberta por série; a do dia substitui a antiga e
  fecha quando volta a haver tema).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from studio.core.project import service
from studio.core.styles.library import StyleNotFound, get_style
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Idea, Job, Project, Publication, Series, SeriesEpisode, utcnow

log = logging.getLogger(__name__)

FORMATS = ("", "short", "long")
SOURCES = ("list", "ideas")
MAX_TOPICS = 500
MAX_TOPIC_LEN = 300
LIMITS = {"cadence_days": (1, 60), "stock_target": (0, 30), "max_per_run": (1, 5)}
EDITABLE = ("channel_id", "name", "theme", "notes", "format", "style_id", "cadence_days", "stock_target",
            "max_per_run", "source", "topics", "idea_filter", "autopilot", "active")

# Situação do episódio pelo status do projeto e pelo job mais recente dele.
REVIEW_STATUSES = ("script_review", "storyboard_review", "preview_ready")
READY_STATUS = "rendered"
ACTIVE_JOB_STATUSES = ("queued", "running")
# Falhou e parado contam no estoque: esperam você tentar de novo ou apagar (a série não empilha episódios novos
# por cima de episódios largados).
OPEN_STATES = ("producao", "revisao", "pronto", "falhou", "parado")
STATE_LABELS = {
    "producao": "Em produção",
    "revisao": "Esperando revisão",
    "pronto": "Pronto",
    "falhou": "Falhou",
    "parado": "Parado",
    "publicado": "Publicado",
    "apagado": "Projeto apagado",
}
WHERE = "/series"

Submit = Callable[..., dict[str, Any]]


class SeriesError(ValueError):
    """Pedido inválido (vira 422 na API)."""


class SeriesNotFound(LookupError):
    pass


class SeriesConflict(ValueError):
    """A fila de temas mudou desde que você abriu a série (vira 409 na API)."""


# ----------------------------------------------------------------------------- validação


def _clean_topics(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        raise SeriesError("os temas vêm numa lista (um por linha)")
    topics = [" ".join(str(t).split()) for t in value]
    topics = [t for t in topics if t]
    if len(topics) > MAX_TOPICS:
        raise SeriesError(f"no máximo {MAX_TOPICS} temas na fila")
    if any(len(t) > MAX_TOPIC_LEN for t in topics):
        raise SeriesError(f"cada tema pode ter até {MAX_TOPIC_LEN} caracteres")
    return topics


def _check(s: Any, data: dict[str, Any]) -> dict[str, Any]:
    data = {k: v for k, v in data.items() if k in EDITABLE}
    if "name" in data:
        data["name"] = str(data["name"] or "").strip()
        if not data["name"]:
            raise SeriesError("a série precisa de um nome")
        if len(data["name"]) > 200:
            raise SeriesError("nome com até 200 caracteres")
    if "channel_id" in data:
        if not data["channel_id"] or s.get(Channel, data["channel_id"]) is None:
            raise SeriesError("escolha um canal que existe")
    if "format" in data:
        data["format"] = data["format"] or ""
        if data["format"] not in FORMATS:
            raise SeriesError("formato inválido (use short, long ou vazio)")
    if "source" in data and data["source"] not in SOURCES:
        raise SeriesError("de onde vêm os temas: list (lista) ou ideas (ideias aprovadas)")
    if "style_id" in data:
        data["style_id"] = (data["style_id"] or "").strip() or None
        if data["style_id"]:
            try:
                get_style(data["style_id"])
            except (StyleNotFound, ValueError) as exc:
                raise SeriesError(f"estilo não encontrado: {data['style_id']}") from exc
    for key, (low, high) in LIMITS.items():
        if key in data:
            try:
                data[key] = int(data[key])
            except (TypeError, ValueError) as exc:
                raise SeriesError(f"{key} precisa ser um número") from exc
            if not low <= data[key] <= high:
                raise SeriesError(f"{key} vai de {low} a {high}")
    if "topics" in data:
        data["topics"] = _clean_topics(data["topics"])
    for key in ("theme", "notes", "idea_filter"):
        if key in data:
            data[key] = str(data[key] or "").strip()
    for key in ("autopilot", "active"):
        if key in data:
            data[key] = bool(data[key])
    return data


# ----------------------------------------------------------------------------- leitura


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _gone(status: str, details: dict[str, Any] | None) -> bool:
    from studio.workers.ops.stock import _gone as stock_gone

    return stock_gone(status, details)


def _episode_rows(s: Any, series_ids: list[str]) -> list[dict[str, Any]]:
    """Episódios das séries com a situação de cada um (calculada na hora pelo projeto, publicações e job)."""
    if not series_ids:
        return []
    rows = (
        s.query(SeriesEpisode)
        .filter(SeriesEpisode.series_id.in_(series_ids))
        .order_by(SeriesEpisode.series_id, SeriesEpisode.number.desc())
        .all()
    )
    pids = [r.project_id for r in rows]
    projects = {p.id: (p.title, p.status) for p in s.query(Project).filter(Project.id.in_(pids))} if pids else {}
    gone: set[str] = set()
    if pids:
        for pid, status, details in s.query(Publication.project_id, Publication.status, Publication.details).filter(
            Publication.project_id.in_(pids)
        ):
            if _gone(status, details):
                gone.add(pid)
    # O job que vale é o mais recente do projeto, não só o primeiro que a série gravou: aprovar o roteiro dispara
    # outro `autopilot`, e "Tentar de novo" na página do projeto também.
    latest: dict[str, tuple[str, str, str]] = {}
    if pids:
        for job in s.query(Job).filter(Job.project_id.in_(pids)).order_by(Job.created_at, Job.id):
            latest[job.project_id] = (job.id, job.status, job.error or "")
    saved_ids = [r.job_id for r in rows if r.job_id and r.project_id not in latest]
    saved = {j.id: (j.id, j.status, j.error or "") for j in s.query(Job).filter(Job.id.in_(saved_ids))} if saved_ids else {}
    out = []
    for r in rows:
        title, status = projects.get(r.project_id, (None, None))
        job_id, job_status, job_error = latest.get(r.project_id) or saved.get(r.job_id or "") or (r.job_id, None, "")
        if title is None:
            state = "apagado"
        elif r.project_id in gone:
            state = "publicado"
        elif status in REVIEW_STATUSES:
            state = "revisao"
        elif status == READY_STATUS:
            state = "pronto"
        elif job_status is None or job_status in ACTIVE_JOB_STATUSES:
            state = "producao"
        elif job_status == "failed":
            state = "falhou"
        else:
            # Cancelado, ou terminou sem chegar a uma revisão: nada rodando, o episódio espera você.
            state = "parado"
        out.append({
            "id": r.id,
            "series_id": r.series_id,
            "number": r.number,
            "project_id": r.project_id,
            "project_title": title,
            "project_status": status,
            "topic": r.topic,
            "idea_id": r.idea_id,
            "job_id": job_id,
            "job_status": job_status,
            "error": job_error[:300] if state == "falhou" else "",
            "origin": r.origin,
            "state": state,
            "state_label": STATE_LABELS[state],
            "open": state in OPEN_STATES,
            "created_at": _iso(r.created_at),
        })
    return out


def _counts(episodes: list[dict[str, Any]]) -> dict[str, int]:
    counts = {state: 0 for state in STATE_LABELS}
    for e in episodes:
        counts[e["state"]] += 1
    counts["open"] = sum(counts[state] for state in OPEN_STATES)
    counts["total"] = len(episodes)
    return counts


def _series_dict(row: Series, channel_name: str, episodes: list[dict[str, Any]],
                 with_episodes: bool = False) -> dict[str, Any]:
    counts = _counts(episodes)
    out = {
        "id": row.id,
        "channel_id": row.channel_id,
        "channel_name": channel_name,
        "name": row.name,
        "theme": row.theme or "",
        "notes": row.notes or "",
        "format": row.format or "",
        "style_id": row.style_id,
        "cadence_days": row.cadence_days,
        "stock_target": row.stock_target,
        "max_per_run": row.max_per_run,
        "source": row.source,
        "topics": list(row.topics or []),
        "idea_filter": row.idea_filter or "",
        "autopilot": bool(row.autopilot),
        "active": bool(row.active),
        "last_run_at": _iso(row.last_run_at),
        "last_run_message": row.last_run_message or "",
        "created_at": _iso(row.created_at),
        "counts": counts,
        "below": counts["open"] < row.stock_target,
        # Dias cobertos pelos episódios abertos, na cadência da série.
        "coverage_days": counts["open"] * row.cadence_days,
        "next_number": max((e["number"] for e in episodes), default=0) + 1,
    }
    if with_episodes:
        out["episodes"] = episodes
    return out


def list_series(channel_id: str | None = None) -> list[dict[str, Any]]:
    with session_scope() as s:
        q = s.query(Series, Channel.name).join(Channel, Channel.id == Series.channel_id)
        if channel_id:
            q = q.filter(Series.channel_id == channel_id)
        pairs = q.order_by(Channel.name, Series.name).all()
        episodes = _episode_rows(s, [row.id for row, _ in pairs])
        by_series: dict[str, list[dict[str, Any]]] = {}
        for e in episodes:
            by_series.setdefault(e["series_id"], []).append(e)
        return [_series_dict(row, name, by_series.get(row.id, [])) for row, name in pairs]


def get_series(series_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Series, series_id)
        if row is None:
            raise SeriesNotFound(series_id)
        channel = s.get(Channel, row.channel_id)
        return _series_dict(row, channel.name if channel else "", _episode_rows(s, [row.id]), with_episodes=True)


# ----------------------------------------------------------------------------- escrita


def create_series(data: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as s:
        clean = _check(s, {"name": data.get("name", ""), "channel_id": data.get("channel_id"), **data})
        row = Series(id=new_id("ser"), **clean)
        s.add(row)
        series_id = row.id
    return get_series(series_id)


def update_series(series_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Salva a série. Com `topics_base` (a fila que o painel leu), recusa salvar `topics` se a fila mudou desde então
    (a rotina ou o "Rodar agora" tirou um tema): sem isso o tema já usado voltaria para a fila."""
    data = dict(data)
    base = data.pop("topics_base", None)
    with session_scope() as s:
        row = s.get(Series, series_id)
        if row is None:
            raise SeriesNotFound(series_id)
        clean = _check(s, data)
        if "topics" in clean and base is not None and _clean_topics(base) != list(row.topics or []):
            raise SeriesConflict("a fila de temas mudou enquanto você editava (um tema virou episódio); "
                                 "feche e abra a série de novo para ver a fila atual")
        old = (row.source, list(row.topics or []), row.idea_filter, row.format)
        for key, value in clean.items():
            setattr(row, key, value)
        # A pendência "série sem temas" perde o sentido se a série parou de rodar sozinha ou a fonte dos temas mudou
        # (a próxima rodada abre outra se ainda faltar tema).
        stale = (not row.active or not row.autopilot or row.stock_target == 0
                 or (row.source, list(row.topics or []), row.idea_filter, row.format) != old)
    if stale:
        _discard_no_topic(series_id)
    return get_series(series_id)


def delete_series(series_id: str) -> None:
    """Apaga a série e a lista de episódios. Os projetos ficam (são seus vídeos)."""
    with session_scope() as s:
        row = s.get(Series, series_id)
        if row is None:
            raise SeriesNotFound(series_id)
        s.query(SeriesEpisode).filter(SeriesEpisode.series_id == series_id).delete()
        s.delete(row)
    _discard_no_topic(series_id)


# ----------------------------------------------------------------------------- piloto automático


def _norm(text: str) -> str:
    from studio.workers.business.ideas import norm

    return norm(text)


def _pick_idea(s: Any, row: Series) -> Idea | None:
    """A ideia aprovada de nota mais alta do canal, sem projeto, no formato da série e com a palavra do filtro."""
    # Ideia que já virou episódio (de qualquer série) não volta: se você apagou o projeto, descartou o episódio.
    used = s.query(SeriesEpisode.idea_id).filter(SeriesEpisode.idea_id.is_not(None))
    ideas = (
        s.query(Idea)
        .filter(Idea.channel_id == row.channel_id, Idea.status == "aprovada", Idea.project_id.is_(None),
                Idea.id.not_in(used))
        .all()
    )
    key = _norm(row.idea_filter or "")
    picked = [
        i for i in ideas
        if (not row.format or not i.format or i.format == row.format)
        and (not key or key in _norm(f"{i.title} {i.angle or ''}"))
    ]
    picked.sort(key=lambda i: (-1 if i.score is None else i.score, i.created_at), reverse=True)
    return picked[0] if picked else None


def _brief(row: Series, number: int, idea: Idea | None) -> str:
    parts = [f'Episódio {number} da série "{row.name}".']
    if row.theme:
        parts.append(f"Tema da série: {row.theme}")
    if row.notes:
        parts.append(f"Instruções da série: {row.notes}")
    if idea is not None:
        if (idea.angle or "").strip():
            parts.append(f"Ângulo: {idea.angle.strip()}")
        if (idea.evidence or "").strip():
            parts.append(f"Evidência: {idea.evidence.strip()}")
    return "\n\n".join(parts)


def _style_for(row: Series) -> str:
    from studio.workers.business.ideas import _default_style

    if row.style_id:
        return row.style_id
    return _default_style(row.channel_id, row.format or "")


def _no_topic_prefix(series_id: str) -> str:
    return f"serie:{series_id}:sem-temas:"


def _discard_no_topic(series_id: str, keep_key: str | None = None) -> None:
    """Fecha as pendências "série sem temas" ainda abertas (menos a do dia)."""
    from studio.models import PendingTask

    with session_scope() as s:
        q = s.query(PendingTask).filter(
            PendingTask.kind == "auto",
            PendingTask.status == "aberta",
            PendingTask.auto_key.like(f"{_no_topic_prefix(series_id)}%"),
        )
        if keep_key:
            q = q.filter(PendingTask.auto_key != keep_key)
        for p in q.all():
            p.status = "descartada"
            p.done_at = datetime.now(UTC)


def discard_orphan_pendings() -> int:
    """Fecha as pendências "série sem temas" de séries que não existem mais (apagar o canal leva as séries junto)."""
    from studio.models import PendingTask

    closed = 0
    with session_scope() as s:
        alive = {sid for (sid,) in s.query(Series.id)}
        for p in s.query(PendingTask).filter(PendingTask.kind == "auto", PendingTask.status == "aberta",
                                             PendingTask.auto_key.like("serie:%:sem-temas:%")):
            if (p.auto_key or "").split(":")[1] not in alive:
                p.status = "descartada"
                p.done_at = datetime.now(UTC)
                closed += 1
    return closed


def _no_topic_pending(row_id: str, name: str, channel_id: str, source: str, now: datetime) -> None:
    from studio.workers.ops.pending import ensure_auto

    day = now.astimezone().date().isoformat()
    key = f"{_no_topic_prefix(row_id)}{day}"
    _discard_no_topic(row_id, keep_key=key)
    what = "aprove ideias do canal em Ideias" if source == "ideas" else "ponha temas na fila da série"
    ensure_auto(key, f'A série "{name}" ficou sem temas para o próximo episódio: {what}', WHERE, channel_id)


def _create_episode(series_id: str, origin: str, submit: Submit, now: datetime) -> dict[str, Any] | None:
    """Cria o próximo episódio (projeto + job do piloto). None quando não há tema."""
    with session_scope() as s:
        row = s.get(Series, series_id)
        assert row is not None
        number = (s.query(SeriesEpisode.number).filter(SeriesEpisode.series_id == series_id)
                  .order_by(SeriesEpisode.number.desc()).limit(1).scalar() or 0) + 1
        idea = None
        if row.source == "ideas":
            idea = _pick_idea(s, row)
            topic = idea.title if idea else ""
        else:
            topics = list(row.topics or [])
            topic = topics[0] if topics else ""
        if not topic:
            snapshot = (row.id, row.name, row.channel_id, row.source)
        else:
            snapshot = None
            spec = {
                "title": topic[:200],
                "topic": topic,
                "brief": _brief(row, number, idea),
                "channel_id": row.channel_id,
                "style_id": _style_for(row),
                "series": {"id": row.id, "name": row.name, "number": number},
                "idea_id": idea.id if idea else None,
            }
    if snapshot is not None:
        _no_topic_pending(*snapshot, now)
        return None
    doc = service.create_project(
        title=spec["title"],
        topic=spec["topic"],
        brief=spec["brief"],
        channel_id=spec["channel_id"],
        style_id=spec["style_id"],
        # Portões ligados sempre: o piloto da série para no roteiro e nas cenas e nunca publica sozinho.
        settings={"gates": {"script": True, "storyboard": True, "preview": True, "publish": True},
                  "series": spec["series"]},
    )
    taken = {"topic": False}
    try:
        return _link_episode(series_id, origin, submit, spec, doc.id, taken)
    except Exception:
        # Nada pela metade: sem isso sobrava um episódio sem job ("em produção" para sempre, ocupando o estoque) ou
        # um projeto sem episódio, com o tema ainda na fila (a próxima rodada repetiria o tema).
        _undo_episode(series_id, spec, doc.id, taken["topic"])
        raise


def _link_episode(series_id: str, origin: str, submit: Submit, spec: dict[str, Any], doc_id: str,
                  taken: dict[str, bool]) -> dict[str, Any]:
    """Grava o episódio, tira o tema da fila (ou marca a ideia) e dispara o job do piloto."""
    popped = False
    with session_scope() as s:
        row = s.get(Series, series_id)
        assert row is not None
        if spec["idea_id"]:
            idea = s.get(Idea, spec["idea_id"])
            if idea is not None:
                idea.status = "em_producao"
                idea.project_id = doc_id
                idea.decided_at = utcnow()
        else:
            topics = list(row.topics or [])
            if topics and topics[0] == spec["topic"]:
                row.topics = topics[1:]
                popped = True
        episode = SeriesEpisode(series_id=series_id, number=spec["series"]["number"], project_id=doc_id,
                                topic=spec["topic"], idea_id=spec["idea_id"], origin=origin)
        s.add(episode)
        s.flush()
        episode_id = episode.id
    taken["topic"] = popped  # só depois do commit: se a sessão falhar, o tema nem saiu da fila
    _discard_no_topic(series_id)
    # Sem skip_gates: o job para no primeiro portão (roteiro) e espera a sua revisão.
    job = submit("autopilot", {"series_id": series_id}, doc_id)
    with session_scope() as s:
        ep = s.get(SeriesEpisode, episode_id)
        if ep is not None:
            ep.job_id = job.get("id")
    return {"number": spec["series"]["number"], "project_id": doc_id, "topic": spec["topic"],
            "idea_id": spec["idea_id"], "job_id": job.get("id")}


def _undo_episode(series_id: str, spec: dict[str, Any], project_id: str, topic_taken: bool) -> None:
    """Desfaz um episódio que não chegou a ter job: apaga episódio e projeto, devolve o tema à frente da fila e a
    ideia para "aprovada". Erro aqui só vai para o log (o erro que importa é o original)."""
    try:
        with session_scope() as s:
            s.query(SeriesEpisode).filter(SeriesEpisode.project_id == project_id).delete()
            if spec["idea_id"]:
                idea = s.get(Idea, spec["idea_id"])
                if idea is not None and idea.project_id == project_id:
                    idea.project_id = None
                    idea.status = "aprovada"
            elif topic_taken:
                row = s.get(Series, series_id)
                if row is not None:
                    row.topics = [spec["topic"], *list(row.topics or [])]
        service.delete_project(project_id)
    except Exception:  # noqa: BLE001
        log.exception("série %s: não deu para desfazer o episódio do projeto %s", series_id, project_id)


_run_lock = threading.Lock()  # a rotina e o botão "Rodar agora" não criam o mesmo episódio duas vezes


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def run_series(series_id: str, *, manual: bool = False, count: int | None = None, now: datetime | None = None,
               submit: Submit | None = None) -> dict[str, Any]:
    """Uma rodada do piloto da série. `manual` roda mesmo com o piloto desligado; `count` cria exatamente N episódios
    (ignorando o estoque). Devolve {"created": [...], "message", "skipped"}."""
    if submit is None:
        from studio.workers.jobs import get_runner

        submit = get_runner().submit
    now = now or datetime.now(UTC)
    with _run_lock:
        info = get_series(series_id)
        if not info["active"]:
            return {"created": [], "message": "série pausada", "skipped": True}
        if not manual and not info["autopilot"]:
            return {"created": [], "message": "piloto automático desligado", "skipped": True}
        if count is not None:
            wanted = max(1, min(int(count), LIMITS["max_per_run"][1]))
        else:
            wanted = min(max(0, info["stock_target"] - info["counts"]["open"]), info["max_per_run"])
            if wanted == 0:
                _discard_no_topic(series_id)  # estoque em dia: não precisa de tema novo agora
        created: list[dict[str, Any]] = []
        out_of_topics = False
        for _ in range(wanted):
            try:
                episode = _create_episode(series_id, "manual" if manual else "auto", submit, now)
            except Exception as exc:  # noqa: BLE001 - registra e para a rodada desta série
                log.exception("série %s: falha ao criar episódio", series_id)
                message = f"falhou ao criar episódio: {type(exc).__name__}: {str(exc)[:200]}"
                _mark_run(series_id, now, message)
                return {"created": created, "message": message, "skipped": False, "error": True}
            if episode is None:
                out_of_topics = True
                break
            created.append(episode)
        open_now = info["counts"]["open"] + len(created)
        if created:
            message = f"{_plural(len(created), 'episódio novo', 'episódios novos')} (roteiro esperando você)"
        elif out_of_topics:
            message = "sem temas para o próximo episódio"
        else:
            message = f"estoque em dia ({open_now} de {info['stock_target']})"
        if created and out_of_topics:
            message += "; acabaram os temas"
        _mark_run(series_id, now, message)
        return {"created": created, "message": message, "skipped": False}


def _mark_run(series_id: str, now: datetime, message: str) -> None:
    with session_scope() as s:
        row = s.get(Series, series_id)
        if row is not None:
            row.last_run_at = now
            row.last_run_message = message


def run_autopilots(now: datetime | None = None, submit: Submit | None = None) -> list[str]:
    """Roda as séries com piloto ligado (rotina da manhã). Uma série quebrada não derruba as outras."""
    with session_scope() as s:
        ids = [(r.id, r.name) for r in s.query(Series).filter(Series.active.is_(True), Series.autopilot.is_(True))
               .order_by(Series.name).all()]
    lines = []
    for series_id, name in ids:
        try:
            result = run_series(series_id, now=now, submit=submit)
        except Exception as exc:  # noqa: BLE001
            log.exception("série %s falhou na rotina", series_id)
            # Mesmo prefixo das partes da manhã ("séries: falhou (") para o comando `rotinas` marcar "parcial".
            lines.append(f'séries: falhou (série "{name}": {type(exc).__name__}: {str(exc)[:200]})')
            continue
        if result.get("error"):
            lines.append(f'séries: falhou (série "{name}": {result["message"]})')
        elif result["created"] or "sem temas" in result["message"]:
            lines.append(f'série "{name}": {result["message"]}')
    return lines


def today() -> list[dict[str, Any]]:
    """Resumo das séries ativas para a página Hoje: estoque da série, revisões e falhas."""
    out = []
    for item in list_series():
        if not item["active"]:
            continue
        c = item["counts"]
        out.append({
            "id": item["id"],
            "name": item["name"],
            "channel_id": item["channel_id"],
            "channel_name": item["channel_name"],
            "autopilot": item["autopilot"],
            "open": c["open"],
            "stock_target": item["stock_target"],
            "below": item["below"],
            "review": c["revisao"],
            "ready": c["pronto"],
            "failed": c["falhou"],
            "stopped": c["parado"],
            "in_production": c["producao"],
            "coverage_days": item["coverage_days"],
            "topics_left": len(item["topics"]) if item["source"] == "list" else None,
            "source": item["source"],
            "last_run_at": item["last_run_at"],
            "last_run_message": item["last_run_message"],
        })
    return out
