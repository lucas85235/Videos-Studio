"""Avatar do apresentador (Fase 4, backend): importação das poses, efetivo canal/projeto/estilo, escolha da pose,
distribuição automática pelas cenas (`assign`), rotas e o PUT do canal que não apaga o avatar.

Tudo em banco e pasta de dados temporários (conftest). A pasta real do canais só é lida, no teste das 17 poses.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import presenter as core
from studio.core.project import service
from studio.core.project.document import (
    PRESENTER_KEY,
    Presenter,
    PresenterOverride,
    PresenterPose,
    PresenterVideo,
    ProjectDoc,
    Scene,
    ScenePresenter,
    Segment,
    Visual,
)
from studio.core.styles.library import get_style
from studio.providers.base import Word
from studio.workers.canais_import import presenter as importer
from studio.workers.pipeline import presenter as scenes_mod
from studio.workers.pipeline.storyboard_check import AVISO, CORRIGIDO

REAL_POSES = Path.home() / "canais" / "plantao-vice" / "video" / "public" / "apresentador"
POSE_IDS = list(importer.POSE_TABLE)


def _png(size=(200, 300), *, alpha=True, opaque_corner=False, touch_side=False, fmt="PNG") -> bytes:
    mode = "RGBA" if alpha else "RGB"
    im = Image.new(mode, size, (0, 0, 0, 0) if alpha else (255, 255, 255))
    w, h = size
    left = 0 if touch_side else w // 4
    for x in range(left, 3 * w // 4):
        for y in range(h // 5, h):
            im.putpixel((x, y), (200, 120, 90, 255) if alpha else (200, 120, 90))
    if opaque_corner and alpha:
        im.putpixel((0, 0), (10, 10, 10, 255))
    buf = io.BytesIO()
    im.save(buf, fmt)
    return buf.getvalue()


def _pose_dir(tmp_path: Path, ids=POSE_IDS) -> Path:
    folder = tmp_path / "poses"
    folder.mkdir()
    for pid in ids:
        (folder / f"{pid}.png").write_bytes(_png())
    return folder


def _channel(client: TestClient) -> str:
    return client.post("/api/channels", json={"name": "Lucas em Vice"}).json()["id"]


def _presenter(**kw) -> Presenter:
    poses = [PresenterPose(id=pid, file=f"library/presenter/c/{pid}.png", emotions=importer.POSE_TABLE[pid]
                           .get("emotions", []), aliases=importer.POSE_TABLE[pid].get("aliases", []),
                           thumb_ok=importer.POSE_TABLE[pid].get("thumb_ok", False),
                           facing=importer.POSE_TABLE[pid].get("facing", "center")) for pid in POSE_IDS]
    return Presenter(**{"enabled": True, "poses": poses, **kw})


# --- importação ----------------------------------------------------------------------------------------------


def test_check_pose_rejects_jpg_no_alpha_and_opaque_background():
    with pytest.raises(importer.PoseError, match="PNG"):
        importer.check_pose(_png(alpha=False, fmt="JPEG"), "x.jpg")
    with pytest.raises(importer.PoseError, match="alfa"):
        importer.check_pose(_png(alpha=False), "x.png")
    with pytest.raises(importer.PoseError, match="transparente"):
        importer.check_pose(_png(opaque_corner=True), "x.png")
    _img, ok = importer.check_pose(_png(), "ok.png")
    assert ok.warnings == []
    _img, side = importer.check_pose(_png(touch_side=True), "lado.png")
    assert side.warnings and "lateral" in side.warnings[0]  # aviso, não erro


def test_import_folder_copies_trims_and_fills_metadata(tmp_path):
    client = TestClient(create_app())
    cid = _channel(client)
    folder = _pose_dir(tmp_path)
    (folder / "foto.jpg").write_bytes(_png(alpha=False, fmt="JPEG"))
    report = importer.import_folder(cid, folder)
    assert sorted(report.imported) == sorted(POSE_IDS)
    assert "foto.jpg" in report.rejected
    pres = core.channel_presenter(cid)
    assert pres.enabled is False and pres.character_id == "lucas" and pres.default_pose == "base"
    data = get_settings().data_dir
    surpreso = pres.pose("surpreso")
    assert surpreso.thumb_ok and "choque" in surpreso.emotions and "espantado" in surpreso.aliases
    assert (data / surpreso.file).read_bytes() == (folder / "surpreso.png").read_bytes()  # original intacto
    with Image.open(data / surpreso.render_file) as im:
        assert im.height == importer.RENDER_HEIGHT and im.mode == "RGBA"
        assert im.getchannel("A").getbbox() == (0, 0, im.width, im.height)  # alfa aparado
    assert pres.pose("apontando").facing == "right"
    assert {p.id for p in pres.thumb_poses()} == {"surpreso", "mao-na-testa", "apontando", "lupa", "comemorando",
                                                 "segredo", "dinheiro"}
    assert pres.pose("controle").aliases == ["jogando"]


def test_reimport_keeps_user_edits_and_enable_flag(tmp_path):
    client = TestClient(create_app())
    cid = _channel(client)
    folder = _pose_dir(tmp_path, ["base", "surpreso"])
    importer.import_folder(cid, folder, enable=True)
    pres = core.channel_presenter(cid)
    pres.pose("surpreso").emotions = ["meu choque"]
    core.save_channel_presenter(cid, pres)
    report = importer.import_folder(cid, folder)
    assert report.updated == ["base", "surpreso"] and report.enabled is True
    assert core.channel_presenter(cid).pose("surpreso").emotions == ["meu choque"]


@pytest.mark.skipif(not REAL_POSES.is_dir(), reason="pasta do canais ausente")
def test_the_17_real_poses_import():
    client = TestClient(create_app())
    cid = _channel(client)
    report = importer.import_folder(cid, REAL_POSES)
    assert len(report.imported) == 17 and not report.rejected
    pres = core.channel_presenter(cid)
    assert {p.id for p in pres.poses} == set(POSE_IDS)


# --- efetivo e pose ------------------------------------------------------------------------------------------


def test_effective_channel_project_style_and_formats():
    long_style = get_style("curiosidades-longo")
    short_style = get_style("misterios-short")
    base = _presenter()
    off_channel = base.model_copy(update={"enabled": False})
    assert core.effective(off_channel, PresenterOverride(), long_style) is None
    assert core.effective(off_channel, PresenterOverride(enabled=True), long_style) is not None
    assert core.effective(base, PresenterOverride(enabled=False), long_style) is None
    assert core.effective(base, PresenterOverride(), long_style) is not None
    # estilo "nunca" desliga as cenas, não a thumb
    never = get_style("curiosidades-longo", {"edit": {"presenter": "off"}})
    assert core.effective(base, PresenterOverride(), never) is None
    assert core.effective(base, PresenterOverride(), never, purpose="thumb") is not None
    # Shorts: desligado por padrão (vídeo e thumb), a não ser com use_in_shorts
    assert core.effective(base, PresenterOverride(), short_style) is None
    assert core.effective(base, PresenterOverride(), short_style, purpose="thumb") is None
    assert core.effective(base.model_copy(update={"use_in_shorts": True}), PresenterOverride(), short_style) is not None
    # projeto liga a thumb e desliga o vídeo; poses fora
    eff = core.effective(base, PresenterOverride(video=False, poses_off=["surpreso"]), long_style, purpose="thumb")
    assert eff is not None and eff.pose("surpreso") is None
    assert core.effective(base, PresenterOverride(video=False), long_style) is None
    # bible inteiro do canal também serve
    assert core.effective({PRESENTER_KEY: base.model_dump()}, None, long_style) is not None
    assert core.effective({}, None, long_style) is None


def test_resolve_pose_alias_emotion_and_fallback():
    pres = _presenter()
    assert core.resolve_pose(pres, "Surpreso").id == "surpreso"
    assert core.resolve_pose(pres, "explicando").id == "apontando"
    assert core.resolve_pose(pres, "Mão na testa").id == "mao-na-testa"
    assert core.resolve_pose(pres, "custo").id == "dinheiro"
    assert core.resolve_pose(pres, "spoiler").id == "segredo"
    assert core.resolve_pose(pres, "detalhe").id == "lupa"  # parte de "detalhe escondido"
    assert core.resolve_pose(pres, "inexistente").id == "base"
    assert core.resolve_pose(pres, None).id == "base"
    assert core.resolve_pose(Presenter(), "x") is None
    assert len(core.thumb_poses(pres)) == 7 and core.thumb_poses(None) == []


def test_trigger_index_and_parse_at():
    texts = ["Isso", "é", "chocante,", "muito", "chocante!"]
    assert core.trigger_index(texts, "chocante") == 2
    assert core.trigger_index(texts, "chocante#2") == 4
    assert core.trigger_index(texts, "Cho") is None  # prefixo curto não vale
    assert core.trigger_index(texts, "nada") is None
    assert core.parse_at("@1200") == (None, 1, 1200)
    assert core.parse_at("palavra#3") == ("palavra", 3, None)


# --- distribuição automática ------------------------------------------------------------------------------------


def _long_doc(n: int = 30, texts: dict[int, str] | None = None, kinds: dict[int, str] | None = None,
              roles: dict[int, str] | None = None) -> tuple[ProjectDoc, dict[str, list[Word]]]:
    """Uma cena por segmento, 20 s cada (n cenas = n×20 s)."""
    texts, kinds, roles = texts or {}, kinds or {}, roles or {}
    segments, scenes, words = [], [], {}
    for i in range(n):
        text = texts.get(i, " ".join(f"fala{i}x{j}" for j in range(10)))
        seg = Segment(id=f"seg_{i:02d}", role=roles.get(i, "body"), text={"pt-BR": text})
        scene = Scene(id=f"scn_{i:02d}", segment_id=seg.id, kind=kinds.get(i, "image"), visual=Visual(prompt="x"))
        segments.append(seg)
        scenes.append(scene)
        toks = text.split()
        words[scene.id] = [Word(t, i * 20 + k * 2, i * 20 + k * 2 + 1.5) for k, t in enumerate(toks)]
    return ProjectDoc(id="prj_av", title="t", style_id="curiosidades-longo", segments=segments, scenes=scenes,
                      seed=1), words


def _with(doc: ProjectDoc) -> list[tuple[int, str, str]]:
    return [(i, s.presenter.pose, s.presenter.source) for i, s in enumerate(scenes_mod.ordered_scenes(doc))
            if s.presenter is not None and not s.presenter.off]


def test_assign_spacing_adjacency_first_scene_and_repeats():
    doc, words = _long_doc(33)  # 11 min
    scenes_mod.assign(doc, _presenter(), words)
    got = _with(doc)
    idx = [i for i, _p, _s in got]
    assert 0 not in idx  # nunca na 1ª cena
    assert all(b - a >= 2 for a, b in zip(idx, idx[1:], strict=False))  # nunca adjacentes
    starts = [i * 20 for i in idx]
    assert all(b - a >= 60 for a, b in zip(starts, starts[1:], strict=False))  # espaçamento mínimo
    assert all(b - a <= 90 for a, b in zip([0, *starts], starts, strict=False))  # sem buraco maior que 90 s
    poses = [p for _i, p, _s in got]
    assert all(a != b for a, b in zip(poses, poses[1:], strict=False))  # nunca a mesma pose seguida
    assert 6 <= len(got) <= 11
    assert got[0][1] == "oculos"  # abertura


def test_assign_blocks_serious_and_hidden_kinds_and_keeps_manual():
    texts = {3: "A vítima foi encontrada pela polícia ontem à noite", 6: "deixa o like e se inscreve no canal"}
    doc, words = _long_doc(12, texts=texts, kinds={4: "document", 5: "quote"})
    doc.scenes[3].presenter = ScenePresenter(pose="surpreso")  # sugestão em cena séria
    doc.scenes[4].presenter = ScenePresenter(pose="lupa")  # sugestão em documento
    doc.scenes[9].presenter = ScenePresenter(pose="rindo", source="manual")
    doc.scenes[10].presenter = ScenePresenter(off=True, source="manual")
    issues = scenes_mod.assign(doc, _presenter(), words)
    assert doc.scenes[3].presenter is None and doc.scenes[4].presenter is None and doc.scenes[5].presenter is None
    assert {i.scene_id for i in issues if i.code == "avatar_tirado"} >= {"scn_03", "scn_04"}
    assert all(i.severity in (AVISO, CORRIGIDO) for i in issues)
    assert doc.scenes[6].presenter.pose == "feliz" and doc.scenes[6].presenter.source == "auto"  # CTA
    assert doc.scenes[9].presenter.pose == "rindo" and doc.scenes[9].presenter.source == "manual"
    assert doc.scenes[10].presenter.off is True
    assert doc.scenes[8].presenter is None  # vizinha da manual


def test_assign_fixes_unknown_pose_missing_word_and_repeated_hint():
    doc, words = _long_doc(12, texts={4: "isso aqui é chocante demais pessoal"})
    doc.scenes[1].presenter = ScenePresenter(pose="espantado")
    doc.scenes[4].presenter = ScenePresenter(pose="nervoso-total", at="inexistente")
    doc.scenes[8].presenter = ScenePresenter(pose="explicando", at="chocante")
    issues = scenes_mod.assign(doc, _presenter(), words)
    by_code = {(i.code, i.scene_id) for i in issues}
    assert doc.scenes[4].presenter.pose == "base"  # sem emoção parecida: a padrão
    assert ("avatar_pose", "scn_04") in by_code and ("avatar_gatilho", "scn_04") in by_code
    assert doc.scenes[4].presenter.at is None
    assert doc.scenes[1].presenter.pose == "surpreso"
    poses = [p for _i, p, _s in _with(doc)]
    assert all(a != b for a, b in zip(poses, poses[1:], strict=False))


def test_assign_disabled_or_vertical_removes_only_auto():
    doc, words = _long_doc(8)
    doc.scenes[2].presenter = ScenePresenter(pose="lupa")
    doc.scenes[5].presenter = ScenePresenter(pose="rindo", source="manual")
    scenes_mod.assign(doc, None, words)
    assert doc.scenes[2].presenter is None and doc.scenes[5].presenter.pose == "rindo"
    doc.scenes[2].presenter = ScenePresenter(pose="lupa")
    scenes_mod.assign(doc, _presenter(), words, vertical=True)
    assert doc.scenes[2].presenter is None


def test_assign_uses_speech_cue_and_is_deterministic():
    doc, words = _long_doc(10, texts={4: "o preço do jogo vai custar caro demais"})
    a = doc.model_copy(deep=True)
    scenes_mod.assign(a, _presenter(), words)
    b = doc.model_copy(deep=True)
    scenes_mod.assign(b, _presenter(), words)
    assert [s.presenter for s in a.scenes] == [s.presenter for s in b.scenes]
    cue = a.scenes[4].presenter
    assert cue is not None and cue.pose == "dinheiro" and cue.at == "preco"


def test_prompt_lines():
    assert scenes_mod.prompt_lines(None) == []
    assert scenes_mod.prompt_lines(_presenter(enabled=False)) == []
    lines = scenes_mod.prompt_lines(_presenter())
    assert "presenter_hint" in lines[1] and any(line.startswith("- surpreso: choque") for line in lines)


def test_presenter_video_defaults():
    v = PresenterVideo()
    assert v.every_s == (60, 90) and "document" in v.hide_on_kinds and v.hide_on_vertical


# --- rotas e PUT do canal ---------------------------------------------------------------------------------------


def test_channel_put_keeps_presenter(tmp_path):
    client = TestClient(create_app())
    cid = _channel(client)
    importer.import_folder(cid, _pose_dir(tmp_path, ["base", "feliz"]), enable=True)
    client.put(f"/api/channels/{cid}", json={"name": "Lucas em Vice", "style_bible": {"identidade": "neon"}})
    client.put(f"/api/channels/{cid}", json={"name": "Lucas em Vice",
                                             "style_bible": {"identidade": "neon", PRESENTER_KEY: {"enabled": False}}})
    pres = core.channel_presenter(cid)
    assert pres.enabled is True and {p.id for p in pres.poses} == {"base", "feliz"}
    bible = next(c for c in client.get("/api/channels").json() if c["id"] == cid)["style_bible"]
    assert bible["identidade"] == "neon"


def test_presenter_routes(tmp_path):
    client = TestClient(create_app())
    cid = _channel(client)
    assert client.get(f"/api/channels/{cid}/presenter").json()["presenter"]["enabled"] is False
    assert client.get("/api/channels/chn_nada/presenter").status_code == 404
    # upload: PNG com alfa entra; JPG é recusado
    up = client.post(f"/api/channels/{cid}/presenter/poses", files={"file": ("surpreso.png", _png(), "image/png")})
    assert up.status_code == 200, up.text
    assert up.json()["pose"]["thumb_ok"] is True and "surpreso" in up.json()["urls"]
    bad = client.post(f"/api/channels/{cid}/presenter/poses", data={"pose_id": "x"},
                      files={"file": ("x.jpg", _png(alpha=False, fmt="JPEG"), "image/jpeg")})
    assert bad.status_code == 422
    # import: só pastas do canais ou da biblioteca
    outside = client.post(f"/api/channels/{cid}/presenter/import", json={"path": str(tmp_path)})
    assert outside.status_code == 422
    lib = get_settings().library_dir / "poses-teste"
    lib.mkdir(parents=True)
    for pid in ("base", "feliz", "lupa", "oculos", "apontando"):
        (lib / f"{pid}.png").write_bytes(_png())
    imp = client.post(f"/api/channels/{cid}/presenter/import", json={"path": str(lib), "enable": True})
    assert imp.status_code == 200, imp.text
    assert imp.json()["report"]["enabled"] is True and len(imp.json()["presenter"]["poses"]) == 6
    # PUT do avatar: valida arquivos
    body = imp.json()["presenter"]
    body["poses"][0]["emotions"] = ["neutro", "calmo"]
    assert client.put(f"/api/channels/{cid}/presenter", json=body).status_code == 200
    body["poses"].append({"id": "fantasma", "file": f"library/presenter/{cid}/fantasma.png"})
    assert client.put(f"/api/channels/{cid}/presenter", json=body).status_code == 422

    # projeto: ajuste, cena manual e redistribuição
    doc = service.create_project(title="P", style_id="curiosidades-longo", channel_id=cid)
    texts = [" ".join(f"fala{i}x{j}" for j in range(40)) for i in range(12)]
    new = doc.model_copy(deep=True)
    new.segments = [Segment(id=f"seg_{i:02d}", text={"pt-BR": t}) for i, t in enumerate(texts)]
    new.scenes = [Scene(id=f"scn_{i:02d}", segment_id=f"seg_{i:02d}", visual=Visual(prompt="x")) for i in range(12)]
    service.commit(doc.id, new, "roteiro")
    view = client.get(f"/api/projects/{doc.id}/presenter").json()
    assert view["video"] is True and view["thumb"] is True and view["count"] == 0
    r = client.put(f"/api/projects/{doc.id}/scenes/scn_05/presenter", json={"presenter": {"pose": "lupa", "at": "fala5x3"}})
    assert r.status_code == 200 and r.json()["presenter"]["source"] == "manual"
    assert client.put(f"/api/projects/{doc.id}/scenes/scn_99/presenter", json={"presenter": None}).status_code == 404
    assigned = client.post(f"/api/projects/{doc.id}/presenter/assign").json()
    assert assigned["count"] >= 2
    doc2 = service.get_doc(doc.id)
    assert doc2.scene("scn_05").presenter.pose == "lupa" and doc2.scene("scn_05").presenter.source == "manual"
    off = client.put(f"/api/projects/{doc.id}/presenter", json={"enabled": False}).json()
    assert off["video"] is False and off["override"]["enabled"] is False
