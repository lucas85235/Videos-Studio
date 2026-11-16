"""LUTs de cor no formato .cube (Adobe/Resolve): leitura, LUTs prontos gerados no código e aplicação na imagem.

A gradação por LUT é feita aqui, com numpy, uma vez por imagem: o resultado vai para o cache de artefatos (chave =
hash da imagem + LUT + intensidade) e a timeline aponta para a imagem já gradada. Assim o render por blocos do
Remotion, a prévia no navegador e a renderização final mostram exatamente a mesma cor, sem custo por quadro.

Formato .cube (Cube LUT Specification 1.0): linhas `TITLE "..."`, `LUT_3D_SIZE N` (2–256) ou `LUT_1D_SIZE N`
(2–65536), `DOMAIN_MIN r g b` / `DOMAIN_MAX r g b` (padrão 0 0 0 / 1 1 1), comentários com `#` e depois as linhas de
dados `R G B`; no 3D o vermelho varia mais rápido, depois o verde, depois o azul.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from studio.config import get_settings
from studio.core.styles.schema import LUT_PRESETS

# Mudou a conta da aplicação ou dos LUTs prontos: sobe a versão e as imagens gradadas são refeitas.
LUT_VERSION = 1
BUILTIN_SIZE = 33
MAX_FILE_BYTES = 12 * 1024 * 1024
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")
_ROWS_PER_BLOCK = 256  # aplica em faixas de linhas: memória limitada mesmo em imagens grandes
_FILE_NAME = re.compile(r"^[\w\- .()]{1,80}\.cube$", re.IGNORECASE)


class LutError(ValueError):
    pass


@dataclass(frozen=True)
class Lut:
    size: int
    dim: int  # 1 ou 3
    data: np.ndarray  # 3D: (N³, 3) na ordem do arquivo (vermelho mais rápido); 1D: (N, 3)
    domain_min: tuple[float, float, float] = (0.0, 0.0, 0.0)
    domain_max: tuple[float, float, float] = (1.0, 1.0, 1.0)
    title: str = ""

    @property
    def digest(self) -> str:
        h = hashlib.sha256()
        h.update(f"{self.dim}|{self.size}|{self.domain_min}|{self.domain_max}".encode())
        h.update(np.ascontiguousarray(self.data, dtype=np.float32).tobytes())
        return h.hexdigest()


# --------------------------------------------------------------------------------------------
# Leitura e escrita
# --------------------------------------------------------------------------------------------


def _floats(parts: list[str], line_no: int, count: int = 3) -> tuple[float, ...]:
    if len(parts) != count:
        raise LutError(f"linha {line_no}: esperava {count} números")
    try:
        values = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise LutError(f"linha {line_no}: número inválido") from exc
    if not all(np.isfinite(values)):
        raise LutError(f"linha {line_no}: número inválido")
    return values


def parse_cube(text: str) -> Lut:
    """Lê um .cube 3D ou 1D. Erros viram LutError com a linha do problema."""
    title = ""
    size = 0
    dim = 0
    dmin: tuple[float, ...] = (0.0, 0.0, 0.0)
    dmax: tuple[float, ...] = (1.0, 1.0, 1.0)
    rows: list[tuple[float, ...]] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        key = parts[0].upper()
        if key == "TITLE":
            title = line[5:].strip().strip('"')[:120]
        elif key in ("LUT_3D_SIZE", "LUT_1D_SIZE"):
            if rows:
                raise LutError(f"linha {line_no}: {key} precisa vir antes dos dados")
            if dim:
                raise LutError(f"linha {line_no}: o arquivo declara o tamanho duas vezes")
            try:
                size = int(parts[1])
            except (IndexError, ValueError) as exc:
                raise LutError(f"linha {line_no}: tamanho inválido") from exc
            dim = 3 if key == "LUT_3D_SIZE" else 1
            limit = 256 if dim == 3 else 65536
            if not 2 <= size <= limit:
                raise LutError(f"linha {line_no}: tamanho {size} fora de 2–{limit}")
        elif key in ("LUT_3D_INPUT_RANGE", "LUT_1D_INPUT_RANGE"):
            # Variante do Resolve: "LUT_3D_INPUT_RANGE min max" vale para os três canais.
            lo, hi = _floats(parts[1:], line_no, 2)
            dmin, dmax = (lo, lo, lo), (hi, hi, hi)
        elif key == "DOMAIN_MIN":
            dmin = _floats(parts[1:], line_no)
        elif key == "DOMAIN_MAX":
            dmax = _floats(parts[1:], line_no)
        elif key[0].isalpha():
            continue  # palavra-chave desconhecida (ex.: LUT_IN_VIDEO_RANGE): ignora, como os editores fazem
        else:
            rows.append(_floats(parts, line_no))
    if not dim:
        raise LutError("o arquivo não tem LUT_3D_SIZE nem LUT_1D_SIZE (não parece um .cube)")
    expected = size**3 if dim == 3 else size
    if len(rows) != expected:
        raise LutError(f"esperava {expected} linhas de cor para o tamanho {size}, achou {len(rows)}")
    if any(hi <= lo for lo, hi in zip(dmin, dmax, strict=True)):
        raise LutError("DOMAIN_MAX precisa ser maior que DOMAIN_MIN em cada canal")
    data = np.asarray(rows, dtype=np.float32)
    return Lut(size=size, dim=dim, data=data, domain_min=tuple(dmin), domain_max=tuple(dmax), title=title)  # type: ignore[arg-type]


def write_cube(lut: Lut) -> str:
    """Texto .cube do LUT (para baixar um LUT pronto e usar em outro editor)."""
    lines = [f'TITLE "{lut.title}"'] if lut.title else []
    lines.append(f"LUT_{lut.dim}D_SIZE {lut.size}")
    if lut.domain_min != (0.0, 0.0, 0.0) or lut.domain_max != (1.0, 1.0, 1.0):
        lines.append("DOMAIN_MIN " + " ".join(f"{v:g}" for v in lut.domain_min))
        lines.append("DOMAIN_MAX " + " ".join(f"{v:g}" for v in lut.domain_max))
    lines.extend(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in lut.data.tolist())
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------------------------
# Aplicação
# --------------------------------------------------------------------------------------------


def apply_lut(pixels: np.ndarray, lut: Lut) -> np.ndarray:
    """Aplica o LUT em cores RGB 0..1 (qualquer forma terminada em 3). 3D com interpolação trilinear."""
    shape = pixels.shape
    flat = pixels.reshape(-1, 3).astype(np.float32, copy=False)
    lo = np.asarray(lut.domain_min, dtype=np.float32)
    hi = np.asarray(lut.domain_max, dtype=np.float32)
    n = lut.size
    if lut.dim == 1:
        out = np.empty_like(flat)
        grid = np.linspace(0.0, 1.0, n, dtype=np.float32)
        norm = np.clip((flat - lo) / (hi - lo), 0.0, 1.0)
        for c in range(3):
            out[:, c] = np.interp(norm[:, c], grid, lut.data[:, c])
        return out.reshape(shape)
    pos = np.clip((flat - lo) / (hi - lo), 0.0, 1.0) * (n - 1)
    i0 = np.minimum(pos.astype(np.int32), n - 2)
    f = pos - i0
    r0, g0, b0 = i0[:, 0], i0[:, 1], i0[:, 2]
    fr, fg, fb = f[:, 0:1], f[:, 1:2], f[:, 2:3]
    table = lut.data

    def at(dr: int, dg: int, db: int) -> np.ndarray:
        return table[(r0 + dr) + n * (g0 + dg) + n * n * (b0 + db)]

    c00 = at(0, 0, 0) * (1 - fr) + at(1, 0, 0) * fr
    c10 = at(0, 1, 0) * (1 - fr) + at(1, 1, 0) * fr
    c01 = at(0, 0, 1) * (1 - fr) + at(1, 0, 1) * fr
    c11 = at(0, 1, 1) * (1 - fr) + at(1, 1, 1) * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    return (c0 * (1 - fb) + c1 * fb).reshape(shape)


def apply_to_array(rgb: np.ndarray, lut: Lut, amount: float = 1.0) -> np.ndarray:
    """Imagem uint8 (H, W, 3) gradada pelo LUT, misturada com a original pela intensidade (0..1)."""
    amount = float(np.clip(amount, 0.0, 1.0))
    out = np.empty_like(rgb)
    for top in range(0, rgb.shape[0], _ROWS_PER_BLOCK):
        block = rgb[top : top + _ROWS_PER_BLOCK].astype(np.float32) / 255.0
        graded = apply_lut(block, lut)
        mixed = block + (graded - block) * amount
        out[top : top + _ROWS_PER_BLOCK] = np.clip(mixed * 255.0 + 0.5, 0, 255).astype(np.uint8)
    return out


def grade_image(src: Path, dst_dir: Path, lut: Lut, amount: float) -> Path:
    """Grava a imagem gradada em `dst_dir` (JPEG; PNG quando tem transparência) e devolve o caminho."""
    from PIL import Image, ImageOps

    with Image.open(src) as opened:
        # O navegador gira pela orientação EXIF; o arquivo novo sai sem EXIF, então gira antes.
        img = ImageOps.exif_transpose(opened)
        alpha = img.getchannel("A") if img.mode in ("RGBA", "LA") or "transparency" in img.info else None
        rgb = np.asarray(img.convert("RGB"))
    out = Image.fromarray(apply_to_array(rgb, lut, amount), "RGB")
    dst_dir.mkdir(parents=True, exist_ok=True)
    if alpha is not None and alpha.getextrema()[0] < 255:
        out.putalpha(alpha)
        target = dst_dir / "image.png"
        out.save(target, optimize=True)
    else:
        target = dst_dir / "image.jpg"
        out.save(target, quality=93, subsampling=0)
    return target


# --------------------------------------------------------------------------------------------
# LUTs prontos (gerados aqui; nada é baixado, sem licença de terceiros)
# --------------------------------------------------------------------------------------------

_LUMA = np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)


def _luma(c: np.ndarray) -> np.ndarray:
    return (c @ _LUMA)[..., None]


def _s_curve(x: np.ndarray, k: float) -> np.ndarray:
    """Curva em S (contraste) que mantém 0, 0,5 e 1 no lugar; k entre 0 e 1."""
    smooth = x * x * (3 - 2 * x)
    return x + k * (smooth - x)


def _saturate(c: np.ndarray, s: float) -> np.ndarray:
    y = _luma(c)
    return y + (c - y) * s


def _teal_orange(c: np.ndarray) -> np.ndarray:
    c = _s_curve(c, 0.35)
    y = _luma(c)
    shadows = (1 - y) ** 2
    highs = y**2
    c = c + shadows * np.array([-0.07, 0.03, 0.07]) + highs * np.array([0.08, 0.02, -0.07])
    return _saturate(c, 1.12)


def _faded_film(c: np.ndarray) -> np.ndarray:
    c = _saturate(c, 0.78)
    c = 0.07 + 0.86 * _s_curve(c, 0.2)  # pretos levantados, brancos rebaixados
    y = _luma(c)
    return c + y * np.array([0.025, 0.01, -0.025])


def _cold_night(c: np.ndarray) -> np.ndarray:
    c = _saturate(c, 0.62)
    c = np.power(np.clip(c, 0, 1), 1.18)
    return c * np.array([0.88, 0.97, 1.1]) + np.array([0.0, 0.01, 0.03])


def _warm_amber(c: np.ndarray) -> np.ndarray:
    c = _s_curve(c, 0.2)
    c = c * np.array([1.08, 1.0, 0.86]) + np.array([0.02, 0.008, 0.0])
    return _saturate(c, 1.08)


def _cross_process(c: np.ndarray) -> np.ndarray:
    r = _s_curve(c[..., 0], 0.7)
    g = _s_curve(c[..., 1], 0.35) * 1.03
    b = 0.16 + 0.66 * c[..., 2]
    return np.stack([r, g, b], axis=-1)


def _mono_contrast(c: np.ndarray) -> np.ndarray:
    y = _s_curve(_s_curve(_luma(c), 0.8), 0.4)
    return y * np.array([0.97, 0.99, 1.03])


_BUILTINS: dict[str, tuple[str, Callable[[np.ndarray], np.ndarray]]] = {
    "teal-orange": ("Azul-petróleo e laranja", _teal_orange),
    "faded-film": ("Filme desbotado", _faded_film),
    "cold-night": ("Noite fria", _cold_night),
    "warm-amber": ("Âmbar quente", _warm_amber),
    "cross-process": ("Processo cruzado", _cross_process),
    "mono-contrast": ("Preto e branco contrastado", _mono_contrast),
}
assert tuple(_BUILTINS) == LUT_PRESETS, "LUT_PRESETS (schema) e os LUTs prontos precisam bater"

BUILTIN_LABELS = {key: label for key, (label, _) in _BUILTINS.items()}


def identity_grid(size: int) -> np.ndarray:
    """Cores de entrada de um LUT 3D (N³, 3) na ordem do .cube (vermelho mais rápido)."""
    axis = np.linspace(0.0, 1.0, size, dtype=np.float32)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r.ravel(), g.ravel(), b.ravel()], axis=-1)


@lru_cache(maxsize=16)
def builtin_lut(name: str, size: int = BUILTIN_SIZE) -> Lut:
    if name not in _BUILTINS:
        raise LutError(f"LUT pronto desconhecido: {name}")
    label, fn = _BUILTINS[name]
    data = np.clip(fn(identity_grid(size)), 0.0, 1.0).astype(np.float32)
    data.setflags(write=False)
    return Lut(size=size, dim=3, data=data, title=f"Vulto {label}")


# --------------------------------------------------------------------------------------------
# LUTs do usuário (biblioteca)
# --------------------------------------------------------------------------------------------


def luts_dir() -> Path:
    path = get_settings().library_dir / "luts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def safe_name(name: str) -> str:
    """Nome de arquivo .cube aceito (sem pastas); LutError se não servir."""
    name = Path(name.replace("\\", "/")).name.strip()
    if not name.lower().endswith(".cube"):
        name = f"{name}.cube"
    if not _FILE_NAME.match(name):
        raise LutError("nome de arquivo inválido: use letras, números, espaço, hífen ou sublinhado e termine em .cube")
    return name


def user_lut_path(name: str) -> Path:
    return luts_dir() / safe_name(name)


def _read_user(path: Path) -> Lut:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise LutError("arquivo .cube grande demais (máx. 12 MB)")
    return parse_cube(path.read_text(encoding="utf-8-sig", errors="replace"))


@lru_cache(maxsize=32)
def _read_user_cached(path: str, mtime: float, size: int) -> Lut:
    return _read_user(Path(path))


def load_lut(preset: str) -> Lut:
    """LUT do efeito: nome de um LUT pronto ou de um .cube da biblioteca (library/luts)."""
    if preset in _BUILTINS:
        return builtin_lut(preset)
    path = user_lut_path(preset)
    if not path.is_file():
        raise LutError(f"o LUT {preset} não está na biblioteca ({luts_dir()})")
    stat = path.stat()
    return _read_user_cached(str(path), stat.st_mtime, stat.st_size)


def save_user_lut(name: str, data: bytes) -> tuple[str, Lut]:
    """Valida e grava um .cube enviado. Devolve o nome final e o LUT lido."""
    if len(data) > MAX_FILE_BYTES:
        raise LutError("arquivo .cube grande demais (máx. 12 MB)")
    final = safe_name(name)
    lut = parse_cube(data.decode("utf-8-sig", errors="replace"))
    (luts_dir() / final).write_bytes(data)
    return final, lut


def list_user_luts() -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for path in sorted(luts_dir().glob("*.cube"), key=lambda p: p.name.lower()):
        item: dict[str, object] = {"name": path.name, "bytes": path.stat().st_size}
        try:
            lut = _read_user(path)
            item.update({"title": lut.title, "size": lut.size, "dim": lut.dim})
        except (LutError, OSError) as exc:
            item["error"] = str(exc)
        items.append(item)
    return items


def swatch(lut: Lut, width: int = 256, height: int = 96) -> bytes:
    """PNG de amostra: em cima as cores originais, embaixo as mesmas cores com o LUT (para o editor de estilos)."""
    import io

    from PIL import Image

    half = height // 2
    hue = np.linspace(0, 1, width, dtype=np.float32)[None, :, None]
    light = np.linspace(0.92, 0.08, half, dtype=np.float32)[:, None, None]
    # Arco-íris (hue → RGB) escurecendo de cima para baixo, com uma faixa de cinzas no fim.
    rgb = np.clip(np.abs(((hue * 6 + np.array([0, 4, 2])) % 6) - 3) - 1, 0, 1)
    rgb = light * (0.35 + 0.65 * rgb)
    rgb[-max(1, half // 6) :] = np.linspace(0, 1, width, dtype=np.float32)[None, :, None]
    top = (rgb * 255 + 0.5).astype(np.uint8)
    bottom = apply_to_array(top, lut, 1.0)
    img = Image.fromarray(np.concatenate([top, bottom], axis=0), "RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
