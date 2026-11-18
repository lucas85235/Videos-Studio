"""Captura de página no lugar certo e sem corte, fundo de cartão sem preto liso e fim sem plano congelado.

O QA do prj_gq4krrs11w ("GTA 6 no PC") achou:
- a manchete das capturas cortada na borda esquerda pelo zoom/pan/punch ("rand Theft Auto VI's PC", "ites falsos");
- a captura virando papel de parede de assunto alheio (a GamesBeat da data do PC sob "ATIVE A VERIFICAÇÃO EM 2
  ETAPAS", a ESET dos golpes sob "pra quem tá no PC", a mesma página da Rockstar de fundo de 5 cartões);
- o fundo do cartão tipográfico escurecido até o preto liso (a página quase preta da Rockstar) e o fim do vídeo com ~9 s
  no mesmo plano congelado.

Tudo offline: as contas do renderer rodam no Node (pula sem Node 22.6+), o resto é Python puro.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from studio.config import REPO_ROOT, get_settings
from studio.core import edit_resolve, timeline
from studio.core.project.document import MediaRef, ProjectDoc, Scene, SceneCard, Visual
from studio.core.project.document import Segment as Seg
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.library import get_style
from studio.core.styles.schema import grammar_for
from studio.workers.pipeline import authoring, edit_plan, media_pool
from tests.test_renderer_fx import _node
from tests.test_storyboard_real_first import STYLE as SHORT_STYLE
from tests.test_storyboard_real_first import base_doc, by_key, make_pool, run

COMP = REPO_ROOT / "renderer" / "src" / "components"
MATH = COMP / "fx" / "math.ts"
LONG = "curiosidades-longo"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


GAMESBEAT = PoolItem(
    id="p225", tier="capture", media="capture",
    desc="Grand Theft Auto VI's PC release is slated for Q1 2027 - GamesBeat · \"GamesBeat · Grand Theft Auto VI's PC\"",
    page_url="https://gamesbeat.com/grand-theft-auto-vis-pc-release-is-slated-for-q1-2027/", credit="gamesbeat.com",
    rects=[[0.0314, 0.1696, 0.7405, 0.1429], [0.0314, 0.2998, 0.8006, 0.1429]], path="uploads/prj/media/gb.jpg")
ESET = PoolItem(
    id="p233", tier="capture", media="capture",
    desc="GTA 6: golpes com downloads falsos e malware aumentam antes do lançame · \"WeLiveSecurity (ESET) · GTA 6\"",
    page_url="https://www.welivesecurity.com/pt/golpes-fraudes/gta-6-golpes/", credit="welivesecurity.com",
    path="uploads/prj/media/eset.jpg")
WINDOWS = PoolItem(
    id="p231", tier="capture", media="capture",
    desc="Grand Theft Auto 6 will be playable on Xbox Cloud Gaming · \"Windows Central · GTA 6 will be playable\"",
    page_url="https://www.windowscentral.com/gaming/xbox/gta-6-xbox-cloud", credit="windowscentral.com",
    path="uploads/prj/media/wc.jpg")
COMMON = frozenset({"gta", "pc", "nao", "baixe", "download"})  # `video_terms` do título "GTA 6 no PC: ..."


# --- captura só no trecho daquele fato ou daquela fonte -------------------------------------------------------------


def test_outlet_keys_come_from_the_site_the_title_and_the_parentheses():
    assert media_pool.outlet_keys(GAMESBEAT) == {"gamesbeat"}
    assert media_pool.outlet_keys(ESET) == {"welivesecurity", "eset"}
    assert {"windowscentral", "windows"} <= media_pool.outlet_keys(WINDOWS)


@pytest.mark.parametrize(("item", "text", "fits"), [
    (GAMESBEAT, "No dia treze de outubro, a GamesBeat publicou, citando quatro fontes", True),
    (GAMESBEAT, "Boato mais forte até agora: primeiro trimestre, segundo a GamesBeat", True),
    (GAMESBEAT, "cookies roubados. Ativa a verificação em duas etapas onde ainda não", False),
    (GAMESBEAT, "tiver. E se pagou alguma coisa, com cartão ou Pix, fala com o seu banco", False),
    (GAMESBEAT, "Segunda: o jogo é comprado à parte, e pra usar a nuvem", False),
    (ESET, "Então não tem saída pra quem tá no PC?", False),  # "PC" e "GTA" são do vídeo inteiro
    (ESET, "Num alerta de treze de novembro, a ESET citou um caso achado", True),
    (WINDOWS, "O cookie é o pior: com ele, o golpista pode entrar na sua conta", False),
    (WINDOWS, "E se você já baixou? Calma, mas age rápido.", False),
    (WINDOWS, "Primeira: no lançamento, a nuvem é só pra consoles Xbox", True),  # nome próprio do título
    # nome do título num trecho que é uma lista de contas: não é sobre o Xbox
    (WINDOWS, "Depois encerra todas as sessões abertas: Google, Discord, Steam, PSN e Xbox.", False),
    # duas palavras do título da matéria (o plural conta uma vez só: "golpes" e "golpe" são uma)
    (ESET, "Os golpes com instalador falso se espalham antes do lançamento", True),
    (ESET, "Como reconhecer esse golpe antes de perder suas contas", False),
    # faixa em caixa alta não tem nome próprio
    (WINDOWS, "XBOX CLOUD? NÃO NO PC", True),
    (GAMESBEAT, "ATIVE A VERIFICAÇÃO EM 2 ETAPAS", False),
])
def test_capture_fits_only_the_stretch_about_its_fact_or_source(item, text, fits):
    assert media_pool.fits_scene(item, text, common=COMMON) is fits


ROCKSTAR = PoolItem(
    id="p228", tier="capture", media="capture",
    desc="Grand Theft Auto VI – Rockstar Games — \"Rockstar Games – Grand Theft Auto VI (site oficial)\"",
    page_url="https://www.rockstargames.com/VI", credit="rockstargames.com", path="uploads/prj/media/r.jpg")


def test_collector_note_in_parentheses_is_not_the_outlet_nor_the_subject():
    """"(site oficial)" é nota do coletor: "site oficial do fabricante" não cita a Rockstar; "(ESET)" é o veículo."""
    assert "siteoficial" not in media_pool.outlet_keys(ROCKSTAR)
    assert not media_pool.fits_scene(ROCKSTAR, "passa um antivírus baixado só do site oficial do fabricante",
                                     common=COMMON)
    assert media_pool.fits_scene(ROCKSTAR, "e isso tá no site oficial da Rockstar", common=COMMON)
    # "Games" é parte do nome do veículo, não um nome do assunto
    assert not media_pool.fits_scene(ROCKSTAR, "Isso vale pra Epic Games e pra qualquer loja", common=COMMON)


def test_sentence_context_joins_the_fragments_the_scene_cuts():
    fragments = ["principal, que recupera todo o resto. Depois encerra todas as",
                 "sessões abertas, inclusive no Discord e nas contas de jogos, como Steam, PlayStation",
                 "e Xbox. Isso derruba os", "cookies roubados.", "Frase inteira."]
    ctx = media_pool.sentence_context(fragments, 2)
    assert isinstance(ctx, media_pool.Sentence)
    assert ctx == ("Depois encerra todas as sessões abertas, inclusive no Discord e nas contas de jogos, como Steam, "
                   "PlayStation e Xbox. Isso derruba os cookies roubados.")
    assert media_pool.sentence_context(fragments, 4) == ""  # já é a frase inteira
    # o "Xbox" do fim da lista não chama a matéria do Xbox Cloud; sem a frase, o trecho parecia ser sobre ele
    assert media_pool.fits_scene(WINDOWS, fragments[2], common=COMMON)
    assert not media_pool.fits_scene(WINDOWS, fragments[2], ctx, common=COMMON)
    # a frase serve só à captura: a foto de banco confere a fala da cena, não a frase toda
    stock = PoolItem(id="p9", tier="stock", desc="Discord app on a phone", path="uploads/prj/media/d.jpg")
    assert not media_pool.fits_scene(stock, fragments[2], ctx, common=COMMON)


def test_card_source_counts_as_citing_the_outlet():
    """O cartão "1º TRI 2027 · PC segundo a GamesBeat" (fonte do cartão: GamesBeat) cabe na captura da GamesBeat."""
    assert media_pool.fits_scene(GAMESBEAT, "que a Rockstar mira o primeiro trimestre", "GamesBeat", common=COMMON)


def test_storyboard_choice_of_an_unrelated_capture_follows_the_fallback(monkeypatch):
    """O LLM pôs a página da Rockstar Newswire na batida do celular: a reserva do mesmo assunto (a foto do celular)
    entra; na batida que cita a Rockstar, a captura fica."""
    doc = base_doc()
    beats = authoring.plan_beats(doc, SHORT_STYLE)
    phone = next(i for i, b in enumerate(beats) if b.text.startswith("Muita gente"))
    news = next(i for i, b in enumerate(beats) if b.text.startswith("A Rockstar"))
    extra = {phone: {"source": "pool", "pool_id": "p2"}, news: {"source": "pool", "pool_id": "p2", "ai_reason": None}}
    new_doc, review, beats, _ = run(monkeypatch, doc, extra, pool=make_pool())
    scenes = by_key(new_doc, beats)
    assert scenes[news].visual.media is not None and scenes[news].visual.media.pool_id == "p2"
    media = scenes[phone].visual.media
    assert media is None or media.pool_id != "p2"
    assert any(i["code"] == "acervo_reserva" and "não tem a ver com a fala" in i["message"]
               and i["scene_id"] == scenes[phone].id for i in review["issues"])


def test_card_background_is_plain_instead_of_an_unrelated_capture(monkeypatch):
    """O cartão vizinho de uma captura de outro assunto fica com o fundo liso (cartão tipográfico), não com ela."""
    pool = Pool(items=[it for it in make_pool().items if it.id == "p2"], gathered_at="2026-11-17T00:00:00Z")
    doc = base_doc()
    beats = authoring.plan_beats(doc, SHORT_STYLE)
    news = next(i for i, b in enumerate(beats) if b.text.startswith("A Rockstar"))
    phone = next(i for i, b in enumerate(beats) if b.text.startswith("Muita gente"))
    extra = {news: {"source": "pool", "pool_id": "p2", "ai_reason": None},
             phone: {"kind": "text", "source": "none", "overlay_text": "Várias vezes"}}
    new_doc, _review, beats, _ = run(monkeypatch, doc, extra, pool=pool)
    card = by_key(new_doc, beats)[phone]
    shown = new_doc.shown_media(card)
    assert shown is None or shown.pool_id != "p2"


# --- anexo: captura alheia e fundo repetido viram cartão tipográfico ------------------------------------------------


def _attach_doc(texts: list[str], kinds: list[str]) -> ProjectDoc:
    segments = [Seg(id=f"seg{i}", text={"pt-BR": t}) for i, t in enumerate(texts)]
    scenes = [Scene(id=f"scn{i}", segment_id=f"seg{i}", kind=k, span=len(texts[i].split()),  # type: ignore[arg-type]
                    visual=Visual(prompt=f"cena {i}" if k == "image" else "")) for i, k in enumerate(kinds)]
    return ProjectDoc(id="prj_cap", title="GTA 6 no PC: quando sai?", style_id=LONG, seed=3, segments=segments,
                      scenes=scenes)


def _fake_materialize(doc: ProjectDoc):
    def fake(item, *a, **k):
        return MediaRef(kind="capture", source="capture", asset=f"uploads/{doc.id}/media/{item.id}.jpg",
                        pool_id=item.id, tier=item.tier, rects=item.rects)
    return fake


def test_attach_keeps_a_capture_out_of_scenes_about_something_else(isolated_settings, monkeypatch):
    doc = _attach_doc(["No dia treze de outubro, a GamesBeat publicou a data do PC.",
                       "Ativa a verificação em duas etapas onde ainda não tiver.",
                       "E se pagou com cartão ou Pix, fala com o seu banco o quanto antes."],
                      ["image", "title", "image"])
    for scene in doc.scenes:
        scene.visual.media = media_pool.pending_ref(GAMESBEAT)
    doc.scenes[1].overlay_text = {"pt-BR": "ATIVE A VERIFICAÇÃO EM 2 ETAPAS"}
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    warnings = media_pool.attach_chosen(doc, Pool(items=[GAMESBEAT]))
    first, card, image = doc.ordered_scenes()
    assert first.visual.media is not None and first.visual.media.pool_id == "p225"
    # o cartão fica com o fundo do estilo; a cena de imagem vira cartão tipográfico (nunca a captura, nem a IA de
    # reserva, que somaria tempo de IA sem conferir o teto, nem a repetição da mídia vizinha)
    assert card.visual.media is None and card.visual.type == "none" and card.visual.reuse_of is None
    assert image.kind == "text" and image.visual.media is None and image.visual.reuse_of is None
    assert image.visual.type == "none"
    assert any("fundo do estilo" in w for w in warnings)
    assert any("cartão tipográfico" in w for w in warnings)
    assert not any("IA de reserva" in w for w in warnings)


def test_attach_swaps_an_off_topic_capture_for_real_material_of_the_scene(isolated_settings, monkeypatch):
    """Com material real do assunto na reserva (a foto do banco que fala do Pix), a cena de imagem e o cartão ficam
    com ele: o cartão tipográfico é só o último recurso."""
    doc = _attach_doc(["No dia treze de outubro, a GamesBeat publicou a data do PC.",
                       "E se pagou com cartão ou Pix, fala com o seu banco o quanto antes.",
                       "Muita coisa mudou desde então no mercado de jogos e na comunidade de fãs pelo mundo afora.",
                       "Muita coisa mudou de novo no mercado de jogos e na comunidade de fãs pelo mundo afora.",
                       "Guarda o comprovante do Pix e fala com o banco."],
                      ["image", "image", "image", "image", "text"])
    pix = PoolItem(id="p30", tier="stock", desc="Pagamento por Pix no celular, banco", path="uploads/prj/media/pix.jpg")
    pix2 = PoolItem(id="p31", tier="stock", desc="Comprovante de Pix do banco na tela", path="uploads/prj/media/p2.jpg")
    doc.scenes[0].visual.media = media_pool.pending_ref(GAMESBEAT)
    doc.scenes[1].visual.media = media_pool.pending_ref(GAMESBEAT)
    doc.scenes[4].visual.media = media_pool.pending_ref(GAMESBEAT)
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    media_pool.attach_chosen(doc, Pool(items=[GAMESBEAT, pix, pix2]))
    scenes = doc.ordered_scenes()
    assert scenes[1].kind == "image" and scenes[1].visual.media is not None
    assert scenes[1].visual.media.pool_id in ("p30", "p31")
    assert scenes[4].visual.media is not None and scenes[4].visual.media.pool_id in ("p30", "p31")
    assert scenes[4].visual.media.pool_id != scenes[1].visual.media.pool_id


def test_attach_never_repeats_a_capture_as_card_background(isolated_settings, monkeypatch):
    """O cartão que voltaria à mesma captura cedo demais (a 60 s ou menos da outra aparição) não repete o fundo."""
    doc = _attach_doc(["A Rockstar não anunciou o PC, diz o site da Rockstar.",
                       "Muita coisa mudou desde então no mercado de jogos de mundo aberto e na comunidade de fãs.",
                       "E a Rockstar repetiu: só no site oficial da Rockstar."],
                      ["image", "image", "text"])
    rockstar = PoolItem(id="p228", tier="capture", media="capture", desc="Grand Theft Auto VI · Rockstar Games",
                        page_url="https://www.rockstargames.com/VI", path="uploads/prj/media/r.jpg")
    doc.scenes[0].visual.media = media_pool.pending_ref(rockstar)
    doc.scenes[2].visual.media = media_pool.pending_ref(rockstar)
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    warnings = media_pool.attach_chosen(doc, Pool(items=[rockstar]))
    assert doc.scenes[0].visual.media is not None
    assert doc.scenes[2].visual.media is None and doc.scenes[2].visual.type == "none"
    assert any("fundo do estilo" in w and "já aparecia demais" in w for w in warnings)


# --- a manchete da captura nunca sai do quadro --------------------------------------------------------------------


def test_keep_rect_is_the_union_of_the_headline_lines():
    assert edit_resolve.keep_rect(GAMESBEAT.rects) == [0.0314, 0.1696, 0.8006, 0.2731]
    assert edit_resolve.keep_rect([]) is None and edit_resolve.keep_rect([[0.1, 0.1, 0, 0.1]]) is None


def test_timeline_sends_the_headline_to_keep(isolated_settings):
    style = get_style(LONG)
    doc = _attach_doc(["A GamesBeat publicou a data do PC."], ["image"])
    doc.scenes[0].visual.media = MediaRef(kind="capture", source="capture", asset="uploads/prj_cap/media/gb.jpg",
                                          tier="capture", rects=GAMESBEAT.rects)
    out = timeline.resolve_scene(doc, style, doc.scenes[0], "pt-BR", is_first=True)
    assert out["keep"] == [0.0314, 0.1696, 0.8006, 0.2731]
    plain = _attach_doc(["Sem captura."], ["image"])
    assert "keep" not in timeline.resolve_scene(plain, style, plain.scenes[0], "pt-BR", is_first=True)


def test_renderer_layers_honor_the_kept_headline():
    """Cada camada que escala respeita o `keep`: o movimento da imagem, o zoom de dentro das molduras e a câmera."""
    visual = _read(COMP / "VisualLayer.tsx")
    assert "keepTransform(keep, scale, [ox, oy], [tx / 100, ty / 100])" in visual
    treatments = _read(COMP / "Treatments.tsx")
    assert treatments.count("keptZoom(scene,") >= 4  # lousa, colagem, tela e a conta da câmera
    scene = _read(COMP / "SceneView.tsx")
    assert "keep={camKeep()}" in scene
    assert "cameraAt(frame, fx, { fps, frames, cuts, width, height, seed, keep })" in _read(COMP / "CameraRig.tsx")
    assert "keep: shot.keep" in _read(COMP / "ShotStack.tsx")


KEEP_HARNESS = r"""
import { pathToFileURL } from 'node:url';
const m = await import(pathToFileURL(process.argv[2]).href);
const gb = [0.0314, 0.1696, 0.8006, 0.2731];
const out = {};
const edge = (k, s, o, t) => [o[0] + s * (k[0] + t[0] - o[0]), o[0] + s * (k[0] + k[2] + t[0] - o[0])];
// Ken Burns de 1,15 em volta do centro: a 1ª letra ficava fora (0,5 − 1,15·0,4686 = −0,039).
const kb = m.keepTransform(gb, 1.15, [0.5, 0.5], [0, 0]);
out.kb = { ...kb, x: edge(gb, kb.s, [0.5, 0.5], kb.t) };
// Pan que empurra a imagem para a esquerda: o deslocamento para na margem.
const pan = m.keepTransform(gb, 1.15, [0.5, 0.5], [-0.06, 0]);
out.pan = { ...pan, x: edge(gb, pan.s, [0.5, 0.5], pan.t) };
// Linha larga demais para a escala pedida: a escala desce até caber (a coluna inteira).
const wide = [0.05, 0.4, 0.9, 0.1];
out.wide = m.keepTransform(wide, 1.4, [0.5, 0.5], [0, 0]);
// Trecho encostado na borda do arquivo (1,45%): a margem não passa disso, e a borda da imagem nunca aparece.
const dex = [0.0145, 0.2351, 0.7778, 0.063];
const d = m.keepTransform(dex, 1.15, [0.5, 0.5], [0, 0]);
out.dex = { ...d, x: edge(dex, d.s, [0.5, 0.5], d.t), room: (0.5 * (d.s - 1)) / d.s };
const ctx = { fps: 30, frames: 120, cuts: [], width: 1920, height: 1080, seed: 1 };
const punch = { type: 'punch', from: 0, dur: 60, params: { scale: 1.2 } };
out.camFree = m.cameraAt(10, [punch], ctx);
out.camKeep = m.cameraAt(10, [punch], { ...ctx, keep: gb });
out.noKeep = m.keepTransform(null, 1.2, [0.5, 0.5], [0, 0]);
out.cardDark = m.cardDim(0.62, 0.3);
out.cardBright = m.cardDim(0.62, 0.9);
out.cardNone = m.cardDim(0.62, undefined);
out.floor = m.CARD_BG_FLOOR;
// Rockstar quase preta e lisa, canto cinza do Xbox: fundo do estilo; arte em neon do VI (escura, cheia de forma): fica.
out.offRockstar = m.cardBackdropOff(0.081, 0.056);
out.offXbox = m.cardBackdropOff(0.519, 0.074);
out.offNeon = m.cardBackdropOff(0.095, 0.107);
out.offBlack = m.cardBackdropOff(0.02, undefined);
out.offOld = m.cardBackdropOff(undefined, undefined);
out.margin = m.KEEP_MARGIN;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def keep_math(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = _node()
    if node is None:
        pytest.skip("Node 22.6+ (remoção de tipos) indisponível")
    script = tmp_path_factory.mktemp("keep") / "harness.mjs"
    script.write_text(KEEP_HARNESS, encoding="utf-8")
    proc = subprocess.run([*node, "--no-warnings", str(script), str(MATH)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_zoom_and_pan_never_cut_the_first_letter(keep_math):
    m = keep_math["margin"]
    for key in ("kb", "pan"):
        left, right = keep_math[key]["x"]
        assert keep_math[key]["s"] == pytest.approx(1.15)
        assert left >= m - 1e-6 and right <= 1 - m + 1e-6, key
    assert keep_math["wide"]["s"] == pytest.approx((1 - 2 * m) / 0.9)
    dex = keep_math["dex"]
    assert dex["x"][0] >= 0.0145 - 1e-6  # a 1ª letra fica (com a margem que o arquivo tem)
    assert -dex["room"] - 1e-9 <= dex["t"][0] <= dex["room"] + 1e-9  # e a borda da imagem não aparece
    assert keep_math["noKeep"] == {"s": 1.2, "t": [0, 0]}


def test_camera_punch_keeps_the_headline(keep_math):
    free, kept = keep_math["camFree"], keep_math["camKeep"]
    assert free["x"] + free["scale"] * 0.0314 < 0  # o punch no centro cortava a manchete
    # a manchete de 80% da largura não cabe inteira com margem num punch de 1,2: a escala desce até caber
    assert kept["scale"] == pytest.approx(min(1.2, (1 - 2 * keep_math["margin"]) / 0.8006))
    assert kept["x"] + kept["scale"] * 0.0314 >= keep_math["margin"] - 1e-6
    assert kept["x"] + kept["scale"] * (0.0314 + 0.8006) <= 1 - keep_math["margin"] + 1e-6


# --- fundo do cartão nunca vira preto liso --------------------------------------------------------------------------


def test_card_dim_stops_at_the_floor(keep_math):
    assert keep_math["cardNone"] == pytest.approx(0.62) and keep_math["cardBright"] == pytest.approx(0.62)
    assert 0.3 * (1 - keep_math["cardDark"]) >= keep_math["floor"] - 1e-6


def test_flat_or_black_card_background_goes_but_dark_detailed_art_stays(keep_math):
    """Valores medidos nos fundos reais do prj_gq4krrs11w (brilho, detalhe)."""
    assert keep_math["offRockstar"] and keep_math["offXbox"] and keep_math["offBlack"]
    assert not keep_math["offNeon"] and not keep_math["offOld"]


def _image(rel: str, value: int, *, stripes: bool = False) -> None:
    path = get_settings().data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (160, 90), (value, value, value))
    if stripes:  # forma: listras claras (a arte escura cheia de detalhe)
        for x in range(0, 160, 10):
            image.paste((value + 120,) * 3, (x, 0, x + 4, 90))
    image.save(path)


def test_dark_card_background_falls_back_to_the_style_background(isolated_settings):
    _image("cache/media/dark.jpg", 20)
    _image("cache/media/light.jpg", 200)
    _image("cache/media/neon.jpg", 15, stripes=True)
    dark = {"kind": "text", "src": "cache/media/dark.jpg", "keep": [0.1, 0.1, 0.5, 0.1]}
    timeline.card_backdrop(dark)
    # o `src` fica (o Short derivado e a conferência leem a imagem); o renderer troca pelo fundo do estilo (lisa)
    assert dark["src"] == "cache/media/dark.jpg" and dark["bg_detail"] < timeline.CARD_BG_MIN_DETAIL
    math = _read(MATH)
    assert f"export const CARD_BG_MIN_LUMA = {timeline.CARD_BG_MIN_LUMA};" in math
    assert f"export const CARD_BG_MIN_DETAIL = {timeline.CARD_BG_MIN_DETAIL};" in math
    assert "cardBackdropOff(scene.bg_luma, scene.bg_detail) ? null : assetUrl(scene.src" in _read(COMP / "SceneView.tsx")
    light = {"kind": "title", "src": "cache/media/light.jpg"}
    timeline.card_backdrop(light)
    assert light["src"] == "cache/media/light.jpg" and light["bg_luma"] == pytest.approx(200 / 255, abs=0.01)
    assert light["bg_luma"] >= timeline.CARD_BG_MIN_LUMA
    # escura, mas com forma: fica (o renderer só escurece menos)
    neon = {"kind": "text", "src": "cache/media/neon.jpg"}
    timeline.card_backdrop(neon)
    assert neon["bg_luma"] < 0.4 and neon["bg_detail"] >= timeline.CARD_BG_MIN_DETAIL
    image = {"kind": "image", "src": "cache/media/dark.jpg"}
    timeline.card_backdrop(image)
    assert image == {"kind": "image", "src": "cache/media/dark.jpg"}  # a cena de imagem não muda
    missing = {"kind": "text", "src": "cache/media/nao-existe.jpg"}
    timeline.card_backdrop(missing)
    assert missing == {"kind": "text", "src": "cache/media/nao-existe.jpg"}


# --- o fim do vídeo não fica parado ---------------------------------------------------------------------------------


CLOSING = ("E manda esse vídeo pra aquele amigo que tá procurando o jogo de graça, porque ele vai acabar caindo num "
           "golpe, e você vai salvar a conta dele antes que seja tarde demais pra recuperar qualquer coisa.")


def test_closing_clip_shorter_than_the_scene_is_cut_where_it_ends(isolated_settings):
    """O clipe de 3 s na última cena (bem mais longa) não fica desacelerado e congelado: corta onde ele acaba."""
    style = get_style(LONG)
    texts = ["O farol da ilha foi construído em 1887 por engenheiros franceses.", CLOSING]
    doc = _attach_doc(texts, ["image", "image"])
    doc.scenes[1].visual = Visual(type="upload", media=MediaRef(
        kind="clip", source="pexels", asset="uploads/prj_cap/media/c.jpg", video="uploads/prj_cap/clips/c.mp4",
        start_s=0.0, dur_s=3.0, tier="stock"))
    planner = edit_plan._Planner(doc, style, grammar_for(style), Pool(), set())
    planner.run()
    beat = planner.beats[-1]
    assert beat.end - beat.start > 6.0
    assert planner._frozen_clip_spans(beat) == []
    selfs = [s for s in beat.shots if s.source == "self"]
    assert len(beat.shots) >= 2 and all(s.dur <= 3.0 + edit_plan.MIN_SHOT_S + 1e-6 for s in selfs)
    # e o que ficou parado na última cena passa no limite do fim
    assert edit_plan.CLOSING_STILL_S <= grammar_for(style).max_still_s


def test_card_scene_with_headline_in_the_card_still_uses_card_source():
    """A fonte do cartão entra nos textos da cena que o anexo confere (`_scene_texts`)."""
    doc = _attach_doc(["que a Rockstar mira o primeiro trimestre pro PC."], ["stat"])
    doc.scenes[0].card = SceneCard(value={"pt-BR": "1º TRI"}, source="GamesBeat")
    assert "GamesBeat" in media_pool._scene_texts(doc, doc.scenes[0])
