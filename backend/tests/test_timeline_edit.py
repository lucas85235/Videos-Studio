"""Camada de edição na timeline (ADR 0004 §3.7): subplanos e eventos resolvidos em quadros, SFX de evento, quedas
da trilha, efeitos por nível (`clean`), ênfase na legenda e câmera pela gramática. Sem a camada, nada muda."""

from __future__ import annotations

import json

import pytest

from studio.core import edit_resolve
from studio.core.hashing import hash_obj
from studio.core.project.document import (
    PRESENTER_AT_RE,
    BeatMark,
    FxEvent,
    MediaRef,
    ProjectDoc,
    Scene,
    SceneCard,
    Segment,
    ShotFraming,
    SubShot,
    Visual,
)
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.core.styles.library import get_style
from studio.core.styles.schema import EditGrammar
from studio.core.timeline import (
    SFX_LEAD,
    NarrationClip,
    build_timeline,
    chunk_timeline,
    lead_in,
    scene_motion,
)
from studio.providers.base import Word

TEXT = "isso aqui é chocante pessoal e de novo chocante no fim da frase toda mesmo"
TEXT_EN = "this here is shocking folks and once again shocking at the end of the whole sentence"
STEP = 0.5
STYLE = get_style("curiosidades-longo")
FPS = STYLE.format.fps


def _doc(*scenes: Scene, extra: dict | None = None) -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", text={"pt-BR": TEXT, "en": TEXT_EN}) for i in range(len(scenes))]
    return ProjectDoc(id="prj_tl", title="t", style_id="curiosidades-longo", segments=segments,
                      scenes=list(scenes), seed=3, languages=["pt-BR", "en"], **(extra or {}))


def _scene(i: int, **kwargs) -> Scene:
    kwargs.setdefault("visual", Visual(prompt="x"))
    return Scene(id=f"scn_{i}", segment_id=f"seg_{i}", **kwargs)


def _clips(doc: ProjectDoc, lang: str = "pt-BR") -> dict[str, NarrationClip]:
    toks = (TEXT if lang == "pt-BR" else TEXT_EN).split()
    return {seg.id: NarrationClip(seg.id, "x.wav", len(toks) * STEP + 0.2,
                                  [Word(t, k * STEP, k * STEP + 0.4) for k, t in enumerate(toks)])
            for seg in doc.segments}


def _timeline(doc: ProjectDoc, style=STYLE, lang: str = "pt-BR", **kwargs):
    images = {s.id: f"cache/images/{s.id}.jpg" for s in doc.scenes}
    return build_timeline(doc, style, lang, _clips(doc, lang), images, **kwargs)


def _doc_scenes(timeline: dict) -> list[dict]:
    """As cenas do documento (sem a tela final que o vídeo longo ganha no fim)."""
    return [s for s in timeline["scenes"] if s["kind"] != "endscreen"]


def _word_frame(k: int) -> int:
    """Quadro (relativo à 1ª cena, que começa no 0) da k-ésima palavra do 1º segmento."""
    return int((lead_in(STYLE) + k * STEP) * FPS + 0.5)


# --- compatibilidade ----------------------------------------------------------------------------------------------


def test_old_project_keeps_the_same_timeline_and_chunk_hashes(isolated_settings):
    from compat_fixture import FIXTURE, timeline_hashes

    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for style_id, expected in data["expected"].items():
        assert timeline_hashes(data["doc"], style_id) == expected


def test_without_edit_fields_no_new_key_appears():
    doc = _doc(_scene(0), _scene(1, kind="title", visual=Visual(type="none"), overlay_text={"pt-BR": "X"}))
    assert not edit_resolve.edit_active(doc)
    timeline = _timeline(doc)
    assert "music_drops" not in timeline
    for scene in timeline["scenes"]:
        assert not {"prov", "clean", "shots", "fx"} & set(scene)
        assert all("emph" not in w for line in scene["captions"] for w in line["words"])
    assert all(set(e) == {"type", "start", "scene_id"} for e in timeline["sfx"])


def test_scene_without_edit_fields_in_an_old_doc_keeps_the_chunk_hash():
    """Só o projeto com a camada muda: o mesmo documento sem ela gera os mesmos blocos de antes."""
    plain = _doc(_scene(0), _scene(1))
    again = _doc(_scene(0, shots=[], fx=[], mark=None), _scene(1))
    a = chunk_timeline(_timeline(plain), "v")
    b = chunk_timeline(_timeline(again), "v")
    assert [c["key"] for c in a] == [c["key"] for c in b]


# --- gatilhos -----------------------------------------------------------------------------------------------------


def test_word_with_occurrence_resolves_the_right_frame():
    doc = _doc(_scene(0, fx=[FxEvent(type="punch", at="chocante#2"), FxEvent(type="flash", at="chocante")]), _scene(1))
    fx = _timeline(doc)["scenes"][0]["fx"]
    by_type = {e["type"]: e for e in fx}
    assert by_type["flash"]["from"] == _word_frame(3)
    assert by_type["punch"]["from"] == _word_frame(8)
    assert [e["from"] for e in fx] == sorted(e["from"] for e in fx)


def test_at_milliseconds_resolves_in_ms():
    doc = _doc(_scene(0, fx=[FxEvent(type="shake", at="@1200", dur_ms=400)]), _scene(1))
    event = _timeline(doc)["scenes"][0]["fx"][0]
    assert event["from"] == round(1.2 * FPS)
    assert event["dur"] == round(0.4 * FPS)


def test_trigger_that_does_not_match_falls_to_the_start_with_a_warning():
    doc = _doc(_scene(0, fx=[FxEvent(type="shake", at="inexistente")]), _scene(1))
    timeline = _timeline(doc)
    assert timeline["scenes"][0]["fx"][0]["from"] == 0
    assert any("inexistente" in w and "Cena 1" in w and '"tremida"' in w for w in timeline["warnings"])


def test_translated_video_maps_the_trigger_by_position_without_warning():
    doc = _doc(_scene(0, fx=[FxEvent(type="punch", at="chocante#2")]), _scene(1))
    timeline = _timeline(doc, lang="en")
    assert "warnings" not in timeline
    event = timeline["scenes"][0]["fx"][0]
    words = timeline["scenes"][0]["words"]
    # "chocante#2" é a 9ª de 15 palavras do pt: no inglês (16 palavras), a palavra na mesma posição.
    assert event["from"] == words[int(8 / 15 * len(words))]["from"]


