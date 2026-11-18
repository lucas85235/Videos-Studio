"""Biblioteca de SFX (ADR 0004 §3.8): procedurais de evento, trava tonal, licença, resolução, importação do canais e
mixagem dos eventos (lib_id, ganho e queda da trilha)."""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.graph.store import ArtifactStore
from studio.media import audio, audio_check, sfx_library
from studio.media.ffmpeg import SAMPLE_RATE, read_wav, write_wav

SR = SAMPLE_RATE


def _sine(hz: float, seconds: float, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * SR)) / SR
    return (amp * np.sin(2 * np.pi * hz * t)).astype(np.float32)


def _noise(seconds: float, seed: int = 1, amp: float = 0.3) -> np.ndarray:
    return (amp * np.random.default_rng(seed).standard_normal(int(seconds * SR))).clip(-1, 1).astype(np.float32)


def _pink(seconds: float, seed: int = 2) -> np.ndarray:
    white = np.random.default_rng(seed).standard_normal(int(seconds * SR))
    spec = np.fft.rfft(white)
    spec /= np.sqrt(np.maximum(np.fft.rfftfreq(white.size, 1 / SR), 20.0))
    x = np.fft.irfft(spec, white.size)
    return (0.4 * x / np.abs(x).max()).astype(np.float32)


def _decaying_noise(seconds: float = 0.4, seed: int = 3) -> np.ndarray:
    x = _noise(seconds, seed, 0.6)
    return (x * np.exp(-8 * np.arange(x.size) / SR)).astype(np.float32)


def _rms_db(x: np.ndarray) -> float:
    return float(20 * np.log10(np.sqrt(np.mean(np.asarray(x, dtype=np.float64) ** 2)) + 1e-12))


# --- procedurais ----------------------------------------------------------------------------------------------------


def test_event_procedurals_offline_match_the_builtin_manifest(isolated_settings, tmp_path):
    """Todos os procedurais saem offline, deterministas, com a duração e o ponto forte do manifesto embutido, sem
    apito; os de evento são todos não tonais (entram no automático com `sfx_tonal_ok=False`)."""
    assert isolated_settings.offline
    builtin = {i.id: i for i in sfx_library.builtin_items()}
    assert set(builtin) == set(audio.SFX_KINDS)
    assert all(i.procedural and i.license == "own" and i.origin == "builtin" for i in builtin.values())
    for kind in audio.EVENT_SFX_KINDS:
        x, rate = read_wav(audio.generate_sfx(kind, tmp_path / f"{kind}.wav"))
        y, _ = read_wav(audio.generate_sfx(kind, tmp_path / f"{kind}-2.wav"))
        assert rate == SR and np.array_equal(x, y), kind
        item = builtin[kind]
        assert x.size / SR == pytest.approx(item.dur, abs=0.002), kind
        assert 0.04 <= item.dur <= 2.0, kind
        assert item.hit_s == audio.EVENT_SFX_HIT_S[kind]
        assert abs(sfx_library.hit_point(x) - item.hit_s) <= 0.1, kind
        assert audio.peak_momentary_lufs(x) == pytest.approx(audio.VOICE_LUFS + audio.SFX_TRIM_DB[kind], abs=0.6), kind
        assert np.abs(x).max() <= 10 ** (-0.95 / 20), kind
        assert not audio_check.tone_report(x)["apito"], kind
        metrics = sfx_library.tonal_metrics(x)
        assert metrics["tonal"] == item.tonal is False, (kind, metrics)
        assert item.auto(tonal_ok=False)
    # o manifesto embutido bate com os arquivos gerados (regravar com `write_builtin_manifest` quando um som mudar)
    for kind in audio.SFX_KINDS:
        entry = sfx_library.procedural_entry(kind, tmp_path / "m")
        assert entry["tonal"] == builtin[kind].tonal and entry["dur"] == pytest.approx(builtin[kind].dur, abs=0.002)
    raw = json.loads((sfx_library.BUILTIN_DIR / sfx_library.MANIFEST).read_text(encoding="utf-8"))
    assert raw["sfx_version"] == audio.SFX_VERSION
    # só procedural ou CC0 com origem e autor no repositório
    for entry in raw["items"]:
        assert entry["file"] is None or (entry["license"] == "cc0" and entry["source_url"] and entry["author"])
    listed = {e["file"] for e in raw["items"] if e["file"]}
    loose = [p.name for p in sfx_library.BUILTIN_DIR.rglob("*")
             if p.is_file() and p.name != sfx_library.MANIFEST and p.relative_to(sfx_library.BUILTIN_DIR).as_posix()
             not in listed]
    assert loose == [], "arquivo de som no repositório fora do manifesto (só procedural ou CC0 com origem)"


