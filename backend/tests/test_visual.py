"""Bloco I (ADR 0003): provas na tela, vídeo de referência e miniaturas para o teste A/B.

Sem navegador nem rede: a página é falsa (`open_page` injetado), os vídeos são sintéticos (lavfi) e o envio ao
YouTube é simulado.
"""

from __future__ import annotations

import io
import json
import subprocess
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw

from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import Metadata, Scene, Segment, ThumbnailDoc, Visual
from studio.db import session_scope
from studio.ids import new_id
from studio.media import page_capture, reference_video, safe_http, thumb_variants
from studio.models import Channel, PageCapture, PendingTask, Publication, VideoMetric
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import youtube as yt
from studio.workers.publish.package import build_package
from studio.workers.visual import proofs, references, thumbs

W, H = 2200, 1360  # 1100 x 680 com DPR 2
LINE_H = 0.03  # altura de uma linha (fração da tela)
LINES = [0.30, 0.36, 0.42, 0.48, 0.54]  # topo de cada linha de texto
COL = (0.10, 0.80)  # coluna de texto (x0, x1)


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


def page_image(dark: bool = False) -> Image.Image:
    """Página sintética: coluna de texto com 5 linhas (barras de "palavras" com espaço entre elas)."""
    bg, ink = ((20, 20, 24), (235, 235, 235)) if dark else ((255, 255, 255), (20, 20, 20))
    img = Image.new("RGB", (W, H), bg)
    dr = ImageDraw.Draw(img)
    for top in LINES:
        x = COL[0] * W
        while x < COL[1] * W:
            word = min(90, COL[1] * W - x)
            dr.rectangle([x, top * H, x + word, (top + LINE_H) * H], fill=ink)
            x += word + 25  # espaço entre palavras (menor que a tolerância de 3% da largura)
    return img


PHRASE_RECT = [0.40, LINES[2], 0.20, LINE_H]  # trecho no meio da linha do meio


# --------------------------------------------------------------------------------------------
# Funções puras
# --------------------------------------------------------------------------------------------


def test_view_for_keeps_the_whole_line_and_zoom_in_range():
    img = page_image()
    view = page_capture.view_for(page_capture.ink_mask(img), [PHRASE_RECT], W, H)
    vx0, vy0, vx1, vy1 = view
    assert vx0 <= COL[0] and vx1 >= COL[1]  # a linha inteira, não só o trecho
    assert vy0 < LINES[2] and vy1 > LINES[2] + LINE_H  # folga acima e abaixo
    assert vy0 == pytest.approx(LINES[2] - 1.4 * LINE_H, abs=0.002)
    z = page_capture.zoom_for(view)
    assert 1.0 <= z <= 1.6


@pytest.mark.parametrize("aspect", [16 / 9, 9 / 16])
def test_crop_to_view_contains_the_window_and_has_the_video_aspect(aspect):
    img = page_image()
    view = page_capture.view_for(page_capture.ink_mask(img), [PHRASE_RECT], W, H)
    x0, y0, x1, y1 = page_capture.crop_box(W, H, view, aspect)
    assert x0 <= view[0] * W + 1 and x1 >= view[2] * W - 1
    assert y0 <= view[1] * H + 1 and y1 >= view[3] * H - 1
    assert (x1 - x0) / (y1 - y0) == pytest.approx(aspect, rel=1e-6)
    out = page_capture.crop_to_view(img, view, aspect)
    assert out.width / out.height == pytest.approx(aspect, rel=0.01)


