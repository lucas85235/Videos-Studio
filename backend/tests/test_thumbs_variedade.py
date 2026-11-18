"""Variedade das thumbs do pacote: histórico do canal (fundo, figura, pose, template, cor e lado do texto), cor do
texto por contraste e novidade, molde espelhado, prova do próprio vídeo (template `prova`/`objeto`) e o aviso de
thumb parecida demais com uma recente do canal. Só ativos sintéticos, banco e pasta de dados do teste."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image, ImageDraw

from studio.config import get_settings, set_settings
from studio.core.project import service
from studio.core.project.document import CHANNEL_THUMBS_KEY, PRESENTER_KEY, PackageCandidate, ThumbLayers
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.media import thumb_variety as V
from studio.models import Channel
from studio.workers import tasks
from studio.workers.pipeline import authoring
from studio.workers.visual import thumb_history

LONG = "curiosidades-longo"
FRAMES = [
    {"id": "neon", "kind": "frame", "file": "frames/neon.png", "tags": ["lugar", "neon"], "editorial": True},
    {"id": "neon-2", "kind": "frame", "file": "frames/neon-2.png", "tags": ["lugar", "neon"], "editorial": True},
    {"id": "praia", "kind": "frame", "file": "frames/praia.png", "tags": ["lugar", "praia"], "editorial": True},
    {"id": "ceu", "kind": "frame", "file": "frames/ceu.png", "tags": ["lugar", "ceu", "claro"], "editorial": True},
]
COLORS = {"neon": (60, 20, 90), "neon-2": (90, 30, 120), "praia": (40, 190, 200), "ceu": (235, 235, 240)}
POSES = [
    {"id": "surpreso", "file": "library/presenter/chn_v/surpreso.png", "emotions": ["choque"], "thumb_ok": True},
    {"id": "chocado", "file": "library/presenter/chn_v/chocado.png", "emotions": ["choque"], "thumb_ok": True},
    {"id": "lupa", "file": "library/presenter/chn_v/lupa.png", "emotions": ["detalhe"], "thumb_ok": True},
]
BIBLE = {PRESENTER_KEY: {"enabled": True, "poses": POSES}, CHANNEL_THUMBS_KEY: {"allow_editorial": True}}


def _online() -> None:
    set_settings(get_settings().model_copy(update={"offline": False}))


def _channel(cid: str = "chn_v") -> str:
    with session_scope() as s:
        s.add(Channel(id=cid, name="Canal", settings={}, style_bible=BIBLE))
    kit = get_settings().library_dir / "thumbkit" / cid
    (kit / "frames").mkdir(parents=True, exist_ok=True)
    for f in FRAMES:
        im = Image.new("RGB", (640, 360), COLORS[f["id"]])
        d = ImageDraw.Draw(im)
        for k in range(5):  # um desenho por quadro: o dHash de cada um é outro
            d.rectangle((k * 120 + len(f["id"]) * 9, 60 + k * 40, k * 120 + 70, 330), fill=(k * 50, 255 - k * 40, 90))
        im.save(kit / f["file"])
    (kit / "thumbkit.json").write_text(json.dumps({"items": FRAMES}), encoding="utf-8")
    pres = get_settings().data_dir / "library" / "presenter" / cid
    pres.mkdir(parents=True, exist_ok=True)
    for p in POSES:
        im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
        ImageDraw.Draw(im).ellipse((120, 20, 280, 180), fill=(220, 170, 140, 255))
        ImageDraw.Draw(im).rectangle((60, 180, 340, 600), fill=(30, 30, 40, 255))
        im.save(get_settings().data_dir / p["file"])
    return cid


def _fake_llm(monkeypatch: pytest.MonkeyPatch, data: dict[str, Any], seen: list[str]) -> None:
    def fake(task, system, prompt, schema, context, project_id, job_id):
        seen.append(prompt)
        return SimpleNamespace(data=data), "fake"

    monkeypatch.setattr(authoring, "_llm", fake)


def _raw(n: int, template: str = "avatar-reage", bg: str | None = "neon", pose: str | None = "choque") -> dict:
    return {"candidates": [
        {"title": f"Título bom número {i}", "thumb_text": ["GOLPE", "NÃO", "CUIDADO", "SAIU"][i % 4], "promise": "p",
         "concept": "c", "template": template, "background_hint": bg, "characters_hint": [], "presenter_pose": pose,
         "pill": None, "cards": []} for i in range(n)]}


def _history(*layers: ThumbLayers) -> V.Usage:
    u = V.Usage()
    for lay in layers:
        u.add_layers(lay)
    return u


def _old(bg: str = "neon", pose: str = "surpreso", fill: str = "yellow") -> ThumbLayers:
    return ThumbLayers.model_validate({
        "template": "avatar-reage", "background": {"kind": "frame", "ref": bg},
        "presenter": {"pose": pose, "side": "right"}, "text": {"words": "X", "fill": fill, "pos": "left"}})


# --- cor e marcação --------------------------------------------------------------------------------------------------
def test_cor_do_texto_foge_do_amarelo_e_respeita_o_contraste():
    recent = _history(*[_old() for _ in range(6)])
    assert V.pick_fill((40, 20, 60), recent, V.Usage()) != "yellow"
    # fundo claro: branco, amarelo e limão não passam no contraste
    pick = V.pick_fill((240, 240, 235), None, None)
    assert pick in ("red",) or V.color_rgb(pick)[0] < 200 or pick == "orange"
    assert pick not in ("white", "yellow", "lime")
    # dentro do pacote: duas cores seguidas não se repetem
    batch = V.Usage()
    picks = []
    for k in range(4):
        c = V.pick_fill((30, 30, 50), recent, batch, turn=k)
        batch.add({"fill": [c]})
        picks.append(c)
    assert len(set(picks)) == 4 and "yellow" not in picks


def test_marcacao_da_prova():
    assert V.mark_for("FALSO") == "x" and V.mark_for("NÃO BAIXE") == "x" and V.mark_for("GOLPE?") == "x"
    assert V.mark_for("2027") == "circle"


# --- gerador ---------------------------------------------------------------------------------------------------------
def test_pacote_foge_das_thumbs_recentes_do_canal(monkeypatch):
    _online()
    cid = _channel()
    seen: list[str] = []
    _fake_llm(monkeypatch, _raw(4), seen)
    doc = service.create_project(title="GTA 6 no PC", topic="GTA 6 no PC", style_id=LONG, channel_id=cid)
    history = _history(*[_old() for _ in range(3)])
    cands, _ = authoring.package_ideas(doc, {"style_bible": BIBLE}, get_style(LONG), 4, 4, history=history)
    bgs = [c.layers.background.ref for c in cands]
    assert bgs[0] == "neon-2"  # o LLM pediu o neon de sempre: entra o outro do mesmo assunto
    assert "neon" not in bgs[:2]
    assert any("repetia as thumbs recentes" in n for n in cands[0].score["notes"])
    fills = [c.layers.text.fill for c in cands]
    assert "yellow" not in fills and len(set(fills)) == len(fills)
    sides = {c.layers.text.pos for c in cands}
    assert sides == {"left", "right"}  # o molde espelha em parte dos candidatos
    for c in cands:
        if c.layers.text.pos == "right":
            assert c.layers.presenter.side == "left"
    poses = [c.layers.presenter.pose for c in cands]
    assert poses[0] == "chocado"  # "choque" pedia surpreso, que o canal já repetiu: outra pose da mesma emoção
    assert "Thumbs recentes do canal" in seen[0] and "neon (3)" in seen[0]


def test_captura_do_proprio_video_vira_prova(monkeypatch):
    _online()
    cid = _channel()
    doc = service.create_project(title="Golpe do GTA 6 no PC", topic="golpe do download falso do GTA 6 no PC",
                                 style_id=LONG, channel_id=cid)
    media = get_settings().uploads_dir / doc.id / "media"
    media.mkdir(parents=True)
    Image.new("RGB", (1200, 700), (250, 250, 250)).save(media / "cap.jpg")
    Image.new("RGB", (1200, 700), (200, 40, 40)).save(media / "foto.jpg")
    rel = f"uploads/{doc.id}/media"
    save_pool(doc.id, Pool(items=[
        PoolItem(id="p1", tier="official", media="image", path=f"{rel}/foto.jpg", desc="Trailer oficial",
                 editorial=True),
        PoolItem(id="p2", tier="capture", media="capture", path=f"{rel}/cap.jpg", credit="canaltech.com.br",
                 desc="Falso GTA 6 para PC espalha vírus", editorial=True),
        PoolItem(id="p3", tier="capture", media="capture", path=None, desc="sem arquivo"),
        PoolItem(id="p4", tier="official", media="image", path="library/thumbkit/chn_v/frames/neon.png"),
    ]))
    style = get_style(LONG)
    assets = authoring.package_assets(doc, style, BIBLE)
    assert [p["id"] for p in assets.proofs] == ["p2", "p1"]  # captura antes; sem arquivo e quadro do kit fora
    assert "prova" in authoring.thumb_templates_for(style, assets)
    raw = _raw(2)
    raw["candidates"][0]["background_hint"] = "p2"  # o LLM cita a captura num template comum
    seen: list[str] = []
    _fake_llm(monkeypatch, raw, seen)
    cands, _ = authoring.package_ideas(doc, {"style_bible": BIBLE}, style, 2, 2, assets=assets)
    prova = cands[0].layers
    assert prova.template == "prova" and prova.background.kind == "media"
    assert prova.background.ref == f"media:{rel}/cap.jpg" and prova.mark == "x"
    assert prova.presenter is not None and prova.presenter.crop == "meio"
    assert "p2 (captura · canaltech.com.br" in seen[0]
    # sem o canal liberar material de terceiros, a captura (editorial) não entra
    assert authoring.project_proofs(doc, allow_editorial=False) == []


def test_offline_usa_prova_quando_ha_captura():
    cid = _channel()
    doc = service.create_project(title="Golpe", topic="golpe", style_id=LONG, channel_id=cid)
    media = get_settings().uploads_dir / doc.id / "media"
    media.mkdir(parents=True)
    Image.new("RGB", (800, 450), (250, 250, 250)).save(media / "cap.jpg")
    save_pool(doc.id, Pool(items=[PoolItem(id="p9", tier="capture", media="capture",
                                           path=f"uploads/{doc.id}/media/cap.jpg", desc="golpe", editorial=True)]))
    style = get_style(LONG).model_copy(deep=True)
    style.thumbnail.templates = ["heroi-palavra"]
    cands, provider = authoring.package_ideas(doc, {"style_bible": BIBLE}, style, 4, 4)
    assert provider == "offline"
    assert "prova" in {c.layers.template for c in cands}


# --- compositor ------------------------------------------------------------------------------------------------------
def test_molde_espelhado_e_cartao_da_prova(tmp_path):
    from studio.media.thumb_assets import AssetResolver

    img = Image.new("RGB", (1200, 700), (250, 250, 250))
    ImageDraw.Draw(img).rectangle((100, 100, 700, 300), fill=(20, 20, 20))  # o "título" da matéria
    img.save(tmp_path / "cap.jpg")
    r = AssetResolver(None, media=lambda ref: tmp_path / "cap.jpg")
    right = ThumbLayers.model_validate({"template": "numero-lista", "logo": "none",
                                        "text": {"words": "5 SINAIS", "fill": "cyan", "pos": "right", "size": 0.6}})
    lay = C.render(right, T.CANVAS, r).info["layout"]
    assert lay["text_bbox"][0] > T.CANVAS[0] * 0.5  # a coluna do número foi para a direita
    prova = ThumbLayers.model_validate({"template": "prova", "logo": "none", "mark": "x",
                                        "background": {"kind": "media", "ref": "media:cap"},
                                        "text": {"words": "FALSO", "fill": "red", "pos": "left"}})
    out = C.render(prova, T.CANVAS, r)
    lay = out.info["layout"]
    card = next(f for f in lay["figures"] if f["kind"] == "card")
    assert card["bbox"][0] > T.CANVAS[0] * 0.35 and lay["objects"] >= 1
    assert lay["text_bbox"][2] < card["bbox"][0]  # texto e prova não se cobrem
    cx, cy = (card["bbox"][0] + card["bbox"][2]) // 2, (card["bbox"][1] + card["bbox"][3]) // 2
    px = out.getpixel((cx, cy))
    assert px[0] > 180 and px[1] < 90  # o X vermelho cruza o meio do cartão
    assert lay["text_fill"] == list(C.fill_rgb("red", C._palette(None)))


# --- histórico do canal e semelhança -----------------------------------------------------------------------------------
def _with_package(cid: str, title: str, layers: ThumbLayers, *, chosen: bool) -> str:
    doc = service.create_project(title=title, style_id=LONG, channel_id=cid)
    cand = PackageCandidate(title=title, thumb_text="X", layers=layers)
    doc.package.candidates = [cand]
    if chosen:
        doc.package.chosen_id = cand.id
        cand.approved = True
    service.commit(doc.id, doc, "pacote")
    tasks.render_package_thumbs(doc.id)
    return doc.id


def test_historico_conta_o_escolhido_e_avisa_thumb_parecida():
    cid = _channel()
    same = ThumbLayers.model_validate({"template": "heroi-palavra", "logo": "none",
                                       "background": {"kind": "frame", "ref": "praia"},
                                       "text": {"words": "TUDO", "fill": "yellow", "pos": "top"}})
    old = _with_package(cid, "Vídeo antigo", same, chosen=True)
    hist = thumb_history.channel_history(cid, exclude="prj_outro")
    assert hist.usage.n("background", "praia") == 1 and hist.usage.n("fill", "yellow") == 1
    assert len(hist.thumbs) == 1 and hist.thumbs[0]["project_id"] == old
    # projeto novo com a mesma thumb: troca o fundo (o menos usado do mesmo assunto) e não fica parecida
    new = service.create_project(title="Novo", style_id=LONG, channel_id=cid)
    a = PackageCandidate(title="Igual", thumb_text="TUDO", layers=same)
    b = PackageCandidate(title="Outra", thumb_text="TUDO", layers=same.model_copy(update={
        "background": same.background.model_copy(update={"ref": "ceu"})}))
    new.package.candidates = [a, b]
    service.commit(new.id, new, "pacote")
    tasks.render_package_thumbs(new.id, demote=True)
    cands = {c.id: c for c in service.get_doc(new.id).package.candidates}
    first = cands[a.id]
    assert first.layers.background.ref != "praia"
    assert any("parecida com uma recente do canal" in w for w in first.score["warnings"])
    assert first.score["recent_dup"] >= thumb_history.RECENT_MIN_DISTANCE or "recent_like" in first.score


def test_parecida_sem_troca_possivel_avisa_e_vai_para_o_fim():
    cid = _channel()
    same = ThumbLayers.model_validate({"template": "heroi-palavra", "logo": "none",
                                       "background": {"kind": "gradient", "ref": "vice"},
                                       "text": {"words": "TUDO", "fill": "yellow", "pos": "top"}})
    _with_package(cid, "Vídeo antigo", same, chosen=True)
    new = service.create_project(title="Novo", style_id=LONG, channel_id=cid)
    a = PackageCandidate(title="Igual", thumb_text="TUDO", layers=same)
    b = PackageCandidate(title="Diferente", thumb_text="TUDO", layers=same.model_copy(update={
        "background": same.background.model_copy(update={"kind": "frame", "ref": "praia"})}))
    new.package.candidates = [a, b]
    service.commit(new.id, new, "pacote")
    tasks.render_package_thumbs(new.id, demote=True)
    cands = service.get_doc(new.id).package.candidates
    assert [c.id for c in cands] == [b.id, a.id]  # a parecida foi para o fim
    assert cands[1].score["recent_dup"] < thumb_history.RECENT_MIN_DISTANCE
    assert any("parecida demais com uma thumb recente" in w for w in cands[1].score["warnings"])
