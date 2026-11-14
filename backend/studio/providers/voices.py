"""Catálogo de vozes e amostras para ouvir antes de escolher.

Cada amostra é gerada uma vez pelo próprio provedor (com o texto de amostra do idioma) e guardada em
`samples/voices`, fora do cache (a limpeza do cache não a apaga). Gerar uma amostra gasta um pedido da cota do
provedor, por isso só acontece quando a pessoa clica para ouvir.
"""

from __future__ import annotations

import shutil
import threading
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.core.hashing import hash_obj
from studio.providers import registry
from studio.providers.base import NotConfigured, ProviderError, QuotaExceeded, TTSProvider

SAMPLE_TEXTS: dict[str, str] = {
    "pt": "Olá! Esta é uma amostra da minha voz. Naquela noite, o farol se acendeu sozinho, e até hoje ninguém sabe "
          "explicar por quê.",
    "en": "Hello! This is a sample of my voice. That night, the lighthouse lit up on its own, and to this day no one "
          "can explain why.",
    "es": "¡Hola! Esta es una muestra de mi voz. Esa noche, el faro se encendió solo, y hasta hoy nadie sabe explicar "
          "por qué.",
    "fr": "Bonjour ! Voici un extrait de ma voix. Cette nuit-là, le phare s'est allumé tout seul, et personne ne sait "
          "encore pourquoi.",
    "de": "Hallo! Das ist eine Hörprobe meiner Stimme. In jener Nacht ging der Leuchtturm von selbst an, und bis heute "
          "kann niemand erklären, warum.",
    "it": "Ciao! Questo è un esempio della mia voce. Quella notte il faro si accese da solo, e ancora oggi nessuno sa "
          "spiegare perché.",
    "ja": "こんにちは。これは私の声のサンプルです。その夜、灯台はひとりでに灯り、今も誰にも理由がわかりません。",
    "ko": "안녕하세요. 제 목소리 샘플입니다. 그날 밤 등대는 저절로 불이 켜졌고, 지금까지 아무도 그 이유를 설명하지 못합니다.",
    "hi": "नमस्ते! यह मेरी आवाज़ का एक नमूना है। उस रात प्रकाशस्तंभ अपने आप जल उठा, और आज तक कोई नहीं बता पाया कि क्यों।",
    "ar": "مرحبًا! هذه عينة من صوتي. في تلك الليلة أضاءت المنارة من تلقاء نفسها، ولا أحد يعرف السبب حتى اليوم.",
    "ru": "Привет! Это образец моего голоса. В ту ночь маяк загорелся сам по себе, и до сих пор никто не может "
          "объяснить почему.",
    "tr": "Merhaba! Bu benim sesimden bir örnek. O gece deniz feneri kendiliğinden yandı ve bugün bile kimse nedenini "
          "açıklayamıyor.",
    "id": "Halo! Ini contoh suara saya. Malam itu, mercusuar menyala dengan sendirinya, dan sampai sekarang tidak ada "
          "yang tahu alasannya.",
}

_locks: dict[Path, threading.Lock] = {}
_locks_guard = threading.Lock()


class VoiceError(ProviderError):
    pass


def sample_text(lang: str) -> str:
    return SAMPLE_TEXTS.get(lang.split("-")[0].lower(), SAMPLE_TEXTS["en"])


def _tts(provider_id: str) -> TTSProvider:
    provider = registry.get(provider_id)
    if not isinstance(provider, TTSProvider) or provider.info.hidden:
        raise VoiceError(f"'{provider_id}' não é um provedor de voz")
    return provider


def sample_path(provider_id: str, voice: str, lang: str) -> Path:
    safe = "".join(ch if ch.isalnum() else "-" for ch in voice)[:60]
    key = hash_obj({"text": sample_text(lang), "voice": voice, "v": 1})[:10]
    return get_settings().data_dir / "samples" / "voices" / provider_id / lang / f"{safe}-{key}.wav"


def _rel(path: Path) -> str:
    return path.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()


def catalog(lang: str, provider_ids: list[str] | None = None) -> list[dict[str, Any]]:
    """Provedores de voz (na ordem pedida ou na do registro) com as vozes e as amostras já geradas."""
    if provider_ids:
        providers = [p for p in (registry._PROVIDERS.get(pid) for pid in provider_ids) if isinstance(p, TTSProvider)]
    else:
        providers = [p for p in registry.all_providers() if isinstance(p, TTSProvider)]
    out = []
    for provider in providers:
        if provider.info.hidden:
            continue
        options = provider.voices(lang)
        if not options:
            continue
        try:
            ok, reason = provider.availability()
        except Exception as exc:  # noqa: BLE001
            ok, reason = False, str(exc)
        voices = []
        for option in options:
            path = sample_path(provider.info.id, option.id, lang)
            voices.append({**asdict(option), "sample": _rel(path) if path.is_file() else None})
        out.append({
            "id": provider.info.id,
            "name": provider.info.name,
            "available": ok,
            "reason": reason,
            "preview_only": not provider.info.commercial_use,
            "voices": voices,
        })
    return out


def make_sample(provider_id: str, voice: str, lang: str) -> str:
    """Gera (ou devolve a já gerada) a amostra de uma voz. Retorna o caminho relativo à pasta de dados."""
    provider = _tts(provider_id)
    if voice not in {v.id for v in provider.voices(lang)}:
        raise VoiceError(f"a voz '{voice}' não existe em {provider.info.name}")
    path = sample_path(provider_id, voice, lang)
    with _locks_guard:
        lock = _locks.setdefault(path, threading.Lock())
    with lock:
        if path.is_file():
            return _rel(path)
        ok, reason = provider.availability()
        if not ok:
            raise NotConfigured(reason)
        if registry.quota_exhausted_today(provider_id):
            raise QuotaExceeded(f"a cota de {provider.info.name} acabou hoje")
        work = get_settings().tmp_dir / f"amostra-{uuid.uuid4().hex[:8]}"
        try:
            try:
                result = provider.synthesize(text=sample_text(lang), lang=lang, voice=voice, style_prompt=None,
                                             speed=1.0, out_dir=work)
            except QuotaExceeded:
                registry.record_failure(provider_id, quota=True)
                raise
            except ProviderError:
                registry.record_failure(provider_id)
                raise
            registry.record_success(provider_id, "tts", result.usage.units, result.usage.unit_name,
                                    result.usage.cost_usd)
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(result.path), path)
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return _rel(path)