def test_mark_multiplies_on_light_and_layers_on_dark():
    light = page_image()
    marked = page_capture.mark(light, [[0.05, 0.05, 0.02, 0.02]], dark=False)
    px = marked.getpixel((int(0.06 * W), int(0.06 * H)))
    assert px[0] == 255 and px[2] < 100  # amarelo no branco: azul cai (multiply)
    assert marked.getpixel((int(0.5 * W), int(0.9 * H))) == (255, 255, 255)  # fora do trecho nada muda
    dark = Image.new("RGB", (400, 300), (20, 20, 24))
    out = page_capture.mark(dark, [[0.1, 0.1, 0.5, 0.2]], dark=True)
    before, after = dark.getpixel((100, 50)), out.getpixel((100, 50))
    assert sum(after) > sum(before)  # no escuro a camada de 45% clareia (multiply sumiria)
    same = page_capture.mark(dark, [[0.1, 0.1, 0.5, 0.2]], dark=False)
    assert sum(same.getpixel((100, 50))) <= sum(before)


def test_is_dark_and_ink_mask():
    assert page_capture.is_dark(page_image(dark=True)) is True
    assert page_capture.is_dark(page_image()) is False
    mask = page_capture.ink_mask(page_image(dark=True))
    assert mask[int((LINES[0] + 0.01) * H), int(0.11 * W)]  # texto claro na página escura


# --------------------------------------------------------------------------------------------
# Captura com página falsa
# --------------------------------------------------------------------------------------------


class FakePage:
    def __init__(self, found: list[bool], rects: list, img: Image.Image) -> None:
        self.found, self.rects, self.img = found, rects, img
        self.calls: list[str] = []

    def js(self, expression: str):
        if "createTreeWalker" in expression:
            self.calls.append("find")
            return self.found
        if "getClientRects" in expression:
            self.calls.append("rects")
            return self.rects
        if "innerText" in expression and "cookie" not in expression:
            return "Texto da página. A inflação subiu 4,5% em março."
        return True

    def sleep(self, seconds: float) -> None:
        self.calls.append(f"sleep {seconds}")

    def screenshot(self) -> bytes:
        buf = io.BytesIO()
        self.img.save(buf, "PNG")
        return buf.getvalue()

    def title(self) -> str:
        return "Inflação sobe em março - Jornal"


def opener(page: FakePage):
    @contextmanager
    def open_page(url, width, height, dpr):
        assert (width, height, dpr) == (1100, 680, 2)
        yield page

    return open_page


def make_project(style_id: str = "curiosidades-longo", n_scenes: int = 3, channel_id: str | None = None):
    doc = service.create_project(title="Prova", style_id=style_id, channel_id=channel_id)
    seg = Segment(id="seg_1", role="hook", text={"pt-BR": "Texto do gancho."})
    scenes = [Scene(id=f"scn_{i}", segment_id="seg_1", part=i, parts=n_scenes, visual=Visual(prompt="x"))
              for i in range(n_scenes)]
    meta = {"pt-BR": Metadata(title="Prova real", description="Resumo.", hashtags=["#a"])}
    doc = doc.model_copy(update={"segments": [seg], "scenes": scenes, "metadata": meta})
    service.commit(doc.id, doc, "pronto")
    return doc.id


def test_capture_proof_attaches_media_and_marks_partial(monkeypatch):
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    pid = make_project()
    page = FakePage([True, False], [[PHRASE_RECT], None], page_image())
    out = proofs.capture_proof(pid, "scn_1", "https://www.jornal.com.br/economia/inflacao",
                               ["A inflação subiu 4,5%", "trecho que não existe"], 0, open_page=opener(page))
    assert page.calls.count("find") >= 2 and "sleep 2.5" in page.calls  # rola, espera e mede de novo
    assert out["status"] == "parcial" and out["domain"] == "jornal.com.br"
    assert out["width"] == W and out["height"] == H and out["dark"] is False
    assert out["phrases"][0]["view"] and out["phrases"][1]["view"] is None
    data_dir = get_settings().data_dir
    assert (data_dir / out["image"]).is_file() and out["image"].startswith(f"uploads/{pid}/captures/")
    assert "4,5%" in (data_dir / out["text_path"]).read_text(encoding="utf-8")
    media = service.get_doc(pid).scene("scn_1").visual.media
    assert media is not None and media.asset == out["proof"]
    # página de terceiros de nível oficial: sempre editorial (aviso de Content ID e crédito, revisão da fundação)
    assert (media.kind, media.source, media.editorial) == ("official", "link", True)
    assert media.outlet == media.credit == "jornal.com.br" and media.title == "Inflação sobe em março - Jornal"
    assert media.page_url == "https://www.jornal.com.br/economia/inflacao"
    with Image.open(data_dir / media.asset) as proof:
        assert proof.width / proof.height == pytest.approx(16 / 9, rel=0.01)
    with session_scope() as s:
        assert s.get(PageCapture, out["id"]).status == "parcial"

    ok = proofs.capture_proof(pid, "scn_2", "https://www.jornal.com.br/x", ["A inflação subiu"], 0,
                              open_page=opener(FakePage([True], [[PHRASE_RECT]], page_image())))
    assert ok["status"] == "ok"


