from __future__ import annotations

from studio.core.project.document import ArtDirection, ProjectDoc, Scene, SceneCard, Segment, Visual
from studio.core.styles.library import get_style
from studio.core.text import split_word_indices, words
from studio.core.timeline import NarrationClip, build_timeline, chunk_timeline, normalize_words, render_style
from studio.providers.base import Word

LEGACY_SCENE_KEYS = {"id", "kind", "motion", "transition", "seed", "look", "text", "number", "from", "durationInFrames",
                     "src", "captions", "words"}


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


# --- cortes por span, cartões e foco ------------------------------------------------------------

TWO_SENTENCES = "O jogo saiu naquele verão e virou febre. Ninguém esperava aquilo tudo agora."


def _span_doc(spans: list[int | None], text: str = TWO_SENTENCES, **extra) -> ProjectDoc:
    seg = Segment(id="seg_s", text={"pt-BR": text})
    scenes = [
        Scene(id=f"scn_s{k}", segment_id=seg.id, part=k, parts=len(spans), visual=Visual(prompt="x"), span=span)
        for k, span in enumerate(spans)
    ]
    return ProjectDoc(id="prj_s", title="s", style_id="misterios-short", segments=[seg], scenes=scenes, seed=3, **extra)


def _uniform_clip(doc: ProjectDoc, text: str | None = None) -> dict[str, NarrationClip]:
    seg = doc.segments[0]
    tokens = words(text if text is not None else seg.text["pt-BR"])
    return {seg.id: NarrationClip(seg.id, "x.wav", len(tokens) * 0.5 + 0.2,
                                  [Word(t, i * 0.5, i * 0.5 + 0.4) for i, t in enumerate(tokens)])}


def _scene_words(timeline) -> list[list[str]]:
    return [[w["text"] for w in s["words"]] for s in timeline["scenes"]]


def test_spans_map_scenes_exactly_to_words():
    tokens = words(TWO_SENTENCES)
    doc = _span_doc([3, 3, 7])
    assert sum(s.span for s in doc.scenes) == len(tokens)
    timeline = build_timeline(doc, get_style("misterios-short"), "pt-BR", _uniform_clip(doc), {})
    assert _scene_words(timeline) == [tokens[:3], tokens[3:6], tokens[6:]]
    # a divisão antiga (pelo tamanho) cortaria em outros pontos
    assert split_word_indices(len(tokens), 3, tokens) != [(0, 3), (3, 6), (6, 13)]


def test_spans_fallback_proportional_when_word_count_differs():
    doc = _span_doc([4, 8])  # spans contados em 12 palavras; a narração (tradução) tem 24
    narration = " ".join(f"word{i}" for i in range(24))
    timeline = build_timeline(doc, get_style("misterios-short"), "pt-BR", _uniform_clip(doc, narration), {})
    assert [len(w) for w in _scene_words(timeline)] == [8, 16]


def test_scenes_without_span_keep_old_ranges():
    for spans in ([None, None, None], [5, None, 4]):  # basta uma cena sem span para valer a divisão antiga
        doc = _span_doc(spans)
        timeline = build_timeline(doc, get_style("misterios-short"), "pt-BR", _uniform_clip(doc), {})
        tokens = words(TWO_SENTENCES)
        expected = [b - a for a, b in split_word_indices(len(tokens), 3, tokens)]
        assert [len(w) for w in _scene_words(timeline)] == expected


def test_card_and_focus_only_emitted_when_set():
    style = get_style("misterios-short")
    old = _doc(n_segments=2, scenes_per_segment=2)
    base = build_timeline(old, style, "pt-BR", _clips(old, 1.0), {})
    assert all(set(s) == LEGACY_SCENE_KEYS for s in base["scenes"])

    # cartão vazio e foco desconhecido não mudam nada (nem a chave dos blocos)
    blank = old.model_copy(deep=True)
    blank.scenes[0].card = SceneCard()
    blank.scenes[1].focus = "lugar_nenhum"
    same = build_timeline(blank, style, "pt-BR", _clips(blank, 1.0), {})
    assert same["scenes"] == base["scenes"]
    assert [c["key"] for c in chunk_timeline(same, "v")] == [c["key"] for c in chunk_timeline(base, "v")]

    filled = old.model_copy(deep=True)
    filled.scenes[0].card = SceneCard(value={"pt-BR": "475 MI"}, label={"pt-BR": ""}, headline={"en": "RECORD"},
                                      source="Take-Two")
    filled.scenes[1].focus = "Top-Left"
    out = build_timeline(filled, style, "en", _clips(filled, 1.0), {})["scenes"]
    assert out[0]["card"] == {"value": "475 MI", "headline": "RECORD", "source": "Take-Two"}  # pt-BR é a reserva
    assert out[1]["focus"] == "top_left"
    assert "card" not in out[1] and "focus" not in out[0]


def test_stat_scene_gets_impact_sfx():
    doc = _doc(n_segments=1, scenes_per_segment=4)
    doc.scenes[1].kind = "stat"
    doc.scenes[2].kind = "compare"
    doc.scenes[3].kind = "stat"
    doc.scenes[3].sfx = "whoosh"  # a cena que escolheu outro som fica com ele
    timeline = build_timeline(doc, get_style("misterios-short"), "pt-BR", _clips(doc, 1.0), {})
    by_scene = {e["scene_id"]: e["type"] for e in timeline["sfx"]}
    assert by_scene["scn_0_1"] == "impact" and by_scene["scn_0_2"] == "impact"
    assert by_scene["scn_0_3"] == "whoosh"


def test_art_grade_overrides_style_grade():
    style = get_style("misterios-short")
    original = next(e for e in style.edit.effects if e.type == "grade")
    doc = _doc(n_segments=1, scenes_per_segment=1)
    assert render_style(style, doc) == render_style(style)  # projeto sem direção: igual ao de antes

    doc.art = ArtDirection(grade="vivid")
    grade = next(e for e in render_style(style, doc)["effects"] if e["type"] == "grade")
    assert grade["preset"] == "vivid" and grade["amount"] == min(original.amount, 0.45)

    doc.art = ArtDirection(grade="inexistente")
    assert render_style(style, doc) == render_style(style)
    doc.art = ArtDirection(grade=None)
    assert render_style(style, doc) == render_style(style)

    # estilo sem efeito de cor continua sem
    plain = get_style("misterios-short", {"edit": {"effects": [{"type": "grain", "amount": 0.2}]}})
    doc.art = ArtDirection(grade="vivid")
    assert render_style(plain, doc)["effects"] == [e.model_dump() for e in plain.edit.effects]


def test_captions_use_digits_only_in_new_documents():
    text = "Em dois mil e treze, o jogo vendeu duzentos milhões de cópias."
    style = get_style("misterios-short")

    def caption_texts(doc: ProjectDoc) -> list[str]:
        timeline = build_timeline(doc, style, "pt-BR", _uniform_clip(doc), {})
        return [w["text"] for s in timeline["scenes"] for line in s["captions"] for w in line["words"]]

    old = _span_doc([None], text)
    assert caption_texts(old) == words(text)  # documento antigo: legenda como sempre foi

    new = _span_doc([len(words(text))], text)
    texts = caption_texts(new)
    assert "2013," in texts and "200 milhões" in texts and "dois" not in texts
    timeline = build_timeline(new, style, "pt-BR", _uniform_clip(new), {})
    assert [w["text"] for w in timeline["scenes"][0]["words"]][:2] == ["Em", "2013,"]
    assert _span_doc([None], text, art=ArtDirection()).scenes[0].span is None
    assert "2013," in caption_texts(_span_doc([None], text, art=ArtDirection()))
