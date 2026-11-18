"""Storyboard real primeiro (ADR 0004 §3.5): com o acervo de material real, o gancho é real, a IA fica abaixo do teto em
tempo de tela e só entra com motivo, o cartão usa o real de fundo e o `pool_id` inválido segue a reserva; sem acervo, a
IA entra e a revisão acusa `faltou_real`. O acervo é um fixture próprio (sem rede); o LLM é falso ou o mock offline."""

from __future__ import annotations

import copy
import shutil
from typing import Any

import pytest
from PIL import Image

from studio.config import get_settings
from studio.core import sources as tiers
from studio.core.project import service
from studio.core.project.document import (
    ArtDirection,
    BeatMark,
    Entity,
    MediaRef,
    ProjectDoc,
    Scene,
    Segment,
    Source,
    Visual,
)
from studio.core.project.pool import Pool, PoolItem, save_pool
from studio.core.styles.library import get_style
from studio.core.styles.schema import sources_for
from studio.core.styles.visual import NON_PHOTO_LANGUAGES, VISUAL_LANGUAGES
from studio.providers.base import LLMResult, Usage
from studio.workers.pipeline import authoring, build, storyboard_check
from studio.workers.pipeline.build import Builder, compose_directed_prompt, image_key
from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA
from studio.workers.pipeline.storyboard_check import Beat

STYLE = get_style("misterios-short")
PID = "prj_rf"
TEXTS = [
    ("hook", "O trailer mostrou Vice City inteira de cima, com a praia e os prédios."),
    ("body", "A Rockstar publicou a notícia no site oficial em maio. Foram 90 milhões de views em 24 horas."),
    ("body", "Lucia aparece dirigindo pela ponte da cidade. E se o mapa for o maior da série inteira?"),
    ("body", "Muita gente assistiu o trailer no celular, várias vezes, procurando detalhes escondidos."),
    ("cta", "Siga para mais notícias de games."),
]


def pool_items() -> list[PoolItem]:
    base = f"uploads/{PID}/pool"
    return [
        PoolItem(id="p1", tier="official", media="clip", desc="Trailer: Vice City vista de cima, praia e prédios",
                 tags=["trailer", "vice city"], path=f"{base}/p1.mp4", poster=f"{base}/p1.jpg", dur_s=3.2,
                 url="https://www.youtube.com/watch?v=abc", credit="Rockstar Games", editorial=True, score=0.9),
        PoolItem(id="p2", tier="capture", media="capture", desc="Página da Rockstar Newswire com a notícia do trailer",
                 tags=["rockstar", "notícia"], path=f"{base}/p2.png", rects=[[0.1, 0.2, 0.5, 0.1]],
                 page_url="https://www.rockstargames.com/newswire", score=0.7),
        PoolItem(id="p3", tier="official", media="clip", desc="Lucia dirigindo na ponte", entities=["lucia"],
                 tags=["ponte"], path=f"{base}/p3.mp4", poster=f"{base}/p3.jpg", dur_s=2.5, score=0.8),
        PoolItem(id="p4", tier="free", media="image", desc="Praia de Miami com prédios, foto livre",
                 tags=["praia", "cidade"], path=f"{base}/p4.jpg", url="https://commons.wikimedia.org/x.jpg",
                 license="CC BY 4.0", credit="Fulano / Wikimedia Commons", score=0.5),
        PoolItem(id="p5", tier="stock", media="image", desc="Pessoas assistindo vídeo no celular",
                 tags=["celular", "assistindo"], path=f"{base}/p5.jpg", score=0.4),
    ]


def make_pool() -> Pool:
    return Pool(items=pool_items(), gathered_at="2026-11-17T00:00:00Z")


def base_doc() -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", role=role, text={"pt-BR": text}) for i, (role, text) in enumerate(TEXTS)]
    return ProjectDoc(id=PID, title="GTA 6", topic="trailer de GTA 6", style_id="misterios-short", seed=7,
                      segments=segments, sources=[Source(title="Newswire", url="https://www.rockstargames.com/n")])


LIGHTS = ["at dawn", "at noon", "at dusk", "at night", "in the rain", "under clouds", "in fog", "at sunrise"]


