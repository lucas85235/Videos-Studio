"""Busca automática de arte oficial e notícias (`media_find`): sugestões pendentes, uma chamada só ao LLM (falso),
conferência dos candidatos sem LLM (rede falsa com `MockTransport`), gravação no documento, `refresh`, nada chamado
sem busca web e a busca no fim do storyboard que nunca derruba o job."""

from __future__ import annotations

import io
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from PIL import Image

from studio.core.project import service
from studio.core.project.document import FoundMedia, MediaRef, RealHint, Scene, Segment, Source, Visual
from studio.core.project.pool import Pool
from studio.db import session_scope
from studio.media import safe_http
from studio.models import CostEntry
from studio.providers import registry
from studio.providers.base import LLMResult, ProviderInfo, TransientError, Usage
from studio.providers.llm import WEB_TASKS, ClaudeCodeLLM
from studio.workers import tasks
from studio.workers.pipeline import media_find


def jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (40, 80, 120)).save(buf, "JPEG")
    return buf.getvalue()


ARTICLE = """<html><head><meta property="og:title" content="Trailer bate recorde">
<meta property="og:image" content="https://img.example.com/og.jpg"></head><body></body></html>"""


class Net:
    def __init__(self) -> None:
        self.urls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.urls.append(url)
        images = {"/big.jpg": (1600, 900), "/big2.jpg": (1920, 1080), "/small.jpg": (400, 300), "/og.jpg": (1200, 675),
                  "/tall.jpg": (500, 800)}
        if request.url.host == "img.example.com" and request.url.path in images:
            return httpx.Response(200, headers={"content-type": "image/jpeg"},
                                  content=jpeg(*images[request.url.path]))
        if request.url.path == "/texto":
            return httpx.Response(200, headers={"content-type": "text/plain"}, text="oi")
        if request.url.host == "news.example.com":
            return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, text=ARTICLE)
        return httpx.Response(404)


@pytest.fixture
def net(monkeypatch):
    fake = Net()
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(safe_http, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    return fake


class FakeClaude:
    info = ProviderInfo(id="claude_code", kind="llm", name="Claude falso", commercial_use=True)

    def __init__(self, data: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.data = data or {"scenes": []}
        self.error = error
        self.calls: list[dict[str, Any]] = []

    def availability(self) -> tuple[bool, str]:
        return True, "ok"

    def complete_json(self, **kwargs: Any) -> LLMResult:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResult(data=self.data, text="", usage=Usage(units=1234, unit_name="tokens", cost_usd=0.0),
                         model="opus")


@pytest.fixture
def online(monkeypatch, isolated_settings):
    """Online, com o Claude Code (falso) buscando na web."""
    isolated_settings.offline = False
    monkeypatch.setattr(media_find, "web_search_status", lambda: (True, "Claude Code com WebSearch e WebFetch"))
    monkeypatch.setattr(registry, "quota_exhausted_today", lambda provider_id: False)
    return isolated_settings


def use(monkeypatch, fake: FakeClaude) -> FakeClaude:
    monkeypatch.setattr(registry, "get", lambda provider_id: fake)
    return fake


def make_project(**extra_scenes: Any) -> str:
    doc = service.create_project(title="GTA 6", style_id="misterios-short", topic="O trailer de GTA 6")
    seg = Segment(id="seg_1", text={"pt-BR": "A Rockstar mostrou a Lucia. O trailer bateu recorde. Miami de noite."})
    scenes = [
        Scene(id="scn_off", segment_id="seg_1", part=0, parts=4, visual=Visual(prompt="woman", seed=1),
              real=RealHint(kind="official", query="Lucia GTA 6 official art",
                            note="Arte oficial da Lucia no Rockstar Newswire")),
        Scene(id="scn_news", segment_id="seg_1", part=1, parts=4, visual=Visual(prompt="trailer", seed=2),
              real=RealHint(kind="news", source_ref=1, note="Notícia do recorde do trailer")),
        Scene(id="scn_photo", segment_id="seg_1", part=2, parts=4, visual=Visual(prompt="miami", seed=3),
              real=RealHint(kind="photo", query="Miami skyline night", note="Foto real de Miami")),
        Scene(id="scn_done", segment_id="seg_1", part=3, parts=4,
              visual=Visual(prompt="x", seed=4, media=MediaRef(kind="official", source="link",
                                                               asset="uploads/x/media/0123456789abcdef.jpg")),
              real=RealHint(kind="official", note="Já anexada")),
    ]
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes,
                                 "sources": [Source(title="Trailer de GTA 6 bate recorde",
                                                    url="https://news.example.com/recorde")]})
    service.commit(doc.id, doc, "cenas")
    return doc.id


