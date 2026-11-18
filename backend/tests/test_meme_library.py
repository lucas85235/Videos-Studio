"""Biblioteca de memes (ADR 0004 §3.8): licença obrigatória no upload, escolha determinística, reações do avatar e os
templates "meme sem meme"."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.core.project.document import PRESENTER_AT_RE, FxEvent, Presenter, PresenterPose
from studio.media import meme_library


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", (64, 64), (255, 0, 0, 128)).save(buf, "PNG")
    return buf.getvalue()


def test_upload_without_license_is_422(isolated_settings):
    client = TestClient(create_app())
    files = {"file": ("patrick.png", _png(), "image/png")}
    assert client.post("/api/library/memes", files=files, data={"tags": "choque"}).status_code == 422
    assert client.post("/api/library/memes", files=files, data={"license": "achei-na-net"}).status_code == 422
    assert client.post("/api/library/memes", files=files, data={"license": "cc_by"}).status_code == 422  # sem autor
    wrong = client.post("/api/library/memes", files={"file": ("x.gif", b"GIF89a", "image/gif")}, data={"license": "own"})
    assert wrong.status_code == 422
    assert meme_library.user_items() == []

    ok = client.post("/api/library/memes", files=files, data={"license": "own", "tags": "choque, Surpresa",
                                                               "max_ms": "900", "sfx_id": "boom"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["id"] == "patrick" and body["license"] == "own" and body["tags"] == ["choque", "surpresa"]
    assert body["file"] == "library/memes/patrick.png" and (isolated_settings.data_dir / body["file"]).is_file()
    listed = client.get("/api/library/memes").json()
    assert [i["id"] for i in listed["items"]] == ["patrick"]
    assert {t["id"] for t in listed["templates"]} == set(meme_library.TEMPLATES)
    assert client.delete("/api/library/memes/patrick").status_code == 200
    assert client.delete("/api/library/memes/patrick").status_code == 404
    assert not (isolated_settings.data_dir / body["file"]).exists()


def test_pick_is_deterministic_respects_exclude_and_risk(isolated_settings, tmp_path):
    src = tmp_path / "m.png"
    src.write_bytes(_png())
    for name in ("a", "b", "c"):
        meme_library.add(src, license="cc0", tags=["piada"], lib_id=name)
    meme_library.add(src, license="fair_comment", tags=["piada", "filme"], risk="high", lib_id="filme")
    first = meme_library.pick("piada", 3)
    assert first is not None and first.id in {"a", "b", "c"}
    assert meme_library.pick("Piada", 3).id == first.id
    other = meme_library.pick("piada", 3, exclude=[first.id])
    assert other is not None and other.id != first.id
    assert meme_library.pick("filme", 3) is None  # risco alto nunca entra no automático
    assert meme_library.pick("piada", 3, exclude=["a", "b", "c"]) is None
    assert meme_library.pick("nada", 3) is None


def test_presenter_poses_become_reactions(isolated_settings):
    presenter = Presenter(enabled=True, poses=[
        PresenterPose(id="choque", file="library/presenter/canal/choque.png", emotions=["surpresa"]),
        PresenterPose(id="base", file="library/presenter/canal/base.png"),
    ])
    reactions = meme_library.presenter_reactions(presenter)
    assert [r.id for r in reactions] == ["pose-choque", "pose-base"]
    assert all(r.license == "own" and r.origin == "presenter" for r in reactions)
    picked = meme_library.pick("surpresa", 1, presenter=presenter)
    assert picked is not None and picked.id == "pose-choque" and picked.file.endswith("choque.png")
    assert meme_library.presenter_reactions(Presenter(enabled=False, poses=presenter.poses)) == []


def test_meme_without_meme_templates_are_valid_events():
    for name in meme_library.TEMPLATES:
        events = meme_library.template_events(name, "chocante#2", line="pera...")
        assert events and all(isinstance(e, FxEvent) and PRESENTER_AT_RE.match(e.at) for e in events)
    freeze = meme_library.template_events("freeze_zoom", "@1200", line="O QUÊ?")
    assert [e.type for e in freeze] == ["freeze", "punch", "sfx", "text_pop"]
    assert freeze[1].params["scale"] == 1.3 and freeze[2].params["lib_id"] == "boom"
    assert freeze[3].params["text"] == "O QUÊ?"
    # sem fala, o texto sai e o resto fica
    assert [e.type for e in meme_library.template_events("freeze_zoom", "chocante")] == ["freeze", "punch", "sfx"]
    assert meme_library.template_for("pera", 1).id == "record_scratch"
    assert meme_library.template_for("tag-sem-template", 1).id in meme_library.TEMPLATES
    with pytest.raises(KeyError):
        meme_library.template_events("nao-existe", "x")
    with pytest.raises(ValueError):
        meme_library.template_events("freeze_zoom", "duas palavras")
