"""Planejador de edição (ADR 0004 §3.6): subplanos e eventos determinísticos, cotas por minuto e travas.

Tudo offline: o projeto mock passa pelos jobs de roteiro e storyboard (que chamam o planejador), e os documentos
pequenos são montados à mão. A biblioteca de memes, quando entra, é trocada por uma falsa.
"""

from __future__ import annotations

import math
import statistics
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import (
    PRESENTER_AT_RE,
    BeatMark,
    FxEvent,
    GagHint,
    MediaRef,
    ProjectDoc,
    Scene,
    ShotFraming,
    SubShot,
    Visual,
)
from studio.core.project.document import Segment as Seg
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.core.project.presenter import trigger_index
from studio.core.styles.library import get_style
from studio.core.styles.schema import grammar_for
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import edit_plan, media_pool
from studio.workers.pipeline.presenter import estimate_words

LONG = "curiosidades-longo"
SHORT = "misterios-short"


def _run_job(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _mock_project(style_id: str = LONG, **kw) -> ProjectDoc:
    doc = service.create_project(title="Farol", style_id=style_id, topic="o farol abandonado da ilha", **kw)
    _run_job("script", doc.id)
    _run_job("storyboard", doc.id)
    return service.get_doc(doc.id)


def _fresh(doc: ProjectDoc) -> ProjectDoc:
    """O documento sem o que o planejador escreveu (como saiu do storyboard)."""
    out = doc.model_copy(deep=True)
    for scene in out.scenes:
        scene.shots, scene.fx, scene.mark = [], [], None
    return out


def _doc(texts: list[str], *, style_id: str = LONG, roles: list[str] | None = None, seed: int = 7) -> ProjectDoc:
    roles = roles or ["hook", *(["body"] * (len(texts) - 1))]
    segments = [Seg(id=f"seg{i}", role=roles[i], text={"pt-BR": t}) for i, t in enumerate(texts)]
    scenes = [Scene(id=f"scn{i}", segment_id=f"seg{i}", visual=Visual(prompt=f"cena {i}")) for i in range(len(texts))]
    return ProjectDoc(id="prj_teste", title="Teste", style_id=style_id, seed=seed, segments=segments, scenes=scenes)


def _fx(doc: ProjectDoc, fx_type: str | None = None) -> list[tuple[str, FxEvent]]:
    return [(s.id, e) for s in doc.ordered_scenes() for e in s.fx if fx_type is None or e.type == fx_type]


def _event_times(doc: ProjectDoc, style) -> list[tuple[str, str, float]]:
    planner = edit_plan._Planner(doc, style, grammar_for(style), Pool(), set())
    return [(beat.scene.id, e.type, planner._time_of(beat, e.at)) for beat in planner.beats for e in beat.scene.fx]


def _duration(doc: ProjectDoc, style) -> float:
    shots = edit_plan.timed_shots(doc, style)
    return max(a + d for _s, a, d, _p in shots)


LONG_TEXT = (
    "O farol da ilha de Santa Clara foi construído em 1887 por engenheiros franceses. Durante quase um século, "
    "ele guiou navios pelo estreito mais perigoso da costa, onde as correntes mudam sem aviso. Os moradores contam "
    "que o último faroleiro desapareceu numa noite de tempestade, deixando a lâmpada acesa e a porta trancada por "
    "dentro. Ninguém nunca explicou como ele saiu. Em 1975, a marinha automatizou a luz e abandonou a construção, "
    "que começou a ruir com o vento salgado. Hoje, turistas atravessam o canal só para fotografar as escadas "
    "quebradas e as janelas sem vidro."
)


# --- projeto mock (jobs de verdade) ------------------------------------------------------------------------------------


def _fixture_pool() -> Pool:
    """Acervo-fixture: fotos e capturas do tema, de níveis variados (o coletor real é o item `acervo-real`). Os
    arquivos existem na pasta de dados do teste: o anexo do acervo copia o arquivo para o projeto e, sem ele, a cena
    cai na reserva."""
    from PIL import Image

    from studio.config import get_settings

    tiers = ["free", "stock", "official", "capture", "free", "official"]
    root = get_settings().data_dir
    for i in range(1, len(tiers) + 1):
        path = root / "uploads" / "prj" / "pool" / f"{i}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (1280, 720), (40 * i, 90, 160 - 20 * i)).save(path, quality=85)
    return Pool(items=[PoolItem(id=f"p{i}", tier=tier, desc="farol", tags=["farol", "ilha", "história", "detalhe"],
                                path=f"uploads/prj/pool/{i}.jpg", w=1280, h=720) for i, tier in enumerate(tiers, 1)])


def test_mock_project_comes_out_with_shots_and_fx_within_quotas(isolated_settings, monkeypatch):
    pool = _fixture_pool()
    monkeypatch.setattr(media_pool, "gather", lambda doc, style, **_: pool)
    doc = _mock_project(LONG)
    style = get_style(doc.style_id, doc.style_overrides)
    grammar = grammar_for(style)
    summary = edit_plan.summary(doc)
    assert summary["scenes_with_shots"] >= 5 and summary["fx_total"] > 20
    pooled = [sh for s in doc.scenes for sh in s.shots if sh.source == "pool"]
    # O storyboard simulado põe os 6 itens do acervo-fixture em todas as cenas (cada um a cada ~30 s): a distribuição
    # do acervo (2 aparições, 60 s entre elas) não deixa nenhum virar subplano também. O subplano do acervo é
    # conferido nos testes de acervo largo (`_wide_pool`).
    assert len(pooled) <= grammar.switch_per_min * _duration(doc, style) / 60
    minutes = _duration(doc, style) / 60
    for fx_type, rate in (("punch", grammar.punch_per_min), ("text_pop", grammar.text_pop_per_min),
                          ("sfx", grammar.sfx_per_min)):
        per_min = len(_fx(doc, fx_type)) / minutes
        assert abs(per_min - rate) <= grammar.tolerance * rate, (fx_type, per_min, rate)
    # ritmo humano: durações variadas
    shots = edit_plan.timed_shots(doc, style, pool)
    durs = [d for _s, _a, d, _p in shots]
    assert statistics.pstdev(durs) / statistics.mean(durs) >= 0.45
    assert len({p for *_x, p in shots}) >= 3  # o acervo traz outros níveis para o meio da IA
    assert min(durs) >= edit_plan.MIN_SHOT_S - 1e-6
    # todas as cenas com marca (completada pelo planejador)
    assert all(s.mark is not None for s in doc.scenes if s.id in {b for b, *_ in edit_plan.timed_shots(doc, style)})


def test_every_trigger_matches_the_regex_and_a_word_of_the_beat(isolated_settings):
    doc = _mock_project(SHORT)
    style = get_style(doc.style_id, doc.style_overrides)
    wpm = style.script.words_per_minute.get(doc.primary_language, 150)
    words = estimate_words(doc, doc.primary_language, wpm)
    ats = [(s.id, sh.at) for s in doc.scenes for sh in s.shots if sh.at is not None]
    ats += [(s.id, e.at) for s in doc.scenes for e in s.fx]
    assert ats
    for scene_id, at in ats:
        assert PRESENTER_AT_RE.match(at), at
        assert not at.startswith("@"), at
        assert trigger_index([w.text for w in words[scene_id]], at) is not None, (scene_id, at)


def test_same_seed_same_plan_and_replanning_is_stable(isolated_settings):
    doc = _mock_project(LONG)
    style = get_style(doc.style_id, doc.style_overrides)
    a, b = _fresh(doc), _fresh(doc)
    edit_plan.plan(a, style, Pool(), recent=set())
    edit_plan.plan(b, style, Pool(), recent=set())
    assert a.model_dump(mode="json") == b.model_dump(mode="json")
    again = a.model_copy(deep=True)
    edit_plan.plan(again, style, Pool(), recent=set())
    assert again.model_dump(mode="json") == a.model_dump(mode="json")
    other = _fresh(doc).model_copy(update={"seed": doc.seed + 1})
    edit_plan.plan(other, style, Pool(), recent=set())
    assert [s.fx for s in other.scenes] != [s.fx for s in a.scenes]


def test_intensity_zero_has_less_than_half_the_events_of_two(isolated_settings):
    doc = _mock_project(LONG)
    style = get_style(doc.style_id, doc.style_overrides)
    calm, normal = _fresh(doc), _fresh(doc)
    edit_plan.plan(calm, style, Pool(), intensity=0, recent=set())
    edit_plan.plan(normal, style, Pool(), intensity=2, recent=set())
    # O punch do cartão parado (`CARD_STILL_S`) é trava, igual em toda intensidade: conta só o que a intensidade escolhe.
    cards = {s.id for s in doc.scenes if s.kind in edit_plan.CARD_KINDS}
    n_calm = len([1 for sid, _ in _fx(calm) if sid not in cards])
    n_normal = len([1 for sid, _ in _fx(normal) if sid not in cards])
    assert n_normal > 10 and n_calm < n_normal / 2, (n_calm, n_normal)
    assert [e for sid, e in _fx(calm, "punch") if sid in cards] == [e for sid, e in _fx(normal, "punch") if sid in cards]
    hot = _fresh(doc)
    edit_plan.plan(hot, style, Pool(), intensity=3, recent=set())
    assert len(_fx(hot)) > n_normal


def test_editor_off_does_nothing(isolated_settings):
    style = get_style(LONG, {"edit": {"grammar": {"editor": "off"}}})
    doc = _doc([LONG_TEXT, LONG_TEXT])
    before = doc.model_dump(mode="json")
    assert edit_plan.plan(doc, style, Pool(), recent=set()) is None
    assert doc.model_dump(mode="json") == before


# --- documento montado à mão -----------------------------------------------------------------------------------------


def test_marks_are_completed_only_when_missing(isolated_settings):
    style = get_style(LONG)
    doc = _doc(["Você sabia que o farol tem 1887 degraus?", LONG_TEXT, "A morte do faroleiro nunca foi explicada."])
    mine = BeatMark(role="joke", emphasis=["Santa"], energy=3)
    doc.scenes[1].mark = mine
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert doc.scenes[0].mark is not None and doc.scenes[0].mark.role == "hook"
    assert "1887" in doc.scenes[0].mark.emphasis
    assert doc.scenes[1].mark == mine
    assert doc.scenes[2].mark is not None and doc.scenes[2].mark.serious


def _fake_sfx_library(items: list[SimpleNamespace], picks: list[tuple] | None = None) -> SimpleNamespace:
    """Biblioteca de SFX falsa no formato de `media.sfx_library` (`get`, `pick` e `SfxItem.auto`)."""
    by_id = {i.id: i for i in items}

    def pick(kind, seed, exclude=(), *, tonal_ok=True):
        if picks is not None:
            picks.append((kind, frozenset(exclude), tonal_ok))
        options = [i for i in items if i.kind == kind and i.auto(tonal_ok) and i.id not in set(exclude)]
        return options[seed % len(options)] if options else None

    return SimpleNamespace(get=by_id.get, pick=pick)


def _sfx_item(lib_id: str, kind: str, *, license: str = "cc0", tonal: bool = False, tags: tuple = (),
              file: str | None = "x.wav") -> SimpleNamespace:
    def auto(tonal_ok: bool = True) -> bool:
        return license != "desconhecida" and (tonal_ok or not tonal)

    return SimpleNamespace(id=lib_id, kind=kind, license=license, tonal=tonal, tags=list(tags), file=file, auto=auto)


def test_serious_beat_never_gets_a_meme_and_gags_use_the_library(isolated_settings, monkeypatch):
    calls: list[tuple] = []

    def pick(tag, seed, exclude, *, presenter=None):
        calls.append((tag, seed, frozenset(exclude), presenter))
        return SimpleNamespace(id="meme-susto", sfx_id="sfx-susto", max_ms=1200, media="png",
                               file="library/memes/meme-susto.png")

    monkeypatch.setattr(edit_plan, "_meme_module", lambda: SimpleNamespace(pick=pick))
    monkeypatch.setattr(edit_plan, "_sfx_module", lambda: _fake_sfx_library([_sfx_item("sfx-susto", "boom")]))
    monkeypatch.setattr(edit_plan._Planner, "_presenter", lambda self: "avatar-do-canal")
    style = get_style(LONG)
    texts = [LONG_TEXT] * 6
    doc = _doc(texts)
    gag = GagHint(payoff="meme", tag="susto", at="desapareceu")
    for i, scene in enumerate(doc.scenes):
        scene.mark = BeatMark(role="joke", serious=i % 2 == 1, gag=gag, emphasis=["faroleiro"])
    edit_plan.plan(doc, style, Pool(), recent={"meme-velho"})
    serious = {s.id for s in doc.scenes if s.mark and s.mark.serious}
    comic = [(sid, e) for sid, e in _fx(doc) if e.type in ("meme", "freeze", "shake")]
    assert comic and all(sid not in serious for sid, _e in comic)
    memes = _fx(doc, "meme")
    assert memes and all(e.params["lib_id"] == "meme-susto" and e.at == "desapareceu" for _s, e in memes)
    # o renderer desenha o meme pelo arquivo (`src` PNG ou `video` WebM), não pelo id
    assert all(e.params["src"] == "library/memes/meme-susto.png" and "video" not in e.params for _s, e in memes)
    assert all(400 <= (e.dur_ms or 0) <= 1500 for _s, e in memes)
    assert any(e.params.get("lib_id") == "sfx-susto" for _s, e in _fx(doc, "sfx"))
    assert calls and all("meme-velho" in exclude for _t, _seed, exclude, _p in calls)  # memória dos últimos vídeos
    assert all(p == "avatar-do-canal" for *_x, p in calls)  # as poses do avatar entram como reação
    # meme a 20 s de distância no longo
    times = sorted(t for _sid, kind, t in _event_times(doc, style) if kind == "meme")
    assert len(times) == len(memes)
    assert all(b - a >= 20 for a, b in zip(times, times[1:], strict=False))


def test_webm_meme_goes_as_video_and_meme_without_file_is_not_drawn_empty(isolated_settings, monkeypatch):
    item = SimpleNamespace(id="danca", sfx_id=None, max_ms=900, media="webm", file="library/memes/danca.webm")
    monkeypatch.setattr(edit_plan, "_meme_module", lambda: SimpleNamespace(pick=lambda *a, **k: item))
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, LONG_TEXT])
    doc.scenes[1].mark = BeatMark(role="joke", gag=GagHint(payoff="meme", tag="danca", at="trancada"))
    edit_plan.plan(doc, style, Pool(), recent=set())
    meme = next(e for _s, e in _fx(doc, "meme"))
    assert meme.params["video"] == "library/memes/danca.webm" and "src" not in meme.params
    # item sem arquivo (biblioteca velha): sai o "meme sem meme", nunca um meme vazio
    blank = SimpleNamespace(id="vazio", sfx_id=None, max_ms=900)
    monkeypatch.setattr(edit_plan, "_meme_module", lambda: SimpleNamespace(pick=lambda *a, **k: blank))
    other = _doc([LONG_TEXT, LONG_TEXT])
    other.scenes[1].mark = BeatMark(role="joke", gag=GagHint(payoff="meme", tag="danca", at="trancada"))
    edit_plan.plan(other, style, Pool(), recent=set())
    assert not _fx(other, "meme") and _fx(other, "freeze")


