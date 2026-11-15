"""Gatilhos tardios: texto que aparece perto demais do corte da cena (origem: canais `motor/passos/check_triggers.py`).

Puro: lê só a timeline salva do vídeo final (`last_final.langs[lang].timeline`). Para cada cena, o tempo na tela antes
do corte é `duração da cena − quadro em que o elemento aparece`. Destaques precisam de pelo menos 2,2 s; carimbo,
placar e anotação, de 1,3 s. Abaixo disso vira aviso com a correção sugerida. Os quadros de entrada seguem o renderer
(`renderer/src/components/SceneView.tsx`, `DataCards.tsx`, `TextCards.tsx`).

A manchete do cartão de notícia não entra aqui: `core.timeline.news_reading_warnings` já avisa no build.
"""

from __future__ import annotations

from typing import Any

# Mínimos do canais (check_triggers.py): 2200 ms para destaques, 1300 ms para carimbo/placar/anotação.
HIGHLIGHT_MIN_S = 2.2
STAMP_MIN_S = 1.3

# Quadros de entrada no renderer.
STAMP_DELAY = 6  # SceneView: carimbo solto entra 6 quadros depois do corte (sem `card.stamp_from`)
PANEL_STAMP_DELAY = 10  # DataCards: carimbo do painel de dado entra 10 quadros depois do painel
REVEAL_LEAD = 3  # SceneView: o painel começa 3 quadros antes do número dito
REVEAL_AT_CUT_S = 0.3  # número dito até 0,3 s depois do corte: o painel entra parado, no quadro 0
KINETIC_PAGE = 6  # TextCards: palavras por página da tipografia cinética

PANEL_KINDS = ("stat", "compare")
# Cartões que mostram o texto da própria cena em destaque desde o corte.
TEXT_CARD_KINDS = ("title", "count", "quote", "document")
SENTENCE_END = (".", "!", "?", "…", ":", ";")

FIX = "alongue a fala da cena, divida a cena ou leve o texto para a próxima"


def _filled(value: Any) -> bool:
    return bool(str(value or "").strip())


def _snippet(text: str, words: int = 6) -> str:
    parts = str(text).split()
    out = " ".join(parts[:words])
    return out + ("…" if len(parts) > words else "")


def _seconds(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _kinetic_pages(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Mesmas páginas do `kineticPages` do renderer (6 palavras ou fim de frase)."""
    pages: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for w in words:
        current.append(w)
        text = str(w.get("text", "")).rstrip("\"'”’»)")
        if len(current) >= KINETIC_PAGE or text.endswith(SENTENCE_END):
            pages.append(current)
            current = []
    if current:
        pages.append(current)
    return pages


def _panel_from(scene: dict[str, Any], fps: float) -> int:
    """Quadro em que o painel do cartão de dado entra (`cardFrom` do SceneView)."""
    reveal = scene.get("reveal")
    if not isinstance(reveal, (int, float)):
        return 0
    if reveal <= round(fps * REVEAL_AT_CUT_S):
        return 0
    return max(0, int(reveal) - REVEAL_LEAD)


def effective_kind(scene: dict[str, Any]) -> str:
    """Tipo que o renderer desenha (`effectiveKind` do SceneView): cartão de dado vazio vira imagem (ou tipografia
    cinética, sem imagem), com o carimbo solto."""
    kind = scene.get("kind") or "image"
    card = scene.get("card") or {}
    if kind == "stat":
        empty = not (_filled(card.get("value")) or _filled(card.get("label")) or scene.get("number") is not None
                     or _filled(scene.get("text")))
    elif kind == "compare":
        empty = len([it for it in card.get("items") or [] if _filled(it.get("label")) or _filled(it.get("value"))]) < 2
    else:
        return kind
    if not empty:
        return kind
    return "image" if scene.get("src") else "text"


def scene_elements(scene: dict[str, Any], fps: float) -> list[dict[str, Any]]:
    """Elementos de texto da cena com o quadro (relativo à cena) em que aparecem e o mínimo na tela."""
    kind = effective_kind(scene)
    card = scene.get("card") or {}
    text = str(scene.get("text") or "").strip()
    out: list[dict[str, Any]] = []

    def add(what: str, label: str, frame: float, minimum: float) -> None:
        out.append({"what": what, "text": label, "frame": max(0, int(frame)), "min_s": minimum})

    if kind in TEXT_CARD_KINDS:
        label = text or (str(scene["number"]) if kind == "count" and scene.get("number") is not None else "")
        if label:
            add("destaque", label, 0, HIGHLIGHT_MIN_S)
    if kind == "text":
        # Tipografia cinética: cada página entra com a primeira palavra dela; a última é a que tem menos tempo.
        words = [w for w in scene.get("words") or [] if isinstance(w, dict)]
        if words:
            last = _kinetic_pages(words)[-1]
            add("texto cinético", " ".join(str(w.get("text", "")) for w in last), last[0].get("from", 0), HIGHLIGHT_MIN_S)
        elif text:
            add("destaque", text, 0, HIGHLIGHT_MIN_S)
    if _filled(card.get("headline")):
        add("faixa", str(card["headline"]).strip(), 0, HIGHLIGHT_MIN_S)
    if kind in PANEL_KINDS:
        start = _panel_from(scene, fps)
        if kind == "stat":
            label = card.get("value") or card.get("label") or (scene.get("number") if scene.get("number") is not None else "")
            label = str(label or text).strip()
        else:
            items = [it for it in card.get("items") or [] if _filled(it.get("label")) or _filled(it.get("value"))]
            label = " × ".join(str(it.get("label") or it.get("value")).strip() for it in items) if len(items) >= 2 else ""
        if label:
            add("placar" if kind == "compare" else "destaque", label, start, HIGHLIGHT_MIN_S)
            if _filled(card.get("stamp")):
                add("carimbo", str(card["stamp"]).strip(), start + PANEL_STAMP_DELAY, STAMP_MIN_S)
    elif _filled(card.get("stamp")):
        stamp_from = card.get("stamp_from")
        frame = round(stamp_from) if isinstance(stamp_from, (int, float)) else STAMP_DELAY
        add("carimbo", str(card["stamp"]).strip(), frame, STAMP_MIN_S)
    return out


def late_triggers(timeline: dict[str, Any]) -> list[dict[str, Any]]:
    """Itens de conferência (`code="late_text"`) para cada texto que fica menos que o mínimo na tela antes do corte."""
    fps = float(timeline.get("fps") or 30)
    items: list[dict[str, Any]] = []
    for index, scene in enumerate(timeline.get("scenes") or []):
        frames = int(scene.get("durationInFrames") or 0)
        if frames <= 0:
            continue
        dur = frames / fps
        start_s = float(scene.get("from") or 0) / fps
        for el in scene_elements(scene, fps):
            appear = min(el["frame"], frames) / fps
            left = dur - appear
            if left >= el["min_s"] - 1e-9:
                continue
            minimum = _seconds(el["min_s"])
            items.append(
                {
                    "code": "late_text",
                    "level": "aviso",
                    "title": f"Cena {index + 1}: {el['what']} com {_seconds(left)} s na tela",
                    "detail": (
                        f"texto '{_snippet(el['text'])}' fica {_seconds(left)} s na tela antes do corte "
                        f"(mínimo {minimum} s): {FIX}."
                    ),
                    "at_s": round(start_s + appear, 2),
                    "scene_id": scene.get("id"),
                }
            )
    return items
