"""Trechos de vídeo para o acervo (ADR 0004 §3.4): baixar, cortar em planos e fazer o proxy mudo de cada plano.

Porte melhorado de `canais/motor/passos/trailer_shots.py` (planos por detecção de corte) e `make_clips.py` (trecho
sem áudio, modo `hold`):

- `fetch`: baixa o link com o yt-dlp (o mesmo de `reference_video.download`), com cache pela chave do link;
- `cut`: planos entre os cortes, cada um com no máximo `max_clip_s` (plano longo vira pedaços iguais), sem os
  micro-planos (flash, corte duplo) e com até `max_shots` planos espalhados pelo vídeo;
- `make_proxy`: h264 mudo no tamanho e no fps finais do vídeo, GOP de 15 quadros (busca rápida no Remotion), com o
  modo `hold` (ida uma vez e o último quadro parado, nunca o vai e volta que "treme");
- `analyze`: tudo junto, com pôster JPEG e descrição de cada plano pelo provedor de visão, em cache pela impressão do
  arquivo e pelos parâmetros (`library/clips/<arquivo>/<parâmetros>/manifest-<rótulo>.json`).

O som original nunca entra (`-an`). Os arquivos ficam em `library/clips/` (fora do cache: a limpeza nunca apaga o que
uma cena usa); o anexo copia o proxy escolhido para a pasta do projeto.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.media import reference_video
from studio.media.ffmpeg import FFmpegError, run_ffmpeg

log = logging.getLogger(__name__)

MIN_POOL_SHOT_S = 0.8
"""Plano mais curto que isto não serve de material de cena (flash, corte duplo, micro-plano)."""
MAX_SHOTS = 24
"""Planos por vídeo no acervo (espalhados do começo ao fim)."""
PROXY_GOP = 15
PROXY_CRF = 20
POSTER_WIDTH = 960
SIGNATURE_BYTES = 4 << 20
ANALYSIS_VERSION = 2
VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})

# Material dito pela visão (`providers.vision.SHOT_SCHEMA`), em palavras para o pedido do storyboard.
MATERIAL_WORDS = {
    "gameplay": "jogo", "official_trailer": "trailer", "page_capture": "página", "news": "notícia",
    "text_card": "texto na tela", "photo_real": "imagem real", "ai_illustration": "ilustração", "chart_map": "gráfico",
    "facecam": "rosto", "mascot": "mascote", "meme_reaction": "meme", "stock_video": "banco de vídeo",
}
MOOD_WORDS = {"dark": "escuro", "bright": "claro", "neutral": ""}


class ClipError(RuntimeError):
    """Falha ao baixar ou cortar o vídeo: mensagem em português."""


@dataclass
class ClipShot:
    k: int
    """Número do plano no vídeo (base 1)."""
    a: float
    """Início no vídeo de origem (s)."""
    dur: float
    proxy: str
    """Proxy mudo (relativo a VS_DATA_DIR)."""
    poster: str
    """Pôster JPEG do meio do plano (relativo a VS_DATA_DIR)."""
    desc: str = ""
    material: str = ""
    width: int = 0
    height: int = 0


# --------------------------------------------------------------------------------------------
# Pastas e chaves
# --------------------------------------------------------------------------------------------


def clips_dir() -> Path:
    return get_settings().library_dir / "clips"


def _rel(path: Path) -> str:
    return path.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()


def link_key(url: str) -> str:
    """Chave do link (o mesmo link baixa uma vez só)."""
    return hashlib.sha256(" ".join(url.split()).encode("utf-8")).hexdigest()[:16]


def file_key(path: Path) -> str:
    """Impressão do arquivo: tamanho e os primeiros 4 MB (trailer 4K de 1 GB não é lido inteiro)."""
    digest = hashlib.sha256()
    digest.update(str(path.stat().st_size).encode())
    with open(path, "rb") as fh:
        digest.update(fh.read(SIGNATURE_BYTES))
    return digest.hexdigest()[:16]


# --------------------------------------------------------------------------------------------
# Download (yt-dlp, opcional)
# --------------------------------------------------------------------------------------------


def cached_source(url: str) -> tuple[Path, str] | None:
    """O vídeo do link já baixado (e o título), sem rede. None: ainda não baixado."""
    folder = clips_dir() / "src" / link_key(url)
    video = next((p for p in sorted(folder.glob("video.*")) if p.suffix.lower() in VIDEO_EXTS), None)
    if video is None:
        return None
    title_file = folder / "title.txt"
    title = title_file.read_text(encoding="utf-8").strip() if title_file.is_file() else ""
    return video, title


def fetch(url: str, *, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> tuple[Path, str]:
    """Baixa o vídeo do link (até 1080p) uma vez; devolve o arquivo e o título limpo."""
    found = cached_source(url)
    if found is not None:
        return found
    folder = clips_dir() / "src" / link_key(url)
    try:
        path, title = reference_video.download(url, folder, "video", runner=runner)
    except reference_video.ReferenceVideoError as exc:
        raise ClipError(str(exc)) from exc
    from studio.core.media_title import clean_media_title

    title = clean_media_title(title) if title else ""
    (folder / "title.txt").write_text(title, encoding="utf-8")
    return path, title


# --------------------------------------------------------------------------------------------
# Cortes e planos
# --------------------------------------------------------------------------------------------


def detect_cuts(path: Path) -> list[float]:
    """Instantes dos cortes do vídeo pelo mesmo extrator da referência (`media/refprofile`): uma passada do ffmpeg
    (`decode.scan_and_gray`) dá a diferença entre quadros e os quadros em cinza; dois limiares (duro e suave, para
    gameplay e trailer), rajadas coladas viradas num corte só e o candidato que é a mesma imagem ampliada descartado
    (punch, não corte). O plano curto que sobra entre cortes sai em `cut` (`min_shot_s`)."""
    from studio.media.refprofile import cuts as ref_cuts
    from studio.media.refprofile import decode, motion

    info = reference_video.probe(path)
    scan, frames, times = decode.scan_and_gray(path, int(info["w"]), int(info["h"]), float(info.get("dur") or 0) or None)
    cands = ref_cuts.merge_bursts(ref_cuts.candidates(scan.times, scan.mafd))
    if not cands:
        return []
    found, _punches = motion.verify_cuts(cands, frames, times)
    return found


def cut(cut_times: list[float], dur: float, *, max_clip_s: float, min_shot_s: float = MIN_POOL_SHOT_S,
        max_shots: int = MAX_SHOTS) -> list[dict[str, Any]]:
    """Planos `{k, a, dur}` entre os cortes. Plano mais longo que `max_clip_s` vira pedaços iguais (todos com no
    máximo `max_clip_s`); plano mais curto que `min_shot_s` sai. Com mais de `max_shots`, ficam planos espalhados
    pelo vídeo inteiro (não só o começo). `k` é o número do plano no vídeo, igual para os pedaços dele."""
    if dur <= 0 or max_clip_s <= 0:
        return []
    times = sorted({round(t, 3) for t in cut_times if 0 < t < dur})
    edges = [0.0, *times, float(dur)]
    out: list[dict[str, Any]] = []
    for k, (a, b) in enumerate(zip(edges, edges[1:], strict=False), 1):
        length = b - a
        if length < min_shot_s:
            continue
        pieces = max(1, math.ceil(length / max_clip_s - 1e-9))
        step = length / pieces
        for i in range(pieces):
            out.append({"k": k, "a": round(a + i * step, 3), "dur": round(step, 3)})
    if len(out) > max_shots > 0:
        picks = sorted({round(i * (len(out) - 1) / (max_shots - 1)) for i in range(max_shots)}) if max_shots > 1 else [0]
        out = [out[i] for i in picks]
    return out


def proxy_filter(width: int, height: int, fps: int, hold_s: float = 0.0) -> str:
    """Preenche o quadro final (recorta o que sobra, sem barra preta), no fps final; `hold_s` congela o último
    quadro por mais esse tempo (o modo `hold` do canais: ida uma vez, nunca o vai e volta)."""
    chain = (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},"
             f"fps={fps},format=yuv420p")
    if hold_s > 0:
        chain += f",tpad=stop_mode=clone:stop_duration={hold_s:.3f}"
    return chain


def make_proxy(src: Path, a: float, dur: float, out: Path, *, width: int, height: int, fps: int,
               hold_s: float = 0.0) -> Path:
    """O trecho `[a, a+dur]` em h264 mudo, no tamanho e fps finais e GOP de 15 quadros. Já feito: não refaz."""
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.tmp.mp4")
    try:
        run_ffmpeg(["-ss", f"{max(0.0, a):.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-an", "-sn", "-dn",
                    "-vf", proxy_filter(width, height, fps, hold_s), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", str(PROXY_CRF), "-g", str(PROXY_GOP), "-keyint_min", str(PROXY_GOP),
                    "-movflags", "+faststart", str(tmp)], timeout=600)
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise ClipError("O ffmpeg não gerou o trecho.")
        tmp.replace(out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def make_poster(src: Path, t: float, out: Path, *, width: int = POSTER_WIDTH) -> Path:
    """Um quadro JPEG do instante `t` (o pôster do trecho)."""
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
                "-q:v", "3", str(out)], timeout=120)
    if not out.is_file():
        raise ClipError("O ffmpeg não gerou o pôster do trecho.")
    return out


def describe(posters: list[Path], label: str = "") -> list[tuple[str, str]]:
    """(descrição curta, material) de cada pôster pelo provedor de visão (offline: o `MockVision`)."""
    from studio.providers import vision

    try:
        answers = vision.classify_frames(posters, vision.SHOT_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - sem visão, a descrição fica só com o título
        log.warning("descrição dos planos falhou: %s", exc)
        answers = [{} for _ in posters]
    out: list[tuple[str, str]] = []
    for answer in answers:
        material = str(answer.get("material") or "")
        words = [MATERIAL_WORDS.get(material, ""), MOOD_WORDS.get(str(answer.get("mood") or ""), "")]
        text = str(answer.get("on_screen_text") or "").strip()
        parts = [p for p in (label, ", ".join(w for w in words if w)) if p]
        if text and text != "TEXTO":
            parts.append(f'texto "{text[:40]}"')
        out.append((" · ".join(parts), material))
    return out


def analyze(src: Path, *, width: int, height: int, fps: int, max_clip_s: float, label: str = "",
            max_shots: int = MAX_SHOTS, hold_s: float = 0.0,
            cuts_of: Callable[[Path], list[float]] | None = None) -> list[ClipShot]:
    """Planos do vídeo com proxy, pôster e descrição. Cache: o mesmo arquivo com os mesmos parâmetros não é cortado
    de novo (`library/clips/<arquivo>/<parâmetros>/manifest-<rótulo>.json`)."""
    if not src.is_file():
        raise ClipError("O vídeo não existe.")
    params = {"w": width, "h": height, "fps": fps, "max": round(max_clip_s, 3), "n": max_shots,
              "hold": round(hold_s, 3), "v": ANALYSIS_VERSION}
    pkey = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    folder = clips_dir() / file_key(src) / pkey
    # O rótulo só muda a descrição: outro título reaproveita os proxies e refaz só o manifesto.
    manifest = folder / f"manifest-{hashlib.sha256(label.encode('utf-8')).hexdigest()[:8]}.json"
    if manifest.is_file():
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
            shots = [ClipShot(**s) for s in data.get("shots") or []]
            root = get_settings().data_dir
            if all((root / s.proxy).is_file() and (root / s.poster).is_file() for s in shots):
                return shots
        except (OSError, ValueError, TypeError):
            pass
    try:
        info = reference_video.probe(src)
    except (reference_video.ReferenceVideoError, FFmpegError) as exc:
        raise ClipError(f"Não consegui ler o vídeo: {exc}") from exc
    times = (cuts_of or detect_cuts)(src)
    planned = cut(times, float(info["dur"]), max_clip_s=max_clip_s, max_shots=max_shots)
    shots: list[ClipShot] = []
    posters: list[Path] = []
    for i, shot in enumerate(planned, 1):
        name = f"{i:03d}-{int(shot['a'] * 1000):08d}"
        proxy = make_proxy(src, shot["a"], shot["dur"], folder / f"{name}.mp4", width=width, height=height, fps=fps,
                           hold_s=hold_s)
        poster = make_poster(src, shot["a"] + shot["dur"] / 2, folder / f"{name}.jpg")
        posters.append(poster)
        shots.append(ClipShot(k=int(shot["k"]), a=float(shot["a"]), dur=float(shot["dur"]), proxy=_rel(proxy),
                              poster=_rel(poster), width=width, height=height))
    for shot, (desc, material) in zip(shots, describe(posters, label), strict=False):
        shot.desc, shot.material = desc or label or f"plano {shot.k}", material
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"params": params, "source": str(src), "shots": [asdict(s) for s in shots]},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    return shots


def copy_into_project(proxy_rel: str, project_id: str) -> str:
    """Copia o proxy para a pasta do projeto (`uploads/<projeto>/clips/<chave>.mp4`): a cena não depende da
    biblioteca. Devolve o caminho relativo."""
    root = get_settings().data_dir
    src = root / proxy_rel
    if not src.is_file():
        raise ClipError("O trecho do acervo sumiu: colete o acervo de novo.")
    key = hashlib.sha256(proxy_rel.encode("utf-8")).hexdigest()[:16]
    rel = f"uploads/{project_id}/clips/{key}.mp4"
    dst = root / rel
    if not (dst.is_file() and dst.stat().st_size == src.stat().st_size):
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f".{key}.tmp")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
    return rel