def response(beats: list[Beat], extra: dict[int, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Resposta "preguiçosa": toda batida pede imagem nova de IA, sem motivo (o pior caso para o teto)."""
    props = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]["properties"]
    scenes = []
    for i, beat in enumerate(beats):
        entry: dict[str, Any] = dict.fromkeys(props)
        entry.update({"key": beat.key, "kind": "image", "source": "new", "language": "painting",
                      "prompt": f"{beat.text} {LIGHTS[i % len(LIGHTS)]}.", "role": "breath", "serious": False})
        entry.update((extra or {}).get(i, {}))
        scenes.append(entry)
    direction = {"genre": "games", "approach": "literal", "world": "a seaside city", "treatment": "soft",
                 "palette": [], "languages": ["painting"], "grade": None, "entities": [], "avoid": [],
                 "style_fit": "ok", "style_note": "", "suggested_style": None, "hook": "the city from above"}
    return {"direction": direction, "scenes": scenes}


class FakeLLM:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.calls: list[dict[str, Any]] = []

    def __call__(self, task, system, prompt, schema, context, project_id, job_id):
        self.calls.append({"system": system, "prompt": prompt, "context": context})
        return LLMResult(data=copy.deepcopy(self.data), text="", usage=Usage()), "fake_llm"


def run(monkeypatch, doc: ProjectDoc, extra: dict[int, dict[str, Any]] | None = None, pool: Pool | None = None,
        style=STYLE):
    beats = authoring.plan_beats(doc, style)
    fake = FakeLLM(response(beats, extra))
    monkeypatch.setattr(authoring, "_llm", fake)
    new_doc, _, review = authoring.storyboard(doc, style, styles={}, pool=pool)
    return new_doc, review, beats, fake


def by_key(doc: ProjectDoc, beats: list[Beat]) -> list[Scene]:
    return [doc.scenes_of(b.segment_id)[b.part] for b in beats]


def ai_share(doc: ProjectDoc, style=STYLE) -> float:
    timed = storyboard_check.screen_seconds(doc, style)
    total = sum(s for *_x, s in timed)
    return sum(s for _scene, prov, s in timed if prov == "ai") / total


def codes(review: dict[str, Any]) -> list[str]:
    return [i["code"] for i in review["issues"]]


def attach_all(doc: ProjectDoc, pool: Pool) -> ProjectDoc:
    """O que `media_pool.attach_chosen` (acervo-real) faz, sem rede: copia o arquivo do item para o projeto e preenche
    `asset`/`video`, com `start_s` 0 (o proxy já é o trecho)."""
    out = doc.model_copy(deep=True)
    for scene in out.scenes:
        media = scene.visual.media
        if not build.media_pending(media):
            continue
        item = pool.item(media.pool_id)
        is_clip = item.media == "clip"
        update = {"asset": item.poster if is_clip else item.path, "video": item.path if is_clip else None,
                  "start_s": 0.0}
        scene.visual.media = media.model_copy(update=update)
    return out


# --- com acervo ------------------------------------------------------------------------------------


def test_hook_is_real_and_ai_stays_under_the_time_cap(monkeypatch):
    new_doc, review, beats, fake = run(monkeypatch, base_doc(), pool=make_pool())
    scenes = by_key(new_doc, beats)
    hook = scenes[0]
    policy = sources_for(STYLE)
    assert hook.kind == "image" and hook.visual.media is not None
    assert tiers.prov_of_scene(hook, new_doc) in policy.hook
    assert hook.visual.media.pool_id == "p1" and hook.motion == "punch_zoom"  # o trailer da cidade vista de cima
    assert "gancho_real" in codes(review)
    assert ai_share(new_doc) <= policy.ai_max_share + 1e-9
    assert review["counts"]["ai_pct"] <= round(policy.ai_max_share * 100)
    # antes do anexo, a mídia do acervo está pendente: não conta como real
    assert review["counts"]["real_pct"] == 0 and review["counts"]["media_pending"] >= 3
    attached = attach_all(new_doc, make_pool())
    after = authoring.review_attached(attached, STYLE, review, make_pool())
    assert after["counts"]["real_pct"] >= 50 and "media_pending" not in after["counts"]
    assert after["counts"]["ai_pct"] == review["counts"]["ai_pct"]
    assert review["counts"]["new_images"] <= review["counts"]["budget"]
    assert not [i for i in review["issues"] if i["code"] in ("ai_over_budget", "hook_not_real")]
    # nenhuma imagem de IA reaproveitada para encher: o que passou do teto virou real ou cartão sobre o real
    for scene in new_doc.scenes:
        if scene.visual.reuse_of:
            assert tiers.prov_of_scene(scene, new_doc) != "ai"
    # o pedido levou o acervo (no formato de `media_pool.prompt_block`), a política e a gramática; o contexto do mock
    # também
    prompt = fake.calls[0]["prompt"]
    assert "Acervo de material real" in prompt and "p3 · official · clip · Lucia dirigindo na ponte · 2.5s" in prompt
    assert "Teto de imagem de IA: 30% do tempo de tela" in prompt
    assert "Gramática de edição deste estilo" in prompt and "planos de ~1,6 s" in prompt
    assert [i["id"] for i in fake.calls[0]["context"]["pool"]] == ["p1", "p2", "p3", "p4", "p5"]


def test_ai_without_reason_uses_the_item_of_the_same_topic(monkeypatch):
    new_doc, review, beats, _ = run(monkeypatch, base_doc(), pool=make_pool())
    scenes = by_key(new_doc, beats)
    lucia = next(s for b, s in zip(beats, scenes, strict=True) if b.text.startswith("Lucia"))
    media = lucia.visual.media
    assert media is not None and media.pool_id == "p3" and media.tier == "official" and media.kind == "clip"
    # pendente de anexo (ADR 0004 §3.4): sem arquivo e sem ponto de entrada, quem preenche é `attach_chosen`
    assert media.asset is None and media.video is None and media.start_s == 0.0 and media.dur_s == 2.5
    assert build.media_pending(media)
    # a reserva de IA fica (o prompt que a cena pedia), em linguagem que não imita foto
    assert lucia.visual.prompt and not VISUAL_LANGUAGES[lucia.visual.language].photoreal
    found = [i["message"] for i in review["issues"] if i["code"] == "ia_sem_motivo" and i["scene_id"] == lucia.id]
    assert found and '"Lucia dirigindo na ponte" (material oficial)' in found[0]
    assert "p3" not in found[0] and "(official)" not in found[0]


def test_ai_with_reason_stays_and_varies_its_language(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    what_if = next(i for i, b in enumerate(beats) if "mapa" in b.text)
    extra = {what_if: {"ai_reason": "hipótese sobre o tamanho do mapa", "prompt": "A giant folded paper map.",
                       "language": "photo"}}
    new_doc, review, beats, _ = run(monkeypatch, doc, extra, pool=make_pool())
    scene = by_key(new_doc, beats)[what_if]
    assert scene.visual.media is None and storyboard_check.has_own_image(scene)
    assert scene.visual.ai_reason == "hipótese sobre o tamanho do mapa"
    assert not VISUAL_LANGUAGES[scene.visual.language].photoreal  # a IA não imita foto
    ai_languages = [s.visual.language for s in new_doc.ordered_scenes() if storyboard_check.has_own_image(s)]
    assert all(a != b for a, b in zip(ai_languages, ai_languages[1:], strict=False))  # varia de uma imagem de IA para a outra
    assert ai_share(new_doc) <= sources_for(STYLE).ai_max_share + 1e-9
    faltou = [i for i in review["issues"] if i["code"] == "faltou_real"]
    assert all(i["scene_id"] != scene.id for i in faltou)  # com motivo, não falta real


def test_no_card_over_ai_when_there_is_real(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    stat = next(i for i, b in enumerate(beats) if "90" in b.text or "milhões" in b.text)
    plain = next(i for i, b in enumerate(beats) if b.text.startswith("Siga"))
    extra = {
        1: {"ai_reason": "conceito"},  # uma IA com motivo vira raiz possível de fundo
        stat: {"kind": "stat", "source": "reuse", "reuse_key": beats[1].key, "value": "90 MI", "label": "views em 24 h"},
        plain: {"kind": "title", "source": "none", "overlay_text": "Siga o canal"},
    }
    new_doc, review, beats, _ = run(monkeypatch, doc, extra, pool=make_pool())
    scenes = by_key(new_doc, beats)
    for scene in new_doc.scenes:
        if scene.kind in ("stat", "compare", "text", "quote") or (scene.kind == "title" and scene.visual.type != "none"):
            assert tiers.prov_of_scene(scene, new_doc) != "ai", scene
    card = scenes[stat]
    assert card.kind == "stat" and new_doc.shown_media(card) is not None
    # o LLM pediu fundo liso: fica liso (sem virar slide sobre a IA nem receber fundo à força)
    assert scenes[plain].kind == "title" and scenes[plain].visual.type == "none"
    assert "cartao_sem_fundo" not in codes(review)


def test_invalid_pool_id_follows_the_fallback_in_the_same_topic(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    news = next(i for i, b in enumerate(beats) if b.text.startswith("A Rockstar"))
    extra = {news: {"source": "pool", "pool_id": "p99", "ai_reason": None}}
    new_doc, review, beats, _ = run(monkeypatch, doc, extra, pool=make_pool())
    scene = by_key(new_doc, beats)[news]
    # a reserva vai pela ordem de `sources.FALLBACK` (own, capture, official...) no mesmo tópico: a captura da notícia
    assert scene.visual.media is not None and scene.visual.media.pool_id == "p2"
    assert scene.visual.media.kind == "capture" and scene.visual.media.rects == [[0.1, 0.2, 0.5, 0.1]]
    fallback = [i for i in review["issues"] if i["code"] == "acervo_reserva"]
    assert fallback and fallback[0]["scene_id"] == scene.id
    assert "não está no acervo" in fallback[0]["message"] and "Rockstar Newswire" in fallback[0]["message"]
    # válido: fica o escolhido
    extra = {news: {"source": "pool", "pool_id": "p4"}}
    new_doc, _, beats, _ = run(monkeypatch, doc, extra, pool=make_pool())
    assert by_key(new_doc, beats)[news].visual.media.pool_id == "p4"


def test_pool_without_real_items_keeps_the_old_storyboard(monkeypatch):
    only_ai = Pool(items=[PoolItem(id="p1", tier="ai", desc="imagem gerada antes")])
    with_pool, review, _, _ = run(monkeypatch, base_doc(), pool=only_ai)
    without, _, _, _ = run(monkeypatch, base_doc(), pool=Pool())
    assert "ai_pct" not in review["counts"]
    assert [s.visual.media for s in with_pool.ordered_scenes()] == [s.visual.media for s in without.ordered_scenes()]
    assert authoring.image_budget(9, STYLE, only_ai) == authoring.image_budget(9, STYLE) == 5


def test_image_budget_follows_the_ai_cap_without_the_minimum():
    pool = make_pool()
    assert authoring.image_budget(4, STYLE) == 3  # sem acervo: o mínimo de 3 de sempre
    assert authoring.image_budget(4, STYLE, pool) == 2  # 30% de 4 batidas, sem o mínimo
    long = get_style("historia-documental")
    assert sources_for(long).ai_max_share == pytest.approx(0.20)
    assert authoring.image_budget(20, long, pool) == 4


def test_storyboard_reads_the_project_pool(monkeypatch):
    save_pool(PID, make_pool())
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    fake = FakeLLM(response(beats))
    monkeypatch.setattr(authoring, "_llm", fake)
    new_doc, _, review = authoring.storyboard(doc, STYLE, project_id=PID, styles={})
    assert "pool" in fake.calls[0]["context"] and "ai_pct" in review["counts"]
    assert by_key(new_doc, beats)[0].visual.media.pool_id == "p1"


def official_share(doc: ProjectDoc, style=STYLE) -> float:
    timed = storyboard_check.screen_seconds(doc, style)
    return sum(s for _scene, prov, s in timed if prov == "official") / sum(s for *_x, s in timed)


def test_official_cap_is_enforced_like_the_ai_cap(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    # tudo no trailer: o teto de material oficial (35%) é corrigido das últimas batidas para as primeiras
    extra = {i: {"source": "pool", "pool_id": "p1"} for i in range(len(beats))}
    new_doc, review, beats, _ = run(monkeypatch, doc, extra, pool=make_pool())
    assert official_share(new_doc) <= sources_for(STYLE).official_max_share + 1e-9
    assert "official_over_share" not in codes(review) and "teto_oficial" in codes(review)
    assert by_key(new_doc, beats)[0].visual.media.pool_id == "p1"  # o gancho fica
    # cartão com fundo oficial também conta: o fundo vira outro real ou fica liso, nunca IA
    for scene in new_doc.scenes:
        if scene.kind in ("stat", "compare", "text", "quote", "title"):
            assert tiers.prov_of_scene(scene, new_doc) != "ai"


def test_review_flags_same_tier_run_with_readable_labels(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    # tudo na mesma foto livre: o mesmo tipo de material muito tempo seguido (a foto livre não tem teto próprio)
    extra = {i: {"source": "pool", "pool_id": "p4"} for i in range(len(beats))}
    _, review, _, _ = run(monkeypatch, doc, extra, pool=make_pool())
    found = {i["code"]: i for i in review["issues"]}
    assert found["same_tier_run"]["severity"] == "aviso"
    message = found["same_tier_run"]["message"]
    assert "(foto livre)" in message and "(free)" not in message


def test_marks_come_from_the_answer_and_only_keep_spoken_words(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    news = next(i for i, b in enumerate(beats) if "milhões" in b.text)
    lucia = next(i for i, b in enumerate(beats) if b.text.startswith("Lucia"))
    extra = {
        news: {"role": "number", "emphasis": ["90 milhões", "inventada", "24"], "serious": False},
        lucia: {"role": "joke", "emphasis": ["ponte"], "gag_payoff": "freeze", "gag_line": "ela de novo",
                "gag_at": "ponte"},
        0: {"role": "reveal", "serious": True, "gag_payoff": "meme", "gag_line": "kkk", "gag_at": "Vice"},
    }
    # o preset de mistério não tem humor (`edit.grammar.humor: none`): para ler a piada, um estilo com humor leve
    funny = STYLE.model_copy(deep=True)
    funny.edit.grammar.humor = "light"
    new_doc, _, beats, _ = run(monkeypatch, doc, extra, pool=make_pool(), style=funny)
    scenes = by_key(new_doc, beats)
    assert scenes[news].mark.role == "number" and scenes[news].mark.emphasis == ["90 milhões", "24"]
    gag = scenes[lucia].mark.gag
    assert scenes[lucia].mark.role == "joke" and gag.payoff == "freeze" and gag.at == "ponte" and gag.line == "ela de novo"
    hook = scenes[0].mark
    assert hook.role == "hook" and hook.serious and hook.gag is None and hook.energy <= 1  # séria: sem piada
    cta = scenes[-1].mark
    assert cta.role == "cta"
    # o mesmo pedido no preset sem humor: a batida continua marcada, mas sem piada
    assert STYLE.edit.grammar.humor == "none"
    plain_doc, _, plain_beats, _ = run(monkeypatch, base_doc(), extra, pool=make_pool())
    assert by_key(plain_doc, plain_beats)[lucia].mark.gag is None


def test_media_from_pool_is_pending_with_tier_and_credit():
    items = {i.id: i for i in pool_items()}
    clip = authoring.media_from_pool(items["p1"])
    assert (clip.kind, clip.source, clip.tier, clip.pool_id) == ("clip", "youtube", "official", "p1")
    assert clip.dur_s == 3.2 and clip.url == "https://www.youtube.com/watch?v=abc"
    assert clip.editorial and clip.credit == "Rockstar Games"
    photo = authoring.media_from_pool(items["p4"])
    assert (photo.kind, photo.source, photo.tier) == ("photo", "commons", "free")
    assert photo.license == "CC BY 4.0"
    capture = authoring.media_from_pool(items["p2"])
    assert capture.kind == "capture" and capture.rects == [[0.1, 0.2, 0.5, 0.1]]
    assert capture.page_url == "https://www.rockstargames.com/newswire"
    # mesmo já local, o item fica pendente: o arquivo do acervo é do cache, `attach_chosen` copia para o projeto
    for media in (clip, photo, capture):
        assert media.asset is None and media.video is None and media.start_s == 0.0 and build.media_pending(media)


def test_offline_mock_with_pool_cites_pool_ids_and_one_ai_with_reason():
    doc, _ = authoring.generate_script(base_doc(), STYLE)
    new_doc, provider, review = authoring.storyboard(doc, STYLE, styles={}, pool=make_pool())
    assert provider == "mock_llm"
    scenes = new_doc.ordered_scenes()
    assert tiers.prov_of_scene(scenes[0], new_doc) in sources_for(STYLE).hook
    cited = [s for s in scenes if s.visual.media is not None and s.visual.media.pool_id]
    assert len(cited) >= 2
    ai = [s for s in scenes if storyboard_check.has_own_image(s)]
    assert any(s.visual.ai_reason for s in ai)
    assert ai_share(new_doc) <= sources_for(STYLE).ai_max_share + 1e-9
    assert all(s.mark is not None and s.mark.emphasis for s in scenes if s.mark is not None and s.mark.role != "cta")
    assert not [i for i in review["issues"] if i["severity"] == "grave"]


def test_offline_long_video_with_pool_keeps_ai_under_20_percent():
    long = get_style("historia-documental")
    doc, _ = authoring.generate_script(base_doc().model_copy(update={"style_id": long.id}), long)
    new_doc, _, review = authoring.storyboard(doc, long, styles={}, pool=make_pool())
    assert ai_share(new_doc, long) <= 0.20 + 1e-9
    assert tiers.prov_of_scene(new_doc.ordered_scenes()[0], new_doc) in sources_for(long).hook
    assert "ai_over_budget" not in codes(review) and "hook_not_real" not in codes(review)


# --- sem acervo ------------------------------------------------------------------------------------


def test_without_pool_ai_enters_and_faltou_real_is_emitted(monkeypatch):
    new_doc, review, beats, fake = run(monkeypatch, base_doc())
    hook = by_key(new_doc, beats)[0]
    assert storyboard_check.has_own_image(hook)  # o gancho é IA, como antes
    faltou = [i for i in review["issues"] if i["code"] == "faltou_real"]
    assert len(faltou) == 1 and faltou[0]["severity"] == "aviso"
    # aponta para o que a web já tem (anexar na cena, Fontes do roteiro, "Por que IA"), não para uma tela de Acervo
    message = faltou[0]["message"]
    assert "anexe mídia real nas cenas" in message and "Por que IA" in message and "no Acervo" not in message
    assert "ai_pct" not in review["counts"] and "Acervo de material real (" not in fake.calls[0]["prompt"]
    assert "pool" not in fake.calls[0]["context"]


def test_old_answer_without_marks_gives_no_mark(monkeypatch):
    props = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]["properties"]
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    data = response(beats)
    for scene in data["scenes"]:
        for key in ("role", "serious", "emphasis", "gag_payoff", "gag_line", "gag_at"):
            scene.pop(key, None)
        assert set(scene) < set(props)
    monkeypatch.setattr(authoring, "_llm", FakeLLM(data))
    new_doc, _, _ = authoring.storyboard(doc, STYLE, styles={})
    assert all(s.mark is None for s in new_doc.scenes)


# --- prompt da IA e clipe real no build -------------------------------------------------------------


def test_world_and_treatment_only_enter_with_an_entity():
    art = ArtDirection(world="neon coastal city", treatment="grainy contrast", palette=["pink"], languages=["painting"],
                       entities=[Entity(id="lucia", name="Lucia", look="original woman, 30s")])
    bare = Visual(prompt="a folded paper map", language="ink_sketch", shot="wide")
    with_entity = bare.model_copy(update={"entities": ["lucia"]})
    plain = compose_directed_prompt(STYLE, art, bare, lean=True)
    assert "World:" not in plain and "Accent colors" not in plain and "Treatment" not in plain
    assert plain.startswith("Wide establishing shot. loose ink sketch")
    full = compose_directed_prompt(STYLE, art, with_entity, lean=True)
    assert "World: neon coastal city" in full and "Treatment: grainy contrast" in full
    user = compose_directed_prompt(STYLE, art.model_copy(update={"source": "user"}), bare, lean=True)
    assert "World: neon coastal city" in user  # direção do usuário fica como ele escreveu
    # documento antigo (sem storyboard novo): o prompt de antes, com o mundo em toda imagem
    assert "World: neon coastal city" in compose_directed_prompt(STYLE, art, bare)
    assert set(NON_PHOTO_LANGUAGES) <= set(VISUAL_LANGUAGES)
    assert not any(VISUAL_LANGUAGES[x].photoreal for x in NON_PHOTO_LANGUAGES)


def _builder(scenes: list[Scene], art: ArtDirection | None = None) -> Builder:
    doc = service.create_project(title="Real", style_id="misterios-short")
    doc = doc.model_copy(update={"segments": [Segment(id="seg_1", text={"pt-BR": "Um texto curto aqui."})],
                                 "scenes": scenes, "art": art})
    service.commit(doc.id, doc, "teste")
    return Builder(doc.id, mode="preview")


def test_image_keys_still_find_the_image_made_with_the_old_prompt():
    art = ArtDirection(world="neon coastal city", treatment="grainy contrast", languages=["painting"])
    visual = Visual(prompt="a lighthouse on a cliff", language="painting", shot="wide", seed=3)
    # storyboard novo (a cena tem marca de edição): o prompt enxuto, achando a imagem feita com o prompt de antes
    builder = _builder([Scene(id="scn_1", segment_id="seg_1", visual=visual, mark=BeatMark(role="hook"))], art)
    scene = builder.doc.scenes[0]
    inputs = builder.image_inputs(scene)
    assert builder.lean and "World:" not in inputs["prompt"]
    old = {**inputs, "prompt": compose_directed_prompt(builder.style, art, visual)}
    assert old["prompt"] != inputs["prompt"]
    assert image_key(old) in builder.image_keys(inputs, scene)


def test_old_document_keeps_the_world_in_every_ai_prompt():
    art = ArtDirection(world="neon coastal city", treatment="grainy contrast", languages=["painting"])
    visual = Visual(prompt="a lighthouse on a cliff", language="painting", shot="wide", seed=3)
    # projeto antigo (sem marca, motivo de IA nem item do acervo) e imagem ainda não gerada: o prompt de sempre, para
    # "Outra imagem" e as variações saírem iguais ao resto do vídeo (só storyboard novo ou "Reeditar" muda o prompt)
    builder = _builder([Scene(id="scn_1", segment_id="seg_1", visual=visual)], art)
    inputs = builder.image_inputs(builder.doc.scenes[0])
    assert not builder.lean and "World: neon coastal city" in inputs["prompt"]
    assert inputs["prompt"] == compose_directed_prompt(builder.style, art, visual)
    varied = builder.doc.scenes[0].model_copy(update={"visual": visual.model_copy(update={"variation": 2})})
    assert "World: neon coastal city" in builder.image_inputs(varied)["prompt"]


def _lavfi(rel: str, seconds: float) -> None:
    from studio.media.ffmpeg import run_ffmpeg

    path = get_settings().data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc=size=320x180:rate=15:duration={seconds}", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", str(path)], timeout=120)


@pytest.mark.skipif(shutil.which(get_settings().ffmpeg) is None, reason="sem ffmpeg")
def test_real_clip_gets_a_poster_and_goes_to_the_timeline_without_edit_marks():
    from compat_fixture import _clips as narration

    from studio.core.timeline import build_timeline

    data = get_settings().data_dir
    rel = f"uploads/{PID}/clips/clip.mp4"
    _lavfi(rel, 2)
    clip = MediaRef(kind="clip", source="youtube", video=rel, dur_s=2.0, tier="official", pool_id="p1")
    photo_rel = f"uploads/{PID}/media/photo.jpg"
    (data / photo_rel).parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), (200, 30, 30)).save(data / photo_rel)
    photo = MediaRef(kind="photo", source="commons", asset=photo_rel, tier="free", pool_id="p4")
    late = clip.model_copy(update={"start_s": 1.0})
    builder = _builder([Scene(id="scn_c", segment_id="seg_1", part=0, visual=Visual(media=clip)),
                        Scene(id="scn_p", segment_id="seg_1", part=1, visual=Visual(media=photo)),
                        Scene(id="scn_l", segment_id="seg_1", part=2, visual=Visual(media=late))])
    poster = builder.image(builder.doc.scene("scn_c"))
    assert poster and (data / poster).is_file()
    assert builder.image(builder.doc.scene("scn_c")) == poster  # do cache
    clips = builder.real_clips()
    assert clips == {"scn_c": {"path": rel, "seconds": 2.0, "prov": "official"}}
    # o trecho que não começa no início do arquivo fica no quadro de entrada, com aviso
    assert any("começa no meio do arquivo" in w for w in builder.warnings)
    assert builder.image(builder.doc.scene("scn_l"))
    # sem marca de edição nenhuma (sem a camada de edição), a cena de imagem leva o vídeo e o `prov`: o renderer toca
    timeline = build_timeline(builder.doc, builder.style, "pt-BR", narration(builder.doc),
                              {"scn_c": poster, "scn_p": photo_rel, "scn_l": poster}, clips=clips)
    scenes = {s["id"]: s for s in timeline["scenes"]}
    assert scenes["scn_c"]["kind"] == "image" and scenes["scn_c"]["video"] == rel
    assert scenes["scn_c"]["prov"] == "official" and scenes["scn_c"]["video_seconds"] == 2.0
    assert "prov" not in scenes["scn_p"] and "video" not in scenes["scn_l"]


@pytest.mark.skipif(shutil.which(get_settings().ffmpeg) is None, reason="sem ffmpeg")
def test_clip_cut_from_the_middle_of_the_source_plays_from_the_start_of_its_proxy():
    """O formato do acervo-real: `PoolItem.start_s` é o ponto no vídeo de ORIGEM e o `path` é o proxy já cortado
    (começa em 0). A cena pendente não herda esse ponto; anexada, toca o proxy do início, com a duração medida."""
    from studio.media.ffmpeg import run_ffmpeg

    data = get_settings().data_dir
    source = f"library/clips/src/{PID}/video.mp4"
    _lavfi(source, 4)
    proxy = f"library/clips/{PID}/shot2.mp4"
    (data / proxy).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", "2.0", "-i", str(data / source), "-t", "1.5", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(data / proxy)], timeout=120)
    item = PoolItem(id="p7", tier="official", media="clip", desc="Trailer: a ponte", path=proxy, start_s=2.0,
                    url="https://www.youtube.com/watch?v=abc", editorial=True)
    pending = authoring.media_from_pool(item)
    assert pending.start_s == 0.0 and pending.video is None and pending.dur_s is None
    # o anexo copia o proxy para o projeto e grava o deslocamento DENTRO dele (0)
    rel = f"uploads/{PID}/clips/shot2.mp4"
    (data / rel).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(data / proxy, data / rel)
    attached = pending.model_copy(update={"video": rel})
    builder = _builder([Scene(id="scn_c", segment_id="seg_1", visual=Visual(media=attached))])
    poster = builder.image(builder.doc.scene("scn_c"))
    assert poster and (data / poster).is_file()  # o quadro 0 do proxy existe (buscar em 2,0 s passaria do fim)
    clips = builder.real_clips()
    assert clips["scn_c"]["path"] == rel and clips["scn_c"]["prov"] == "official"
    assert clips["scn_c"]["seconds"] == pytest.approx(1.5, abs=0.15)  # sem `dur_s`: a duração medida do proxy
    assert not [w for w in builder.warnings if "clipe" in w]


def test_pending_pool_media_falls_back_to_the_reserve_with_a_warning():
    art = ArtDirection(languages=["painting"])
    pending = authoring.media_from_pool(pool_items()[0])
    with_reserve = Visual(prompt="a city seen from above", language="ink_sketch", shot="wide", seed=3, media=pending)
    builder = _builder([Scene(id="scn_r", segment_id="seg_1", part=0, visual=with_reserve),
                        Scene(id="scn_n", segment_id="seg_1", part=1, kind="stat", visual=Visual(media=pending))], art)
    assert builder.pending_media == ["scn_r", "scn_n"] and builder.lean
    assert any("A mídia do acervo da Cena 1 ainda não foi baixada; a cena usa a imagem de IA de reserva" in w
               for w in builder.warnings)
    assert any("Cena 2 ainda não foi baixada; a cena fica com o fundo animado" in w for w in builder.warnings)
    # sem mídia na cena do build: nem crédito nem clipe de um item que não está na tela; a reserva entra
    assert builder.doc.scene("scn_r").visual.media is None and builder.image_inputs(builder.doc.scene("scn_r"))
    estimate = build.plan(builder.project_id, mode="preview")
    assert estimate["media_pending"] == ["scn_r", "scn_n"] and "scn_r" in estimate["images_missing"]
    assert "media_scenes" not in estimate
    # a revisão depois do anexo: a cena pendente não conta como real e ganha um aviso
    doc = service.get_doc(builder.project_id)
    counts = storyboard_check.review_counts(doc, 3, style=STYLE, pool=make_pool())
    assert counts["media_pending"] == 2 and counts["real_pct"] == 0
    review = authoring.review_attached(doc, STYLE, {"counts": {"budget": 3}, "issues": []}, make_pool())
    pending_issues = [i for i in review["issues"] if i["code"] == "midia_pendente"]
    assert [i["scene_id"] for i in pending_issues] == ["scn_r", "scn_n"]
    assert pending_issues[0]["severity"] == "aviso" and "imagem de IA de reserva" in pending_issues[0]["message"]


def test_redoing_the_storyboard_keeps_the_new_pool_choice_and_only_hand_attached_media(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    news = next(i for i, b in enumerate(beats) if b.text.startswith("A Rockstar"))
    phone = next(i for i, b in enumerate(beats) if b.text.startswith("Muita gente"))
    first, _, beats, _ = run(monkeypatch, doc, {news: {"source": "pool", "pool_id": "p4"}}, pool=make_pool())
    assert by_key(first, beats)[news].visual.media.pool_id == "p4"
    # o usuário anexa à mão uma foto numa cena (sem `pool_id`)
    hand = MediaRef(kind="photo", source="upload", asset=f"uploads/{PID}/media/0123456789abcdef.jpg")
    by_key(first, beats)[phone].visual.media = hand
    second, review, beats, _ = run(monkeypatch, first, {news: {"source": "pool", "pool_id": "p2"}}, pool=make_pool())
    scenes = by_key(second, beats)
    assert scenes[news].visual.media.pool_id == "p2"  # vale a escolha nova, não a do storyboard anterior
    assert scenes[phone].visual.media == hand  # a anexada à mão fica
    kept = [i for i in review["issues"] if i["code"] == "midia_mantida"]
    assert len(kept) == 1 and kept[0]["message"].startswith("1 cena manteve")


def test_llm_choice_of_an_ai_graphic_or_meme_item_follows_the_fallback(monkeypatch):
    pool = Pool(items=[*pool_items(),
                       PoolItem(id="p8", tier="ai", desc="Ilustração de IA da cidade", tags=["cidade"]),
                       PoolItem(id="p9", tier="meme", media="meme", desc="Meme do cara chorando", tags=["celular"]),
                       PoolItem(id="p10", tier="graphic", desc="Gráfico de views", tags=["views"])])
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    picks = {1: "p8", 2: "p9", 3: "p10", 4: "p8", 5: "p8"}
    extra = {i: {"source": "pool", "pool_id": pid} for i, pid in picks.items() if i < len(beats)}
    new_doc, review, beats, fake = run(monkeypatch, doc, extra, pool=pool)
    scenes = by_key(new_doc, beats)
    for i in extra:
        media = scenes[i].visual.media
        assert media is None or media.pool_id not in ("p8", "p9", "p10"), (i, media)
    assert ai_share(new_doc) <= sources_for(STYLE).ai_max_share + 1e-9
    assert "ai_over_budget" not in codes(review)
    refused = [i for i in review["issues"] if i["code"] == "acervo_reserva"]
    assert len(refused) == len(extra) and all("não serve de imagem da batida" in i["message"] for i in refused)
    assert any('"Meme do cara chorando" (meme)' in i["message"] for i in refused)
    assert "não cite em pool_id" in fake.calls[0]["prompt"]


def test_pool_without_real_items_is_not_in_the_request(monkeypatch):
    only_ai = Pool(items=[PoolItem(id="p1", tier="ai", desc="imagem gerada antes"),
                          PoolItem(id="p2", tier="meme", media="meme", desc="meme")])
    _, _, _, fake = run(monkeypatch, base_doc(), pool=only_ai)
    assert "Acervo de material real" not in fake.calls[0]["prompt"] and "pool" not in fake.calls[0]["context"]


def test_without_pool_the_ai_language_follows_the_policy(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    extra = {1: {"language": "ink_sketch"}, 2: {"language": "photo"}, 3: {"language": "line_art"}}
    new_doc, _, beats, _ = run(monkeypatch, doc, extra)
    scenes = by_key(new_doc, beats)
    assert scenes[1].visual.language == "ink_sketch"  # não fotográfica, pedida pela regra 8: fica
    assert scenes[2].visual.language == "painting"  # foto com `ai_photo` desligado: a dominante da direção
    assert scenes[3].visual.language == "painting"  # fora da direção e das não fotográficas: a dominante
    assert scenes[0].visual.language == "painting"


def test_set_ai_reason_op_edits_the_reason_without_touching_the_image():
    from studio.core.project.ops import apply_ops, parse_ops

    doc = base_doc().model_copy(update={"scenes": [
        Scene(id="scn_a", segment_id="seg_0", visual=Visual(prompt="a map", seed=1, asset="cache/x.jpg"))]})
    out = apply_ops(doc, parse_ops([{"op": "set_ai_reason", "scene_id": "scn_a", "text": "  hipótese   do mapa "}]))
    assert out.scene("scn_a").visual.ai_reason == "hipótese do mapa"
    assert out.scene("scn_a").visual.asset == "cache/x.jpg" and out.scene("scn_a").visual.prompt == "a map"
    cleared = apply_ops(out, parse_ops([{"op": "set_ai_reason", "scene_id": "scn_a", "text": ""}]))
    assert cleared.scene("scn_a").visual.ai_reason is None


# --- job do storyboard ------------------------------------------------------------------------------


class _Runner:
    def submit(self, kind, payload=None, project_id=None):
        return {"id": "job_x"}


def _job_project() -> str:
    doc = service.create_project(title="GTA 6", style_id="misterios-short", topic="trailer de GTA 6")
    doc = doc.model_copy(update={"segments": base_doc().segments, "sources": base_doc().sources})
    service.commit(doc.id, doc, "roteiro")
    return doc.id


def _ctx(pid: str) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(project_id=pid, job_id=None, progress=lambda f, m="": None, cancelled=lambda: False)


def test_storyboard_job_uses_this_gathering_not_an_old_pool_json(monkeypatch):
    from studio.core.project.pool import load_pool
    from studio.workers import tasks

    pid = _job_project()
    save_pool(pid, make_pool())  # acervo de uma coleta anterior
    monkeypatch.setattr(tasks, "get_runner", lambda: _Runner())
    monkeypatch.setattr(tasks.media_pool, "gather", lambda doc, style, **_: Pool(gathered_at="2026-11-18T00:00:00Z"))
    tasks.run_storyboard(_ctx(pid), {})
    doc = service.get_doc(pid)
    # a coleta voltou vazia: nenhuma cena cita item do acervo antigo (ficaria pendente para sempre)
    assert not [s for s in doc.scenes if s.visual.media is not None and s.visual.media.pool_id]
    assert load_pool(pid).items == []  # o arquivo antigo não vale mais (a coleta vazia é gravada no lugar)
    review = service.get_project_row(pid)["settings"]["storyboard_review"]
    assert "ai_pct" not in review["counts"]

    def broken(doc, style, **_):
        raise RuntimeError("coletor fora do ar")

    save_pool(pid, make_pool())
    monkeypatch.setattr(tasks.media_pool, "gather", broken)
    result = tasks.run_storyboard(_ctx(pid), {})
    assert any("acervo" in w for w in result["warnings"])  # aviso em português, sem a exceção crua
    assert not any("coletor fora do ar" in w for w in result["warnings"])
    assert not [s for s in service.get_doc(pid).scenes if s.visual.media is not None and s.visual.media.pool_id]


def test_storyboard_job_passes_the_gathered_pool_and_reviews_after_the_attach(monkeypatch):
    from studio.workers import tasks

    pid = _job_project()
    monkeypatch.setattr(tasks, "get_runner", lambda: _Runner())
    monkeypatch.setattr(tasks.media_pool, "gather", lambda doc, style, **_: make_pool())
    # anexo que não baixou nada (sem rede, item sumido): a mídia do acervo fica pendente na cena
    monkeypatch.setattr(tasks.media_pool, "attach_chosen", lambda doc, pool, **_: [])
    tasks.run_storyboard(_ctx(pid), {})
    doc = service.get_doc(pid)
    cited = [s for s in doc.scenes if s.visual.media is not None and s.visual.media.pool_id]
    assert cited
    # a revisão gravada acusa cada cena pendente e não a conta como real
    review = service.get_project_row(pid)["settings"]["storyboard_review"]
    assert review["counts"]["media_pending"] == len(cited) and review["counts"]["real_pct"] == 0
    pending = [i for i in review["issues"] if i["code"] == "midia_pendente"]
    assert {i["scene_id"] for i in pending} == {s.id for s in cited}
