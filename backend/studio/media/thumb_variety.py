"""Variedade das thumbs do pacote: o que cada thumb usa (fundo, figura, pose, template, cor e lado do texto), a
contagem do que o canal já usou e as escolhas que fogem da repetição (cor do texto por contraste e por novidade).

Puro (sem banco): o histórico do canal vem de `workers/visual/thumb_history.py`; o pós-processamento do pacote
(`authoring._normalize_candidates`) e a checagem das prévias (`tasks.render_package_thumbs`) usam daqui.
"""

from __future__ import annotations

import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image

from studio.core.project.document import ThumbLayers
from studio.core.styles.schema import DEFAULT_THUMB_PALETTE

TEXT_COLORS = ("yellow", "white", "red", "cyan", "lime", "orange")
"""Paleta de alto contraste do texto (com o contorno escuro do compositor), na ordem de desempate."""
COLOR_NAMES = {"yellow": "amarelo", "white": "branco", "red": "vermelho", "cyan": "ciano", "lime": "verde-limão",
               "orange": "laranja", "vice": "degradê vice"}
SIDE_NAMES = {"left": "esquerda", "right": "direita", "top": "topo", "center": "centro"}
MIN_TEXT_CONTRAST = 1.3
"""Contraste (WCAG) mínimo da cor do texto contra a média do fundo atrás dele: abaixo disso a cor nem entra (branco
no céu claro, limão no branco)."""
GOOD_TEXT_CONTRAST = 3.0
"""Acima disso o contraste já basta (o compositor ainda põe contorno escuro e halo); abaixo, a cor perde pontos."""
CONTRAST_WEIGHT = 1.2
BATCH_WEIGHT = 2.5
"""Repetir dentro do mesmo pacote pesa mais que repetir as thumbs recentes do canal."""
KINDS = ("background", "character", "pose", "template", "fill", "side")
KIND_NAMES = {"background": "fundo", "character": "figura", "pose": "pose", "template": "template",
              "fill": "cor do texto", "side": "lado do texto"}
NEGATIVE_WORDS = {"falso", "falsa", "golpe", "nao", "fake", "mentira", "cuidado", "virus", "perigo", "erro", "pare",
                  "evite", "nunca", "roubo", "rouba", "malware", "bloqueado", "cancelado", "adiado", "boato"}
"""Texto da thumb com uma destas palavras: a prova leva um X vermelho; nos outros casos, um círculo."""
CARD_TEMPLATES = ("prova", "objeto")


def _fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()


def mark_for(words: str) -> str:
    """X para a prova de algo falso/perigoso ("FALSO", "GOLPE", "NÃO"); círculo para destacar o resto."""
    tokens = {t.strip("?!.,:;") for t in _fold(words).split()}
    return "x" if tokens & NEGATIVE_WORDS else "circle"


def bg_key(layers: ThumbLayers) -> str | None:
    """O fundo como chave estável: o id do quadro da biblioteca, ou `media:<arquivo>` da mídia do projeto."""
    bg = layers.background
    if bg.kind == "gradient" or not bg.ref:
        return None
    ref = bg.ref.removeprefix("frame:")
    if bg.kind in ("media", "upload"):
        return f"media:{Path(ref.split(':', 1)[-1]).name}"
    return ref


def side_of(layers: ThumbLayers) -> str | None:
    return layers.text.pos if layers.text and layers.text.words.strip() else None


def features(layers: ThumbLayers) -> dict[str, list[str]]:
    """O que a thumb usa, por tipo (listas: uma thumb pode ter duas figuras)."""
    out: dict[str, list[str]] = {k: [] for k in KINDS}
    key = bg_key(layers)
    if key:
        out["background"].append(key)
    out["character"] = [c.asset for c in layers.characters if not c.asset.startswith(("logo:", "object:"))]
    if layers.presenter is not None:
        out["pose"].append(layers.presenter.pose)
    out["template"].append(layers.template)
    if layers.text and layers.text.words.strip():
        out["fill"].append(layers.text.fill)
    side = side_of(layers)
    if side:
        out["side"].append(side)
    return out


