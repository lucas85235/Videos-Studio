"""Acervo de material real antes do storyboard e anexo automático (ADR 0004 §3.4). Tudo offline.

Fixture em `fixtures/acervo/`: duas fotos sintéticas e uma captura de página (PNG + trechos com `rects`). Os dois
clipes são gerados aqui com o `lavfi` do ffmpeg (um corte conhecido em 2,0 s e som que nunca pode sobrar no proxy).
"""

from __future__ import annotations

import json
import shutil
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api import routes_media
from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import ProjectDoc, Scene, Segment, Visual
from studio.core.project.pool import Pool, PoolItem, load_pool, save_pool
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.media import clips, novelty, page_capture
from studio.media.ffmpeg import probe, run_ffmpeg
from studio.models import PageCapture
from studio.providers import media_search
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import media_find, media_pool
from studio.workers.visual import proofs

ACERVO = Path(__file__).parent / "fixtures" / "acervo"
LINK = "https://www.youtube.com/watch?v=trailer00001"
STYLE = "curiosidades-longo"


def lavfi_clip(path: Path, *, cut_at: float = 2.0, total: float = 4.0, first: str = "testsrc2",
               second: str = "color=c=0x2060c0") -> Path:
    """Vídeo 320x180 a 30 q/s com um corte em `cut_at` (dois geradores diferentes) e um tom de áudio."""
    path.parent.mkdir(parents=True, exist_ok=True)

    def gen(source: str, dur: float) -> str:
        return f"{source}{':' if '=' in source else '='}size=320x180:rate=30:duration={dur}"

    run_ffmpeg(["-f", "lavfi", "-i", gen(first, cut_at), "-f", "lavfi", "-i", gen(second, total - cut_at),
                "-f", "lavfi", "-i", f"sine=frequency=440:duration={total}",
                "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(path)], timeout=120)
    return path


@pytest.fixture
def no_network(monkeypatch):
    """Qualquer tentativa de rede falha o teste."""

    def boom(*args, **kwargs):
        raise AssertionError("o acervo offline tocou a rede")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)


def _project(title: str = "O farol", style_id: str = STYLE) -> ProjectDoc:
    doc = service.create_project(title=title, style_id=style_id, topic="o farol da Ilha Rasa")
    seg = Segment(id="seg_1", text={"pt-BR": "O Farol da Ilha Rasa voltou a acender. A Marinha confirmou. "
                                            "O Farol da Ilha Rasa tem 150 anos."})
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=5, visual=Visual(prompt=f"farol {i}", seed=i))
              for i in range(5)]
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes})
    service.commit(doc.id, doc, "cenas")
    return service.get_doc(doc.id)


def _seed_capture(project_id: str) -> str:
    """Uma prova já capturada no projeto (como `capture_proof` deixa): PNG da página e os trechos."""
    meta = json.loads((ACERVO / "captura.json").read_text(encoding="utf-8"))
    rel = f"uploads/{project_id}/captures/cap_teste.png"
    dst = get_settings().data_dir / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ACERVO / "captura.png", dst)
    with Image.open(dst) as im:
        img = im.convert("RGB")
    phrases = proofs.compute_views(img, meta["phrases"])
    with session_scope() as s:
        s.add(PageCapture(id="cap_teste", project_id=project_id, scene_id=None, url=meta["url"], domain=meta["domain"],
                          title=meta["title"], image=rel, proof="", width=img.width, height=img.height, dark=False,
                          phrases=phrases, status="ok"))
    return "cap_teste"


def _seed_project_files(project_id: str, tmp_path: Path) -> None:
    folder = get_settings().uploads_dir / project_id / media_pool.OWN_DIR
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "ilha.jpg")
    lavfi_clip(folder / "mergulho.mp4", first="testsrc", second="smptebars")


def _seed_link(project_id: str) -> None:
    """Um link oficial já baixado (o cache do yt-dlp), para a coleta offline cortar os planos sem rede."""
    folder = clips.clips_dir() / "src" / clips.link_key(LINK)
    lavfi_clip(folder / "video.mp4")
    (folder / "title.txt").write_text("Trailer do farol", encoding="utf-8")
    service.update_settings(project_id, {media_pool.LINKS_KEY: [{"url": LINK, "label": "", "tier": "official"}]})


