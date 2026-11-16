"""Conferência do vídeo final antes de publicar (ADR 0003, bloco H): gatilhos, conferência rápida, medidas num MP4
sintético do lavfi, folha de contato, job e trava do envio ao YouTube. Sem rede."""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api import routes_publish
from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import Segment
from studio.db import session_scope
from studio.ids import new_id
from studio.models import FactClaim, QualityReport
from studio.workers.factcheck.claims import segment_hash
from studio.workers.finalcheck import measure
from studio.workers.finalcheck.contact import contact_sheet, frame_times
from studio.workers.finalcheck.quick import load_context, quick_items, worst
from studio.workers.finalcheck.report import preflight, run_final_check
from studio.workers.finalcheck.triggers import late_triggers
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import youtube as yt

LANG = "pt-BR"


# ---------------------------------------------------------------- vídeo sintético


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True, capture_output=True)


def _make_video(path: Path, *, size: str = "320x180", music_in_gap: bool = False) -> Path:
    """testsrc de 10 s com 1 s de preto no meio (4,5 a 5,5 s) e um seno a −20 dBFS com um respiro de 3 a 4 s
    (silêncio, ou um tom 4 dB abaixo da "voz" quando `music_in_gap`)."""
    gap = "0.063*sin(2*PI*220*t)" if music_in_gap else "0"
    audio = f"aevalsrc='if(between(t\\,3\\,4)\\,{gap}\\,0.1*sin(2*PI*440*t))':s=48000:d=10"
    _ffmpeg("-f", "lavfi", "-i", f"testsrc=size={size}:rate=30:duration=10", "-f", "lavfi", "-i", audio,
            "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black:t=fill:enable='between(t,4.5,5.5)'",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))
    return path


NARRATION = [
    {"segment_id": "seg_1", "path": "", "start": 0.0, "duration": 3.0},
    {"segment_id": "seg_2", "path": "", "start": 4.0, "duration": 6.0},
]


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return _make_video(tmp_path_factory.mktemp("video") / "final.mp4")


# ---------------------------------------------------------------- gatilhos


def _scene(sid: str, kind: str, frm: int, frames: int, **extra: Any) -> dict[str, Any]:
    return {"id": sid, "kind": kind, "from": frm, "durationInFrames": frames, "text": "", "number": None,
            "captions": [], "words": [], **extra}


def test_triggers_on_synthetic_timeline():
    timeline = {
        "fps": 30,
        "scenes": [
            # número dito perto do fim: painel entra em 70 − 3 = 67, fica 23 quadros (0,8 s)
            _scene("s_reveal", "stat", 0, 90, card={"value": "42%", "label": "dos casos"}, reveal=70),
            # carimbo com 1,0 s antes do corte (mínimo 1,3 s)
            _scene("s_stamp", "image", 90, 90, card={"stamp": "1900", "stamp_from": 60}),
            # título numa cena de 1,5 s
            _scene("s_short", "title", 180, 45, text="O mistério do farol abandonado"),
            # tudo com folga: título de 4 s, carimbo padrão (6 quadros)
            _scene("s_ok", "title", 225, 120, text="Tudo certo", card={"stamp": "2013"}),
            # manchete de notícia curta não entra (news_reading_warnings já avisa)
            _scene("s_news", "image", 345, 30, news={"outlet": "X", "headline": "Uma manchete bem longa aqui"}),
        ],
    }
    items = late_triggers(timeline)
    by_scene = {i["scene_id"]: i for i in items}
    assert set(by_scene) == {"s_reveal", "s_stamp", "s_short"}
    assert all(i["code"] == "late_text" and i["level"] == "aviso" for i in items)
    assert "0,8 s na tela antes do corte (mínimo 2,2 s)" in by_scene["s_reveal"]["detail"]
    assert "'1900' fica 1,0 s na tela antes do corte (mínimo 1,3 s)" in by_scene["s_stamp"]["detail"]
    assert "1,5 s" in by_scene["s_short"]["detail"] and "divida a cena" in by_scene["s_short"]["detail"]
    assert by_scene["s_stamp"]["at_s"] == 5.0  # 90 + 60 quadros a 30 fps