def cand(image: str, page: str = "", **extra: str) -> dict[str, str]:
    return {"image": image, "page_url": page, "title": "t", "outlet": "", "why": "mostra o que a nota pede", **extra}


# ------------------------------------------------------------------------------------------------
# Contrato e prompt
# ------------------------------------------------------------------------------------------------


def test_media_find_is_a_web_task_with_tools():
    assert "media_find" in WEB_TASKS and "factcheck" in WEB_TASKS
    from pathlib import Path

    args = ClaudeCodeLLM()._command("media_find", media_find.SCHEMA, Path("s.txt"))
    assert "WebSearch,WebFetch" in args


def test_real_hint_defaults_and_found_round_trip():
    hint = RealHint(kind="news")
    assert hint.found == [] and hint.searched is False
    hint = RealHint.model_validate({"kind": "official", "searched": True, "found": [
        {"image": "https://img.example.com/a.jpg", "kind": "official", "width": 1600, "height": 900}]})
    assert hint.found[0].page_url == "" and hint.found[0].width == 1600


def test_only_pending_hints_go_in_the_prompt(online):
    pid = make_project()
    doc = service.get_doc(pid)
    ids = [s.id for s in media_find.pending_scenes(doc)]
    # foto sem busca livre vazia não entra; cena com mídia não entra
    assert ids == ["scn_off", "scn_news"]
    ctx = media_find.build_context(doc, media_find.pending_scenes(doc), today="2026-11-16")
    prompt = media_find.build_prompt(ctx)
    assert "Arte oficial da Lucia no Rockstar Newswire" in prompt and "Lucia GTA 6 official art" in prompt
    assert "https://news.example.com/recorde" in prompt and "2026-11-16" in prompt
    assert "Já anexada" not in prompt and "A Rockstar mostrou a Lucia" in prompt
    assert "image" in media_find.SCHEMA["properties"]["scenes"]["items"]["properties"]["candidates"]["items"][
        "properties"]
    assert "Nunca invente URL" in media_find.SYSTEM


def test_photo_hint_enters_only_when_free_search_found_nothing(online, monkeypatch):
    pid = make_project()
    monkeypatch.setattr(media_find, "_photo_search_empty", lambda query, orientation: query == "Miami skyline night")
    assert [s.id for s in media_find.pending_scenes(service.get_doc(pid))] == ["scn_off", "scn_news", "scn_photo"]


# ------------------------------------------------------------------------------------------------
# Conferência dos candidatos
# ------------------------------------------------------------------------------------------------


def test_validation_drops_small_non_image_http_and_dupes(net):
    items = {"scn": [
        cand("https://img.example.com/big.jpg", "https://www.rockstargames.com/newswire"),
        cand("https://img.example.com/big.jpg"),  # repetida
        cand("https://img.example.com/small.jpg"),  # pequena
        cand("https://img.example.com/texto"),  # não é imagem
        cand("http://img.example.com/big2.jpg"),  # sem https
        cand("https://img.example.com/sumiu.jpg"),  # fora do ar, sem página
        cand("https://img.example.com/tall.jpg"),  # 800 no lado maior: serve
    ]}
    found = media_find.validate_all(items, {"scn": "official"})["scn"]
    assert [f.image for f in found] == ["https://img.example.com/big.jpg", "https://img.example.com/tall.jpg"]
    assert (found[0].width, found[0].height) == (1600, 900) and found[0].kind == "official"
    assert found[0].outlet == "rockstargames.com" and found[0].page_url == "https://www.rockstargames.com/newswire"
    assert not any(u.startswith("http://") for u in net.urls)


def test_validation_falls_back_to_the_page_og_image(net):
    items = {"scn": [
        cand("https://img.example.com/sumiu.jpg", "https://news.example.com/recorde", outlet="Notícias"),
        cand("https://news.example.com/outra"),  # endereço de página no lugar da imagem: segue a og:image
    ]}
    found = media_find.validate_all(items, {"scn": "news"})["scn"]
    assert [f.image for f in found] == ["https://img.example.com/og.jpg"]  # a mesma og:image, sem repetir
    assert found[0].page_url == "https://news.example.com/recorde" and found[0].outlet == "Notícias"
    assert (found[0].width, found[0].height) == (1200, 675)


