"""O gancho cumpre a thumb escolhida e o molde `prova` com o avatar grande.

- `hook_thumb.thumb_hook`: o elemento central da thumb no acervo (o quadro de onde saiu o recorte do personagem, o
  quadro de fundo, a captura da prova) e a pose do avatar;
- o planejador de edição abre a 1ª cena do gancho com esse elemento e volta para o visual da cena nos primeiros 3 s;
- `presenter.assign` põe a pose da thumb na 1ª cena (por cima de "nunca na primeira cena");
- a fala em destaque sobre a imagem desfocada no gancho vira a própria imagem (`fix_hook_text`);
- o molde `prova`: avatar em busto grande à direita (também nos candidatos gravados no desenho antigo), palavra
  grande e o print de apoio embaixo dela, sem a barra lateral do site.

Só ativos sintéticos, na pasta de dados do teste.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from studio.config import get_settings
from studio.core.project.document import (
    BgLayer,
    CutoutLayer,
    MediaRef,
    PackageCandidate,
    PresenterThumbLayer,
    ProjectDoc,
    Scene,
    Segment,
    ThumbLayers,
    Visual,
)
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.library import get_style
from studio.media import thumb_assets as A
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.providers.base import Word
from studio.workers.pipeline import edit_plan, hook_thumb
from studio.workers.pipeline import presenter as scenes_mod
from studio.workers.pipeline.storyboard_check import Beat, _check_numbers, fix_hook_text, fix_hook_text_doc

LONG = "curiosidades-longo"
CID = "chn_gancho"
HOOK = ("Se você achou um GTA 6 pra PC pra baixar hoje, não baixa, porque isso é golpe e rouba as suas senhas do "
        "navegador e do banco, e eu vou te mostrar como funciona.")


def _doc(layers: ThumbLayers, *, kinds: tuple[str, ...] = ("image", "image", "image")) -> ProjectDoc:
    texts = [HOOK, "Agora a parte que importa: a data de verdade do jogo no computador.",
             "E no fim eu te conto como se proteger desse tipo de golpe."]
    roles = ["hook", "body", "body"]
    segments = [Segment(id=f"seg{i}", role=roles[i], text={"pt-BR": t}) for i, t in enumerate(texts)]
    scenes = [Scene(id=f"scn{i}", segment_id=f"seg{i}", kind=kinds[i], visual=Visual(prompt=f"cena {i}"))
              for i in range(len(texts))]
    doc = ProjectDoc(id="prj_gancho", title="GTA 6 no PC", style_id=LONG, seed=3, channel_id=CID,
                     segments=segments, scenes=scenes)
    cand = PackageCandidate(title="GTA 6 no PC", thumb_text="ESPERA?", layers=layers)
    doc.package.candidates = [cand]
    doc.package.chosen_id = cand.id
    doc.package.locked = True
    return doc


def _kit() -> Path:
    """Biblioteca do canal com um quadro de fundo, a capa e o recorte do personagem tirado da capa."""
    kit = A.thumbkit_dir(CID)
    (kit / "frames").mkdir(parents=True)
    Image.new("RGB", (1280, 720), (40, 190, 200)).save(kit / "frames" / "praia.jpg")
    Image.new("RGB", (1200, 1400), (200, 120, 60)).save(kit / "frames" / "capa.jpg")
    A.save_manifest(kit, {**A.empty_manifest(CID), "items": [
        {"id": "praia", "kind": "frame", "file": "frames/praia.jpg", "tags": ["lugar"], "focus_x": 0.4,
         "focus_y": 0.5},
        {"id": "capa", "kind": "frame", "file": "frames/capa.jpg", "tags": ["personagem"]},
        {"id": "heroi-capa", "kind": "cutout", "file": "cutouts/heroi.png", "focus_y": 0.4,
         "source": {"from": "capa", "crop": [0.4, 0.0, 1.0, 1.0]}},
    ]})
    return kit


def _pool() -> Pool:
    root = get_settings().data_dir
    cap = root / "uploads" / "prj_gancho" / "media" / "cap.jpg"
    cap.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1280, 720), (250, 250, 250)).save(cap)
    own = root / "uploads" / "prj_gancho" / "media" / "own.jpg"
    Image.new("RGB", (1280, 720), (90, 90, 90)).save(own)
    return Pool(items=[
        PoolItem(id="p1", tier="official", media="image", path=f"library/thumbkit/{CID}/frames/praia.jpg",
                 tags=["lugar"]),
        PoolItem(id="p2", tier="official", media="image", path=f"library/thumbkit/{CID}/frames/capa.jpg",
                 tags=["personagem"]),
        PoolItem(id="p3", tier="capture", media="capture", path="uploads/prj_gancho/media/cap.jpg", tags=["golpe"],
                 editorial=True),
        PoolItem(id="p4", tier="own", media="image", path="uploads/prj_gancho/media/own.jpg", tags=["golpe"]),
    ])


HEROI = ThumbLayers(template="heroi-palavra", background=BgLayer(kind="frame", ref="praia"),
                    characters=[CutoutLayer(asset="heroi-capa")])
PROVA = ThumbLayers(template="prova", background=BgLayer(kind="media", ref="media:uploads/prj_gancho/media/cap.jpg"),
                    presenter=PresenterThumbLayer(pose="surpreso", side="left", crop="meio", h=0.64), mark="x")


# --- o que a thumb promete ------------------------------------------------------------------------------------------
def test_elemento_da_thumb_figura_prova_e_avatar():
    _kit()
    pool = _pool()
    hook = hook_thumb.thumb_hook(_doc(HEROI), pool)
    assert hook is not None and hook.pose is None
    # o personagem (o quadro de onde o recorte saiu, centrado no recorte) antes do quadro de fundo
    assert [a.item.id for a in hook.anchors] == ["p2", "p1"]
    assert hook.anchors[0].focus == (0.7, 0.4) and hook.anchors[0].zoom == 1.6  # fecha no personagem
    hook = hook_thumb.thumb_hook(_doc(PROVA), pool)
    assert hook is not None and hook.pose == "surpreso" and [a.item.id for a in hook.anchors] == ["p3"]
    doc = _doc(HEROI)
    doc.package.chosen_id = None
    assert hook_thumb.thumb_hook(doc, pool) is None  # sem thumb escolhida, nada muda
    doc = _doc(HEROI)
    doc.package.locked = False
    assert hook_thumb.thumb_hook(doc, pool) is None  # só clicada, sem travar o pacote, ainda não manda no vídeo


def test_gancho_abre_com_o_elemento_da_thumb_e_volta_para_a_cena():
    _kit()
    pool = _pool()
    doc = _doc(HEROI)
    doc.scenes[0].visual.media = MediaRef(kind="photo", source="upload", asset="uploads/prj_gancho/media/own.jpg",
                                          tier="own", pool_id="p4")
    edit_plan.plan(doc, get_style(LONG), pool, recent=set())
    shots = doc.scenes[0].shots
    assert shots and shots[0].at is None and shots[0].source == "pool" and shots[0].pool_id == "p2"
    assert shots[0].framing is not None and shots[0].framing.x == 0.7 and shots[0].framing.scale == 1.6
    # volta para o visual da própria cena dentro dos primeiros 3 s
    assert len(shots) >= 2 and shots[1].source == "self"
    timed = [t for t in edit_plan.timed_shots(doc, get_style(LONG), pool) if t[0] == "scn0"]
    assert timed[0][1] == 0.0 and 0.8 <= timed[0][2] <= 3.0
    # as outras cenas não abrem com a thumb
    assert all(not s.shots or s.shots[0].source == "self" for s in doc.scenes[1:])


def test_gancho_que_ja_mostra_a_thumb_nao_ganha_corte():
    _kit()
    pool = _pool()
    doc = _doc(PROVA)
    doc.scenes[0].visual.media = MediaRef(kind="capture", source="capture", asset="uploads/prj_gancho/media/cap.jpg",
                                          tier="capture", pool_id="p3")
    edit_plan.plan(doc, get_style(LONG), pool, recent=set())
    assert all(s.pool_id != "p3" for s in doc.scenes[0].shots)


def test_avatar_da_thumb_entra_na_primeira_cena():
    from tests.test_presenter import _presenter

    doc = _doc(PROVA, kinds=("image", "image", "image"))
    words = {s.id: [Word(t, i * 20 + k * 0.5, i * 20 + k * 0.5 + 0.4)
                    for k, t in enumerate(doc.segments[i].text["pt-BR"].split())]
             for i, s in enumerate(doc.scenes)}
    scenes_mod.assign(doc, _presenter(), words)
    first = doc.scenes[0].presenter
    assert first is not None and first.pose == "surpreso" and first.at is None
    # sem thumb escolhida: a regra de sempre (nunca na 1ª cena)
    doc.package.chosen_id = None
    doc.scenes[0].presenter = None
    scenes_mod.assign(doc, _presenter(), words)
    assert doc.scenes[0].presenter is None


def test_fala_em_destaque_desfocada_no_gancho_vira_imagem():
    doc = _doc(HEROI, kinds=("image", "text", "text"))
    doc.scenes[1].segment_id = "seg0"  # 2ª batida do gancho
    doc.segments[0].text["pt-BR"] = HOOK
    for s in doc.scenes[1:]:
        s.visual.media = MediaRef(kind="photo", source="upload", asset="uploads/x.jpg", tier="own")
    beats = [Beat(key="seg0:0", segment_id="seg0", part=0, parts=2, index=0, role="hook", words=[], seconds=3),
             Beat(key="seg0:1", segment_id="seg0", part=1, parts=2, index=0, role="hook", words=[], seconds=3),
             Beat(key="seg2:0", segment_id="seg2", part=0, parts=1, index=2, role="body", words=[], seconds=3)]
    issues = fix_hook_text(doc, beats)
    assert doc.scenes[1].kind == "image" and [i.code for i in issues] == ["gancho_texto_desfocado"]
    assert doc.scenes[2].kind == "text"  # fora do gancho o cartão de texto fica


def test_fala_em_destaque_no_gancho_com_imagem_de_ia_ainda_por_gerar_vira_imagem():
    doc = _doc(HEROI, kinds=("text", "image", "image"))
    doc.scenes[0].visual = Visual(prompt="neon VI skyline")  # o build gera a imagem e o cartão a desfocaria
    beats = [Beat(key="seg0:0", segment_id="seg0", part=0, parts=1, index=0, role="hook", words=HOOK.split(),
                  seconds=6)]
    assert [i.code for i in fix_hook_text(doc, beats)] == ["gancho_texto_desfocado"]
    assert doc.scenes[0].kind == "image"
    doc = _doc(HEROI, kinds=("text", "image", "image"))
    doc.scenes[0].visual = Visual(type="none", prompt="")  # fundo liso do estilo: o cartão fica
    assert fix_hook_text(doc, beats) == [] and doc.scenes[0].kind == "text"


def test_fala_com_numero_no_gancho_mantem_o_cartao():
    doc = _doc(HEROI, kinds=("text", "image", "image"))
    doc.scenes[0].visual.media = MediaRef(kind="photo", source="upload", asset="uploads/x.jpg", tier="own")
    words = "o arquivo tem só 1,1 MB e custa 300 reais".split()
    beats = [Beat(key="seg0:0", segment_id="seg0", part=0, parts=1, index=0, role="hook", words=words, seconds=4)]
    assert fix_hook_text(doc, beats) == [] and doc.scenes[0].kind == "text"
    assert [i.code for i in _check_numbers(doc, beats)] == []  # o número continua na tela


def test_reeditar_converte_o_gancho_de_projeto_antigo():
    doc = _doc(HEROI, kinds=("text", "text", "image"))
    for s in doc.scenes[:2]:
        s.visual.media = MediaRef(kind="photo", source="upload", asset="uploads/x.jpg", tier="own")
    issues = fix_hook_text_doc(doc)
    assert [i.scene_id for i in issues] == ["scn0"] and doc.scenes[0].kind == "image"
    assert doc.scenes[1].kind == "text"  # fora do gancho


# --- molde prova ----------------------------------------------------------------------------------------------------
def _pose(tmp_path: Path) -> Path:
    folder = tmp_path / "poses"
    folder.mkdir()
    im = Image.new("RGBA", (400, 600), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.ellipse((140, 10, 260, 140), fill=(200, 150, 120, 255))
    d.rectangle((80, 140, 320, 600), fill=(30, 30, 30, 255))
    im.save(folder / "surpreso.png")
    return folder


def _pagina(tmp_path: Path) -> Path:
    """Matéria com manchete de 4 linhas à esquerda e a foto (colorida) logo ao lado, à direita."""
    img = Image.new("RGB", (1500, 850), (250, 250, 250))
    d = ImageDraw.Draw(img)
    for top, right in ((200, 820), (280, 760), (360, 600), (440, 520)):
        for x in range(30, right, 22):
            d.rectangle((x, top, x + 12, top + 55), fill=(10, 80, 250))
    for y in range(60, 780, 4):
        d.rectangle((940, y, 1500, y + 3), fill=(150 + (y % 60), 60, 200))
    img.save(tmp_path / "cap.jpg")
    return tmp_path / "cap.jpg"


def test_prova_avatar_grande_e_print_de_apoio_mesmo_no_candidato_antigo(tmp_path):
    cap = _pagina(tmp_path)
    r = A.AssetResolver(None, pose_dirs=[_pose(tmp_path)], media=lambda ref: cap)
    layers = ThumbLayers(template="prova", logo="none", mark="x",
                         background=BgLayer(kind="media", ref="media:cap"),
                         presenter=PresenterThumbLayer(pose="surpreso", side="left", crop="meio", h=0.64),
                         text={"words": "SENHAS", "fill": "white", "pos": "left", "size": 0.26})
    out = C.render(layers, T.CANVAS, r)
    lay = out.info["layout"]
    W, H = T.CANVAS
    avatar = next(f for f in lay["figures"] if f["kind"] == "presenter")
    card = next(f for f in lay["figures"] if f["kind"] == "card")
    assert avatar["side"] == "right" and avatar["crop"] == "busto" and avatar["face_h"] >= 0.2  # rosto lê a 168 px
    assert card["bbox"][2] < W * 0.55 and card["bbox"][1] > H * 0.38  # o print é apoio, embaixo da palavra
    assert lay["text_bbox"][3] - lay["text_bbox"][1] >= H * 0.25  # a palavra domina
    # a janela da página fica na coluna da manchete: a foto/anúncio do lado não entra no cartão
    x0, _y0, x1, _y1 = C.page_window(Image.open(cap), 0.47 * W / (0.48 * H))
    assert x0 <= 30 and x1 < 940


def test_prova_respeita_o_avatar_que_o_dono_ajustou(tmp_path):
    cap = _pagina(tmp_path)
    r = A.AssetResolver(None, pose_dirs=[_pose(tmp_path)], media=lambda ref: cap)
    for side, crop in (("left", "busto"), ("right", "meio")):
        layers = ThumbLayers(template="prova", logo="none", mark="x", background=BgLayer(kind="media", ref="media:cap"),
                             presenter=PresenterThumbLayer(pose="surpreso", side=side, crop=crop, h=0.9),
                             text={"words": "SENHAS", "fill": "white", "pos": "left", "size": 0.3})
        avatar = next(f for f in C.render(layers, T.CANVAS, r).info["layout"]["figures"] if f["kind"] == "presenter")
        assert avatar["side"] == side and avatar["crop"] == crop  # o editor manda: o desenho antigo é só o legado