@dataclass
class Usage:
    """Quantas vezes cada fundo, figura, pose, template, cor e lado apareceram (com peso)."""

    counts: dict[str, Counter[str]] = field(default_factory=lambda: {k: Counter() for k in KINDS})
    total: float = 0.0

    def add(self, feats: dict[str, Iterable[str]], weight: float = 1.0) -> None:
        for kind, values in feats.items():
            for v in values:
                self.counts.setdefault(kind, Counter())[v] += weight  # type: ignore[assignment]
        self.total += weight

    def add_layers(self, layers: ThumbLayers, weight: float = 1.0) -> None:
        self.add(features(layers), weight)

    def n(self, kind: str, value: str | None) -> float:
        return float(self.counts.get(kind, Counter()).get(value, 0)) if value else 0.0

    def top(self, kind: str, k: int = 12) -> list[tuple[str, float]]:
        return [(v, round(float(c), 1)) for v, c in self.counts.get(kind, Counter()).most_common(k) if c > 0]

    def prompt_line(self) -> str:
        """Resumo para o pedido do LLM ("fundos: t1-neon (3), ..."): tudo o que foi usado (até 12 por tipo), não só
    os mais usados: o fundo que apareceu uma vez também é repetição para quem viu."""
        parts = []
        for kind, label in (("background", "fundos"), ("character", "figuras"), ("pose", "poses do avatar"),
                            ("template", "templates"), ("fill", "cor do texto"), ("side", "lado do texto")):
            top = self.top(kind)
            if top:
                names = COLOR_NAMES if kind == "fill" else SIDE_NAMES if kind == "side" else {}
                parts.append(f"{label}: " + ", ".join(f"{names.get(v, v)} ({c:g})" for v, c in top))
        return "; ".join(parts)


def penalty(kind: str, value: str | None, recent: Usage | None, batch: Usage | None) -> float:
    return (recent.n(kind, value) if recent else 0.0) + BATCH_WEIGHT * (batch.n(kind, value) if batch else 0.0)


def least_used(kind: str, options: Sequence[str], recent: Usage | None, batch: Usage | None,
               turn: int = 0) -> str | None:
    """A opção menos usada (canal + pacote); no empate, a ordem de `options` girada por `turn`."""
    if not options:
        return None
    k = turn % len(options)
    ordered = list(options[k:]) + list(options[:k])
    return min(ordered, key=lambda v: penalty(kind, v, recent, batch))


REPEAT_MIN = {"background": 1.0, "character": 1.0, "pose": 1.5, "template": 8.0, "fill": 8.0}
"""Uso recente (com peso) a partir do qual o aviso "repete as thumbs recentes" cita aquele item. Fundo e figura
são o que o olho reconhece: bastam 1 escolhida ou 2 vistas. Template e cor do texto são poucos e se repetem de
propósito (o estilo do canal): só citados quando viram regra."""


def repeated(layers: ThumbLayers, recent: Usage | None, *, min_count: dict[str, float] | None = None) -> list[str]:
    """O que esta thumb repete das recentes do canal (a partir de `REPEAT_MIN` por tipo), em português."""
    if recent is None:
        return []
    limits = {**REPEAT_MIN, **(min_count or {})}
    out = []
    for kind, values in features(layers).items():
        if kind not in limits:
            continue
        for v in values:
            c = recent.n(kind, v)
            if c >= limits[kind]:
                name = COLOR_NAMES.get(v, v) if kind == "fill" else v
                out.append(f"{KIND_NAMES[kind]} {name} ({c:g}x)")
    return out


# --- Cor do texto ----------------------------------------------------------------------------------------------------
def _rgb(value: str) -> tuple[int, int, int]:
    s = value.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def color_rgb(fill: str, palette: dict[str, str] | None = None) -> tuple[int, int, int]:
    pal = {**DEFAULT_THUMB_PALETTE, **(palette or {})}
    if fill == "vice":
        fill = "pink"
    return _rgb(pal.get(fill) or "#FFD400")


