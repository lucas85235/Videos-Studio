"""Tela final do vídeo longo (QA r4 do prj_gq4krrs11w: o vídeo terminava na arte-chave borrada, sem tela final).

O vídeo longo horizontal ganha 8 s de tela final (end screen do YouTube: dois vídeos sugeridos e o inscrever) logo
depois da fala, sobre o material real do próprio vídeo em movimento, com o avatar quando o projeto tem um; o Short e o
vídeo curto demais para o end screen ficam como antes."""

from __future__ import annotations

import math

from PIL import Image

from studio.core.project.document import Presenter, PresenterPose, ProjectDoc, Scene, Segment, Visual
from studio.core.styles.library import get_style
from studio.core.timeline import (
    END_SCREEN_BREATH_S,
    END_SCREEN_MIN_S,
    END_SCREEN_S,
    END_SCREEN_SHOTS,
    NarrationClip,
    build_timeline,
    chunk_timeline,
    end_screen_material,
    lead_in,
    tail_out,
)
from studio.providers.base import Word

TEXT = "isso aqui é chocante pessoal e de novo chocante no fim da frase toda mesmo"
STEP = 0.5
LONG = get_style("curiosidades-longo")
SHORT = get_style("curiosidades-short")


def _doc(n: int, style_id: str = "curiosidades-longo") -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", text={"pt-BR": TEXT, "en": TEXT}) for i in range(n)]
    scenes = [Scene(id=f"scn_{i}", segment_id=f"seg_{i}", visual=Visual(prompt="x")) for i in range(n)]
    return ProjectDoc(id="prj_es", title="t", style_id=style_id, segments=segments, scenes=scenes, seed=5,
                      languages=["pt-BR", "en"])


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    toks = TEXT.split()
    return {seg.id: NarrationClip(seg.id, "x.wav", len(toks) * STEP + 0.2,
                                  [Word(t, k * STEP, k * STEP + 0.4) for k, t in enumerate(toks)])
            for seg in doc.segments}


def _speech_end(doc: ProjectDoc, style) -> float:
    clips = _clips(doc)
    t = lead_in(style) + sum(c.duration for c in clips.values()) + style.voice.gap_s * (len(clips) - 1)
    return t


def _timeline(doc: ProjectDoc, style=LONG, lang: str = "pt-BR", **kwargs):
    images = {s.id: f"cache/images/{s.id}.jpg" for s in doc.scenes}
    return build_timeline(doc, style, lang, _clips(doc), images, **kwargs)


def test_long_video_ends_with_end_screen_right_after_the_speech():
    doc = _doc(5)
    assert _speech_end(doc, LONG) >= END_SCREEN_MIN_S
    tl = _timeline(doc)
    fps = tl["fps"]
    *body, end = tl["scenes"]
    assert end["kind"] == "endscreen" and end["id"] == "endscreen"
    assert end["durationInFrames"] == int(math.floor(END_SCREEN_S * fps + 0.5))
    # Entra logo depois da fala (o respiro), no lugar da cauda parada de 1,5 s; as cenas continuam contínuas.
    expected = int(math.floor((_speech_end(doc, LONG) + END_SCREEN_BREATH_S) * fps + 0.5))
    assert end["from"] == expected
    assert body[-1]["from"] + body[-1]["durationInFrames"] == end["from"]
    assert tl["durationInFrames"] == end["from"] + end["durationInFrames"]
    assert tl["duration"] == round(tl["durationInFrames"] / fps, 3)
    # Sem legenda nem fala; textos na língua do vídeo; o material é o do vídeo (aqui só imagens de IA, sem arquivo).
    assert end["captions"] == [] and end["words"] == []
    assert end["end"] == {"title": "Assista também", "subscribe": "Inscreva-se"}
    assert end["transition"] == "cut"
    assert "presenter" not in end
    # O último quadro da fala continua com a legenda até o fim dela: nada da tela final cobre a fala.
    last_line = body[-1]["captions"][-1]
    assert body[-1]["from"] + last_line["to"] <= end["from"]
    en = _timeline(doc, lang="en")["scenes"][-1]
    assert en["end"] == {"title": "Watch next", "subscribe": "Subscribe"}


def test_end_screen_blocks_render_and_cover_the_whole_video():
    tl = _timeline(_doc(5))
    chunks = chunk_timeline(tl, "r1")
    assert chunks[-1]["scene_ids"][-1] == "endscreen"
    assert sum(c["durationInFrames"] for c in chunks) == tl["durationInFrames"]


def test_short_and_too_short_video_keep_the_old_tail():
    doc = _doc(5, "curiosidades-short")
    tl = _timeline(doc, SHORT)
    assert all(s["kind"] != "endscreen" for s in tl["scenes"])
    fps = tl["fps"]
    assert tl["durationInFrames"] == int(math.floor((_speech_end(doc, SHORT) + tail_out(SHORT)) * fps + 0.5))
    small = _doc(2)
    assert _speech_end(small, LONG) < END_SCREEN_MIN_S
    tl = _timeline(small)
    assert all(s["kind"] != "endscreen" for s in tl["scenes"])
    assert tl["durationInFrames"] == int(math.floor((_speech_end(small, LONG) + tail_out(LONG)) * fps + 0.5))


