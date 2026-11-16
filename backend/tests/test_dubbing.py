"""Modo B: várias faixas de idioma no mesmo vídeo (README §7.3). Áudio sintético, sem serviços reais."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.media.ffmpeg import SAMPLE_RATE, duration_of, read_wav, wav_duration, write_wav
from studio.models import Publication
from studio.providers.base import Word
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import authoring, dubbing
from studio.workers.pipeline.build import Builder
from studio.workers.publish import youtube as yt
from studio.workers.publish.package import build_dub_package


def _run(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _tone(path: Path, seconds: float) -> Path:
    t = np.arange(int(seconds * SAMPLE_RATE)) / SAMPLE_RATE
    return write_wav(path, (0.2 * np.sin(2 * np.pi * 220 * t)).astype(np.float32))


# --------------------------------------------------------------------------------------------
# Tempo dos segmentos e aceleração
# --------------------------------------------------------------------------------------------


def test_fit_keeps_primary_time_and_speeds_up_within_limit():
    fits = dubbing.fit_segments(["a", "b"], {"pt-BR": {"a": 5.0, "b": 4.0}, "en-US": {"a": 5.3, "b": 3.9}}, "pt-BR")
    a, b = fits
    assert a.slot == 5.0 and a.pause == 0 and a.longest is None
    assert a.tempos["en-US"] == pytest.approx(1.06, abs=0.001)
    assert b.slot == 4.0 and b.tempos["en-US"] == 1.0  # mais curto: não desacelera
    assert dubbing.fit_warnings(fits, "pt-BR") == []


def test_longest_language_defines_the_segment_and_warns():
    fits = dubbing.fit_segments(["a"], {"pt-BR": {"a": 5.0}, "en-US": {"a": 6.6}, "es-ES": {"a": 5.2}}, "pt-BR")
    fit = fits[0]
    assert fit.slot == pytest.approx(6.0) and fit.longest == "en-US"
    assert fit.tempos["en-US"] == pytest.approx(1.10, abs=0.001)  # no teto, cabe exato na vaga
    assert fit.tempos["es-ES"] == 1.0
    assert fit.pause == pytest.approx(1.0)
    warnings = dubbing.fit_warnings(fits, "pt-BR", {"en-US": "inglês"}.get)
    assert len(warnings) == 1 and "inglês" in warnings[0] and "1.0 s de pausa" in warnings[0]
    summary = dubbing.fit_summary(fits, "pt-BR")
    assert summary["added_pause_s"] == 1.0 and summary["langs"]["en-US"]["longest_in"] == 1


def test_short_translation_warns_about_silence():
    fits = dubbing.fit_segments(["a"], {"pt-BR": {"a": 8.0}, "en-US": {"a": 4.0}}, "pt-BR")
    warnings = dubbing.fit_warnings(fits, "pt-BR")
    assert len(warnings) == 1 and "curta" in warnings[0]


def test_stretch_uses_atempo_and_respects_the_limit(tmp_path):
    src = _tone(tmp_path / "voz.wav", 2.0)
    out = dubbing.stretch(src, tmp_path / "rapida.wav", 1.08)
    assert wav_duration(out) == pytest.approx(2.0 / 1.08, abs=0.02)
    audio, rate = read_wav(out)
    assert rate == SAMPLE_RATE and float(np.abs(audio).max()) > 0.1  # som, não silêncio
    with pytest.raises(ValueError):
        dubbing.stretch(src, tmp_path / "demais.wav", 1.3)


def test_scale_words_and_srt():
    words = dubbing.scale_words([Word("Hello", 0.1, 0.5), Word("world.", 0.6, 1.1), Word("Next", 1.2, 1.5)], 1.1)
    assert words[1].end == pytest.approx(1.0)
    srt = dubbing.narration_srt([(1.0, 2.5, words), (3.0, 4.0, [Word("Fim", 0.0, 0.4)])])
    blocks = [b for b in srt.strip().split("\n\n") if b]
    assert len(blocks) == 3
    assert blocks[0].splitlines()[1].startswith("00:00:01,091 --> ")
    assert blocks[0].splitlines()[2] == "Hello world."
    assert blocks[2].splitlines()[1] == "00:00:03,000 --> 00:00:03,700"


def test_caption_lines_break_on_limits():
    words = [Word("palavra", i * 0.3, i * 0.3 + 0.25) for i in range(12)]
    lines = dubbing.caption_lines(words, max_chars=42, max_words=8)
    assert all(len(" ".join(w.text for w in line)) <= 42 for line in lines)
    assert sum(len(line) for line in lines) == 12


# --------------------------------------------------------------------------------------------
# Orçamento de duração da tradução
# --------------------------------------------------------------------------------------------


def _project(langs: list[str], delivery: str = dubbing.MULTI_TRACK) -> str:
    doc = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado da ilha",
                                 languages=langs)
    doc.style_overrides = {"languages": {"delivery": delivery}}
    service.commit(doc.id, doc, "Modo B")
    return doc.id


def test_budgets_and_over_budget(isolated_settings):
    pid = _project(["pt-BR", "en-US", "ja-JP"])
    _run("script", pid)
    doc = service.get_doc(pid)
    style = get_style(doc.style_id, doc.style_overrides)
    budgets = dubbing.segment_budgets(doc, style, "en-US")
    assert len(budgets) == len(doc.segments) and all(b.max_words and b.seconds > 0 for b in budgets)
    assert dubbing.segment_budgets(doc, style, "ja-JP")[0].max_chars  # medido por caracteres
    seg = doc.segments[0]
    seg.text["en-US"] = " ".join(["word"] * (budgets[0].max_words * 2))
    assert dubbing.over_budget(doc, style, "en-US") == [seg.id]
    seg.text["en-US"] = " ".join(["word"] * budgets[0].max_words)
    assert dubbing.over_budget(doc, style, "en-US") == []


def test_translation_prompt_has_budget_and_long_text_is_shortened(isolated_settings, monkeypatch):
    pid = _project(["pt-BR", "en-US"])
    _run("script", pid)
    doc = service.get_doc(pid)
    calls: list[dict] = []
    long_id = doc.segments[0].id

    class Result:
        def __init__(self, data):
            self.data = data

    def fake_llm(task, system, prompt, schema, context, project_id, job_id):
        calls.append({"prompt": prompt, "context": context})
        if context.get("shorten"):
            return Result({"segments": [{"id": s["id"], "text": "Short one."} for s in context["segments"]]}), "fake"
        segs = [{"id": s["id"], "text": ("very " * 200 if s["id"] == long_id else "ok text") + "."}
                for s in context["segments"]]
        return Result({"title": "T", "segments": segs, "overlays": []}), "fake"

    monkeypatch.setattr(authoring, "_llm", fake_llm)
    new_doc, _ = authoring.translate(doc, "en-US")
    assert "palavras (~" in calls[0]["prompt"] and calls[0]["context"]["segments"][0]["budget"]
    assert len(calls) == 2 and [s["id"] for s in calls[1]["context"]["segments"]] == [long_id]
    assert new_doc.segment(long_id).text["en-US"] == "Short one."

    # Modo A: o orçamento vai no pedido, mas não há segunda rodada.
    calls.clear()
    doc.style_overrides = {}
    authoring.translate(doc, "en-US")
    assert len(calls) == 1


# --------------------------------------------------------------------------------------------
# Build de ponta a ponta (voz de teste sintética)
# --------------------------------------------------------------------------------------------


def _set_delivery(pid: str, delivery: str) -> None:
    with TestClient(create_app()) as client:
        resp = client.put(f"/api/projects/{pid}/delivery", json={"delivery": delivery})
        assert resp.status_code == 200, resp.text
        bad = client.put(f"/api/projects/{pid}/delivery", json={"delivery": "outro"})
        assert bad.status_code == 422


def test_preview_mixes_every_language_over_the_same_timeline(isolated_settings):
    pid = _project(["pt-BR", "en-US"], delivery="separate_videos")
    _set_delivery(pid, dubbing.MULTI_TRACK)
    assert service.get_doc(pid).style_overrides["languages"]["delivery"] == dubbing.MULTI_TRACK
    _run("script", pid)
    _run("storyboard", pid)
    _run("translate", pid)
    preview = _run("preview", pid)
    assert preview["delivery"] == dubbing.MULTI_TRACK and set(preview["langs"]) == {"pt-BR", "en-US"}
    data = isolated_settings.data_dir
    pt, en = preview["langs"]["pt-BR"], preview["langs"]["en-US"]
    tl_pt = json.loads((data / pt["timeline"]).read_text(encoding="utf-8"))
    tl_en = json.loads((data / en["timeline"]).read_text(encoding="utf-8"))
    # Mesma imagem: mesmas cenas, mesma duração, mesmos inícios de segmento.
    assert tl_en["scenes"] == tl_pt["scenes"] and tl_en["duration"] == tl_pt["duration"]
    assert [n["start"] for n in tl_en["narration"]] == [n["start"] for n in tl_pt["narration"]]
    assert all(not s["captions"] for s in tl_pt["scenes"])  # nada de legenda queimada
    for entry in (pt, en):
        assert abs(duration_of(data / entry["mix"]) - tl_pt["duration"]) < 0.1
        assert "-->" in (data / entry["srt"]).read_text(encoding="utf-8")
    # A narração traduzida (mais longa na voz de teste) cabe no segmento: acelerada ou com o segmento esticado.
    for n, seg_start in zip(tl_en["narration"], tl_en["narration"][1:], strict=False):
        assert n["start"] + n["duration"] <= seg_start["start"] + 1e-3
    assert preview["dub"]["langs"]["en-US"]["max_tempo"] <= dubbing.MAX_SPEEDUP + 1e-6
    # A voz de teste em inglês ("[en-US] " a mais) passa do tempo: alguma narração foi acelerada pelo atempo.
    assert preview["dub"]["langs"]["en-US"]["stretched"] > 0 and preview["generated"].get("dub", 0) > 0
    assert "dub_package" not in preview  # o pacote sai só do vídeo final

    # Segunda prévia: nada é refeito.
    again = _run("preview", pid)
    assert again["generated"].get("tts", 0) == 0 and again["generated"].get("dub", 0) == 0


def test_final_build_writes_studio_package_and_publish_sends_captions(isolated_settings, monkeypatch):
    pid = _project(["pt-BR", "en-US"])
    _run("script", pid)
    _run("storyboard", pid)
    _run("translate", pid)
    _run("metadata", pid)

    rendered: list[str] = []

    def fake_render(self, lang, timeline, mix_rel, images, entry):  # sem Remotion nos testes
        out = self.outputs_dir(lang) / "video.mp4"
        out.write_bytes(b"mp4")
        entry["video"] = self.store.rel(out)
        rendered.append(lang)

    monkeypatch.setattr(Builder, "_render_final", fake_render)
    final = _run("render", pid)
    assert rendered == ["pt-BR"]  # um vídeo só
    data = isolated_settings.data_dir
    en = final["langs"]["en-US"]
    assert en["mix"].endswith(".wav") and "video" not in en and en["dub_of"] == "pt-BR"
    timeline_s = final["langs"]["pt-BR"]["duration"]
    assert abs(duration_of(data / en["mix"]) - timeline_s) < 0.1

    package = final["dub_package"]
    folder = data / package["folder"]
    assert (folder / "en-US" / "audio-en-US.wav").exists()
    assert (folder / "en-US" / "legendas-en-US.srt").exists() and (folder / "en-US" / "titulo.txt").exists()
    assert (folder / "pt-BR" / "legendas-pt-BR.srt").exists() and not (folder / "pt-BR" / "audio-pt-BR.wav").exists()
    checklist = (folder / "CHECKLIST.md").read_text(encoding="utf-8")
    assert "Dublagem" in checklist and "audio-en-US.wav" in checklist and "recursos avançados" in checklist
    manifest = json.loads((folder / "pacote.json").read_text(encoding="utf-8"))
    assert manifest["primary"] == "pt-BR" and "en-US" in manifest["langs"]

    # Publicar o vídeo principal: títulos traduzidos e legendas de todos os idiomas vão pela API.
    sent: dict = {"captions": []}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None):
        sent.update(localizations=localizations)
        return {"id": "vid123"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "upload_captions", lambda vid, srt, lang, client, account=None: sent["captions"].append(lang))
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    result = _run("publish_youtube", pid, {"lang": "pt-BR", "privacy": "private"})
    assert "en-US" in (sent["localizations"] or {})
    assert sent["captions"] == ["pt-BR", "en-US"]
    assert any("Dublagem" in w for w in result["warnings"])
    with session_scope() as s:
        details = s.query(Publication).filter_by(project_id=pid, platform="youtube").one().details
    assert details["caption_langs"] == ["en-US"]

    # Pacote refeito depois do envio: link direto do vídeo no Studio.
    again = build_dub_package(pid)
    assert again["video_id"] == "vid123" and "vid123" in (data / again["checklist"]).read_text(encoding="utf-8")
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/projects/{pid}/publish/dub-package")
        assert resp.status_code == 200, resp.text


def test_dub_package_needs_a_multi_track_render(isolated_settings):
    pid = _project(["pt-BR", "en-US"], delivery="separate_videos")
    with pytest.raises(ValueError):
        build_dub_package(pid)


def test_separate_videos_is_unchanged(isolated_settings):
    pid = _project(["pt-BR", "en-US"], delivery="separate_videos")
    _run("script", pid)
    _run("storyboard", pid)
    _run("translate", pid)
    preview = _run("preview", pid)
    assert "delivery" not in preview and "dub" not in preview
    data = isolated_settings.data_dir
    tl_pt = json.loads((data / preview["langs"]["pt-BR"]["timeline"]).read_text(encoding="utf-8"))
    assert any(s["captions"] for s in tl_pt["scenes"])
