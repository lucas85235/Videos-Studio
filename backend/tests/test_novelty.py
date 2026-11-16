"""Música sem repetir no canal e verificador de novidade (impressões digitais visuais, miniatura e título)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.config import get_settings
from studio.core.project import service
from studio.db import session_scope
from studio.ids import new_id
from studio.media import music_library
from studio.media import novelty as fp
from studio.models import Channel, Project, Publication
from studio.workers import novelty

# --------------------------------------------------------------------------------------------
# imagens sintéticas
# --------------------------------------------------------------------------------------------


def _pattern(seed: int, size: int = 256) -> Image.Image:
    """Imagem de blocos aleatórios (cada semente dá uma imagem bem diferente)."""
    rng = np.random.default_rng(seed)
    blocks = rng.integers(0, 256, size=(8, 8, 3), dtype=np.uint8)
    return Image.fromarray(blocks).resize((size, size), Image.Resampling.NEAREST)


def _save(img: Image.Image, path: Path, **kw) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, **kw)
    return path


def test_dhash_finds_near_copies(tmp_path):
    base = _pattern(1)
    a = fp.hash_file(_save(base, tmp_path / "a.png"))
    # mesma imagem, outro tamanho, JPEG forte e um pouco mais clara: continua "a mesma"
    copy = base.resize((180, 180)).point(lambda v: min(255, v + 12))
    b = fp.hash_file(_save(copy, tmp_path / "b.jpg", quality=40))
    other = fp.hash_file(_save(_pattern(2), tmp_path / "c.webp"))
    assert a and b and other and len(a) == 16
    assert fp.similar(a, b)
    assert not fp.similar(a, other)
    assert fp.distance(a, a) == 0
    # imagem lisa (tela preta) não tem impressão digital; arquivo que não é imagem também não
    assert fp.hash_file(_save(Image.new("RGB", (64, 64), (5, 5, 5)), tmp_path / "preto.png")) is None
    (tmp_path / "x.png").write_bytes(b"nada")
    assert fp.hash_file(tmp_path / "x.png") is None
    assert fp.hash_file(tmp_path / "notas.txt") is None


def test_title_similarity():
    assert fp.title_similarity("O Farol Abandonado!", "o farol abandonado") == 1.0
    assert fp.title_similarity("O mistério do farol abandonado", "Farol abandonado: o mistério") >= 0.8
    assert fp.title_similarity("O mistério do farol abandonado", "A cidade que sumiu do mapa") < 0.5
    assert fp.title_similarity("", "algo") == 0.0
    # títulos de padrão fixo com assunto diferente não são "parecidos"
    for a, b in (("O mistério do Titanic", "O mistério do Triângulo"), ("5 fatos sobre gatos", "5 fatos sobre ratos"),
                 ("O que é DNA", "O que é RNA"), ("Por que o céu é azul?", "Por que o mar é azul?")):
        assert fp.title_similarity(a, b) < 0.8, (a, b)
    assert fp.normalize_title("Ação 🔥 É Já!") == "acao e ja"


# --------------------------------------------------------------------------------------------
# música sem repetir
# --------------------------------------------------------------------------------------------


def _tracks():
    return [music_library.Track(path=p, name=p, mood=m, size=1) for p, m in (
        ("terror/a.mp3", "horror"), ("terror/b.mp3", "horror"), ("terror/c.mp3", "horror"), ("misterio/d.mp3", "mystery"))]


def test_pick_track_avoids_recent():
    tracks = _tracks()
    for seed in range(6):
        pick = music_library.pick_track(tracks, "terror", seed, avoid=["terror/a.mp3", "terror/b.mp3"])
        assert pick.path == "terror/c.mp3"
    # todas do clima tocaram: volta a que tocou há mais tempo (a última da lista)
    every = ["terror/b.mp3", "terror/c.mp3", "terror/a.mp3"]
    assert music_library.pick_track(tracks, "terror", 0, avoid=every).path == "terror/a.mp3"
    # a faixa que o projeto já usava fica, se não tocou no canal
    assert music_library.pick_track(tracks, "terror", 0, avoid=["terror/c.mp3"], prefer="terror/b.mp3").path == "terror/b.mp3"
    assert music_library.pick_track(tracks, "terror", 0, avoid=["terror/b.mp3"], prefer="terror/b.mp3").path != "terror/b.mp3"
    # a fixada vale mais que a novidade
    assert music_library.pick_track(tracks, "terror", 0, pinned="terror/a.mp3", avoid=["terror/a.mp3"]).path == "terror/a.mp3"


def test_repeat_window_setting():
    assert music_library.repeat_window(None) == 3
    assert music_library.repeat_window({"novelty": {"music_window": 0}}) == 0
    assert music_library.repeat_window({"novelty": {"music_window": 999}}) == 50
    assert music_library.repeat_window({"novelty": {"music_window": "x"}}) == 3
    assert novelty.window_of({}) == 10
    assert novelty.window_of({"novelty": {"window": 0}}) == 0


def _channel(settings: dict | None = None) -> str:
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name="Canal", settings=settings or {}))
    return cid


def _project(channel_id: str | None, title: str, minutes_ago: int) -> str:
    pid = service.create_project(title=title, style_id="misterios-short", topic=title, channel_id=channel_id).id
    with session_scope() as s:
        s.get(Project, pid).created_at = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    return pid


def test_recent_tracks_of_channel():
    cid = _channel({"novelty": {"music_window": 2}})
    old = _project(cid, "Velho", 50)
    mid = _project(cid, "Meio", 40)
    last = _project(cid, "Último", 30)
    other_channel = _project(_channel(), "Outro canal", 20)
    current = _project(cid, "Atual", 10)
    for pid, track in ((old, "terror/a.mp3"), (mid, "terror/b.mp3"), (last, "terror/c.mp3"),
                       (other_channel, "terror/d.mp3")):
        service.update_settings(pid, {"last_preview": {"music": {"source": "library", "track": track}}})
    # o vídeo final vale mais que a prévia
    service.update_settings(last, {"last_final": {"music": {"source": "library", "track": "terror/f.mp3"}}})
    # rascunhos sem build e vídeo com música gerada não ocupam lugar na janela
    for i in range(3):
        _project(cid, f"Rascunho {i}", 15 - i)
    procedural = _project(cid, "Música gerada", 12)
    service.update_settings(procedural, {"last_preview": {"music": {"source": "procedural"}}})
    assert music_library.recent_tracks(current) == ["terror/f.mp3", "terror/b.mp3"]
    assert music_library.recent_tracks(current, window=5) == ["terror/f.mp3", "terror/b.mp3", "terror/a.mp3"]
    assert music_library.recent_tracks(current, window=0) == []
    assert music_library.recent_tracks(_project(None, "Sem canal", 1)) == []


def test_recent_tracks_counts_render_order():
    """Episódios criados em lote e renderizados fora de ordem: vale a ordem de renderização."""
    cid = _channel({"novelty": {"music_window": 1}})
    a = _project(cid, "Episódio A", 20)
    b = _project(cid, "Episódio B", 10)
    now = datetime.now(UTC)
    service.update_settings(b, {"last_preview": {"music": {"source": "library", "track": "terror/a.mp3"},
                                                 "built_at": (now - timedelta(minutes=5)).isoformat()}})
    # B (criado depois) já tocou a faixa: A não pode repetir
    assert music_library.recent_tracks(a) == ["terror/a.mp3"]
    service.update_settings(a, {"last_preview": {"music": {"source": "library", "track": "terror/b.mp3"},
                                                 "built_at": now.isoformat()}})
    assert music_library.recent_tracks(b) == ["terror/b.mp3"]


# --------------------------------------------------------------------------------------------
# verificador de novidade
# --------------------------------------------------------------------------------------------


def _fake_build(project_id: str, images: list[Image.Image], thumb: Image.Image | None = None, *,
                key: str = "last_final") -> None:
    """Simula o resultado de um build: linha do tempo com as imagens das cenas e a miniatura."""
    data_dir = get_settings().data_dir
    folder = data_dir / "outputs" / project_id / "pt-BR"
    scenes = []
    for i, img in enumerate(images):
        rel = f"outputs/{project_id}/img/{i}.png"
        _save(img, data_dir / rel)
        scenes.append({"id": f"c{i}", "src": rel})
    scenes.append({"id": "cartao", "src": None})
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "timeline.json").write_text(json.dumps({"scenes": scenes}), encoding="utf-8")
    entry = {"timeline": f"outputs/{project_id}/pt-BR/timeline.json"}
    if thumb is not None:
        entry["thumbnail"] = _save(thumb, folder / "thumbnail.jpg", quality=85).relative_to(data_dir).as_posix()
    service.update_settings(project_id, {key: {"langs": {"pt-BR": entry}, "warnings": []}})
    novelty.refresh(project_id)  # como no fim do job de build


def test_novelty_check_flags_repeated_images_thumbnail_and_title():
    cid = _channel()
    old = _project(cid, "O mistério do farol abandonado", 30)
    _fake_build(old, [_pattern(s) for s in (1, 2, 3)], thumb=_pattern(50), key="last_preview")
    unbuilt = _project(cid, "Sem build nenhum", 25)
    current = _project(cid, "Farol abandonado: o mistério", 10)
    # 2 das 4 imagens vêm do vídeo antigo (uma redimensionada); a imagem repetida dentro do vídeo conta uma vez
    _fake_build(current, [_pattern(1), _pattern(2).resize((200, 200)), _pattern(7), _pattern(8), _pattern(7)],
                thumb=_pattern(50).resize((640, 640)))
    report = novelty.check(current)
    assert report["enabled"] and report["ready"]
    assert [c["project_id"] for c in report["compared"]] == [old]  # projeto sem build fica de fora
    assert unbuilt not in [c["project_id"] for c in report["compared"]]
    assert report["images"]["total"] == 4
    assert sorted(r["scene"] for r in report["images"]["repeated"]) == ["c0", "c1"]
    assert report["images"]["share"] == 0.5
    assert report["thumbnail"]["project_id"] == old
    assert report["titles"][0]["project_id"] == old
    warnings = report["warnings"]
    assert len(warnings) == 3 and all(w.startswith("Novidade:") for w in warnings)
    assert "2 de 4 imagens (50%)" in warnings[0]

    # a impressão digital fica guardada e só é refeita quando as imagens mudam
    key = service.get_project_row(current)["settings"]["novelty_fp"]["key"]
    assert novelty.fingerprint(current)["key"] == key

    # um vídeo diferente não gera aviso
    fresh = _project(cid, "A cidade que sumiu do mapa", 5)
    _fake_build(fresh, [_pattern(s) for s in (20, 21, 22)], thumb=_pattern(60))
    clean = novelty.check(fresh)
    assert clean["warnings"] == [] and clean["images"]["repeated"] == [] and clean["thumbnail"] is None


def test_novelty_new_thumbnail_clears_warning():
    """Trocar só a miniatura (mesmo caminho em outputs/, mesmas cenas) refaz a impressão digital."""
    cid = _channel()
    old = _project(cid, "A cidade que sumiu", 30)
    _fake_build(old, [_pattern(1)], thumb=_pattern(50))
    current = _project(cid, "O navio fantasma", 10)
    _fake_build(current, [_pattern(9)], thumb=_pattern(50))
    assert novelty.check(current)["thumbnail"]["project_id"] == old
    _fake_build(current, [_pattern(9)], thumb=_pattern(51))  # outra miniatura, mesmo caminho
    report = novelty.check(current)
    assert report["thumbnail"] is None and report["warnings"] == []


def test_novelty_reads_others_from_cache_without_touching_them():
    """A conferência não calcula nem grava nada nos outros projetos; sem impressão em dia, fica pendente."""
    cid = _channel()
    old = _project(cid, "Primeiro", 30)
    _fake_build(old, [_pattern(1), _pattern(2)])
    legacy = _project(cid, "Antigo sem impressão", 20)
    _fake_build(legacy, [_pattern(1), _pattern(2)])
    with session_scope() as s:  # build de antes do verificador: sem impressão digital guardada
        row = s.get(Project, legacy)
        row.settings = {k: v for k, v in row.settings.items() if k != "novelty_fp"}
    current = _project(cid, "Atual", 10)
    _fake_build(current, [_pattern(1), _pattern(2)])
    with session_scope() as s:
        before = {pid: s.get(Project, pid).updated_at for pid in (old, legacy, current)}
        settings_before = {pid: dict(s.get(Project, pid).settings) for pid in (old, legacy)}
    report = novelty.check(current)
    assert [c["project_id"] for c in report["compared"]] == [old]
    assert [p["project_id"] for p in report["pending"]] == [legacy]
    with session_scope() as s:
        assert {pid: s.get(Project, pid).updated_at for pid in (old, legacy, current)} == before
        assert {pid: dict(s.get(Project, pid).settings) for pid in (old, legacy)} == settings_before


def test_novelty_broken_media_does_not_break_check(monkeypatch):
    """ffmpeg que passa do tempo ou imagem gigante: sem impressão digital, mas a conferência responde."""
    import subprocess

    from PIL import Image as PILImage

    from studio.media import ffmpeg

    def slow(*_a, **_kw):
        raise subprocess.TimeoutExpired("ffmpeg", 60)

    monkeypatch.setattr(ffmpeg, "run_ffmpeg", slow)
    video = get_settings().data_dir / "lento.mp4"
    video.write_bytes(b"\0" * 10)
    assert fp.hash_file(video) is None

    def bomb(*_a, **_kw):
        raise PILImage.DecompressionBombError("grande demais")

    monkeypatch.setattr(PILImage, "open", bomb)
    assert fp.hash_file(_save(_pattern(1), get_settings().data_dir / "grande.png")) is None

    cid = _channel()
    current = _project(cid, "Algum título", 5)
    service.update_settings(current, {"last_final": {"langs": {"pt-BR": {"timeline": "x.json"}}}})
    monkeypatch.setattr(novelty, "fingerprint", lambda _pid: 1 / 0)
    report = novelty.check(current)
    assert report["enabled"] and not report["ready"]


def test_novelty_one_repeated_image_is_fine_and_window_limits():
    cid = _channel({"novelty": {"window": 1}})
    oldest = _project(cid, "Primeiro", 30)
    _fake_build(oldest, [_pattern(1), _pattern(2)])
    middle = _project(cid, "Segundo", 20)
    _fake_build(middle, [_pattern(30), _pattern(31)])
    current = _project(cid, "Terceiro", 10)
    _fake_build(current, [_pattern(1), _pattern(2), _pattern(40)])
    # janela de 1: só o vídeo mais recente entra (o primeiro, com as imagens iguais, fica de fora)
    report = novelty.check(current)
    assert [c["project_id"] for c in report["compared"]] == [middle]
    assert report["warnings"] == []
    with session_scope() as s:
        s.get(Channel, cid).settings = {"novelty": {"window": 5}}
    report = novelty.check(current)
    assert len(report["images"]["repeated"]) == 2 and report["warnings"]
    # uma imagem só repetida, de muitas, não chega a ser aviso
    _fake_build(current, [_pattern(1), *[_pattern(s) for s in range(60, 66)]])
    one = novelty.check(current)
    assert len(one["images"]["repeated"]) == 1 and one["warnings"] == []


def test_novelty_disabled_without_channel_or_window():
    alone = _project(None, "Sozinho", 5)
    report = novelty.check(alone)
    assert not report["enabled"] and "não tem canal" in report["reason"]
    off = _project(_channel({"novelty": {"window": 0}}), "Desligado", 5)
    report = novelty.check(off)
    assert not report["enabled"] and "desligado" in report["reason"]
    assert novelty.safe_warnings("nao-existe") == []


def test_novelty_compares_youtube_titles_but_not_own_upload():
    cid = _channel({"youtube": {"channel_id": "UC1", "recent_videos": [
        {"title": "O FAROL ABANDONADO", "video_id": "v1"},
        {"title": "Receita de bolo", "video_id": "v2"},
    ]}})
    current = _project(cid, "O farol abandonado", 5)
    report = novelty.check(current)
    assert not report["ready"]  # sem build: só os títulos
    assert report["titles"][0]["source"] == "youtube"
    assert "no YouTube" in report["warnings"][0]
    # o vídeo do YouTube é este projeto mesmo (já publicado): não conta
    with session_scope() as s:
        s.add(Publication(id=new_id("pub"), project_id=current, platform="youtube", language="pt-BR",
                          driver="youtube_native", external_id="v1"))
    assert novelty.check(current)["titles"] == []


def test_novelty_ignores_own_manual_publication():
    """Publicado à mão (só o link guardado, sem external_id): o vídeo do YouTube não é comparado com ele mesmo."""
    cid = _channel({"youtube": {"channel_id": "UC1", "recent_videos": [
        {"title": "O farol abandonado", "video_id": "abcDEF12345"},
    ]}})
    current = _project(cid, "O farol abandonado", 5)
    assert novelty.check(current)["titles"]
    with session_scope() as s:
        s.add(Publication(id=new_id("pub"), project_id=current, platform="youtube", language="pt-BR",
                          driver="manual", status="published", url="https://youtu.be/abcDEF12345?si=x"))
    assert novelty.check(current)["titles"] == []
    assert novelty._video_id("https://www.youtube.com/watch?v=abcDEF12345&t=3") == "abcDEF12345"
    assert novelty._video_id("https://youtube.com/shorts/abcDEF12345") == "abcDEF12345"
    assert novelty._video_id(None) is None


def test_novelty_api_and_channel_settings():
    with TestClient(create_app()) as client:
        created = client.post("/api/channels", json={"name": "Canal API"}).json()
        assert created["novelty"] == {"music_window": 3, "window": 10}
        updated = client.put(f"/api/channels/{created['id']}",
                             json={"name": "Canal API", "novelty": {"music_window": 5, "window": 0}})
        assert updated.status_code == 200, updated.text
        assert updated.json()["novelty"] == {"music_window": 5, "window": 0}
        # salvar sem mandar `novelty` não apaga as janelas
        again = client.put(f"/api/channels/{created['id']}", json={"name": "Outro nome"}).json()
        assert again["novelty"] == {"music_window": 5, "window": 0}
        bad = client.put(f"/api/channels/{created['id']}", json={"name": "x", "novelty": {"window": -1}})
        assert bad.status_code == 422
        pid = _project(created["id"], "Com canal", 1)
        res = client.get(f"/api/projects/{pid}/novelty")
        assert res.status_code == 200 and res.json()["enabled"] is False
        assert client.get("/api/projects/nao-existe/novelty").status_code == 404


def test_final_render_adds_novelty_warnings(monkeypatch):
    """Depois do vídeo final, os avisos de novidade entram nos avisos da renderização (sem falhar o job)."""
    from studio.workers import tasks

    cid = _channel()
    old = _project(cid, "O farol abandonado", 30)
    _fake_build(old, [_pattern(1), _pattern(2)])
    current = _project(cid, "O farol abandonado", 10)

    class FakeBuilder:
        def __init__(self, project_id, **_kw):
            self.project_id = project_id

        def run(self):
            _fake_build(self.project_id, [_pattern(1), _pattern(2), _pattern(3)])
            built = service.get_project_row(self.project_id)["settings"]["last_final"]
            return {**built, "warnings": ["aviso do build"]}

    class Ctx:
        project_id = current

    monkeypatch.setattr(tasks, "Builder", FakeBuilder)
    result = tasks._build(Ctx(), {}, "final")
    assert result["warnings"][0] == "aviso do build"
    assert any("imagens" in w for w in result["warnings"][1:]) and any("título" in w for w in result["warnings"][1:])
    assert service.get_project_row(current)["settings"]["last_final"]["warnings"] == result["warnings"]

    monkeypatch.setattr(novelty, "check", lambda _pid: 1 / 0)  # o verificador quebrado não derruba o vídeo
    assert tasks._build(Ctx(), {}, "final")["warnings"] == ["aviso do build"]


def test_package_checklist_has_novelty_warning():
    from studio.workers.publish.package import build_package

    cid = _channel()
    old = _project(cid, "O farol abandonado", 30)
    _fake_build(old, [_pattern(1)])
    current = _project(cid, "O farol abandonado", 10)
    _fake_build(current, [_pattern(5)])
    built = service.get_project_row(current)["settings"]["last_final"]
    video = get_settings().data_dir / "outputs" / current / "pt-BR" / "video.mp4"
    video.write_bytes(b"\0" * 10)
    built["langs"]["pt-BR"]["video"] = video.relative_to(get_settings().data_dir).as_posix()
    service.update_settings(current, {"last_final": built})
    result = build_package(current, "pt-BR", ["tiktok"])
    assert any("título" in w for w in result["novelty_warnings"])
    checklist = (Path(result["items"][0]["folder"]) / "CHECKLIST.md").read_text(encoding="utf-8")
    assert "**Atenção:** Novidade: o título é muito parecido" in checklist
