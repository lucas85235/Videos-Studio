"""Rotas das telas de edição (ADR 0004 §3.11): trilha de edição por cena, travar/apagar/mover eventos e planos,
trocar o candidato do acervo, "Novo projeto a partir desta referência" e a aderência dos projetos ao perfil. Offline."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import FxEvent, Scene, Segment, SubShot, Visual
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.db import session_scope
from studio.models import ReferenceVideo

ACERVO = Path(__file__).parent / "fixtures" / "acervo"
PROFILE = json.loads((Path(__file__).parent / "fixtures" / "perfil_gta.json").read_text(encoding="utf-8"))
STYLE = "curiosidades-longo"
TEXT = "O farol voltou a acender depois de trinta anos. A Marinha confirmou a data. O farol tem cento e cinquenta anos."


@pytest.fixture
def client(isolated_settings):
    with TestClient(create_app()) as c:
        yield c


def _project(locked: bool = False):
    doc = service.create_project(title="O farol", style_id=STYLE, topic="o farol")
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=2, visual=Visual(prompt=f"farol {i}", seed=i),
                    locked=locked and i == 1) for i in range(2)]
    doc = doc.model_copy(update={"segments": [Segment(id="seg_1", text={"pt-BR": TEXT})], "scenes": scenes})
    service.commit(doc.id, doc, "cenas")
    return service.get_doc(doc.id)


def test_edit_track_lists_words_with_triggers_and_events(client):
    doc = _project()
    doc.scenes[0].fx = [FxEvent(type="punch", at="acender"), FxEvent(type="text_pop", at="@400", locked=True)]
    doc.scenes[0].shots = [SubShot(), SubShot(at="depois", source="self")]
    service.commit(doc.id, doc, "edição")
    body = client.get(f"/api/projects/{doc.id}/edit-track").json()
    assert body["editor"] == "auto" and body["intensity"] == 2
    first = body["scenes"][0]
    assert first["scene_id"] == "scn_0" and first["words"][0]["at"] == "O" and first["words"][0]["t"] == 0
    ats = [w["at"] for w in first["words"]]
    assert "acender" in ats and "depois" in ats
    punch, pop = first["fx"]
    assert punch["type"] == "punch" and punch["t"] > 0 and pop["t"] == pytest.approx(0.4) and pop["locked"]
    assert [s["at"] for s in first["shots"]] == [None, "depois"] and first["shots"][1]["t"] > 0


def test_patch_fx_moves_locks_deletes_and_checks_the_trigger(client):
    doc = _project()
    doc.scenes[0].fx = [FxEvent(type="punch", at="acender"), FxEvent(type="sfx", at="anos", params={"kind": "pop"})]
    service.commit(doc.id, doc, "edição")
    moved = [{"type": "punch", "at": "trinta", "locked": True, "origin": "user"}]
    r = client.patch(f"/api/projects/{doc.id}/scenes/scn_0/fx", json={"fx": moved})
    assert r.status_code == 200 and r.json()["changed"] and r.json()["version"]
    fx = service.get_doc(doc.id).scene("scn_0").fx
    assert len(fx) == 1 and fx[0].at == "trinta" and fx[0].locked and fx[0].origin == "user"
    # o mesmo de novo não grava versão
    assert client.patch(f"/api/projects/{doc.id}/scenes/scn_0/fx", json={"fx": moved}).json()["changed"] is False
    bad = client.patch(f"/api/projects/{doc.id}/scenes/scn_0/fx", json={"fx": [{"type": "punch", "at": "cinquenta"}]})
    assert bad.status_code == 422 and "cinquenta" in bad.json()["detail"]  # palavra de outra cena
    assert client.patch(f"/api/projects/{doc.id}/scenes/scn_0/fx",
                        json={"fx": [{"type": "punch", "at": "palavra com espaço"}]}).status_code == 422
    assert client.patch(f"/api/projects/{doc.id}/scenes/scn_9/fx", json={"fx": []}).status_code == 404


def test_patch_shots_reopens_the_scene_and_respects_the_scene_lock(client):
    doc = _project(locked=True)
    shots = [{"at": "depois", "source": "self", "locked": True}, {"at": "anos", "source": "self"}]
    r = client.patch(f"/api/projects/{doc.id}/scenes/scn_0/shots", json={"shots": shots})
    assert r.status_code == 200
    saved = service.get_doc(doc.id).scene("scn_0").shots
    assert [s.at for s in saved] == [None, "anos"] and saved[0].locked  # o 1º plano abre a cena
    locked = client.patch(f"/api/projects/{doc.id}/scenes/scn_1/shots", json={"shots": []})
    assert locked.status_code == 409


def test_attach_pool_item_swaps_the_scene_media(client):
    doc = _project()
    rel = f"uploads/{doc.id}/pool/ilha.jpg"
    dst = get_settings().data_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ACERVO / "ilha.jpg", dst)
    save_pool(doc.id, Pool(items=[PoolItem(id="p3", tier="own", media="image", desc="A ilha vista do mar",
                                           path=rel, source_id="acervo:ilha.jpg")]))
    r = client.post(f"/api/projects/{doc.id}/scenes/scn_1/media", json={"via": "pool", "pool_id": "p3"})
    assert r.status_code == 200, r.text
    media = service.get_doc(doc.id).scene("scn_1").visual.media
    assert media is not None and media.pool_id == "p3" and media.tier == "own" and media.asset
    missing = client.post(f"/api/projects/{doc.id}/scenes/scn_1/media", json={"via": "pool", "pool_id": "p99"})
    assert missing.status_code == 404


def test_new_project_from_reference_and_its_adherence(client):
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_gta", title="Você vai zerar GTA 6", info={"w": 1920, "h": 1080, "fps": 60,
                             "dur": 804.5}, shots=[], stats={"shots": 283, "median_s": 1.43, "mean_s": 2.73,
                             "cuts_per_min": 21.0, "profile": PROFILE}, status="pronto"))
        s.add(ReferenceVideo(id="ref_novo", title="Novo", status="novo"))
    r = client.post("/api/references/ref_gta/project",
                    json={"title": "  GTA 6:  tudo do trailer ", "style_name": "GTA (referência)", "use_llm": False})
    assert r.status_code == 200, r.text
    body = r.json()
    doc = service.get_doc(body["project"]["id"])
    assert doc.title == "GTA 6: tudo do trailer" and doc.style_id == body["style"]["id"]
    assert body["style"]["name"] == "GTA (referência)"
    assert client.post("/api/references/ref_novo/project", json={"title": "x"}).status_code == 409

    adh = client.get("/api/references/ref_gta/adherence").json()
    assert [s["id"] for s in adh["styles"]] == [doc.style_id]
    assert [p["id"] for p in adh["projects"]] == [doc.id]  # sem cenas ainda: pode vir sem nota, mas aparece
    assert client.get("/api/references/ref_x/adherence").status_code == 404


def test_library_meme_goes_to_the_renderer_with_its_file(isolated_settings, monkeypatch):
    """O meme da biblioteca escolhido pelo planejador leva o arquivo (`src`) que o renderer desenha."""
    from test_edit_plan import LONG, LONG_TEXT, _doc, _fx

    from studio.core.project.document import BeatMark, GagHint
    from studio.core.styles.library import get_style
    from studio.media import meme_library
    from studio.workers.pipeline import edit_plan

    item = meme_library.MemeItem(id="susto", file="library/memes/susto.png", tags=["susto"], license="own")
    monkeypatch.setattr(meme_library, "user_items", lambda: [item])
    doc = _doc([LONG_TEXT] * 3)
    for scene in doc.scenes:
        scene.mark = BeatMark(role="joke", gag=GagHint(payoff="meme", tag="susto", at="desapareceu"))
    edit_plan.plan(doc, get_style(LONG), Pool(), recent=set())
    memes = _fx(doc, "meme")
    assert memes and all(e.params["lib_id"] == "susto" and e.params["src"] == "library/memes/susto.png"
                         for _s, e in memes)
