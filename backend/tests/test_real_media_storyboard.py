"""Storyboard com sugestões de mídia real: esquema, normalização, limite, reserva sem rosto, mock offline, mídia
mantida ao refazer as cenas e a manchete na tradução. O LLM é falso (monkeypatch de `authoring._llm`) ou o mock."""

from __future__ import annotations

import copy
from typing import Any

from studio.core.project import media_edit
from studio.core.project.document import (
    ArtDirection,
    Entity,
    MediaRef,
    ProjectDoc,
    RealHint,
    Scene,
    Segment,
    Source,
    Visual,
)
from studio.core.styles.library import get_style
from studio.providers.base import LLMResult, Usage
from studio.workers.pipeline import authoring, storyboard_check
from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA
from studio.workers.pipeline.storyboard_check import FACELESS, Beat

STYLE = get_style("misterios-short")
TEXTS = [
    ("hook", "Você conhece Miami de verdade? A cidade esconde um segredo antigo."),
    ("body", "Em dois mil e vinte e cinco, um trailer mostrou a cidade inteira de cima. A notícia correu o mundo em "
             "poucas horas e ninguém falou de outra coisa."),
    ("body", "A personagem principal aparece na praia ao entardecer. Ninguém viu o rosto dela até agora. A espera "
             "continua."),
    ("cta", "Siga para mais histórias como esta."),
]
LIGHTS = ["at dawn", "at noon", "at dusk", "at night", "in the rain", "under clouds", "in fog", "at sunrise"]
PHOTO = MediaRef(kind="photo", source="commons", asset="uploads/prj_sb/media/0123456789abcdef.jpg",
                 credit="Fulano / Wikimedia Commons · CC0", candidate_id="commons:File:Miami.jpg")


def base_doc(**extra) -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", role=role, text={"pt-BR": text}) for i, (role, text) in enumerate(TEXTS)]
    return ProjectDoc(**{"id": "prj_sb", "title": "Miami", "topic": "Miami Beach", "style_id": "misterios-short",
                         "seed": 11, "segments": segments,
                         "sources": [Source(title="Sem link"), Source(title="Notícia", url="https://news.example.com/a")],
                         **extra})


def response(beats: list[Beat], extra: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    props = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]["properties"]
    scenes = []
    for i, beat in enumerate(beats):
        entry: dict[str, Any] = dict.fromkeys(props)
        entry.update({"key": beat.key, "kind": "image", "source": "new", "language": "painting",
                      "prompt": f"An empty harbor boardwalk {LIGHTS[i % len(LIGHTS)]}, quiet water {i}."})
        entry.update((extra or {}).get(i, {}))
        scenes.append(entry)
    direction = {"genre": "curiosidades", "approach": "literal", "world": "a seaside city", "treatment": "soft",
                 "palette": [], "languages": ["painting"], "grade": None, "entities": [], "avoid": [],
                 "style_fit": "ok", "style_note": "", "suggested_style": None, "hook": "the city from above"}
    return {"direction": direction, "scenes": scenes}


class FakeLLM:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    def __call__(self, task, system, prompt, schema, context, project_id, job_id):
        self.calls.append({"system": system, "context": context})
        return LLMResult(data=copy.deepcopy(self.data), text="", usage=Usage()), "fake_llm"


def run(monkeypatch, doc: ProjectDoc, extra: dict[int, dict[str, Any]] | None = None):
    beats = authoring.plan_beats(doc, STYLE)
    fake = FakeLLM(response(beats, extra))
    monkeypatch.setattr(authoring, "_llm", fake)
    new_doc, _, review = authoring.storyboard(doc, STYLE, styles={})
    return new_doc, review, beats, fake


def by_key(doc: ProjectDoc, beats: list[Beat]) -> list[Scene]:
    return [doc.scenes_of(b.segment_id)[b.part] for b in beats]


def test_schema_has_the_flat_real_fields():
    item = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]
    for name, kind in (("real_kind", ["string", "null"]), ("real_query", ["string", "null"]),
                       ("real_note", ["string", "null"]), ("real_source_ref", ["integer", "null"])):
        assert item["properties"][name] == {"type": kind} and name in item["required"]
    assert item["additionalProperties"] is False
    for text in ("real_kind", "real_query", "real_note", "real_source_ref", "Wikimedia Commons"):
        assert text in authoring.STORYBOARD_SYSTEM


