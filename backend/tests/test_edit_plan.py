"""Planejador de edição (ADR 0004 §3.6): subplanos e eventos determinísticos, cotas por minuto e travas.

Tudo offline: o projeto mock passa pelos jobs de roteiro e storyboard (que chamam o planejador), e os documentos
pequenos são montados à mão. A biblioteca de memes, quando entra, é trocada por uma falsa.
"""

from __future__ import annotations

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
    """Acervo-fixture: fotos e capturas do tema, de níveis variados (o coletor real é o item `acervo-real`)."""
    tiers = ["free", "stock", "official", "capture", "free", "official"]
    return Pool(items=[PoolItem(id=f"p{i}", tier=tier, desc="farol", tags=["farol", "ilha", "história", "detalhe"],
                                path=f"uploads/prj/pool/{i}.jpg") for i, tier in enumerate(tiers, 1)])


def test_mock_project_comes_out_with_shots_and_fx_within_quotas(isolated_settings, monkeypatch):
    pool = _fixture_pool()
    monkeypatch.setattr(media_pool, "gather", lambda doc, style, **_: pool)
    doc = _mock_project(LONG)
    style = get_style(doc.style_id, doc.style_overrides)
    grammar = grammar_for(style)
    summary = edit_plan.summary(doc)
    assert summary["scenes_with_shots"] >= 5 and summary["fx_total"] > 20
    pooled = [sh for s in doc.scenes for sh in s.shots if sh.source == "pool"]
    assert pooled and len(pooled) <= grammar.switch_per_min * _duration(doc, style) / 60
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
    n_calm, n_normal = len(_fx(calm)), len(_fx(normal))
    assert n_normal > 10 and n_calm < n_normal / 2, (n_calm, n_normal)
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


def test_serious_beat_never_gets_a_meme_and_gags_use_the_library(isolated_settings, monkeypatch):
    calls: list[tuple] = []

    def pick(tag, seed, exclude):
        calls.append((tag, seed, frozenset(exclude)))
        return SimpleNamespace(id="meme-susto", sfx_id="sfx-susto", max_ms=1200)

    monkeypatch.setattr(edit_plan, "_pick_meme", pick)
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
    assert all(400 <= (e.dur_ms or 0) <= 1500 for _s, e in memes)
    assert any(e.params.get("lib_id") == "sfx-susto" for _s, e in _fx(doc, "sfx"))
    assert calls and all("meme-velho" in exclude for _t, _seed, exclude in calls)  # memória dos últimos vídeos
    # meme a 20 s de distância no longo
    times = sorted(t for _sid, kind, t in _event_times(doc, style) if kind == "meme")
    assert len(times) == len(memes)
    assert all(b - a >= 20 for a, b in zip(times, times[1:], strict=False))


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
    drop = [e for e in scene.fx if e.type == "music_drop"]
    assert drop and drop[0].at == "um" and 300 <= (drop[0].dur_ms or 0) <= 600


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
            assert t <= beat.end - edit_plan.TAIL_S + 1e-6 or e.type == "music_drop"
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
    assert kinds["highlight"].at == kinds["zoom_rect"].at


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