def test_rebake_locks_the_view_and_updates_the_scene(monkeypatch, client):
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    pid = make_project()
    page = FakePage([True], [[PHRASE_RECT]], page_image())
    cap = proofs.capture_proof(pid, "scn_0", "https://jornal.com.br/a", ["A inflação"], 0, open_page=opener(page))
    res = client.put(f"/api/captures/{cap['id']}/phrases/0/view", json={"view": [0.3, 0.3, 0.7, 0.6], "lock": True})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["phrases"][0]["view"] == [0.3, 0.3, 0.7, 0.6] and body["phrases"][0]["lock"] is True
    assert body["proof"] != cap["proof"]
    assert service.get_doc(pid).scene("scn_0").visual.media.asset == body["proof"]
    assert client.get(f"/api/projects/{pid}/captures").json()[0]["id"] == cap["id"]
    assert client.put(f"/api/captures/{cap['id']}/phrases/5/view", json={"view": None}).status_code == 422


def test_failed_capture_is_recorded(monkeypatch):
    pid = make_project()

    def broken(url, texts, focus, open_page=None):
        raise page_capture.CaptureError("A página não abriu: tempo esgotado")

    monkeypatch.setattr(page_capture, "capture", broken)
    with pytest.raises(page_capture.CaptureError):
        proofs.capture_proof(pid, "scn_0", "https://www.jornal.com.br/a", ["um trecho"], 0)
    last = proofs.list_captures(pid)[0]
    assert last["status"] == "falhou" and last["error"] == "A página não abriu: tempo esgotado"
    assert last["scene_id"] == "scn_0" and last["domain"] == "jornal.com.br" and not last["image"]
    assert [p["text"] for p in last["phrases"]] == ["um trecho"]


def test_browser_missing_gives_the_message(monkeypatch, client):
    monkeypatch.setattr(page_capture, "_has_playwright", lambda: False)
    status = page_capture.browser_status()
    assert status == {"ok": False, "message": page_capture.BROWSER_MISSING}
    assert "uv pip install playwright" in status["message"]
    pid = make_project()
    res = client.post(f"/api/projects/{pid}/scenes/scn_0/proof", json={"url": "https://x.com", "phrases": ["a"]})
    assert res.status_code == 400 and res.json()["detail"] == page_capture.BROWSER_MISSING
    caps = client.get("/api/visual/capabilities").json()
    assert caps["browser"]["ok"] is False and set(caps) == {"browser", "ytdlp"}


# --------------------------------------------------------------------------------------------
# Vídeo de referência
# --------------------------------------------------------------------------------------------


def synthetic_video(path: Path, vertical: bool = False) -> Path:
    """Vermelho 1 s, verde 1 s, flash branco 0,2 s, azul 1 s."""
    size = "180x320" if vertical else "320x180"
    parts = [("0xFF0000", 1), ("0x00FF00", 1), ("0xFFFFFF", 0.2), ("0x0000FF", 1)]
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for color, dur in parts:
        cmd += ["-f", "lavfi", "-i", f"color=c={color}:s={size}:d={dur}:r=25"]
    cmd += ["-filter_complex", "".join(f"[{i}:v]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True, capture_output=True)
    return path


def _near(hex_color: str, rgb: tuple[int, int, int], tol: int = 45) -> bool:
    got = tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5))
    return max(abs(a - b) for a, b in zip(got, rgb, strict=True)) <= tol


