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


def test_cc_by_never_enters_auto(isolated_settings, tmp_path):
    """A descrição ainda não credita SFX: CC BY só toca pelo lib_id (escolha de alguém), com o crédito no aviso."""
    src = write_wav(tmp_path / "palmas.wav", _decaying_noise(0.6, 13))
    item = sfx_library.import_file(src, kind="palmas", license="cc_by", author="Fulano",
                                   source_url="https://freesound.org/s/1/")
    assert not item.auto() and "CC BY" in (item.warning() or "")
    assert sfx_library.candidates("palmas") == [] and sfx_library.pick("palmas", 1) is None
    assert sfx_library.lookup(item.id, explicit=True).id == item.id
    assert "Fulano" in item.credit() and "https://freesound.org/s/1/" in item.credit()


def test_bad_user_manifest_never_breaks_resolution(isolated_settings, tmp_path):
    root = sfx_library.user_root()
    good = sfx_library.import_file(write_wav(tmp_path / "bom.wav", _decaying_noise(0.4, 30)), kind="bom",
                                   license="own")
    raw = json.loads((root / sfx_library.MANIFEST).read_text(encoding="utf-8"))
    raw["items"] += [{**raw["items"][0], "id": "maiusculo", "license": "CC0"},  # editado à mão: aceito
                     {"id": "ruim", "file": "ruim.wav", "kind": "x", "license": "roubada"},
                     "nem-objeto"]
    (root / sfx_library.MANIFEST).write_text(json.dumps(raw), encoding="utf-8")
    ids = {i.id for i in sfx_library.user_items()}
    assert ids == {good.id, "maiusculo"} and sfx_library.get("maiusculo").license == "cc0"
    issues = sfx_library.warnings()
    assert any("'ruim'" in w for w in issues) and any("'?'" in w for w in issues)
    assert sfx_library.lookup("whoosh").id == "whoosh" and sfx_library.lookup("bom").id == good.id
    # gravar de novo não apaga as entradas inválidas (o usuário corrige à mão)
    sfx_library.update(good.id, tags=["x"])
    saved = json.loads((root / sfx_library.MANIFEST).read_text(encoding="utf-8"))["items"]
    assert {"id": "ruim", "file": "ruim.wav", "kind": "x", "license": "roubada"} in saved and "nem-objeto" in saved
    # JSON corrompido: nada do usuário, aviso, e o tipo procedural resolve sem ler o manifesto
    (root / sfx_library.MANIFEST).write_text("{ quebrado", encoding="utf-8")
    assert sfx_library.user_items() == [] and sfx_library.event_ref({"type": "whoosh", "lib_id": "bom"}) == (
        "whoosh", False)
    assert sfx_library.lookup("whoosh").id == "whoosh" and sfx_library.lookup("riser").id == "riser"
    assert any("ilegível" in w for w in sfx_library.warnings())
    # gravar por cima guarda o ilegível ao lado
    sfx_library.import_file(write_wav(tmp_path / "novo.wav", _decaying_noise(0.4, 31)), kind="novo", license="own")
    assert (root / f"{sfx_library.MANIFEST}.ilegivel").read_text(encoding="utf-8") == "{ quebrado"


def test_tonal_lock_swaps_the_tonal_riser(isolated_settings):
    """Com `sfx_tonal_ok=False` o riser de transição (tonal) vira o riser_short, com o ponto forte no mesmo lugar."""
    assert sfx_library.get("riser").tonal and not sfx_library.get("riser_short").tonal
    assert sfx_library.lookup("riser").id == "riser"
    assert sfx_library.lookup("riser", tonal_ok=False).id == "riser_short"
    old = {"type": "riser", "start": 1.5, "scene_id": "s1"}  # timeline: palavra em 3,0 - 1,5
    assert sfx_library.event_start(old) == 1.5
    assert sfx_library.event_start(old, tonal_ok=False) == pytest.approx(3.0 - 0.6)
    assert sfx_library.event_start({"type": "riser", "at": 3.0}, tonal_ok=False) == pytest.approx(2.4)


