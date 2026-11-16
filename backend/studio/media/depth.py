"""Parallax 2.5D: mapa de profundidade da imagem e camadas para o renderer mover em velocidades diferentes.

Profundidade: Depth Anything V2 **Small** (Apache-2.0; os modelos Base/Large são CC-BY-NC e ficam de fora) em ONNX,
rodando no `onnxruntime` que o projeto já tem pelo `kokoro-onnx`, só na CPU. O arquivo (~99 MB) vem do repositório
`onnx-community/depth-anything-v2-small` no Hugging Face, fixado num commit, e é baixado na primeira cena com
parallax para `<dados>/models/depth-anything-v2-small`, com conferência do tamanho e do SHA-256.

Camadas: a imagem vira N camadas (fundo, meio, frente) cortadas pelo mapa, com borda suave. Cada camada tem o
"buraco" das camadas da frente preenchido com as cores em volta (borrão normalizado), para que, quando a frente
anda, apareça um fundo plausível em vez de uma cópia do sujeito. Tudo em numpy/Pillow: testável com mapa sintético.

Sem o modelo (pacote ausente, sem internet, modo offline, download corrompido), quem chama cai no Ken Burns e
avisa o usuário.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from PIL import Image

from studio.config import get_settings

log = logging.getLogger(__name__)

MODEL_ID = "depth-anything-v2-small-onnx"
MODEL_REPO = "onnx-community/depth-anything-v2-small"
MODEL_REVISION = "4472b7362082ad9968fee890ca0f1e5aca36b93d"  # commit conferido em 15/11/2026
MODEL_URL = f"https://huggingface.co/{MODEL_REPO}/resolve/{MODEL_REVISION}/onnx/model.onnx"
MODEL_FILE = "model.onnx"
MODEL_SIZE = 99_060_839
MODEL_SHA256 = "afb6a5c28f3b6bf1618c6e43f02073ef9dfdc70e937502d51603e57b0a1df10c"
MODEL_MB = round(MODEL_SIZE / 1e6)

# Pré-processamento do modelo (preprocessor_config.json do repositório): lado menor em 518, múltiplo de 14,
# normalização ImageNet.
INPUT_SIDE = 518
PATCH = 14
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)

# Quantas camadas o renderer recebe (fundo + meio + frente).
LAYERS = 3
LAYERS_VERSION = 1


class DepthUnavailable(RuntimeError):
    """Sem como estimar a profundidade agora (pacote, modelo ou rede). A cena cai no Ken Burns."""


def models_dir() -> Path:
    return get_settings().data_dir / "models" / "depth-anything-v2-small"


def model_ready() -> bool:
    path = models_dir() / MODEL_FILE
    return path.is_file() and path.stat().st_size == MODEL_SIZE


def availability() -> tuple[bool, str]:
    """(dá para usar, motivo). Não baixa nada."""
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False, "pacote onnxruntime não instalado (rode scripts\\setup.ps1)"
    if model_ready():
        return True, "ok"
    if get_settings().offline:
        return False, f"o modelo de profundidade ({MODEL_MB} MB) ainda não foi baixado e o modo offline está ligado"
    return True, f"ok (o modelo de {MODEL_MB} MB será baixado na primeira cena com parallax)"


_download_lock = threading.Lock()


def ensure_model() -> Path:
    """Baixa o modelo se faltar (uma vez só, conferindo tamanho e SHA-256). Devolve o caminho do .onnx."""
    target = models_dir() / MODEL_FILE
    with _download_lock:
        if model_ready():
            return target
        if get_settings().offline:
            raise DepthUnavailable("o modelo de profundidade não foi baixado e o modo offline está ligado")
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_suffix(".onnx.part")
        log.info("baixando o modelo de profundidade (%d MB)", MODEL_MB)
        digest = hashlib.sha256()
        try:
            with httpx.stream("GET", MODEL_URL, follow_redirects=True, timeout=httpx.Timeout(60, read=120)) as resp:
                if resp.status_code != 200:
                    raise DepthUnavailable(f"o download do modelo de profundidade falhou ({resp.status_code})")
                with part.open("wb") as fh:
                    for chunk in resp.iter_bytes(1 << 20):
                        fh.write(chunk)
                        digest.update(chunk)
        except httpx.HTTPError as exc:
            part.unlink(missing_ok=True)
            raise DepthUnavailable(f"sem conexão para baixar o modelo de profundidade: {exc}") from exc
        if part.stat().st_size != MODEL_SIZE or digest.hexdigest() != MODEL_SHA256:
            part.unlink(missing_ok=True)
            raise DepthUnavailable("o modelo de profundidade baixado veio corrompido; tente de novo")
        shutil.move(str(part), target)
        return target


# ---------------------------------------------------------------------------------------------
# Estimativa
# ---------------------------------------------------------------------------------------------

def input_size(width: int, height: int) -> tuple[int, int]:
    """Tamanho de entrada (largura, altura): lado menor em 518, proporção mantida, múltiplos de 14."""
    scale = INPUT_SIDE / min(width, height)

    def snap(v: float) -> int:
        return max(PATCH, int(round(v / PATCH)) * PATCH)

    return snap(width * scale), snap(height * scale)


def preprocess(image: Image.Image) -> np.ndarray:
    w, h = input_size(*image.size)
    arr = np.asarray(image.convert("RGB").resize((w, h), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    arr = (arr - MEAN) / STD
    return arr.transpose(2, 0, 1)[None].astype(np.float32)


def normalize_depth(raw: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Saída do modelo (disparidade relativa: maior = mais perto) → 0..1 no tamanho da imagem (perto = 1).
    Corta os extremos (1% de cada lado) para um ponto estourado não achatar o resto."""
    depth = np.asarray(raw, dtype=np.float32).squeeze()
    if depth.ndim != 2:
        raise ValueError(f"mapa de profundidade com forma inesperada: {raw.shape}")
    lo, hi = np.percentile(depth, [1, 99])
    if hi - lo < 1e-6:
        depth = np.zeros_like(depth)
    else:
        depth = np.clip((depth - lo) / (hi - lo), 0.0, 1.0)
    img = Image.fromarray(depth.astype(np.float32)).resize(size, Image.Resampling.BILINEAR)
    return np.clip(np.asarray(img, dtype=np.float32), 0.0, 1.0)