def test_meme_without_meme_uses_the_library_template(isolated_settings, monkeypatch):
    def template_events(name, at, line=""):
        assert name == "record_scratch"
        return [FxEvent(type="freeze", at=at, dur_ms=1100), FxEvent(type="sfx", at=at, params={"lib_id": "scratch"}),
                FxEvent(type="text_pop", at=at, dur_ms=1100, params={"text": line})]

    lib = SimpleNamespace(pick=lambda *a, **k: None, template_for=lambda tag, seed: SimpleNamespace(id="record_scratch"),
                          template_events=template_events)
    monkeypatch.setattr(edit_plan, "_meme_module", lambda: lib)
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, LONG_TEXT])
    doc.scenes[1].mark = BeatMark(role="joke", gag=GagHint(payoff="meme", tag="pera", at="trancada", line="Pera aí"))
    edit_plan.plan(doc, style, Pool(), recent=set())
    fx = {e.type: e for e in doc.scenes[1].fx if e.at == "trancada"}
    assert {"freeze", "sfx", "text_pop"} <= set(fx), fx
    assert fx["freeze"].dur_ms == 1100 and fx["text_pop"].params["text"] == "Pera aí"
    assert fx["sfx"].params["purpose"] == "gag" and "lib_id" not in fx["sfx"].params  # "scratch" é procedural
    assert not _fx(doc, "meme")