# --- funções puras --------------------------------------------------------------------------------------------------


def test_cut_splits_long_shots_and_drops_micro_shots():
    shots = clips.cut([1.0, 2.3, 2.6, 5.0], 10.0, max_clip_s=4.0)
    assert [(s["k"], s["a"], s["dur"]) for s in shots] == [
        (1, 0.0, 1.0), (2, 1.0, 1.3), (4, 2.6, 2.4), (5, 5.0, 2.5), (5, 7.5, 2.5)]  # 2,3→2,6 é micro e sai
    assert all(s["dur"] <= 4.0 for s in shots)
    spread = clips.cut([float(t) for t in range(1, 60)], 60.0, max_clip_s=4.0, max_shots=5)
    assert len(spread) == 5 and spread[0]["a"] == 0.0 and spread[-1]["a"] == 59.0  # espalhados até o fim


def test_topics_come_from_names_the_script_repeats():
    doc = ProjectDoc(id="prj_x", title="GTA", style_id=STYLE, topic="o próximo GTA", segments=[
        Segment(id="s1", text={"pt-BR": "O GTA 6 sai em 2026. A Rockstar Games confirmou a data."}),
        Segment(id="s2", text={"pt-BR": "No trailer, a Lucia aparece em Vice City. O GTA 6 promete."}),
    ])
    labels = [t.label for t in media_pool.topics_of(doc)]
    assert labels[0] == "GTA 6"  # o mais citado primeiro
    assert {"Rockstar Games", "Lucia", "Vice City"} <= set(labels)
    assert not any(label.split()[0] in ("O", "A", "No") for label in labels)


def test_prompt_block_groups_by_topic_and_respects_the_line_budget():
    items = [PoolItem(id=f"p{i}", tier="official" if i % 2 else "free", media="clip" if i % 2 else "image",
                      desc=f"item {i}", dur_s=2.5 if i % 2 else None, topic="GTA 6" if i < 6 else "Lucia")
             for i in range(1, 11)]
    block = media_pool.prompt_block(Pool(items=items))
    assert block.splitlines()[0] == "[GTA 6]" and "[Lucia]" in block
    assert "p1 · official · clip · item 1 · 2.5s" in block and "p2 · free · image · item 2 · -" in block
    short = media_pool.prompt_block(Pool(items=items), max_lines=6)
    lines = short.splitlines()
    assert len(lines) <= 6 and lines[-1].startswith("… mais") and "[Lucia]" in short  # os dois tópicos aparecem
    assert media_pool.prompt_block(Pool()) == ""


def test_novelty_penalty_by_image_hash_and_address():
    farol = novelty.hash_file(ACERVO / "farol.jpg")
    items = [{"source_id": "acervo:farol.jpg", "dhash": farol, "score": 1.0},
             {"source_id": "acervo:ilha.jpg", "dhash": novelty.hash_file(ACERVO / "ilha.jpg"), "score": 1.0},
             {"source_id": "commons:File:X.jpg", "url": "https://upload.example/x.jpg", "score": 0.7}]
    hit = media_pool.apply_novelty(items, [farol], {"https://upload.example/x.jpg"})
    assert hit == 2
    assert items[0]["score"] == 0.5 and "repetido" in items[0]["tags"]
    assert items[1]["score"] == 1.0 and "tags" not in items[1]
    assert items[2]["score"] == pytest.approx(0.2)


def test_page_capture_returns_clean_image_and_rects():
    meta = json.loads((ACERVO / "captura.json").read_text(encoding="utf-8"))
    with Image.open(ACERVO / "captura.png") as im:
        img = im.convert("RGB")
    phrases = proofs.compute_views(img, meta["phrases"])
    clean, rects = proofs.clean_proof(img, phrases, 16 / 9)
    marked = proofs.bake(img, phrases, False, 16 / 9)
    assert clean.size == marked.size and len(rects) == 1
    x, y, w, h = rects[0]
    assert 0 <= x < 1 and 0 <= y < 1 and 0 < w <= 1 - x + 1e-6 and 0 < h <= 1 - y + 1e-6
    W, H = clean.size
    box = (int(x * W), int(y * H), int((x + w) * W), int((y + h) * H))

    def yellow(image: Image.Image) -> int:
        return sum(1 for r, g, b in image.crop(box).getdata() if r > 200 and g > 180 and b < 140)

    assert yellow(marked) > 0 and yellow(clean) == 0  # o destaque não é queimado no PNG do acervo
    # o retângulo cai em cima do texto do trecho
    ink = page_capture.ink_mask(clean.crop(box))
    assert ink.mean() > 0.2


