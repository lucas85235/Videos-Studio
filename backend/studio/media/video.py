"""Montagem final: concatena os blocos, aplica sobreposições globais e junta o áudio mixado."""

from __future__ import annotations

import tempfile
from pathlib import Path

from studio.media.ffmpeg import run_ffmpeg, video_encoder_args


def _hex_to_ffmpeg(color: str) -> str:
    color = color.strip().lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    return f"0x{color[:6]}"


def assemble(
    chunks: list[Path],
    audio: Path,
    out_path: Path,
    *,
    width: int,
    height: int,
    fps: int,
    duration: float,
    progress_color: str | None = None,
    watermark: Path | None = None,
) -> Path:
    """Gera o MP4 final. Sem sobreposições, só copia os fluxos (rápido)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        listing = Path(tmp) / "chunks.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in chunks), encoding="utf-8")
        needs_overlay = bool(progress_color) or (watermark is not None and watermark.exists())
        if not needs_overlay:
            run_ffmpeg([
                "-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "copy",
                "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out_path),
            ])
            return out_path
        inputs = ["-f", "concat", "-safe", "0", "-i", str(listing), "-i", str(audio)]
        graph: list[str] = []
        current = "[0:v]"
        next_input = 2
        if progress_color:
            bar_h = max(6, round(height * 0.006))
            inputs += ["-f", "lavfi", "-i", f"color=c={_hex_to_ffmpeg(progress_color)}:s={width}x{bar_h}:r={fps}"]
            graph.append(f"{current}[{next_input}:v]overlay=x='-w+w*t/{max(0.1, duration):.3f}':y=H-h:shortest=1[pb]")
            current = "[pb]"
            next_input += 1
        if watermark is not None and watermark.exists():
            inputs += ["-i", str(watermark)]
            wm_w = max(32, round(width * 0.12))
            margin = round(width * 0.03)
            graph.append(f"[{next_input}:v]format=rgba,colorchannelmixer=aa=0.65,scale={wm_w}:-1[wm]")
            graph.append(f"{current}[wm]overlay=x=W-w-{margin}:y={margin}[wmo]")
            current = "[wmo]"
            next_input += 1
        graph.append(f"{current}format=yuv420p[vout]")
        run_ffmpeg([
            *inputs, "-filter_complex", ";".join(graph), "-map", "[vout]", "-map", "1:a:0",
            *video_encoder_args("high"), "-r", str(fps), "-c:a", "copy",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out_path),
        ])
    return out_path
