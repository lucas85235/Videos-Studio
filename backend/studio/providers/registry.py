"""Registro de provedores e execução de cadeias com fallback."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from studio.config import get_settings
from studio.db import session_scope
from studio.models import CostEntry, ProviderUsage
from studio.providers import credentials
from studio.providers.align import EstimateAligner, FasterWhisperAligner
from studio.providers.base import (
    NotConfigured,
    Provider,
    ProviderError,
    QuotaExceeded,
    Refused,
    TransientError,
)
from studio.providers.image import AntigravityCLIImage, CodexCLIImage, PexelsStock, PlaceholderImage
from studio.providers.kokoro import KokoroTTS
from studio.providers.llm import ClaudeCodeLLM, MockLLM
from studio.providers.tts import (
    ElevenLabsTTS,
    GeminiTTS,
    GoogleChirp3HD,
    MockTTS,
    Qwen3LocalTTS,
    WindowsSapiTTS,
)
from studio.providers.video import MockVideo, kling_3_turbo, kling_turbo

log = logging.getLogger(__name__)


_PROVIDERS: dict[str, Provider] = {}
_usage_lock = threading.Lock()


def _register(*providers: Provider) -> None:
    for provider in providers:
        _PROVIDERS[provider.info.id] = provider


_register(
    ClaudeCodeLLM(),
    MockLLM(),
    GeminiTTS(),
    GoogleChirp3HD(),
    ElevenLabsTTS(),
    Qwen3LocalTTS(),
    KokoroTTS(),
    WindowsSapiTTS(),
    MockTTS(),
    CodexCLIImage(),
    AntigravityCLIImage(),
    PexelsStock(),
    PlaceholderImage(),
    FasterWhisperAligner(),
    EstimateAligner(),
    kling_turbo(),
    kling_3_turbo(),
    MockVideo(),
)


def _register_publishers() -> None:
    from studio.providers.media_search import WikimediaCommonsSource
    from studio.workers.publish.buffer import BufferPublisher
    from studio.workers.publish.instagram import InstagramPublisher
    from studio.workers.publish.r2 import R2Storage
    from studio.workers.publish.youtube import YouTubePublisher

    _register(YouTubePublisher(), InstagramPublisher(), BufferPublisher(), R2Storage(), WikimediaCommonsSource())


_register_publishers()

# Provedores de último recurso (sempre funcionam, sem rede). Os de prévia são bloqueados na publicação.
DEV_FALLBACKS: dict[str, list[str]] = {
    "llm": [],  # sem roteiro de exemplo: sem o Claude, o job falha e diz por quê
    "tts": ["windows_sapi", "mock_tts"],
    "image": ["placeholder"],
    "align": ["estimate"],
    "video": [],  # sem vídeo IA a cena fica com a imagem e o movimento de câmera (nada é gerado nem cobrado)
}
OFFLINE_ONLY: dict[str, list[str]] = {
    "llm": ["mock_llm"],
    "tts": ["mock_tts"],
    "image": ["placeholder"],
    "align": ["estimate"],
    "video": ["mock_video"],
}
# Voz da prévia: local e grátis, para conferir o conteúdo antes de gastar a cota da voz final (README §8.4).
PREVIEW_TTS_CHAIN: list[str] = ["kokoro_local"]
DEFAULT_CHAINS: dict[str, list[str]] = {
    "llm": ["claude_code"],
    "tts": ["gemini_tts", "google_chirp3hd", "qwen3_tts_local"],
    "image": ["codex_cli", "antigravity_cli"],
    "align": ["faster_whisper"],
    "video": ["fal_kling_turbo", "fal_kling_3"],
}


def get(provider_id: str) -> Provider:
    try:
        return _PROVIDERS[provider_id]
    except KeyError as exc:
        raise ProviderError(f"provedor desconhecido: {provider_id}") from exc


def all_providers() -> list[Provider]:
    return list(_PROVIDERS.values())


def resolve_chain(kind: str, configured: list[str] | None) -> list[str]:
    """Cadeia efetiva: a configurada (filtrada pelo tipo) + os fallbacks de desenvolvimento."""
    if get_settings().offline:
        return list(OFFLINE_ONLY[kind])
    chain = [p for p in (configured or DEFAULT_CHAINS[kind]) if p in _PROVIDERS and _PROVIDERS[p].info.kind == kind]
    for fallback in DEV_FALLBACKS[kind]:
        if fallback not in chain:
            chain.append(fallback)
    return chain


def _today() -> str:
    return date.today().isoformat()


def _usage_row(session, provider_id: str) -> ProviderUsage:
    row = session.query(ProviderUsage).filter_by(provider=provider_id, day=_today()).one_or_none()
    if row is None:
        row = ProviderUsage(provider=provider_id, day=_today(), calls=0, units=0.0, errors=0, quota_exhausted=False)
        session.add(row)
        session.flush()
    return row


def quota_exhausted_today(provider_id: str) -> bool:
    with session_scope() as s:
        row = s.query(ProviderUsage).filter_by(provider=provider_id, day=_today()).one_or_none()
        return bool(row and row.quota_exhausted)


def record_success(provider_id: str, kind: str, units: float, unit_name: str, cost_usd: float,
                   project_id: str | None = None, job_id: str | None = None) -> None:
    with _usage_lock, session_scope() as s:
        row = _usage_row(s, provider_id)
        row.calls += 1
        row.units += units
        s.add(CostEntry(project_id=project_id, job_id=job_id, provider=provider_id, kind=kind, units=units,
                        unit_name=unit_name, cost_usd=cost_usd))


def record_failure(provider_id: str, quota: bool = False) -> None:
    with _usage_lock, session_scope() as s:
        row = _usage_row(s, provider_id)
        row.errors += 1
        if quota:
            row.quota_exhausted = True


@dataclass
class ChainAttempt:
    provider: str
    outcome: str
    detail: str = ""


class ChainFailed(ProviderError):
    def __init__(self, kind: str, attempts: list[ChainAttempt]):
        self.attempts = attempts
        summary = "; ".join(f"{a.provider}: {a.outcome} ({a.detail})" for a in attempts)
        super().__init__(f"nenhum provedor de {kind} funcionou: {summary}")


def run_chain[T](
    kind: str,
    chain: list[str],
    call: Callable[[Provider], T],
    *,
    usage_of: Callable[[T], tuple[float, str, float]] | None = None,
    project_id: str | None = None,
    job_id: str | None = None,
) -> tuple[T, Provider, list[ChainAttempt]]:
    """Executa `call` no primeiro provedor disponível da cadeia, seguindo em caso de falha recuperável."""
    attempts: list[ChainAttempt] = []
    for provider_id in chain:
        provider = get(provider_id)
        ok, reason = provider.availability()
        if not ok:
            attempts.append(ChainAttempt(provider_id, "indisponível", reason))
            continue
        if quota_exhausted_today(provider_id):
            attempts.append(ChainAttempt(provider_id, "cota esgotada hoje"))
            continue
        try:
            result = call(provider)
        except QuotaExceeded as exc:
            record_failure(provider_id, quota=True)
            attempts.append(ChainAttempt(provider_id, "cota esgotada", str(exc)))
            continue
        except (NotConfigured, TransientError, Refused) as exc:
            record_failure(provider_id)
            attempts.append(ChainAttempt(provider_id, type(exc).__name__, str(exc)))
            log.warning("provedor %s falhou (%s); tentando o próximo", provider_id, exc)
            continue
        if usage_of is not None:
            units, unit_name, cost = usage_of(result)
            record_success(provider_id, kind, units, unit_name, cost, project_id, job_id)
        attempts.append(ChainAttempt(provider_id, "ok"))
        return result, provider, attempts
    raise ChainFailed(kind, attempts)


def describe_all() -> list[dict[str, Any]]:
    """Estado de todos os provedores (para a tela de configurações)."""
    items = []
    for provider in all_providers():
        info = provider.info
        if info.hidden:
            continue
        try:
            ok, reason = provider.availability()
        except Exception as exc:  # noqa: BLE001
            ok, reason = False, str(exc)
        items.append(
            {
                "id": info.id,
                "kind": info.kind,
                "name": info.name,
                "available": ok,
                "reason": reason,
                "commercial_use": info.commercial_use,
                "local": info.local,
                "free": info.free,
                "credential_fields": list(info.credential_fields),
                "env_vars": list(info.env_vars),
                "credentials": credentials.describe(info.id, info.credential_fields, info.env_vars) if info.credential_fields else None,
                "notes": info.notes,
                "cost_note": info.cost_note,
                "accepts_references": info.accepts_references,
                "quota_exhausted_today": quota_exhausted_today(info.id),
            }
        )
    return items