# --- clipes ----------------------------------------------------------------------------------------------------------


def test_clips_cut_lavfi_with_known_cut(tmp_path):
    src = lavfi_clip(tmp_path / "corte.mp4")
    times = clips.detect_cuts(src)
    assert any(abs(t - 2.0) <= 1 / 30 + 1e-6 for t in times)
    shots = clips.analyze(src, width=320, height=180, fps=30, max_clip_s=1.5, label="Trailer")
    assert [s.k for s in shots] == [1, 1, 2, 2]
    assert shots[2].a == pytest.approx(2.0, abs=1 / 30 + 1e-3)
    assert all(s.dur <= 1.5 for s in shots) and all(s.desc.startswith("Trailer") for s in shots)
    root = get_settings().data_dir
    info = probe(root / shots[0].proxy)
    kinds = [st["codec_type"] for st in info["streams"]]
    assert kinds == ["video"]  # mudo: o som original nunca entra
    video = info["streams"][0]
    assert (video["width"], video["height"], video["codec_name"]) == (320, 180, "h264")
    assert video["r_frame_rate"] == "30/1"
    assert (root / shots[0].poster).is_file()
    again = clips.analyze(src, width=320, height=180, fps=30, max_clip_s=1.5, label="Trailer",
                          cuts_of=lambda p: (_ for _ in ()).throw(AssertionError("refez os cortes")))
    assert again == shots  # cache pelo arquivo e pelos parâmetros


def test_clip_hold_mode_freezes_the_last_frame(tmp_path):
    src = lavfi_clip(tmp_path / "hold.mp4")
    out = clips.make_proxy(src, 0.5, 1.0, tmp_path / "hold-proxy.mp4", width=320, height=180, fps=30, hold_s=1.0)
    dur = float(probe(out)["format"]["duration"])
    assert dur == pytest.approx(2.0, abs=0.1)  # ida de 1 s e o último quadro parado por mais 1 s


# --- coleta ----------------------------------------------------------------------------------------------------------


def test_gather_offline_collects_own_capture_and_official_without_network(isolated_settings, tmp_path):
    doc = _project()
    _seed_project_files(doc.id, tmp_path)
    _seed_capture(doc.id)
    _seed_link(doc.id)
    style = get_style(STYLE)
    with pytest.MonkeyPatch.context() as mp:
        def boom(*args, **kwargs):
            raise AssertionError("o acervo offline tocou a rede")

        mp.setattr(socket.socket, "connect", boom)
        mp.setattr(socket, "getaddrinfo", boom)
        mp.setattr(httpx.Client, "send", boom)
        pool = media_pool.gather(doc, style)
    assert pool.warnings == []
    tiers = {i.tier for i in pool.items}
    assert {"own", "capture", "official"} <= tiers
    assert len({i.id for i in pool.items}) == len(pool.items)
    capture = next(i for i in pool.items if i.media == "capture")
    assert capture.rects and capture.path and capture.page_url.startswith("https://example.org")
    official = [i for i in pool.items if i.tier == "official"]
    assert official and all(i.media == "clip" and i.dur_s and i.dur_s <= 4.0 and i.editorial for i in official)
    assert all(i.url == LINK and i.desc.startswith("Trailer do farol") for i in official)
    own_clips = [i for i in pool.items if i.tier == "own" and i.media == "clip"]
    assert own_clips and all(i.poster for i in own_clips)
    own_images = [i for i in pool.items if i.tier == "own" and i.media == "image"]
    assert len(own_images) == 2 and all(i.dhash for i in own_images)
    block = media_pool.prompt_block(pool)
    assert " · official · clip · " in block and " · capture · capture · " in block


