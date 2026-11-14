from __future__ import annotations

from studio.core.project import service
from studio.core.project.ops import apply_ops, parse_ops
from studio.core.styles.library import get_style
from studio.media import music_library
from studio.media.ffmpeg import duration_of, probe, run_ffmpeg
from studio.workers.jobs import get_job, get_runner


def _touch(path, size: int = 10) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def test_list_and_pick_tracks(isolated_settings):
    root = music_library.music_root()
    _touch(root / "terror" / "corredor.mp3")
    _touch(root / "misterio" / "nevoa.wav")
    _touch(root / "sem clima.ogg")
    _touch(root / "notas.txt")
    tracks = music_library.list_tracks()
    moods = {t.path: t.mood for t in tracks}
    assert moods == {"terror/corredor.mp3": "horror", "misterio/nevoa.wav": "mystery", "sem clima.ogg": "drone"}

    assert music_library.pick_track(tracks, "terror analógico", 7).path == "terror/corredor.mp3"
    # sem faixa do clima pedido: qualquer uma, de forma determinística
    a = music_library.pick_track(tracks, "épico", 3)
    assert a is not None and a == music_library.pick_track(tracks, "épico", 3)
    # faixa fixada vence o clima
    assert music_library.pick_track(tracks, "terror", 1, pinned="misterio/nevoa.wav").path == "misterio/nevoa.wav"
    assert music_library.pick_track([], "terror", 1) is None
    assert music_library.resolve("../fora.mp3") is None


def _run(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _commit(project_id: str, ops: list[dict]) -> None:
    doc = service.get_doc(project_id)
    service.commit(project_id, apply_ops(doc, parse_ops(ops), get_style(doc.style_id)), "teste")


def test_preview_uses_library_music(isolated_settings):
    doc = service.create_project(title="Teste", style_id="misterios-short", topic="o farol abandonado")
    project_id = doc.id
    _run("script", project_id)
    _run("storyboard", project_id)
    _commit(project_id, [{"op": "set_style", "path": "audio.music.source", "value": "library"}])

    # biblioteca vazia: cai na trilha procedural com aviso
    empty = _run("preview", project_id)
    assert empty["music"]["source"] == "procedural"
    assert any("biblioteca de músicas está vazia" in w for w in empty["warnings"])

    # faixa estéreo curta: é repetida até cobrir o vídeo
    root = music_library.music_root()
    for folder, name, freq in (("misterio", "faixa.mp3", 330), ("terror", "outra.mp3", 550)):
        (root / folder).mkdir(parents=True, exist_ok=True)
        run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency={freq}:duration=3", "-ac", "2", str(root / folder / name)])
    preview = _run("preview", project_id)
    assert preview["music"]["source"] == "library"
    assert preview["music"]["track"] in {"misterio/faixa.mp3", "terror/outra.mp3"}
    assert preview["generated"].get("music", 0) == 1

    # fixar outra faixa troca só a música (narração e imagens reaproveitadas)
    other = "terror/outra.mp3" if preview["music"]["track"] == "misterio/faixa.mp3" else "misterio/faixa.mp3"
    _commit(project_id, [{"op": "set_music", "track": other}])
    pinned = _run("preview", project_id)
    assert pinned["music"]["track"] == other
    assert pinned["generated"].get("tts", 0) == 0 and pinned["generated"].get("image", 0) == 0
    music_files = sorted(isolated_settings.cache_dir.glob("music/*/*/music.wav"))
    assert music_files, "trilha preparada não encontrada no cache"
    longest = max(duration_of(p) for p in music_files)
    assert longest > 10  # bem maior que a faixa de 3 s
    assert any(s["channels"] == 2 for p in music_files for s in probe(p)["streams"])

    # soltar a faixa fixada
    _commit(project_id, [{"op": "set_music", "track": ""}])
    assert service.get_doc(project_id).music[0].track is None
