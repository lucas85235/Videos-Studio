from __future__ import annotations

from studio.core.project.document import ArtDirection, CardItem, ProjectDoc, Scene, SceneCard, Segment, Visual
from studio.core.styles.library import get_style
from studio.core.text import normalize_token, split_word_indices, words
from studio.core.timeline import (
    CAPTION_MERGE_S,
    CAPTION_MIN_S,
    FUNCTION_WORDS,
    SFX_LEAD,
    NarrationClip,
    _caption_lines,
    build_timeline,
    chunk_timeline,
    normalize_words,
    render_style,
)
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


def test_caption_line_cut_by_scene_does_not_flash():
    # O corte da cena cai logo depois de "bateu": a última linha só fica até o corte, não até o fim da palavra + 0,25 s.
    # A divisão antiga deixava "bateu" sozinho por 0,37 s (piscava).
    text = "Sobre o novo trailer de GTA 6 bateu recorde de views em um só dia."
    tokens = words(text)
    doc = _span_doc([8, len(tokens) - 8], text)
    t, timed = 0.12, []
    for tok in tokens:  # tempos da voz de teste: palavras quase coladas
        d = max(0.12, len(tok) / 14.5 + 0.05)
        timed.append(Word(tok, t, t + d))
        t += d + 0.04
    style = get_style("games-cultura-pop-short")
    seg_id = doc.segments[0].id
    timeline = build_timeline(doc, style, "pt-BR", {seg_id: NarrationClip(seg_id, "x.wav", t + 0.12, timed)}, {})
    lines = timeline["scenes"][0]["captions"]
    assert all(line["to"] - line["from"] >= round(CAPTION_MIN_S * style.format.fps) for line in lines), lines


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


# --- legendas: divisão por custo ------------------------------------------------------------------


def _spoken(text: str, per: float = 0.36) -> list[Word]:
    """Palavras com tempos de fala (palavra curta é rápida; vírgula e ponto dão pausa)."""
    out, t = [], 0.0
    for tok in words(text):
        d = per * max(0.6, min(1.6, len(tok) / 6))
        out.append(Word(tok, t, t + d * 0.9))
        t += d + (0.15 if tok.endswith(",") else 0.3 if tok.endswith((".", ":", "?", "!")) else 0.0)
    return out


def _texts(lines: list[list[Word]]) -> list[str]:
    return [" ".join(w.text for w in line) for line in lines]


def _fits(line: list[Word], max_words: int) -> bool:
    return len(line) <= max_words or (len(line) == max_words + 1 and len(" ".join(w.text for w in line)) <= 18)


def test_caption_lines_do_not_end_on_function_words():
    fn = FUNCTION_WORDS["pt"]
    for text in (
        "Com Lucia, a primeira protagonista mulher da era moderna da série.",
        "O recorde que ele já quebrou é outro:",
        "Vendeu mais de 200 milhões de cópias.",
        "O primeiro trailer passou de 90 milhões de views em 24 horas.",
    ):
        lines = _caption_lines(_spoken(text), 3, "karaoke", "pt-BR")
        assert " ".join(_texts(lines)) == " ".join(words(text))
        assert all(normalize_token(line[-1].text) not in fn for line in lines[:-1]), _texts(lines)
        assert all(_fits(line, 3) for line in lines), _texts(lines)
    lucia = _texts(_caption_lines(_spoken("Com Lucia, a primeira protagonista mulher da era moderna da série."), 3, "karaoke", "pt-BR"))
    assert lucia[0] == "Com Lucia,"  # a vírgula fecha a linha
    assert "da era moderna" in lucia  # o sintagma não se parte
    # linha curta pode ter uma palavra a mais
    may = _texts(_caption_lines(_spoken("Em maio de 2025, o segundo trailer chegou."), 3, "karaoke", "pt-BR"))
    assert may[0] == "Em maio de 2025,"


def test_caption_lines_do_not_flash():
    # Fala rápida: na divisão por contagem, "que não" e "ia dar" ficariam ~0,3 s na tela.
    for text in ("Ele já sabia que não ia dar certo naquela noite fria.", "Tem um detalhe que quase ninguém comenta."):
        lines = _caption_lines(_spoken(text, per=0.26), 3, "karaoke", "pt-BR")
        for line, nxt in zip(lines, lines[1:], strict=False):
            assert nxt[0].start - line[0].start >= CAPTION_MERGE_S, _texts(lines)
        assert all(_fits(line, 3) for line in lines)


def test_caption_lines_keep_numbers_and_follow_the_language():
    en = _caption_lines(_spoken("The first trailer of the new game reached 90 million views in a single day."), 3, "karaoke", "en-US")
    assert all(normalize_token(line[-1].text) not in FUNCTION_WORDS["en"] for line in en[:-1]), _texts(en)
    assert not any(line[0].text == "million" for line in en)  # "90 | million" não se separa
    es = _caption_lines(_spoken("El primer tráiler del juego rompió el récord en un solo día."), 3, "karaoke", "es-ES")
    assert all(normalize_token(line[-1].text) not in FUNCTION_WORDS["es"] for line in es[:-1]), _texts(es)
    # documento antigo (número por extenso, sem algarismos): o número não se parte entre linhas
    old = _caption_lines(_spoken("Tudo mudou em dois mil e treze para sempre."), 3, "karaoke", "pt-BR")
    assert "dois mil e treze" in " | ".join(_texts(old)), _texts(old)
    single = _spoken("Uma frase qualquer aqui.")
    assert _caption_lines(single, 3, "word", "pt-BR") == [[w] for w in single]


