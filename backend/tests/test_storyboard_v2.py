"""Storyboard v2: direção de arte, batidas, cartões, reaproveitamento, orçamento e revisão.

O LLM é falso (monkeypatch de `authoring._llm`); a resposta realista do Short do GTA 6 está em
`fixtures/storyboard_v2_gta.json`, com as chaves "#<índice do segmento>:<batida>".
"""

from __future__ import annotations

import copy
import json
from datetime import date
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
    assert len(scenes) == 21
    for seg in new_doc.segments:  # cada segmento coberto exatamente pelas batidas
        assert sum(s.span or 0 for s in new_doc.scenes_of(seg.id)) == word_count(seg.text_for("pt-BR"))

    art = new_doc.art
    assert art is not None and art.source == "ai"
    assert art.genre == "games" and art.languages == ["key_art", "render_3d"] and art.grade == "vivid"
    assert art.entity("lucia") is not None and art.entity("lucia").look.startswith("original Latina")
    # personagem oficial citado pelo nome: de costas ou em silhueta, sem rosto
    assert art.entity("lucia").look.endswith(storyboard_check.FACELESS)
    assert not art.entity("vice_city").look.endswith(storyboard_check.FACELESS)
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
    # source_ref 1 vira o nome curto da 1ª fonte do roteiro
    assert stat.card.resolved("pt-BR") == {"value": "200 MI+", "label": "cópias vendidas", "source": "Take-Two"}
    # "Depois disso, silêncio." é batida própria, sobre a imagem de 2013 escurecida
    assert scenes[4].kind == "text" and scenes[4].visual.reuse_of == scenes[2].id and scenes[4].span == 3
    lone = scenes[11]  # 475 MI: a resposta pediu o fundo liso; fica a última imagem nova, desfocada
    assert lone.kind == "stat" and lone.visual.reuse_of == scenes[9].id and lone.motion is None
    assert all(s.visual.type != "none" for s in scenes)
    compare = scenes[12].card.resolved("pt-BR")
    assert scenes[12].kind == "compare" and compare["highlight"] == 1 and compare["label"] == "views nas primeiras 24 h"
    assert compare["items"] == [{"label": "Trailer 1 · só YouTube", "value": "90 MI"},
                                {"label": "Trailer 2 · todas as plataformas", "value": "475 MI"}]
    assert scenes[8].kind == "title" and scenes[8].overlay_text == {"pt-BR": "Recorde no YouTube"}
    assert scenes[8].card is None  # o carimbo "RECORDE" só repetia o título da cena: saiu
    # "Agora, o segredo." fica numa imagem; o cartão do 2 começa em "Depois de dois adiamentos"
    assert scenes[15].kind == "image" and storyboard_check.has_own_image(scenes[15]) and scenes[15].span == 3
    assert scenes[16].kind == "stat" and scenes[16].card.resolved("pt-BR") == {
        "value": "2", "label": "adiamentos do lançamento", "stamp": "ADIADO"}
    assert scenes[16].visual.reuse_of == scenes[15].id
    assert scenes[18].kind == "text" and scenes[18].visual.reuse_of == scenes[13].id
    assert scenes[14].visual.entities == ["lucia"]
    # o nome da personagem (tirado da narração) não vai para o gerador: a imagem descreve a aparência
    assert scenes[14].visual.prompt.startswith("The Latina woman seen from behind at a neon-lit diner")
    assert "lucia" not in scenes[14].visual.prompt.lower()

    assert review["counts"] == {"scenes": 21, "new_images": 10, "reused": 11, "cards": 8, "budget": 11, "est_seconds": 350}
    # a imagem de maio de 2025 fica em 3 cenas seguidas (~9 s) e o roteiro (do estilo de mistério) fecha com a chamada
    # do outro estilo: só avisos, sem pedir correção. Sem acervo de material real, as imagens de IA (sem motivo) dão um
    # aviso só de `faltou_real` (ADR 0004), na primeira delas.
    assert [(i["code"], i["severity"], i["scene_id"]) for i in review["issues"]] == [
        ("imagem_seguida", "aviso", scenes[11].id), ("cta_outro_estilo", "aviso", None),
        ("faltou_real", "aviso", scenes[0].id),
        ("nome_no_prompt", "corrigido", scenes[14].id), ("personagem_sem_rosto", "corrigido", None),
        ("carimbo_repetido", "corrigido", scenes[8].id)], review["issues"]
    assert review["retried"] is False
    assert (review["style_fit"], review["style_note"], review["suggested_style"]) == ("ok", art.style_note, None)
    assert new_doc.storyboard_approved is False
    ProjectDoc.model_validate(json.loads(json.dumps(new_doc.model_dump(mode="json"))))

    prompt = fake.calls[0]["prompt"]
    assert "Tratamento de referência do estilo: cores saturadas e neon, contraste limpo, sem grão" in prompt
    assert 'Limite de imagens novas (source "new"): 11 de 21 batidas.' in prompt
    assert "Linguagens preferidas do estilo: key_art, render_3d, ui_mockup, editorial" in prompt
    assert "- games-cultura-pop-short: Games e Cultura Pop (Short)" in prompt
    assert "- key_art: Pôster ilustrado" in prompt and "photo" not in prompt.split("Linguagens visuais")[1].split("\n")[1]
    assert "Legenda: no meio" in prompt
    assert "Fontes do roteiro (source_ref é o número entre colchetes):\n- [1] Take-Two: GTA V vendeu" in prompt
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
    scene_data(data, doc, "5:4")["entities"] = ["lucia"]  # 2ª aparição da Lucia
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    assert len(fake.calls) == 1
    assert review["counts"]["new_images"] == 11 == review["counts"]["budget"]
    assert codes(review, "corrigido").count("orcamento") == 10
    assert all(i["scene_id"] for i in review["issues"] if i["code"] == "orcamento")
    own = [i for i, s in enumerate(scenes) if storyboard_check.has_own_image(s)]
    # gancho e 1ª aparição da Lucia têm prioridade; o resto entra na ordem
    assert own == [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 14]
    assert scenes[19].visual.reuse_of == scenes[14].id  # reaproveita a imagem com a mesma entidade
    assert scenes[10].visual.reuse_of == scenes[9].id and scenes[10].visual.prompt == ""
    assert scenes[10].focus == FOCUS_ROTATION[1] and scenes[11].focus == FOCUS_ROTATION[2]
    assert scenes[10].motion == "zoom_to"
    assert "cena 10" in next(i["message"] for i in review["issues"] if i["scene_id"] == scenes[10].id)
    # "475 MI" na faixa é só o número da batida, não outro evento: o reaproveitamento da imagem de maio vale
    assert "reuso_outra_data" not in codes(review)
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
    assert scenes[7].kind == "image" and storyboard_check.has_own_image(scenes[7])
    assert scenes[8].kind == "stat" and scenes[8].card.resolved("pt-BR")["value"] == "24 H"  # count fora do Top 10
    assert scenes[9].motion is None and scenes[9].visual.composition in ("upper_half", "left_third", "right_third")
    assert scenes[10].visual.reuse_of is None and scenes[10].visual.prompt.startswith("Red sports car")
    assert scenes[11].kind == "stat"
    assert "highlight" not in scenes[12].card.resolved("pt-BR")
    assert scenes[14].visual.entities == ["lucia_maria"] and scenes[14].card is None  # valor só vale em stat
    assert scenes[20].visual.reuse_of == scenes[0].id and scenes[20].focus in FOCUS_ROTATION
    assert scenes[20].focus != "middle" and scenes[20].motion == "zoom_to"
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
    assert review["counts"]["scenes"] == 21
    assert second.reuse_root(second.ordered_scenes()[18]) is not None  # 5:3 apontava para a batida travada