def test_triggers_empty_data_card_uses_loose_stamp():
    """Cartão de dado vazio é desenhado como imagem (ou texto) com o carimbo solto: o carimbo tardio é conferido."""
    timeline = {"fps": 30, "scenes": [
        _scene("s_stat", "stat", 0, 90, src="img.png", card={"stamp": "1900", "stamp_from": 70}),
        _scene("s_cmp", "compare", 90, 90, card={"items": [{"label": "A"}], "stamp": "2020", "stamp_from": 80}),
        _scene("s_ok", "stat", 180, 90, src="img.png", card={"stamp": "1999"}),  # carimbo padrão: 6 quadros
    ]}
    items = late_triggers(timeline)
    assert [i["scene_id"] for i in items] == ["s_stat", "s_cmp"]
    assert all("carimbo" in i["title"] for i in items)


def test_triggers_kinetic_last_page_and_panel_stamp():
    words = [{"text": w, "from": f, "to": f + 5} for w, f in
             [("Um", 0), ("dois", 10), ("três.", 20), ("Quatro", 70), ("cinco", 75)]]
    timeline = {"fps": 30, "scenes": [
        _scene("s_kin", "text", 0, 120, words=words),  # última página entra no quadro 70: 1,7 s
        _scene("s_cmp", "compare", 120, 70, card={"items": [{"label": "A", "value": "1"}, {"label": "B", "value": "2"}],
                                                  "stamp": "2020"}),  # placar 2,3 s; carimbo do painel no quadro 10: 2,0 s
    ]}
    items = late_triggers(timeline)
    assert [i["scene_id"] for i in items] == ["s_kin"]
    assert "'Quatro cinco'" in items[0]["detail"]


# ---------------------------------------------------------------- conferência rápida


