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
from studio.core.project.document import FxEvent, MediaRef, Scene, Segment, SubShot, Visual
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.db import session_scope
from studio.models import ReferenceVideo

ACERVO = Path(__file__).parent / "fixtures" / "acervo"
PROFILE = json.loads((Path(__file__).parent / "fixtures" / "perfil_gta.json").read_text(encoding="utf-8"))
STYLE = "curiosidades-longo"
# Nome que o downloader deu ao arquivo (as referências antigas guardaram isso como título).
RAW_TITLE = "YTDown.com_YouTube_Media_dQw4w9WgXcQ_Voce_vai_zerar_GTA_6_1080p"
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


def _seed_references() -> None:
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_gta", title=RAW_TITLE,
                             info={"w": 1920, "h": 1080, "fps": 60, "dur": 804.5}, shots=[],
                             stats={"shots": 283, "median_s": 1.43, "mean_s": 2.73, "cuts_per_min": 21.0,
                                    "profile": PROFILE}, status="pronto"))
        s.add(ReferenceVideo(id="ref_velha", title="Analisada antes do perfil", shots=[],
                             stats={"shots": 10, "median_s": 2.0}, status="pronto"))
        s.add(ReferenceVideo(id="ref_novo", title="Novo", status="novo"))


def test_new_project_from_reference_and_its_adherence(client):
    _seed_references()
    ref = client.get("/api/references/ref_gta").json()
    assert ref["display_title"] == "Voce vai zerar GTA 6" and ref["title"] == RAW_TITLE
    r = client.post("/api/references/ref_gta/project",
                    json={"topic": "  a história do  farol ", "title": "  Farol:  tudo o que sabemos ",
                          "style_name": "GTA (referência)", "use_llm": False})
    assert r.status_code == 200, r.text
    body = r.json()
    doc = service.get_doc(body["project"]["id"])
    assert doc.title == "Farol: tudo o que sabemos" and doc.style_id == body["style"]["id"]
    assert doc.topic == "a história do farol"  # o roteiro sai do tema do usuário, não do título da referência
    assert body["style"]["name"] == "GTA (referência)"

    adh = client.get("/api/references/ref_gta/adherence").json()
    assert [s["id"] for s in adh["styles"]] == [doc.style_id]
    assert [p["id"] for p in adh["projects"]] == [doc.id]  # sem cenas ainda: pode vir sem nota, mas aparece
    assert client.get("/api/references/ref_x/adherence").status_code == 404


def test_project_from_reference_needs_topic_profile_and_reuses_the_style(client):
    _seed_references()
    # tema é obrigatório (só a fórmula vem da referência)
    assert client.post("/api/references/ref_gta/project", json={"title": "x"}).status_code == 422
    assert client.post("/api/references/ref_gta/project", json={"topic": "   "}).status_code == 422
    # sem análise: 409; analisada antes do ADR 0004 (sem perfil): 409 com o pedido de medir
    assert client.post("/api/references/ref_novo/project", json={"topic": "x"}).status_code == 409
    old = client.post("/api/references/ref_velha/project", json={"topic": "x", "use_llm": False})
    assert old.status_code == 409 and "perfil" in old.json()["detail"]
    # título do downloader mandado de volta sem mudar: o projeto nasce com o título limpo, sem o id do vídeo
    first = client.post("/api/references/ref_gta/project",
                        json={"topic": "o farol", "title": RAW_TITLE, "use_llm": False})
    assert first.status_code == 200, first.text
    doc = service.get_doc(first.json()["project"]["id"])
    assert doc.title == "Voce vai zerar GTA 6"
    # sem título: vale o tema
    no_title = client.post("/api/references/ref_gta/project",
                           json={"topic": "o farol de novo", "style_id": doc.style_id}).json()
    assert service.get_doc(no_title["project"]["id"]).title == "o farol de novo"
    # `style_id` usa o estilo já feito dela: nada de estilo duplicado
    assert no_title["style"]["id"] == doc.style_id
    assert [s["id"] for s in client.get("/api/references/ref_gta/adherence").json()["styles"]] == [doc.style_id]
    bad = client.post("/api/references/ref_gta/project", json={"topic": "x", "style_id": STYLE})
    assert bad.status_code == 422  # estilo que não foi feito desta referência


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


