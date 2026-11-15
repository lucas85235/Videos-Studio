"""Checagem de fatos no roteiro (ADR 0003, bloco F): texto da página, conferência literal, job, correção e rotas.

Nada de rede: o DNS é trocado por monkeypatch (IP público) e o httpx usa `MockTransport`; o LLM é o offline.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import Segment, Source
from studio.db import session_scope
from studio.ids import new_id
from studio.media import safe_http
from studio.models import FactClaim, PageCapture
from studio.providers import llm
from studio.providers.llm import ClaudeCodeLLM
from studio.workers.factcheck import verify as verify_mod
from studio.workers.factcheck.apply import apply_correction, summary
from studio.workers.factcheck.claims import FactcheckError, run_factcheck, segment_hash
from studio.workers.factcheck.textify import find_quote, html_to_text, literal_span, normalize, words
from studio.workers.factcheck.verify import MISSING_NOTE, PDF_NOTE, verify
from studio.workers.jobs import get_runner

PUBLIC_IP = "93.184.216.34"
PAGE_URL = "https://noticias.example.com/materia"
SEG1 = "O farol foi aceso em 1872 pela marinha. Depois disso, ninguém mais subiu."
SEG2 = "Ninguém sabe ao certo o que aconteceu naquela noite."
PAGE_HTML = f"""<!doctype html><html><head><title>Matéria</title><style>p {{ color: red }}</style>
<script>var segredo = "não deve aparecer";</script></head><body>
<nav>Menu Início Sobre</nav><header>Cabeçalho do site</header>
<article><h1>O farol</h1><p>{SEG1}</p><p>Fonte: arquivo &amp; registros.</p></article>
<footer>Rodapé com direitos</footer></body></html>"""


@pytest.fixture(autouse=True)
def dns(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: [PUBLIC_IP])


def mock_client(pages: dict[str, httpx.Response | int]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        page = pages.get(str(request.url))
        if page is None:
            return httpx.Response(404)
        if isinstance(page, int):
            return httpx.Response(page)
        return page

    return httpx.Client(transport=httpx.MockTransport(handler))


def html(text: str) -> httpx.Response:
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=text.encode("utf-8"))


@pytest.fixture
def web(monkeypatch):
    """Toda busca do app (`safe_http.fetch` sem cliente) cai nesta tabela de páginas."""
    pages: dict[str, httpx.Response | int] = {PAGE_URL: html(PAGE_HTML)}
    monkeypatch.setattr(safe_http, "new_client", lambda: mock_client(pages))
    return pages


@pytest.fixture
def project():
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado")
    doc.segments = [Segment(id="seg1", role="hook", text={doc.primary_language: SEG1}),
                    Segment(id="seg2", text={doc.primary_language: SEG2})]
    doc.sources = [Source(title="Notícias", url=PAGE_URL, note="matéria de 2020")]
    service.commit(doc.id, doc, "roteiro")
    return service.get_doc(doc.id)


def _edit_segment(project_id: str, seg_id: str, text: str, *, locked: bool | None = None) -> None:
    doc = service.get_doc(project_id)
    seg = doc.segment(seg_id)
    seg.text[doc.primary_language] = text
    if locked is not None:
        seg.locked = locked
    service.commit(project_id, doc, "edição à mão")


def _claims(project_id: str) -> list[FactClaim]:
    with session_scope() as s:
        rows = s.query(FactClaim).filter_by(project_id=project_id).all()
        for r in rows:
            s.expunge(r)
        return rows


# ------------------------------------------------------------------------------------------------
# Texto e comparação
# ------------------------------------------------------------------------------------------------


def test_normalize_quotes_dashes_and_hard_space():
    assert normalize("“Ele disse” ‘sim’ — ok") == normalize('"ele disse" \'sim\' - ok')
    assert normalize("d’Água Branca") == "d'água branca"
    assert normalize("10− 5 – 3  \n  x") == "10- 5 - 3 x"
    assert normalize("ＡＢＣ") == "abc"  # NFKC


def test_html_to_text_drops_scripts_and_menus():
    text = html_to_text(PAGE_HTML)
    assert "segredo" not in text and "color" not in text
    assert "Menu" not in text and "Cabeçalho" not in text and "Rodapé" not in text
    assert SEG1 in text and "Fonte: arquivo & registros." in text
    assert text.splitlines()[:2] == ["Matéria", "O farol"]  # quebra de linha nos blocos
    assert html_to_text("<p>a<svg><path d='x'/><text>nada</text></svg>b</p><p>c</p>") == "ab\nc"


def test_find_quote_found_loose_missing():
    page = "O relatório diz: “o farol foi aceso em 1872 pela marinha”."
    assert find_quote('o farol foi aceso em 1872 pela Marinha', page) == "found"
    assert find_quote("“o farol foi aceso", page) == "found"
    assert find_quote("o farol foi aceso em 1872", "ofarolfoiaceso em1872 pela") == "loose"
    assert find_quote("o farol foi apagado em 1990", page) == "missing"
    assert find_quote("", page) == "missing"
    assert words("um, dois — três") == ["um,", "dois", "três"]


def test_literal_span_keeps_the_script_text():
    text = "Ele disse  “não”  em 1872."
    assert literal_span('disse "não" em', text) == "disse  “não”  em"
    assert literal_span("outra coisa", text) is None


# ------------------------------------------------------------------------------------------------
# Conferência na página
# ------------------------------------------------------------------------------------------------


def test_verify_html_found_and_missing():
    client = mock_client({PAGE_URL: html(PAGE_HTML)})
    assert verify(PAGE_URL, "O farol foi aceso em 1872 pela marinha", http=client) == (True, "")
    found, note = verify(PAGE_URL, "o farol foi aceso em 1900 pela marinha", http=client)
    assert found is False and note == MISSING_NOTE
    # texto de OCR que junta palavras
    ocr = mock_client({PAGE_URL: httpx.Response(200, headers={"content-type": "text/plain"},
                                                content=b"ofarol foiaceso em1872 pela marinha")})
    found, note = verify(PAGE_URL, "o farol foi aceso em 1872", http=ocr)
    assert found is True and "OCR" in note


def test_verify_word_count_note():
    client = mock_client({PAGE_URL: html(PAGE_HTML)})
    found, note = verify(PAGE_URL, "O farol foi", http=client)
    assert found is True and note == "trecho com 3 palavras (o certo é de 5 a 30)"
    long_quote = " ".join(["palavra"] * 31)
    found, note = verify(PAGE_URL, long_quote, http=client)
    assert found is False and "trecho com 31 palavras (o certo é de 5 a 30)" in note


def test_verify_without_link_or_quote():
    assert verify("", "um dois três quatro cinco") == (None, "sem link da fonte")
    assert verify(PAGE_URL, "  ") == (None, "sem trecho literal da fonte")


def test_verify_pdf_is_checked_by_hand():
    client = mock_client({PAGE_URL: httpx.Response(200, headers={"content-type": "application/pdf"},
                                                   content=b"%PDF-1.7 ...")})
    assert verify(PAGE_URL, "o farol foi aceso em 1872", http=client) == (None, PDF_NOTE)


def test_verify_blocked_page_uses_the_page_capture(isolated_settings):
    blocked = mock_client({PAGE_URL: 403})
    found, note = verify(PAGE_URL, "o farol foi aceso em 1872", http=blocked)
    assert found is None and note.startswith("a página não abriu (O site respondeu 403)")
    assert "capture a página" in note

    rel = "captures/farol.txt"
    path = Path(get_settings().data_dir) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("Arquivo: O farol foi aceso em 1872 pela marinha.", encoding="utf-8")
    with session_scope() as s:
        s.add(PageCapture(id=new_id("cap"), url=PAGE_URL + "/", text_path=rel))
    found, note = verify(PAGE_URL, "o farol foi aceso em 1872", http=blocked)
    assert found is True and "captura" in note
    assert verify(PAGE_URL, "o farol foi demolido em 1990", http=blocked)[0] is False


# ------------------------------------------------------------------------------------------------
# Job, correção e resumo
# ------------------------------------------------------------------------------------------------


def test_job_creates_and_checks_claims_offline(project, web):
    job = get_runner().submit("factcheck", {}, project.id, run_inline=True)
    from studio.workers.jobs import get_job

    done = get_job(job["id"])
    assert done["status"] == "done", done["error"]
    assert done["result"]["claims"] == 1 and done["result"]["ok"] == 1 and done["result"]["web"] is False
    rows = _claims(project.id)
    assert len(rows) == 1
    claim = rows[0]
    assert claim.segment_id == "seg1" and claim.verdict == "ok" and claim.url == PAGE_URL
    assert claim.excerpt == "O farol foi aceso em 1872 pela marinha."
    assert claim.quote == "O farol foi aceso em 1872" and claim.quote_found is True
    assert claim.check_note == "" and claim.checked_at is not None and claim.run_id == job["id"]
    assert claim.segment_hash == segment_hash(SEG1)
    state = service.get_project_row(project.id)["settings"]["factcheck"][project.primary_language]
    assert state["checked"] == {"seg1": segment_hash(SEG1), "seg2": segment_hash(SEG2)}
    assert state["last_job"] == job["id"] and state["web"] is False

    # checagem nova do segmento substitui as afirmações anteriores dele
    run_factcheck(project.id, segment_ids=["seg1"])
    rows2 = _claims(project.id)
    assert len(rows2) == 1 and rows2[0].id != claim.id


def test_page_that_does_not_open_leaves_the_claim_unchecked(project, web):
    web[PAGE_URL] = 403
    result = run_factcheck(project.id)
    assert result["quote_missing"] == 0
    claim = _claims(project.id)[0]
    assert claim.quote_found is None and "a página não abriu" in claim.check_note


def _with_correction(project_id: str) -> FactClaim:
    run_factcheck(project_id)
    claim = _claims(project_id)[0]
    with session_scope() as s:
        row = s.get(FactClaim, claim.id)
        row.verdict = "corrigir"
        row.correction = "O farol foi aceso em 1874 pela marinha."
    return claim


def test_apply_writes_a_new_version_and_marks_applied(project, web):
    claim = _with_correction(project.id)
    lang = project.primary_language
    assert summary(project.id, lang)["open"] == 1
    before = service.get_project_row(project.id)["current_version"]
    out = apply_correction(project.id, claim.id)
    assert out["version"] == before + 1 and out["claim"]["status"] == "aplicada"
    doc = service.get_doc(project.id)
    assert doc.segment("seg1").text[lang].startswith("O farol foi aceso em 1874 pela marinha. Depois")
    versions = service.list_versions(project.id)
    assert versions[0]["author"] == "factcheck" and versions[0]["message"].startswith("Checagem: ")
    s = summary(project.id, lang)
    assert s["open"] == 0 and s["stale"] == 0 and s["unchecked"] == 0 and s["applied"] == 1
    with pytest.raises(FactcheckError) as info:
        apply_correction(project.id, claim.id)
    assert info.value.status == 409


def test_apply_refuses_locked_segment_and_changed_excerpt(project, web):
    claim = _with_correction(project.id)
    _edit_segment(project.id, "seg1", SEG1, locked=True)
    with pytest.raises(FactcheckError) as info:
        apply_correction(project.id, claim.id)
    assert info.value.status == 409 and info.value.message == "segmento travado"

    _edit_segment(project.id, "seg1", "O farol foi aceso há muito tempo.", locked=False)
    with pytest.raises(FactcheckError) as info:
        apply_correction(project.id, claim.id)
    assert info.value.status == 409 and info.value.message == "o trecho mudou desde a checagem: edite à mão"


def test_claim_goes_stale_after_editing_the_segment(project, web):
    run_factcheck(project.id)
    lang = project.primary_language
    s = summary(project.id, lang)
    assert s["stale"] == 0 and s["unchecked"] == 0 and s["ok"] == 1
    _edit_segment(project.id, "seg1", SEG1 + " Mais um detalhe.")
    s = summary(project.id, lang)
    assert s["stale"] == 1 and s["unchecked"] == 1


# ------------------------------------------------------------------------------------------------
# Rotas
# ------------------------------------------------------------------------------------------------


def test_routes(project, web):
    run_factcheck(project.id)
    claim_id = _claims(project.id)[0].id
    with TestClient(create_app()) as c:
        r = c.get(f"/api/projects/{project.id}/factcheck")
        assert r.status_code == 200, r.text
        data = r.json()
        assert len(data["claims"]) == 1 and data["claims"][0]["stale"] is False
        assert data["summary"]["ok"] == 1 and data["web"]["available"] is False

        r = c.patch(f"/api/projects/{project.id}/factcheck/{claim_id}",
                    json={"quote": "pela marinha. Depois disso, ninguém", "verdict": "corrigir",
                          "correction": "O farol foi aceso em 1874 pela marinha."})
        assert r.status_code == 200 and r.json()["quote_found"] is None
        r = c.post(f"/api/projects/{project.id}/factcheck/{claim_id}/verify")
        assert r.status_code == 200 and r.json()["quote_found"] is True

        md = c.get(f"/api/projects/{project.id}/factcheck/sources.md")
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        assert "| Trecho | Fala |" in md.text and PAGE_URL in md.text and "(achada)" in md.text

        r = c.post(f"/api/projects/{project.id}/factcheck/{claim_id}/apply")
        assert r.status_code == 200 and r.json()["claim"]["status"] == "aplicada"
        r = c.patch(f"/api/projects/{project.id}/factcheck/{claim_id}", json={"status": "ignorada"})
        assert r.status_code == 409
        assert c.post(f"/api/projects/{project.id}/factcheck/xyz/apply").status_code == 404
        assert c.get(f"/api/projects/{project.id}/factcheck?lang=xx-XX").status_code == 400


# ------------------------------------------------------------------------------------------------
# Claude Code: ferramentas web só na checagem
# ------------------------------------------------------------------------------------------------


def test_command_releases_web_tools_only_for_factcheck(monkeypatch, tmp_path):
    monkeypatch.delenv("VS_FACTCHECK_WEB", raising=False)
    provider = ClaudeCodeLLM()
    system = tmp_path / "system.txt"
    cmd = provider._command("factcheck", {"type": "object"}, system)
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    allowed = cmd.index("--allowedTools")
    assert cmd[allowed + 1:allowed + 3] == ["WebSearch", "WebFetch"]
    assert "--safe-mode" in cmd and "--no-session-persistence" in cmd
    assert provider._timeout("factcheck") == 1800

    for task in ("script", "storyboard", "edit", "translate"):
        other = provider._command(task, {"type": "object"}, system)
        assert other[other.index("--tools") + 1] == "" and "--allowedTools" not in other
        assert "WebSearch" not in " ".join(other) and provider._timeout(task) == provider.timeout_s

    monkeypatch.setenv("VS_FACTCHECK_WEB", "0")
    off = provider._command("factcheck", {"type": "object"}, system)
    assert off[off.index("--tools") + 1] == "" and "--allowedTools" not in off
    assert provider._timeout("factcheck") == provider.timeout_s


def test_web_search_status_offline():
    available, reason = llm.web_search_status()
    assert available is False and reason
    assert llm.web_search_available() is False


def test_verify_module_uses_safe_http(monkeypatch):
    """A conferência nunca abre rede interna: o link passa pelo `check_url` do safe_http."""
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["10.0.0.5"])
    found, note = verify_mod.verify("https://interno.example.com/x", "um dois três quatro cinco")
    assert found is None and "a página não abriu" in note


# ------------------------------------------------------------------------------------------------
# Correções da revisão
# ------------------------------------------------------------------------------------------------


def test_prompt_only_offers_web_search_to_claude_code(project, web, monkeypatch):
    """Quem responde sem WebSearch/WebFetch não lê que pode buscar (empurraria para link inventado)."""
    from studio.providers import registry
    from studio.workers.factcheck import claims as claims_mod

    ctx = claims_mod.build_context(project, project.primary_language, project.segments)
    assert "WebSearch" in claims_mod.build_prompt(ctx, web=True)
    monkeypatch.delenv("VS_FACTCHECK_WEB", raising=False)
    provider = registry.get(registry.resolve_chain("llm", None)[0])
    assert provider.info.id != "claude_code"
    prompts: list[str] = []
    original = provider.complete_json

    def spy(**kwargs):
        prompts.append(kwargs["prompt"])
        return original(**kwargs)

    monkeypatch.setattr(provider, "complete_json", spy)
    result = run_factcheck(project.id)
    assert prompts and "WebSearch" not in prompts[0] and "Sem busca web" in prompts[0]
    assert result["web"] is False
    assert claims_mod.provider_has_web("claude_code") is True
    monkeypatch.setenv("VS_FACTCHECK_WEB", "0")
    assert claims_mod.provider_has_web("claude_code") is False


def test_web_search_status_skips_provider_out_of_quota(monkeypatch):
    from studio.providers import registry

    class Fake:
        def availability(self):
            return True, ""

    monkeypatch.delenv("VS_FACTCHECK_WEB", raising=False)
    monkeypatch.setattr(registry, "resolve_chain", lambda kind, override: ["claude_code"])
    monkeypatch.setattr(registry, "get", lambda provider_id: Fake())
    monkeypatch.setattr(registry, "quota_exhausted_today", lambda provider_id: False)
    assert llm.web_search_status()[0] is True
    monkeypatch.setattr(registry, "quota_exhausted_today", lambda provider_id: True)
    available, reason = llm.web_search_status()
    assert available is False and "limite do dia" in reason


def test_verify_reads_latin1_page_declared_in_meta():
    page = ('<html><head><meta charset="iso-8859-1"></head><body><p>A Constituição da República garante a '
            'função social da propriedade.</p></body></html>').encode("latin-1")
    client = mock_client({PAGE_URL: httpx.Response(200, headers={"content-type": "text/html"}, content=page)})
    found, _ = verify(PAGE_URL, "A Constituição da República garante a função social", http=client)
    assert found is True
    # sem charset nenhum e sem <meta>: UTF-8 falha, windows-1252 lê
    bare = mock_client({PAGE_URL: httpx.Response(200, headers={"content-type": "text/plain"},
                                                 content="Ação de 1872 pela Marinha do Brasil".encode("cp1252"))})
    assert verify(PAGE_URL, "Ação de 1872 pela Marinha do Brasil", http=bare)[0] is True


def test_verify_truncated_page_is_not_a_sure_miss():
    body = b"<html><body><p>" + b"x " * (safe_http.HTML_MAX_BYTES // 2 + 10) + b"</p></body></html>"
    client = mock_client({PAGE_URL: httpx.Response(200, headers={"content-type": "text/html"}, content=body)})
    found, note = verify(PAGE_URL, "o farol foi aceso em 1872 pela marinha", http=client)
    assert found is None and note.startswith("página grande demais")


def test_html_to_text_keeps_form_body_and_article_header():
    assert html_to_text("<form id=aspnetForm><div><p>O farol foi aceso em 1872.</p></div></form>") == \
        "O farol foi aceso em 1872."
    text = html_to_text("<header>Site</header><article><header><h1>Título</h1><p>Linha fina com 1872</p></header>"
                        "<p>Corpo</p><footer>Crédito</footer></article><footer>Rodapé</footer>")
    assert text.splitlines() == ["Título", "Linha fina com 1872", "Corpo", "Crédito"]


def test_verify_finds_quote_outside_the_article():
    page = "<html><body><header><p>Atualizado em 3 de março de 2020 às 10h</p></header><article>x</article></body></html>"
    client = mock_client({PAGE_URL: html(page)})
    assert verify(PAGE_URL, "Atualizado em 3 de março de 2020", http=client)[0] is True


def test_literal_span_with_decomposed_accents():
    import unicodedata

    text = unicodedata.normalize("NFD", "a ação x")
    span = literal_span("ação", text)
    assert span == unicodedata.normalize("NFD", "ação")
    assert literal_span("a ação", unicodedata.normalize("NFD", "Na ação.")) == unicodedata.normalize("NFD", "a ação")


def test_cancelled_factcheck_keeps_the_old_claims(project, web):
    from studio.workers.pipeline.build import BuildCancelled

    run_factcheck(project.id)
    before = [c.id for c in _claims(project.id)]
    with pytest.raises(BuildCancelled):
        run_factcheck(project.id, cancelled=lambda: True)
    assert [c.id for c in _claims(project.id)] == before


def test_checking_again_keeps_ignored_claims(project, web):
    run_factcheck(project.id)
    claim = _claims(project.id)[0]
    with session_scope() as s:
        s.get(FactClaim, claim.id).status = "ignorada"
    run_factcheck(project.id)
    rows = _claims(project.id)
    assert len(rows) == 1 and rows[0].id != claim.id and rows[0].status == "ignorada"


def test_summary_lists_unchecked_segments(project, web):
    run_factcheck(project.id, segment_ids=["seg1"])
    s = summary(project.id, project.primary_language)
    assert s["unchecked"] == 1 and s["unchecked_ids"] == ["seg2"]


def test_patch_refuses_non_http_link(project, web):
    run_factcheck(project.id)
    claim_id = _claims(project.id)[0].id
    with TestClient(create_app()) as c:
        url = f"/api/projects/{project.id}/factcheck/{claim_id}"
        for bad in ("javascript:alert(1)", "ftp://x.example.com/a", "arquivo.pdf"):
            r = c.patch(url, json={"url": bad})
            assert r.status_code == 400 and "http" in r.json()["detail"]
        assert c.patch(url, json={"url": ""}).status_code == 200
        assert c.patch(url, json={"url": PAGE_URL}).status_code == 200
