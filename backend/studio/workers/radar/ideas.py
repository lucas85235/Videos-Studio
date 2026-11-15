"""Do radar para o banco de ideias (onda 1, bloco D): vídeo fora da curva (`origin="outlier"`) e canal para modelar
(`origin="radar"`) viram `Idea`; a pendência semanal "decidir os nichos" vai para Hoje/Pendências."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Idea, RadarChannel, RadarRun, RadarVideo
from studio.workers.business.ideas import idea_dict
from studio.workers.radar.analysis import fmt
from studio.workers.radar.catalog import load_catalog
from studio.workers.radar.collect import RadarError

PENDING_TEXT = "Ler o radar de nichos da semana e decidir nichos e ideias"
PENDING_WHERE = "/radar"


def iso_week(now: datetime | None = None) -> str:
    """Semana ISO local, "AAAA-Www"."""
    year, week, _ = (now or datetime.now(UTC)).astimezone().isocalendar()
    return f"{year}-W{week:02d}"


def _ratio(views: int, subs: int | None) -> str:
    r = views / max(subs or 0, 1000)
    return str(round(r)) if r >= 10 else f"{r:.1f}".replace(".", ",")


def _subs(subs: int | None) -> str:
    return f"{fmt(subs)} inscritos" if subs is not None else "inscritos escondidos"


def _first_term(niche: str, market: str) -> str:
    for n in load_catalog()["nichos"]:
        if n["id"] == niche:
            terms = n["en" if market == "us" else "pt"]
            return terms[0] if terms else ""
    return ""


def _check_channel(s: Any, channel_id: str | None) -> None:
    if channel_id and s.get(Channel, channel_id) is None:
        raise KeyError(f"canal não encontrado: {channel_id}")


def _existing(s: Any, channel_id: str | None, url: str) -> Idea | None:
    # a evidência sempre termina com "; <link>": comparar o fim evita que @abc case com @abcdef (e `_` vire curinga)
    q = s.query(Idea).filter(Idea.evidence.endswith("; " + url, autoescape=True))
    q = q.filter(Idea.channel_id.is_(None)) if channel_id is None else q.filter(Idea.channel_id == channel_id)
    return q.order_by(Idea.created_at).first()


def _create(
    s: Any, channel_id: str | None, title: str, origin: str, evidence: str, search: str, url: str
) -> dict[str, Any]:
    old = _existing(s, channel_id, url)
    if old is not None:
        return {"idea": idea_dict(old), "created": False}
    row = Idea(
        id=new_id("ide"),
        channel_id=channel_id,
        title=title[:300],
        format="long",
        origin=origin,
        evidence=evidence,
        search=search,
        status="nova",
    )
    s.add(row)
    s.flush()
    return {"idea": idea_dict(row), "created": True}


def _verdict(ch: RadarChannel | None) -> str:
    recent = (ch.recent if ch else None) or {}
    return recent.get("verdict") or "sem veredito"


def idea_from_video(run_id: int, youtube_id: str, channel_id: str | None = None) -> dict[str, Any]:
    """Vídeo fora da curva → ideia (`origin="outlier"`). Repetida (mesmo canal do Vulto e mesmo link) devolve a
    existente com `created: false`."""
    with session_scope() as s:
        _check_channel(s, channel_id)
        run = s.get(RadarRun, run_id)
        video = (
            (
                s.query(RadarVideo)
                .filter(RadarVideo.run_id == run_id, RadarVideo.youtube_id == youtube_id)
                .order_by(RadarVideo.id)
                .first()
            )
            if run
            else None
        )
        if video is None:
            raise RadarError(f"vídeo {youtube_id} não está na coleta {run_id}")
        ch = s.get(RadarChannel, video.channel_youtube_id)
        days = (run.created_at - video.published_at).days if video.published_at else 0
        subs = ch.subscribers if ch else None
        url = f"https://youtu.be/{youtube_id}"
        evidence = (
            f"{fmt(video.views)} views em {days} dias; canal com {_subs(subs)} "
            f"({_ratio(video.views, subs)}× inscritos); {_verdict(ch)}; {url}"
        )
        return _create(
            s,
            channel_id,
            video.title or youtube_id,
            "outlier",
            evidence,
            _first_term(video.niche, video.market),
            url,
        )


def idea_from_channel(run_id: int, channel_youtube_id: str, channel_id: str | None = None) -> dict[str, Any]:
    """Canal para modelar (veredito do radar) → ideia (`origin="radar"`), com o melhor vídeo dele na coleta."""
    with session_scope() as s:
        _check_channel(s, channel_id)
        run = s.get(RadarRun, run_id)
        ch = s.get(RadarChannel, channel_youtube_id)
        best = (
            (
                s.query(RadarVideo)
                .filter(RadarVideo.run_id == run_id, RadarVideo.channel_youtube_id == channel_youtube_id)
                .order_by(RadarVideo.views.desc())
                .first()
            )
            if run
            else None
        )
        if ch is None or best is None:
            raise RadarError(f"canal {channel_youtube_id} não está na coleta {run_id}")
        days = (run.created_at - best.published_at).days if best.published_at else 0
        recent = ch.recent or {}
        # o link é o do canal (o do melhor vídeo é a chave da ideia de vídeo fora da curva)
        url = ch.url or f"https://www.youtube.com/channel/{channel_youtube_id}"
        verdict = _verdict(ch) + (f" ({recent['format']})" if recent.get("format") else "")
        evidence = (
            f"{fmt(best.views)} views em {days} dias no melhor vídeo; canal com {_subs(ch.subscribers)} "
            f"({_ratio(best.views, ch.subscribers)}× inscritos); {verdict}; {url}"
        )
        title = f"No formato de {ch.name}: {best.title}"
        return _create(s, channel_id, title, "radar", evidence, _first_term(best.niche, best.market), url)


def ensure_pending(week: str | None = None) -> dict[str, Any] | None:
    """Pendência automática da semana para ler o radar e decidir (não duplica nem reabre)."""
    from studio.workers.ops.pending import ensure_auto

    return ensure_auto(f"radar:decidir:{week or iso_week()}", PENDING_TEXT, PENDING_WHERE)


def new_ideas_count() -> int:
    with session_scope() as s:
        return s.query(Idea).filter(Idea.status == "nova").count()