def test_check_flags_number_as_image_and_avoid_terms(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "1:1").update({"kind": "image", "source": "new", "reuse_key": None, "value": None, "label": None,
                                         "source_ref": None, "prompt": "Stack of boxed games on a store shelf."})
    scene_data(data, doc, "5:2")["prompt"] = "Closed studio gate at night with candles burning by the barrier."
    scene_data(data, doc, "4:0")["prompt"] += " Oil painting style."
    scene_data(data, doc, "5:4")["prompt"] = "Crowd watching a gameplay screenshot on a giant screen."
    new_doc, provider, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    keys = {i["code"]: i["scene_key"] for i in review["issues"] if i["severity"] == "grave"}
    seg = doc.segments
    assert keys == {
        "numero_sem_cartao": f"{seg[1].id}:1",
        "motivo_proibido": f"{seg[5].id}:2",
        "estilo_no_prompt": f"{seg[4].id}:0",
        "marca": f"{seg[5].id}:4",
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
    scene = new_doc.ordered_scenes()[6]
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
    scene_data(bad, doc, "1:1").update({"kind": "image", "source": "new", "value": None, "label": None, "source_ref": None,
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
    assert review["counts"]["new_images"] == 11


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
    for ref in ("4:0", "4:1", "5:0"):  # cartões seguidos e demais
        scene_data(data, doc, ref).update({"kind": "text", "source": "reuse", "reuse_key": data["scenes"][0]["key"]})
    data["direction"]["avoid"] = []
    _, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    warnings = {i["code"]: i for i in review["issues"] if i["severity"] == "aviso"}
    assert set(warnings) == {"motivo_repetido", "cartoes_seguidos", "cartoes_demais", "imagem_seguida",
                             "cta_outro_estilo", "faltou_real"}
    # os cartões seguidos ficam todos sobre a imagem do gancho
    same = [i["message"] for i in review["issues"] if i["code"] == "imagem_seguida"]
    assert any(m.startswith("A mesma imagem fica em 4 cenas seguidas (cenas 14 a 17") for m in same), same
    motifs = [i for i in review["issues"] if i["code"] == "motivo_repetido"]
    assert any('"hourglass" aparece em 5 de' in i["message"] for i in motifs)
    assert all(i["scene_id"] is None for i in motifs) and len(motifs) <= 3
    assert warnings["cartoes_demais"]["message"].startswith("11 de 21 cenas são cartões")
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
    assert "en-US" not in translated.ordered_scenes()[16].card.value  # "2" não tem o que traduzir
    compare = translated.ordered_scenes()[12].card.resolved("en-US", "pt-BR")
    assert compare["items"][0] == {"label": "[en-US] Trailer 1 · só YouTube", "value": "[en-US] 90 MI"}
    assert compare["label"] == "[en-US] views nas primeiras 24 h"
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
    assert {"source", "reuse_key", "value", "items", "headline", "stamp", "source_ref", "focus"} <= set(fields)
    # a fonte é o número de uma fonte do roteiro, nunca texto livre
    assert "source_note" not in fields and fields["source_ref"] == {"type": ["integer", "null"]}


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
                                                                        "para o mesmo lado e ninguém sabe a causa."}),
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
    assert new_doc.ordered_scenes()[14].visual.prompt == "Coastal heroine leaning on a convertible at sunset."
    assert "nome_no_prompt" not in codes(review) and "personagem_sem_rosto" not in codes(review)


def test_entity_name_leaves_hook_and_world(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    data["direction"]["hook"] = "Lucia on a rooftop over the neon coastal city at sunset"
    data["direction"]["world"] = "Vice City in the 2020s, beaches and art deco hotels"
    new_doc, _, _, _ = run(monkeypatch, doc, get_style(GAMES), data)
    art = new_doc.art
    assert art.hook == "The Latina woman on a rooftop over the neon coastal city at sunset"
    assert art.world == "The neon coastal city in the 2020s, beaches and art deco hotels"


def test_v2_trims_headlines_keeping_hook_and_numbers(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    for scene in data["scenes"]:
        scene["headline"] = scene.get("headline") or "FAIXA EXTRA"
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    with_headline = {i for i, s in enumerate(scenes) if s.card and s.card.headline.get("pt-BR")}
    # gancho, as faixas que são o único número falado da cena (2013, DEZ 2023, MAI 2025) e, para completar 7 (1 a
    # cada 3 de 21), as viradas de assunto (1ª batida de cada trecho)
    assert with_headline == {0, 2, 5, 9, 13, 15, 20}, with_headline
    assert scenes[0].card.headline == {"pt-BR": "MELHOR JOGO DA HISTÓRIA?"}
    assert scenes[2].card.headline == {"pt-BR": "GTA V · 2013"}
    assert scenes[3].kind == "stat" and scenes[3].card.resolved("pt-BR").get("headline") is None
    trimmed = next(i for i in review["issues"] if i["code"] == "faixas_cortadas")
    assert trimmed["severity"] == "corrigido"
    assert trimmed["message"].startswith("14 faixas no topo tiradas: ficam 7 neste vídeo (limite 7)")
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
    # o nome sai da aparência e a personagem citada pelo nome fica sem rosto; o detetive (sem nome falado) fica igual
    assert doc.art.entities[0].look == f"Latina woman in her late 20s, {storyboard_check.FACELESS}"
    assert doc.art.entities[1].look == "tall man in a trench coat"
    assert [i.code for i in issues] == ["nome_no_prompt", "personagem_sem_rosto"]


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


# --- crítica do Short do GTA 6: luz, reaproveitamento, cartões lisos, carimbos, fontes e faixas -------------------


def _new_scenes(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in data["scenes"] if s["source"] == "new" and s["prompt"]]


def test_system_prompt_asks_for_varied_light_and_honest_cards():
    system = " ".join(authoring.STORYBOARD_SYSTEM.split())
    assert "SEM hora do dia, clima, céu, luz ou objetos" in system  # tratamento
    assert "o look de lugar não fixa céu, hora nem luz" in system
    assert "no máximo 1 em cada 3 no mesmo momento" in system and "Tela dentro da imagem" in system
    assert "nunca reaproveita imagem em tela cheia" in system
    assert "não vira entidade com rosto" in system and "de costas, em silhueta" in system
    assert '"INÉDITO" nem "EXCLUSIVO"' in system and "source_ref" in system and "source_note" not in system
    assert "mesma plataforma, mesma janela" in system and "o título do cartão" in system
    assert "fundo animado" not in system and '"2×"' not in system


def test_check_flags_the_same_time_of_day_and_retries(monkeypatch):
    doc = gta_doc()
    good = load_fixture(doc)
    bad = copy.deepcopy(good)
    for scene in _new_scenes(bad):
        scene["prompt"] = scene["prompt"].rstrip(".") + ", all bathed in sunset light."
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), bad, good)
    assert len(fake.calls) == 2 and review["retried"] is True and codes(review, "grave") == []
    retry = fake.calls[1]["prompt"]
    assert "- [luz_repetida] (10 de 10 imagens novas se passam no mesmo momento (pôr do sol)" in retry
    assert "no máximo 1 em cada 3 no mesmo momento" in retry
    # a resposta da correção (luz variada) ganhou
    assert "sunset light" not in new_doc.ordered_scenes()[2].visual.prompt


def test_check_lighting_counts_the_direction_when_the_scene_has_no_light(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    # direção antiga: o tratamento fixa a hora e os prompts não dizem luz nenhuma. O tratamento só entra na imagem que
    # cita entidade (ADR 0004): todas citam a cidade.
    data["direction"]["treatment"] = "saturated golden-hour light, clean contrast"
    for index, scene in enumerate(_new_scenes(data)):
        scene["prompt"] = f"Quiet subject number {index} on a coastal avenue, 2020s."
        scene["entities"] = ["vice_city"]
    _, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    grave = [i for i in review["issues"] if i["code"] == "luz_repetida"]
    assert grave and grave[0]["severity"] == "grave" and len(fake.calls) == 2
    # direção definida pelo usuário: o Claude não pode mudar, fica o aviso (sem pedir correção)
    first, _, _, _ = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    first.art.source = "user"
    first.art.treatment = "saturated golden-hour light, clean contrast"
    _, _, review, fake = run(monkeypatch, first, get_style(GAMES), load_fixture(first))
    warning = next(i for i in review["issues"] if i["code"] == "luz_repetida")
    assert warning["severity"] == "aviso" and "direção definida por você" in warning["message"]
    assert len(fake.calls) == 1


def test_lighting_helpers():
    from studio.workers.pipeline import lighting

    assert lighting.moments("A boardwalk at sunset, then night falls") == {"por_do_sol", "noite"}
    assert lighting.moments("late afternoon by the lake") == {"por_do_sol"}
    assert lighting.moments("early morning fog") == {"amanhecer"} and lighting.moments("under the midday sun") == {"dia"}
    assert lighting.moments("no sunset, a sunset-orange car, midnight blue walls") == set()
    assert lighting.names_light("a boy lit by the cold glow of a TV") and lighting.names_light("candlelit hall")
    assert not lighting.names_light("a light blue jacket on a chair") and not lighting.names_light("a split second")
    strip = lighting.strip_light
    assert strip("saturated sunset light, clean contrast, no grain", light=True) == "clean contrast, no grain"
    assert strip("Saturated sunset and neon glow, clean contrast, golden-hour light", light=True) == "clean contrast"
    assert strip("seaside city with art deco hotels under a pink-orange sunset sky") == "seaside city with art deco hotels"
    assert strip("sunny neon coastal metropolis, beaches at sunset, 2020s") == "neon coastal metropolis, beaches, 2020s"
    assert strip("sunset orange") == "orange" and strip("hot pink") == "hot pink"
    # sem hora nem luz: o texto volta igual (mesmo prompt, mesma chave de cache)
    assert strip("art deco hotels; palm trees") == "art deco hotels; palm trees"


def test_reuse_for_another_date_asks_for_a_new_image(monkeypatch):
    doc = gta_doc()
    good = load_fixture(doc)
    bad = copy.deepcopy(good)
    # "Em maio de 2025" reaproveitando a multidão de "dezembro de 2023" em tela cheia
    scene_data(bad, doc, "3:0").update({"source": "reuse", "reuse_key": scene_data(bad, doc, "2:0")["key"],
                                        "prompt": None, "language": None, "shot": None, "composition": None,
                                        "focus": "bottom_left"})
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), bad, good)
    assert len(fake.calls) == 2 and review["retried"] is True and codes(review, "grave") == []
    retry = fake.calls[1]["prompt"]
    assert ('(A batida "Em maio de dois mil e…" reaproveita a imagem de "Até dezembro de dois mil e…": é outra '
            "data ou outro evento, use imagem nova.)") in retry
    assert storyboard_check.has_own_image(new_doc.ordered_scenes()[9])
    # sem vaga para imagem nova: só aviso, sem pedir correção
    style = get_style(GAMES).model_copy(deep=True)
    style.edit.new_image_ratio = 0.42  # 9 de 21, as 9 imagens novas da resposta
    _, _, review, fake = run(monkeypatch, doc, style, bad)
    issue = next(i for i in review["issues"] if i["code"] == "reuso_outra_data")
    assert issue["severity"] == "aviso" and "não sobra vaga" in issue["message"] and len(fake.calls) == 1
    # a mesma faixa ou nenhuma data diferente: reaproveitar vale (o 3:1 volta à imagem de maio)
    assert "reuso_outra_data" not in codes(run(monkeypatch, doc, style, good)[2])
    topic = storyboard_check._other_topic
    assert topic("TRAILER 2 · MAI 2025", "TRAILER 1 · DEZ 2023") and topic("RECORDE", "GTA V · 2013")
    assert not topic("475 MI", "TRAILER 2 · MAI 2025")  # só o número da batida
    assert not topic("TRAILER 2 · MAIO DE 2025", "TRAILER 2 · MAI 2025") and not topic("", "GTA V · 2013")


