"""Timeline com mídia real: crédito na tela, cartão de notícia, câmera suave e foco. Sem mídia, nada muda."""

from __future__ import annotations

from studio.core.project.document import (
    ArtDirection,
    MediaRef,
    ProjectDoc,
    RealHint,
    Scene,
    SceneCard,
    Segment,
    Visual,
)
from studio.core.styles.library import get_style
from studio.core.timeline import (
    REAL_MOTIONS,
    NarrationClip,
    build_timeline,
    chunk_timeline,
    news_date,
    resolve_scene,
)
from studio.providers.base import Word

LEGACY_SCENE_KEYS = {"id", "kind", "motion", "transition", "seed", "look", "text", "number", "from", "durationInFrames",
                     "src", "captions", "words"}
STYLE = get_style("misterios-short")
PHOTO = MediaRef(kind="photo", source="commons", asset="uploads/prj_t/media/aaaaaaaaaaaaaaaa.jpg", width=3840,
                 height=2400, credit="Fulano / Wikimedia Commons · CC BY-SA 4.0", focus="top_left")
NEWS = MediaRef(kind="news", source="link", asset="uploads/prj_t/media/bbbbbbbbbbbbbbbb.jpg", outlet="IGN Brasil",
                published="2026-11-14", credit="IGN Brasil", editorial=True,
                headline={"pt-BR": "“GTA 6 bate recorde”", "en-US": "GTA 6 breaks a record"})


def _doc(media: dict[str, MediaRef] | None = None, real: dict[str, RealHint] | None = None, **extra) -> ProjectDoc:
    seg = Segment(id="seg_0", text={"pt-BR": " ".join(f"palavra{j}" for j in range(24)) + "."})
    kinds = {"scn_3": "stat"}
    scenes = []
    for k in range(5):
        sid = f"scn_{k}"
        visual = Visual(prompt=f"imagem {k}") if k in (0, 2) else Visual(reuse_of="scn_0" if k == 1 else "scn_2")
        visual.media = (media or {}).get(sid)
        scenes.append(Scene(id=sid, segment_id="seg_0", part=k, parts=5, kind=kinds.get(sid, "image"), visual=visual,
                            real=(real or {}).get(sid), span=5 if k < 4 else 4,
                            card=SceneCard(value={"pt-BR": "2"}) if sid == "scn_3" else None))
    return ProjectDoc(**{"id": "prj_t", "title": "t", "style_id": "misterios-short", "segments": [seg],
                         "scenes": scenes, "seed": 7, **extra})


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    words = doc.segments[0].text["pt-BR"].split()
    return {"seg_0": NarrationClip("seg_0", "x.wav", 10.0, [Word(w, i * 0.4, i * 0.4 + 0.35) for i, w in enumerate(words)])}


def _timeline(doc: ProjectDoc, lang: str = "pt-BR") -> dict:
    return build_timeline(doc, STYLE, lang, _clips(doc), {s.id: f"cache/x/{s.id}.jpg" for s in doc.scenes})


def _scenes(doc: ProjectDoc, lang: str = "pt-BR") -> dict[str, dict]:
    return {s["id"]: s for s in _timeline(doc, lang)["scenes"]}


def test_without_media_the_timeline_does_not_change():
    plain = _doc()
    hinted = _doc(real={"scn_0": RealHint(kind="photo", query="Miami Beach"), "scn_2": RealHint(kind="news", note="x")})
    for scene in _timeline(plain)["scenes"]:
        extra = set(scene) - LEGACY_SCENE_KEYS
        assert extra <= {"card", "reveal"}, extra  # nada de credit/news
    # a sugestão do storyboard não muda nenhum bloco (hash igual)
    keys = [c["key"] for c in chunk_timeline(_timeline(plain), "v")]
    assert keys == [c["key"] for c in chunk_timeline(_timeline(hinted), "v")]
    reloaded = ProjectDoc.model_validate({k: v for k, v in plain.model_dump().items()})
    assert keys == [c["key"] for c in chunk_timeline(_timeline(reloaded), "v")]


def test_media_scene_has_the_credit_in_the_video_language():
    doc = _doc(media={"scn_0": PHOTO})
    pt, en = _scenes(doc), _scenes(doc, "en-US")
    assert pt["scn_0"]["credit"] == "Foto: Fulano / Wikimedia Commons · CC BY-SA 4.0"
    assert en["scn_0"]["credit"] == "Photo: Fulano / Wikimedia Commons · CC BY-SA 4.0"
    assert pt["scn_1"]["credit"] == pt["scn_0"]["credit"]  # quem reaproveita mostra a mídia (e o crédito)
    assert "credit" not in pt["scn_2"] and "credit" not in pt["scn_4"]
    assert "news" not in pt["scn_0"]


