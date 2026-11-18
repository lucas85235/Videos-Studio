"""Foto de banco que não serve de imagem de cena pelo formato: moldura, marca d'água ou proporção errada.

Banco de imagens livre (Openverse, Commons, Pexels) traz muita foto "de portfólio": a foto dentro de uma moldura lisa
(faixa preta ou branca em cima e embaixo, ou dos lados) com o título e a assinatura do fotógrafo escritos nela
("RETRO ROCKSTAR", "CARSTEN LINKE PHOTOGRAPHY"), ou a prévia de banco pago com a marca d'água por cima. No vídeo isso
sai como quadro dentro do quadro, com texto alheio na tela. E a foto quadrada ou em pé num vídeo deitado só entra com
as laterais desfocadas (pillarbox), que denuncia o banco.

Três filtros baratos, só para o acervo usar no banco de imagens (material próprio, oficial e captura nunca passam por
aqui: página e app têm faixa lisa de propósito):

- `describes_watermark`: o endereço ou a descrição é de prévia de banco pago (Shutterstock, iStock, Alamy...) ou se
  diz arte de fã (na coleta, antes de baixar);
- `pillarboxed`: a proporção da foto é tão diferente da do vídeo que ela entraria com as laterais (ou o topo)
  desfocados (`clips.needs_blur`);
- `framed`: a imagem baixada tem moldura (ou tarja de cinema). Numa miniatura de 192 px de largura, conta as linhas
  (e colunas) a partir de cada borda em que pelo menos `BAND_COVER` dos pixels (e `BAND_MEAN` na média da faixa) têm
  a cor da borda (a menos de `BAND_TOL` em cada canal), com a borda lisa (quase preta, quase branca ou cinza sem
  cor). É moldura quando duas bordas opostas têm faixa de pelo menos `MIN_BAND` da altura (ou largura), da mesma cor,
  e a faixa termina de uma vez: a linha seguinte tem menos de `EDGE_COVER` da cor (céu escuro, parede e fundo de estúdio vão sumindo aos poucos, a moldura corta seco).
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import numpy as np
from PIL import Image

GRID_WIDTH = 192
BAND_TOL = 18.0
BAND_COVER = 0.7
"""Linha da faixa: a legenda e a assinatura escritas na moldura tiram até uns 20% dos pixels da cor."""
BAND_MEAN = 0.9
"""A faixa inteira, na média: quase toda lisa."""
EDGE_COVER = 0.5
MIN_BAND = 0.05
MAX_BANDS = 0.7
"""Faixas somadas acima disso: a foto é quase toda lisa (céu, parede), não é moldura."""
SAME_COLOR = 24.0

FRAMED = "moldura lisa em volta da foto"
WATERMARK = "marca d'água de banco"
FAN_ART = "arte de fã"
PILLARBOX = "proporção que pede laterais desfocadas"

# Bancos pagos cuja prévia gratuita vem com a marca d'água por cima.
_WATERMARK_HOSTS = re.compile(
    r"(shutterstock|istockphoto|gettyimages|alamy|dreamstime|123rf|depositphotos|stock\.adobe|bigstockphoto"
    r"|canstockphoto|vectorstock|fotolia|pond5|agefotostock|stocksy)\.", re.I)
# Texto já dobrado (minúsculas, sem acento). "fan" sozinho não: é a ventoinha do PC ("glowing fans").
_WORDS = re.compile(
    r"\b(watermark(ed)?|marca d agua|fan ?(art|made|cover|edit|concept|poster)s?|fanart|fan ?fiction"
    r"|stock preview|preview watermark)\b")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return " ".join("".join(ch if ch.isalnum() else " " for ch in text if not unicodedata.combining(ch)).split())


def describes_watermark(desc: str | None, *urls: str | None) -> str | None:
    """`WATERMARK` quando um endereço é de banco pago com prévia marcada ou a descrição fala em marca d'água; `FAN_ART`
    quando a descrição se diz arte de fã ("GTA 6 Fan Cover"); senão None."""
    if any(_WATERMARK_HOSTS.search(u or "") for u in urls):
        return WATERMARK
    found = _WORDS.search(_fold(desc or ""))
    if found is None:
        return None
    return WATERMARK if found.group(1).startswith(("watermark", "marca", "stock", "preview")) else FAN_ART


def pillarboxed(src_w: int, src_h: int, width: int, height: int) -> str | None:
    """`PILLARBOX` quando a foto de `src_w`x`src_h` só entra no quadro `width`x`height` com o fundo desfocado."""
    from studio.media.clips import needs_blur

    return PILLARBOX if needs_blur(int(src_w or 0), int(src_h or 0), int(width or 0), int(height or 0)) else None


def _band(lines: np.ndarray) -> tuple[int, np.ndarray | None]:
    """Quantas linhas, a partir da primeira de `lines` (linhas x pixels x RGB), têm a cor lisa da borda. Devolve
    (tamanho, cor), ou (0, None) quando a borda não é lisa ou a faixa não corta seco."""
    color = np.median(lines[0], axis=0)
    top, low = float(color.max()), float(color.min())
    if not (top <= 40 or low >= 215 or top - low <= 12):
        return 0, None  # borda colorida: é a foto, não a moldura
    cover = (np.abs(lines - color).max(axis=2) < BAND_TOL).mean(axis=1)
    n = 0
    while n < len(cover) and cover[n] >= BAND_COVER:
        n += 1
    if n == 0 or n >= len(cover) or cover[n] >= EDGE_COVER or float(cover[:n].mean()) < BAND_MEAN:
        return 0, None
    return n, color


def framed(image: Image.Image) -> str | None:
    """`FRAMED` quando a imagem tem moldura lisa em duas bordas opostas (ver o critério no topo do módulo)."""
    rgb = image.convert("RGB")
    rows = max(8, round(GRID_WIDTH * rgb.height / max(1, rgb.width)))
    px = np.asarray(rgb.resize((GRID_WIDTH, rows), Image.Resampling.BOX), dtype=np.float32)
    for grid in (px, px.transpose(1, 0, 2)):
        size = grid.shape[0]
        a, ca = _band(grid)
        b, cb = _band(grid[::-1])
        if ca is None or cb is None or float(np.abs(ca - cb).max()) >= SAME_COLOR:
            continue
        if min(a, b) >= MIN_BAND * size and a + b <= MAX_BANDS * size:
            return FRAMED
    return None


def framed_file(path: Path) -> str | None:
    """`framed` de um arquivo; arquivo ilegível não tem veredito (quem anexa já recusa por outro motivo)."""
    try:
        with Image.open(path) as im:
            return framed(im)
    except Exception:  # noqa: BLE001 - imagem ruim ou gigante: sem veredito
        return None
