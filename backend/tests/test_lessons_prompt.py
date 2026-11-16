"""Lições aplicadas entrando no pedido do roteiro e dos metadados (BACKLOG, "Ficou para depois")."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any

from studio.core.project import service
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel, Lesson
from studio.providers.base import LLMResult, Usage
from studio.workers.analytics import lessons as ls
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import authoring

STYLE = "misterios-short"
T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)

SCRIPT_ANSWER = {"title": "O farol", "sources": [], "segments": [
    {"role": "hook", "speaker": "narrator", "chapter": None, "number": None, "text": "Ninguém volta do farol."},
    {"role": "cta", "speaker": "narrator", "chapter": None, "number": None, "text": "Siga o canal."}]}


class FakeLLM:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    def __call__(self, task, system, prompt, schema, context, project_id, job_id):
        self.calls.append({"task": task, "prompt": prompt, "context": context})
        return LLMResult(data=copy.deepcopy(self.data), text="", usage=Usage()), "fake_llm"


def _lesson(lid: str, text: str, scope: str = "global", status: str = "aplicada", minutes: int = 0) -> str:
    with session_scope() as s:
        s.add(Lesson(id=lid, scope=scope, text=text, status=status, created_at=T0,
                     decided_at=T0 + timedelta(minutes=minutes) if status != "proposta" else None))
    return lid


def _project(channel_id: str | None = "ch_a") -> str:
    if channel_id:
        with session_scope() as s:
            if s.get(Channel, channel_id) is None:
                s.add(Channel(id=channel_id, name=f"Canal {channel_id}"))
    doc = service.create_project(title="Teste", style_id=STYLE, topic="o farol abandonado", channel_id=channel_id)
    return doc.id


def _run(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _texts(lessons: list[dict[str, Any]]) -> list[str]:
    return [item["text"] for item in lessons]


def test_only_applied_lessons_of_the_project_scopes():
    fmt = get_style(STYLE).format.type
    _lesson("lic_g", "Gancho com pergunta nos 2 primeiros segundos.")
    _lesson("lic_c", "Canal A: cite o ano logo no início.", scope="canal:ch_a", minutes=1)
    _lesson("lic_e", "Estilo: termine com mistério em aberto.", scope=f"estilo:{STYLE}", minutes=2)
    _lesson("lic_f", "Formato: frases de até 12 palavras.", scope=f"formato:{fmt}", minutes=3)
    _lesson("lic_outro", "Canal B: nunca use humor.", scope="canal:ch_b", minutes=4)
    _lesson("lic_outro_fmt", "Formato oposto.", scope="formato:" + ("long" if fmt == "short" else "short"), minutes=5)
    _lesson("lic_prop", "Proposta ainda não decidida.", status="proposta")
    _lesson("lic_desc", "Descartada.", status="descartada", minutes=6)

    got = ls.applied_lessons("ch_a", STYLE, fmt)
    assert [item["id"] for item in got] == ["lic_f", "lic_e", "lic_c", "lic_g"]  # mais recentes primeiro
    # sem canal, a do canal A não entra
    assert "lic_c" not in [item["id"] for item in ls.applied_lessons(None, STYLE, fmt)]


def test_limit_and_char_budget():
    for i in range(12):
        _lesson(f"lic_{i:02d}", f"Lição número {i:02d} " + "x" * 80, minutes=i)
    got = ls.applied_lessons(None, None, None)
    assert len(got) == ls.MAX_LESSONS
    assert got[0]["id"] == "lic_11"
    small = ls.applied_lessons(None, None, None, max_chars=250)
    assert sum(len(t) for t in _texts(small)) <= 250
    assert len(small) == 2
    # lição gigante é cortada e não estoura o orçamento sozinha
    _lesson("lic_big", "y" * 5000, minutes=99)
    big = ls.applied_lessons(None, None, None)
    assert big[0]["id"] == "lic_big" and len(big[0]["text"]) == ls.MAX_LESSON_CHARS
    assert sum(len(t) for t in _texts(big)) <= ls.MAX_CHARS


def test_lessons_go_into_the_script_prompt_and_key_changes(monkeypatch):
    fake = FakeLLM(SCRIPT_ANSWER)
    monkeypatch.setattr(authoring, "_llm", fake)
    doc = service.get_doc(_project())
    style = get_style(STYLE)

    authoring.generate_script(doc, style)
    assert "Lições aprendidas" not in fake.calls[0]["prompt"]
    assert fake.calls[0]["context"]["lessons_key"] == ""

    one = [{"id": "lic_1", "scope": "global", "text": "Gancho com pergunta."}]
    authoring.generate_script(doc, style, lessons=one)
    prompt = fake.calls[1]["prompt"]
    assert "Lições aprendidas com vídeos anteriores" in prompt and "- Gancho com pergunta." in prompt
    key1 = fake.calls[1]["context"]["lessons_key"]
    assert key1

    two = [*one, {"id": "lic_2", "scope": "global", "text": "Frases curtas."}]
    authoring.generate_script(doc, style, lessons=two)
    assert fake.calls[2]["context"]["lessons_key"] not in ("", key1)
    # texto da lição editado também muda a chave
    edited = [{**one[0], "text": "Gancho com pergunta direta."}]
    assert ls.lessons_key(edited) != key1
    assert ls.lessons_key(one) == key1


def test_script_job_uses_and_records_lessons(monkeypatch):
    fake = FakeLLM(SCRIPT_ANSWER)
    monkeypatch.setattr(authoring, "_llm", fake)
    _lesson("lic_ok", "Abra com o fato mais estranho.")
    _lesson("lic_b", "Só para o canal B.", scope="canal:ch_b")
    _lesson("lic_p", "Proposta.", status="proposta")
    project_id = _project()

    result = _run("script", project_id)
    prompt = fake.calls[0]["prompt"]
    assert "- Abra com o fato mais estranho." in prompt
    assert "Só para o canal B." not in prompt and "Proposta." not in prompt

    used = service.get_project_row(project_id)["settings"]["lessons_used"]["script"]
    assert used["ids"] == ["lic_ok"] and used["version"] == result["version"]
    assert used["items"][0]["text"] == "Abra com o fato mais estranho."
    assert used["key"] == fake.calls[0]["context"]["lessons_key"]

    # metadados também recebem as lições e gravam à parte, sem apagar o registro do roteiro
    fake.data = {"title": "T", "description": "D", "tags": ["a"], "hashtags": ["b"], "thumbnail_text": "X"}
    _run("metadata", project_id)
    assert "- Abra com o fato mais estranho." in fake.calls[-1]["prompt"]
    settings = service.get_project_row(project_id)["settings"]["lessons_used"]
    assert set(settings) == {"script", "metadata"} and settings["metadata"]["ids"] == ["lic_ok"]


def test_script_job_without_lessons_records_empty_list(monkeypatch):
    fake = FakeLLM(SCRIPT_ANSWER)
    monkeypatch.setattr(authoring, "_llm", fake)
    project_id = _project(channel_id=None)
    _run("script", project_id)
    assert "Lições aprendidas" not in fake.calls[0]["prompt"]
    used = service.get_project_row(project_id)["settings"]["lessons_used"]["script"]
    assert used["ids"] == [] and used["key"] == ""


def test_record_failure_does_not_break_script_job(monkeypatch):
    fake = FakeLLM(SCRIPT_ANSWER)
    monkeypatch.setattr(authoring, "_llm", fake)
    project_id = _project(channel_id=None)

    def boom(*_args, **_kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(service, "update_settings_key", boom)
    result = _run("script", project_id)
    row = service.get_project_row(project_id)
    assert result["version"] and row["status"] == "script_review"
    assert "lessons_used" not in row["settings"]


def test_update_settings_key_keeps_other_stages():
    project_id = _project(channel_id=None)
    service.update_settings(project_id, {"lessons_used": {"script": {"ids": ["a"]}}, "outro": 1})
    service.update_settings_key(project_id, "lessons_used", "metadata", {"ids": ["b"]})
    settings = service.get_project_row(project_id)["settings"]
    assert settings["lessons_used"] == {"script": {"ids": ["a"]}, "metadata": {"ids": ["b"]}}
    assert settings["outro"] == 1