def test_real_from_normalizes_the_answer():
    sources = [Source(title="Sem link"), Source(title="Com link", url="https://news.example.com/a")]

    def real(kind: str = "image", **data):
        return authoring._real_from(data, kind, sources)

    assert real(real_kind="photo", real_query='"Miami Beach skyline"', real_note="  Foto   de Miami ") == RealHint(
        kind="photo", query="Miami Beach skyline", note="Foto de Miami")
    assert real(real_kind="video", real_query="x") is None  # tipo inválido sai
    assert real("stat", real_kind="photo", real_query="x") is None  # fora de imagem sai
    assert real(real_kind="photo") is None  # sem busca, nota nem fonte
    assert real(real_kind="news", real_source_ref=2).source_ref == 2
    assert real(real_kind="news", real_source_ref="2").source_ref == 2
    assert real(real_kind="news", real_source_ref=1, real_note="n").source_ref is None  # fonte sem link
    assert real(real_kind="news", real_source_ref=9, real_note="n").source_ref is None  # fora da faixa
    assert real(real_kind="news", real_source_ref=True, real_note="n").source_ref is None
    assert real(real_kind=" Official ", real_note="arte oficial").kind == "official"
    assert len(real(real_kind="photo", real_query="x" * 300).query) == 100


def test_storyboard_keeps_hints_only_on_image_scenes_and_caps_them(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    n = len(beats)
    limit = max(2, -(-n // 3))
    extra = {i: {"real_kind": "photo", "real_query": f"Miami place {i}"} for i in range(n)}
    extra[1] = {"real_kind": "news", "real_source_ref": 2, "real_note": "a notícia"}
    extra[2] = {"kind": "stat", "source": "reuse", "reuse_key": beats[0].key, "value": "2025", "label": "o ano",
                "real_kind": "photo", "real_query": "card"}
    extra[3] = {"real_kind": "official", "real_note": "arte oficial"}
    new_doc, review, _, _ = run(monkeypatch, doc, extra)
    scenes = by_key(new_doc, beats)
    hinted = [i for i, s in enumerate(scenes) if s.real is not None]
    assert len(hinted) == limit
    assert scenes[2].real is None  # cartão não tem sugestão
    assert 1 in hinted and 3 in hinted  # notícia com fonte e material oficial vêm antes das fotos
    assert scenes[1].real.source_ref == 2 and scenes[3].real.kind == "official"
    cut = [i for i in review["issues"] if i["code"] == "sugestoes_cortadas"]
    assert len(cut) == 1 and cut[0]["severity"] == "corrigido"
    assert cut[0]["message"].endswith(f"tiradas: ficam {limit} neste vídeo.")
    assert review["counts"]["real_hints"] == limit


def test_review_counts_has_real_hints_only_when_there_are_any(monkeypatch):
    new_doc, review, beats, _ = run(monkeypatch, base_doc())
    assert "real_hints" not in review["counts"]
    assert set(review["counts"]) == {"scenes", "new_images", "reused", "cards", "budget", "est_seconds"}
    scene = by_key(new_doc, beats)[1]
    hinted = media_edit.set_real(new_doc, scene.id, RealHint(kind="photo", query="Miami"))
    assert storyboard_check.review_counts(hinted, 5)["real_hints"] == 1
    attached = media_edit.attach_media(hinted, scene.id, PHOTO)
    assert "real_hints" not in storyboard_check.review_counts(attached, 5)  # já tem mídia


def _beat(scene: Scene, words: str) -> Beat:
    return Beat(key=f"{scene.segment_id}:{scene.part}", segment_id=scene.segment_id, part=scene.part,
                parts=scene.parts, index=0, role="body", words=words.split(), seconds=2.0)


def test_fix_real_reserve_hides_the_face():
    seg = Segment(id="seg_r", text={"pt-BR": "um dois três quatro cinco seis sete oito nove dez onze doze"})
    official = RealHint(kind="official", note="arte oficial")
    prompts = ["A woman walking on the beach at dusk.", "A woman seen from behind on the pier.",
               "The neon city skyline at night.", "A man at the bar counter.", "At a neon diner counter."]
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_r", part=i, parts=len(prompts), real=official,
                    visual=Visual(prompt=p, seed=i, entities=["lucia"] if i == 4 else []))
              for i, p in enumerate(prompts)]
    scenes[3].locked = True
    art = ArtDirection(entities=[Entity(id="lucia", name="Lucia", look="Latina woman in her 30s, dark hair")])
    doc = ProjectDoc(id="prj_r", title="r", style_id="misterios-short", segments=[seg], scenes=scenes, art=art)
    beats = [_beat(s, "um dois") for s in scenes]
    issues = storyboard_check.fix_real_reserve(doc, beats)
    assert [i.scene_id for i in issues] == ["scn_0", "scn_4"]
    assert all(i.code == "reserva_sem_rosto" and i.severity == "corrigido" for i in issues)
    assert doc.scene("scn_0").visual.prompt == f"A woman walking on the beach at dusk, {FACELESS}."
    assert doc.scene("scn_1").visual.prompt == prompts[1]  # já sem rosto
    assert doc.scene("scn_2").visual.prompt == prompts[2]  # sem gente
    assert doc.scene("scn_3").visual.prompt == prompts[3]  # travada
    assert doc.scene("scn_4").visual.prompt.endswith(f"{FACELESS}.")  # a entidade é gente
    doc.art.entities[0].look += ", seen from behind"
    doc.scene("scn_4").visual.prompt = prompts[4]
    assert storyboard_check.fix_real_reserve(doc, beats) == []  # a aparência já esconde o rosto
    photo = doc.model_copy(deep=True)
    photo.scene("scn_0").visual.prompt = prompts[0]
    photo.scene("scn_0").real = RealHint(kind="photo", query="beach")
    assert all(i.scene_id != "scn_0" for i in storyboard_check.fix_real_reserve(photo, beats))


def test_offline_mock_suggests_a_photo_and_a_news_card():
    doc, _ = authoring.generate_script(base_doc(), STYLE)
    doc = doc.model_copy(update={"sources": [Source(title="Sem link"), Source(title="Notícia",
                                                                                url="https://news.example.com/a")]})
    new_doc, provider, review = authoring.storyboard(doc, STYLE, styles={})
    assert provider == "mock_llm"
    hints = [s.real for s in new_doc.ordered_scenes() if s.real is not None]
    assert [h.kind for h in hints] == ["photo", "news"]
    assert hints[0].query == "Miami Beach" and hints[0].note == "Foto real de Miami Beach (Wikimedia Commons)"
    assert hints[1].source_ref == 2 and hints[1].note == "Cartão de notícia com a fonte do roteiro"
    assert review["counts"]["real_hints"] == 2
    assert not [i for i in review["issues"] if i["severity"] == "grave"]
    no_sources = authoring.storyboard(doc.model_copy(update={"sources": []}), STYLE, styles={})[0]
    assert [s.real.kind for s in no_sources.ordered_scenes() if s.real] == ["photo"]


def test_context_lists_the_sources(monkeypatch):
    _, _, _, fake = run(monkeypatch, base_doc())
    assert fake.calls[0]["context"]["sources"] == [{"n": 1, "title": "Sem link", "url": ""},
                                                    {"n": 2, "title": "Notícia", "url": "https://news.example.com/a"}]


def test_attached_media_survives_redoing_the_scenes(monkeypatch):
    first, _, beats, _ = run(monkeypatch, base_doc())
    target = by_key(first, beats)[2]
    with_media = media_edit.attach_media(first, target.id, PHOTO)
    second, review, _, _ = run(monkeypatch, with_media)
    scene = by_key(second, beats)[2]
    assert scene.id != target.id and scene.visual.media == PHOTO
    assert scene.visual.prompt  # o resto vem do storyboard novo (prompt de reserva)
    assert sum(1 for s in second.scenes if s.visual.media is not None) == 1
    kept = [i for i in review["issues"] if i["code"] == "midia_mantida"]
    assert [i["message"] for i in kept] == ["1 cena manteve a mídia real que você anexou."]
    assert review["counts"]["new_images"] == storyboard_check.review_counts(second, 99)["new_images"]
    assert not storyboard_check.has_own_image(scene)


def test_card_keeps_the_attached_media_as_background(monkeypatch):
    # Refazer as cenas: o trecho com mídia anexada voltou como cartão sem fundo. A mídia fica (é o fundo do cartão) e a
    # revisão não diz que o cartão "ficava sobre o fundo liso".
    first, _, beats, _ = run(monkeypatch, base_doc())
    for scene in first.scenes_of("seg_0"):
        scene.locked = True
    target = by_key(first, beats)[2]
    assert target.segment_id == "seg_1"
    with_media = media_edit.attach_media(first, target.id, PHOTO)
    card = {"kind": "stat", "source": "none", "value": "2025", "label": "o ano"}
    second, review, _, _ = run(monkeypatch, with_media, {2: card})
    scene = by_key(second, beats)[2]
    assert scene.kind == "stat" and scene.visual.media == PHOTO and not scene.visual.reuse_of
    assert not [i for i in review["issues"] if i["code"] == "cartao_sem_fundo" and i.get("scene_id") == scene.id]
    kept = [i for i in review["issues"] if i["code"] == "midia_mantida"]
    assert [i["message"] for i in kept] == ["1 cena manteve a mídia real que você anexou."]


def test_news_headline_is_a_screen_text_to_translate():
    doc = base_doc(languages=["pt-BR", "en-US"])
    doc.scenes = [Scene(id="scn_n", segment_id="seg_0", visual=Visual(media=MediaRef(
        kind="news", outlet="IGN", headline={"pt-BR": "Manchete da notícia"})))]
    assert {"id": "scn_n#media.headline", "text": "Manchete da notícia"} in authoring.missing_screen_texts(doc, "en-US")
    translated, _ = authoring.translate(doc, "en-US", screen_only=True)
    assert translated.scene("scn_n").visual.media.headline["en-US"] == "[en-US] Manchete da notícia"
    assert authoring.missing_screen_texts(translated, "en-US") == []