class DepthEstimator:
    """Sessão ONNX carregada uma vez (o modelo é pesado); uma inferência por vez."""

    def __init__(self) -> None:
        self._session: Any = None
        self._lock = threading.Lock()

    def _load(self) -> Any:
        if self._session is None:
            try:
                import onnxruntime as ort
            except ImportError as exc:
                raise DepthUnavailable("pacote onnxruntime não instalado") from exc
            path = ensure_model()
            try:
                self._session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            except Exception as exc:  # noqa: BLE001 - arquivo ruim vira aviso, não quebra o build
                raise DepthUnavailable(f"não deu para abrir o modelo de profundidade: {exc}") from exc
        return self._session

    def __call__(self, image_path: Path) -> np.ndarray:
        with self._lock:
            session = self._load()
            with Image.open(image_path) as im:
                image = im.convert("RGB")
            name = session.get_inputs()[0].name
            raw = session.run(None, {name: preprocess(image)})[0]
        return normalize_depth(raw, image.size)


_estimator: Callable[[Path], np.ndarray] | None = None


def get_estimator() -> Callable[[Path], np.ndarray]:
    global _estimator
    if _estimator is None:
        _estimator = DepthEstimator()
    return _estimator


def set_estimator(estimator: Callable[[Path], np.ndarray] | None) -> None:
    """Troca o estimador (testes usam um mapa sintético; None volta ao modelo)."""
    global _estimator
    _estimator = estimator


