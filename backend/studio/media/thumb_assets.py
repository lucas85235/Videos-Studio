"""Biblioteca de imagens das thumbs por canal (thumbkit) e o resolvedor de referências do compositor.

A biblioteca fica em `<data>/library/thumbkit/<channel_id>/`, com o manifesto `thumbkit.json`:

    {"version": 1, "channel_id": "...",
     "videos": {"GTAVI_Trailer_2": "C:/.../GTAVI_Trailer_2.mp4"},     # fontes de `frame:<video>@<t>`
     "items": [{"id", "kind": "frame"|"cutout"|"logo"|"object", "file" (relativo à pasta do kit), "tags": [],
                "credit", "editorial": bool, "focus_x", "focus_y", "face_box": [x0, y0, x1, y1] | null,
                "zoom": float | null, "source": {...}}]}

Referências aceitas pelo `AssetResolver.resolve` (as camadas do `ThumbLayers` guardam só essas strings):

- `frame:<video>@<t>`: quadro do vídeo `<video>` (nome em `videos`) no segundo `t`, extraído uma vez e guardado em
  `frames/` (cache). Os trailers são 4K e pesam até 1,3 GB: a busca é rápida (`-ss` antes do `-i`) e nunca roda
  durante o render se o quadro já estiver no cache;
- `frame:<id>`, `cutout:<id>`, `logo:<id>`, `object:<id>` ou só `<id>`: item do manifesto;
- `pose:<id>`: pose do avatar do apresentador (PNG original com alfa; a thumb não usa o `render_file` de 900 px);
- `media:<id>`: mídia do projeto, por uma função passada ao resolvedor (a integração com o projeto é da Fase 8);
- `upload:<caminho>`: arquivo enviado pelo usuário (absoluto ou relativo ao diretório de dados).

Recorte (Fase 2b, opcional): com o `rembg` instalado (`pip install videos-studio[cutout]`), `make_cutouts` gera os
recortes de personagem da semente (`cutouts`) e o resolvedor recorta na hora uma figura sem alfa (`cutout_for`, com
cache pelo conteúdo em `cutouts/_auto/`). Sem o rembg tudo segue no modo sem recorte.

Material oficial (quadros de trailer, capa, logos) tem `editorial: true`. Com `allow_editorial=False` o resolvedor
recusa esses itens e o compositor usa o fallback (degradê, sem logo), registrando um aviso.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageFilter
from PIL.PngImagePlugin import PngInfo

from studio.config import REPO_ROOT, get_settings

log = logging.getLogger(__name__)

MANIFEST = "thumbkit.json"
MANIFEST_VERSION = 1
# Fora de `media/data/` (a especificação dizia lá): o `.gitignore` ignora toda pasta `data/`.
SEED_FILE = Path(__file__).resolve().parent / "thumbkit-seed.json"
ITEM_KINDS = ("frame", "cutout", "logo", "object")
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_\-.]{0,79}$")
FRAME_REF_RE = re.compile(r"^(?P<video>[A-Za-z0-9_\-.]+)@(?P<t>\d+(?:\.\d+)?)$")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")

try:  # recorte automático (Decisão 3: fase opcional; o compositor funciona sem ele)
    from rembg import remove as _rembg_remove  # type: ignore[import-not-found]

    HAS_REMBG = True
except Exception:  # noqa: BLE001 - rembg ausente ou quebrado: só desliga o recorte automático
    _rembg_remove = None
    HAS_REMBG = False


class AssetError(LookupError):
    """Referência de imagem que não resolve (item inexistente, editorial bloqueado, quadro sem fonte)."""


@dataclass
class Asset:
    ref: str
    path: Path
    kind: str = "frame"
    editorial: bool = False
    meta: dict[str, Any] = field(default_factory=dict)
    """Item do manifesto (focus_x, focus_y, face_box, zoom, tags...) ou {} para uploads e poses."""


# --- Pastas e manifesto ----------------------------------------------------------------------------------------------
def thumbkit_dir(channel_id: str) -> Path:
    if not re.match(r"^[A-Za-z0-9_\-]+$", channel_id or ""):
        raise ValueError(f"id de canal inválido: {channel_id!r}")
    return get_settings().library_dir / "thumbkit" / channel_id


def empty_manifest(channel_id: str) -> dict[str, Any]:
    return {"version": MANIFEST_VERSION, "channel_id": channel_id, "videos": {}, "items": []}


def load_manifest(kit_dir: Path, channel_id: str = "") -> dict[str, Any]:
    path = kit_dir / MANIFEST
    if not path.is_file():
        return empty_manifest(channel_id or kit_dir.name)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log.warning("manifesto do thumbkit ilegível: %s", path)
        return empty_manifest(channel_id or kit_dir.name)
    data.setdefault("videos", {})
    data.setdefault("items", [])
    return data


def save_manifest(kit_dir: Path, manifest: dict[str, Any]) -> Path:
    kit_dir.mkdir(parents=True, exist_ok=True)
    path = kit_dir / MANIFEST
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return path


def upsert_item(manifest: dict[str, Any], item: dict[str, Any]) -> dict[str, Any]:
    """Grava/substitui o item pelo `id`, com os campos padrão preenchidos."""
    if not ID_RE.match(str(item.get("id", ""))):
        raise ValueError(f"id de item inválido: {item.get('id')!r}")
    if item.get("kind") not in ITEM_KINDS:
        raise ValueError(f"tipo de item inválido: {item.get('kind')!r}")
    clean = {"tags": [], "credit": "", "editorial": False, "focus_x": 0.5, "focus_y": 0.42, "face_box": None,
             "zoom": None, **item}
    items = [i for i in manifest["items"] if i.get("id") != clean["id"]]
    items.append(clean)
    manifest["items"] = items
    return clean


# --- Imagens ---------------------------------------------------------------------------------------------------------
@lru_cache(maxsize=4)  # um quadro 4K em RGBA ocupa ~33 MB: no máximo ~130 MB presos no processo
def _open_cached(path: str, mtime: float, size: int) -> Image.Image:
    with Image.open(path) as im:
        im.load()
        return im.convert("RGBA")


def open_rgba(path: Path) -> Image.Image:
    """Abre como RGBA (com cache por caminho+mtime). Devolve uma cópia: quem chama pode alterar à vontade."""
    stat = path.stat()
    return _open_cached(str(path), stat.st_mtime, stat.st_size).copy()


def has_alpha(path: Path) -> bool:
    """PNG com alfa de verdade (algum pixel transparente)."""
    with Image.open(path) as im:
        if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
            return False
        alpha = im.convert("RGBA").getchannel("A")
        return alpha.getextrema()[0] < 255


def extract_frame(video: Path, t: float, out: Path, *, timeout: float = 120) -> Path:
    """Um quadro do vídeo no segundo `t` (busca rápida: `-ss` antes do `-i`). Usa o cache se já existir."""
    if out.is_file() and out.stat().st_size > 0:
        return out
    if not video.is_file():
        raise AssetError(f"vídeo não encontrado: {video}")
    from studio.media.ffmpeg import run_ffmpeg

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.tmp{out.suffix}")
    run_ffmpeg(["-ss", f"{max(0.0, float(t)):.3f}", "-i", str(video), "-frames:v", "1", "-q:v", "2", str(tmp)],
               timeout=timeout)
    if not tmp.is_file():
        raise AssetError(f"o ffmpeg não gerou o quadro {video.name}@{t}")
    tmp.replace(out)
    return out


def frame_file_name(video: str, t: float) -> str:
    return f"{video}@{float(t):.2f}.jpg"


# --- Recorte (rembg, opcional) ---------------------------------------------------------------------------------------
CUTOUT_MODEL = "isnet-general-use"
"""Modelo padrão do rembg: o melhor nas artes da capa (cabelo da Lucia). `u2net_human_seg` separa melhor gente de
carro/porta nos quadros de trailer; o item da semente escolhe pelo campo `model`."""
CUTOUT_MODELS = ("isnet-general-use", "u2net_human_seg", "birefnet-general", "birefnet-portrait", "isnet-anime")
"""Modelos aceitos no campo `model` da semente (o rembg baixa cada um na 1ª vez, em ~/.rembg/models)."""
CUTOUT_VERSION = 2
"""Versão do algoritmo de recorte (entra na chave do cache e no `source.v` do item): mudar a máscara, a limpeza do
alfa ou a descontaminação refaz os recortes automáticos e os da semente na próxima importação.
v2: lados cortados (`cut_edges`), buraco tapado também quando encosta no lado cortado, cor da borda puxada do
interior (sem halo do fundo) e borda do alfa suavizada."""
CUTOUT_MAX_SIDE = 2400
CUT_SIDES = ("top", "bottom", "left", "right")
CUT_TEXT_KEY = "vs:cut_edges"
"""Chave do texto no PNG do recorte com os lados cortados (o compositor lê sem precisar do manifesto)."""
EDGE_BAND_TRIMMED = 4
"""Faixa (px) da medida de plano B no PNG já aparado/pose: a borda suavizada não chega a alfa 128 na última linha."""
SRC_BOX_KEY = "vs:src_box"
"""Caixa do recorte aparado no original (fração x0,y0,x1,y1): converte o `face_src` da semente em `face_box`."""
_SESSIONS: dict[str, Any] = {}


def rembg_available() -> bool:
    return HAS_REMBG and _rembg_remove is not None


def _rembg_session(model: str) -> Any:
    if model not in _SESSIONS:
        from rembg import new_session  # type: ignore[import-not-found]

        _SESSIONS[model] = new_session(model)
    return _SESSIONS[model]


def _edge_strip(a: Image.Image, side: str, band: int) -> Image.Image:
    """A linha/coluna da borda `side` com o máximo da faixa de `band` px junto dela (a máscara suave costuma chegar
    à borda com alfa caindo nos últimos 2 a 4 px: a figura cortada ali ainda é cortada)."""
    w, h = a.size
    band = max(1, min(band, w if side in ("left", "right") else h))
    out: Image.Image | None = None
    for k in range(band):
        box = {"top": (0, k, w, k + 1), "bottom": (0, h - 1 - k, w, h - k),
               "left": (k, 0, k + 1, h), "right": (w - 1 - k, 0, w - k, h)}[side]
        line = a.crop(box)
        out = line if out is None else ImageChops.lighter(out, line)
    assert out is not None
    return out


def detect_cut_edges(alpha: Image.Image, *, trimmed: bool | None = None, min_px: int = 4, min_frac: float = 0.004,
                     trimmed_frac: float = 0.12, band: int = 1) -> list[str]:
    """Lados em que a figura foi cortada pela borda do quadro (não é contorno natural): pixel opaco (alfa ≥ 128)
    encostado na borda da imagem.

    Imagem com margem transparente (pose do avatar, máscara antes de aparar): basta um trecho curto (≥ `min_px` e
    ≥ `min_frac` do lado: o cotovelo que encosta na lateral do PNG). Imagem já aparada (`trimmed`; None: descobre
    pelo alfa tocar os 4 lados) tem contorno natural tocando cada lado, então só conta trecho longo
    (≥ `trimmed_frac`). O recorte da semente grava os lados medidos antes de aparar (`cut_edges` no manifesto e no
    PNG); esta medida na imagem aparada é só o plano B de arquivo antigo. `band` > 1 mede o máximo de uma faixa
    de `band` px junto de cada borda (alfa suavizado que não chega a 128 bem na última linha)."""
    a = alpha if alpha.mode == "L" else alpha.convert("L")
    w, h = a.size
    box = a.point(lambda v: 255 if v > 8 else 0).getbbox()
    if not box:
        return []
    if trimmed is None:
        trimmed = box == (0, 0, w, h)
    out = []
    for side in CUT_SIDES:
        n = w if side in ("top", "bottom") else h
        hits = _edge_strip(a, side, band).point(lambda v: 255 if v >= 128 else 0).histogram()[255]
        if hits >= max(min_px, n * (trimmed_frac if trimmed else min_frac)):
            out.append(side)
    return out


def _norm_edges(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [s.strip() for s in value.split(",")]
    return [s for s in CUT_SIDES if s in set(value or [])]


@lru_cache(maxsize=64)
def _cut_edges_cached(path: str, mtime: float, size: int) -> tuple[str, ...]:
    with Image.open(path) as im:
        stored = _norm_edges(im.info.get(CUT_TEXT_KEY)) if hasattr(im, "info") else None
        if stored is not None:
            return tuple(stored)
        if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
            return ()
        alpha = im.convert("RGBA").getchannel("A")
        if alpha.getextrema()[0] >= 250:
            return ()  # sem transparência nenhuma: é foto inteira, não figura recortada (não há lado "cortado")
        return tuple(detect_cut_edges(alpha, band=EDGE_BAND_TRIMMED))


def cut_edges_of(path: Path, meta: dict[str, Any] | None = None) -> list[str]:
    """Lados cortados da figura em `path`: o `cut_edges` do item do manifesto; senão o gravado no PNG pelo recorte;
    senão medido no alfa (pose do avatar com margem; arquivo aparado só com corte longo)."""
    stored = _norm_edges((meta or {}).get("cut_edges"))
    if stored is not None:
        return stored
    try:
        stat = path.stat()
        return list(_cut_edges_cached(str(path), stat.st_mtime, stat.st_size))
    except OSError:
        return []


STRAIGHT_TEXT_KEY = "vs:straight"
"""Chave do texto no PNG do recorte com os trechos retos de dentro da imagem (`straight_cuts`, em JSON)."""
STRAIGHT_MIN_FRAC = 0.05
"""Trecho reto mínimo, em fração do lado: contorno de gente não fica reto (±1 px) por 5% da altura."""
STRAIGHT_SLOPE_MAX = 0.055
"""Inclinação máxima de um trecho "reto" (~3°): a porta do carro ou o crop levemente torto também contam."""


def _first_opaque(grid: Any) -> Any:
    """Para cada linha de `grid` (lado já virado para a esquerda), o índice da 1ª coluna opaca (alfa ≥ 128); -1 sem
    figura na linha."""
    import numpy as np

    solid = grid >= 128
    return np.where(solid.any(axis=1), solid.argmax(axis=1), -1)


def _line_fit(p: Any, a: int, b: int, tol: float, slope_max: float) -> bool:
    """`p[a..b]` cabe numa reta (mínimos quadrados) com inclinação ≤ `slope_max` e desvio ≤ `tol` px."""
    import numpy as np

    y = p[a:b + 1].astype(np.float64)
    if len(y) < 3:
        return True
    x = np.arange(len(y), dtype=np.float64)
    slope, icpt = np.polyfit(x, y, 1)
    return abs(slope) <= slope_max and float(np.abs(y - (slope * x + icpt)).max()) <= tol


def _linear_runs(p: Any, ok: Any, need: int, *, tol: float = 1.25,
                 slope_max: float = STRAIGHT_SLOPE_MAX) -> list[tuple[int, int]]:
    """Trechos [a, b] (inclusivos, com pelo menos `need` linhas) em que o perfil `p` anda em linha reta: passo de no
    máximo 1 px entre linhas vizinhas, só onde `ok`, e cabendo numa reta (`_line_fit`). Guloso: cada trecho cresce
    o quanto der (busca exponencial + binária) e o próximo começa depois dele."""
    n = len(p)
    out: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if not ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and ok[j + 1] and abs(int(p[j + 1]) - int(p[j])) <= 1:
            j += 1
        a = i
        while j - a + 1 >= need:
            lo = a + need - 1
            if not _line_fit(p, a, lo, tol, slope_max):
                a += max(1, need // 4)
                continue
            hi, step = lo, need
            while hi < j and _line_fit(p, a, min(j, hi + step), tol, slope_max):
                hi, step = min(j, hi + step), step * 2
            left, right = hi, min(j, hi + step)
            while left < right:
                mid = (left + right + 1) // 2
                if _line_fit(p, a, mid, tol, slope_max):
                    left = mid
                else:
                    right = mid - 1
            out.append((a, left))
            a = left + 1
        i = j + 1
    return out


def _bent(p: Any, limit: float = 0.5) -> bool:
    """O perfil `p` faz curva: a parábola que melhor cabe nele se afasta da reta mais que `limit` px no trecho."""
    import numpy as np

    if len(p) < 5:
        return False
    x = np.arange(len(p), dtype=np.float64)
    return abs(float(np.polyfit(x, np.asarray(p, dtype=np.float64), 2)[0]) * (len(p) / 2) ** 2) > limit


def _side_grid(a: Any, side: str) -> Any:
    return {"left": a, "right": a[:, ::-1], "top": a.T, "bottom": a.T[:, ::-1]}[side]


def detect_straight_cuts(alpha: Image.Image, *, min_frac: float = STRAIGHT_MIN_FRAC, min_px: int = 24,
                         tol: float = 1.25, hard_px: int = 4, max_std: float = 12.0) -> list[dict[str, Any]]:
    """Trechos retos do contorno que NÃO encostam na borda da imagem: a linha que sobra quando o recorte veio de um
    quadro já cortado (crop da semente), de uma figura tirada da frente (`minus`) ou de um objeto que tapava o corpo
    (a porta do carro). Para cada lado, a primeira coluna/linha opaca (alfa ≥ 128) vista daquele lado: um trecho de
    pelo menos `min_frac` do lado (e `min_px`) que cabe numa reta de até ~3° (`STRAIGHT_SLOPE_MAX`, desvio ≤ `tol`
    px), com borda firme (o alfa vai de < 50 a > 190 em `hard_px` px: aceita o degradê curto do recorte) e uniforme
    (o perfil do alfa através da linha, alinhado pela posição dela, quase não muda de uma linha para outra: desvio ≤
    `max_std`; o cabelo reto de verdade oscila e não conta), vira {side, pos, pos_a, pos_b, start, end} em fração
    da imagem (`pos_a`/`pos_b`: onde a linha está no começo e no fim do trecho; `pos`, a mediana). Quem chama
    encosta o lado na borda da thumb (se for o caso) ou some com ele num degradê."""
    import numpy as np

    a = np.asarray(alpha if alpha.mode == "L" else alpha.convert("L"), dtype=np.int16)
    h, w = a.shape
    if not h or not w:
        return []
    out: list[dict[str, Any]] = []
    k = np.arange(-hard_px, hard_px + 1)
    for side in CUT_SIDES:
        grid = _side_grid(a, side)
        n, depth = grid.shape
        first = _first_opaque(grid)
        ok = (first > 1) & (first < depth - 2)  # sem figura, ou encostado na borda (é `cut_edges`)
        need = max(min_px, round(n * min_frac))
        for i, j in _linear_runs(first, ok, need, tol=tol):
            rows = np.arange(i, j + 1)
            p = first[rows]
            x = np.arange(len(p), dtype=np.float64)
            slope, icpt = np.polyfit(x, p.astype(np.float64), 1) if len(p) >= 3 else (0.0, float(p[0]))
            prof = grid[rows[:, None], np.clip(p[:, None] + k[None, :], 0, depth - 1)]
            core = max(1, (j - i + 1) // 10)  # as pontas do trecho entram e saem da linha: mede o miolo
            if abs(slope) < 0.01:
                # reta em pé/deitada: as mesmas colunas em todas as linhas (o topo de uma curva, que anda meio
                # pixel aqui e ali, não passa)
                med_i = int(np.median(p))
                mid = grid[i + core:j + 1 - core, max(0, med_i - hard_px):min(depth, med_i + hard_px + 1)]
            else:
                # inclinada: longa (o dobro do mínimo), sem curvatura (o flanco de uma curva também cabe numa reta
                # por alguns px) e com o perfil alinhado pela posição da linha igual em todas as linhas
                if len(p) < 2 * need or _bent(p):
                    continue
                mid = prof[core:len(rows) - core]
            uniform = mid.size and float(mid.std(axis=0).max()) <= max_std
            if not uniform or np.mean((prof[:, 0] < 50) & (prof[:, -1] > 190)) < 0.8:
                continue
            ends = [icpt, icpt + slope * (len(p) - 1)]
            if side in ("right", "bottom"):
                ends = [depth - 1 - e for e in ends]
            med = float(np.median(p))
            med = med if side in ("left", "top") else depth - 1 - med
            span = max(1, depth - 1)
            out.append({"side": side, "pos": round(med / span, 4), "pos_a": round(float(ends[0]) / span, 4),
                        "pos_b": round(float(ends[1]) / span, 4), "start": round(i / n, 4),
                        "end": round((j + 1) / n, 4)})
    return out


NEAR_EDGE_FRAC = 0.10
"""Trecho reto a menos disso (fração do lado) da borda da figura aparada é corte, não contorno: a porta do carro
que tapa a cintura, o crop torto. A figura é aparada ali e o lado vira lado cortado (ancorado na borda da thumb)."""
NEAR_EDGE_MIN_RUN = 0.15
"""Comprimento mínimo do trecho reto perto da borda, em fração do lado."""
EDGE_TRIM_PX = 3
"""Px aparados de cada lado cortado: a última linha às vezes traz a moldura do quadro original (roxo da keyart)."""


def near_edge_cuts(alpha: Image.Image, *, frac: float = NEAR_EDGE_FRAC, min_run: float = NEAR_EDGE_MIN_RUN,
                   tol: float = 2.0) -> dict[str, int]:
    """{lado: px a aparar} dos lados em que o contorno tem um trecho reto longo (≥ `min_run` do lado, até ~3°)
    a menos de `frac` da borda da figura aparada, sem encostar nela: aparando até a parte de dentro do trecho, a
    figura passa a terminar reta naquele lado inteiro, e o compositor encosta esse lado na borda da thumb em vez de
    deixar a reta (com contorno e sombra) flutuando dentro do quadro."""
    import numpy as np

    a = np.asarray(alpha if alpha.mode == "L" else alpha.convert("L"), dtype=np.int16)
    h, w = a.shape
    out: dict[str, int] = {}
    if h < 32 or w < 32:
        return out
    for side in CUT_SIDES:
        grid = _side_grid(a, side)
        n, depth = grid.shape
        first = _first_opaque(grid)
        ok = (first > 2) & (first <= frac * depth)
        runs = [(i, j) for i, j in _linear_runs(first, ok, max(24, round(n * min_run)), tol=tol)
                # o flanco de uma curva (ombro, cabeça) não é corte; a porta do carro, longa, entorta ~2 px em 700
                if not _bent(first[i:j + 1], max(0.5, 0.004 * (j + 1 - i)))]
        if runs:
            out[side] = int(max(int(first[i:j + 1].max()) for i, j in runs)) + 1
    return out


@lru_cache(maxsize=32)
def _prepared_cached(path: str, mtime: float, size: int, edges_key: str,
                     straight_key: str) -> tuple[Image.Image, tuple[str, ...], str, tuple[int, int, int, int],
                                                 tuple[int, int]]:
    im = open_rgba(Path(path))
    box = im.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box:
        im = im.crop(box)
    edges = set(json.loads(edges_key))
    stored = _norm_straight(json.loads(straight_key))
    w0, h0 = im.size
    crop = {s: 0 for s in CUT_SIDES}
    for side, px in near_edge_cuts(im.getchannel("A")).items():
        if side in edges or anchorable(edges | {side}):
            crop[side] = max(crop[side], px)
            edges.add(side)
    for side in edges:
        crop[side] = max(crop[side], EDGE_TRIM_PX)
    left, top, right, bottom = crop["left"], crop["top"], crop["right"], crop["bottom"]
    if left + right < w0 - 16 and top + bottom < h0 - 16 and (left or top or right or bottom):
        im = im.crop((left, top, w0 - right, h0 - bottom))
    else:
        left = top = right = bottom = 0
    big = any(v > EDGE_TRIM_PX for v in (left, top, right, bottom))
    straight = detect_straight_cuts(im.getchannel("A")) if (stored is None or big) else stored
    # o trecho colado num lado cortado é o próprio corte (vai para a borda da thumb), não uma reta de dentro
    straight = [s for s in straight if not (s["side"] in edges and (
        s["pos"] < 0.02 if s["side"] in ("left", "top") else s["pos"] > 0.98))]
    return (im, tuple(s for s in CUT_SIDES if s in edges), json.dumps(straight), (left, top, right, bottom),
            (w0, h0))


def prepared_cutout(path: Path, meta: dict[str, Any] | None = None
                    ) -> tuple[Image.Image, list[str], list[dict[str, Any]], dict[str, Any]]:
    """O recorte pronto para o compositor: aparado no alfa, com os lados cortados de verdade (`cut_edges_of` mais os
    trechos retos colados na borda, `near_edge_cuts`, aparados até ficarem retos), `EDGE_TRIM_PX` a menos em cada
    lado cortado (sem a moldura do quadro original) e os trechos retos de dentro (`straight_cuts_of`, medidos de novo
    quando a figura foi aparada). O `meta` volta com o `face_box` nas frações da figura aparada. Cache pela data do
    arquivo: o compositor chama a cada render. Não altere a imagem devolvida."""
    stat = path.stat()
    edges = cut_edges_of(path, meta)
    stored = straight_cuts_of(path, meta)
    im, cut, straight, (left, top, right, bottom), (w0, h0) = _prepared_cached(
        str(path), stat.st_mtime, stat.st_size, json.dumps(sorted(edges)), json.dumps(stored))
    out_meta = dict(meta or {})
    fb = out_meta.get("face_box")
    if (left or top or right or bottom) and isinstance(fb, (list, tuple)) and len(fb) == 4:
        w1, h1 = max(1, w0 - left - right), max(1, h0 - top - bottom)

        def fx(v: float) -> float:
            return round(min(1.0, max(0.0, (float(v) * w0 - left) / w1)), 4)

        def fy(v: float) -> float:
            return round(min(1.0, max(0.0, (float(v) * h0 - top) / h1)), 4)

        out_meta["face_box"] = [fx(fb[0]), fy(fb[1]), fx(fb[2]), fy(fb[3])]
    return im, list(cut), json.loads(straight), out_meta


def _norm_straight(value: Any) -> list[dict[str, Any]] | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return None
    out = []
    for seg in value if isinstance(value, list) else []:
        try:
            if seg.get("side") in CUT_SIDES:
                pos = float(seg["pos"])
                out.append({"side": seg["side"], "pos": pos, "pos_a": float(seg.get("pos_a", pos)),
                            "pos_b": float(seg.get("pos_b", pos)), "start": float(seg["start"]),
                            "end": float(seg["end"])})
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
    return out


@lru_cache(maxsize=64)
def _straight_cached(path: str, mtime: float, size: int) -> str:
    with Image.open(path) as im:
        stored = _norm_straight(im.info.get(STRAIGHT_TEXT_KEY)) if hasattr(im, "info") else None
        if stored is not None:
            return json.dumps(stored)
        if im.mode not in ("RGBA", "LA", "PA") and "transparency" not in im.info:
            return "[]"
        alpha = im.convert("RGBA").getchannel("A")
        if alpha.getextrema()[0] >= 250:
            return "[]"
        box = alpha.point(lambda v: 255 if v > 8 else 0).getbbox()
        if box and box != (0, 0, *alpha.size):  # mede na figura aparada (frações batem com o `_trim` do compositor)
            alpha = alpha.crop(box)
        return json.dumps(detect_straight_cuts(alpha))


def straight_cuts_of(path: Path, meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Trechos retos de dentro do recorte (`detect_straight_cuts`): os do manifesto (`straight_cuts`, gravados na
    importação); senão os do PNG; senão medidos na hora (recorte antigo), com cache pela data do arquivo."""
    stored = _norm_straight((meta or {}).get("straight_cuts"))
    if stored is not None:
        return stored
    try:
        stat = path.stat()
        return json.loads(_straight_cached(str(path), stat.st_mtime, stat.st_size))
    except (OSError, ValueError):
        return []