def test_all_events_fit_the_scene_and_keep_the_renderer_shape():
    doc = _doc(_scene(0, fx=[FxEvent(type=t, at="mesmo", dur_ms=8000) for t in ("pip", "counter", "highlight")]),
               _scene(1))
    timeline = _timeline(doc)
    scene = timeline["scenes"][0]
    for event in scene["fx"]:
        assert set(event) == {"type", "from", "dur", "params"}
        assert event["dur"] >= 1
        assert event["from"] + event["dur"] <= scene["durationInFrames"]


# --- SFX e trilha -------------------------------------------------------------------------------------------------


def test_fx_event_generates_sfx():
    doc = _doc(_scene(0, fx=[
        FxEvent(type="sfx", at="chocante", params={"kind": "impact", "lib_id": "mixkit-123", "gain_db": -3.0}),
        FxEvent(type="text_pop", at="pessoal", params={"sfx": "whoosh"}),
        FxEvent(type="punch", at="novo"),
    ]), _scene(1))
    timeline = _timeline(doc)
    events = [e for e in timeline["sfx"] if "at" in e]
    assert len(events) == 2  # o punch sem som não ganha som sozinho
    impact = next(e for e in events if e["type"] == "impact")
    at = _word_frame(3) / FPS
    assert impact == {"type": "impact", "at": round(at, 3), "start": round(at - SFX_LEAD["impact"], 3),
                      "scene": "scn_0", "scene_id": "scn_0", "lib_id": "mixkit-123", "gain_db": -3.0}
    whoosh = next(e for e in events if e["type"] == "whoosh")
    assert whoosh["start"] == round(_word_frame(4) / FPS - SFX_LEAD["whoosh"], 3)
    # O evento só de áudio não vai para o renderer; os outros vão.
    assert [e["type"] for e in timeline["scenes"][0]["fx"]] == ["text_pop", "punch"]
    assert timeline["sfx"] == sorted(timeline["sfx"], key=lambda e: e["start"])


def test_music_drop_goes_to_the_timeline_only_when_present():
    """O gatilho da queda é a batida do reveal: a trilha some `dur` antes da palavra e volta nela."""
    doc = _doc(_scene(0, fx=[FxEvent(type="music_drop", at="chocante", dur_ms=500)]), _scene(1))
    timeline = _timeline(doc)
    word_s = _word_frame(3) / FPS
    assert timeline["music_drops"] == [{"at": round(word_s - 0.5, 3), "dur": 0.5}]
    drop = timeline["music_drops"][0]
    assert drop["at"] + drop["dur"] == pytest.approx(word_s, abs=1e-3)
    assert "fx" not in timeline["scenes"][0]


def test_event_sfx_on_the_cut_replaces_the_automatic_cut_sound():
    plain = _doc(_scene(0), _scene(1, transition_in="flash", mark=BeatMark()))
    cut = [e for e in _timeline(plain)["sfx"] if e["scene_id"] == "scn_1"]
    assert [e["type"] for e in cut] == ["whoosh"]
    stacked = _doc(_scene(0), _scene(1, transition_in="flash", fx=[FxEvent(type="sfx", at="@0")]))
    events = [e for e in _timeline(stacked)["sfx"] if e["scene_id"] == "scn_1"]
    assert [e["type"] for e in events] == ["impact"]


def test_cut_sfx_only_fill_what_the_event_sfx_leave_of_the_budget():
    """Vídeo longo com o planejador: os SFX automáticos do corte não passam do orçamento da gramática."""
    events = [{"type": "impact", "at": float(t), "start": t - 0.05, "scene_id": f"e{t}"} for t in range(0, 60, 15)]
    cut = [{"type": kind, "start": float(t), "scene_id": f"c{t}"}
           for t, kind in [(1, "whoosh"), (2, "impact"), (3, "glitch"), (4, "whoosh"), (5, "impact")]]
    chosen = {"type": "whoosh", "start": 6.0, "scene_id": "mine"}
    out = edit_resolve.cap_cut_sfx(events + cut + [chosen], per_min=7.0, duration_s=60.0, keep_scene_ids={"mine"})
    # orçamento 7: 4 de evento + o escolhido na cena + 2 do corte (os impactos primeiro)
    assert len(out) == 7 and out == sorted(out, key=lambda e: e["start"])
    assert [e["scene_id"] for e in out if e["scene_id"].startswith("c")] == ["c2", "c5"]
    assert chosen in out and all(e in out for e in events)
    # sobra orçamento: nada muda
    assert edit_resolve.cap_cut_sfx(cut, per_min=10.0, duration_s=60.0) == cut
    # orçamento estourado só com eventos: nenhum do corte entra, os eventos ficam
    assert edit_resolve.cap_cut_sfx(events + cut, per_min=1.0, duration_s=60.0) == events


# --- efeitos por nível --------------------------------------------------------------------------------------------


def _tiers_doc() -> ProjectDoc:
    news = MediaRef(kind="news", source="link", asset="uploads/prj_tl/media/0123456789abcdef.jpg", outlet="X",
                    headline={"pt-BR": "Manchete"})
    official = MediaRef(kind="official", source="link", asset="uploads/prj_tl/media/fedcba9876543210.jpg")
    own = MediaRef(kind="photo", source="upload", asset="uploads/prj_tl/media/00112233445566aa.jpg")
    return _doc(
        _scene(0, mark=BeatMark(role="hook")),
        _scene(1, visual=Visual(prompt="x", media=news)),
        _scene(2, visual=Visual(prompt="x", media=official)),
        _scene(3, visual=Visual(prompt="x", media=own)),
        _scene(4, kind="stat", visual=Visual(type="none"), card=SceneCard(value={"pt-BR": "12"})),
    )


def test_clean_appears_by_tier():
    scenes = _doc_scenes(_timeline(_tiers_doc()))
    assert [s["prov"] for s in scenes] == ["ai", "capture", "official", "own", "graphic"]
    assert [s.get("clean") for s in scenes] == [None, True, None, True, True]