def _cards_doc() -> tuple[ProjectDoc, list]:
    from studio.core.project.document import Scene, SceneCard, Visual

    narration = ["O jogo saiu em dois mil e treze.", "Vendeu duzentos milhões de cópias.", "Número dez. O lago."]
    doc = ProjectDoc(id="prj_c", title="t", topic="t", style_id=GAMES, seed=1,
                     segments=[Segment(id=f"seg_{i}", role="hook" if i == 0 else "body", text={"pt-BR": text})
                               for i, text in enumerate(narration)])
    doc.scenes = [
        Scene(id="scn_img", segment_id="seg_0", kind="image",
              visual=Visual(prompt="An old console on a desk at night.", language="key_art", shot="wide")),
        Scene(id="scn_stat", segment_id="seg_1", kind="stat", visual=Visual(type="none"), motion="static",
              card=SceneCard(value={"pt-BR": "200 MI"})),
        Scene(id="scn_count", segment_id="seg_2", kind="count", number=10, visual=Visual(type="none")),
    ]
    beats = [storyboard_check.Beat(key=f"seg_{i}:0", segment_id=f"seg_{i}", part=0, parts=1, index=i,
                                   role="body", words=text.split(), seconds=2.0) for i, text in enumerate(narration)]
    return doc, beats