def anchorable(cut: list[str] | set[str]) -> bool:
    """O compositor consegue encostar todo lado cortado na borda da thumb: embaixo, numa lateral (espelhando se
    preciso) e em cima só junto com embaixo (figura da altura toda). Dois lados opostos na horizontal ou só o topo
    não encaixam."""
    s = set(cut)
    return not ({"left", "right"} <= s or ("top" in s and "bottom" not in s))


def _clean_alpha(alpha: Image.Image, *, fill_holes: bool, min_part: float, max_hole: float = 0.015,
                 cut: list[str] | None = None) -> Image.Image:
    """Limpa a máscara do rembg: tira ilhas pequenas (< `min_part` da maior parte) e tapa buracos internos (a
    regata branca do Jason na capa some no fundo claro). Buraco que encosta num lado cortado (`cut`) também é
    buraco: a regata vazada que chega na base do quadro não é "fundo de fora". Sem scipy devolve como veio."""
    try:
        import numpy as np
        from scipy import ndimage  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001 - scipy vem com o rembg; sem ele, só não limpa
        return alpha
    a = np.asarray(alpha, dtype=np.uint8)
    solid = a > 127
    labels, n = ndimage.label(solid)
    if n > 1 and min_part > 0:
        sizes = ndimage.sum(solid, labels, range(1, n + 1))
        keep = np.zeros(n + 1, dtype=bool)
        keep[1:] = sizes >= sizes.max() * min_part
        a = np.where(keep[labels] | (a <= 127), a, 0).astype(np.uint8)
        # semitransparente solto (borda de ilha removida) também sai
        near = ndimage.binary_dilation(keep[labels], iterations=max(3, max(a.shape) // 150))
        a = np.where(near, a, 0).astype(np.uint8)
    if fill_holes:
        solid = a > 127
        # o lado cortado vira "parede" (1 px de figura por fora): o vão que só abre para ele fica fechado
        sides = set(cut or [])
        padded = np.pad(solid, 1, constant_values=False)
        if "top" in sides:
            padded[0, :] = True
        if "bottom" in sides:
            padded[-1, :] = True
        if "left" in sides:
            padded[:, 0] = True
        if "right" in sides:
            padded[:, -1] = True
        holes = ndimage.binary_fill_holes(padded)[1:-1, 1:-1] & ~solid
        hl, hn = ndimage.label(holes)
        if hn:
            # só buraco pequeno (regata, brinco); vão grande entre cabeças é fundo de verdade
            sizes = ndimage.sum(holes, hl, range(1, hn + 1))
            small = np.zeros(hn + 1, dtype=bool)
            small[1:] = sizes <= max(1, solid.sum()) * max_hole
            a = np.where(small[hl], 255, a).astype(np.uint8)
    return Image.fromarray(a, "L")


def _decontaminate(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    """Tira o halo do fundo na borda: o pixel semitransparente (cabelo, contorno) carrega a cor do fundo misturada;
    a cor dele passa a vir do interior da figura (média do opaco vizinho), na proporção do quanto ele é fundo. A
    1ª linha opaca da borda (onde a máscara costuma vazar 1 px de fundo) leva um terço da correção. Sem scipy
    devolve como veio."""
    try:
        import numpy as np
        from scipy import ndimage  # type: ignore[import-untyped]
    except Exception:  # noqa: BLE001
        return rgb
    a = np.asarray(alpha, dtype=np.float32) / 255.0
    c = np.asarray(rgb.convert("RGB"), dtype=np.float32)
    opaque = a >= 0.97
    core = ndimage.binary_erosion(opaque, iterations=2)
    if not core.any():
        return rgb
    weight = np.where(opaque & ~core, 0.33, 1.0 - a).astype(np.float32)
    weight[core] = 0.0
    band = weight > 0.01
    if not band.any():
        return rgb
    fill = np.zeros_like(c)
    done = np.zeros(a.shape, dtype=bool)
    coref = core.astype(np.float32)
    for sigma in (2.0, 5.0, 12.0, 30.0):
        wsum = ndimage.gaussian_filter(coref, sigma)
        ok = band & ~done & (wsum > 0.03)
        if ok.any():
            for k in range(3):
                fill[..., k][ok] = ndimage.gaussian_filter(c[..., k] * coref, sigma)[ok] / wsum[ok]
            done |= ok
    weight = np.where(done, weight, 0.0)[..., None]
    out = c * (1.0 - weight) + fill * weight
    return Image.fromarray(np.clip(out + 0.5, 0, 255).astype(np.uint8), "RGB")


def _cut_band(size: tuple[int, int]) -> int:
    """Faixa (px) junto da borda do quadro em que a máscara crua ainda conta como "encostada": ~0,3% do lado."""
    return max(5, round(min(size) * 0.003))


def _square_cut(alpha: Image.Image, cut: list[str], band: int) -> Image.Image:
    """Nos lados cortados, a faixa de `band` px junto da borda passa a ter o máximo dela em cada linha/coluna: a
    figura chega opaca na borda (sem a rampa da máscara suave), e a medida e o encaixe na thumb ficam certos."""
    if not cut or band <= 1:
        return alpha
    out = alpha.copy()
    w, h = out.size
    for side in cut:
        strip = _edge_strip(out, side, band)
        if side in ("top", "bottom"):
            box = (0, 0, w, band) if side == "top" else (0, h - band, w, h)
            fill = strip.resize((w, band), Image.Resampling.NEAREST)
        else:
            box = (0, 0, band, h) if side == "left" else (w - band, 0, w, h)
            fill = strip.resize((band, h), Image.Resampling.NEAREST)
        out.paste(ImageChops.lighter(out.crop(box), fill), box[:2])
    return out


def _soft_post(alpha: Image.Image) -> Image.Image:
    """Limpeza da máscara crua sem endurecer a borda (o `post_process_mask` do rembg binariza: cabelo e contorno
    viravam serra). Abre 1 px para tirar pontinhos e devolve o alfa com a transição suave do modelo."""
    opened = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.MaxFilter(3))
    return ImageChops.darker(alpha, opened.filter(ImageFilter.GaussianBlur(0.8)))


def _crop_box(size: tuple[int, int], crop: list[float] | None) -> tuple[int, int, int, int]:
    w, h = size
    if not crop:
        return (0, 0, w, h)
    return (round(crop[0] * w), round(crop[1] * h), round(crop[2] * w), round(crop[3] * h))


def _load_crop(path: Path, crop: list[float] | None) -> tuple[Image.Image, tuple[int, int, int, int]]:
    """A imagem (RGB) no recorte `crop`, reduzida a `CUTOUT_MAX_SIDE`; devolve também a caixa no original."""
    with Image.open(path) as src:
        im = src.convert("RGB")
    box = _crop_box(im.size, crop)
    if crop:
        im = im.crop(box)
    if max(im.size) > CUTOUT_MAX_SIDE:
        im.thumbnail((CUTOUT_MAX_SIDE, CUTOUT_MAX_SIDE), Image.Resampling.LANCZOS)
    return im, box


def _raw_mask(im: Image.Image, model: str | None, matting: bool) -> Image.Image:
    """Máscara do rembg (L, mesmo tamanho de `im`), sem o pós-processo do rembg (ele binariza a borda: cabelo em
    serra); a limpeza é a nossa, que mantém a borda suave."""
    kwargs: dict[str, Any] = {"post_process_mask": False}
    if matting:
        kwargs.update(alpha_matting=True, alpha_matting_foreground_threshold=240,
                      alpha_matting_background_threshold=12, alpha_matting_erode_size=8)
    try:
        kwargs["session"] = _rembg_session(model or CUTOUT_MODEL)
    except Exception as exc:  # noqa: BLE001 - modelo sem download/rede: tenta o padrão interno do rembg
        log.warning("rembg: modelo %s indisponível (%s); usando o padrão", model or CUTOUT_MODEL, exc)
    result = _rembg_remove(im, **kwargs)  # type: ignore[misc]
    if not isinstance(result, Image.Image):  # rembg devolve bytes quando recebe bytes; aqui é sempre imagem
        from io import BytesIO

        result = Image.open(BytesIO(result))
    # só a máscara vem do rembg: a cor sai do original (o rembg zera o RGB do fundo, e buraco tapado ficaria preto)
    alpha = result.getchannel("A") if result.mode == "RGBA" else result.convert("L")
    if alpha.size != im.size:
        alpha = alpha.resize(im.size, Image.Resampling.LANCZOS)
    return _soft_post(alpha)


def figure_mask(path: Path, *, model: str | None = None, crop: list[float] | None = None,
                matting: bool = False) -> tuple[Image.Image, tuple[int, int, int, int]] | None:
    """Máscara crua de uma figura (para tirar de outro recorte com `minus`): (alfa no recorte, caixa no original)."""
    if not rembg_available():
        return None
    im, box = _load_crop(path, crop)
    return _raw_mask(im, model, matting), box


def _subtract(alpha: Image.Image, box: tuple[int, int, int, int],
              others: list[tuple[Image.Image, tuple[int, int, int, int]]]) -> Image.Image:
    """Tira de `alpha` (recorte na caixa `box` do original) as figuras de `others` (máscara + caixa no mesmo
    original), com 2 px de folga para não sobrar a franja da outra figura."""
    bw, bh = box[2] - box[0], box[3] - box[1]
    sx, sy = alpha.width / max(1, bw), alpha.height / max(1, bh)
    out = alpha
    for mask, obox in others:
        ow, oh = obox[2] - obox[0], obox[3] - obox[1]
        canvas = Image.new("L", alpha.size, 0)
        scaled = mask.resize((max(1, round(ow * sx)), max(1, round(oh * sy))), Image.Resampling.LANCZOS)
        canvas.paste(scaled, (round((obox[0] - box[0]) * sx), round((obox[1] - box[1]) * sy)))
        canvas = canvas.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(1))
        out = ImageChops.subtract(out, canvas)
    return out


def cutout(path: Path, out: Path, *, model: str | None = None, crop: list[float] | None = None,
           fill_holes: bool = True, min_part: float = 0.04, force: bool = False, matting: bool = False,
           decontaminate: bool = True,
           minus: list[tuple[Image.Image, tuple[int, int, int, int]]] | None = None) -> Path | None:
    """Recorte automático com o rembg (se instalado), gravado como PNG com alfa e aparado na figura.

    `crop` (x0, y0, x1, y1 em fração) corta a imagem antes (ex.: só a Lucia na capa do casal). `matting` liga o
    alpha matting do rembg (cabelo fino; mais lento). `minus`: máscaras de outras figuras do mesmo original a tirar
    (o rosto da Lucia na frente do peito do Jason, na capa). Os lados em que a figura encosta na borda do quadro são
    medidos antes de aparar e gravados no PNG (`vs:cut_edges`): o compositor ancora esses lados na borda da thumb.
    Cache: se `out` já existe e é mais novo que `path`, não roda de novo. Sem rembg devolve None: a tela mostra
    "precisa de recorte" e o compositor segue com o quadro inteiro."""
    if not rembg_available():
        return None
    if not force and out.is_file() and out.stat().st_size > 0 and out.stat().st_mtime >= path.stat().st_mtime:
        return out
    im, box = _load_crop(path, crop)
    alpha = _raw_mask(im, model, matting)
    if minus:
        alpha = _subtract(alpha, box, minus)
    band = _cut_band(alpha.size)
    alpha = _clean_alpha(alpha, fill_holes=fill_holes, min_part=min_part,
                         cut=detect_cut_edges(alpha, trimmed=False, band=band))
    if alpha.getextrema()[1] < 128:
        log.warning("rembg não achou figura em %s", path)
        return None
    # depois da limpeza (a ilha que encostava na borda pode ter saído); medido numa faixa: a máscara suave chega na
    # borda do quadro com o alfa caindo nos últimos px, e o lado cortado ficava sem ser visto
    cut = detect_cut_edges(alpha, trimmed=False, band=band)
    alpha = _square_cut(alpha, cut, band)
    rgb = _decontaminate(im, alpha) if decontaminate else im
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    trim = alpha.point(lambda v: 255 if v > 8 else 0).getbbox() or (0, 0, *alpha.size)
    result = result.crop(trim)
    with Image.open(path) as src:
        sw, sh = src.size
    sx, sy = (box[2] - box[0]) / im.width, (box[3] - box[1]) / im.height
    src_box = [(box[0] + trim[0] * sx) / sw, (box[1] + trim[1] * sy) / sh,
               (box[0] + trim[2] * sx) / sw, (box[1] + trim[3] * sy) / sh]
    info = PngInfo()
    info.add_text(CUT_TEXT_KEY, ",".join(cut))
    info.add_text(SRC_BOX_KEY, ",".join(f"{v:.5f}" for v in src_box))
    info.add_text(STRAIGHT_TEXT_KEY, json.dumps(detect_straight_cuts(result.getchannel("A"))))
    info.add_text("vs:cutout_v", str(CUTOUT_VERSION))
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(f".{out.stem}.tmp.png")
    result.save(tmp, "PNG", optimize=True, pnginfo=info)
    tmp.replace(out)
    return out


def cutout_cache_key(path: Path, *, model: str | None = None, crop: list[float] | None = None) -> str:
    """Chave do recorte automático: conteúdo do arquivo + modelo + corte + versão da limpeza."""
    h = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    h.update(f"|{model or CUTOUT_MODEL}|{crop or ''}|v{CUTOUT_VERSION}".encode())
    return h.hexdigest()[:20]


def auto_cutout(path: Path, cache_dir: Path, *, model: str | None = None, crop: list[float] | None = None,
                ) -> Path | None:
    """Recorte cacheado pelo conteúdo (para o compositor recortar na hora uma figura sem alfa). Falha vira None."""
    if not rembg_available():
        return None
    try:
        out = cache_dir / f"{cutout_cache_key(path, model=model, crop=crop)}.png"
        if out.is_file() and out.stat().st_size > 0:
            return out
        empty = out.with_suffix(".vazio")  # cache negativo: o rembg não achou figura nesse conteúdo
        if empty.is_file():
            return None
        done = cutout(path, out, model=model, crop=crop, force=True)
        if done is None:
            cache_dir.mkdir(parents=True, exist_ok=True)
            empty.write_text("", encoding="utf-8")
        return done
    except Exception as exc:  # noqa: BLE001 - recorte é opcional: qualquer falha cai no modo sem recorte
        log.warning("recorte automático falhou em %s: %s", path, exc)
        return None


# --- Resolvedor ------------------------------------------------------------------------------------------------------
class AssetResolver:
    """Resolve as referências das camadas para arquivos. Não acessa banco: o canal entra pelo `kit_dir`, o avatar
    pelas pastas de pose (ou pelo `Presenter` do canal) e a mídia do projeto por uma função."""

    def __init__(
        self,
        kit_dir: Path | None = None,
        *,
        pose_dirs: list[Path] | None = None,
        poses: dict[str, Path] | None = None,
        media: Callable[[str], Path | None] | None = None,
        allow_editorial: bool = False,
        extract: bool = True,
        auto_cutout: bool = True,
        cutout_cache: Path | None = None,
        presenter_on: bool = True,
    ) -> None:
        self.kit_dir = kit_dir
        self.manifest = load_manifest(kit_dir) if kit_dir else empty_manifest("")
        self.pose_dirs = list(pose_dirs or [])
        self.poses = dict(poses or {})
        self.media = media
        self.allow_editorial = allow_editorial
        self.extract = extract
        self.auto_cutout = auto_cutout
        self.cutout_cache = cutout_cache
        self.presenter_on = presenter_on
        """False: o avatar está desligado para a thumb deste projeto e a camada `presenter` não é desenhada."""
        self.used: list[str] = []

    # Construtores ----------------------------------------------------------------------------------------------------
    @classmethod
    def for_channel(cls, channel_id: str, *, presenter: Any = None, allow_editorial: bool = False,
                    media: Callable[[str], Path | None] | None = None) -> AssetResolver:
        """Kit do canal + poses do `Presenter` efetivo na thumb (None: avatar desligado, nenhuma pose resolve e a
        camada do avatar não é desenhada). O material oficial só entra com `allow_editorial` (padrão desligado: quem
        chama passa a permissão efetiva do canal)."""
        data = get_settings().data_dir
        poses: dict[str, Path] = {}
        for pose in getattr(presenter, "poses", None) or []:
            poses[pose.id] = data / pose.file
        return cls(thumbkit_dir(channel_id), poses=poses, media=media, allow_editorial=allow_editorial,
                   presenter_on=presenter is not None)

    # Consultas -------------------------------------------------------------------------------------------------------
    def item(self, item_id: str) -> dict[str, Any] | None:
        return next((i for i in self.manifest.get("items", []) if i.get("id") == item_id), None)

    def items(self, kind: str | None = None, tag: str | None = None, *, include_low: bool = False,
              ) -> list[dict[str, Any]]:
        """Itens do manifesto (sem o material oficial bloqueado). Recorte de qualidade baixa (`cutout_quality`) só
        entra com `include_low`: o seletor não oferece figura que sai torta na thumb."""
        out = []
        for it in self.manifest.get("items", []):
            if kind and it.get("kind") != kind:
                continue
            if it.get("kind") == "cutout" and not include_low and cutout_quality(it, self.kit_dir) == QUALITY_LOW:
                continue
            if tag and tag not in (it.get("tags") or []):
                continue
            if it.get("editorial") and not self.allow_editorial:
                continue
            out.append(it)
        return out

    def logo_id(self, preferred: str | None = None) -> str | None:
        """O logo do canal: o preferido (estilo) se existir; senão o primeiro com a tag `lockup`, senão o 1º logo."""
        if preferred and self.item(preferred):
            return preferred
        logos = self.items("logo")
        lockup = next((i for i in logos if "lockup" in (i.get("tags") or [])), None)
        return (lockup or (logos[0] if logos else {})).get("id")

    def big_vi_id(self) -> str | None:
        logos = self.items("logo")
        vi = next((i for i in logos if "vi" in (i.get("tags") or [])), None)
        return vi.get("id") if vi else None

    def next_frame(self, current_ref: str | None, tags: list[str] | None = None, skip: set[str] | None = None) -> str | None:
        """O próximo quadro da biblioteca (mesma tag, se houver) depois de `current_ref`: base da diversidade (B.7)."""
        frames = self.items("frame")
        if not frames:
            return None
        cur_id = (current_ref or "").split(":", 1)[-1]
        cur = self.item(cur_id)
        want = set(tags or (cur or {}).get("tags") or [])
        ids = [f["id"] for f in frames]
        start = ids.index(cur_id) + 1 if cur_id in ids else 0
        ordered = ids[start:] + ids[:start]
        skip = skip or set()
        same = [i for i in ordered if i != cur_id and i not in skip and want & set(self.item(i).get("tags") or [])]
        other = [i for i in ordered if i != cur_id and i not in skip]
        pick = (same or other or [None])[0]
        return f"frame:{pick}" if pick else None

    def cutout_for(self, asset: Asset) -> Asset | None:
        """Versão recortada (alfa) de uma figura sem transparência, pelo rembg e com cache pelo conteúdo. None quando
        o rembg falta, o recorte automático está desligado ou falhou: o compositor avisa "precisa de recorte"."""
        if not self.auto_cutout or not rembg_available():
            return None
        cache = self.cutout_cache or (self.kit_dir / "cutouts" / "_auto" if self.kit_dir
                                      else get_settings().cache_dir / "thumb-cutouts")
        meta = asset.meta or {}
        path = auto_cutout(asset.path, cache, model=meta.get("cutout_model"), crop=meta.get("cutout_crop"))
        if path is None:
            return None
        return Asset(asset.ref, path, "cutout", asset.editorial, {**meta, "auto_cutout": True})

    # Resolução -------------------------------------------------------------------------------------------------------
    def resolve(self, ref: str) -> Asset:
        ref = (ref or "").strip()
        if not ref:
            raise AssetError("referência vazia")
        prefix, _, rest = ref.partition(":")
        if not rest:
            prefix, rest = "", ref
        if prefix == "pose":
            asset = self._pose(rest, ref)
        elif prefix == "upload":
            asset = self._upload(rest, ref)
        elif prefix == "media":
            asset = self._media(rest, ref)
        elif prefix == "frame" and FRAME_REF_RE.match(rest):
            asset = self._video_frame(rest, ref)
        elif prefix in ("", "frame", "cutout", "logo", "object"):
            asset = self._item(rest, ref, prefix)
        else:
            raise AssetError(f"referência desconhecida: {ref}")
        if asset.editorial and not self.allow_editorial:
            raise AssetError(f"material oficial bloqueado neste canal (allow_editorial desligado): {ref}")
        self.used.append(ref)
        return asset

    def _item(self, item_id: str, ref: str, prefix: str) -> Asset:
        it = self.item(item_id)
        if not it or not self.kit_dir:
            raise AssetError(f"item não está na biblioteca de thumbs: {ref}")
        if prefix and prefix != it.get("kind"):
            raise AssetError(f"{ref}: o item é do tipo {it.get('kind')}")
        path = self.kit_dir / it["file"]
        if not path.is_file():
            raise AssetError(f"arquivo do item sumiu: {path}")
        return Asset(ref, path, it["kind"], bool(it.get("editorial")), dict(it))

    def _video_frame(self, rest: str, ref: str) -> Asset:
        m = FRAME_REF_RE.match(rest)
        assert m
        video, t = m.group("video"), float(m.group("t"))
        def src(i: dict[str, Any]) -> dict[str, Any]:  # item enviado antigo podia ter `source` em texto
            value = i.get("source")
            return value if isinstance(value, dict) else {}

        meta = next((dict(i) for i in self.manifest.get("items", [])
                     if src(i).get("video") == video and abs(float(src(i).get("t", -1)) - t) < 0.01), {})
        if not self.kit_dir:
            raise AssetError(f"sem biblioteca para extrair {ref}")
        out = self.kit_dir / "frames" / frame_file_name(video, t)
        if not out.is_file():
            src = self.manifest.get("videos", {}).get(video)
            if not src:
                raise AssetError(f"vídeo {video} não está registrado no thumbkit")
            if not self.extract:
                raise AssetError(f"quadro {ref} ainda não extraído")
            extract_frame(Path(src), t, out)
        return Asset(ref, out, "frame", True if not meta else bool(meta.get("editorial", True)), meta)

    def _pose(self, pose_id: str, ref: str) -> Asset:
        if not self.presenter_on:
            raise AssetError(f"avatar desligado na thumb deste projeto: {pose_id}")
        path = self.poses.get(pose_id)
        if path is None or not path.is_file():
            path = next((d / f"{pose_id}.png" for d in self.pose_dirs if (d / f"{pose_id}.png").is_file()), None)
        if path is None:
            raise AssetError(f"pose do avatar não encontrada: {pose_id}")
        return Asset(ref, path, "pose", False, {})

    def _upload(self, raw: str, ref: str) -> Asset:
        path = Path(raw)
        if not path.is_absolute():
            path = get_settings().data_dir / raw
        if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS:
            raise AssetError(f"upload não encontrado: {raw}")
        meta = _kit_item_for(path)
        if meta is None:
            return Asset(ref, path, "upload", False, {})
        # Arquivo de uma biblioteca de thumbs (deste canal ou de outro): vale o `editorial` do manifesto dela; quadro
        # extraído ou arquivo fora do manifesto conta como material oficial (bloqueado sem `allow_editorial`).
        return Asset(ref, path, "upload", bool(meta.get("editorial", True)), meta)

    def _media(self, media_id: str, ref: str) -> Asset:
        path = self.media(media_id) if self.media else None
        if path is None or not Path(path).is_file():
            raise AssetError(f"mídia do projeto indisponível: {media_id}")
        return Asset(ref, Path(path), "media", False, {})


def _kit_item_for(path: Path) -> dict[str, Any] | None:
    """O item do manifesto quando `path` fica dentro de `library/thumbkit/<canal>/`; `{}` para arquivo da biblioteca
    sem item no manifesto (conta como oficial); None fora de qualquer biblioteca de thumbs."""
    root = (get_settings().library_dir / "thumbkit").resolve()
    try:
        rel = path.resolve().relative_to(root)
    except ValueError:
        return None
    if len(rel.parts) < 2:
        return None
    kit = root / rel.parts[0]
    inner = Path(*rel.parts[1:]).as_posix()
    manifest = load_manifest(kit)
    item = next((dict(i) for i in manifest.get("items", []) if str(i.get("file") or "") == inner), None)
    return item if item is not None else {}


# --- Importação inicial (CLI `studio thumbkit import`) --------------------------------------------------------------
def load_seed(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SEED_FILE).read_text(encoding="utf-8"))


