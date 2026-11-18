"""Templates das thumbs por camadas: geometria em fração do quadro e as camadas padrão de cada molde (seção B.3).

Cada template é um `dict` com as posições (frações do canvas), o enquadramento do fundo e as zonas proibidas. O
compositor (`thumb_compose.render`) lê daqui; o pacote (Fase 3) monta as camadas com `default_layers`.

Ordem das camadas, comum a todos: fundo → VI gigante → personagens/objetos → avatar → texto/pílula/cartões → seta →
logo.
"""

from __future__ import annotations

from typing import Any

from studio.core.project.document import (
    ArrowLayer,
    BgLayer,
    CardLayer,
    CutoutLayer,
    PresenterThumbLayer,
    TextLayer,
    ThumbLayers,
)

CANVAS = (1280, 720)
"""Canvas lógico do longo (a exportação escala para 3840×2160; tudo aqui é fração)."""
SHORT_CANVAS = (1080, 1920)
EXPORT_LONG = (3840, 2160)

# Zonas proibidas (x0, y0, x1, y1 em fração): o selo de duração no canto inferior direito e as faixas de cima/baixo.
FORBIDDEN_LONG: dict[str, tuple[float, float, float, float]] = {
    "selo": (1 - 0.22, 1 - 0.18, 1.0, 1.0),
    "faixa_topo": (0.0, 0.0, 1.0, 0.06),
    "faixa_base": (0.0, 0.94, 1.0, 1.0),
}
# Short: o texto fica no centro seguro (15% a 70% da altura); cantos e base ficam livres para a interface.
FORBIDDEN_SHORT: dict[str, tuple[float, float, float, float]] = {
    "topo": (0.0, 0.0, 1.0, 0.15),
    "base": (0.0, 0.70, 1.0, 1.0),
}

HEAD_FRACTION = 0.18
"""Altura da cabeça nas poses do avatar (corpo até a cintura), em fração da altura da pose recortada."""

