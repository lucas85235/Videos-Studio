"""Tabelas do banco. O documento de projeto e os estilos ficam em JSON."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from studio.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """Data/hora guardada sempre em UTC e devolvida com fuso UTC.

    O SQLite não guarda fuso: sem isto, 18:00-03:00 voltaria como 18:00 "sem fuso" (3 h de erro) e a
    interface leria horários sem fuso como hora local. Valores sem fuso são tratados como UTC.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    niche: Mapped[str] = mapped_column(String(200), default="")
    persona: Mapped[str] = mapped_column(Text, default="")
    languages: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["pt-BR"])
    default_style_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    style_bible: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # Gestão do canal (campos que vinham do canal.toml). Todos com padrão simples para as linhas antigas.
    audience: Mapped[str] = mapped_column(Text, default="")  # público do canal
    cadence: Mapped[str] = mapped_column(Text, default="")  # cadência em texto livre
    posts_per_day: Mapped[int] = mapped_column(Integer, default=0)  # 0 = sem meta
    min_stock: Mapped[int] = mapped_column(Integer, default=0)  # 0 = sem alerta de estoque
    stock_format: Mapped[str] = mapped_column(String(10), default="short")  # short | long | any
    short_hashtag: Mapped[str] = mapped_column(String(60), default="")  # hashtag no fim do título dos Shorts
    credits_per_char: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = sem estimativa de créditos
    credit_price_usd: Mapped[float] = mapped_column(Float, default=0.0)  # 0 = mostra só créditos
    voice_defaults: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # {"final": {"provider", "voice"}}


class Style(Base):
    """Estilos do usuário. Os presets vêm de arquivos YAML e não ficam aqui."""

    __tablename__ = "styles"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    parent_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    channel_id: Mapped[str | None] = mapped_column(ForeignKey("channels.id", ondelete="SET NULL"), nullable=True)
    style_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    # Projeto longo de onde este Short foi cortado (sem FK: o ALTER TABLE do SQLite fica simples).
    parent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)