def test_gag_without_library_is_the_meme_without_meme(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, LONG_TEXT])
    doc.scenes[1].mark = BeatMark(role="joke", gag=GagHint(payoff="freeze", at="trancada", line="Plot twist!"))
    edit_plan.plan(doc, style, Pool(), recent=set())
    fx = [e for e in doc.scenes[1].fx if e.at == "trancada"]
    types = {e.type for e in fx}
    assert {"freeze", "punch", "sfx", "text_pop"} <= types, types
    assert next(e for e in fx if e.type == "punch").params["scale"] == 1.3
    assert next(e for e in fx if e.type == "text_pop").params["text"] == "Plot twist"
    assert next(e for e in fx if e.type == "sfx").params["purpose"] == "gag"
    assert not _fx(doc, "meme")


def test_reveal_cuts_on_the_emphasis_with_punch_sfx_and_music_drop(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, "E o que encontraram lá dentro foi um diário escondido atrás da parede."])
    doc.scenes[1].mark = BeatMark(role="reveal", emphasis=["diário"])
    edit_plan.plan(doc, style, Pool(), recent=set())
    scene = doc.scenes[1]
    assert "diário" in [s.at for s in scene.shots]
    at_word = {e.type for e in scene.fx if e.at == "diário"}
    assert {"punch", "sfx"} <= at_word
    # a queda da trilha termina na palavra da revelação (o gatilho é o FIM da queda, contrato do timeline-fx)
    drop = [e for e in scene.fx if e.type == "music_drop"]
    assert drop and drop[0].at == "diário" and 300 <= (drop[0].dur_ms or 0) <= 600
    assert not [e for e in doc.scenes[0].fx if e.type == "music_drop"]


def _counters(doc: ProjectDoc) -> list[tuple[str, str, str]]:
    return [(sid, e.params["text"], e.at) for sid, e in _fx(doc, "counter")]


def test_list_counter_follows_the_spoken_item_not_the_scene(isolated_settings):
    """"5 sinais" em 6 cenas: o contador saía 1/6..6/6 pela posição da cena ("3/6" no "Dois:", nada no "Quatro").
    Agora sai na palavra do número dito, com o total de itens ditos."""
    style = get_style(LONG)
    doc = _doc([
        LONG_TEXT,
        "Pra não cair, cinco sinais. Um: até a Rockstar anunciar no site oficial dela, não existe versão de PC.",
        "Então todo download de PC que aparece hoje é golpe, não importa o site.",
        "Dois: o tamanho não bate, tipo um arquivinho minúsculo ou uma imagem gigante.",
        "Três: pediu cripto ou cartão-presente, fuja. Quatro: site que imita a Rockstar com botão de jogar agora.",
        "Cinco, o maior alarme de todos: pediu pra desativar o antivírus antes de instalar.",
        "Nenhum jogo legítimo precisa disso, nem os da loja oficial do seu console.",
    ])
    for scene in doc.scenes[1:]:
        scene.mark = BeatMark(role="list")
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert _counters(doc) == [("scn1", "1/5", "Um"), ("scn3", "2/5", "Dois"), ("scn4", "3/5", "Três"),
                              ("scn4", "4/5", "Quatro"), ("scn5", "5/5", "Cinco")]


def test_list_counter_starts_on_the_beat_before_and_skips_incoherent_counts(isolated_settings):
    style = get_style(LONG)
    # "Primeira:" vem na batida antes da lista; "Segunda:" está nela.
    doc = _doc([
        LONG_TEXT,
        "Mas tem duas pegadinhas. Primeira: no lançamento, a nuvem é só para os consoles da marca.",
        "O próprio fabricante confirmou que o computador fica de fora desse serviço por enquanto.",
        "Segunda: o jogo é comprado à parte, e para usar a nuvem você ainda precisa da assinatura.",
    ])
    for scene in doc.scenes[2:]:
        scene.mark = BeatMark(role="list")
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert _counters(doc) == [("scn1", "1/2", "Primeira"), ("scn3", "2/2", "Segunda")]

    # Só "Primeiro:" dito (o resto da lista está longe): número nenhum fecha com a fala, então não há contador.
    lone = _doc([
        LONG_TEXT,
        "São três jeitos, e um deles pode destruir todos os seus arquivos de uma vez só.",
        "Primeiro: o site falso de demonstração, que a empresa de segurança encontrou em agosto.",
        "Com nomes tipo demonstração grátis, copiando a divulgação oficial do estúdio do jogo.",
    ])
    for scene in lone.scenes[2:]:
        scene.mark = BeatMark(role="list")
    edit_plan.plan(lone, style, Pool(), recent=set())
    assert _counters(lone) == []


def test_list_counter_without_spoken_numbers_counts_the_scenes(isolated_settings):
    style = get_style(LONG)
    doc = _doc([
        LONG_TEXT,
        "Ativa a verificação em duas etapas em todas as contas onde ela ainda não estiver ligada.",
        "E se pagou alguma coisa, fala com o seu banco o quanto antes e guarda o comprovante.",
    ])
    for scene in doc.scenes[1:]:
        scene.mark = BeatMark(role="list")
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert [(sid, text) for sid, text, _at in _counters(doc)] == [("scn1", "1/2"), ("scn2", "2/2")]


def test_list_counter_follows_a_countdown(isolated_settings):
    style = get_style(LONG)
    doc = _doc([
        LONG_TEXT,
        "Número três: o farol que guiou navios por quase um século inteiro na costa sul.",
        "Número dois: a ilha que ninguém visita desde que a marinha abandonou a construção.",
        "Número um: a porta trancada por dentro na noite em que o faroleiro desapareceu.",
    ])
    for scene in doc.scenes[1:]:
        scene.mark = BeatMark(role="list")
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert [(sid, text) for sid, text, _at in _counters(doc)] == [("scn1", "3/3"), ("scn2", "2/3"), ("scn3", "1/3")]