def test_flat_card_gets_the_previous_image_blurred():
    doc, beats = _cards_doc()
    issues = storyboard_check.fix_flat_cards(doc, beats)
    stat, count = doc.scene("scn_stat"), doc.scene("scn_count")
    assert stat.visual.reuse_of == "scn_img" and stat.visual.type == "ai_image" and stat.focus in FOCUS_ROTATION
    assert stat.motion is None and not storyboard_check.has_own_image(stat)
    assert count.visual.type == "none"  # o contador do Top 10 não mostra a imagem do item anterior
    assert [(i.code, i.severity, i.scene_id) for i in issues] == [("cartao_sem_fundo", "corrigido", "scn_stat")]
    assert "imagem da cena 1" in issues[0].message
    # cartão antes de qualquer imagem fica com o fundo animado
    doc, beats = _cards_doc()
    doc.scenes = [s for s in doc.scenes if s.id != "scn_img"]
    assert storyboard_check.fix_flat_cards(doc, beats[1:]) == []


def test_inedito_stamp_is_dropped(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "4:1")["stamp"] = "INÉDITO"
    scene_data(data, doc, "3:2")["stamp"] = "EXCLUSIVO"
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    assert scenes[14].card is None and "stamp" not in scenes[11].card.resolved("pt-BR")
    dropped = [i for i in review["issues"] if i["code"] == "carimbo_tirado"]
    assert [(i["severity"], i["scene_id"]) for i in dropped] == [("corrigido", scenes[11].id),
                                                               ("corrigido", scenes[14].id)]
    assert 'O carimbo "INÉDITO" saiu' in dropped[1]["message"] and len(fake.calls) == 1