# --- carimbo, reveal e efeitos sonoros ---------------------------------------------------------


def test_stamp_side_follows_the_image_composition():
    style = get_style("misterios-short")
    doc = _doc(n_segments=1, scenes_per_segment=5)
    doc.art = ArtDirection(languages=["key_art", "editorial"])
    framing = [("medium", "right_third", 0), ("medium", "left_third", 0), ("close", "center", 0), ("wide", "center", 0),
               ("medium", "left_third", 1)]  # fmt: skip
    for scene, (shot, comp, variation) in zip(doc.scenes, framing, strict=True):
        scene.visual = Visual(prompt="x", language="key_art", shot=shot, composition=comp, variation=variation)
        scene.card = SceneCard(stamp={"pt-BR": "RECORDE"})
    out = build_timeline(doc, style, "pt-BR", _clips(doc, 1.0), {})["scenes"]
    # sujeito à direita → carimbo à esquerda; close no centro → embaixo; "Outra imagem" (variação 1) gira
    # left_third para right_third
    assert [s["card"]["stamp_at"] for s in out] == ["left", "right", "low", "right", "left"]

    # imagem reaproveitada: vale a composição da cena dona da imagem
    doc.scenes[3].visual = Visual(prompt="", reuse_of=doc.scenes[0].id)
    assert build_timeline(doc, style, "pt-BR", _clips(doc, 1.0), {})["scenes"][3]["card"]["stamp_at"] == "left"

    # sem carimbo, no painel do cartão de dado ou sem composição conhecida: nada novo no dict
    doc.scenes[1].card = SceneCard(headline={"pt-BR": "MAIO"})
    doc.scenes[2].kind = "stat"
    doc.scenes[2].card = SceneCard(value={"pt-BR": "90 MI"}, stamp={"pt-BR": "RECORDE"})
    out = build_timeline(doc, style, "pt-BR", _clips(doc, 1.0), {})["scenes"]
    assert out[1]["card"] == {"headline": "MAIO"}
    assert "stamp_at" not in out[2]["card"]
    old = _doc(n_segments=1, scenes_per_segment=2)
    old.scenes[0].card = SceneCard(stamp={"pt-BR": "RECORDE"})
    assert build_timeline(old, style, "pt-BR", _clips(old, 1.0), {})["scenes"][0]["card"] == {"stamp": "RECORDE"}


DATA_TEXT = (
    "O trailer saiu ontem. Ele passou de noventa milhões de views. "
    "Depois o segundo chegou a quatrocentos e setenta e cinco milhões."
)


def _data_doc() -> ProjectDoc:
    doc = _span_doc([4, 7, 11], DATA_TEXT, art=ArtDirection())
    doc.scenes[1].kind = "stat"
    doc.scenes[1].card = SceneCard(value={"pt-BR": "90 MI"}, label={"pt-BR": "views"})
    doc.scenes[2].kind = "compare"
    doc.scenes[2].card = SceneCard(items=[CardItem(label={"pt-BR": "Trailer 1"}, value={"pt-BR": "90 MI"}),
                                          CardItem(label={"pt-BR": "Trailer 2"}, value={"pt-BR": "475 MI"})])  # fmt: skip
    for scene in doc.scenes:
        scene.transition_in = "cut"
    return doc


def _word_frame(timeline: dict, scene_index: int, text: str) -> int:
    scene = timeline["scenes"][scene_index]
    return next(w["from"] for w in scene["words"] if w["text"].startswith(text))