def test_locked_and_user_items_are_preserved(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, LONG_TEXT, LONG_TEXT])
    user_fx = FxEvent(type="text_pop", at="farol", params={"text": "FAROL"}, origin="user")
    locked_fx = FxEvent(type="meme", at="@500", params={"lib_id": "x"}, locked=True)
    planner_fx = FxEvent(type="flash", at="ilha", origin="planner")
    doc.scenes[0].fx = [user_fx, locked_fx, planner_fx]
    user_shots = [SubShot(), SubShot(at="guiou", framing=ShotFraming(scale=1.5), origin="user")]
    doc.scenes[1].shots = user_shots
    doc.scenes[2].locked = True
    doc.scenes[2].fx = [FxEvent(type="punch", at="farol")]
    before_locked = doc.scenes[2].model_dump(mode="json")
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert user_fx in doc.scenes[0].fx and locked_fx in doc.scenes[0].fx
    assert planner_fx not in doc.scenes[0].fx  # o do planejador é refeito
    assert not [e for e in doc.scenes[0].fx if e.type == "text_pop" and e.at == "farol" and e.origin == "planner"]
    assert doc.scenes[1].shots == user_shots
    assert doc.scenes[2].model_dump(mode="json") == before_locked


def test_quotas_scale_and_spacing(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 8)
    edit_plan.plan(doc, style, Pool(), recent=set())
    shots = edit_plan.timed_shots(doc, style)
    starts = {sid: a for sid, a, _d, _p in reversed(shots)}
    planner = edit_plan._Planner(doc, style, grammar_for(style), Pool(), set())
    times: dict[str, list[float]] = {}
    for beat in planner.beats:
        for e in beat.scene.fx:
            t = planner._time_of(beat, e.at)
            assert t <= beat.end - edit_plan.TAIL_S + 1e-6
            times.setdefault(e.type, []).append(t)
    for fx_type, values in times.items():
        if fx_type in ("punch", "text_pop", "sfx", "shake"):
            values.sort()
            assert all(b - a >= edit_plan.SPACING_S - 1e-6 for a, b in zip(values, values[1:], strict=False)), fx_type
    assert starts


def test_pool_clip_and_capture_sources(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 5)
    clip = MediaRef(kind="clip", source="youtube", asset="uploads/prj_teste/media/a.jpg",
                    video="uploads/prj_teste/media/a.mp4", start_s=10.0, dur_s=60.0, tier="official")
    capture = MediaRef(kind="capture", source="capture", asset="uploads/prj_teste/media/c.jpg",
                       rects=[[0.1, 0.2, 0.5, 0.1]], tier="capture")
    doc.scenes[1].visual = Visual(type="upload", media=clip)
    doc.scenes[1].mark = BeatMark(role="aside")
    doc.scenes[2].visual = Visual(type="upload", media=capture)
    pool = Pool(items=[
        PoolItem(id=f"p{i}", tier=tier, media="image", desc="farol", tags=["farol", "ilha"],
                 path=f"uploads/prj_teste/pool/{i}.jpg") for i, tier in enumerate(["free", "stock", "official"], 1)
    ] + [PoolItem(id="p9", tier="meme", media="meme", tags=["farol"], path="uploads/prj_teste/pool/m.png")])
    edit_plan.plan(doc, style, pool, recent=set())
    pooled = [sh for s in doc.scenes for sh in s.shots if sh.source == "pool"]
    assert pooled and all(sh.pool_id in {"p1", "p2", "p3"} for sh in pooled)
    clip_shots = doc.scenes[1].shots
    assert len(clip_shots) >= 2 and all(sh.motion == "none" for sh in clip_shots if sh.source == "self")
    ins = [sh.clip_in for sh in clip_shots if sh.source == "self" and sh.clip_in is not None]
    assert ins == sorted(ins) and all(10.0 < x <= 70.0 for x in ins)
    cap = doc.scenes[2]
    assert all(sh.motion == "static" for sh in cap.shots if sh.source == "self")
    kinds = {e.type: e for e in cap.fx}
    assert "highlight" in kinds and "zoom_rect" in kinds
    assert kinds["highlight"].params["rect"] == [0.1, 0.2, 0.5, 0.1]
    # o zoom acompanha o marca-texto (outro `zoom_rect` pode vir depois, quebrando o plano parado da captura)
    assert any(e.type == "zoom_rect" and e.at == kinds["highlight"].at for e in cap.fx)
    assert "punch" not in kinds


def _window(framing: ShotFraming | None) -> tuple[float, float, float, float]:
    """A janela que o renderer mostra (`framingBox`): x0, y0, x1, y1 de 0 a 1."""
    if framing is None or framing.scale <= 1.0001:
        return 0.0, 0.0, 1.0, 1.0
    win = 1 / framing.scale
    left = min(max(framing.x - win / 2, 0.0), 1 - win)
    top = min(max(framing.y - win / 2, 0.0), 1 - win)
    return left, top, left + win, top + win


def test_capture_never_crops_the_marked_headline(isolated_settings):
    """Manchete larga (GamesBeat: de 5% a 65% da largura): o jump cut a 1,25 com centro sorteado e o punch no meio do
    quadro cortavam a primeira letra ("rand Theft"). Na captura, a janela fechada contém o trecho com folga, e o punch
    não entra (quem aproxima é o `zoom_rect`, que enquadra o trecho)."""
    style = get_style(SHORT)
    rect = [0.05, 0.2, 0.6, 0.12]
    doc = _doc([LONG_TEXT] * 6, style_id=SHORT)
    for i, scene in enumerate(doc.scenes):
        scene.visual = Visual(type="upload", media=MediaRef(kind="capture", source="capture", tier="capture",
                                                            asset=f"uploads/prj_teste/media/c{i}.jpg", rects=[rect]))
    edit_plan.plan(doc, style, Pool(), recent=set())
    framings = [sh.framing for s in doc.scenes for sh in s.shots if sh.source == "self"]
    assert any(f is not None and f.scale > 1 for f in framings)  # o jump cut continua quando cabe
    for framing in framings:
        x0, y0, x1, y1 = _window(framing)
        m = edit_plan.TEXT_MARGIN - 0.002
        assert x0 <= rect[0] - m and x1 >= rect[0] + rect[2] + m and y0 <= rect[1] - m and y1 >= rect[1] + rect[3] + m
    assert all(sh.motion == "static" for s in doc.scenes for sh in s.shots if sh.source == "self")
    assert not _fx(doc, "punch")
    assert _fx(doc, "zoom_rect")


def test_long_still_capture_breaks_with_zoom_rect_not_punch(isolated_settings):
    """Captura cujo trecho não cabe na janela do jump cut (sem corte) e sem punch: o plano parado passava de
    `max_still_s` (7,4 s no ponta a ponta). Quem quebra é o `zoom_rect` no trecho, já a 80% do limite (a duração aqui
    é estimada e a captura não tem a folga dos cortes)."""
    style = get_style(LONG)
    grammar = grammar_for(style)
    rects = [[0.05, 0.1, 0.9, 0.1], [0.05, 0.75, 0.9, 0.1]]  # do alto ao pé da página: não cabe na janela de 1/1,25
    doc = _doc([LONG_TEXT] * 4)
    for i, scene in enumerate(doc.scenes):
        scene.visual = Visual(type="upload", media=MediaRef(kind="capture", source="capture", tier="capture",
                                                            asset=f"uploads/prj_teste/media/c{i}.jpg", rects=rects))
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert not _fx(doc, "punch")
    assert all(sh.framing is None or sh.framing.scale <= 1.0 for s in doc.scenes for sh in s.shots)
    planner = edit_plan._Planner(doc, style, grammar, Pool(), set())
    for beat in planner.beats:
        marks = sorted({beat.start, beat.end, *(planner._time_of(beat, sh.at) for sh in beat.scene.shots if sh.at),
                        *(planner._time_of(beat, e.at) for e in beat.scene.fx if e.type == "zoom_rect")})
        assert all(b - a <= grammar.max_still_s + 1e-6 for a, b in zip(marks, marks[1:], strict=False)), marks


