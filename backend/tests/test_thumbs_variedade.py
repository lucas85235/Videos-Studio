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
    draw = ImageDraw.Draw(img)
    for top in (100, 210):  # o "título" da matéria: 2 linhas de "letras" (tinta esparsa, não um bloco cheio)
        for x in range(100, 700, 22):
            draw.rectangle((x, top, x + 9, top + 80), fill=(20, 20, 20))
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
    # a manchete (as 2 linhas de "letras") foi achada e o X fica fora dela, no pedaço livre de baixo do cartão
    hx0, hy0, hx1, hy1 = card["headline"]
    assert hx0 < card["bbox"][0] + 80 and hx1 > card["bbox"][2] - 80  # a janela tem a manchete inteira
    row = [out.getpixel((x, (hy0 + hy1) // 2)) for x in range(hx0, hx1, 3)]
    assert not any(p[0] > 180 and p[1] < 90 for p in row)  # nenhum vermelho em cima da manchete
    cx, cy = (card["bbox"][0] + card["bbox"][2]) // 2, (hy1 + card["bbox"][3]) // 2
    px = out.getpixel((cx, cy))
    assert px[0] > 180 and px[1] < 90  # o X vermelho no meio do pedaço livre
    assert lay["text_fill"] == list(C.fill_rgb("red", C._palette(None)))


def test_janela_da_pagina_comeca_na_manchete_perto_do_fim_da_captura():
    """Manchete no fim da captura (anúncio e menu em cima): a janela começa nela e sai mais baixa, em vez de subir
    e mostrar o espaço vazio de cima."""
    img = Image.new("RGB", (1200, 700), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    draw.rectangle((300, 60, 900, 300), fill=(225, 225, 225))  # o bloco cinza do anúncio
    for top in (430, 520):
        for x in range(100, 1000, 22):
            draw.rectangle((x, top, x + 9, top + 70), fill=(20, 20, 20))
    x0, y0, x1, y1 = C.page_window(img, 16 / 10)
    assert 380 <= y0 <= 430 and y1 == 700  # do topo da manchete até o fim da página
    assert (x1 - x0) / (y1 - y0) > 16 / 10  # mais baixa que a pedida


def _manchete_colada() -> Image.Image:
    """Página com sobretítulo pequeno, manchete de 4 linhas de entrelinha apertada (as hastes de uma linha encostam
    nas da outra: nenhuma linha de pixels vazia entre elas) e a foto da matéria ao lado."""
    img = Image.new("RGB", (1400, 800), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    for x in range(100, 560, 14):  # o sobretítulo (categoria): letra pequena, mais estreito que a manchete
        draw.rectangle((x, 120, x + 7, 135), fill=(20, 60, 230))
    for i, (top, right) in enumerate(((170, 900), (240, 850), (310, 620), (380, 450))):
        for k, x in enumerate(range(100, right, 22)):
            draw.rectangle((x, top + 18, x + 12, top + 58), fill=(10, 80, 250))  # o corpo da letra (grossa)
            if k % 4 == 0:
                draw.rectangle((x, top, x + 5, top + 18), fill=(10, 80, 250))  # haste de cima
            if k % 5 == 2 and i < 3:
                draw.rectangle((x + 6, top + 58, x + 11, top + 72), fill=(10, 80, 250))  # perna que encosta embaixo
    for y in range(100, 700, 4):  # a foto ao lado: mancha cheia de cor (não é texto)
        draw.rectangle((1000, y, 1380, y + 3), fill=(60 + (y % 40), 40, 90 + (y % 60)))
    return img


def test_manchete_de_varias_linhas_coladas_inteira_no_cartao(tmp_path):
    """Manchete de 4 linhas sem vão entre elas (um trecho de tinta alto demais para ser "uma linha") e sobretítulo
    pequeno em cima: a manchete achada é o bloco das 4 linhas (não a categoria), a janela tem a largura dela inteira
    e o X da prova fica fora das letras."""
    from studio.media.thumb_assets import AssetResolver

    img = _manchete_colada()
    w, h = img.size
    band = C.headline_band(img)
    assert band is not None
    assert band[0] * w <= 105 and band[2] * w >= 890  # da 1ª à linha mais larga (todas as linhas)
    assert 160 <= band[1] * h <= 175 and band[3] * h >= 435  # do topo da 1ª linha ao pé da 4ª; categoria fora
    x0, y0, x1, y1 = C.page_window(img, 16 / 10)
    assert x0 <= 100 and x1 >= 900 and y0 <= 170
    img.save(tmp_path / "cap.jpg")
    r = AssetResolver(None, media=lambda ref: tmp_path / "cap.jpg")
    prova = ThumbLayers.model_validate({"template": "prova", "logo": "none", "mark": "x",
                                        "background": {"kind": "media", "ref": "media:cap"},
                                        "text": {"words": "GOLPE", "fill": "white", "pos": "left"}})
    out = C.render(prova, T.CANVAS, r)
    card = next(f for f in out.info["layout"]["figures"] if f["kind"] == "card")
    hx0, hy0, hx1, hy1 = card["headline"]
    assert hy1 - hy0 > (card["bbox"][3] - card["bbox"][1]) * 0.3  # as 4 linhas, não uma faixa fina

    def red(p: tuple[int, ...]) -> bool:
        return p[0] > 180 and p[1] < 90 and p[2] < 120

    assert not any(red(out.getpixel((x, y))) for x in range(hx0, hx1, 2) for y in range(hy0, hy1, 2))
    assert any(red(out.getpixel((x, y))) for x in range(card["bbox"][0], card["bbox"][2], 3)
               for y in range(hy1, card["bbox"][3], 3))  # o X está no cartão, embaixo da manchete


def test_x_da_prova_vai_ao_lado_quando_a_manchete_enche_o_cartao():
    """Manchete ocupando o cartão quase todo na altura: o X vai no pedaço livre do lado dela; sem nenhum pedaço
    livre, logo embaixo dela (saindo pela borda do cartão), nunca em cima das letras."""
    H = T.CANVAS[1]
    box = (500, 40, 1260, 620)
    x0, y0, x1, y1 = C._mark_spot(box, (520, 60, 900, 600), H)
    assert x0 > 900 and x1 <= 1260  # à direita da manchete
    x0, y0, x1, y1 = C._mark_spot(box, (510, 50, 1250, 560), H)
    assert y0 > 560 and x1 > x0  # sem lado livre: embaixo da manchete, saindo do cartão


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


# --- revisão: espelho, atributos, candidatos do dono, orientação -------------------------------------------------------
@pytest.mark.parametrize("template", sorted(set(T.MIRRORABLE) | set(T.TEMPLATES)))
def test_lado_do_texto_aceita_todos_os_templates(template):
    for turn in range(3):
        side = authoring._text_side(template, _history(_old()), V.Usage(), turn)
        assert side in ("top", "left", "right", "center")
    assert authoring._text_side("lugar-mapa", None, None, 0) == "center"  # o padrão vem primeiro no desempate


def test_lugar_mapa_no_pacote_nao_derruba(monkeypatch):
    _online()
    cid = _channel()
    _fake_llm(monkeypatch, _raw(3, template="lugar-mapa", pose=None), [])
    doc = service.create_project(title="Mapa", topic="mapa", style_id=LONG, channel_id=cid)
    style = get_style(LONG).model_copy(deep=True)
    style.thumbnail.templates = ["lugar-mapa", "heroi-palavra"]
    cands, _ = authoring.package_ideas(doc, {"style_bible": BIBLE}, style, 3, 3, history=_history(_old()))
    assert cands and {c.layers.template for c in cands} == {"lugar-mapa"}


def test_espelho_nao_engana_a_semelhanca():
    img = Image.new("RGB", (640, 360), (30, 30, 60))
    ImageDraw.Draw(img).rectangle((20, 40, 260, 340), fill=(250, 210, 0))
    ImageDraw.Draw(img).ellipse((400, 60, 600, 300), fill=(200, 60, 90))
    direct, _ = thumb_history.image_hashes(img)
    hist = thumb_history.ChannelThumbs(thumbs=[{"dhash": direct, "landscape": True}])
    mirrored = img.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    assert hist.nearest(thumb_history.image_hashes(mirrored)[0])[0] > 0  # só o dHash: "outra" imagem
    assert hist.nearest(thumb_history.image_hashes(mirrored))[0] == 0  # com o espelho na conta, é a mesma


def test_mesmo_fundo_e_mesma_pose_ou_palavra_e_repeticao():
    old = _old(bg="neon", pose="surpreso")
    hist = thumb_history.ChannelThumbs(thumbs=[{"landscape": True, "title": "Antigo",
                                                "feats": thumb_history.recent_features(old, "NÃO!")}])
    mirrored = old.model_copy(deep=True)
    mirrored.text.pos = "right"
    mirrored.presenter.side = "left"
    found = hist.like(mirrored)
    assert found is not None and "pose" in found[1]  # do outro lado continua a mesma thumb
    other_pose = _old(bg="neon", pose="lupa")
    assert hist.like(other_pose, "nao") is not None  # mesmo fundo e mesma palavra (sem acento e pontuação)
    assert hist.like(other_pose, "SAIU") is None
    assert hist.like(_old(bg="praia", pose="surpreso"), "SAIU") is None


def test_candidato_do_dono_parecido_so_ganha_aviso():
    cid = _channel()
    same = _old(bg="praia", pose="surpreso").model_copy(update={"logo": "none"})
    _with_package(cid, "Vídeo antigo", same, chosen=True)
    new = service.create_project(title="Novo", style_id=LONG, channel_id=cid)
    manual = PackageCandidate(title="Manual", thumb_text="X", layers=same, source="manual")
    chosen = PackageCandidate(title="Escolhido", thumb_text="X", layers=same.model_copy(deep=True))
    auto = PackageCandidate(title="Auto", thumb_text="X", layers=same.model_copy(deep=True))
    new.package.candidates = [manual, chosen, auto]
    new.package.chosen_id = chosen.id
    service.commit(new.id, new, "pacote")
    tasks.render_package_thumbs(new.id)
    cands = {c.id: c for c in service.get_doc(new.id).package.candidates}
    for keep in (manual.id, chosen.id):
        assert cands[keep].layers.background.ref == "praia" and cands[keep].layers.presenter.pose == "surpreso"
        assert any("parecida demais com uma thumb recente" in w for w in cands[keep].score["warnings"])
        assert not any(w.startswith("CORRIGIDO:") and "recente" in w for w in cands[keep].score["warnings"])
    fixed = cands[auto.id].layers
    assert (fixed.background.ref, fixed.presenter.pose) != ("praia", "surpreso")  # o automático troca


def test_historico_soma_so_a_mesma_orientacao():
    cid = _channel()
    livre = ThumbLayers.model_validate({"template": "livre", "logo": "none",
                                        "background": {"kind": "frame", "ref": "ceu"},
                                        "text": {"words": "SAIU", "fill": "yellow", "pos": "center"}})
    _with_package(cid, "Short antigo", livre, chosen=True)
    _with_package(cid, "Longo antigo", _old(bg="neon"), chosen=True)
    long_hist = thumb_history.channel_history(cid, landscape=True)
    short_hist = thumb_history.channel_history(cid, landscape=False)
    assert long_hist.usage.n("background", "ceu") == 0 and long_hist.usage.n("background", "neon") == 1
    assert short_hist.usage.n("background", "ceu") == 1 and short_hist.usage.n("background", "neon") == 0


def test_pacote_sem_canal_sai_com_historico_vazio():
    doc = service.create_project(title="Sem canal", topic="sem canal", style_id=LONG)
    assert thumb_history.channel_history(None).thumbs == []
    out = tasks.generate_package(doc.id, {"n_titles": 2, "n_thumbs": 2})
    assert out["candidates"] >= 1
    assert all(c.image for c in service.get_doc(doc.id).package.candidates)


def test_aviso_de_repeticao_cita_fundo_e_nao_template():
    recent = _history(*[_old() for _ in range(3)])
    rep = V.repeated(_old(bg="neon"), recent)
    assert any(r.startswith("fundo neon") for r in rep)
    assert not any(r.startswith(("template", "cor do texto")) for r in rep)  # 3 usos: ainda é o estilo do canal


# --- revisão: troca de figura, capa, pose e cor -------------------------------------------------------------------------
CUTS = [
    {"id": "jason-capa", "tags": ["personagem", "jason", "keyart", "rosto"]},
    {"id": "jason-carro", "tags": ["personagem", "jason", "rosto"]},
    {"id": "jason-rua", "tags": ["personagem", "jason", "corpo"]},
    {"id": "casal-carro", "tags": ["personagem", "jason", "lucia", "casal"]},
]


def test_troca_de_figura_respeita_personagem_quantidade_e_enquadramento():
    recent = V.Usage()
    recent.add({"character": ["jason-capa"]}, 0.8)  # só vista, sem ser escolhida: já conta
    notes: list[str] = []
    assert authoring._pick_characters(["jason-capa"], CUTS, recent, V.Usage(), notes) == ["jason-carro"]
    assert notes and "jason-carro" in notes[0]
    # o close solo usado mais vezes também: nada do mesmo tipo é menos usado, fica (nunca vira casal nem corpo)
    recent.add({"character": ["jason-carro"]}, 2.0)
    assert authoring._pick_characters(["jason-capa"], CUTS, recent, V.Usage(), []) == ["jason-capa"]
    # corpo inteiro no heroi-palavra vira um close do mesmo personagem
    assert authoring._pick_characters(["jason-rua"], CUTS, None, V.Usage(), []) in (["jason-capa"], ["jason-carro"])
    # a capa já está no pacote: o recorte da mesma arte dá lugar a outro
    assert authoring._pick_characters(["jason-capa"], CUTS, None, V.Usage(), [], avoid_keyart=True) == ["jason-carro"]


def test_capa_nao_entra_com_recorte_da_mesma_arte():
    frames = [{"id": "capa-oficial", "tags": ["keyart", "capa", "claro"]},
              {"id": "capa-video", "tags": ["keyart", "capa", "claro"]},
              {"id": "rua", "tags": ["lugar", "rua", "claro"]}, {"id": "neon", "tags": ["lugar", "neon"]}]
    pick, note = authoring._pick_frame(frames[0], frames, "avatar-reage", 0, None, V.Usage(), False,
                                       avoid_keyart=True)
    assert pick["id"] == "rua" and "mesma arte" in note
    # capa repetida nas recentes: não troca por outra capa (a arte é a mesma), e sim por um quadro claro
    recent = V.Usage()
    recent.add({"background": ["capa-oficial"]}, 0.8)
    pick, _ = authoring._pick_frame(frames[0], frames, "avatar-reage", 0, recent, V.Usage(), False)
    assert pick["id"] == "rua"
    usage = V.Usage()
    usage.add({"background": ["capa-oficial"]})
    assert authoring._uses_keyart(usage, {f["id"]: f for f in frames}, "background")


def test_pose_vista_troca_e_sem_outra_da_mesma_emocao_troca_a_emocao():
    poses = [{"id": "surpreso", "emotions": ["choque"]}, {"id": "chocado", "emotions": ["choque"]},
             {"id": "lupa", "emotions": ["detalhe"]}]
    recent = V.Usage()
    recent.add({"pose": ["surpreso"]}, 0.8)
    assert authoring._pick_pose("choque", poses, set(), recent) == "chocado"
    recent.add({"pose": ["chocado"]}, 0.8)
    assert authoring._pick_pose("choque", poses, set(), recent) == "lupa"


def test_cor_do_mesmo_matiz_do_fundo_perde():
    sky = (70, 150, 235)
    assert V.same_hue(V.color_rgb("cyan"), sky)
    assert V.pick_fill(sky, None, None) != "cyan"
    fixed = tasks._better_fill(_old(fill="cyan"), sky, None, None)
    assert fixed is not None and fixed != "cyan"
    # a paleta do estilo vale: o "amarelo" escuro do estilo é medido com a cor que o compositor desenha
    dark = {"yellow": "#403000"}
    assert tasks._better_fill(_old(fill="yellow"), (30, 25, 20), None, None, dark) is not None
    assert tasks._better_fill(_old(fill="yellow"), (30, 25, 20), None, None) is None


def test_um_cartao_de_captura_por_pacote_e_objeto_so_com_objeto():
    proofs = [{"id": "p1", "file": "uploads/x/a.jpg", "kind": "captura", "desc": "a", "relevance": 2},
              {"id": "p2", "file": "uploads/x/b.jpg", "kind": "captura", "desc": "b", "relevance": 1}]
    assets = authoring.PackageAssets(frames=FRAMES, proofs=proofs, allow_editorial=True)
    raw = _raw(3, template="prova", pose=None)
    raw["candidates"][1]["background_hint"] = "p2"
    raw["candidates"][2]["template"] = "objeto"
    cands = authoring._normalize_candidates(raw["candidates"], assets=assets,
                                            templates=["heroi-palavra", "prova", "objeto"],
                                            is_long=True)
    templates = [c.layers.template for c in cands]
    assert templates.count("prova") == 1 and "objeto" not in templates
    prova = next(c for c in cands if c.layers.template == "prova")
    assert prova.layers.backdrop in {f["id"] for f in FRAMES}  # um quadro do jogo atrás do print
    assert any("já há uma prova" in n for c in cands for n in c.score.get("notes", []))
    assert "objeto" not in authoring.thumb_templates_for(get_style(LONG), assets)


def test_direita_fora_dos_templates_que_espelham_nao_espelha(tmp_path):
    from studio.media.thumb_assets import AssetResolver

    Image.new("RGB", (1200, 700), (40, 160, 90)).save(tmp_path / "cap.jpg")
    r = AssetResolver(None, media=lambda ref: tmp_path / "cap.jpg")
    prova = ThumbLayers.model_validate({"template": "prova", "logo": "none",
                                        "background": {"kind": "media", "ref": "media:cap"},
                                        "text": {"words": "FALSO", "fill": "red", "pos": "right"}})
    assert not C._mirrored(prova)
    card = next(f for f in C.render(prova, T.CANVAS, r).info["layout"]["figures"] if f["kind"] == "card")
    assert card["bbox"][0] > T.CANVAS[0] * 0.35  # o cartão continua à direita


def test_prova_ja_gerada_some_quando_o_canal_deixa_de_liberar():
    from studio.workers.visual import thumbs as visual_thumbs

    doc = service.create_project(title="Prova", style_id=LONG)
    media = get_settings().uploads_dir / doc.id / "media"
    media.mkdir(parents=True)
    Image.new("RGB", (800, 450), (250, 250, 250)).save(media / "cap.jpg")
    rel = f"uploads/{doc.id}/media/cap.jpg"
    save_pool(doc.id, Pool(items=[PoolItem(id="p1", tier="capture", media="capture", path=rel, desc="golpe",
                                           editorial=True)]))
    assert visual_thumbs._media_finder(doc, None, allow=True)(rel) is not None
    assert visual_thumbs._media_finder(doc, None, allow=False)(rel) is None
