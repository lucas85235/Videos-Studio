from __future__ import annotations

from studio.core.project.document import ProjectDoc, Scene, Segment, Visual
from studio.core.styles.library import get_style
from studio.core.timeline import NarrationClip, build_timeline, chunk_timeline, normalize_words
from studio.providers.base import Word


def _doc(n_segments: int = 5, scenes_per_segment: int = 4) -> ProjectDoc:
    segments, scenes = [], []
    for i in range(n_segments):
        seg = Segment(id=f"seg_{i}", text={"pt-BR": " ".join(f"palavra{j}" for j in range(18)) + "."})
        segments.append(seg)
        for k in range(scenes_per_segment):
            scenes.append(Scene(id=f"scn_{i}_{k}", segment_id=seg.id, part=k, parts=scenes_per_segment, visual=Visual(prompt="x")))
    return ProjectDoc(id="prj_t", title="t", style_id="misterios-short", segments=segments, scenes=scenes, seed=1)


def _clips(doc: ProjectDoc, overshoot: float) -> dict[str, NarrationClip]:
    clips = {}
    for seg in doc.segments:
        words = seg.text["pt-BR"].split()
        duration = 6.0
        # posições relatadas além do fim do áudio (como a voz do Windows às vezes faz)
        step = duration * overshoot / len(words)
        clips[seg.id] = NarrationClip(
            segment_id=seg.id,
            path="x.wav",
            duration=duration,
            words=[Word(w, i * step, (i + 0.9) * step) for i, w in enumerate(words)],
        )
    return clips


def test_scenes_are_contiguous_even_when_word_times_overshoot():
    doc = _doc()
    style = get_style("misterios-short")
    for overshoot in (1.0, 1.08, 1.4):
        timeline = build_timeline(doc, style, "pt-BR", _clips(doc, overshoot), {})
        scenes = timeline["scenes"]
        for a, b in zip(scenes, scenes[1:], strict=False):
            assert a["durationInFrames"] >= 1
            assert a["from"] + a["durationInFrames"] == b["from"], (overshoot, a["id"], b["id"])
        assert sum(s["durationInFrames"] for s in scenes) == timeline["durationInFrames"]
        chunks = chunk_timeline(timeline, "v")
        assert sum(c["durationInFrames"] for c in chunks) == timeline["durationInFrames"]


def test_caption_lines_are_balanced_inside_scenes():
    doc = _doc(n_segments=1, scenes_per_segment=2)
    style = get_style("misterios-short")
    timeline = build_timeline(doc, style, "pt-BR", _clips(doc, 1.0), {})
    sizes = [len(line["words"]) for scene in timeline["scenes"] for line in scene["captions"]]
    assert max(sizes) <= style.edit.captions.max_words_per_line
    assert sizes.count(1) <= 1


def test_normalize_words_clamps_and_scales():
    words = [Word("a", 0.0, 1.0), Word("b", 1.0, 2.5), Word("c", 2.5, 4.0)]
    out = normalize_words(words, 3.0)
    assert out[-1].end <= 3.0
    assert all(w.start <= w.end for w in out)
    assert [w.start for w in out] == sorted(w.start for w in out)