def test_capture_without_marked_text_is_never_zoomed(isolated_settings):
    """Captura sem o retângulo do trecho (página em que a manchete não foi achada): sem saber onde está o texto, nada
    de reenquadrar, de câmera andando nem de punch."""
    style = get_style(SHORT)
    doc = _doc([LONG_TEXT] * 6, style_id=SHORT)
    for i, scene in enumerate(doc.scenes):
        scene.visual = Visual(type="upload", media=MediaRef(kind="capture", source="capture", tier="capture",
                                                            asset=f"uploads/prj_teste/media/c{i}.jpg"))
    edit_plan.plan(doc, style, Pool(), recent=set())
    shots = [sh for s in doc.scenes for sh in s.shots if sh.source == "self"]
    assert all(sh.framing is None or sh.framing.scale <= 1.0 for sh in shots)
    assert all(sh.motion == "static" for sh in shots)
    assert not _fx(doc, "punch")


def test_fit_window_keeps_the_rect_and_the_margin():
    assert edit_plan._fit_window([], 1.25, 0.5, 0.5) is None
    assert edit_plan._fit_window([[0.0, 0.1, 0.9, 0.1]], 1.25, 0.5, 0.5) is None  # mais largo que a janela
    x, y = edit_plan._fit_window([[0.05, 0.2, 0.6, 0.12]], 1.25, 0.65, 0.5)
    assert (x, y) == (0.41, 0.5)  # empurrada para a esquerda até a manchete caber
    assert edit_plan._fit_window([[0.3, 0.3, 0.2, 0.1]], 1.25, 0.45, 0.4) == (0.45, 0.4)  # cabe: fica onde estava


def test_reframes_are_capped_per_asset(isolated_settings):
    style = get_style(SHORT)
    doc = _doc([LONG_TEXT] * 3, style_id=SHORT)
    for scene in doc.scenes[1:]:
        scene.visual = Visual(reuse_of="scn0")
    edit_plan.plan(doc, style, Pool(), recent=set())
    reframes = [sh for s in doc.scenes for sh in s.shots[1:] if sh.source == "self"]
    assert 1 <= len(reframes) <= edit_plan.MAX_REFRAMES


def test_camera_shares_and_no_long_still_shot(isolated_settings):
    style = get_style(LONG)
    grammar = grammar_for(style)
    doc = _doc([LONG_TEXT] * 10)
    for i, scene in enumerate(doc.scenes):
        scene.visual = Visual(prompt=f"cena {i}", shot="wide")
    edit_plan.plan(doc, style, Pool(), recent=set())
    motions = [sh.motion for s in doc.scenes for sh in s.shots if sh.motion != "none"]
    assert motions
    slow = sum(1 for m in motions if m in edit_plan.SLOW_MOTIONS) / len(motions)
    static = sum(1 for m in motions if m == "static") / len(motions)
    assert slow <= grammar.slow_zoom_share + 1e-9 and static >= grammar.static_share_min - 1e-9
    assert all(m in ("static", "push", "drift", "pan_left", "pan_right") for m in motions)
    # nenhum trecho parado (sem corte nem punch) passa de max_still_s
    planner = edit_plan._Planner(doc, style, grammar, Pool(), set())
    for beat in planner.beats:
        marks = sorted({beat.start, beat.end, *(planner._time_of(beat, sh.at) for sh in beat.scene.shots if sh.at),
                        *(planner._time_of(beat, e.at) for e in beat.scene.fx if e.type in ("punch", "zoom_rect"))})
        assert all(b - a <= grammar.max_still_s + 1e-6 for a, b in zip(marks, marks[1:], strict=False))


def test_shot_durations_avoid_three_alike_in_a_row(isolated_settings):
    style = get_style(SHORT)
    doc = _mock_project(SHORT)
    durs = [d for _s, _a, d, _p in edit_plan.timed_shots(doc, style)]
    alike = sum(1 for a, b, c in zip(durs, durs[1:], durs[2:], strict=False)
                if edit_plan._similar(a, b) and edit_plan._similar(b, c))
    assert alike <= len(durs) // 10  # a regra vale para os cortes; a cena inteira (sem corte) pode coincidir


# --- memória e rota ----------------------------------------------------------------------------------------------------


def test_recent_lib_ids_reads_the_channel_history(isolated_settings):
    client = TestClient(create_app())
    cid = client.post("/api/channels", json={"name": "Canal"}).json()["id"]
    old = service.create_project(title="Antigo", style_id=LONG, channel_id=cid)
    old_doc = service.get_doc(old.id).model_copy(deep=True)
    old_doc.segments = [Seg(id="s1", text={"pt-BR": "um farol"})]
    old_doc.scenes = [Scene(id="c1", segment_id="s1", fx=[
        FxEvent(type="meme", at="farol", params={"lib_id": "meme-a"}),
        FxEvent(type="sfx", at="farol", params={"kind": "impact", "lib_id": "sfx-b"}),
        FxEvent(type="text_pop", at="farol", params={"lib_id": "ignorado"}),
    ])]
    service.commit(old.id, old_doc, "fx")
    other_channel = service.create_project(title="Outro", style_id=LONG)
    new = service.create_project(title="Novo", style_id=LONG, channel_id=cid)
    assert edit_plan.recent_lib_ids(service.get_doc(new.id)) == {"meme-a", "sfx-b"}
    assert edit_plan.recent_lib_ids(service.get_doc(other_channel.id)) == set()


def test_reedit_route_is_deterministic_and_saves_the_intensity(isolated_settings):
    doc = _mock_project(LONG)
    save_pool(doc.id, Pool(items=[PoolItem(id="p1", tier="free", tags=["farol"], path="uploads/x/p1.jpg")]))
    client = TestClient(create_app())
    first = client.post(f"/api/projects/{doc.id}/reedit", json={"intensity": 1})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["changed"] and body["intensity"] == 1 and body["version"]
    saved = service.get_doc(doc.id)
    assert saved.style_overrides["edit"]["grammar"]["intensity"] == 1
    assert body["summary"]["fx_total"] < edit_plan.summary(doc)["fx_total"]
    second = client.post(f"/api/projects/{doc.id}/reedit", json={"intensity": 1}).json()
    assert second["changed"] is False and second["version"] is None
    assert client.post(f"/api/projects/{doc.id}/reedit", json={"intensity": 5}).status_code == 422
    assert client.post("/api/projects/nao-existe/reedit", json={}).status_code == 404
    no_body = client.post(f"/api/projects/{doc.id}/reedit")
    assert no_body.status_code == 200 and no_body.json()["intensity"] == 1


def test_reedit_with_editor_off_changes_nothing(isolated_settings):
    doc = service.create_project(title="Farol", style_id=LONG, topic="o farol")
    current = service.get_doc(doc.id).model_copy(deep=True)
    current.style_overrides = {"edit": {"grammar": {"editor": "off"}}}
    service.commit(doc.id, current, "editor desligado")
    resp = TestClient(create_app()).post(f"/api/projects/{doc.id}/reedit", json={})
    assert resp.status_code == 200 and resp.json()["editor"] == "off" and resp.json()["version"] is None


@pytest.mark.parametrize("style_id", [LONG, SHORT])
def test_storyboard_job_runs_the_planner(isolated_settings, style_id):
    doc = _mock_project(style_id)
    assert edit_plan.summary(doc)["fx_total"] > 0
    assert any(len(s.shots) > 1 for s in doc.scenes)