def test_source_ref_maps_to_a_script_source_and_must_back_the_number(monkeypatch):
    doc = gta_doc()
    doc.sources += [Source(title="Guinness World Records — recorde do trailer", note="trailer de game mais visto no YouTube"),
                    Source(title="Rockstar Newswire: Trailer 2", note="475 milhões de visualizações em 24 horas")]
    data = load_fixture(doc)
    scene_data(data, doc, "1:1")["source_ref"] = 2  # 200 MI+ só está na fonte 1 (Take-Two)
    scene_data(data, doc, "2:1")["source_ref"] = 2  # 90 MI não está em fonte nenhuma, mas está na narração
    scene_data(data, doc, "3:2")["source_ref"] = 9  # número fora da lista
    scene_data(data, doc, "3:3")["source_ref"] = 3
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    assert scenes[3].card.source == "Take-Two"
    assert scenes[6].card.source == "Guinness World Records"  # nome curto, até o travessão
    assert scenes[11].card.source is None
    assert scenes[12].card.source == "Rockstar Newswire"
    swapped = next(i for i in review["issues"] if i["code"] == "fonte_trocada")
    assert swapped["scene_id"] == scenes[3].id and swapped["severity"] == "corrigido"
    assert '"200 MI+" está na fonte "Take-Two", não em "Guinness World Records"' in swapped["message"]
    assert "- [2] Guinness World Records — recorde do trailer: trailer de game" in fake.calls[0]["prompt"]
    assert len(fake.calls) == 1


def test_check_sources_drops_a_source_that_is_not_in_the_script():
    from studio.core.project.document import Scene, SceneCard, Visual

    doc = gta_doc()
    doc.scenes = [Scene(id="scn_a", segment_id=doc.segments[1].id, kind="stat", visual=Visual(type="none"),
                        card=SceneCard(value={"pt-BR": "200 MI+"}, source="Fonte inventada"))]
    beat = storyboard_check.Beat(key=f"{doc.segments[1].id}:0", segment_id=doc.segments[1].id, part=0, parts=1,
                                 index=1, role="body", words=GTA[1].split(), seconds=3.0)
    issues = storyboard_check._check_sources(doc, [beat])
    # o 200 MI+ está numa fonte só (Take-Two): o cartão passa a citar essa
    assert doc.scenes[0].card.source == "Take-Two" and [i.code for i in issues] == ["fonte_trocada"]
    doc.sources = []
    doc.scenes[0].card.source = "Fonte inventada"
    issues = storyboard_check._check_sources(doc, [beat])
    assert doc.scenes[0].card.source is None and [i.code for i in issues] == ["fonte_tirada"]
    assert storyboard_check.source_label(Source(title="Um título bem comprido de fonte que passa do limite de 40")) == (
        "Um título bem comprido de fonte que")


