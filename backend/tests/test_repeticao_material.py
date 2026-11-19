"""Repetição e material que não casa com a fala (QA r4 do `prj_gq4krrs11w`, rodada 5).

- a mesma imagem noutra fonte (a arte em outro item, dentro da captura do site, no meio do fundo desfocado) conta como
  o mesmo material (`media.lookalike`), e a 3ª aparição é trava no anexo (nem a fala sobre o item a fura);
- pôster com fundo desfocado parado não passa de `PILLARBOX_STILL_S`;
- o texto que pula sobre material verdadeiro diz o que está na tela, e sai quando a fala muda de parte da frase;
- "site oficial" e a loja citada na fala pedem a captura daquela página;
- a moldura de post do feed só emoldura post de rede social;
- o prompt de IA não usa metáfora-clichê de banco que a fala não cita.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFilter

from studio.config import get_settings
from studio.core import sources
from studio.core.project.document import MediaRef, ProjectDoc
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.library import get_style
from studio.media import lookalike
from studio.workers.pipeline import edit_plan, media_pool, storyboard_check, typographic
from tests.test_captura_reuso import _attach_doc, _fake_materialize
from tests.test_material_certo import LONG, _doc, _planner

# --- imagens sintéticas -------------------------------------------------------------------------------------------------


def _art(seed: int, size: tuple[int, int] = (640, 360)) -> Image.Image:
    """Uma "arte" com desenho: manchas de cor em baixa frequência e formas com borda."""
    rng = np.random.default_rng(seed)
    low = rng.random((6, 10, 3)) * 255
    img = Image.fromarray(low.astype(np.uint8)).resize(size, Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = int(rng.integers(0, size[0])), int(rng.integers(0, size[1]))
        r = int(rng.integers(8, 60))
        color = tuple(int(c) for c in rng.integers(0, 255, 3))
        if rng.random() < 0.5:
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
        else:
            draw.rectangle((x - r, y - r // 2, x + r, y + r // 2), fill=color)
    return img


def _page(seed: int, art: Image.Image | None = None, size: tuple[int, int] = (1280, 720)) -> Image.Image:
    """Uma página capturada: fundo branco com linhas de "texto" e, opcionalmente, a arte no meio dela."""
    rng = np.random.default_rng(seed)
    img = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(img)
    for row in range(40, size[1] - 20, 22):
        x = 40
        while x < size[0] - 60:
            w = int(rng.integers(20, 90))
            draw.rectangle((x, row, x + w, row + 9), fill=(40, 40, 40))
            x += w + int(rng.integers(8, 16))
    if art is not None:
        w = int(size[0] * 0.55)
        front = art.resize((w, round(art.height * w / art.width)))
        img.paste(front, ((size[0] - w) // 2, 160))
    return img


def _pillarbox(art: Image.Image, size: tuple[int, int] = (1280, 720)) -> Image.Image:
    """A arte vertical inteira no meio do quadro deitado, sobre ela mesma desfocada (como `real_media.fit_media`)."""
    back = art.resize(size).filter(ImageFilter.GaussianBlur(30))
    front = art.resize((round(art.width * size[1] / art.height), size[1]))
    back.paste(front, ((size[0] - front.width) // 2, 0))
    return back


def _sig(img: Image.Image, page: bool = False) -> lookalike.Signature | None:
    return lookalike.signature(img, page=page)


def test_same_art_in_another_size_or_color_is_the_same_look():
    art = _art(1)
    other = art.resize((1920, 1080)).point(lambda v: min(255, int(v * 0.85 + 20)))
    assert lookalike.same_look(_sig(art), _sig(other))
    assert not lookalike.same_look(_sig(art), _sig(_art(2)))


def test_art_inside_a_page_capture_is_the_same_look():
    """O site oficial (e o site falso que copia a colagem) usa a arte-chave de fundo: a captura dele é a mesma imagem
    na tela."""
    art = _art(3)
    page = _page(10, art)
    assert lookalike.same_look(_sig(page, page=True), _sig(art))
    # páginas de matéria diferentes (texto sobre branco) não são a mesma imagem
    assert not lookalike.same_look(_sig(_page(11), page=True), _sig(_page(12), page=True))
    # a arte numa miniatura de canto (menos de um terço da página) não conta
    small = _page(13)
    small.paste(art.resize((200, 112)), (1040, 560))
    assert not lookalike.same_look(_sig(small, page=True), _sig(art))


def test_poster_in_the_middle_of_its_blurred_copy_is_the_same_look():
    poster = _art(4, size=(360, 640))
    assert lookalike.same_look(_sig(_pillarbox(poster)), _sig(poster))
    assert not lookalike.same_look(_sig(_pillarbox(poster)), _sig(_pillarbox(_art(5, size=(360, 640)))))


def test_a_small_detail_on_black_does_not_join_different_arts():
    """O logo sobre o preto casava com toda arte que leva o logo: o detalhe pequeno não é a imagem."""
    logo = _art(6, size=(220, 120))
    on_black = Image.new("RGB", (1280, 720), "black")
    on_black.paste(logo, (530, 300))
    poster = _art(7)
    poster.paste(logo.resize((160, 88)), (240, 220))
    assert not lookalike.same_look(_sig(on_black), _sig(poster))
    assert _sig(Image.new("RGB", (640, 360), "white")) is None  # lisa: sem assinatura


def test_look_index_groups_and_tokens(tmp_path: Path):
    art = _art(8)
    files = {"a.jpg": art, "b.jpg": _page(20, art), "c.jpg": _art(9)}
    for name, img in files.items():
        img.save(tmp_path / name)
    index = lookalike.LookIndex(load=lambda p, page: lookalike.signature_file(tmp_path / p, page=page))
    a = index.token("a.jpg")
    assert a is not None and index.token("b.jpg", page=True) == a
    assert index.token("c.jpg") not in (None, a)
    assert index.token("missing.jpg") is None
    media = MediaRef(kind="capture", source="capture", asset="b.jpg", tier="capture", pool_id="p9")
    assert sources.media_tokens(media, index) == {"pool:p9", "file:b.jpg", a}
    item = PoolItem(id="p1", tier="official", media="image", path="a.jpg")
    assert a in sources.item_tokens(item, index)
    clip = MediaRef(kind="clip", source="youtube", asset="a.jpg", video="v.mp4", tier="official")
    assert not index.media(clip)  # trecho de vídeo: planos do mesmo trailer são planos diferentes


# --- anexo: teto de aparições é trava --------------------------------------------------------------------------------


FILLER = "Enquanto isso, a imagem de IA mostra outra coisa por um tempo, sem nada daquilo, e segue."


def test_third_appearance_is_capped_even_where_the_speech_is_about_it(isolated_settings, monkeypatch):
    """A captura do site oficial entrava em 4 cenas porque toda fala cita a Rockstar: a 3ª aparição vira cartão
    tipográfico (que o `typographic.settle` ainda tenta encher), com aviso do teto."""
    site = PoolItem(id="p228", tier="capture", media="capture",
                    desc='Grand Theft Auto VI – Rockstar Games — "Rockstar Games – Grand Theft Auto VI (site oficial)"',
                    page_url="https://www.rockstargames.com/VI", credit="rockstargames.com",
                    path="uploads/prj_cap/media/p228.jpg")
    texts = ["E requisitos de sistema? A Rockstar não publicou nenhum.", FILLER, FILLER,
             "A Rockstar não detalhou os aparelhos, mas esses apps já existem.", FILLER, FILLER,
             "Se inscreve pra eu te avisar no dia que a Rockstar anunciar o PC."]
    doc = _attach_doc(texts, ["image"] * len(texts))
    for scene in (doc.scenes[0], doc.scenes[3], doc.scenes[6]):
        scene.visual.media = media_pool.pending_ref(site)
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    warnings = media_pool.attach_chosen(doc, Pool(items=[site]))
    first, second, third = (doc.ordered_scenes()[i] for i in (0, 3, 6))
    assert first.visual.media is not None and second.visual.media is not None
    assert third.kind == "text" and third.visual.media is None
    assert any("aparições" in w for w in warnings)


def test_same_image_from_another_item_counts_for_the_cap(isolated_settings, monkeypatch):
    """A arte-chave entrava por itens diferentes (o blog, o pôster com fundo desfocado): pela imagem, são a mesma."""
    data = get_settings().data_dir
    media_dir = data / "uploads" / "prj_cap" / "media"
    media_dir.mkdir(parents=True)
    art = _art(30)
    art.save(media_dir / "p1.jpg")
    art.resize((1280, 720)).save(media_dir / "p2.jpg")
    items = [PoolItem(id=f"p{i}", tier="official", media="image", desc="GTA VI arte oficial", topic="GTA 6",
                      path=f"uploads/prj_cap/media/p{i}.jpg") for i in (1, 2)]
    texts = ["O GTA 6 chegou no PlayStation 5 e no Xbox.", FILLER, FILLER,
             "O GTA 6 é o jogo mais esperado do ano.", FILLER, FILLER, "E o GTA 6 vende muito no console."]
    doc = _attach_doc(texts, ["image"] * len(texts))
    doc.scenes[0].visual.media = media_pool.pending_ref(items[0])
    doc.scenes[3].visual.media = media_pool.pending_ref(items[1])
    doc.scenes[6].visual.media = media_pool.pending_ref(items[1])

    def fake(item, *a, **k):
        return MediaRef(kind="official", source="link", asset=item.path, pool_id=item.id, tier=item.tier)

    monkeypatch.setattr(media_pool, "materialize", fake)
    media_pool.attach_chosen(doc, Pool(items=items))
    last = doc.ordered_scenes()[6]
    assert last.kind == "text" and last.visual.media is None  # p2 só 2 vezes, mas a imagem já está 2 vezes na tela


def test_cards_over_the_scene_image_past_the_cap_get_the_style_background(isolated_settings, monkeypatch):
    """A matéria e dois cartões sobre ela separados por outra imagem eram 3 aparições da mesma captura em 30 s: o
    cartão mais tarde fica com o fundo do estilo; a matéria (a prova) fica."""
    from studio.core.project.document import Visual

    mb = PoolItem(id="p232", tier="capture", media="capture", desc="Sites falsos de demo — Malwarebytes",
                  page_url="https://www.malwarebytes.com/blog/fake-gta-6", credit="malwarebytes.com",
                  path="uploads/prj_cap/media/p232.jpg")
    texts = ["Em agosto, a Malwarebytes encontrou sites falsos de demo do GTA 6.", "Era um ladrão de dados.",
             "Ele puxa senhas salvas de 19 navegadores.", "O cookie é o pior de todos.",
             "Dependendo do site, até com a verificação em duas etapas ligada."]
    doc = _attach_doc(texts, ["image", "image", "stat", "image", "text"])
    doc.scenes[0].visual.media = media_pool.pending_ref(mb)
    for i in (2, 4):
        doc.scenes[i].visual = Visual(reuse_of=doc.scenes[0].id)
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    media_pool.attach_chosen(doc, Pool(items=[mb]))
    proof, _ai, stat, _ai2, last = doc.ordered_scenes()
    assert proof.visual.media is not None and proof.visual.media.pool_id == "p232"
    assert stat.visual.reuse_of == proof.id
    assert last.visual.reuse_of is None and last.visual.type == "none"


# --- edição -----------------------------------------------------------------------------------------------------------


POSTER = MediaRef(kind="official", source="link", asset="uploads/prj_mc/media/poster.jpg", tier="official",
                  width=1080, height=1600, title="GTA VI – Trailer 2 (capa oficial)", pool_id="p30")
TRAILER = MediaRef(kind="official", source="link", asset="uploads/prj_mc/media/trailer.jpg", tier="official",
                   width=1920, height=1080, title="Trailer 1 oficial (Rockstar)", pool_id="p31")


def test_pillarboxed_poster_never_stays_still_longer_than_the_limit():
    text = ("Mas o próprio GTA 6 já foi adiado duas vezes, então trata janeiro a março como janela provável, não como "
            "promessa, e espera o anúncio oficial da Rockstar antes de acreditar em qualquer data.")
    planner = _planner(_doc([text], media=[POSTER]))
    beat = planner.beats[0]
    assert planner._still_limit(beat) == edit_plan.PILLARBOX_STILL_S
    wide = _planner(_doc([text], media=[TRAILER]))
    assert wide._still_limit(wide.beats[0]) == wide.g.max_still_s
    doc = _doc([text], media=[POSTER])
    edit_plan.plan(doc, get_style(LONG), Pool(), recent=set())
    planner = _planner(doc)
    spans = planner._still_spans(planner.beats[0])
    cuts = sorted(planner._time_of(planner.beats[0], e.at) for e in doc.scenes[0].fx if e.type == "punch")
    for a, b in spans:
        marks = [a, *(t for t in cuts if a < t < b), b]
        assert max(y - x for x, y in zip(marks, marks[1:], strict=False)) <= edit_plan.PILLARBOX_STILL_S + 0.6


def test_text_pop_over_true_material_says_what_is_on_screen():
    """"CHAOS" (o malware) sobre o quadro do trailer: palavra de outro assunto em letra gigante sobre o material
    verdadeiro. Sobre a IA, ou palavra do próprio material, ou número, fica."""
    text = "E lembra daquela ISO gigante? Dentro dela tinha coisa pior: o Chaos, que apaga tudo em 2026."
    on_trailer = _planner(_doc([text], media=[TRAILER]))
    beat = on_trailer.beats[0]
    word = {w.key: w for w in beat.words}
    assert not on_trailer._free(beat, word["chaos"], "text_pop")
    assert on_trailer._free(beat, word["chaos"], "punch")
    assert on_trailer._free(beat, word["2026"], "text_pop")  # número
    on_ai = _planner(_doc([text]))
    assert on_ai._free(on_ai.beats[0], {w.key: w for w in on_ai.beats[0].words}["chaos"], "text_pop")
    # a palavra do próprio material (o trailer) fica
    about = _planner(_doc(["O trailer oficial mostra a cidade inteira de novo, agora de noite."], media=[TRAILER]))
    assert about._free(about.beats[0], {w.key: w for w in about.beats[0].words}["trailer"], "text_pop")


def test_text_pop_leaves_when_the_speech_moves_to_the_other_half():
    """"um arquivinho de um MEGABYTE ou uma ISO vazada gigante": o texto sai antes de "ou"."""
    planner = _planner(_doc(["Dois: o tamanho não bate, tipo um arquivinho de um megabyte ou uma ISO vazada gigante."]))
    beat = planner.beats[0]
    word = next(w for w in beat.words if w.key == "megabyte")
    nxt = next(w for w in beat.words if w.key == "ou")
    ms = planner._pop_ms(beat, word)
    assert word.start + ms / 1000 <= max(nxt.start, word.start + edit_plan.POP_MIN_MS / 1000) + 1e-6
    lone = _planner(_doc(["Cinco, o maior alarme de todos é pedirem pra desativar o antivírus do computador."]))
    w = next(x for x in lone.beats[0].words if x.key == "alarme")
    assert lone._pop_ms(lone.beats[0], w) == edit_plan.POP_MS


STORE_POOL = Pool(items=[
    PoolItem(id="p40", tier="capture", media="capture", path="uploads/prj_mc/pool/p40.jpg",
             desc="PlayStation Store — Grand Theft Auto VI", page_url="https://store.playstation.com/pt-br/concept/1",
             credit="store.playstation.com", rects=[[0.1, 0.2, 0.5, 0.08]]),
    PoolItem(id="p41", tier="capture", media="capture", path="uploads/prj_mc/pool/p41.jpg",
             desc='Grand Theft Auto VI – Rockstar Games — "Rockstar Games – Grand Theft Auto VI (site oficial)"',
             page_url="https://www.rockstargames.com/VI", credit="rockstargames.com"),
])


def test_store_in_the_speech_shows_the_store_capture():
    keyart = MediaRef(kind="official", source="link", asset="uploads/prj_mc/media/k.jpg", tier="official",
                      width=1920, height=1080)
    doc = _doc(["Nenhum jogo legítimo precisa disso. E compra só na PlayStation Store, ou direto no console."],
               media=[keyart])
    edit_plan.plan(doc, get_style(LONG), STORE_POOL, recent=set())
    assert ("PlayStation", "pool", "p40") in [(s.at, s.source, s.pool_id) for s in doc.scenes[0].shots]
    # "Rockstar Store" pede a loja: a página do jogo no site da Rockstar não é ela
    doc = _doc(["Nenhum jogo legítimo precisa disso. Compra na Rockstar Store, digitando o endereço você mesmo."],
               media=[keyart])
    edit_plan.plan(doc, get_style(LONG), STORE_POOL, recent=set())
    assert all(s.pool_id != "p41" for s in doc.scenes[0].shots)


# --- moldura de post e prompt de IA ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("page_url", "post"), [
    ("https://x.com/RockstarGames/status/1", True), ("https://www.instagram.com/p/abc/", True),
    ("https://www.reddit.com/r/GTA6/comments/1", True), ("https://www.dexerto.com/gta/gta-6-pc", False),
    ("https://www.rockstargames.com/VI", False), ("https://box.com/x", False),
])
def test_only_social_posts_get_the_feed_frame(page_url, post):
    media = MediaRef(kind="official", source="link", asset="a.jpg", page_url=page_url, tier="official")
    assert sources.is_social_post(media) is post


@pytest.mark.parametrize(("prompt", "speech", "found"), [
    ("A gloved hand pulls keys out of a drawer at night.", "Ele puxa senhas salvas.", "gloved hand"),
    ("A key in a lock on a wooden door, warm light.", "A verificação não protege a sessão.", "key in a lock"),
    ("A hooded hacker typing in a dark room.", "O golpista entra na conta.", "hooded hacker"),
    ("A hand holding a phone with a blank screen in a cafe.", "Se pedirem Pix, é o mesmo golpe.",
     "hand holding a phone"),
    ("A padlock on a wooden desk at dusk.", "Troca o cadeado da bicicleta.", None),  # a fala cita o objeto
    ("A woman changes her password on a phone in a kitchen at night.", "Troca as senhas de outro aparelho.", None),
    ("Downtown avenue at night, giant blank screens above people.", "Até dezembro de 2023.", None),
])
def test_cliche_metaphor_in_the_ai_prompt(prompt, speech, found):
    assert storyboard_check.cliche_metaphor(prompt, speech) == found


def test_scenario_with_a_cliche_is_refused():
    doc: ProjectDoc = _doc(["É o mesmo golpe."])
    scene = doc.scenes[0]
    assert not typographic.is_scenario(doc, scene, "A gloved hand steals a ring of house keys from a desk drawer.",
                                       "É o mesmo golpe.")
    assert typographic.is_scenario(doc, scene, "A young man reads a bank message on his phone at a bus stop at "
                                   "dusk.", "É o mesmo golpe.")