def test_gather_offline_notes_links_not_downloaded_yet(isolated_settings, no_network):
    doc = _project()
    service.update_settings(doc.id, {media_pool.LINKS_KEY: [{"url": LINK}]})
    pool = media_pool.gather(doc, get_style(STYLE))
    assert pool.items == []
    assert any("offline" in w and "link" in w for w in pool.warnings)


def test_gather_keeps_ids_stable_between_runs(isolated_settings, tmp_path):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    first = media_pool.gather(doc, get_style(STYLE))
    save_pool(doc.id, first)
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "a-ilha.jpg")  # vem antes na ordem alfabética
    second = media_pool.gather(doc, get_style(STYLE))
    ids = {i.source_id: i.id for i in second.items}
    assert ids["acervo:farol.jpg"] == first.items[0].id
    assert ids["acervo:a-ilha.jpg"] != first.items[0].id


def test_gather_penalizes_material_from_recent_videos(isolated_settings, monkeypatch):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "ilha.jpg")
    monkeypatch.setattr(media_pool, "history_marks", lambda d: ([novelty.hash_file(ACERVO / "farol.jpg")], set()))
    pool = media_pool.gather(doc, get_style(STYLE))
    farol = next(i for i in pool.items if i.source_id == "acervo:farol.jpg")
    ilha = next(i for i in pool.items if i.source_id == "acervo:ilha.jpg")
    assert farol.score < ilha.score and "repetido" in farol.tags
    assert [i.source_id for i in pool.items].index("acervo:ilha.jpg") < [i.source_id for i in pool.items].index(
        "acervo:farol.jpg")


def test_channel_library_items(isolated_settings, no_network):
    from studio.media import thumb_assets

    kit = thumb_assets.thumbkit_dir("canal_teste")
    (kit / "frames").mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", kit / "frames" / "farol.jpg")
    shutil.copyfile(ACERVO / "ilha.jpg", kit / "frames" / "logo.jpg")
    thumb_assets.save_manifest(kit, {"version": 1, "channel_id": "canal_teste", "videos": {}, "items": [
        {"id": "farol-noite", "kind": "frame", "file": "frames/farol.jpg", "tags": ["Ilha Rasa"], "editorial": True,
         "credit": "Marinha"},
        {"id": "logo", "kind": "logo", "file": "frames/logo.jpg", "tags": []},
    ]})
    doc = ProjectDoc(id="prj_kit", title="Farol", style_id=STYLE, channel_id="canal_teste", segments=[
        Segment(id="s1", text={"pt-BR": "O farol fica na Ilha Rasa. A Ilha Rasa é longe."})])
    pool = media_pool.gather(doc, get_style(STYLE))
    assert [(i.tier, i.source_id, i.topic, i.credit) for i in pool.items] == [
        ("official", "kit:farol-noite", "Ilha Rasa", "Marinha")]  # o logo não serve de cena


def test_online_collectors_are_skipped_offline(isolated_settings, no_network, monkeypatch):
    called = []
    monkeypatch.setattr(media_find, "_ask_llm", lambda *a, **k: called.append(1))
    doc = _project()
    assert media_find.find_for_topics(doc, [("t1", "Farol", "lighthouse")]) == {}
    assert media_search.search_videos("lighthouse", orientation="landscape") == []
    assert media_pool.gather(doc, get_style(STYLE)).items == []
    assert called == []


# --- anexo -----------------------------------------------------------------------------------------------------------


def _pool_and_doc(tmp_path: Path) -> tuple[ProjectDoc, Pool]:
    doc = _project()
    _seed_project_files(doc.id, tmp_path)
    _seed_capture(doc.id)
    _seed_link(doc.id)
    pool = media_pool.gather(doc, get_style(STYLE))
    save_pool(doc.id, pool)
    return doc, pool