def _source_doc(*, edited: bool = True) -> ProjectDoc:
    """Cartões com "Fonte:" sobre materiais diferentes (o vídeo do GTA 6 no PC: a fonte da matéria saía sobre arte de
    IA, foto de banco e a captura de outro veículo)."""
    capture = MediaRef(kind="capture", source="capture", asset="uploads/prj_tl/media/0123456789abcdef.jpg",
                       page_url="https://gamesbeat.com/gta-6-pc/", credit="gamesbeat.com")
    eset = MediaRef(kind="capture", source="capture", asset="uploads/prj_tl/media/fedcba9876543210.jpg",
                    page_url="https://www.welivesecurity.com/pt/golpes/gta-6/")
    stock = MediaRef(kind="photo", source="pexels", asset="uploads/prj_tl/media/00112233445566aa.jpg",
                     credit="Kindel Media / Pexels", title="hacker stealing card data on a laptop")
    art = MediaRef(kind="official", source="link", asset="uploads/prj_tl/media/aabbccddeeff0011.jpg",
                   credit="Rockstar Games")
    head = {"pt-BR": "GOLPE 3 · PAGAMENTO"}
    return _doc(
        _scene(0, mark=BeatMark(role="hook") if edited else None,
               card=SceneCard(headline=head, source="WeLiveSecurity (ESET)")),  # IA
        _scene(1, visual=Visual(prompt="x", media=stock), card=SceneCard(stamp={"pt-BR": "GOLPE"}, source="Canaltech")),
        _scene(2, visual=Visual(prompt="x", media=capture), card=SceneCard(headline=head, source="GamesBeat")),
        _scene(3, kind="text", visual=Visual(reuse_of="scn_2"), card=SceneCard(source="Rockstar Games")),
        _scene(4, visual=Visual(prompt="x", media=eset), card=SceneCard(headline=head, source="WeLiveSecurity (ESET)")),
        _scene(5, kind="stat", visual=Visual(prompt="x", media=stock),
               card=SceneCard(value={"pt-BR": "US$ 250"}, source="Malwarebytes")),
        _scene(6, visual=Visual(prompt="x", media=art), card=SceneCard(headline=head, source="Rockstar Games")),
        _scene(7, visual=Visual(prompt="x", media=capture), card=SceneCard(headline=head, source="GamesBeat"),
               shots=[SubShot(at="pessoal", source="pool", pool_id="p2")] if edited else []),
    )


def test_card_source_only_over_the_material_of_that_source():
    pool = Pool(items=[PoolItem(id="p2", tier="stock", media="image", path="uploads/prj_tl/pool/p2.jpg",
                                credit="Autor / Pexels")])
    scenes = _doc_scenes(_timeline(_source_doc(), pool=pool))
    sources = [(s.get("card") or {}).get("source") for s in scenes]
    # sobre IA, foto de banco, a captura de outro veículo e o subplano de banco, a fonte sai; fica sobre a captura e a
    # arte daquela fonte e no cartão de dado
    assert sources == [None, None, "GamesBeat", None, "WeLiveSecurity (ESET)", "Malwarebytes", "Rockstar Games",
                       None]
    # o resto do cartão fica; o cartão que só tinha a fonte sai inteiro
    assert scenes[0]["card"] == {"headline": "GOLPE 3 · PAGAMENTO"} and scenes[1]["card"]["stamp"] == "GOLPE"
    assert "card" not in scenes[3]


def _verdict_doc() -> ProjectDoc:
    """Vereditos do vídeo do GTA 6 no PC: "GOLPE" sobre o quadro do trailer, "FALSO" sobre a matéria da Dexerto e a
    arte oficial de Jason e Lucia (QA: o espectador lê que o trailer, a imprensa ou a arte é que são falsos); "GOLPE"
    sobre o print do site falso fica, como sobre ilustração e no cartão de texto com a afirmação sobre fundo liso; o
    cartão de texto sobre o trailer desfocado ("FALSO" com "Fonte: Rockstar Games" sobre o letreiro VICE) perde."""
    trailer = MediaRef(kind="official", source="library", asset="uploads/prj_tl/media/0123456789abcdef.jpg",
                       credit="Rockstar Games (trailer)")
    dexerto = MediaRef(kind="capture", source="capture", asset="uploads/prj_tl/media/fedcba9876543210.jpg",
                       page_url="https://www.dexerto.com/gta/gta-6-cloud/", credit="dexerto.com")
    art = MediaRef(kind="official", source="link", asset="uploads/prj_tl/media/aabbccddeeff0011.jpg",
                   credit="Canaltech", pool_id="p245")
    scam = MediaRef(kind="official", source="link", asset="uploads/prj_tl/media/00112233445566aa.jpg",
                    credit="Malwarebytes", pool_id="p16")
    golpe, falso = {"pt-BR": "GOLPE"}, {"pt-BR": "FALSO"}
    return _doc(
        _scene(0, visual=Visual(prompt="x", media=trailer), card=SceneCard(stamp=golpe)),
        _scene(1, visual=Visual(prompt="x", media=dexerto), card=SceneCard(stamp=falso, headline={"pt-BR": "PC"})),
        _scene(2, visual=Visual(prompt="x", media=art), card=SceneCard(stamp=falso)),
        _scene(3, visual=Visual(prompt="x", media=scam), card=SceneCard(stamp=golpe)),
        _scene(4, card=SceneCard(stamp=golpe)),  # IA
        _scene(5, kind="text", visual=Visual(prompt="x", media=trailer), card=SceneCard(stamp=falso)),
        _scene(6, visual=Visual(prompt="x", media=trailer), card=SceneCard(stamp={"pt-BR": "CONFIRMADO"})),
        _scene(7, visual=Visual(prompt="x", media=scam), card=SceneCard(stamp=golpe),
               shots=[SubShot(at="pessoal", source="pool", pool_id="p30")]),
        _scene(8, kind="text", visual=Visual(type="none"), card=SceneCard(stamp=falso, source="Rockstar Games")),
        _scene(9, visual=Visual(prompt="x", media=trailer), card=SceneCard(stamp={"pt-BR": "NÃO É GOLPE"})),
    )


VERDICT_POOL = Pool(items=[
    PoolItem(id="p245", tier="official", media="image", path="uploads/prj_tl/pool/p245.jpg",
             desc="GTA 6 vira isca para roubar dados e dinheiro de brasileiros"),
    PoolItem(id="p16", tier="official", media="image", path="uploads/prj_tl/pool/p16.jpg",
             desc="Site copia o Extended Look e adiciona botão falso 'Play Now'"),
    PoolItem(id="p30", tier="official", media="image", path="uploads/prj_tl/pool/p30.jpg",
             desc="casal-conversivel · personagem · jason · lucia"),
])


