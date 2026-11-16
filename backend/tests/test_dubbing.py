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
    assert b.slot == 4.0 and b.tempos["en-US"] == pytest.approx(0.975, abs=0.001)  # curta: desacelera para ocupar
    assert dubbing.fit_warnings(fits, "pt-BR") == []


def test_fit_ignores_tiny_changes_and_limits_slowdown():
    fits = dubbing.fit_segments(["a", "b", "c"], {"pt-BR": {"a": 4.0, "b": 4.0, "c": 8.0},
                                                  "en-US": {"a": 4.012, "b": 3.99, "c": 4.0}}, "pt-BR")
    a, b, c = fits
    # +0.3% e -0.25%: o FFmpeg nem aplicaria (`to_wav` ignora abaixo de 0.5%); conta como 1.0.
    assert a.tempos["en-US"] == 1.0 and b.tempos["en-US"] == 1.0
    assert c.tempos["en-US"] == pytest.approx(dubbing.MIN_SLOWDOWN)  # metade do tempo: desacelera só até o limite
    for fit in fits:  # nunca passa da vaga
        assert fit.durations["en-US"] / fit.tempos["en-US"] <= fit.slot + 0.02
    summary = dubbing.fit_summary(fits, "pt-BR")["langs"]["en-US"]
    assert summary["min_tempo"] == pytest.approx(dubbing.MIN_SLOWDOWN) and summary["stretched"] == 1


def test_longest_language_defines_the_segment_and_warns():
    fits = dubbing.fit_segments(["a"], {"pt-BR": {"a": 5.0}, "en-US": {"a": 6.6}, "es-ES": {"a": 5.2}}, "pt-BR")
    fit = fits[0]
    assert fit.slot == pytest.approx(6.0) and fit.longest == "en-US"
    assert fit.tempos["en-US"] == pytest.approx(1.10, abs=0.001)  # no teto, cabe exato na vaga
    assert fit.tempos["es-ES"] == pytest.approx(dubbing.MIN_SLOWDOWN)  # sobra tempo: desacelera até o limite
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
    slow = dubbing.stretch(src, tmp_path / "lenta.wav", 0.95)
    assert wav_duration(slow) == pytest.approx(2.0 / 0.95, abs=0.02)
    with pytest.raises(ValueError):
        dubbing.stretch(src, tmp_path / "lenta-demais.wav", 0.8)


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
    seg.locked = True
    seg.text["en-US"] = " ".join(["word"] * (budgets[0].max_words * 2))
    assert dubbing.over_budget(doc, style, "en-US", skip_locked=True) == []


@pytest.mark.parametrize("speed", [0.8, 1.0, 1.2])
def test_budget_word_limit_matches_the_check_at_any_voice_speed(isolated_settings, speed):
    pid = _project(["pt-BR", "en-US"])
    _run("script", pid)
    doc = service.get_doc(pid)
    style = get_style(doc.style_id, {**doc.style_overrides, "voice": {"speed": speed}})
    assert style.voice.speed == speed
    budget = dubbing.segment_budgets(doc, style, "en-US")[0]
    wpm = dubbing._wpm(style, "en-US")
    assert budget.max_words == max(1, int(budget.seconds * speed * wpm / 60))
    # O limite do prompt é o mesmo que a checagem usa: exatamente o limite cabe no tempo, sem a folga da aceleração.
    seg = doc.segments[0]
    seg.text["en-US"] = " ".join(["word"] * budget.max_words)
    assert dubbing.speech_seconds(seg.text["en-US"], "en-US", style) <= budget.seconds + 1e-6
    assert dubbing.over_budget(doc, style, "en-US", tolerance=1.0) == []


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

    # Modo A: cada idioma tem vídeo próprio. Nada de orçamento no pedido, nem segunda rodada.
    calls.clear()
    doc.style_overrides = {}
    authoring.translate(doc, "en-US")
    assert len(calls) == 1
    assert "palavras (~" not in calls[0]["prompt"] and "não passe dele" not in calls[0]["prompt"]
    assert not any(s.get("budget") for s in calls[0]["context"]["segments"])


def test_shorten_job_rewrites_only_long_unlocked_segments(isolated_settings, monkeypatch):
    # Traduzido no Modo A e passado ao Modo B depois: a rodada de encurtar roda sozinha, sem traduzir de novo.
    pid = _project(["pt-BR", "en-US"], delivery="separate_videos")
    _run("script", pid)
    _run("translate", pid)
    doc = service.get_doc(pid)
    long_id, locked_id, ok_id = doc.segments[0].id, doc.segments[1].id, doc.segments[2].id
    doc.segment(long_id).text["en-US"] = "very " * 200 + "long."
    doc.segment(locked_id).text["en-US"] = "locked " * 200 + "text."
    doc.segment(locked_id).locked = True
    doc.segment(ok_id).text["en-US"] = "Fixed by hand."
    service.commit(pid, doc, "correções manuais")

    calls: list[dict] = []

    class Result:
        def __init__(self, data):
            self.data = data

    def fake_llm(task, system, prompt, schema, context, project_id, job_id):
        calls.append(context)
        return Result({"segments": [{"id": s["id"], "text": "Short one."} for s in context["segments"]]}), "fake"

    monkeypatch.setattr(authoring, "_llm", fake_llm)
    # Fora do Modo B não faz nada.
    assert _run("translate", pid, {"shorten": True})["shortened"] == {} and not calls

    with TestClient(create_app()) as client:
        resp = client.put(f"/api/projects/{pid}/delivery", json={"delivery": dubbing.MULTI_TRACK})
        assert resp.status_code == 200 and resp.json()["over_budget"] == {"en-US": [long_id]}
        assert client.get(f"/api/projects/{pid}/delivery/over-budget").json()["over_budget"] == {"en-US": [long_id]}
        assert client.get("/api/projects/nao-existe/delivery/over-budget").status_code == 404
    result = _run("translate", pid, {"shorten": True})
    assert result["shortened"] == {"en-US": [long_id]} and result["version"]
    assert [s["id"] for s in calls[0]["segments"]] == [long_id]
    after = service.get_doc(pid)
    assert after.segment(long_id).text["en-US"] == "Short one."
    assert after.segment(locked_id).text["en-US"].startswith("locked")  # travado: fica
    assert after.segment(ok_id).text["en-US"] == "Fixed by hand."  # correção manual: fica


