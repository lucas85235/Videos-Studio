"""Mídia real no documento: compatibilidade, reaproveitamento, crédito, edições de mídia e as operações de cena."""

from __future__ import annotations

import pytest

from studio.core.project import media_edit
from studio.core.project.document import MediaRef, ProjectDoc, RealHint, Scene, Segment, Visual
from studio.core.project.ops import OpError, apply_ops, parse_ops

PHOTO = MediaRef(kind="photo", source="commons", asset="uploads/prj_doc/media/0123456789abcdef.jpg",
                 title="Miami skyline", author="Fulano", license="CC BY-SA 4.0",
                 credit="Fulano / Wikimedia Commons · CC BY-SA 4.0", candidate_id="commons:File:Miami.jpg",
                 width=3840, height=2400)


def _doc(**extra) -> ProjectDoc:
    seg_a = Segment(id="seg_a", text={"pt-BR": "Primeira parte do roteiro com várias palavras aqui."})
    seg_b = Segment(id="seg_b", text={"pt-BR": "Segunda parte do roteiro."})
    scenes = [
        Scene(id="scn_root", segment_id="seg_a", part=0, parts=2, visual=Visual(prompt="a city at dusk", seed=3), span=5),
        Scene(id="scn_card", segment_id="seg_a", part=1, parts=2, kind="stat", visual=Visual(reuse_of="scn_root"),
              span=3),
        Scene(id="scn_r1", segment_id="seg_b", part=0, parts=2, visual=Visual(reuse_of="scn_root"), span=2),
        Scene(id="scn_r2", segment_id="seg_b", part=1, parts=2, visual=Visual(reuse_of="scn_r1"), span=2),
    ]
    return ProjectDoc(**{"id": "prj_doc", "title": "Doc", "style_id": "misterios-short", "segments": [seg_a, seg_b],
                         "scenes": scenes, **extra})


def _with_media(doc: ProjectDoc, scene_id: str = "scn_root", media: MediaRef = PHOTO) -> ProjectDoc:
    return media_edit.attach_media(doc, scene_id, media)


def _apply(doc: ProjectDoc, *ops: dict) -> ProjectDoc:
    return apply_ops(doc, parse_ops(list(ops)))


# --- modelo -------------------------------------------------------------------------------------


def test_old_document_still_validates():
    raw = {"id": "prj_old", "title": "Antigo", "style_id": "misterios-short",
           "segments": [{"id": "seg_1", "text": {"pt-BR": "Oi."}}],
           "scenes": [{"id": "scn_1", "segment_id": "seg_1", "visual": {"prompt": "x", "seed": 1}}]}
    doc = ProjectDoc.model_validate(raw)
    assert doc.scenes[0].visual.media is None and doc.scenes[0].real is None
    assert doc.shown_media(doc.scenes[0]) is None and "mídia" not in doc.outline()


@pytest.mark.parametrize("bad", ["../x.jpg", "/abs/x.jpg", "C:/x.jpg", "uploads//x.jpg", "./x.jpg", "uploads/../x",
                                 "", "  "])
def test_media_asset_must_be_relative(bad):
    with pytest.raises(ValueError):
        MediaRef(asset=bad)


def test_media_asset_normalizes_backslashes():
    assert MediaRef(asset="uploads\\p\\media\\x.jpg").asset == "uploads/p/media/x.jpg"
    assert MediaRef(kind="news").asset is None


def test_reuse_stops_at_the_media_and_shown_media():
    doc = _with_media(_doc())
    root = doc.scene("scn_root")
    assert doc.reuse_root(doc.scene("scn_r2")).id == "scn_root"
    assert doc.shown_media(root) == PHOTO and doc.shown_media(doc.scene("scn_r2")) == PHOTO
    # a cena com mídia é dona da imagem: a cadeia para nela (o reuse_of dela é reserva)
    chained = _with_media(_doc(), "scn_r1")
    assert chained.reuse_root(chained.scene("scn_r2")).id == "scn_r1"
    assert chained.reuse_root(chained.scene("scn_r1")).id == "scn_r1"
    # imagem fixada na própria cena vence a mídia da raiz
    pinned = doc.model_copy(deep=True)
    pinned.scene("scn_r1").visual.asset = "uploads/prj_doc/fixada.jpg"
    assert pinned.shown_media(pinned.scene("scn_r1")) is None
    assert "mídia real=photo" in doc.outline()
    hinted = _doc()
    hinted.scene("scn_root").real = RealHint(kind="news", note="cartão")
    assert "sugestão de mídia real=news" in hinted.outline()


