"""Checagem das ideias da síntese contra o que o canal já fez e o banco de ideias, e conferência dos links de notícia.

- `repetida`: título com semelhança ≥ 0,75 a um vídeo feito (não vai para o banco, fica só no relatório);
- `parecida`: semelhança de 0,5 a 0,75 com um vídeo feito (vai para o banco, com o aviso no risco);
- `ja_no_banco`: o mesmo título normalizado de uma ideia que já existe no canal, ou semelhança ≥ 0,9 com ela (o mesmo
  título com as palavras em outra ordem, por exemplo); vale também contra as ideias gravadas antes na mesma rodada
  (devolve o id dela);
- `nova`: o resto.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Any

import httpx

from studio.config import get_settings
from studio.db import session_scope
from studio.media import safe_http
from studio.media.novelty import title_similarity
from studio.models import Channel, Idea, Project
from studio.workers.business.ideas import norm

log = logging.getLogger(__name__)

REPEATED = 0.75
BANK_REPEATED = 0.9  # mais alto que REPEATED: títulos curtos do mesmo nicho dividem muitas palavras
SIMILAR = 0.5
DONE_STATUSES = ("feita", "em_producao")
OPEN_STATUSES = ("nova", "aprovada")
VALIDATE_WORKERS = 6
VALIDATE_BUDGET_S = 60.0
PAGE_BYTES = 64_000


def done_titles(channel_id: str) -> list[dict[str, Any]]:
    """O que o canal já fez: vídeos recentes do YouTube, vídeos com métrica, projetos e ideias feitas/em produção."""
    from studio.workers.analytics.variables import load_videos

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(title: str | None, url: str = "") -> None:
        key = norm(title or "")
        if key and key not in seen:
            seen.add(key)
            out.append({"title": title, "url": url})

    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        recent = (((channel.settings or {}).get("youtube") or {}).get("recent_videos") or []) if channel else []
        projects = [p.title for p in s.query(Project).filter(Project.channel_id == channel_id)
                    .order_by(Project.created_at.desc()).all()]
        ideas = [i.title for i in s.query(Idea).filter(Idea.channel_id == channel_id,
                                                         Idea.status.in_(DONE_STATUSES)).all()]
    for v in recent:
        if isinstance(v, dict):
            add(v.get("title"), f"https://youtu.be/{v['video_id']}" if v.get("video_id") else "")
    try:
        for v in load_videos(channel_id):
            add(v.get("title"), f"https://youtu.be/{v['youtube_id']}")
    except Exception as exc:  # noqa: BLE001 - sem métricas, ficam as outras fontes
        log.debug("vídeos do canal %s não lidos: %s", channel_id, exc)
    for title in projects + ideas:
        add(title)
    return out


def open_ideas(channel_id: str) -> list[dict[str, Any]]:
    """Todas as ideias do canal (para o "já no banco") com o status."""
    with session_scope() as s:
        rows = s.query(Idea).filter(Idea.channel_id == channel_id).order_by(Idea.created_at.desc()).all()
        return [{"id": r.id, "title": r.title, "status": r.status} for r in rows]


def check_idea(idea: dict[str, Any], done: list[dict[str, Any]], existing: list[dict[str, Any]]) -> dict[str, Any]:
    """`{status, similar_to: {title, url}|None, similarity, idea_id}` (ver o topo do módulo)."""
    key = norm(idea["title"])
    for row in existing:
        if norm(row["title"]) == key:
            return {"status": "ja_no_banco", "similar_to": {"title": row["title"], "url": ""}, "similarity": 1.0,
                    "idea_id": row["id"]}
    bank_best, bank_match = 0.0, None
    for row in existing:
        sim = title_similarity(idea["title"], row["title"])
        if sim > bank_best:
            bank_best, bank_match = sim, row
    if bank_match is not None and bank_best >= BANK_REPEATED:
        return {"status": "ja_no_banco", "similar_to": {"title": bank_match["title"], "url": ""},
                "similarity": round(bank_best, 2), "idea_id": bank_match["id"]}
    best, match = 0.0, None
    for row in done:
        sim = title_similarity(idea["title"], row["title"])
        if sim > best:
            best, match = sim, row
    similar = {"title": match["title"], "url": match.get("url") or ""} if match else None
    if best >= REPEATED:
        return {"status": "repetida", "similar_to": similar, "similarity": round(best, 2), "idea_id": None}
    if best >= SIMILAR:
        return {"status": "parecida", "similar_to": similar, "similarity": round(best, 2), "idea_id": None}
    return {"status": "nova", "similar_to": None, "similarity": round(best, 2), "idea_id": None}


def _url_ok(url: str, http: httpx.Client) -> bool:
    try:
        safe_http.fetch(url, max_bytes=PAGE_BYTES, accept=safe_http.ACCEPT_HTML, http=http, truncate=True)
    except safe_http.FetchError:
        return False
    except Exception as exc:  # noqa: BLE001 - um link ruim nunca derruba a rodada
        log.debug("link %s não conferido: %s", url, exc)
        return False
    return True


def validate_urls(ideas: list[dict[str, Any]], *, http: httpx.Client | None = None,
                  budget_s: float = VALIDATE_BUDGET_S, workers: int = VALIDATE_WORKERS) -> list[str]:
    """Confere por HTTP os links "web" (notícias) das evidências e tira os que não abrem (ou que não responderam no
    tempo). Os links de sinais já vêm do YouTube. Devolve os links removidos. No modo offline sem cliente, não confere
    (nada sai para a rede) e os links ficam."""
    urls = list(dict.fromkeys(ev["url"] for i in ideas for ev in i["evidence"] if not ev.get("checked")))
    if not urls or (http is None and get_settings().offline):
        return []
    ok: set[str] = set()
    client = http or safe_http.new_client()
    pool = ThreadPoolExecutor(max_workers=max(1, min(workers, len(urls))))
    try:
        futures = {pool.submit(_url_ok, u, client): u for u in urls}
        deadline = time.monotonic() + budget_s
        pending = set(futures)
        while pending:
            left = deadline - time.monotonic()
            if left <= 0:
                log.warning("conferência dos links parou no tempo (%d sem resposta)", len(pending))
                break
            done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
            for future in done:
                if future.result():
                    ok.add(futures[future])
    finally:
        pool.shutdown(wait=False, cancel_futures=True)
        if http is None:
            client.close()
    removed = [u for u in urls if u not in ok]
    for idea in ideas:
        idea["evidence"] = [ev for ev in idea["evidence"] if ev.get("checked") or ev["url"] in ok]
        for ev in idea["evidence"]:
            ev["checked"] = True
    return removed