# --- revisão: arquivo do meme que sumiu ------------------------------------------------------------------------------


def test_planned_meme_whose_file_was_deleted_leaves_the_timeline_without_src(isolated_settings, monkeypatch):
    """O meme planejado cujo arquivo foi apagado da biblioteca sai sem `src` (o renderer não desenha), com aviso: o
    Remotion cancelaria o render inteiro com o 404."""
    from test_edit_plan import LONG, LONG_TEXT, _doc, _fx

    from studio.core import edit_resolve
    from studio.core.project.document import BeatMark, GagHint
    from studio.core.styles.library import get_style
    from studio.media import meme_library
    from studio.workers.pipeline import edit_plan

    data = get_settings().data_dir
    file = data / "library" / "memes" / "susto.png"
    file.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ACERVO / "ilha.jpg", file)
    item = meme_library.MemeItem(id="susto", file="library/memes/susto.png", tags=["susto"], license="own")
    monkeypatch.setattr(meme_library, "user_items", lambda: [item])
    doc = _doc([LONG_TEXT] * 3)
    for scene in doc.scenes:
        scene.mark = BeatMark(role="joke", gag=GagHint(payoff="meme", tag="susto", at="desapareceu"))
    edit_plan.plan(doc, get_style(LONG), Pool(), recent=set())
    scene_id, _meme = _fx(doc, "meme")[0]
    scene = doc.scene(scene_id)
    words = [{"text": w, "from": i * 10, "to": i * 10 + 9} for i, w in enumerate(LONG_TEXT.split())]

    fx, _sfx, _drops, warnings = edit_resolve.resolve_fx(scene, words, 30, 3000, doc=doc)
    meme = next(e for e in fx if e["type"] == "meme")
    assert meme["params"]["src"] == "library/memes/susto.png" and not any("sumiu" in w for w in warnings)

    file.unlink()  # o usuário apagou o meme da biblioteca
    fx, _sfx, _drops, warnings = edit_resolve.resolve_fx(scene, words, 30, 3000, doc=doc)
    meme = next(e for e in fx if e["type"] == "meme")
    assert "src" not in meme["params"] and "video" not in meme["params"]
    assert any("sumiu" in w and "susto.png" in w for w in warnings)
    assert scene.fx[[e.type for e in scene.fx].index("meme")].params["src"]  # o documento não muda


# --- revisão: apagar não volta no "Reeditar" -------------------------------------------------------------------------


def test_deleted_event_is_kept_off_and_reedit_does_not_bring_it_back(isolated_settings):
    from test_edit_plan import LONG, LONG_TEXT, _doc

    from studio.core import edit_resolve
    from studio.core.styles.library import get_style
    from studio.workers.pipeline import edit_plan

    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 2)
    edit_plan.plan(doc, style, Pool(), recent=set())
    scene = next(s for s in doc.scenes if any(e.type == "punch" for e in s.fx))
    i = next(k for k, e in enumerate(scene.fx) if e.type == "punch")
    gone = scene.fx[i]
    # o que a tela manda ao apagar: o mesmo evento, desligado e do usuário
    scene.fx[i] = gone.model_copy(update={"params": {**gone.params, "off": True}, "origin": "user"})
    before = edit_plan.summary(doc)["fx"].get("punch", 0)
    edit_plan.plan(doc, style, Pool(), recent=set())
    again = doc.scene(scene.id).fx
    assert any(e.off and e.type == "punch" and e.at == gone.at for e in again)  # a marca ficou
    assert not any(e.type == "punch" and e.at == gone.at and not e.off for e in again)  # e o punch não voltou
    assert edit_plan.summary(doc)["fx"].get("punch", 0) <= before + 1
    words = [{"text": w, "from": n * 10, "to": n * 10 + 9} for n, w in enumerate(LONG_TEXT.split())]
    fx, _sfx, _d, _w = edit_resolve.resolve_fx(doc.scene(scene.id), words, 30, 3000, doc=doc)
    assert len(fx) == sum(1 for e in again if not e.off and e.type not in edit_resolve.AUDIO_ONLY_FX)


# --- revisão: avatar desligado no estilo não vira meme -----------------------------------------------------------------