def test_credit_line_by_language():
    assert PHOTO.credit_line("pt-BR") == "Foto: Fulano / Wikimedia Commons · CC BY-SA 4.0"
    assert PHOTO.credit_line("en-US") == "Photo: Fulano / Wikimedia Commons · CC BY-SA 4.0"
    official = MediaRef(kind="official", credit="Foto: Foto:  Rockstar   Games")
    assert official.credit_line("pt-BR") == "Imagem: Rockstar Games"
    assert official.credit_line("es-ES") == "Imagen: Rockstar Games"
    assert official.credit_line("ja-JP") == "Image: Rockstar Games"
    assert MediaRef(credit="  ").credit_line("pt-BR") == ""
    news = MediaRef(kind="news", title="Título da página", headline={"pt-BR": "Manchete", "en-US": "Headline"})
    assert news.headline_for("en-US") == "Headline" and news.headline_for("es-ES", "pt-BR") == "Manchete"
    assert MediaRef(kind="news", title="Só o título").headline_for("pt-BR") == "Só o título"


# --- edições da mídia ----------------------------------------------------------------------------


def test_attach_and_remove_keep_everything_else():
    doc = _doc()
    doc.scene("scn_r1").visual.asset = "uploads/prj_doc/fixada.jpg"
    for scene_id in ("scn_root", "scn_r1"):
        attached = _with_media(doc, scene_id)
        before = doc.scene(scene_id).visual.model_dump()
        after = attached.scene(scene_id).visual.model_dump()
        assert after.pop("media") == PHOTO.model_dump() and before.pop("media") is None
        assert after == before  # prompt, reuse_of, asset e semente ficam de reserva
        removed = media_edit.remove_media(attached, scene_id)
        assert removed.model_dump() == doc.model_dump()  # tirar devolve a cena ao que era
    assert doc.scene("scn_root").visual.media is None  # o original não muda


def test_media_edit_errors():
    doc = _doc()
    with pytest.raises(OpError, match="cena não encontrada"):
        media_edit.attach_media(doc, "scn_x", PHOTO)
    with pytest.raises(OpError, match="não tem mídia real"):
        media_edit.update_media(doc, "scn_root", {"credit": "x"})
    with pytest.raises(OpError, match="não tem mídia real"):
        media_edit.remove_media(doc, "scn_root")
    locked = doc.model_copy(deep=True)
    locked.scene("scn_root").locked = True
    with pytest.raises(OpError, match="travada"):
        media_edit.attach_media(locked, "scn_root", PHOTO)
    with pytest.raises(OpError, match="travada"):
        media_edit.set_real(locked, "scn_root", None)


def test_update_media_fields():
    doc = _with_media(_doc())
    patched = media_edit.update_media(doc, "scn_root", {
        "kind": "official", "credit": "  Rockstar    Games " + "x" * 200, "editorial": True, "focus": "top_left",
        "fit": "contain", "outlet": "  IGN   Brasil ", "published": "2025-05-06T10:00:00Z",
        "headline": '"Uma manchete bem comprida que passa de catorze palavras para ver o corte no fim dela agora"',
    })  # fmt: skip
    media = patched.scene("scn_root").visual.media
    assert media.kind == "official" and media.editorial is True and media.focus == "top_left"
    assert media.credit.startswith("Rockstar Games x") and len(media.credit) == 120
    assert media.fit == "contain" and media.outlet == "IGN Brasil" and media.published == "2025-05-06"
    assert media.headline["pt-BR"].endswith("…") and len(media.headline["pt-BR"].split()) == 14
    assert not media.headline["pt-BR"].startswith('"')
    again = media_edit.update_media(patched, "scn_root", {"fit": "auto", "focus": "nada", "headline": "Headline",
                                                          "lang": "en-US"})
    media = again.scene("scn_root").visual.media
    assert media.fit is None and media.focus is None and media.headline["en-US"] == "Headline"
    cleared = media_edit.update_media(again, "scn_root", {"headline": "", "published": ""})
    media = cleared.scene("scn_root").visual.media
    assert "pt-BR" not in media.headline and "en-US" in media.headline and media.published == ""
    with pytest.raises(OpError, match="data inválida"):
        media_edit.update_media(doc, "scn_root", {"published": "ontem"})
    with pytest.raises(OpError):
        media_edit.update_media(doc, "scn_root", {"fit": "esticar"})
    with pytest.raises(OpError):
        media_edit.update_media(doc, "scn_root", {"kind": "video"})