# --- cotas, travas e contratos (revisão) -------------------------------------------------------------------------------


def _per_min(doc: ProjectDoc, style, fx_type: str) -> float:
    return len(_fx(doc, fx_type)) / (_duration(doc, style) / 60)


def test_short_quotas_are_within_tolerance(isolated_settings):
    doc = _mock_project(SHORT)
    style = get_style(doc.style_id, doc.style_overrides)
    grammar = grammar_for(style)
    for fx_type, rate in (("punch", grammar.punch_per_min), ("text_pop", grammar.text_pop_per_min),
                          ("sfx", grammar.sfx_per_min)):
        per_min = _per_min(doc, style, fx_type)
        assert abs(per_min - rate) <= grammar.tolerance * rate, (fx_type, per_min, rate)


def test_budget_rounds_the_total_then_splits_hook_and_body(isolated_settings):
    grammar = grammar_for(get_style(SHORT))
    budget = edit_plan._Budget(grammar, 1.0, 61.0)
    # total pelo mais perto: 2/min no corpo + 4/min no gancho de 5 s = 2,2 → 2 (o floor do corpo dava 1)
    assert sum(budget.left["text_pop"]) == 2
    assert sum(budget.left["punch"]) == round(3 * 56 / 60 + 6 * 5 / 60)
    calm = edit_plan._Budget(grammar, 0.5, 61.0)
    assert all(sum(calm.left[k]) <= sum(budget.left[k]) / 2 + 1 for k in ("punch", "text_pop", "sfx"))


def test_hook_window_has_its_own_quota(isolated_settings):
    style = get_style(LONG, {"edit": {"grammar": {"hook": {"window_s": 10, "punch_per_min": 12,
                                                             "text_pop_per_min": 0, "sfx_per_min": 0}}}})
    grammar = grammar_for(style)
    doc = _doc([LONG_TEXT] * 6)
    edit_plan.plan(doc, style, Pool(), recent=set())
    in_hook = [(kind, t) for _sid, kind, t in _event_times(doc, style) if t < 10.0]
    assert sum(1 for k, _t in in_hook if k == "punch") <= round(12 * 10 / 60)  # 2 na janela, sem empréstimo
    assert not [k for k, _t in in_hook if k in ("text_pop", "sfx")]
    body = [t for _sid, kind, t in _event_times(doc, style) if kind == "text_pop" and t >= 10.0]
    assert len(body) >= grammar.text_pop_per_min * (_duration(doc, style) - 10) / 60 * (1 - grammar.tolerance)


def _wide_pool(n: int = 30, tiers: tuple[str, ...] = ("free", "stock", "capture")) -> Pool:
    return Pool(items=[PoolItem(id=f"p{i}", tier=tiers[i % len(tiers)], tags=["farol", "ilha"],
                                path=f"uploads/prj_teste/pool/{i}.jpg") for i in range(n)])


def test_switch_quota_is_spread_over_the_whole_video(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 18)
    pool = _wide_pool()
    edit_plan.plan(doc, style, pool, recent=set())
    total = _duration(doc, style)
    times = sorted(a for _sid, a, _d, p in edit_plan.timed_shots(doc, style, pool) if p != "ai")
    assert times and len(times) <= grammar_for(style).switch_per_min * total / 60 + 1
    # os subplanos do acervo chegam ao meio e ao fim do vídeo (a cota não se esgota no começo)
    assert any(total / 3 <= t < 2 * total / 3 for t in times), times
    assert any(t >= 2 * total / 3 for t in times), times


def test_at_most_three_shots_in_a_row_with_the_same_prov_when_the_pool_allows(isolated_settings):
    style = get_style(LONG, {"edit": {"grammar": {"switch_per_min": 30}}})
    doc = _doc([LONG_TEXT] * 4)
    pool = _wide_pool(60)
    edit_plan.plan(doc, style, pool, recent=set())
    provs = [p for _s, _a, _d, p in edit_plan.timed_shots(doc, style, pool)]
    run = longest = 1
    for a, b in zip(provs, provs[1:], strict=False):
        run = run + 1 if a == b else 1
        longest = max(longest, run)
    assert len(provs) > 10 and longest <= edit_plan.MAX_SAME_PROV, provs


def test_pool_needs_real_affinity(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 3)
    loose = Pool(items=[PoolItem(id=f"p{i}", tier="free", tags=["farol", "carro"], path=f"uploads/x/{i}.jpg")
                        for i in range(10)])
    edit_plan.plan(doc, style, loose, recent=set())
    assert not [sh for s in doc.scenes for sh in s.shots if sh.source == "pool"]  # 1 etiqueta só não basta
    doc = _doc([LONG_TEXT] * 3)
    by_entity = Pool(items=[PoolItem(id=f"p{i}", tier="free", entities=["Santa Clara"], tags=["mapa"],
                                     path=f"uploads/x/{i}.jpg") for i in range(10)])
    edit_plan.plan(doc, style, by_entity, recent=set())
    assert [sh for s in doc.scenes for sh in s.shots if sh.source == "pool"]  # a entidade dita na fala basta


SHORT_TEXT = "O farol da ilha guiou navios por mais de um século, e os moradores ainda contam essa história."


def _shown(item: PoolItem) -> MediaRef:
    """A mídia anexada de um item do acervo (o arquivo no projeto e o `pool_id`)."""
    return MediaRef(kind="photo", source="upload", asset=f"uploads/prj_teste/media/{item.id}.jpg", tier=item.tier,
                    pool_id=item.id)


def test_pool_subshot_follows_the_distribution_over_time(isolated_settings):
    """O item que a cena vizinha já mostra não volta como subplano a menos de 60 s (antes só contavam os subplanos:
    a captura do gancho voltava como corte logo depois), e nenhum item passa de 2 aparições."""
    style = get_style(LONG, {"edit": {"grammar": {"switch_per_min": 30}}})
    shown, other = (PoolItem(id=f"p{i}", tier="free", tags=["farol", "ilha"], path=f"uploads/x/{i}.jpg")
                    for i in range(2))
    doc = _doc([SHORT_TEXT] * 6)
    doc.scenes[0].visual = Visual(type="upload", media=_shown(shown))
    pool = Pool(items=[shown, other])
    edit_plan.plan(doc, style, pool, recent=set())
    pooled = [(s.id, sh.pool_id) for s in doc.ordered_scenes() for sh in s.shots if sh.source == "pool"]
    assert pooled and all(pid == other.id for _sid, pid in pooled), pooled
    assert len({sid for sid, _pid in pooled}) <= 2  # 2 aparições do item (as cenas são seguidas e curtas)


def test_same_image_across_scenes_is_broken_by_the_pool_even_without_quota(isolated_settings):
    """A mesma imagem em cenas seguidas (a foto, o cartão sobre ela, a foto de novo: o "VI" neon de 3:30 a 3:57) passa
    do limite de mesma imagem: o planejador troca para o acervo mesmo sem cota de troca; reenquadrar não troca a
    imagem. Dentro do limite, nada é forçado."""
    from studio.workers.pipeline.storyboard_check import same_image_seconds

    style = get_style(LONG, {"edit": {"grammar": {"switch_per_min": 0}}})
    neon, other = (PoolItem(id=f"p{i}", tier="free", tags=["farol", "ilha"], path=f"uploads/x/{i}.jpg")
                   for i in range(2))
    pool = Pool(items=[neon, other])
    doc = _doc([SHORT_TEXT] * 4)
    for scene in doc.scenes[:3]:
        scene.visual = Visual(type="upload", media=_shown(neon))
    edit_plan.plan(doc, style, pool, recent=set())
    limit = same_image_seconds(style)
    planner = edit_plan._Planner(doc, style, grammar_for(style), pool, set())
    cuts = [(planner._time_of(b, sh.at), sh.pool_id) for b in planner.beats[:3] for sh in b.scene.shots
            if sh.source == "pool"]
    assert cuts and all(pid == other.id for _t, pid in cuts), cuts
    assert min(t for t, _pid in cuts) > limit - 1e-6  # só depois do limite seguido na mesma imagem
    # cada cena com a própria imagem (sem emenda entre cenas): nada é forçado sem cota
    doc = _doc([SHORT_TEXT] * 4)
    edit_plan.plan(doc, style, pool, recent=set())
    assert not [sh for s in doc.scenes for sh in s.shots if sh.source == "pool"]


