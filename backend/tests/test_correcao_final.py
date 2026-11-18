"""Correção final do pacote (título e thumb), do compositor e do avatar: os achados confirmados na revisão."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
from PIL import Image
from test_thumb_compose import _layers, kit  # noqa: F401 - fixture do kit sintético

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import PackageCandidate, Presenter, PresenterPose, ThumbLayers
from studio.core.styles.library import get_style
from studio.media import thumb_assets as A
from studio.media import thumb_check as K
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.workers import tasks
from studio.workers.pipeline import authoring
from studio.workers.pipeline import presenter as presenter_scenes
from studio.workers.shorts.derive import short_cover_text
from studio.workers.visual import thumbs as visual_thumbs

LONG, SHORT = "curiosidades-longo", "curiosidades-short"


def ctx_for(pid: str) -> Any:
    return SimpleNamespace(project_id=pid, job_id=None, progress=lambda f, m="": None, cancelled=lambda: False)


# --- fluxo do pacote -------------------------------------------------------------------------------------------------
def test_ideia_longa_gera_o_pacote_ao_lado_do_candidato_semeado():
    doc = service.create_project(title="GTA 6: a primeira semana", style_id=LONG, seed_candidate=True)
    assert tasks.package_missing(doc)
    result = tasks.run_autopilot(ctx_for(doc.id), {})
    assert result["stopped_at"] == "package" and result["steps"] == ["package"]
    doc = service.get_doc(doc.id)
    sources = [c.source for c in doc.package.candidates]
    assert sources.count("manual") == 1 and sources.count("auto") >= 2
    assert all(c.image for c in doc.package.candidates)  # o semeado também ganha prévia
    assert not tasks.package_missing(doc)


def test_short_de_ideia_gera_e_trava_o_pacote():
    doc = service.create_project(title="Um Short da ideia", style_id=SHORT, seed_candidate=True)
    result = tasks.run_autopilot(ctx_for(doc.id), {})
    assert result["steps"][:2] == ["package", "script"]
    doc = service.get_doc(doc.id)
    assert doc.package.locked and any(c.source == "auto" for c in doc.package.candidates)


def test_reescrever_com_roteiro_nao_para_no_pacote_e_aprovar_sai_do_status():
    from studio.api import routes_package

    doc = service.create_project(title="Reescrita", style_id=LONG)
    tasks.run_script(ctx_for(doc.id), {})  # roteiro direto pelo job, sem passar pelo portão
    result = tasks.run_autopilot(ctx_for(doc.id), {"rewrite": True})
    assert result["stopped_at"] != "package"
    tasks.generate_package(doc.id, {})
    service.set_status(doc.id, tasks.PACKAGE_STATUS)
    cand = service.get_doc(doc.id).package.candidates[0]
    out = routes_package.approve_package(doc.id, cand.id)
    assert out["notice"] and out["job"] is None
    assert service.get_project_row(doc.id)["status"] == "draft"


def test_aprovar_outro_par_limpa_titulo_e_texto_dos_outros_idiomas():
    doc = service.create_project(title="Bilíngue", style_id=LONG, languages=["pt-BR", "en-US"])
    tasks.generate_package(doc.id, {})
    a, b = service.get_doc(doc.id).package.candidates[:2]
    tasks.approve_package(doc.id, a.id)
    doc = service.get_doc(doc.id)
    doc, _ = authoring.translate(doc, "en-US")
    service.commit(doc.id, doc, "tradução")
    assert doc.metadata["en-US"].title == f"[en-US] {a.title}"
    doc.package.locked = False
    service.commit(doc.id, doc, "destrava")
    tasks.approve_package(doc.id, b.id)
    doc = service.get_doc(doc.id)
    assert "en-US" not in doc.thumbnail.text and doc.metadata["en-US"].title == ""
    doc, _ = authoring.translate(doc, "en-US")
    assert doc.metadata["en-US"].title == f"[en-US] {b.title}"
    if b.thumb_text:
        assert doc.thumbnail.text["en-US"] == f"[en-US] {b.thumb_text}"
    # reaprovar o mesmo par não apaga a tradução
    service.commit(doc.id, doc, "tradução B")
    tasks.approve_package(doc.id, b.id)
    assert service.get_doc(doc.id).metadata["en-US"].title == f"[en-US] {b.title}"


def test_pacote_destravado_nao_manda_na_capa_nem_no_ab():
    doc = service.create_project(title="Destravado", style_id=LONG)
    doc.package.candidates = [PackageCandidate(title="A", thumb_text="TUDO")]
    doc.package.chosen_id = doc.package.candidates[0].id
    assert doc.locked_package() is None and visual_thumbs.package_stamp(doc, "pt-BR") is None
    doc.package.locked = True
    assert doc.locked_package() is not None and visual_thumbs.package_stamp(doc, "pt-BR")


def test_titulo_de_trabalho_da_tela_vira_candidato():
    from studio.api.app import create_app

    with TestClient(create_app()) as client:
        res = client.post("/api/projects", json={"title": "O farol", "style_id": LONG, "seed_candidate": True})
        assert res.status_code == 200, res.text
        pid = res.json()["project"]["id"]
        plain = client.post("/api/projects", json={"title": "Sem semente", "style_id": LONG}).json()["project"]["id"]
    assert [c.title for c in service.get_doc(pid).package.candidates] == ["O farol"]
    assert service.get_doc(plain).package.candidates == []


def test_texto_da_capa_do_short_derivado_tem_ate_3_palavras():
    assert short_cover_text("Você não vai acreditar no que a Rockstar escondeu no mapa") == "acreditar Rockstar escondeu"
    assert short_cover_text("O MAPA VAZOU") == "O MAPA VAZOU"
    assert short_cover_text("") == ""


# --- compositor e checagem -----------------------------------------------------------------------------------------
def test_versus_texto_fica_fora_do_logo(kit):  # noqa: F811
    resolver, _ = kit
    lay = T.default_layers("versus", {"words": "MAPA NOVO", "pos": "left", "background": "frame:sol",
                                      "left": "frame:praia", "logo": "corner"})
    img = C.render(lay, T.CANVAS, resolver)
    layout = img.info["layout"]
    logos = [f["bbox"] for f in layout["figures"] if f["kind"] == "logo"]
    assert logos and all(not K._intersects(b, layout["text_bbox"]) for b in logos)
    assert "logo×texto" not in json.dumps(K.check(img, lay, True))


def test_logo_nao_cobre_o_avatar_no_lugar_mapa(kit):  # noqa: F811
    resolver, _ = kit
    img = C.render(_layers("lugar-mapa"), T.CANVAS, resolver)
    layout = img.info["layout"]
    face = next(f["face_box"] for f in layout["figures"] if f["kind"] == "presenter")
    for f in layout["figures"]:
        if f["kind"] == "logo":
            assert not K._intersects(f["bbox"], face)


def test_checagem_avisa_texto_em_cima_do_rosto(kit):  # noqa: F811
    resolver, _ = kit
    lay = _layers("avatar-reage", words="VENCE AGORA", side="left")
    img = C.render(lay, T.CANVAS, resolver)
    score = K.check(img, lay, True)
    assert "texto×rosto" in score["overlaps"]
    assert any("um em cima do outro" in w for w in score["warnings"])


def test_contraste_mede_o_fundo_e_nao_o_contorno():
    yellow = T.default_layers("heroi-palavra", {"words": "MAPA", "gradient": "#FFD400,#FFD400"})
    yellow.background.ref = "#FFD400,#FFD400"
    dark = T.default_layers("heroi-palavra", {"words": "MAPA"})
    dark.background.ref = "#1A0B2E,#1A0B2E"
    s_yellow = K.check(img := C.render(yellow, T.CANVAS, None), yellow, True)
    s_dark = K.check(C.render(dark, T.CANVAS, None), dark, True)
    assert img.info["layout"]["text_bg"]
    assert s_dark["legib_168"]["contrast"] > s_yellow["legib_168"]["contrast"] + 3
    assert s_dark["legib_168"]["contrast"] >= 4.5


def test_numero_estreito_nao_encolhe_a_linha_de_baixo():
    lay = T.default_layers("numero-lista", {"words": "1 SEMANA"})
    img = C.render(lay, T.CANVAS, None)
    lines = img.info["layout"]["text_lines"]
    sub = next(line for line in lines if line["text"] == "SEMANA")
    assert sub["size"] >= 0.12 * T.CANVAS[1]
    assert K.check(img, lay, True)["legib_168"]["stroke_px"] >= K.LIMITS["stroke_px"]


def test_texto_longo_no_short_cabe_no_centro_seguro():
    lay = T.default_layers("livre", {"words": "NÃO PROCURE ISSO AGORA MESMO"})
    img = C.render(lay, T.SHORT_CANVAS, None)
    assert img.info["layout"]["text_bbox"][3] <= 0.70 * T.SHORT_CANVAS[1]


def test_rosto_do_avatar_no_short_cai_na_interface(kit):  # noqa: F811
    resolver, _ = kit
    lay = T.default_layers("livre", {"words": "MAPA", "presenter": True, "pose": "surpreso"})
    img = C.render(lay, T.SHORT_CANVAS, resolver)
    assert "rosto×base" in json.dumps(K.check(img, lay, False)["warnings"], ensure_ascii=False)


# --- ativos: material oficial e avatar ------------------------------------------------------------------------------
def _kit_channel(cid: str, kit_dir: Path) -> Path:
    import shutil

    dest = A.thumbkit_dir(cid)
    shutil.copytree(kit_dir, dest)
    return dest


def test_upload_de_quadro_oficial_nao_fura_o_bloqueio(kit):  # noqa: F811
    _resolver, kit_dir = kit
    dest = _kit_channel("chn_outro", kit_dir)
    rel = (dest / "frames" / "sol.png").relative_to(get_settings().data_dir).as_posix()
    blocked = A.AssetResolver.for_channel("chn_meu")
    assert blocked.allow_editorial is False  # padrão: bloqueado
    lay = T.default_layers("heroi-palavra", {"words": "MAPA", "background": f"upload:{rel}"})
    img = C.render(lay, T.CANVAS, blocked)
    assert any("material oficial bloqueado" in w for w in img.info["layout"]["warnings"])
    free = get_settings().data_dir / "uploads" / "livre.png"
    free.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 36), (10, 20, 30)).save(free)
    assert blocked.resolve("upload:uploads/livre.png").editorial is False


def test_avatar_desligado_nao_aparece_mesmo_com_a_pose_na_pasta(kit):  # noqa: F811
    _resolver, kit_dir = kit
    cid = "chn_avatar"
    _kit_channel(cid, kit_dir)
    pose_dir = get_settings().library_dir / "presenter" / cid
    pose_dir.mkdir(parents=True)
    Image.new("RGBA", (200, 300), (200, 100, 100, 255)).save(pose_dir / "surpreso.png")
    lay = _layers("avatar-reage")
    off = A.AssetResolver.for_channel(cid, presenter=None, allow_editorial=True)
    img = C.render(lay, T.CANVAS, off)
    assert not [f for f in img.info["layout"]["figures"] if f["kind"] == "presenter"]
    pres = Presenter(enabled=True, poses=[PresenterPose(id="surpreso", file=f"library/presenter/{cid}/surpreso.png")])
    on = A.AssetResolver.for_channel(cid, presenter=pres, allow_editorial=True)
    img = C.render(lay, T.CANVAS, on)
    assert [f for f in img.info["layout"]["figures"] if f["kind"] == "presenter"]


def test_cli_render_segue_o_canal_e_aceita_oficial(kit, tmp_path, capsys):  # noqa: F811
    from studio.cli import main

    _resolver, kit_dir = kit
    _kit_channel("chn_cli2", kit_dir)
    layers = tmp_path / "l.json"
    layers.write_text(_layers("heroi-palavra").model_dump_json(), encoding="utf-8")
    out = tmp_path / "t.png"
    main(["thumbkit", "render", str(layers), str(out), "--canal", "chn_cli2"])
    blocked = json.loads(capsys.readouterr().out)
    assert any("material oficial bloqueado" in w for w in blocked["warnings"])
    main(["thumbkit", "render", str(layers), str(out), "--canal", "chn_cli2", "--oficial"])
    assert not any("bloqueado" in w for w in json.loads(capsys.readouterr().out)["warnings"])


def test_troca_de_fundo_leva_foco_e_zoom_do_quadro_novo(monkeypatch):
    frames = [{"id": "a", "kind": "frame", "file": "GTAVI_Trailer_2@1.jpg", "tags": ["x"], "focus_x": 0.7},
              {"id": "b", "kind": "frame", "file": "aereo.jpg", "tags": ["x"], "focus_x": 0.3}]
    monkeypatch.setattr(authoring, "thumbkit_manifest", lambda cid: frames)
    doc = service.create_project(title="Troca", style_id=LONG)
    bg = {"kind": "frame", "ref": "a", "zoom": 1.4, "focus_x": 0.7}
    same = ThumbLayers.model_validate({"template": "lugar-mapa", "background": bg, "text": {"words": "TUDO"}})
    doc.package.candidates = [PackageCandidate(title="A", thumb_text="TUDO", layers=same),
                              PackageCandidate(title="B", thumb_text="TUDO", layers=same)]
    service.commit(doc.id, doc, "dois iguais")
    tasks.render_package_thumbs(doc.id)
    second = service.get_doc(doc.id).package.candidates[1].layers.background
    assert second.ref == "b" and second.focus_x == 0.3 and second.zoom == 1.0


def test_cache_da_capa_muda_com_o_arquivo(kit):  # noqa: F811
    resolver, kit_dir = kit
    lay = _layers("heroi-palavra")
    before = visual_thumbs.assets_stamp(lay, resolver)
    path = kit_dir / "frames" / "sol.png"
    Image.new("RGB", (1600, 900), (1, 2, 3)).save(path)
    assert visual_thumbs.assets_stamp(lay, resolver) != before
    assert C.VERSION >= 2


# --- avatar --------------------------------------------------------------------------------------------------------
def test_cena_seria_por_palavra_inteira():
    assert presenter_scenes._serious("o Jason lutou contra a polícia") is None
    assert presenter_scenes._serious("igual ao Assassin's Creed") is None
    assert presenter_scenes._serious("a família está de luto") == "luto"
    assert presenter_scenes._serious("o assassinato do prefeito") == "assassin"


def test_pacote_oferece_as_poses_pela_regra_da_thumb():
    pres = {"enabled": True, "use_in_thumb": False, "use_in_shorts": True,
            "poses": [{"id": "surpreso", "file": "library/presenter/c/surpreso.png", "thumb_ok": True}]}
    doc = service.create_project(title="Short", style_id=SHORT)
    assets = authoring.package_assets(doc, get_style(SHORT), {"presenter": pres})
    assert assets.poses == []  # "Nas thumbs" desmarcado: o Short também não usa
    long_doc = service.create_project(title="Longo", style_id=LONG)
    long_doc.presenter.poses_off = ["Surpreso"]
    pres["use_in_thumb"] = True
    assert authoring.package_assets(long_doc, get_style(LONG), {"presenter": pres}).poses == []


def test_pose_trocada_muda_a_camada_e_gatilho_em_numero_fundido(tmp_path):
    from studio.core import timeline as tl
    from studio.core.project.document import Scene, ScenePresenter
    from studio.providers.base import Word

    rel = "library/presenter/c/surpreso.r900.png"
    path = get_settings().data_dir / rel
    path.parent.mkdir(parents=True)
    Image.new("RGBA", (10, 10)).save(path)
    pres = Presenter(enabled=True, poses=[PresenterPose(id="surpreso", file="library/presenter/c/surpreso.png",
                                                        render_file=rel)])
    scene = Scene(id="s1", segment_id="g1", presenter=ScenePresenter(pose="surpreso", at="2026"))
    raw = [Word(text=t, start=i * 0.3, end=i * 0.3 + 0.25) for i, t in
           enumerate("o jogo sai em dois mil e vinte e seis mesmo".split())]
    merged = tl.merge_number_words(raw, "pt-BR")
    frame = lambda s: int(round(s * 30))  # noqa: E731
    resolved: dict[str, Any] = {"layout": "full"}
    layer = tl.presenter_layer(pres, scene, resolved, raw, 0, 300, frame, 30, merged_words=merged)
    assert layer is not None and layer["from"] == frame(merged[4].start) - tl.PRESENTER_LEAD_FRAMES
    rev = layer["rev"]
    Image.new("RGBA", (30, 30)).save(path)
    again = tl.presenter_layer(pres, scene, resolved, raw, 0, 300, frame, 30, merged_words=merged)
    assert again and again["rev"] != rev