def test_attach_chosen_attaches_and_fills_the_tier(isolated_settings, tmp_path, no_network):
    doc, pool = _pool_and_doc(tmp_path)
    own = next(i for i in pool.items if i.tier == "own" and i.media == "image")
    cap = next(i for i in pool.items if i.media == "capture")
    clip = next(i for i in pool.items if i.tier == "official")
    scenes = doc.ordered_scenes()
    for scene, item in zip(scenes, (own, cap, clip), strict=False):
        scene.visual.media = media_pool.pending_ref(item)
    scenes[3].visual.ai_reason = "conceito: o farol daqui a cem anos"
    scenes[4].visual.media = media_pool.pending_ref(own).model_copy(update={"pool_id": "p999"})
    warnings = media_pool.attach_chosen(doc, pool)
    data_dir = get_settings().data_dir
    a, b, c = (s.visual.media for s in scenes[:3])
    assert a is not None and a.tier == "own" and a.pool_id == own.id and a.kind == "photo" and a.source == "upload"
    assert (data_dir / a.asset).is_file() and a.asset.startswith(f"uploads/{doc.id}/media/")
    assert b is not None and b.kind == "capture" and b.tier == "capture" and b.rects == cap.rects
    assert c is not None and c.kind == "clip" and c.tier == "official" and c.editorial and c.hold and c.mute
    assert c.video and c.video.startswith(f"uploads/{doc.id}/clips/") and (data_dir / c.video).is_file()
    assert c.dur_s == clip.dur_s and c.source == "youtube" and (data_dir / c.asset).is_file()
    assert scenes[4].visual.media is None  # id que não existe: a cena volta à imagem de reserva
    assert any("p999" in w for w in warnings)
    # a cena 4 é IA com motivo; a 5 caiu na reserva de IA sem motivo
    faltou = [w for w in warnings if w.startswith("faltou_real")]
    assert len(faltou) == 1 and "1 cena" in faltou[0] and "Cena 5" in faltou[0]
    # o documento continua válido e grava
    service.commit(doc.id, ProjectDoc.model_validate(doc.model_dump()), "anexo do acervo")


def test_attach_falls_back_to_the_same_topic(isolated_settings, tmp_path, no_network):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "ilha.jpg")
    rel = f"uploads/{doc.id}/{media_pool.OWN_DIR}/ilha.jpg"
    pool = Pool(items=[
        PoolItem(id="p1", tier="official", media="image", path=f"uploads/{doc.id}/sumiu.jpg", topic="Farol",
                 source_id="kit:sumiu"),
        PoolItem(id="p2", tier="own", media="image", path=rel, topic="Farol", source_id="acervo:ilha.jpg"),
        PoolItem(id="p3", tier="own", media="image", path=rel, topic="Outro", source_id="acervo:outra"),
    ])
    scene = doc.ordered_scenes()[0]
    scene.visual.media = media_pool.pending_ref(pool.items[0])
    warnings = media_pool.attach_chosen(doc, pool)
    media = scene.visual.media
    assert media is not None and media.pool_id == "p2" and media.tier == "own"
    assert any("entrou p2" in w for w in warnings)


def test_faltou_real_when_real_was_available_and_quiet_with_reason(isolated_settings):
    doc = _project()
    pool = Pool(items=[PoolItem(id="p1", tier="own", path="uploads/x/y.jpg", source_id="acervo:y")])
    warnings = media_pool.attach_chosen(doc, pool)
    assert len(warnings) == 1 and warnings[0].startswith("faltou_real: 5 cena(s)")
    assert [s.id for s in media_pool.missing_real(doc)] == [s.id for s in doc.ordered_scenes()]
    assert media_pool.attach_chosen(doc, Pool()) == []  # sem acervo: o achado fica para a revisão das cenas
    for scene in doc.scenes:
        scene.visual.ai_reason = "hipótese"
    assert media_pool.attach_chosen(doc, pool) == [] and media_pool.missing_real(doc) == []


def test_locked_scene_is_not_touched(isolated_settings, tmp_path):
    doc = _project()
    item = PoolItem(id="p1", tier="own", media="image", path="uploads/x/y.jpg", source_id="acervo:y")
    scene = doc.ordered_scenes()[0]
    scene.visual.media = media_pool.pending_ref(item)
    scene.locked = True
    media_pool.attach_chosen(doc, Pool(items=[item]))
    assert scene.visual.media is not None and scene.visual.media.asset is None


# --- Pexels (vídeos) -------------------------------------------------------------------------------------------------

