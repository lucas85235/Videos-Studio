"""Variáveis de cada vídeo para comparar grupos (origem: `faixas` em estudio/melhoria.py do canais).

Hora e dia da publicação no fuso do canal (`PostingPlan.timezone`), faixas de duração e de nota com os mesmos cortes
do canais, tipo do Short (bloco B), origem (corte do longo ou original), formato e estilo.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel, Idea, Project, Publication
from studio.workers.analytics.metrics import latest_metrics, parse_when, views_per_day
from studio.workers.publish.schedule import plan_of

log = logging.getLogger(__name__)

VARIABLES: dict[str, str] = {
    "formato": "Formato",
    "tipo": "Tipo de Short",
    "faixa_duracao": "Duração",
    "faixa_nota": "Nota da ideia",
    "hora": "Hora da publicação",
    "dia": "Dia da publicação",
    "origem": "Origem",
    "estilo": "Estilo",
    "versao": "Versão (ramo A/B)",
}
# Variáveis comparadas entre os Shorts em "O que funcionou" (o formato já separa Shorts de longos).
SHORT_VARIABLES = ("tipo", "faixa_duracao", "faixa_nota", "hora", "dia", "origem", "estilo")
SHORT_MAX_S = 180  # Short tem no máximo 3 min
UNKNOWN_FORMAT = "desconhecido"  # sem estilo e sem duração (ex.: privado que a coleta nunca leu)

_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?$")


def parse_iso_duration(value: str | None) -> float | None:
    """`contentDetails.duration` do YouTube (ISO 8601, ex.: PT1M5S) em segundos."""
    m = _ISO_DURATION.match(value or "")
    if not m or not any(m.groups()):
        return None
    d, h, mi, s = m.groups()
    return int(d or 0) * 86400 + int(h or 0) * 3600 + int(mi or 0) * 60 + float(s or 0)


def hour_band(when: datetime) -> str:
    h = when.hour
    return "madrugada (0-6h)" if h < 6 else "manhã (6-12h)" if h < 12 else "tarde (12-18h)" if h < 18 else "noite (18-24h)"


def day_band(when: datetime) -> str:
    return "fim de semana" if when.weekday() >= 5 else "dia útil"


def duration_band(seconds: float) -> str:
    return "até 35 s" if seconds < 35 else "35 a 45 s" if seconds <= 45 else "mais de 45 s"


def score_band(score: float) -> str:
    return "nota abaixo de 7,5" if score < 7.5 else "nota 7,5 a 8" if score < 8 else "nota 8+"


def video_format(style_format: str | None, duration_s: float | None) -> str:
    """'short' se o estilo do projeto é Short ou o vídeo tem até 3 min; 'long' pelo estilo ou com mais de 3 min;
    sem estilo e sem duração, `UNKNOWN_FORMAT` (não dá para saber; antes virava 'long')."""
    if style_format == "short" or (duration_s is not None and duration_s <= SHORT_MAX_S):
        return "short"
    if style_format == "long" or duration_s is not None:
        return "long"
    return UNKNOWN_FORMAT


def video_variables(
    *,
    published_at: datetime | None = None,
    timezone: str | None = None,
    duration_s: float | None = None,
    style_format: str | None = None,
    score: float | None = None,
    short_tipo: str | None = None,
    parent_id: str | None = None,
    style_id: str | None = None,
    versao: str | None = None,
) -> dict[str, str]:
    """Valores das variáveis de um vídeo (só as que dá para saber; as outras ficam de fora).

    `tipo`, `origem` e `faixa_duracao` são só de Short (como no canais, onde o longo não tinha esses campos): um longo
    não entra num experimento sobre elas nem se mistura com os Shorts da variante "original".
    """
    formato = video_format(style_format, duration_s)
    out: dict[str, str] = {"formato": formato}
    if published_at is not None:
        local = published_at.astimezone(ZoneInfo(timezone or plan_of(None).timezone))
        out["hora"] = hour_band(local)
        out["dia"] = day_band(local)
    if score is not None:
        out["faixa_nota"] = score_band(score)
    if formato == "short":
        if duration_s is not None:
            out["faixa_duracao"] = duration_band(duration_s)
        out["tipo"] = short_tipo or "original"
        out["origem"] = "corte do longo" if parent_id else "original"
    if style_id:
        out["estilo"] = style_id
    if versao:
        out["versao"] = versao  # ramo A/B do projeto ("A" principal, "B"...): `Publication.details["versao"]`
    return out


# --------------------------------------------------------------------------------------------
# Catálogo: vídeos conhecidos (publicações do Vulto + vídeos recentes do canal) com variáveis e última métrica
# --------------------------------------------------------------------------------------------

def _style_format(style_id: str | None, memo: dict[str, str | None]) -> str | None:
    if not style_id:
        return None
    if style_id not in memo:
        try:
            memo[style_id] = get_style(style_id).format.type
        except Exception:  # noqa: BLE001 - estilo apagado: decide pela duração
            memo[style_id] = None
    return memo[style_id]


def _final_duration(project: Project | None, lang: str | None) -> float | None:
    """Duração do vídeo final renderizado (`settings["last_final"]["langs"][idioma]["duration"]`)."""
    if project is None:
        return None
    langs = ((project.settings or {}).get("last_final") or {}).get("langs") or {}
    entries = [langs.get(lang)] if lang in langs else []
    entries += [e for k, e in langs.items() if k != lang]
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("duration"), int | float):
            return float(entry["duration"])
    return None


def _analytics_cache(channels: Any) -> dict[str, dict[str, Any]]:
    """Dados dos vídeos guardados pela coleta (`settings["analytics"]["videos"]`) de todos os canais."""
    cache: dict[str, dict[str, Any]] = {}
    for c in channels:
        cache.update(((c.settings or {}).get("analytics") or {}).get("videos") or {})
    return cache


def known_published_at(youtube_id: str) -> datetime | None:
    """Quando o vídeo foi ao ar, pelo que o Vulto já sabe (mesma ordem de `load_videos`).

    Cache da coleta > `Publication.scheduled_at` > `recent_videos` do canal > `Publication.created_at`.
    """
    with session_scope() as s:
        channels = s.query(Channel).all()
        info = _analytics_cache(channels).get(youtube_id) or {}
        when = parse_when(info.get("published_at"))
        if when is not None:
            return when
        pub = (
            s.query(Publication)
            .filter(Publication.external_id == youtube_id, Publication.platform == "youtube")
            .order_by(Publication.created_at.desc())
            .first()
        )
        if pub is not None and pub.scheduled_at:
            return parse_when(pub.scheduled_at)
        for c in channels:
            for v in ((c.settings or {}).get("youtube") or {}).get("recent_videos") or []:
                if isinstance(v, dict) and v.get("video_id") == youtube_id and parse_when(v.get("published")):
                    return parse_when(v.get("published"))
        return parse_when(pub.created_at) if pub is not None and pub.created_at else None


def load_videos(channel_id: str | None = None) -> list[dict[str, Any]]:
    """Vídeos do YouTube conhecidos (do canal `channel_id`, ou de todos), cada um com variáveis e a última métrica.

    Fontes: `Publication` do YouTube com `external_id`, `Channel.settings["youtube"]["recent_videos"]` e vídeos que só
    têm métrica (lançada à mão). Os dados vindos da coleta (`settings["analytics"]["videos"]`) completam título,
    data de publicação e duração.
    """
    metrics = latest_metrics()
    out: dict[str, dict[str, Any]] = {}
    formats: dict[str, str | None] = {}
    with session_scope() as s:
        channels = {c.id: c for c in s.query(Channel).all()}
        cache = _analytics_cache(channels.values())
        scores = {
            i.project_id: i.score
            for i in s.query(Idea).filter(Idea.project_id.isnot(None), Idea.score.isnot(None)).all()
        }

        def add(yid: str, *, project: Project | None, pub: Publication | None, chan_id: str | None,
                title: str | None, published: Any) -> None:
            if not yid or yid in out or (channel_id and chan_id != channel_id):
                return
            info = cache.get(yid) or {}
            channel = channels.get(chan_id or "")
            when = (parse_when(info.get("published_at")) or (pub.scheduled_at if pub else None)
                    or parse_when(published) or (pub.created_at if pub else None))
            details = (pub.details or {}) if pub else {}
            # Publicação de um ramo A/B ("versão B"): duração e estilo são os do vídeo do ramo, gravados na publicação.
            on_branch = details.get("branch") not in (None, "main")
            style_id = (details.get("style_id") if on_branch else None) or (project.style_id if project else None)
            if on_branch:
                duration = float(details["duration"]) if isinstance(details.get("duration"), int | float) else None
            else:
                duration = _final_duration(project, pub.language if pub else None)
            if duration is None and isinstance(info.get("duration_s"), int | float):
                duration = float(info["duration_s"])
            short_of = (project.settings or {}).get("short_of") if project else None
            variables = video_variables(
                published_at=when,
                timezone=plan_of(channel.settings if channel else None).timezone,
                duration_s=duration,
                style_format=_style_format(style_id, formats) if project else None,
                score=scores.get(project.id) if project else None,
                short_tipo=(short_of or {}).get("tipo") if isinstance(short_of, dict) else None,
                parent_id=project.parent_id if project else None,
                style_id=style_id,
                versao=details.get("versao") or None,
            )
            metric = metrics.get(yid)
            out[yid] = {
                "youtube_id": yid,
                "title": info.get("title") or title or (project.title if project else None),
                "project_id": project.id if project else None,
                "channel_id": chan_id,
                "published_at": when.isoformat() if when else None,
                "duration_s": duration,
                "score": scores.get(project.id) if project else None,
                "format": variables["formato"],
                "privacy": info.get("privacy"),  # da última coleta (None: nunca lido)
                "variables": variables,
                "metric": metric,
                "views_per_day": views_per_day(metric),
            }

        pubs = (
            s.query(Publication, Project)
            .join(Project, Project.id == Publication.project_id)
            .filter(Publication.platform == "youtube", Publication.external_id.isnot(None))
            .order_by(Publication.created_at.desc())
            .all()
        )
        for pub, project in pubs:
            add(pub.external_id or "", project=project, pub=pub, chan_id=project.channel_id, title=project.title,
                published=None)
        for c in channels.values():
            for v in ((c.settings or {}).get("youtube") or {}).get("recent_videos") or []:
                if isinstance(v, dict):
                    add(v.get("video_id") or "", project=None, pub=None, chan_id=c.id, title=v.get("title"),
                        published=v.get("published"))
        for yid, m in metrics.items():
            add(yid, project=None, pub=None, chan_id=m.get("channel_id"), title=None, published=None)
    return sorted(out.values(), key=lambda v: v["published_at"] or "", reverse=True)
