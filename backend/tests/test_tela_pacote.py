"""Fase 6 (tela do pacote): o contrato que a tela "Título e thumb" usa. A tela é TypeScript (sem teste de unidade no
front); aqui ficam as garantias do lado do servidor: as constantes espelhadas em `apps/web/src/apiPackage.ts` batem
com o backend, toda rota que a tela chama existe, e o GET do pacote traz os campos que a tela lê."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.config import REPO_ROOT
from studio.core.project.document import EDITORIAL_NOTICE, PACKAGE_STATUS, PACKAGE_TITLE_MAX, THUMB_WORDS_MAX
from studio.core.styles.schema import THUMB_TEMPLATES

WEB = REPO_ROOT / "apps" / "web" / "src"
API_TS = (WEB / "apiPackage.ts").read_text(encoding="utf-8")

# Rotas que `packageApi` (apiPackage.ts) e o switch do avatar (apiPresenter.saveProject) chamam.
FRONT_ROUTES = [
    ("GET", "/api/projects/{project_id}/package"),
    ("POST", "/api/projects/{project_id}/package"),
    ("PUT", "/api/projects/{project_id}/package/candidates/{candidate_id}"),
    ("POST", "/api/projects/{project_id}/package/candidates"),
    ("DELETE", "/api/projects/{project_id}/package/candidates/{candidate_id}"),
    ("POST", "/api/projects/{project_id}/package/thumbs"),
    ("PUT", "/api/projects/{project_id}/package/choice"),
    ("POST", "/api/projects/{project_id}/package/approve"),
    ("POST", "/api/projects/{project_id}/package/unlock"),
    ("POST", "/api/channels/{channel_id}/thumbkit"),
]


def _ts_const(name: str) -> str:
    m = re.search(rf"export const {name}\s*=\s*([^;]+);", API_TS, re.S)
    assert m, f"{name} não está em apiPackage.ts"
    return m.group(1).strip()


def _ts_string(name: str) -> str:
    raw = _ts_const(name)
    parts = re.findall(r"'((?:[^'\\]|\\.)*)'", raw)
    assert parts, raw
    return "".join(parts)


def _shape(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{}", path)


def test_constantes_espelhadas():
    assert int(_ts_const("PACKAGE_TITLE_MAX")) == PACKAGE_TITLE_MAX
    assert int(_ts_const("THUMB_WORDS_MAX")) == THUMB_WORDS_MAX
    assert _ts_string("PACKAGE_STATUS") == PACKAGE_STATUS
    assert _ts_string("DEFAULT_EDITORIAL_NOTICE") == EDITORIAL_NOTICE
    labels = re.search(r"export const TEMPLATE_LABELS[^{]*\{(.*?)\};", API_TS, re.S)
    assert labels
    keys = set(re.findall(r"^\s*'?([a-z\-]+)'?\s*:", labels.group(1), re.M))
    assert keys == set(THUMB_TEMPLATES)


def test_etapa_e_aba_na_tela():
    model = (WEB / "pages" / "project" / "core" / "model.ts").read_text(encoding="utf-8")
    assert "const STEP_ORDER: StepId[] = ['package', 'script', 'scenes', 'preview', 'video', 'publish'];" in model
    assert "package: 'Título e thumb'" in model
    types = (WEB / "pages" / "project" / "types.ts").read_text(encoding="utf-8")
    assert "package: 'titulo-thumb'" in types
    meta = (WEB / "pages" / "projects" / "meta.ts").read_text(encoding="utf-8")
    assert f"{PACKAGE_STATUS}:" in meta


def test_rotas_que_a_tela_chama_existem():
    # Pelo OpenAPI: os routers incluídos não aparecem um a um em `app.routes` nesta versão do FastAPI.
    paths = create_app().openapi()["paths"]
    have = {(method.upper(), _shape(path)) for path, ops in paths.items() for method in ops}
    missing = [(m, p) for m, p in FRONT_ROUTES if (m, _shape(p)) not in have]
    assert not missing, f"rotas que a tela chama e o servidor não tem: {missing}"
    presenter = ("PUT", _shape("/api/projects/{id}/presenter"))
    if presenter not in have:
        pytest.skip("rota do avatar do projeto (Fase 4) ainda não registrada")


def test_get_do_pacote_traz_o_que_a_tela_le():
    with TestClient(create_app()) as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "curiosidades-longo", "topic": "o farol"})
        assert created.status_code == 200, created.text
        project_id = created.json()["project"]["id"]
        res = client.get(f"/api/projects/{project_id}/package")
        assert res.status_code == 200, res.text
        body = res.json()
        # PackageDoc (normalizePackageState) e os extras que a tela usa.
        for key in ("candidates", "chosen_id", "locked", "references", "generated_at"):
            assert key in body, key
        assert isinstance(body["references_resolved"], list)
        assert isinstance(body["allow_editorial"], bool)
        assert "editorial_notice" in body
        assert isinstance(body["templates"], list) and set(body["templates"]) <= set(THUMB_TEMPLATES)
        assert "livre" not in body["templates"]  # projeto longo
        assert isinstance(body["title_candidates"], int) and isinstance(body["thumb_candidates"], int)
        assert set(body["presenter"]) >= {"enabled", "poses"}
        assert set(body["assets"]) >= {"frames", "cutouts", "logos", "objects"}
        # Sem avatar no canal: o template do avatar não é oferecido.
        assert not body["presenter"]["poses"] and "avatar-reage" not in body["templates"]
        # Editorial desligado por padrão: sem aviso.
        if not body["allow_editorial"]:
            assert body["editorial_notice"] is None


def test_candidato_manual_devolve_id_e_escolha():
    """`createCandidate` lê `candidate.id`; a escolha devolve os aprovados com o escolhido primeiro."""
    with TestClient(create_app()) as client:
        created = client.post("/api/projects", json={"title": "Farol", "style_id": "curiosidades-longo", "topic": "o farol"})
        project_id = created.json()["project"]["id"]
        res = client.post(f"/api/projects/{project_id}/package/candidates", json={"title": "O farol sem faroleiro", "thumb_text": "SOZINHO"})
        assert res.status_code == 200, res.text
        cid = res.json()["candidate"]["id"]
        assert cid.startswith("pkc_")
        dup = client.post(f"/api/projects/{project_id}/package/candidates", json={"from_id": cid})
        assert dup.status_code == 200, dup.text
        other = dup.json()["candidate"]["id"]
        choice = client.put(f"/api/projects/{project_id}/package/choice", json={"chosen_id": cid, "approved_ids": [other]})
        assert choice.status_code == 200, choice.text
        assert choice.json()["approved_ids"] == [cid, other]
        pkg = client.get(f"/api/projects/{project_id}/package").json()
        assert pkg["chosen_id"] == cid
        assert {c["id"]: c["approved"] for c in pkg["candidates"]} == {cid: True, other: True}
        assert all(c["source"] == "manual" for c in pkg["candidates"])
