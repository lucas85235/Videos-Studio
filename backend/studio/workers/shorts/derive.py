"""Short cortado do vídeo longo = projeto filho (ADR 0002, decisão 4).

O filho copia os segmentos escolhidos (mesmo id e texto) e as cenas deles, com as imagens fixadas no arquivo que o
longo já usou (`visual.asset`) e as mesmas sobreposições de voz. Como a narração é endereçada pelo hash das entradas
(texto, idioma, voz, estilo de fala, velocidade, cadeia), o filho acha a narração no cache: nenhuma voz nova; com as
imagens fixadas, nenhuma imagem nova. O render, as legendas, a trilha e a publicação são os de sempre.

As durações e as imagens saem da timeline do longo (`outputs/<id>/<idioma>/timeline.json`). A prévia e o vídeo final
gravam o mesmo arquivo; ele só conta como "do vídeo final" quando o mix bate com o do último render. A voz final de
cada trecho e as imagens de teste (que o render ainda troca) saem do `plan` do longo no modo final: trecho sem a voz
final é gravado no render do Short (a duração é estimada), e imagem de teste não é fixada (o Short gera a dele).

O Short tem um idioma só (o do corte): a duração e os avisos foram conferidos nele, e os metadados são desse idioma.
O gancho vai na faixa do topo da primeira cena (`card.headline`) e o "Vídeo completo" no carimbo da última
(`card.stamp`): os dois o renderer desenha sobre qualquer tipo de cena, sem mexer no texto do cartão.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import Metadata, MusicCue, ProjectDoc, Scene, SceneCard, Segment, Source
from studio.core.styles.library import get_style
from studio.core.styles.schema import StyleSpec
from studio.core.timeline import lead_in, tail_out
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Project
from studio.workers.pipeline.build import plan
from studio.workers.pipeline.storyboard_check import source_label
from studio.workers.shorts.rules import (
    TIPOS,
    ShortTooLong,
    channel_rules,
    check_short,
    description_hashtags,
    final_title,
)

NO_TIMELINE = "Renderize o vídeo (ou gere a prévia) antes de cortar Shorts"
TITLE_MAX = 100
DESCRIPTION_MAX = 5000

# Linha da descrição ("<texto>: “<título do longo>”"), por idioma (2 letras); idioma sem entrada usa o inglês.
FULL_VIDEO = {
    "pt": "Vídeo completo no canal",
    "en": "Full video on the channel",
    "es": "Video completo en el canal",
    "fr": "Vidéo complète sur la chaîne",
    "de": "Ganzes Video auf dem Kanal",
    "it": "Video completo sul canale",
}
# Carimbo da última cena (até 2 palavras, como o "VÍDEO COMPLETO" do shorts.py antigo).
FULL_VIDEO_STAMP = {
    "pt": "Vídeo completo",
    "en": "Full video",
    "es": "Video completo",
    "fr": "Vidéo complète",
    "de": "Ganzes Video",
    "it": "Video completo",
}
VOICE_WARNING = ("a narração do longo é da voz de teste da prévia: a duração é estimada e o render final do Short vai "
                 "gravar a voz do canal")
CROP_WARNING = "as imagens do longo são 16:9: no Short vertical aparecem recortadas no centro (confira o enquadramento)"


class ShortsError(Exception):
    """Erro com a mensagem para o usuário e o código HTTP (404, 409 ou 422)."""

    def __init__(self, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


def full_video_text(lang: str) -> str:
    return FULL_VIDEO.get(lang.lower()[:2], FULL_VIDEO["en"])


def full_video_stamp(lang: str) -> str:
    return FULL_VIDEO_STAMP.get(lang.lower()[:2], FULL_VIDEO_STAMP["en"])


# ---------------------------------------------------------------------------------------------
# timeline do longo
# ---------------------------------------------------------------------------------------------
def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("narration") and data.get("scenes") else None


def _lang_entry(settings: dict[str, Any], key: str, lang: str) -> dict[str, Any]:
    entry = ((settings.get(key) or {}).get("langs") or {}).get(lang)
    return entry if isinstance(entry, dict) else {}


def load_timeline(project_id: str, lang: str, settings: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
    """Timeline do longo no idioma. Devolve (timeline, "final"|"preview").

    A prévia e o render gravam o mesmo `timeline.json` (o último sobrescreve): é "final" só quando o mix do arquivo é
    o do último render; uma prévia feita depois do render (outra voz, outro mix) volta a ser "preview"."""
    cfg = get_settings()
    settings = settings or {}
    final = _lang_entry(settings, "last_final", lang)
    rels = (final.get("timeline"), _lang_entry(settings, "last_preview", lang).get("timeline"))
    paths = [cfg.data_dir / rel for rel in rels if isinstance(rel, str) and rel]
    paths.append(cfg.outputs_dir / project_id / lang / "timeline.json")
    for path in paths:
        data = _read_json(path)
        if data is not None:
            is_final = bool(final.get("mix")) and data.get("mix") == final.get("mix")
            return data, "final" if is_final else "preview"
    raise ShortsError(NO_TIMELINE, 409)


def _render_gaps(parent_id: str, lang: str) -> tuple[set[str], set[str]]:
    """O que o render final do longo ainda faria: (segmentos sem a narração final no cache, cenas com imagem de teste
    ou sem imagem)."""
    p = plan(parent_id, langs=[lang], mode="final")
    return {m["segment_id"] for m in p["tts_missing"]}, set(p["images_missing"])


def short_overrides(parent: ProjectDoc, duration_s: float, ceiling: float) -> dict[str, Any]:
    """Sobreposições do estilo do filho: as do longo (voz incluída) + formato vertical de Short."""
    max_s = float(max(ceiling, math.ceil(duration_s)))
    target = round(min(max(duration_s, 15.0), max_s), 1)
    fmt = {
        "type": "short",
        "aspect": "9:16",
        "resolution": "1080x1920",
        "duration": {"target_s": target, "min_s": 15.0, "max_s": max_s},
    }
    return {**json.loads(json.dumps(parent.style_overrides)), "format": fmt}


def _short_style(parent: ProjectDoc) -> StyleSpec:
    return get_style(parent.style_id, short_overrides(parent, 30.0, 45.0))


def _parent_parts(parent_id: str) -> tuple[dict[str, Any], ProjectDoc]:
    try:
        row = service.get_project_row(parent_id)
        doc = service.get_doc(parent_id)
    except service.ProjectNotFound as exc:
        raise ShortsError("Projeto não encontrado", 404) from exc
    return row, doc


def _check_lang(doc: ProjectDoc, lang: str | None) -> str:
    lang = lang or doc.primary_language
    if lang not in doc.languages:
        raise ShortsError(f"O projeto não tem o idioma {lang}", 422)
    return lang


def segments_overview(parent_id: str, lang: str | None = None) -> dict[str, Any]:
    """Segmentos do longo na ordem, com texto, capítulo, papel, duração da narração e cenas (com a imagem usada).

    Sem timeline: `ShortsError` 409."""
    row, doc = _parent_parts(parent_id)
    lang = _check_lang(doc, lang)
    timeline, source = load_timeline(parent_id, lang, row.get("settings"))
    narration = {n.get("segment_id"): n for n in timeline.get("narration") or []}
    srcs = {s.get("id"): s.get("src") for s in timeline.get("scenes") or []}
    long_style = get_style(doc.style_id, doc.style_overrides)
    short_style = _short_style(doc)
    segments = []
    chapter: str | None = None
    for index, seg in enumerate(doc.segments):
        if seg.chapter:
            chapter = seg.chapter
        clip = narration.get(seg.id)
        segments.append(
            {
                "id": seg.id,
                "index": index,
                "text": seg.text_for(lang, doc.primary_language),
                "role": seg.role,
                "chapter": seg.chapter or chapter,
                "chapter_start": seg.role == "chapter" or bool(
                    seg.chapter and (index == 0 or doc.segments[index - 1].chapter != seg.chapter)),
                "start_s": round(float(clip["start"]), 3) if clip else None,
                "duration_s": round(float(clip["duration"]), 3) if clip else None,
                "scenes": [{"id": sc.id, "kind": sc.kind, "src": srcs.get(sc.id)} for sc in doc.scenes_of(seg.id)],
            }
        )
    version = timeline.get("version")
    voice_missing, images_redo = _render_gaps(parent_id, lang)
    return {
        "lang": lang,
        "source": source,
        "voice_missing": [s["id"] for s in segments if s["id"] in voice_missing],
        "images_redo": sorted(images_redo),
        "stale": isinstance(version, int) and version != row.get("current_version"),
        "gap_s": long_style.voice.gap_s,
        "lead_in_s": lead_in(short_style),
        "tail_out_s": tail_out(short_style),
        "segments": segments,
    }


def short_duration(overview: dict[str, Any], segment_ids: list[str]) -> float:
    """Duração do Short: narrações + pausas entre elas + entrada e saída do formato Short."""
    by_id = {s["id"]: s for s in overview["segments"]}
    total = sum(float(by_id[i]["duration_s"]) for i in segment_ids)
    gaps = overview["gap_s"] * max(0, len(segment_ids) - 1)
    return round(total + gaps + overview["lead_in_s"] + overview["tail_out_s"], 2)


def expand_ranges(overview: dict[str, Any], ranges: list[list[str]]) -> list[str]:
    """Ids dos segmentos dos trechos, validando: cada trecho é [primeiro, último] contínuo, e os trechos vêm na ordem
    do documento sem se sobrepor."""
    if not ranges:
        raise ShortsError("Escolha pelo menos um trecho do vídeo", 422)
    order = {s["id"]: s["index"] for s in overview["segments"]}
    ids = [s["id"] for s in overview["segments"]]
    out: list[str] = []
    last_end = -1
    for pair in ranges:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            raise ShortsError("Cada trecho tem o primeiro e o último segmento", 422)
        first, last = pair
        if first not in order or last not in order:
            raise ShortsError("Trecho com segmento que não existe neste vídeo", 422)
        a, b = order[first], order[last]
        if b < a:
            raise ShortsError("O trecho termina antes de começar: escolha os segmentos na ordem do vídeo", 422)
        if a <= last_end:
            raise ShortsError("Os trechos precisam seguir a ordem do vídeo, sem repetir segmentos", 422)
        out.extend(ids[a : b + 1])
        last_end = b
    missing = [i for i in out if next(s for s in overview["segments"] if s["id"] == i)["duration_s"] is None]
    if missing:
        raise ShortsError("A timeline está desatualizada (falta a narração de algum trecho): gere a prévia de novo", 409)
    return out


# ---------------------------------------------------------------------------------------------
# o filho
# ---------------------------------------------------------------------------------------------
def _fill_lang(texts: dict[str, str], lang: str, fallback: str) -> dict[str, str]:
    """Textos por idioma com `lang` preenchido pelo `fallback` quando falta (o filho só tem o idioma do corte, então
    perde a reserva do idioma principal do longo)."""
    if (texts.get(lang) or "").strip() or not (texts.get(fallback) or "").strip():
        return texts
    return {**texts, lang: texts[fallback]}


def _fill_scene_lang(scene: Scene, lang: str, fallback: str) -> None:
    if lang == fallback:
        return
    scene.overlay_text = _fill_lang(scene.overlay_text, lang, fallback)
    card = scene.card
    if card is not None:
        for key in ("value", "label", "headline", "stamp"):
            setattr(card, key, _fill_lang(getattr(card, key), lang, fallback))
        for item in card.items:
            item.label = _fill_lang(item.label, lang, fallback)
            item.value = _fill_lang(item.value, lang, fallback)
    if scene.visual.media is not None:
        scene.visual.media.headline = _fill_lang(scene.visual.media.headline, lang, fallback)


def _child_scenes(
    parent: ProjectDoc, keep: set[str], srcs: dict[str, str | None], redo: set[str]
) -> tuple[list[Scene], int]:
    """Cenas dos segmentos do Short, com a imagem fixada no arquivo que o longo mostrou. Imagem de teste (`redo`: o
    render do longo ainda vai trocá-la) não é fixada: o Short gera a dele. Devolve (cenas, nº de imagens fixadas)."""
    out: list[Scene] = []
    pinned = 0
    for scene in parent.ordered_scenes():
        if scene.segment_id not in keep:
            continue
        copy = scene.model_copy(deep=True)
        visual = copy.visual
        if visual.media is None:
            root = parent.reuse_root(scene)
            test_image = root is not None and root.id in redo and not scene.visual.asset
            target = visual.reuse_of
            outside = bool(target) and not any(s.id == target and s.segment_id in keep for s in parent.scenes)
            if outside:
                # Reaproveita imagem de cena que ficou fora: vira a imagem (ou a mídia real) resolvida.
                media = parent.shown_media(scene)
                visual.reuse_of = None
                if media is not None and not visual.asset:
                    visual.media = media.model_copy(deep=True)
            src = srcs.get(scene.id)
            if visual.media is None and src and not visual.asset and not test_image:
                visual.asset = src  # nenhuma imagem nova
                pinned += 1
        copy.locked = True
        out.append(copy)
    return out, pinned


def _groups(parent: ProjectDoc, keep: list[str]) -> list[list[str]]:
    """Os segmentos do Short em trechos contínuos do longo."""
    order = {seg.id: i for i, seg in enumerate(parent.segments)}
    groups: list[list[str]] = []
    for seg_id in keep:
        if groups and order[seg_id] == order[groups[-1][-1]] + 1:
            groups[-1].append(seg_id)
        else:
            groups.append([seg_id])
    return groups


def _child_music(parent: ProjectDoc, keep: list[str]) -> list[MusicCue]:
    """A trilha do longo: cada trecho abre com a deixa em vigor no primeiro segmento dele (a do primeiro trecho sem
    `start_segment`); as deixas que começam dentro de um trecho continuam. Sem deixa em vigor (todas começam depois),
    vale a primeira do longo, como no build."""
    if not parent.music:
        return []
    order = {seg.id: i for i, seg in enumerate(parent.segments)}
    ordered = sorted(parent.music, key=lambda c: order.get(c.start_segment or "", -1))

    def active(pos: int) -> MusicCue:
        cue = parent.music[0]
        for c in ordered:
            if order.get(c.start_segment or "", -1) <= pos:
                cue = c
        return cue

    cues: list[MusicCue] = []
    used: set[str] = set()
    current: str | None = None

    def add(cue: MusicCue, start: str | None) -> None:
        nonlocal current
        update: dict[str, Any] = {"start_segment": start}
        if cue.id in used:
            update["id"] = new_id("mus", 6)  # a mesma deixa volta num trecho depois: id novo
        used.add(cue.id)
        current = cue.id
        cues.append(cue.model_copy(update=update, deep=True))

    for n, group in enumerate(_groups(parent, keep)):
        opening = active(order[group[0]])
        if opening.id != current:
            add(opening, None if n == 0 else group[0])
        for seg_id in group[1:]:
            for cue in parent.music:
                if cue.start_segment == seg_id and cue.id != current:
                    add(cue, seg_id)
    return cues


def _short_sources(parent: ProjectDoc, scenes: list[Scene]) -> list[Source]:
    """Fontes do Short: as citadas pelas cenas que entram (fonte do cartão, fonte do cartão de notícia, página da
    mídia real). Longo sem esse vínculo em nenhuma cena: todas as fontes."""

    def links(scene_list: list[Scene]) -> tuple[set[str], set[int], set[str]]:
        labels: set[str] = set()
        refs: set[int] = set()
        urls: set[str] = set()
        for sc in scene_list:
            if sc.card is not None and sc.card.source and sc.card.source.strip():
                labels.add(sc.card.source.strip().lower())
            if sc.real is not None and sc.real.source_ref is not None:
                refs.add(sc.real.source_ref)
            media = sc.visual.media or parent.shown_media(sc)
            if media is not None:
                urls |= {u.strip().lower() for u in (media.url, media.page_url) if u and u.strip()}
        return labels, refs, urls

    if not any(links(parent.scenes)):
        return [s.model_copy(deep=True) for s in parent.sources]
    labels, refs, urls = links(scenes)
    out = []
    for n, src in enumerate(parent.sources, 1):
        url = src.url.strip().lower()
        if n in refs or source_label(src).lower() in labels or (url and url in urls):
            out.append(src.model_copy(deep=True))
    return out


def short_description(
    parent: ProjectDoc, hook: str, lang: str, hashtag: str, sources: list[Source] | None = None
) -> tuple[str, list[str]]:
    """Descrição do Short (gancho, vídeo completo, fontes do trecho) e as hashtags sem a principal. Os créditos das
    imagens e as hashtags entram no envio (`publish_tasks._description`), como em qualquer projeto."""
    meta = parent.metadata.get(lang) or parent.metadata.get(parent.primary_language)
    long_title = (meta.title.strip() if meta else "") or parent.title.strip()  # o título publicado do longo
    parts = [hook.strip(), f"{full_video_text(lang)}: “{long_title}”"]
    sources = parent.sources if sources is None else sources
    if sources:
        lines = [f"- {s.title}: {s.url}" if s.url else f"- {s.title}" for s in sources]
        parts.append("Fontes:\n" + "\n".join(lines))
    hashtags = description_hashtags(meta.hashtags if meta else [], hashtag)
    return "\n\n".join(p for p in parts if p)[:DESCRIPTION_MAX], hashtags[:15]


def derive_short(
    parent_id: str,
    ranges: list[list[str]],
    title: str,
    hook: str = "",
    tipo: str = "normal",
    lang: str | None = None,
) -> dict[str, Any]:
    """Cria o Short (projeto filho) a partir dos trechos do longo. Devolve {project_id, duration_s, warnings}.

    O filho tem só o idioma do corte (a duração e os avisos foram conferidos nele)."""
    row, parent = _parent_parts(parent_id)
    if row.get("parent_id"):
        raise ShortsError("Este projeto já é um Short cortado de outro vídeo", 409)
    if tipo not in TIPOS:
        raise ShortsError("Tipo de Short inválido (normal, lista ou iceberg)", 422)
    title = " ".join((title or "").split())
    if not title:
        raise ShortsError("Dê um título ao Short", 422)
    hook = " ".join((hook or "").split())
    overview = segments_overview(parent_id, lang)
    lang = overview["lang"]
    keep = expand_ranges(overview, ranges)
    duration = short_duration(overview, keep)
    limits, hashtag = channel_rules(parent.channel_id)
    try:
        warnings = check_short(title, duration, tipo, limits)
    except ShortTooLong as exc:
        raise ShortsError(str(exc), 422) from exc
    if overview["stale"]:
        warnings.append("o longo mudou depois da última prévia: confira o Short antes de publicar")
    if set(keep) & set(overview["voice_missing"]):
        warnings.append(VOICE_WARNING)

    fallback = parent.primary_language
    redo = set(overview["images_redo"])
    srcs = {sc["id"]: sc["src"] for s in overview["segments"] for sc in s["scenes"]}
    segments = [Segment.model_validate({**parent.segment(i).model_dump(), "locked": True}) for i in keep]
    scenes, pinned = _child_scenes(parent, set(keep), srcs, redo)
    redone = sum(1 for sc in scenes if sc.id in redo and not sc.visual.asset and sc.visual.media is None)
    if redone:
        warnings.append(f"{redone} imagem(ns) de teste do longo não foram reaproveitadas: o Short gera imagem nova")
    if pinned:
        warnings.append(CROP_WARNING)
    for sc in scenes:
        _fill_scene_lang(sc, lang, fallback)
    if scenes:
        # Gancho na faixa do topo da primeira cena e "Vídeo completo" no carimbo da última (pode ser a mesma).
        first, end = scenes[0], scenes[-1]
        if hook:
            first.card = first.card or SceneCard()
            first.card.headline = {lang: hook}
        end.card = end.card or SceneCard()
        end.card.stamp = {lang: full_video_stamp(lang)}
    sources = _short_sources(parent, scenes)
    description, hashtags = short_description(parent, hook, lang, hashtag, sources)
    parent_meta = parent.metadata.get(lang) or parent.metadata.get(fallback)
    thumb = parent.thumbnail.model_copy(deep=True)
    thumb.text = _fill_lang(thumb.text, lang, fallback)
    if thumb.scene_id and not any(sc.id == thumb.scene_id for sc in scenes):
        thumb.scene_id = None
    ceiling = limits.ceiling(tipo)
    # Documento montado (e validado) antes de criar o projeto: erro aqui não deixa filho órfão.
    doc = ProjectDoc(
        id="prj_novo",
        title=title[:200],
        topic=parent.topic,
        brief=parent.brief,
        channel_id=parent.channel_id,
        style_id=parent.style_id,
        style_overrides=short_overrides(parent, duration, ceiling),
        primary_language=lang,
        languages=[lang],
        seed=parent.seed,
        segments=segments,
        scenes=scenes,
        music=_child_music(parent, keep),
        thumbnail=thumb,
        metadata={
            lang: Metadata(
                title=final_title(title, hashtag)[:TITLE_MAX],
                description=description,
                tags=list(parent_meta.tags) if parent_meta else [],
                hashtags=hashtags,
            )
        },
        sources=sources,
        script_approved=True,
        storyboard_approved=True,
        art=parent.art.model_copy(deep=True) if parent.art is not None else None,
    )

    short_of = {
        "project_id": parent_id,
        "ranges": [list(r) for r in ranges],
        "tipo": tipo,
        "hook": hook,
        "duration_s": duration,
        "warnings": warnings,
        "lang": lang,
    }
    gates = {"script": False, "storyboard": False, "preview": True, "publish": True}
    created = service.create_project(
        title=title[:200],
        style_id=parent.style_id,
        topic=parent.topic,
        brief=parent.brief,
        channel_id=parent.channel_id,
        languages=[lang],
        settings={"gates": gates, "short_of": short_of},
    )
    child_id = created.id
    try:
        with session_scope() as s:
            child_row = s.get(Project, child_id)
            if child_row is not None:
                child_row.parent_id = parent_id
        doc.id = child_id
        service.commit(child_id, doc, f"Short cortado de “{parent.title}”")
    except Exception:
        service.delete_project(child_id)  # sem o roteiro, o filho não fica listado pela metade
        raise
    return {"project_id": child_id, "duration_s": duration, "warnings": warnings}


def children_of(parent_id: str) -> list[dict[str, Any]]:
    """Shorts já cortados deste longo, do mais novo para o mais velho."""
    with session_scope() as s:
        rows = (
            s.query(Project)
            .filter(Project.parent_id == parent_id)
            .order_by(Project.created_at.desc())
            .all()
        )
        out = []
        for r in rows:
            info = (r.settings or {}).get("short_of") or {}
            out.append(
                {
                    "project_id": r.id,
                    "title": r.title,
                    "status": r.status,
                    "duration_s": info.get("duration_s"),
                    "warnings": list(info.get("warnings") or []),
                    "tipo": info.get("tipo") or "normal",
                    "ranges": info.get("ranges") or [],
                    "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                }
            )
        return out