def test_avatar_poses_respect_presenter_off_in_style(isolated_settings, monkeypatch):
    from test_edit_plan import LONG, LONG_TEXT, _doc, _fx

    from studio.core.project import presenter as presenter_core
    from studio.core.project.document import BeatMark, GagHint, Presenter, PresenterPose
    from studio.core.styles.library import get_style
    from studio.media import meme_library
    from studio.workers.pipeline import edit_plan

    avatar = Presenter(enabled=True, poses=[
        PresenterPose(id="choque", file="library/presenter/canal/choque.png", emotions=["surpresa"])])
    monkeypatch.setattr(presenter_core, "channel_presenter", lambda _cid: avatar)
    monkeypatch.setattr(meme_library, "user_items", lambda: [])

    def planned(overrides):
        doc = _doc([LONG_TEXT] * 3).model_copy(update={"channel_id": "ch_teste"})
        for scene in doc.scenes:
            scene.mark = BeatMark(role="joke", gag=GagHint(payoff="meme", tag="surpresa", at="desapareceu"))
        edit_plan.plan(doc, get_style(LONG, overrides), Pool(), recent=set())
        return [e.params.get("lib_id") for _s, e in _fx(doc, "meme")]

    assert "pose-choque" in planned({})  # com o avatar ligado, a pose é a reação
    assert planned({"edit": {"presenter": "off"}}) == []  # desligado no estilo: nenhuma pose como meme


# --- revisão: trocar candidato com trecho quebrado ------------------------------------------------------------------


def test_attach_pool_clip_that_ffmpeg_cannot_read_is_a_422(client, monkeypatch):
    from studio.media import clips

    doc = _project()
    rel = "library/clips/quebrado.mp4"
    bad = get_settings().data_dir / rel
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"isto nao e um video")
    save_pool(doc.id, Pool(items=[PoolItem(id="p4", tier="official", media="clip", desc="trailer", path=rel,
                                           source_id="yt:abc#0-2000", editorial=True)]))
    r = client.post(f"/api/projects/{doc.id}/scenes/scn_1/media", json={"via": "pool", "pool_id": "p4"})
    assert r.status_code == 422 and "p4" in r.json()["detail"]  # FFmpegError do pôster (antes, 500)

    poster = get_settings().data_dir / "library/clips/quebrado.jpg"
    shutil.copyfile(ACERVO / "ilha.jpg", poster)
    save_pool(doc.id, Pool(items=[PoolItem(id="p4", tier="official", media="clip", desc="trailer", path=rel,
                                           poster="library/clips/quebrado.jpg", source_id="yt:abc#0-2000")]))

    def gone(*_a, **_k):
        raise clips.ClipError("O trecho do acervo sumiu: colete o acervo de novo.")

    monkeypatch.setattr(clips, "proxy_for_project", gone)
    r = client.post(f"/api/projects/{doc.id}/scenes/scn_1/media", json={"via": "pool", "pool_id": "p4"})
    assert r.status_code == 422 and "sumiu" in r.json()["detail"]


def test_attach_pool_with_missing_style_is_a_422(client):
    doc = _project()
    save_pool(doc.id, Pool(items=[PoolItem(id="p1", tier="own", media="image", desc="x", path="a.jpg",
                                           source_id="acervo:a.jpg")]))
    broken = service.get_doc(doc.id).model_copy(update={"style_id": "estilo-que-sumiu"})
    service.commit(doc.id, broken, "estilo apagado")
    r = client.post(f"/api/projects/{doc.id}/scenes/scn_1/media", json={"via": "pool", "pool_id": "p1"})
    assert r.status_code == 422 and "estilo" in r.json()["detail"]


# --- revisão: id do trecho estável no conteúdo ------------------------------------------------------------------------


def test_clip_source_id_follows_the_cut_not_its_position():
    from types import SimpleNamespace

    from studio.workers.pipeline import media_pool

    def shot(a, dur):
        return SimpleNamespace(desc="", src="v.mp4", proxy=f"p{a}.mp4", poster=None, a=a, dur=dur, width=320, height=180,
                               material="")

    old = media_pool._clip_items([shot(0.0, 2.0), shot(2.0, 1.5)], tier="official", prefix="yt", key="k", topic="")
    new = media_pool._clip_items([shot(0.0, 1.2), shot(1.2, 0.8), shot(2.0, 1.5)], tier="official", prefix="yt",
                                 key="k", topic="")
    assert [i["source_id"] for i in old] == ["yt:k#0-2000", "yt:k#2000-1500"]
    previous = Pool(items=[PoolItem(id=f"p{n}", tier="official", media="clip", source_id=i["source_id"])
                           for n, i in enumerate(old, 1)])
    media_pool._assign_ids(new, previous)
    # o trecho que mudou ganha id novo; o igual mantém o seu (p7 nunca vira outro pedaço do vídeo)
    assert [i["id"] for i in new] == ["p3", "p4", "p2"]