def test_validation_keeps_at_most_three_and_never_raises(net, monkeypatch):
    items = {"scn": [cand(f"https://img.example.com/big.jpg?v={i}") for i in range(6)]}
    assert len(media_find.validate_all(items, {"scn": "photo"})["scn"]) == 3

    def boom(*a, **k):
        raise RuntimeError("quebrou")

    monkeypatch.setattr(media_find, "_probe", boom)
    assert media_find.validate_candidate(cand("https://img.example.com/big.jpg"), "photo", httpx.Client()) is None


def test_parse_candidates_ignores_unknown_scenes_and_junk():
    data = {"scenes": [{"scene_id": "x", "candidates": [cand("https://a/1.jpg")]},
                       {"scene_id": "scn_off", "candidates": ["lixo", cand(""), cand("https://a/1.jpg"),
                                                              cand("https://a/1.jpg")]}]}
    out = media_find.parse_candidates(data, {"scn_off"})
    assert list(out) == ["scn_off"] and [c["image"] for c in out["scn_off"]] == ["https://a/1.jpg"]
    assert media_find.parse_candidates("nada", {"scn_off"}) == {}


# ------------------------------------------------------------------------------------------------
# Job
# ------------------------------------------------------------------------------------------------


LLM_ANSWER = {"scenes": [
    {"scene_id": "scn_off", "candidates": [cand("https://img.example.com/big.jpg", "https://www.rockstargames.com/x",
                                                outlet="Rockstar Games"),
                                           cand("https://img.example.com/small.jpg")]},
    {"scene_id": "scn_news", "candidates": [cand("https://img.example.com/og.jpg", "https://news.example.com/recorde",
                                                 outlet="Notícias Exemplo")]},
]}


def test_run_saves_found_in_the_doc_with_one_call(online, net, monkeypatch):
    pid = make_project()
    fake = use(monkeypatch, FakeClaude(LLM_ANSWER))
    result = media_find.run_media_find(pid, job_id=None)
    assert len(fake.calls) == 1 and fake.calls[0]["task"] == "media_find"
    assert result["scenes"] == 2 and result["found"] == 2 and "imagens achadas" in result["message"]
    doc = service.get_doc(pid)
    off, news, photo = doc.scene("scn_off").real, doc.scene("scn_news").real, doc.scene("scn_photo").real
    assert off.searched and [f.image for f in off.found] == ["https://img.example.com/big.jpg"]
    assert off.found[0].outlet == "Rockstar Games" and off.found[0].kind == "official"
    assert news.searched and news.found[0].page_url == "https://news.example.com/recorde"
    assert news.found[0].kind == "news"
    assert not photo.searched and doc.scene("scn_done").real.searched is False
    assert any("Busca de mídia real" in v["message"] for v in service.list_versions(pid))
    with session_scope() as s:  # o uso entra no registro de custos como as outras tarefas
        rows = s.query(CostEntry).filter_by(project_id=pid, provider="claude_code").all()
        assert len(rows) == 1 and rows[0].units == 1234


def test_searched_hints_are_skipped_unless_refresh(online, net, monkeypatch):
    pid = make_project()
    fake = use(monkeypatch, FakeClaude({"scenes": []}))
    first = media_find.run_media_find(pid)
    assert first["scenes"] == 2 and first["found"] == 0
    doc = service.get_doc(pid)
    assert doc.scene("scn_off").real.searched and doc.scene("scn_off").real.found == []
    again = media_find.run_media_find(pid)
    assert again["scenes"] == 0 and len(fake.calls) == 1
    fake.data = LLM_ANSWER
    refreshed = media_find.run_media_find(pid, refresh=True)
    assert refreshed["scenes"] == 2 and len(fake.calls) == 2
    assert service.get_doc(pid).scene("scn_off").real.found