def save_depth(depth: np.ndarray, out: Path) -> Path:
    """Grava o mapa como PNG de 16 bits (perto = claro)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray((np.clip(depth, 0, 1) * 65535).round().astype(np.uint16)).save(out)
    return out


def load_depth(path: Path) -> np.ndarray:
    with Image.open(path) as im:
        arr = np.asarray(im, dtype=np.float32)
    return arr / (65535.0 if arr.max() > 255 else 255.0)


# ---------------------------------------------------------------------------------------------
# Camadas
# ---------------------------------------------------------------------------------------------

def _box_blur(arr: np.ndarray, radius: int) -> np.ndarray:
    """Média numa janela (2r+1) nos dois eixos, com borda replicada (soma acumulada: O(n))."""
    if radius < 1:
        return arr
    out = arr
    for axis in (0, 1):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (radius + 1, radius)
        padded = np.pad(out, pad, mode="edge")
        csum = np.cumsum(padded, axis=axis, dtype=np.float64)
        n = out.shape[axis]
        hi = np.take(csum, np.arange(2 * radius + 1, 2 * radius + 1 + n), axis=axis)
        lo = np.take(csum, np.arange(0, n), axis=axis)
        out = ((hi - lo) / (2 * radius + 1)).astype(np.float32)
    return out


def _smooth(arr: np.ndarray, radius: int) -> np.ndarray:
    """Três passadas de média ≈ desfoque gaussiano."""
    for _ in range(3):
        arr = _box_blur(arr, radius)
    return arr


def fill_holes(rgb: np.ndarray, known: np.ndarray) -> np.ndarray:
    """Preenche onde `known` é 0 com as cores em volta (borrão normalizado em escalas crescentes). Trabalha numa
    cópia reduzida (o preenchimento é liso de qualquer jeito) e devolve no tamanho original."""
    h, w = known.shape
    if known.min() >= 0.999:
        return rgb.astype(np.float32)
    factor = max(1, min(h, w) // 270)
    sw, sh = max(1, w // factor), max(1, h // factor)
    small_rgb = np.asarray(
        Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8)).resize((sw, sh), Image.Resampling.BOX), dtype=np.float32)
    small_k = np.asarray(Image.fromarray(known.astype(np.float32)).resize((sw, sh), Image.Resampling.BOX),
                         dtype=np.float32)
    small_k = np.clip(small_k, 0, 1)
    filled = small_rgb * small_k[..., None]
    have = small_k.copy()
    radius = 2
    while have.min() < 0.999 and radius <= max(sw, sh):
        num = _smooth(small_rgb * small_k[..., None], radius)
        den = _smooth(small_k, radius)
        ok = den > 1e-3
        guess = np.where(ok[..., None], num / np.maximum(den, 1e-3)[..., None], 0)
        take = (1 - have) * ok
        filled = filled + guess * take[..., None]
        have = np.clip(have + take, 0, 1)
        radius *= 2
    if have.min() < 0.999:  # nada conhecido (imagem toda "perto"): cor média
        filled = filled + small_rgb.mean(axis=(0, 1)) * (1 - have)[..., None]
    big = np.asarray(Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8)).resize((w, h), Image.Resampling.BICUBIC),
                     dtype=np.float32)
    return rgb * known[..., None] + big * (1 - known[..., None])


def layer_thresholds(depth: np.ndarray, layers: int = LAYERS) -> list[float]:
    """Cortes entre camadas pelos quantis da profundidade (cada camada fica com uma parte da imagem)."""
    qs = [k / layers for k in range(1, layers)]
    cuts = [float(np.quantile(depth, q)) for q in qs]
    # Cortes iguais (mapa quase plano) viram cortes espaçados, senão as camadas somem.
    out: list[float] = []
    for c in cuts:
        floor = (out[-1] + 0.02) if out else 0.0
        out.append(min(0.98, max(c, floor)))
    return out


def soft_mask(depth: np.ndarray, cut: float, feather: float = 0.04) -> np.ndarray:
    """1 onde a profundidade passa do corte (mais perto), com transição suave."""
    t = np.clip((depth - (cut - feather)) / (2 * feather), 0, 1)
    return (t * t * (3 - 2 * t)).astype(np.float32)


def make_layers(image: Image.Image, depth: np.ndarray, out_dir: Path, layers: int = LAYERS) -> list[Path]:
    """Corta a imagem em camadas (0 = fundo, opaca; as outras com transparência), na ordem de desenho."""
    rgb_img = image.convert("RGB")
    w, h = rgb_img.size
    if depth.shape != (h, w):
        depth = np.asarray(Image.fromarray(depth.astype(np.float32)).resize((w, h), Image.Resampling.BILINEAR),
                           dtype=np.float32)
    rgb = np.asarray(rgb_img, dtype=np.float32)
    cuts = layer_thresholds(depth, layers)
    feather_px = max(1, min(w, h) // 360)
    masks = [np.ones_like(depth)] + [_smooth(soft_mask(depth, c), feather_px) for c in cuts]
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for k in range(layers):
        # O que está na frente desta camada vira "buraco" a preencher (com folga: a borda suave não deixa halo).
        known = 1 - np.clip(masks[k + 1] * 4, 0, 1) if k + 1 < layers else np.ones_like(depth)
        color = fill_holes(rgb, known)
        if k == 0:
            path = out_dir / "layer0.jpg"
            Image.fromarray(np.clip(color, 0, 255).astype(np.uint8)).save(path, quality=92)
        else:
            alpha = np.clip(masks[k] * 255, 0, 255).astype(np.uint8)
            rgba = np.dstack([np.clip(color, 0, 255).astype(np.uint8), alpha])
            path = out_dir / f"layer{k}.webp"
            Image.fromarray(rgba).save(path, quality=90, method=4)
        paths.append(path)
    return paths
