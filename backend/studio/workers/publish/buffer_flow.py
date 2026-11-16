"""Fluxo de publicação pelo Buffer: envio do vídeo ao R2, criação do post, acompanhamento e limpeza do arquivo.

Cada post vira uma `Publication` com `driver="buffer"`. Em `details` ficam o post do Buffer, a chave do vídeo no R2
(`r2_key`, com a conta e o bucket usados), `tracking` (verdadeiro enquanto falta saber o fim do post ou apagar o vídeo)
e `next_check` (quando conferir de novo). A sincronização consulta o Buffer e, quando o post sai (ou é apagado lá),
apaga o vídeo do R2.

A API do Buffer tem cota pequena no plano grátis (600 pedidos por dia e 1.000 por canal a cada 30 dias, somando
tudo), então a conferência é econômica: um post agendado só é consultado alguns minutos depois do horário; sem horário
(ou se ainda não saiu), o intervalo cresce (10 min, 30 min, 1 h, 3 h, 6 h, 12 h). Com o saldo de pedidos baixo, a
conferência de fundo espera. Post que falhou no Buffer mantém o vídeo no R2 por alguns dias (dá para tentar de novo
pelo próprio Buffer) e é conferido duas vezes por dia; depois disso o vídeo é apagado.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from studio.db import session_scope
from studio.models import Channel, Publication
from studio.workers.publish import buffer, r2

log = logging.getLogger(__name__)

DRIVER = "buffer"
# Status da Publication
SCHEDULED = "scheduled"  # na fila ou agendado no Buffer
UPLOADED = "uploaded"  # entregue ao Buffer, sem horário conhecido
PUBLISHED = "published"
FAILED = "failed"
CANCELED = "canceled"
MIN_INTERVAL = timedelta(minutes=5)  # registros antigos, sem `next_check`
AFTER_DUE = timedelta(minutes=5)  # primeira conferência depois do horário marcado
BACKOFF = (timedelta(minutes=10), timedelta(minutes=30), timedelta(hours=1), timedelta(hours=3), timedelta(hours=6),
           timedelta(hours=12))
FAILED_KEEP = timedelta(days=3)  # quanto tempo o vídeo de um post que falhou fica no R2
FAILED_CHECK = timedelta(hours=12)
UNCERTAIN_KEEP = timedelta(days=7)  # criação sem resposta: o post pode existir no Buffer
DELETABLE = ("draft", "needs_approval", "scheduled", "error")  # status do Buffer em que dá para apagar o post


def parse_time(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def status_for(post: dict[str, Any]) -> str:
    st = str(post.get("status") or "")
    if st == "sent":
        return PUBLISHED
    if st == "error":
        return FAILED
    return SCHEDULED if post.get("dueAt") else UPLOADED


# --------------------------------------------------------------------------------------------
# Canal do Buffer lembrado por canal do Vulto
# --------------------------------------------------------------------------------------------


def remembered_channels(channel_id: str | None) -> dict[str, str]:
    """Perfis do Buffer usados da última vez pelo canal do Vulto ({"tiktok": id, "instagram": id})."""
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return dict(((row.settings or {}).get("buffer") or {}) if row else {})


def remember_channel(channel_id: str | None, platform: str, buffer_channel_id: str) -> None:
    if not channel_id:
        return
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return
        settings = dict(row.settings or {})
        settings["buffer"] = {**(settings.get("buffer") or {}), platform: buffer_channel_id}
        row.settings = settings


# --------------------------------------------------------------------------------------------
# Limpeza e acompanhamento
# --------------------------------------------------------------------------------------------


def _cleanup(details: dict[str, Any], http: httpx.Client | None) -> dict[str, Any]:
    """Apaga o vídeo do R2 (uma vez). Em caso de falha, anota e tenta de novo na próxima sincronização."""
    key = details.get("r2_key")
    if not key or details.get("r2_deleted") or details.get("r2_left"):
        return details
    try:
        r2.delete(key, http=http, account_id=details.get("r2_account"), bucket=details.get("r2_bucket"))
    except r2.R2Moved as exc:
        log.warning("vídeo %s ficou no bucket antigo: %s", key, exc)
        return {**details, "cleanup_error": str(exc), "r2_left": True}
    except r2.R2Error as exc:
        log.warning("não foi possível apagar %s do R2: %s", key, exc)
        return {**details, "cleanup_error": str(exc)}
    out = {**details, "r2_deleted": True, "r2_deleted_at": datetime.now(UTC).isoformat()}
    out.pop("cleanup_error", None)
    return out


def _resolved(details: dict[str, Any]) -> bool:
    """O vídeo já saiu do R2 (ou não há o que apagar aqui)."""
    return bool(details.get("r2_deleted") or details.get("r2_left") or not details.get("r2_key"))


def _finish_details(details: dict[str, Any], final: bool, now: datetime | None = None) -> dict[str, Any]:
    details = {**details, "last_checked": (now or datetime.now(UTC)).isoformat()}
    details["tracking"] = not (final and _resolved(details))
    if not details["tracking"]:
        details.pop("next_check", None)
    return details


def next_check(status: str, due: datetime | None, checks: int, now: datetime) -> datetime:
    """Quando conferir o post de novo: depois do horário marcado ou com espera crescente."""
    if status == SCHEDULED and due is not None and due > now:
        return due + AFTER_DUE
    return now + BACKOFF[min(max(checks, 0), len(BACKOFF) - 1)]


def failed_details(details: dict[str, Any], now: datetime, keep: timedelta = FAILED_KEEP) -> dict[str, Any]:
    """Post que falhou: o vídeo fica no R2 por um prazo (para tentar de novo pelo Buffer) e a conferência fica rara."""
    keep_until = parse_time(details.get("keep_until")) or now + keep
    return {**details, "keep_until": keep_until.isoformat(), "tracking": True,
            "next_check": min(keep_until, now + FAILED_CHECK).isoformat()}


def _in_hold(details: dict[str, Any], now: datetime) -> bool:
    keep_until = parse_time(details.get("keep_until"))
    return keep_until is not None and now < keep_until


def _needs_buffer(status: str, post_id: str | None, details: dict[str, Any], now: datetime) -> bool:
    """Se conferir este item pede uma chamada à API do Buffer (os já terminados só precisam da limpeza do R2)."""
    if not post_id:
        return False
    if status in (SCHEDULED, UPLOADED):
        return True
    return status == FAILED and _in_hold(details, now)


def _pending(force: bool, now: datetime) -> list[tuple[str, bool]]:
    """(id, precisa do Buffer) dos posts acompanhados cuja hora de conferir chegou."""
    with session_scope() as s:
        rows = s.query(Publication).filter(Publication.driver == DRIVER).all()
        out = []
        for row in rows:
            d = row.details or {}
            if not d.get("tracking"):
                continue
            if not force:
                due = parse_time(d.get("next_check"))
                if due is None and (last := parse_time(d.get("last_checked"))):
                    due = last + MIN_INTERVAL
                if due is not None and due > now:
                    continue
            out.append((row.id, _needs_buffer(row.status, row.external_id, d, now)))
        return out


_NOT_FETCHED: Any = object()


def sync_one(publication_id: str, http: httpx.Client | None = None, *, now: datetime | None = None,
             post: Any = _NOT_FETCHED) -> dict[str, Any]:
    """Consulta o post no Buffer, atualiza a publicação e apaga o vídeo do R2 quando o post terminou.

    `post` permite passar um post já consultado (evita gastar outra chamada)."""
    now = now or datetime.now(UTC)
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        if row is None or row.driver != DRIVER:
            raise KeyError(publication_id)
        post_id, status, details, error = row.external_id, row.status, dict(row.details or {}), row.error
        old_due = parse_time(row.scheduled_at.isoformat()) if row.scheduled_at else None
    update: dict[str, Any] = {}
    final = status in (PUBLISHED, CANCELED) or (status == FAILED and not _in_hold(details, now))
    if not post_id and status != FAILED:
        final = True  # sem post no Buffer para acompanhar
    if not final and not post_id:
        # Falhou sem post para consultar (criação sem resposta): só espera o prazo do vídeo no R2.
        details = failed_details(details, now)
    elif not final:
        if post is _NOT_FETCHED:
            try:
                post = buffer.get_post(post_id, http=http)
            except buffer.BufferError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                post = None
        if not post:
            update = {"status": CANCELED, "error": "o post foi apagado no Buffer"}
        else:
            new_status = status_for(post)
            details["buffer_status"] = post.get("status")
            update = {"status": new_status}
            due = parse_time(post.get("dueAt"))
            if due is not None:
                update["scheduled_at"] = due
                details["publish_at"] = due.isoformat()
            if new_status == PUBLISHED:
                if post.get("externalLink"):
                    update["url"] = post["externalLink"]
                details["published_at"] = post.get("sentAt") or now.isoformat()
                update["error"] = None
            elif new_status == FAILED:
                update["error"] = ((post.get("error") or {}).get("message")) or "o Buffer não conseguiu publicar"
                if status != FAILED:
                    details.pop("keep_until", None)
                details = failed_details(details, now)
            else:
                if status == FAILED:  # tentaram de novo pelo Buffer e o post voltou para a fila
                    update["error"] = None
                    details.pop("keep_until", None)
                same = new_status == status and old_due == due
                details["checks"] = int(details.get("checks") or 0) + 1 if same else 0
                details["next_check"] = next_check(new_status, due, details["checks"], now).isoformat()
        final = update["status"] in (PUBLISHED, CANCELED)
    elif status == FAILED and not _resolved(details):
        # Prazo acabou: o vídeo sai do R2 e uma nova tentativa só enviando de novo pelo Vulto.
        note = "o vídeo saiu do R2; para tentar de novo, envie outra vez pelo Vulto"
        if note not in (error or ""):
            update["error"] = f"{error}. {note}" if error else note
    if final:
        details = _cleanup(details, http)
        if not _resolved(details):  # o R2 falhou: tenta apagar de novo daqui a pouco
            details["next_check"] = (now + BACKOFF[0]).isoformat()
    details = _finish_details(details, final, now)
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        if row is None:
            raise KeyError(publication_id)
        for field, value in update.items():
            setattr(row, field, value)
        row.details = details
        return {"id": row.id, "status": row.status, "url": row.url, "error": row.error,
                "tracking": details["tracking"], "r2_deleted": bool(details.get("r2_deleted"))}


def sync(force: bool = False, http: httpx.Client | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Acompanha os posts do Buffer cuja hora de conferir chegou (`force`: todos os pendentes, agora).

    Sem chave do Buffer, ou com o saldo de pedidos baixo (na conferência de fundo), só faz a limpeza do R2 dos posts
    que já terminaram."""
    now = now or datetime.now(UTC)
    pending = _pending(force, now)
    if not pending:
        return {"checked": 0, "items": []}
    reason = None
    if not buffer.api_key():
        reason = "sem chave do Buffer"
    elif not force and buffer.quota_low():
        reason = "saldo de pedidos da API do Buffer baixo; a conferência espera a janela do limite zerar"
    if reason:
        pending = [(pub_id, needs) for pub_id, needs in pending if not needs]
    items: list[dict[str, Any]] = []
    own = http is None
    client = http or buffer.new_client()
    blocked = False
    try:
        for pub_id, needs in pending:
            if needs and blocked:
                continue
            try:
                items.append(sync_one(pub_id, http=client, now=now))
            except buffer.BufferError as exc:
                items.append({"id": pub_id, "error": str(exc)})
                if exc.code in ("RATE_LIMIT_EXCEEDED", "UNAUTHORIZED", "NETWORK"):
                    blocked = True  # as próximas consultas dariam o mesmo erro; a limpeza do R2 continua
            except KeyError:
                continue
    finally:
        if own:
            client.close()
    out: dict[str, Any] = {"checked": len(items), "items": items}
    if reason:
        out["reason"] = reason
    return out