def test_reference_shots_sheet_and_palette(tmp_path):
    video = synthetic_video(tmp_path / "ref.mp4")
    with video.open("rb") as fh:
        ref = references.save_upload(fh, "trailer.mp4", "Trailer de teste")
    assert ref["status"] == "novo" and ref["path"].startswith("library/references/")
    done = references.analyze_reference(ref["id"])
    assert done["status"] == "pronto"
    assert len(done["shots"]) == 3  # o flash de 0,2 s sai
    assert [s["k"] for s in done["shots"]] == [1, 2, 3]
    assert done["shots"][0]["a"] == 0.0 and done["shots"][-1]["b"] == pytest.approx(3.2, abs=0.1)
    assert len(done["sheets"]) == 1
    with Image.open(get_settings().data_dir / done["sheets"][0]) as sheet:
        assert sheet.size == (5 * 384, 4 * 216)
    st = done["stats"]
    assert st["shots"] == 3 and st["median_s"] == pytest.approx(1.0, abs=0.15)
    assert st["cuts_per_min"] == pytest.approx(2 / (3.2 / 60), rel=0.1)
    palette = st["palette"]
    assert 3 <= len(palette) <= 5
    for rgb in ((255, 0, 0), (0, 255, 0), (0, 0, 255)):
        assert any(_near(c, rgb) for c in palette), (rgb, palette)
    assert references.delete_reference(ref["id"]) is True
    assert not (get_settings().data_dir / done["sheets"][0]).exists()


def test_vertical_reference_uses_vertical_tiles(tmp_path):
    info = {"w": 180, "h": 320}
    assert reference_video.sheet_tile(info) == (216, 384)
    assert reference_video.shots_from([0.1, 1.0, 1.2, 2.0], 3.0) == [
        {"k": 1, "a": 0.0, "b": 1.0, "dur": 1.0}, {"k": 2, "a": 1.2, "b": 2.0, "dur": 0.8},
        {"k": 3, "a": 2.0, "b": 3.0, "dur": 1.0}]


def test_ytdlp_missing_gives_the_message(monkeypatch, client, tmp_path):
    monkeypatch.delenv("VULTO_YTDLP", raising=False)
    monkeypatch.setattr(reference_video.shutil, "which", lambda name: None)
    assert reference_video.ytdlp_status() == {"ok": False, "message": reference_video.YTDLP_MISSING}
    assert "winget install yt-dlp" in reference_video.YTDLP_MISSING
    with pytest.raises(reference_video.ReferenceVideoError, match="yt-dlp"):
        reference_video.download("https://youtu.be/abc", tmp_path, "x")
    res = client.post("/api/references/link", json={"url": "https://youtu.be/abc"})
    assert res.status_code == 400 and res.json()["detail"] == reference_video.YTDLP_MISSING


def test_ytdlp_download_command(monkeypatch, tmp_path):
    monkeypatch.setenv("VULTO_YTDLP", "yt-dlp-falso")
    monkeypatch.setattr(reference_video.shutil, "which", lambda name: "C:/bin/yt-dlp.exe")
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    seen = {}

    def runner(cmd, **kw):
        seen["cmd"], seen["timeout"] = cmd, kw["timeout"]
        (tmp_path / "ref_x.mp4").write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="Trailer oficial\n", stderr="")

    path, title = reference_video.download("https://youtu.be/abc", tmp_path, "ref_x", runner=runner)
    assert path == tmp_path / "ref_x.mp4" and title == "Trailer oficial"
    assert seen["timeout"] == 900
    i = seen["cmd"].index("-f")
    assert seen["cmd"][i + 1] == "bv*[height<=1080]+ba/b[height<=1080]"
    assert seen["cmd"][seen["cmd"].index("--merge-output-format") + 1] == "mp4"