def test_no_llm_call_without_web_search(isolated_settings, monkeypatch):
    pid = make_project()
    fake = use(monkeypatch, FakeClaude(LLM_ANSWER))
    offline = media_find.run_media_find(pid)  # testes: modo offline
    assert offline["unavailable"] and "Claude Code" in offline["message"] and not fake.calls
    isolated_settings.offline = False
    monkeypatch.setattr(media_find, "web_search_status", lambda: (False, "o LLM em uso (outro) não busca na web"))
    result = media_find.run_media_find(pid)
    assert result["unavailable"] and "não busca na web" in result["message"] and not fake.calls
    assert service.get_doc(pid).scene("scn_off").real.searched is False


def test_llm_failure_is_a_readable_error(online, monkeypatch):
    pid = make_project()
    use(monkeypatch, FakeClaude(error=TransientError("limite de uso do plano do Claude atingido")))
    with pytest.raises(media_find.MediaFindError, match="busca de arte oficial e notícias na web falhou"):
        media_find.run_media_find(pid)
    assert service.get_doc(pid).scene("scn_off").real.searched is False


def test_concurrent_edit_is_not_clobbered(online, net, monkeypatch):
    pid = make_project()
    fake = FakeClaude(LLM_ANSWER)
    original = fake.complete_json

    def edit_meanwhile(**kwargs):
        doc = service.get_doc(pid)
        doc.scene("scn_news").real.note = "Nota editada pelo usuário"
        doc.scene("scn_off").overlay_text = {"pt-BR": "Lucia"}
        service.commit(pid, doc, "edição do usuário")
        return original(**kwargs)

    fake.complete_json = edit_meanwhile  # type: ignore[method-assign]
    use(monkeypatch, fake)
    media_find.run_media_find(pid)
    doc = service.get_doc(pid)
    assert doc.scene("scn_off").overlay_text == {"pt-BR": "Lucia"} and doc.scene("scn_off").real.searched
    assert doc.scene("scn_news").real.note == "Nota editada pelo usuário" and not doc.scene("scn_news").real.searched


def test_hint_cap_per_call(online, net, monkeypatch):
    doc = service.create_project(title="Muitas", style_id="misterios-short")
    seg = Segment(id="seg_1", text={"pt-BR": "texto"})
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=11, visual=Visual(prompt="x", seed=i),
                    real=RealHint(kind="official", note=f"arte {i}")) for i in range(11)]
    service.commit(doc.id, doc.model_copy(update={"segments": [seg], "scenes": scenes}), "cenas")
    fake = use(monkeypatch, FakeClaude())
    result = media_find.run_media_find(doc.id)
    assert result["scenes"] == media_find.MAX_HINTS and result["skipped"] == 3
    assert "ficaram para a próxima" in result["message"] and len(fake.calls) == 1
    assert len(media_find.pending_scenes(service.get_doc(doc.id))) == 3


def test_job_kind_is_registered():
    from studio.api.routes_projects import JOB_KINDS
    from studio.workers.jobs import _REGISTRY

    assert "media_find" in JOB_KINDS and "media_find" in _REGISTRY


# ------------------------------------------------------------------------------------------------
# Fim do storyboard
# ------------------------------------------------------------------------------------------------


def ctx_for(pid: str) -> Any:
    return SimpleNamespace(project_id=pid, job_id=None, progress=lambda f, m="": None, cancelled=lambda: False)


def test_auto_find_offline_calls_nothing(isolated_settings, monkeypatch):
    pid = make_project()

    def never(*a, **k):
        raise AssertionError("não devia buscar")

    monkeypatch.setattr(media_find, "run_media_find", never)
    monkeypatch.setattr(media_find, "web_search_status", never)
    assert media_find.auto_find(pid, service.get_doc(pid)) == (None, None)


def test_auto_find_skips_without_web_or_pending(online, monkeypatch):
    pid = make_project()
    calls: list[str] = []
    monkeypatch.setattr(media_find, "run_media_find", lambda *a, **k: calls.append("x") or {})
    monkeypatch.setattr(media_find, "web_search_status", lambda: (False, "sem Claude Code"))
    assert media_find.auto_find(pid, service.get_doc(pid)) == (None, None) and not calls
    monkeypatch.setattr(media_find, "web_search_status", lambda: (True, "ok"))
    doc = service.get_doc(pid)
    for scene in doc.scenes:
        if scene.real:
            scene.real.searched = True
    assert media_find.auto_find(pid, doc) == (None, None) and not calls