def test_official_material_is_short_capped_and_out_of_the_hook(isolated_settings):
    style = get_style(LONG)
    sources = style.art.sources
    doc = _doc([LONG_TEXT] * 6)
    trailer = MediaRef(kind="clip", source="youtube", asset="uploads/prj_teste/media/t.jpg",
                       video="uploads/prj_teste/media/t.mp4", start_s=0.0, dur_s=600.0, tier="official")
    doc.scenes[2].visual = Visual(type="upload", media=trailer)
    doc.scenes[2].mark = BeatMark(role="breath")  # sem o respiro longo: é oficial
    pool = _wide_pool(40, ("official",))
    edit_plan.plan(doc, style, pool, recent=set())
    shots = edit_plan.timed_shots(doc, style, pool)
    official = [(sid, a, d) for sid, a, d, p in shots if p == "official"]
    assert official
    assert all(d <= sources.official_max_clip_s + 1e-6 for _sid, _a, d in official), official
    total = _duration(doc, style)
    from_pool = sum(d for sid, _a, d, p in shots if p == "official" and sid != doc.scenes[2].id)
    # o teto vale para a escolha do acervo (o que já passou dele não ganha mais oficial)
    assert from_pool <= sources.official_max_share * total + sources.official_max_clip_s
    window = edit_plan._Budget(grammar_for(style), 1.0, total).window
    assert all(p in sources.hook or p == "ai" for _sid, a, _d, p in shots if a < window)


def test_clip_without_duration_does_not_run_past_its_end(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 2)
    clip = MediaRef(kind="clip", source="youtube", asset="uploads/prj_teste/media/a.jpg",
                    video="uploads/prj_teste/media/a.mp4", start_s=3.0, dur_s=None, tier="free")
    doc.scenes[1].visual = Visual(type="upload", media=clip)
    edit_plan.plan(doc, style, Pool(), recent=set())
    shots = doc.scenes[1].shots
    assert not [sh for sh in shots if sh.source == "self" and sh.clip_in is not None]
    assert not [sh for sh in shots if sh.hold]
    doc = _doc([LONG_TEXT] * 2)
    doc.scenes[1].visual = Visual(type="upload", media=clip.model_copy(update={"dur_s": 6.0}))
    edit_plan.plan(doc, style, Pool(), recent=set())
    ins = [sh.clip_in for sh in doc.scenes[1].shots if sh.clip_in is not None]
    assert all(x <= 3.0 + 6.0 - edit_plan.CLIP_END_PAD_S for x in ins), ins


def test_sfx_palette_and_library_respect_license_tonal_and_serious(isolated_settings, monkeypatch):
    items = [
        _sfx_item("tapa-livre", "impact"), _sfx_item("tapa-sem-licenca", "impact", license="desconhecida"),
        _sfx_item("apito-tonal", "pop", tonal=True), _sfx_item("buzina-comica", "pop", tags=("comico",)),
    ]
    monkeypatch.setattr(edit_plan, "_sfx_module", lambda: _fake_sfx_library(items))
    palette = {i.id: 1.0 for i in items}
    style = get_style(LONG, {"edit": {"grammar": {"sfx_palette": palette, "sfx_tonal_ok": False}}})
    doc = _doc([LONG_TEXT] * 6)
    for i, scene in enumerate(doc.scenes):
        scene.mark = BeatMark(role="breath", serious=i % 2 == 0)
    edit_plan.plan(doc, style, Pool(), recent=set())
    used = [(sid, e.params.get("lib_id")) for sid, e in _fx(doc, "sfx")]
    ids = {lib for _sid, lib in used if lib}
    assert "tapa-livre" in ids
    assert not ids & {"tapa-sem-licenca", "apito-tonal"}
    serious = {s.id for s in doc.scenes if s.mark and s.mark.serious}
    assert not [sid for sid, lib in used if lib == "buzina-comica" and sid in serious]
    # sem a biblioteca, id de biblioteca da paleta não tem como ser conferido: fica de fora
    monkeypatch.setattr(edit_plan, "_sfx_module", lambda: None)
    doc = _doc([LONG_TEXT] * 3)
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert not [e for _s, e in _fx(doc, "sfx") if e.params.get("lib_id")]


def test_library_sfx_enter_by_kind_with_memory(isolated_settings, monkeypatch):
    picks: list[tuple] = []
    items = [_sfx_item("swish-meu", "swish"), _sfx_item("swish-velho", "swish"),
             _sfx_item("pop-embutido", "pop", file=None)]
    monkeypatch.setattr(edit_plan, "_sfx_module", lambda: _fake_sfx_library(items, picks))
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 4)
    edit_plan.plan(doc, style, Pool(), recent={"swish-velho"})
    ids = {e.params.get("lib_id") for _s, e in _fx(doc, "sfx")}
    assert "swish-meu" in ids and "swish-velho" not in ids
    assert "pop-embutido" not in ids  # procedural embutido: só o tipo
    assert picks and all("swish-velho" in exclude for _k, exclude, _t in picks)


def test_no_comic_sound_in_a_serious_beat(isolated_settings, monkeypatch):
    items = [_sfx_item("pop-risada", "pop", tags=("risada",)), _sfx_item("tick-seco", "tick")]
    monkeypatch.setattr(edit_plan, "_sfx_module", lambda: _fake_sfx_library(items))
    style = get_style(LONG)
    doc = _doc([LONG_TEXT] * 4)
    for scene in doc.scenes:
        scene.mark = BeatMark(role="joke", serious=True, gag=GagHint(payoff="freeze", at="trancada"))
    edit_plan.plan(doc, style, Pool(), recent=set())
    sfx = [e for _s, e in _fx(doc, "sfx")]
    assert sfx and not [e for e in sfx if e.params.get("purpose") == "gag"]
    assert not [e for e in sfx if e.params.get("lib_id") == "pop-risada"]
    assert not _fx(doc, "freeze") and not _fx(doc, "meme") and not _fx(doc, "shake")


def test_music_drop_is_spaced_and_reveal_is_not_every_exclamation(isolated_settings):
    style = get_style(LONG)
    texts = [f"Ninguém esperava o que veio depois da reunião de Genebra, a de número {i}!" for i in range(1, 19)]
    doc = _doc(texts)
    edit_plan.plan(doc, style, Pool(), recent=set())
    times = _event_times(doc, style)
    drops = sorted(t for _sid, kind, t in times if kind == "music_drop")
    punches = {t for _sid, kind, t in times if kind == "punch"}
    total = _duration(doc, style)
    assert len(drops) <= math.ceil(total / edit_plan.MUSIC_DROP_GAP_S) + 1, (len(drops), total)
    assert all(b - a >= edit_plan.MUSIC_DROP_GAP_S - 1e-6 for a, b in zip(drops, drops[1:], strict=False))
    assert all(t in punches for t in drops)  # só com o punch da revelação
    reveals = [s for s in doc.scenes if s.mark and s.mark.role == "reveal"]
    assert reveals and len(reveals) <= math.ceil(total / edit_plan.REVEAL_GAP_S) + 1
    plain = _doc([LONG_TEXT, "Que dia lindo para passear com a família!"])
    edit_plan.plan(plain, style, Pool(), recent=set())
    assert plain.scenes[1].mark is not None and plain.scenes[1].mark.role == "breath"


