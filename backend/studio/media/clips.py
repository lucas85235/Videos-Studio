"""Trechos de vídeo para o acervo (ADR 0004 §3.4): baixar, cortar em planos e fazer o proxy mudo do plano escolhido.

Porte melhorado de `canais/motor/passos/trailer_shots.py` (planos por detecção de corte) e `make_clips.py` (trecho
sem áudio, modo `hold`):

- `fetch`: baixa o link com o yt-dlp, com cache pela chave do link. Só conta como baixado o `video.<ext>` final com o
  `title.txt` ao lado (escrito depois do sucesso): sobra de download que falhou (`video.f137.mp4`) não vale;
- `cut`: planos entre os cortes, cada um com no máximo `max_clip_s` (plano longo vira pedaços iguais), sem os
  micro-planos (flash, corte duplo) e com até `max_shots` planos espalhados pelo vídeo;
- `dead_frame` / `trim_dead`: quadro morto (tela preta, branca ou lisa, fade, cartela legal ou final com texto ou logo
  sobre fundo liso) não é material: o plano fica só com o trecho vivo e, sem trecho vivo que sirva, sai;
- `analyze`: cortes, pôster JPEG e descrição de cada plano, em cache pela impressão do arquivo. Não faz proxy: o
  proxy sai sob demanda, só do plano que uma cena escolheu (`proxy_for_project`);
- `make_proxy`: h264 mudo no tamanho e no fps finais do vídeo, GOP de 15 quadros (busca rápida no Remotion), com o
  modo `hold` (ida uma vez e o último quadro parado, nunca o vai e volta que "treme"). Vídeo deitado num quadro em pé
  (ou o contrário) entra inteiro sobre o próprio quadro desfocado, sem perder dois terços da imagem.

O som original nunca entra (`-an`). Os arquivos ficam em `library/clips/` (fora do cache do grafo); as fontes baixadas
por link têm teto de espaço (`prune_sources`, as menos usadas saem primeiro). O proxy do plano escolhido vai direto
para a pasta do projeto.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

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
ANALYSIS_VERSION = 3
"""3: os planos perdem os quadros mortos (`trim_dead`); o corte antigo tinha a cartela do ESRB e a tela final."""
VIDEO_EXTS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".m4v"})
BLUR_ASPECT_RATIO = 1.4
"""Vídeo de origem com proporção tão diferente assim do quadro final (16:9 num Short 9:16) entra inteiro sobre o
próprio quadro desfocado, em vez de recortado."""
SOURCES_MAX_BYTES = 20_000_000_000
"""Teto das fontes baixadas por link (`library/clips/src`): passou, as menos usadas saem."""

# Material dito pela visão (`providers.vision.SHOT_SCHEMA`), em palavras para o pedido do storyboard.
MATERIAL_WORDS = {
    "gameplay": "jogo", "official_trailer": "trailer", "page_capture": "página", "news": "notícia",
    "text_card": "texto na tela", "photo_real": "imagem real", "ai_illustration": "ilustração", "chart_map": "gráfico",
    "facecam": "rosto", "mascot": "mascote", "meme_reaction": "meme", "stock_video": "banco de vídeo",
}
MOOD_WORDS = {"dark": "escuro", "bright": "claro", "neutral": ""}

# Quadro morto (`dead_frame`), medido no quadro em cinza (0 a 1) da passada dos cortes. O "fundo" é o tom mediano do
# quadro; o que fica a menos de DEAD_TOL dele é fundo liso.
DEAD_TOL = 0.03
DEAD_BLANK_SHARE = 0.97
"""Quadro em branco de verdade, em qualquer tom: tela preta, flash ou fade para branco, cor lisa."""
DEAD_DARK = 0.2
"""Fora o quadro em branco, só fundo escuro (tom mediano até aqui) é quadro morto: a cartela legal, a tela final e o
fade são letra ou logo sobre o preto. Fundo claro ou médio com conteúdo é material (matéria e post sobre o branco,
produto no fundo infinito, quadro branco, infográfico), mesmo com muito fundo liso."""
DEAD_SPREAD = 0.7
DEAD_COL_INK = 0.02
"""Cartela e tela final são um bloco no meio do fundo: o conteúdo (fora do fundo liso em mais de DEAD_COL_INK da
coluna) ocupa até DEAD_SPREAD das colunas. Página ou post no modo escuro enche a largura e fica."""
DEAD_FLAT_SHARE = 0.85
"""Fundo escuro liso em tanto do quadro: fade, cartela final com logo ou data pequenos."""
SLATE_BG_SHARE = 0.6
SLATE_FAR = 0.45
SLATE_TWO_TONE = 0.5
"""Cartela (aviso legal, classificação etária, texto sobre fundo escuro liso): fundo liso em SLATE_BG_SHARE do quadro
e, do que sobra, pelo menos esta parte em contraste duro com ele (letra ou moldura branca no preto). Arte com fundo
liso (a capa no meio de um fundo cinza) tem meio-tom de sobra e fica."""
# Texto de cartela legal lido pela visão no plano (`texto "..."` da descrição): aviso de classificação etária, direitos
# e marca registrada. Vale também para a análise já em cache (feita antes da medida dos quadros).
_SLATE_TEXT_RE = re.compile(
    r"\b(esrb|pegi|classind|rating information|content (?:may be )?inappropriate|may contain content|"
    r"all rights reserved|todos os direitos reservados|registered trademarks?|trademarks? of|"
    r"not affiliated with|not endorsed by|classifica[cç][aã]o indicativa|n[aã]o recomendado para menores)\b",
    re.IGNORECASE)
_DESC_TEXT_RE = re.compile(r'texto "([^"]*)"')


class ClipError(RuntimeError):
    """Falha ao baixar ou cortar o vídeo: mensagem em português."""


class ClipCancelled(ClipError):
    """A coleta foi cancelada no meio da análise."""


@dataclass
class ClipShot:
    k: int
    """Número do plano no vídeo (base 1)."""
    a: float
    """Início no vídeo de origem (s)."""
    dur: float
    poster: str
    """Pôster JPEG do meio do plano (relativo a VS_DATA_DIR)."""
    desc: str = ""
    material: str = ""
    width: int = 0
    """Tamanho do vídeo de ORIGEM (o proxy sai no tamanho do estilo)."""
    height: int = 0
    src: str = ""
    """Vídeo de origem relativo a VS_DATA_DIR ("" quando ele fica fora da pasta de dados, como o thumbkit)."""


@dataclass
class LinkSource:
    path: Path
    title: str = ""
    uploader: str = ""
    """Canal/dono do vídeo segundo o yt-dlp (vai no crédito)."""


# --------------------------------------------------------------------------------------------
# Pastas, chaves e arquivos
# --------------------------------------------------------------------------------------------


def clips_dir() -> Path:
    return get_settings().library_dir / "clips"


def rel_or_none(path: Path) -> str | None:
    try:
        return path.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()
    except ValueError:
        return None


def _rel(path: Path) -> str:
    rel = rel_or_none(path)
    if rel is None:
        raise ClipError("O arquivo do trecho ficou fora da pasta de dados.")
    return rel


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


def _tmp_for(out: Path) -> Path:
    """Nome temporário ÚNICO ao lado de `out` (duas coletas ao mesmo tempo nunca escrevem no mesmo arquivo)."""
    return out.with_name(f".{out.stem}.{uuid.uuid4().hex[:10]}.tmp{out.suffix}")


def place(tmp: Path, out: Path) -> None:
    """Troca atômica do temporário pelo final. No Windows, outra coleta pode ter acabado de pôr o mesmo arquivo (e
    ele estar aberto): o que já está lá vale e o temporário sai."""
    try:
        os.replace(tmp, out)
    except PermissionError:
        if not (out.is_file() and out.stat().st_size > 0):
            raise
        tmp.unlink(missing_ok=True)


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    try:
        tmp.write_text(text, encoding="utf-8")
        place(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


# --------------------------------------------------------------------------------------------
# Download (yt-dlp, opcional)
# --------------------------------------------------------------------------------------------


def source_dir(url: str) -> Path:
    return clips_dir() / "src" / link_key(url)


def _final_video(folder: Path) -> Path | None:
    """O `video.<ext>` final da pasta; os intermediários do yt-dlp (`video.f137.mp4`) nunca contam."""
    for path in sorted(folder.glob("video.*")):
        if path.is_file() and path.stem == "video" and path.suffix.lower() in VIDEO_EXTS and path.stat().st_size > 0:
            return path
    return None


def cached_source(url: str) -> LinkSource | None:
    """O vídeo do link já baixado, sem rede. None: ainda não baixado (ou o download não terminou: sem `title.txt`)."""
    folder = source_dir(url)
    title_file = folder / "title.txt"
    if not title_file.is_file():
        return None
    video = _final_video(folder)
    if video is None:
        return None
    try:
        os.utime(title_file)  # marca de uso: a limpeza tira primeiro as fontes paradas há mais tempo
    except OSError:
        pass
    uploader_file = folder / "uploader.txt"
    return LinkSource(video, title_file.read_text(encoding="utf-8").strip(),
                      uploader_file.read_text(encoding="utf-8").strip() if uploader_file.is_file() else "")


def save_source(url: str, data_or_file: bytes | Path, *, title: str = "", uploader: str = "") -> LinkSource:
    """Grava a fonte do link já baixada (bytes ou arquivo temporário) como `video.mp4` e marca como concluída."""
    folder = source_dir(url)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / ("video.mp4" if isinstance(data_or_file, bytes) else f"video{data_or_file.suffix.lower()}")
    (folder / "title.txt").unlink(missing_ok=True)  # incompleto até o fim
    for old in folder.glob("video.*"):
        if old != target:
            old.unlink(missing_ok=True)
    tmp = _tmp_for(target)
    try:
        if isinstance(data_or_file, bytes):
            tmp.write_bytes(data_or_file)
        else:
            shutil.move(str(data_or_file), tmp)
        place(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)
    write_text_atomic(folder / "uploader.txt", uploader)
    write_text_atomic(folder / "title.txt", title)  # por último: é a marca de conclusão
    return LinkSource(target, title, uploader)


def fetch(url: str, *, runner: Callable[..., subprocess.CompletedProcess] | None = None) -> LinkSource:
    """Baixa o vídeo do link (até 1080p) uma vez; devolve o arquivo, o título limpo e o canal. Offline só com um
    `runner` injetado (testes): nunca chama o yt-dlp de verdade nem resolve DNS."""
    found = cached_source(url)
    if found is not None:
        return found
    offline = get_settings().offline
    if offline and runner is None:
        raise ClipError("Modo offline: o link não pode ser baixado agora.")
    exe = reference_video.ytdlp_path() if runner is None else "yt-dlp"
    if not exe:
        raise ClipError(reference_video.YTDLP_MISSING)
    target_url = url
    if not offline:
        from studio.media import safe_http

        target_url = safe_http.check_url(url)
    folder = source_dir(url)  # a chave é a do link como o usuário deu (a mesma de `cached_source`)
    work = folder / f".dl-{uuid.uuid4().hex[:10]}"
    work.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [exe, "-f", reference_video.YTDLP_FORMAT, "--merge-output-format", "mp4", "--no-playlist",
               "--no-progress", "--no-simulate", "--print", "%(title)s", "--print", "%(channel,uploader|)s",
               "-o", str(work / "video.%(ext)s")]
        ffmpeg = Path(get_settings().ffmpeg)
        if ffmpeg.is_file():
            cmd += ["--ffmpeg-location", str(ffmpeg.parent)]
        cmd.append(target_url)
        run = runner or subprocess.run
        try:
            proc = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=reference_video.YTDLP_TIMEOUT_S, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise ClipError("O download passou de 15 minutos e foi parado.") from exc
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["erro desconhecido"]
            raise ClipError(f"O yt-dlp não baixou o vídeo: {tail[0][:300]}")
        video = _final_video(work)
        if video is None:
            raise ClipError("O yt-dlp terminou, mas o vídeo final não apareceu (o download ficou pela metade).")
        lines = [ln.strip() for ln in (proc.stdout or "").splitlines()]
        from studio.core.media_title import clean_media_title

        title = clean_media_title(lines[0]) if lines and lines[0] else ""
        uploader = " ".join((lines[1] if len(lines) > 1 else "").split())[:120]
        if uploader.upper() == "NA":
            uploader = ""
        return save_source(url, video, title=title, uploader=uploader)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def sources_bytes() -> int:
    root = clips_dir() / "src"
    if not root.is_dir():
        return 0
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def prune_sources(*, max_bytes: int = SOURCES_MAX_BYTES, keep: set[str] | None = None) -> int:
    """Apaga as fontes baixadas por link além do teto, das usadas há mais tempo para as mais recentes (as chaves em
    `keep` ficam). O proxy que uma cena já usa mora na pasta do projeto e não depende da fonte. Devolve os bytes
    liberados."""
    root = clips_dir() / "src"
    if not root.is_dir():
        return 0
    folders: list[tuple[float, int, Path]] = []
    for folder in root.iterdir():
        if not folder.is_dir():
            continue
        size = sum(p.stat().st_size for p in folder.rglob("*") if p.is_file())
        stamp = folder / "title.txt"
        used = stamp.stat().st_mtime if stamp.is_file() else folder.stat().st_mtime
        folders.append((used, size, folder))
    total = sum(size for _, size, _ in folders)
    freed = 0
    for _, size, folder in sorted(folders):
        if total - freed <= max_bytes:
            break
        if keep and folder.name in keep:
            continue
        shutil.rmtree(folder, ignore_errors=True)
        freed += size
    return freed


# --------------------------------------------------------------------------------------------
# Cortes e planos
# --------------------------------------------------------------------------------------------


def scan(path: Path) -> tuple[list[float], list[tuple[float, bool]]]:
    """Cortes e quadros mortos numa passada só, pelo mesmo extrator da referência (`media/refprofile`): uma passada
    do ffmpeg (`decode.scan_and_gray`) dá a diferença entre quadros e os quadros em cinza; dois limiares (duro e
    suave, para gameplay e trailer), rajadas coladas viradas num corte só e o candidato que é a mesma imagem ampliada
    descartado (punch, não corte). Os mesmos quadros em cinza dizem quais instantes são quadro morto (`dead_frame`):
    `(instante, morto)` de cada quadro analisado."""
    from studio.media.refprofile import cuts as ref_cuts
    from studio.media.refprofile import decode, motion

    info = reference_video.probe(path)
    found_scan, frames, times = decode.scan_and_gray(path, int(info["w"]), int(info["h"]),
                                                     float(info.get("dur") or 0) or None)
    marks = [(float(t), dead_frame(f) is not None) for f, t in zip(frames, times, strict=False)]
    cands = ref_cuts.merge_bursts(ref_cuts.candidates(found_scan.times, found_scan.mafd))
    if not cands:
        return [], marks
    found, _punches = motion.verify_cuts(cands, frames, times)
    return found, marks


def detect_cuts(path: Path) -> list[float]:
    """Instantes dos cortes do vídeo (`scan`). O plano curto que sobra entre cortes sai em `cut` (`min_shot_s`)."""
    return scan(path)[0]


def scan_step() -> float:
    """Intervalo (s) entre os quadros medidos em `scan`."""
    from studio.media.refprofile import motion

    return 1.0 / float(motion.FPS)


def dead_frame(gray: np.ndarray) -> str | None:
    """Por que o quadro (em cinza, 0–255 ou 0–1) não serve de material, ou None. "tela lisa": em branco num tom só
    (preto, branco, cor lisa) ou quase tudo preto com um bloco pequeno no meio (fade, cartela final com logo ou data);
    "cartela": texto ou moldura em contraste duro num bloco sobre o fundo escuro liso (aviso de classificação etária,
    aviso legal). Fica: imagem escura de verdade (cena noturna, com meio-tom), arte sobre fundo liso (a capa num fundo
    cinza), o que tem fundo claro (matéria, post, produto no fundo infinito, quadro branco) e o que enche a largura
    sobre o preto (página no modo escuro). A cartela legal sobre fundo claro sai pelo texto lido (`slate_text`)."""
    g = np.asarray(gray, dtype=np.float32)
    if g.size == 0 or g.ndim != 2:
        return None
    if float(g.max()) > 1.0:
        g = g / 255.0
    tone = float(np.median(g))
    dev = np.abs(g - tone)
    flat = dev < DEAD_TOL
    bg = float(flat.mean())
    if bg >= DEAD_BLANK_SHARE:
        return "tela lisa"  # em branco de verdade, em qualquer tom: preto, branco do flash, cor lisa
    if tone > DEAD_DARK:
        return None  # fundo claro ou médio com conteúdo: página, matéria, produto no fundo infinito, quadro branco
    if float(((~flat).mean(axis=0) > DEAD_COL_INK).mean()) > DEAD_SPREAD:
        return None  # o conteúdo enche a largura: página ou post no modo escuro, não um bloco no meio do preto
    if bg >= DEAD_FLAT_SHARE:
        return "tela lisa"
    far = float((dev > SLATE_FAR).mean())
    if bg >= SLATE_BG_SHARE and far >= SLATE_TWO_TONE * (1.0 - bg):
        return "cartela"
    return None


def live_span(a: float, dur: float, marks: list[tuple[float, bool]], *, step: float,
              min_s: float = MIN_POOL_SHOT_S) -> tuple[float, float] | None:
    """O trecho vivo `(início, duração)` do plano `[a, a+dur]`: a maior sequência de quadros que não são mortos
    (`marks`, um a cada `step` s). O plano que começa ou acaba num fade, na cartela ou na tela preta perde só esse
    pedaço; sem trecho vivo de `min_s`, None (o plano sai). Sem quadro medido dentro do plano, ele fica como está."""
    inside = [(t, dead) for t, dead in marks if a - 1e-6 <= t < a + dur - 1e-6]
    if not inside:
        return a, dur
    best: tuple[int, int] | None = None
    i = 0
    while i < len(inside):
        if inside[i][1]:
            i += 1
            continue
        j = i
        while j + 1 < len(inside) and not inside[j + 1][1]:
            j += 1
        if best is None or j - i > best[1] - best[0]:
            best = (i, j)
        i = j + 1
    if best is None:
        return None
    start = a if best[0] == 0 else inside[best[0]][0]
    end = a + dur if best[1] == len(inside) - 1 else min(a + dur, inside[best[1]][0] + step)
    if end - start < min_s - 1e-6:
        return None
    return round(start, 3), round(end - start, 3)


def trim_dead(planned: list[dict[str, Any]], marks: list[tuple[float, bool]], *, step: float,
              min_s: float = MIN_POOL_SHOT_S) -> list[dict[str, Any]]:
    """Os planos de `cut` só com o trecho vivo (`live_span`); o que não tem trecho vivo sai."""
    out: list[dict[str, Any]] = []
    for shot in planned:
        span = live_span(float(shot["a"]), float(shot["dur"]), marks, step=step, min_s=min_s)
        if span is not None:
            out.append({**shot, "a": span[0], "dur": span[1]})
    return out


def slate_text(desc: str) -> bool:
    """A visão leu na tela texto de cartela legal (classificação etária, direitos, marca registrada): o plano é a
    cartela, não material de cena."""
    return any(_SLATE_TEXT_RE.search(text) for text in _DESC_TEXT_RE.findall(desc or ""))


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
    return spread(out, max_shots)


def spread(shots: list[dict[str, Any]], max_shots: int) -> list[dict[str, Any]]:
    """Com mais de `max_shots` planos, ficam planos espalhados pelo vídeo inteiro (não só o começo)."""
    if len(shots) > max_shots > 0:
        picks = sorted({round(i * (len(shots) - 1) / (max_shots - 1)) for i in range(max_shots)}) if max_shots > 1 else [0]
        shots = [shots[i] for i in picks]
    return shots


def needs_blur(src_w: int, src_h: int, width: int, height: int) -> bool:
    """A origem tem proporção muito diferente do quadro final (trailer 16:9 num Short 9:16)."""
    if not (src_w and src_h and width and height):
        return False
    a, b = src_w / src_h, width / height
    return max(a, b) / min(a, b) >= BLUR_ASPECT_RATIO


def proxy_filter(width: int, height: int, fps: int, hold_s: float = 0.0, fit: str = "cover") -> str:
    """`cover` preenche o quadro final (recorta o que sobra, sem barra preta); `blur` põe o quadro de origem inteiro
    sobre ele mesmo ampliado e desfocado. No fps final; `hold_s` congela o último quadro por mais esse tempo (o modo
    `hold` do canais: ida uma vez, nunca o vai e volta)."""
    if fit == "blur":
        chain = (f"split[bgsrc][fgsrc];"
                 f"[bgsrc]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},"
                 f"boxblur=20:2,eq=brightness=-0.08[bg];"
                 f"[fgsrc]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos[fg];"
                 f"[bg][fg]overlay=(W-w)/2:(H-h)/2,fps={fps},format=yuv420p")
    else:
        chain = (f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},"
                 f"fps={fps},format=yuv420p")
    if hold_s > 0:
        chain += f",tpad=stop_mode=clone:stop_duration={hold_s:.3f}"
    return chain


def make_proxy(src: Path, a: float, dur: float, out: Path, *, width: int, height: int, fps: int,
               hold_s: float = 0.0, fit: str = "cover") -> Path:
    """O trecho `[a, a+dur]` em h264 mudo, no tamanho e fps finais e GOP de 15 quadros. Já feito: não refaz."""
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(out)
    try:
        run_ffmpeg(["-ss", f"{max(0.0, a):.3f}", "-t", f"{dur:.3f}", "-i", str(src), "-an", "-sn", "-dn",
                    "-vf", proxy_filter(width, height, fps, hold_s, fit), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", str(PROXY_CRF), "-g", str(PROXY_GOP), "-keyint_min", str(PROXY_GOP),
                    "-movflags", "+faststart", str(tmp)], timeout=600)
        if not tmp.is_file() or tmp.stat().st_size == 0:
            raise ClipError("O ffmpeg não gerou o trecho.")
        place(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


def proxy_for_project(src: Path, a: float, dur: float, project_id: str, *, width: int, height: int, fps: int,
                      src_w: int = 0, src_h: int = 0, hold_s: float = 0.0) -> str:
    """O proxy do plano direto na pasta do projeto (`uploads/<projeto>/clips/<chave>.mp4`): a cena não depende da
    biblioteca. A chave leva o arquivo de origem, o trecho, o tamanho, o fps e o enquadramento (mudou um deles, outro
    proxy). Devolve o caminho relativo."""
    if not src.is_file():
        raise ClipError("O vídeo de origem do trecho sumiu: colete o acervo de novo.")
    if not (src_w and src_h):
        try:
            info = reference_video.probe(src)
            src_w, src_h = int(info["w"]), int(info["h"])
        except (reference_video.ReferenceVideoError, FFmpegError):
            src_w = src_h = 0
    fit = "blur" if needs_blur(src_w, src_h, width, height) else "cover"
    params = {"src": file_key(src), "a": round(a, 3), "dur": round(dur, 3), "w": width, "h": height, "fps": fps,
              "fit": fit, "hold": round(hold_s, 3)}
    key = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
    rel = f"uploads/{project_id}/clips/{key}.mp4"
    make_proxy(src, a, dur, get_settings().data_dir / rel, width=width, height=height, fps=fps, hold_s=hold_s, fit=fit)
    return rel


def make_poster(src: Path, t: float, out: Path, *, width: int = POSTER_WIDTH) -> Path:
    """Um quadro JPEG do instante `t` (o pôster do trecho)."""
    if out.is_file() and out.stat().st_size > 0:
        return out
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(out)
    try:
        run_ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", "-vf", f"scale={width}:-2",
                    "-q:v", "3", str(tmp)], timeout=120)
        if not tmp.is_file():
            raise ClipError("O ffmpeg não gerou o pôster do trecho.")
        place(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
    return out


# --------------------------------------------------------------------------------------------
# Descrição e análise
# --------------------------------------------------------------------------------------------


def vision_id() -> str:
    """Identidade do provedor de visão em uso (entra no cache da descrição: trocou o provedor, descreve de novo)."""
    from studio.providers import vision

    provider = vision.get_vision()
    return str(getattr(provider, "id", "") or type(provider).__name__)


def _is_mock_vision() -> bool:
    from studio.providers import vision

    return isinstance(vision.get_vision(), vision.MockVision)


def _clock(t: float) -> str:
    t = max(0, int(round(t)))
    return f"{t // 60}:{t % 60:02d}"


def plain_desc(label: str, k: int, a: float) -> str:
    """A descrição sem visão: o rótulo do vídeo, o plano e o instante ("Trailer · plano 3, 0:42")."""
    return " · ".join(p for p in (label, f"plano {k}, {_clock(a)}") if p)


def describe(posters: list[Path], label: str = "", marks: list[tuple[int, float]] | None = None
             ) -> list[tuple[str, str]]:
    """(descrição curta, material) de cada pôster pelo provedor de visão. Com o `MockVision` (o único até o
    `ref-perfil` ligar a visão de verdade) nada de material ou humor chutado: só o rótulo, o plano e o instante."""
    marks = marks or [(i, 0.0) for i in range(1, len(posters) + 1)]
    if _is_mock_vision():
        return [(plain_desc(label, k, a), "") for k, a in marks]
    from studio.providers import vision

    try:
        answers = vision.classify_frames(posters, vision.SHOT_SCHEMA)
    except Exception as exc:  # noqa: BLE001 - sem visão, a descrição fica só com o título
        log.warning("descrição dos planos falhou: %s", exc)
        answers = [{} for _ in posters]
    out: list[tuple[str, str]] = []
    for answer, (k, a) in zip(answers, marks, strict=False):
        material = str(answer.get("material") or "")
        words = [MATERIAL_WORDS.get(material, ""), MOOD_WORDS.get(str(answer.get("mood") or ""), "")]
        text = str(answer.get("on_screen_text") or "").strip()
        parts = [p for p in (label, ", ".join(w for w in words if w)) if p]
        if text and text != "TEXTO":
            parts.append(f'texto "{text[:40]}"')
        out.append((" · ".join(parts) or plain_desc(label, k, a), material))
    return out


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def analyze(src: Path, *, max_clip_s: float, label: str = "", max_shots: int = MAX_SHOTS,
            cuts_of: Callable[[Path], list[float]] | None = None, cached_only: bool = False,
            cancelled: Callable[[], bool] | None = None,
            scan_of: Callable[[Path], tuple[list[float], list[tuple[float, bool]]]] | None = None
            ) -> list[ClipShot] | None:
    """Planos do vídeo com pôster e descrição (sem proxy: ele sai só para o plano escolhido). Os planos ficam só com
    o trecho vivo (`trim_dead`: sem a cartela legal, a tela preta ou branca, o fade e a cartela final) e o plano cuja
    tela a visão leu como cartela legal (`slate_text`) sai. `cuts_of` (só os cortes, sem medir os quadros) ou
    `scan_of` (cortes e quadros mortos) trocam a passada do ffmpeg.

    Cache em `library/clips/<arquivo>/<parâmetros>/`: `shots.json` (cortes e pôsteres; não depende do tamanho do
    vídeo final, então Short e longo usam a mesma análise) e `desc-<visão>-<rótulo>.json` (a descrição, refeita só
    quando o provedor de visão ou o rótulo mudam). `cached_only`: sem análise pronta devolve None, sem ffmpeg."""
    if not src.is_file():
        raise ClipError("O vídeo não existe.")
    params = {"max": round(max_clip_s, 3), "n": max_shots, "v": ANALYSIS_VERSION}
    pkey = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:12]
    folder = clips_dir() / file_key(src) / pkey
    root = get_settings().data_dir
    src_rel = rel_or_none(src) or ""
    shots_file = folder / "shots.json"
    data = _load_json(shots_file) if shots_file.is_file() else None
    shots: list[ClipShot] | None = None
    if isinstance(data, dict):
        try:
            shots = [ClipShot(**s) for s in data.get("shots") or []]
        except TypeError:
            shots = None
        if shots is not None and not all((root / s.poster).is_file() for s in shots):
            shots = None
    if shots is None:
        if cached_only:
            return None
        try:
            info = reference_video.probe(src)
        except (reference_video.ReferenceVideoError, FFmpegError) as exc:
            raise ClipError(f"Não consegui ler o vídeo: {exc}") from exc
        if cuts_of is not None:
            times, marks = cuts_of(src), []
        else:
            times, marks = (scan_of or scan)(src)
        # Os mortos saem antes de espalhar: a cartela tirada não leva junto a vaga de um plano vivo.
        planned = cut(times, float(info["dur"]), max_clip_s=max_clip_s, max_shots=0)
        planned = spread(trim_dead(planned, marks, step=scan_step()), max_shots)
        shots = []
        for i, shot in enumerate(planned, 1):
            if cancelled is not None and cancelled():
                raise ClipCancelled("Coleta cancelada.")
            name = f"{i:03d}-{int(shot['a'] * 1000):08d}"
            poster = make_poster(src, shot["a"] + shot["dur"] / 2, folder / f"{name}.jpg")
            shots.append(ClipShot(k=int(shot["k"]), a=float(shot["a"]), dur=float(shot["dur"]), poster=_rel(poster),
                                  width=int(info["w"]), height=int(info["h"])))
        write_text_atomic(shots_file, json.dumps({"params": params, "shots": [asdict(s) for s in shots]},
                                                 ensure_ascii=False, indent=1))
    for shot in shots:
        shot.src = src_rel
    vid = vision_id()
    desc_file = folder / (f"desc-{hashlib.sha256(vid.encode()).hexdigest()[:6]}-"
                          f"{hashlib.sha256(label.encode('utf-8')).hexdigest()[:8]}.json")
    descs = _load_json(desc_file) if desc_file.is_file() else None
    if not (isinstance(descs, list) and len(descs) == len(shots)):
        if cached_only and not _is_mock_vision():
            descs = [[plain_desc(label, s.k, s.a), ""] for s in shots]  # sem chamar a visão (pode ser paga)
        else:
            pairs = describe([root / s.poster for s in shots], label, [(s.k, s.a) for s in shots])
            descs = [[d, m] for d, m in pairs]
            write_text_atomic(desc_file, json.dumps(descs, ensure_ascii=False))
    for shot, (desc, material) in zip(shots, descs, strict=False):
        shot.desc, shot.material = str(desc) or plain_desc(label, shot.k, shot.a), str(material)
    return [s for s in shots if not slate_text(s.desc)]
