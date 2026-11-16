"""Voz local grátis para a prévia: Kokoro-82M (Apache-2.0) via `kokoro-onnx`, só na CPU.

Escolhida em 14/11/2026 para a prévia: num teste com o Whisper, leu o roteiro inteiro sem erro em português, gera
cerca de 2,5× mais rápido que a fala na CPU e não tem cota, rede nem instrução de estilo que possa vazar no áudio.
A qualidade fica abaixo das vozes de API: serve para conferir o conteúdo antes de gastar créditos na voz final.

O modelo (~350 MB) fica em `<dados>/models/kokoro` e é baixado na primeira vez, com conferência do SHA-256.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from pathlib import Path
from typing import Any

import httpx

from studio.config import get_settings
from studio.media.ffmpeg import wav_duration, write_wav
from studio.providers.base import (
    NotConfigured,
    ProviderError,
    ProviderInfo,
    TransientError,
    TTSProvider,
    TTSResult,
    Usage,
    VoiceOption,
)
from studio.providers.tts import _final_wav

log = logging.getLogger(__name__)

_RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
MODEL_FILES: dict[str, tuple[str, int, str]] = {
    "kokoro-v1.0.onnx": (f"{_RELEASE}/kokoro-v1.0.onnx", 325_532_387,
                         "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"),
    "voices-v1.0.bin": (f"{_RELEASE}/voices-v1.0.bin", 28_214_398,
                        "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"),
}
MODEL_MB = round(sum(size for _, size, _ in MODEL_FILES.values()) / 1e6)

# Idioma do app → (código do Kokoro/espeak, prefixos das vozes desse idioma).
# Japonês e chinês ficam de fora: o Kokoro foi treinado com outro conversor de fonemas para eles e a leitura pelo
# espeak sai errada. Idiomas sem voz local fazem a prévia com a voz final.
_LANGS: dict[str, tuple[str, tuple[str, ...]]] = {
    "pt": ("pt-br", ("p",)),
    "en": ("en-us", ("a", "b")),
    "en-gb": ("en-gb", ("b", "a")),
    "es": ("es", ("e",)),
    "fr": ("fr-fr", ("f",)),
    "it": ("it", ("i",)),
    "hi": ("hi", ("h",)),
}

# Vozes oferecidas (as de melhor nota no catálogo do Kokoro v1.0). Gênero pela 2ª letra do id (f/m).
VOICE_INFO: dict[str, tuple[str, str]] = {
    "pf_dora": ("Dora", "clara"), "pm_alex": ("Alex", "firme"), "pm_santa": ("Santa", "grave"),
    "af_heart": ("Heart", "calorosa"), "af_bella": ("Bella", "animada"), "af_nicole": ("Nicole", "suave"),
    "af_sarah": ("Sarah", "equilibrada"), "am_michael": ("Michael", "firme"), "am_fenrir": ("Fenrir", "grave"),
    "am_puck": ("Puck", "animada"), "am_adam": ("Adam", "casual"),
    "bf_emma": ("Emma", "clara"), "bf_isabella": ("Isabella", "suave"), "bm_george": ("George", "grave"),
    "bm_fable": ("Fable", "narrativa"), "bm_lewis": ("Lewis", "firme"),
    "ef_dora": ("Dora", "clara"), "em_alex": ("Alex", "firme"), "em_santa": ("Santa", "grave"),
    "ff_siwis": ("Siwis", "clara"),
    "if_sara": ("Sara", "clara"), "im_nicola": ("Nicola", "firme"),
    "hf_alpha": ("Alpha", "clara"), "hf_beta": ("Beta", "suave"), "hm_omega": ("Omega", "firme"),
    "hm_psi": ("Psi", "grave"),
}
_GENDER = {"f": "feminina", "m": "masculina"}


def _lang_entry(lang: str) -> tuple[str, tuple[str, ...]] | None:
    code = lang.lower()
    return _LANGS.get(code) or _LANGS.get(code.split("-")[0])


def supports(lang: str) -> bool:
    return _lang_entry(lang) is not None


def voice_gender(voice_id: str) -> str | None:
    return _GENDER.get(voice_id[1:2]) if len(voice_id) > 2 and voice_id[2] == "_" else None


def voices_for(lang: str) -> list[VoiceOption]:
    entry = _lang_entry(lang)
    if not entry:
        return []
    prefixes = entry[1]
    ordered = [v for p in prefixes for v in VOICE_INFO if v.startswith(p)]
    return [VoiceOption(id=v, label=VOICE_INFO[v][0], gender=voice_gender(v), tone=VOICE_INFO[v][1]) for v in ordered]


def pick_voice(lang: str, wanted: str | None = None, gender: str | None = None, avoid: str | None = None) -> str | None:
    """Voz local para um idioma: a pedida, se for desse idioma; senão uma do mesmo gênero (evitando `avoid`)."""
    options = [v.id for v in voices_for(lang)]
    if not options:
        return None
    if wanted in options:
        return wanted
    gender = gender or (voice_gender(wanted) if wanted else None)
    same = [v for v in options if gender is None or voice_gender(v) == gender]
    for pool in (same, options):
        for v in pool:
            if v != avoid:
                return v
    return options[0]


def models_dir() -> Path:
    return get_settings().data_dir / "models" / "kokoro"


def model_ready() -> bool:
    d = models_dir()
    return all((d / name).is_file() and (d / name).stat().st_size == size for name, (_, size, _) in MODEL_FILES.items())


_download_lock = threading.Lock()


def ensure_model() -> Path:
    """Baixa o modelo se faltar (uma vez só, conferindo tamanho e SHA-256). Devolve a pasta."""
    d = models_dir()
    with _download_lock:
        if model_ready():
            return d
        d.mkdir(parents=True, exist_ok=True)
        for name, (url, size, sha) in MODEL_FILES.items():
            target = d / name
            if target.is_file() and target.stat().st_size == size:
                continue
            part = target.with_suffix(target.suffix + ".part")
            log.info("baixando a voz local: %s (%d MB)", name, size // 1_000_000)
            digest = hashlib.sha256()
            try:
                with httpx.stream("GET", url, follow_redirects=True, timeout=httpx.Timeout(60, read=120)) as resp:
                    if resp.status_code != 200:
                        raise TransientError(f"download da voz local falhou ({resp.status_code})")
                    with part.open("wb") as fh:
                        for chunk in resp.iter_bytes(1 << 20):
                            fh.write(chunk)
                            digest.update(chunk)
            except httpx.HTTPError as exc:
                part.unlink(missing_ok=True)
                raise TransientError(f"sem conexão para baixar a voz local: {exc}") from exc
            if part.stat().st_size != size or digest.hexdigest() != sha:
                part.unlink(missing_ok=True)
                raise ProviderError(f"o arquivo baixado da voz local veio corrompido ({name}); tente de novo")
            shutil.move(str(part), target)
        return d


class KokoroTTS(TTSProvider):
    info = ProviderInfo(
        id="kokoro_local",
        kind="tts",
        name="Voz local (Kokoro)",
        commercial_use=True,
        local=True,
        free=True,
        deterministic=True,
        spoken_text=True,
        cost_note="Grátis e sem cota: roda no seu computador",
        notes="Usada nas prévias para você conferir o conteúdo sem gastar a cota das vozes de API. Português, inglês, "
        f"espanhol, francês, italiano e hindi. Modelo de {MODEL_MB} MB, baixado na primeira vez (licença Apache-2.0).",
    )

    def __init__(self) -> None:
        self._engine: Any = None
        self._lock = threading.Lock()

    def availability(self) -> tuple[bool, str]:
        try:
            import kokoro_onnx  # noqa: F401
        except ImportError:
            return False, "pacote kokoro-onnx não instalado (rode scripts\\setup.ps1)"
        if not model_ready():
            return True, f"ok (o modelo de {MODEL_MB} MB será baixado na primeira prévia)"
        return True, "ok"

    def voices(self, lang: str) -> list[VoiceOption]:
        return voices_for(lang)

    def _load(self) -> Any:
        if self._engine is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:
                raise NotConfigured("pacote kokoro-onnx não instalado") from exc
            d = ensure_model()
            self._engine = Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))
        return self._engine

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        entry = _lang_entry(lang)
        if not entry:
            raise NotConfigured(f"a voz local não fala {lang}")
        chosen = pick_voice(lang, voice)
        speed = max(0.5, min(2.0, speed or 1.0))
        out_dir.mkdir(parents=True, exist_ok=True)
        # Uma narração por vez: o ONNX já usa todos os núcleos; três em paralelo só disputam a CPU.
        with self._lock:
            engine = self._load()
            try:
                samples, rate = engine.create(text, voice=chosen, speed=speed, lang=entry[0])
            except Exception as exc:  # noqa: BLE001 - erro do espeak/onnx vira erro do provedor
                raise ProviderError(f"a voz local falhou: {exc}") from exc
        if samples is None or len(samples) == 0:
            raise ProviderError("a voz local não gerou áudio")
        raw = write_wav(out_dir / "raw_kokoro.wav", samples, rate)
        final = _final_wav(raw, out_dir)
        return TTSResult(path=final, duration_s=wav_duration(final), words=None,
                         usage=Usage(units=len(text), unit_name="chars"), model=f"kokoro-v1.0/{chosen}")