def import_kit(channel_id: str, canais_dir: Path, *, seed: dict[str, Any] | None = None, frames: bool = True,
               kit_dir: Path | None = None, cutouts: bool = True) -> dict[str, Any]:
    """Copia logos e quadros com personagem do canais, extrai os tempos curados dos trailers, recorta os personagens
    (se o rembg estiver instalado) e grava o manifesto.
    Idempotente: itens com o mesmo id são substituídos; quadros e recortes já feitos não são refeitos.
    Devolve um resumo {items, frames, cutouts, skipped, warnings}."""
    seed = seed or load_seed()
    kit = kit_dir or thumbkit_dir(channel_id)
    kit.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(kit, channel_id)
    manifest["channel_id"] = channel_id
    summary: dict[str, Any] = {"kit_dir": str(kit), "items": 0, "frames": 0, "skipped": [], "warnings": []}

    for name, rel in (seed.get("videos") or {}).items():
        src = canais_dir / rel
        if src.is_file():
            manifest["videos"][name] = str(src).replace("\\", "/")
        else:
            summary["skipped"].append(f"vídeo {rel}")

    for entry in seed.get("files") or []:
        src = canais_dir / entry["src"]
        if not src.is_file():
            summary["skipped"].append(entry["src"])
            continue
        kind = entry.get("kind", "frame")
        if kind in ("cutout", "logo") and not has_alpha(src):
            summary["warnings"].append(f"{entry['src']}: {kind} sem alfa, ignorado")
            continue
        sub = {"frame": "frames", "cutout": "cutouts", "logo": "logos", "object": "objects"}[kind]
        dest = kit / sub / f"{entry['id']}{src.suffix.lower()}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if entry.get("crop"):  # corte de borda (ex.: faixa lateral da capa); focus/face_box valem no arquivo cortado
            if not dest.is_file() or dest.stat().st_mtime < src.stat().st_mtime:
                _crop_save(src, dest, entry["crop"])
        elif not dest.is_file() or dest.stat().st_size != src.stat().st_size:
            shutil.copy2(src, dest)
        item = {k: v for k, v in entry.items() if k not in ("src", "crop")}
        item.update(kind=kind, file=dest.relative_to(kit).as_posix(), source={"file": entry["src"]},
                    size=_image_size(dest))
        upsert_item(manifest, item)
        summary["items"] += 1

    if frames:
        for entry in seed.get("frames") or []:
            video = entry["video"]
            src = manifest["videos"].get(video)
            if not src:
                summary["skipped"].append(f"{entry['id']} (sem {video})")
                continue
            t = float(entry["t"])
            out = kit / "frames" / frame_file_name(video, t)
            try:
                extract_frame(Path(src), t, out)
            except Exception as exc:  # noqa: BLE001 - um quadro ruim não derruba a importação
                summary["warnings"].append(f"{entry['id']}: {exc}")
                continue
            crop = entry.get("crop")
            file = out
            if crop:
                file = kit / "frames" / f"{entry['id']}.jpg"
                if not file.is_file():
                    _crop_save(out, file, crop)
            item = {k: v for k, v in entry.items() if k not in ("video", "t", "crop")}
            item.update(kind="frame", file=file.relative_to(kit).as_posix(), source={"video": video, "t": t},
                        editorial=entry.get("editorial", True), size=_image_size(file))
            upsert_item(manifest, item)
            summary["frames"] += 1
            summary["items"] += 1

    summary["cutouts"] = 0
    if cutouts and seed.get("cutouts"):
        make_cutouts(kit, manifest, seed["cutouts"], summary)

    save_manifest(kit, manifest)
    summary["total"] = len(manifest["items"])
    return summary


