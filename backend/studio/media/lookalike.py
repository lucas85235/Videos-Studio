"""A mesma imagem em outra fonte: o par que a contagem de aparições do mesmo material não pega pelo item nem pelo
arquivo (QA r4 do prj_gq4krrs11w: a arte-chave do GTA VI entrou por seis itens do acervo, o blog da PlayStation, a
capa do kit do canal, o pôster com fundo desfocado, a captura do site oficial e a do site falso que copia a mesma
colagem, e somou umas 25 aparições com o teto de 2 por item).

Duas imagens são "parecidas" quando uma contém a outra em escala menor ou igual, com correlação normalizada alta, em
cinza e em baixa resolução: a mesma arte noutro tamanho ou com outro recorte de borda, a arte dentro de uma página
capturada (o site que a usa de fundo) e a arte no centro do quadro com o fundo desfocado (pôster vertical em
pillarbox). A correlação é do desenho (cinza, sem média), não da cor: a mesma arte com outro tratamento de cor ainda
bate, e duas páginas brancas de matéria com texto não, porque a correlação do texto miúdo em 96 px cai (e página
lisa demais não tem assinatura, como no `novelty.dhash`).

`LookIndex` agrupa as imagens de um vídeo por parecença (cada imagem nova é comparada com as que já entraram, com
cache por arquivo) e devolve a marca do grupo, `look:<n>`, que entra junto do item e do arquivo nas aparições do
mesmo material (`sources.media_tokens`/`item_tokens` com `look`)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageOps

if TYPE_CHECKING:
    from studio.core.project.document import MediaRef
    from studio.core.project.pool import PoolItem

BIG_W = 96
"""Largura da imagem onde a outra é procurada."""
MIN_TPL = 20
"""Menor lado (px, na escala de `BIG_W`) do modelo procurado: menos que isso casa com qualquer textura."""
MIN_FRACTION = 0.34
"""A imagem contida ocupa pelo menos esta fração da largura (ou da altura) da outra: a arte num cantinho da página
(miniatura de matéria relacionada) não é a mesma imagem na tela."""
SCALES = 9
MIN_STD = 12.0
"""Desvio do cinza abaixo do qual a imagem (ou o modelo) é lisa demais para comparar (fundo branco de página)."""
_KERNEL = tuple(k / 256.0 for k in (1, 8, 28, 56, 70, 56, 28, 8, 1))
"""Borrado do passa-alta (binomial de 9 pontos, ~gaussiana de raio 1,4)."""
SAME_LOOK = 0.75
"""Correlação (do desenho, `_detail`) a partir da qual as duas são a mesma imagem. Medido no acervo do
prj_gq4krrs11w: a mesma arte noutra fonte, no pôster com fundo desfocado ou no fundo do site oficial dá de 0,80 a
1,0; duas capturas de matéria diferentes (páginas brancas com texto) chegam a 0,65, e dois quadros do mesmo trailer
na mesma rua, a 0,67."""


@dataclass(frozen=True)
class Signature:
    gray: np.ndarray
    """Cinza em `BIG_W` de largura (float32)."""
    detail: np.ndarray
    """O desenho do cinza (`_detail`), onde a outra é procurada."""
    crops: tuple[tuple[np.ndarray, bool], ...]
    """O que se procura dentro da outra, em cinza: a imagem inteira, o miolo dela (sem 12% de cada borda) e, quando
    ela tem faixas sem desenho dos lados ou em cima e embaixo (o pôster vertical no meio do fundo desfocado), o
    conteúdo sem elas e o miolo dele. O segundo valor diz se o recorte é procurado em várias escalas (o conteúdo do
    pôster é um pedaço da arte, que na outra pode estar maior)."""
    page: bool = False
    """Captura de página: a outra imagem é procurada nela em várias escalas (a arte de fundo do site, a foto da
    matéria). Nas outras, só na maior escala em que cabe (a mesma imagem noutro tamanho ou recorte, e o pôster no
    meio do fundo desfocado), o que deixa a comparação barata."""
    spectrum: dict[tuple[int, int], np.ndarray] = field(default_factory=dict, compare=False, repr=False)
    """Cache da transformada do `detail` por tamanho."""
    templates: dict[tuple[int, int, int, int, int], tuple[np.ndarray, float] | None] = field(
        default_factory=dict, compare=False, repr=False)
    """Cache dos recortes já redimensionados (a maioria das imagens tem a mesma proporção: o mesmo tamanho volta):
    (índice do recorte, largura, altura, tamanho da transformada) → (transformada do modelo sem média, norma)."""
    window: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = field(default_factory=dict, compare=False,
                                                                          repr=False)
    """Cache das somas do `detail` e do quadrado dele por tamanho de janela."""

    def sums(self, h: int, w: int) -> tuple[np.ndarray, np.ndarray]:
        """Somas do `detail` e do quadrado dele em cada janela `h`x`w` (em cache)."""
        if (h, w) not in self.window:
            self.window[(h, w)] = (_window_sums(self.detail, h, w), _window_sums(self.detail * self.detail, h, w))
        return self.window[(h, w)]


def _gray(image: Image.Image, width: int) -> np.ndarray:
    img = ImageOps.exif_transpose(image).convert("L")
    h = max(1, round(img.height * width / max(1, img.width)))
    return np.asarray(img.resize((width, h), Image.Resampling.BILINEAR), dtype=np.float32)


def _detail(gray: np.ndarray) -> np.ndarray:
    """O desenho da imagem sem o degradê: o cinza menos o borrado dele (passa-alta). Duas fotos de céu nublado ou
    dois fundos escuros não se correlacionam mais pelo degradê."""
    blur = gray
    for axis in (0, 1):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (4, 4)
        padded = np.pad(blur, pad, mode="edge")
        n = blur.shape[axis]
        blur = sum(k * np.take(padded, range(i, i + n), axis=axis) for i, k in enumerate(_KERNEL))
    return (gray - blur).astype(np.float32)


def signature(image: Image.Image, *, page: bool = False) -> Signature | None:
    """A assinatura da imagem (`page`: é a captura de uma página); None quando ela é lisa demais para comparar."""
    gray = _gray(image, BIG_W)
    if float(gray.std()) < MIN_STD or min(gray.shape) < MIN_TPL:
        return None
    detail = _detail(gray)
    crops: list[tuple[np.ndarray, bool]] = [(gray, False)]
    _add_core(crops, gray, False)
    content = _content(gray, detail)
    if content is not None:
        crops.append((content, True))
        _add_core(crops, content, True)
    crops = [(c, m) for c, m in crops if _coverage(_detail(c)) >= MIN_COVERAGE]
    return Signature(gray=gray, detail=detail, crops=tuple(crops), page=page)


WINDOW_DETAIL = 0.4
"""O desvio do desenho da janela da outra imagem é pelo menos esta fração do desvio do modelo."""
MIN_COVERAGE = 0.35
"""Fração mínima dos blocos do recorte com desenho para ele ser procurado na outra: o logo no meio do preto tem o
desenho num canto só e casaria com toda arte que leva o logo. Ele ainda é a imagem onde as outras são procuradas."""


def _coverage(detail: np.ndarray, cells: int = 8) -> float:
    energy = np.abs(detail)
    h, w = energy.shape
    ys = np.linspace(0, h, cells + 1).astype(int)
    xs = np.linspace(0, w, cells + 1).astype(int)
    blocks = np.array([[energy[ys[i]:ys[i + 1], xs[j]:xs[j + 1]].mean() for j in range(cells)] for i in range(cells)])
    ref = float(np.percentile(blocks, 90))
    return float((blocks >= 0.25 * ref).mean()) if ref > 1e-6 else 0.0


def _add_core(crops: list[tuple[np.ndarray, bool]], gray: np.ndarray, multi: bool) -> None:
    h, w = gray.shape
    dy, dx = round(h * 0.12), round(w * 0.12)
    core = gray[dy:h - dy, dx:w - dx]
    if min(core.shape) >= MIN_TPL and float(core.std()) >= MIN_STD:
        crops.append((core, multi))


MIN_CONTENT = 0.4
"""O conteúdo sem as faixas ocupa pelo menos esta fração da imagem: menos que isso é um detalhe (logo, selo) que
outras artes também têm, e procurá-lo nelas juntaria imagens diferentes."""
TRIM_ENERGY = 0.3
"""Coluna (ou linha) com menos desenho que esta fração do das colunas mais desenhadas é faixa de fundo (borrado,
liso)."""


def _content(gray: np.ndarray, detail: np.ndarray) -> np.ndarray | None:
    """O conteúdo sem as faixas sem desenho das bordas (fundo desfocado do pôster, tarjas pretas); None quando não
    há faixa que tire pelo menos 15% da largura ou da altura."""
    energy = np.abs(detail)
    box = [0, gray.shape[0], 0, gray.shape[1]]
    for axis, (lo, hi) in ((0, (2, 3)), (1, (0, 1))):
        profile = energy.mean(axis=axis)
        keep = np.nonzero(profile >= TRIM_ENERGY * float(np.percentile(profile, 90)))[0]
        if keep.size == 0:
            return None
        box[lo], box[hi] = int(keep[0]), int(keep[-1]) + 1
    y0, y1, x0, x1 = box
    width, height = (x1 - x0) / gray.shape[1], (y1 - y0) / gray.shape[0]
    if (width > 0.85 and height > 0.85) or width * height < MIN_CONTENT:
        return None  # sem faixa, ou só um detalhe no meio do liso (o logo sobre o preto: não é a arte que o usa)
    crop = gray[y0:y1, x0:x1]
    return crop if min(crop.shape) >= MIN_TPL and float(crop.std()) >= MIN_STD else None


def _resize(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    img = Image.fromarray(arr.astype(np.float32), mode="F")
    return np.asarray(img.resize((w, h), Image.Resampling.BILINEAR), dtype=np.float32)


def _window_sums(img: np.ndarray, h: int, w: int) -> np.ndarray:
    ii = np.pad(img, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    return ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]


def _template(inner: Signature, index: int, w: int, h: int, fh: int, fw: int) -> tuple[np.ndarray, float] | None:
    """O recorte `index` de `inner` em `w`x`h`, como desenho sem média: (transformada no tamanho `fh`x`fw`, norma).
    None quando ele fica liso demais nesse tamanho."""
    key = (index, w, h, fh, fw)
    if key not in inner.templates:
        tpl = _resize(inner.crops[index][0], w, h)
        if float(tpl.std()) < MIN_STD:
            inner.templates[key] = None
        else:
            t = _detail(tpl)
            t = t - t.mean()
            tn = float(np.sqrt((t * t).sum()))
            inner.templates[key] = (np.conj(np.fft.rfft2(t, (fh, fw))), tn) if tn > 1e-6 else None
    got = inner.templates[key]
    return got


def _ncc_max(outer: Signature, inner: Signature, index: int, w: int, h: int) -> float:
    """A maior correlação normalizada do recorte `index` de `inner` em `w`x`h` em qualquer posição dentro do `detail`
    de `outer` (posições inteiras). A transformada usa o dobro do tamanho da imagem (o modelo nunca é maior que
    ela): a da imagem e a do modelo ficam em cache."""
    big = outer.detail
    H, W = big.shape
    if h > H or w > W:
        return -1.0
    fh, fw = 2 * H, 2 * W
    tpl = _template(inner, index, w, h, fh, fw)
    if tpl is None:
        return -1.0
    spec_t, tn = tpl
    spec = outer.spectrum.get((fh, fw))
    if spec is None:
        spec = outer.spectrum[(fh, fw)] = np.fft.rfft2(big, (fh, fw))
    corr = np.fft.irfft2(spec * spec_t, (fh, fw))[:H - h + 1, :W - w + 1]
    n = h * w
    s1, s2 = outer.sums(h, w)
    var = np.maximum(s2 - s1 * s1 / n, 0.0)
    den = np.sqrt(var) * tn
    # A janela tem de ter desenho comparável ao do modelo: a quase lisa (o preto em volta do logo) dá correlação
    # alta com qualquer coisa que tenha um traço no mesmo lugar.
    ok = var >= (WINDOW_DETAIL * tn) ** 2
    if not ok.any():
        return -1.0
    return float(np.max(np.where(ok, corr / np.where(ok, den, 1.0), -1.0)))


def contains_score(outer: Signature, inner: Signature) -> float:
    """O quanto `inner` aparece dentro de `outer` (inteira ou o miolo), de -1 a 1: na maior escala em que ela cabe
    e, na captura de página, também nas menores, até ocupar `MIN_FRACTION` da largura ou da altura da outra."""
    H, W = outer.detail.shape
    best = -1.0
    for index, (crop, multi) in enumerate(inner.crops):
        ch, cw = crop.shape
        fit = min(W / cw, H / ch)  # a maior escala em que a imagem inteira cabe na outra
        for k in range(SCALES if outer.page or multi else 1):
            scale = fit * (1.0 - (1.0 - MIN_FRACTION) * k / max(1, SCALES - 1))
            w, h = round(cw * scale), round(ch * scale)
            if min(w, h) < MIN_TPL or (w < MIN_FRACTION * W and h < MIN_FRACTION * H):
                continue
            best = max(best, _ncc_max(outer, inner, index, w, h))
            if best >= SAME_LOOK:
                return best
    return best


def match_score(a: Signature | None, b: Signature | None) -> float:
    """A parecença das duas imagens, nos dois sentidos (uma dentro da outra)."""
    if a is None or b is None:
        return -1.0
    first = contains_score(a, b)
    if first >= SAME_LOOK:
        return first
    return max(first, contains_score(b, a))


def same_look(a: Signature | None, b: Signature | None) -> bool:
    return match_score(a, b) >= SAME_LOOK


def signature_file(path: Path, *, page: bool = False) -> Signature | None:
    """A assinatura do arquivo de imagem (ou do pôster): None quando não abre ou é lisa demais."""
    try:
        with Image.open(path) as img:
            img.seek(0)
            return signature(img, page=page)
    except Exception:  # noqa: BLE001 - arquivo ruim, vídeo, imagem gigante: sem assinatura (conta só pelo item)
        return None


def _load_data_file(rel: str, page: bool) -> Signature | None:
    from studio.config import get_settings

    path = Path(rel)
    if not path.is_absolute():
        path = get_settings().data_dir / rel
    return signature_file(path, page=page) if path.is_file() else None


INDEX_BUDGET_S = 60.0
"""Tempo máximo de comparação por índice (um anexo, uma edição): passou disso, a imagem nova conta só pelo item e
pelo arquivo, como antes (cada comparação leva uns 10 ms; 60 s dão conta de uns 100 itens distintos na tela)."""


@dataclass
class LookIndex:
    """Grupos de imagens parecidas de um vídeo. `token(path)` dá a marca do grupo da imagem (`look:<n>`), ou None
    quando ela não tem assinatura (não abre, é lisa demais). `load(path, page)` abre a assinatura; o padrão lê o
    arquivo relativo à pasta de dados. Cada imagem nova é comparada com a primeira de cada grupo (uma vez: cache por
    caminho); a parecença não encadeia, para um detalhe em comum não juntar imagens diferentes."""

    load: Callable[[str, bool], Signature | None] = _load_data_file
    budget_s: float = INDEX_BUDGET_S
    _sigs: dict[str, Signature | None] = field(default_factory=dict)
    _group: dict[str, int] = field(default_factory=dict)
    _members: list[list[Signature]] = field(default_factory=list)
    _spent: float = 0.0

    def token(self, path: str | None, *, page: bool = False) -> str | None:
        if not path:
            return None
        if path in self._group:
            return f"look:{self._group[path]}"
        if self._spent > self.budget_s:
            return None  # acervo enorme: a imagem nova conta só pelo item e pelo arquivo
        started = time.perf_counter()
        try:
            return self._token(path, page)
        finally:
            self._spent += time.perf_counter() - started

    def _token(self, path: str, page: bool) -> str | None:
        if path not in self._sigs:
            self._sigs[path] = self.load(path, page)
        sig = self._sigs[path]
        if sig is None:
            return None
        for n, members in enumerate(self._members):
            if same_look(sig, members[0]):  # só com a primeira do grupo: parecença não encadeia (A~B~C)
                members.append(sig)
                self._group[path] = n
                return f"look:{n}"
        self._members.append([sig])
        self._group[path] = len(self._members) - 1
        return f"look:{self._group[path]}"

    def media(self, media: MediaRef | None) -> frozenset[str]:
        """A marca da imagem que a mídia anexada mostra (nunca a de trecho de vídeo: planos do mesmo trailer são
        planos diferentes, como em `sources.media_tokens`)."""
        if media is None or media.video or media.kind == "clip" or not media.asset:
            return frozenset()
        token = self.token(media.asset, page=media.kind == "capture" or media.tier == "capture")
        return frozenset({token}) if token else frozenset()

    def item(self, item: PoolItem) -> frozenset[str]:
        """A marca da imagem do item do acervo (a baixada; trecho de vídeo e meme não entram)."""
        if item.media in ("clip", "meme") or not item.path:
            return frozenset()
        token = self.token(item.path, page=item.tier == "capture" or item.media == "capture")
        return frozenset({token}) if token else frozenset()
