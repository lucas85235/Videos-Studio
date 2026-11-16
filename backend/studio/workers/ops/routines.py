"""Rotinas automáticas num laço dentro do processo (ADR 0002, decisão 5; origem: estudio/rotinas.py do canais).

Uma thread daemon acorda a cada 5 min e, uma vez por dia, submete os jobs `backup` (cópia do banco) e
`routine_morning` (a manhã, depois do horário configurado, na hora local). Só roda com
`get_settings().workers_enabled` (os testes desligam). O estado fica em `AppSetting["routines"]`.

A manhã (`morning`), cada parte isolada (uma quebrada não derruba as outras):
1. pede a coleta de métricas (job `metrics_collect`, se registrado) quando há canal com YouTube;
2. estoque abaixo do mínimo vira pendência (uma aberta por canal: a do dia substitui as dos dias anteriores, e todas
   fecham quando o estoque volta ao mínimo);
3. nos dias 1 a 10, canal com oferta de afiliado ativa e sem receita do mês anterior: pendência para lançar os números;
4. marcos dos vídeos longos (48 h, 7 dias, 28 dias no ar): pendência de análise, só se o marco foi atingido há menos de
   72 h (nada retroativo);
5. radar de nichos (ADR 0003, bloco E), uma vez por semana ISO (`step_radar`);
6. comentários novos dos canais cuja caixa de comentários já está em uso (`step_comments`).
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.models import AffiliateOffer, AppSetting, Channel, Project, Publication, RevenueEntry

log = logging.getLogger(__name__)

KEY = "routines"
INTERVAL_S = 300  # o laço acorda a cada 5 min
FIRST_WAIT_S = 30  # primeira volta logo depois de o app subir
MARCOS = ((48, "48 h"), (24 * 7, "7 dias"), (24 * 28, "28 dias"))
JANELA_MARCO_H = 72  # análise só se o marco foi atingido há menos de 3 dias (nada retroativo)
REVENUE_LAST_DAY = 10  # pedido dos números das lojas só nos dias 1 a 10
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro",
          "novembro", "dezembro")

DEFAULTS: dict[str, Any] = {
    "morning_time": "08:00",
    "backup": True,
    "metrics": True,
    "last_backup": None,
    "last_morning": None,
    "last_backup_job": None,
    "last_morning_job": None,
    "last_morning_lines": [],
    "backup_day": None,  # dia (local) em que o laço já submeteu o backup
    "morning_day": None,  # dia (local) em que o laço já submeteu a manhã
    "last_offline_run": None,  # última execução do comando `rotinas` (Vulto fechado, ops/offline.py)
    "last_offline_status": None,  # ok | parcial | falhou
    "last_offline_message": "",
    "last_offline_skipped": None,  # última vez que o comando não rodou porque outro processo segurava a trava
    "last_offline_skipped_message": "",
}


# ----------------------------------------------------------------------------- estado


def get_state() -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(AppSetting, KEY)
        value = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
    return {**DEFAULTS, **value}


_state_lock = threading.Lock()  # o tick, os jobs (pool de workers) e a API gravam o estado ao mesmo tempo


def update_state(**changes: Any) -> dict[str, Any]:
    """Junta `changes` ao estado salvo (ler-juntar-gravar sob um lock: uma gravação não apaga a outra)."""
    with _state_lock, session_scope() as s:
        row = s.get(AppSetting, KEY)
        current = dict(row.value or {}) if row and isinstance(row.value, dict) else {}
        merged = {**DEFAULTS, **current, **changes}
        if row is None:
            s.add(AppSetting(key=KEY, value=merged))
        else:
            row.value = merged
    return merged


def public_state() -> dict[str, Any]:
    st = get_state()
    return {
        "enabled": get_settings().workers_enabled,
        "morning_time": st["morning_time"],
        "backup": bool(st["backup"]),
        "metrics": bool(st["metrics"]),
        "last_backup": st["last_backup"],
        "last_morning": st["last_morning"],
        "last_backup_job": st["last_backup_job"],
        "last_morning_job": st["last_morning_job"],
        "last_morning_lines": st["last_morning_lines"] or [],
        "last_offline_run": st["last_offline_run"],
        "last_offline_status": st["last_offline_status"],
        "last_offline_message": st["last_offline_message"] or "",
        "last_offline_skipped": st["last_offline_skipped"],
        "last_offline_skipped_message": st["last_offline_skipped_message"] or "",
    }


def _local(now: datetime | None) -> datetime:
    return (now or datetime.now(UTC)).astimezone()


def local_today(now: datetime | None = None) -> str:
    """O dia local (ISO) usado em `backup_day` e `morning_day`."""
    return _local(now).date().isoformat()


def _clock(value: str) -> time:
    hour, minute = (int(p) for p in value.split(":"))
    return time(hour, minute)


# ----------------------------------------------------------------------------- laço


def tick(now: datetime | None = None, submit: Any = None) -> list[str]:
    """Uma volta do laço: submete o backup e a manhã do dia se ainda não foram. Devolve os tipos submetidos."""
    if submit is None:
        from studio.workers.jobs import get_runner

        submit = get_runner().submit
    local = _local(now)
    today = local_today(now)
    st = get_state()
    done: list[str] = []
    if st["backup"] and st["backup_day"] != today:
        job = submit("backup", {})
        update_state(backup_day=today, last_backup_job=job.get("id"))
        done.append("backup")
    try:
        due = local.time() >= _clock(st["morning_time"])
    except ValueError:
        due = local.time() >= _clock(DEFAULTS["morning_time"])
    if due and st["morning_day"] != today:
        job = submit("routine_morning", {})
        update_state(morning_day=today, last_morning_job=job.get("id"))
        done.append("routine_morning")
    return done


_stop = threading.Event()
_thread: threading.Thread | None = None


def _loop() -> None:
    wait = FIRST_WAIT_S
    while not _stop.wait(wait):
        wait = INTERVAL_S
        try:
            from studio.workers.ops import lock

            other = lock.acquire("app", lock.APP_TTL)  # o comando `rotinas` rodando: esta volta fica para ele
            if other is not None:
                log.info("rotinas: volta pulada, %s", lock.describe(other))
                continue
            tick()
        except Exception:  # noqa: BLE001 - o laço não pode morrer
            log.exception("rotinas: falha numa volta do laço")


def start_routines() -> None:
    """Inicia o laço das rotinas (chamado no `lifespan` depois do `recover()`)."""
    global _thread
    if not get_settings().workers_enabled:
        return
    if _thread is not None and _thread.is_alive():
        return
    from studio.workers.ops import lock

    lock.acquire("app", lock.APP_TTL)  # já na abertura: o comando `rotinas` não roda junto com o app
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="rotinas", daemon=True)
    _thread.start()


def stop_routines() -> None:
    """Para o laço das rotinas (chamado no fim do `lifespan`)."""
    global _thread
    _stop.set()
    if _thread is not None:
        _thread.join(timeout=5)
        from studio.workers.ops import lock

        lock.release()
    _thread = None


# ----------------------------------------------------------------------------- manhã


def _has_youtube(settings: dict[str, Any] | None) -> bool:
    return bool(((settings or {}).get("youtube") or {}).get("channel_id"))


def step_metrics(now: datetime) -> list[str]:
    """Pede a coleta de métricas (job do bloco A, por nome: sem importar o código dele)."""
    from studio.workers import jobs

    if not get_state()["metrics"] or "metrics_collect" not in jobs._REGISTRY:
        return []
    with session_scope() as s:
        any_youtube = any(_has_youtube(c.settings) for c in s.query(Channel).all())
    if not any_youtube:
        return []
    job = jobs.get_runner().submit("metrics_collect", {})
    return [f"coleta de métricas na fila ({job['id']})"]


STOCK_WHERE = "/"  # lista de projetos: terminar os que estão andando ou abrir um novo


def _discard_stock_pending(channel_id: str, keep_key: str | None = None) -> None:
    """Descarta as pendências de estoque ainda abertas do canal (menos a `keep_key`, a do dia)."""
    from studio.models import PendingTask

    with session_scope() as s:
        query = s.query(PendingTask).filter(
            PendingTask.kind == "auto",
            PendingTask.status == "aberta",
            PendingTask.auto_key.like(f"estoque:{channel_id}:%"),
        )
        if keep_key:
            query = query.filter(PendingTask.auto_key != keep_key)
        for row in query.all():
            row.status = "descartada"
            row.done_at = datetime.now(UTC)


def step_stock(now: datetime) -> list[str]:
    from studio.workers.ops.pending import ensure_auto
    from studio.workers.ops.stock import stock

    day = _local(now).date().isoformat()
    lines = []
    for item in stock(now):
        if not item["below"]:
            _discard_stock_pending(item["channel_id"])  # estoque voltou ao mínimo: fecha o alerta
            continue
        key = f"estoque:{item['channel_id']}:{day}"
        _discard_stock_pending(item["channel_id"], keep_key=key)  # só a do dia fica aberta
        what = item["format_label"][0].upper() + item["format_label"][1:]
        text = (f"Estoque de {what} do {item['channel_name']}: {item['total']} prontos ou agendados, "
                f"mínimo {item['min_stock']}")
        if ensure_auto(key, text, STOCK_WHERE, item["channel_id"]):
            lines.append(text)
    return lines


def previous_month(day: date) -> str:
    return f"{day.replace(day=1) - timedelta(days=1):%Y-%m}"


def month_label(period: str) -> str:
    year, month = period.split("-")
    return f"{MONTHS[int(month) - 1]} de {year}"


def step_revenue(now: datetime) -> list[str]:
    """Dias 1 a 10: lançar os números do mês anterior das lojas (canal com oferta ativa e sem receita do mês)."""
    from studio.workers.ops.pending import ensure_auto

    today = _local(now).date()
    if today.day > REVENUE_LAST_DAY:
        return []
    period = previous_month(today)
    with session_scope() as s:
        channels = (
            s.query(Channel.id, Channel.name)
            .join(AffiliateOffer, AffiliateOffer.channel_id == Channel.id)
            .filter(AffiliateOffer.status == "ativa")
            .distinct()
            .all()
        )
        with_revenue = {
            cid for (cid,) in s.query(RevenueEntry.channel_id).filter(RevenueEntry.period == period).distinct().all()
        }
    lines = []
    for channel_id, name in channels:
        if channel_id in with_revenue:
            continue
        text = f"Lançar os números de {month_label(period)} das lojas de afiliado ({name})"
        if ensure_auto(f"receita:{channel_id}:{period}", text, "/dinheiro", channel_id):
            lines.append(text)
    return lines


def _parse(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def published_at(pub: Publication, now: datetime) -> datetime | None:
    """Quando o vídeo foi ao ar: hora agendada (se já passou), data de publicação ou de envio. Privado não conta."""
    from studio.workers.publish.schedule import PUBLISHED, SCHEDULED, UPLOADED

    details = pub.details or {}
    if pub.status not in (SCHEDULED, UPLOADED, PUBLISHED) or details.get("privacy") == "private":
        return None
    if pub.scheduled_at is not None:
        when = pub.scheduled_at if pub.scheduled_at.tzinfo else pub.scheduled_at.replace(tzinfo=UTC)
        return when if when <= now else None
    if pub.status == SCHEDULED:
        return None
    return _parse(details.get("published_at")) or (
        pub.created_at.replace(tzinfo=UTC) if pub.created_at and not pub.created_at.tzinfo else pub.created_at
    )


def milestone(age_h: float) -> tuple[int, str] | None:
    """Marco atingido há menos de 72 h (o mais alto primeiro, como no canais)."""
    return next(((h, name) for h, name in reversed(MARCOS) if h <= age_h < h + JANELA_MARCO_H), None)


def step_milestones(now: datetime) -> list[str]:
    from studio.workers.ops.pending import ensure_auto
    from studio.workers.ops.stock import project_format

    now = now.astimezone(UTC)
    with session_scope() as s:
        pairs = s.query(Publication, Project).join(Project, Project.id == Publication.project_id).all()
        first: dict[str, tuple[datetime, Project]] = {}
        for pub, project in pairs:
            when = published_at(pub, now)
            if when is None:
                continue
            if project.id not in first or when < first[project.id][0]:
                first[project.id] = (when, project)
        items = [(when, p.id, p.title, p.style_id, dict(p.settings or {}), p.channel_id)
                 for when, p in first.values()]
    lines = []
    for when, pid, title, style_id, settings, channel_id in items:
        hit = milestone((now - when).total_seconds() / 3600)
        if hit is None or project_format(pid, style_id, settings) != "long":
            continue
        text = f'Ver como foi "{title}" com {hit[1]} no ar'
        if ensure_auto(f"analise:{pid}:{hit[0]}h", text, "/desempenho", channel_id):
            lines.append(text)
    return lines


RADAR_IDEAS_LIMIT = 15  # LIMITE_IDEIAS do canais: o radar não roda com tantas ideias novas esperando


def step_radar(now: datetime) -> list[str]:
    """Radar de nichos (ADR 0003, bloco E), uma vez por semana ISO: só com "Rodar toda semana" ligado, o job
    registrado, acesso à YouTube Data API e menos de 15 ideias novas esperando."""
    from studio.workers import jobs
    from studio.workers.radar import access, catalog, collect, ideas

    st = catalog.radar_state()
    week = ideas.iso_week(now)
    if not st.get("auto") or "radar_collect" not in jobs._REGISTRY or st.get("last_week") == week:
        return []
    if not access.has_access():
        return ["radar de nichos: sem acesso à YouTube Data API (cole uma chave em Radar → Acesso)"]
    waiting = ideas.new_ideas_count()
    if waiting >= RADAR_IDEAS_LIMIT:
        catalog.update_radar_state(last_week=week)
        return [f"radar pulado: {waiting} ideias novas esperando você"]
    if collect.active_job() is not None:  # coleta na fila ou rodando (clique na página Radar, por exemplo)
        return []
    # `last_week` só é marcada pelo job quando a coleta termina bem: se falhar (cota do dia, token vencido, sem
    # rede), a manhã seguinte da mesma semana tenta de novo, como o canais.
    job = jobs.get_runner().submit("radar_collect", {"weekly": week})
    catalog.update_radar_state(last_week_submitted=week, last_job=job.get("id"))
    return [f"radar de nichos na fila ({job['id']})"]


def step_comments(now: datetime) -> list[str]:
    """Lê os comentários novos dos canais cuja caixa de comentários já foi usada (já leram uma vez e o acesso estava
    ok). Custa 1 a 3 unidades de cota por canal; perguntas sem resposta aparecem em Hoje."""
    from studio.workers.comments import inbox

    with session_scope() as s:
        ids = [c.id for c in s.query(Channel).order_by(Channel.name).all()
               if _has_youtube(c.settings) and ((c.settings or {}).get("comments") or {}).get("state") == inbox.OK]
    if not ids:
        return []
    lines: list[str] = []
    for cid in ids:
        res = inbox.refresh(cid, now=now)["channels"][0]
        if res.get("state") != inbox.OK:
            lines.append(f"comentários do {res['name']}: {res.get('error') or res.get('state')}")
        elif res.get("new"):
            lines.append(f"{res['new']} comentário(s) novo(s) no {res['name']}")
    return lines


STEPS = (
    ("métricas", step_metrics),
    ("comentários", step_comments),
    ("estoque", step_stock),
    ("receita", step_revenue),
    ("marcos", step_milestones),
    ("radar", step_radar),
)


def morning(now: datetime | None = None) -> list[str]:
    """A manhã do estúdio. Devolve o que as partes fizeram (uma linha por ação)."""
    now = (now or datetime.now(UTC))
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    lines: list[str] = []
    for name, step in STEPS:
        try:
            lines += step(now)
        except Exception as exc:  # noqa: BLE001 - uma parte quebrada não derruba as outras
            log.exception("rotina da manhã: %s falhou", name)
            lines.append(f"{name}: falhou ({type(exc).__name__}: {str(exc)[:200]})")
    return lines
