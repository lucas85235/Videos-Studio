"""Fluxo de publicação pelo Buffer: envio do vídeo ao R2, criação do post, acompanhamento e limpeza do arquivo.

Cada post vira uma `Publication` com `driver="buffer"`. Em `details` ficam o post do Buffer, a chave do vídeo no R2
(`r2_key`) e `tracking` (verdadeiro enquanto falta saber o fim do post ou apagar o vídeo). A sincronização consulta
o Buffer e, quando o post sai (ou falha, ou é apagado lá), apaga o vídeo do R2.
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
MIN_INTERVAL = timedelta(minutes=5)  # não consulta o mesmo post mais de uma vez nesse intervalo (limite da API)


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
    if not key or details.get("r2_deleted"):
        return details
    try:
        r2.delete(key, http=http)
    except r2.R2Error as exc:
        log.warning("não foi possível apagar %s do R2: %s", key, exc)
        return {**details, "cleanup_error": str(exc)}
    out = {**details, "r2_deleted": True, "r2_deleted_at": datetime.now(UTC).isoformat()}
    out.pop("cleanup_error", None)
    return out


def _finish_details(details: dict[str, Any], final: bool) -> dict[str, Any]:
    details = {**details, "last_checked": datetime.now(UTC).isoformat()}
    details["tracking"] = not (final and (details.get("r2_deleted") or not details.get("r2_key")))
    return details


def _pending_ids(force: bool) -> list[str]:
    now = datetime.now(UTC)
    with session_scope() as s:
        rows = s.query(Publication).filter(Publication.driver == DRIVER).all()
        out = []
        for row in rows:
            d = row.details or {}
            if not d.get("tracking"):
                continue
            last = parse_time(d.get("last_checked"))
            if not force and last and now - last < MIN_INTERVAL:
                continue
            out.append(row.id)
        return out


def sync_one(publication_id: str, http: httpx.Client | None = None) -> dict[str, Any]:
    """Consulta o post no Buffer, atualiza a publicação e apaga o vídeo do R2 quando o post terminou."""
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        if row is None or row.driver != DRIVER:
            raise KeyError(publication_id)
        post_id, status, details = row.external_id, row.status, dict(row.details or {})
    final = status in (PUBLISHED, FAILED, CANCELED)
    update: dict[str, Any] = {}
    if not final and post_id:
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
            if (due := parse_time(post.get("dueAt"))) is not None:
                update["scheduled_at"] = due
            if new_status == PUBLISHED:
                if post.get("externalLink"):
                    update["url"] = post["externalLink"]
                details["published_at"] = post.get("sentAt") or datetime.now(UTC).isoformat()
            elif new_status == FAILED:
                update["error"] = ((post.get("error") or {}).get("message")) or "o Buffer não conseguiu publicar"
        final = update["status"] in (PUBLISHED, FAILED, CANCELED)
    if final:
        details = _cleanup(details, http)
    details = _finish_details(details, final)
    with session_scope() as s:
        row = s.get(Publication, publication_id)
        if row is None:
            raise KeyError(publication_id)
        for field, value in update.items():
            setattr(row, field, value)
        row.details = details
        return {"id": row.id, "status": row.status, "url": row.url, "error": row.error,
                "tracking": details["tracking"], "r2_deleted": bool(details.get("r2_deleted"))}


def sync(force: bool = False, http: httpx.Client | None = None) -> dict[str, Any]:
    """Acompanha todos os posts do Buffer pendentes. Sem chave do Buffer, não faz nada."""
    ids = _pending_ids(force)
    if not ids:
        return {"checked": 0, "items": []}
    if not buffer.api_key():
        return {"checked": 0, "items": [], "reason": "sem chave do Buffer"}
    items: list[dict[str, Any]] = []
    own = http is None
    client = http or buffer.new_client()
    try:
        for pub_id in ids:
            try:
                items.append(sync_one(pub_id, http=client))
            except buffer.BufferError as exc:
                items.append({"id": pub_id, "error": str(exc)})
                if exc.code in ("RATE_LIMIT_EXCEEDED", "UNAUTHORIZED", "NETWORK"):
                    break  # as próximas dariam o mesmo erro
            except KeyError:
                continue
    finally:
        if own:
            client.close()
    return {"checked": len(items), "items": items}


def sync_quietly() -> None:
    """Para o laço em segundo plano: nunca levanta erro."""
    try:
        result = sync()
        if result["checked"]:
            log.info("Buffer: %s publicações conferidas", result["checked"])
    except Exception as exc:  # noqa: BLE001 - laço de fundo não pode cair
        log.warning("sincronização do Buffer falhou: %s", exc)


def cancel(publication_id: str, http: httpx.Client | None = None) -> dict[str, Any]:
    """Apaga o post no Buffer (se ainda não saiu) e o vídeo do R2."""
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
        if post_id and status not in (FAILED, CANCELED):
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