@lru_cache(maxsize=256)
def _region_cached(path: str, mtime: float, box: tuple[float, float, float, float]) -> tuple[float, float, float]:
    with Image.open(path) as im:
        im.draft("RGB", (320, 180))
        small = im.convert("RGB").resize((160, 90), Image.Resampling.BOX)
    x0, y0, x1, y1 = box
    crop = small.crop((round(x0 * 160), round(y0 * 90), max(round(x0 * 160) + 1, round(x1 * 160)),
                       max(round(y0 * 90) + 1, round(y1 * 90))))
    px = list(crop.getdata())
    n = max(1, len(px))
    return (sum(p[0] for p in px) / n, sum(p[1] for p in px) / n, sum(p[2] for p in px) / n)


def region_rgb(path: Path | None, box: tuple[float, float, float, float]) -> tuple[float, float, float] | None:
    """Cor média da imagem na caixa (x0, y0, x1, y1 em fração): o fundo atrás do texto, para escolher a cor."""
    if path is None:
        return None
    try:
        return _region_cached(str(path), Path(path).stat().st_mtime, tuple(round(v, 3) for v in box))
    except (OSError, ValueError):
        return None


HUE_WEIGHT = 1.0
"""Custo extra da cor do mesmo matiz do fundo (ciano no céu azul, laranja no pôr do sol): o contraste de luminância
pode até bastar, mas a palavra não destaca."""
HUE_NEAR = 35.0


def _hsv(rgb: Sequence[float]) -> tuple[float, float, float]:
    import colorsys

    h, s, v = colorsys.rgb_to_hsv(*(max(0.0, min(255.0, float(c))) / 255 for c in rgb[:3]))
    return h * 360, s, v


def same_hue(fill_rgb: Sequence[float], bg_rgb: Sequence[float] | None) -> bool:
    """A cor do texto tem o matiz do fundo atrás dele (os dois saturados): azul/ciano no céu, laranja no pôr do sol."""
    if bg_rgb is None:
        return False
    fh, fs, fv = _hsv(fill_rgb)
    bh, bs, bv = _hsv(bg_rgb)
    if fs < 0.25 or bs < 0.18 or bv < 0.2:
        return False
    d = abs(fh - bh) % 360
    return min(d, 360 - d) < HUE_NEAR


def pick_fill(bg_rgb: Sequence[float] | None, recent: Usage | None, batch: Usage | None, *, turn: int = 0,
              palette: dict[str, str] | None = None, options: Sequence[str] = TEXT_COLORS) -> str:
    """A cor do texto: entre as cores com contraste de pelo menos `MIN_TEXT_CONTRAST` com o fundo atrás do texto,
    a de menor custo = uso (canal + pacote) + falta de contraste (abaixo de `GOOD_TEXT_CONTRAST`) + o mesmo matiz
    do fundo (`same_hue`); no empate, a de mais contraste. Sem a cor do fundo, só a novidade decide."""
    from studio.media.thumb_check import contrast_ratio

    def contrast(c: str) -> float:
        return contrast_ratio(color_rgb(c, palette), bg_rgb) if bg_rgb is not None else 4.5

    ok = [c for c in options if contrast(c) >= MIN_TEXT_CONTRAST] or sorted(options, key=contrast, reverse=True)[:2]
    k = turn % len(ok)
    ordered = ok[k:] + ok[:k]

    def cost(c: str) -> float:
        lack = max(0.0, GOOD_TEXT_CONTRAST - contrast(c)) * CONTRAST_WEIGHT
        hue = HUE_WEIGHT if same_hue(color_rgb(c, palette), bg_rgb) else 0.0
        return round(penalty("fill", c, recent, batch) + lack + hue, 2)

    return min(ordered, key=lambda c: (cost(c), -round(contrast(c), 1)))
