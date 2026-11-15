"""Storyboard v2: direção de arte, batidas, cartões, reaproveitamento, orçamento e revisão.

O LLM é falso (monkeypatch de `authoring._llm`); a resposta realista do Short do GTA 6 está em
`fixtures/storyboard_v2_gta.json`, com as chaves "#<índice do segmento>:<batida>".
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from studio.core.hashing import seed_from
from studio.core.project import service
from studio.core.project.document import ProjectDoc, Segment, Source
from studio.core.styles.library import get_style
from studio.core.styles.visual import FOCUS_ROTATION, SHOTS
from studio.core.text import word_count
from studio.providers.base import LLMResult, Usage
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import authoring, storyboard_check
from studio.workers.pipeline.build import scene_needs_image
from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA

FIXTURE = Path(__file__).parent / "fixtures" / "storyboard_v2_gta.json"

# Segmentos do projeto prj_8befnbkkzr (cópia do banco), na ordem.
GTA = [
    "GTA 6 vai ser o melhor jogo de todos os tempos? Tem um detalhe que quase ninguém comenta.",
    "Pensa comigo. GTA 5 saiu em dois mil e treze. Vendeu mais de duzentos milhões de cópias. Depois disso, silêncio.",
    "Até dezembro de dois mil e vinte e três. O primeiro trailer passou de noventa milhões de visualizações em "
    "vinte e quatro horas. Recorde no YouTube para um vídeo que não era de música.",
    "Em maio de dois mil e vinte e cinco, o segundo trailer foi ainda mais longe. Quatrocentos e setenta e cinco "
    "milhões de visualizações em um dia, somando as plataformas.",
    "E o cenário? Leonida. A volta de Vice City. Com Lucia, a primeira protagonista mulher da era moderna da série.",
    "Agora, o segredo. Depois de dois adiamentos, quase ninguém fora da Rockstar jogou um minuto sequer. Melhor da "
    "história ainda é promessa. O recorde que ele já quebrou é outro: o da expectativa.",
    "Siga para descobrir o próximo mistério.",
]
ROLES = ["hook", "body", "body", "body", "body", "body", "cta"]
STYLES = {"games-cultura-pop-short": "Games e Cultura Pop (Short)", "misterios-short": "Mistérios (Short)"}
GAMES = "games-cultura-pop-short"


def gta_doc(style_id: str = GAMES) -> ProjectDoc:
    segments = [Segment(id=f"seg_gta{i}", role=role, text={"pt-BR": text}) for i, (role, text) in enumerate(zip(ROLES, GTA, strict=True))]
    return ProjectDoc(
        id="prj_gta",
        title="GTA 6 vai ser o melhor jogo de todos os tempos?",
        topic="GTA 6 vai ser o melhor jogo de todos os tempos",
        style_id=style_id,
        seed=4242,
        segments=segments,
        sources=[Source(title="Take-Two", note="GTA V vendeu mais de 200 milhões de cópias")],
    )


def load_fixture(doc: ProjectDoc) -> dict[str, Any]:
    """A resposta do GTA com as chaves "#i:k" trocadas pelos ids reais dos segmentos."""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def real(key: str | None) -> str | None:
        if key and key.startswith("#"):
            index, part = key[1:].split(":")
            return f"{doc.segments[int(index)].id}:{part}"
        return key

    for scene in data["scenes"]:
        scene["key"] = real(scene["key"])
        scene["reuse_key"] = real(scene["reuse_key"])
    return data


def scene_data(data: dict[str, Any], doc: ProjectDoc, ref: str) -> dict[str, Any]:
    index, part = ref.split(":")
    key = f"{doc.segments[int(index)].id}:{part}"
    return next(s for s in data["scenes"] if s["key"] == key)


class FakeLLM:
    """Devolve as respostas na ordem (a última se repete); `fail_after` faz a chamada seguinte falhar."""

    def __init__(self, *responses: dict[str, Any], fail_after: int | None = None) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.fail_after = fail_after

    def __call__(self, task, system, prompt, schema, context, project_id, job_id):
        self.calls.append({"task": task, "system": system, "prompt": prompt, "schema": schema, "context": context})
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise RuntimeError("cadeia de LLM indisponível")
        data = self.responses[min(len(self.calls), len(self.responses)) - 1]
        return LLMResult(data=copy.deepcopy(data), text="", usage=Usage()), "fake_llm"


def run(monkeypatch, doc: ProjectDoc, style, *responses: dict[str, Any], fail_after: int | None = None):
    fake = FakeLLM(*responses, fail_after=fail_after)
    monkeypatch.setattr(authoring, "_llm", fake)
    new_doc, provider, review = authoring.storyboard(doc, style, styles=STYLES)
    return new_doc, provider, review, fake


def codes(review: dict[str, Any], severity: str | None = None) -> list[str]:
    return [i["code"] for i in review["issues"] if severity is None or i["severity"] == severity]


def test_v2_builds_direction_cards_and_reuse(monkeypatch):
    doc = gta_doc()
    style = get_style(GAMES)
    new_doc, provider, review, fake = run(monkeypatch, doc, style, load_fixture(doc))
    assert provider == "fake_llm" and len(fake.calls) == 1
    assert fake.calls[0]["schema"] is STORYBOARD_SCHEMA
    scenes = new_doc.ordered_scenes()
    assert len(scenes) == 19
    for seg in new_doc.segments:  # cada segmento coberto exatamente pelas batidas
        assert sum(s.span or 0 for s in new_doc.scenes_of(seg.id)) == word_count(seg.text_for("pt-BR"))

    art = new_doc.art
    assert art is not None and art.source == "ai"
    assert art.genre == "games" and art.languages == ["key_art", "render_3d"] and art.grade == "vivid"
    assert art.entity("lucia") is not None and art.entity("lucia").look.startswith("original Latina")
    assert "candles" in art.avoid and art.style_fit == "ok" and art.suggested_style is None

    hook = scenes[0]
    assert hook.kind == "image" and hook.motion == "punch_zoom"
    assert hook.visual.prompt.startswith("Neon coastal city") and "cores saturadas" not in hook.visual.prompt
    assert (hook.visual.language, hook.visual.shot, hook.visual.composition) == ("key_art", "wide", "upper_half")
    assert hook.visual.entities == ["vice_city"]
    assert hook.visual.seed == seed_from(doc.seed, doc.segments[0].id, 0)
    assert hook.card is not None and hook.card.resolved("pt-BR") == {"headline": "MELHOR JOGO DA HISTÓRIA?"}

    # reaproveitamento sempre aponta para a raiz, sem imagem nova
    assert scenes[1].visual.reuse_of == hook.id and scenes[1].focus == "top" and scenes[1].motion == "zoom_to"
    cta = scenes[-1]
    assert cta.visual.reuse_of == hook.id and cta.visual.prompt == "" and not scene_needs_image(cta)
    assert all(new_doc.reuse_root(s) is not None for s in scenes)

    stat = scenes[3]
    assert stat.kind == "stat" and stat.visual.reuse_of == scenes[2].id
    assert stat.card.resolved("pt-BR") == {"value": "200 MI+", "label": "cópias vendidas", "source": "Take-Two"}
    lone = scenes[10]  # 475 MI sobre o fundo animado
    assert lone.kind == "stat" and lone.visual.type == "none" and lone.motion == "static"
    compare = scenes[11].card.resolved("pt-BR")
    assert scenes[11].kind == "compare" and compare["highlight"] == 1
    assert compare["items"] == [{"label": "Trailer 1", "value": "90 MI"}, {"label": "Trailer 2", "value": "475 MI"}]
    assert scenes[7].kind == "title" and scenes[7].overlay_text == {"pt-BR": "Recorde no YouTube"}
    assert scenes[7].card.resolved("pt-BR") == {"stamp": "RECORDE"}
    assert scenes[14].kind == "stat" and scenes[14].card.resolved("pt-BR")["stamp"] == "ADIADO"
    assert scenes[16].kind == "text" and scenes[16].visual.reuse_of == scenes[12].id
    assert scenes[13].visual.entities == ["lucia"]
    # o nome da personagem (tirado da narração) não vai para o gerador: a imagem descreve a aparência
    assert scenes[13].visual.prompt.startswith("The Latina woman leaning on a convertible")
    assert "lucia" not in scenes[13].visual.prompt.lower()

    assert review["counts"] == {"scenes": 19, "new_images": 9, "reused": 9, "cards": 7, "budget": 10, "est_seconds": 315}
    assert [(i["code"], i["severity"], i["scene_id"]) for i in review["issues"]] == [
        ("nome_no_prompt", "corrigido", scenes[13].id)], review["issues"]
    assert review["retried"] is False
    assert (review["style_fit"], review["style_note"], review["suggested_style"]) == ("ok", art.style_note, None)
    assert new_doc.storyboard_approved is False
    ProjectDoc.model_validate(json.loads(json.dumps(new_doc.model_dump(mode="json"))))

    prompt = fake.calls[0]["prompt"]
    assert "Tratamento de referência do estilo: cores saturadas de pôr do sol e neon" in prompt
    assert 'Limite de imagens novas (source "new"): 10 de 19 batidas.' in prompt
    assert "Linguagens preferidas do estilo: key_art, render_3d, ui_mockup, editorial" in prompt
    assert "- games-cultura-pop-short: Games e Cultura Pop (Short)" in prompt
    assert "- key_art: Pôster ilustrado" in prompt and "photo" not in prompt.split("Linguagens visuais")[1].split("\n")[1]
    assert "Legenda: no meio" in prompt and "Fontes do roteiro:\n- Take-Two: GTA V vendeu" in prompt
    assert f"- {doc.segments[1].id}:0 [body, ~3,8 s]: Pensa comigo. GTA 5 saiu em dois mil e treze." in prompt
    assert "Direção definida pelo usuário" not in prompt
    assert "diretor de arte" in fake.calls[0]["system"]


def test_v2_enforces_new_image_budget(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    for index, scene in enumerate(data["scenes"]):  # tudo imagem nova (os números ficam na headline)
        headline = scene["headline"] or scene["value"]
        scene.update({"kind": "image", "source": "new", "reuse_key": None, "shot": None, "headline": headline,
                      "prompt": scene["prompt"] or f"Quiet subject {index} on a palm avenue.", "value": None,
                      "label": None, "items": None, "overlay_text": None})
    scene_data(data, doc, "5:3")["entities"] = ["lucia"]  # 2ª aparição da Lucia
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    assert len(fake.calls) == 1
    assert review["counts"]["new_images"] == 10 == review["counts"]["budget"]
    assert codes(review, "corrigido").count("orcamento") == 9
    assert all(i["scene_id"] for i in review["issues"] if i["code"] == "orcamento")
    own = [i for i, s in enumerate(scenes) if storyboard_check.has_own_image(s)]
    # gancho e 1ª aparição da Lucia têm prioridade; o resto entra na ordem
    assert own == [0, 1, 2, 3, 4, 5, 6, 7, 8, 13]
    assert scenes[17].visual.reuse_of == scenes[13].id  # reaproveita a imagem com a mesma entidade
    assert scenes[9].visual.reuse_of == scenes[8].id and scenes[9].visual.prompt == ""
    assert scenes[9].focus == FOCUS_ROTATION[1] and scenes[10].focus == FOCUS_ROTATION[2]
    assert scenes[9].motion == "zoom_to"
    assert "cena 9" in next(i["message"] for i in review["issues"] if i["scene_id"] == scenes[9].id)
    shots = [scenes[i].visual.shot for i in own]
    assert all(a != b for a, b in zip(shots, shots[1:], strict=False))


def test_v2_normalizes_invalid_values(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    data["direction"].update({
        "genre": " Games ", "approach": "documental", "style_fit": "pessimo", "grade": "sepia-forte",
        "languages": ["photo", "Key-Art", "nope"], "suggested_style": "estilo-inexistente",
        "entities": [{"id": "Lúcia Maria", "name": "Lúcia", "look": "x"}, {"id": "lucia maria", "name": "dup", "look": ""}],
    })  # fmt: skip
    scene_data(data, doc, "0:0").update({"language": "photo"})
    scene_data(data, doc, "0:1").update({"reuse_key": data["scenes"][5]["key"], "focus": None})  # para a frente
    scene_data(data, doc, "1:0").update({"shot": "dutch_angle"})
    scene_data(data, doc, "2:2").update({"kind": "video"})
    scene_data(data, doc, "2:3").update({"kind": "count", "value": "24 H"})
    scene_data(data, doc, "3:0").update({"motion": "voar", "composition": "center"})
    scene_data(data, doc, "3:1").update({"reuse_key": "nao_existe", "prompt": "Red sports car seen from behind on the highway."})
    scene_data(data, doc, "3:2").update({"kind": "count"})
    scene_data(data, doc, "3:3").update({"highlight": 7})
    scene_data(data, doc, "4:1").update({"entities": ["Lúcia Maria", "ghost"], "value": "99"})
    scene_data(data, doc, "6:0").update({"focus": "middle", "motion": None})
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    art = new_doc.art
    assert (art.genre, art.approach, art.style_fit, art.grade, art.suggested_style) == ("games", "literal", "ok", None, None)
    assert art.languages == ["key_art"] and [e.id for e in art.entities] == ["lucia_maria"]
    assert scenes[0].visual.language == "key_art"  # photo sem photoreal vira a dominante
    assert scenes[1].visual.reuse_of is None and scenes[1].visual.prompt == "Tem um detalhe que quase ninguém comenta."
    assert scenes[2].visual.shot in SHOTS and scenes[2].visual.shot != scenes[1].visual.shot
    assert scenes[6].kind == "image" and storyboard_check.has_own_image(scenes[6])
    assert scenes[7].kind == "stat" and scenes[7].card.resolved("pt-BR")["value"] == "24 H"  # count fora do Top 10
    assert scenes[8].motion is None and scenes[8].visual.composition in ("upper_half", "left_third", "right_third")
    assert scenes[9].visual.reuse_of is None and scenes[9].visual.prompt.startswith("Red sports car")
    assert scenes[10].kind == "stat"
    assert "highlight" not in scenes[11].card.resolved("pt-BR")
    assert scenes[13].visual.entities == ["lucia_maria"] and scenes[13].card is None  # valor só vale em stat
    assert scenes[18].visual.reuse_of == scenes[0].id and scenes[18].focus in FOCUS_ROTATION
    assert scenes[18].focus != "middle" and scenes[18].motion == "zoom_to"
    assert review["counts"]["new_images"] <= review["counts"]["budget"]
    assert codes(review, "grave") == []


def test_v2_suggests_existing_style_only(monkeypatch):
    doc = gta_doc("misterios-short")
    data = load_fixture(doc)
    data["direction"].update({"style_fit": "fraco", "style_note": "Notícia de games não é mistério.",
                              "suggested_style": "games-cultura-pop-short"})
    new_doc, _, review, fake = run(monkeypatch, doc, get_style("misterios-short"), data)
    assert (review["style_fit"], review["suggested_style"]) == ("fraco", "games-cultura-pop-short")
    assert new_doc.art.languages == ["key_art", "render_3d"]  # a direção pode sair das linguagens do estilo
    prompt = fake.calls[0]["prompt"]
    assert "(id misterios-short)" in prompt and "Tratamento de referência do estilo: pintura a óleo sombria" in prompt
    # sugerir o próprio estilo do projeto não vale
    data["direction"]["suggested_style"] = "misterios-short"
    _, _, review, _ = run(monkeypatch, doc, get_style("misterios-short"), data)
    assert review["suggested_style"] is None


def test_v2_locked_segments_keep_old_scenes(monkeypatch):
    doc = gta_doc()
    style = get_style(GAMES)
    first, _, _, _ = run(monkeypatch, doc, style, load_fixture(doc))
    hook = first.ordered_scenes()[0]
    locked_seg = first.segments[4].id
    kept = first.scenes_of(locked_seg)
    kept[1].visual = kept[1].visual.model_copy(update={"reuse_of": hook.id, "prompt": "", "language": None})
    for scene in kept:
        scene.locked = True
    before = {s.id: s.model_dump() for s in kept}
    data = load_fixture(first)
    data["scenes"] = [s for s in data["scenes"] if not s["key"].startswith(locked_seg)]
    second, _, review, fake = run(monkeypatch, first, style, data)
    after = second.scenes_of(locked_seg)
    assert [s.id for s in after] == list(before)
    assert after[0].model_dump() == before[after[0].id]
    # a cena travada que reaproveitava o gancho refeito herda a imagem dele (mesmo cache)
    assert after[1].visual.reuse_of is None and after[1].visual.prompt == hook.visual.prompt
    assert after[1].visual.seed == hook.visual.seed
    assert second.ordered_scenes()[0].id != hook.id
    prompt = fake.calls[0]["prompt"]
    assert f"{locked_seg}:0 [" not in prompt and "[body, cenas travadas] E o cenário?" in prompt
    assert review["counts"]["scenes"] == 19
    assert second.reuse_root(second.ordered_scenes()[14]) is not None  # 5:0 apontava para a batida travada


def test_check_flags_number_as_image_and_avoid_terms(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "1:1").update({"kind": "image", "source": "new", "reuse_key": None, "value": None, "label": None,
                                         "source_note": None, "prompt": "Stack of boxed games on a store shelf."})
    scene_data(data, doc, "5:1")["prompt"] = "Closed studio gate at night with candles burning by the barrier."
    scene_data(data, doc, "4:0")["prompt"] += " Oil painting style."
    scene_data(data, doc, "5:3")["prompt"] = "Crowd watching a gameplay screenshot on a giant screen."
    new_doc, provider, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    keys = {i["code"]: i["scene_key"] for i in review["issues"] if i["severity"] == "grave"}
    seg = doc.segments
    assert keys == {
        "numero_sem_cartao": f"{seg[1].id}:1",
        "motivo_proibido": f"{seg[5].id}:1",
        "estilo_no_prompt": f"{seg[4].id}:0",
        "marca": f"{seg[5].id}:3",
    }
    message = next(i["message"] for i in review["issues"] if i["code"] == "numero_sem_cartao")
    assert message == "A batida fala de duzentos milhões e não mostra o número."
    assert all(i["scene_id"] for i in review["issues"] if i["severity"] == "grave")
    # uma correção pedida; a resposta igual não ganha e fica a primeira
    assert len(fake.calls) == 2 and review["retried"] is True and provider == "fake_llm"
    retry = fake.calls[1]["prompt"]
    assert "Sua resposta anterior teve estes problemas; devolva tudo corrigindo:" in retry
    assert f"- [numero_sem_cartao] batida {seg[1].id}:1 (A batida fala de duzentos milhões" in retry
    assert "Sua resposta anterior (JSON):" in retry and fake.calls[1]["context"]["retry"] is True
    assert review["issues"][0]["severity"] == "grave"


def test_check_ignores_negated_terms():
    assert storyboard_check.find_terms("Crowd at night, no logos, without candles.", ["logo", "candles"]) == []
    assert storyboard_check.find_terms("A GTA logo over frames from the game trailers.", storyboard_check.BRAND_TERMS) == [
        "logo", "game trailer"]
    assert storyboard_check.find_terms("Miami-style hotel lobby.", storyboard_check.STYLE_TERMS) == []


def test_check_terms_skip_common_words():
    find, brand, style = storyboard_check.find_terms, storyboard_check.BRAND_TERMS, storyboard_check.STYLE_TERMS
    # palavras comuns não são marca nem estilo; só a forma técnica ou de mídia conta
    assert find("A rusted trailer in a trailer park.", brand) == [] and find("A gas leak in an old kitchen.", brand) == []
    for prompt in ("A family watching a 4K TV.", "A canvas tent on a beach.", "A painter holding a palette.",
                   "A retro style diner at noon."):
        assert find(prompt, style) == [], prompt
    assert find("Leaked footage of the new map.", brand) == ["leaked"]
    assert find("A street in the style of Miami Vice, rendered in 4K.", style)[:1] == ["in the style of"]
    # "news" não vira "new"
    assert find("A new console on a desk.", ["news"]) == [] and find("Breaking news on a TV.", ["news"]) == ["news"]


def test_check_numbers_tolerate_small_counts_signs_and_halves():
    from studio.core.numbers import notable_number_runs
    from studio.core.text import words

    # contagem pequena por extenso não pede cartão; número de verdade pede
    for text in ("Depois de dois adiamentos, nada.", "Saiu duas vezes.", "Há três anos.", "Começou do zero."):
        assert notable_number_runs(words(text)) == [], text
    assert notable_number_runs(words("Vendeu duzentos milhões.")) == [(1, 3, 200_000_000)]
    assert notable_number_runs(words("Dez anos depois.")) == [(0, 1, 10)]
    # o sinal não conta, "meio milhão" vale 500 mil e o 1 sempre vale
    doc = ProjectDoc(id="prj_n", title="n", style_id=GAMES, segments=[
        Segment(id="seg_1", text={"pt-BR": "As vendas caíram trinta por cento. Meio milhão de pessoas viram o primeiro trailer."})])
    allowed = storyboard_check.allowed_numbers(doc)
    from studio.core.numbers import card_number

    for value in ("-30%", "500 MIL", "#1", "1º", "1 DIA"):
        assert storyboard_check._known_number(card_number(value), allowed), value
    assert not storyboard_check._known_number(card_number("2 MI"), allowed)


def test_check_invented_number_is_only_a_warning_outside_portuguese(monkeypatch):
    doc = gta_doc()
    doc.primary_language, doc.languages = "en-US", ["en-US"]
    doc.segments =[Segment(id="seg_en", role="hook", text={"en-US": "It sold more than two hundred million copies, and "
                                                                       "the trailer got ninety million views in a day."})]
    beats = authoring.plan_beats(doc, get_style(GAMES))
    data = {"direction": {}, "scenes": [
        {"key": beats[0].key, "kind": "image", "source": "new", "prompt": "A crowd in a mall."},
        *({"key": b.key, "kind": "stat", "source": "none", "value": "200M+", "label": "copies"} for b in beats[1:]),
    ]}
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    invented = [i for i in review["issues"] if i["code"] == "numero_inventado"]
    assert invented and all(i["severity"] == "aviso" for i in invented)
    assert len(fake.calls) == 1 and "cartao_trocado" not in codes(review)
    assert any(s.kind == "stat" for s in new_doc.scenes)


def test_check_drops_invented_card_number(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "2:1")["value"] = "999 MI"
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scene = new_doc.ordered_scenes()[5]
    assert len(fake.calls) == 2 and review["retried"] is True
    assert scene.kind == "text" and scene.card is None
    assert "numero_inventado" not in codes(review)
    swapped = [i for i in review["issues"] if i["code"] == "cartao_trocado"]
    assert len(swapped) == 1 and swapped[0]["severity"] == "corrigido" and swapped[0]["scene_id"] == scene.id
    assert "999 MI" in swapped[0]["message"]
    assert codes(review, "grave") == []


def test_check_accepts_numbers_from_sources_and_names(monkeypatch):
    doc = gta_doc()
    doc.sources.append(Source(title="Relatório", note="GTA Online rendeu 8 bilhões de dólares"))
    data = load_fixture(doc)
    scene_data(data, doc, "2:3").update({"kind": "stat", "value": "US$ 8 BI", "label": "GTA Online", "overlay_text": None})
    scene_data(data, doc, "0:1").update({"kind": "stat", "source": "reuse", "value": "GTA 6", "label": "o próximo"})
    _, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    assert "numero_inventado" not in codes(review) and len(fake.calls) == 1


def test_v2_retries_once_on_severe_issues(monkeypatch):
    doc = gta_doc()
    good = load_fixture(doc)
    bad = copy.deepcopy(good)
    scene_data(bad, doc, "1:1").update({"kind": "image", "source": "new", "value": None, "label": None, "source_note": None,
                                        "prompt": "Stack of boxed games on a store shelf."})
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), bad, good, bad)
    assert len(fake.calls) == 2  # no máximo 2 chamadas
    assert review["retried"] is True and codes(review, "grave") == []
    assert new_doc.ordered_scenes()[3].kind == "stat"
    # a correção falhou: fica a primeira resposta, sem quebrar o storyboard
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), bad, fail_after=1)
    assert len(fake.calls) == 2 and review["retried"] is False
    assert codes(review, "grave") == ["numero_sem_cartao"] and new_doc.ordered_scenes()[3].kind == "image"


def test_v2_missing_beat_falls_back_to_new_image_without_bible(monkeypatch):
    doc = gta_doc()
    style = get_style(GAMES)
    data = load_fixture(doc)
    missing = f"{doc.segments[0].id}:1"
    data["scenes"] = [s for s in data["scenes"] if s["key"] != missing]
    new_doc, _, review, _ = run(monkeypatch, doc, style, data)
    scene = new_doc.ordered_scenes()[1]
    assert scene.kind == "image" and scene.visual.prompt == "Tem um detalhe que quase ninguém comenta."
    assert style.art.bible not in scene.visual.prompt and scene.visual.language == "key_art"
    assert scene.visual.shot in SHOTS and scene.visual.reuse_of is None and scene.span == 7
    assert review["counts"]["new_images"] == 10


def test_v2_hook_card_becomes_new_image(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "0:0").update({"kind": "title", "source": "none", "overlay_text": "MELHOR JOGO?", "headline": None})
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    hook = new_doc.ordered_scenes()[0]
    assert hook.kind == "image" and storyboard_check.has_own_image(hook) and hook.motion == "punch_zoom"
    assert hook.visual.prompt == new_doc.art.hook and hook.visual.language == "key_art"
    assert hook.card.resolved("pt-BR") == {"headline": "MELHOR JOGO?"}
    assert "gancho_sem_imagem" in codes(review, "corrigido")
    assert review["counts"]["new_images"] <= review["counts"]["budget"]


def test_check_fixes_repeated_shot(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "1:0")["shot"] = "wide"  # igual ao gancho
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    scene = new_doc.ordered_scenes()[2]
    assert scene.visual.shot == "close"
    issue = next(i for i in review["issues"] if i["code"] == "plano_repetido")
    assert issue["severity"] == "corrigido" and issue["scene_id"] == scene.id
    assert issue["message"] == "Plano repetido trocado para close."


def test_check_warns_repeated_motif_and_too_many_cards(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    for ref in ("0:0", "1:0", "2:0", "2:2", "3:0"):
        scene_data(data, doc, ref)["prompt"] += " A golden hourglass on a table."
    for ref in ("4:0", "4:1", "5:1"):  # cartões seguidos e demais
        scene_data(data, doc, ref).update({"kind": "text", "source": "reuse", "reuse_key": data["scenes"][0]["key"]})
    data["direction"]["avoid"] = []
    _, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    warnings = {i["code"]: i for i in review["issues"] if i["severity"] == "aviso"}
    assert set(warnings) == {"motivo_repetido", "cartoes_seguidos", "cartoes_demais"}
    motifs = [i for i in review["issues"] if i["code"] == "motivo_repetido"]
    assert any('"hourglass" aparece em 5 de' in i["message"] for i in motifs)
    assert all(i["scene_id"] is None for i in motifs) and len(motifs) <= 3
    assert warnings["cartoes_demais"]["message"].startswith("10 de 19 cenas são cartões")
    assert len(fake.calls) == 1  # aviso não pede correção


def test_v2_top10_keeps_count(monkeypatch):
    style = get_style("top10-listas")
    segments = [
        Segment(id="seg_hook", role="hook", text={"pt-BR": "Estes são os lugares mais estranhos do planeta, e o primeiro vai te surpreender."}),
        Segment(id="seg_i10", role="item", chapter="#10", text={"pt-BR": "Número dez. O lago que muda de cor sozinho todos os anos no verão."}),
        Segment(id="seg_i9", role="item", chapter="#9", text={"pt-BR": "Número nove. A floresta onde as árvores crescem tortas para o mesmo lado."}),
        Segment(id="seg_cta", role="cta", text={"pt-BR": "Siga para ver o resto da lista."}),
    ]
    doc = ProjectDoc(id="prj_top", title="Lugares estranhos", style_id="top10-listas", seed=7, segments=segments)
    new_doc, _, review, fake = run(monkeypatch, doc, style, {"direction": {}, "scenes": []})
    counts = [s for s in new_doc.ordered_scenes() if s.kind == "count"]
    assert [(s.segment_id, s.number, s.overlay_text) for s in counts] == [
        ("seg_i10", 10, {"pt-BR": "#10"}), ("seg_i9", 9, {"pt-BR": "#9"}),
    ]
    assert all(s.part == 0 for s in counts)
    assert '[item, ~' in fake.calls[0]["prompt"] and 'count "#10"' in fake.calls[0]["prompt"]
    assert new_doc.art.languages == style.art.languages[:3]  # direção vazia cai nas linguagens do estilo
    assert codes(review, "grave") == []


def test_v2_user_direction_is_kept(monkeypatch):
    doc = gta_doc()
    first, _, _, _ = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    first.art.source = "user"
    first.art.world = "rainy cyberpunk harbor"
    data = load_fixture(first)
    data["direction"]["world"] = "outro mundo"
    second, _, _, fake = run(monkeypatch, first, get_style(GAMES), data)
    assert second.art.world == "rainy cyberpunk harbor" and second.art.source == "user"
    assert "Direção definida pelo usuário (não mude):" in fake.calls[0]["prompt"]
    assert "rainy cyberpunk harbor" in fake.calls[0]["prompt"]


def test_translate_carries_card_texts(monkeypatch):
    doc = gta_doc()
    new_doc, _, _, _ = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    monkeypatch.undo()  # volta para o LLM offline (mock)
    new_doc.languages.append("en-US")
    translated, _ = authoring.translate(new_doc, "en-US")
    stat = translated.ordered_scenes()[3].card
    assert stat.label["en-US"] == "[en-US] cópias vendidas" and stat.value["en-US"] == "[en-US] 200 MI+"
    assert "en-US" not in translated.ordered_scenes()[14].card.value  # "2×" não tem o que traduzir
    compare = translated.ordered_scenes()[11].card.resolved("en-US", "pt-BR")
    assert compare["items"][0] == {"label": "[en-US] Trailer 1", "value": "[en-US] 90 MI"}
    assert translated.ordered_scenes()[0].card.headline["en-US"].startswith("[en-US] MELHOR")


def test_storyboard_schema_is_strict():
    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object" or "properties" in node:
                assert node["additionalProperties"] is False
                assert node["required"] == list(node["properties"])
            types = node.get("type")
            if "enum" in node:  # enum que aceita null pode falhar no --json-schema
                assert types == "string"
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(STORYBOARD_SCHEMA)
    fields = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]["properties"]
    assert {"source", "reuse_key", "value", "items", "headline", "stamp", "source_note", "focus"} <= set(fields)


# --- pipeline offline (mock), revisão gravada nas configurações ----------------------------------


def _run_job(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def test_mock_storyboard_job_records_review(isolated_settings):
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado da ilha")
    _run_job("script", doc.id)
    result = _run_job("storyboard", doc.id)
    new_doc = service.get_doc(doc.id)
    scenes = new_doc.ordered_scenes()
    assert result["provider"] == "mock_llm" and result["scenes"] == len(scenes)
    assert result["new_images"] == sum(1 for s in scenes if storyboard_check.has_own_image(s))
    assert {"version", "reused", "cards"} <= set(result)
    assert new_doc.art is not None and new_doc.art.genre == "curiosidades" and new_doc.art.languages == ["editorial"]
    assert scenes[0].kind == "image" and storyboard_check.has_own_image(scenes[0]) and scenes[0].card.headline
    assert scenes[-1].visual.reuse_of == scenes[0].id  # chamada final reaproveita o gancho
    assert any(s.visual.reuse_of for s in scenes[1:-1])
    assert all(s.span is not None for s in scenes) and not new_doc.storyboard_approved
    row = service.get_project_row(doc.id)
    review = row["settings"]["storyboard_review"]
    assert review["provider"] == "mock_llm" and review["version"] == result["version"]
    assert review["counts"]["scenes"] == len(scenes) and review["retried"] is False
    assert [i for i in review["issues"] if i["severity"] == "grave"] == []
    versions = service.list_versions(doc.id)
    assert versions[0]["message"] == f"Cenas planejadas (mock_llm): {len(scenes)} cenas, {result['new_images']} imagens novas"
    assert row["status"] == "storyboard_review"  # projeto novo: a parada das cenas vem ligada


@pytest.mark.parametrize("ratio,expected", [(0.5, 10), (0.1, 3), (1.0, 19)])
def test_image_budget(ratio, expected):
    style = get_style(GAMES).model_copy(deep=True)
    style.edit.new_image_ratio = ratio
    assert authoring.image_budget(19, style) == expected


def test_v2_top10_count_shows_the_image_of_its_own_item(monkeypatch):
    style = get_style("top10-listas")
    segments = [
        Segment(id="seg_hook", role="hook", text={"pt-BR": "Estes são os lugares mais estranhos do planeta, e o primeiro vai te surpreender."}),
        Segment(id="seg_i10", role="item", chapter="#10", text={"pt-BR": "Número dez. O lago que muda de cor sozinho todos os anos "
                                                                          "no verão. Ninguém sabe explicar por que isso acontece."}),
        Segment(id="seg_i9", role="item", chapter="#9", text={"pt-BR": "Número nove. A floresta onde as árvores crescem tortas "
                                                                        "para o mesmo lado. Os cientistas ainda discutem a causa."}),
    ]
    doc = ProjectDoc(id="prj_top", title="Lugares estranhos", style_id="top10-listas", seed=7, segments=segments)
    beats = authoring.plan_beats(doc, style)
    keys = [b.key for b in beats]
    scenes = [{"key": k, "kind": "image", "source": "new", "prompt": f"A place {i}."} for i, k in enumerate(keys)]
    for entry in scenes:
        if entry["key"].endswith(":0") and not entry["key"].startswith("seg_hook"):
            # o LLM aponta o contador para a imagem do item anterior: não vale
            entry.update({"kind": "count", "source": "reuse", "reuse_key": keys[0]})
    new_doc, _, _, _ = run(monkeypatch, doc, style, {"direction": {}, "scenes": scenes})
    counts = {s.segment_id: s for s in new_doc.ordered_scenes() if s.kind == "count"}
    assert set(counts) == {"seg_i10", "seg_i9"}
    # item com várias batidas: o contador mostra a imagem de uma batida seguinte do mesmo item
    assert len(new_doc.scenes_of("seg_i10")) > 1
    root = new_doc.reuse_root(counts["seg_i10"])
    assert root is not None and root.id != counts["seg_i10"].id and root.segment_id == "seg_i10"
    # item de uma batida só: o contador tem imagem própria
    assert len(new_doc.scenes_of("seg_i9")) == 1 and storyboard_check.has_own_image(counts["seg_i9"])


def test_words_max_ignores_separators_and_dangling_words():
    assert authoring._words_max("TRAILER 2 · MAIO DE 2025", 5) == "TRAILER 2 · MAIO DE 2025"
    assert authoring._words_max("O MAIOR LANÇAMENTO DA HISTÓRIA DOS GAMES", 4) == "O MAIOR LANÇAMENTO"
    assert authoring._words_max("VENDAS DE GTA V EM 2013", 5) == "VENDAS DE GTA V EM 2013"  # o número fica
    assert authoring._words_max("RECORDE ABSOLUTO DE", 2) == "RECORDE ABSOLUTO"


def test_style_catalog_keeps_the_video_format():
    short = authoring.style_catalog(get_style(GAMES))
    assert "misterios-short" in short and "top10-listas" not in short
    assert "top10-listas" in authoring.style_catalog()


def test_translate_screen_only_keeps_the_narration(monkeypatch):
    doc = gta_doc()
    new_doc, _, _, _ = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    monkeypatch.undo()  # volta para o LLM offline (mock)
    new_doc.languages.append("en-US")
    for seg in new_doc.segments:
        seg.text["en-US"] = f"EN {seg.id}"
    assert authoring.needs_translation(new_doc, "en-US")
    pending = authoring.missing_screen_texts(new_doc, "en-US")
    assert pending and all("#" in p["id"] or p["id"].startswith("scn_") for p in pending)
    translated, _ = authoring.translate(new_doc, "en-US", screen_only=True)
    assert all(seg.text["en-US"] == f"EN {seg.id}" for seg in translated.segments)
    assert not authoring.needs_translation(translated, "en-US")
    assert authoring.translate(translated, "en-US", screen_only=True) == (translated, "nenhum")
    # valor com separador muda com o idioma; "2×" não
    stat = next(s for s in translated.scenes if s.card and s.card.value)
    stat.card.value = {"pt-BR": "3,5%"}
    assert any(p["text"] == "3,5%" for p in authoring.missing_screen_texts(translated, "en-US"))


def test_system_prompt_has_no_real_character_example():
    assert '"lucia"' not in authoring.STORYBOARD_SYSTEM.lower()
    assert "nunca pelo nome" in authoring.STORYBOARD_SYSTEM
    assert "no máximo 1 a cada 3 cenas" in authoring.STORYBOARD_SYSTEM


def test_entity_reference_and_name_replacement():
    assert storyboard_check._split_entity_name("Lucia (original heroine)") == ("Lucia", "heroine")
    assert storyboard_check._split_entity_name("Vice City") == ("Vice City", "")
    assert storyboard_check.entity_reference("original Latina woman in her 30s, dark curly hair", "") == "the Latina woman"
    assert storyboard_check.entity_reference("fictional Miami-style seaside city with pastel hotels", "") == (
        "the Miami-style seaside city")
    assert storyboard_check.entity_reference("", "heroine") == "the heroine"
    assert storyboard_check.entity_reference("", "") == "the character"
    text = "A red car. Lucia's jacket on a chair; lucia waits. Lucian art."
    assert storyboard_check._replace_name(text, "Lucia", "the woman") == (
        "A red car. The woman's jacket on a chair; The woman waits. Lucian art.")


def test_entity_name_out_of_the_narration_stays(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    for entity in data["direction"]["entities"]:
        if entity["id"] == "lucia":
            entity["name"] = "Coastal heroine"
    scene_data(data, doc, "4:1")["prompt"] = "Coastal heroine leaning on a convertible at sunset."
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    assert new_doc.ordered_scenes()[13].visual.prompt == "Coastal heroine leaning on a convertible at sunset."
    assert "nome_no_prompt" not in codes(review)


def test_entity_name_leaves_hook_and_world(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    data["direction"]["hook"] = "Lucia on a rooftop over the neon coastal city at sunset"
    data["direction"]["world"] = "Vice City in the 2020s, beaches and art deco hotels"
    new_doc, _, _, _ = run(monkeypatch, doc, get_style(GAMES), data)
    art = new_doc.art
    assert art.hook == "The Latina woman on a rooftop over the neon coastal city at sunset"
    assert art.world == "The sunny neon coastal city in the 2020s, beaches and art deco hotels"


def test_v2_trims_headlines_keeping_hook_and_numbers(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    for scene in data["scenes"]:
        scene["headline"] = scene.get("headline") or "FAIXA EXTRA"
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    with_headline = {i for i, s in enumerate(scenes) if s.card and s.card.headline.get("pt-BR")}
    # gancho, as faixas que são o único número falado da cena (2013, DEZ 2023, MAI 2025), as viradas de assunto
    # e, para completar 7 (1 a cada 3 de 19), a imagem mais longe das outras faixas
    assert with_headline == {0, 2, 4, 8, 12, 15, 18}, with_headline
    assert scenes[0].card.headline == {"pt-BR": "MELHOR JOGO DA HISTÓRIA?"}
    assert scenes[2].card.headline == {"pt-BR": "GTA V · 2013"}
    assert scenes[3].kind == "stat" and scenes[3].card.resolved("pt-BR").get("headline") is None
    trimmed = next(i for i in review["issues"] if i["code"] == "faixas_cortadas")
    assert trimmed["severity"] == "corrigido"
    assert trimmed["message"].startswith("12 faixas no topo tiradas: ficam 7 neste vídeo (limite 7)")
    assert len(fake.calls) == 1


def test_v2_headlines_within_the_limit_stay(monkeypatch):
    doc = gta_doc()
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    assert sum(1 for s in new_doc.scenes if s.card and s.card.headline.get("pt-BR")) == 5
    assert "faixas_cortadas" not in codes(review)


def test_check_motif_ignores_palette_colors(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    for ref in ("0:0", "1:0", "2:0", "2:2", "3:0", "4:0"):
        scene_data(data, doc, ref)["prompt"] += " Pink and teal neon glow on wet asphalt puddles."
    _, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    motifs = [i["message"] for i in review["issues"] if i["code"] == "motivo_repetido"]
    assert not any(f'"{w}"' in m for m in motifs for w in ("pink", "teal", "neon", "glow")), motifs
    assert any('"puddle"' in m or '"asphalt"' in m for m in motifs), motifs


def test_v2_headline_with_a_name_digit_is_not_protected(monkeypatch):
    """ "GTA 6" na faixa não é número falado: a faixa entra no limite como as outras."""
    doc = gta_doc()
    data = load_fixture(doc)
    for scene in data["scenes"]:
        if not any(ch.isdigit() for ch in scene.get("headline") or ""):
            scene["headline"] = "GTA 6 EM FOCO"
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    kept = [s for s in new_doc.ordered_scenes() if s.card and s.card.headline.get("pt-BR")]
    assert len(kept) == 7
    assert "faixas_cortadas" in codes(review)


def _pick(kind: str, part: int, headline: str, segment: str, words: str = "uma frase qualquer"):
    from studio.core.project.document import SceneCard

    beat = storyboard_check.Beat(key=f"{segment}:{part}", segment_id=segment, part=part, parts=2, index=0,
                                 role="item", words=words.split(), seconds=2.0)
    number = 1 if kind == "count" else None
    return authoring._Pick(beat, kind, "new", number=number, card=SceneCard(headline={"pt-BR": headline}))


def test_trim_headlines_keeps_top10_counter_names():
    picks = [_pick("image", 0, "GANCHO", "seg_h")]
    for item in range(5):
        picks += [_pick("count", 0, f"ITEM {item}", f"seg_{item}"), _pick("image", 1, "DETALHE", f"seg_{item}")]
    issues = authoring._trim_headlines(picks, "pt-BR", 0)
    kept = [p.card.headline.get("pt-BR") for p in picks if p.card and p.card.headline.get("pt-BR")]
    # limite 4 (11 cenas): o gancho e 3 contadores (virada de assunto) espalhados; nenhuma faixa de imagem
    assert kept == ["GANCHO", "ITEM 2", "ITEM 3", "ITEM 4"], kept
    assert issues and issues[0].code == "faixas_cortadas"


def test_entity_reference_prefers_who_or_what():
    assert storyboard_check.entity_reference("Dark wavy hair, tan skin, Latina woman", "") == "the Latina woman"
    assert storyboard_check.entity_reference("Late 20s, Latina, dark hair", "heroine") == "the heroine"
    assert storyboard_check.entity_reference("Late 20s, dark hair", "") == "the Late 20s"


def test_replace_name_absorbs_article_and_accents():
    replace = storyboard_check._replace_name
    assert replace("Aerial view of the Vice City skyline. A Vice City street.", "Vice City", "the neon city") == (
        "Aerial view of the neon city skyline. The neon city street.")
    assert replace("Lúcia on a rooftop", "Lucia", "the woman") == "The woman on a rooftop"
    assert replace("a Vice City-style boulevard", "Vice City", "the neon city") == "The neon city-style boulevard"
    assert replace("near a Vice City-style pier", "Vice City", "the neon city") == "near the neon city-style pier"
    assert replace("Theatre near the Lucian gate", "Lucia", "the woman") == "Theatre near the Lucian gate"


def _names_doc(narration: str, entities: list[dict], prompt: str, *, source: str = "ai") -> tuple:
    from studio.core.project.document import ArtDirection, Entity, Scene, Visual

    doc = ProjectDoc(id="prj_n", title="t", topic="t", style_id=GAMES, seed=1,
                     segments=[Segment(id="seg_a", role="hook", text={"pt-BR": narration})])
    doc.art = ArtDirection(world="the world of the story", hook="", entities=[Entity(**e) for e in entities],
                           source=source)
    doc.scenes = [Scene(segment_id="seg_a", part=0, parts=1, kind="image",
                        visual=Visual(prompt=prompt, language="key_art", shot="wide", composition="upper_half"))]
    beat = storyboard_check.Beat(key="seg_a:0", segment_id="seg_a", part=0, parts=1, index=0, role="hook",
                                 words=narration.split(), seconds=3.0)
    return doc, [beat]


def test_real_entities_keep_their_names():
    doc, beats = _names_doc(
        "Em 1912, o capitão Edward Smith comandava o Titanic.",
        [{"id": "smith", "name": "Edward Smith (captain)", "look": "white-bearded sea captain", "real": True},
         {"id": "titanic", "name": "Titanic", "look": "four-funnel ocean liner", "real": True}],
        "Edward Smith on the bridge of the Titanic at night, 1912.")
    assert storyboard_check.fix_entity_names(doc, beats) == []
    assert doc.scenes[0].visual.prompt == "Edward Smith on the bridge of the Titanic at night, 1912."


def test_fictional_names_variants_and_looks_are_scrubbed():
    doc, beats = _names_doc(
        "Com Lúcia, a primeira protagonista, e o detetive da cidade.",
        [{"id": "lucia", "name": "Lucia Caminos - protagonist", "look": "Lucia, Latina woman in her late 20s"},
         {"id": "detetive", "name": "detective", "look": "tall man in a trench coat"}],
        "Lucia Caminos and the detective on a rooftop; Lucia's jacket in the wind.")
    issues = storyboard_check.fix_entity_names(doc, beats)
    prompt = doc.scenes[0].visual.prompt
    assert prompt == "The Latina woman and the detective on a rooftop; The Latina woman's jacket in the wind.", prompt
    assert doc.art.entities[0].look == "Latina woman in her late 20s"
    assert [i.code for i in issues] == ["nome_no_prompt"]


def test_user_direction_is_not_rewritten_only_warned():
    doc, beats = _names_doc(
        "A volta de Vice City.",
        [{"id": "vice", "name": "Vice City", "look": "neon coastal city"}],
        "A quiet street at dawn.", source="user")
    doc.art.world = "Vice City in the 2020s"
    issues = storyboard_check.fix_entity_names(doc, beats)
    assert doc.art.world == "Vice City in the 2020s"
    assert [(i.code, i.severity) for i in issues] == [("nome_na_direcao", "aviso")]


def test_entity_real_flag_is_parsed_and_required_in_schema(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    data["direction"]["entities"][0]["real"] = True
    new_doc, _, _, _ = run(monkeypatch, doc, get_style(GAMES), data)
    assert new_doc.art.entities[0].real is True and new_doc.art.entities[1].real is False
    item = STORYBOARD_SCHEMA["properties"]["direction"]["properties"]["entities"]["items"]
    assert "real" in item["required"] and item["properties"]["real"] == {"type": "boolean"}