# --------------------------------------------------------------------------------------------
# Miniaturas
# --------------------------------------------------------------------------------------------


def _scene_image(pid: str, name: str, color: tuple[int, int, int]) -> str:
    rel = f"uploads/{pid}/media/{name}.jpg"
    path = get_settings().data_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    grad = np.linspace(0, 1, 1920)[None, :, None]
    arr = (np.array(color)[None, None, :] * (0.4 + 0.6 * grad)).repeat(1080, axis=0)
    Image.fromarray(arr.astype(np.uint8), "RGB").save(path, quality=90)
    return rel


def thumbs_project(text: str = "GTA 6 CHEGOU", channel_id: str | None = None) -> str:
    pid = make_project(channel_id=channel_id)
    doc = service.get_doc(pid)
    scenes = []
    for i, scene in enumerate(doc.scenes):
        rel = _scene_image(pid, f"img{i}", [(200, 40, 40), (40, 160, 60), (40, 60, 200)][i])
        scenes.append(scene.model_copy(update={"visual": scene.visual.model_copy(update={"asset": rel})}))
    scenes[0] = scenes[0].model_copy(update={"kind": "image"})
    scenes[2] = scenes[2].model_copy(update={"kind": "stat"})
    doc = doc.model_copy(update={"scenes": scenes, "thumbnail": ThumbnailDoc(text={"pt-BR": text}, scene_id="scn_0")})
    service.commit(pid, doc, "miniatura")
    out = get_settings().outputs_dir / pid / "pt-BR"
    out.mkdir(parents=True, exist_ok=True)
    (out / "video.mp4").write_bytes(b"mp4")
    Image.new("RGB", (1280, 720), (9, 9, 9)).save(out / "thumbnail.jpg")
    service.update_settings(pid, {"last_final": {"langs": {"pt-BR": {
        "video": f"outputs/{pid}/pt-BR/video.mp4", "thumbnail": f"outputs/{pid}/pt-BR/thumbnail.jpg",
        "duration": 754.0}}, "preview_only_providers": []}})
    return pid


def test_default_specs_follow_the_rules():
    pid = thumbs_project()
    state = thumbs.get_state(pid, "pt-BR")
    spec = state["spec"]
    assert [s["label"] for s in spec] == ["Recomendada", "Outra cena", "Outro modelo"]
    assert spec[0]["scene_id"] == "scn_0" and spec[0]["template"] == "texto-grande-esquerda"
    assert spec[1]["scene_id"] != "scn_0" and spec[1]["text"] == spec[0]["text"]
    assert spec[2]["scene_id"] == "scn_0" and spec[2]["template"] == "texto-centro"
    assert state["size"] == "3840x2160" and state["ab"]["eligible"] is False
    assert thumb_variants.next_template("texto-baixo") == "texto-grande-esquerda"


def test_three_thumbnails_size_limit_and_sheets(client):
    pid = thumbs_project(text="GTA 6 CHEGOU HOJE")  # 4 palavras
    state = thumbs.make_variants(pid, "pt-BR")
    variants = state["variants"]
    assert len(variants) == 3
    data_dir = get_settings().data_dir
    for v in variants:
        assert v["size"] == "3840x2160" and v["mb"] <= 2 and v["errors"] == []
        assert (data_dir / v["path"]).stat().st_size <= 2_000_000
        with Image.open(data_dir / v["path"]) as im:
            assert im.size == (3840, 2160) and im.format == "JPEG"
        with Image.open(data_dir / v["preview"]) as im:
            assert im.size == (1280, 720)
        assert v["warnings"] == [thumb_variants.TOO_MANY_WORDS]
    with Image.open(data_dir / state["sheet_small"]) as small:
        assert small.size == (934, 730)  # 168 + 246 + 360 + 4 x 40; 40 + 3 x 230
    with Image.open(data_dir / state["sheet_big"]) as big:
        assert big.size == (1280, 3 * 720)
    assert state["chosen"] == 1 and state["made_at"]
    # escolher e editar pela API
    res = client.put(f"/api/projects/{pid}/thumbs/chosen", json={"lang": "pt-BR", "n": 2})
    assert res.status_code == 200 and res.json()["chosen"] == 2
    assert client.put(f"/api/projects/{pid}/thumbs/chosen", json={"lang": "pt-BR", "n": 3}).json()["chosen"] == 3
    bad = client.put(f"/api/projects/{pid}/thumbs/spec", json={"lang": "pt-BR", "variants": [{"template": "x"}]})
    assert bad.status_code == 422
    ok = client.put(f"/api/projects/{pid}/thumbs/spec",
                    json={"lang": "pt-BR", "variants": [{"scene_id": "scn_1", "text": "OUTRO", "template": "texto-baixo"}]})
    assert ok.status_code == 200 and ok.json()["spec"][0]["text"] == "OUTRO"


