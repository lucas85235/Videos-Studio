"""Respostas do LLM offline (sem chave). Servem para testar o pipeline e o agente de edição.

Não substituem um LLM de verdade: os roteiros são genéricos. O agente offline entende instruções
simples em português ("mais escuro", "legenda maior", "outra imagem na cena 3"...).
"""

from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if not unicodedata.combining(ch))


_FILLERS = [
    "Poucos sabem, mas os registros mais antigos contam uma história bem diferente da versão oficial.",
    "Testemunhas descreveram o mesmo detalhe estranho, mesmo sem nunca terem se conhecido.",
    "E foi exatamente aí que tudo começou a mudar.",
    "Os documentos da época mostram que alguém tentou esconder o que realmente aconteceu.",
    "Até hoje, ninguém conseguiu explicar esse detalhe por completo.",
    "Mas o que veio depois deixou todos ainda mais intrigados.",
]


def mock_script(ctx: dict[str, Any]) -> dict[str, Any]:
    topic = ctx.get("topic") or "um mistério esquecido"
    structure: list[str] = ctx.get("structure") or ["hook", "contexto", "desenvolvimento", "cta"]
    target_words = int(ctx.get("target_words") or 130)
    cta = ctx.get("cta") or "Siga para mais histórias como esta."
    is_top = ctx.get("structure_kind") == "top10"
    segments: list[dict[str, Any]] = []
    per_section = max(12, target_words // max(1, len(structure)))
    filler_index = 0
    for index, step in enumerate(structure):
        name = _norm(step)
        if index == 0 or name.startswith("hook"):
            text = f"Você já ouviu falar sobre {topic}? O que vou te contar agora parece impossível, mas aconteceu."
            segments.append({"role": "hook", "speaker": "narrator", "chapter": None, "number": None, "text": text})
            continue
        if name.startswith("cta") or name.startswith("frase_final") or "final" in name and index == len(structure) - 1:
            segments.append({"role": "cta", "speaker": "narrator", "chapter": None, "number": None, "text": cta})
            continue
        number = None
        role = "body"
        chapter = None
        match = re.search(r"(\d+)", name)
        if is_top and name.startswith("item") and match:
            number = int(match.group(1))
            role = "item"
            lead = f"Número {number}. "
        elif name.startswith("capitulo"):
            role = "chapter"
            chapter = f"Capítulo {match.group(1) if match else index}"
            lead = f"{chapter}. "
        else:
            lead = ""
        sentences = [lead + f"Sobre {topic}, existe um detalhe que muda tudo."]
        words = len(sentences[0].split())
        while True:
            sentence = _FILLERS[filler_index % len(_FILLERS)]
            if words + len(sentence.split()) > per_section * 1.1 and len(sentences) > 1:
                break
            filler_index += 1
            sentences.append(sentence)
            words += len(sentence.split())
            if words >= per_section:
                break
        segments.append({"role": role, "speaker": "narrator", "chapter": chapter, "number": number, "text": " ".join(sentences)})
    return {"title": f"O segredo: {topic}", "segments": segments, "sources": []}


def mock_storyboard(ctx: dict[str, Any]) -> dict[str, Any]:
    bible = ctx.get("bible") or ""
    scenes = []
    for item in ctx.get("scenes", []):
        text = item.get("text", "")
        focus = " ".join(text.split()[:12])
        kind = item.get("kind_hint") or "image"
        overlay = None
        if kind in ("title", "count", "text"):
            overlay = item.get("overlay_hint") or focus
        scenes.append({"key": item["key"], "kind": kind, "prompt": f"{bible}. Cena: {focus}", "overlay_text": overlay})
    return {"scenes": scenes}


def mock_translate(ctx: dict[str, Any]) -> dict[str, Any]:
    lang = ctx.get("target_lang", "en-US")
    return {
        "title": f"[{lang}] {ctx.get('title', '')}",
        "segments": [{"id": s["id"], "text": f"[{lang}] {s['text']}"} for s in ctx.get("segments", [])],
        "overlays": [{"id": o["id"], "text": f"[{lang}] {o['text']}"} for o in ctx.get("overlays", [])],
    }


def mock_metadata(ctx: dict[str, Any]) -> dict[str, Any]:
    title = ctx.get("title") or "Vídeo"
    topic = ctx.get("topic") or title
    words = [w.strip(".,!?").lower() for w in topic.split() if len(w) > 3][:6]
    chapters = ctx.get("chapters") or []
    description = f"{title}.\n\n" + ctx.get("summary", "")
    if chapters:
        description += "\n\nCapítulos:\n" + "\n".join(chapters)
    return {
        "title": title[:95],
        "description": description.strip(),
        "tags": words or ["misterio"],
        "hashtags": ["#" + w.replace(" ", "") for w in (words[:3] or ["misterio"])],
        "thumbnail_text": " ".join(title.split()[:4]).upper(),
    }


# --------------------------------------------------------------------------------------------
# Agente de edição offline
# --------------------------------------------------------------------------------------------

_SCENE_REF = re.compile(r"cena\s*(\d+)")
_SEG_REF = re.compile(r"(?:segmento|paragrafo|parágrafo|trecho)\s*(\d+)")


def _op(op: str, **fields: Any) -> dict[str, Any]:
    from studio.workers.pipeline.schemas import FLAT_OP_FIELDS

    base = {k: None for k in FLAT_OP_FIELDS}
    base.update({"op": op, "reason": fields.pop("reason", "")})
    base.update(fields)
    return base


def mock_edit(ctx: dict[str, Any]) -> dict[str, Any]:
    instruction = _norm(ctx.get("instruction", ""))
    scenes: list[str] = ctx.get("scene_ids", [])
    segments: list[str] = ctx.get("segment_ids", [])
    scoped: list[str] = ctx.get("scope_scene_ids") or []
    captions_size = int(ctx.get("captions_size") or 72)
    match = _SCENE_REF.search(instruction)
    if match and scenes:
        idx = int(match.group(1)) - 1
        if 0 <= idx < len(scenes):
            scoped = [scenes[idx]]
    targets = scoped or scenes
    ops: list[dict[str, Any]] = []
    notes: list[str] = []

    if "legenda" in instruction and ("maior" in instruction or "aument" in instruction):
        ops.append(_op("set_style", path="edit.captions.size", value_json=json.dumps(min(240, round(captions_size * 1.25))), reason="legendas maiores"))
        notes.append("aumentar as legendas")
    if "legenda" in instruction and ("menor" in instruction or "diminu" in instruction):
        ops.append(_op("set_style", path="edit.captions.size", value_json=json.dumps(max(12, round(captions_size * 0.8))), reason="legendas menores"))
        notes.append("diminuir as legendas")
    if "escur" in instruction or "sombri" in instruction:
        for sid in targets:
            ops.append(_op("set_scene_look", scene_id=sid, brightness=-0.25, contrast=0.15, reason="mais escuro"))
        notes.append(f"escurecer {len(targets)} cena(s)")
    if "clar" in instruction and "escur" not in instruction:
        for sid in targets:
            ops.append(_op("set_scene_look", scene_id=sid, brightness=0.2, reason="mais claro"))
        notes.append(f"clarear {len(targets)} cena(s)")
    if "quente" in instruction:
        for sid in targets:
            ops.append(_op("set_scene_look", scene_id=sid, warmth=0.35, reason="tons mais quentes"))
        notes.append("esquentar as cores")
    if "frio" in instruction or "fria" in instruction:
        for sid in targets:
            ops.append(_op("set_scene_look", scene_id=sid, warmth=-0.35, reason="tons mais frios"))
        notes.append("esfriar as cores")
    if any(k in instruction for k in ("outra imagem", "regener", "refaz a imagem", "troca a imagem", "nova imagem")):
        for sid in (scoped or targets[:1]):
            ops.append(_op("regenerate_scene", scene_id=sid, reason="nova variação de imagem"))
        notes.append("gerar outra imagem")
    if "zoom" in instruction:
        for sid in (scoped or targets[:1]):
            ops.append(_op("set_scene_motion", scene_id=sid, motion="punch_zoom", reason="zoom de impacto"))
        notes.append("zoom de impacto")
    if "glitch" in instruction:
        for sid in (scoped or targets[:1]):
            ops.append(_op("set_scene_transition", scene_id=sid, transition="glitch", reason="transição glitch"))
        notes.append("transição glitch")
    if "musica" in instruction:
        mood = "tension" if "tens" in instruction else ("epic" if "epic" in instruction else ("calm" if "calm" in instruction else "mystery"))
        if "sem musica" in instruction or "tira a musica" in instruction:
            ops.append(_op("set_music", enabled=False, reason="sem música"))
            notes.append("remover a música")
        elif "baix" in instruction:
            ops.append(_op("set_music", volume_db=-30.0, reason="música mais baixa"))
            notes.append("baixar a música")
        else:
            ops.append(_op("set_music", mood=mood, enabled=True, reason=f"trilha {mood}"))
            notes.append(f"trocar a trilha para '{mood}'")
    seg_match = _SEG_REF.search(instruction)
    if seg_match and "texto:" in ctx.get("instruction", "").lower() and segments:
        idx = int(seg_match.group(1)) - 1
        new_text = ctx["instruction"].split("texto:", 1)[1].strip()
        if 0 <= idx < len(segments) and new_text:
            ops.append(_op("update_segment_text", segment_id=segments[idx], lang=ctx.get("lang", "pt-BR"), text=new_text, reason="novo texto"))
            notes.append(f"reescrever o segmento {idx + 1}")
    if not ops:
        return {
            "summary": "O agente offline não entendeu a instrução. Configure o Gemini ou o Claude para instruções livres; "
            "offline eu entendo: mais escuro/claro, mais quente/frio, legenda maior/menor, outra imagem na cena N, zoom, glitch, música.",
            "ops": [],
        }
    return {"summary": "Vou " + ", ".join(notes) + ".", "ops": ops}


def mock_style(ctx: dict[str, Any]) -> dict[str, Any]:
    text = _norm(ctx.get("description", ""))
    changes: list[dict[str, str]] = []

    def change(path: str, value: Any, reason: str) -> None:
        changes.append({"path": path, "value_json": json.dumps(value, ensure_ascii=False), "reason": reason})

    effects = list(ctx.get("effects") or [])
    if "sepia" in text or "antig" in text or "jornal" in text:
        effects = [e for e in effects if e.get("type") != "grade"] + [{"type": "grade", "preset": "warm-sepia", "amount": 0.7}]
        change("art.bible", "fotografia e gravura antigas, papel envelhecido, tons sépia", "visual de arquivo antigo")
    if "noir" in text or "preto e branco" in text:
        effects = [e for e in effects if e.get("type") != "grade"] + [{"type": "grade", "preset": "noir", "amount": 0.8}]
    if "vhs" in text or "analog" in text:
        effects += [{"type": "vhs", "amount": 0.6}, {"type": "scanlines", "amount": 0.4}]
    if effects != list(ctx.get("effects") or []):
        change("edit.effects", effects, "efeitos de cor e textura")
    if "lento" in text or "calmo" in text:
        change("edit.shot_seconds", {"min": 4.0, "max": 7.0}, "cortes lentos")
        change("edit.transitions", {"cut": 4, "fade_black": 2}, "transições suaves")
    if "rapido" in text or "dinamico" in text:
        change("edit.shot_seconds", {"min": 1.5, "max": 2.5}, "cortes rápidos")
    if "maquina de escrever" in text or "typewriter" in text:
        change("edit.text_cards.animation", "typewriter", "texto de máquina de escrever")
        change("edit.text_cards.font", "Special Elite", "fonte de máquina de escrever")
    if "grande" in text and "legenda" in text:
        change("edit.captions.size", 96, "legendas grandes")
    return {"summary": f"{len(changes)} ajuste(s) a partir da descrição." if changes else "Nada reconhecido na descrição (offline).", "changes": changes}
