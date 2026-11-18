"""Fase 8 (integração do pacote, do compositor e do avatar): capa do build pelo candidato escolhido, A/B com os
aprovados, variantes sem imagem repetida, material oficial pelo canal, avatar distribuído no build e no storyboard,
Short sem o texto da capa do longo e as rotas novas."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import (
    CHANNEL_THUMBS_KEY,
    BgLayer,
    MediaRef,
    PackageCandidate,
    PackageDoc,
    Presenter,
    PresenterPose,
    ProjectDoc,
    Scene,
    Segment,
    TextLayer,
    ThumbLayers,
    Visual,
)
from studio.core.timeline import NarrationClip
from studio.db import session_scope
from studio.models import Channel
from studio.providers.base import Word
from studio.workers import tasks
from studio.workers.pipeline import authoring
from studio.workers.pipeline.build import _bare_builder
from studio.workers.visual import thumbs

LONG = "curiosidades-longo"


def _cand(cid: str, title: str, words: str, *, approved: bool = False, fill: str = "yellow",
          bg: BgLayer | None = None) -> PackageCandidate:
    return PackageCandidate(id=cid, title=title, thumb_text=words, promise="p", concept="c", approved=approved,
                            layers=ThumbLayers(template="heroi-palavra", background=bg or BgLayer(kind="gradient"),
                                               text=TextLayer(words=words, fill=fill), logo="none"))


def _package(chosen: str = "pkc_b", locked: bool = True) -> PackageDoc:
    return PackageDoc(candidates=[_cand("pkc_a", "Título A", "ANTES"), _cand("pkc_b", "Título B", "AGORA"),
                                  _cand("pkc_c", "Título C", "SEGREDO", approved=True, fill="white")],
                      chosen_id=chosen, locked=locked)


def _channel(cid: str = "chn_vice", bible: dict[str, Any] | None = None) -> str:
    with session_scope() as s:
        s.add(Channel(id=cid, name="Lucas em Vice", settings={}, style_bible=bible or {}))
    return cid


def _project_with_package(channel_id: str | None = None) -> str:
    doc = service.create_project(title="GTA 6", style_id=LONG, topic="GTA 6", channel_id=channel_id)
    doc = service.get_doc(doc.id)
    doc.package = _package()
    service.commit(doc.id, doc, "pacote")
    return doc.id


def _img(rel: str, color: tuple[int, int, int]) -> str:
    path = get_settings().data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), color).save(path)
    return rel


# --- A/B e variantes ----------------------------------------------------------------------------------------------
def test_specs_com_pacote_sao_o_escolhido_e_os_aprovados():
    doc = ProjectDoc(id="prj_x", title="t", style_id=LONG, package=_package())
    specs = thumbs.default_specs(doc, "texto-centro", "pt-BR", {}, 3)
    assert [s["candidate_id"] for s in specs] == ["pkc_b", "pkc_c"]
    assert specs[0]["text"] == "AGORA" and specs[0]["label"] == "Escolhida no pacote"
    # tradução: o mesmo desenho com o texto traduzido (só no escolhido)
    doc.languages = ["pt-BR", "en-US"]
    doc.thumbnail.text = {"pt-BR": "AGORA", "en-US": "NOW"}
    en = thumbs.candidate_layers(doc, doc.chosen_package(), "en-US")
    assert en.text.words == "NOW" and doc.chosen_package().layers.text.words == "AGORA"


def test_variantes_legadas_nao_repetem_imagem_nem_reuse_of():
    a = _img("img/a.png", (200, 10, 10))
    same = _img("img/a-copia.png", (200, 10, 10))  # outro arquivo, mesma imagem
    c = _img("img/c.png", (10, 200, 10))
    seg = Segment(id="seg_0", text={"pt-BR": "x"})
    scenes = [Scene(id="s0", segment_id=seg.id, part=0, visual=Visual(prompt="a")),
              Scene(id="s1", segment_id=seg.id, part=1, kind="stat", visual=Visual(reuse_of="s0")),
              Scene(id="s2", segment_id=seg.id, part=2, kind="stat", visual=Visual(prompt="b")),
              Scene(id="s3", segment_id=seg.id, part=3, visual=Visual(prompt="c"))]
    doc = ProjectDoc(id="prj_y", title="t", style_id=LONG, segments=[seg], scenes=scenes)
    images = {"s0": a, "s1": a, "s2": same, "s3": c}
    specs = thumbs.default_specs(doc, "texto-centro", "pt-BR", images, 3)
    assert [s["scene_id"] for s in specs] == ["s0", "s3", "s0"]
    # só a mesma imagem: a 2ª sai (não há outra cena para mostrar)
    only = thumbs.default_specs(doc, "texto-centro", "pt-BR", {"s0": a, "s1": a}, 3)
    assert [s["scene_id"] for s in only] == ["s0", "s0"]


def test_variantes_legadas_respeitam_material_oficial():
    a, b = _img("img/of.png", (1, 2, 3)), _img("img/livre.png", (9, 9, 9))
    seg = Segment(id="seg_0", text={"pt-BR": "x"})
    scenes = [Scene(id="s0", segment_id=seg.id, part=0,
                    visual=Visual(media=MediaRef(kind="official", asset=a, editorial=True))),
              Scene(id="s1", segment_id=seg.id, part=1, visual=Visual(prompt="b"))]
    doc = ProjectDoc(id="prj_z", title="t", style_id=LONG, segments=[seg], scenes=scenes)
    images = {"s0": a, "s1": b}
    assert thumbs.default_specs(doc, "texto-centro", "pt-BR", images, 1)[0]["scene_id"] == "s1"
    assert thumbs.default_specs(doc, "texto-centro", "pt-BR", images, 1, allow=True)[0]["scene_id"] == "s0"


def test_allow_editorial_canal_vence_estilo():
    doc = ProjectDoc(id="prj_w", title="t", style_id=LONG, channel_id="chn_vice")
    style = authoring.get_style(LONG)
    assert thumbs.allow_editorial(doc, style, {}) is False
    assert thumbs.allow_editorial(doc, style, {CHANNEL_THUMBS_KEY: {"allow_editorial": True}}) is True


def test_make_variants_com_pacote_antes_do_render():
    pid = _project_with_package()
    state = thumbs.get_state(pid, "pt-BR")
    assert state["package"]["chosen_id"] == "pkc_b" and [s["candidate_id"] for s in state["spec"]] == ["pkc_b", "pkc_c"]
    done = thumbs.make_variants(pid, "pt-BR")
    assert [v["candidate_id"] for v in done["variants"]] == ["pkc_b", "pkc_c"]
    first = get_settings().data_dir / done["variants"][0]["path"]
    with Image.open(first) as im:
        assert im.size == (3840, 2160)
    assert not done["stale"]
    with pytest.raises(thumbs.ThumbsError):
        thumbs.set_spec(pid, "pt-BR", [{"scene_id": None, "text": "X", "template": "texto-centro"}])
    # mudar as camadas de um aprovado deixa as miniaturas desatualizadas
    doc = service.get_doc(pid)
    doc.package.candidate("pkc_c").layers.pill = "SEM SPOILER"
    service.commit(pid, doc, "pílula")
    assert thumbs.get_state(pid, "pt-BR")["stale"] is True


# --- capa do build ------------------------------------------------------------------------------------------------
def test_capa_do_build_e_o_candidato_escolhido():
    pid = _project_with_package()
    doc = service.get_doc(pid)
    builder = _bare_builder(pid, "final", doc)
    rel = builder.thumbnail("pt-BR", {})
    with Image.open(get_settings().data_dir / rel) as im:
        assert im.size == (1280, 720)
    assert builder.thumbnail("pt-BR", {}) == rel  # cache
    doc.package.candidate("pkc_b").layers.text.words = "OUTRA"
    other = _bare_builder(pid, "final", doc).thumbnail("pt-BR", {})
    assert other != rel  # camadas novas: capa nova (a chave do cache tem as camadas)
    doc.package = PackageDoc()
    legacy = _bare_builder(pid, "final", doc).thumbnail("pt-BR", {})
    assert legacy not in (rel, other)


# --- avatar no build e no storyboard ------------------------------------------------------------------------------
TEXT = " ".join(f"palavra{k}" for k in range(40))


def _presenter() -> Presenter:
    return Presenter(enabled=True, poses=[
        PresenterPose(id="base", file="library/presenter/c/base.png"),
        PresenterPose(id="surpreso", file="library/presenter/c/surpreso.png", emotions=["choque"]),
        PresenterPose(id="feliz", file="library/presenter/c/feliz.png", emotions=["cta"]),
        PresenterPose(id="oculos", file="library/presenter/c/oculos.png"),
    ], default_pose="base")


def _long_doc(n: int = 16) -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", text={"pt-BR": TEXT}) for i in range(n)]
    scenes = [Scene(id=f"scn_{i}", segment_id=s.id, visual=Visual(prompt="x")) for i, s in enumerate(segments)]
    return ProjectDoc(id="prj_av", title="t", style_id=LONG, segments=segments, scenes=scenes, seed=1)


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    toks = TEXT.split()
    return {seg.id: NarrationClip(seg.id, "x.wav", len(toks) * 0.5 + 0.2,
                                  [Word(t, k * 0.5, k * 0.5 + 0.4) for k, t in enumerate(toks)])
            for seg in doc.segments}


def test_build_distribui_o_avatar_na_timeline():
    doc = _long_doc()
    builder = _bare_builder("prj_av", "preview", doc)
    builder.__dict__["video_presenter"] = None
    plain = builder.timeline("pt-BR", _clips(doc), {}, None, None)
    assert all("presenter" not in s for s in plain["scenes"])

    builder = _bare_builder("prj_av", "preview", doc)
    builder.__dict__["video_presenter"] = _presenter()
    tl = builder.timeline("pt-BR", _clips(doc), {}, None, None)
    with_avatar = [i for i, s in enumerate(tl["scenes"]) if "presenter" in s]
    assert with_avatar and 0 not in with_avatar  # nunca na 1ª cena
    assert all(b - a >= 2 for a, b in zip(with_avatar, with_avatar[1:], strict=False))  # nunca em cenas seguidas
    assert all(s.presenter is None for s in builder.doc.scenes)  # o documento salvo não muda
    # cenas sem avatar ficam iguais às da timeline sem avatar (mesmo hash de bloco)
    same = [i for i in range(len(tl["scenes"])) if i not in with_avatar]
    assert all(tl["scenes"][i] == plain["scenes"][i] for i in same)


def test_storyboard_com_avatar_pede_e_converte_as_dicas(monkeypatch):
    bible = {"presenter": _presenter().model_dump(mode="json")}
    cid = _channel(bible=bible)
    doc = service.create_project(title="Avatar", style_id=LONG, topic="o farol", channel_id=cid)
    tasks_doc = service.get_doc(doc.id)
    tasks_doc.segments = _long_doc(8).segments
    style = authoring.get_style(LONG)
    seen: dict[str, Any] = {}

    def fake(task, system, prompt, schema, context, project_id, job_id):
        seen.update(prompt=prompt, schema=schema)
        props = schema["properties"]["scenes"]["items"]["properties"]
        scenes = []
        for k, beat in enumerate(context["scenes"]):
            item = {key: None for key in props}
            item.update(key=beat["key"], kind="image", source="new", prompt="algo")
            if k == 3:
                item.update(presenter_pose="choque", presenter_at="palavra5", presenter_mode="reacao")
            scenes.append(item)
        direction = {"genre": "", "approach": "literal", "world": "w", "treatment": "t", "palette": [],
                     "languages": [], "grade": None, "entities": [], "avoid": [], "style_fit": "ok",
                     "style_note": "", "suggested_style": None, "hook": ""}
        return type("R", (), {"data": {"direction": direction, "scenes": scenes}})(), "fake"

    monkeypatch.setattr(authoring, "_llm", fake)
    channel = {"style_bible": bible}
    new_doc, _, _ = authoring.storyboard(tasks_doc, style, channel=channel)
    item = seen["schema"]["properties"]["scenes"]["items"]
    assert {"presenter_pose", "presenter_at", "presenter_mode"} <= set(item["required"])
    assert "presenter_pose" in seen["prompt"] and "- surpreso: choque" in seen["prompt"]
    hinted = [s for s in new_doc.scenes if s.presenter is not None]
    assert hinted and all(s.presenter.source == "auto" for s in hinted)
    assert any(s.presenter.pose == "surpreso" for s in hinted)
    # sem avatar no canal, o esquema é o de sempre
    assert authoring.storyboard_schema(False) is authoring.STORYBOARD_SCHEMA


# --- compositor no job do pacote ----------------------------------------------------------------------------------
def test_job_do_pacote_usa_o_compositor_e_o_material_do_canal():
    cid = _channel(bible={CHANNEL_THUMBS_KEY: {"allow_editorial": False}})
    kit = get_settings().library_dir / "thumbkit" / cid
    kit.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (320, 180), (30, 160, 220)).save(kit / "oficial.jpg")
    (kit / "thumbkit.json").write_text(json.dumps({"items": [
        {"id": "oficial", "kind": "frame", "file": "oficial.jpg", "tags": [], "editorial": True}]}), encoding="utf-8")
    pid = _project_with_package(cid)
    doc = service.get_doc(pid)
    doc.package.candidate("pkc_a").layers.background = BgLayer(kind="frame", ref="oficial")
    service.commit(pid, doc, "fundo oficial")
    tasks.render_package_thumbs(pid, ["pkc_a"])
    cand = service.get_doc(pid).package.candidate("pkc_a")
    warnings = " ".join(cand.score["warnings"])
    assert tasks.STUB_NOTICE not in warnings and "material oficial bloqueado" in warnings
    # canal libera: o mesmo fundo entra
    with session_scope() as s:
        row = s.get(Channel, cid)
        row.style_bible = {CHANNEL_THUMBS_KEY: {"allow_editorial": True}}
    tasks.render_package_thumbs(pid, ["pkc_a"])
    cand = service.get_doc(pid).package.candidate("pkc_a")
    assert "material oficial bloqueado" not in " ".join(cand.score["warnings"])


# --- rotas --------------------------------------------------------------------------------------------------------
@pytest.fixture
def client(monkeypatch):
    from studio.api import routes_package
    from studio.api.app import create_app

    monkeypatch.setattr(routes_package, "get_runner",
                        lambda: type("R", (), {"submit": staticmethod(lambda *a, **k: {"id": "job_x"})})())
    with TestClient(create_app()) as c:
        yield c


def test_rota_do_material_oficial_e_form_do_canal_nao_apaga(client):
    cid = _channel()
    r = client.put(f"/api/channels/{cid}/thumbs", json={"allow_editorial": True})
    assert r.status_code == 200 and r.json()["thumbs"]["allow_editorial"] is True
    assert client.get(f"/api/channels/{cid}/thumbs").json()["thumbs"]["ai_official_characters"] is False
    client.put(f"/api/channels/{cid}", json={"name": "Lucas em Vice", "style_bible": {"identidade": "neon"}})
    with session_scope() as s:
        bible = dict(s.get(Channel, cid).style_bible or {})
    assert bible[CHANNEL_THUMBS_KEY]["allow_editorial"] is True and bible["identidade"] == "neon"
    assert client.put("/api/channels/nada/thumbs", json={"allow_editorial": True}).status_code == 404


def test_escolhido_travado_aceita_trocar_so_o_fundo(client):
    pid = _project_with_package()
    layers = service.get_doc(pid).package.candidate("pkc_b").layers.model_dump(mode="json")
    layers["background"] = {"kind": "media", "ref": "media:scn_qualquer"}
    r = client.put(f"/api/projects/{pid}/package/candidates/pkc_b", json={"layers": layers})
    assert r.status_code == 200, r.text
    layers["text"]["words"] = "TROCADO"
    assert client.put(f"/api/projects/{pid}/package/candidates/pkc_b", json={"layers": layers}).status_code == 409


def test_upload_da_biblioteca_grava_source_como_objeto(client):
    cid = _channel()
    buf = io.BytesIO()
    Image.new("RGB", (64, 36), (1, 2, 3)).save(buf, "JPEG")
    r = client.post(f"/api/channels/{cid}/thumbkit", data={"kind": "frame", "tags": "", "credit": "", "editorial": "false"},
                    files={"file": ("quadro.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 200, r.text
    assert isinstance(r.json()["source"], dict)


# --- Short derivado -----------------------------------------------------------------------------------------------
def test_short_derivado_nao_herda_o_texto_da_capa(isolated_settings):
    from studio.workers.jobs import get_job, get_runner
    from studio.workers.shorts import derive

    def run(kind: str, project_id: str) -> None:
        job = get_runner().submit(kind, {}, project_id, run_inline=True)
        assert get_job(job["id"])["status"] == "done"

    doc = service.create_project(title="O farol", style_id=LONG, topic="o farol abandonado da ilha")
    run("script", doc.id)
    run("storyboard", doc.id)
    parent = service.get_doc(doc.id)
    parent.thumbnail.text = {"pt-BR": "TEXTO DO LONGO"}
    parent.package = _package()
    service.commit(doc.id, parent, "capa do longo")
    run("preview", doc.id)
    seg = parent.segments[1].id
    result = derive.derive_short(doc.id, [[seg, seg]], "Corte", hook="GANCHO DO CORTE")
    child = service.get_doc(result["project_id"])
    assert child.thumbnail.text == {"pt-BR": "GANCHO DO CORTE"}
    assert child.package.candidates == [] and child.package.chosen_id is None
    plain = derive.derive_short(doc.id, [[seg, seg]], "Corte sem gancho")
    assert service.get_doc(plain["project_id"]).thumbnail.text == {}