def test_imported_short_sounds_follow_the_type_trim_and_onset(isolated_settings, tmp_path):
    # um "click" importado soa como o click procedural (mesmo ajuste do tipo), não 13 dB acima
    click = _decaying_noise(0.06, 14)
    item = sfx_library.import_file(write_wav(tmp_path / "clique.wav", click), kind="click", license="own")
    y, _ = read_wav(sfx_library.prepared_path(item))
    assert audio.peak_momentary_lufs(y) == pytest.approx(audio.VOICE_LUFS + audio.SFX_TRIM_DB["click"], abs=0.6)
    # curto de tipo sem ajuste: abaixo da referência
    beep = sfx_library.import_file(write_wav(tmp_path / "tec.wav", _decaying_noise(0.08, 15)), kind="tec",
                                   license="own")
    z, _ = read_wav(sfx_library.prepared_path(beep))
    assert audio.peak_momentary_lufs(z) == pytest.approx(audio.VOICE_LUFS + sfx_library.SHORT_SFX_TRIM_DB, abs=0.6)
    # som longo (plateia): o ponto forte é o ataque, não o pico no meio
    t = np.arange(int(3.0 * SR)) / SR
    crowd = (_noise(3.0, 16, 0.5) * np.clip(np.minimum(t / 0.05, 1.0) * (0.45 + 0.55 * np.exp(-((t - 1.6) / 0.3) ** 2)),
                                            0, 1)).astype(np.float32)
    assert sfx_library.hit_point(crowd) > 1.0  # o pico
    cheer = sfx_library.import_file(write_wav(tmp_path / "plateia.wav", crowd), kind="cheer", license="own")
    assert cheer.hit_s < 0.1
    # o que sobe até o golpe fica no pico
    rise = (_noise(1.0, 17) * np.linspace(0.05, 1.0, SR)).astype(np.float32)
    assert sfx_library.import_file(write_wav(tmp_path / "sobe.wav", rise), kind="riser", license="own").hit_s > 0.8


def test_final_check_flags_library_licenses(isolated_settings, tmp_path):
    """Conferência final: SFX de licença desconhecida escolhido pelo planejador é erro; pelo usuário, aviso; CC BY
    dá a linha de crédito; meme de terceiros, aviso de reivindicação. Projeto sem eventos: nada."""
    from types import SimpleNamespace

    from studio.core.project.document import FxEvent
    from studio.media import meme_library
    from studio.workers.finalcheck.quick import QUICK_CODES, library_items

    src = write_wav(tmp_path / "x.wav", _decaying_noise(0.4, 50))
    sfx_library.import_file(src, kind="tapa", lib_id="tapa")
    sfx_library.import_file(src, kind="palmas", license="cc_by", author="Fulano", lib_id="palmas",
                            source_url="https://freesound.org/s/9/")
    png = tmp_path / "m.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    meme_library.add(png, license="fair_comment", tags=["filme"], lib_id="filme")

    def doc(*events: FxEvent) -> SimpleNamespace:
        return SimpleNamespace(ordered_scenes=lambda: [SimpleNamespace(fx=list(events))])

    assert library_items(doc()) == [] and "library_license" in QUICK_CODES
    planner = FxEvent(type="sfx", at="palavra", params={"lib_id": "tapa"}, origin="planner")
    items = library_items(doc(planner))
    assert [(i["level"], i["code"]) for i in items] == [("erro", "library_license")] and "'tapa'" in items[0]["detail"]
    user = FxEvent(type="sfx", at="palavra", params={"lib_id": "tapa"}, origin="user")
    assert [i["level"] for i in library_items(doc(user))] == ["aviso"]
    credit = FxEvent(type="punch", at="palavra", params={"sfx_id": "palmas"}, origin="user")
    meme = FxEvent(type="meme", at="palavra", params={"lib_id": "filme"}, origin="user")
    out = library_items(doc(credit, meme))
    assert [i["title"] for i in out] == ["Crédito CC BY a pôr na descrição", "Meme de terceiros no vídeo"]
    assert "Fulano" in out[0]["detail"] and "https://freesound.org/s/9/" in out[0]["detail"]


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
    assert sfx_library.mix_key(lib.id) == sfx_library.prep_key(lib) and sfx_library.mix_key("impact") is None
    # tipo só da biblioteca (automático): o item
    sfx_library.import_file(write_wav(tmp_path / "palmas.wav", _decaying_noise(0.6, 9)), kind="palmas", license="cc0")
    assert sfx_library.lookup("palmas").id == "palmas"
    # tipo desconhecido: o gerador procedural (whoosh, como antes do ADR)
    unknown = sfx_library.resolve("nao-existe", store=store)
    whoosh, _ = read_wav(sfx_library.procedural_path("whoosh", store))
    assert np.array_equal(read_wav(unknown)[0], whoosh)


