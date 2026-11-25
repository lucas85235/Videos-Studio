"""Avatar do apresentador na timeline: gatilho em quadros, duração, lado e o hash dos blocos."""

from __future__ import annotations

from studio.core.project.document import (
    Presenter,
    PresenterPose,
    ProjectDoc,
    Scene,
    ScenePresenter,
    Segment,
    Visual,
)
from studio.core.styles.library import get_style
from studio.core.timeline import NarrationClip, build_timeline, chunk_timeline
from studio.providers.base import Word

TEXT = "isso aqui é chocante pessoal e de novo chocante no fim da frase toda mesmo"


def _presenter() -> Presenter:
    return Presenter(enabled=True, poses=[
        PresenterPose(id="base", file="library/presenter/c/base.png", render_file="library/presenter/c/base.r900.png"),
        PresenterPose(id="surpreso", file="library/presenter/c/surpreso.png", emotions=["choque"]),
        PresenterPose(id="apontando", file="library/presenter/c/apontando.png", facing="right"),
    ])


def _doc(presenters: dict[int, ScenePresenter | None]) -> ProjectDoc:
    segments, scenes = [], []
    for i in range(3):
        seg = Segment(id=f"seg_{i}", text={"pt-BR": TEXT})
        segments.append(seg)
        scenes.append(Scene(id=f"scn_{i}", segment_id=seg.id, visual=Visual(prompt="x"), presenter=presenters.get(i)))
    return ProjectDoc(id="prj_tp", title="t", style_id="curiosidades-longo", segments=segments, scenes=scenes, seed=1)


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    toks = TEXT.split()
    return {seg.id: NarrationClip(seg.id, "x.wav", len(toks) * 0.5 + 0.2,
                                  [Word(t, k * 0.5, k * 0.5 + 0.4) for k, t in enumerate(toks)]) for seg in doc.segments}


def _timeline(doc: ProjectDoc, presenter: Presenter | None = None, style_id: str = "curiosidades-longo"):
    return build_timeline(doc, get_style(style_id), "pt-BR", _clips(doc), {}, presenter=presenter)


def test_no_presenter_keeps_timeline_identical():
    doc = _doc({1: ScenePresenter(pose="surpreso")})
    plain = _timeline(doc)
    assert all("presenter" not in s for s in plain["scenes"])
    other = _doc({})
    assert _timeline(other, _presenter())["scenes"] == _timeline(other)["scenes"]


def test_trigger_word_ms_and_occurrence():
    doc = _doc({0: ScenePresenter(pose="surpreso", at="chocante"), 1: ScenePresenter(pose="base", at="chocante#2"),
                2: ScenePresenter(pose="base", at="@1200")})
    tl = _timeline(doc, _presenter())
    fps = tl["fps"]
    s0, s1, s2 = tl["scenes"]
    first = s0["words"][[w["text"] for w in s0["words"]].index("chocante")]["from"]
    assert s0["presenter"]["from"] == first - 2
    assert s0["presenter"]["punch"] is True
    words1 = [w["text"] for w in s1["words"]]
    second = [i for i, t in enumerate(words1) if t == "chocante"][1]
    assert s1["presenter"]["from"] == s1["words"][second]["from"] - 2
    assert s2["presenter"]["from"] == round(1.2 * fps)
    assert s0["presenter"]["src"] == "library/presenter/c/surpreso.png"
    assert s1["presenter"]["src"] == "library/presenter/c/base.r900.png"  # versão de 900 px quando há
    assert "punch" not in s1["presenter"]


def test_duration_limits_and_default_start():
    doc = _doc({0: ScenePresenter(pose="base"), 1: ScenePresenter(pose="base", mode="explica"),
                2: ScenePresenter(pose="base", at="mesmo")})
    tl = _timeline(doc, _presenter())
    fps = tl["fps"]
    a, b, c = tl["scenes"]
    assert a["presenter"]["from"] == 6 and a["presenter"]["dur"] == round(2.2 * fps)
    assert b["presenter"]["dur"] <= 8 * fps and b["presenter"]["dur"] <= b["durationInFrames"] - b["presenter"]["from"]
    assert b["presenter"]["height"] < a["presenter"]["height"]
    # gatilho na última palavra: recua para caber 1,5 s
    assert c["presenter"]["from"] + c["presenter"]["dur"] <= c["durationInFrames"]
    assert c["presenter"]["dur"] >= round(1.5 * fps)


def test_side_auto_off_and_vertical():
    doc = _doc({0: ScenePresenter(pose="apontando"), 1: ScenePresenter(pose="base", side="left"),
                2: ScenePresenter(pose="base", off=True)})
    doc.scenes[0].focus = "left"
    tl = _timeline(doc, _presenter())
    assert tl["scenes"][0]["presenter"]["side"] == "left"  # aponta para a direita: fica à esquerda
    assert tl["scenes"][1]["presenter"]["side"] == "left"
    assert "presenter" not in tl["scenes"][2]
    doc.scenes[0].presenter = ScenePresenter(pose="base")
    doc.scenes[0].focus = "top_right"
    assert _timeline(doc, _presenter())["scenes"][0]["presenter"]["side"] == "left"
    doc.scenes[0].focus = "left"
    assert _timeline(doc, _presenter())["scenes"][0]["presenter"]["side"] == "right"
    short = _doc({0: ScenePresenter(pose="base")})
    assert all("presenter" not in s for s in _timeline(short, _presenter(), "misterios-short")["scenes"])


def test_changing_one_scene_changes_only_its_chunk():
    doc = _doc({0: ScenePresenter(pose="base")})
    before = chunk_timeline(_timeline(doc, _presenter()), "v1")
    doc.scenes[2].presenter = ScenePresenter(pose="surpreso")
    after = chunk_timeline(_timeline(doc, _presenter()), "v1")
    changed = [i for i, (x, y) in enumerate(zip(before, after, strict=True)) if x != y]
    assert len(before) == len(after) and changed
    assert all(any(s["id"] == "scn_2" for s in after[i].get("scenes", [])) for i in changed) or len(changed) == 1


def test_presenter_stage_shows_the_avatar_big_for_the_whole_scene():
    """O palco do avatar (cena de imagem sem imagem nenhuma, o cartão tipográfico que virou o apresentador
    explicando): ele entra no corte, fica a cena inteira e grande."""
    doc = _doc({0: ScenePresenter(pose="base", mode="explica"),
                1: ScenePresenter(pose="base", mode="explica", at="chocante")})
    doc.scenes[1].visual = Visual(type="none")
    tl = _timeline(doc, _presenter())
    plain, stage = tl["scenes"][0], tl["scenes"][1]
    assert stage["presenter"]["from"] == 0
    assert stage["presenter"]["dur"] == stage["durationInFrames"]
    assert stage["presenter"]["height"] >= 0.8 > plain["presenter"]["height"]