# --------------------------------------------------------------------------------------------
# Build de ponta a ponta (voz de teste sintética)
# --------------------------------------------------------------------------------------------


def test_plan_in_multi_track_counts_the_primary_language(isolated_settings):
    from studio.workers.pipeline.build import plan

    pid = _project(["pt-BR", "en-US"])
    _run("script", pid)
    _run("storyboard", pid)
    _run("translate", pid)
    langs = {m["lang"] for m in plan(pid, langs=["en-US"], mode="preview")["tts_missing"]}
    assert langs == {"pt-BR", "en-US"}  # o build grava o principal também (referência do tempo)


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
    # As legendas do render final ficam numa cópia própria: uma prévia depois não mexe nelas.
    assert en["srt"].endswith("legendas-final.srt") and en["timeline"].endswith("timeline-final.json")
    final_srt = (data / en["srt"]).read_text(encoding="utf-8")
    doc = service.get_doc(pid)
    doc.segments[0].text["en-US"] = "A completely different line."
    service.commit(pid, doc, "tradução editada")
    _run("preview", pid)
    assert (data / en["srt"]).read_text(encoding="utf-8") == final_srt
    assert (data / en["srt"]).with_name("legendas.srt").read_text(encoding="utf-8") != final_srt

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
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id, account=None: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    result = _run("publish_youtube", pid, {"lang": "pt-BR", "privacy": "private"})
    assert "en-US" in (sent["localizations"] or {})
    assert sent["captions"] == ["pt-BR", "en-US"]
    assert any("Dublagem" in w for w in result["warnings"])
    with session_scope() as s:
        details = s.query(Publication).filter_by(project_id=pid, platform="youtube").one().details
    assert details["caption_langs"] == ["en-US"]

    # O idioma que é só faixa não tem vídeo para enviar: mensagem clara, e não "renderize de novo".
    job = get_runner().submit("publish_youtube", {"lang": "en-US", "privacy": "private"}, pid, run_inline=True)
    failed = get_job(job["id"])
    assert failed["status"] == "failed" and "faixa de áudio" in failed["error"]

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
    with TestClient(create_app()) as client:
        assert client.post("/api/projects/nao-existe/publish/dub-package").status_code == 404
        assert client.post("/api/projects/nao-existe/publish/dub-package/open").status_code == 404


def test_package_overwrites_without_wiping_the_folder(isolated_settings, tmp_path):
    pid = _project(["pt-BR", "en-US"])
    doc = service.get_doc(pid)
    data = isolated_settings.data_dir
    _tone(data / "mix-en.wav", 0.5)
    folder = tmp_path / "faixas"
    (folder / "en-US").mkdir(parents=True)
    (folder / "en-US" / "minhas-notas.txt").write_text("não apague", encoding="utf-8")
    (folder / "fr-FR").mkdir()  # idioma que saiu do projeto
    entries = {"en-US": {"mix": "mix-en.wav", "duration": 0.5}}
    dubbing.write_package(folder, doc=doc, entries=entries, data_dir=data)
    assert (folder / "en-US" / "audio-en-US.wav").exists() and (folder / "CHECKLIST.md").exists()
    assert (folder / "en-US" / "minhas-notas.txt").exists() and not (folder / "fr-FR").exists()
    dubbing.write_package(folder, doc=doc, entries=entries, data_dir=data)  # de novo, por cima
    assert (folder / "en-US" / "audio-en-US.wav").exists()


def test_final_render_survives_a_locked_package(isolated_settings, monkeypatch):
    pid = _project(["pt-BR", "en-US"])
    _run("script", pid)
    _run("storyboard", pid)
    _run("translate", pid)

    def fake_render(self, lang, timeline, mix_rel, images, entry):
        out = self.outputs_dir(lang) / "video.mp4"
        out.write_bytes(b"mp4")
        entry["video"] = self.store.rel(out)

    def busy(*args, **kwargs):
        raise PermissionError("arquivo aberto em outro programa")

    monkeypatch.setattr(Builder, "_render_final", fake_render)
    monkeypatch.setattr(dubbing, "write_package", busy)
    final = _run("render", pid)
    assert "dub_package" not in final and any("feche os arquivos" in w for w in final["warnings"])
    assert service.get_project_row(pid)["settings"]["last_final"]["langs"]["pt-BR"]["video"]  # render salvo
    with TestClient(create_app()) as client:
        resp = client.post(f"/api/projects/{pid}/publish/dub-package")
        assert resp.status_code == 409 and "feche os arquivos" in resp.json()["detail"]


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