def test_event_start_keeps_old_events_and_lands_hit_on_the_word(isolated_settings, tmp_path):
    assert sfx_library.event_start({"type": "whoosh", "start": 1.65, "scene_id": "s1"}) == 1.65
    assert sfx_library.event_start({"type": "riser_short", "at": 3.0, "scene": "s1"}) == pytest.approx(2.4)
    assert sfx_library.event_start({"type": "boom", "at": 0.1, "scene": "s1"}) == pytest.approx(0.1)
    assert sfx_library.event_start({"type": "riser", "at": 0.5, "scene": "s1"}) == 0.0
    # formato real do timeline-fx (`edit_resolve.resolve_fx`): `start` já adiantado pelo SFX_LEAD do tipo (0,12 s
    # para os tipos de evento) E `at`. Quem manda é o `at`: o ponto forte cai na palavra.
    real = {"type": "riser_short", "start": 2.88, "scene_id": "s1", "at": 3.0, "scene": "s1"}
    assert sfx_library.event_start(real) == pytest.approx(2.4)
    assert sfx_library.event_start({**real, "type": "pop", "start": 2.88}) == pytest.approx(3.0)
    lib = sfx_library.import_file(write_wav(tmp_path / "golpe.wav", np.concatenate(
        [np.zeros(int(0.3 * SR), dtype=np.float32), _decaying_noise(0.4, 12)])), kind="golpe", license="own")
    lib = sfx_library.update(lib.id, hit_s=0.3)
    assert sfx_library.event_start({**real, "type": "impact", "start": 3.0, "lib_id": lib.id}) == pytest.approx(2.7)
    # transição do timeline-fx com `at` (SFX_LEAD == hit_s): o mesmo início de antes
    assert sfx_library.event_start({"type": "whoosh", "start": 2.65, "at": 3.0}) == pytest.approx(2.65)


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


def test_pick_prefers_recordings_over_procedural(isolated_settings, tmp_path):
    """Real como padrão: com gravação automática do tipo, o procedural fica de reserva (todas as gravações usadas)."""
    for n in range(2):
        sfx_library.import_file(write_wav(tmp_path / f"b{n}.wav", _decaying_noise(0.5, 40 + n)), kind="boom",
                                license="cc0", lib_id=f"boom-real-{n}")
    picks = {sfx_library.pick("boom", seed).id for seed in range(20)}
    assert picks == {"boom-real-0", "boom-real-1"}
    assert sfx_library.pick("boom", 3, exclude=["boom-real-0", "boom-real-1"]).id == "boom"
    assert sfx_library.pick("boom", 3, exclude=["boom-real-0", "boom-real-1", "boom"]).id.startswith("boom-real")
    # a resolução por tipo (transições de projetos antigos) não muda: o procedural
    assert sfx_library.lookup("boom").id == "boom"