def _fake_storyboard(monkeypatch, pid: str) -> None:
    planned = service.get_doc(pid)
    monkeypatch.setattr(tasks.authoring, "storyboard", lambda doc, style, **k: (
        planned, "falso", {"counts": {"scenes": 4, "new_images": 4, "reused": 0, "cards": 0, "real_hints": 3}}))
    monkeypatch.setattr(tasks.media_search, "prefetch", lambda *a, **k: 0)
    # O acervo (ADR 0004 §3.4) busca na web por conta própria quando está online: aqui só interessa a busca por cena.
    monkeypatch.setattr(tasks.media_pool, "gather", lambda doc, style: Pool())


def test_storyboard_queues_the_search_as_its_own_job(online, monkeypatch):
    # Fora do piloto, a busca (que pode levar minutos) não segura o storyboard: entra na fila como job próprio.
    pid = make_project()
    _fake_storyboard(monkeypatch, pid)
    queued: list[tuple[Any, ...]] = []

    class Runner:
        def submit(self, kind, payload=None, project_id=None):
            queued.append((kind, payload, project_id))
            return {"id": "job_busca"}

    monkeypatch.setattr(tasks, "get_runner", lambda: Runner())
    monkeypatch.setattr(media_find, "run_media_find", lambda *a, **k: pytest.fail("não devia buscar no storyboard"))
    result = tasks.run_storyboard(ctx_for(pid), {})
    assert queued == [("media_find", {}, pid)] and result["media_find_job"] == "job_busca"
    assert "media_find" not in result and "warnings" not in result

    def broken(*a, **k):
        raise RuntimeError("fila fora do ar")

    monkeypatch.setattr(Runner, "submit", broken)
    result = tasks.run_storyboard(ctx_for(pid), {})
    assert result["scenes"] == 4 and "media_find_job" not in result
    assert result["warnings"] and "fila fora do ar" in result["warnings"][0]

    queued.clear()
    monkeypatch.setattr(Runner, "submit", lambda self, *a, **k: queued.append(a) or {"id": "x"})
    monkeypatch.setattr(media_find, "web_search_status", lambda: (False, "sem Claude Code"))
    result = tasks.run_storyboard(ctx_for(pid), {})
    assert not queued and "media_find_job" not in result


def test_autopilot_storyboard_runs_the_search_inline_best_effort(online, monkeypatch):
    pid = make_project()
    _fake_storyboard(monkeypatch, pid)
    monkeypatch.setattr(tasks, "get_runner", lambda: pytest.fail("no piloto nada vai para a fila"))

    def fail(*a, **k):
        raise media_find.MediaFindError("A busca de arte oficial e notícias na web falhou: fora do ar")

    monkeypatch.setattr(media_find, "run_media_find", fail)
    result = tasks.run_storyboard(ctx_for(pid), {}, find_inline=True)
    assert result["scenes"] == 4 and "media_find" not in result
    assert result["warnings"] and "fora do ar" in result["warnings"][0]

    monkeypatch.setattr(media_find, "run_media_find", lambda *a, **k: {"scenes": 2, "found": 3, "message": "ok"})
    result = tasks.run_storyboard(ctx_for(pid), {}, find_inline=True)
    assert result["media_find"]["found"] == 3 and "warnings" not in result


def test_found_media_model_matches_contract():
    fields = set(FoundMedia.model_fields)
    assert fields == {"image", "page_url", "title", "outlet", "kind", "width", "height", "why"}


# ------------------------------------------------------------------------------------------------
# Cancelamento, busca de novo e anexo do achado
# ------------------------------------------------------------------------------------------------


def test_canceled_storyboard_starts_no_web_search(online, monkeypatch):
    # Cancelado no meio do storyboard: a busca paga não entra na fila nem roda no piloto.
    pid = make_project()
    _fake_storyboard(monkeypatch, pid)
    monkeypatch.setattr(tasks, "get_runner", lambda: pytest.fail("cancelado: nada vai para a fila"))
    monkeypatch.setattr(media_find, "run_media_find", lambda *a, **k: pytest.fail("cancelado: não busca"))
    ctx = SimpleNamespace(project_id=pid, job_id=None, progress=lambda f, m="": None, cancelled=lambda: True)
    result = tasks.run_storyboard(ctx, {})
    assert "media_find_job" not in result and "warnings" not in result
    result = tasks.run_storyboard(ctx, {}, find_inline=True)
    assert "media_find" not in result and "warnings" not in result