def test_transition_hit_points_unchanged_and_lookup_by_kind():
    assert audio.sfx_hit_s("whoosh") == 0.35 and audio.sfx_hit_s("riser_short") == 0.6
    assert audio.sfx_hit_s("nao-existe") == 0.0
    assert set(audio.SFX_HIT_S) == set(audio.TRANSITION_SFX_KINDS)
    assert audio.SFX_RETUNED_VERSION < audio.SFX_VERSION


# --- trava tonal ----------------------------------------------------------------------------------------------------


def test_tonal_lock_rejects_pure_sine_and_accepts_noise():
    for hz in (220, 440, 1000, 3000):
        m = sfx_library.tonal_metrics(_sine(hz, 0.6))
        assert m["tonal"] and m["tonal_share"] > 0.9 and m["flatness"] < 0.01, (hz, m)
    harmonic = sum(_sine(330 * k, 0.6, 0.3 / k) for k in range(1, 6))
    assert sfx_library.tonal_metrics(harmonic)["tonal"]
    for x in (_noise(0.6), _pink(0.6), _decaying_noise()):
        m = sfx_library.tonal_metrics(x)
        assert not m["tonal"] and m["tonal_share"] < 0.05, m
    assert sfx_library.tonal_metrics(_noise(0.6))["flatness"] > 0.4


def test_tonal_import_only_enters_auto_with_tonal_ok(isolated_settings, tmp_path):
    tone = write_wav(tmp_path / "plim.wav", _sine(600, 0.5))
    item = sfx_library.import_file(tone, kind="plim", license="cc0")
    assert item.tonal and not item.apito  # 600 Hz: nota, mas abaixo da faixa do apito
    assert item.auto(tonal_ok=True) and not item.auto(tonal_ok=False)
    assert [i.id for i in sfx_library.candidates("plim", tonal_ok=True)] == ["plim"]
    assert sfx_library.candidates("plim", tonal_ok=False) == []
    whistle = sfx_library.import_file(write_wav(tmp_path / "apito.wav", _sine(3000, 0.4)), kind="apito", license="own")
    assert whistle.apito and not whistle.auto() and "apito" in (whistle.warning() or "")


# --- licença --------------------------------------------------------------------------------------------------------


def test_unknown_license_stays_out_of_auto(isolated_settings, tmp_path):
    src = write_wav(tmp_path / "boing.wav", _decaying_noise())
    item = sfx_library.import_file(src, kind="boing")
    assert item.license == "desconhecida" and not item.auto()
    assert "licença desconhecida" in (item.warning() or "")
    assert sfx_library.candidates("boing") == [] and sfx_library.pick("boing", 1) is None
    assert any("boing" in w for w in sfx_library.warnings())
    # por tipo, sem item automático, cai no gerador procedural (whoosh, como antes)
    assert sfx_library.lookup("boing") is None
    # declarada a licença, entra
    declared = sfx_library.update("boing", license="cc0")
    assert declared.auto() and sfx_library.pick("boing", 1).id == "boing"
    with pytest.raises(sfx_library.SfxLibraryError):
        sfx_library.update("boing", license="cc_by")  # CC BY sem autor
    with pytest.raises(sfx_library.SfxLibraryError):
        sfx_library.import_file(src, kind="x", license="roubado")


def test_ids_never_shadow_procedurals_or_builtins(isolated_settings, tmp_path):
    src = write_wav(tmp_path / "impact.wav", _decaying_noise())
    item = sfx_library.import_file(src, kind="impact", license="own")
    assert item.id == "impact-2" and sfx_library.get("impact").origin == "builtin"
    again = sfx_library.import_file(src, kind="impact", license="own")
    assert again.id == "impact-3"
    with pytest.raises(sfx_library.SfxLibraryError):
        sfx_library.delete("impact")
    assert sfx_library.delete("impact-3") and not sfx_library.delete("impact-3")