PEXELS_VIDEOS = {"videos": [{
    "id": 123, "width": 3840, "height": 2160, "duration": 12, "url": "https://www.pexels.com/video/farol-na-costa-123/",
    "image": "https://images.pexels.com/videos/123/thumb.jpg", "user": {"name": "Ana"},
    "video_files": [
        {"id": 1, "quality": "sd", "file_type": "video/mp4", "width": 960, "height": 540, "link": "https://v.example/sd.mp4"},
        {"id": 2, "quality": "hd", "file_type": "video/mp4", "width": 1920, "height": 1080, "link": "https://v.example/hd.mp4"},
        {"id": 3, "quality": "uhd", "file_type": "video/mp4", "width": 3840, "height": 2160, "link": "https://v.example/4k.mp4"},
    ]}]}


def test_pexels_videos_parse_cache_and_rate_limit(isolated_settings, monkeypatch):
    isolated_settings.offline = False
    monkeypatch.setenv("PEXELS_API_KEY", "chave-teste")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=PEXELS_VIDEOS)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    found = media_search.search_videos("lighthouse", orientation="landscape", http=http)
    assert len(found) == 1
    v = found[0]
    assert v.id == "pexels-video:123" and v.download_url == "https://v.example/hd.mp4"  # o menor que cobre 1920
    assert v.title == "farol na costa" and v.credit.startswith("Ana") and v.duration == 12
    assert calls[0].url.path == "/videos/search" and calls[0].headers["Authorization"] == "chave-teste"
    assert media_search.search_videos("lighthouse", orientation="landscape", http=http) == found  # do cache
    assert len(calls) == 1
    monkeypatch.setattr(media_search, "PEXELS_HOURLY_LIMIT", 0)
    assert media_search.search_videos("outra busca", orientation="landscape", http=http) == []
    assert len(calls) == 1  # no teto da hora, nem pede


# --- online com a rede falsa: fotos livres, vídeos do Pexels e arte oficial; anexo de item remoto -----------------


def test_online_gather_and_attach_with_fake_network(isolated_settings, monkeypatch, tmp_path):
    from types import SimpleNamespace

    from test_real_media_routes import OFFICIAL_HTML, Net, jpeg

    from studio.media import safe_http

    video_bytes = lavfi_clip(tmp_path / "pexels.mp4").read_bytes()

    class FakeNet(Net):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            host, path = request.url.host, request.url.path
            if host == "api.pexels.com":
                self.requests.append(request)
                return httpx.Response(200, json=PEXELS_VIDEOS if path == "/videos/search" else {"photos": []})
            if host == "v.example":
                self.requests.append(request)
                return httpx.Response(200, headers={"content-type": "video/mp4"}, content=video_bytes)
            if host == "www.marinha.example":
                self.requests.append(request)
                return httpx.Response(200, headers={"content-type": "text/html"}, text=OFFICIAL_HTML)
            if host == "cdn.example.com":
                self.requests.append(request)
                return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=jpeg(1600, 900))
            return super().__call__(request)

    net = FakeNet()
    isolated_settings.offline = False
    monkeypatch.setenv("PEXELS_API_KEY", "chave-teste")
    monkeypatch.setattr(media_search, "new_client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(net)))
    monkeypatch.setattr(safe_http, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(net)))
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    monkeypatch.setattr(media_find, "web_search_status", lambda: (True, "ok"))
    asked: list[dict] = []

    def ask(ctx, project_id, job_id):
        asked.append(ctx)
        return SimpleNamespace(data={"scenes": [{"scene_id": "t1", "candidates": [{
            "image": "https://cdn.example.com/farol.jpg", "page_url": "https://www.marinha.example/farol",
            "title": "Farol aceso", "outlet": "Marinha", "why": "mostra o farol"}]}]})

    monkeypatch.setattr(media_find, "_ask_llm", ask)
    doc = _project()
    pool = media_pool.gather(doc, get_style(STYLE))
    assert len(asked) == 1 and len(asked[0]["hints"]) <= media_find.MAX_TOPICS  # uma chamada por vídeo
    assert asked[0]["hints"][0]["note"].startswith("Tópico do vídeo: Farol da Ilha Rasa")
    tiers = {(i.tier, i.media) for i in pool.items}
    assert {("free", "image"), ("stock", "clip"), ("official", "image")} <= tiers
    free = next(i for i in pool.items if i.source_id == "commons:File:Miami skyline from the ocean.jpg")
    video = next(i for i in pool.items if i.source_id == "pexels-video:123")
    official = next(i for i in pool.items if i.source_id.startswith("found:"))
    assert official.credit == "Marinha" and official.editorial and official.topic == "Farol da Ilha Rasa"
    # a mesma coleta de novo não chama o LLM (cache de 7 dias por tópicos)
    media_pool.gather(doc, get_style(STYLE))
    assert len(asked) == 1

    scenes = doc.ordered_scenes()
    for scene, item in zip(scenes, (free, video, official), strict=False):
        scene.visual.media = media_pool.pending_ref(item)
    warnings = media_pool.attach_chosen(doc, pool)
    a, b, c = (s.visual.media for s in scenes[:3])
    assert a is not None and a.source == "commons" and a.tier == "free" and a.asset and a.license
    assert b is not None and b.kind == "clip" and b.tier == "stock" and b.video and b.source == "pexels"
    assert b.dur_s == pytest.approx(4.0)  # o trecho do banco também respeita `official_max_clip_s`
    assert c is not None and c.kind == "official" and c.tier == "official" and c.editorial and c.asset
    assert not any("não baixou" in w for w in warnings)