def test_scan_picks_up_loose_files_as_unknown(isolated_settings, monkeypatch):
    root = sfx_library.user_root()
    write_wav(root / "sub" / "tapa.wav", _decaying_noise())
    (root / "quebrado.mp3").write_bytes(b"nao e audio")
    assert sorted(p.name for p in sfx_library.loose_files()) == ["quebrado.mp3", "tapa.wav"]
    result = sfx_library.scan()
    found = result["new"]
    assert [i.id for i in found] == ["tapa"] and found[0].license == "desconhecida" and found[0].file == "sub/tapa.wav"
    assert [f["file"] for f in result["failed"]] == ["quebrado.mp3"]
    # o que falhou não é decodificado de novo enquanto o arquivo não muda
    calls = []
    monkeypatch.setattr(sfx_library, "decode", lambda path: calls.append(path) or _decaying_noise())
    again = sfx_library.scan()
    assert again["new"] == [] and [f["file"] for f in again["failed"]] == ["quebrado.mp3"] and calls == []
    # o GET não importa nada (sem efeito colateral)
    write_wav(root / "solto.wav", _decaying_noise(0.3, 8))
    client = TestClient(create_app())
    listed = client.get("/api/library/sfx").json()
    assert "solto.wav" in listed["loose"] and sfx_library.get("solto") is None


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

    # sem a conferência da licença item a item, os rastreados do Mixkit entram como "desconhecida", com o link
    # do arquivo no Mixkit para conferir
    report = canais_sfx.import_sfx()
    by_id = {r["id"]: r for r in report.imported}
    assert by_id["canais-scratch"]["license"] == "desconhecida" and not by_id["canais-scratch"]["auto"]
    assert by_id["canais-click"]["license"] == "desconhecida" and not by_id["canais-click"]["auto"]
    assert any(r["file"] == "static_orig.wav" for r in report.skipped)
    scratch = sfx_library.get("canais-scratch")
    assert scratch.source_url == "https://assets.mixkit.co/active_storage/sfx/704/704-preview.mp3"
    assert scratch.author == "Mixkit" and scratch.imported_from.startswith("canais") and "Restricted" in scratch.note
    assert (sfx_library.user_root() / "canais-scratch.wav").is_file()
    assert any("canais-click" in w for w in report.warnings)
    # conferida pelo usuário: mixkit-free (os banidos continuam fora do automático)
    checked = canais_sfx.import_sfx(replace=True, license_checked=True)
    by_id = {r["id"]: r for r in checked.imported}
    assert by_id["canais-scratch"]["license"] == "mixkit-free" and by_id["canais-scratch"]["auto"]
    assert by_id["canais-ding"]["license"] == "mixkit-free" and not by_id["canais-ding"]["auto"]  # banido (apito)
    assert by_id["canais-trombone"]["tonal"] and not by_id["canais-trombone"]["auto"]  # banido
    # refazer sem a conferência não desfaz a licença declarada
    sfx_library.update("canais-click", license="own")
    redo = {r["id"]: r for r in canais_sfx.import_sfx(replace=True).imported}
    assert redo["canais-click"]["license"] == "own" and redo["canais-scratch"]["license"] == "mixkit-free"
    # 2ª vez sem replace: nada repetido; a pasta do canais não mudou (só leitura)
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


def _final_tts(provider_id: str = "fake_final"):
    """Voz "final" falsa para os testes (a saída é a do mock, mas o provedor se declara comercial)."""
    import dataclasses

    from studio.providers.tts import MockTTS

    class FakeFinal(MockTTS):
        info = dataclasses.replace(MockTTS.info, id=provider_id, name="Voz final de teste", commercial_use=True)

    return FakeFinal()


