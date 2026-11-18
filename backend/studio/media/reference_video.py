"""Vídeo de referência em planos: cortes, folhas de contato, ritmo e paleta. Origem: `motor/passos/trailer_shots.py`.

Serve para estudar ritmo e enquadramento, não para reusar o material. O download por link usa o yt-dlp, opcional
(ADR 0003, decisão 4): `VULTO_YTDLP` ou o `yt-dlp` no PATH.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from statistics import median
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from studio.config import get_settings
from studio.media import safe_http
from studio.media.ffmpeg import FFmpegError
from studio.media.ffmpeg import probe as ff_probe

TW, TH, COLS, ROWS = 384, 216, 5, 4  # quadro da folha (216 x 384 no vídeo vertical)
PER_SHEET = COLS * ROWS
CUT_THRESHOLD = 0.3
MIN_SHOT_S = 0.4  # plano mais curto que isto é flash ou corte duplo
LABEL_COLOR = (255, 230, 80)
SHEET_QUALITY = 82
PALETTE_SIZE = 5
PALETTE_PX = 64
YTDLP_FORMAT = "bv*[height<=1080]+ba/b[height<=1080]"
YTDLP_TIMEOUT_S = 900
YTDLP_MISSING = ("Para baixar por link, instale o yt-dlp (`winget install yt-dlp`) ou envie o arquivo do vídeo.")
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class ReferenceVideoError(RuntimeError):
    """Falha ao baixar ou analisar o vídeo de referência: mensagem em português."""


def references_dir() -> Path:
    return get_settings().library_dir / "references"


# --------------------------------------------------------------------------------------------
# yt-dlp (opcional)
# --------------------------------------------------------------------------------------------


def ytdlp_path() -> str | None:
    env = (os.environ.get("VULTO_YTDLP") or "").strip()
    if env:
        return env if Path(env).is_file() or shutil.which(env) else None
    return shutil.which("yt-dlp")


def ytdlp_status() -> dict[str, Any]:
    if ytdlp_path():
        return {"ok": True, "message": "Download por link disponível (yt-dlp)."}
    return {"ok": False, "message": YTDLP_MISSING}


def download(url: str, out_dir: Path, name: str, *, runner: Callable[..., subprocess.CompletedProcess] | None = None
             ) -> tuple[Path, str]:
    """Baixa o vídeo (até 1080p, MP4) em `out_dir/<name>.mp4`; devolve o arquivo e o título."""
    exe = ytdlp_path()
    if not exe:
        raise ReferenceVideoError(YTDLP_MISSING)
    url = safe_http.check_url(url)
    out_dir.mkdir(parents=True, exist_ok=True)
    settings = get_settings()
    cmd = [exe, "-f", YTDLP_FORMAT, "--merge-output-format", "mp4", "--no-playlist", "--no-progress",
           "--no-simulate", "--print", "%(title)s", "-o", str(out_dir / f"{name}.%(ext)s")]
    ffmpeg = Path(settings.ffmpeg)
    if ffmpeg.is_file():
        cmd += ["--ffmpeg-location", str(ffmpeg.parent)]
    cmd.append(url)
    run = runner or subprocess.run
    try:
        proc = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                   timeout=YTDLP_TIMEOUT_S, creationflags=_NO_WINDOW)
    except subprocess.TimeoutExpired as exc:
        raise ReferenceVideoError("O download passou de 15 minutos e foi parado.") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["erro desconhecido"]
        raise ReferenceVideoError(f"O yt-dlp não baixou o vídeo: {tail[0][:300]}")
    target = out_dir / f"{name}.mp4"
    if not target.is_file():
        found = sorted(out_dir.glob(f"{name}.*"))
        if not found:
            raise ReferenceVideoError("O yt-dlp terminou, mas o arquivo não apareceu.")
        target = found[0]
    title = next((ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()), "")
    return target, title


# --------------------------------------------------------------------------------------------
# Análise
# --------------------------------------------------------------------------------------------


def _fps(text: str) -> float:
    try:
        num, _, den = str(text).partition("/")
        return round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    """Rotação do vídeo em graus (0, 90, 180 ou 270): side_data (displaymatrix) ou a tag antiga `rotate`."""
    values = [sd.get("rotation") for sd in stream.get("side_data_list") or [] if isinstance(sd, dict)]
    values.append((stream.get("tags") or {}).get("rotate"))
    for v in values:
        try:
            deg = round(float(v)) % 360
        except (TypeError, ValueError):
            continue
        if deg:
            return deg
    return 0


def probe(path: Path) -> dict[str, Any]:
    """Largura, altura, fps, codec e duração (ffprobe)."""
    data = ff_probe(path)
    stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if stream is None:
        raise ReferenceVideoError("O arquivo não tem vídeo.")
    dur = float(data.get("format", {}).get("duration") or stream.get("duration") or 0.0)
    w, h = int(stream.get("width") or 0), int(stream.get("height") or 0)
    if _rotation(stream) in (90, 270):  # vídeo de celular gravado "deitado" com rotação: vale o que se vê
        w, h = h, w
    return {"w": w, "h": h,
            "fps": _fps(stream.get("r_frame_rate") or "0/1"), "codec": stream.get("codec_name") or "",
            "dur": round(dur, 3)}


def cuts(path: Path, thr: float = CUT_THRESHOLD) -> list[float]:
    """Instantes dos cortes (`select='gt(scene,thr)'` num vídeo reduzido a 320 px)."""
    cmd = [get_settings().ffmpeg, "-hide_banner", "-nostats", "-i", str(path), "-an", "-vf",
           f"scale=320:-2,select='gt(scene,{thr})',showinfo", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=3600,
                          creationflags=_NO_WINDOW)
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg falhou ao achar os cortes: {proc.stderr.strip()[-500:]}")
    return [float(m) for m in re.findall(r"pts_time:([0-9.]+)", proc.stderr)]


def shots_from(cut_times: list[float], dur: float, micro: list[dict[str, Any]] | None = None
               ) -> list[dict[str, Any]]:
    """Planos entre os cortes. O plano com menos de 0,4 s fica fora da folha de contato e do ritmo antigo, mas não se
    perde: vai para `micro` quando a lista é passada (stinger, flash, corte de meme; ADR 0004 §3.2)."""
    ts = [0.0] + [t for t in cut_times if t > CUT_THRESHOLD]
    ts.append(dur)
    shots: list[dict[str, Any]] = []
    for a, b in zip(ts, ts[1:], strict=False):
        if b - a < MIN_SHOT_S:
            if micro is not None and b - a > 0:
                micro.append({"a": round(a, 2), "dur": round(b - a, 2)})
            continue
        shots.append({"k": len(shots) + 1, "a": round(a, 2), "b": round(b, 2), "dur": round(b - a, 2)})
    return shots


def frame_at(path: Path, t: float, width: int, height: int) -> Image.Image:
    """Um quadro do vídeo no instante `t`, no tamanho pedido."""
    cmd = [get_settings().ffmpeg, "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(path), "-frames:v", "1",
           "-vf", f"scale={width}:{height}", "-f", "image2pipe", "-vcodec", "png", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=_NO_WINDOW)
    if proc.returncode != 0 or not proc.stdout:
        return Image.new("RGB", (width, height), (0, 0, 0))
    with Image.open(io.BytesIO(proc.stdout)) as im:
        return im.convert("RGB")


def _label_font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    for path in ("C:/Windows/Fonts/consolab.ttf", "C:/Windows/Fonts/consola.ttf"):
        try:
            return ImageFont.truetype(path, 22)
        except OSError:
            continue
    return ImageFont.load_default(size=22)


def sheet_tile(info: dict[str, Any]) -> tuple[int, int]:
    """Tamanho do quadro na folha: 384 x 216, ou 216 x 384 no vídeo vertical."""
    return (TH, TW) if info.get("h", 0) > info.get("w", 0) else (TW, TH)


def contact_sheets(path: Path, shots: list[dict[str, Any]], info: dict[str, Any], out_dir: Path, name: str,
                   *, on_frame: Callable[[int, Image.Image], None] | None = None) -> list[Path]:
    """Folhas de 5 x 4 quadros (o do meio de cada plano) com o rótulo "#k a s (dur)"; 20 planos por folha, JPG 82."""
    tw, th = sheet_tile(info)
    out_dir.mkdir(parents=True, exist_ok=True)
    font = _label_font()
    sheets: list[Path] = []
    for si in range(0, len(shots), PER_SHEET):
        sheet = Image.new("RGB", (COLS * tw, ROWS * th), (0, 0, 0))
        dr = ImageDraw.Draw(sheet)
        for j, sh in enumerate(shots[si:si + PER_SHEET]):
            t = sh["a"] + sh["dur"] * 0.5
            frame = frame_at(path, t, tw, th)
            if on_frame:
                on_frame(si + j, frame)
            x, y = (j % COLS) * tw, (j // COLS) * th
            sheet.paste(frame, (x, y))
            lab = f"#{sh['k']} {sh['a']:.1f}s ({sh['dur']:.1f})"
            dr.rectangle([x, y, x + 12 * len(lab) + 10, y + 28], fill=(0, 0, 0))
            dr.text((x + 5, y + 2), lab, fill=LABEL_COLOR, font=font)
        out = out_dir / f"{name}-planos-{si // PER_SHEET + 1}.jpg"
        sheet.save(out, "JPEG", quality=SHEET_QUALITY)
        sheets.append(out)
    return sheets


def kmeans_palette(pixels: np.ndarray, k: int = PALETTE_SIZE, iterations: int = 20) -> list[str]:
    """Paleta por k-means com início determinístico (quantis da luminância); cores em ordem de quantidade, sem
    repetir cor quase igual."""
    data = np.asarray(pixels, dtype=np.float64).reshape(-1, 3)
    if not len(data):
        return []
    lum = data @ np.array([0.299, 0.587, 0.114])
    order = np.argsort(lum, kind="stable")
    picks = order[np.linspace(0, len(order) - 1, k).round().astype(int)]
    centers = data[picks].copy()
    labels = np.zeros(len(data), dtype=int)
    for _ in range(iterations):
        dist = ((data[:, None, :] - centers[None, :, :]) ** 2).sum(-1)
        labels = dist.argmin(1)
        moved = False
        for c in range(k):
            members = data[labels == c]
            if len(members):
                new = members.mean(0)
                if not np.allclose(new, centers[c]):
                    moved = True
                centers[c] = new
        if not moved:
            break
    counts = np.bincount(labels, minlength=k)
    out: list[str] = []
    kept: list[np.ndarray] = []
    for c in np.argsort(-counts, kind="stable"):
        if counts[c] == 0:
            continue
        if any(np.abs(centers[c] - other).max() < 12 for other in kept):
            continue
        kept.append(centers[c])
        r, g, b = (int(round(v)) for v in np.clip(centers[c], 0, 255))
        out.append(f"#{r:02x}{g:02x}{b:02x}")
    return out


def stats(shots: list[dict[str, Any]], info: dict[str, Any], frames: list[Image.Image] | None = None) -> dict[str, Any]:
    """Planos, duração mediana e média, cortes por minuto e paleta de 5 cores (quadros do meio a 64 px)."""
    durs = [float(s["dur"]) for s in shots]
    dur = float(info.get("dur") or sum(durs) or 0.0)
    out: dict[str, Any] = {
        "shots": len(shots),
        "median_s": round(median(durs), 2) if durs else 0.0,
        "mean_s": round(sum(durs) / len(durs), 2) if durs else 0.0,
        "cuts_per_min": round(max(0, len(shots) - 1) / (dur / 60), 1) if dur > 0 else 0.0,
        "palette": [],
    }
    if frames:
        pixels = []
        for frame in frames:
            small = frame.convert("RGB").copy()
            small.thumbnail((PALETTE_PX, PALETTE_PX))
            pixels.append(np.asarray(small, dtype=np.float64).reshape(-1, 3))
        out["palette"] = kmeans_palette(np.concatenate(pixels))
    return out


def analyze(path: Path, out_dir: Path, name: str, *, progress: Callable[[float, str], None] | None = None
            ) -> dict[str, Any]:
    """Tudo de uma vez: `info`, `shots`, folhas (caminhos absolutos) e `stats`."""
    say = progress or (lambda f, m: None)
    say(0.05, "Lendo o vídeo")
    info = probe(path)
    say(0.15, "Achando os cortes")
    micro: list[dict[str, Any]] = []
    shots = shots_from(cuts(path), info["dur"], micro)
    frames: list[Image.Image] = []

    def keep(i: int, frame: Image.Image) -> None:
        frames.append(frame)
        say(0.3 + 0.6 * (i + 1) / max(1, len(shots)), f"Folha de contato ({i + 1}/{len(shots)})")

    sheets = contact_sheets(path, shots, info, out_dir, name, on_frame=keep)
    measured = stats(shots, info, frames)
    measured["micro_shots"] = len(micro)  # o perfil (`media/refprofile`) mede os micro-planos com mais cuidado
    return {"info": info, "shots": shots, "sheets": sheets, "stats": measured, "micro": micro}
