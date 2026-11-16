"""Verificador de novidade: o vídeo novo repete demais imagens, miniatura ou título dos últimos vídeos do canal?

Cada projeto guarda a impressão digital do seu último build (`settings.novelty_fp`: hash perceptual de cada imagem
ou quadro das cenas e da miniatura), refeita só quando as imagens mudam. A conferência compara o projeto com os
últimos vídeos do mesmo canal (janela no canal, `settings.novelty.window`; 0 desliga) e com os títulos recentes do
canal do YouTube ligado (vêm com o canal, sem chamar a internet aqui).

O resultado é só aviso: nunca bloqueia a renderização nem a publicação.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from studio.config import get_settings
from studio.core.hashing import hash_obj
from studio.core.project import service
from studio.db import session_scope
from studio.media import novelty as fp
from studio.models import Channel, Project, Publication

log = logging.getLogger(__name__)

DEFAULT_WINDOW = 10
MAX_WINDOW = 100
# Aviso de imagens: pelo menos IMAGE_MIN_REPEATED imagens repetidas e IMAGE_SHARE do total.
IMAGE_MIN_REPEATED = 2
IMAGE_SHARE = 0.3
# Títulos com semelhança a partir disso (0 a 1) contam como "muito parecidos".
TITLE_SIMILAR = 0.8
FP_VERSION = 1


def window_of(channel_settings: dict[str, Any] | None) -> int:
    """Quantos vídeos recentes do canal entram na comparação (padrão 10; 0 desliga)."""
    raw = ((channel_settings or {}).get("novelty") or {}).get("window", DEFAULT_WINDOW)
    try:
        return max(0, min(MAX_WINDOW, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW


def _last_build(settings: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("last_final", "last_preview"):
        build = settings.get(key)
        if build and build.get("langs"):
            return build
    return None


def _entry(build: dict[str, Any], lang: str) -> dict[str, Any] | None:
    langs = build.get("langs") or {}
    return langs.get(lang) or next(iter(langs.values()), None)


def _sources(entry: dict[str, Any]) -> list[tuple[str, str]]:
    """(cena, arquivo) de cada imagem ou vídeo da linha do tempo, sem repetir o arquivo."""
    data_dir = get_settings().data_dir
    try:
        timeline = json.loads((data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    except (KeyError, OSError, ValueError):
        return []
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for scene in timeline.get("scenes") or []:
        src = scene.get("src")
        if isinstance(src, str) and src and src not in seen:
            seen.add(src)
            out.append((str(scene.get("id") or ""), src))
    return out


def fingerprint(project_id: str) -> dict[str, Any] | None:
    """Impressão digital das imagens e da miniatura do último build (do cache quando nada mudou). None sem build."""
    row = service.get_project_row(project_id)
    settings = row["settings"] or {}
    build = _last_build(settings)
    if build is None:
        return None
    doc = service.get_doc(project_id)
    entry = _entry(build, doc.primary_language)
    if entry is None:
        return None
    sources = _sources(entry)
    thumb = entry.get("thumbnail")
    key = hash_obj({"srcs": [s for _, s in sources], "thumb": thumb, "v": FP_VERSION})
    cached = settings.get("novelty_fp") or {}
    if cached.get("key") == key:
        return cached
    data_dir = get_settings().data_dir
    images: list[dict[str, str]] = []
    for scene_id, src in sources:
        h = fp.hash_file(data_dir / src)
        if h and not any(fp.similar(h, other["hash"]) for other in images):  # imagem reaproveitada conta uma vez
            images.append({"scene": scene_id, "hash": h})
    result = {"key": key, "images": images, "thumbnail": fp.hash_file(data_dir / thumb) if thumb else None}
    service.update_settings(project_id, {"novelty_fp": result})
    return result


def _title_of(project_id: str) -> str:
    doc = service.get_doc(project_id)
    meta = doc.metadata.get(doc.primary_language)
    return (meta.title if meta and meta.title.strip() else doc.title).strip()


def _history(project_id: str, channel_id: str, window: int) -> list[str]:
    """Os `window` projetos mais recentes do canal que já tiveram prévia ou vídeo final (sem o próprio)."""
    with session_scope() as s:
        rows = (
            s.query(Project)
            .filter(Project.channel_id == channel_id, Project.id != project_id)
            .order_by(Project.created_at.desc(), Project.id.desc())
            .all()
        )
        return [r.id for r in rows if _last_build(r.settings or {}) is not None][:window]


def _youtube_titles(channel_settings: dict[str, Any], project_id: str) -> list[dict[str, str]]:
    """Títulos recentes do canal do YouTube ligado, sem os vídeos que este projeto mesmo publicou."""
    videos = ((channel_settings.get("youtube") or {}).get("recent_videos")) or []
    with session_scope() as s:
        own = {p.external_id for p in s.query(Publication).filter_by(project_id=project_id).all() if p.external_id}
    return [v for v in videos if v.get("title") and v.get("video_id") not in own]


def check(project_id: str) -> dict[str, Any]:
    """Compara o projeto com os últimos vídeos do canal. Sempre devolve um relatório (com `warnings`, talvez vazio)."""
    row = service.get_project_row(project_id)
    channel_id = row.get("channel_id")
    report: dict[str, Any] = {
        "enabled": False, "ready": False, "window": 0, "compared": [], "warnings": [],
        "images": {"total": 0, "repeated": [], "share": 0.0}, "thumbnail": None, "titles": [],
    }
    if not channel_id:
        report["reason"] = "O projeto não tem canal: não há vídeos anteriores para comparar."
        return report
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        channel_settings = dict(channel.settings or {}) if channel else {}
    window = window_of(channel_settings)
    report["window"] = window
    if window <= 0:
        report["reason"] = "O verificador de novidade está desligado neste canal."
        return report
    report["enabled"] = True
    title = _title_of(project_id)

    # Títulos: projetos do canal e vídeos recentes do YouTube (o mesmo título conta uma vez).
    history = _history(project_id, channel_id, window)
    seen_titles: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for other in history:
        candidates.append({"title": _title_of(other), "source": "projeto", "project_id": other})
    for video in _youtube_titles(channel_settings, project_id)[:window]:
        candidates.append({"title": video["title"], "source": "youtube", "video_id": video.get("video_id") or ""})
    for cand in candidates:
        norm = fp.normalize_title(cand["title"])
        if not norm or norm in seen_titles:
            continue
        seen_titles.add(norm)
        score = fp.title_similarity(title, cand["title"])
        if score >= TITLE_SIMILAR:
            report["titles"].append({**cand, "similarity": round(score, 2)})
    report["titles"].sort(key=lambda t: -t["similarity"])

    mine = fingerprint(project_id)
    report["ready"] = mine is not None
    if mine is not None:
        others: list[tuple[str, str, dict[str, Any]]] = []
        for other in history:
            try:
                other_fp = fingerprint(other)
            except Exception as exc:  # noqa: BLE001 - um projeto quebrado não derruba a conferência
                log.warning("impressão digital do projeto %s falhou: %s", other, exc)
                continue
            if other_fp is not None:
                others.append((other, _title_of(other), other_fp))
        report["compared"] = [{"project_id": pid, "title": t} for pid, t, _ in others]
        repeated = []
        for image in mine["images"]:
            best: tuple[int, str, str] | None = None
            for pid, ptitle, ofp in others:
                for o in ofp["images"]:
                    d = fp.distance(image["hash"], o["hash"])
                    if d <= fp.SIMILAR_BITS and (best is None or d < best[0]):
                        best = (d, pid, ptitle)
            if best is not None:
                repeated.append({"scene": image["scene"], "distance": best[0], "project_id": best[1],
                                 "project_title": best[2]})
        total = len(mine["images"])
        report["images"] = {"total": total, "repeated": repeated,
                            "share": round(len(repeated) / total, 2) if total else 0.0}
        if mine.get("thumbnail"):
            best_thumb = None
            for pid, ptitle, ofp in others:
                if ofp.get("thumbnail") and fp.similar(mine["thumbnail"], ofp["thumbnail"]):
                    d = fp.distance(mine["thumbnail"], ofp["thumbnail"])
                    if best_thumb is None or d < best_thumb["distance"]:
                        best_thumb = {"project_id": pid, "project_title": ptitle, "distance": d}
            report["thumbnail"] = best_thumb

    report["warnings"] = _warnings(report)
    return report


def _names(items: list[dict[str, Any]], key: str = "project_title", limit: int = 3) -> str:
    names = list(dict.fromkeys(f"“{i[key]}”" for i in items if i.get(key)))
    extra = f" e mais {len(names) - limit}" if len(names) > limit else ""
    return ", ".join(names[:limit]) + extra


def _warnings(report: dict[str, Any]) -> list[str]:
    out: list[str] = []
    images = report["images"]
    repeated = images["repeated"]
    if len(repeated) >= IMAGE_MIN_REPEATED and images["share"] >= IMAGE_SHARE:
        out.append(
            f"Novidade: {len(repeated)} de {images['total']} imagens ({round(images['share'] * 100)}%) parecem com "
            f"imagens de vídeos recentes do canal ({_names(repeated)}). Troque algumas para o vídeo não parecer repetido."
        )
    if report.get("thumbnail"):
        out.append(f"Novidade: a miniatura parece com a de “{report['thumbnail']['project_title']}”. "
                   "Mude a imagem ou o texto da miniatura.")
    if report["titles"]:
        top = report["titles"][0]
        where = "no YouTube" if top["source"] == "youtube" else "no canal"
        out.append(f"Novidade: o título é muito parecido com “{top['title']}” ({where}, "
                   f"{round(top['similarity'] * 100)}% igual).")
    return out


def safe_warnings(project_id: str) -> list[str]:
    """Os avisos da conferência sem nunca falhar (para a renderização e o pacote de publicação)."""
    try:
        return check(project_id)["warnings"]
    except Exception as exc:  # noqa: BLE001 - o verificador é só uma ajuda
        log.warning("verificador de novidade falhou no projeto %s: %s", project_id, exc)
        return []