# --- rotas e job -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def test_pool_routes(isolated_settings, client, monkeypatch):
    doc = _project()
    assert client.get("/api/projects/prj_nao_existe/pool").status_code == 404
    empty = client.get(f"/api/projects/{doc.id}/pool").json()
    assert empty["pool"]["items"] == [] and empty["links"] == [] and empty["summary"]["items"] == 0
    bad = client.post(f"/api/projects/{doc.id}/pool/links", json={"links": [{"url": "ftp://example.org/x.mp4"}]})
    assert bad.status_code == 422
    ok = client.post(f"/api/projects/{doc.id}/pool/links", json={"links": [{"url": LINK, "label": "Trailer 1"}]})
    assert ok.status_code == 200 and ok.json()["links"] == [{"url": LINK, "label": "Trailer 1", "tier": "official"}]
    more = client.post(f"/api/projects/{doc.id}/pool/links",
                       json={"links": [{"url": "https://www.youtube.com/watch?v=gameplay0001", "tier": "own"}]})
    assert [link["url"] for link in more.json()["links"]] == [LINK, "https://www.youtube.com/watch?v=gameplay0001"]
    replaced = client.post(f"/api/projects/{doc.id}/pool/links", json={"links": [], "replace": True})
    assert replaced.json()["links"] == []
    submitted: list[tuple] = []

    class FakeRunner:
        def submit(self, kind, payload=None, project_id=None, **kw):
            submitted.append((kind, project_id))
            return {"id": "job_x", "kind": kind, "status": "queued"}

    monkeypatch.setattr(routes_media, "get_runner", lambda: FakeRunner())
    assert client.post(f"/api/projects/{doc.id}/pool/refresh").json()["id"] == "job_x"
    assert submitted == [("media_pool", doc.id)]


def test_media_pool_job_saves_the_pool(isolated_settings, tmp_path):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    job = get_runner().submit("media_pool", {}, doc.id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    assert final["result"]["items"] == 1 and final["result"]["by_tier"] == {"own": 1}
    assert load_pool(doc.id).items[0].source_id == "acervo:farol.jpg"


def test_storyboard_job_gathers_before_and_writes_the_pool(isolated_settings):
    doc = service.create_project(title="Farol", style_id="misterios-short", topic="o farol abandonado da ilha")
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    for kind in ("script", "storyboard"):
        job = get_runner().submit(kind, {}, doc.id, run_inline=True)
        final = get_job(job["id"])
        assert final is not None and final["status"] == "done", final and final["error"]
    pool = load_pool(doc.id)
    assert pool.items[0].tier == "own"
    # o storyboard leu o acervo gravado antes dele: o real entra (já anexado) e a IA que sobra vem com motivo, então o
    # anexo não acusa `faltou_real` (o aviso de IA sem motivo fica em `test_attach_*`/`missing_real`)
    shown = service.get_doc(doc.id)
    real = [s for s in shown.scenes if s.visual.media is not None and s.visual.media.pool_id == pool.items[0].id]
    assert real and all(s.visual.media.asset for s in real)
    assert not media_pool.missing_real(shown)
    assert not any(w.startswith("faltou_real") for w in final["result"].get("warnings", []))