def test_end_screen_shows_the_avatar_until_the_last_frame():
    presenter = Presenter(enabled=True, default_pose="base", poses=[
        PresenterPose(id="base", file="library/presenter/c/base.png", render_file="library/presenter/c/base.r900.png"),
        PresenterPose(id="feliz", file="library/presenter/c/feliz.png"),
    ])
    end = _timeline(_doc(5), presenter=presenter)["scenes"][-1]
    layer = end["presenter"]
    assert layer["src"] == "library/presenter/c/feliz.png"
    assert layer["side"] == "right" and layer["from"] >= 0
    # Passa do fim da cena: a saída descendo nunca entra.
    assert layer["from"] + layer["dur"] > end["durationInFrames"]


def _clip_scene(prov: str, video: str, seconds: float, clip_in: float = 0.0) -> dict:
    return {"kind": "image", "prov": prov, "src": "cache/a.jpg",
            "shots": [{"from": 0, "dur": 30, "prov": prov, "video": video, "clipIn": clip_in, "seconds": seconds,
                       "src": "cache/poster.jpg"}]}


def test_material_prefers_real_video_clips_longest_first_without_repeats():
    scenes = [
        _clip_scene("official", "lib/trailer.mp4", 2.0, 10.0),
        _clip_scene("ai", "lib/ai.mp4", 9.0),  # IA não
        _clip_scene("capture", "lib/tela.mp4", 9.0),  # captura de tela não
        _clip_scene("official", "lib/trailer.mp4", 2.8, 20.0),
        _clip_scene("official", "lib/trailer.mp4", 2.8, 20.0),  # o mesmo trecho de novo
        _clip_scene("free", "lib/rua.mp4", 3.5),
        {"kind": "image", "prov": "official", "src": "x.jpg", "video": "uploads/p/clips/c.mp4", "video_seconds": 1.5},
    ]
    picked = end_screen_material(scenes)
    assert len(picked) == END_SCREEN_SHOTS
    assert [(p["video"], p.get("clipIn")) for p in picked] == [
        ("lib/rua.mp4", None), ("lib/trailer.mp4", 20.0), ("lib/trailer.mp4", 10.0)]
    assert all(p["prov"] in ("official", "free") for p in picked)


def test_material_without_clips_uses_wide_real_images_and_skips_standing_posters(isolated_settings):
    data = isolated_settings.data_dir
    (data / "img").mkdir(parents=True)
    Image.new("RGB", (60, 100)).save(data / "img" / "poster.jpg")  # pôster em pé: viraria pilar desfocado
    Image.new("RGB", (160, 90)).save(data / "img" / "wide.jpg")
    Image.new("RGB", (160, 90)).save(data / "img" / "ai.jpg")
    scenes = [
        {"kind": "image", "prov": "official", "src": "img/poster.jpg"},
        {"kind": "image", "prov": "official", "src": "img/wide.jpg"},
        {"kind": "image", "prov": "ai", "src": "img/ai.jpg"},
        {"kind": "title", "prov": "graphic", "src": "img/wide2.jpg"},  # fundo de cartão não
    ]
    assert [p["src"] for p in end_screen_material(scenes)] == ["img/wide.jpg"]
    # Sem material real, a imagem do próprio vídeo (IA) em movimento; nunca o pôster em pé.
    only_ai = [scenes[0] | {"prov": "ai"}, scenes[2]]
    assert [p["src"] for p in end_screen_material(only_ai)] == ["img/ai.jpg"]


def test_end_screen_shots_cover_the_scene_and_move():
    doc = _doc(5)
    tl = _timeline(doc)
    end = tl["scenes"][-1]
    shots = end.get("shots") or []
    # Imagens de IA sem arquivo (tamanho desconhecido): entram, com câmera andando.
    assert 1 <= len(shots) <= END_SCREEN_SHOTS
    assert shots[0]["from"] == 0 and sum(s["dur"] for s in shots) == end["durationInFrames"]
    for a, b in zip(shots, shots[1:], strict=False):
        assert a["from"] + a["dur"] == b["from"]
    assert all(s["motion"] in ("push", "ken_burns_in") for s in shots if not s.get("video"))


def test_final_check_reads_the_end_screen_and_empty_cards_like_the_renderer():
    """A conferência final lê a tela final sem aviso de texto curto, e o título, a citação e a contagem vazios como o
    renderer: a imagem nítida (sem cartão vazio sobre o fundo desfocado)."""
    from studio.workers.finalcheck.triggers import effective_kind, late_triggers

    tl = _timeline(_doc(5))
    assert not [i for i in late_triggers(tl) if "endscreen" in str(i)]
    assert effective_kind({"kind": "endscreen"}) == "endscreen"
    assert effective_kind({"kind": "title", "text": " ", "src": "a.jpg"}) == "image"
    assert effective_kind({"kind": "quote", "text": "", "src": None}) == "text"
    assert effective_kind({"kind": "count", "number": None, "text": "", "src": "a.jpg"}) == "image"
    assert effective_kind({"kind": "count", "number": 3, "text": ""}) == "count"
    assert effective_kind({"kind": "title", "text": "5 SINAIS"}) == "title"
