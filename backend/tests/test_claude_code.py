from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from studio.config import get_settings, set_settings
from studio.providers import llm
from studio.providers.base import NotConfigured, ProviderError, Refused, TransientError
from studio.providers.llm import ClaudeCodeLLM
from studio.providers.registry import DEFAULT_CHAINS

SCHEMA = {"type": "object", "properties": {"titulo": {"type": "string"}}, "required": ["titulo"]}


def _result(**extra) -> str:
    base = {
        "type": "result", "subtype": "success", "is_error": False, "api_error_status": None, "stop_reason": "tool_use",
        "result": '{"titulo": "A Luz"}', "structured_output": {"titulo": "A Luz"}, "total_cost_usd": 0.0179,
        "usage": {"input_tokens": 2, "cache_creation_input_tokens": 1422, "cache_read_input_tokens": 0, "output_tokens": 326},
        "modelUsage": {"claude-opus-5-5": {}},
    }
    base.update(extra)
    return json.dumps(base)


@pytest.fixture
def online(isolated_settings):
    set_settings(get_settings().model_copy(update={"offline": False}))
    return isolated_settings


@pytest.fixture
def fake_claude(monkeypatch, online):
    """Substitui o `claude` por respostas prontas e guarda o que foi chamado."""
    calls: list[dict] = []
    replies = {"auth": json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}),
               "print": _result(), "code": 0, "stderr": ""}

    def fake_run(cmd, **kwargs):
        if cmd[1:3] == ["auth", "status"]:
            calls.append({"auth": True})
            return subprocess.CompletedProcess(cmd, 0, replies["auth"], "")
        system_file = Path(cmd[cmd.index("--system-prompt-file") + 1])
        calls.append({"cmd": cmd, "input": kwargs.get("input"), "env": kwargs.get("env"), "cwd": kwargs.get("cwd"),
                      "system": system_file.read_text(encoding="utf-8")})
        return subprocess.CompletedProcess(cmd, replies["code"], replies["print"], replies["stderr"])

    monkeypatch.setattr(llm.shutil, "which", lambda name: "C:/fake/claude.exe" if name == "claude" else None)
    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "nao-deve-vazar")
    return calls, replies


def test_claude_code_is_first_in_the_default_llm_chain():
    assert DEFAULT_CHAINS["llm"][0] == "claude_code"
    assert DEFAULT_CHAINS["llm"][-1] == "anthropic"  # a API paga só como último recurso


def test_complete_json_uses_the_account_without_tools_or_api_key(fake_claude):
    calls, _ = fake_claude
    provider = ClaudeCodeLLM()
    result = provider.complete_json(task="script", system="Você é roteirista.", prompt="Farol " + "x" * 50_000, schema=SCHEMA)
    assert result.data == {"titulo": "A Luz"}
    assert result.model == "claude-opus-5-5"
    assert result.usage.cost_usd == 0.0 and result.usage.units == 2 + 1422 + 326
    assert result.usage.details["api_equivalent_usd"] == 0.0179
    call = calls[-1]
    cmd = call["cmd"]
    assert cmd[:4] == ["C:/fake/claude.exe", "-p", "--output-format", "json"]
    assert cmd[cmd.index("--tools") + 1] == "" and "--safe-mode" in cmd and "--no-session-persistence" in cmd
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == SCHEMA
    assert cmd[cmd.index("--model") + 1] == "opus"
    # o prompt longo vai pela entrada padrão, o sistema por arquivo e sem a chave da API no ambiente
    assert call["input"].startswith("Farol ") and all(len(part) < 5000 for part in cmd)
    assert call["system"] == "Você é roteirista."
    assert "ANTHROPIC_API_KEY" not in call["env"]
    assert not Path(call["cwd"]).exists()  # pasta temporária apagada


def test_falls_back_to_result_text_and_respects_model_env(fake_claude, monkeypatch):
    calls, replies = fake_claude
    monkeypatch.setenv("VS_CLAUDE_MODEL", "sonnet")
    replies["print"] = _result(structured_output=None, result='{"titulo": "Outro"}', modelUsage={})
    result = ClaudeCodeLLM().complete_json(task="t", system="s", prompt="p", schema=SCHEMA)
    assert result.data == {"titulo": "Outro"} and result.model == "sonnet"
    assert calls[-1]["cmd"][calls[-1]["cmd"].index("--model") + 1] == "sonnet"