def test_set_real_and_scene_label():
    doc = _doc()
    hinted = media_edit.set_real(doc, "scn_r1", RealHint(kind="photo", query="Miami Beach"))
    assert hinted.scene("scn_r1").real.query == "Miami Beach"
    assert media_edit.set_real(hinted, "scn_r1", None).scene("scn_r1").real is None
    assert media_edit.scene_label(doc, "scn_root") == "Cena 1" and media_edit.scene_label(doc, "scn_r2") == "Cena 4"


# --- operações de cena com mídia ---------------------------------------------------------------


def test_regenerate_scene_with_media_is_refused():
    doc = _with_media(_doc())
    with pytest.raises(OpError, match="tire a mídia"):
        _apply(doc, {"op": "regenerate_scene", "scene_id": "scn_root"})
    # quem reaproveita a cena com mídia pode pedir imagem de IA própria: vem a reserva da raiz, sem a mídia
    own = _apply(doc, {"op": "regenerate_scene", "scene_id": "scn_r1"}).scene("scn_r1")
    assert own.visual.media is None and own.visual.reuse_of is None and own.visual.prompt == "a city at dusk"
    assert own.visual.variation == 1


def test_own_visual_from_a_media_scene_uses_its_reserve():
    doc = _with_media(_doc(), "scn_r1")  # scn_r1 reaproveitava scn_root antes da mídia
    own = _apply(doc, {"op": "regenerate_scene", "scene_id": "scn_r2"}).scene("scn_r2")
    assert own.visual.media is None and own.visual.prompt == "a city at dusk"


def test_split_scene_reuses_the_media():
    doc = _with_media(_doc())
    split = _apply(doc, {"op": "split_scene", "scene_id": "scn_root"})
    first, second = split.scenes_of("seg_a")[:2]
    assert first.visual.media == PHOTO and second.visual.reuse_of == first.id and second.visual.media is None
    assert split.shown_media(second) == PHOTO


def test_deleting_the_media_root_passes_the_media_to_the_heir():
    doc = _with_media(_doc())
    out = _apply(doc, {"op": "delete_segment", "segment_id": "seg_a"})
    heir, rest = out.scene("scn_r1"), out.scene("scn_r2")
    assert heir.visual.media == PHOTO and heir.visual.reuse_of is None and heir.visual.prompt == "a city at dusk"
    assert rest.visual.reuse_of == "scn_r1" and out.shown_media(rest) == PHOTO


def test_deleting_the_reserve_of_a_media_scene_keeps_a_valid_reserve():
    doc = _with_media(_doc(), "scn_r1")  # mídia na cena que reaproveitava scn_root
    out = _apply(doc, {"op": "delete_segment", "segment_id": "seg_a"})
    scene = out.scene("scn_r1")
    assert scene.visual.media == PHOTO and scene.visual.reuse_of != "scn_r1"
    back = media_edit.remove_media(out, "scn_r1")
    assert back.reuse_root(back.scene("scn_r1")) is not None  # sem a mídia, volta à imagem de reserva
    assert back.scene("scn_r1").visual.prompt == "a city at dusk"


def test_set_scene_kind_and_prompt_keep_the_media():
    doc = _with_media(_doc(), "scn_card", MediaRef(kind="news", asset=None, outlet="IGN"))
    doc.scene("scn_card").visual.type = "none"
    image = _apply(doc, {"op": "set_scene_kind", "scene_id": "scn_card", "kind": "image"})
    assert image.scene("scn_card").kind == "image" and image.scene("scn_card").visual.media.outlet == "IGN"
    prompt = _apply(_with_media(_doc()), {"op": "update_scene_prompt", "scene_id": "scn_root", "prompt": "outro"})
    assert prompt.scene("scn_root").visual.prompt == "outro" and prompt.scene("scn_root").visual.media == PHOTO
