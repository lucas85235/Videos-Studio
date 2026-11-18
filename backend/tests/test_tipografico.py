"""Cartão tipográfico só como último recurso, e nunca quadro morto.

O QA do prj_gq4krrs11w ("GTA 6 no PC", r3) achou ~27% do vídeo em fundo roxo liso com a legenda palavra a palavra:
blocos de ~21 s sem imagem (3:15-3:33, 6:00-6:18), quatro cartões lisos depois do CTA, quadros com uma palavra só ("e",
"que") ou vazios, a posição do texto pulando e o detector acusando "tela preta". A causa: cada caminho que tira a mídia
da cena (captura de outro assunto no anexo, cartão sem real que caiba de fundo, tetos de IA e de material oficial)
caía direto no cartão tipográfico, sem hierarquia nem regra de uso; e o renderer cobria o fundo do estilo a 85% e
revelava a fala palavra a palavra. Aqui: a hierarquia (`typographic.settle`), o avatar no cartão (`presenter.assign`),
as páginas por frase inteira (renderer e o espelho da conferência final) e o fundo clareado.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from studio.config import REPO_ROOT
from studio.core import sources as tiers
from studio.core.project.document import MediaRef, ProjectDoc, Scene, Segment, Visual
from studio.core.project.ops import scene_text
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.library import get_style, load_presets
from studio.providers.base import Word
from studio.workers.finalcheck import triggers
from studio.workers.pipeline import presenter as presenter_scenes
from studio.workers.pipeline import typographic
from tests.test_presenter import _presenter
from tests.test_renderer_fx import _node

COMP = REPO_ROOT / "renderer" / "src" / "components"
MATH = COMP / "fx" / "math.ts"
LONG = "curiosidades-longo"
PID = "prj_tipo"

FILL = "Muita coisa mudou desde então no mercado de jogos e na comunidade de fãs pelo mundo afora."  # 16 palavras
PIX = "E se pagou com cartão ou Pix, fala com o seu banco"  # 11 palavras: 4,4 s
SHORT = "Isso derruba os cookies roubados da sessão"  # 7 palavras: 2,8 s
SHORT2 = "Ativa a verificação em duas etapas agora"  # 7 palavras
LONG_TEXT = "Porque pro site você já tá logado e a verificação continua importante mesmo com a sessão aberta"


def _own(name: str) -> MediaRef:
    """Mídia própria já anexada (real, fora do teto de material oficial)."""
    return MediaRef(kind="photo", source="upload", asset=f"uploads/{PID}/media/{name}.jpg", tier="own")


def _doc(rows: list[tuple[str, str]], roles: dict[int, str] | None = None) -> ProjectDoc:
    """Uma cena por segmento: ("image", texto) com mídia própria anexada; ("text", texto) sem nada (cartão
    tipográfico)."""
    roles = roles or {}
    segments, scenes = [], []
    for i, (kind, text) in enumerate(rows):
        segments.append(Segment(id=f"seg{i:02d}", role=roles.get(i, "body"), text={"pt-BR": text}))  # type: ignore[arg-type]
        visual = Visual(media=_own(f"m{i}")) if kind == "image" else Visual(type="none", prompt=text)
        scenes.append(Scene(id=f"scn{i:02d}", segment_id=f"seg{i:02d}", kind=kind, span=len(text.split()),  # type: ignore[arg-type]
                            visual=visual))
    return ProjectDoc(id=PID, title="Golpe do download falso", style_id=LONG, seed=5, segments=segments,
                      scenes=scenes)


def _theme(n: int = 8) -> list[PoolItem]:
    return [PoolItem(id=f"p{100 + k}", tier="official", media="clip", desc="Trailer 1 oficial (Rockstar) · trailer",
                     tags=["official_trailer"], path="library/clips/src/trailer.mp4", start_s=float(5 * k), dur_s=4.0,
                     credit="Rockstar Games") for k in range(n)]


PIX_ITEM = PoolItem(id="p30", tier="stock", desc="Pagamento por Pix no celular, banco", path="uploads/x/pix.jpg")


def _fake_attach(doc: ProjectDoc) -> list[str]:
    """O anexo sem rede: a mídia pendente ganha o arquivo na pasta do projeto."""
    for scene in doc.scenes:
        media = scene.visual.media
        if media is not None and media.pool_id and not (media.asset or media.video):
            if media.kind == "clip":
                media.video = f"uploads/{doc.id}/clips/{media.pool_id}.mp4"
            else:
                media.asset = f"uploads/{doc.id}/media/{media.pool_id}.jpg"
    return []


def _typographic(doc: ProjectDoc) -> list[str]:
    return [s.id for s in doc.ordered_scenes() if tiers.is_typographic(s, doc)]


def _settle(doc: ProjectDoc, pool: Pool, *, avatar: bool = False) -> list[str]:
    return typographic.settle(doc, get_style(LONG), pool, avatar=avatar, attach=_fake_attach)


def _base(n: int = 10) -> list[tuple[str, str]]:
    return [("image", FILL)] * n


# --- hierarquia -------------------------------------------------------------------------------------------------------


def test_is_typographic_is_a_text_scene_with_nothing_behind(isolated_settings):
    doc = _doc([("image", FILL), ("text", SHORT)])
    image, card = doc.ordered_scenes()
    assert tiers.is_typographic(card, doc) and not tiers.is_typographic(image, doc)
    card.visual = Visual(reuse_of=image.id)  # o cartão sobre a imagem da vizinha não é tipográfico
    assert not tiers.is_typographic(card, doc)


def test_subject_material_comes_first_then_the_video_theme(isolated_settings):
    rows = _base(4) + [("text", PIX)] + _base(4) + [("text", LONG_TEXT)] + _base(6)
    doc = _doc(rows)
    notes = _settle(doc, Pool(items=[PIX_ITEM, *_theme()]))
    scenes = doc.ordered_scenes()
    pix, other = scenes[4], scenes[9]
    # a fala do Pix mostra a foto do Pix (o mesmo assunto, de qualquer nível); a outra, o trailer do tema
    assert pix.visual.media is not None and pix.visual.media.pool_id == "p30" and pix.kind == "image"
    assert other.visual.media is not None and other.visual.media.pool_id.startswith("p1") and other.kind == "image"
    assert other.visual.media.video  # anexado
    assert _typographic(doc) == []
    assert any("material real do assunto" in n for n in notes) and any("tema do vídeo" in n for n in notes)


def test_card_with_its_own_phrase_keeps_the_phrase_over_the_material(isolated_settings):
    rows = _base(4) + [("text", LONG_TEXT)] + _base(6)
    doc = _doc(rows)
    card = doc.ordered_scenes()[4]
    card.overlay_text = {"pt-BR": "O cookie é o pior"}
    _settle(doc, Pool(items=_theme()))
    # a frase do cartão fica; o trailer vira o fundo dele
    assert card.kind == "text" and card.visual.media is not None and not tiers.is_typographic(card, doc)


def test_ai_only_inside_the_cap_when_there_is_no_material(isolated_settings):
    rows = _base(12) + [("text", LONG_TEXT)] + _base(12)
    doc = _doc(rows)
    notes = _settle(doc, Pool(items=[]))
    card = doc.ordered_scenes()[12]
    assert card.kind == "image" and card.visual.type == "ai_image" and card.visual.prompt
    assert card.visual.ai_reason == typographic.AI_REASON
    assert any("imagem de IA" in n for n in notes)


def test_ai_over_the_cap_never_enters(isolated_settings, monkeypatch):
    monkeypatch.setattr(typographic, "_ai_fits", lambda state, scene: False)
    rows = _base(3) + [("text", SHORT)] + _base(6)
    doc = _doc(rows)
    _settle(doc, Pool(items=[]))
    card = doc.ordered_scenes()[3]
    # sem material e sem IA: o cartão curto e sozinho fica (é o último recurso e cabe nas regras)
    assert tiers.is_typographic(card, doc) and card.visual.type == "none"


# --- regras do cartão que fica ----------------------------------------------------------------------------------------


def test_with_avatar_a_short_lonely_card_stays_for_the_avatar(isolated_settings):
    rows = _base(4) + [("text", SHORT)] + _base(8) + [("text", LONG_TEXT)] + _base(6)
    doc = _doc(rows)
    _settle(doc, Pool(items=_theme()), avatar=True)
    scenes = doc.ordered_scenes()
    assert tiers.is_typographic(scenes[4], doc)  # curto e sozinho: o avatar entra nele
    assert not tiers.is_typographic(scenes[13], doc)  # mais de 4 s: o trailer do tema
    assert scenes[13].visual.media is not None


def test_never_two_cards_in_a_row_nor_two_in_twenty_seconds(isolated_settings):
    rows = _base(4) + [("text", SHORT), ("text", SHORT2)] + _base(2) + [("text", SHORT)] + _base(8)
    doc = _doc(rows)
    _settle(doc, Pool(items=_theme()), avatar=True)
    left = _typographic(doc)
    assert left == ["scn04"]
    order = doc.ordered_scenes()
    assert order[5].visual.media is not None and order[8].visual.media is not None


def test_the_end_of_the_video_never_stays_a_card(isolated_settings):
    rows = _base(10) + [("text", SHORT), ("image", FILL), ("text", SHORT2)]
    doc = _doc(rows, roles={12: "cta"})
    _settle(doc, Pool(items=_theme()), avatar=True)
    assert _typographic(doc) == []


def test_forced_card_borrows_a_real_theme_image_never_an_unrelated_stock(isolated_settings, monkeypatch):
    monkeypatch.setattr(typographic, "_ai_fits", lambda state, scene: False)
    rows = _base(4) + [("text", SHORT), ("text", LONG_TEXT)] + _base(4)
    doc = _doc(rows)
    xbox = PoolItem(id="p208", tier="stock", media="clip", desc="joysticks on a video game console",
                    path="uploads/x/xbox.mp4")
    order = doc.ordered_scenes()
    order[3].visual.media = MediaRef(kind="clip", source="library", video=f"uploads/{PID}/clips/p208.mp4",
                                     pool_id="p208", tier="stock")
    notes = _settle(doc, Pool(items=[xbox]))
    order = doc.ordered_scenes()
    assert tiers.is_typographic(order[4], doc)  # o primeiro fica (curto)
    long_card = order[5]
    # o segundo não pode ficar (colado e longo): repete a imagem própria mais perto, nunca a foto de banco do Xbox
    assert long_card.visual.reuse_of is not None and long_card.visual.reuse_of != order[3].id
    assert long_card.kind == "image"
    assert any("não cabia ali" in n for n in notes)


def test_card_problem_reasons(isolated_settings):
    rows = _base(4) + [("text", SHORT), ("text", SHORT2), ("image", SHORT), ("text", SHORT)] + _base(4)
    rows += [("text", LONG_TEXT)] + _base(4) + [("text", SHORT)]
    doc = _doc(rows, roles={len(rows) - 1: "cta"})
    state = typographic._State(doc=doc, style=get_style(LONG), pool=Pool(), avatar=False, common=frozenset())
    state.refresh()
    order = doc.ordered_scenes()
    assert typographic.card_problem(state, order[4]) is None
    state.kept.append(order[4].id)
    assert "colado" in typographic.card_problem(state, order[5])
    assert "20 s" in typographic.card_problem(state, order[7])  # 5,6 s depois do outro
    assert "4 s" in typographic.card_problem(state, order[12])  # longo
    assert typographic.card_problem(state, order[-1]) == "fim do vídeo"


def test_storyboard_job_runs_the_typographic_step_after_the_attach(isolated_settings, monkeypatch):
    from studio.workers import tasks
    from studio.workers.pipeline import edit_plan, media_pool

    calls: list[str] = []
    monkeypatch.setattr(media_pool, "attach_chosen", lambda d, p: calls.append("anexo") or [])
    monkeypatch.setattr(typographic, "settle", lambda d, s, p: calls.append("tipografico") or [])
    monkeypatch.setattr(edit_plan, "plan", lambda d, s, p: calls.append("plano") or [])
    tasks._attach_and_plan(_doc(_base(2)), get_style(LONG), Pool())
    assert calls == ["anexo", "tipografico", "plano"]


# --- avatar no cartão -------------------------------------------------------------------------------------------------


def test_avatar_enters_the_typographic_card(isolated_settings):
    rows = [("image", FILL)] * 3 + [("text", SHORT)] + [("image", FILL)] * 3
    doc = _doc(rows)
    for scene in doc.scenes:
        if scene.kind == "image":
            scene.visual = Visual(prompt="x")
    words = {}
    t = 0.0
    for scene in doc.ordered_scenes():
        toks = scene_text(doc, scene).split()
        words[scene.id] = [Word(w, t + k * 0.4, t + k * 0.4 + 0.3) for k, w in enumerate(toks)]
        t += 20.0
    presenter_scenes.assign(doc, _presenter(), words)
    card = doc.ordered_scenes()[3]
    assert card.presenter is not None and not card.presenter.off
    neighbours = [doc.ordered_scenes()[k].presenter for k in (2, 4)]
    assert all(p is None for p in neighbours)


# --- páginas do cartão (renderer e conferência final) -----------------------------------------------------------------


def _words(text: str, step: int = 6) -> list[dict]:
    return [{"text": w, "from": k * step, "to": k * step + 4} for k, w in enumerate(text.split())]


PAGE_CASES = [
    "Isso derruba os cookies roubados. E",
    "Porque, pro site, você já tá logado. A verificação continua importante, ela só não protege uma sessão que já "
    "tá aberta.",
    "Segunda: o jogo é comprado à parte, e pra usar a nuvem",
    "como se fosse você, dependendo do site até com a verificação em duas etapas ligada.",
    "e",
    "um dois três quatro cinco seis sete oito nove dez onze doze treze quatorze quinze dezesseis",
    "dependendo do site até com a verificação em duas etapas ligada.",
    "você vai esperar o PC ou já comprou num console",
]


@pytest.mark.parametrize("text", PAGE_CASES)
def test_pages_are_whole_phrases_never_a_lone_word(text):
    pages = triggers._kinetic_pages(_words(text))
    assert sum(len(p) for p in pages) == len(text.split())
    if len(text.split()) >= triggers.PAGE_MIN:
        assert all(len(p) >= triggers.PAGE_MIN for p in pages)
    assert len(pages) >= 1


def test_a_page_never_ends_on_a_link_word():
    pages = triggers._kinetic_pages(_words("dependendo do site até com a verificação em duas etapas ligada."))
    assert [w["text"] for w in pages[0]][-1] not in ("a", "com")
    assert all(len(p) >= triggers.PAGE_MIN for p in pages)


def test_phrase_card_is_checked_from_the_cut(isolated_settings):
    scene = {"id": "s", "kind": "text", "from": 0, "durationInFrames": 90, "text": "O cookie é o pior",
             "words": _words("O cookie é o pior: com ele, o golpista pode entrar na sua conta", 8)}
    elements = triggers.scene_elements(scene, 30)
    assert [(e["what"], e["frame"]) for e in elements] == [("destaque", 0)]


def test_panel_without_backdrop_enters_at_the_cut():
    scene = {"id": "s", "kind": "stat", "from": 0, "durationInFrames": 120, "reveal": 87,
             "card": {"value": "2013", "label": "GTA 5 nos consoles"}, "text": "", "number": None}
    assert triggers._panel_from(scene, 30) == 0
    assert triggers._panel_from({**scene, "src": "a.jpg"}, 30) == 84
    assert triggers._panel_from({**scene, "src": "a.jpg", "bg_luma": 0.02}, 30) == 0


PAGES_HARNESS = r"""
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
const m = await import(pathToFileURL(process.argv[2]).href);
const cases = JSON.parse(readFileSync(process.argv[3], 'utf8'));
const out = {
  pages: cases.map((ws) => m.kineticPages(ws).map((p) => p.map((w) => w.text))),
  marks: cases.map((ws) => m.kineticPages(ws).map((p) => m.kineticMarks(p).filter(Boolean).length)),
  at: cases.map((ws) => { const pages = m.kineticPages(ws); return [m.pageAt(pages, 0), m.pageAt(pages, 100000)]; }),
  phrase: m.scenePages({ text: 'O cookie e o pior', words: cases[0], durationInFrames: 90 }).map((p) => p.length),
  min: m.PAGE_MIN, max: m.PAGE_MAX, center: m.KINETIC_CENTER_Y,
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def page_math(tmp_path_factory: pytest.TempPathFactory) -> dict:
    node = _node()
    if node is None:
        pytest.skip("Node 22.6+ (remoção de tipos) indisponível")
    folder = tmp_path_factory.mktemp("pages")
    script = folder / "harness.mjs"
    script.write_text(PAGES_HARNESS, encoding="utf-8")
    cases = folder / "cases.json"
    cases.write_text(json.dumps([_words(t) for t in PAGE_CASES], ensure_ascii=False), encoding="utf-8")
    proc = subprocess.run([*node, "--no-warnings", str(script), str(MATH), str(cases)], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_renderer_pages_match_the_final_check_mirror(page_math):
    assert (page_math["min"], page_math["max"]) == (triggers.PAGE_MIN, triggers.PAGE_MAX)
    for text, pages in zip(PAGE_CASES, page_math["pages"], strict=True):
        assert pages == [[w["text"] for w in p] for p in triggers._kinetic_pages(_words(text))]
    assert all(1 <= n <= 2 for marks in page_math["marks"] for n in marks if n)  # a palavra-chave, não meia frase
    assert all(first == 0 for first, _last in page_math["at"])  # a 1ª página desde o corte: nunca quadro vazio
    assert page_math["phrase"] == [5]  # a frase do cartão inteira de uma vez
    assert 0.4 <= page_math["center"] < 0.5  # centro óptico, fixo


# --- o fundo do cartão não é tela preta -------------------------------------------------------------------------------


def _hex(c: str) -> tuple[float, float, float]:
    c = c.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _luma(rgb: tuple[float, float, float]) -> float:
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def test_typographic_background_is_lifted_above_the_black_detector(isolated_settings):
    data = (COMP / "DataCards.tsx").read_text(encoding="utf-8")
    floor = float(data.split("export const TYPO_BG_MIN_LUMA = ")[1].split(";")[0])
    # `blackdetect` (finalcheck) usa pix_th 0,10: o fundo clareado fica acima com folga
    assert floor >= 0.14
    for style in load_presets().values():
        cards = style.edit.text_cards
        bg, accent = _hex(cards.background), _hex(cards.accent)
        # 60% do caminho até o destaque (o máximo de `liftTo`) passa do limiar do detector em todos os estilos
        assert _luma(tuple(b * 0.4 + a * 0.6 for b, a in zip(bg, accent, strict=True))) >= 0.10, style.id
    visual = (COMP / "VisualLayer.tsx").read_text(encoding="utf-8")
    assert "lift={solid}" in visual and "withAlpha(cards.background, 0.85)" not in visual
    scene = (COMP / "SceneView.tsx").read_text(encoding="utf-8")
    assert "!(kind === 'text' && filled(scene.text))" in scene  # a frase do cartão deixa a legenda da fala
    assert "!spokenAtCut && url ?" in scene  # o cartão de dado sem fundo entra no corte