@pytest.mark.parametrize(
    ("reply", "error"),
    [
        (_result(is_error=True, api_error_status=429, result="Claude AI usage limit reached|1763150400"), TransientError),
        (_result(is_error=True, api_error_status=None, result="You've hit your limit · resets 3pm"), TransientError),
        (_result(is_error=True, api_error_status=401, result="Invalid API key · Please run /login"), NotConfigured),
        (_result(is_error=True, api_error_status=404, result="There's an issue with the selected model"), NotConfigured),
        (_result(is_error=True, api_error_status=529, result="Overloaded"), TransientError),
        (_result(is_error=True, api_error_status=400, result="prompt is too long"), ProviderError),
        (_result(stop_reason="refusal", structured_output=None, result=""), Refused),
        (_result(structured_output=None, result="desculpe, não sei"), ProviderError),
        ("não é json", TransientError),
    ],
)
def test_errors_map_to_chain_outcomes(fake_claude, reply, error):
    _, replies = fake_claude
    replies["print"] = reply
    with pytest.raises(error):
        ClaudeCodeLLM().complete_json(task="t", system="s", prompt="p", schema=SCHEMA)


def test_availability_checks_login_and_caches(fake_claude):
    calls, replies = fake_claude
    provider = ClaudeCodeLLM()
    assert provider.availability() == (True, "ok (plano max)")
    provider.availability()
    assert sum(1 for c in calls if c.get("auth")) == 1  # em cache

    replies["auth"] = json.dumps({"loggedIn": False})
    provider.forget_auth()
    ok, reason = provider.availability()
    assert not ok and "claude auth login" in reason

    replies["auth"] = json.dumps({"loggedIn": True, "authMethod": "api_key"})
    provider.forget_auth()
    ok, reason = provider.availability()
    assert not ok and "API" in reason


def test_unavailable_without_cli_or_offline(monkeypatch, online):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    ok, reason = ClaudeCodeLLM().availability()
    assert not ok and "não encontrado" in reason
    set_settings(get_settings().model_copy(update={"offline": True}))
    assert ClaudeCodeLLM().availability() == (False, "modo offline")


@pytest.mark.skipif(os.environ.get("VS_TEST_CLAUDE") != "1", reason="chamada real ao Claude da conta: VS_TEST_CLAUDE=1")
def test_real_claude_code_call(online):
    provider = ClaudeCodeLLM()
    ok, reason = provider.availability()
    assert ok, reason
    result = provider.complete_json(
        task="probe", system="Responda só com os dados pedidos.",
        prompt="Dê um título curto, em português, para um vídeo sobre um farol abandonado.", schema=SCHEMA,
    )
    assert result.data["titulo"].strip() and result.usage.cost_usd == 0.0


def test_example_script_never_replaces_a_configured_llm_that_failed(monkeypatch, online):
    from studio.providers import registry
    from studio.providers.base import LLMProvider, ProviderInfo

    class Down(LLMProvider):
        info = ProviderInfo(id="down_llm", kind="llm", name="fora do ar", commercial_use=True)

        def complete_json(self, **kwargs):
            raise TransientError("erro 503 no Gemini")

    class Missing(LLMProvider):
        info = ProviderInfo(id="missing_llm", kind="llm", name="sem chave", commercial_use=True)

        def availability(self):
            return False, "sem chave"

    monkeypatch.setitem(registry._PROVIDERS, "down_llm", Down())
    monkeypatch.setitem(registry._PROVIDERS, "missing_llm", Missing())
    call = lambda p: p.complete_json(task="script", system="", prompt="", schema={}, context={"topic": "farol"})  # noqa: E731

    with pytest.raises(registry.ChainFailed) as failed:
        registry.run_chain("llm", ["missing_llm", "down_llm", "mock_llm"], call)
    assert [a.outcome for a in failed.value.attempts] == ["indisponível", "TransientError", "não usado"]
    assert "503" in str(failed.value)

    # sem nenhum provedor real configurado, o modo prévia continua funcionando
    _, provider, _ = registry.run_chain("llm", ["missing_llm", "mock_llm"], call)
    assert provider.info.id == "mock_llm"