def test_check_sources_with_two_sources_of_the_same_name():
    """Dois posts do mesmo site viram o mesmo nome curto: o cartão que cita esse nome vale se um deles traz o número
    (antes saía "está na fonte "Rockstar Newswire", não em "Rockstar Newswire"")."""
    from studio.core.project.document import Scene, SceneCard, Visual

    doc = gta_doc()
    doc.sources = [Source(title="Rockstar Newswire — Trailer 1", note="primeiro trailer em dezembro de 2023"),
                   Source(title="Take-Two — resultados", note="200 milhões de cópias"),
                   Source(title="Rockstar Newswire — Trailer 2", note="475 milhões de visualizações em 24 horas")]
    doc.scenes = [Scene(id="scn_a", segment_id=doc.segments[1].id, kind="stat", visual=Visual(type="none"),
                        card=SceneCard(value={"pt-BR": "475 MI"}, source="Rockstar Newswire"))]
    beat = storyboard_check.Beat(key=f"{doc.segments[1].id}:0", segment_id=doc.segments[1].id, part=0, parts=1,
                                 index=1, role="body", words=GTA[1].split(), seconds=3.0)
    assert storyboard_check._check_sources(doc, [beat]) == []
    assert doc.scenes[0].card.source == "Rockstar Newswire"
    # O número só está em outras duas fontes com o mesmo nome: o cartão passa a citar esse nome.
    doc.sources = [Source(title="Guinness World Records — recorde", note="recorde de 2023"),
                   Source(title="Rockstar Newswire — Trailer 2", note="475 milhões de visualizações"),
                   Source(title="Rockstar Newswire — balanço", note="475 milhões de visualizações em um dia")]
    doc.scenes[0].card.source = "Guinness World Records"
    issues = storyboard_check._check_sources(doc, [beat])
    assert doc.scenes[0].card.source == "Rockstar Newswire" and [i.code for i in issues] == ["fonte_trocada"]


def test_stamp_that_repeats_the_scene_title_is_dropped():
    from studio.core.project.document import Scene, SceneCard, Visual

    doc = gta_doc()
    seg = doc.segments[2].id
    doc.scenes = [
        Scene(id="scn_a", segment_id=seg, kind="title", visual=Visual(type="none"),
              overlay_text={"pt-BR": "RECORDE NO YOUTUBE"}, card=SceneCard(stamp={"pt-BR": "RECORDE"})),
        Scene(id="scn_b", segment_id=seg, part=1, parts=3, kind="stat", visual=Visual(type="none"),
              card=SceneCard(value={"pt-BR": "2"}, label={"pt-BR": "adiamentos do lançamento"},
                             stamp={"pt-BR": "ADIADO"})),
        Scene(id="scn_c", segment_id=seg, part=2, parts=3, kind="image", visual=Visual(type="none"),
              card=SceneCard(headline={"pt-BR": "TRAILER 1 · DEZ 2023"}, stamp={"pt-BR": "2013"})),
    ]
    beats = [storyboard_check.Beat(key=f"{seg}:{i}", segment_id=seg, part=i, parts=3, index=2, role="body",
                                   words=GTA[2].split(), seconds=3.0) for i in range(3)]
    issues = storyboard_check.fix_echo_stamps(doc, beats)
    assert [(i.code, i.scene_id) for i in issues] == [("carimbo_repetido", "scn_a")]
    assert issues[0].message == 'O carimbo "RECORDE" só repetia "RECORDE NO YOUTUBE" na mesma cena: saiu.'
    assert doc.scenes[0].card is None
    assert doc.scenes[1].card.stamp == {"pt-BR": "ADIADO"} and doc.scenes[2].card.stamp == {"pt-BR": "2013"}


def test_headline_that_only_repeats_the_beat_is_dropped(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "0:0")["headline"] = "MELHOR JOGO DOS TEMPOS?"  # gancho: fica
    scene_data(data, doc, "4:0")["headline"] = "LEONIDA · VICE CITY"  # só repete a fala: sai
    scene_data(data, doc, "2:0")["headline"] = "DEZEMBRO DE 2023"  # repete, mas é o único lugar do número: fica
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scenes = new_doc.ordered_scenes()
    assert scenes[0].card.headline == {"pt-BR": "MELHOR JOGO DOS TEMPOS?"}
    assert scenes[5].card.headline == {"pt-BR": "DEZEMBRO DE 2023"}
    assert scenes[13].card is None
    issue = next(i for i in review["issues"] if i["code"] == "faixa_repetida")
    assert issue["severity"] == "corrigido" and issue["scene_id"] == scenes[13].id
    assert issue["message"] == 'A faixa "LEONIDA · VICE CITY" só repetia a fala da batida: saiu.'
    assert codes(review, "grave") == [] and len(fake.calls) == 1
    repeats = storyboard_check.repeats_speech
    words = "Até dezembro de dois mil e vinte e três.".split()
    assert repeats("DEZ 2023", words, "pt-BR") and not repeats("TRAILER 1 · DEZ 2023", words, "pt-BR")
    assert repeats("200 MI+", "Vendeu mais de duzentos milhões de cópias.".split(), "pt-BR")
    assert not repeats("2 BI", "Vendeu mais de duzentos milhões de cópias.".split(), "pt-BR")