def test_negative_verdict_never_over_official_or_press_material():
    scenes = _doc_scenes(_timeline(_verdict_doc(), pool=VERDICT_POOL))
    stamps = [(s.get("card") or {}).get("stamp") for s in scenes]
    # trailer, Dexerto, arte oficial e o cartão sobre o trailer desfocado perdem o carimbo; o site falso e a IA ficam
    # com ele; veredito positivo ("CONFIRMADO", "NÃO É GOLPE") fica; o subplano do trailer dentro da cena do site falso
    # também tira o "GOLPE". Com a camada de edição, o cartão tipográfico (texto puro) nunca leva carimbo nem fonte,
    # nem sobre fundo liso (QA r4: CONFIRMADO sobre "só cita os consoles Xbox."); sem ela, fica como antes.
    assert stamps == [None, None, None, "GOLPE", "GOLPE", None, "CONFIRMADO", None, None, "NÃO É GOLPE"]
    assert "card" not in scenes[0] and scenes[1]["card"] == {"headline": "PC"}
    assert "stamp_at" not in (scenes[2].get("card") or {})


def test_storyboard_review_drops_negative_verdict_over_true_material():
    from studio.workers.pipeline import storyboard_check

    doc = _verdict_doc()
    beats = [storyboard_check.Beat(key=f"seg_{i}:0", segment_id=f"seg_{i}", part=0, parts=1, index=i, role="body",
                                   words=TEXT.split(), seconds=3.0) for i in range(len(doc.scenes))]
    issues = storyboard_check.fix_verdict_stamps(doc, beats, VERDICT_POOL)
    assert [(i.code, i.severity, i.scene_id) for i in issues] == [
        ("carimbo_sobre_material_real", "corrigido", f"scn_{k}") for k in (0, 1, 2, 5, 7)]
    assert issues[0].message.startswith('O carimbo "GOLPE" saiu: a cena mostra material oficial ou de imprensa')
    assert doc.scenes[0].card is None and doc.scenes[1].card.headline == {"pt-BR": "PC"}
    assert doc.scenes[3].card.stamp == {"pt-BR": "GOLPE"} and doc.scenes[8].card.stamp == {"pt-BR": "FALSO"}
    # Cena travada pelo usuário não muda; a revisão inteira roda a correção.
    doc = _verdict_doc()
    doc.scenes[0].locked = True
    assert all(i.scene_id != "scn_0" for i in storyboard_check.fix_verdict_stamps(doc, beats, VERDICT_POOL))
    import inspect

    assert "fix_verdict_stamps(doc, beats, pool)" in inspect.getsource(storyboard_check.check)


def test_verdict_reads_pool_without_edit_layer(monkeypatch):
    """Sem camada de edição o build não passa o acervo: a timeline lê `pool.json` só quando um veredito negativo cai
    sobre material oficial, e o "GOLPE" sobre o site falso (descrição do acervo) continua na tela."""
    from studio.core import timeline as tl

    calls: list[str] = []

    def fake_pool(doc):
        calls.append(doc.id)
        return VERDICT_POOL

    monkeypatch.setattr(tl, "project_pool", fake_pool)
    doc = _verdict_doc()
    doc.scenes[7].shots = []  # sem subplano: sem camada de edição, o build não passa acervo nenhum
    assert not edit_resolve.edit_active(doc)
    scenes = _doc_scenes(_timeline(doc))
    stamps = [(s.get("card") or {}).get("stamp") for s in scenes]
    assert stamps == [None, None, None, "GOLPE", "GOLPE", None, "CONFIRMADO", "GOLPE", "FALSO", "NÃO É GOLPE"]
    assert calls == ["prj_tl"]  # uma leitura só
    # Sem veredito negativo sobre material real, o acervo nem é lido.
    calls.clear()
    _timeline(_doc(_scene(0, card=SceneCard(stamp={"pt-BR": "FALSO"})), _scene(1)))
    assert calls == []


def test_scam_artifact_description_and_negated_verdict():
    from studio.core import verdict

    artifact = ["Sites falsos de GTA 6 no Google", "Downloads falsos do GTA 6 em fórum",
                "Instaladores falsos espalhados no Discord", "Anúncios falsos de GTA 6 no Facebook",
                "Site copia o Extended Look e adiciona botão falso 'Play Now'", "Pop-up com alerta falso de vírus"]
    not_artifact = ["Rockstar alerta: cuidado com download falso", "GTA 6 vira isca para roubar dados",
                    "casal-conversivel · personagem · jason · lucia", "Como se proteger de sites falsos do GTA 6"]
    assert all(verdict.describes_scam(t) for t in artifact)
    assert not any(verdict.describes_scam(t) for t in not_artifact)
    assert all(verdict.is_negative(t) for t in ("FALSO", "É GOLPE", "FAKE", "GOLPE · PAGAMENTO"))
    assert not any(verdict.is_negative(t) for t in ("NÃO É GOLPE", "NÃO É FALSO", "NOT FAKE", "CONFIRMADO", "BOATO"))


def test_card_source_stays_in_old_projects_without_the_edit_layer():
    scenes = _timeline(_source_doc(edited=False))["scenes"]
    assert [(s.get("card") or {}).get("source") for s in scenes][:2] == ["WeLiveSecurity (ESET)", "Canaltech"]


def test_global_effects_policy_has_no_clean():
    style = STYLE.model_copy(deep=True)
    style.edit.grammar = EditGrammar(effects_policy="global")
    scenes = _timeline(_tiers_doc(), style=style)["scenes"]
    assert all("clean" not in s for s in scenes)
    assert all("prov" in s for s in scenes)


# --- legenda ------------------------------------------------------------------------------------------------------


def _emph(timeline) -> list[tuple[str, str, int]]:
    return [(w["text"], w["emph"], w["from"]) for line in timeline["scenes"][0]["captions"] for w in line["words"]
            if "emph" in w]


def test_emph_falls_on_the_right_words():
    doc = _doc(_scene(0, mark=BeatMark(role="reveal", emphasis=["chocante", "fim da frase"])), _scene(1))
    marked = _emph(_timeline(doc))
    assert [(t, e) for t, e, _ in marked] == [("chocante", "pop"), ("fim", "pop"), ("da", "pop"), ("frase", "pop")]
    assert marked[0][2] == _word_frame(3)  # a 1ª ocorrência, não a 2ª


def test_emph_follows_the_caption_emphasis_of_the_grammar():
    doc = _doc(_scene(0, mark=BeatMark(emphasis=["pessoal"]), fx=[
        FxEvent(type="text_pop", at="pessoal", params={"emoji": "😱"})]), _scene(1))
    for mode, expected in (("color", "color"), ("none", None)):
        style = STYLE.model_copy(deep=True)
        style.edit.grammar = EditGrammar(caption_emphasis=mode)
        marked = _emph(_timeline(doc, style=style))
        assert (marked[0][1] if marked else None) == expected
    style = STYLE.model_copy(deep=True)
    style.edit.grammar = EditGrammar(caption_emphasis="emoji")
    words = [w for line in _timeline(doc, style=style)["scenes"][0]["captions"] for w in line["words"]]
    assert next(w for w in words if w["text"] == "pessoal")["emoji"] == "😱"


