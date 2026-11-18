"""Acervo de material real antes do storyboard e anexo automático (ADR 0004 §3.4). Tudo offline.

Fixture em `fixtures/acervo/`: duas fotos sintéticas e uma captura de página (PNG + trechos com `rects`). Os dois
clipes são gerados aqui com o `lavfi` do ffmpeg (um corte conhecido em 2,0 s e som que nunca pode sobrar no proxy).
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api import routes_media
from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import MediaRef, ProjectDoc, Scene, Segment, Visual
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


def test_clips_cut_lavfi_with_known_cut(isolated_settings, tmp_path):
    src = lavfi_clip(get_settings().data_dir / "fontes" / "corte.mp4")
    times = clips.detect_cuts(src)
    assert any(abs(t - 2.0) <= 1 / 30 + 1e-6 for t in times)
    shots = clips.analyze(src, max_clip_s=1.5, label="Trailer")
    assert shots is not None
    assert [s.k for s in shots] == [1, 1, 2, 2]
    assert shots[2].a == pytest.approx(2.0, abs=1 / 30 + 1e-3)
    assert all(s.dur <= 1.5 for s in shots) and all(s.desc.startswith("Trailer") for s in shots)
    # o MockVision não chuta material nem humor: só o rótulo, o plano e o instante
    assert shots[2].desc == "Trailer · plano 2, 0:02" and all(s.material == "" for s in shots)
    root = get_settings().data_dir
    assert all((root / s.poster).is_file() for s in shots)
    assert (shots[0].width, shots[0].height) == (320, 180) and shots[0].src == "fontes/corte.mp4"
    assert not list(clips.clips_dir().rglob("*.mp4"))  # a análise não faz proxy nenhum
    again = clips.analyze(src, max_clip_s=1.5, label="Trailer",
                          cuts_of=lambda p: (_ for _ in ()).throw(AssertionError("refez os cortes")))
    assert again == shots  # cache pelo arquivo e pelos parâmetros
    other = clips.analyze(src, max_clip_s=1.5, label="Outro título",
                          cuts_of=lambda p: (_ for _ in ()).throw(AssertionError("refez os cortes")))
    assert other is not None and other[0].desc.startswith("Outro título")  # o rótulo só refaz a descrição
    assert clips.analyze(lavfi_clip(tmp_path / "novo.mp4", cut_at=1.0), max_clip_s=1.5, cached_only=True) is None
    # o proxy só sai para o plano escolhido, no tamanho final, mudo
    rel = clips.proxy_for_project(src, shots[0].a, shots[0].dur, "prj_x", width=320, height=180, fps=30)
    info = probe(root / rel)
    kinds = [st["codec_type"] for st in info["streams"]]
    assert kinds == ["video"]  # mudo: o som original nunca entra
    video = info["streams"][0]
    assert (video["width"], video["height"], video["codec_name"]) == (320, 180, "h264")
    assert video["r_frame_rate"] == "30/1" and rel.startswith("uploads/prj_x/clips/")


def test_vertical_proxy_keeps_the_whole_frame_over_a_blur(tmp_path):
    src = lavfi_clip(tmp_path / "deitado.mp4")
    assert clips.needs_blur(320, 180, 180, 320) and not clips.needs_blur(1920, 1080, 1280, 720)
    assert "boxblur" in clips.proxy_filter(180, 320, 30, fit="blur")
    out = clips.make_proxy(src, 0.0, 1.0, tmp_path / "em-pe.mp4", width=180, height=320, fps=30, fit="blur")
    video = probe(out)["streams"][0]
    assert (video["width"], video["height"]) == (180, 320)


def test_clip_hold_mode_freezes_the_last_frame(tmp_path):
    src = lavfi_clip(tmp_path / "hold.mp4")
    out = clips.make_proxy(src, 0.5, 1.0, tmp_path / "hold-proxy.mp4", width=320, height=180, fps=30, hold_s=1.0)
    dur = float(probe(out)["format"]["duration"])
    assert dur == pytest.approx(2.0, abs=0.1)  # ida de 1 s e o último quadro parado por mais 1 s


def test_cached_source_ignores_leftovers_of_a_failed_download(isolated_settings):
    folder = clips.source_dir(LINK)
    folder.mkdir(parents=True)
    (folder / "video.f137.mp4").write_bytes(b"so o video")
    (folder / "video.f251.webm").write_bytes(b"so o audio")
    assert clips.cached_source(LINK) is None  # sobra do yt-dlp não conta como baixado
    (folder / "video.mp4").write_bytes(b"x")
    assert clips.cached_source(LINK) is None  # sem title.txt o download não terminou
    calls: list[list[str]] = []

    def runner(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1]).parent
        (out / "video.f137.mp4").write_bytes(b"parcial")
        (out / "video.mp4").write_bytes(b"final")
        return subprocess.CompletedProcess(cmd, 0, stdout="Trailer oficial\nRockstar Games\n", stderr="")

    found = clips.fetch(LINK, runner=runner)
    assert len(calls) == 1 and found.path == folder / "video.mp4" and found.path.read_bytes() == b"final"
    assert (found.title, found.uploader) == ("Trailer oficial", "Rockstar Games")
    assert sorted(p.name for p in folder.iterdir()) == ["title.txt", "uploader.txt", "video.mp4"]
    assert clips.cached_source(LINK) == found
    with pytest.raises(clips.ClipError):
        clips.fetch("https://www.youtube.com/watch?v=outro0000001")  # offline sem runner: nunca chama o yt-dlp


def test_prune_sources_keeps_the_recent_and_the_kept(isolated_settings):
    import os
    import time

    for n, url in enumerate(("https://a.example/1", "https://a.example/2", "https://a.example/3")):
        clips.save_source(url, b"x" * 1000, title=f"v{n}")
        stamp = clips.source_dir(url) / "title.txt"
        os.utime(stamp, (time.time() - 1000 + n * 100,) * 2)
    keep = {clips.link_key("https://a.example/1")}
    freed = clips.prune_sources(max_bytes=2100, keep=keep)
    assert freed > 0
    assert clips.cached_source("https://a.example/1") is not None  # o mais velho, mas do projeto
    assert clips.cached_source("https://a.example/2") is None
    assert clips.cached_source("https://a.example/3") is not None  # o mais recente


# --- coleta ----------------------------------------------------------------------------------------------------------


def _no_net(mp: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise AssertionError("o acervo offline tocou a rede")

    mp.setattr(socket.socket, "connect", boom)
    mp.setattr(socket, "getaddrinfo", boom)
    mp.setattr(httpx.Client, "send", boom)


def test_gather_offline_collects_own_capture_and_official_without_network(isolated_settings, tmp_path):
    doc = _project()
    _seed_project_files(doc.id, tmp_path)
    _seed_capture(doc.id)
    _seed_link(doc.id)
    style = get_style(STYLE)
    with pytest.MonkeyPatch.context() as mp:
        _no_net(mp)
        quick = media_pool.gather(doc, style)  # rápida: os vídeos ainda não cortados ficam para a completa
        assert not any(i.media == "clip" for i in quick.items)
        assert any("não cortado" in w for w in quick.warnings)
        pool = media_pool.gather(doc, style, full=True)
        again = media_pool.gather(doc, style)  # rápida de novo: usa a análise que a completa deixou
    assert pool.warnings == []
    tiers = {i.tier for i in pool.items}
    assert {"own", "capture", "official"} <= tiers
    assert len({i.id for i in pool.items}) == len(pool.items)
    capture = next(i for i in pool.items if i.media == "capture")
    assert capture.rects and capture.path and capture.page_url.startswith("https://example.org") and capture.editorial
    official = [i for i in pool.items if i.tier == "official"]
    assert official and all(i.media == "clip" and i.dur_s and i.dur_s <= 4.0 and i.editorial for i in official)
    assert all(i.url == LINK and i.desc.startswith("Trailer do farol") for i in official)
    assert all(i.path == f"library/clips/src/{clips.link_key(LINK)}/video.mp4" and i.poster for i in official)
    own_clips = [i for i in pool.items if i.tier == "own" and i.media == "clip"]
    assert own_clips and all(i.poster for i in own_clips)
    own_images = [i for i in pool.items if i.tier == "own" and i.media == "image"]
    assert len(own_images) == 2 and all(i.dhash for i in own_images)
    assert [(i.source_id, i.desc) for i in again.items] == [(i.source_id, i.desc) for i in pool.items]
    block = media_pool.prompt_block(pool)
    assert " · official · clip · " in block and " · capture · capture · " in block


def test_gather_offline_notes_links_not_downloaded_yet(isolated_settings, no_network):
    doc = _project()
    service.update_settings(doc.id, {media_pool.LINKS_KEY: [{"url": LINK}]})
    for full in (False, True):
        pool = media_pool.gather(doc, get_style(STYLE), full=full)
        assert pool.items == []
        assert any("offline" in w and "link" in w for w in pool.warnings)


def test_offline_with_injected_http_never_opens_browser_or_ytdlp(isolated_settings, monkeypatch, no_network):
    from studio.core.project.document import Source

    doc = _project()
    doc = doc.model_copy(update={"sources": [Source(url="https://example.org/noticia", title="O Farol da Ilha Rasa")]})
    service.update_settings(doc.id, {media_pool.LINKS_KEY: [{"url": LINK}]})

    def boom(*a, **k):
        raise AssertionError("tocou a rede de verdade")

    monkeypatch.setattr(page_capture, "browser_status", boom)
    monkeypatch.setattr(proofs, "capture_clean", boom)
    monkeypatch.setattr(clips.reference_video, "ytdlp_path", boom)
    searched: list[str] = []
    monkeypatch.setattr(media_search, "search", lambda q, **k: searched.append(q) or media_search.SearchResult(
        query=q, candidates=[]))
    monkeypatch.setattr(media_search, "search_videos", lambda q, **k: [])
    pool = media_pool.gather(doc, get_style(STYLE), http=object(), full=True)
    assert searched  # o `http` injetado vale para as buscas httpx
    assert not any("tocou a rede" in w for w in pool.warnings)
    assert any("offline" in w for w in pool.warnings)


def test_quick_gather_keeps_the_source_captures_of_the_full_one(isolated_settings, monkeypatch, no_network):
    """O storyboard faz a coleta rápida: as fontes capturadas pela completa (cache em arquivo) não podem sumir."""
    from studio.core.project.document import Source

    doc = _project()
    doc = doc.model_copy(update={"sources": [Source(url="https://example.org/noticia", title="O Farol da Ilha Rasa")]})
    folder = get_settings().uploads_dir / doc.id / "captures"
    folder.mkdir(parents=True)
    Image.new("RGB", (1280, 720), "white").save(folder / "pool-x.png")
    entry = {"asset": f"uploads/{doc.id}/captures/pool-x.png", "rects": [[0.1, 0.1, 0.5, 0.2]], "width": 1280,
             "height": 720, "url": "https://example.org/noticia", "domain": "example.org",
             "title": "O Farol da Ilha Rasa", "text": "O Farol da Ilha Rasa"}
    asked: list[tuple[str, list[str]]] = []

    def cached(project_id, url, phrases, aspect):
        asked.append((url, list(phrases)))
        return entry

    monkeypatch.setattr(proofs, "cached_capture_clean", cached)
    monkeypatch.setattr(proofs, "capture_clean", lambda *a, **k: (_ for _ in ()).throw(AssertionError("abriu")))
    pool = media_pool.gather(doc, get_style(STYLE))
    captures = [i for i in pool.items if i.tier == "capture"]
    assert asked == [("https://example.org/noticia", ["O Farol da Ilha Rasa"])]
    assert len(captures) == 1 and captures[0].page_url == "https://example.org/noticia"


def test_cached_capture_clean_reads_only_what_was_captured(isolated_settings):
    import json as _json

    pid = "prj_cache"
    assert proofs.cached_capture_clean(pid, "https://a.example/x", ["Trecho"], 16 / 9) is None
    done = proofs._pool_capture_file(pid, "https://a.example/x", ["Trecho"], 16 / 9)
    done.parent.mkdir(parents=True)
    asset = f"uploads/{pid}/captures/pool-y.png"
    done.write_text(_json.dumps({"asset": asset}), encoding="utf-8")
    assert proofs.cached_capture_clean(pid, "https://a.example/x", ["Trecho"], 16 / 9) is None  # imagem sumiu
    Image.new("RGB", (10, 10)).save(get_settings().data_dir / asset)
    assert proofs.cached_capture_clean(pid, "https://a.example/x", ["  Trecho "], 16 / 9) == {"asset": asset}


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


def test_ids_are_never_given_again_when_the_pool_shrinks(isolated_settings):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        shutil.copyfile(ACERVO / "farol.jpg", folder / name)
    first = media_pool.gather(doc, get_style(STYLE))
    save_pool(doc.id, first)
    given = {i.source_id: i.id for i in first.items}
    (folder / "b.jpg").unlink()
    (folder / "c.jpg").unlink()
    only_a = media_pool.gather(doc, get_style(STYLE))
    save_pool(doc.id, only_a)  # o acervo encolheu (coleta offline, tópicos mudaram...)
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "d.jpg")
    third = media_pool.gather(doc, get_style(STYLE))
    ids = {i.source_id: i.id for i in third.items}
    assert ids["acervo:a.jpg"] == given["acervo:a.jpg"]
    assert ids["acervo:d.jpg"] not in (given["acervo:b.jpg"], given["acervo:c.jpg"])
    save_pool(doc.id, third)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "b.jpg")  # o mesmo arquivo de volta: o id de antes
    assert {i.source_id: i.id for i in media_pool.gather(doc, get_style(STYLE)).items}["acervo:b.jpg"] == given[
        "acervo:b.jpg"]
    # um pool_id citado numa cena nunca vai para um item novo, nem sem pool.json
    scene = doc.ordered_scenes()[0]
    scene.visual.media = media_pool.pending_ref(PoolItem(id="p40", tier="own", source_id="x"))
    ids_new, top = media_pool._assign_ids([{"source_id": "novo"}], Pool(), doc)
    assert ids_new["novo"] == "p41" and top == 42


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


def test_novelty_penalizes_only_the_used_shot_of_a_link(isolated_settings):
    key = clips.link_key(LINK)
    shots = [{"source_id": f"yt:{key}#{k}", "media": "clip", "url": LINK, "page_url": LINK, "score": 1.05}
             for k in (1, 2, 3)]
    found = [{"source_id": f"found:https://cdn.example/{n}.jpg", "url": f"https://cdn.example/{n}.jpg",
              "page_url": "https://news.example/materia", "score": 0.9} for n in (1, 2)]
    # o vídeo anterior usou o plano 2 do trailer e a imagem 1 da matéria
    refs = {f"yt:{key}#2", "https://cdn.example/1.jpg", LINK, "https://news.example/materia"}
    assert media_pool.apply_novelty(shots + found, [], refs) == 2
    assert [s["score"] for s in shots] == [1.05, pytest.approx(0.55), 1.05]
    assert [f["score"] for f in found] == [pytest.approx(0.4), 0.9]


def test_history_marks_use_the_shot_not_the_link(isolated_settings, monkeypatch):
    from studio.core.project.document import MediaRef
    from studio.workers import novelty as checker

    old = _project("Vídeo anterior")
    item = PoolItem(id="p3", tier="official", media="clip", url=LINK, source_id=f"yt:{clips.link_key(LINK)}#2")
    save_pool(old.id, Pool(items=[item]))
    scene = old.ordered_scenes()[0]
    scene.visual.media = MediaRef(kind="clip", source="youtube", asset="uploads/x/media/a.jpg",
                                  video="uploads/x/clips/a.mp4", url=LINK, page_url=LINK, pool_id="p3")
    service.commit(old.id, old, "cena com trecho")
    doc = _project().model_copy(update={"channel_id": "canal"})
    monkeypatch.setattr(checker, "_history", lambda *a: [old.id])
    monkeypatch.setattr(checker, "cached_fingerprint", lambda pid: None)
    _, refs = media_pool.history_marks(doc)
    assert refs == {item.source_id}  # nunca o endereço do trailer inteiro


def test_selection_keeps_own_and_official_when_online_fills_the_pool():
    topics = [media_pool.Topic(id=f"t{n}", label=f"Tópico {n}", query=f"t{n}") for n in range(15)]
    items = [{"source_id": f"stock:{n}:{k}", "tier": "stock", "media": "image", "topic": f"Tópico {n}",
              "score": 0.6 + k / 1000} for n in range(15) for k in range(20)]
    items += [{"source_id": f"acervo:{k}.jpg", "tier": "own", "media": "image", "topic": media_pool.TOPIC_FILES,
               "score": 1.0} for k in range(40)]
    items += [{"source_id": f"yt:x#{k}", "tier": "official", "media": "clip", "topic": "Trailer", "score": 0.95}
              for k in range(24)]
    kept = media_pool._select(items, topics)
    assert len(kept) == media_pool.MAX_ITEMS
    assert sum(i["tier"] == "own" for i in kept) == 40  # a pasta acervo/ passa de 30 arquivos
    assert sum(i["tier"] == "official" for i in kept) == 24
    assert [i["topic"] for i in kept][0] == "Tópico 0"  # a exibição segue a ordem do roteiro
    pool = Pool(items=[PoolItem(id=f"p{n}", **{k: v for k, v in i.items() if k != "score"}, score=i["score"])
                       for n, i in enumerate(kept, 1)])
    block = media_pool.prompt_block(pool, max_lines=20)
    assert f"[{media_pool.TOPIC_FILES}]" in block and "[Trailer]" in block  # o mais real tem lugar no pedido


def test_channel_library_items(isolated_settings, no_network):
    from studio.media import thumb_assets

    kit = thumb_assets.thumbkit_dir("canal_teste")
    (kit / "frames").mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", kit / "frames" / "farol.jpg")
    shutil.copyfile(ACERVO / "ilha.jpg", kit / "frames" / "logo.jpg")
    with Image.open(ACERVO / "ilha.jpg") as im:
        cut = im.convert("RGBA")
    cut.putpixel((0, 0), (0, 0, 0, 0))
    cut.save(kit / "frames" / "recorte.png")
    lavfi_clip(kit / "videos" / "Trailer_1.mp4")
    thumb_assets.save_manifest(kit, {"version": 1, "channel_id": "canal_teste",
                                     "videos": {"Trailer_1": "videos/Trailer_1.mp4"}, "items": [
        {"id": "farol-noite", "kind": "frame", "file": "frames/farol.jpg", "tags": ["Ilha Rasa"], "editorial": True,
         "credit": "Marinha"},
        {"id": "logo", "kind": "logo", "file": "frames/logo.jpg", "tags": []},
        {"id": "recorte", "kind": "object", "file": "frames/recorte.png", "tags": ["Ilha Rasa"]},
    ]})
    doc = ProjectDoc(id="prj_kit", title="Farol", style_id=STYLE, channel_id="canal_teste", segments=[
        Segment(id="s1", text={"pt-BR": "O farol fica na Ilha Rasa. A Ilha Rasa é longe."})])
    pool = media_pool.gather(doc, get_style(STYLE), full=True)
    images = [(i.tier, i.source_id, i.topic, i.credit) for i in pool.items if i.media == "image"]
    assert images == [("official", "kit:farol-noite", "Ilha Rasa", "Marinha")]  # nem o logo nem o recorte com alfa
    kit_clips = [i for i in pool.items if i.source_id.startswith("kitvideo:")]
    assert kit_clips and all(i.credit == "Trailer 1" and i.editorial for i in kit_clips)


def test_online_collectors_are_skipped_offline(isolated_settings, no_network, monkeypatch):
    called = []
    monkeypatch.setattr(media_find, "_ask_llm", lambda *a, **k: called.append(1))
    doc = _project()
    assert media_find.find_for_topics(doc, [("t1", "Farol", "lighthouse")]) == {}
    assert media_search.search_videos("lighthouse", orientation="landscape") == []
    assert media_pool.gather(doc, get_style(STYLE), full=True).items == []
    assert called == []


def test_gather_stops_when_cancelled(isolated_settings, tmp_path):
    doc = _project()
    _seed_project_files(doc.id, tmp_path)
    pool = media_pool.gather(doc, get_style(STYLE), full=True, cancelled=lambda: True)
    assert pool.items == [] and any("cancelada" in w for w in pool.warnings)


# --- anexo -----------------------------------------------------------------------------------------------------------


def _pool_and_doc(tmp_path: Path) -> tuple[ProjectDoc, Pool]:
    doc = _project()
    _seed_project_files(doc.id, tmp_path)
    _seed_capture(doc.id)
    _seed_link(doc.id)
    pool = media_pool.gather(doc, get_style(STYLE), full=True)
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
    scenes[4].visual.media = media_pool.pending_ref(own).model_copy(update={"pool_id": "p999"})
    warnings = media_pool.attach_chosen(doc, pool)
    data_dir = get_settings().data_dir
    a, b, c = (s.visual.media for s in scenes[:3])
    assert a is not None and a.tier == "own" and a.pool_id == own.id and a.kind == "photo" and a.source == "upload"
    assert (data_dir / a.asset).is_file() and a.asset.startswith(f"uploads/{doc.id}/media/")
    assert b is not None and b.kind == "capture" and b.tier == "capture" and b.rects == cap.rects and b.editorial
    assert c is not None and c.kind == "clip" and c.tier == "official" and c.editorial and c.hold and c.mute
    assert c.video and c.video.startswith(f"uploads/{doc.id}/clips/") and (data_dir / c.video).is_file()
    assert c.dur_s == clip.dur_s and c.start_s == 0.0 and c.source == "youtube" and (data_dir / c.asset).is_file()
    assert float(probe(data_dir / c.video)["format"]["duration"]) == pytest.approx(clip.dur_s, abs=0.1)
    assert scenes[4].visual.media is None  # id que não existe: a cena volta à imagem de IA (tem prompt)
    assert any("p999" in w and "imagem de IA" in w for w in warnings)
    assert not any("faltou_real" in w for w in warnings)  # o achado é da conferência do storyboard
    # rodar de novo não refaz nada: as mídias já estão prontas no projeto
    before = [s.visual.media for s in scenes[:3]]
    assert media_pool.attach_chosen(doc, pool) == [] and [s.visual.media for s in scenes[:3]] == before
    # o documento continua válido e grava
    service.commit(doc.id, ProjectDoc.model_validate(doc.model_dump()), "anexo do acervo")


def test_attach_handles_scenes_built_the_storyboard_real_way(isolated_settings, tmp_path, no_network):
    """O storyboard-real monta a cena com o caminho do item (`asset=item.path`, `video=item.path`): mesmo assim o
    anexo copia para o projeto, normaliza e preenche hold/mute/tier."""
    from studio.core.project.document import MediaRef

    doc, pool = _pool_and_doc(tmp_path)
    own = next(i for i in pool.items if i.tier == "own" and i.media == "image")
    clip = next(i for i in pool.items if i.tier == "official")
    scenes = doc.ordered_scenes()
    scenes[0].visual.media = MediaRef(kind="photo", source="upload", asset=own.path, tier="own", pool_id=own.id)
    scenes[1].visual.media = MediaRef(kind="clip", source="youtube", asset=clip.poster, video=clip.path,
                                      start_s=clip.start_s, dur_s=clip.dur_s, tier="official", pool_id=clip.id)
    assert all(media_pool.needs_attach(s.visual.media, doc.id) for s in scenes[:2])
    assert media_pool.attach_chosen(doc, pool) == []
    a, b = scenes[0].visual.media, scenes[1].visual.media
    assert a is not None and a.asset.startswith(f"uploads/{doc.id}/media/") and a.pool_id == own.id
    assert b is not None and b.video.startswith(f"uploads/{doc.id}/clips/") and b.asset.startswith(
        f"uploads/{doc.id}/media/")
    assert b.hold and b.mute and b.start_s == 0.0 and b.tier == "official" and b.editorial
    assert not any(media_pool.needs_attach(s.visual.media, doc.id) for s in scenes[:2])


def test_manual_media_survives_a_new_storyboard(isolated_settings, tmp_path, no_network):
    """A mídia anexada à mão vira item `file:`; o storyboard novo reescreve as cenas, mas o item continua no acervo e
    anexa a partir dos próprios campos."""
    from studio.core.project.document import MediaRef
    from studio.media import real_media

    doc = _project()
    stored = real_media.store_image((ACERVO / "farol.jpg").read_bytes(), doc.id)
    scene = doc.ordered_scenes()[0]
    scene.visual.media = MediaRef(kind="photo", source="upload", asset=stored.rel, credit="Ana",
                                             width=stored.width, height=stored.height)
    service.commit(doc.id, doc, "mídia à mão")
    first = media_pool.gather(doc, get_style(STYLE))
    save_pool(doc.id, first)
    item = next(i for i in first.items if i.source_id.startswith("file:"))
    new_doc = _project("Storyboard novo").model_copy(update={"id": doc.id})  # cenas refeitas, sem a mídia
    again = media_pool.gather(new_doc, get_style(STYLE))
    assert any(i.source_id == item.source_id and i.id == item.id for i in again.items)
    target = new_doc.ordered_scenes()[2]
    target.visual.media = media_pool.pending_ref(item)
    assert media_pool.attach_chosen(new_doc, again) == []
    media = target.visual.media
    assert media is not None and media.asset == stored.rel and media.pool_id == item.id and media.credit == "Ana"


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


def test_failed_clip_falls_back_to_a_real_image_then_to_a_real_neighbour(isolated_settings, no_network):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "ilha.jpg")
    rel = f"uploads/{doc.id}/{media_pool.OWN_DIR}/ilha.jpg"
    pool = Pool(items=[
        PoolItem(id="p1", tier="official", media="clip", topic="Farol", source_id="yt:sumiu#1", dur_s=2.0),
        PoolItem(id="p2", tier="own", media="image", path=rel, topic="Farol", source_id="acervo:ilha.jpg"),
        PoolItem(id="p3", tier="official", media="clip", topic="Outro", source_id="yt:sumiu#2", dur_s=2.0),
    ])
    scenes = doc.ordered_scenes()
    for scene in scenes:
        scene.visual.prompt = ""  # o storyboard-real não deixa prompt de reserva na cena do acervo
    scenes[1].visual.media = media_pool.pending_ref(pool.items[0])
    scenes[3].visual.media = media_pool.pending_ref(pool.items[2])
    warnings = media_pool.attach_chosen(doc, pool)
    first = scenes[1].visual.media
    assert first is not None and first.pool_id == "p2"  # o trecho falhou; entrou a foto real do mesmo tópico
    assert scenes[3].visual.media is None and scenes[3].visual.reuse_of == scenes[1].id  # repete o real vizinho
    assert any("repete a mídia real" in w for w in warnings)
    lonely = _project("Sozinho")
    for scene in lonely.scenes:
        scene.visual.prompt = ""
    lonely.ordered_scenes()[0].visual.media = media_pool.pending_ref(pool.items[0])
    warnings = media_pool.attach_chosen(lonely, Pool(items=[pool.items[0]]))
    assert lonely.ordered_scenes()[0].visual.type == "none" and any("sem imagem" in w for w in warnings)


def test_attach_limits_retries_per_scene(isolated_settings, monkeypatch):
    doc = _project()
    items = [PoolItem(id=f"p{n}", tier="free", media="image", url=f"https://x.example/{n}.jpg", topic="Farol",
                      source_id=f"commons:File:{n}.jpg") for n in range(1, 10)]
    tried: list[str] = []

    def fail(item, *a, **k):
        tried.append(item.id)
        raise FileNotFoundError(item.id)

    monkeypatch.setattr(media_pool, "materialize", fail)
    doc.ordered_scenes()[0].visual.media = media_pool.pending_ref(items[0])
    media_pool.attach_chosen(doc, Pool(items=items))
    assert len(tried) == 1 + media_pool.MAX_FALLBACK_TRIES


def test_attach_download_budget_grows_with_the_scenes_waiting():
    assert media_pool.attach_download_budget(0) == media_pool.MAX_ATTACH_DOWNLOADS
    assert media_pool.attach_download_budget(10) == media_pool.MAX_ATTACH_DOWNLOADS
    assert media_pool.attach_download_budget(60) == 60 + media_pool.ATTACH_DOWNLOAD_SLACK
    assert media_pool.attach_download_budget(10_000) == media_pool.MAX_ATTACH_DOWNLOADS_CAP


def test_attach_downloads_every_scene_of_a_long_video(isolated_settings, monkeypatch):
    """Vídeo longo: 40 cenas com foto de banco baixam todas (o teto fixo de 24 jogava as do fim na IA)."""
    doc = _project()
    template = doc.ordered_scenes()[0]
    doc.scenes = []
    for n in range(40):
        scene = template.model_copy(deep=True)
        scene.id = f"scn_{n:03d}"
        doc.scenes.append(scene)
    items = [PoolItem(id=f"p{n}", tier="stock", media="image", url=f"https://x.example/{n}.jpg", topic="Farol",
                      source_id=f"pexels:{n}") for n in range(40)]
    for scene, item in zip(doc.scenes, items, strict=True):
        scene.visual.media = media_pool.pending_ref(item)
    got: list[str] = []

    def fake(item, *a, **k):
        got.append(item.id)
        return MediaRef(kind="photo", source="pexels", asset=f"uploads/{doc.id}/media/{item.id}.jpg", pool_id=item.id,
                        tier="stock")

    monkeypatch.setattr(media_pool, "materialize", fake)
    media_pool.attach_chosen(doc, Pool(items=items))
    assert len(got) == 40
    assert all(s.visual.media is not None and s.visual.media.pool_id for s in doc.scenes)


def test_missing_real_helper_respects_the_policy_and_locks(isolated_settings):
    doc = _project()
    pool = Pool(items=[PoolItem(id="p1", tier="own", path="uploads/x/y.jpg", source_id="acervo:y")])
    assert media_pool.attach_chosen(doc, pool) == []  # o aviso é da conferência do storyboard, não do anexo
    style = get_style(STYLE)
    assert [s.id for s in media_pool.missing_real(doc, style)] == [s.id for s in doc.ordered_scenes()]
    note = media_pool.faltou_real_warning(doc, style)
    assert note and note.startswith("5 cena(s) usam imagem de IA sem motivo") and "faltou_real" not in note
    doc.ordered_scenes()[0].locked = True
    assert len(media_pool.missing_real(doc, style)) == 4
    relaxed = style.model_copy(deep=True)
    relaxed.art.sources.require_ai_reason = False
    assert media_pool.missing_real(doc, relaxed) == []
    for scene in doc.scenes:
        scene.visual.ai_reason = "hipótese"
    assert media_pool.missing_real(doc, style) == []


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
    quick = media_pool.gather(doc, get_style(STYLE))  # a coleta rápida (storyboard) não faz a chamada paga
    assert asked == [] and not any(i.source_id.startswith("found:") for i in quick.items)
    pool = media_pool.gather(doc, get_style(STYLE), full=True)
    assert len(asked) == 1 and len(asked[0]["hints"]) <= media_find.MAX_TOPICS  # uma chamada por vídeo
    assert asked[0]["hints"][0]["note"].startswith("Tópico do vídeo: Farol da Ilha Rasa")
    tiers = {(i.tier, i.media) for i in pool.items}
    assert {("free", "image"), ("stock", "clip"), ("official", "image")} <= tiers
    free = next(i for i in pool.items if i.source_id == "commons:File:Miami skyline from the ocean.jpg")
    video = next(i for i in pool.items if i.source_id == "pexels-video:123")
    official = next(i for i in pool.items if i.source_id.startswith("found:"))
    assert official.credit == "Marinha" and official.editorial and official.topic == "Farol da Ilha Rasa"
    assert video.dur_s == pytest.approx(4.0)  # o pedido recebe a duração que a cena mostra, não os 12 s do vídeo
    block = media_pool.prompt_block(pool)
    assert f"{video.id} · stock · clip · " in block and "· 4.0s" in block
    # a mesma coleta de novo não chama o LLM (cache de 7 dias por tópicos), e a rápida usa o cache
    media_pool.gather(doc, get_style(STYLE), full=True)
    cached = media_pool.gather(doc, get_style(STYLE))
    assert len(asked) == 1 and any(i.source_id.startswith("found:") for i in cached.items)

    scenes = doc.ordered_scenes()
    for scene, item in zip(scenes, (free, video, official), strict=False):
        scene.visual.media = media_pool.pending_ref(item)
    warnings = media_pool.attach_chosen(doc, pool)
    a, b, c = (s.visual.media for s in scenes[:3])
    assert a is not None and a.source == "commons" and a.tier == "free" and a.asset and a.license
    assert b is not None and b.kind == "clip" and b.tier == "stock" and b.video and b.source == "pexels"
    assert b.dur_s == pytest.approx(4.0)  # o trecho do banco também respeita `official_max_clip_s`
    data_dir = get_settings().data_dir
    assert float(probe(data_dir / b.video)["format"]["duration"]) == pytest.approx(4.0, abs=0.1)
    assert (clips.source_dir(video.url) / "video.mp4").is_file()  # o arquivo do Pexels baixa uma vez só
    assert c is not None and c.kind == "official" and c.tier == "official" and c.editorial and c.asset
    assert not any("não baixou" in w for w in warnings)


# --- rotas e job -----------------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


class FakeRunner:
    def __init__(self) -> None:
        self.submitted: list[tuple] = []

    def submit(self, kind, payload=None, project_id=None, **kw):
        self.submitted.append((kind, project_id))
        return {"id": f"job_{len(self.submitted)}", "kind": kind, "status": "queued"}


def _active(project_id: str, kind: str) -> str:
    from studio.models import Job

    with session_scope() as s:
        s.add(Job(id=f"job_{kind}", kind=kind, project_id=project_id, status="running", payload={}, message=""))
    return f"job_{kind}"


def test_pool_routes(isolated_settings, client, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(routes_media, "get_runner", lambda: runner)
    doc = _project()
    assert client.get("/api/projects/prj_nao_existe/pool").status_code == 404
    empty = client.get(f"/api/projects/{doc.id}/pool").json()
    assert empty["pool"]["items"] == [] and empty["links"] == [] and empty["summary"]["items"] == 0
    assert empty["files"] == [] and empty["job"] is None and empty["sources_bytes"] == 0
    bad = client.post(f"/api/projects/{doc.id}/pool/links", json={"links": [{"url": "ftp://example.org/x.mp4"}]})
    assert bad.status_code == 422
    ok = client.post(f"/api/projects/{doc.id}/pool/links", json={"links": [{"url": LINK, "label": "Trailer 1"}]})
    assert ok.status_code == 200 and ok.json()["links"] == [{"url": LINK, "label": "Trailer 1", "tier": "official"}]
    assert ok.json()["job"]["kind"] == "media_pool"  # link novo: a coleta completa já começa
    more = client.post(f"/api/projects/{doc.id}/pool/links",
                       json={"links": [{"url": "https://www.youtube.com/watch?v=gameplay0001", "tier": "own"}]})
    assert [link["url"] for link in more.json()["links"]] == [LINK, "https://www.youtube.com/watch?v=gameplay0001"]
    replaced = client.post(f"/api/projects/{doc.id}/pool/links", json={"links": [], "replace": True})
    assert replaced.json()["links"] == [] and replaced.json()["job"] is None  # só tirou: nada a baixar
    assert client.post(f"/api/projects/{doc.id}/pool/refresh").json()["kind"] == "media_pool"
    assert runner.submitted == [("media_pool", doc.id)] * 3


def test_refresh_does_not_run_twice_nor_during_the_storyboard(isolated_settings, client, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(routes_media, "get_runner", lambda: runner)
    doc = _project()
    job_id = _active(doc.id, "media_pool")
    assert client.post(f"/api/projects/{doc.id}/pool/refresh").json()["id"] == job_id  # devolve a que já roda
    assert client.get(f"/api/projects/{doc.id}/pool").json()["job"]["id"] == job_id
    other = _project("Outro")
    _active(other.id, "storyboard")
    busy = client.post(f"/api/projects/{other.id}/pool/refresh")
    assert busy.status_code == 409 and "espere" in busy.json()["detail"]
    links = client.post(f"/api/projects/{other.id}/pool/links", json={"links": [{"url": LINK}]})
    assert links.status_code == 200 and links.json()["job"] is None
    assert runner.submitted == []


def test_pool_files_upload_and_delete(isolated_settings, client, monkeypatch):
    runner = FakeRunner()
    monkeypatch.setattr(routes_media, "get_runner", lambda: runner)
    doc = _project()
    files = [("files", ("Foto do farol!.JPG", (ACERVO / "farol.jpg").read_bytes(), "image/jpeg")),
             ("files", ("../../fora.jpg", (ACERVO / "ilha.jpg").read_bytes(), "image/jpeg"))]
    resp = client.post(f"/api/projects/{doc.id}/pool/files", files=files)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["saved"] == ["Foto do farol.jpg", "fora.jpg"] and data["job"]["kind"] == "media_pool"
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    assert sorted(p.name for p in folder.iterdir()) == ["Foto do farol.jpg", "fora.jpg"]
    again = client.post(f"/api/projects/{doc.id}/pool/files",
                        files=[("files", ("fora.jpg", (ACERVO / "ilha.jpg").read_bytes(), "image/jpeg"))])
    assert again.json()["saved"] == ["fora-2.jpg"]  # não pisa no que já existe
    bad = client.post(f"/api/projects/{doc.id}/pool/files", files=[("files", ("x.exe", b"MZ", "application/x"))])
    assert bad.status_code == 422
    fake = client.post(f"/api/projects/{doc.id}/pool/files", files=[("files", ("x.jpg", b"nada", "image/jpeg"))])
    assert fake.status_code == 422 and not (folder / "x.jpg").exists()
    assert sorted(f["name"] for f in client.get(f"/api/projects/{doc.id}/pool/files").json()["files"]) == [
        "Foto do farol.jpg", "fora-2.jpg", "fora.jpg"]
    assert client.delete(f"/api/projects/{doc.id}/pool/files/fora-2.jpg").status_code == 200
    assert client.delete(f"/api/projects/{doc.id}/pool/files/.hidden").status_code == 404
    assert not (folder / "fora-2.jpg").exists() and not list(folder.glob(".*"))
    pool = media_pool.gather(service.get_doc(doc.id), get_style(STYLE))
    assert {i.source_id for i in pool.items} == {"acervo:Foto do farol.jpg", "acervo:fora.jpg"}


def test_ai_reason_route(isolated_settings, client):
    doc = _project()
    scene = doc.ordered_scenes()[0]
    resp = client.put(f"/api/projects/{doc.id}/scenes/{scene.id}/ai_reason", json={"reason": "  hipótese:  o futuro "})
    assert resp.status_code == 200 and resp.json()["ai_reason"] == "hipótese: o futuro"
    assert service.get_doc(doc.id).scene(scene.id).visual.ai_reason == "hipótese: o futuro"
    assert len(media_pool.missing_real(service.get_doc(doc.id), get_style(STYLE))) == 4
    assert client.put(f"/api/projects/{doc.id}/scenes/{scene.id}/ai_reason", json={"reason": ""}).json()[
        "ai_reason"] is None
    assert client.put(f"/api/projects/{doc.id}/scenes/scn_x/ai_reason", json={"reason": "x"}).status_code == 404


def test_media_pool_job_saves_the_pool(isolated_settings, tmp_path):
    doc = _project()
    folder = get_settings().uploads_dir / doc.id / media_pool.OWN_DIR
    folder.mkdir(parents=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    lavfi_clip(folder / "mergulho.mp4")
    job = get_runner().submit("media_pool", {}, doc.id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    assert final["result"]["by_tier"] == {"own": final["result"]["items"]} and final["result"]["items"] > 1
    pool = load_pool(doc.id)
    assert pool.items[0].source_id == "acervo:farol.jpg" and any(i.media == "clip" for i in pool.items)
    assert final["progress"] == 1.0


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
    assert not any("faltou_real" in w for w in final["result"].get("warnings", []))