def test_data_cards_reveal_when_the_number_is_spoken():
    style = get_style("misterios-short")
    doc = _data_doc()
    timeline = build_timeline(doc, style, "pt-BR", _uniform_clip(doc), {})
    stat, compare = timeline["scenes"][1], timeline["scenes"][2]
    assert stat["reveal"] == _word_frame(timeline, 1, "90") > 0  # "noventa milhões" (algarismos na legenda)
    assert compare["reveal"] == _word_frame(timeline, 2, "475") > 0  # o número que bate com um item
    assert "reveal" not in timeline["scenes"][0]

    # o impacto do cartão bate quando o número é dito, não no corte
    impacts = {e["scene_id"]: e["start"] for e in timeline["sfx"] if e["type"] == "impact"}
    for scene in (stat, compare):
        assert abs(impacts[scene["id"]] - (scene["from"] + scene["reveal"]) / timeline["fps"]) <= 1.5 / timeline["fps"]

    # com transição de som, o corte fica com ela
    doc.scenes[1].transition_in = "flash"
    timeline = build_timeline(doc, style, "pt-BR", _uniform_clip(doc), {})
    kinds = sorted(e["type"] for e in timeline["sfx"] if e["scene_id"] == doc.scenes[1].id)
    assert kinds == ["impact", "whoosh"]

    # sem o número do cartão na fala, vale o primeiro número que dá cartão; e o cartão fica 1,2 s na tela
    late = _span_doc([4, 7, 11], DATA_TEXT, art=ArtDirection())
    late.scenes[2].kind = "stat"
    late.scenes[2].card = SceneCard(value={"pt-BR": "12"}, label={"pt-BR": "x"})
    scene = build_timeline(late, style, "pt-BR", _uniform_clip(late), {})["scenes"][2]
    assert 0 < scene["reveal"] <= scene["durationInFrames"] - 36
    # sem número dito, sem reveal (o renderer faz como antes)
    quiet_text = DATA_TEXT.replace("noventa milhões", "muitos").replace("quatrocentos e setenta e cinco milhões", "vários")
    quiet = _span_doc([None, None], quiet_text, art=ArtDirection())
    quiet.scenes[1].kind = "stat"
    quiet.scenes[1].card = SceneCard(value={"pt-BR": "90 MI"})
    assert "reveal" not in build_timeline(quiet, style, "pt-BR", _uniform_clip(quiet), {})["scenes"][1]


def test_sfx_land_on_the_cut_and_on_overlays():
    style = get_style("misterios-short")
    fps = style.format.fps
    doc = _doc(n_segments=1, scenes_per_segment=5)
    doc.scenes[1].transition_in = "flash"  # whoosh: chega ao pico no corte
    doc.scenes[2].transition_in = "cut"
    doc.scenes[2].kind = "title"  # impacto no corte
    doc.scenes[3].transition_in = "cut"
    doc.scenes[3].card = SceneCard(headline={"pt-BR": "DEZEMBRO"})  # faixa sem outro som: impacto
    doc.scenes[4].transition_in = "cut"
    doc.scenes[4].card = SceneCard(stamp={"pt-BR": "RECORDE"})  # carimbo: impacto quando pousa
    timeline = build_timeline(doc, style, "pt-BR", _clips(doc, 1.0), {})
    scenes = timeline["scenes"]
    by_scene: dict[str, list[dict]] = {}
    for e in timeline["sfx"]:
        by_scene.setdefault(e["scene_id"], []).append(e)

    def cut(i: int) -> float:
        return scenes[i]["from"] / fps

    tol = 1.0 / fps
    [whoosh] = by_scene[scenes[1]["id"]]
    assert whoosh["type"] == "whoosh" and abs(whoosh["start"] - (cut(1) - 0.35)) <= tol
    [title] = by_scene[scenes[2]["id"]]
    assert title["type"] == "impact" and abs(title["start"] - cut(2)) <= tol
    [headline] = by_scene[scenes[3]["id"]]
    assert headline["type"] == "impact" and abs(headline["start"] - cut(3)) <= tol
    [stamp] = by_scene[scenes[4]["id"]]
    assert stamp["type"] == "impact" and abs(stamp["start"] - (cut(4) + 6 / fps + 0.1)) <= tol

    # a antecedência de cada efeito é o ponto forte do arquivo gerado (o mesmo número dos dois lados)
    from studio.media import audio

    for kind, hit in getattr(audio, "SFX_HIT_S", {}).items():
        assert SFX_LEAD[kind] == hit, kind

    # som automático desligado no estilo: só o escolhido na cena, com a antecedência do tipo
    manual = get_style("misterios-short", {"audio": {"sfx": {"auto": False}}})
    doc.scenes[2].sfx = "riser"
    events = build_timeline(doc, manual, "pt-BR", _clips(doc, 1.0), {})["sfx"]
    assert [e["type"] for e in events] == ["riser"] and abs(events[0]["start"] - max(0.0, cut(2) - 1.5)) <= tol


def test_number_stamp_lands_when_the_number_is_spoken():
    style = get_style("misterios-short")
    text = "Tudo começou numa sala pequena em dois mil e treze, longe de tudo."
    doc = _span_doc([None], text, art=ArtDirection())
    doc.scenes[0].card = SceneCard(stamp={"pt-BR": "2013"})
    timeline = build_timeline(doc, style, "pt-BR", _uniform_clip(doc), {})
    scene = timeline["scenes"][0]
    assert scene["card"]["stamp_from"] == _word_frame(timeline, 0, "2013")
    [stamp] = timeline["sfx"]
    assert abs(stamp["start"] - (scene["card"]["stamp_from"] / style.format.fps + 0.1)) <= 1 / style.format.fps
    # carimbo sem número fica no tempo de sempre
    doc.scenes[0].card = SceneCard(stamp={"pt-BR": "RECORDE"})
    assert build_timeline(doc, style, "pt-BR", _uniform_clip(doc), {})["scenes"][0]["card"] == {"stamp": "RECORDE"}