# --- resolução ------------------------------------------------------------------------------------------------------


def test_resolve_prefers_library_id_then_procedural(isolated_settings, tmp_path):
    store = ArtifactStore()
    # tipo procedural: o gerador, na mesma chave do Builder
    pop = sfx_library.resolve("pop", store=store)
    assert pop == sfx_library.procedural_path("pop", store)
    x, _ = read_wav(pop)
    assert x.size / SR == pytest.approx(sfx_library.get("pop").dur, abs=0.002)
    # um "impact" da biblioteca não troca o som das transições de projetos antigos...
    lib = sfx_library.import_file(write_wav(tmp_path / "meu.wav", 0.05 * _decaying_noise(0.5)), kind="impact",
                                  license="own", lib_id="meu-impacto")
    assert sfx_library.resolve("impact", store=store) == sfx_library.procedural_path("impact", store)
    # ...mas entra pelo lib_id: 48 kHz mono, sem silêncio no começo, normalizado na referência da voz
    path = sfx_library.resolve(lib.id, store=store)
    y, rate = read_wav(path)
    assert rate == SR and audio.peak_momentary_lufs(y) == pytest.approx(audio.VOICE_LUFS, abs=0.6)
    assert sfx_library.mix_key(lib.id) == sfx_library.content_key(lib) and sfx_library.mix_key("impact") is None
    # tipo só da biblioteca (automático): o item
    sfx_library.import_file(write_wav(tmp_path / "palmas.wav", _decaying_noise(0.6, 9)), kind="palmas", license="cc0")
    assert sfx_library.lookup("palmas").id == "palmas"
    # tipo desconhecido: o gerador procedural (whoosh, como antes do ADR)
    unknown = sfx_library.resolve("nao-existe", store=store)
    whoosh, _ = read_wav(sfx_library.procedural_path("whoosh", store))
    assert np.array_equal(read_wav(unknown)[0], whoosh)


def test_event_start_keeps_old_events_and_lands_hit_on_the_word(isolated_settings):
    assert sfx_library.event_start({"type": "whoosh", "start": 1.65, "scene_id": "s1"}) == 1.65
    assert sfx_library.event_start({"type": "riser_short", "at": 3.0, "scene": "s1"}) == pytest.approx(2.4)
    assert sfx_library.event_start({"type": "boom", "at": 0.1, "scene": "s1"}) == pytest.approx(0.1)
    assert sfx_library.event_start({"type": "riser", "at": 0.5, "scene": "s1"}) == 0.0


def test_pick_is_deterministic_and_avoids_recent(isolated_settings, tmp_path):
    for n in range(3):
        sfx_library.import_file(write_wav(tmp_path / f"r{n}.wav", _decaying_noise(0.4, 20 + n)), kind="risada",
                                license="own", lib_id=f"risada-{n}")
    a = sfx_library.pick("risada", 7)
    assert a is not None and sfx_library.pick("risada", 7).id == a.id
    other = sfx_library.pick("risada", 7, exclude=[a.id])
    assert other is not None and other.id != a.id
    # todos usados: o tipo vale mais que a novidade
    assert sfx_library.pick("risada", 7, exclude=["risada-0", "risada-1", "risada-2"]) is not None
    # o procedural do tipo também é candidato
    assert sfx_library.pick("boom", 1).id == "boom"


def test_scan_picks_up_loose_files_as_unknown(isolated_settings):
    root = sfx_library.user_root()
    write_wav(root / "sub" / "tapa.wav", _decaying_noise())
    found = sfx_library.scan()
    assert [i.id for i in found] == ["tapa"] and found[0].license == "desconhecida" and found[0].file == "sub/tapa.wav"
    assert sfx_library.scan() == []


# --- importação do canais (pasta fixture; nunca a real) ---------------------------------------------------------------


