"""Biblioteca de trilhas do usuário: arquivos de áudio numa pasta, com clima deduzido da subpasta/nome.

Estrutura sugerida (qualquer uma funciona):

    <dados>/library/music/terror/faixa1.mp3
    <dados>/library/music/misterio/faixa2.mp3
    <dados>/library/music/tensao - faixa3.wav

O clima de cada faixa sai de `mood_key()` aplicado ao caminho relativo (subpastas + nome). A escolha é
determinística pela semente do projeto; uma faixa pode ser fixada no projeto (`MusicCue.track`).

Sem repetir: as faixas usadas nos últimos vídeos do mesmo canal (janela configurável no canal, `settings.novelty.
music_window`; 0 desliga) ficam de fora da escolha. Se todas as faixas do clima já foram usadas nessa janela, volta a
que foi usada há mais tempo (o clima vale mais que a novidade).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

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


def pick_track(tracks: list[Track], mood: str | None, seed: int, pinned: str | None = None,
               avoid: Sequence[str] = (), prefer: str | None = None) -> Track | None:
    """Faixa fixada (se existir) ou uma do mesmo clima; sem faixa do clima, qualquer uma da biblioteca.

    `avoid`: faixas usadas nos últimos vídeos do canal, da mais recente para a mais antiga (ficam de fora; se todas
    do clima estão nela, vale a usada há mais tempo). `prefer`: a faixa que o projeto já usava, mantida se ainda
    servir (a prévia e o vídeo final não trocam de música só porque outro vídeo do canal foi feito no meio).
    """
    if not tracks:
        return None
    if pinned:
        for t in tracks:
            if t.path == pinned:
                return t
    key = mood_key(mood)
    pool = [t for t in tracks if t.mood == key] or list(tracks)
    pool.sort(key=lambda t: t.path)
    recent = list(avoid)
    fresh = [t for t in pool if t.path not in recent]
    if prefer:
        for t in fresh:
            if t.path == prefer:
                return t
    if fresh:
        return fresh[seed % len(fresh)]
    # Todas do clima já tocaram na janela: a que tocou há mais tempo (maior posição na lista de recentes).
    return max(pool, key=lambda t: (recent.index(t.path), t.path))


DEFAULT_REPEAT_WINDOW = 3
MAX_REPEAT_WINDOW = 50


def repeat_window(channel_settings: dict[str, Any] | None) -> int:
    """Quantos vídeos anteriores do canal não repetem faixa (padrão 3; 0 desliga)."""
    raw = ((channel_settings or {}).get("novelty") or {}).get("music_window", DEFAULT_REPEAT_WINDOW)
    try:
        return max(0, min(MAX_REPEAT_WINDOW, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_REPEAT_WINDOW


def used_track(project_settings: dict[str, Any] | None) -> str | None:
    """Faixa da biblioteca que o projeto usou no último build (vídeo final antes da prévia)."""
    settings = project_settings or {}
    for key in ("last_final", "last_preview"):
        music = (settings.get(key) or {}).get("music") or {}
        if music.get("source") == "library" and music.get("track"):
            return str(music["track"])
    return None


def recent_tracks(project_id: str, window: int | None = None) -> list[str]:
    """Faixas da biblioteca usadas nos `window` vídeos do mesmo canal feitos antes deste projeto (mais recente
    primeiro). Projeto sem canal ou janela 0: lista vazia."""
    from studio.db import session_scope
    from studio.models import Channel, Project

    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None or not row.channel_id:
            return []
        if window is None:
            channel = s.get(Channel, row.channel_id)
            window = repeat_window(channel.settings if channel else None)
        if window <= 0:
            return []
        others = (
            s.query(Project)
            .filter(Project.channel_id == row.channel_id, Project.id != row.id, Project.created_at <= row.created_at)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .limit(window)
            .all()
        )
        out: list[str] = []
        for other in others:
            track = used_track(other.settings)
            if track and track not in out:
                out.append(track)
        return out


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