TEMPLATES: dict[str, dict[str, Any]] = {
    # Molde JTM 187x: o rosto do personagem (dentro do quadro, sem recorte) no terço direito e 1 palavra gigante.
    "heroi-palavra": {
        "bg": {"target_x": 0.70, "target_y": 0.40, "zoom": 1.5},
        "shade": {"side": "left", "width": 0.50, "alpha": 0.62},
        "text": {"left": (0.035, 0.08, 0.47, 0.66), "top": (0.035, 0.075, 0.58, 0.40), "center": (0.10, 0.20, 0.80, 0.55),
                 "size": 0.30, "sub": 0.62},
        "presenter": {"side": "left", "crop": "meio", "h_max": 0.30},
        "logo": {"anchor": "top-right", "w": 0.12},
        "figure_min_face": 0.30,
        "face_target": 0.50,
    },
    # Molde LuskasGTA 22x: avatar grande em busto à direita, objeto do jogo à esquerda, 0 a 2 palavras.
    "avatar-reage": {
        "bg": {"target_x": 0.32, "target_y": 0.45, "zoom": 1.15},
        "shade": {"side": "left", "width": 0.55, "alpha": 0.45},
        "text": {"left": (0.035, 0.075, 0.50, 0.40), "top": (0.035, 0.075, 0.55, 0.36), "center": (0.05, 0.10, 0.52, 0.45),
                 "size": 0.27, "sub": 0.70},
        "presenter": {"side": "right", "crop": "busto", "h": 1.5, "head_x": 0.765},
        "object": {"x": 0.25, "y": 0.92, "h": 0.42},
        "logo": {"anchor": "none", "w": 0.12},
        "figure_min_face": 0.25,
    },
    # Molde RsZ_ 57x: quadro aéreo, VI gigante atrás de 1-2 palavras amarelas.
    "lugar-mapa": {
        "bg": {"target_x": 0.5, "target_y": 0.5, "zoom": 1.1},
        "shade": {"side": "left", "width": 0.60, "alpha": 0.35},
        "text": {"left": (0.05, 0.16, 0.55, 0.66), "top": (0.05, 0.08, 0.70, 0.40), "center": (0.12, 0.20, 0.76, 0.58),
                 "size": 0.32, "sub": 0.62},
        "big_vi": {"h": 0.86, "alpha": 0.92},
        "presenter": {"side": "right", "crop": "busto", "h": 1.45, "head_x": 0.79},
        "logo": {"anchor": "top-right", "w": 0.13},
        "figure_min_face": 0.25,
    },
    # Molde BreeZe/9TR2: número gigante à esquerda e o avatar apontando cortado na borda direita. Sem cartões (o
    # padrão do pacote: texto total de até 3 palavras), o número + 1 palavra ("7 DIAS") usa a caixa `solo`, mais larga;
    # com cartões (só quando o conceito depende deles), até 3 ao lado da coluna estreita.
    "numero-lista": {
        "bg": {"target_x": 0.5, "target_y": 0.45, "zoom": 1.1},
        "shade": {"side": "left", "width": 0.62, "alpha": 0.55},
        "text": {"left": (0.03, 0.07, 0.30, 0.86), "top": (0.03, 0.07, 0.30, 0.86), "center": (0.03, 0.07, 0.30, 0.86),
                 "solo": (0.035, 0.08, 0.46, 0.84), "solo_sub": 0.42, "size": 0.60, "sub": 0.30, "number": True},
        "cards": {"x": 0.345, "w": 0.285, "y0": 0.21, "y1": 0.90, "h": 0.17},
        "presenter": {"side": "right", "crop": "busto", "h": 1.45, "head_x": 0.815},
        "logo": {"anchor": "top-mid", "w": 0.115, "x": 0.39},
        "figure_min_face": 0.25,
    },
    # 5×6 / antes e depois: diagonal, esquerda em sépia e direita saturada, logos V e VI.
    "versus": {
        "bg": {"target_x": 0.62, "target_y": 0.42, "zoom": 1.1},
        "left_bg": {"target_x": 0.35, "target_y": 0.42, "zoom": 1.1},
        "split": (0.56, 0.44),
        # o texto "left" fica abaixo do logo do GTA (canto de cima à esquerda, até ~28% da altura)
        "text": {"left": (0.035, 0.31, 0.40, 0.30), "top": (0.25, 0.075, 0.50, 0.26), "center": (0.30, 0.36, 0.40, 0.30),
                 "size": 0.24, "sub": 0.7},
        "presenter": {"side": "right", "crop": "busto", "h": 1.35, "head_x": 0.80},
        "logo": {"anchor": "versus", "w": 0.15},
        "figure_min_face": 0.25,
    },
    # Prova: o avatar em busto grande à direita com a emoção (o rosto é o que lê a 168 px), 1 palavra grande no alto
    # da esquerda e, embaixo dela, a captura do próprio vídeo (matéria, site falso, janela do instalador) como APOIO:
    # um cartão menor e mais inclinado com a marcação vermelha (X ou círculo, fora da manchete). O print grande (meia
    # thumb de página branca) virava ruído branco a 168 px, com avatar minúsculo e cara de recorte de jornal. O fundo
    # é um quadro do jogo escurecido (`ThumbLayers.backdrop`), o próprio quadro desfocado (quando ele é colorido) ou
    # o degradê do canal. `presenter.lock`: o molde manda no lugar e no tamanho do avatar (candidatos antigos, que
    # gravaram o avatar pequeno de corpo, saem no desenho novo).
    "prova": {
        "bg": {"target_x": 0.5, "target_y": 0.42, "zoom": 1.0},
        "backdrop": "vice",
        "card": {"box": (0.03, 0.42, 0.47, 0.48), "tilt": -2.5, "zoom": 1.0, "border": 8},
        "mark": "x",
        "text": {"left": (0.035, 0.07, 0.56, 0.33), "top": (0.035, 0.07, 0.56, 0.33),
                 "center": (0.035, 0.07, 0.56, 0.33), "right": (0.035, 0.07, 0.56, 0.33), "size": 0.30,
                 "sub": 0.62},  # não espelha: a palavra à direita cairia em cima do avatar
        "presenter": {"side": "right", "crop": "busto", "h": 1.4, "head_x": 0.79, "lock": True},
        "logo": {"anchor": "none", "w": 0.12},
        "figure_min_face": 0.0,
    },
    # Objeto: o item-chave em close (recorte de objeto da biblioteca, ou um trecho ampliado da mídia do projeto num
    # cartão reto) à direita e 1 palavra à esquerda. Sem avatar: o objeto é o foco.
    "objeto": {
        "bg": {"target_x": 0.5, "target_y": 0.45, "zoom": 1.1},
        "backdrop": "noite",
        "card": {"box": (0.42, 0.09, 0.55, 0.78), "tilt": 0.0, "zoom": 1.8, "border": 8},
        "shade": {"side": "left", "width": 0.55, "alpha": 0.5},
        "object": {"x": 0.70, "y": 0.96, "h": 0.86},
        "text": {"left": (0.035, 0.10, 0.37, 0.62), "top": (0.035, 0.075, 0.55, 0.36),
                 "center": (0.035, 0.10, 0.37, 0.62), "size": 0.30, "sub": 0.62},
        "presenter": {"side": "right", "crop": "busto", "h": 1.3, "head_x": 0.82},
        "logo": {"anchor": "top-right", "w": 0.12},
        "figure_min_face": 0.0,
    },
    # Short (1080×1920): caixa de 2 a 3 palavras no centro seguro (15% a 70% da altura).
    "livre": {
        "bg": {"target_x": 0.5, "target_y": 0.42, "zoom": 1.0},
        "band": 0.16,
        "text": {"left": (0.06, 0.475, 0.88, 0.215), "top": (0.06, 0.475, 0.88, 0.215),
                 "center": (0.06, 0.475, 0.88, 0.215), "size": 0.10, "sub": 0.75, "box": True},
        "presenter": {"side": "right", "crop": "busto", "h": 0.55, "head_x": 0.70, "top": 0.70},
        "logo": {"anchor": "short-top", "w": 0.24},
        "figure_min_face": 0.0,
    },
}