# --- subplanos ----------------------------------------------------------------------------------------------------


def _pool() -> Pool:
    return Pool(items=[
        PoolItem(id="p1", tier="official", media="clip", path="uploads/prj_tl/pool/p1.mp4",
                 poster="uploads/prj_tl/pool/p1.jpg", start_s=4.0),
        PoolItem(id="p2", tier="capture", media="capture", path="uploads/prj_tl/pool/p2.jpg",
                 rects=[[0.1, 0.2, 0.3, 0.1]]),
    ])


def _shots_doc() -> ProjectDoc:
    media = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg")
    return _doc(
        _scene(0, shots=[
            SubShot(at="chocante", source="pool", pool_id="p1", speed=1.5),
            SubShot(at="pessoal", source="media", media=media, framing=ShotFraming(scale=1.25, x=0.3, y=0.4),
                    motion="push"),
            SubShot(at="novo", source="pool", pool_id="p2"),
            SubShot(at="chocante#2", source="reuse", reuse_of="scn_1"),
            SubShot(at="fim", source="pool", pool_id="p99"),
        ]),
        _scene(1),
    )


def test_shots_resolve_to_frames_covering_the_scene():
    timeline = _timeline(_shots_doc(), pool=_pool())
    scene = timeline["scenes"][0]
    shots = scene["shots"]
    assert shots[0]["from"] == 0 and "src" not in shots[0] and shots[0]["prov"] == "ai"  # antes do 1º corte: a cena
    assert [s["from"] for s in shots[1:]] == [_word_frame(k) for k in (3, 4, 7, 8, 10)]
    assert sum(s["dur"] for s in shots) == scene["durationInFrames"]
    for a, b in zip(shots, shots[1:], strict=False):
        assert a["from"] + a["dur"] == b["from"]
    clip, photo, capture, reuse, missing = shots[1:]
    assert clip == {**clip, "prov": "official", "video": "uploads/prj_tl/pool/p1.mp4",
                    "src": "uploads/prj_tl/pool/p1.jpg", "clipIn": 4.0, "speed": 1.5, "motion": "none"}
    assert photo["prov"] == "free" and photo["src"].endswith("0123456789abcdef.jpg")
    assert photo["framing"] == {"scale": 1.25, "x": 0.3, "y": 0.4} and photo["motion"] == "push"
    assert photo["easing"] in ("linear", "outCubic", "inOutQuad")
    assert capture["prov"] == "capture" and capture["motion"] == "static" and "easing" not in capture
    assert reuse == {**reuse, "prov": "ai", "src": "cache/images/scn_1.jpg"}
    assert missing["prov"] == "ai" and "src" not in missing
    assert any("p99" in w for w in timeline["warnings"])
    for shot in shots:
        assert {"from", "dur", "prov", "framing", "motion"} <= set(shot)


def test_shot_clip_carries_its_useful_seconds_for_the_renderer():
    # `seconds` (TimelineShot no renderer): com ele o plano com `hold` congela quando o trecho acaba.
    pool = Pool(items=[PoolItem(id="p1", tier="official", media="clip", path="uploads/prj_tl/pool/p1.mp4",
                                start_s=2.0, dur_s=1.25)])
    media = MediaRef(kind="clip", source="upload", asset="uploads/prj_tl/media/a.jpg",
                     video="uploads/prj_tl/media/a.mp4", dur_s=0.8, hold=True)
    doc = _doc(_scene(0, shots=[SubShot(at="chocante", source="pool", pool_id="p1", hold=True),
                                SubShot(at="pessoal", source="media", media=media)]), _scene(1))
    pool_shot, media_shot = _timeline(doc, pool=pool)["scenes"][0]["shots"][1:]
    assert pool_shot["seconds"] == 1.25 and pool_shot["hold"] is True and pool_shot["clipIn"] == 2.0
    assert media_shot["seconds"] == 0.8 and media_shot["hold"] is True
    assert "seconds" not in _timeline(_shots_doc(), pool=_pool())["scenes"][0]["shots"][1]  # sem dur_s: até o fim


def test_shots_read_the_pool_file_when_not_given(isolated_settings):
    save_pool("prj_tl", _pool())
    shots = _timeline(_shots_doc())["scenes"][0]["shots"]
    assert shots[1]["video"] == "uploads/prj_tl/pool/p1.mp4"


def test_highlight_takes_the_rect_of_the_capture():
    capture = MediaRef(kind="capture", source="capture", asset="uploads/prj_tl/media/0123456789abcdef.jpg",
                       rects=[[0.1, 0.2, 0.5, 0.1]])
    doc = _doc(_scene(0, visual=Visual(prompt="x", media=capture), fx=[
        FxEvent(type="highlight", at="chocante"), FxEvent(type="text_pop", at="pessoal,"),
        FxEvent(type="punch", at="novo")]), _scene(1))
    fx = {e["type"]: e for e in _timeline(doc)["scenes"][0]["fx"]}
    assert fx["highlight"]["params"]["rect"] == [0.1, 0.2, 0.5, 0.1]
    assert fx["text_pop"]["params"]["text"] == "pessoal"
    low, high = STYLE.edit.grammar.punch_scale.min, STYLE.edit.grammar.punch_scale.max
    assert low <= fx["punch"]["params"]["scale"] <= high


def test_same_doc_gives_the_same_timeline():
    a = _timeline(_shots_doc(), pool=_pool())
    b = _timeline(_shots_doc(), pool=_pool())
    assert hash_obj(a) == hash_obj(b)


def test_edit_triggers_validate_with_the_presenter_syntax():
    for scene in _shots_doc().scenes:
        for shot in scene.shots:
            assert shot.at is None or PRESENTER_AT_RE.match(shot.at)


# --- câmera -------------------------------------------------------------------------------------------------------


def test_grammar_motion_respects_the_shares():
    grammar = EditGrammar(static_share_min=0.25, slow_zoom_share=0.30)
    camera = {"ken_burns_in": 3.0, "ken_burns_out": 2.0, "drift": 1.0, "push": 1.0}
    picks = [edit_resolve.grammar_motion(camera, grammar, seed) for seed in range(3000)]
    static = picks.count("static") / len(picks)
    slow = sum(p in edit_resolve.SLOW_ZOOMS for p in picks) / len(picks)
    assert 0.22 <= static <= 0.28
    assert 0.26 <= slow <= 0.34