def _canais_fixture(root: Path) -> Path:
    folder = root / "plantao-vice" / "video" / "public" / "sfx"
    write_wav(folder / "scratch.wav", _decaying_noise(0.5, 4))
    write_wav(folder / "click.wav", _decaying_noise(0.05, 5))
    write_wav(folder / "ding.wav", _sine(2400, 0.6))
    write_wav(folder / "trombone.wav", sum(_sine(233 * k, 1.0, 0.25 / k) for k in range(1, 6)))
    write_wav(folder / "static_orig.wav", _noise(0.3))
    return folder


def _snapshot(folder: Path) -> dict[str, tuple[int, int]]:
    return {p.name: (p.stat().st_size, p.stat().st_mtime_ns) for p in folder.rglob("*")}


def test_canais_import_from_fixture_folder(isolated_settings, tmp_path, monkeypatch):
    from studio.workers.canais_import import sfx as canais_sfx

    canais = tmp_path / "canais-fixture"
    folder = _canais_fixture(canais)
    monkeypatch.setattr(isolated_settings, "canais_dir", canais)
    assert canais_sfx.sfx_dir() == folder
    before = _snapshot(canais)

    dry = canais_sfx.import_sfx(dry_run=True)
    assert {r["id"] for r in dry.imported} == {"canais-scratch", "canais-click", "canais-ding", "canais-trombone"}
    assert sfx_library.user_items() == []

    report = canais_sfx.import_sfx()
    by_id = {r["id"]: r for r in report.imported}
    assert by_id["canais-scratch"]["license"] == "mixkit-free" and by_id["canais-scratch"]["auto"]
    assert by_id["canais-click"]["license"] == "desconhecida" and not by_id["canais-click"]["auto"]
    assert by_id["canais-ding"]["license"] == "mixkit-free" and not by_id["canais-ding"]["auto"]  # banido (apito)
    assert by_id["canais-trombone"]["tonal"] and not by_id["canais-trombone"]["auto"]  # banido
    assert any(r["file"] == "static_orig.wav" for r in report.skipped)
    scratch = sfx_library.get("canais-scratch")
    assert scratch.source_url.endswith("#704") and scratch.author == "Mixkit" and scratch.imported_from.startswith("canais")
    assert (sfx_library.user_root() / "canais-scratch.wav").is_file()
    assert any("canais-click" in w for w in report.warnings)
    # 2ª vez: nada repetido; a pasta do canais não mudou (só leitura)
    second = canais_sfx.import_sfx()
    assert second.imported == [] and len(second.skipped) == 5
    assert _snapshot(canais) == before
    # o tipo "scratch" continua no procedural; o do canais entra pelo lib_id
    assert sfx_library.lookup("scratch").origin == "builtin"


def test_canais_import_without_folder_only_warns(isolated_settings, tmp_path):
    from studio.workers.canais_import import sfx as canais_sfx

    report = canais_sfx.import_sfx(tmp_path / "nao-tem")
    assert report.imported == [] and report.warnings


# --- voz e Freesound --------------------------------------------------------------------------------------------------


def test_voice_sfx_generated_once_with_own_license(isolated_settings):
    items = sfx_library.ensure_voice_sfx("pm_alex")
    assert [i.id for i in items] == ["voz-ue-pm_alex", "voz-pera-pm_alex", "voz-oxe-pm_alex", "voz-ha-pm_alex"]
    assert all(i.license == "own" and i.kind == "voice" and i.auto(tonal_ok=False) for i in items)
    mtimes = [i.path().stat().st_mtime_ns for i in items]
    again = sfx_library.ensure_voice_sfx("pm_alex")
    assert [i.path().stat().st_mtime_ns for i in again] == mtimes


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, content: bytes = b"") -> None:
        self.status_code, self._payload, self.content = status, payload, content

    def json(self) -> dict:
        return self._payload or {}


class _FakeFreesound:
    def __init__(self, license_url: str, audio: bytes) -> None:
        self.license_url, self.audio, self.calls = license_url, audio, []

    def get(self, url: str, params: dict | None = None) -> _Resp:
        self.calls.append(url)
        if "/apiv2/sounds/" in url:
            return _Resp(200, {"id": 42, "name": "Big Boom", "username": "autor", "license": self.license_url,
                               "url": "https://freesound.org/people/autor/sounds/42/",
                               "previews": {"preview-hq-mp3": "https://cdn.freesound.org/previews/0/42-hq.mp3"}})
        return _Resp(200, content=self.audio)