def _mirror_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return (round(1 - x - w, 4), y, w, h)


# Texto à direita (`pos="right"`): o espelho da coluna da esquerda; a figura e o avatar vão para a esquerda.
def _clear_badge_box(box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    """Caixa do lado direito que desceria até o selo de duração (canto de baixo à direita): termina antes dele."""
    x, y, w, h = box
    sx, sy = FORBIDDEN_LONG["selo"][:2]
    if x + w > sx and y + h > sy - 0.02:
        h = round(sy - 0.02 - y, 4)
    return (x, y, w, h)


for _name, _tpl in TEMPLATES.items():
    if _name == "livre":
        continue
    _tpl["text"].setdefault("right", _clear_badge_box(_mirror_box(_tpl["text"]["left"])))
    if "solo" in _tpl["text"]:
        _tpl["text"].setdefault("solo_right", _clear_badge_box(_mirror_box(_tpl["text"]["solo"])))

MIRRORABLE = ("heroi-palavra", "avatar-reage", "numero-lista", "lugar-mapa", "objeto")
"""Templates que aceitam o texto à direita (o molde inteiro espelhado)."""


# Emoções → pose (mapa do canais, para o pacote escolher a expressão pelo conceito).
POSE_BY_EMOTION: dict[str, str] = {
    "choque": "surpreso", "surpresa": "surpreso", "erro": "mao-na-testa", "vergonha": "mao-na-testa",
    "detalhe": "lupa", "escondido": "lupa", "investigando": "lupa", "explicando": "apontando", "lista": "apontando",
    "comemoracao": "comemorando", "animado": "comemorando", "segredo": "segredo", "spoiler": "segredo",
    "rumor": "segredo", "dinheiro": "dinheiro", "preco": "dinheiro", "custo": "dinheiro", "gameplay": "controle",
    "nervoso": "suando", "prazo": "suando", "raiva": "bravo", "golpe": "bravo", "feliz": "feliz",
}


def spec(template: str) -> dict[str, Any]:
    return TEMPLATES.get(template) or TEMPLATES["heroi-palavra"]


def forbidden_zones(size: tuple[int, int]) -> dict[str, tuple[float, float, float, float]]:
    w, h = size
    return FORBIDDEN_SHORT if h > w else FORBIDDEN_LONG


def default_layers(template: str, hints: dict[str, Any] | None = None) -> ThumbLayers:
    """Camadas padrão de um template a partir de dicas simples (o que o pacote ou a tela já sabem):

    - `words` (texto principal; o texto total visível, com pílula e cartões, fica em ≤ 3 palavras), `fill`, `pos`,
      `size`, `pill` (desligada por padrão), `strike`;
    - `background` (ref de fundo: `frame:<id>`, `frame:<video>@<t>`, `upload:...`), `focus_x`, `focus_y` (só para
      o enquadramento; não fica no modelo), `zoom`, `kind`;
    - `presenter` (bool) e `pose`, `side`, `flip`;
    - `cards` (lista de rótulos ou de {asset, label}; nenhum por padrão), `object` (ref do objeto do jogo no `avatar-reage`),
      `left` (ref do lado esquerdo no `versus`), `characters` (lista de {asset, x, y, h, flip}), `big_vi`, `logo`,
      `arrow` (bool ou {from_x, from_y, to_x, to_y}).
    Valores ausentes caem no padrão do template."""
    hints = dict(hints or {})
    tpl_name = template if template in TEMPLATES else "heroi-palavra"
    tpl = spec(tpl_name)
    bg_ref = hints.get("background")
    kind = hints.get("kind") or ("frame" if bg_ref and str(bg_ref).startswith("frame:") else
                                 "upload" if bg_ref and str(bg_ref).startswith("upload:") else
                                 "media" if bg_ref and str(bg_ref).startswith("media:") else
                                 "frame" if bg_ref else "gradient")
    background = BgLayer(
        kind=kind, ref=bg_ref or (hints.get("gradient") or "vice"),
        zoom=float(hints.get("zoom") or tpl["bg"].get("zoom", 1.0)),
        focus_x=float(hints.get("focus_x", 0.5)),
        blur=int(hints.get("blur", 0)), darken=float(hints.get("darken", 0.0)),
    )
    words = " ".join(str(hints.get("words") or "").split())
    text = None
    if words:
        default_pos = "left" if tpl_name in ("numero-lista", "avatar-reage", "lugar-mapa", "prova", "objeto") else "top"
        if tpl_name == "livre":
            default_pos = "center"
        text = TextLayer(words=words.upper(), fill=hints.get("fill", "yellow"), pos=hints.get("pos", default_pos),
                         size=float(hints.get("size") or tpl["text"]["size"]))

    presenter = None
    if hints.get("presenter") and tpl_name != "versus" or (tpl_name == "avatar-reage" and hints.get("presenter", True)):
        pt = tpl.get("presenter", {})
        pose = hints.get("pose") or ("apontando" if tpl_name == "numero-lista" else "surpreso")
        side = hints.get("side") or pt.get("side", "right")
        flip = hints.get("flip")
        if flip is None:
            # `apontando` aponta para a direita de quem vê: à direita do quadro, vira para apontar para o texto.
            flip = pose == "apontando" and side == "right"
        h = float(hints.get("presenter_h") or pt.get("h") or pt.get("h_max") or 1.5)
        if "h_max" in pt:
            h = min(h, pt["h_max"])
        presenter = PresenterThumbLayer(pose=pose, side=side, crop=pt.get("crop", "busto"), h=h,
                                        outline_px=int(hints.get("outline_px", 10)), flip=bool(flip))

    characters = [CutoutLayer(**c) for c in hints.get("characters") or []]
    if tpl_name == "avatar-reage" and hints.get("object", "logo:lockup"):
        ob = tpl["object"]
        characters.append(CutoutLayer(asset=hints.get("object", "logo:lockup"), x=ob["x"], y=ob["y"], h=ob["h"]))

    cards: list[CardLayer] = []
    if tpl_name == "numero-lista":
        for c in (hints.get("cards") or [])[:3]:
            cards.append(CardLayer(**c) if isinstance(c, dict) else CardLayer(label=str(c)))
    elif tpl_name == "versus" and hints.get("left"):
        cards.append(CardLayer(asset=hints["left"], label=str(hints.get("left_label", ""))))

    arrow = None
    if hints.get("arrow"):
        arrow = ArrowLayer(**hints["arrow"]) if isinstance(hints["arrow"], dict) else ArrowLayer()

    logo = hints.get("logo")
    if logo not in ("corner", "none"):
        logo = "none" if tpl.get("logo", {}).get("anchor") == "none" else "corner"
    mark = hints.get("mark", tpl.get("mark"))
    return ThumbLayers(
        template=tpl_name, background=background, big_vi=bool(hints.get("big_vi", tpl_name == "lugar-mapa")),
        characters=characters, presenter=presenter, text=text, pill=(hints.get("pill") or None),
        cards=cards, logo=logo, arrow=arrow, strike=bool(hints.get("strike", False)),
        mark=mark if mark in ("x", "circle") else None,
    )


def focus_hints(item: dict[str, Any] | None, template: str | None = None) -> dict[str, Any]:
    """Dicas de enquadramento de um item do thumbkit para somar às `hints` do `default_layers`: o `focus_x` e, quando
    o item tem `face_box` e o template pede rosto grande (`face_target`), o zoom que leva o rosto a esse tamanho
    (entre 1,0 e 2,2). A tarja preta dos trailers é removida pelo compositor; o `focus_y` é lido do manifesto no
    render."""
    if not item:
        return {}
    out: dict[str, Any] = {"focus_x": float(item.get("focus_x", 0.5))}
    target = spec(template).get("face_target") if template else None
    face = item.get("face_box")
    if target and face:
        w, h = (item.get("size") or [16, 9])[:2]
        aspect = w / max(1, h)
        visible_h = min(1.0, aspect / (16 / 9))  # fração da altura que sobra no cover 16:9
        face_h = (face[3] - face[1]) / visible_h
        if face_h > 0:
            out["zoom"] = round(min(2.2, max(1.0, target / face_h)), 3)
    return out