def test_compare_of_different_units_is_a_warning(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    scene_data(data, doc, "3:3")["items"] = [{"label": "Trailer 1", "value": "24%"}, {"label": "Trailer 2", "value": "475 MI"}]
    _, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    issue = next(i for i in review["issues"] if i["code"] == "comparacao_desigual")
    assert issue["severity"] == "aviso" and "(%, número)" in issue["message"] and len(fake.calls) == 1


def test_motifs_count_world_words_and_es_plurals(monkeypatch):
    assert [storyboard_check._singular(w) for w in ("beaches", "glasses", "palms", "cities", "houses", "status")] == [
        "beach", "glass", "palm", "city", "house", "status"]
    doc = gta_doc()
    data = load_fixture(doc)
    data["direction"]["world"] += ", wooden boardwalks"
    for ref in ("0:0", "1:0", "2:0", "2:2", "3:0"):
        scene_data(data, doc, ref)["prompt"] += " Wooden boardwalks in the background."
    _, _, review, _ = run(monkeypatch, doc, get_style(GAMES), data)
    motifs = [i["message"] for i in review["issues"] if i["code"] == "motivo_repetido"]
    # palavra do mundo repetida em quase toda imagem conta (antes ficava de fora); cor continua de fora
    assert any('"boardwalk" aparece em 6 de 10' in m for m in motifs), motifs


# --- crítica do vídeo do GTA 6 (15/11/2026): mundo, fonte solta, mesma imagem seguida, chamada final e datas ----------


def test_system_prompt_keeps_the_world_to_the_setting_and_spreads_reuse():
    system = " ".join(authoring.STORYBOARD_SYSTEM.split())
    # o app junta o world a toda imagem nova: só lugar e época, curto; o resto vira entidade ou vai no prompt da cena
    assert "world: só o cenário, lugar e época, em até 20 palavras" in system
    assert "nada de pessoas, objetos ou ações nele" in system and "vira entidade (só entra nas imagens que a citam)" in system
    assert "pessoas genéricas do assunto" not in system
    assert "no máximo 3 batidas seguidas na mesma imagem" in system


def test_card_left_with_only_a_source_is_dropped(monkeypatch):
    doc = gta_doc()
    data = load_fixture(doc)
    # a faixa só repete a fala e sai; a fonte, sem nada que ela apoie, ficaria sozinha na tela ("Fonte: Take-Two")
    scene_data(data, doc, "4:0").update({"headline": "LEONIDA · VICE CITY", "source_ref": 1})
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)
    scene = new_doc.ordered_scenes()[13]
    assert scene.card is None
    lone = next(i for i in review["issues"] if i["code"] == "fonte_solta")
    assert (lone["severity"], lone["scene_id"]) == ("corrigido", scene.id)
    assert lone["message"] == ('A fonte "Take-Two" ficava sozinha na cena, sem número, faixa nem texto que ela '
                               "apoiasse: saiu.")
    assert "faixa_repetida" in codes(review, "corrigido") and len(fake.calls) == 1
    assert new_doc.ordered_scenes()[3].card.source == "Take-Two"  # o cartão com número fica com a fonte


def test_lone_source_check_keeps_text_cards_and_drops_empty_ones_quietly():
    from studio.core.project.document import MediaRef, Scene, SceneCard, Visual

    doc = gta_doc()
    seg = doc.segments[1].id
    doc.scenes = [
        Scene(id="scn_img", segment_id=seg, part=0, parts=5, kind="image", visual=Visual(prompt="A desk."),
              card=SceneCard(source="Take-Two")),
        Scene(id="scn_text", segment_id=seg, part=1, parts=5, kind="text", visual=Visual(reuse_of="scn_img"),
              card=SceneCard(source="Take-Two")),
        Scene(id="scn_empty", segment_id=seg, part=2, parts=5, kind="image", visual=Visual(prompt="A shelf."),
              card=SceneCard(highlight=1)),
        Scene(id="scn_news", segment_id=seg, part=3, parts=5, kind="image", visual=Visual(
            prompt="A desk.", media=MediaRef(kind="news", outlet="IGN Brasil")), card=SceneCard(source="IGN Brasil")),
        Scene(id="scn_locked", segment_id=seg, part=4, parts=5, kind="image", locked=True,
              visual=Visual(prompt="A car."), card=SceneCard(source="Take-Two")),
    ]
    beats = [storyboard_check.Beat(key=f"{seg}:{i}", segment_id=seg, part=i, parts=5, index=1, role="body",
                                   words=GTA[1].split(), seconds=2.0) for i in range(5)]
    issues = storyboard_check.fix_lone_sources(doc, beats)
    assert [(i.code, i.scene_id) for i in issues] == [("fonte_solta", "scn_img")]  # o cartão vazio sai sem aviso
    assert doc.scene("scn_img").card is None and doc.scene("scn_empty").card is None
    # a fala em destaque, o cartão de notícia e a cena travada ficam com a fonte
    assert [doc.scene(s).card.source for s in ("scn_text", "scn_news", "scn_locked")] == ["Take-Two", "IGN Brasil",
                                                                                         "Take-Two"]


def _same_image_doc(seconds: list[float], *, locked: bool = False) -> tuple[ProjectDoc, list]:
    """Cena 1 com imagem nova, as seguintes reaproveitando (em cadeia, com um cartão no meio) e a última com outra."""
    from studio.core.project.document import Scene, SceneCard, Visual

    n = len(seconds)
    doc = ProjectDoc(id="prj_s", title="t", topic="t", style_id=GAMES, seed=1,
                     segments=[Segment(id="seg_a", role="body", text={"pt-BR": " ".join(["palavra"] * (2 * n))})])
    scenes = [Scene(id="scn_0", segment_id="seg_a", part=0, parts=n, span=2, visual=Visual(prompt="A desk."))]
    for k in range(1, n - 1):
        kind = "stat" if k == 2 else "image"
        scenes.append(Scene(id=f"scn_{k}", segment_id="seg_a", part=k, parts=n, span=2, kind=kind,
                            visual=Visual(reuse_of=f"scn_{k - 1}"),
                            card=SceneCard(value={"pt-BR": "2"}) if kind == "stat" else None))
    scenes.append(Scene(id=f"scn_{n - 1}", segment_id="seg_a", part=n - 1, parts=n, span=2,
                        visual=Visual(prompt="A car.")))
    for scene in scenes:
        scene.locked = locked
    doc.scenes = scenes
    beats = [] if locked else [
        storyboard_check.Beat(key=f"seg_a:{k}", segment_id="seg_a", part=k, parts=n, index=0, role="body",
                              words=["palavra", "palavra"], seconds=s) for k, s in enumerate(seconds)]
    return doc, beats


