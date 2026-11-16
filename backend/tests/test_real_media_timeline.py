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
from studio.media.link_preview import clean_headline
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


def test_media_scene_has_no_credit_on_screen():
    # O crédito da mídia real vai só na descrição da publicação, nunca na tela (nem em quem reaproveita).
    doc = _doc(media={"scn_0": PHOTO})
    for lang in ("pt-BR", "en-US"):
        scenes = _scenes(doc, lang)
        assert all("credit" not in s for s in scenes.values())
    assert "news" not in _scenes(doc)["scn_0"]


def test_news_card_only_on_its_own_image_scene():
    doc = _doc(media={"scn_2": NEWS})
    pt, en = _scenes(doc), _scenes(doc, "en-US")
    assert pt["scn_2"]["news"] == {"outlet": "IGN Brasil", "headline": "GTA 6 bate recorde", "quoted": False,
                                   "date": "14 nov. 2026", "image": NEWS.asset}
    assert en["scn_2"]["news"]["date"] == "Nov 14, 2026" and en["scn_2"]["news"]["headline"] == "GTA 6 breaks a record"
    assert "credit" not in pt["scn_2"] and "credit" not in en["scn_2"]  # o cartão fica; o crédito, não
    assert "news" not in pt["scn_4"] and "credit" not in pt["scn_4"]  # reaproveita: só a imagem de fundo
    card = _doc(media={"scn_3": NEWS})
    stat = _scenes(card)["scn_3"]
    assert "news" not in stat and "credit" not in stat  # em cartão, só o fundo
    no_image = _doc(media={"scn_2": NEWS.model_copy(update={"asset": None, "published": ""})})
    scene = _scenes(no_image)["scn_2"]
    assert scene["news"] == {"outlet": "IGN Brasil", "headline": "GTA 6 bate recorde", "quoted": False}
    assert "credit" not in scene
    empty = _doc(media={"scn_2": MediaRef(kind="news")})
    assert "news" not in _scenes(empty)["scn_2"]


def test_news_card_quotes_only_the_outlet_headline():
    # Aspas só na manchete do veículo, palavra por palavra (o título da página, limpo como na importação); a tradução
    # e a manchete reescrita pelo usuário não são citação.
    title = "GTA 6 bate recorde | IGN Brasil"
    news = NEWS.model_copy(update={"title": title})
    assert _scenes(_doc(media={"scn_2": news}))["scn_2"]["news"]["quoted"] is True
    assert _scenes(_doc(media={"scn_2": news}), "en-US")["scn_2"]["news"]["quoted"] is False  # tradução

    def quoted(headline: dict[str, str], lang: str = "pt-BR", **update) -> bool:
        media = news.model_copy(update={"headline": headline, **update})
        return _scenes(_doc(media={"scn_2": media}), lang)["scn_2"]["news"]["quoted"]

    assert quoted({"pt-BR": "gta 6  BATE   recorde"})  # espaços e maiúsculas não contam
    assert quoted({"pt-BR": title})  # o título cru também é a manchete do veículo
    assert quoted({})  # sem manchete escrita, o cartão mostra o título
    assert quoted({"pt-BR": "GTA 6 bate recorde"}, "en-US")  # vídeo em inglês com a manchete original
    long_title = " ".join(f"palavra{i}" for i in range(20))
    assert quoted({"pt-BR": clean_headline(long_title, "IGN Brasil")}, title=long_title)  # cortada com "…"
    assert not quoted({"pt-BR": "GTA 6 quebra recorde"})  # reescrita pelo usuário
    assert not quoted({"pt-BR": "GTA 6 bate recorde"}, title="")  # sem título (arquivo enviado): não dá para conferir
    # veículo editado depois da importação ("IGN Brasil" → "IGN"): a manchete continua sendo a do veículo
    assert quoted({"pt-BR": "GTA 6 bate recorde"}, outlet="IGN")
    assert quoted({"pt-BR": "GTA 6 bate recorde"}, outlet="IGN", title="IGN Brasil - GTA 6 bate recorde")


LONG_HEADLINE = "Rockstar confirma que GTA 6 chega em maio de 2026 com o maior mapa"


def test_news_card_too_short_to_read_warns():
    # Cena de 2 s; 14 palavras pedem 0,3 s cada mais 0,6 s (4,8 s): o aviso diz quanto cabe.
    long = NEWS.model_copy(update={"headline": {"pt-BR": LONG_HEADLINE, "en-US": "Record for GTA 6"}})
    timeline = _timeline(_doc(media={"scn_2": long}))
    assert timeline["warnings"] == [
        "Cena 3: a manchete da notícia (14 palavras) fica 2,0 s na tela; encurte para até 4 palavras."
    ]
    assert "warnings" not in _timeline(_doc(media={"scn_2": NEWS}))  # 4 palavras cabem (1,8 s)
    assert "warnings" not in _timeline(_doc(media={"scn_2": long}), "en-US")
    # com mais de um idioma, o aviso diz de qual manchete é
    both = _doc(media={"scn_2": long.model_copy(update={"headline": {"pt-BR": "GTA 6 bate recorde",
                                                                     "en-US": LONG_HEADLINE}})},
                languages=["pt-BR", "en-US"])
    assert "warnings" not in _timeline(both)
    assert _timeline(both, "en-US")["warnings"] == [
        "Cena 3 (en-US): a manchete da notícia (14 palavras) fica 2,0 s na tela; encurte para até 4 palavras."
    ]


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