def test_short_thumbnail_is_vertical_and_has_no_word_warning():
    out = Path(get_settings().tmp_dir) / "short" / "miniatura-1.jpg"
    done = thumb_variants.render_variant(None, out, text="UMA DUAS TRES QUATRO", template="texto-centro", is_long=False)
    assert done["size"] == "1080x1920" and done["warnings"] == [] and done["errors"] == []


def test_size_limit_lowers_quality_and_errors(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    noise = Image.fromarray(rng.integers(0, 255, (2160, 3840, 3), dtype=np.uint8), "RGB")
    size, quality = thumb_variants.encode_jpg(noise, tmp_path / "ruido.jpg")
    assert quality == 76 and size > thumb_variants.MAX_BYTES  # tentou 92, 88, 84, 80 e 76
    monkeypatch.setattr(thumb_variants, "make_thumbnail",
                        lambda base, out, **kw: noise.save(out))
    done = thumb_variants.render_variant(None, tmp_path / "m.jpg", text="A", template="texto-centro", is_long=True)
    assert any("passa de 2 MB (limite pelo celular)" in e for e in done["errors"])


def _channel(name: str = "Canal") -> str:
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name=name))
    return cid


def _metrics(cid: str, impressions: list[int | None], project_id: str | None = None) -> None:
    with session_scope() as s:
        for i, imp in enumerate(impressions):
            vid = f"v{cid[-4:]}{i}"
            # 3 dias (não conta); 7 dias da coleta diária (sem impressões); 8 dias lançado à mão (vale: a linha
            # com impressões mais perto de 168 h); 9 dias à mão (mais longe, não vale)
            s.add(VideoMetric(youtube_id=vid, day="2026-10-01", channel_id=cid, project_id=project_id, age_hours=72,
                              impressions=99_999))
            s.add(VideoMetric(youtube_id=vid, day="2026-10-05", channel_id=cid, project_id=project_id, age_hours=170,
                              impressions=None))
            s.add(VideoMetric(youtube_id=vid, day="2026-10-06", channel_id=cid, project_id=project_id, age_hours=192,
                              impressions=imp))
            s.add(VideoMetric(youtube_id=vid, day="2026-10-07", channel_id=cid, project_id=project_id, age_hours=216,
                              impressions=None if imp is None else 99_999))


