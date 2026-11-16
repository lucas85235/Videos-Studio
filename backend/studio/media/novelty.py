"""Impressões digitais para o verificador de novidade: hash perceptual de imagens e quadros, e títulos parecidos.

O hash é o dHash de 64 bits (diferença de brilho entre vizinhos numa miniatura 9x8 em tons de cinza): imagens
quase iguais (recorte leve, outra compressão, outro tamanho, cor um pouco diferente) dão hashes a poucos bits de
distância. Imagem quase lisa (tela preta, fundo de cartão) não tem impressão digital útil e fica de fora.
"""

from __future__ import annotations

import re
import tempfile
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

IMAGE_EXTS = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"})
VIDEO_EXTS = frozenset({".mp4", ".mov", ".webm", ".mkv", ".m4v"})

# Até quantos bits (de 64) de diferença duas imagens contam como "a mesma imagem".
SIMILAR_BITS = 10
# Desvio mínimo do brilho na miniatura: abaixo disso a imagem é lisa demais para comparar.
MIN_CONTRAST = 4.0


def dhash(image: Image.Image) -> str | None:
    """dHash de 64 bits em hexadecimal (16 caracteres); None para imagem lisa demais."""
    gray = ImageOps.exif_transpose(image).convert("L").resize((9, 8), Image.Resampling.LANCZOS)
    pixels = np.asarray(gray, dtype=np.float32)
    if float(pixels.std()) < MIN_CONTRAST:
        return None
    bits = (pixels[:, 1:] > pixels[:, :-1]).flatten()
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hash_file(path: Path) -> str | None:
    """Hash de uma imagem ou de um quadro de vídeo (o do meio do primeiro segundo e pouco). None se não der."""
    suffix = path.suffix.lower()
    try:
        if suffix in IMAGE_EXTS:
            with Image.open(path) as img:
                img.seek(0)
                return dhash(img)
        if suffix in VIDEO_EXTS:
            return _video_frame_hash(path)
    except (OSError, ValueError):
        return None
    return None


def _video_frame_hash(path: Path) -> str | None:
    from studio.media.ffmpeg import FFmpegError, run_ffmpeg

    with tempfile.TemporaryDirectory(prefix="vulto-novidade-") as tmp:
        frame = Path(tmp) / "quadro.png"
        try:
            run_ffmpeg(["-ss", "1", "-i", str(path), "-frames:v", "1", "-vf", "scale=160:-2", str(frame)], timeout=60)
            if not frame.is_file():  # vídeo com menos de 1 s: o primeiro quadro
                run_ffmpeg(["-i", str(path), "-frames:v", "1", "-vf", "scale=160:-2", str(frame)], timeout=60)
        except (FFmpegError, OSError, TimeoutError):
            return None
        if not frame.is_file():
            return None
        with Image.open(frame) as img:
            return dhash(img)


def distance(a: str, b: str) -> int:
    """Bits diferentes entre dois hashes."""
    return (int(a, 16) ^ int(b, 16)).bit_count()


def similar(a: str | None, b: str | None, bits: int = SIMILAR_BITS) -> bool:
    return bool(a and b) and distance(a, b) <= bits  # type: ignore[arg-type]


# --------------------------------------------------------------------------------------------
# Títulos
# --------------------------------------------------------------------------------------------

# Palavras que não dizem nada sobre o assunto (pt, en, es): ficam fora da comparação por palavras.
_STOP = frozenset(
    "a o as os um uma uns umas de do da dos das e em no na nos nas por para com sem que se seu sua ao aos "
    "the of and in on for to with an is are was it this that what why how who "
    "el la los las un una del y en por para con que es".split()
)


def normalize_title(text: str) -> str:
    """Minúsculas, sem acentos, sem pontuação e sem emojis."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", text)).strip()


def _words(text: str) -> set[str]:
    return {w for w in normalize_title(text).split() if w not in _STOP and len(w) > 1}


def title_similarity(a: str, b: str) -> float:
    """0 a 1: o maior entre a semelhança das letras e a das palavras de conteúdo (ordem não importa)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    chars = SequenceMatcher(None, na, nb).ratio()
    wa, wb = _words(a), _words(b)
    words = len(wa & wb) / len(wa | wb) if wa and wb else 0.0
    return max(chars, words)
