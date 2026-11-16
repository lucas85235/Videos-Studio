"""Provedor de LLM: Claude pela conta do usuário (Claude Code). O mock de regras só existe no modo offline (testes).

A API paga da Anthropic e o Gemini (texto) foram retirados a pedido do usuário em 14/11/2026: roteiro, tradução e
agente usam só o plano do Claude. Se o Claude Code não estiver logado, o job falha com o motivo.
"""

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

from studio.core.subproc import run_tree
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


# --------------------------------------------------------------------------------------------
# Claude pela conta (Claude Code em modo não interativo)
# --------------------------------------------------------------------------------------------

CLAUDE_CODE_MODEL = "opus"  # apelido do Claude Code para o Opus mais recente
# Variáveis que fariam o Claude Code cobrar pela API em vez de usar o plano da conta.
_API_ENV_VARS = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")
_AUTH_TTL_S = 300.0
# Tarefas que podem buscar na web (checagem de fatos, ADR 0003 bloco F; busca de arte oficial e notícias para as
# sugestões de mídia real). As outras rodam sem nenhuma ferramenta.
WEB_TASKS = frozenset({"factcheck", "media_find"})
WEB_TIMEOUT_S = 1800  # a busca e a leitura das páginas levam bem mais que a escrita


def web_tools_enabled() -> bool:
    """Busca web na checagem ligada (padrão). `VS_FACTCHECK_WEB=0` desliga."""
    return os.environ.get("VS_FACTCHECK_WEB", "1").strip() != "0"


def web_search_status() -> tuple[bool, str]:
    """A checagem de fatos terá busca web? Só quando o 1º LLM disponível da cadeia é o Claude Code."""
    from studio.providers import registry  # aqui: o registro importa este módulo

    if not web_tools_enabled():
        return False, "busca web desligada (VS_FACTCHECK_WEB=0)"
    reasons: list[str] = []
    for provider_id in registry.resolve_chain("llm", None):
        try:
            ok, reason = registry.get(provider_id).availability()
        except Exception as exc:  # noqa: BLE001 - só informa
            ok, reason = False, str(exc)
        if ok and registry.quota_exhausted_today(provider_id):  # o run_chain pula esse provedor
            ok, reason = False, f"{provider_id}: limite do dia atingido"
        if ok:
            if provider_id == "claude_code":
                return True, "Claude Code com WebSearch e WebFetch"
            return False, f"o LLM em uso ({provider_id}) não busca na web"
        reasons.append(reason)
    return False, "nenhum LLM disponível" + (f": {reasons[0]}" if reasons else "")


def web_search_available() -> bool:
    return web_search_status()[0]


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
        notes="Usa o Claude Code com a sua conta (faça login uma vez no terminal com `claude auth login`). "
        "Para uso pessoal. Modelo e esforço: VS_CLAUDE_MODEL (padrão opus) e VS_CLAUDE_EFFORT (padrão medium).",
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
                proc = run_tree([claude, "auth", "status"], env=self._env(), timeout=30)
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
            cmd = [claude, *self._command(task, schema, system_file)]
            timeout = self._timeout(task)
            try:
                proc = run_tree(cmd, input=prompt, cwd=workdir, env=self._env(), timeout=timeout)
            except subprocess.TimeoutExpired as exc:
                raise TransientError(f"o Claude demorou mais de {timeout // 60} min na tarefa '{task}'") from exc
            except OSError as exc:
                raise TransientError(f"não consegui iniciar o Claude Code: {exc}") from exc
        return self._parse(task, proc.stdout or "", proc.stderr or "", proc.returncode)

    @staticmethod
    def _web(task: str) -> bool:
        return task in WEB_TASKS and web_tools_enabled()

    def _timeout(self, task: str) -> int:
        return max(self.timeout_s, WEB_TIMEOUT_S) if self._web(task) else self.timeout_s

    def _command(self, task: str, schema: dict[str, Any], system_file: Path) -> list[str]:
        """Argumentos do `claude` (sem o executável). Só as tarefas de `WEB_TASKS` ganham WebSearch e WebFetch,
        liberadas também em `--allowedTools` (sem isso o `-p` para pedindo permissão); o resto roda sem ferramentas."""
        tools = (
            ["--tools", "WebSearch,WebFetch", "--allowedTools", "WebSearch", "WebFetch"] if self._web(task)
            else ["--tools", ""]
        )
        return [
            "-p", "--output-format", "json",
            "--json-schema", json.dumps(schema, ensure_ascii=False),
            *tools, "--no-session-persistence", "--safe-mode",
            "--model", self.model, "--effort", self.effort,
            "--system-prompt-file", str(system_file),
        ]

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
# Chamada HTTP ao Google AI Studio (usada pela voz do Gemini)
# --------------------------------------------------------------------------------------------

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


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
        hidden=True,
        notes="Só no modo offline (testes): roteiros de exemplo por regras.",
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