def test_ab_rule_with_and_without_2000_impressions():
    low, high = _channel("Pequeno"), _channel("Grande")
    _metrics(low, [500, 1500, 1900, None])
    _metrics(high, [1800, 2500, 4000])
    small = thumbs.ab_rule(low)
    assert small["eligible"] is False and small["median_impressions"] == 1500 and small["n"] == 3
    assert "48 h" in small["rule"] and "4%" in small["rule"]
    big = thumbs.ab_rule(high)
    assert big["eligible"] is True and big["median_impressions"] == 2500
    assert "Testar e comparar" in big["rule"]
    assert thumbs.ab_rule(None)["eligible"] is False
    # Shorts do canal não contam
    short_pid = make_project(style_id="misterios-short", channel_id=high)
    shorts_only = _channel("Shorts")
    _metrics(shorts_only, [9000, 9000], project_id=short_pid)
    assert thumbs.ab_rule(shorts_only)["n"] == 0
    # Shorts enviados fora do Vulto (sem projeto) saem pela duração guardada pela coleta
    outside = _channel("De fora")
    _metrics(outside, [9000, 9000, 1000])
    durations = {f"v{outside[-4:]}0": 45.0, f"v{outside[-4:]}1": 58.0, f"v{outside[-4:]}2": 720.0}
    with session_scope() as s:
        s.get(Channel, outside).settings = {"analytics": {"videos": {k: {"duration_s": v} for k, v in durations.items()}}}
    rule = thumbs.ab_rule(outside)
    assert rule["n"] == 1 and rule["median_impressions"] == 1000 and rule["eligible"] is False
    # a troca é pela outra opção, nunca pela que já está no ar
    assert thumbs.ab_rule(low, 2)["rule"] == "Suba a 2ª; em 48 h, CTR abaixo de 4% → troque pela 1ª."
    assert thumbs.swap_option(1) == 2 and thumbs.swap_option(3) == 1


def test_publish_uses_the_chosen_thumbnail_and_creates_the_pending(monkeypatch):
    cid = _channel()
    pid = thumbs_project(channel_id=cid)
    state = thumbs.make_variants(pid, "pt-BR")
    thumbs.choose(pid, "pt-BR", 2)
    sent: dict = {}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None):
        return {"id": "vidAB1"}

    def set_thumbnail(video_id, path, client, account=None):
        sent["thumb"] = Path(path)

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "set_thumbnail", set_thumbnail)
    monkeypatch.setattr(yt, "upload_captions", lambda *a, **k: None)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id, chosen=None: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "public"}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    assert sent["thumb"] == get_settings().data_dir / state["variants"][1]["path"]
    with session_scope() as s:
        pending = s.query(PendingTask).filter_by(auto_key="miniatura:vidAB1").one()
        assert pending.kind == "auto" and pending.channel_id == cid and pending.where_hint == f"/projetos/{pid}"
        assert pending.order > 0
        # a 2ª já está no ar: a troca é pela 1ª
        assert pending.text == 'Em 48 h, ver o CTR de "Prova real": abaixo de 4%, trocar a miniatura pela 1ª opção'
        details = s.query(Publication).filter_by(project_id=pid, platform="youtube").one().details
    assert details["thumbs"]["chosen"] == 2 and details["thumbs"]["ab_eligible"] is False
    assert len(details["thumbs"]["variants"]) == 3


def _fake_upload(monkeypatch, video_id: str, sent: dict) -> None:
    monkeypatch.setattr(yt, "upload_video", lambda path, **kw: {"id": video_id})
    monkeypatch.setattr(yt, "set_thumbnail", lambda vid, path, client, account=None: sent.update(thumb=Path(path)))
    monkeypatch.setattr(yt, "upload_captions", lambda *a, **k: None)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id, chosen=None: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")


def test_private_upload_has_no_ctr_pending(monkeypatch):
    pid = thumbs_project(channel_id=_channel())
    thumbs.make_variants(pid, "pt-BR")
    _fake_upload(monkeypatch, "vidAB3", {})
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "unlisted"}, pid, run_inline=True)
    assert get_job(job["id"])["status"] == "done"
    with session_scope() as s:
        assert s.query(PendingTask).filter_by(auto_key="miniatura:vidAB3").first() is None
        assert s.query(Publication).filter_by(project_id=pid).one().details["thumbs"]["chosen"] == 1