SEED_ONLY_KEYS = ("from", "crop", "model", "fill_holes", "matting", "minus", "face_src", "occluded")


def _face_from_src(face_src: list[float] | None, cut_png: Path) -> list[float] | None:
    """`face_src` (rosto em fração do original, na semente) → `face_box` (fração do recorte aparado): continua certo
    quando o modelo novo apara a figura em outro lugar."""
    if not face_src or len(face_src) != 4:
        return None
    with Image.open(cut_png) as im:
        raw = im.info.get(SRC_BOX_KEY)
    try:
        bx0, by0, bx1, by1 = (float(v) for v in str(raw).split(","))
    except ValueError:
        return None
    bw, bh = max(1e-6, bx1 - bx0), max(1e-6, by1 - by0)

    def fr(v: float, lo: float, size: float) -> float:
        return round(min(1.0, max(0.0, (v - lo) / size)), 3)

    return [fr(face_src[0], bx0, bw), fr(face_src[1], by0, bh), fr(face_src[2], bx0, bw), fr(face_src[3], by0, bh)]
QUALITY_LOW = "baixa"


def cutout_quality(item: dict[str, Any], kit_dir: Path | None = None) -> str:
    """"baixa" quando o recorte não serve para thumb (marcado na semente ou cortado de um jeito que não encaixa na
    borda); "ok" no resto. O seletor de ativos do pacote deixa os de qualidade baixa de fora. Item sem `cut_edges`
    (enviado pela tela, recorte antigo) é medido no arquivo com `kit_dir`, como o compositor faz (`cut_edges_of`)."""
    if str(item.get("quality") or "").lower() == QUALITY_LOW or item.get("no_thumb"):
        return QUALITY_LOW
    edges = _norm_edges(item.get("cut_edges"))
    if edges is None and kit_dir is not None and item.get("file"):
        edges = cut_edges_of(kit_dir / str(item["file"]))
    if edges is not None and not anchorable(edges):
        return QUALITY_LOW
    return "ok"


