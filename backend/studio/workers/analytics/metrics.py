"""Métricas por vídeo e dia (origem: tabela `metricas` do canais, `cmd_metricas_add` e `rotinas.gravar_metrica`).

Uma linha por vídeo do YouTube por dia. Gravar é sempre um upsert com COALESCE: a coleta automática nunca troca um
número por vazio (um campo `None` mantém o que já estava); um número novo, da coleta ou lançado à mão, entra.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from studio.db import session_scope
from studio.models import Project, Publication, VideoMetric

# Números de uma linha (o que a coleta ou o lançamento à mão podem preencher).
METRIC_FIELDS = (
    "age_hours", "views", "likes", "comments", "impressions", "ctr", "avg_view_s", "pct_watched",
    "subscribers_gained", "revenue_usd", "rpm",
)
INT_FIELDS = ("views", "likes", "comments", "impressions", "subscribers_gained")
SOURCES = ("youtube_api", "youtube_analytics", "manual", "public")


def today() -> str:
    """Dia de hoje na hora local (a mesma base do lançamento à mão no navegador)."""
    return date.today().isoformat()


def day_of(now: datetime) -> str:
    """Dia (AAAA-MM-DD) de um instante na hora local. A coleta e o lançamento à mão usam a mesma base: com UTC numa
    ponta e hora local na outra, a coleta das 21h às 24h (Brasília) gravava no dia seguinte e escondia o que foi lançado
    à mão na mesma noite."""
    return _aware(now).astimezone().date().isoformat()


def day_end(day: str) -> datetime:
    """Fim do dia `day` (23:59:59 na hora local), com fuso."""
    return datetime.combine(date.fromisoformat(day), time(23, 59, 59)).astimezone()


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def parse_when(value: str | datetime | None) -> datetime | None:
    """Data ISO (com "Z" ou fuso) em datetime com fuso; vazio ou inválido vira None."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return _aware(value)
    try:
        return _aware(datetime.fromisoformat(str(value).replace("Z", "+00:00")))
    except ValueError:
        return None


def age_hours(published_at: datetime | None, now: datetime | None = None) -> float | None:
    """Horas desde que o vídeo foi ao ar (agendado: da hora em que saiu; nunca negativo)."""
    if published_at is None:
        return None
    now = _aware(now or datetime.now(UTC))
    return round(max(0.0, (now - _aware(published_at)).total_seconds() / 3600), 1)


def metric_dict(row: VideoMetric) -> dict[str, Any]:
    return {
        "youtube_id": row.youtube_id,
        "day": row.day,
        "publication_id": row.publication_id,
        "project_id": row.project_id,
        "channel_id": row.channel_id,
        **{k: getattr(row, k) for k in METRIC_FIELDS},
        "source": row.source,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _link(s: Any, youtube_id: str) -> tuple[str | None, str | None, str | None]:
    """(publication_id, project_id, channel_id) do vídeo pela `Publication.external_id`."""
    pub = (
        s.query(Publication)
        .filter(Publication.external_id == youtube_id, Publication.platform == "youtube")
        .order_by(Publication.created_at.desc())
        .first()
    )
    if pub is None:
        return None, None, None
    project = s.get(Project, pub.project_id)
    return pub.id, pub.project_id, project.channel_id if project else None


def upsert_metric(
    youtube_id: str,
    day: str | None = None,
    source: str = "manual",
    *,
    published_at: datetime | None = None,
    channel_id: str | None = None,
    now: datetime | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Grava os números de `youtube_id` no dia `day` (AAAA-MM-DD, hoje se vazio), com COALESCE.

    `published_at` calcula `age_hours` quando ele não vem nos campos. `channel_id` só vale para vídeo sem
    `Publication` (vídeo do canal enviado fora do Vulto): a ligação pela publicação manda.
    """
    unknown = set(fields) - set(METRIC_FIELDS)
    if unknown:
        raise ValueError(f"campo de métrica desconhecido: {', '.join(sorted(unknown))}")
    day = day or (day_of(now) if now else today())
    if fields.get("age_hours") is None and published_at is not None:
        fields["age_hours"] = age_hours(published_at, now)
    values = {k: (int(v) if k in INT_FIELDS and v is not None else v) for k, v in fields.items()}
    with session_scope() as s:
        pub_id, project_id, linked_channel = _link(s, youtube_id)
        row = s.query(VideoMetric).filter(VideoMetric.youtube_id == youtube_id, VideoMetric.day == day).first()
        if row is None:
            row = VideoMetric(youtube_id=youtube_id, day=day, source=source)
            s.add(row)
        for key, value in values.items():
            if value is not None:  # COALESCE(novo, antigo): vazio nunca apaga um número
                setattr(row, key, value)
        row.source = source
        row.publication_id = pub_id or row.publication_id
        row.project_id = project_id or row.project_id
        row.channel_id = linked_channel or row.channel_id or channel_id
        row.updated_at = datetime.now(UTC)
        s.flush()
        return metric_dict(row)


def latest_metrics(channel_id: str | None = None) -> dict[str, dict[str, Any]]:
    """Última leitura de cada vídeo; com `channel_id`, só os vídeos daquele canal.

    Campo a campo, vale o número não vazio mais recente: o CTR ou as impressões lançados à mão num dia continuam na
    leitura depois que a coleta do dia seguinte cria uma linha nova sem eles. `day`, `source` e `updated_at` são os da
    linha mais recente.
    """
    out: dict[str, dict[str, Any]] = {}
    with session_scope() as s:
        for row in s.query(VideoMetric).order_by(VideoMetric.youtube_id, VideoMetric.day).all():
            data = metric_dict(row)
            prev = out.get(row.youtube_id)
            if prev is not None:
                for key in (*METRIC_FIELDS, "publication_id", "project_id", "channel_id"):
                    if data[key] is None:
                        data[key] = prev[key]
            out[row.youtube_id] = data
    if channel_id:
        out = {k: v for k, v in out.items() if v["channel_id"] == channel_id}
    return out


def history(youtube_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.query(VideoMetric).filter(VideoMetric.youtube_id == youtube_id).order_by(VideoMetric.day).all()
        return [metric_dict(r) for r in rows]


def views_per_day(metric: dict[str, Any] | None) -> float | None:
    """views / max(idade em dias, 1), como no canais (idade vazia conta 24 h)."""
    if not metric or metric.get("views") is None:
        return None
    return metric["views"] / max((metric.get("age_hours") or 24) / 24, 1)