class ProjectVersion(Base):
    """Cada alteração no documento do projeto vira um commit (permite desfazer e comparar)."""

    __tablename__ = "project_versions"
    __table_args__ = (UniqueConstraint("project_id", "number"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    parent_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doc: Mapped[dict[str, Any]] = mapped_column(JSON)
    message: Mapped[str] = mapped_column(Text, default="")
    author: Mapped[str] = mapped_column(String(40), default="user")
    branch: Mapped[str] = mapped_column(String(20), default="main")
    """Ramo da versão: "main" (principal, a versão A) ou o nome de um ramo A/B ("b", "c"...)."""
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ProjectBranch(Base):
    """Ramo A/B de um projeto ("versão B do gancho"): uma linha de versões ao lado da principal.

    A principal não tem linha aqui (a cabeça dela é `Project.current_version`). O ramo guarda a própria cabeça e os
    últimos resultados da prévia e do vídeo final, que ficam em `outputs/<projeto>/ramos/<nome>/<idioma>`.
    """

    __tablename__ = "project_branches"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(20))
    letter: Mapped[str] = mapped_column(String(4))
    note: Mapped[str] = mapped_column(Text, default="")
    base_number: Mapped[int] = mapped_column(Integer)
    head_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="aberto")
    """aberto | mesclado | promovido | descartado"""
    last_preview: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    last_final: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    experiment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Artifact(Base):
    """Artefato gerado (narração, imagem, bloco de render...), endereçado pelo hash das entradas."""

    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("kind", "input_hash"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    output: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    provider: Mapped[str | None] = mapped_column(String(60), nullable=True)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class CostEntry(Base):
    __tablename__ = "cost_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(40))
    units: Mapped[float] = mapped_column(Float, default=0.0)
    unit_name: Mapped[str] = mapped_column(String(40), default="")
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ProviderCredential(Base):
    __tablename__ = "provider_credentials"

    provider: Mapped[str] = mapped_column(String(60), primary_key=True)
    secret: Mapped[str] = mapped_column(Text)
    """JSON criptografado (Fernet) com os campos da credencial."""
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ProviderUsage(Base):
    __tablename__ = "provider_usage"
    __table_args__ = (UniqueConstraint("provider", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(60))
    day: Mapped[str] = mapped_column(String(10))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[float] = mapped_column(Float, default=0.0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    quota_exhausted: Mapped[bool] = mapped_column(default=False)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class Publication(Base):
    __tablename__ = "publications"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    platform: Mapped[str] = mapped_column(String(30))
    language: Mapped[str] = mapped_column(String(10))
    driver: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="draft")
    scheduled_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    package_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


# --------------------------------------------------------------------------------------------
# Gestão de canal (ADR 0002): métricas, experimentos, lições, pendências, ideias e dinheiro
# --------------------------------------------------------------------------------------------


class VideoMetric(Base):
    """Números de um vídeo do YouTube num dia (uma linha por vídeo por dia). Origem: tabela `metricas`."""

    __tablename__ = "video_metrics"
    __table_args__ = (UniqueConstraint("youtube_id", "day"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    youtube_id: Mapped[str] = mapped_column(String(32), index=True)
    day: Mapped[str] = mapped_column(String(10))  # AAAA-MM-DD
    publication_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    channel_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    age_hours: Mapped[float | None] = mapped_column(Float, nullable=True)  # horas desde que foi ao ar
    views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    likes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    impressions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ctr: Mapped[float | None] = mapped_column(Float, nullable=True)  # % 0-100
    avg_view_s: Mapped[float | None] = mapped_column(Float, nullable=True)  # duração média vista (s)
    pct_watched: Mapped[float | None] = mapped_column(Float, nullable=True)  # % médio assistido, 0-100
    subscribers_gained: Mapped[int | None] = mapped_column(Integer, nullable=True)
    revenue_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    rpm: Mapped[float | None] = mapped_column(Float, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")  # youtube_api|youtube_analytics|manual|public
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class Experiment(Base):
    """Hipótese sobre uma variável, com variantes comparadas por uma métrica. Origem: `experimentos`."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("exp")
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), index=True, nullable=True
    )
    hypothesis: Mapped[str] = mapped_column(Text, default="")
    variable: Mapped[str] = mapped_column(String(60), default="")
    variants: Mapped[list[str]] = mapped_column(JSON, default=list)
    metric: Mapped[str] = mapped_column(String(30), default="views")  # views|views_dia|ctr|retencao|avd_s|likes
    project_ids: Mapped[list[str]] = mapped_column(JSON, default=list)  # vazio = todos os vídeos do canal
    status: Mapped[str] = mapped_column(String(20), default="aberto")  # aberto|concluido|abandonado
    result: Mapped[str] = mapped_column(Text, default="")
    learning: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Lesson(Base):
    """Lição aprendida (proposta, aplicada ou descartada). Origem: `licoes`."""

    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("lic")
    scope: Mapped[str] = mapped_column(String(120), default="global")  # global|canal:<id>|estilo:<id>|formato:...
    text: Mapped[str] = mapped_column(Text, default="")
    evidence: Mapped[str] = mapped_column(Text, default="")
    origin: Mapped[str] = mapped_column(String(40), default="manual")  # manual|experimento|metricas|retro
    experiment_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    project_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="proposta")  # proposta|aplicada|descartada
    applied_where: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class PendingTask(Base):
    """Pendência do canal (à mão ou criada por rotina). Origem: `pendencias`.

    O "onde fazer" fica em `where_hint` (`where` é palavra reservada no SQL); na API o campo se chama `where`.
    """

    __tablename__ = "pending_tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("pen")
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), index=True, nullable=True
    )
    text: Mapped[str] = mapped_column(Text, default="")
    where_hint: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(20), default="manual")  # manual|auto
    auto_key: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)  # rotina: não duplica
    status: Mapped[str] = mapped_column(String(20), default="aberta")  # aberta|feita|descartada
    order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    done_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class Idea(Base):
    """Ideia de vídeo no banco de ideias. Origem: `ideias`."""

    __tablename__ = "ideas"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("ide")
    channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), index=True, nullable=True
    )
    title: Mapped[str] = mapped_column(String(300))
    angle: Mapped[str] = mapped_column(Text, default="")
    format: Mapped[str] = mapped_column(String(20), default="")  # short|long|""
    origin: Mapped[str] = mapped_column(String(60), default="manual")
    evidence: Mapped[str] = mapped_column(Text, default="")
    search: Mapped[str] = mapped_column(Text, default="")  # busca que trouxe a ideia
    score: Mapped[float | None] = mapped_column(Float, nullable=True)  # 0-10
    # nova|aprovada|rejeitada|em_producao|feita|descartada
    status: Mapped[str] = mapped_column(String(20), default="nova")
    reason: Mapped[str] = mapped_column(Text, default="")
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class AffiliateOffer(Base):
    """Oferta de afiliado do canal. Origem: `[[monetizacao.oferta]]` do canal.toml."""

    __tablename__ = "affiliate_offers"
    __table_args__ = (UniqueConstraint("channel_id", "slug"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("ofe")
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    slug: Mapped[str] = mapped_column(String(60))  # id legível, ex. "gta6-ps5"
    name: Mapped[str] = mapped_column(String(200))
    store: Mapped[str] = mapped_column(String(40), default="amazon")
    asin: Mapped[str] = mapped_column(String(20), default="")
    link: Mapped[str] = mapped_column(Text, default="")
    text: Mapped[str] = mapped_column(Text, default="")  # linha da descrição
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)  # "sempre" entra em todo vídeo
    status: Mapped[str] = mapped_column(String(20), default="ideia")  # ideia|ativa|pausada|descartada
    checked_on: Mapped[str] = mapped_column(String(10), default="")  # AAAA-MM-DD da última conferência
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class RevenueEntry(Base):
    """Receita de uma loja num mês: total da loja (tracking_id "") ou por ID de rastreamento. Origem: `receita`."""

    __tablename__ = "revenue_entries"
    __table_args__ = (UniqueConstraint("channel_id", "store", "period", "tracking_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    store: Mapped[str] = mapped_column(String(40))  # amazon, mercadolivre, adsense...
    period: Mapped[str] = mapped_column(String(7))  # AAAA-MM
    tracking_id: Mapped[str] = mapped_column(String(60), default="")  # "" = linha de total da loja
    clicks: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sales: Mapped[float | None] = mapped_column(Float, nullable=True)
    commission: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


# --------------------------------------------------------------------------------------------
# Qualidade e pesquisa (ADR 0003): radar de nichos, checagem de fatos, capturas, referências e
# relatórios de conferência (áudio e vídeo final)
# --------------------------------------------------------------------------------------------


class RadarRun(Base):
    """Uma coleta do radar de nichos. Origem: `radar_coletas` (estudio/radar.py)."""

    __tablename__ = "radar_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    # "chave própria (YOUTUBE_API_KEY)" | "token do canal <título>"
    source: Mapped[str] = mapped_column(String(120), default="")
    searches: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)  # cota gasta: busca = 100, o resto = 1
    videos: Mapped[int] = mapped_column(Integer, default=0)
    channels: Mapped[int] = mapped_column(Integer, default=0)
    # "nicho/mercado" sem nenhuma busca; "(falhou)" no fim quando a busca falhou
    skipped: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|falhou
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # dias, hit_us, hit_br, novo_meses, candidatos, orcamento, nichos, mercados
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RadarNiche(Base):
    """Agregado de um nicho num mercado numa coleta. Origem: `radar_nichos`."""

    __tablename__ = "radar_niches"
    __table_args__ = (UniqueConstraint("run_id", "niche", "market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("radar_runs.id", ondelete="CASCADE"), index=True)
    niche: Mapped[str] = mapped_column(String(60))
    market: Mapped[str] = mapped_column(String(2))  # us|br
    n: Mapped[int] = mapped_column(Integer, default=0)
    h100: Mapped[int] = mapped_column(Integer, default=0)
    h500: Mapped[int] = mapped_column(Integer, default=0)
    new_hit: Mapped[int] = mapped_column(Integer, default=0)
    best_new: Mapped[int] = mapped_column(Integer, default=0)
    n10k: Mapped[int] = mapped_column(Integer, default=0)
    best: Mapped[int] = mapped_column(Integer, default=0)
    med: Mapped[float] = mapped_column(Float, default=0.0)
    p75: Mapped[float] = mapped_column(Float, default=0.0)
    new_share: Mapped[float] = mapped_column(Float, default=0.0)  # de 0 a 1
    subs_med: Mapped[float] = mapped_column(Float, default=0.0)
    length_med: Mapped[float] = mapped_column(Float, default=0.0)  # minutos


class RadarVideo(Base):
    """Vídeo visto numa coleta do radar (por nicho e mercado). Origem: `radar_videos`."""

    __tablename__ = "radar_videos"
    __table_args__ = (UniqueConstraint("run_id", "youtube_id", "niche", "market"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("radar_runs.id", ondelete="CASCADE"), index=True)
    youtube_id: Mapped[str] = mapped_column(String(20), index=True)
    niche: Mapped[str] = mapped_column(String(60))
    market: Mapped[str] = mapped_column(String(2))
    title: Mapped[str] = mapped_column(Text, default="")
    channel_youtube_id: Mapped[str] = mapped_column(String(32), index=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    duration_s: Mapped[int] = mapped_column(Integer, default=0)


class RadarChannel(Base):
    """Canal do YouTube visto pelo radar (atualizado a cada coleta). Origem: `radar_canais`."""

    __tablename__ = "radar_channels"

    youtube_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    country: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_on: Mapped[str | None] = mapped_column(String(10), nullable=True)  # AAAA-MM-DD
    subscribers: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None: canal esconde inscritos
    views: Mapped[int] = mapped_column(Integer, default=0)
    videos: Mapped[int] = mapped_column(Integer, default=0)
    # saída de `recentes`: latest, med, hits, settled, up30, typLen, verdict, format, risk
    recent: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    alert: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class FactClaim(Base):
    """Afirmação do roteiro conferida numa fonte. Origem: itens de roteiro/checagem-<slug>.json."""

    __tablename__ = "fact_claims"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("fat")
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)  # id do job de checagem
    segment_id: Mapped[str] = mapped_column(String(32), index=True)
    scene_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # citação de cena "quote"
    lang: Mapped[str] = mapped_column(String(10), default="pt-BR")
    # sha256 do texto do segmento na checagem (16 hex): mudou → "desatualizada"
    segment_hash: Mapped[str] = mapped_column(String(16), default="")
    excerpt: Mapped[str] = mapped_column(Text, default="")  # trecho literal do roteiro que a afirmação cobre
    claim: Mapped[str] = mapped_column(Text)
    verdict: Mapped[str] = mapped_column(String(20), default="sem_fonte")  # ok|corrigir|sem_fonte|boato
    source_title: Mapped[str] = mapped_column(String(300), default="")
    url: Mapped[str] = mapped_column(Text, default="")
    quote: Mapped[str] = mapped_column(Text, default="")  # trecho literal da fonte, 5 a 30 palavras
    quote_found: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # None = não conferido
    check_note: Mapped[str] = mapped_column(Text, default="")  # por que não conferiu
    correction: Mapped[str] = mapped_column(Text, default="")  # texto novo para `excerpt`
    note: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="aberta")  # aberta|aplicada|ignorada
    checked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class PageCapture(Base):
    """Captura de página com trechos marcados (prova na tela). Origem: assets/web/shots.json."""

    __tablename__ = "page_captures"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("cap")
    project_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    scene_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    url: Mapped[str] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(200), default="")
    title: Mapped[str] = mapped_column(String(300), default="")
    image: Mapped[str] = mapped_column(String(300), default="")  # PNG da página capturada (relativo)
    proof: Mapped[str] = mapped_column(String(300), default="")  # imagem final marcada e recortada (relativa)
    width: Mapped[int] = mapped_column(Integer, default=0)  # px do PNG, já com o DPR
    height: Mapped[int] = mapped_column(Integer, default=0)
    dark: Mapped[bool] = mapped_column(Boolean, default=False)  # página escura: marca-texto sem multiply
    # [{"text", "rects": [[x,y,w,h] 0..1], "view": [x0,y0,x1,y1] | None, "lock": bool}]
    phrases: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    text_path: Mapped[str] = mapped_column(String(300), default="")  # innerText salvo (checagem literal)
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok|parcial|falhou
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ReferenceVideo(Base):
    """Vídeo de referência analisado em planos. Origem: assets/trailers/planos-<nome>.json."""

    __tablename__ = "reference_videos"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)  # new_id("ref")
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(Text, default="")
    path: Mapped[str] = mapped_column(String(300), default="")  # vídeo relativo à pasta de dados
    info: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # w, h, fps, codec, dur
    shots: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)  # [{"k", "a", "b", "dur"}]
    sheets: Mapped[list[str]] = mapped_column(JSON, default=list)  # folhas JPG relativas
    # shots, median_s, cuts_per_min, palette: ["#rrggbb"...]
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="novo")  # novo|pronto|falhou
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class QualityReport(Base):
    """Relatório de conferência: áudio (kind "audio", bloco G) ou vídeo final (kind "final", bloco H).

    Cada item: {"code", "level": "ok"|"aviso"|"erro", "title", "detail", "at_s", "scene_id"}.
    """

    __tablename__ = "quality_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # audio|final
    lang: Mapped[str] = mapped_column(String(10))
    version: Mapped[int] = mapped_column(Integer, default=0)  # Project.current_version na conferência
    source: Mapped[str] = mapped_column(String(300), default="")  # arquivo conferido (relativo): mix ou vídeo
    status: Mapped[str] = mapped_column(String(10), default="ok")  # ok|aviso|erro: o pior dos itens
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # medidas brutas do bloco
    contact_sheet: Mapped[str] = mapped_column(String(300), default="")  # folha de contato (relativa)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class YouTubeQuotaUsage(Base):
    """Cota da YouTube Data API gasta por dia (no fuso do Pacífico, como o Google conta) e por método."""

    __tablename__ = "youtube_quota_usage"
    __table_args__ = (UniqueConstraint("day", "method"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    day: Mapped[str] = mapped_column(String(10), index=True)
    method: Mapped[str] = mapped_column(String(40))
    calls: Mapped[int] = mapped_column(Integer, default=0)
    units: Mapped[int] = mapped_column(Integer, default=0)


class YouTubeComment(Base):
    """Fio de comentários de um vídeo do canal, lido pelo `commentThreads.list` (caixa de comentários).

    O id é o do fio no YouTube. `read`, `suggestion` e a resposta enviada pelo app são só do Vulto: ler de novo os
    comentários não apaga o que você marcou nem a sugestão.
    """

    __tablename__ = "youtube_comments"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)  # id do fio (commentThread)
    channel_id: Mapped[str] = mapped_column(ForeignKey("channels.id", ondelete="CASCADE"), index=True)
    youtube_channel_id: Mapped[str] = mapped_column(String(40), default="")
    video_id: Mapped[str] = mapped_column(String(40), default="", index=True)
    video_title: Mapped[str] = mapped_column(String(300), default="")
    comment_id: Mapped[str] = mapped_column(String(100), default="")  # comentário principal (parentId da resposta)
    author: Mapped[str] = mapped_column(String(200), default="")
    author_channel_id: Mapped[str] = mapped_column(String(40), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    reply_count: Mapped[int] = mapped_column(Integer, default=0)
    can_reply: Mapped[bool] = mapped_column(Boolean, default=True)
    is_question: Mapped[bool] = mapped_column(Boolean, default=False)
    answered: Mapped[bool] = mapped_column(Boolean, default=False)  # o canal já respondeu (no YouTube ou pelo app)
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    suggestion: Mapped[str] = mapped_column(Text, default="")
    suggestion_provider: Mapped[str] = mapped_column(String(60), default="")
    reply_text: Mapped[str] = mapped_column(Text, default="")  # resposta enviada pelo app
    reply_id: Mapped[str] = mapped_column(String(100), default="")
    replied_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, index=True)
    updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
