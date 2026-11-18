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
from studio.workers.pipeline import authoring, storyboard_check
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
    assert review["counts"]["real_pct"] >= 50
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
    assert lucia.visual.media is not None and lucia.visual.media.pool_id == "p3"
    assert lucia.visual.media.tier == "official" and lucia.visual.media.video == f"uploads/{PID}/pool/p3.mp4"
    assert lucia.visual.media.asset == f"uploads/{PID}/pool/p3.jpg"  # o pôster
    assert "ia_sem_motivo" in codes(review)


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
    assert fallback and "p99" in fallback[0]["message"] and fallback[0]["scene_id"] == scene.id
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


def test_review_flags_official_over_share_and_same_tier_run(monkeypatch):
    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    # tudo no trailer: oficial acima de 35% e o mesmo nível muito tempo seguido
    extra = {i: {"source": "pool", "pool_id": "p1"} for i in range(len(beats))}
    new_doc, review, _, _ = run(monkeypatch, doc, extra, pool=make_pool())
    found = {i["code"]: i for i in review["issues"]}
    assert found["official_over_share"]["severity"] == "aviso"
    assert found["same_tier_run"]["severity"] == "aviso" and "official" in found["same_tier_run"]["message"]


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


def test_media_from_pool_fills_tier_and_credit():
    items = {i.id: i for i in pool_items()}
    clip = authoring.media_from_pool(items["p1"])
    assert (clip.kind, clip.source, clip.tier, clip.pool_id) == ("clip", "youtube", "official", "p1")
    assert clip.video.endswith("p1.mp4") and clip.asset.endswith("p1.jpg") and clip.dur_s == 3.2
    assert clip.editorial and clip.credit == "Rockstar Games"
    photo = authoring.media_from_pool(items["p4"])
    assert (photo.kind, photo.source, photo.tier, photo.video) == ("photo", "commons", "free", None)
    assert photo.license == "CC BY 4.0"
    pending = authoring.media_from_pool(items["p5"].model_copy(update={"path": None}))
    assert pending.asset is None and pending.pool_id == "p5"  # pendente: `attach_chosen` baixa


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
    assert len(faltou) == 1 and faltou[0]["severity"] == "aviso" and "acervo de material real está vazio" in (
        faltou[0]["message"])
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
    plain = compose_directed_prompt(STYLE, art, bare)
    assert "World:" not in plain and "Accent colors" not in plain and "Treatment" not in plain
    assert plain.startswith("Wide establishing shot. loose ink sketch")
    full = compose_directed_prompt(STYLE, art, with_entity)
    assert "World: neon coastal city" in full and "Treatment: grainy contrast" in full
    user = compose_directed_prompt(STYLE, art.model_copy(update={"source": "user"}), bare)
    assert "World: neon coastal city" in user  # direção do usuário fica como ele escreveu
    assert "World: neon coastal city" in compose_directed_prompt(STYLE, art, bare, world_always=True)
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
    builder = _builder([Scene(id="scn_1", segment_id="seg_1", visual=visual)], art)
    scene = builder.doc.scenes[0]
    inputs = builder.image_inputs(scene)
    assert "World:" not in inputs["prompt"]
    old = {**inputs, "prompt": compose_directed_prompt(builder.style, art, visual, world_always=True)}
    assert old["prompt"] != inputs["prompt"]
    assert image_key(old) in builder.image_keys(inputs, scene)


@pytest.mark.skipif(shutil.which(get_settings().ffmpeg) is None, reason="sem ffmpeg")
def test_real_clip_gets_a_poster_and_goes_to_the_timeline():
    from studio.media.ffmpeg import run_ffmpeg

    data = get_settings().data_dir
    rel = f"uploads/{PID}/pool/clip.mp4"
    (data / rel).parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-f", "lavfi", "-i", "testsrc=size=320x180:rate=15:duration=2", "-c:v", "libx264", "-pix_fmt",
                "yuv420p", str(data / rel)], timeout=120)
    clip = MediaRef(kind="clip", source="youtube", video=rel, dur_s=2.0, tier="official", pool_id="p1")
    photo_rel = f"uploads/{PID}/pool/photo.jpg"
    Image.new("RGB", (64, 64), (200, 30, 30)).save(data / photo_rel)
    photo = MediaRef(kind="photo", source="commons", asset=photo_rel, tier="free", pool_id="p4")
    late = clip.model_copy(update={"start_s": 1.0})
    builder = _builder([Scene(id="scn_c", segment_id="seg_1", part=0, visual=Visual(media=clip)),
                        Scene(id="scn_p", segment_id="seg_1", part=1, visual=Visual(media=photo)),
                        Scene(id="scn_l", segment_id="seg_1", part=2, visual=Visual(media=late))])
    poster = builder.image(builder.doc.scene("scn_c"))
    assert poster and (data / poster).is_file()
    assert builder.image(builder.doc.scene("scn_c")) == poster  # do cache
    assert builder.real_clips() == {"scn_c": {"path": rel, "seconds": 2.0}}  # o trecho do meio fica só no pôster
    assert builder.image(builder.doc.scene("scn_l"))