# --- revisão: limites da tela "Cortes" = o que o planejador gera ------------------------------------------------------


def test_cuts_route_accepts_every_shot_the_planner_can_make(client):
    from studio.api import routes_edit
    from studio.workers.pipeline import edit_plan

    assert routes_edit.MAX_SHOTS == edit_plan.MAX_SCENE_SHOTS >= 41
    assert routes_edit.MAX_FX == edit_plan.MAX_SCENE_FX
    words = [f"palavra{n}" for n in range(60)]
    doc = service.create_project(title="Longa", style_id=STYLE, topic="cena longa")
    scene = Scene(id="scn_0", segment_id="seg_1", visual=Visual(prompt="longa"))
    doc = doc.model_copy(update={"segments": [Segment(id="seg_1", text={"pt-BR": " ".join(words) + "."})],
                                 "scenes": [scene]})
    service.commit(doc.id, doc, "cena longa")
    shots = [{"at": None, "source": "self"}] + [{"at": w, "source": "self"} for w in words[1:edit_plan.MAX_SCENE_SHOTS]]
    assert len(shots) == edit_plan.MAX_SCENE_SHOTS > 24
    r = client.patch(f"/api/projects/{doc.id}/scenes/scn_0/shots", json={"shots": shots})
    assert r.status_code == 200, r.text


# --- revisão: créditos de subplano do acervo e de meme --------------------------------------------------------------


def test_credits_cover_pool_subshots_and_cc_by_memes(isolated_settings, monkeypatch):
    from studio.media import meme_library
    from studio.workers.publish import credits

    doc = _project()
    save_pool(doc.id, Pool(items=[
        PoolItem(id="p7", tier="official", media="clip", desc="Trailer oficial", path="library/clips/t.mp4",
                 url="https://www.youtube.com/watch?v=trailer00001", credit="Rockstar Games", editorial=True,
                 source_id="yt:k#0-2000"),
        PoolItem(id="p8", tier="own", media="image", desc="Foto minha", path="a.jpg", source_id="acervo:a.jpg"),
    ]))
    doc = service.get_doc(doc.id)
    assert credits.credit_lines(doc, "pt-BR") == [] and credits.has_editorial(doc) is False
    doc.scenes[0].shots = [SubShot(), SubShot(at="depois", source="pool", pool_id="p7"),
                           SubShot(at="anos", source="pool", pool_id="p8")]
    lines = credits.credit_lines(doc, "pt-BR")
    assert len(lines) == 1 and "Rockstar Games" in lines[0] and "trailer00001" in lines[0]
    assert credits.has_editorial(doc) is True  # trecho oficial: aviso de Content ID

    meme = meme_library.MemeItem(id="gato", file="library/memes/gato.png", tags=["susto"], license="cc_by",
                                 author="Fulana de Tal", source_url="https://commons.example/gato")
    own = meme_library.MemeItem(id="meu", file="library/memes/meu.png", tags=["x"], license="own")
    monkeypatch.setattr(meme_library, "user_items", lambda: [meme, own])
    doc.scenes[1].fx = [FxEvent(type="meme", at="Marinha", params={"lib_id": "gato"}),
                        FxEvent(type="meme", at="data", params={"lib_id": "meu"}),
                        FxEvent(type="meme", at="confirmou", params={"lib_id": "gato", "off": True})]
    lines = credits.credit_lines(doc, "pt-BR")
    assert len(lines) == 2 and "Fulana de Tal" in lines[1] and "CC BY" in lines[1]
    assert "commons.example/gato" in lines[1]
    # a mídia da cena que veio do mesmo item do acervo não se repete
    doc.scenes[1].visual.media = MediaRef(kind="clip", source="youtube", asset="uploads/x/p.jpg", pool_id="p7",
                                          credit="Rockstar Games", editorial=True)
    assert len(credits.credit_lines(doc, "pt-BR")) == 2