def _cutout_source(entry: dict[str, Any]) -> dict[str, Any]:
    """Parâmetros que fazem o recorte (`source` do item): mudou algum, ou a versão do algoritmo, o recorte é refeito.
    Aceita a entrada da semente ou o `source` gravado (o `from` igual nos dois)."""
    out: dict[str, Any] = {"from": entry.get("from"), "crop": entry.get("crop"),
                           "model": entry.get("model") or CUTOUT_MODEL}
    if entry.get("matting"):
        out["matting"] = True
    if entry.get("minus"):
        out["minus"] = list(entry["minus"])
    out["v"] = CUTOUT_VERSION if "id" in entry else entry.get("v", 1)  # source antigo sem "v" é da v1
    return out


def make_cutouts(kit: Path, manifest: dict[str, Any], entries: list[dict[str, Any]],
                 summary: dict[str, Any] | None = None, *, force: bool = False) -> dict[str, Any]:
    """Gera os recortes de personagem da semente a partir de itens já no manifesto (`from`: id do quadro/capa).

    Cada entrada: {id, from, crop?, model?, matting?, minus?, occluded?, fill_holes?, quality?, quality_note?,
    tags, credit, face_box? | face_src?}. `minus`: ids de outras entradas do mesmo `from` cuja figura sai deste recorte (pessoa na frente).
    O recorte herda `editorial` do item de origem (capa e trailers são material oficial) e ganha `cut_edges` (lados
    cortados pela borda do quadro, para o compositor ancorar) e `quality` ("baixa" sai do seletor do pacote).
    Recorte de versão antiga do algoritmo (`source.v`) ou com parâmetros trocados é refeito. Sem rembg, nada é
    gerado e o resumo ganha um aviso."""
    summary = summary if summary is not None else {"cutouts": 0, "skipped": [], "warnings": [], "items": 0}
    summary.setdefault("cutouts", 0)
    if not rembg_available():
        summary["warnings"].append(f"rembg não instalado: {len(entries)} recortes não gerados (thumbs sem recorte)")
        return summary
    by_id = {i.get("id"): i for i in manifest.get("items", [])}
    seed_by_id = {e.get("id"): e for e in entries}
    for entry in entries:
        origin = by_id.get(entry.get("from"))
        clash = by_id.get(entry.get("id"))
        if clash and clash.get("kind") != "cutout":  # não sobrescreve o quadro de origem (mesmo id)
            summary["warnings"].append(f"recorte {entry.get('id')}: id já usado por um {clash.get('kind')}")
            continue
        if not origin or origin.get("kind") == "cutout":
            summary["skipped"].append(f"recorte {entry.get('id')} (sem {entry.get('from')})")
            continue
        src = kit / origin["file"]
        dest = kit / "cutouts" / f"{entry['id']}.png"
        source = _cutout_source(entry)
        old = (clash or {}).get("source")
        stale = force or not dest.is_file() or not isinstance(old, dict) or _cutout_source(old) != source
        # o `cutout` também refaz quando o quadro de origem ficou mais novo (capa reimportada com `crop`): decide
        # aqui, para o `minus` entrar sempre que o recorte for refeito
        redo = stale or dest.stat().st_size == 0 or dest.stat().st_mtime < src.stat().st_mtime
        try:
            minus = []
            for other_id in source.get("minus", []):
                other = seed_by_id.get(other_id)
                if not other or other.get("from") != entry["from"]:
                    summary["warnings"].append(f"recorte {entry['id']}: minus {other_id} não é do mesmo quadro")
                    continue
                if redo:
                    m = figure_mask(src, model=other.get("model"), crop=other.get("crop"),
                                    matting=bool(other.get("matting")))
                    if m is not None:
                        minus.append(m)
            done = cutout(src, dest, model=entry.get("model"), crop=entry.get("crop"),
                          fill_holes=entry.get("fill_holes", True), matting=bool(entry.get("matting")),
                          minus=minus or None, force=redo)
        except Exception as exc:  # noqa: BLE001 - um recorte ruim não derruba a importação
            summary["warnings"].append(f"recorte {entry['id']}: {exc}")
            continue
        if done is None:
            summary["warnings"].append(f"recorte {entry['id']}: o rembg não achou a figura")
            continue
        item = {k: v for k, v in entry.items() if k not in SEED_ONLY_KEYS}
        face = _face_from_src(entry.get("face_src"), dest)
        if face:
            item["face_box"] = face
        item.update(kind="cutout", file=dest.relative_to(kit).as_posix(),
                    editorial=entry.get("editorial", bool(origin.get("editorial", True))),
                    credit=entry.get("credit") or origin.get("credit", ""),
                    source=source, size=_image_size(dest),
                    # `occluded`: lado em que a figura termina reta atrás de um objeto (a porta do carro), que
                    # o alfa não mostra como borda do quadro mas precisa ser ancorado igual
                    cut_edges=_norm_edges([*cut_edges_of(dest), *(entry.get("occluded") or [])]),
                    # trechos retos de dentro (crop, `minus`, objeto na frente): o compositor some com eles
                    straight_cuts=straight_cuts_of(dest))
        item["quality"] = cutout_quality(item)
        if item["quality"] == QUALITY_LOW and not item.get("quality_note"):
            item["quality_note"] = f"cortado em {', '.join(item['cut_edges'])}: não encaixa na borda da thumb"
        if item["quality"] == QUALITY_LOW:
            summary["warnings"].append(f"recorte {entry['id']}: qualidade baixa ({item.get('quality_note')})")
        upsert_item(manifest, item)
        summary["cutouts"] += 1
        summary["items"] = summary.get("items", 0) + 1
    return summary


