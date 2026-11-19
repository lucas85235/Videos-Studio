"""Rodada 4 do QA (GTA 6 no PC): fundo da thumb sem figurante como assunto, cartão da prova com círculo justo na
manchete, cartela (logo/ESRB no preto) fora do acervo usável e o gancho que responde a pergunta da thumb."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from studio.core.project.document import ThumbLayers
from studio.core.project.pool import PoolItem
from studio.media import thumb_assets as A
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.media import thumb_variety as V
from studio.workers.pipeline import authoring, media_pool
from studio.workers.pipeline import storyboard_check as sc


# --- fundo de lugar com figurante ------------------------------------------------------------------------------------
def _beach(path: Path) -> Path:
    """Quadro de praia 16:9: céu e prédios em cima, areia embaixo, três "corpos" grandes na frente à esquerda."""
    im = Image.new("RGB", (1600, 900), (120, 180, 230))
    d = ImageDraw.Draw(im)
    for k in range(8):
        d.rectangle((150 + k * 170, 260 - (k % 3) * 60, 260 + k * 170, 480), fill=(200, 200 - k * 10, 170))
    d.rectangle((0, 480, 1600, 900), fill=(235, 220, 190))
    for x in (60, 220, 380):
        d.ellipse((x, 430, x + 120, 900), fill=(150, 100, 80))
    im.save(path)
    return path


def _people(size: tuple[int, int]) -> Image.Image:
    """A máscara de gente do `_beach` (o que o rembg acharia), no tamanho `size`."""
    m = Image.new("L", (1600, 900), 0)
    d = ImageDraw.Draw(m)
    for x in (60, 220, 380):
        d.ellipse((x, 430, x + 120, 900), fill=255)
    return m.resize(size)


def _kit(tmp_path: Path, *, face: bool = False, tags: tuple[str, ...] = ("lugar", "praia")) -> A.AssetResolver:
    kit_dir = tmp_path / "kit"
    (kit_dir / "frames").mkdir(parents=True)
    (kit_dir / "cutouts").mkdir()
    _beach(kit_dir / "frames" / "praia.png")
    manifest = A.empty_manifest("chn_r4")
    A.upsert_item(manifest, {"id": "praia", "kind": "frame", "file": "frames/praia.png", "tags": list(tags),
                             "editorial": False, "focus_x": 0.5, "focus_y": 0.5,
                             "face_box": [0.05, 0.5, 0.3, 0.9] if face else None, "size": [1600, 900]})
    fig = Image.new("RGBA", (500, 800), (0, 0, 0, 0))
    ImageDraw.Draw(fig).rectangle((40, 60, 460, 800), fill=(30, 30, 40, 255))
    fig.save(kit_dir / "cutouts" / "heroi.png")
    A.upsert_item(manifest, {"id": "heroi", "kind": "cutout", "file": "cutouts/heroi.png", "tags": ["personagem"],
                             "editorial": False, "face_box": [0.2, 0.05, 0.8, 0.4]})
    A.save_manifest(kit_dir, manifest)
    r = A.AssetResolver(kit_dir, allow_editorial=True, detect_people=True)
    r.people_for = lambda asset: _people((640, 360))  # type: ignore[method-assign]
    return r


def _hero(zoom: float = 1.0) -> ThumbLayers:
    return ThumbLayers.model_validate({
        "template": "heroi-palavra", "logo": "none",
        "background": {"kind": "frame", "ref": "frame:praia", "zoom": zoom, "focus_x": 0.5},
        "characters": [{"asset": "heroi", "x": 0.72, "y": 1.0, "h": 0.95}],
        "text": {"words": "ESPERA?", "fill": "white", "pos": "top"}})


def test_fundo_de_lugar_reenquadra_para_figurante_nao_virar_assunto(tmp_path):
    """O quadro de lugar com corpos grandes na frente, na parte que a figura não tampa: o compositor troca a janela
    pela de menos gente (o cenário de cima e da direita) e anota; a gente que sobra fica abaixo do limite."""
    r = _kit(tmp_path)
    for zoom in (1.0, 1.5):
        out = C.render(_hero(zoom), T.CANVAS, r)
        lay = out.info["layout"]
        assert lay["bg_people"] is not None and lay["bg_people"] <= C.PEOPLE_FREE_MAX
        assert any("figurante" in n for n in lay["notes"])
        assert not any("figurantes em close" in w for w in lay["warnings"])
        # a metade esquerda da thumb (fora do herói) não é a mancha marrom dos corpos
        left = out.crop((0, T.CANVAS[1] // 2, T.CANVAS[0] * 2 // 5, T.CANVAS[1])).resize((40, 20))
        brown = sum(1 for p in left.getdata() if abs(p[0] - 150) < 30 and abs(p[1] - 100) < 30 and p[2] < 110)
        assert brown < 40 * 20 * 0.1


def test_fundo_sem_medida_ou_de_personagem_fica_como_pedido(tmp_path):
    """Sem a medida de gente (rembg ausente/desligado) o enquadramento é o de sempre; quadro com rosto marcado (a
    pessoa é o assunto) também não é re-enquadrado."""
    r = _kit(tmp_path)
    r.detect_people = False
    r.people_for = lambda asset: None  # type: ignore[method-assign]
    lay = C.render(_hero(), T.CANVAS, r).info["layout"]
    assert lay["bg_people"] is None and not [n for n in lay["notes"] if n.startswith("fundo")]
    r2 = _kit(tmp_path / "b", face=True)
    lay2 = C.render(_hero(), T.CANVAS, r2).info["layout"]
    assert lay2["bg_people"] is None and not [n for n in lay2["notes"] if n.startswith("fundo")]


def test_uma_pessoa_so_e_o_assunto_e_nao_e_reenquadrada(tmp_path):
    """Uma mancha só na máscara (o homem no telhado, ou o jet ski que a medida confundiu com gente) não é figurante:
    o quadro fica no enquadramento pedido."""
    r = _kit(tmp_path)
    one = Image.new("L", (640, 360), 0)
    ImageDraw.Draw(one).ellipse((20, 150, 200, 360), fill=255)
    r.people_for = lambda asset: one  # type: ignore[method-assign]
    assert C.people_count(one) == 1 and C.people_count(_people((640, 360))) == 3
    lay = C.render(_hero(), T.CANVAS, r).info["layout"]
    assert lay["bg_people"] is None and not [n for n in lay["notes"] if n.startswith("fundo")]


def test_so_quadro_marcado_como_lugar_e_reenquadrado(tmp_path):
    """Capa/keyart (o elenco e o logo são o assunto) e quadro sem a tag `lugar` (a dupla, a banda, os apresentadores
    de outro canal, sem rosto marcado) ficam no enquadramento pedido e sem aviso de figurante, mesmo com duas ou mais
    pessoas na medida; o gerador do pacote segue a mesma regra (`crowded`)."""
    for k, tags in enumerate((("keyart", "capa", "claro"), ("lugar", "capa"), ("dupla", "dia"), ())):
        r = _kit(tmp_path / str(k), tags=tags)
        lay = C.render(_hero(), T.CANVAS, r).info["layout"]
        assert lay["bg_people"] is None and not [n for n in lay["notes"] if n.startswith("fundo")], tags
        assert not any("figurante" in w for w in lay["warnings"]), tags
        assert not C.scenery_frame({"id": "x", "tags": list(tags)})
    assert C.scenery_frame({"id": "x", "tags": ["lugar", "praia"]})
    assert not C.scenery_frame({"id": "x", "tags": ["lugar"], "face_box": [0.1, 0.1, 0.3, 0.5]})


def test_people_window_mantem_a_janela_pedida_quando_ja_serve():
    people = Image.new("L", (640, 360), 0)
    box, before, after = C._people_window(people, (1600, 900), T.CANVAS, 1.2, (0.5, 0.5), (0.5, 0.42),
                                          C._open_weights(T.CANVAS, None))
    assert before == after == 0.0
    assert box == C._cover_box((1600, 900), T.CANVAS, 1.2, (0.5, 0.5), (0.5, 0.42))


def test_people_mask_sem_rembg(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "rembg_available", lambda: False)
    _beach(tmp_path / "q.png")
    assert A.people_mask(tmp_path / "q.png") is None


def test_scenery_people_so_acusa_o_que_o_reenquadramento_nao_tira():
    """Corpos só num canto: o re-enquadramento acha cenário livre (não é multidão). Gente na largura toda da
    areia, do meio para baixo: nem re-enquadrando sai (o gerador troca o quadro)."""
    assert C.scenery_people(_people((640, 360)), (1600, 900)) <= C.PEOPLE_FREE_MAX
    assert C.scenery_people(_crowd((640, 360)), (1600, 900)) > C.PEOPLE_FREE_MAX


def _crowd(size: tuple[int, int]) -> Image.Image:
    m = Image.new("L", (1600, 900), 0)
    d = ImageDraw.Draw(m)
    for x in range(0, 1600, 70):
        for y in (250, 450, 650):
            d.ellipse((x, y, x + 60, y + 260), fill=255)
    return m.resize(size)


def test_heroi_palavra_so_aproxima_quadro_de_personagem():
    """O zoom de 1,5× do heroi-palavra é para o rosto do quadro (sem recorte) ir para o terço direito; o quadro de
    lugar fica aberto (o zoom punha os figurantes da frente em close)."""
    lugar = {"id": "t1-praia", "file": "frames/x.jpg", "focus_x": 0.5, "face_box": None, "tags": ["lugar"]}
    rosto = {**lugar, "id": "t1-lucia", "face_box": [0.4, 0.1, 0.6, 0.5]}
    assert authoring._frame_layer(lugar, "heroi-palavra")["zoom"] == 1.0
    assert authoring._frame_layer(rosto, "heroi-palavra")["zoom"] == 1.5


def test_pick_frame_troca_quadro_com_figurante_em_close():
    frames = [{"id": "praia", "tags": ["lugar", "praia"]}, {"id": "praia-aerea", "tags": ["lugar", "praia", "aereo"]},
              {"id": "neon", "tags": ["lugar", "neon"]}]
    crowded = {"praia"}
    pick, note = authoring._pick_frame(frames[0], frames, "heroi-palavra", 0, None, V.Usage(), False,
                                       has_figure=True, crowded=lambda f: f["id"] in crowded)
    assert pick["id"] == "praia-aerea" and "figurantes em close" in note
    # sem outro do mesmo assunto, fica (o compositor re-enquadra)
    pick, note = authoring._pick_frame(frames[2], frames, "heroi-palavra", 0, None, V.Usage(), False,
                                       has_figure=True, crowded=lambda f: True)
    assert pick["id"] == "neon" and note is None


def test_package_assets_crowded_mede_so_quadro_de_lugar(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_mask(path, cache_dir=None):
        calls.append(Path(path).name)
        return _crowd((160, 90))

    monkeypatch.setattr(A, "people_mask", fake_mask)
    _beach(tmp_path / "frames.png")
    assets = authoring.PackageAssets(kit_dir=tmp_path)
    lugar = {"id": "praia", "file": "frames.png", "tags": ["lugar"], "face_box": None}
    assert assets.crowded(lugar) is True and assets.crowded(lugar) is True
    assert calls == ["frames.png"]  # medido uma vez
    assert assets.crowded({**lugar, "id": "p2", "tags": ["personagem"]}) is False
    assert assets.crowded({**lugar, "id": "p3", "tags": ["keyart", "capa"]}) is False
    assert assets.crowded({**lugar, "id": "p4", "tags": ["dupla"]}) is False  # sem a tag lugar
    assert authoring.PackageAssets().crowded(lugar) is False  # sem kit


# --- prova com círculo -----------------------------------------------------------------------------------------------
def _page() -> Image.Image:
    """Matéria: sobretítulo, manchete de 4 linhas, linha do autor e a foto embaixo (como a da Malwarebytes)."""
    img = Image.new("RGB", (1400, 1000), (250, 250, 250))
    draw = ImageDraw.Draw(img)
    for x in range(100, 560, 14):  # o sobretítulo (categoria)
        draw.rectangle((x, 120, x + 7, 135), fill=(20, 60, 230))
    for i, (top, right) in enumerate(((170, 900), (240, 850), (310, 620), (380, 450))):
        for k, x in enumerate(range(100, right, 22)):
            draw.rectangle((x, top + 18, x + 12, top + 58), fill=(10, 80, 250))
            if k % 4 == 0:
                draw.rectangle((x, top, x + 5, top + 18), fill=(10, 80, 250))
            if k % 5 == 2 and i < 3:
                draw.rectangle((x + 6, top + 58, x + 11, top + 72), fill=(10, 80, 250))
    for x in range(100, 520, 11):  # linha do autor e data
        draw.rectangle((x, 500, x + 6, 512), fill=(40, 40, 40))
    for y in range(560, 980, 4):  # a foto da matéria
        draw.rectangle((100, y, 900, y + 3), fill=(60 + (y % 40), 40, 90 + (y % 60)))
    return img


def test_prova_com_circulo_o_cartao_acaba_na_manchete(tmp_path):
    """Com o círculo em volta da manchete, o cartão (na mesma escala) acaba logo depois dela: a linha do autor e a
    foto da matéria não sobram cortadas pelo traço. Com o X, o cartão continua com o pedaço de baixo (lugar do X)."""
    _page().save(tmp_path / "cap.jpg")
    r = A.AssetResolver(None, media=lambda ref: tmp_path / "cap.jpg")

    def card(mark: str) -> dict:
        layers = ThumbLayers.model_validate({"template": "prova", "logo": "none", "mark": mark,
                                             "background": {"kind": "media", "ref": "media:cap"},
                                             "text": {"words": "SENHAS", "fill": "white", "pos": "left"}})
        return next(f for f in C.render(layers, T.CANVAS, r).info["layout"]["figures"] if f["kind"] == "card")

    circ, x = card("circle"), card("x")
    hh = circ["headline"][3] - circ["headline"][1]
    assert circ["bbox"][3] - circ["headline"][3] < hh * 0.6  # o pé do cartão logo depois da manchete
    assert x["bbox"][3] - x["bbox"][1] > circ["bbox"][3] - circ["bbox"][1]
    assert abs((circ["bbox"][2] - circ["bbox"][0]) - (x["bbox"][2] - x["bbox"][0])) < 40  # mesma largura/escala


# --- cartela -----------------------------------------------------------------------------------------------------------
def _poster(path: Path, *, dark: bool) -> Path:
    im = Image.new("RGB", (640, 360), (0, 0, 0) if dark else (120, 160, 200))
    ImageDraw.Draw(im).rectangle((250, 100, 390, 260), fill=(200, 60, 220))  # o "VI" em neon
    im.save(path)
    return path


def _clip(pid: str, poster: Path, desc: str, tier: str = "official") -> PoolItem:
    from studio.config import get_settings

    rel = poster.relative_to(get_settings().data_dir).as_posix()
    return PoolItem(id=pid, tier=tier, media="clip", desc=desc, poster=rel, path=rel)


def test_cartela_no_preto_nao_serve_de_imagem():
    from studio.config import get_settings

    tmp_path = get_settings().data_dir / "cartela"
    tmp_path.mkdir(parents=True, exist_ok=True)
    dark = _poster(tmp_path / "dark.jpg", dark=True)
    bright = _poster(tmp_path / "bright.jpg", dark=False)
    black = tmp_path / "black.jpg"
    Image.new("RGB", (640, 360), (0, 0, 0)).save(black)
    logo = _clip("p1", dark, 'Trailer 1 oficial · trailer · texto "VI"')
    esrb = _clip("p2", dark, 'Trailer 2 · trailer · texto "MAY CONTAIN CONTENT INAPPROPRIATE FOR CH"')
    data = _clip("p3", dark, 'Trailer 2 · trailer · texto "COMING MAY 26 2026"')
    scene = _clip("p4", bright, 'Trailer 1 · trailer · texto "VICE"')
    night = _clip("p5", dark, "Trailer 1 · trailer")
    fade = _clip("p6", black, "Trailer 1 · trailer")
    own = _clip("p7", dark, 'meu vídeo · texto "VI"', tier="own")
    assert media_pool.is_title_card(logo) and media_pool.is_title_card(esrb) and media_pool.is_title_card(fade)
    assert not media_pool.is_title_card(data)  # cartela com data é o dado
    assert not media_pool.is_title_card(scene) and not media_pool.is_title_card(night)
    assert not media_pool.is_title_card(own)
    assert not authoring.usable_item(logo) and authoring.usable_item(scene)
    assert not media_pool.is_title_card(_clip("p8", tmp_path / "sumiu.jpg", 'texto "VI"'))


# --- gancho que responde a pergunta --------------------------------------------------------------------------------
def _doc(hook: str, *, thumb: str = "ESPERA?", promise: str = "A data do PC (1º tri de 2027 segundo a GamesBeat).",
         title: str = "GTA 6 no PC: quando sai?"):
    from studio.core.project.document import PackageCandidate, PackageDoc, ProjectDoc, Segment

    cand = PackageCandidate(title=title, thumb_text=thumb, promise=promise)
    return ProjectDoc(id="prj_r4", title=title, style_id="curiosidades-longo", segments=[
        Segment(role="hook", text={"pt-BR": hook}),
        Segment(role="body", text={"pt-BR": "E o resto do vídeo vem aqui com mais detalhes. " * 6})],
        package=PackageDoc(candidates=[cand], chosen_id=cand.id, locked=True))


def _style():
    from studio.core.styles.library import get_style

    return get_style("curiosidades-longo")


def test_gancho_que_nao_responde_a_pergunta_da_thumb_vira_aviso():
    st = _style()
    vago = _doc("Se você achou um GTA 6 pra PC pra baixar hoje, não baixa. É golpe. Daqui a pouco eu te mostro "
                "quando o GTA 6 de verdade deve chegar no PC.")
    issues = sc._check_hook_answer(vago, st)
    assert [i.code for i in issues] == ["gancho_sem_resposta"] and "2027" in issues[0].message
    for fala in ("O GTA 6 de PC deve sair só em 2027, e ainda é boato. E não baixa o que tem por aí.",
                 "O GTA 6 de PC deve sair só em dois mil e vinte e sete, e ainda é boato."):
        assert sc._check_hook_answer(_doc(fala), st) == []
    # sem pergunta na thumb nem no título, ou sem dado na promessa: nada a cobrar
    assert sc._check_hook_answer(_doc(vago.segments[0].text["pt-BR"], thumb="GOLPE", title="GTA 6 no PC"), st) == []
    assert sc._check_hook_answer(_doc("Daqui a pouco eu te mostro.", promise="Você entende o golpe."), st) == []


def test_pedido_do_roteiro_manda_responder_a_pergunta_no_gancho():
    st = _style()
    block = authoring.locked_package_block(_doc("x"), st)
    assert "pergunta" in block and "resposta curta" in block
    sem = authoring.locked_package_block(_doc("x", thumb="GOLPE", title="GTA 6 no PC"), st)
    assert "pergunta" not in sem
