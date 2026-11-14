"""Biblioteca de trilhas do usuário: arquivos de áudio numa pasta, com clima deduzido da subpasta/nome.

Estrutura sugerida (qualquer uma funciona):

    <dados>/library/music/terror/faixa1.mp3
    <dados>/library/music/misterio/faixa2.mp3
    <dados>/library/music/tensao - faixa3.wav

O clima de cada faixa sai de `mood_key()` aplicado ao caminho relativo (subpastas + nome). A escolha é
determinística pela semente do projeto; uma faixa pode ser fixada no projeto (`MusicCue.track`).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from studio.config import get_settings
from studio.media.audio import mood_key
from studio.media.ffmpeg import SAMPLE_RATE, run_ffmpeg

AUDIO_EXTS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".ogg", ".oga", ".opus", ".flac"})


@dataclass(frozen=True)
class Track:
    path: str  # relativo à pasta de músicas, com "/"
    name: str
    mood: str
    size: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def music_root() -> Path:
    root = get_settings().library_dir / "music"
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_tracks(root: Path | None = None) -> list[Track]:
    root = root or music_root()
    if not root.is_dir():
        return []
    tracks: list[Track] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in AUDIO_EXTS or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        label = rel.rsplit(".", 1)[0].replace("_", " ").replace("-", " ")
        tracks.append(Track(path=rel, name=p.stem, mood=mood_key(label), size=p.stat().st_size))
    return tracks


def resolve(rel: str, root: Path | None = None) -> Path | None:
    """Caminho absoluto de uma faixa (só dentro da pasta de músicas)."""
    root = (root or music_root()).resolve()
    full = (root / rel).resolve()
    if not full.is_relative_to(root) or not full.is_file():
        return None
    return full


def pick_track(tracks: list[Track], mood: str | None, seed: int, pinned: str | None = None) -> Track | None:
    """Faixa fixada (se existir) ou uma do mesmo clima; sem faixa do clima, qualquer uma da biblioteca."""
    if not tracks:
        return None
    if pinned:
        for t in tracks:
            if t.path == pinned:
                return t
    key = mood_key(mood)
    pool = [t for t in tracks if t.mood == key] or list(tracks)
    pool.sort(key=lambda t: t.path)
    return pool[seed % len(pool)]


def prepare_track(src: Path, duration: float, out_path: Path) -> Path:
    """Ajusta a faixa ao vídeo: repete se for curta, corta se for longa, fade de entrada/saída e loudness -16 LUFS."""
    duration = max(2.0, duration)
    fade_in = min(1.5, duration / 6)
    fade_out = min(4.0, duration / 4)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(
        [
            "-stream_loop", "-1", "-i", str(src), "-t", f"{duration:.2f}", "-vn",
            "-af",
            f"afade=t=in:d={fade_in:.2f},afade=t=out:st={duration - fade_out:.2f}:d={fade_out:.2f},"
            "loudnorm=I=-16:TP=-2:LRA=9",
            "-ac", "2", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out_path),
        ]
    )
    return out_path
