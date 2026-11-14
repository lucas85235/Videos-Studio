"""Provedores de voz (TTS).

Ordem padrão (README §8.4): Gemini TTS (grátis) → Google Chirp 3 HD (1 M caracteres grátis/mês)
→ Qwen3-TTS local → (prévia) voz do Windows → mock. Toda saída é normalizada para WAV 48 kHz mono.
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import platform
import subprocess
import tempfile
from pathlib import Path

import httpx
import numpy as np

from studio.core.text import words as split_words
from studio.media.ffmpeg import SAMPLE_RATE, pcm16_to_wav, to_wav, wav_duration, write_wav
from studio.providers import credentials
from studio.providers.base import (
    NotConfigured,
    ProviderError,
    ProviderInfo,
    QuotaExceeded,
    TransientError,
    TTSProvider,
    TTSResult,
    Usage,
    VoiceOption,
    Word,
)
from studio.providers.llm import gemini_post

log = logging.getLogger(__name__)

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _final_wav(raw: Path, out_dir: Path, tempo: float = 1.0) -> Path:
    final = out_dir / "voice.wav"
    to_wav(raw, final, SAMPLE_RATE, tempo=tempo)
    if raw != final and raw.exists():
        raw.unlink(missing_ok=True)
    return final


# --------------------------------------------------------------------------------------------
# Gemini TTS
# --------------------------------------------------------------------------------------------

GEMINI_TTS_MODELS = ("gemini-3.8-flash-tts", "gemini-3.8-flash-lite-tts")
# Vozes pré-prontas do Gemini TTS (as mesmas do Chirp 3 HD). Timbre conforme a documentação do Google.
GEMINI_VOICE_INFO: dict[str, tuple[str, str]] = {
    "Zephyr": ("feminina", "brilhante"), "Puck": ("masculina", "animada"), "Charon": ("masculina", "informativa"),
    "Kore": ("feminina", "firme"), "Fenrir": ("masculina", "empolgada"), "Leda": ("feminina", "jovem"),
    "Orus": ("masculina", "firme"), "Aoede": ("feminina", "leve"), "Callirrhoe": ("feminina", "descontraída"),
    "Autonoe": ("feminina", "brilhante"), "Enceladus": ("masculina", "soprosa"), "Iapetus": ("masculina", "clara"),
    "Umbriel": ("masculina", "descontraída"), "Algieba": ("masculina", "macia"), "Despina": ("feminina", "macia"),
    "Erinome": ("feminina", "clara"), "Algenib": ("masculina", "rouca"), "Rasalgethi": ("masculina", "informativa"),
    "Laomedeia": ("feminina", "animada"), "Achernar": ("feminina", "suave"), "Alnilam": ("masculina", "firme"),
    "Schedar": ("masculina", "equilibrada"), "Gacrux": ("feminina", "madura"), "Pulcherrima": ("feminina", "direta"),
    "Achird": ("masculina", "amigável"), "Zubenelgenubi": ("masculina", "casual"),
    "Vindemiatrix": ("feminina", "gentil"), "Sadachbia": ("masculina", "viva"),
    "Sadaltager": ("masculina", "experiente"), "Sulafat": ("feminina", "calorosa"),
}
GEMINI_VOICES = tuple(GEMINI_VOICE_INFO)


def gemini_voice_options() -> list[VoiceOption]:
    return [VoiceOption(id=name, label=name, gender=gender, tone=tone) for name, (gender, tone) in GEMINI_VOICE_INFO.items()]


def gemini_tts_prompt(text: str, style_prompt: str | None, speed: float | None = None) -> str:
    """Pedido para o Gemini TTS com a direção separada do texto falado.

    O formato antigo (`"estilo: texto"`) fazia o modelo ler a instrução em voz alta, sobretudo quando o próprio
    estilo tinha dois-pontos ("Narre como um documentário: voz grave…"). O modelo não aceita instrução de sistema,
    então a direção vai em uma seção própria, no formato de notas de direção + transcrição da documentação do
    Google, sem dois-pontos nem quebras que imitem o texto. Mesmo assim ele ainda erra às vezes (lê a direção ou
    repete o texto): o build confere cada narração e refaz (ver `narration_problems`).
    """
    notes = " ".join((style_prompt or "").replace(":", ",").replace("#", " ").split()).strip(" .,")
    if speed and abs(speed - 1.0) >= 0.05:
        notes = ", ".join(p for p in (notes, "um pouco mais rápido" if speed > 1 else "um pouco mais devagar") if p)
    if not notes:
        return text
    return (
        "Read aloud only the text under TRANSCRIPT, word for word and exactly once. The director's notes only "
        "describe the voice and pacing: never read them aloud.\n\n"
        f"### DIRECTOR'S NOTES\n{notes}.\n\n"
        f"#### TRANSCRIPT\n{text}"
    )


class GeminiTTS(TTSProvider):
    info = ProviderInfo(
        id="gemini_tts",
        kind="tts",
        name="Gemini 3.8 Flash TTS (Google AI Studio)",
        commercial_use=True,
        free=True,
        credential_fields=("api_key",),
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        cost_note="Camada gratuita; limite diário não publicado (relatos de 10–100 pedidos/dia por modelo)",
        notes="Tenta o Flash e, se a cota acabar, o Flash-Lite. Sem timestamps: o alinhamento vem depois.",
    )

    def _key(self) -> str | None:
        return credentials.resolve(self.info.id, "api_key", self.info.env_vars) or credentials.resolve(
            "gemini", "api_key", ()
        )

    def availability(self) -> tuple[bool, str]:
        return (True, "ok") if self._key() else (False, "sem chave do Gemini (GEMINI_API_KEY ou cofre)")

    def voices(self, lang: str) -> list[VoiceOption]:
        return gemini_voice_options()  # multilíngues: as mesmas vozes em todos os idiomas

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        key = self._key()
        if not key:
            raise NotConfigured("sem chave do Gemini")
        voice_name = voice if voice in GEMINI_VOICES else "Charon"
        spoken = text.strip()
        # Instrução de estilo no pedido fazia o Gemini ler a instrução ou repetir o texto: 5 de 6 tentativas em
        # 14/11/2026, inclusive no formato de notas de direção; só o texto saiu certo em todas. Por padrão vai só
        # o texto (o tom vem da voz) e a velocidade do estilo é aplicada no áudio. VS_GEMINI_TTS_STYLE=1 reativa
        # a instrução; o build confere cada narração e refaz as que saírem erradas.
        use_style = os.environ.get("VS_GEMINI_TTS_STYLE") == "1"
        prompt = gemini_tts_prompt(spoken, style_prompt, speed) if use_style else spoken
        tempo = 1.0 if use_style or not speed else max(0.8, min(1.25, speed))
        body = {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_name}}},
            },
        }
        last_error: Exception | None = None
        for model in GEMINI_TTS_MODELS:
            try:
                data = gemini_post(f"models/{model}:generateContent", key, body, timeout=600)
            except QuotaExceeded as exc:
                last_error = exc
                continue
            parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            inline = next((p["inlineData"] for p in parts if "inlineData" in p), None)
            if not inline:
                raise ProviderError("o Gemini não devolveu áudio")
            pcm = base64.b64decode(inline["data"])
            rate = 24000
            mime = inline.get("mimeType", "")
            if "rate=" in mime:
                try:
                    rate = int(mime.split("rate=")[1].split(";")[0])
                except ValueError:
                    pass
            raw = pcm16_to_wav(pcm, rate, out_dir / "raw.wav")
            final = _final_wav(raw, out_dir, tempo=tempo)
            return TTSResult(
                path=final,
                duration_s=wav_duration(final),
                words=None,
                usage=Usage(units=len(spoken), unit_name="chars", cost_usd=0.0,
                            details={"model": model, "voice": voice_name, "style_in_request": use_style}),
                model=model,
            )
        raise last_error or QuotaExceeded("cota do Gemini TTS esgotada")


# --------------------------------------------------------------------------------------------
# Google Cloud Text-to-Speech (Chirp 3 HD)
# --------------------------------------------------------------------------------------------


class GoogleChirp3HD(TTSProvider):
    info = ProviderInfo(
        id="google_chirp3hd",
        kind="tts",
        name="Google Cloud TTS Chirp 3 HD",
        commercial_use=True,
        free=True,
        credential_fields=("api_key",),
        env_vars=("GOOGLE_CLOUD_TTS_API_KEY",),
        cost_note="1 M caracteres grátis/mês; exige projeto com cobrança ativada (use alerta de orçamento)",
    )

    def _key(self) -> str | None:
        return credentials.resolve(self.info.id, "api_key", self.info.env_vars)

    def availability(self) -> tuple[bool, str]:
        return (True, "ok") if self._key() else (False, "sem chave do Google Cloud TTS")

    def voices(self, lang: str) -> list[VoiceOption]:
        return gemini_voice_options()  # o Chirp 3 HD usa os mesmos nomes ({idioma}-Chirp3-HD-{voz})

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        key = self._key()
        if not key:
            raise NotConfigured("sem chave do Google Cloud TTS")
        voice_name = f"{lang}-Chirp3-HD-{voice or 'Charon'}"
        body = {
            "input": {"text": text},
            "voice": {"languageCode": lang, "name": voice_name},
            "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 24000, "speakingRate": speed or 1.0},
        }
        try:
            resp = httpx.post(
                "https://texttospeech.googleapis.com/v1/text:synthesize", params={"key": key}, json=body, timeout=300
            )
        except httpx.HTTPError as exc:
            raise TransientError(f"falha de conexão com o Google TTS: {exc}") from exc
        if resp.status_code == 429:
            raise QuotaExceeded("cota do Google TTS atingida")
        if resp.status_code in (401, 403):
            raise NotConfigured(f"chave do Google TTS recusada: {resp.text[:300]}")
        if resp.status_code >= 500:
            raise TransientError(f"erro {resp.status_code} no Google TTS")
        if resp.status_code != 200:
            raise ProviderError(f"erro do Google TTS ({resp.status_code}): {resp.text[:400]}")
        audio = base64.b64decode(resp.json()["audioContent"])
        raw = out_dir / "raw_chirp.wav"
        raw.write_bytes(audio)
        final = _final_wav(raw, out_dir)
        return TTSResult(path=final, duration_s=wav_duration(final), words=None,
                         usage=Usage(units=len(text), unit_name="chars"), model=voice_name)


# --------------------------------------------------------------------------------------------
# ElevenLabs (com timestamps por caractere)
# --------------------------------------------------------------------------------------------


class ElevenLabsTTS(TTSProvider):
    info = ProviderInfo(
        id="elevenlabs",
        kind="tts",
        name="ElevenLabs",
        commercial_use=True,
        credential_fields=("api_key", "voice_id"),
        env_vars=("ELEVENLABS_API_KEY",),
        cost_note="Plano pago (Starter/Creator) para uso comercial; usado como referência no teste cego",
    )

    def availability(self) -> tuple[bool, str]:
        key = credentials.resolve(self.info.id, "api_key", self.info.env_vars)
        return (True, "ok") if key else (False, "sem chave da ElevenLabs")

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        key = credentials.resolve(self.info.id, "api_key", self.info.env_vars)
        voice_id = voice if voice and len(voice) >= 16 else credentials.resolve(self.info.id, "voice_id", ())
        if not key or not voice_id:
            raise NotConfigured("configure a chave e o voice_id da ElevenLabs")
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"
        body = {"text": text, "model_id": "eleven_multilingual_v2", "voice_settings": {"stability": 0.45, "similarity_boost": 0.8, "speed": speed or 1.0}}
        try:
            resp = httpx.post(url, params={"output_format": "mp3_44100_128"}, json=body, headers={"xi-api-key": key}, timeout=300)
        except httpx.HTTPError as exc:
            raise TransientError(f"falha de conexão com a ElevenLabs: {exc}") from exc
        if resp.status_code == 429:
            raise QuotaExceeded("limite da ElevenLabs atingido")
        if resp.status_code == 401:
            raise NotConfigured("chave da ElevenLabs recusada")
        if resp.status_code >= 500:
            raise TransientError(f"erro {resp.status_code} na ElevenLabs")
        if resp.status_code != 200:
            raise ProviderError(f"erro da ElevenLabs ({resp.status_code}): {resp.text[:400]}")
        payload = resp.json()
        raw = out_dir / "raw.mp3"
        raw.write_bytes(base64.b64decode(payload["audio_base64"]))
        final = _final_wav(raw, out_dir)
        words = _words_from_chars(payload.get("alignment") or {})
        return TTSResult(path=final, duration_s=wav_duration(final), words=words or None,
                         usage=Usage(units=len(text), unit_name="chars"), model="eleven_multilingual_v2")


def _words_from_chars(alignment: dict) -> list[Word]:
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    words: list[Word] = []
    current, w_start, w_end = "", None, 0.0
    for ch, st, en in zip(chars, starts, ends, strict=False):
        if ch.isspace():
            if current:
                words.append(Word(current, w_start or 0.0, w_end))
            current, w_start = "", None
            continue
        if w_start is None:
            w_start = st
        current += ch
        w_end = en
    if current:
        words.append(Word(current, w_start or 0.0, w_end))
    return words


# --------------------------------------------------------------------------------------------
# Qwen3-TTS local (opcional)
# --------------------------------------------------------------------------------------------


class Qwen3LocalTTS(TTSProvider):
    info = ProviderInfo(
        id="qwen3_tts_local",
        kind="tts",
        name="Qwen3-TTS local (faster-qwen3-tts)",
        commercial_use=True,
        local=True,
        free=True,
        notes="Apache-2.0. Instale o pacote faster-qwen3-tts e a GPU fará a narração sem custo (README §8.4).",
    )

    def availability(self) -> tuple[bool, str]:
        try:
            import faster_qwen3_tts  # noqa: F401
        except ImportError:
            return False, "pacote faster-qwen3-tts não instalado (opcional, ver docs/BACKLOG.md)"
        return True, "ok"

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        raise NotConfigured("integração do Qwen3-TTS local ainda não implementada (backlog)")


# --------------------------------------------------------------------------------------------
# Voz do Windows (SAPI) — só prévia
# --------------------------------------------------------------------------------------------

_SAPI_SCRIPT = r"""
param([string]$TextFile, [string]$Voice, [int]$Rate, [string]$OutWav, [string]$OutMarks)
Add-Type -AssemblyName System.Speech
$code = @"
using System;
using System.Text;
using System.Speech.Synthesis;
public static class VsSapi {
  public static string Run(string text, string voice, int rate, string outPath) {
    var sb = new StringBuilder();
    using (var s = new SpeechSynthesizer()) {
      if (!String.IsNullOrEmpty(voice)) { try { s.SelectVoice(voice); } catch (Exception) { } }
      s.Rate = rate;
      s.SetOutputToWaveFile(outPath);
      s.SpeakProgress += (o, e) => {
        sb.Append(e.CharacterPosition).Append('\t').Append(e.CharacterCount).Append('\t')
          .Append(e.AudioPosition.TotalMilliseconds.ToString(System.Globalization.CultureInfo.InvariantCulture)).Append('\n');
      };
      s.Speak(text);
    }
    return sb.ToString();
  }
}
"@
Add-Type -TypeDefinition $code -ReferencedAssemblies System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$marks = [VsSapi]::Run($text, $Voice, $Rate, $OutWav)
[System.IO.File]::WriteAllText($OutMarks, $marks, [System.Text.Encoding]::UTF8)
"""

SAPI_VOICES = {"pt": "Microsoft Maria Desktop", "en": "Microsoft Zira Desktop", "ja": "Microsoft Haruka Desktop"}


class WindowsSapiTTS(TTSProvider):
    info = ProviderInfo(
        id="windows_sapi",
        kind="tts",
        name="Voz do Windows (prévia)",
        commercial_use=False,
        local=True,
        free=True,
        notes="Só para prévias e testes: o direito de uso comercial não está claro, então o app bloqueia na publicação.",
        deterministic=True,
    )

    def availability(self) -> tuple[bool, str]:
        if platform.system() != "Windows":
            return False, "disponível só no Windows"
        return True, "ok"

    def voices(self, lang: str) -> list[VoiceOption]:
        name = SAPI_VOICES.get(lang.split("-")[0].lower())
        if not name:
            return []
        short = name.removeprefix("Microsoft ").removesuffix(" Desktop")
        return [VoiceOption(id=name, label=short, gender="feminina", tone="voz do Windows")]

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        if platform.system() != "Windows":
            raise NotConfigured("voz SAPI só existe no Windows")
        out_dir.mkdir(parents=True, exist_ok=True)
        voice_name = SAPI_VOICES.get(lang.split("-")[0].lower(), "")
        rate = max(-10, min(10, round(((speed or 1.0) - 1.0) * 10)))
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            script = tmpdir / "sapi.ps1"
            script.write_text(_SAPI_SCRIPT, encoding="utf-8-sig")
            text_file = tmpdir / "text.txt"
            text_file.write_text(text, encoding="utf-8")
            raw = out_dir / "raw_sapi.wav"
            marks_file = tmpdir / "marks.tsv"
            cmd = [
                "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                "-TextFile", str(text_file), "-Voice", voice_name, "-Rate", str(rate),
                "-OutWav", str(raw), "-OutMarks", str(marks_file),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600, creationflags=_NO_WINDOW)
            if proc.returncode != 0 or not raw.exists():
                raise ProviderError(f"voz do Windows falhou: {proc.stderr.strip()[-800:]}")
            marks_raw = marks_file.read_text(encoding="utf-8-sig") if marks_file.exists() else ""
        final = _final_wav(raw, out_dir)
        duration = wav_duration(final)
        words = _words_from_sapi(text, marks_raw, duration)
        return TTSResult(path=final, duration_s=duration, words=words or None,
                         usage=Usage(units=len(text), unit_name="chars"), model=voice_name or "sapi")


def _words_from_sapi(text: str, marks_raw: str, duration: float) -> list[Word]:
    marks: list[tuple[int, int, float]] = []
    for line in marks_raw.splitlines():
        parts = line.strip().split("\t")
        if len(parts) == 3:
            try:
                marks.append((int(parts[0]), int(parts[1]), float(parts[2]) / 1000.0))
            except ValueError:
                continue
    words: list[Word] = []
    for i, (pos, count, start) in enumerate(marks):
        token = text[pos : pos + count]
        end = marks[i + 1][2] if i + 1 < len(marks) else duration
        if token.strip():
            words.append(Word(token, start, max(start + 0.05, end - 0.02)))
    return words


# --------------------------------------------------------------------------------------------
# Mock (testes)
# --------------------------------------------------------------------------------------------


class MockTTS(TTSProvider):
    info = ProviderInfo(
        id="mock_tts",
        kind="tts",
        name="Voz de teste (tom)",
        commercial_use=False,
        local=True,
        free=True,
        notes="Gera um tom suave com a duração estimada da fala. Só para testes.",
        deterministic=True,
    )

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir) -> TTSResult:
        tokens = split_words(text)
        seconds_per_char = 1.0 / (14.5 * (speed or 1.0))
        pad = 0.12
        words: list[Word] = []
        t = pad
        for token in tokens:
            dur = max(0.12, len(token) * seconds_per_char + 0.05)
            words.append(Word(token, t, t + dur))
            t += dur + 0.04
        total = t + pad
        n = int(total * SAMPLE_RATE)
        tt = np.arange(n) / SAMPLE_RATE
        env = np.zeros(n, dtype=np.float32)
        for w in words:
            a, b = int(w.start * SAMPLE_RATE), int(w.end * SAMPLE_RATE)
            env[a:b] = 1.0
        audio = 0.08 * np.sin(2 * math.pi * 180 * tt) * env
        final = write_wav(out_dir / "voice.wav", audio.astype(np.float32))
        return TTSResult(path=final, duration_s=wav_duration(final), words=words,
                         usage=Usage(units=len(text), unit_name="chars"), model="mock")


def words_json(words: list[Word] | None) -> str:
    return json.dumps([w.to_dict() for w in (words or [])], ensure_ascii=False)