def test_text_pop_shows_the_whole_number(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, "O farol tinha trezentos e quarenta e dois degraus até a lanterna, e cada um rangia."])
    edit_plan.plan(doc, style, Pool(), recent=set())
    mark = doc.scenes[1].mark
    assert mark is not None and mark.origin == "planner"
    assert mark.emphasis[0] == "trezentos e quarenta e dois"
    pops = [e for e in doc.scenes[1].fx if e.type == "text_pop"]
    assert pops and pops[0].at == "trezentos" and pops[0].params["text"] == "342"
    assert not [e for e in doc.scenes[1].fx if e.at in ("quarenta", "dois")]


def test_inferred_proper_noun_ignores_the_capital_after_a_period(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, "O vento derrubou a torre. Depois, ninguém voltou para consertar a escada."])
    edit_plan.plan(doc, style, Pool(), recent=set())
    mark = doc.scenes[1].mark
    assert mark is not None and "Depois" not in mark.emphasis


def test_max_still_only_counts_the_shot_and_respects_user_cuts(isolated_settings):
    style = get_style(LONG)
    grammar = grammar_for(style)
    doc = _doc([LONG_TEXT, LONG_TEXT])
    cuts = [c for c in (edit_plan._at_text(w) for w in LONG_TEXT.split()[8::9]) if PRESENTER_AT_RE.match(c)]
    cuts = [c for c in dict.fromkeys(cuts) if trigger_index(LONG_TEXT.split(), c) is not None]
    user = [SubShot(origin="user"), *(SubShot(at=c, origin="user", framing=ShotFraming(scale=1.2)) for c in cuts)]
    doc.scenes[1].shots = user
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert doc.scenes[1].shots == user
    planner = edit_plan._Planner(doc, style, grammar, Pool(), set())
    beat = next(b for b in planner.beats if b.scene.id == "scn1")
    spans = planner._still_spans(beat)
    assert len(spans) == len(user)  # os cortes do usuário dividem a cena (antes era um trecho só)
    # nenhum trecho parado passa do limite sem um punch dentro dele
    times = sorted(planner._time_of(beat, e.at) for e in doc.scenes[1].fx if e.type in ("punch", "zoom_rect"))
    for a, b in spans:
        marks = [a, *(t for t in times if a < t < b), b]
        assert all(y - x <= grammar.max_still_s + 1e-6 for x, y in zip(marks, marks[1:], strict=False)), (a, b)


def test_reframe_limit_is_shared_by_the_root_with_a_pinned_image(isolated_settings):
    style = get_style(SHORT)
    doc = _doc([LONG_TEXT] * 3, style_id=SHORT)
    doc.scenes[0].visual = Visual(type="ai_image", prompt="farol", asset="uploads/prj_teste/fixa.jpg")
    for scene in doc.scenes[1:]:
        scene.visual = Visual(reuse_of="scn0")
    edit_plan.plan(doc, style, Pool(), recent=set())
    reframes = [sh for s in doc.scenes for sh in s.shots[1:] if sh.source == "self"]
    assert 1 <= len(reframes) <= edit_plan.MAX_REFRAMES


def test_shake_carries_the_key_the_renderer_reads(isolated_settings):
    style = get_style(LONG, {"edit": {"grammar": {"shake_per_min": 6}}})
    doc = _doc([LONG_TEXT] * 3)
    edit_plan.plan(doc, style, Pool(), recent=set())
    shakes = [e for _s, e in _fx(doc, "shake")]
    assert shakes and all(e.params["strength"] == e.params["amount"] for e in shakes)


def test_music_drop_contract_with_the_timeline_resolver(isolated_settings):
    """Com o `timeline-fx` junto: a queda resolvida termina no quadro do punch da revelação (o silêncio encosta no
    impacto)."""
    edit_resolve = pytest.importorskip("studio.core.edit_resolve")
    style = get_style(LONG)
    text = "E o que encontraram lá dentro foi um diário escondido atrás da parede."
    doc = _doc([LONG_TEXT, text])
    doc.scenes[1].mark = BeatMark(role="reveal", emphasis=["diário"])
    edit_plan.plan(doc, style, Pool(), recent=set())
    fps = 30
    words = [{"text": t, "from": 10 * i, "to": 10 * i + 9} for i, t in enumerate(text.split())]
    fx, _sfx, drops, _w = edit_resolve.resolve_fx(doc.scenes[1], words, fps, 10 * len(words), doc=doc, seed=doc.seed)
    punch = next(e for e in fx if e["type"] == "punch")
    assert drops and abs(drops[0]["at"] + drops[0]["dur"] - punch["from"] / fps) < 1e-3


# --- edição que ficou velha --------------------------------------------------------------------------------------------


def test_text_change_drops_the_planned_edit_of_that_scene(isolated_settings):
    from studio.core.project.ops import apply_ops, parse_ops

    style = get_style(LONG)
    doc = _doc([LONG_TEXT, LONG_TEXT])
    user_fx = FxEvent(type="text_pop", at="farol", params={"text": "FAROL"}, origin="user")
    doc.scenes[1].fx = [user_fx]
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert doc.scenes[0].mark is not None and doc.scenes[1].mark.origin == "planner"
    planned0 = list(doc.scenes[0].fx)
    new = apply_ops(doc, parse_ops([{"op": "update_segment_text", "segment_id": "seg1", "lang": "pt-BR",
                                     "text": "Outra fala, completamente diferente."}]))
    assert new.scenes[1].fx == [user_fx] and not new.scenes[1].shots and new.scenes[1].mark is None
    assert new.scenes[0].fx == planned0  # a outra cena não mudou
    # o Reeditar infere a marca de novo pela fala nova
    edit_plan.plan(new, style, Pool(), recent=set())
    assert new.scenes[1].mark is not None and new.scenes[1].mark.origin == "planner"


def test_media_change_drops_the_planned_shots(isolated_settings):
    from studio.core.project import media_edit

    style = get_style(LONG)
    doc = _doc([LONG_TEXT, LONG_TEXT])
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert doc.scenes[1].shots
    clip = MediaRef(kind="clip", source="youtube", asset="uploads/prj_teste/media/a.jpg",
                    video="uploads/prj_teste/media/a.mp4", dur_s=30.0)
    new = media_edit.attach_media(doc, "scn1", clip)
    assert not new.scenes[1].shots and not [e for e in new.scenes[1].fx if e.origin == "planner"]
    assert new.scenes[0].shots == doc.scenes[0].shots


def test_reedit_refreshes_the_planner_mark_but_keeps_the_storyboard_one(isolated_settings):
    style = get_style(LONG)
    doc = _doc([LONG_TEXT, "A morte do faroleiro nunca foi explicada."])
    mine = BeatMark(role="joke", emphasis=["faroleiro"])
    doc.scenes[1].mark = mine
    edit_plan.plan(doc, style, Pool(), recent=set())
    stale = doc.scenes[0].mark.model_copy(update={"emphasis": ["inexistente"]})
    doc.scenes[0].mark = stale
    edit_plan.plan(doc, style, Pool(), recent=set())
    assert doc.scenes[0].mark != stale and "inexistente" not in doc.scenes[0].mark.emphasis
    assert doc.scenes[1].mark == mine