def test_voice_sfx_uses_the_channel_final_voice_never_the_test_voice(isolated_settings):
    from studio.providers import registry

    # offline (ou sem voz final pronta): recusa, nada de seno de 180 Hz na biblioteca
    with pytest.raises(sfx_library.SfxLibraryError, match="offline"):
        sfx_library.ensure_voice_sfx(style_id="misterios-short")
    target = sfx_library.VoiceTarget(provider="mock_tts", voice="pm_alex")
    with pytest.raises(sfx_library.SfxLibraryError, match="voz de teste"):
        sfx_library.ensure_voice_sfx(target=target, provider=registry.get("mock_tts"))
    assert sfx_library.user_items() == []

    target = sfx_library.VoiceTarget(provider="fake_final", voice="Charon", source="canal x")
    tts = _final_tts()
    items = sfx_library.ensure_voice_sfx(target=target, provider=tts)
    assert [i.id for i in items] == ["voz-ue-fake_final-charon", "voz-pera-fake_final-charon",
                                     "voz-oxe-fake_final-charon", "voz-ha-fake_final-charon"]
    assert all(i.license == "own" and i.kind == "voice" and i.auto(tonal_ok=False) for i in items)
    assert all(i.imported_from == "voz:fake_final:Charon" for i in items)
    mtimes = [i.path().stat().st_mtime_ns for i in items]
    again = sfx_library.ensure_voice_sfx(target=target, provider=tts)
    assert [i.path().stat().st_mtime_ns for i in again] == mtimes
    # outra voz: outro item (o provedor e a voz estão no id)
    other = sfx_library.ensure_voice_sfx(target=sfx_library.VoiceTarget(provider="fake_final", voice="Kore"),
                                         provider=tts, words={"ue": "Ué?"})
    assert other[0].id == "voz-ue-fake_final-kore"


def test_voice_target_reads_the_channel_final_voice(isolated_settings, monkeypatch):
    from studio.db import session_scope
    from studio.models import Channel
    from studio.providers import registry

    monkeypatch.setattr(isolated_settings, "offline", False)
    tts = _final_tts("gemini_tts")
    monkeypatch.setitem(registry._PROVIDERS, "gemini_tts", tts)
    with session_scope() as s:
        s.add(Channel(id="canal1", name="Canal", voice_defaults={"final": {"provider": "gemini_tts", "voice": "Charon"}}))
        s.add(Channel(id="canal2", name="Sem voz", default_style_id="misterios-short"))
    target = sfx_library.voice_target("canal1")
    assert target.provider == "gemini_tts" and target.voice == "Charon"
    # sem voz no canal: a cadeia da voz final do estilo padrão dele (o 1º provedor comercial disponível)
    assert sfx_library.voice_target("canal2").provider == "gemini_tts"
    with pytest.raises(sfx_library.SfxLibraryError, match="canal"):
        sfx_library.voice_target()
    monkeypatch.setattr(tts, "availability", lambda: (False, "sem chave"))
    with pytest.raises(sfx_library.SfxLibraryError, match="sem chave"):
        sfx_library.voice_target("canal1")


def test_old_mock_voice_items_stay_out_of_auto(isolated_settings, tmp_path):
    """Interjeição gravada com a voz de teste antes da correção (o seno): fica fora do automático, com aviso."""
    src = write_wav(tmp_path / "ue.wav", _sine(180, 0.5))
    item = sfx_library.import_file(src, kind="voice", license="own", lib_id="voz-ue-pm_alex",
                                   author="voz do canal (mock_tts)", imported_from="voz:pm_alex")
    assert item.placeholder and not item.auto() and "voz de teste" in (item.warning() or "")
    assert sfx_library.candidates("voice") == []


class _Resp:
    def __init__(self, status: int, payload: dict | None = None, content: bytes = b"") -> None:
        self.status_code, self._payload, self.content = status, payload, content

    def json(self) -> dict:
        return self._payload or {}


