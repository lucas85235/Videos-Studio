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

import threading
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.core.hashing import hash_obj
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


def _used(project_settings: dict[str, Any] | None) -> tuple[str, str | None] | None:
    """(faixa da biblioteca, quando foi renderizada) do último build (vídeo final antes da prévia)."""
    settings = project_settings or {}
    for key in ("last_final", "last_preview"):
        build = settings.get(key) or {}
        music = build.get("music") or {}
        if music.get("source") == "library" and music.get("track"):
            return str(music["track"]), build.get("built_at")
    return None


def used_track(project_settings: dict[str, Any] | None) -> str | None:
    """Faixa da biblioteca que o projeto usou no último build (vídeo final antes da prévia)."""
    used = _used(project_settings)
    return used[0] if used else None


def _when(raw: str | None, fallback: datetime) -> datetime:
    try:
        when = datetime.fromisoformat(raw) if raw else fallback
    except ValueError:
        when = fallback
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def recent_tracks(project_id: str, window: int | None = None) -> list[str]:
    """Faixas da biblioteca usadas nos `window` vídeos mais recentes do mesmo canal que já tocaram uma faixa (mais
    recente primeiro, pela hora da renderização; build antigo sem essa hora vale pela criação do projeto).

    Conta todo projeto do canal já renderizado, mesmo criado depois deste (episódios feitos em lote e renderizados
    fora de ordem); rascunhos sem build e vídeos com música gerada não ocupam lugar na janela. Projeto sem canal ou
    janela 0: lista vazia."""
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
        others = s.query(Project).filter(Project.channel_id == row.channel_id, Project.id != row.id).all()
        used: list[tuple[datetime, str, str]] = []
        for other in others:
            got = _used(other.settings)
            if got:
                used.append((_when(got[1], other.created_at), other.id, got[0]))
        used.sort(reverse=True)
        out: list[str] = []
        for _, _, track in used[:window]:
            if track not in out:
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


# --------------------------------------------------------------------------------------------
# Curadoria (ADR 0003, bloco G): trecho "só a batida" em loop no compasso, em AppSetting["music_curation"]
# --------------------------------------------------------------------------------------------

CURATION_KEY = "music_curation"
OVERRIDES_KEY = "_overrides"  # ajustes manuais por faixa: {caminho: {loop_start_s, loop_bars, exact, avoid, intro, ...}}
_curation_lock = threading.Lock()


def curation_key(rel: str, root: Path | None = None) -> str | None:
    """Chave do cache: `caminho|tamanho|mtime` (o arquivo trocado no disco é curado de novo)."""
    full = resolve(rel, root)
    if full is None:
        return None
    st = full.stat()
    return f"{rel}|{st.st_size}|{int(st.st_mtime)}"


def _read_cache() -> dict[str, Any]:
    from studio.db import session_scope
    from studio.models import AppSetting

    with session_scope() as s:
        row = s.get(AppSetting, CURATION_KEY)
        return dict(row.value) if row and isinstance(row.value, dict) else {}


def _write_cache(changes: dict[str, Any], drop_prefixes: set[str] | None = None) -> None:
    """Junta `changes` ao cache (ler-juntar-gravar sob um lock); tira as chaves antigas das faixas em `drop_prefixes`."""
    from studio.db import session_scope
    from studio.models import AppSetting

    with _curation_lock, session_scope() as s:
        row = s.get(AppSetting, CURATION_KEY)
        current = dict(row.value) if row and isinstance(row.value, dict) else {}
        if drop_prefixes:
            current = {k: v for k, v in current.items() if k.split("|", 1)[0] not in drop_prefixes or k in changes}
        merged = {**current, **changes}
        if row is None:
            s.add(AppSetting(key=CURATION_KEY, value=merged))
        else:
            row.value = merged


def curation_overrides(rel: str) -> dict[str, Any]:
    return dict((_read_cache().get(OVERRIDES_KEY) or {}).get(rel) or {})


def get_curation(rel: str) -> dict[str, Any] | None:
    """Curadoria guardada da faixa (None = ainda não curada ou o arquivo mudou)."""
    key = curation_key(rel)
    if key is None:
        return None
    found = _read_cache().get(key)
    return dict(found) if isinstance(found, dict) else None


def curation_map() -> dict[str, dict[str, Any]]:
    """Curadoria válida de cada faixa da biblioteca, por caminho relativo."""
    cache = _read_cache()
    out: dict[str, dict[str, Any]] = {}
    for t in list_tracks():
        key = curation_key(t.path)
        if key and isinstance(cache.get(key), dict):
            out[t.path] = cache[key]
    return out


def loop_path(key: str) -> Path:
    """WAV do loop curado (48 kHz) no cache."""
    return get_settings().cache_dir / "music_loops" / f"{hash_obj(key)[:24]}.wav"


def curate_track(rel: str, *, force: bool = False) -> dict[str, Any]:
    """Cura a faixa (ou devolve a curadoria guardada) e grava no cache. Faixa que não existe → ok False."""
    from studio.media import music_curate

    key = curation_key(rel)
    full = resolve(rel)
    if key is None or full is None:
        return {"ok": False, "reason": "faixa não encontrada na biblioteca"}
    ov = curation_overrides(rel)
    if not force:
        found = _read_cache().get(key)
        if isinstance(found, dict) and found.get("override", {}) == ov:
            return dict(found)
    try:
        result = music_curate.curate(full, ov, loop_path(key + str(hash_obj(ov))))
    except Exception as exc:  # noqa: BLE001 - arquivo que o FFmpeg não lê vira "não curada", com o motivo
        result = {"ok": False, "reason": f"não consegui analisar a faixa: {exc}"[:300]}
    if result.get("loop"):
        result["loop"] = Path(result["loop"]).resolve().relative_to(get_settings().data_dir.resolve()).as_posix()
    result["override"] = ov
    _write_cache({key: result}, drop_prefixes={rel})
    return result
