"""QA r3 do prj_gq4krrs11w: quadros mortos, transições lavadas e vereditos confusos.

- Trecho de vídeo oficial nunca usa a cartela legal (ESRB), a tela preta, a cartela final nem o fade: os planos ficam
  só com o trecho vivo (`clips.dead_frame`, `clips.trim_dead`) e a cartela lida pela visão sai do acervo.
- Cartão (título, texto, dado) não fica mais de `CARD_STILL_S` parado sem evento (`edit_plan._max_still`).
- Veredito positivo ("CONFIRMADO") não fica ao lado de frase negativa ("PC: NÃO ANUNCIADO").

As transições curtas e a tarja da legenda sobre a lavagem estão em `test_renderer_fx.py`.
"""

from __future__ import annotations

import numpy as np
import pytest

from studio.core import verdict
from studio.media import clips

H, W = 90, 160


def _black() -> np.ndarray:
    return np.zeros((H, W), dtype=np.uint8)


def _slate() -> np.ndarray:
    """Cartela da classificação etária: fundo preto, caixa branca com letras pretas em cima e moldura embaixo."""
    g = _black()
    g[20:45, 30:130] = 255
    g[26:30, 40:120] = 0  # letras pretas na faixa branca
    g[36:40, 40:110] = 0
    g[45:72, 30:34] = 255
    g[45:72, 126:130] = 255
    g[69:72, 30:130] = 255
    g[52:56, 40:100] = 255  # letras brancas embaixo
    return g


def _end_card() -> np.ndarray:
    """Tela final: fundo cinza-escuro liso com o logo pequeno no meio."""
    g = np.full((H, W), 33, dtype=np.uint8)
    g[38:52, 72:88] = 230
    return g


def _scene(seed: int = 1) -> np.ndarray:
    """Imagem de verdade: gradientes e textura em meio-tom."""
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:H, 0:W]
    base = 60 + 90 * (x / W) + 40 * np.sin(y / 7.0)
    return np.clip(base + rng.normal(0, 18, (H, W)), 0, 255).astype(np.uint8)


def _night() -> np.ndarray:
    """Cena noturna: quase tudo escuro, mas com meio-tom (não é tela preta nem cartela)."""
    rng = np.random.default_rng(3)
    g = np.clip(rng.normal(14, 9, (H, W)), 0, 255)
    g[30:70, 20:80] += 50  # vulto iluminado
    g[10:16, 100:150] += 120  # letreiro
    return np.clip(g, 0, 255).astype(np.uint8)


def _art_on_flat() -> np.ndarray:
    """Capa do jogo no meio de um fundo cinza liso: arte em meio-tom ocupando um terço do quadro."""
    g = np.full((H, W), 33, dtype=np.uint8)
    g[10:80, 50:110] = _scene(5)[10:80, 50:110]
    return g


def test_dead_frame_catches_slates_black_white_and_end_cards() -> None:
    assert clips.dead_frame(_black()) == "tela lisa"
    assert clips.dead_frame(np.full((H, W), 250, dtype=np.uint8)) == "tela lisa"  # fade para branco
    assert clips.dead_frame(_end_card()) == "tela lisa"
    assert clips.dead_frame(_slate()) == "cartela"
    assert clips.dead_frame(_slate() / 255.0) == "cartela"  # aceita 0–1 também


def test_dead_frame_keeps_real_images_dark_scenes_and_art_on_flat_background() -> None:
    """Falso positivo derruba material bom de outros canais: cena noturna, arte sobre fundo liso e imagem comum
    ficam."""
    assert clips.dead_frame(_scene()) is None
    assert clips.dead_frame(_night()) is None
    assert clips.dead_frame(_art_on_flat()) is None


def test_live_span_trims_fades_and_drops_dead_shots() -> None:
    step = 0.2
    # plano de 0 a 3 s: começa na cartela (0–1 s) e é vivo depois
    marks = [(round(i * step, 3), i * step < 1.0) for i in range(15)]
    assert clips.live_span(0.0, 3.0, marks, step=step) == (1.0, 2.0)
    # plano todo morto (a cartela do começo do trailer): sai
    assert clips.live_span(0.0, 1.0, marks, step=step) is None
    # termina num fade de 0,6 s
    fade = [(round(10 + i * step, 3), i >= 7) for i in range(10)]
    assert clips.live_span(10.0, 2.0, fade, step=step) == (10.0, 1.4)
    # sobra viva curta demais: sai
    short = [(round(i * step, 3), i not in (4, 5)) for i in range(10)]
    assert clips.live_span(0.0, 2.0, short, step=step) is None
    # sem quadro medido (cortes de teste sem a passada dos quadros): fica como está
    assert clips.live_span(5.0, 2.0, [], step=step) == (5.0, 2.0)
    planned = [{"k": 1, "a": 0.0, "dur": 1.0}, {"k": 2, "a": 1.0, "dur": 2.0}]
    assert clips.trim_dead(planned, marks, step=step) == [{"k": 2, "a": 1.0, "dur": 2.0}]