def test_cancel_before_the_llm_call(online, monkeypatch):
    pid = make_project()
    fake = use(monkeypatch, FakeClaude(LLM_ANSWER))
    with pytest.raises(media_find.BuildCancelled):
        media_find.run_media_find(pid, cancelled=lambda: True)
    assert not fake.calls and service.get_doc(pid).scene("scn_off").real.searched is False


def test_refresh_starts_with_the_hints_searched_longest_ago(online, net, monkeypatch):
    doc = service.create_project(title="Muitas", style_id="misterios-short")
    seg = Segment(id="seg_1", text={"pt-BR": "texto"})
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=10, visual=Visual(prompt="x", seed=i),
                    real=RealHint(kind="official", note=f"arte {i}")) for i in range(10)]
    service.commit(doc.id, doc.model_copy(update={"segments": [seg], "scenes": scenes}), "cenas")
    fake = use(monkeypatch, FakeClaude())

    def asked() -> list[str]:
        return [h["scene_id"] for h in fake.calls[-1]["context"]["hints"]]

    media_find.run_media_find(doc.id)
    assert asked() == [f"scn_{i}" for i in range(8)]
    media_find.run_media_find(doc.id)
    assert asked() == ["scn_8", "scn_9"]
    hints = [s.real for s in service.get_doc(doc.id).scenes]
    assert all(h.searched and h.searched_at for h in hints)
    # Busca de novo: as buscadas há mais tempo primeiro (as 8 da 1ª vez), as 2 recentes ficam para a próxima.
    media_find.run_media_find(doc.id, refresh=True)
    assert asked() == [f"scn_{i}" for i in range(8)]
    media_find.run_media_find(doc.id, refresh=True)
    assert asked()[:2] == ["scn_8", "scn_9"] and len(asked()) == 8


def test_autopilot_keeps_the_search_warning(online, monkeypatch):
    pid = make_project()
    _fake_storyboard(monkeypatch, pid)
    doc = service.get_doc(pid)
    service.commit(pid, doc.model_copy(update={"scenes": []}), "sem cenas")
    service.update_settings(pid, {"gates": {"script": True, "storyboard": True, "preview": True}})

    def fail(*a, **k):
        raise media_find.MediaFindError("A busca de arte oficial e notícias na web falhou: fora do ar")

    monkeypatch.setattr(media_find, "run_media_find", fail)
    result = tasks.run_autopilot(ctx_for(pid), {})
    assert result["stopped_at"] == "storyboard"
    assert result["warnings"] and "fora do ar" in result["warnings"][0]


def test_attaching_a_found_item_keeps_outlet_page_and_chosen_image(isolated_settings, net):
    from studio.media.real_media import media_from_link

    http = httpx.Client(transport=httpx.MockTransport(net))
    found = {"image": "https://img.example.com/big.jpg", "page_url": "https://www.rockstargames.com/newswire/x",
             "title": "Lucia no trailer", "outlet": "Rockstar Games"}
    media, warnings = media_from_link(found["image"], "prj_x", kind="official", lang="pt-BR", http=http, found=found)
    assert media.credit == "Rockstar Games" and media.page_url == found["page_url"]
    assert media.url == found["image"] and media.title == "Lucia no trailer"
    assert not any("Confira o crédito" in w for w in warnings)
    plain, warnings = media_from_link(found["image"], "prj_x", kind="official", lang="pt-BR", http=http)
    assert plain.page_url == found["image"] and any("Confira o crédito" in w for w in warnings)
    # Notícia: a imagem escolhida (não a og:image da página) e a página como fonte.
    news, _ = media_from_link("https://news.example.com/recorde", "prj_x", kind="news", lang="pt-BR", http=http,
                              found={"image": "https://img.example.com/big2.jpg",
                                     "page_url": "https://news.example.com/recorde", "outlet": "Notícias Exemplo"})
    assert news.kind == "news" and news.url == "https://img.example.com/big2.jpg"
    assert news.page_url == "https://news.example.com/recorde" and news.outlet == "Notícias Exemplo"
    og, _ = media_from_link("https://news.example.com/recorde", "prj_x", kind="news", lang="pt-BR", http=http)
    assert og.url == "https://img.example.com/og.jpg"
