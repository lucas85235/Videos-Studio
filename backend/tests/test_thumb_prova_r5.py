"""Rodada 5 do QA (GTA 6 no PC): o template prova reprojetado. A manchete inteira num cartão virava, a 168 px, um
retângulo branco com riscos azuis; agora a prova é o recorte de 2-3 palavras da própria manchete (pixels da matéria)
em corpo grande, com marca-texto na palavra do perigo, o logo do jogo atrás do avatar, o avatar apontando para a
prova e o texto com folga do recorte das figuras (o "?" de "ESPERA?" encostava no cabelo do Jason)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from studio.core.project.document import ThumbLayers
from studio.media import thumb_assets as A
from studio.media import thumb_clip as K
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.media.thumbnail import load_font
from studio.workers.pipeline import authoring

BLUE = (10, 80, 250)


def _page(lines: list[str], size: tuple[int, int] = (1500, 850), *, font_px: int = 70,
          ink: tuple[int, int, int] = BLUE, bg: tuple[int, int, int] = (252, 252, 252)) -> tuple[Image.Image, list]:
    """Uma matéria capturada: a manchete em `lines` (uma por linha) e a caixa de cada linha em fração (os `rects` que
    a captura mede no DOM), mais uma foto ao lado."""
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    font = load_font("Anton", font_px)
    rects = []
    y = 160
    for line in lines:
        x0, y0, x1, y1 = d.textbbox((40, y), line, font=font)
        d.text((40, y), line, font=font, fill=ink)
        rects.append([round((x0 - 4) / size[0], 4), round((y0 - 10) / size[1], 4), round((x1 - x0 + 8) / size[0], 4),
                      round((y1 - y0 + 20) / size[1], 4)])
        y += round(font_px * 1.35)
    d.rectangle((size[0] - 520, 80, size[0] - 20, 760), fill=(170, 60, 200))  # a foto da matéria
    return img, rects


HEADLINE = ["Sites falsos com o visual", "estendido e demos do", "GTA 6 roubam", "informações."]


def test_recorte_acha_as_palavras_de_cada_linha_e_escolhe_jogo_mais_perigo():
    img, rects = _page(HEADLINE)
    meta = {"desc": " ".join(HEADLINE) + ' — "Malwarebytes – Sites falsos"', "rects": rects}
    lines = K.headline_lines(img, K.headline_text(meta["desc"]), rects)
    assert lines is not None and [ln.words for ln in lines] == [h.split() for h in HEADLINE]
    clip, info = K.headline_clip(img, meta)
    # "GTA 6 roubam" diria que o jogo rouba: quem rouba são os sites falsos (o jogo vem depois de "demos do")
    assert info["text"] == "Sites falsos" and info["marked"] == ["falsos"]
    # o recorte é só a linha (com folga de papel), não a página: baixo e largo
    assert clip.width > 2.5 * clip.height
    # marca-texto amarelo atrás de "roubam" e a letra continua azul (não vira verde do amarelo multiplicado)
    px = list(clip.getdata())
    assert sum(1 for p in px if p[0] > 230 and p[1] > 200 and p[2] < 60) > len(px) * 0.05
    assert sum(1 for p in px if p[2] > 200 and p[0] < 60) > len(px) * 0.05


def test_desc_cortado_no_fim_ainda_casa_as_linhas():
    """O `desc` do acervo guarda ~70 caracteres: a última palavra chega cortada ("informaçõe")."""
    img, rects = _page(HEADLINE)
    lines = K.headline_lines(img, "Sites falsos com o visual estendido e demos do GTA 6 roubam informaçõe", rects)
    assert lines is not None and lines[2].words == ["GTA", "6", "roubam"]


def test_texto_que_nao_casa_com_a_tinta_nao_vira_recorte():
    """Contagem de palavras que não fecha com os blocos de tinta (texto de outra coisa): fica o cartão da página."""
    img, rects = _page(HEADLINE)
    meta = {"desc": "Um momento enquanto verificamos se você é humano e não um robô de verdade mesmo", "rects": rects}
    assert K.headline_clip(img, meta) is None
    assert K.headline_clip(img, {"desc": "GTA 6 roubam", "rects": []}) is None
    assert K.headline_clip(img, None) is None


def test_recorte_nao_corta_nome_ao_meio_nem_comeca_em_palavra_vazia():
    lines = [K.Line(box=(0, 0, 10, 10), spans=[(0, 1)] * 9, bg=(255, 255, 255),
                    words="Grand Theft Auto 6 will be playable on Xbox".split())]
    choice = K.pick(lines)
    assert choice is None or "Theft" not in choice.words(lines) and choice.words(lines)[0] not in ("on", "be")
    lines = [K.Line(box=(0, 0, 10, 10), spans=[(0, 1)] * 10, bg=(255, 255, 255),
                    words="Cuidado! Falso GTA 6 para PC espalha vírus e pode".split())]
    assert K.pick(lines).words(lines) == ["Falso", "GTA", "6"]


def _line(text: str) -> list[K.Line]:
    words = text.split()
    return [K.Line(box=(0, 0, 10, 10), spans=[(0, 1)] * len(words), bg=(255, 255, 255), words=words)]


def _picked(text: str, **kw) -> str | None:
    lines = _line(text)
    choice = K.pick(lines, **kw)
    return None if choice is None else " ".join(choice.words(lines))


def test_recorte_nao_inverte_nem_troca_o_sujeito_da_manchete():
    """O marca-texto nos pixels da matéria faz o recorte parecer o que ela diz: ele nunca pode dizer o contrário
    (manchete que nega ou desmente) nem pôr o jogo como quem faz o que os sites falsos fazem."""
    # quem rouba são os sites (o jogo vem depois de "demos do")
    assert _picked("Sites falsos com demos do GTA 6 roubam informações") == "Sites falsos"
    # manchete que desmente: nenhum trecho
    assert _picked("Rockstar nega que GTA 6 tenha vírus no PC") is None
    assert _picked("Mito: falso GTA 6 rouba senhas") is None
    assert _picked("Rockstar denies GTA 6 PC malware") is None
    # negação na oração: o trecho dela perde ("golpe: iPhone 17" diria o contrário)
    got = _picked("Não é golpe: iPhone 17 chega ao Brasil")
    assert got is None or "golpe" not in got.lower()
    assert _picked("Download de GTA 6 para PC não é golpe") is None
    # o trecho não começa no objeto de uma preposição ("golpe do PIX" depois de "alerta para")
    assert _picked("Banco Central alerta para golpe do PIX") != "golpe do PIX"
    # o "não" imperativo é o aviso, não nega; depois de artigo o trecho vale
    assert _picked("Não baixe o falso GTA 6 para PC") == "falso GTA 6"


def test_manchete_cortada_nao_recorta_a_ultima_palavra_conhecida():
    """O acervo guarda 70 caracteres da manchete: o resto (que pode negar) não se vê."""
    assert K.headline_truncated("x" * 70 + ' — "site"') and not K.headline_truncated('GTA 6 vem aí — "site"')
    assert not K.faithful(["Falso", "GTA", "6"], 0, 2, truncated=True)
    assert K.faithful(["Falso", "GTA", "6", "rouba"], 0, 2, truncated=True)
    assert _picked("Cuidado com o falso GTA 6", truncated=True) != "falso GTA 6"


def test_manchete_sem_perigo_nem_nome_nao_vira_recorte():
    lines = [K.Line(box=(0, 0, 10, 10), spans=[(0, 1)] * 5, bg=(255, 255, 255),
                    words="o que muda na sua vida".split())]
    assert K.pick(lines) is None


# --- compositor --------------------------------------------------------------------------------------------------------
def _resolver(tmp_path: Path, *, meta: dict | None, vi: bool = True) -> A.AssetResolver:
    img, rects = _page(HEADLINE)
    img.save(tmp_path / "cap.jpg")
    kit_dir = tmp_path / "kit"
    (kit_dir / "logos").mkdir(parents=True)
    manifest = A.empty_manifest("chn_r5")
    if vi:
        logo = Image.new("RGBA", (300, 360), (0, 0, 0, 0))
        ImageDraw.Draw(logo).polygon([(0, 0), (120, 0), (150, 260), (180, 0), (300, 0), (150, 360)],
                                     fill=(255, 60, 160, 255))
        logo.save(kit_dir / "logos" / "vi.png")
        A.upsert_item(manifest, {"id": "vi", "kind": "logo", "file": "logos/vi.png", "tags": ["vi"],
                                 "editorial": False})
    A.save_manifest(kit_dir, manifest)
    r = A.AssetResolver(kit_dir, media=lambda ref: tmp_path / "cap.jpg", allow_editorial=True)
    full = {"desc": " ".join(HEADLINE) + ' — "x"', "rects": rects}
    r.media_meta = (lambda ref: full) if meta is None else (lambda ref: meta)
    return r


def _prova(**over) -> ThumbLayers:
    return ThumbLayers.model_validate({"template": "prova", "mark": "x", "logo": "corner",
                                       "background": {"kind": "media", "ref": "media:cap"},
                                       "text": {"words": "SENHAS", "fill": "white", "pos": "left"}, **over})


def test_prova_com_manchete_no_acervo_vira_recorte_grande_sem_x(tmp_path):
    r = _resolver(tmp_path, meta=None)
    img = C.render(_prova(), T.CANVAS, r)
    lay = img.info["layout"]
    card = next(f for f in lay["figures"] if f["kind"] == "card")
    assert card["clip"] == "Sites falsos" and card["marked"] == ["falsos"]
    x0, y0, x1, y1 = card["bbox"]
    # grande: a linha recortada ocupa boa parte da coluna do texto, embaixo da palavra
    assert x1 - x0 > T.CANVAS[0] * 0.35 and y0 > lay["text_bbox"][3]
    # nenhum X vermelho em cima do recorte (o marca-texto já marca)
    red = sum(1 for p in img.crop(tuple(card["bbox"])).getdata() if p[0] > 200 and p[1] < 60 and p[2] < 80)
    assert red < (x1 - x0) * (y1 - y0) * 0.01
    # a marca do jogo: o VI atrás (o logo pequeno sai)
    assert lay["warnings"] == [] or all("logo omitido" in w for w in lay["warnings"])
    assert any(a == "logo:vi" for a in lay["assets"])


def test_prova_sem_manchete_casada_fica_com_o_cartao_da_pagina(tmp_path):
    r = _resolver(tmp_path, meta={"desc": "texto que não é o da imagem de jeito nenhum", "rects": [[0, 0, 1, 1]]})
    lay = C.render(_prova(), T.CANVAS, r).info["layout"]
    card = next(f for f in lay["figures"] if f["kind"] == "card")
    assert "clip" not in card


def test_prova_sem_logo_vi_no_kit_ou_sem_logo_nao_inventa_vi(tmp_path):
    r = _resolver(tmp_path, meta=None, vi=False)
    img = C.render(_prova(), T.CANVAS, r)
    assert not any(a.startswith("logo:") for a in img.info["layout"]["assets"])
    # nenhum "VI" de fonte no lugar do logo: a metade direita (sem avatar) é só o fundo
    (tmp_path / "b").mkdir()
    r2 = _resolver(tmp_path / "b", meta=None)
    lay2 = C.render(_prova(logo="none"), T.CANVAS, r2).info["layout"]
    assert not any(a == "logo:vi" for a in lay2["assets"])


def test_prova_sem_logo_vi_usa_o_logo_do_canal_no_canto(tmp_path):
    """Kit de outro jogo (sem logo VI): a marca do jogo vem do logo do canal, no canto de cima."""
    r = _resolver(tmp_path, meta=None, vi=False)
    manifest = A.load_manifest(tmp_path / "kit")
    Image.new("RGBA", (400, 160), (240, 200, 30, 255)).save(tmp_path / "kit" / "logos" / "lockup.png")
    A.upsert_item(manifest, {"id": "lockup", "kind": "logo", "file": "logos/lockup.png", "tags": ["lockup"],
                             "editorial": False})
    A.save_manifest(tmp_path / "kit", manifest)
    r = A.AssetResolver(tmp_path / "kit", media=r.media, allow_editorial=True)
    lay = C.render(_prova(), T.CANVAS, r).info["layout"]
    assert "logo:lockup" in lay["assets"] and any(f["kind"] == "logo" for f in lay["figures"]), lay["warnings"]
    assert T.default_layers("prova", {}).logo == "corner"


def test_recorte_para_antes_do_avatar(tmp_path):
    """O papel nunca entra embaixo do avatar (ou da mão que aponta para ele): fica uma folga."""
    r = _resolver(tmp_path, meta=None)
    canvas = Image.new("RGBA", T.CANVAS, (40, 20, 60, 255))
    occ = Image.new("L", T.CANVAS, 0)
    ImageDraw.Draw(occ).rectangle((520, 0, 1280, 720), fill=255)
    layout = C.Layout(T.CANVAS, "prova")
    clip = K.headline_clip(Image.open(tmp_path / "cap.jpg").convert("RGB"), r.media_meta("cap"))
    box = C._paste_clip(canvas, clip, T.spec("prova")["clip"], C._palette(None), layout, "media:cap", False, occ)
    assert box[2] <= 520 - C._px(20, 720)


# --- texto com folga da figura -----------------------------------------------------------------------------------------
def _text_layers(words: str = "ESPERA?") -> ThumbLayers:
    return ThumbLayers.model_validate({"template": "heroi-palavra", "text": {"words": words, "fill": "white",
                                                                              "pos": "top"}})


def _draw_text(avoid: Image.Image | None) -> C.Layout:
    layout = C.Layout(T.CANVAS, "heroi-palavra")
    C._text(Image.new("RGBA", T.CANVAS, (90, 90, 90, 255)), _text_layers(), C._palette(None), layout,
            T.spec("heroi-palavra"), avoid)
    return layout


def test_texto_que_encosta_na_figura_encolhe_ate_ter_folga():
    free = _draw_text(None).text_bbox
    avoid = Image.new("L", T.CANVAS, 0)
    ImageDraw.Draw(avoid).ellipse((free[2] - 60, 30, free[2] + 500, 900), fill=255)  # o cabelo encosta no "?"
    lay = _draw_text(avoid)
    assert lay.text_bbox[2] < free[2] and any("menor" in n for n in lay.notes)
    # a folga: o miolo das letras fica a mais de ~20 px (a 720p) do recorte (o contorno escuro fica no meio)
    grown = avoid.filter(ImageFilter.MaxFilter(2 * 20 + 1))
    hit = ImageChops.multiply(lay.text_mask.point(lambda v: 255 if v > 128 else 0), grown)
    assert hit.getbbox() is None


def test_texto_por_cima_da_figura_de_proposito_fica_do_tamanho():
    free = _draw_text(None).text_bbox
    avoid = Image.new("L", T.CANVAS, 0)
    ImageDraw.Draw(avoid).rectangle((0, 0, 1280, 720), fill=255)  # a figura enche a thumb
    lay = _draw_text(avoid)
    assert lay.text_bbox == free and not lay.notes


# --- pacote ------------------------------------------------------------------------------------------------------------
POSES = [
    {"id": "surpreso", "emotions": ["choque", "surpresa"], "aliases": ["espantado"], "facing": "center"},
    {"id": "apontando", "emotions": ["explicar", "mostrar a prova"], "aliases": ["explicando"], "facing": "right"},
    {"id": "mao-na-testa", "emotions": ["vergonha alheia", "erro"], "aliases": ["vergonha"], "facing": "center"},
]


def test_prova_troca_o_choque_generico_pela_pose_que_aponta_para_a_prova():
    notes: list[str] = []
    assert authoring._proof_pose("surpreso", POSES, set(), notes) == "apontando" and notes
    assert authoring._proof_pose("choque", POSES, set(), []) == "apontando"
    assert authoring._proof_pose(None, POSES, set(), []) == "apontando"
    # emoção específica citada fica; sem pose que aponte (ou já usada no pacote), fica o que veio
    assert authoring._proof_pose("mao-na-testa", POSES, set(), []) == "mao-na-testa"
    assert authoring._proof_pose("surpreso", POSES, {"apontando"}, []) == "surpreso"
    assert authoring._proof_pose("surpreso", [p for p in POSES if p["id"] != "apontando"], set(), []) == "surpreso"


LUPA = {"id": "lupa", "emotions": ["detalhe escondido", "investigação"], "aliases": ["investigando"],
        "facing": "center"}


class _Recent:
    def __init__(self, **seen: float) -> None:
        self.seen = seen

    def n(self, kind: str, pid: str) -> float:
        return self.seen.get(pid, 0.0) if kind == "pose" else 0.0


def test_prova_escolhe_a_emocao_que_o_texto_pede_e_varia_entre_provas():
    """Não é sempre a mesma pose: o texto pede a emoção (erro → mão na testa, reconhecer o falso → lupa) e, sem
    pedido, a pose da prova menos vista nas thumbs recentes do canal."""
    poses = [*POSES, LUPA]
    notes: list[str] = []
    assert authoring._proof_pose("surpreso", poses, set(), notes,
                                 text="Você vai reconhecer o falso GTA 6 de PC") == "lupa"
    assert notes and "lupa" in notes[0]
    assert authoring._proof_pose(None, poses, set(), [], text="Quem caiu no golpe do GTA 6 de PC") == "mao-na-testa"
    assert authoring._proof_pose(None, poses, set(), [], text="GTA 6 de PC é golpe") == "apontando"
    assert authoring._proof_pose(None, poses, set(), [], text="GTA 6 de PC é golpe",
                                 recent=_Recent(apontando=3)) == "lupa"
    # a emoção pedida já usada no pacote: a da prova
    assert authoring._proof_pose(None, poses, {"lupa"}, [], text="como reconhecer o falso") == "apontando"


def test_pacote_prova_aponta_para_a_prova_e_prefere_fundo_aereo():
    frames = [{"id": "praia", "kind": "frame", "tags": ["lugar", "praia", "dia"]},
              {"id": "orla", "kind": "frame", "tags": ["lugar", "aereo", "dia"]}]
    assets = authoring.PackageAssets(frames=frames, poses=POSES, allow_editorial=True,
                                     proofs=[{"id": "p1", "file": "uploads/x/cap.jpg", "kind": "captura",
                                              "relevance": 2}])
    item = {"title": "GTA 6 de PC pra baixar? É GOLPE", "thumb_text": "SENHAS", "template": "prova",
            "background_hint": "p1", "presenter_pose": "surpreso", "concept": "alerta", "promise": "golpe"}
    (cand,) = authoring._normalize_candidates([item], assets=assets, templates=["heroi-palavra", "prova"],
                                              is_long=True)
    assert cand.layers.template == "prova"
    assert cand.layers.presenter.pose == "apontando" and cand.layers.presenter.flip  # aponta para a esquerda
    assert cand.layers.backdrop == "orla"