def test_analyze_drops_the_rating_slate_before_spreading(isolated_settings, tmp_path) -> None:
    """A cartela do começo e a tela final saem antes de espalhar os planos: elas não levam a vaga de um plano vivo."""
    from studio.config import get_settings
    from tests.test_media_pool import lavfi_clip

    src = lavfi_clip(get_settings().data_dir / "fontes" / "trailer.mp4", cut_at=2.0, total=6.0,
                     first="testsrc2", second="smptebars")
    step = clips.scan_step()
    # 0–1 s cartela, 1–2 s vivo, 2–5 s vivo, 5–6 s tela final (o corte real do arquivo fica em 2 s)
    marks = [(round(i * step, 3), i * step < 1.0 or i * step >= 5.0) for i in range(int(6 / step))]
    shots = clips.analyze(src, max_clip_s=4.0, label="Trailer", scan_of=lambda p: ([2.0], marks))
    assert shots is not None
    assert [(s.k, round(s.a, 2), round(s.dur, 2)) for s in shots] == [(1, 1.0, 1.0), (2, 2.0, 3.0)]


def test_slate_text_from_vision_leaves_the_pool() -> None:
    from studio.workers.pipeline import media_pool

    esrb = 'Trailer 1 oficial (Rockstar) · trailer · texto "MAY CONTAIN CONTENT INAPPROPRIATE FOR CH"'
    assert clips.slate_text(esrb)
    assert clips.slate_text('Trailer · texto "Visit esrb.org for rating information"')
    assert not clips.slate_text('Trailer 1 oficial (Rockstar) · trailer · texto "VICE"')
    assert not clips.slate_text("Trailer sobre classificação ESRB")  # só o texto lido NA TELA conta
    assert media_pool.is_slate({"media": "clip", "desc": esrb})
    assert not media_pool.is_slate({"media": "image", "desc": esrb})


# --- carimbo de veredito -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("stamp", "texts", "lang", "ambiguous"), [
    ("CONFIRMADO", ["PC: NÃO ANUNCIADO"], "pt-BR", True),
    ("CONFIRMADO", ["PC HOJE: NÃO EXISTE"], "pt-BR", True),
    ("Confirmado", ["Sem data para o PC"], "pt-BR", True),
    ("CONFIRMED", ["NOT ON PC"], "en", True),
    ("CONFIRMADO", ["REMOTE PLAY"], "pt-BR", False),
    ("CONFIRMADO", ["SAIU 19/11 · PS5 E XBOX"], "pt-BR", False),
    ("CONFIRMADO", ["Disponível no site oficial"], "pt-BR", False),  # "no" em português não é negação
    ("GOLPE", ["ACESSO ANTECIPADO NÃO EXISTE"], "pt-BR", False),  # veredito negativo casa com a negação
    ("BOATO", ["PC: NÃO ANUNCIADO"], "pt-BR", False),
    ("NÃO CONFIRMADO", ["PC: NÃO ANUNCIADO"], "pt-BR", False),
    ("", ["PC: NÃO ANUNCIADO"], "pt-BR", False),
])
def test_ambiguous_positive_verdict(stamp: str, texts: list[str], lang: str, ambiguous: bool) -> None:
    assert verdict.ambiguous_positive(stamp, texts, lang) is ambiguous


def test_timeline_drops_confirmed_stamp_next_to_negation() -> None:
    from studio.core.project.document import Scene
    from studio.core.timeline import drop_ambiguous_verdict

    scene = Scene(id="scn_a", segment_id="seg", kind="title", overlay_text={"pt-BR": "PC: NÃO ANUNCIADO"})
    resolved = {"text": "PC: NÃO ANUNCIADO", "card": {"stamp": "CONFIRMADO", "stamp_at": "side", "source": "Rockstar"}}
    drop_ambiguous_verdict(scene, resolved, "pt-BR")
    assert resolved["card"] == {"source": "Rockstar"}
    alone = {"text": "PC HOJE: NÃO EXISTE", "card": {"stamp": "CONFIRMADO"}}
    drop_ambiguous_verdict(scene, alone, "pt-BR")
    assert "card" not in alone
    kept = {"text": "REMOTE PLAY", "card": {"stamp": "CONFIRMADO"}}
    drop_ambiguous_verdict(scene, kept, "pt-BR")
    assert kept["card"] == {"stamp": "CONFIRMADO"}