def test_same_image_on_too_many_scenes_in_a_row_is_a_warning():
    style = get_style(GAMES)
    # 4 cenas seguidas na mesma imagem (a raiz da cadeia de reaproveitamento, contando o cartão sobre ela)
    doc, beats = _same_image_doc([2.0, 2.0, 2.0, 2.0, 2.0])
    issues = storyboard_check._check_same_image(doc, style, beats)
    assert [(i.code, i.severity, i.scene_id) for i in issues] == [("imagem_seguida", "aviso", "scn_3")]
    assert issues[0].message == ("A mesma imagem fica em 4 cenas seguidas (cenas 1 a 4, ~8,0 s; o limite é 3): alterne "
                                 "com imagem nova ou mídia real.")
    # 3 cenas, mas mais de 8 s na mesma imagem: a 3ª passa do limite
    doc, beats = _same_image_doc([3.5, 3.0, 2.5, 2.0])
    issues = storyboard_check._check_same_image(doc, style, beats)
    assert [(i.scene_id, i.scene_key) for i in issues] == [("scn_2", "seg_a:2")]
    assert issues[0].message == ("A mesma imagem fica ~9,0 s na tela, em 3 cenas seguidas (cenas 1 a 3; o limite é 8 s): "
                                 "alterne com imagem nova ou mídia real.")
    # até 3 cenas e 8 s: vale (a imagem nova seguinte não conta)
    doc, beats = _same_image_doc([3.0, 2.5, 2.5, 4.0])
    assert storyboard_check._check_same_image(doc, style, beats) == []
    # só cenas travadas: ficam como o usuário deixou
    doc, beats = _same_image_doc([2.0] * 6, locked=True)
    assert storyboard_check._check_same_image(doc, style, beats) == []
    # vídeo longo (batidas de 4 a 7 s): uma imagem e o cartão sobre ela não passam do limite; 4 cenas passam
    long_style = get_style("documentario-sombrio")
    assert storyboard_check.same_image_seconds(long_style) == 21.0
    doc, beats = _same_image_doc([5.5, 5.5, 6.0, 4.0])
    assert storyboard_check._check_same_image(doc, long_style, beats) == []
    doc, beats = _same_image_doc([5.0, 5.0, 5.0, 5.0, 4.0])
    assert [i.code for i in storyboard_check._check_same_image(doc, long_style, beats)] == ["imagem_seguida"]


def test_cta_from_another_style_is_a_warning():
    doc = gta_doc()  # roteiro escrito no estilo de mistério, projeto agora no de games
    games = get_style(GAMES)
    issues = storyboard_check._check_cta(doc, games)
    assert [(i.code, i.severity) for i in issues] == [("cta_outro_estilo", "aviso")]
    assert issues[0].message == (
        'A chamada final "Siga para descobrir o próximo mistério." é do estilo Mistérios Sombrios: Short. A do estilo '
        'atual, Games e Cultura Pop (Short), é "Siga para mais novidades de games.": ajuste o fim do roteiro.')
    assert storyboard_check._check_cta(doc, get_style("misterios-short")) == []
    doc.segments[-1].text["pt-BR"] = "E aí, vai jogar? Siga para mais novidades de games!"
    assert storyboard_check._check_cta(doc, games) == []
    # estilo do usuário sem chamada fixa: o aviso diz só de onde veio a chamada
    doc = gta_doc()
    mine = games.model_copy(deep=True)
    mine.id, mine.name, mine.script.cta = "meu-estilo", "Meu estilo", None
    message = storyboard_check._check_cta(doc, mine)[0].message
    assert message.endswith("Mistérios Sombrios: Short. O estilo atual, Meu estilo, não tem essa chamada: ajuste o fim "
                            "do roteiro.")


def test_script_prompt_has_today_and_the_date_rule(monkeypatch):
    answer = {"title": "GTA 6", "sources": [], "segments": [
        {"role": "hook", "speaker": "narrator", "chapter": None, "number": None, "text": "GTA 6 chega quinta."}]}
    fake = FakeLLM(answer)
    monkeypatch.setattr(authoring, "_llm", fake)
    new_doc, _ = authoring.generate_script(gta_doc(), get_style(GAMES), today=date(2026, 11, 15))
    assert new_doc.segments[0].text == {"pt-BR": "GTA 6 chega quinta."}
    call = fake.calls[0]
    assert call["task"] == "script" and call["prompt"].startswith("Data de hoje: 2026-11-15\n")
    system = " ".join(call["system"].split())
    assert "Se o tema tem data marcada (lançamento, estreia, evento) perto de hoje, diga a data e quanto falta" in system
    assert "Evite afirmação que vence em poucos dias" in system
    # sem a data injetada, vale a de hoje
    authoring.generate_script(gta_doc(), get_style(GAMES))
    assert fake.calls[1]["prompt"].startswith(f"Data de hoje: {date.today().isoformat()}\n")
