"""Créditos da mídia real na publicação: lista "Créditos das imagens", descrição do YouTube e pacote manual.
Sem mídia real, tudo sai como antes."""

from __future__ import annotations

from pathlib import Path

from studio.core.project import service
from studio.core.project.document import MediaRef, Metadata, ProjectDoc, Scene, Segment, Visual
from studio.db import session_scope
from studio.media.real_media import EDITORIAL_WARNING
from studio.models import Publication
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import package
from studio.workers.publish import youtube as yt
from studio.workers.publish.credits import credit_lines, credits_block, has_editorial, media_credits
from studio.workers.publish.package import build_package, caption_for
from studio.workers.publish_tasks import _description, youtube_localizations

COMMONS = MediaRef(kind="photo", source="commons", asset="uploads/p/media/aaaaaaaaaaaaaaaa.jpg",
                   title="Miami skyline from the ocean", author="Matthew T Rader", license="CC BY-SA 4.0",
                   license_url="https://creativecommons.org/licenses/by-sa/4.0",
                   page_url="https://commons.wikimedia.org/wiki/File:Miami_skyline_from_the_ocean.jpg",
                   credit="Matthew T Rader / Wikimedia Commons · CC BY-SA 4.0",
                   candidate_id="commons:File:Miami skyline from the ocean.jpg")
CC0 = MediaRef(kind="photo", source="openverse", asset="uploads/p/media/bbbbbbbbbbbbbbbb.jpg", title="Ocean Drive",
               license="CC0", page_url="https://stocksnap.io/photo/ocean-drive", candidate_id="openverse:1")
NEWS = MediaRef(kind="news", source="link", asset=None, outlet="IGN Brasil", published="2025-05-06",
                headline={"pt-BR": "Trailer 2 bate recorde", "en-US": "Trailer 2 breaks a record"},
                page_url="https://br.ign.com/gta", credit="IGN Brasil", editorial=True)
UPLOAD_BARE = MediaRef(kind="photo", source="upload", asset="uploads/p/media/cccccccccccccccc.jpg")
UPLOAD_OFFICIAL = MediaRef(kind="official", source="upload", asset="uploads/p/media/dddddddddddddddd.jpg",
                           credit="Rockstar Games", editorial=True)


def doc_with(media: list[MediaRef | None], **extra) -> ProjectDoc:
    seg = Segment(id="seg_1", text={"pt-BR": "Texto."})
    scenes = []
    for i, item in enumerate(media):
        scenes.append(Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=len(media) + 1,
                            visual=Visual(prompt=f"p{i}", media=item)))
    scenes.append(Scene(id="scn_reuse", segment_id="seg_1", part=len(media), parts=len(media) + 1,
                        visual=Visual(reuse_of="scn_0")))
    return ProjectDoc(**{"id": "prj_pub", "title": "Publicar", "style_id": "misterios-short", "segments": [seg],
                         "scenes": scenes, **extra})


def test_credit_lines_order_dedupe_and_tasl():
    doc = doc_with([COMMONS, NEWS, CC0, COMMONS.model_copy(), UPLOAD_BARE, UPLOAD_OFFICIAL])
    assert credit_lines(doc, "pt-BR") == [
        "Miami skyline from the ocean — Matthew T Rader — CC BY-SA 4.0 (https://creativecommons.org/licenses/by-sa/4.0)"
        " — https://commons.wikimedia.org/wiki/File:Miami_skyline_from_the_ocean.jpg",
        "IGN Brasil, 6 mai. 2025 — “Trailer 2 bate recorde” — https://br.ign.com/gta",
        "Ocean Drive — CC0 — https://stocksnap.io/photo/ocean-drive",  # CC0 também mostra a fonte
        "Rockstar Games",
    ]  # a 2ª vez da mesma foto e o arquivo enviado sem dados não entram
    assert credit_lines(doc, "en-US")[1] == "IGN Brasil, May 6, 2025 — “Trailer 2 breaks a record” — https://br.ign.com/gta"
    assert has_editorial(doc) is True
    data = media_credits(doc, "en-US")
    assert data["heading"] == "Image credits" and data["editorial"] is True and len(data["lines"]) == 4
    assert data["text"].startswith("Image credits:\n- Miami skyline")
    assert credits_block(doc, "es-ES").startswith("Créditos de las imágenes:\n- ")
    assert credits_block(doc_with([None]), "pt-BR") == "" and not has_editorial(doc_with([None]))


def test_author_inside_the_title_is_not_repeated():
    media = COMMONS.model_copy(update={"title": "Foto de Matthew T Rader"})
    assert credit_lines(doc_with([media]), "pt-BR")[0].startswith("Foto de Matthew T Rader — CC BY-SA 4.0")


def test_youtube_description_puts_credits_before_hashtags_and_cuts_only_the_text():
    meta = Metadata(title="T", description="Resumo do vídeo.", hashtags=["#miami", "#gta"])
    assert _description(meta) == "Resumo do vídeo.\n\n#miami #gta"  # sem mídia: como sempre
    credits = credits_block(doc_with([COMMONS]), "pt-BR")
    text = _description(meta, credits)
    assert text == f"Resumo do vídeo.\n\n{credits}\n\n#miami #gta"
    long = Metadata(description="x" * 6000, hashtags=["#miami"])
    cut = _description(long, credits)
    assert len(cut) <= 5000 and cut.endswith(f"{credits}\n\n#miami") and cut.startswith("xxx")
    assert _description(Metadata(description="", hashtags=[]), credits) == credits