def test_chosen_with_errors_or_stale_falls_back_to_the_build_thumbnail(monkeypatch):
    pid = thumbs_project()
    thumbs.make_variants(pid, "pt-BR")
    settings = service.get_project_row(pid)["settings"]
    assert thumbs.pick_thumbnail(settings, "pt-BR", pid) == (settings["thumbs"]["pt-BR"]["variants"][0]["path"], None)
    # o texto da capa mudou depois da geração: desatualizadas, vai a do build e a especificação recomeça
    doc = service.get_doc(pid)
    service.commit(pid, doc.model_copy(update={"thumbnail": ThumbnailDoc(text={"pt-BR": "NOVO"}, scene_id="scn_0")}),
                   "capa nova")
    state = thumbs.get_state(pid, "pt-BR")
    assert state["stale"] is True and state["spec"][0]["text"] == "NOVO"
    assert thumbs.pick_thumbnail(settings, "pt-BR", pid) == (None, thumbs.STALE_WARNING)
    edited = thumbs.set_spec(pid, "pt-BR", [{"scene_id": "scn_0", "text": "NOVO", "template": "texto-baixo"}])
    assert edited["stale"] is True and edited["spec"][0]["template"] == "texto-baixo"
    state = thumbs.make_variants(pid, "pt-BR")
    assert state["stale"] is False and state["variants"][0]["text"] == "NOVO"
    # variação com erro (passa de 2 MB): vai a do build, com aviso
    thumbs._save(pid, "pt-BR", {"variants": [{**v, "errors": ["passa de 2 MB (limite pelo celular)"]}
                                             for v in state["variants"]]})
    sent: dict = {}
    _fake_upload(monkeypatch, "vidAB4", sent)
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private", "captions": False}, pid,
                              run_inline=True)
    final = get_job(job["id"])
    assert final["status"] == "done" and sent["thumb"].name == "thumbnail.jpg"
    assert any("A miniatura 1 não serve" in w for w in final["result"]["warnings"])


def test_missing_scene_image_falls_back_with_a_warning():
    pid = thumbs_project()
    thumbs.set_spec(pid, "pt-BR", [{"scene_id": "scn_1", "text": "A", "template": "texto-centro"}])
    (get_settings().data_dir / f"uploads/{pid}/media/img1.jpg").unlink()
    variant = thumbs.make_variants(pid, "pt-BR")["variants"][0]
    assert variant["warnings"][0] == "A cena escolhida não tem mais imagem: usei a cena da capa."


def test_publish_without_variants_uses_the_build_thumbnail(monkeypatch):
    pid = thumbs_project()
    sent: dict = {}
    monkeypatch.setattr(yt, "upload_video", lambda path, **kw: {"id": "vidAB2"})
    monkeypatch.setattr(yt, "set_thumbnail", lambda vid, path, client, account=None: sent.update(thumb=Path(path)))
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id, chosen=None: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private", "captions": False}, pid,
                              run_inline=True)
    assert get_job(job["id"])["status"] == "done"
    assert sent["thumb"].name == "thumbnail.jpg"
    with session_scope() as s:
        assert s.query(PendingTask).filter_by(auto_key="miniatura:vidAB2").first() is None
        assert "thumbs" not in s.query(Publication).filter_by(project_id=pid).one().details


def test_package_copies_the_thumbnails_and_the_rule():
    pid = thumbs_project()
    thumbs.make_variants(pid, "pt-BR")
    thumbs.choose(pid, "pt-BR", 3)
    result = build_package(pid, "pt-BR", ["youtube", "tiktok"])
    folders = {item["platform"]: Path(item["folder"]) for item in result["items"]}
    yt_dir = folders["youtube"]
    for n in (1, 2, 3):
        assert (yt_dir / f"miniatura-{n}.jpg").is_file()
    assert (yt_dir / "capa.jpg").read_bytes() == (yt_dir / "miniatura-3.jpg").read_bytes()
    checklist = (yt_dir / "CHECKLIST.md").read_text(encoding="utf-8")
    assert "Miniaturas para o teste A/B" in checklist and thumbs.manual_rule(3) in checklist
    assert "troque pela 1ª" in checklist
    assert not (folders["tiktok"] / "miniatura-1.jpg").exists()
    assert json.loads((yt_dir.parent / "pacote.json").read_text(encoding="utf-8"))