def test_scene_motion_follows_the_grammar_only_in_edited_projects():
    scenes = [_scene(i) for i in range(60)]
    old = _doc(*scenes)
    new = _doc(*[s.model_copy(update={"mark": BeatMark()}) for s in scenes])
    style = STYLE.model_copy(deep=True)
    style.edit.camera = {"ken_burns_in": 1.0}
    assert {scene_motion(old, style, s) for s in old.scenes} == {"ken_burns_in"}
    motions = [scene_motion(new, style, s) for s in new.scenes]
    assert "static" in motions and "push" in motions
    assert motions.count("ken_burns_in") / len(motions) <= style.edit.grammar.slow_zoom_share + 0.15
    chosen = new.scenes[0].model_copy(update={"motion": "parallax"})
    assert scene_motion(new, style, chosen) == "parallax"


def test_real_media_can_be_static_or_push_only_with_the_edit_layer():
    media = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg")
    old = _doc(*[_scene(i, visual=Visual(prompt="x", media=media)) for i in range(40)])
    assert {scene_motion(old, STYLE, s) for s in old.scenes} <= {"ken_burns_in", "ken_burns_out", "drift"}
    new = _doc(*[_scene(i, visual=Visual(prompt="x", media=media), mark=BeatMark()) for i in range(40)])
    assert "static" in {scene_motion(new, STYLE, s) for s in new.scenes}


@pytest.mark.parametrize("motion", ["static", "push"])
def test_chosen_motion_is_kept(motion):
    doc = _doc(_scene(0, motion=motion, mark=BeatMark()), _scene(1))
    assert _timeline(doc)["scenes"][0]["motion"] == motion


# --- revisão: SFX de evento -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["../../evil", "explosao", "whip"])
def test_unknown_sfx_kind_never_reaches_the_timeline(kind):
    from studio.media.audio import SFX_KINDS

    doc = _doc(_scene(0, fx=[FxEvent(type="sfx", at="chocante", params={"kind": kind}),
                             FxEvent(type="punch", at="novo", params={"sfx": kind})]), _scene(1))
    timeline = _timeline(doc)
    events = [e for e in timeline["sfx"] if "at" in e]
    assert len(events) == 2
    assert all(e["type"] == "impact" for e in events)
    assert all(e["type"] in SFX_KINDS for e in timeline["sfx"])
    assert sum(f"o som '{kind}' não existe; usei o impacto" in w for w in timeline["warnings"]) == 2


def test_sfx_from_the_library_keeps_the_procedural_fallback_quietly():
    doc = _doc(_scene(0, fx=[FxEvent(type="sfx", at="chocante", params={"kind": "explosao", "lib_id": "lib-7"})]),
               _scene(1))
    timeline = _timeline(doc)
    event = next(e for e in timeline["sfx"] if "at" in e)
    assert event["type"] == "impact" and event["lib_id"] == "lib-7"
    assert "warnings" not in timeline


def test_auto_sfx_off_keeps_only_the_user_event_sounds():
    style = STYLE.model_copy(deep=True)
    style.audio.sfx.auto = False
    doc = _doc(_scene(0, fx=[
        FxEvent(type="sfx", at="chocante", params={"kind": "whoosh"}),
        FxEvent(type="sfx", at="pessoal", params={"kind": "impact"}, origin="llm"),
        FxEvent(type="sfx", at="novo", params={"kind": "glitch"}, origin="user"),
        FxEvent(type="punch", at="fim", params={"sfx": "riser"}, locked=True),
    ]), _scene(1))
    events = [e for e in _timeline(doc, style=style)["sfx"] if "at" in e]
    assert sorted(e["type"] for e in events) == ["glitch", "riser"]


# --- revisão: gatilhos e duração ----------------------------------------------------------------------------------


def test_trigger_past_the_end_of_the_scene_drops_the_event_with_a_warning():
    doc = _doc(_scene(0, fx=[FxEvent(type="punch", at="@60000"), FxEvent(type="flash", at="chocante")],
                      shots=[SubShot(at="@60000", source="ai")]), _scene(1))
    timeline = _timeline(doc)
    scene = timeline["scenes"][0]
    assert [e["type"] for e in scene["fx"]] == ["flash"]
    assert "shots" not in scene  # o único corte passava do fim
    assert any("Cena 1" in w and "60,0 s passa do fim da cena" in w and "soco de zoom" in w
               for w in timeline["warnings"])
    assert any("o corte em 60,0 s passa do fim da cena" in w for w in timeline["warnings"])


def test_short_shots_merge_into_the_neighbour():
    media = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg")
    doc = _doc(_scene(0, shots=[SubShot(at="@1000", source="pool", pool_id="p2"),
                                SubShot(at="@1100", source="media", media=media)]), _scene(1))
    shots = _timeline(doc, pool=_pool())["scenes"][0]["shots"]
    minimum = edit_resolve.min_shot_frames(FPS)
    assert all(s["dur"] >= minimum for s in shots)
    assert [s["from"] for s in shots] == [0, round(1.0 * FPS)]  # o plano de 3 quadros cedeu ao seguinte
    assert shots[1]["prov"] == "free"


def test_fit_to_length_merges_a_shot_that_became_too_short():
    resolved = {"durationInFrames": 100, "shots": [
        {"from": 0, "dur": 50, "prov": "ai", "motion": "static"},
        {"from": 50, "dur": 46, "prov": "free", "motion": "push"},
        {"from": 96, "dur": 4, "prov": "capture", "motion": "static"},
    ], edit_resolve.CAPS_KEY: [None, None, None]}
    edit_resolve.fit_to_length(resolved, 30)
    assert [(s["from"], s["dur"], s["prov"]) for s in resolved["shots"]] == [(0, 50, "ai"), (50, 50, "free")]
    assert edit_resolve.CAPS_KEY not in resolved


# --- revisão: text_pop e idioma -----------------------------------------------------------------------------------


def test_text_pop_on_a_fused_number_takes_the_digits():
    from studio.core.project.document import ArtDirection

    text = "em dois mil e treze o jogo mudou tudo para sempre mesmo"
    toks = text.split()
    doc = ProjectDoc(id="prj_tl", title="t", style_id="curiosidades-longo", art=ArtDirection(), seed=3,
                     segments=[Segment(id="seg_0", text={"pt-BR": text})],
                     scenes=[_scene(0, span=len(toks), fx=[FxEvent(type="text_pop", at="2013")])])
    clips = {"seg_0": NarrationClip("seg_0", "x.wav", len(toks) * STEP + 0.2,
                                    [Word(t, k * STEP, k * STEP + 0.4) for k, t in enumerate(toks)])}
    timeline = build_timeline(doc, STYLE, "pt-BR", clips, {"scn_0": "cache/images/scn_0.jpg"})
    event = timeline["scenes"][0]["fx"][0]
    assert event["params"]["text"] == "2013"
    assert event["from"] == _word_frame(1)


