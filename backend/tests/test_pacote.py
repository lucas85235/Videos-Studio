"""Fase 3 (pacote: título e thumb antes do roteiro): autoria, jobs, gate no piloto e na criação, rotas."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.config import get_settings, set_settings
from studio.core.project import service
from studio.core.project.document import CHANNEL_THUMBS_KEY, PRESENTER_KEY, PackageCandidate, ThumbLayers
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel
from studio.workers import tasks
from studio.workers.ops import today
from studio.workers.pipeline import authoring
from studio.workers.series import core as series_core

LONG, SHORT = "curiosidades-longo", "curiosidades-short"


def ctx_for(pid: str) -> Any:
    return SimpleNamespace(project_id=pid, job_id=None, progress=lambda f, m="": None, cancelled=lambda: False)


def _channel(cid: str = "chn_vice", bible: dict[str, Any] | None = None) -> str:
    with session_scope() as s:
        s.add(Channel(id=cid, name="Lucas em Vice", settings={}, style_bible=bible or {}))
    return cid


def _manifest(cid: str, items: list[dict[str, Any]]) -> None:
    path = get_settings().library_dir / "thumbkit" / cid / "thumbkit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"items": items}), encoding="utf-8")


POSES = [
    {"id": "surpreso", "file": "library/presenter/chn_vice/surpreso.png", "emotions": ["choque"], "thumb_ok": True},
    {"id": "lupa", "file": "library/presenter/chn_vice/lupa.png", "emotions": ["detalhe escondido"], "thumb_ok": True},
    {"id": "apontando", "file": "library/presenter/chn_vice/apontando.png", "aliases": ["explicando"],
     "thumb_ok": True, "facing": "right"},
]
FRAMES = [
    {"id": "t2-praia", "kind": "frame", "file": "GTAVI_Trailer_2@12.jpg", "tags": ["praia"], "editorial": True,
     "focus_x": 0.7},
    {"id": "t1-cidade", "kind": "frame", "file": "GTAVI_Trailer_1@40.jpg", "tags": ["praia"], "editorial": True},
    {"id": "ceu-livre", "kind": "frame", "file": "ceu.jpg", "tags": ["ceu"], "editorial": False},
]


def _fake_llm(monkeypatch: pytest.MonkeyPatch, data_by_task: dict[str, Any], seen: list[dict[str, Any]]) -> None:
    def fake(task, system, prompt, schema, context, project_id, job_id):
        seen.append({"task": task, "prompt": prompt, "context": context})
        data = data_by_task[task]
        return SimpleNamespace(data=data(context) if callable(data) else data), "fake"

    monkeypatch.setattr(authoring, "_llm", fake)


def _online() -> None:
    set_settings(get_settings().model_copy(update={"offline": False}))


# --- autoria ------------------------------------------------------------------------------------------------------
def test_package_ideas_normaliza_candidatos(monkeypatch):
    _online()
    cid = _channel()
    _manifest(cid, FRAMES)
    raw = {"candidates": [
        {"title": "GTA 6 primeira semana: o que saber",
         "thumb_text": "gta 6 primeira semana inteira", "promise": "Saber o que fazer primeiro.", "concept": "c",
         "template": "avatar-reage", "background_hint": "nao-existe", "characters_hint": ["jason"],
         "presenter_pose": None, "pill": "sem spoiler", "cards": []},
        {"title": "O segredo do mapa", "thumb_text": "SEGREDO", "promise": "p", "concept": "c",
         "template": "lugar-mapa", "background_hint": "ceu-livre", "characters_hint": [], "presenter_pose": None,
         "pill": None, "cards": []},
        {"title": "O segredo do mapa", "thumb_text": "REPETIDO", "promise": "p", "concept": "c",
         "template": "heroi-palavra", "background_hint": None, "characters_hint": [], "presenter_pose": None,
         "pill": None, "cards": []},
    ]}
    seen: list[dict[str, Any]] = []
    _fake_llm(monkeypatch, {"package": raw}, seen)
    doc = service.create_project(title="GTA 6", topic="GTA 6 a primeira semana", style_id=LONG, channel_id=cid)
    style = get_style(LONG)
    cands, provider = authoring.package_ideas(doc, {"name": "Lucas em Vice"}, style, 5, 3,
                                              references=[{"id": "abc", "title": "TUDO do GTA 6", "mult": 187.0,
                                                           "channel": "JTM"}])
    assert provider == "fake"
    assert len(cands) == 2  # o título repetido sai
    first, second = cands
    assert first.id.startswith("pkc_") and first.source == "auto"
    assert len(first.title) <= authoring.PACKAGE_TITLE_GEN_MAX
    assert len(first.thumb_text.split()) <= 3
    # "GTA", "6"... no máximo 1 palavra do título fica no texto
    title_words = {w.lower() for w in first.title.split()}
    assert sum(1 for w in first.thumb_text.lower().split() if w in title_words) <= 1
    assert first.layers.template != "avatar-reage"  # sem avatar ligado, o template some
    assert first.layers.background.kind == "frame"
    # editorial não liberado no canal (padrão do estilo: False): só o quadro livre é usado
    assert first.layers.background.ref == "ceu-livre"
    assert first.layers.characters == []
    assert first.layers.pill is None  # selo/rótulo genérico não vai na thumb (pacote enxuto)
    assert first.layers.logo == "none"
    assert any("CORRIGIDO" in n for n in first.score["notes"])
    assert second.layers.background.ref == "ceu-livre"
    prompt = seen[0]["prompt"]
    assert "TUDO do GTA 6 (187.0x)" in prompt and "t2-praia" not in prompt and "ceu-livre" in prompt


def test_package_ideas_usa_avatar_e_editorial_do_canal(monkeypatch):
    _online()
    bible = {PRESENTER_KEY: {"enabled": True, "poses": POSES}, CHANNEL_THUMBS_KEY: {"allow_editorial": True}}
    cid = _channel(bible=bible)
    _manifest(cid, [*FRAMES, {"id": "logo-vi", "kind": "logo", "file": "vi.png", "editorial": True}])
    raw = {"candidates": [
        {"title": f"Título {i}", "thumb_text": "TUDO", "promise": "p", "concept": "c", "template": "avatar-reage",
         "background_hint": "t2-praia", "characters_hint": [], "presenter_pose": "choque" if i == 0 else None,
         "pill": None, "cards": []} for i in range(3)]}
    _fake_llm(monkeypatch, {"package": raw}, [])
    doc = service.create_project(title="GTA 6", style_id=LONG, channel_id=cid)
    cands, _ = authoring.package_ideas(doc, {"style_bible": bible}, get_style(LONG), 3, 3)
    poses = [c.layers.presenter.pose for c in cands if c.layers.presenter]
    assert poses[0] == "surpreso"  # pela emoção
    assert len(poses) == 3 and len(set(poses)) == 3  # uma pose diferente por candidato
    assert cands[0].layers.presenter.crop == "busto" and cands[0].layers.presenter.h == 1.5
    assert cands[0].layers.background.ref == "t2-praia" and cands[0].layers.background.zoom == 1.4
    assert cands[0].layers.logo == "corner"
    # o projeto desliga o avatar na thumb
    doc.presenter.thumb = False
    assets = authoring.package_assets(doc, get_style(LONG), bible)
    assert assets.poses == [] and assets.allow_editorial


def test_short_gera_so_livre_e_um_conceito(monkeypatch):
    _online()
    raw = {"candidates": [{"title": "Curto", "thumb_text": "UAU MESMO", "promise": "p", "concept": "c",
                           "template": "heroi-palavra", "background_hint": None, "characters_hint": [],
                           "presenter_pose": "x", "pill": None, "cards": [{"asset": None, "label": "a"}]}]}
    seen: list[dict[str, Any]] = []
    _fake_llm(monkeypatch, {"package": raw}, seen)
    doc = service.create_project(title="Curto", style_id=SHORT)
    cands, _ = authoring.package_ideas(doc, {}, get_style(SHORT), 5, 3)
    assert cands[0].layers.template == "livre" and cands[0].layers.presenter is None
    assert cands[0].layers.cards == [] and cands[0].layers.background.kind == "gradient"
    assert seen[0]["context"]["templates"] == ["livre"]


def _lock(pid: str, title: str = "O título escolhido", thumb: str = "TUDO") -> None:
    doc = service.get_doc(pid)
    cand = PackageCandidate(title=title, thumb_text=thumb, promise="Saber tudo antes de jogar.",
                            concept="palavra gigante", layers=ThumbLayers())
    doc.package.candidates = [cand]
    doc.package.chosen_id = cand.id
    doc.package.locked = True
    service.commit(pid, doc, "pacote travado")


def test_roteiro_com_pacote_travado_nao_troca_o_titulo(monkeypatch):
    seen: list[dict[str, Any]] = []
    script = {"title": "Outro título", "segments": [{"role": "hook", "text": "Olá"}], "sources": []}
    _fake_llm(monkeypatch, {"script": script}, seen)
    doc = service.create_project(title="Trabalho", style_id=LONG)
    new_doc, _ = authoring.generate_script(doc, get_style(LONG))
    assert new_doc.title == "Outro título"  # sem pacote: como antes
    _lock(doc.id)
    locked = service.get_doc(doc.id)
    new_doc, _ = authoring.generate_script(locked, get_style(LONG))
    assert new_doc.title == locked.title
    prompt = seen[-1]["prompt"]
    assert 'Título fixo: "O título escolhido"' in prompt and 'Thumb: "TUDO"' in prompt
    assert "Saber tudo antes de jogar." in prompt and "não troque o título" in prompt


def test_metadata_e_traducao_com_pacote_travado():
    doc = service.create_project(title="Trabalho", style_id=LONG, languages=["pt-BR", "en-US"])
    _lock(doc.id)
    doc = service.get_doc(doc.id)
    doc, _ = authoring.metadata(doc, get_style(LONG), "pt-BR")
    assert doc.metadata["pt-BR"].title == "O título escolhido"
    assert doc.thumbnail.text["pt-BR"] == "TUDO"
    assert doc.metadata["pt-BR"].description  # a descrição continua vindo do LLM
    doc, _ = authoring.translate(doc, "en-US")
    assert doc.thumbnail.text["en-US"] == "[en-US] TUDO"  # o texto da thumb vai junto da tradução
    assert doc.metadata["en-US"].title == "[en-US] O título escolhido"
    doc, _ = authoring.metadata(doc, get_style(LONG), "en-US")
    assert doc.metadata["en-US"].title == "[en-US] O título escolhido"
    assert doc.thumbnail.text["en-US"] == "[en-US] TUDO"


# --- criação e gates ----------------------------------------------------------------------------------------------
def test_gate_do_pacote_na_criacao():
    long_doc = service.create_project(title="Longo", style_id=LONG)
    short_doc = service.create_project(title="Short", style_id=SHORT)
    assert service.get_project_row(long_doc.id)["settings"]["gates"]["package"] is True
    assert service.get_project_row(short_doc.id)["settings"]["gates"]["package"] is False
    # gates do chamador (Short derivado, série) substituem os padrões: sem a chave, não trava
    child = service.create_project(title="Filho", style_id=LONG, settings={"gates": {"script": False}})
    assert "package" not in service.get_project_row(child.id)["settings"]["gates"]
    seeded = service.create_project(title="Da ideia", style_id=LONG, seed_candidate=True)
    assert seeded.package.candidates[0].title == "Da ideia" and seeded.package.candidates[0].source == "manual"


def test_ideia_vira_projeto_com_candidato_manual():
    from studio.workers.business import ideas

    cid = _channel()
    idea = ideas.create({"title": "GTA 6: a primeira semana", "channel_id": cid, "format": "long"})
    out = ideas.to_project(idea["id"], style_id=LONG)
    doc = service.get_doc(out["project"]["id"])
    assert [c.title for c in doc.package.candidates] == ["GTA 6: a primeira semana"]
    assert out["project"]["settings"]["gates"]["package"] is True


def test_status_novo_na_serie_e_no_hoje():
    assert "package_review" in series_core.REVIEW_STATUSES
    assert today.REVIEWS["package_review"][1] == "titulo-thumb"
    source = open(series_core.__file__, encoding="utf-8").read()
    assert '"package": False' in source


def test_short_derivado_de_estilo_longo_nao_trava():
    doc = service.create_project(title="Short derivado", style_id=LONG)
    doc.style_overrides = {"format": {"type": "short"}}
    service.commit(doc.id, doc, "override")
    style = get_style(LONG, {"format": {"type": "short"}})
    assert tasks.package_gate_on({"package": True}, style) is False
    assert tasks.package_gate_on({"package": True}, get_style(LONG)) is True
    assert tasks.package_gate_on({}, get_style(LONG)) is False  # projeto antigo


# --- jobs ---------------------------------------------------------------------------------------------------------
def test_autopilot_para_no_pacote_no_longo_novo():
    doc = service.create_project(title="GTA 6 a primeira semana", style_id=LONG)
    result = tasks.run_autopilot(ctx_for(doc.id), {})
    assert result["stopped_at"] == "package" and result["steps"] == ["package"]
    row = service.get_project_row(doc.id)
    assert row["status"] == "package_review"
    doc = service.get_doc(doc.id)
    assert not doc.segments and len(doc.package.candidates) >= 3
    for cand in doc.package.candidates:
        assert cand.image and (get_settings().data_dir / cand.image).is_file()
        with Image.open(get_settings().data_dir / cand.image) as im:
            assert im.size == (1280, 720)
        assert "warnings" in cand.score and "dup" in cand.score
    # de novo, sem aprovar: não gera outra vez, só para
    again = tasks.run_autopilot(ctx_for(doc.id), {})
    assert again["stopped_at"] == "package" and again["steps"] == []
    # aprovado: segue para o roteiro com o título travado
    chosen = doc.package.candidates[1]
    tasks.approve_package(doc.id, chosen.id)
    after = tasks.run_autopilot(ctx_for(doc.id), {})
    assert after["stopped_at"] == "script"
    final = service.get_doc(doc.id)
    assert final.segments and final.title == chosen.title and final.thumbnail.text["pt-BR"] == chosen.thumb_text


def test_autopilot_nao_para_em_projeto_antigo():
    doc = service.create_project(title="Antigo", style_id=LONG)
    service.update_settings(doc.id, {"gates": {"script": True, "storyboard": True, "preview": True}})
    result = tasks.run_autopilot(ctx_for(doc.id), {})
    assert result["stopped_at"] == "script" and "package" not in result["steps"]
    assert service.get_doc(doc.id).package.candidates == []


def test_autopilot_short_gera_pacote_sem_travar():
    doc = service.create_project(title="Um Short", style_id=SHORT)
    result = tasks.run_autopilot(ctx_for(doc.id), {})
    assert result["steps"][:2] == ["package", "script"] and result["stopped_at"] == "script"
    doc = service.get_doc(doc.id)
    assert doc.package.locked and doc.package.chosen_id
    assert all(c.layers.template == "livre" for c in doc.package.candidates)
    with Image.open(get_settings().data_dir / doc.package.candidates[0].image) as im:
        assert im.size == (1080, 1920)
    assert doc.title == doc.chosen_package().title


def test_package_thumbs_sem_cenas_e_duplicata():
    doc = service.create_project(title="Sem cenas", style_id=LONG)
    same = ThumbLayers(text={"words": "TUDO"})  # type: ignore[arg-type]
    doc.package.candidates = [PackageCandidate(title="A", thumb_text="TUDO", layers=same),
                              PackageCandidate(title="B", thumb_text="TUDO", layers=same)]
    service.commit(doc.id, doc, "dois iguais")
    out = tasks.render_package_thumbs(doc.id)
    assert out["rendered"] == 2
    cands = service.get_doc(doc.id).package.candidates
    assert cands[1].score["dup"] < tasks.DUP_MIN_DISTANCE
    assert any("igual" in w for w in cands[1].score["warnings"])
    # re-render parcial só do 1º
    out = tasks.render_package_thumbs(doc.id, [cands[0].id])
    assert out["candidates"] == [cands[0].id]


# --- rotas --------------------------------------------------------------------------------------------------------
class _FakeRunner:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], str | None]] = []

    def submit(self, kind, payload=None, project_id=None, **_):
        self.calls.append((kind, payload or {}, project_id))
        return {"id": f"job_{len(self.calls)}", "kind": kind}


@pytest.fixture
def client(monkeypatch):
    from studio.api import routes_package
    from studio.api.app import create_app

    runner = _FakeRunner()
    monkeypatch.setattr(routes_package, "get_runner", lambda: runner)
    with TestClient(create_app()) as c:
        c.runner = runner  # type: ignore[attr-defined]
        yield c


def test_rotas_do_pacote(client):
    runner = client.runner
    doc = service.create_project(title="Rotas", style_id=LONG)
    pid = doc.id
    tasks.generate_package(pid, {})
    data = client.get(f"/api/projects/{pid}/package").json()
    assert data["gate"] is True and data["format"] == "long" and data["size"] == [1280, 720]
    assert data["candidates"][0]["image_url"].startswith("/files/")
    assert "avatar-reage" not in data["templates"] and data["presenter"]["enabled"] is False
    c1, c2 = data["candidates"][0]["id"], data["candidates"][1]["id"]

    r = client.post(f"/api/projects/{pid}/package", json={"n_titles": 3})
    assert r.status_code == 200 and runner.calls[-1][:2] == ("package", {"n_titles": 3, "keep_manual": True,
                                                                         "append": False})

    r = client.put(f"/api/projects/{pid}/package/candidates/{c1}", json={"thumb_text": "NOVO TEXTO"})
    assert r.status_code == 200, r.text
    cand = r.json()["candidate"]
    assert cand["source"] == "manual" and cand["layers"]["text"]["words"] == "NOVO TEXTO"
    assert runner.calls[-1][:2] == ("package_thumbs", {"candidate_ids": [c1]})

    r = client.post(f"/api/projects/{pid}/package/candidates", json={"from_id": c1, "title": "Cópia"})
    copy_id = r.json()["candidate"]["id"]
    assert r.json()["candidate"]["thumb_text"] == "NOVO TEXTO" and copy_id != c1
    assert client.delete(f"/api/projects/{pid}/package/candidates/{copy_id}").status_code == 200
    assert client.delete(f"/api/projects/{pid}/package/candidates/nada").status_code == 404

    r = client.put(f"/api/projects/{pid}/package/choice", json={"chosen_id": c1, "approved_ids": [c2]})
    assert r.json()["approved_ids"] == [c1, c2]

    r = client.post(f"/api/projects/{pid}/package/approve", json={})
    assert r.status_code == 200, r.text
    assert runner.calls[-1][0] == "autopilot"
    doc = service.get_doc(pid)
    assert doc.package.locked and doc.title == doc.chosen_package().title

    assert client.put(f"/api/projects/{pid}/package/candidates/{c1}", json={"title": "x"}).status_code == 409
    assert client.post(f"/api/projects/{pid}/package").status_code == 409
    assert client.put(f"/api/projects/{pid}/package/choice", json={"chosen_id": c2}).status_code == 409

    r = client.post(f"/api/projects/{pid}/package/unlock")
    assert r.status_code == 200 and r.json()["notice"] is None  # ainda sem roteiro
    assert not service.get_doc(pid).package.locked

    # alias no approve geral
    r = client.post(f"/api/projects/{pid}/approve", json={"stage": "package"})
    assert r.status_code == 200, r.text
    assert service.get_doc(pid).package.locked

    r = client.post(f"/api/projects/{pid}/package/thumbs", json={"candidate_ids": [c2]})
    assert r.status_code == 200 and runner.calls[-1][:2] == ("package_thumbs", {"candidate_ids": [c2]})


def _png(alpha: bool) -> bytes:
    im = Image.new("RGBA" if alpha else "RGB", (40, 60), (255, 0, 0, 0) if alpha else (255, 0, 0))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_upload_da_biblioteca_de_thumbs(client):
    cid = _channel()
    bad = client.post(f"/api/channels/{cid}/thumbkit", data={"kind": "cutout"},
                      files={"file": ("jason.png", _png(False), "image/png")})
    assert bad.status_code == 422
    ok = client.post(f"/api/channels/{cid}/thumbkit", data={"kind": "cutout", "tags": "jason, rosto"},
                     files={"file": ("Jason.png", _png(True), "image/png")})
    assert ok.status_code == 200, ok.text
    item = ok.json()
    assert item["kind"] == "cutout" and item["tags"] == ["jason", "rosto"]
    assert [i["id"] for i in authoring.thumbkit_manifest(cid)] == [item["id"]]
    assert client.post("/api/channels/nada/thumbkit", files={"file": ("a.png", _png(True), "image/png")}
                       ).status_code == 404
