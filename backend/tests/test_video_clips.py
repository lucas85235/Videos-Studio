"""Vídeo IA nas cenas-chave: provedor do fal.ai com a rede falsa (httpx.MockTransport), preço, marcar/sugerir,
estimativa com confirmação, geração com teto de custo, cache por hash, build que só usa clipe pronto, timeline,
rotas e a sugestão do storyboard. Nada chama o fal.ai de verdade."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import ClipSpec, MediaRef, Scene, Segment, Visual
from studio.core.styles.library import get_style
from studio.core.timeline import build_timeline
from studio.providers import registry
from studio.providers.base import (
    NotConfigured,
    ProviderInfo,
    QuotaExceeded,
    Refused,
    TransientError,
    Usage,
    VideoProvider,
    VideoResult,
)
from studio.providers.video import (
    FAL_QUEUE,
    KLING_3_TURBO,
    KLING_25_TURBO,
    FalImageToVideo,
    first_frame_data_uri,
    kling_turbo,
    looks_like_mp4,
)
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import authoring, video_clips
from studio.workers.pipeline.build import Builder, plan
from studio.workers.pipeline.video_clips import ClipError

MP4 = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + b"\x00" * 64
SHORT = get_style("misterios-short")
ENDPOINT = KLING_25_TURBO.endpoint
APP = "fal-ai/kling-video"


# --- rede falsa do fal.ai ------------------------------------------------------------------------


class FalNet:
    """A fila do fal.ai de mentira: envio, situação (fila → rodando → pronto), resultado e download."""

    def __init__(self, *, statuses=("IN_QUEUE", "IN_PROGRESS", "COMPLETED"), submit_status=200, submit_body=None,
                 completed_extra=None, video=MP4) -> None:
        self.statuses = list(statuses)
        self.submit_status = submit_status
        self.submit_body = submit_body
        self.completed_extra = completed_extra or {}
        self.video = video
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        url = str(request.url)
        if request.method == "POST" and url == f"{FAL_QUEUE}/{ENDPOINT}":
            if self.submit_status != 200:
                return httpx.Response(self.submit_status, json=self.submit_body or {"detail": "erro"})
            base = f"{FAL_QUEUE}/{APP}/requests/req-1"
            return httpx.Response(200, json={"request_id": "req-1", "status_url": f"{base}/status",
                                             "response_url": base, "cancel_url": f"{base}/cancel", "queue_position": 0})
        if url.endswith("/requests/req-1/status"):
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            body = {"status": status}
            if status == "COMPLETED":
                body.update({"metrics": {"inference_time": 42.0}, **self.completed_extra})
            return httpx.Response(200, json=body)
        if request.method == "PUT" and url.endswith("/cancel"):
            return httpx.Response(202, json={"status": "CANCELLATION_REQUESTED"})
        if url == f"{FAL_QUEUE}/{APP}/requests/req-1":
            return httpx.Response(200, json={"video": {"url": "https://v3.fal.media/files/out.mp4", "content_type": "video/mp4"}})
        if url == "https://v3.fal.media/files/out.mp4":
            return httpx.Response(200, content=self.video)
        return httpx.Response(404)


def fal(net: FalNet, **kw) -> FalImageToVideo:
    base = kling_turbo()
    return FalImageToVideo(base.info, KLING_25_TURBO, transport=httpx.MockTransport(net), sleep=lambda s: None, **kw)


def png(tmp_path: Path, size=(1080, 1920)) -> Path:
    path = tmp_path / "frame.png"
    Image.new("RGB", size, (40, 80, 120)).save(path)
    return path


@pytest.fixture
def fal_key(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "chave-de-teste")


def test_prices_and_durations_follow_the_model_pages():
    assert KLING_25_TURBO.seconds(5) == 5 and KLING_25_TURBO.seconds(7) == 5 and KLING_25_TURBO.seconds(9) == 10
    assert KLING_25_TURBO.price(5) == 0.35 and KLING_25_TURBO.price(10) == 0.70
    assert KLING_3_TURBO.seconds(4) == 4 and KLING_3_TURBO.price(5) == 0.56 and KLING_3_TURBO.price(10) == 1.12


def test_first_frame_is_a_small_jpeg_data_uri(tmp_path):
    uri = first_frame_data_uri(png(tmp_path, (2160, 3840)))
    assert uri.startswith("data:image/jpeg;base64,")
    with Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))) as im:
        assert max(im.size) == 1536 and im.format == "JPEG"
    tiny = first_frame_data_uri(png(tmp_path, (200, 400)))
    with Image.open(io.BytesIO(base64.b64decode(tiny.split(",", 1)[1]))) as im:
        assert min(im.size) >= 300  # o modelo exige 300 px por lado


def test_fal_queue_happy_path(tmp_path, fal_key):
    net = FalNet()
    result = fal(net).animate(image=png(tmp_path), prompt="slow push-in", seconds=7, out_dir=tmp_path / "out")
    assert result.path.read_bytes() == MP4 and looks_like_mp4(result.path)
    assert result.seconds == 5.0 and result.usage.cost_usd == 0.35 and result.usage.unit_name == "seconds"
    assert result.meta == {"request_id": "req-1", "inference_s": 42.0} and result.model == ENDPOINT
    submit = net.requests[0]
    assert submit.headers["Authorization"] == "Key chave-de-teste"
    body = json.loads(submit.content)
    assert body["duration"] == "5" and body["prompt"] == "slow push-in"
    assert body["image_url"].startswith("data:image/jpeg;base64,") and "negative_prompt" in body
    # A situação é consultada até COMPLETED (fila, rodando, pronto) pelo endereço que o envio devolveu.
    assert sum(r.url.path.endswith("/status") for r in net.requests) == 3


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"detail": "Invalid key"}, NotConfigured),
        (403, {"detail": "User is locked. Reason: Exhausted balance."}, QuotaExceeded),
        (429, {"detail": "Too many requests"}, QuotaExceeded),
        (503, {"detail": "down"}, TransientError),
    ],
)
def test_fal_http_errors_map_to_chain_errors(tmp_path, fal_key, status, body, error):
    net = FalNet(submit_status=status, submit_body=body)
    with pytest.raises(error):
        fal(net).animate(image=png(tmp_path), prompt="x", seconds=5, out_dir=tmp_path / "out")


def test_fal_failed_request_and_safety_refusal(tmp_path, fal_key):
    refused = FalNet(completed_extra={"error": "Content flagged by safety checker", "error_type": "content_policy"})
    with pytest.raises(Refused):
        fal(refused).animate(image=png(tmp_path), prompt="x", seconds=5, out_dir=tmp_path / "a")
    broken = FalNet(completed_extra={"error": "GPU crashed"})
    with pytest.raises(TransientError):
        fal(broken).animate(image=png(tmp_path), prompt="x", seconds=5, out_dir=tmp_path / "b")


def test_fal_timeout_cancels_the_request(tmp_path, fal_key):
    net = FalNet(statuses=("IN_PROGRESS",))
    with pytest.raises(TransientError, match="sem terminar"):
        fal(net, timeout_s=-1).animate(image=png(tmp_path), prompt="x", seconds=5, out_dir=tmp_path / "out")
    assert any(r.method == "PUT" and r.url.path.endswith("/cancel") for r in net.requests)


def test_fal_rejects_a_download_that_is_not_a_video(tmp_path, fal_key):
    net = FalNet(video=b"<html>erro</html>")
    with pytest.raises(TransientError, match="MP4"):
        fal(net).animate(image=png(tmp_path), prompt="x", seconds=5, out_dir=tmp_path / "out")
    assert not (tmp_path / "out" / "clip.mp4").exists()


def test_fal_availability_and_shared_key(monkeypatch, isolated_settings):
    monkeypatch.delenv("FAL_KEY", raising=False)
    provider = kling_turbo()
    isolated_settings.offline = False
    try:
        assert provider.availability() == (False, "falta a chave do fal.ai (FAL_KEY ou Configurações)")
        registry.credentials.set_credential("fal_kling_turbo", {"api_key": "do-cofre"})
        assert provider.availability() == (True, "ok")
        # O Kling 3 não pede a chave de novo: usa a do cofre do Kling 2.5.
        assert registry.get("fal_kling_3").availability() == (True, "ok")
    finally:
        isolated_settings.offline = True
    assert provider.availability() == (False, "modo offline")


def test_video_chain_is_registered():
    assert registry.DEFAULT_CHAINS["video"] == ["fal_kling_turbo", "fal_kling_3"]
    assert registry.resolve_chain("video", SHORT.art.video_chain) == ["mock_video"]  # testes rodam offline
    listed = {p["id"]: p for p in registry.describe_all()}
    assert listed["fal_kling_turbo"]["kind"] == "video" and listed["fal_kling_turbo"]["credential_fields"] == ["api_key"]
    assert "mock_video" not in listed  # o clipe de teste não aparece em Configurações


# --- documento: marcar, limite, sugestão ---------------------------------------------------------


def project(*, hints: dict[str, str] | None = None, style_id: str = "misterios-short") -> str:
    doc = service.create_project(title="Vídeo IA", style_id=style_id)
    segs = [Segment(id="seg_1", role="hook", text={"pt-BR": "O porto acordou coberto de névoa. Ninguém sabia por quê."}),
            Segment(id="seg_2", role="body", text={"pt-BR": "Os barcos sumiram um a um. A cidade ficou em silêncio."})]
    scenes = [
        Scene(id="scn_a", segment_id="seg_1", part=0, parts=2, visual=Visual(prompt="foggy harbor at dawn", seed=1), span=6),
        Scene(id="scn_b", segment_id="seg_1", part=1, parts=2, visual=Visual(reuse_of="scn_a"), span=4),
        Scene(id="scn_c", segment_id="seg_2", part=0, parts=2, visual=Visual(prompt="empty boats at night", seed=2), span=5),
        Scene(id="scn_d", segment_id="seg_2", part=1, parts=2, kind="stat", visual=Visual(reuse_of="scn_c"), span=5),
    ]
    for scene in scenes:
        scene.video_hint = (hints or {}).get(scene.id)
    service.commit(doc.id, doc.model_copy(update={"segments": segs, "scenes": scenes}), "cenas")
    return doc.id


def test_mark_limit_lock_and_unmark():
    pid = project()
    doc = service.get_doc(pid)
    marked = video_clips.set_video(doc, SHORT, "scn_a", enabled=True, prompt="  fog   rolls in ", seconds=10)
    scene = marked.scene("scn_a")
    assert scene.kind == "video" and scene.clip == ClipSpec(prompt="fog rolls in", seconds=10)
    assert doc.scene("scn_a").kind == "image"  # cópia: o original não muda
    two = video_clips.set_video(marked, SHORT, "scn_c", enabled=True)
    assert video_clips.max_video_scenes(SHORT) == 2
    with pytest.raises(ClipError, match="limite do estilo é 2"):
        video_clips.set_video(two, SHORT, "scn_b", enabled=True)
    with pytest.raises(ClipError, match="Só cena de imagem"):
        video_clips.set_video(doc, SHORT, "scn_d", enabled=True)
    off = video_clips.set_video(two, SHORT, "scn_a", enabled=False)
    assert off.scene("scn_a").kind == "image" and off.scene("scn_a").clip is not None  # fica de reserva
    locked = doc.model_copy(deep=True)
    locked.scene("scn_c").locked = True
    with pytest.raises(ClipError, match="travada"):
        video_clips.set_video(locked, SHORT, "scn_c", enabled=True)
    long = SHORT.model_copy(update={"format": SHORT.format.model_copy(update={"type": "long"})})
    assert video_clips.max_video_scenes(long) == 6
    off_style = SHORT.model_copy(update={"art": SHORT.art.model_copy(update={"video_scenes": 0})})
    with pytest.raises(ClipError, match="limite do estilo é 0"):
        video_clips.set_video(doc, off_style, "scn_a", enabled=True)


def test_real_media_scene_is_never_animated():
    pid = project()
    doc = service.get_doc(pid)
    doc.scene("scn_a").visual.media = MediaRef(kind="photo", source="commons", asset="uploads/x/media/a.jpg")
    with pytest.raises(ClipError, match="mídia real"):
        video_clips.set_video(doc, SHORT, "scn_a", enabled=True)
    with pytest.raises(ClipError, match="mídia real"):
        video_clips.set_video(doc, SHORT, "scn_b", enabled=True)  # reaproveita a foto real


def test_suggestions_prefer_storyboard_hints_and_one_scene_per_image():
    pid = project(hints={"scn_c": "boats drift away"})
    doc = service.get_doc(pid)
    assert video_clips.suggest(doc, SHORT) == ["scn_c", "scn_a"]  # a do storyboard, depois o gancho
    one = video_clips.set_video(doc, SHORT, "scn_a", enabled=True)
    # scn_b mostra a mesma imagem de scn_a: não entra; scn_c completa o limite.
    assert video_clips.suggest(one, SHORT) == ["scn_c"]
    full = video_clips.set_video(one, SHORT, "scn_c", enabled=True)
    assert video_clips.suggest(full, SHORT) == []


def test_clip_prompt_uses_the_user_motion_then_the_hint():
    scene = Scene(segment_id="s", visual=Visual(prompt="foggy harbor"), video_hint="waves roll")
    assert video_clips.clip_prompt(scene).startswith("waves roll. Scene: foggy harbor")
    scene.clip = ClipSpec(prompt="camera rises.")
    assert video_clips.clip_prompt(scene).startswith("camera rises. Scene: foggy harbor")
    assert video_clips.DEFAULT_MOTION in video_clips.clip_prompt(Scene(segment_id="s", visual=Visual(prompt="x")))


# --- estimativa, geração, cache e build ------------------------------------------------------------


class PricedVideo(VideoProvider):
    """Gerador falso com preço (no lugar do fal.ai na cadeia): grava um MP4 mínimo e conta as chamadas."""

    def __init__(self, provider_id: str, per_second: float) -> None:
        self.info = ProviderInfo(id=provider_id, kind="video", name=f"Falso {provider_id}", commercial_use=True)
        self.per_second = per_second
        self.calls: list[dict] = []

    def clip_seconds(self, seconds: float) -> int:
        return round(seconds)

    def price(self, seconds: float) -> float:
        return round(self.per_second * seconds, 4)

    def animate(self, *, image, prompt, seconds, out_dir) -> VideoResult:
        self.calls.append({"image": image, "prompt": prompt, "seconds": seconds})
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / "clip.mp4"
        path.write_bytes(MP4)
        return VideoResult(path=path, seconds=seconds, usage=Usage(seconds, "seconds", self.price(seconds)), model="falso")


@pytest.fixture
def priced(monkeypatch):
    """Cadeia de vídeo com dois geradores pagos falsos (US$ 0,10/s e US$ 0,20/s)."""
    cheap, dear = PricedVideo("video_barato", 0.10), PricedVideo("video_caro", 0.20)
    monkeypatch.setitem(registry._PROVIDERS, cheap.info.id, cheap)
    monkeypatch.setitem(registry._PROVIDERS, dear.info.id, dear)
    monkeypatch.setitem(registry.OFFLINE_ONLY, "video", [cheap.info.id, dear.info.id])
    return cheap, dear


def mark(pid: str, *scene_ids: str, seconds: int = 5) -> None:
    doc = service.get_doc(pid)
    for scene_id in scene_ids:
        doc = video_clips.set_video(doc, SHORT, scene_id, enabled=True, seconds=seconds)
    service.commit(pid, doc, "marcadas")


def test_estimate_generate_cache_and_budget(priced):
    cheap, dear = priced
    pid = project()
    mark(pid, "scn_a", "scn_c")
    status = video_clips.estimate(Builder(pid, mode="preview"))
    assert status["provider"] == "video_barato" and status["missing"] == 2
    assert status["estimated_cost_usd"] == 1.0 and status["max_cost_usd"] == 2.0  # pior caso: o caro assume
    assert [s["status"] for s in status["scenes"]] == ["missing", "missing"]
    assert "ainda não existe" in status["scenes"][0]["detail"]
    with pytest.raises(ClipError, match="custo mudou"):
        video_clips.check_confirmed(status, 1.5)

    # Teto de US$ 0,60: o primeiro clipe (US$ 0,50) sai, o segundo passaria do valor e fica para depois.
    result = video_clips.generate(Builder(pid, mode="preview"), 0.6)
    assert result["generated"] == ["scn_a"] and result["skipped"] == ["scn_c"] and result["cost_usd"] == 0.5
    assert len(cheap.calls) == 1 and not dear.calls
    assert cheap.calls[0]["seconds"] == 5 and "Scene: foggy harbor at dawn" in cheap.calls[0]["prompt"]

    status = video_clips.estimate(Builder(pid, mode="preview"))
    assert [s["status"] for s in status["scenes"]] == ["ready", "missing"] and status["estimated_cost_usd"] == 0.5
    result = video_clips.generate(Builder(pid, mode="preview"), 1.0)
    assert result["generated"] == ["scn_c"] and result["reused"] == ["scn_a"]  # cache: não paga de novo
    assert len(cheap.calls) == 2

    # Mudar o movimento pede outro clipe; voltar ao de antes acha o antigo no cache.
    doc = video_clips.set_video(service.get_doc(pid), SHORT, "scn_a", enabled=True, prompt="fog rolls in")
    service.commit(pid, doc, "movimento")
    assert video_clips.estimate(Builder(pid, mode="preview"))["missing"] == 1
    doc = video_clips.set_video(service.get_doc(pid), SHORT, "scn_a", enabled=True, prompt="")
    service.commit(pid, doc, "volta")
    assert video_clips.estimate(Builder(pid, mode="preview"))["missing"] == 0


def test_fallback_to_the_next_model_when_the_first_fails(priced, monkeypatch):
    cheap, dear = priced

    def broken(**kw):
        raise TransientError("fora do ar")

    monkeypatch.setattr(cheap, "animate", broken)
    pid = project()
    mark(pid, "scn_a")
    result = video_clips.generate(Builder(pid, mode="preview"), 1.0)
    assert result["generated"] == ["scn_a"] and result["cost_usd"] == 1.0 and len(dear.calls) == 1


def test_preview_never_generates_clips_and_uses_the_ready_ones(priced, monkeypatch, isolated_settings):
    cheap, _ = priced
    pid = project()
    mark(pid, "scn_a")
    kinds: list[str] = []
    original = registry.run_chain

    def spy(kind, *args, **kwargs):
        kinds.append(kind)
        return original(kind, *args, **kwargs)

    monkeypatch.setattr(registry, "run_chain", spy)
    first = Builder(pid, mode="preview").run()
    assert "video" not in kinds and not cheap.calls
    assert any("o vídeo IA ainda não foi gerado" in w and "Cena 1" in w for w in first["warnings"])
    timeline = json.loads((isolated_settings.data_dir / first["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    scene = next(s for s in timeline["scenes"] if s["id"] == "scn_a")
    assert scene["kind"] == "video" and "video" not in scene and scene["src"]
    assert plan(pid, mode="preview")["video_missing"] == ["scn_a"]

    video_clips.generate(Builder(pid, mode="preview"), 1.0)
    second = Builder(pid, mode="preview").run()
    assert not any("vídeo IA" in w for w in second["warnings"])
    timeline = json.loads((isolated_settings.data_dir / second["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    scene = next(s for s in timeline["scenes"] if s["id"] == "scn_a")
    assert scene["video"].startswith("cache/clip/") and scene["video_seconds"] == 5.0
    assert all("video" not in s for s in timeline["scenes"] if s["id"] != "scn_a")
    assert "video_missing" not in plan(pid, mode="preview") and plan(pid, mode="preview")["video_scenes"] == ["scn_a"]
    assert any(a.startswith("clip:") for a in second["artifacts"])


def test_timeline_adds_clip_keys_only_on_scenes_with_a_clip():
    pid = project()
    doc = service.get_doc(pid)
    from studio.core.timeline import NarrationClip
    narration = {s.id: NarrationClip(s.id, "x.wav", 4.0, []) for s in doc.segments}
    images = {s.id: f"cache/image/{s.id}.jpg" for s in doc.scenes}
    plain = build_timeline(doc, SHORT, "pt-BR", narration, images)
    with_clip = build_timeline(doc, SHORT, "pt-BR", narration, images, {"scn_c": {"path": "cache/clip/c.mp4", "seconds": 5}})
    by_id = {s["id"]: s for s in with_clip["scenes"]}
    assert by_id["scn_c"]["video"] == "cache/clip/c.mp4" and by_id["scn_c"]["video_seconds"] == 5.0
    assert [s for s in plain["scenes"] if s["id"] != "scn_c"] == [s for s in with_clip["scenes"] if s["id"] != "scn_c"]


def test_offline_mock_clip_is_a_real_video(isolated_settings):
    """O gerador do modo offline grava um MP4 de verdade (FFmpeg) e fica marcado como só de prévia."""
    pid = project()
    mark(pid, "scn_a", seconds=3)
    builder = Builder(pid, mode="preview")
    result = video_clips.generate(builder, 0.0)
    assert result["generated"] == ["scn_a"] and result["cost_usd"] == 0.0
    status = video_clips.estimate(Builder(pid, mode="preview"))
    ready = status["scenes"][0]
    assert ready["status"] == "ready" and ready["provider"] == "mock_video"
    assert looks_like_mp4(isolated_settings.data_dir / ready["path"])
    built = Builder(pid, mode="preview")
    images = {"scn_a": video_clips.known_image(built, built.doc.scene("scn_a"))}
    clips = video_clips.build_clips(built, images)
    assert clips["scn_a"]["seconds"] == 3.0 and "mock_video" in built.stats.noncommercial


# --- rotas --------------------------------------------------------------------------------------------


@pytest.fixture
def client():
    return TestClient(create_app())


def test_routes_mark_suggest_estimate_and_generate(client, priced, monkeypatch):
    submitted: list[tuple] = []
    monkeypatch.setattr(get_runner(), "submit", lambda kind, payload=None, project_id=None, **kw: submitted.append(
        (kind, payload, project_id)) or {"id": "job_x", "kind": kind})
    pid = project(hints={"scn_c": "boats drift"})
    status = client.get(f"/api/projects/{pid}/video").json()
    assert status["scenes"] == [] and status["suggestions"] == ["scn_c", "scn_a"] and status["max_scenes"] == 2
    assert client.put(f"/api/projects/{pid}/scenes/scn_d/video", json={"enabled": True}).status_code == 422
    assert client.put(f"/api/projects/{pid}/scenes/nada/video", json={"enabled": True}).status_code == 404
    applied = client.post(f"/api/projects/{pid}/video/suggest", json={"apply": True}).json()
    assert applied["scene_ids"] == ["scn_c", "scn_a"] and applied["applied"]
    assert client.put(f"/api/projects/{pid}/scenes/scn_b/video", json={"enabled": True}).status_code == 409
    resp = client.put(f"/api/projects/{pid}/scenes/scn_a/video", json={"enabled": True, "prompt": "fog", "seconds": 10})
    assert resp.status_code == 200 and resp.json()["clip"] == {"prompt": "fog", "seconds": 10}
    assert client.put(f"/api/projects/{pid}/scenes/scn_a/video", json={"seconds": 30}).status_code == 422
    status = client.get(f"/api/projects/{pid}/video").json()
    assert status["missing"] == 2 and status["estimated_cost_usd"] == 1.5 and status["max_cost_usd"] == 3.0
    low = client.post(f"/api/projects/{pid}/video/generate", json={"confirm_cost_usd": 1.5})
    assert low.status_code == 409 and "US$ 3,00" in low.json()["detail"] and not submitted
    ok = client.post(f"/api/projects/{pid}/video/generate", json={"confirm_cost_usd": 3.0})
    assert ok.status_code == 200 and submitted == [("video_clips", {"max_cost_usd": 3.0}, pid)]
    off = client.put(f"/api/projects/{pid}/scenes/scn_a/video", json={"enabled": False}).json()
    assert off["kind"] == "image"


def test_video_job_runs_inline_with_the_confirmed_budget(priced):
    cheap, _ = priced
    pid = project()
    mark(pid, "scn_a")
    job = get_runner().submit("video_clips", {"max_cost_usd": 0.5}, pid, run_inline=True)
    done = get_job(job["id"])
    assert done["status"] == "done" and done["result"]["generated"] == ["scn_a"] and len(cheap.calls) == 1
    again = get_job(get_runner().submit("video_clips", {"max_cost_usd": 0.1}, pid, run_inline=True)["id"])
    assert again["status"] == "done" and again["result"]["reused"] == ["scn_a"]


# --- sugestão do storyboard ---------------------------------------------------------------------------


def test_storyboard_hint_only_on_new_images_and_capped(monkeypatch):
    from test_real_media_storyboard import STYLE, base_doc, by_key, run  # mesmos dados do storyboard falso

    doc = base_doc()
    beats = authoring.plan_beats(doc, STYLE)
    extra = {i: {"video_motion": f"motion {i}"} for i in range(len(beats))}
    extra[1] = {"kind": "stat", "source": "reuse", "reuse_key": beats[0].key, "value": "2025", "label": "o ano",
                "video_motion": "card motion"}
    new_doc, _, _, fake = run(monkeypatch, doc, extra)
    scenes = by_key(new_doc, beats)
    hinted = [i for i, s in enumerate(scenes) if s.video_hint]
    assert scenes[1].video_hint is None  # cartão nunca
    assert hinted == [i for i in range(len(beats)) if i != 1][: video_clips.max_video_scenes(STYLE)]
    assert all(s.kind != "video" for s in new_doc.scenes)  # sugestão não marca nada sozinha
    assert "Vídeo IA (video_motion)" in fake.calls[0]["system"]
    assert "Limite de vídeo IA (video_motion): 2 batida(s)." in authoring.storyboard_prompt(doc, STYLE, beats, 3)


def test_schema_has_video_motion():
    from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA

    item = STORYBOARD_SCHEMA["properties"]["scenes"]["items"]
    assert item["properties"]["video_motion"] == {"type": ["string", "null"]} and "video_motion" in item["required"]


def test_agent_edit_cannot_mark_video_scenes():
    from studio.core.project.ops import OpError, apply_ops, parse_ops

    doc = service.get_doc(project())
    with pytest.raises(OpError, match="Animar com vídeo IA"):
        apply_ops(doc, parse_ops([{"op": "set_scene_kind", "scene_id": "scn_a", "kind": "video"}]))
    marked = video_clips.set_video(doc, SHORT, "scn_a", enabled=True)
    back = apply_ops(marked, parse_ops([{"op": "set_scene_kind", "scene_id": "scn_a", "kind": "image"}]))
    assert back.scene("scn_a").kind == "image"  # tirar continua livre