def _project(isolated_settings, style_id: str = "misterios-short", *, duration: float = 30.0,
             video: Path | None = None, preview_only: list[str] | None = None, version: int | None = None) -> str:
    doc = service.create_project(title="Farol", style_id=style_id)
    doc = doc.model_copy(update={"segments": [Segment(id="seg_1", text={LANG: SEG_TEXT})]})
    service.commit(doc.id, doc, "roteiro")
    row = service.get_project_row(doc.id)
    out = isolated_settings.outputs_dir / doc.id / LANG
    out.mkdir(parents=True, exist_ok=True)
    target = out / "farol.mp4"
    if video is not None:
        target.write_bytes(video.read_bytes())
    else:
        target.write_bytes(b"mp4 falso")
    (out / "legendas.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nOi\n", encoding="utf-8")
    timeline = {"version": version or row["current_version"], "fps": 30, "duration": duration, "scenes": [],
                "narration": NARRATION}
    (out / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
    rel = f"outputs/{doc.id}/{LANG}"
    service.update_settings(doc.id, {"last_final": {
        "langs": {LANG: {"video": f"{rel}/farol.mp4", "timeline": f"{rel}/timeline.json", "srt": f"{rel}/legendas.srt",
                         "duration": duration}},
        "preview_only_providers": preview_only or []}})
    return doc.id


SEG_TEXT = "O farol foi abandonado em 1900."


def _claim(pid: str, verdict: str, status: str = "aberta", seg_hash: str | None = None,
           segment_id: str = "seg_1") -> None:
    """Afirmação; sem `seg_hash`, com o hash do texto atual do segmento (checagem em dia)."""
    with session_scope() as s:
        s.add(FactClaim(id=new_id("fat"), project_id=pid, segment_id=segment_id, lang=LANG, claim="O farol é de 1900.",
                        verdict=verdict, status=status,
                        segment_hash=segment_hash(SEG_TEXT) if seg_hash is None else seg_hash))


def test_quick_short_too_long_with_open_claim_is_error(isolated_settings):
    pid = _project(isolated_settings, duration=200.0, preview_only=["edge_tts"])
    _claim(pid, "sem_fonte")
    _claim(pid, "boato")
    _claim(pid, "ok", status="aplicada", seg_hash="0000000000000000")  # texto mudou depois da checagem
    items = quick_items(load_context(pid, LANG))
    codes = {i["code"]: i for i in items}
    assert worst(items) == "erro"
    assert codes["short_duration"]["level"] == "erro" and "200 s passa de 3 min" in codes["short_duration"]["title"]
    assert codes["factcheck_open"]["level"] == "erro"
    assert codes["factcheck_rumor"]["level"] == "aviso"
    assert codes["factcheck_outdated"]["level"] == "aviso"
    assert codes["preview_only"]["level"] == "erro"
    assert codes["thumbnail"]["level"] == "aviso"  # sem miniatura
    assert "captions" not in codes  # legendas existem
    assert codes["audio_check"]["level"] == "ok"  # sem relatório de áudio: só informa
    assert codes["ai_label"]["level"] == "ok"


def test_quick_without_video_and_with_audio_report(isolated_settings):
    pid = _project(isolated_settings, style_id="curiosidades-longo", version=99)
    items = quick_items(load_context(pid, LANG))
    codes = {i["code"] for i in items}
    assert "final_version" in codes  # timeline da versão 99, projeto em outra
    assert "factcheck_missing" in codes  # estilo pede checagem e não há nenhuma
    with session_scope() as s:
        s.add(QualityReport(project_id=pid, kind="audio", lang=LANG, version=99, status="erro", items=[
            {"code": "sfx_tone", "level": "erro", "title": "Apito no efeito", "detail": "", "at_s": 2.0, "scene_id": None},
            {"code": "music_distance", "level": "aviso", "title": "Música alta", "detail": "", "at_s": None,
             "scene_id": None},
            {"code": "music_distance", "level": "aviso", "title": "Música alta 2", "detail": "", "at_s": None,
             "scene_id": None},
        ]))
    audio = [i for i in quick_items(load_context(pid, LANG)) if i["code"] == "audio_check"]
    assert [i["level"] for i in audio] == ["erro", "aviso"] and "2 avisos" in audio[1]["title"]
    # relatório de áudio de outra versão (e de outro mix): não trava, pede para rodar de novo
    with session_scope() as s:
        s.add(QualityReport(project_id=pid, kind="audio", lang=LANG, version=98, source="mix/velho.wav", status="erro",
                            items=[{"code": "sfx_tone", "level": "erro", "title": "Apito", "detail": "", "at_s": 1.0,
                                    "scene_id": None}]))
    audio = [i for i in quick_items(load_context(pid, LANG)) if i["code"] == "audio_check"]
    assert [i["level"] for i in audio] == ["aviso"] and "outra versão" in audio[0]["title"]
    # o mesmo mix do vídeo final vale mesmo com versão diferente
    last = service.get_project_row(pid)["settings"]["last_final"]
    service.update_settings(pid, {"last_final": {**last, "langs": {LANG: {**last["langs"][LANG], "mix": "mix/velho.wav"}}}})
    audio = [i for i in quick_items(load_context(pid, LANG)) if i["code"] == "audio_check"]
    assert [i["level"] for i in audio] == ["erro"]
    # sem o arquivo do vídeo: erro
    (isolated_settings.outputs_dir / pid / LANG / "farol.mp4").unlink()
    items = quick_items(load_context(pid, LANG))
    assert [i["code"] for i in items] == ["final_video"] and items[0]["level"] == "erro"


def test_quick_claims_of_deleted_or_edited_segments_are_outdated(isolated_settings):
    """Afirmação aberta de trecho apagado ou reescrito não trava: vira "checagem desatualizada" (regra do bloco F)."""
    pid = _project(isolated_settings)
    _claim(pid, "sem_fonte", segment_id="seg_apagado")  # segmento que não existe mais
    codes = {i["code"]: i for i in quick_items(load_context(pid, LANG))}
    assert "factcheck_open" not in codes and codes["factcheck_outdated"]["level"] == "aviso"
    assert "1 trecho" in codes["factcheck_outdated"]["title"]

    _claim(pid, "corrigir", seg_hash=segment_hash("texto antigo do trecho"))  # trecho reescrito à mão
    _claim(pid, "boato", seg_hash=segment_hash("texto antigo do trecho"))
    codes = {i["code"]: i for i in quick_items(load_context(pid, LANG))}
    assert "factcheck_open" not in codes and "factcheck_rumor" not in codes
    assert "2 trechos" in codes["factcheck_outdated"]["title"]

    _claim(pid, "sem_fonte")  # hash do texto atual: continua erro
    codes = {i["code"]: i for i in quick_items(load_context(pid, LANG))}
    assert codes["factcheck_open"]["level"] == "erro"


def test_quick_segment_without_text_in_lang_uses_empty_text(isolated_settings):
    """Sem texto no idioma, o hash é o do texto vazio (como no F), sem cair no idioma principal."""
    pid = _project(isolated_settings)
    doc = service.get_doc(pid)
    doc = doc.model_copy(update={"segments": [Segment(id="seg_1", text={"en": "The lighthouse."})]})
    service.commit(pid, doc, "só em inglês")
    _claim(pid, "ok", status="aplicada", seg_hash=segment_hash(""))
    codes = {i["code"] for i in quick_items(load_context(pid, LANG))}
    assert "factcheck_outdated" not in codes


def _final_report(pid: str, version: int, items: list[dict[str, Any]], data: dict[str, Any] | None = None) -> None:
    with session_scope() as s:
        s.add(QualityReport(project_id=pid, kind="final", lang=LANG, version=version,
                            source=f"outputs/{pid}/{LANG}/farol.mp4", status=worst(items), items=items,
                            data=data or {}))


BLACK = {"code": "black", "level": "aviso", "title": "Tela preta", "detail": "", "at_s": 1.0, "scene_id": None}


def test_freshness_by_file_and_version(isolated_settings):
    pid = _project(isolated_settings)
    version = service.get_project_row(pid)["current_version"]
    _final_report(pid, version, [BLACK])
    state = preflight(pid, LANG)
    assert state["fresh"] is True and any(i["code"] == "black" for i in state["items"])

    out = isolated_settings.outputs_dir / pid / LANG
    later = time.time() + 30
    os.utime(out / "farol.mp4", (later, later))  # render novo no mesmo caminho
    os.utime(out / "timeline.json", (later, later))
    state = preflight(pid, LANG)
    assert state["fresh"] is False and not any(i["code"] == "black" for i in state["items"])

    _final_report(pid, version, [BLACK])  # relatório novo do mesmo arquivo ...
    earlier = time.time() - 3600
    timeline = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    (out / "timeline.json").write_text(json.dumps({**timeline, "version": version + 1}), encoding="utf-8")
    os.utime(out / "farol.mp4", (earlier, earlier))
    os.utime(out / "timeline.json", (earlier, earlier))
    state = preflight(pid, LANG)  # ... mas a timeline do final é de outra versão
    assert state["fresh"] is False and not any(i["code"] == "black" for i in state["items"])


def test_preview_rewrite_of_timeline_is_not_the_final(isolated_settings):
    """A Prévia regrava o timeline.json depois do vídeo final: versão desconhecida, nada medido pela timeline nova."""
    pid = _project(isolated_settings, version=1)
    out = isolated_settings.outputs_dir / pid / LANG
    timeline = json.loads((out / "timeline.json").read_text(encoding="utf-8"))
    current = service.get_project_row(pid)["current_version"]
    (out / "timeline.json").write_text(json.dumps({**timeline, "version": current}), encoding="utf-8")
    later = time.time() + 30
    os.utime(out / "timeline.json", (later, later))
    ctx = load_context(pid, LANG)
    assert ctx.timeline_stale and ctx.version == 0 and ctx.duration == 30.0
    codes = {i["code"]: i for i in quick_items(ctx)}
    assert codes["final_timeline"]["level"] == "aviso" and "final_version" not in codes
    # relatório feito depois deste MP4 (antes da prévia, com a timeline do final): continua valendo
    _final_report(pid, 1, [BLACK])
    assert preflight(pid, LANG)["fresh"] is True


def test_final_check_skips_timeline_measures_when_preview_rewrote_it(isolated_settings, synthetic_video: Path,
                                                                        monkeypatch):
    pid = _project(isolated_settings, video=synthetic_video, duration=10.0)
    out = isolated_settings.outputs_dir / pid / LANG
    later = time.time() + 30
    os.utime(out / "timeline.json", (later, later))
    called: list[str] = []
    monkeypatch.setattr(measure, "voice_vs_music", lambda *a: called.append("voz") or ([], []))
    report = run_final_check(pid, LANG)
    assert called == [] and report["version"] == 0 and report["data"]["timeline_stale"] is True
    assert any(i["code"] == "timeline_skipped" for i in report["items"])
    assert preflight(pid, LANG)["fresh"] is True


# ---------------------------------------------------------------- medidas e folha


def test_measures_on_synthetic_mp4(synthetic_video: Path, tmp_path: Path):
    volume = measure.loudness(synthetic_video)
    assert volume["lufs"] is not None and -30 < volume["lufs"] < -15
    items = measure.loudness_items(volume, -14.0)
    assert items[0]["code"] == "lufs" and items[0]["level"] == "aviso"  # bem abaixo de −14
    assert measure.loudness_items({"lufs": -14.4, "true_peak": -0.5}, -14.0)[0]["level"] == "ok"
    assert [i["code"] for i in measure.loudness_items({"lufs": -14.0, "true_peak": -0.5}, -14.0)] == ["lufs", "true_peak"]

    black = measure.black_segments(synthetic_video)
    assert len(black) == 1 and abs(black[0]["start"] - 4.5) < 0.15 and abs(black[0]["dur"] - 1.0) < 0.15
    assert measure.black_items(black)[0]["code"] == "black"

    # respiro de 3 a 4 s em silêncio: música sozinha bem abaixo da voz, sem aviso
    pauses, warn = measure.voice_vs_music(synthetic_video, {"narration": NARRATION})
    assert len(pauses) == 1 and pauses[0]["diff_db"] > 10 and warn == []
    loud_gap = _make_video(tmp_path / "gap.mp4", music_in_gap=True)
    pauses, warn = measure.voice_vs_music(loud_gap, {"narration": NARRATION})
    assert pauses[0]["diff_db"] < 10 and warn[0]["code"] == "music_distance" and "0:03" in warn[0]["title"]

    sheet = contact_sheet(synthetic_video, tmp_path / "folha.jpg")
    assert sheet["tiles"] == 12 and (sheet["cols"], sheet["rows"]) == (3, 4)
    with Image.open(tmp_path / "folha.jpg") as im:
        assert im.size == (640 * 3, 360 * 4) and im.format == "JPEG"
    assert frame_times(12.0) == [0.5 + k for k in range(12)]


def test_vertical_contact_sheet(tmp_path: Path):
    video = _make_video(tmp_path / "vertical.mp4", size="180x320")
    sheet = contact_sheet(video, tmp_path / "v.jpg")
    assert (sheet["cols"], sheet["rows"]) == (6, 2)
    with Image.open(tmp_path / "v.jpg") as im:
        assert im.size == (270 * 6, 480 * 2)


def test_pauses_spread_and_rules():
    narr = [{"start": i * 2.0, "duration": 1.5} for i in range(17)]  # 16 respiros de 0,5 s
    picks = measure.pauses(narr)
    assert [round(p["start"], 1) for p in picks] == [5.5, 13.5, 21.5, 29.5]  # índices 2, 6, 10, 14
    assert measure.pauses([{"start": 0, "duration": 1.0}, {"start": 1.1, "duration": 1}]) == []  # 0,1 s não conta
    assert measure.duration_items(True, 181, False)[0]["level"] == "erro"
    assert measure.duration_items(True, 61, True)[0]["code"] == "short_editorial"
    assert measure.duration_items(True, 61, False) == [] and measure.duration_items(False, 900, True) == []


# ---------------------------------------------------------------- job e rota


def test_final_check_job_and_preflight(isolated_settings, synthetic_video: Path):
    pid = _project(isolated_settings, video=synthetic_video, duration=10.0)
    client = TestClient(create_app())
    before = client.get(f"/api/projects/{pid}/preflight", params={"lang": LANG}).json()
    assert before["report"] is None and before["fresh"] is False and before["video"].endswith("farol.mp4")
    job = get_runner().submit("final_check", {"lang": LANG}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    with session_scope() as s:
        row = s.query(QualityReport).filter_by(project_id=pid, kind="final").one()
        assert row.lang == LANG and row.source == f"outputs/{pid}/{LANG}/farol.mp4"
        assert row.version == service.get_project_row(pid)["current_version"]
        assert set(row.data) >= {"lufs", "lra", "true_peak", "duration", "pauses", "black"}
        assert len(row.data["black"]) == 1
        assert row.contact_sheet == f"outputs/{pid}/{LANG}/conferencia-quadros.jpg"
        codes = {i["code"] for i in row.items}
    assert {"lufs", "black"} <= codes
    assert (isolated_settings.data_dir / f"outputs/{pid}/{LANG}/conferencia-quadros.jpg").is_file()
    state = client.get(f"/api/projects/{pid}/preflight", params={"lang": LANG}).json()
    assert state["fresh"] is True and state["report"]["kind"] == "final"
    assert state["status"] in ("aviso", "erro") and state["quick"]
    assert client.get("/api/projects/nada/preflight").status_code == 404


def test_post_preflight_submits_job(isolated_settings, monkeypatch):
    pid = _project(isolated_settings)
    sent: dict = {}

    class Runner:
        def submit(self, kind, payload=None, project_id=None, **_):
            sent.update(kind=kind, payload=payload, project_id=project_id)
            return {"id": "job_1", "kind": kind}

    from studio.api import routes_checks
    monkeypatch.setattr(routes_checks, "get_runner", lambda: Runner())
    resp = TestClient(create_app()).post(f"/api/projects/{pid}/preflight", json={"lang": LANG})
    assert resp.status_code == 200 and sent == {"kind": "final_check", "payload": {"lang": LANG}, "project_id": pid}


def test_publish_gate(isolated_settings, monkeypatch):
    pid = _project(isolated_settings, preview_only=["edge_tts"])  # erro na conferência rápida
    sent: list[dict] = []

    class Runner:
        def submit(self, kind, payload=None, project_id=None, **_):
            sent.append(payload)
            return {"id": f"job_{len(sent)}", "kind": kind}

    monkeypatch.setattr(routes_publish, "get_runner", lambda: Runner())
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id, chosen=None: "global")
    client = TestClient(create_app())
    url = f"/api/projects/{pid}/publish/youtube"
    resp = client.post(url, json={"lang": LANG, "privacy": "public"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail.startswith("A conferência antes de publicar achou problema: Voz ou imagens de teste no vídeo")
    assert detail.endswith("Corrija ou envie como privado.")  # item que "publicar mesmo assim" não libera
    assert client.post(url, json={"lang": LANG, "privacy": "unlisted"}).status_code == 409
    future = "2099-01-01T12:00:00Z"
    assert client.post(url, json={"lang": LANG, "privacy": "scheduled", "publish_at": future}).status_code == 409
    assert sent == []
    assert client.post(url, json={"lang": LANG, "privacy": "private"}).status_code == 200
    # voz/imagens só de prévia: "publicar mesmo assim" não libera (o job de envio recusaria)
    resp = client.post(url, json={"lang": LANG, "privacy": "public", "ignore_checks": True})
    assert resp.status_code == 409 and resp.json()["detail"].startswith("Não dá para publicar mesmo assim")
    assert len(sent) == 1
    state = preflight(pid, LANG)
    assert state["status"] == "erro" and any(i.get("blocking") for i in state["items"])

    # erro que o envio aceita (Short longo): agendado trava; "publicar mesmo assim" passa
    pid = _project(isolated_settings, duration=200.0)
    url = f"/api/projects/{pid}/publish/youtube"
    resp = client.post(url, json={"lang": LANG, "privacy": "scheduled", "publish_at": future})
    assert resp.status_code == 409 and "publicar mesmo assim" in resp.json()["detail"]
    resp = client.post(url, json={"lang": LANG, "privacy": "public", "ignore_checks": True})
    assert resp.status_code == 200 and sent[-1]["ignore_checks"] is True


def test_batch_schedule_stops_on_check_error(isolated_settings):
    from studio.workers.publish.schedule import plan_youtube_batch

    pid = _project(isolated_settings, duration=200.0)  # Short de 200 s: erro
    submitted: list[str] = []
    results = plan_youtube_batch([{"project_id": pid, "lang": LANG}],
                                 lambda kind, payload, project_id: submitted.append(kind) or {"id": "job"})
    assert submitted == [] and "conferência antes de publicar" in results[0]["error"]
    assert "passa de 3 min" in results[0]["error"]
