"""Fase 2: checagem de legibilidade das thumbs (B.6) e o dHash manual das variantes (B.7)."""

from __future__ import annotations

from PIL import Image

from studio.core.project.document import BgLayer, TextLayer, ThumbLayers
from studio.media import thumb_check as K
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T


def _bg(color: str) -> BgLayer:
    return BgLayer(kind="gradient", ref=f"{color},{color}")


def _render(words: str, *, size: float = 0.3, bg: str = "#7A5AA0", template: str = "heroi-palavra",
            pos: str = "left") -> tuple[Image.Image, ThumbLayers]:
    lay = ThumbLayers(template=template, background=_bg(bg), text=TextLayer(words=words, size=size, pos=pos),
                      logo="none")
    return C.render(lay, T.CANVAS, None), lay


def test_dhash_e_hamming():
    a = Image.linear_gradient("L").rotate(90).resize((90, 80))  # claro → escuro na horizontal
    h = K.dhash(a)
    assert 0 <= h < 2 ** 64
    assert K.hamming(h, h) == 0
    assert K.hamming(h, K.dhash(a.transpose(Image.Transpose.FLIP_LEFT_RIGHT))) >= 10
    assert K.hamming(0, (1 << 64) - 1) == 64


def test_texto_bom_passa():
    img, lay = _render("TUDO", size=0.32)
    score = K.check(img, lay, True, title="GTA 6: o guia da primeira semana")
    assert score["words"] == 1
    assert score["text_h"] >= 0.25
    assert score["legib_168"]["ok"] is True
    assert score["legib_168"]["contrast"] >= 4.5
    assert score["forbidden"] == 0
    assert not [w for w in score["warnings"] if "texto" in w]


def test_quatro_palavras_avisam():
    img, lay = _render("NÃO JOGUE SEM ISSO", size=0.2)
    score = K.check(img, lay, True)
    assert score["words"] == 4
    assert any("4 palavras" in w for w in score["warnings"])


def test_texto_pequeno_falha_legib_168():
    img, lay = _render("PRIMEIRA SEMANA", size=0.04, pos="top")
    score = K.check(img, lay, True)
    assert score["legib_168"]["ok"] is False
    assert score["legib_168"]["stroke_px"] < 2
    assert any("168 px" in w for w in score["warnings"])
    assert any("texto baixo" in w for w in score["warnings"])


def test_quadro_escuro_falha_luminancia():
    lay = ThumbLayers(template="lugar-mapa", background=BgLayer(kind="gradient", ref="#050505,#0A0A10"),
                      text=TextLayer(words="NOITE"), logo="none")
    img = C.render(lay, T.CANVAS, None)
    score = K.check(img, lay, True)
    assert score["luminance"] < 0.40
    assert any("escuro" in w for w in score["warnings"])
    claro, lay2 = _render("DIA", bg="#E0C060")
    assert K.check(claro, lay2, True)["luminance"] >= 0.40


def test_variantes_iguais_pegas_pelo_dup():
    # caso do prj_0ag1zyfhyd: mesma imagem base e o mesmo texto, só a posição muda pouco
    a, lay_a = _render("GTA 6 SEM SPOILER", size=0.2)
    b, lay_b = _render("GTA 6 SEM SPOILER", size=0.21)
    score = K.check(b, lay_b, True, others=[a])
    assert score["dup"] is not None and score["dup"] < 10
    assert any("igual a outra variante" in w for w in score["warnings"])
    c, lay_c = _render("TUDO", bg="#19D3C5", template="numero-lista")
    assert K.check(c, lay_c, True, others=[K.dhash(a), f"{K.dhash(b):016x}"])["dup"] >= 10


def test_repete_titulo_e_rosto_pequeno():
    img, lay = _render("PRIMEIRA SEMANA", size=0.2)
    score = K.check(img, lay, True, title="GTA 6: sua PRIMEIRA SEMANA, dia a dia")
    assert score["repeat_title"] == 2
    assert any("repete" in w for w in score["warnings"])
    # heroi-palavra sem rosto no quadro nem avatar: avisa
    assert any("rosto pequeno" in w for w in score["warnings"])


def test_sem_layout_nao_quebra():
    lay = ThumbLayers(template="heroi-palavra", text=TextLayer(words="TUDO"))
    score = K.check(Image.new("RGB", (1280, 720), (128, 128, 128)), lay, True)
    assert score["text_h"] is None and score["legib_168"]["ok"] is False
    assert len(score["dhash"]) == 16


def test_contraste_wcag():
    assert round(K.contrast_ratio((255, 255, 255), (0, 0, 0)), 1) == 21.0
    assert K.contrast_ratio((255, 212, 0), (26, 11, 46)) > 10