class _FakeFreesound:
    def __init__(self, license_url: str, audio: bytes) -> None:
        self.license_url, self.audio, self.calls, self.requests = license_url, audio, [], []

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> _Resp:
        self.calls.append(url)
        self.requests.append({"url": url, "params": params or {}, "headers": headers or {}})
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
    item = sfx_library.fetch_freesound("42", kind="boom", tags=["meme"], client=cc0, api_key="chave-secreta")
    assert item.id == "fs-42" and item.license == "cc0" and item.author == "autor" and item.auto()
    assert item.source_url.endswith("/sounds/42/")
    # a chave vai no cabeçalho, nunca na URL (o httpx registra a URL no log); a prévia pública não a recebe
    api_call, preview_call = cc0.requests
    assert api_call["headers"] == {"Authorization": "Token chave-secreta"}
    assert "chave-secreta" not in api_call["url"] and "chave-secreta" not in json.dumps(api_call["params"])
    assert "token" not in api_call["params"] and preview_call["headers"] == {}
    with pytest.raises(sfx_library.SfxLibraryError):
        sfx_library.freesound_id("abc42")
    # sem chave: a mensagem aponta para onde ela é configurada de verdade
    monkeypatch.delenv("VS_FREESOUND_API_KEY", raising=False)
    monkeypatch.delenv("FREESOUND_API_KEY", raising=False)
    with pytest.raises(sfx_library.SfxLibraryError, match="VS_FREESOUND_API_KEY"):
        sfx_library.fetch_freesound("42", kind="boom", client=cc0)
    # a chave gravada pela rota de Configurações vale
    client = TestClient(create_app())
    assert not client.get("/api/library/sfx").json()["freesound"]["configured"]
    put = client.put("/api/library/freesound-key", json={"api_key": "abcd1234efgh"})
    assert put.status_code == 200 and put.json()["configured"] and "abcd1234efgh" not in put.text
    sfx_library.fetch_freesound("42", kind="boom", client=cc0)
    assert cc0.requests[-2]["headers"] == {"Authorization": "Token abcd1234efgh"}
    assert client.delete("/api/library/freesound-key").json()["configured"] is False


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
    # sem o tipo: 422 com a mensagem da biblioteca, em português
    no_kind = client.post("/api/library/sfx", files={"file": ("t.wav", data, "audio/wav")})
    assert no_kind.status_code == 422 and "tipo do efeito" in no_kind.json()["detail"]
    patched = client.patch("/api/library/sfx/tapa", json={"license": "own"}).json()
    assert patched["auto"] and patched["warning"] is None
    # o ponto forte pode ser corrigido à mão (dentro do efeito)
    assert client.patch("/api/library/sfx/tapa", json={"hit_s": 0.05}).json()["hit_s"] == 0.05
    assert client.patch("/api/library/sfx/tapa", json={"hit_s": 5.0}).status_code == 422
    wav = client.get("/api/library/sfx/tapa/audio")
    assert wav.status_code == 200 and wav.content[:4] == b"RIFF"
    assert client.get("/api/library/sfx/pop/audio").status_code == 200
    assert client.delete("/api/library/sfx/pop").status_code == 422
    assert client.delete("/api/library/sfx/tapa").status_code == 200
    assert client.delete("/api/library/sfx/tapa").status_code == 404
    assert client.post("/api/library/sfx/fetch-freesound", json={"sound": "42", "kind": "boom"}).status_code == 503
    # offline não há voz final: 422 na hora, nenhum job e nada gravado
    voice = client.post("/api/library/sfx/voice", json={"style_id": "misterios-short"})
    assert voice.status_code == 422 and "offline" in voice.json()["detail"]
    assert not any(i.kind == "voice" for i in sfx_library.user_items())
    dry = client.post("/api/library/sfx/import-canais", json={"dry_run": True}).json()
    assert "imported" in dry and "warnings" in dry
    # o trabalho longo vira job (aqui só o pedido; o job roda inline abaixo)
    from studio.workers.jobs import get_job, get_runner

    runner = get_runner()
    runner.submit = lambda kind, payload=None, project_id=None, **kw: {"kind": kind, "payload": payload}
    try:
        job = client.post("/api/library/sfx/import-canais", json={"license_checked": True}).json()
        assert job["kind"] == "sfx_import_canais" and job["payload"]["license_checked"] is True
        assert client.post("/api/library/sfx/scan").json()["kind"] == "sfx_scan"
    finally:
        del runner.submit
    write_wav(sfx_library.user_root() / "solto.wav", _decaying_noise(0.3, 6))
    done = get_job(runner.submit("sfx_scan", {}, run_inline=True)["id"])
    assert done["status"] == "done" and done["result"]["new"] == ["solto"]


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
    # quedas sobrepostas: a junta também fica no teto, e a regra é idempotente (chave, mix e conferência iguais)
    overlap = [{"at": 1.0, "dur": 0.6}, {"at": 1.5, "dur": 0.6}, {"at": 5.8, "dur": 0.6}]
    spans = audio.clamp_drops(overlap, total)
    assert spans == [(1.0, 1.6), (5.8, 6.0)]
    assert audio.clamp_drops([{"at": a, "dur": b - a} for a, b in spans], total) == spans
    # o stem da conferência (que prende de novo) e o mix com os trechos prontos calam o mesmo trecho
    c, _ = audio.music_stem(voice, music, tmp_path / "c.wav", total=total, music_drops=overlap[:2])
    c, _ = read_wav(c)
    assert _rms_db(c[int(1.05 * SR):int(1.55 * SR)]) < -70 and _rms_db(c[int(1.75 * SR):int(2.05 * SR)]) > -45


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

    # evento no formato real do timeline-fx: `start` adiantado pelo SFX_LEAD (0,12 s) E `at`. O início vale
    # `at - hit_s` do que toca (o item da biblioteca, o procedural de evento), não o `start`.
    sfx_library.update(lib.id, hit_s=0.1)
    real = [{"type": "impact", "start": round(at - 0.12, 3), "scene_id": scene, "at": at, "scene": scene,
             "lib_id": lib.id},
            {"type": "riser_short", "start": round(at + 0.88, 3), "scene_id": scene, "at": at + 1.0, "scene": scene},
            {"type": "pop", "start": round(at + 1.88, 3), "scene_id": scene, "at": at + 2.0, "scene": scene}]
    plan = {p["ref"]: p for p in builder.sfx_plan({**timeline, "sfx": real})}
    assert plan["tapa"]["start"] == pytest.approx(at - 0.1)
    assert plan["riser_short"]["start"] == pytest.approx(at + 1.0 - 0.6)
    assert plan["pop"]["start"] == pytest.approx(at + 2.0)

    # licença desconhecida escolhida pelo planejador (origin no evento): toca o tipo, com aviso
    sfx_library.update(lib.id, license="desconhecida")
    builder.warnings.clear()
    picked = builder.sfx_plan({**timeline, "sfx": [{**real[0], "origin": "planner"}]})
    assert [(p["ref"], p["explicit"]) for p in picked] == [("impact", False)]
    assert any("escolhido pelo planner" in w for w in builder.warnings)
    by_user = builder.sfx_plan({**timeline, "sfx": [{**real[0], "origin": "user"}]})
    assert [(p["ref"], p["explicit"]) for p in by_user] == [("tapa", True)]
    sfx_library.update(lib.id, license="own")

    # o arquivo do item some da pasta (apagado à mão): o mix sai com o tipo e o aviso, sem falhar
    lib.path().unlink()
    builder.warnings.clear()
    missing = {**timeline, "sfx": [{"type": "boom", "at": at, "scene": scene, "lib_id": lib.id}]}
    assert [p["ref"] for p in builder.sfx_plan(missing)] == ["boom"]
    assert any("sumiu da pasta" in w for w in builder.warnings)
    assert builder.mix("pt-BR", missing, tts)
    # o tipo resolvido por `candidates` também ignora o item sem arquivo
    assert sfx_library.lookup("tapa") is None and sfx_library.candidates("tapa") == []
    assert sfx_library.get(lib.id).missing and any("sumiu" in w for w in sfx_library.warnings())
    assert sfx_library.scan()["orphans"] == [lib.id]

    # manifesto do usuário corrompido: o projeto antigo mixa igual (mesma chave), sem ler o manifesto
    (sfx_library.user_root() / sfx_library.MANIFEST).write_text("{ quebrado", encoding="utf-8")
    assert builder.mix("pt-BR", timeline, tts) == entry["mix"]
    assert sfx_library.lookup("whoosh").id == "whoosh"
    assert any("ilegível" in w for w in sfx_library.warnings())
