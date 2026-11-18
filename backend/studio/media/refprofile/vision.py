"""Visão sobre os planos da referência (ADR 0004 §3.2, `vision.py`): material, texto na tela, tipografia, meme.

- Tiras de 3 quadros por plano (início, meio e fim), cada quadro com 640 px no lado maior, lado a lado: a folha antiga
  (1 quadro de 384 px) deixava o texto ilegível.
- Os quadros saem de uma passada só do ffmpeg (4 fps, leitura em fluxo), não de um `-ss` por quadro.
- Classificação em lotes de 12 tiras pelo provedor de `providers/vision.py` (Claude Code; Codex de reserva; `MockVision`
  offline), com o esquema `SHOT_SCHEMA`.
- Cache pelo sha256 da tira (mais a versão do esquema): reanalisar não gasta cota de novo.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from studio.config import get_settings
from studio.core.editing.profile import MATERIALS
from studio.media.ffmpeg import FFmpegError
from studio.providers import vision as vision_provider

STRIP_SIDE = 640
STRIP_FPS = 4.0
BATCH = 12
MAX_SHOTS = 360  # acima disso, uma amostra espaçada no tempo (a mistura é pesada pela duração)
MIN_SHOT_S = 0.4
SCHEMA_VERSION = "v1"
STRIP_QUALITY = 80
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def frame_dims(w: int, h: int, side: int = STRIP_SIDE) -> tuple[int, int]:
    if w <= 0 or h <= 0:
        return side, side * 9 // 16 // 2 * 2
    if w >= h:
        return side, max(2, int(round(side * h / w / 2)) * 2)
    return max(2, int(round(side * w / h / 2)) * 2), side


def pick_shots(shots: list[dict[str, Any]], limit: int = MAX_SHOTS) -> list[dict[str, Any]]:
    """Os planos regulares a classificar (todos, ou `limit` espaçados no tempo)."""
    regular = [s for s in shots if not s.get("micro") and float(s["dur"]) >= MIN_SHOT_S]
    if len(regular) <= limit:
        return regular
    step = len(regular) / limit
    return [regular[int(i * step)] for i in range(limit)]


def strip_times(shot: dict[str, Any]) -> list[float]:
    a, b = float(shot["a"]), float(shot["b"])
    d = b - a
    return [a + 0.1 * d, a + 0.5 * d, max(a, b - 0.1 * d)]


def frames_at(path: Path, wanted: list[float], w: int, h: int, *, fps: float = STRIP_FPS,
              timeout: float = 7200) -> dict[int, Image.Image]:
    """Os quadros mais próximos dos instantes pedidos, numa passada do ffmpeg a `fps` (índice = round(t × fps))."""
    fw, fh = frame_dims(w, h)
    need = {max(0, int(round(t * fps))) for t in wanted}
    if not need:
        return {}
    last = max(need)
    cmd = [get_settings().ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(path), "-an", "-sn", "-dn",
           "-vf", f"fps={fps},scale={fw}:{fh}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    size = fw * fh * 3
    out: dict[int, Image.Image] = {}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW)
    try:
        assert proc.stdout is not None
        idx = 0
        while idx <= last:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            if idx in need:
                out[idx] = Image.frombytes("RGB", (fw, fh), buf)
            idx += 1
    finally:
        proc.kill()
        proc.wait(timeout=30)
    if not out and need:
        raise FFmpegError("ffmpeg não devolveu os quadros das tiras")
    return out


def build_strips(path: Path, shots: list[dict[str, Any]], info: dict[str, Any], out_dir: Path,
                 progress: Callable[[float, str], None] | None = None) -> list[Path]:
    """Uma tira JPG por plano em `out_dir/strip-<k>.jpg` (reaproveita as que já existem)."""
    say = progress or (lambda f, m: None)
    out_dir.mkdir(parents=True, exist_ok=True)
    targets = [out_dir / f"strip-{int(s['k'])}.jpg" for s in shots]
    missing = [(s, t) for s, t in zip(shots, targets, strict=False) if not t.is_file()]
    if missing:
        say(0.0, f"Lendo os quadros de {len(missing)} planos")
        times = [t for s, _ in missing for t in strip_times(s)]
        frames = frames_at(path, times, int(info.get("w") or 0), int(info.get("h") or 0))
        fw, fh = frame_dims(int(info.get("w") or 0), int(info.get("h") or 0))
        blank = Image.new("RGB", (fw, fh), (0, 0, 0))
        for n, (shot, target) in enumerate(missing):
            strip = Image.new("RGB", (fw * 3, fh), (0, 0, 0))
            for j, t in enumerate(strip_times(shot)):
                strip.paste(frames.get(max(0, int(round(t * STRIP_FPS))), blank), (j * fw, 0))
            strip.save(target, "JPEG", quality=STRIP_QUALITY)
            if n % 20 == 0:
                say(0.3 + 0.7 * (n + 1) / len(missing), f"Tiras dos planos ({n + 1}/{len(missing)})")
    return targets


def _cache_dir() -> Path:
    return get_settings().cache_dir / "refprofile" / "vision"


def strip_key(path: Path) -> str:
    return hashlib.sha256(path.read_bytes() + SCHEMA_VERSION.encode()).hexdigest()[:40]


def classify(strips: list[Path], provider: Any | None = None,
             progress: Callable[[float, str], None] | None = None) -> tuple[list[dict[str, Any]], str]:
    """Classificação de cada tira (na ordem), em lotes de 12, com cache pelo sha da tira. Devolve também o id do
    provedor que respondeu ("cache" se tudo veio do cache). O `MockVision` não usa o cache: a resposta de mentira do
    modo offline nunca passa por resposta real depois."""
    say = progress or (lambda f, m: None)
    vis = provider or vision_provider.get_vision()
    cacheable = not isinstance(vis, vision_provider.MockVision)
    cache = _cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    keys = [strip_key(p) for p in strips]
    results: list[dict[str, Any] | None] = [None] * len(strips)
    used = "cache" if cacheable else vision_provider.MockVision.id
    for i, key in enumerate(keys):
        file = cache / f"{key}.json"
        if cacheable and file.is_file():
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                results[i] = data["result"]
                used = data.get("provider") or used
            except (OSError, ValueError, KeyError):
                results[i] = None
    todo = [i for i, r in enumerate(results) if r is None]
    for b in range(0, len(todo), BATCH):
        batch = todo[b:b + BATCH]
        say(b / max(1, len(todo)), f"Classificando os planos ({b + len(batch)}/{len(todo)})")
        answers = vis.classify_frames([strips[i] for i in batch], vision_provider.SHOT_SCHEMA)
        used = getattr(vis, "used", None) or getattr(vis, "id", "visão")
        for i, answer in zip(batch, answers, strict=False):
            clean = normalize(answer)
            results[i] = clean
            if cacheable and not answer.get("missing"):
                (cache / f"{keys[i]}.json").write_text(json.dumps({"provider": used, "result": clean},
                                                                  ensure_ascii=False), encoding="utf-8")
    return [r or normalize({}) for r in results], used


def _choice(value: Any, allowed: tuple[str, ...]) -> str:
    text = str(value or "").strip().lower()
    return text if text in allowed else ""


def normalize(answer: dict[str, Any]) -> dict[str, Any]:
    """Resposta da visão limpa: material válido (senão "other"), textos curtos, booleanos de verdade."""
    material = str(answer.get("material") or "").strip().lower()
    text = " ".join(str(answer.get("on_screen_text") or "").split())[:120]
    return {
        "material": material if material in MATERIALS else "other",
        "on_screen_text": text,
        "text_case": _choice(answer.get("text_case"), ("upper", "lower", "mixed")),
        "text_pos": _choice(answer.get("text_pos"), ("top", "center", "bottom", "mixed")),
        "font_desc": " ".join(str(answer.get("font_desc") or "").split())[:80],
        "has_overlay": bool(answer.get("has_overlay")),
        "overlay_kind": " ".join(str(answer.get("overlay_kind") or "").lower().split())[:20],
        "is_meme": bool(answer.get("is_meme")),
        "mood": " ".join(str(answer.get("mood") or "").split())[:30],
    }
