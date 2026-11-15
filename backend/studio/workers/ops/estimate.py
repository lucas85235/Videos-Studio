"""Créditos estimados da narração (origem: `[voz] creditos_por_caractere` do canal.toml).

O plano do build já sabe quantos caracteres faltam gravar; com os créditos por caractere do canal (medidos no uso
real, ex. 0,44 no ElevenLabs) dá para saber quantos créditos o vídeo final vai gastar antes de renderizar.
"""

from __future__ import annotations

from typing import Any

from studio.db import session_scope
from studio.models import Channel, Project


def credits_for(tts_chars: int, per_char: float, price_usd: float = 0.0) -> dict[str, Any] | None:
    if tts_chars <= 0 or per_char <= 0:
        return None
    credits = round(tts_chars * per_char)
    return {
        "credits": credits,
        "per_char": per_char,
        "usd": round(credits * price_usd, 2) if price_usd > 0 else None,
    }


def _paid_provider(tts_provider: str | None) -> bool:
    """A voz final cobra por uso (créditos)? Voz local ou grátis (ex. kokoro_local, gemini_tts) não gasta créditos.
    Provedor desconhecido (ou nenhum resolvido) conta como pago: melhor mostrar a estimativa do que esconder um gasto."""
    if not tts_provider:
        return True
    from studio.providers import registry

    try:
        info = registry.get(tts_provider).info
    except Exception:  # noqa: BLE001 - provedor fora do registro: segue com a estimativa
        return True
    return not (info.local or info.free)


def credits_estimate(project_id: str, tts_chars: int, tts_local: bool,
                     tts_provider: str | None = None) -> dict[str, Any] | None:
    """Créditos da narração que falta gravar com a voz final; None se a voz é a local da prévia, se a voz final é
    local ou grátis (`tts_provider`), se não há o que gravar ou se o canal do projeto não tem créditos por caractere."""
    if tts_local or tts_chars <= 0 or not _paid_provider(tts_provider):
        return None
    with session_scope() as s:
        project = s.get(Project, project_id)
        channel = s.get(Channel, project.channel_id) if project and project.channel_id else None
        if channel is None:
            return None
        per_char, price = channel.credits_per_char or 0.0, channel.credit_price_usd or 0.0
    return credits_for(tts_chars, per_char, price)
