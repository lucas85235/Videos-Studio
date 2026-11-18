"""Vídeo sintético da análise de referência (ADR 0004 §3.2), gerado pelo `lavfi` do ffmpeg num diretório temporário.

6,5 s a 30 fps, 640x360:
- cortes em 1,0 / 2,3 / 2,6 / 5,0 s (2,3→2,6 é um micro-plano de 0,3 s, vermelho);
- de 2,6 a 5,0, um quadro parado do `testsrc2` com `zoompan` de 1,0 para 1,25 em 3 quadros (~3,2 s): um punch;
- áudio mudo com um `sine` de 50 ms em 3,7 s (longe dos cortes): um SFX isolado.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

CUTS = (1.0, 2.3, 2.6, 5.0)
PUNCH_S = 3.2
ONSET_S = 3.7
DURATION = 6.5
FPS = 30


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def make_video(target: Path) -> Path:
    size = f"size=640x360:rate={FPS}"
    graph = (
        "[3:v]trim=end_frame=1,loop=loop=71:size=1:start=0,setpts=N/30/TB,"
        "zoompan=z='if(lt(in,18),1,if(lt(in,21),1+0.25*(in-17)/3,1.25))':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2'"
        ":d=1:s=640x360:fps=30,setsar=1[p];"
        "[0:v][1:v][2:v][p][4:v]concat=n=5:v=1:a=0,format=yuv420p[v];"
        f"[5:a]adelay={int(ONSET_S * 1000)}|{int(ONSET_S * 1000)},apad=whole_dur={DURATION}[a]"
    )
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc2={size}:duration=1.0",
        "-f", "lavfi", "-i", f"smptehdbars={size}:duration=1.3",
        "-f", "lavfi", "-i", f"color=c=red:{size}:duration=0.3",
        "-f", "lavfi", "-i", f"testsrc2={size}:duration=1",
        "-f", "lavfi", "-i", f"rgbtestsrc={size}:duration=1.5",
        "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=0.05",
        "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-shortest", str(target),
    ]
    target.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, capture_output=True, timeout=120)
    return target