def test_text_pop_without_text_is_dropped_with_a_warning():
    doc = _doc(_scene(0, fx=[FxEvent(type="text_pop", at="@1200", params={"sfx": "whoosh"}),
                             FxEvent(type="text_pop", at="@1500", params={"text": "UAU"})]), _scene(1))
    timeline = _timeline(doc)
    assert [e["params"]["text"] for e in timeline["scenes"][0]["fx"]] == ["UAU"]
    assert not [e for e in timeline["sfx"] if "at" in e]  # o som do texto que saiu também sai
    assert any('"texto que pula" em "@1200" não tem texto' in w for w in timeline["warnings"])


def test_translated_video_does_not_stamp_a_guessed_word():
    doc = _doc(_scene(0, fx=[FxEvent(type="text_pop", at="chocante"),
                             FxEvent(type="text_pop", at="pessoal", params={"text": "FOLKS"})]), _scene(1))
    timeline = _timeline(doc, lang="en")
    assert [e["params"]["text"] for e in timeline["scenes"][0]["fx"]] == ["FOLKS"]
    assert any("não foi traduzido" in w for w in timeline["warnings"])
    # No idioma principal, a palavra dita vira o texto.
    pt = _timeline(doc)["scenes"][0]["fx"]
    assert pt[0]["params"]["text"] == "chocante"


def test_translated_video_maps_the_emphasis_by_position():
    doc = _doc(_scene(0, mark=BeatMark(role="reveal", emphasis=["chocante", "fim da frase"])), _scene(1))
    marked = _emph(_timeline(doc, lang="en"))
    texts = [t for t, _e, _f in marked]
    assert texts[0] == "shocking"
    assert len(texts) == 4  # 1 + 3 palavras, na posição proporcional


def test_translated_trigger_prefers_the_position_over_a_false_cognate():
    words = [{"text": t, "from": k * 10} for k, t in enumerate("the final note is final".split())]
    script = "a nota final é final mesmo".split()
    trig = edit_resolve.trigger_frame("final#2", words, FPS, script=script)
    # Pela posição (5ª de 6 no pt → 4ª das 5 no inglês), não pela busca direta ("final#2" do inglês).
    assert trig.by_position and trig.frame == words[int(4 / 6 * 5)]["from"]
    assert edit_resolve.trigger_frame("2013", [{"text": "in", "from": 0}, {"text": "2013", "from": 7}], FPS,
                                      script=["em", "2013"]).frame == 7


# --- revisão: retângulo, câmera e material --------------------------------------------------------------------------


def test_highlight_takes_the_rect_of_the_shot_on_screen():
    doc = _doc(_scene(0, shots=[SubShot(at="aqui", source="pool", pool_id="p2"), SubShot(at="novo", source="ai")],
                      fx=[FxEvent(type="highlight", at="chocante"), FxEvent(type="zoom_rect", at="fim")]), _scene(1))
    timeline = _timeline(doc, pool=_pool())
    fx = timeline["scenes"][0]["fx"]
    assert [e["type"] for e in fx] == ["highlight"]
    assert fx[0]["params"]["rect"] == [0.1, 0.2, 0.3, 0.1]
    # O zoom no trecho cai num plano de IA, sem retângulo: sai com aviso.
    assert any('"zoom no trecho" em "fim" não tem trecho marcado' in w for w in timeline["warnings"])


def test_capture_scene_stays_static_in_edited_projects():
    capture = MediaRef(kind="capture", source="capture", asset="uploads/prj_tl/media/0123456789abcdef.jpg",
                       rects=[[0.1, 0.2, 0.5, 0.1]])
    scenes = [_scene(i, visual=Visual(prompt="x", media=capture), fx=[FxEvent(type="highlight", at="chocante")])
              for i in range(8)]
    doc = _doc(*scenes)
    assert {s["motion"] for s in _timeline(doc)["scenes"]} == {"static"}


def test_grammar_quota_holds_on_every_short_video():
    style = STYLE.model_copy(deep=True)
    style.edit.camera = {"ken_burns_in": 3.0, "ken_burns_out": 2.0, "drift": 1.0, "push": 1.0}
    grammar = style.edit.grammar
    for seed in range(40):
        doc = _doc(*[_scene(i, mark=BeatMark()) for i in range(10)]).model_copy(update={"seed": seed})
        motions = [scene_motion(doc, style, s) for s in doc.scenes]
        assert motions.count("static") >= grammar.static_share_min * 10
        assert sum(m in edit_resolve.SLOW_ZOOMS for m in motions) <= grammar.slow_zoom_share * 10 + 1e-9


def test_self_shot_on_a_video_scene_does_not_move_and_inherits_the_chosen_motion():
    doc = _doc(_scene(0, shots=[SubShot(at="chocante", source="self"), SubShot(at="novo", source="ai")]),
               _scene(1, motion="parallax", shots=[SubShot(at="chocante", source="self")]),
               _scene(2, motion="drift", shots=[SubShot(at="chocante", source="self")]))
    clips = {"scn_0": {"path": "cache/clips/scn_0.mp4", "seconds": 5.0}}
    scenes = _timeline(doc, clips=clips)["scenes"]
    assert {s["motion"] for s in scenes[0]["shots"]} == {"none"}
    assert [s["motion"] for s in scenes[1]["shots"]] == ["ken_burns_in", "ken_burns_in"]
    assert [s["motion"] for s in scenes[2]["shots"]] == ["drift", "drift"]


def test_real_photo_shot_uses_the_calm_cameras():
    from studio.core.timeline import REAL_MOTIONS

    style = STYLE.model_copy(deep=True)
    style.edit.camera = {"pan_left": 1.0, "punch_zoom": 1.0, "zoom_to": 1.0}
    photo = PoolItem(id="p3", tier="free", media="image", path="uploads/prj_tl/pool/p3.jpg")
    pool = Pool(items=[photo])
    shots = [SubShot(at=w, source="pool", pool_id="p3") for w in ("aqui", "chocante", "novo", "fim", "toda")]
    doc = _doc(*[_scene(i, shots=shots) for i in range(6)])
    for scene in _timeline(doc, style=style, pool=pool)["scenes"]:
        for shot in scene["shots"]:
            if shot["prov"] == "free":
                assert shot["motion"] in REAL_MOTIONS


