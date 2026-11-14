"""Interfaces dos provedores e erros comuns.

Cada etapa do pipeline (LLM, voz, imagem, alinhamento, vídeo) chama provedores através de uma
cadeia com fallback: se um provedor não está configurado, esgotou a cota ou falhou de forma
transitória, o próximo da cadeia assume (README §8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

Kind = Literal["llm", "tts", "image", "align", "video", "stock"]


class ProviderError(Exception):
    """Erro genérico de provedor (não tenta o próximo da cadeia)."""


class NotConfigured(ProviderError):
    """Falta chave, binário ou dependência. A cadeia segue para o próximo."""


class QuotaExceeded(ProviderError):
    """Cota/limite atingido. A cadeia segue e o provedor fica marcado como esgotado hoje."""


class TransientError(ProviderError):
    """Falha temporária (rede, timeout, 5xx). A cadeia segue para o próximo."""


class Refused(ProviderError):
    """O modelo recusou a tarefa (política). A cadeia segue para o próximo."""


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    kind: Kind
    name: str
    commercial_use: bool
    """Se a saída pode ir para um canal monetizado (provedores de prévia ficam bloqueados na publicação)."""
    local: bool = False
    free: bool = False
    credential_fields: tuple[str, ...] = ()
    env_vars: tuple[str, ...] = ()
    """Variáveis de ambiente aceitas no lugar do cofre (a primeira encontrada vale)."""
    notes: str = ""
    cost_note: str = ""
    deterministic: bool = False
    """Mesma entrada gera a mesma saída (não adianta tentar de novo no controle de qualidade)."""
    hidden: bool = False
    """Não aparece em Configurações (ex.: o roteiro de regras, que só existe no modo offline dos testes)."""


@dataclass
class Usage:
    units: float = 0.0
    unit_name: str = ""
    cost_usd: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Word:
    text: str
    start: float
    end: float

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "start": round(self.start, 3), "end": round(self.end, 3)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Word:
        return cls(text=data["text"], start=float(data["start"]), end=float(data["end"]))


@dataclass
class LLMResult:
    data: Any
    text: str
    usage: Usage
    model: str = ""


@dataclass
class TTSResult:
    path: Path
    duration_s: float
    words: list[Word] | None
    usage: Usage
    model: str = ""


@dataclass
class ImageResult:
    path: Path
    usage: Usage
    model: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


class Provider:
    info: ProviderInfo

    def availability(self) -> tuple[bool, str]:
        """(disponível, motivo). Não deve fazer chamadas pagas."""
        return True, "ok"


class LLMProvider(Provider):
    def complete_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 32000,
        context: dict[str, Any] | None = None,
    ) -> LLMResult:
        """Gera JSON validado pelo `schema`. `context` traz os dados estruturados da tarefa (usado pelo mock)."""
        raise NotImplementedError


@dataclass(frozen=True)
class VoiceOption:
    """Uma voz que o provedor oferece (para a escolha com amostra)."""

    id: str
    label: str
    gender: str | None = None
    """"feminina" ou "masculina" (quando o provedor informa)."""
    tone: str = ""


class TTSProvider(Provider):
    def voices(self, lang: str) -> list[VoiceOption]:
        """Vozes que dá para escolher neste idioma (vazio: o provedor não deixa escolher)."""
        return []

    def synthesize(
        self,
        *,
        text: str,
        lang: str,
        voice: str | None,
        style_prompt: str | None,
        speed: float,
        out_dir: Path,
    ) -> TTSResult:
        raise NotImplementedError


class ImageProvider(Provider):
    def generate(
        self,
        *,
        prompt: str,
        width: int,
        height: int,
        seed: int,
        out_dir: Path,
        references: list[Path] | None = None,
    ) -> ImageResult:
        raise NotImplementedError


class AlignProvider(Provider):
    def align(self, *, audio_path: Path, text: str, lang: str) -> list[Word]:
        raise NotImplementedError