def sync_quietly() -> None:
    """Para o laço em segundo plano: nunca levanta erro."""
    try:
        result = sync()
        if result["checked"]:
            log.info("Buffer: %s publicações conferidas", result["checked"])
    except Exception as exc:  # noqa: BLE001 - laço de fundo não pode cair
        log.warning("sincronização do Buffer falhou: %s", exc)


def cancel(publication_id: str, http: httpx.Client | None = None) -> dict[str, Any]:
    """Apaga o post no Buffer (se ainda não saiu) e o vídeo do R2.

    Confere o post no Buffer antes: o status local pode estar atrasado (o post pode estar saindo ou já ter saído)."""
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        if row is None or row.driver != DRIVER:
            raise KeyError(publication_id)
        post_id, status, details = row.external_id, row.status, dict(row.details or {})
    if status == PUBLISHED:
        raise ValueError("o post já foi publicado; apague-o pela própria rede")
    own = http is None
    client = http or buffer.new_client()
    try:
        if post_id and status != CANCELED:
            try:
                post = buffer.get_post(post_id, http=client)
            except buffer.BufferError as exc:
                if exc.code != "NOT_FOUND":
                    raise
                post = None
            remote = str((post or {}).get("status") or "")
            if remote == "sent":
                sync_one(publication_id, http=client, post=post)
                raise ValueError("o post já foi publicado; apague-o pela própria rede")
            if remote == "sending":
                raise ValueError("o post está sendo publicado agora; tente de novo em instantes")
            if post and remote not in DELETABLE:
                raise ValueError(f"o Buffer informa o status “{remote}”; confira o post no Buffer")
            if post:
                try:
                    buffer.delete_post(post_id, http=client)
                except buffer.BufferError as exc:
                    if exc.code != "NOT_FOUND":
                        raise
        details = _finish_details(_cleanup(details, client), True)
    finally:
        if own:
            client.close()
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        assert row is not None
        if row.status not in (FAILED,):
            row.status = CANCELED
            row.error = None
        row.details = details
        return {"id": row.id, "status": row.status, "r2_deleted": bool(details.get("r2_deleted"))}
