"""Provedores de LLM: Claude pela conta (Claude Code), Claude pela API (Anthropic SDK), Gemini (camada grátis)
e um mock offline."""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from studio.providers import credentials
from studio.providers.base import (
    LLMProvider,
    LLMResult,
    NotConfigured,
    ProviderError,
    ProviderInfo,
    QuotaExceeded,
    Refused,
    TransientError,
    Usage,
)

log = logging.getLogger(__name__)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# --------------------------------------------------------------------------------------------
# Claude pela conta (Claude Code em modo não interativo)
# --------------------------------------------------------------------------------------------

CLAUDE_CODE_MODEL = "opus"  # apelido do Claude Code para o Opus mais recente
# Variáveis que fariam o Claude Code cobrar pela API em vez de usar o plano da conta.
_API_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_AUTH_TTL_S = 300.0


def _limit_message(text: str) -> bool:
    lowered = text.lower()
    return any(s in lowered for s in ("usage limit", "limit reached", "hit your limit", "rate limit", "out of extra usage"))


class ClaudeCodeLLM(LLMProvider):
    """Claude pela assinatura (Pro/Max): chama o `claude -p` instalado e logado nesta máquina.

    Sem ferramentas, sem sessão salva e sem as personalizações do usuário (`--safe-mode`). O prompt vai pela
    entrada padrão (o Windows limita a linha de comando a ~32 mil caracteres) e o sistema por arquivo.
    """

    info = ProviderInfo(
        id="claude_code",
        kind="llm",
        name="Claude (sua conta, via Claude Code)",
        commercial_use=True,
        local=True,
        free=True,
        cost_note="Incluído no seu plano do Claude (Pro/Max); conta nos limites de uso do plano, sem cobrança extra",
        notes="Usa o comando `claude` logado com a sua conta (rode `claude auth login` uma vez). Modelo em "
        "VS_CLAUDE_MODEL (padrão: opus) e esforço em VS_CLAUDE_EFFORT (padrão: medium). Para uso pessoal.",
    )

    timeout_s = 900

    def __init__(self) -> None:
        self._auth_lock = threading.Lock()
        self._auth_cache: tuple[float, bool, str] | None = None

    @property
    def model(self) -> str:
        return os.environ.get("VS_CLAUDE_MODEL", CLAUDE_CODE_MODEL)

    @property
    def effort(self) -> str:
        return os.environ.get("VS_CLAUDE_EFFORT", "medium")

    def _claude(self) -> str | None:
        return shutil.which("claude")

    @staticmethod
    def _env() -> dict[str, str]:
        return {k: v for k, v in os.environ.items() if k not in _API_ENV_VARS}

    def _auth_status(self, claude: str) -> tuple[bool, str]:
        """`claude auth status` (em cache por 5 min: a tela de configurações e cada chamada consultam)."""
        with self._auth_lock:
            cached = self._auth_cache
            if cached and time.monotonic() - cached[0] < _AUTH_TTL_S:
                return cached[1], cached[2]
            try:
                proc = subprocess.run(
                    [claude, "auth", "status"], env=self._env(), capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=30, creationflags=_NO_WINDOW,
                )
                data = json.loads(proc.stdout or "{}")
            except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
                return False, f"não consegui consultar o login do Claude Code: {exc}"
            if not data.get("loggedIn"):
                result = (False, "Claude Code sem login: rode `claude auth login` com a sua conta")
            elif data.get("authMethod") != "claude.ai":
                result = (False, f"o Claude Code está logado por '{data.get('authMethod')}', não pela conta do "
                                 "Claude (seria cobrado como API); rode `claude auth login`")
            else:
                plan = data.get("subscriptionType")
                result = (True, f"ok (plano {plan})" if plan else "ok")
            self._auth_cache = (time.monotonic(), *result)
            return result

    def forget_auth(self) -> None:
        with self._auth_lock:
            self._auth_cache = None

    def availability(self) -> tuple[bool, str]:
        from studio.config import get_settings

        if get_settings().offline:
            return False, "modo offline"
        claude = self._claude()
        if not claude:
            return False, "Claude Code não encontrado no PATH (instale e faça login com a sua conta do Claude)"
        return self._auth_status(claude)

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
        claude = self._claude()
        if not claude:
            raise NotConfigured("Claude Code não encontrado")
        with tempfile.TemporaryDirectory(prefix="vs-claude-") as workdir:
            system_file = Path(workdir) / "system.txt"
            system_file.write_text(system, encoding="utf-8")
            cmd = [
                claude, "-p", "--output-format", "json",
                "--json-schema", json.dumps(schema, ensure_ascii=False),
                "--tools", "", "--no-session-persistence", "--safe-mode",
                "--model", self.model, "--effort", self.effort,
                "--system-prompt-file", str(system_file),
            ]
            try:
                proc = subprocess.run(
                    cmd, input=prompt, cwd=workdir, env=self._env(), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=self.timeout_s, creationflags=_NO_WINDOW,
                )
            except subprocess.TimeoutExpired as exc:
                raise TransientError(f"o Claude demorou mais de {self.timeout_s // 60} min na tarefa '{task}'") from exc
            except OSError as exc:
                raise TransientError(f"não consegui iniciar o Claude Code: {exc}") from exc
        return self._parse(task, proc.stdout or "", proc.stderr or "", proc.returncode)

    def _parse(self, task: str, stdout: str, stderr: str, returncode: int) -> LLMResult:
        try:
            out = json.loads(stdout)
        except json.JSONDecodeError:
            out = None
        if not isinstance(out, dict):
            detail = (stderr or stdout).strip()[:400]
            if _limit_message(detail):
                raise TransientError(f"limite de uso do plano do Claude atingido: {detail}")
            if "login" in detail.lower() or "auth" in detail.lower():
                self.forget_auth()
                raise NotConfigured(f"Claude Code sem login: {detail}")
            raise TransientError(f"o Claude Code falhou (código {returncode}): {detail}")

        message = str(out.get("result") or "")
        status = out.get("api_error_status")
        if out.get("is_error"):
            if status == 429 or _limit_message(message):
                # o limite do plano volta em algumas horas: segue a cadeia sem marcar o dia como esgotado
                raise TransientError(f"limite de uso do plano do Claude atingido: {message[:300]}")
            if status in (401, 403) or "login" in message.lower():
                self.forget_auth()
                raise NotConfigured(f"o Claude Code recusou a conta ({status}): {message[:300]}")
            if status == 404:
                raise NotConfigured(f"modelo '{self.model}' indisponível na sua conta (ajuste VS_CLAUDE_MODEL)")
            if status is None or (isinstance(status, int) and status >= 500):
                raise TransientError(f"erro temporário no Claude ({status}): {message[:300]}")
            raise ProviderError(f"erro do Claude ({status}): {message[:300]}")
        if out.get("stop_reason") == "refusal":
            raise Refused(f"o modelo recusou a tarefa '{task}'")

        data = out.get("structured_output")
        if not isinstance(data, dict):
            try:
                data = json.loads(message)
            except json.JSONDecodeError as exc:
                raise ProviderError(f"o Claude não devolveu o JSON pedido na tarefa '{task}': {message[:200]}") from exc
        usage = out.get("usage") or {}
        input_tokens = sum(int(usage.get(k) or 0) for k in
                           ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        output_tokens = int(usage.get("output_tokens") or 0)
        model_usage = out.get("modelUsage") or {}
        model = next(iter(model_usage), self.model)
        return LLMResult(
            data=data,
            text=json.dumps(data, ensure_ascii=False),
            usage=Usage(
                units=input_tokens + output_tokens, unit_name="tokens", cost_usd=0.0,
                details={"input_tokens": input_tokens, "output_tokens": output_tokens,
                         # quanto custaria pela API (o plano não cobra por chamada)
                         "api_equivalent_usd": out.get("total_cost_usd")},
            ),
            model=model,
        )


# --------------------------------------------------------------------------------------------
# Claude pela API (Anthropic)
# --------------------------------------------------------------------------------------------

CLAUDE_MODEL = "claude-opus-5-5"
CLAUDE_PRICE = {"input": 4.00, "output": 20.00}  # US$ por 1M tokens (Claude Opus 5.5)


class AnthropicLLM(LLMProvider):
    info = ProviderInfo(
        id="anthropic",
        kind="llm",
        name="Claude (API paga da Anthropic)",
        commercial_use=True,
        credential_fields=("api_key",),
        env_vars=("ANTHROPIC_API_KEY",),
        cost_note="Claude Opus 5.5: US$ 4 / US$ 20 por 1M tokens (entrada/saída)",
        notes="Cobrado por token. Só entra se houver chave e o Claude da conta e o Gemini falharem. "
        "Fallback de recusa do servidor ligado.",
    )

    def __init__(self, model: str = CLAUDE_MODEL, effort: str = "medium") -> None:
        self.model = model
        self.effort = effort

    def _api_key(self) -> str | None:
        return credentials.resolve(self.info.id, "api_key", self.info.env_vars)

    def availability(self) -> tuple[bool, str]:
        if not self._api_key():
            return False, "sem chave da Anthropic (ANTHROPIC_API_KEY ou cofre)"
        return True, "ok"

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
        import anthropic

        key = self._api_key()
        if not key:
            raise NotConfigured("sem chave da Anthropic")
        client = anthropic.Anthropic(api_key=key, max_retries=2)
        params: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
        }
        try:
            try:
                with client.beta.messages.stream(
                    **params,
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                ) as stream:
                    message = stream.get_final_message()
            except anthropic.BadRequestError as exc:
                if "fallback" not in str(exc).lower():
                    raise
                log.warning("fallback de recusa indisponível nesta conta; tentando sem ele")
                with client.messages.stream(**params) as stream:
                    message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise NotConfigured(f"chave da Anthropic inválida: {exc}") from exc
        except anthropic.PermissionDeniedError as exc:
            raise NotConfigured(f"chave sem permissão: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise TransientError(f"limite de taxa da Anthropic: {exc}") from exc
        except (anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            raise TransientError(f"falha de conexão com a Anthropic: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise TransientError(f"erro {exc.status_code} na Anthropic") from exc
            raise ProviderError(f"erro da Anthropic ({exc.status_code}): {exc.message}") from exc

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            raise Refused(f"o modelo recusou a tarefa '{task}' (categoria: {category})")
        if message.stop_reason == "max_tokens":
            raise ProviderError(f"resposta cortada por max_tokens na tarefa '{task}'")
        text = next((b.text for b in message.content if getattr(b, "type", "") == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"JSON inválido da Anthropic: {exc}") from exc
        usage = message.usage
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        cost = input_tokens / 1e6 * CLAUDE_PRICE["input"] + output_tokens / 1e6 * CLAUDE_PRICE["output"]
        return LLMResult(
            data=data,
            text=text,
            usage=Usage(units=input_tokens + output_tokens, unit_name="tokens", cost_usd=round(cost, 6),
                        details={"input_tokens": input_tokens, "output_tokens": output_tokens}),
            model=getattr(message, "model", self.model),
        )


# --------------------------------------------------------------------------------------------
# Gemini (Google AI Studio)
# --------------------------------------------------------------------------------------------

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_TEXT_MODEL = "gemini-3.8-flash"


def json_schema_to_gemini(schema: dict[str, Any]) -> dict[str, Any]:
    """Converte JSON Schema (com tipos anuláveis em lista) para o subconjunto OpenAPI do Gemini."""
    result: dict[str, Any] = {}
    stype = schema.get("type")
    if isinstance(stype, list):
        non_null = [t for t in stype if t != "null"]
        if "null" in stype:
            result["nullable"] = True
        stype = non_null[0] if non_null else "string"
    if stype:
        result["type"] = str(stype).upper()
    for key in ("description", "enum", "minItems", "maxItems", "format"):
        if key in schema:
            result[key] = schema[key]
    if "properties" in schema:
        result["properties"] = {k: json_schema_to_gemini(v) for k, v in schema["properties"].items()}
        result["propertyOrdering"] = list(schema["properties"].keys())
    if "required" in schema:
        result["required"] = list(schema["required"])
    if "items" in schema:
        result["items"] = json_schema_to_gemini(schema["items"])
    return result


class GeminiLLM(LLMProvider):
    info = ProviderInfo(
        id="gemini",
        kind="llm",
        name="Gemini (Google AI Studio)",
        commercial_use=True,
        free=True,
        credential_fields=("api_key",),
        env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        cost_note="Camada gratuita (limites diários não publicados); na camada grátis o Google pode usar os textos para treino",
        notes="Use um único projeto sem cobrança para a cota grátis.",
    )

    def __init__(self, model: str = GEMINI_TEXT_MODEL) -> None:
        self.model = model

    def _api_key(self) -> str | None:
        return credentials.resolve(self.info.id, "api_key", self.info.env_vars)

    def availability(self) -> tuple[bool, str]:
        if not self._api_key():
            return False, "sem chave do Gemini (GEMINI_API_KEY ou cofre)"
        return True, "ok"

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
        key = self._api_key()
        if not key:
            raise NotConfigured("sem chave do Gemini")
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": json_schema_to_gemini(schema),
                "maxOutputTokens": max_tokens,
            },
        }
        data = gemini_post(f"models/{self.model}:generateContent", key, body)
        text = _gemini_text(data)
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"JSON inválido do Gemini: {exc}") from exc
        meta = data.get("usageMetadata", {})
        tokens = (meta.get("promptTokenCount") or 0) + (meta.get("candidatesTokenCount") or 0)
        return LLMResult(data=parsed, text=text, usage=Usage(units=tokens, unit_name="tokens", cost_usd=0.0), model=self.model)


def gemini_post(path: str, key: str, body: dict[str, Any], timeout: float = 300) -> dict[str, Any]:
    url = f"{GEMINI_BASE}/{path}"
    try:
        resp = httpx.post(url, json=body, headers={"x-goog-api-key": key}, timeout=timeout)
    except httpx.HTTPError as exc:
        raise TransientError(f"falha de conexão com o Gemini: {exc}") from exc
    if resp.status_code == 429:
        raise QuotaExceeded(f"cota do Gemini esgotada: {resp.text[:300]}")
    if resp.status_code in (401, 403):
        raise NotConfigured(f"chave do Gemini recusada ({resp.status_code}): {resp.text[:300]}")
    if resp.status_code >= 500:
        raise TransientError(f"erro {resp.status_code} no Gemini")
    if resp.status_code != 200:
        raise ProviderError(f"erro do Gemini ({resp.status_code}): {resp.text[:500]}")
    return resp.json()


def _gemini_text(data: dict[str, Any]) -> str:
    candidates = data.get("candidates") or []
    if not candidates:
        feedback = data.get("promptFeedback", {})
        raise Refused(f"o Gemini não respondeu (bloqueio: {feedback.get('blockReason', 'desconhecido')})")
    parts = candidates[0].get("content", {}).get("parts", [])
    text = "".join(p.get("text", "") for p in parts if "text" in p)
    if not text:
        raise Refused(f"resposta vazia do Gemini (finishReason={candidates[0].get('finishReason')})")
    return text


# --------------------------------------------------------------------------------------------
# Mock offline (testes e uso sem chave)
# --------------------------------------------------------------------------------------------


class MockLLM(LLMProvider):
    """Gera respostas por regras a partir do `context`. Sem custo, sem rede, determinístico."""

    info = ProviderInfo(
        id="mock_llm",
        kind="llm",
        name="LLM offline (regras)",
        commercial_use=True,
        local=True,
        free=True,
        notes="Roteiros de exemplo para testar o pipeline sem nenhuma chave. Configure Gemini ou Claude para conteúdo real.",
    )

    def complete_json(
        self,
        *,
        task: str,
        system: str,
        prompt: str,
        schema: dict[str, Any],
        max_tokens: int = 8000,
        context: dict[str, Any] | None = None,
    ) -> LLMResult:
        from studio.workers.pipeline import mock_content

        handler = getattr(mock_content, f"mock_{task}", None)
        if handler is None:
            raise ProviderError(f"o LLM offline não sabe fazer a tarefa '{task}'")
        data = handler(context or {})
        text = json.dumps(data, ensure_ascii=False)
        return LLMResult(data=data, text=text, usage=Usage(units=0, unit_name="tokens"), model="mock")
