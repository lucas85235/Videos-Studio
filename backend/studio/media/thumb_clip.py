"""Recorte da manchete para a thumb de prova: 2 ou 3 palavras da própria manchete capturada, grandes e com marca-texto.

A captura inteira num cartão (meia thumb de página branca com quatro linhas de manchete) virava, a 168 px, um
retângulo branco com riscos azuis ilegíveis: a prova não dizia nada e a thumb vivia só da palavra e do rosto. Um
designer de thumb recorta o pedaço que conta ("GTA 6 roubam", "Falso GTA 6") e o mostra em corpo grande, com o
marca-texto na palavra do perigo. Os pixels continuam sendo os da matéria (nada é redesenhado: é a prova real).

Como acha as palavras sem OCR: o acervo guarda o texto da manchete (`desc` do item do acervo, antes do " — ") e a
caixa de cada linha dela na captura (`rects`, medida no DOM na hora da captura). Em cada linha, as palavras são os
blocos de tinta separados por um vão largo (espaço entre palavras ≫ espaço entre letras); a contagem de blocos
casa as palavras do texto com as linhas, em ordem. Qualquer descompasso (contagem que não fecha, linha com letras
largas demais para o texto que caiu nela) devolve None e o compositor fica com o cartão da página inteira.

Puro (Pillow + numpy, sem banco nem arquivos além da imagem recebida).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from studio.media import pose_tone

MARKER = (255, 226, 0)
"""Amarelo do marca-texto (multiplicado sobre a letra: a tinta azul/preta continua legível por cima)."""
WORD_GAP = 0.12
"""Piso do vão entre palavras, em fração da altura da tinta da linha (o espaço de palavra fica perto de 0,2-0,4)."""
WORD_JUMP = 1.6
"""O vão de palavra é pelo menos isto vezes o maior vão de letra da linha (fonte condensada: 4 px contra 13 px)."""
MIN_SCORE = 3.0
"""Abaixo disso o recorte não tem nem palavra de perigo nem nome próprio do tema: fica o cartão da página."""
MAX_WORDS = 3

_STOP = frozenset({
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das", "dos", "e", "em", "no", "na", "nos",
    "nas", "por", "para", "pra", "com", "sem", "que", "se", "ao", "aos", "ou", "mas", "seu", "sua", "seus",
    "suas", "the", "of", "and", "to", "in", "on", "for", "is", "are", "but", "with", "by", "at", "an", "its",
    "it", "be", "this", "that", "from", "or", "el", "la", "los", "las", "y", "en", "del", "con",
})
# palavras de perigo em inglês (as matérias de fora): o `pose_tone` cobre o português e parte do inglês
_DANGER_EN = ("steal", "stole", "stolen", "infostealer", "danger", "warning", "warn", "banned", "breach", "leak",
              "infected", "attack")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", (text or "").lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _bare(word: str) -> str:
    return re.sub(r"[^\w]", "", _fold(word))


def headline_text(desc: str | None) -> str:
    """O texto da manchete no `desc` do item do acervo ("<manchete> — \"<site> – <rótulo>\"")."""
    head = re.split(r"\s+[—–]\s+[\"“]", desc or "", maxsplit=1)[0]
    return " ".join(head.replace("…", " ").split())


def is_danger(word: str) -> bool:
    w = _bare(word)
    if not w:
        return False
    return pose_tone.text_tone(w) == pose_tone.ALERTA or any(w.startswith(s) for s in _DANGER_EN)


def is_entity(word: str) -> bool:
    """Nome próprio curto do tema: sigla em caixa alta (GTA, PC, PS5) ou número ("6", "2027")."""
    w = re.sub(r"[^\w]", "", word)
    if not w:
        return False
    letters = re.sub(r"\d", "", w)
    return w.isdigit() or (len(letters) >= 2 and letters.isupper() and len(w) <= 5)


# --- Palavras na imagem --------------------------------------------------------------------------------------------
@dataclass
class Line:
    box: tuple[int, int, int, int]
    """Caixa da tinta da linha na captura (px)."""
    spans: list[tuple[int, int]]
    """x0/x1 (px da captura) de cada bloco de tinta = uma palavra."""
    bg: tuple[int, int, int]
    words: list[str]


def _ink_line(img: Image.Image, rect: Sequence[float]) -> tuple[tuple[int, int, int, int],
                                                                list[tuple[int, int]], tuple[int, int, int]] | None:
    """A tinta de uma linha da manchete (`rect` em fração da captura): caixa justa, blocos de palavra e cor do
    fundo. None quando não há tinta ou a caixa sai da imagem."""
    import numpy as np

    W, H = img.size
    x, y, w, h = (float(v) for v in rect[:4])
    x0, y0 = max(0, round(x * W)), max(0, round(y * H))
    x1, y1 = min(W, round((x + w) * W)), min(H, round((y + h) * H))
    if x1 - x0 < 8 or y1 - y0 < 6:
        return None
    pad = max(2, round((y1 - y0) * 0.15))
    cx0, cx1 = max(0, x0 - pad), min(W, x1 + pad)
    crop = np.asarray(img.crop((cx0, y0, cx1, y1)).convert("RGB"), dtype=np.int16)
    border = np.concatenate([crop[0], crop[-1], crop[:, 0], crop[:, -1]])
    bg = np.median(border, axis=0)
    ink = np.abs(crop - bg).sum(axis=2) > 110
    per_row = ink.sum(axis=1)
    rows = np.nonzero(per_row >= max(1, ink.shape[1] // 400))[0]
    if rows.size == 0:
        return None
    # a faixa de tinta principal: a caixa do DOM às vezes pega o pé da linha de cima (sobretítulo, menu); o pingo
    # do "i" e o acento ficam (vão de poucos px)
    bands: list[list[int]] = [[int(rows[0]), int(rows[0]) + 1]]
    for r in rows[1:]:
        r = int(r)
        if r - bands[-1][1] <= max(2, round(ink.shape[0] * 0.08)):
            bands[-1][1] = r + 1
        else:
            bands.append([r, r + 1])
    ty0, ty1 = max(bands, key=lambda b: int(per_row[b[0]:b[1]].sum()))
    ink_h = ty1 - ty0
    cols = ink[ty0:ty1].sum(axis=0) >= 1
    xs = np.nonzero(cols)[0]
    if xs.size == 0 or ink_h < 4:
        return None
    # blocos de tinta separados por qualquer vão; depois, o vão de palavra é o degrau entre os vãos de letra
    # (muitos, pequenos) e os de palavra (poucos, ~2-4× maiores): o maior salto da lista ordenada de vãos
    blocks: list[tuple[int, int]] = []
    start, last = int(xs[0]), int(xs[0])
    for c in xs[1:]:
        c = int(c)
        if c - last - 1 >= 1:
            blocks.append((start, last + 1))
            start = c
        last = c
    blocks.append((start, last + 1))
    gaps = sorted(b[0] - a[1] for a, b in zip(blocks, blocks[1:], strict=False))
    floor = max(2.0, ink_h * WORD_GAP)
    thr = math.inf
    jump = 0.0
    for lo, hi in zip(gaps, gaps[1:], strict=False):
        if hi >= floor and hi / max(1, lo) >= WORD_JUMP and hi / max(1, lo) > jump:
            jump, thr = hi / max(1, lo), (lo + hi) / 2
    if thr == math.inf and gaps and gaps[0] >= max(floor, ink_h * 0.3):
        thr = gaps[0] - 0.5  # nenhum vão de letra (palavras de uma letra só): todos são de palavra
    spans: list[tuple[int, int]] = [blocks[0]]
    for a, b in blocks[1:]:
        if a - spans[-1][1] > thr:
            spans.append((a, b))
        else:
            spans[-1] = (spans[-1][0], b)
    box = (cx0 + int(xs[0]), y0 + ty0, cx0 + int(xs[-1]) + 1, y0 + ty1)
    return box, [(cx0 + a, cx0 + b) for a, b in spans], tuple(int(v) for v in bg)  # type: ignore[return-value]


def headline_lines(img: Image.Image, text: str, rects: Sequence[Sequence[float]]) -> list[Line] | None:
    """As linhas da manchete com as palavras de cada uma (na ordem do texto) e a caixa de cada palavra. None quando
    a contagem de blocos de tinta não casa com o texto (fonte de vão apertado, captura com outra coisa na caixa)."""
    words = [w for w in text.split() if re.search(r"\w", w)]  # travessão e hífen soltos não são tinta de palavra
    if not words or not rects:
        return None
    measured = []
    for rect in rects:
        got = _ink_line(img, rect)
        if got is None:
            return None
        measured.append(got)
    out: list[Line] = []
    i = 0
    for k, (box, spans, bg) in enumerate(measured):
        last = k == len(measured) - 1
        n = len(spans)
        if i + n > len(words):
            if not last:
                return None
            n = len(words) - i  # texto cortado no fim (o `desc` guarda até ~70 caracteres): só as palavras conhecidas
            if n <= 0:
                break
            spans = spans[:n]
        out.append(Line(box=box, spans=spans, bg=bg, words=words[i:i + n]))
        i += n
    if i < len(words) - 1:  # sobrou texto além de uma palavra: os blocos não eram as palavras
        return None
    # cada palavra cabe na largura do bloco dela (letras por px parecidas na manchete toda): um bloco com duas
    # palavras grudadas e outro com meia palavra dariam a mesma contagem com larguras trocadas
    rates = [len(re.sub(r"\s", "", w)) / max(1, b - a) for ln in out for w, (a, b) in zip(ln.words, ln.spans, strict=True)
             if len(_bare(w)) >= 3]
    if rates:
        rates.sort()
        mid = rates[len(rates) // 2]
        if any(r < mid * 0.55 or r > mid * 1.8 for r in rates):
            return None
    return out


# --- Escolha do trecho ---------------------------------------------------------------------------------------------
@dataclass
class Pick:
    line: int
    first: int
    last: int
    marked: list[int]
    """Índices (na linha) das palavras com marca-texto."""
    score: float

    def words(self, lines: Sequence[Line]) -> list[str]:
        return lines[self.line].words[self.first:self.last + 1]


def _score(words: Sequence[str]) -> tuple[float, list[int]]:
    danger = [i for i, w in enumerate(words) if is_danger(w)]
    entity = [i for i, w in enumerate(words) if is_entity(w)]
    # o nome do tema vale mais com as partes juntas ("GTA 6" diz mais que "PC")
    score = 3.0 * min(2, len(danger)) + (3.0 + min(2, len(entity) - 1) if entity else 0.0)
    if danger and entity:
        score += 2.0
    score += 0.5 * sum(1 for i, w in enumerate(words) if i not in danger and i not in entity
                       and len(_bare(w)) >= 4 and _bare(w) not in _STOP)
    if _bare(words[0]) in _STOP or _bare(words[-1]) in _STOP:
        score -= 3.0
    if len(words) >= 2:
        score += 1.0
    if len(" ".join(words)) > 20:
        score -= 2.0
    if any(re.search(r"[,;.!?]$", w) for w in words[:-1]):
        score -= 1.0  # trecho que atravessa o fim de uma frase
    marked = danger or entity or [max(range(len(words)), key=lambda i: len(_bare(words[i])))]
    return score, marked


def _names(lines: Sequence[Line]) -> list[list[bool]]:
    """Que palavras fazem parte de um nome (sigla, número ou palavra com maiúscula; a 1ª da manchete só quando a
    seguinte também é): o recorte não corta um nome ao meio ("Theft Auto 6" sem o "Grand", "GTA" sem o "6").
    Manchete em Title Case (quase toda palavra com maiúscula) só conta sigla e número."""
    flat = [w for ln in lines for w in ln.words]
    caps = [w for w in flat if len(_bare(w)) >= 3]
    title_case = bool(caps) and sum(1 for w in caps if w[:1].isupper()) >= 0.75 * len(caps)
    named = [is_entity(w) or (not title_case and w[:1].isupper()) for w in flat]
    if named and not is_entity(flat[0]):
        named[0] = len(flat) > 1 and named[1] and not _ends_sentence(flat[0])
    out: list[list[bool]] = []
    k = 0
    for ln in lines:
        out.append(named[k:k + len(ln.words)])
        k += len(ln.words)
    return out


def _ends_sentence(word: str) -> bool:
    return bool(re.search(r"[.!?:;,]$", word))


def _chained(words: Sequence[str], names: Sequence[bool], i: int) -> bool:
    """As palavras `i` e `i + 1` são o mesmo nome (a pontuação no fim da 1ª separa: "Cuidado! Falso")."""
    return names[i] and names[i + 1] and not _ends_sentence(words[i])


def pick(lines: Sequence[Line]) -> Pick | None:
    """O melhor trecho de 1 a `MAX_WORDS` palavras seguidas numa linha: palavra de perigo (roubam, falso, golpe) e
    nome do tema (GTA 6, PC) juntos valem mais; trecho que começa ou acaba em palavra vazia ("de", "com") perde; um
    nome não é cortado ao meio (`_names`). None sem trecho que valha (`MIN_SCORE`)."""
    best: Pick | None = None
    names = _names(lines)
    for li, ln in enumerate(lines):
        n = len(ln.words)
        nm = names[li]
        for a in range(n):
            for b in range(a, min(n, a + MAX_WORDS)):
                if (a > 0 and _chained(ln.words, nm, a - 1)) or (b + 1 < n and _chained(ln.words, nm, b)):
                    continue
                words = ln.words[a:b + 1]
                score, marked = _score(words)
                if a == 0 and b == n - 1:
                    score += 0.5  # a linha inteira: nenhum corte no meio da manchete
                if best is None or score > best.score + 1e-9:
                    best = Pick(li, a, b, [a + i for i in marked], score)
    if best is None or best.score < MIN_SCORE:
        return None
    # o marca-texto vai na palavra de perigo; sem ela, no nome do tema (todas as partes dele)
    ln = lines[best.line]
    danger = [i for i in best.marked if is_danger(ln.words[i])]
    best.marked = danger[-1:] if danger else best.marked
    return best


# --- Desenho -------------------------------------------------------------------------------------------------------
def render_clip(img: Image.Image, lines: Sequence[Line], choice: Pick) -> tuple[Image.Image, dict[str, Any]]:
    """O recorte (RGB, em px da captura) com folga de papel em volta e o marca-texto nas palavras marcadas.
    Devolve a imagem e um resumo (texto, palavra marcada, fundo claro ou escuro)."""
    ln = lines[choice.line]
    x0 = ln.spans[choice.first][0]
    x1 = ln.spans[choice.last][1]
    y0, y1 = ln.box[1], ln.box[3]
    ink_h = y1 - y0
    W, H = img.size
    pad_x, pad_y = round(ink_h * 0.34), round(ink_h * 0.26)
    box = (max(0, x0 - pad_x), max(0, y0 - pad_y), min(W, x1 + pad_x), min(H, y1 + pad_y))
    page = img.convert("RGB")
    clip = page.crop(box)
    # a folga que caiu fora da imagem (manchete encostada na borda) vira papel da cor do fundo
    want = (x1 - x0 + 2 * pad_x, y1 - y0 + 2 * pad_y)
    if clip.size != want:
        paper = Image.new("RGB", want, ln.bg)
        paper.paste(clip, (box[0] - (x0 - pad_x), box[1] - (y0 - pad_y)))
        clip, box = paper, (x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y)
    # tinta das palavras de fora do trecho que entrou na folga (a ponta da palavra vizinha, com a borda suavizada
    # dela) vira papel; a folga nunca encosta nas palavras do trecho
    d = ImageDraw.Draw(clip)
    fringe = max(2, round(ink_h * 0.08))
    for i, (a, b) in enumerate(ln.spans):
        if choice.first <= i <= choice.last:
            continue
        ea, eb = (a - fringe, min(b + fringe, x0 - 1)) if i < choice.first else (max(a - fringe, x1 + 1), b + fringe)
        if eb > box[0] and ea < box[2]:
            d.rectangle((max(0, ea - box[0]), 0, min(clip.width, eb - box[0]), clip.height), fill=ln.bg)
    light = sum(ln.bg) / 3 >= 140
    marker = Image.new("L", clip.size, 0)
    md = ImageDraw.Draw(marker)
    groups: list[list[int]] = []
    for i in sorted(choice.marked):
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    ext = round(ink_h * 0.10)
    for g in groups:
        mx0, mx1 = ln.spans[g[0]][0] - box[0] - ext, ln.spans[g[-1]][1] - box[0] + ext
        if light:
            # marca-texto atrás da palavra inteira, com as pontas levemente tortas (traço de caneta, não caixa)
            ty0, ty1 = y0 - box[1] - round(ink_h * 0.04), y1 - box[1] + round(ink_h * 0.08)
            md.polygon([(mx0, ty0 + round(ink_h * 0.06)), (mx1, ty0), (mx1, ty1 - round(ink_h * 0.03)), (mx0, ty1)],
                       fill=255)
        else:
            # página escura: letra clara sobre amarelo não se lê; vira um sublinhado grosso embaixo da palavra
            ty = y1 - box[1] + round(ink_h * 0.08)
            md.rectangle((mx0, ty, mx1, ty + max(3, round(ink_h * 0.14))), fill=255)
    if groups:
        import numpy as np

        arr = np.asarray(clip, dtype=np.float32)
        m = np.asarray(marker.filter(ImageFilter.GaussianBlur(max(1, ink_h // 50))), dtype=np.float32)[..., None] / 255
        if light:
            # a tinta fica por cima do amarelo (a cor da letra não muda); só o papel em volta dela vira amarelo
            dist = np.abs(arr - np.asarray(ln.bg, dtype=np.float32)).sum(axis=2, keepdims=True)
            m = m * (1 - np.clip(dist / 220, 0, 1))
        arr = arr * (1 - m) + np.asarray(MARKER, dtype=np.float32) * m
        clip = Image.fromarray(arr.round().clip(0, 255).astype(np.uint8), "RGB")
    words = choice.words(lines)
    info = {"text": " ".join(words), "marked": [ln.words[i] for i in choice.marked], "light": light,
            "ink_h": ink_h, "source_box": list(box)}
    return clip, info


def headline_clip(img: Image.Image, meta: dict[str, Any] | None) -> tuple[Image.Image, dict[str, Any]] | None:
    """O recorte de 2-3 palavras da manchete de uma captura (`meta`: o item do acervo, com `desc` e `rects`), ou None
    quando não dá para achar as palavras com segurança."""
    if not meta or not meta.get("rects"):
        return None
    text = headline_text(meta.get("desc"))
    try:
        lines = headline_lines(img, text, meta["rects"])
    except (ValueError, TypeError, IndexError):
        return None
    if not lines:
        return None
    choice = pick(lines)
    if choice is None:
        return None
    return render_clip(img, lines, choice)