def test_short_clip_in_a_long_shot_goes_back_to_the_scene_visual():
    pool = Pool(items=[
        PoolItem(id="p1", tier="free", media="clip", path="uploads/prj_tl/pool/p1.mp4", start_s=2.0, dur_s=1.0),
        PoolItem(id="p4", tier="free", media="clip", path="uploads/prj_tl/pool/p4.mp4", dur_s=1.0),
    ])
    doc = _doc(_scene(0, shots=[SubShot(at="aqui", source="pool", pool_id="p1", speed=0.5)]),
               _scene(1, shots=[SubShot(at="aqui", source="pool", pool_id="p4", hold=True)]))
    scenes = _timeline(doc, pool=pool)["scenes"]
    clip, back = scenes[0]["shots"][1:]
    assert clip["dur"] == 2 * FPS  # 1 s de trecho útil a meia velocidade
    assert back["from"] == clip["from"] + clip["dur"] and "video" not in back and back["prov"] == "ai"
    held = scenes[1]["shots"][1]
    assert held["hold"] is True and held["from"] + held["dur"] == scenes[1]["durationInFrames"]


def test_official_clip_is_cut_at_the_rights_limit():
    pool = Pool(items=[PoolItem(id="p1", tier="official", media="clip", path="uploads/prj_tl/pool/p1.mp4")])
    doc = _doc(_scene(0, shots=[SubShot(at="isso", source="pool", pool_id="p1", hold=True)]), _scene(1))
    timeline = _timeline(doc, pool=pool)
    shots = timeline["scenes"][0]["shots"]
    official = next(s for s in shots if s["prov"] == "official")
    assert official["dur"] == round(STYLE.art.sources.official_max_clip_s * FPS)
    assert shots[-1]["prov"] == "ai"
    assert any("trecho oficial cortado em 4,0 s" in w for w in timeline["warnings"])


def test_shot_files_point_to_the_prepared_images_and_missing_files_fall_back():
    media = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg")
    doc = _doc(_scene(0, shots=[SubShot(at="chocante", source="media", media=media),
                                SubShot(at="novo", source="pool", pool_id="p2")]), _scene(1))
    files = {media.asset: "cache/media/ab/fit.jpg", "uploads/prj_tl/pool/p2.jpg": None}
    timeline = _timeline(doc, pool=_pool(), shot_files=files)
    shots = timeline["scenes"][0]["shots"]
    assert shots[1]["src"] == "cache/media/ab/fit.jpg"
    assert shots[2]["prov"] == "ai" and "src" not in shots[2]
    assert any("Cena 1: o arquivo do subplano sumiu; ficou o visual da cena." == w for w in timeline["warnings"])


def test_reuse_warning_names_the_scene_label():
    doc = _doc(_scene(0, shots=[SubShot(at="chocante", source="reuse", reuse_of="scn_1")]), _scene(1))
    timeline = build_timeline(doc, STYLE, "pt-BR", _clips(doc), {})
    assert any("a Cena 2 reaproveitada no subplano não tem imagem" in w for w in timeline["warnings"])
    assert not any("scn_1" in w for w in timeline["warnings"])


# --- revisão: créditos do material dos subplanos ------------------------------------------------------------------


def test_credits_and_editorial_warning_include_the_shot_material(isolated_settings):
    from studio.workers.publish.credits import credit_lines, has_editorial

    photo = MediaRef(kind="photo", source="commons", asset="uploads/prj_tl/media/0123456789abcdef.jpg",
                     title="Foto do castelo", author="Fulano", license="CC BY 4.0",
                     page_url="https://commons.example/castelo")
    trailer = PoolItem(id="p7", tier="official", media="clip", path="uploads/prj_tl/pool/p7.mp4",
                       credit="Estúdio X (trailer oficial)", license="uso editorial",
                       page_url="https://video.example/trailer", editorial=True)
    save_pool("prj_tl", Pool(items=[trailer]))
    doc = _doc(_scene(0, shots=[SubShot(at="chocante", source="media", media=photo),
                                SubShot(at="novo", source="pool", pool_id="p7")]), _scene(1))
    lines = credit_lines(doc, "pt-BR")
    assert any("Foto do castelo" in line and "CC BY 4.0" in line for line in lines)
    assert any("Estúdio X" in line and "https://video.example/trailer" in line for line in lines)
    assert has_editorial(doc)
    materials = edit_resolve.shown_materials(doc, Pool(items=[trailer]))
    assert [type(m).__name__ for m in materials] == ["MediaRef", "PoolItem"]


def test_build_prepares_the_shot_files_like_the_scene_media(isolated_settings):
    import io

    from PIL import Image

    from studio.core.project import service
    from studio.media.real_media import store_image
    from studio.workers.pipeline.build import Builder

    created = service.create_project(title="Subplanos", style_id="misterios-short")
    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), (30, 90, 160)).save(buf, "JPEG")
    stored = store_image(buf.getvalue(), created.id)
    photo = MediaRef(kind="photo", source="commons", asset=stored.rel, width=1920, height=1080)
    gone = MediaRef(kind="photo", source="commons", asset=f"uploads/{created.id}/media/0123456789abcdef.jpg")
    clip = MediaRef(kind="clip", source="youtube", video=f"uploads/{created.id}/media/clip.mp4")
    seg = Segment(id="seg_1", text={"pt-BR": TEXT})
    scene = Scene(id="scn_a", segment_id="seg_1", visual=Visual(prompt="x"), shots=[
        SubShot(at="chocante", source="media", media=photo), SubShot(at="novo", source="media", media=gone),
        SubShot(at="fim", source="media", media=clip)])
    doc = created.model_copy(update={"segments": [seg], "scenes": [scene]})
    service.commit(doc.id, doc, "cenas")
    builder = Builder(doc.id, mode="preview")
    files = builder.prepare_shot_files(None)
    assert files[stored.rel].startswith("cache/media/")
    with Image.open(isolated_settings.data_dir / files[stored.rel]) as im:
        assert im.size == (builder.style.format.width, builder.style.format.height)
    assert files[gone.asset] is None and files[clip.video] is None  # sumiram: a timeline avisa e usa o da cena
    assert Builder(doc.id, mode="preview").prepare_shot_files(None)[stored.rel] == files[stored.rel]  # cache