def test_localizations_get_the_credits_of_each_language():
    doc = doc_with([NEWS], languages=["pt-BR", "en-US"],
                   metadata={"pt-BR": Metadata(title="T", description="D"), "en-US": Metadata(title="T", description="D")})
    out = youtube_localizations(doc, "pt-BR")
    assert "Image credits:\n- IGN Brasil, May 6, 2025" in out["en-US"]["description"]


def test_captions_with_credits_per_platform():
    meta = {"title": "Título", "description": "Resumo.\n\nMais texto.", "hashtags": ["#a", "#b"]}
    credits = "Créditos das imagens:\n- Foto — CC0"
    assert caption_for("youtube", meta, "T", credits) == f"Resumo.\n\nMais texto.\n\n{credits}\n\n#a #b"
    assert caption_for("tiktok", meta, "T", credits) == f"Título\n\nResumo.\n\n{credits}\n\n#a #b"
    assert caption_for("x", meta, "T", credits) == caption_for("x", meta, "T")  # X e Threads: nunca
    assert caption_for("threads", meta, "T", credits) == caption_for("threads", meta, "T")
    huge = "Créditos das imagens:\n" + "\n".join(f"- linha {i} " + "y" * 60 for i in range(40))
    assert caption_for("instagram", meta, "T", huge) == caption_for("instagram", meta, "T")  # não cabe: fica sem
    for platform in package.LIMITS:
        assert caption_for(platform, meta, "T", "") == caption_for(platform, meta, "T")


def _rendered(isolated_settings, media: list[MediaRef | None]) -> str:
    doc = service.create_project(title="Pacote", style_id="misterios-short")
    seg = Segment(id="seg_1", text={"pt-BR": "Texto."})
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=len(media), visual=Visual(prompt="x", media=m))
              for i, m in enumerate(media)]
    meta = {"pt-BR": Metadata(title="Pacote", description="Resumo.", hashtags=["#a"])}
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes, "metadata": meta})
    service.commit(doc.id, doc, "pronto")
    out = isolated_settings.outputs_dir / doc.id / "pt-BR"
    out.mkdir(parents=True, exist_ok=True)
    (out / "video.mp4").write_bytes(b"mp4")
    service.update_settings(doc.id, {"last_final": {"langs": {"pt-BR": {"video": f"outputs/{doc.id}/pt-BR/video.mp4"}},
                                                    "preview_only_providers": []}})
    return doc.id


def test_package_writes_credits_and_editorial_note(isolated_settings):
    pid = _rendered(isolated_settings, [COMMONS, UPLOAD_OFFICIAL])
    result = build_package(pid, "pt-BR", ["youtube", "tiktok", "x"])
    folders = {item["platform"]: item["folder"] for item in result["items"]}
    credits = credits_block(service.get_doc(pid), "pt-BR")
    for platform, folder in folders.items():
        base = Path(folder)
        assert (base / "creditos.txt").read_text(encoding="utf-8") == credits + "\n"
        checklist = (base / "CHECKLIST.md").read_text(encoding="utf-8")
        assert f"**Atenção:** {EDITORIAL_WARNING}" in checklist
        caption = (base / "legenda.txt").read_text(encoding="utf-8")
        if platform == "x":
            assert credits not in caption and "cole o texto de creditos.txt na descrição." in checklist
        else:
            assert credits in caption and "Créditos das imagens: já estão na legenda." in checklist
    assert (Path(folders["youtube"]) / "legenda.txt").read_text(encoding="utf-8") == f"Resumo.\n\n{credits}\n\n#a"


def test_package_without_media_is_unchanged(isolated_settings):
    pid = _rendered(isolated_settings, [None])
    result = build_package(pid, "pt-BR", ["youtube", "tiktok"])
    for item in result["items"]:
        base = Path(item["folder"])
        assert not (base / "creditos.txt").exists()
        checklist = (base / "CHECKLIST.md").read_text(encoding="utf-8")
        assert "Créditos das imagens" not in checklist and "Content ID" not in checklist
    youtube = next(i for i in result["items"] if i["platform"] == "youtube")
    assert youtube["caption"] == "Resumo.\n\n#a"


def test_youtube_job_sends_the_credits_and_records_them(isolated_settings, monkeypatch):
    pid = _rendered(isolated_settings, [COMMONS, UPLOAD_OFFICIAL])
    sent: dict = {}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None):
        sent.update(snippet=snippet)
        return {"id": "vid123"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id, prefer=None: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private"}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    credits = credits_block(service.get_doc(pid), "pt-BR")
    assert sent["snippet"]["description"] == f"Resumo.\n\n{credits}\n\n#a"
    assert final["result"]["warnings"] == [EDITORIAL_WARNING]
    with session_scope() as s:
        details = s.query(Publication).filter_by(project_id=pid, platform="youtube").one().details
    assert details["credits"] == 2 and details["editorial"] is True