def _wav_bytes(x: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def test_freesound_only_cc0(isolated_settings, monkeypatch):
    with pytest.raises(sfx_library.SfxLibraryError, match="offline"):
        sfx_library.fetch_freesound(42, kind="boom", api_key="k")
    monkeypatch.setattr(isolated_settings, "offline", False)
    audio_bytes = _wav_bytes(_decaying_noise())
    by = _FakeFreesound("http://creativecommons.org/licenses/by/4.0/", audio_bytes)
    with pytest.raises(sfx_library.SfxLibraryError, match="CC0"):
        sfx_library.fetch_freesound("https://freesound.org/people/autor/sounds/42/", kind="boom", client=by,
                                    api_key="k")
    assert len(by.calls) == 1  # nada baixado
    cc0 = _FakeFreesound("http://creativecommons.org/publicdomain/zero/1.0/", audio_bytes)
    item = sfx_library.fetch_freesound("42", kind="boom", tags=["meme"], client=cc0, api_key="k")
    assert item.id == "fs-42" and item.license == "cc0" and item.author == "autor" and item.auto()
    assert item.source_url.endswith("/sounds/42/")
    with pytest.raises(sfx_library.SfxLibraryError):
        sfx_library.freesound_id("abc42")


# --- rotas ----------------------------------------------------------------------------------------------------------


def test_sfx_routes(isolated_settings, tmp_path):
    client = TestClient(create_app())
    listed = client.get("/api/library/sfx").json()
    assert {i["id"] for i in listed["items"]} >= set(audio.SFX_KINDS)
    assert all(i["auto"] for i in listed["items"] if i["id"] in audio.EVENT_SFX_KINDS)
    data = _wav_bytes(_decaying_noise())
    up = client.post("/api/library/sfx", data={"kind": "tapa", "tags": "meme, piada"},
                     files={"file": ("tapa.wav", data, "audio/wav")})
    assert up.status_code == 200, up.text
    body = up.json()
    assert body["license"] == "desconhecida" and not body["auto"] and body["warning"]
    assert body["tags"] == ["meme", "piada"]
    bad = client.post("/api/library/sfx", data={"kind": "tapa", "license": "pirata"},
                      files={"file": ("t.wav", data, "audio/wav")})
    assert bad.status_code == 422
    wrong = client.post("/api/library/sfx", data={"kind": "x"}, files={"file": ("t.txt", b"oi", "text/plain")})
    assert wrong.status_code == 422
    patched = client.patch("/api/library/sfx/tapa", json={"license": "own"}).json()
    assert patched["auto"] and patched["warning"] is None
    wav = client.get("/api/library/sfx/tapa/audio")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert client.get("/api/library/sfx/pop/audio").status_code == 200
    assert client.delete("/api/library/sfx/pop").status_code == 422
    assert client.delete("/api/library/sfx/tapa").status_code == 200
    assert client.delete("/api/library/sfx/tapa").status_code == 404
    assert client.post("/api/library/sfx/fetch-freesound", json={"sound": "42", "kind": "boom"}).status_code == 503
    voice = client.post("/api/library/sfx/voice", json={"voice": "pm_alex"}).json()
    assert len(voice["items"]) == 4
    dry = client.post("/api/library/sfx/import-canais", json={"dry_run": True}).json()
    assert "imported" in dry and "warnings" in dry


# --- mixagem --------------------------------------------------------------------------------------------------------


def test_music_drop_silences_the_track(tmp_path):
    total = 6.0
    voice = write_wav(tmp_path / "voice.wav", np.zeros(int(total * SR), dtype=np.float32))
    music = audio.generate_music("upbeat", 8.0, 3, tmp_path / "music.wav")
    plain, _ = audio.music_stem(voice, music, tmp_path / "a.wav", total=total)
    dropped, _ = audio.music_stem(voice, music, tmp_path / "b.wav", total=total,
                                  music_drops=[{"at": 3.0, "dur": 0.45}])
    a, _ = read_wav(plain)
    b, _ = read_wav(dropped)
    inside = slice(int(3.05 * SR), int(3.4 * SR))
    assert _rms_db(a[inside]) > -45 and _rms_db(b[inside]) < -70
    outside = slice(int(1.0 * SR), int(2.5 * SR))
    assert np.allclose(a[outside], b[outside], atol=2e-4)
    # a duração da queda fica entre 0,3 e 0,6 s, dentro do vídeo e sem sobreposição
    assert audio.clamp_drops([{"at": 1.0, "dur": 2.0}, {"at": 1.2, "dur": 0.1}, {"at": 9, "dur": 0.4}], total) == [
        (1.0, 1.6)]
    assert audio.clamp_drops([{"at": 1.0, "dur": 0.1}, {"bad": 1}], total) == [(1.0, 1.3)]
    assert audio.clamp_drops(None, total) == []


def test_builder_mixes_event_sfx_by_lib_id_gain_and_drop(isolated_settings, tmp_path):
    """O mix da prévia offline toca os SFX de evento (lib_id, at, gain_db) e a queda da trilha; a timeline antiga
    (sem nada disso) mantém a mesma chave de mix."""
    from studio.core.project import service
    from studio.media.audio_check import decode
    from studio.workers.jobs import get_job, get_runner
    from studio.workers.pipeline.build import Builder

    project_id = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado").id
    final = None
    for kind in ("script", "storyboard", "preview"):
        job = get_runner().submit(kind, {}, project_id, run_inline=True)
        final = get_job(job["id"])
        assert final is not None and final["status"] == "done", final and final["error"]
    entry = final["result"]["langs"]["pt-BR"]
    timeline = json.loads((isolated_settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    tts = {n["segment_id"]: Path(n["path"]).parent.name for n in timeline["narration"]}
    builder = Builder(project_id)
    assert builder.mix("pt-BR", timeline, tts) == entry["mix"]  # timeline antiga: a mesma chave, sem refazer

    lib = sfx_library.import_file(write_wav(tmp_path / "tapa.wav", _decaying_noise(0.5, 11)), kind="tapa",
                                  license="own", lib_id="tapa")
    at = round(min(2.0, timeline["duration"] / 2), 3)
    scene = timeline["scenes"][0]["id"]
    events = [*timeline["sfx"], {"type": "sfx", "at": at, "scene": scene, "lib_id": lib.id, "gain_db": 3.0},
              {"type": "boom", "at": at + 1.0, "scene": scene}]
    edited = {**timeline, "sfx": events, "music_drops": [{"at": at - 0.5, "dur": 0.4}]}
    rel = builder.mix("pt-BR", edited, tts)
    assert rel != entry["mix"]
    plan = builder.sfx_plan(edited)
    tapa = next(p for p in plan if p["ref"] == "tapa")
    assert tapa["start"] == pytest.approx(at) and tapa["gain_db"] == 3.0
    boom = next(p for p in plan if p["ref"] == "boom")
    assert boom["start"] == pytest.approx(at + 1.0)
    before = decode(isolated_settings.data_dir / entry["mix"])
    after = decode(isolated_settings.data_dir / rel)
    n = min(before.size, after.size)
    before, after = before[:n], after[:n]
    # o master renormaliza o mix: tira o "antes" na escala certa (medida longe dos eventos) e olha o que sobrou
    control = np.ones(n, dtype=bool)
    control[int((at - 0.7) * SR):int((at + 2.0) * SR)] = False
    k = float(np.dot(after[control], before[control]) / np.dot(before[control], before[control]))
    residual = after - k * before
    floor = _rms_db(residual[control])
    tapa_win = slice(int(at * SR), int((at + 0.15) * SR))
    boom_win = slice(int((at + 1.0) * SR), int((at + 1.2) * SR))
    assert _rms_db(residual[tapa_win]) - floor > 12  # o tapa da biblioteca é audível na palavra
    assert _rms_db(residual[boom_win]) - floor > 12  # o boom procedural também
    # lib_id que sumiu da biblioteca: toca o tipo, com aviso
    gone = {**timeline, "sfx": [{"type": "boom", "at": at, "scene": scene, "lib_id": "sumiu"}]}
    assert [p["ref"] for p in builder.sfx_plan(gone)] == ["boom"]
    assert any("sumiu" in w for w in builder.warnings)