def test_storyboard_review_drops_confirmed_next_to_negation() -> None:
    import inspect

    from studio.core.project.document import ProjectDoc, Scene, SceneCard, Visual
    from studio.core.project.document import Segment as Seg
    from studio.workers.pipeline import storyboard_check

    seg = Seg(id="seg0", role="body", text={"pt-BR": "Versão de PC, a Rockstar não anunciou."})
    cases = [("PC: NÃO ANUNCIADO", "CONFIRMADO"), ("REMOTE PLAY", "CONFIRMADO"), ("ACESSO ANTECIPADO NÃO EXISTE", "GOLPE")]
    scenes = [Scene(id=f"scn_{i}", segment_id="seg0", part=i, parts=len(cases), kind="title", visual=Visual(type="none"),
                    overlay_text={"pt-BR": text}, card=SceneCard(stamp={"pt-BR": stamp}, source="Rockstar Games"))
              for i, (text, stamp) in enumerate(cases)]
    doc = ProjectDoc(id="prj_teste", title="Teste", style_id="curiosidades-longo", segments=[seg], scenes=scenes)
    beats = [storyboard_check.Beat(key=f"seg0:{i}", segment_id="seg0", part=i, parts=len(cases), index=0, role="body",
                                   words=["Versão", "de", "PC"], seconds=3.0) for i in range(len(cases))]
    issues = storyboard_check.fix_ambiguous_stamps(doc, beats)
    assert [(i.code, i.scene_id) for i in issues] == [("carimbo_ambiguo", "scn_0")]
    assert doc.scenes[0].card is not None and doc.scenes[0].card.stamp == {}
    assert doc.scenes[0].card.source == "Rockstar Games"
    assert doc.scenes[1].card.stamp == {"pt-BR": "CONFIRMADO"} and doc.scenes[2].card.stamp == {"pt-BR": "GOLPE"}
    assert "fix_ambiguous_stamps(doc, beats)" in inspect.getsource(storyboard_check.check)


# --- cartão parado ---------------------------------------------------------------------------------------------------


def test_long_card_gets_a_punch_before_card_still_limit(isolated_settings) -> None:
    """Cartão de título com a fala longa ficava parado (9 s de "PC: NÃO ANUNCIADO"): nenhum trecho do cartão sem
    evento passa de `CARD_STILL_S`."""
    from studio.core.project.document import ProjectDoc, Scene, Visual
    from studio.core.project.document import Segment as Seg
    from studio.core.project.pool import Pool
    from studio.core.styles.library import get_style
    from studio.core.styles.schema import grammar_for
    from studio.workers.pipeline import edit_plan

    text = ("Versão de PC, a Rockstar não anunciou. Nem demo, nem beta, nem acesso antecipado: nada disso aparece no "
            "site oficial, e quem promete o jogo no computador hoje está mentindo para você, sem exceção nenhuma.")
    style = get_style("curiosidades-longo")
    segments = [Seg(id="seg0", role="hook", text={"pt-BR": "Abertura curta do vídeo."}),
                Seg(id="seg1", role="body", text={"pt-BR": text})]
    scenes = [Scene(id="scn0", segment_id="seg0", visual=Visual(prompt="abertura")),
              Scene(id="scn1", segment_id="seg1", kind="title", overlay_text={"pt-BR": "PC: NÃO ANUNCIADO"},
                    visual=Visual(prompt="fundo"))]
    doc = ProjectDoc(id="prj_teste", title="Teste", style_id=style.id, seed=7, segments=segments, scenes=scenes)
    edit_plan.plan(doc, style, Pool(), recent=set())
    planner = edit_plan._Planner(doc, style, grammar_for(style), Pool(), set())
    beat = next(b for b in planner.beats if b.scene.id == "scn1")
    assert beat.dur > edit_plan.CARD_STILL_S + 1
    marks = sorted({beat.start, beat.end, *(planner._time_of(beat, e.at) for e in beat.scene.fx
                                            if e.type in ("punch", "zoom_rect", "shake"))})
    gaps = [b - a for a, b in zip(marks, marks[1:], strict=False)]
    assert max(gaps) <= edit_plan.CARD_STILL_S + 1e-6, gaps