def test_news_card_only_on_its_own_image_scene():
    doc = _doc(media={"scn_2": NEWS})
    pt, en = _scenes(doc), _scenes(doc, "en-US")
    assert pt["scn_2"]["news"] == {"outlet": "IGN Brasil", "headline": "GTA 6 bate recorde", "date": "14 nov. 2026",
                                   "image": NEWS.asset}
    assert en["scn_2"]["news"]["date"] == "Nov 14, 2026" and en["scn_2"]["news"]["headline"] == "GTA 6 breaks a record"
    assert pt["scn_2"]["credit"] == "Imagem: IGN Brasil"
    assert "news" not in pt["scn_4"] and pt["scn_4"]["credit"] == "Imagem: IGN Brasil"  # reaproveita: só o crédito
    card = _doc(media={"scn_3": NEWS})
    stat = _scenes(card)["scn_3"]
    assert "news" not in stat and stat["credit"] == "Imagem: IGN Brasil"  # em cartão, só fundo e crédito
    no_image = _doc(media={"scn_2": NEWS.model_copy(update={"asset": None, "published": ""})})
    scene = _scenes(no_image)["scn_2"]
    assert scene["news"] == {"outlet": "IGN Brasil", "headline": "GTA 6 bate recorde"} and "credit" not in scene
    empty = _doc(media={"scn_2": MediaRef(kind="news")})
    assert "news" not in _scenes(empty)["scn_2"]


def test_media_camera_is_smooth_and_follows_the_focus():
    style = STYLE.model_copy(deep=True)
    style.edit.camera = {"pan_left": 5, "punch_zoom": 3}  # nenhum movimento suave no estilo
    for i in range(12):
        doc = _doc(media={"scn_0": PHOTO}, seed=i)
        out = resolve_scene(doc, style, doc.scene("scn_0"), "pt-BR", is_first=False)
        assert out["motion"] in REAL_MOTIONS
        assert out["focus"] == "top_left"
    doc = _doc(media={"scn_0": PHOTO})
    doc.scene("scn_0").motion = "pan_left"
    doc.scene("scn_0").focus = "bottom"
    out = resolve_scene(doc, style, doc.scene("scn_0"), "pt-BR", is_first=False)
    assert out["motion"] == "pan_left" and out["focus"] == "bottom"  # o escolhido na cena vale
    centered = _doc(media={"scn_0": PHOTO.model_copy(update={"focus": "center"})})
    assert "focus" not in resolve_scene(centered, style, centered.scene("scn_0"), "pt-BR", is_first=False)
    plain = _doc()
    assert resolve_scene(plain, style, plain.scene("scn_0"), "pt-BR", is_first=False)["motion"] in style.edit.camera


def test_no_stamp_place_on_media_scene():
    art = ArtDirection(genre="games", languages=["key_art"])
    directed = Visual(prompt="x", language="key_art", shot="wide", composition="right_third")

    def stamped(with_media: bool) -> dict:
        doc = _doc(art=art)
        scene = doc.scene("scn_0")
        scene.visual = directed.model_copy(update={"media": PHOTO if with_media else None})
        scene.card = SceneCard(stamp={"pt-BR": "RECORDE"})
        return resolve_scene(doc, STYLE, scene, "pt-BR", is_first=False)["card"]

    assert stamped(False)["stamp_at"] == "left"
    assert "stamp_at" not in stamped(True)  # composição da IA não vale para a foto real


def test_news_date_formats():
    assert news_date("2026-11-14", "pt-BR") == "14 nov. 2026"
    assert news_date("2026-03-05", "en-US") == "Mar 5, 2026"
    assert news_date("2026-09-01", "es-ES") == "1 sept 2026"
    assert news_date("2026-11-14", "ja-JP") == "Nov 14, 2026"
    assert news_date("2026-11", "pt-BR") == "nov. 2026" and news_date("2026-11", "en-US") == "Nov 2026"
    assert news_date("2026-01", "es-MX") == "ene 2026" and news_date("2026", "pt-BR") == "2026"
    assert news_date("", "pt-BR") == "" and news_date("2026-13", "pt-BR") == "" and news_date("ontem", "en") == ""