def _crop_save(src: Path, dest: Path, crop: list[float]) -> None:
    """Corta `src` pela caixa em fração (x0, y0, x1, y1) e grava em `dest` (PNG mantém o alfa)."""
    with Image.open(src) as im:
        w, h = im.size
        box = (round(crop[0] * w), round(crop[1] * h), round(crop[2] * w), round(crop[3] * h))
        part = im.crop(box)
        if dest.suffix.lower() == ".png":
            part.save(dest, "PNG")
        else:
            part.convert("RGB").save(dest, "JPEG", quality=94)


def _image_size(path: Path) -> list[int]:
    with Image.open(path) as im:
        return [im.width, im.height]


def default_canais_dir() -> Path:
    configured = get_settings().canais_dir
    return Path(configured) if configured else Path.home() / "canais"


__all__ = [
    "CUTOUT_MODEL", "CUTOUT_MODELS", "CUTOUT_VERSION", "QUALITY_LOW", "anchorable", "cut_edges_of", "cutout_quality",
    "detect_cut_edges", "figure_mask", "Asset", "AssetError", "AssetResolver", "HAS_REMBG", "MANIFEST", "REPO_ROOT", "SEED_FILE",
    "auto_cutout", "cutout", "cutout_cache_key", "default_canais_dir", "extract_frame", "has_alpha", "import_kit",
    "load_manifest", "load_seed", "make_cutouts", "open_rgba", "rembg_available", "save_manifest", "thumbkit_dir",
    "upsert_item",
]
