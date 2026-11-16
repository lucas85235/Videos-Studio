"""3 miniaturas para o teste A/B (ADR 0003, bloco I) e a regra de quando usar o teste do YouTube Studio.

- 1ª = a do projeto (`doc.thumbnail`: cena, texto e o modelo do estilo), rótulo "Recomendada";
- 2ª = outra cena (a primeira com imagem, não editorial, de tipo stat/compare/title ou do gancho), mesmo texto;
- 3ª = a cena da 1ª com outro modelo (texto-grande-esquerda → texto-centro → texto-baixo).

Estado em `Project.settings["thumbs"][lang]` (contrato no ADR). O envio ao YouTube e o pacote manual usam
`variants[chosen - 1].path` quando o arquivo existe (`chosen_thumbnail`).
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from statistics import median
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.media import thumb_variants as tv
from studio.models import Channel, Project, Publication, VideoMetric
from studio.workers.analytics import reach
from studio.workers.analytics.variables import video_format

AB_MIN_IMPRESSIONS = 2000
AB_AGE_HOURS = 168  # 7 dias
AB_AGE_MAX_HOURS = 240  # linha de 7 dias aceita até 10 dias (depois disso já não é o número de 7 dias)
CTR_MIN_IMPRESSIONS = 500  # abaixo disso o CTR é ruído: nem troca nem mantém
CTR_SWAP = 4.0  # CTR (%) abaixo do qual a troca em 48 h vale
CTR_HOURS = 48
LABELS = ("Recomendada", "Outra cena", "Outro modelo")
CARD_KINDS = ("stat", "compare", "title")
RULE_AB = ("Teste A/B: use o 'Testar e comparar' do YouTube Studio com as 3 (a API não faz o teste).")
STALE_WARNING = ("As miniaturas são de antes da última mudança na capa ou nas imagens: gere de novo. "
                 "Até lá vai a capa do vídeo final.")
_LOCK = threading.Lock()


class ThumbsError(ValueError):
    """Pedido recusado (modelo, cena, número da variação): mensagem em português."""


# --------------------------------------------------------------------------------------------
# Regra do A/B
# --------------------------------------------------------------------------------------------


def swap_option(chosen: int) -> int:
    """A opção da troca em 48 h: a 2ª quando a escolhida é a 1ª; senão a 1ª (nunca a que já está no ar)."""
    return 2 if chosen == 1 else 1


def manual_rule(chosen: int = 1) -> str:
    return f"Suba a {chosen}ª; em 48 h, CTR abaixo de 4% → troque pela {swap_option(chosen)}ª."


RULE_MANUAL = manual_rule(1)


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}".replace(".", ",") + "%"


def ctr_check(project_id: str, chosen: int = 1, eligible: bool = False) -> dict[str, Any] | None:
    """CTR coletado (ou lançado à mão) do vídeo do projeto no YouTube e o que fazer com a miniatura.

    `verdict`: sem_dado | cedo (menos de 48 h no ar ou menos de 500 impressões) | ab (o teste do YouTube Studio decide)
    | trocar (abaixo de 4%) | manter. None quando o projeto não tem vídeo no YouTube."""
    with session_scope() as s:
        ids = [pid for (pid,) in s.query(Publication.external_id)
               .filter(Publication.project_id == project_id, Publication.platform == "youtube",
                       Publication.external_id.isnot(None)).all() if pid]
        if not ids:
            return None
        rows = (s.query(VideoMetric).filter(VideoMetric.youtube_id.in_(ids))
                .order_by(VideoMetric.day).all())
        # Campo a campo, o número não vazio mais recente (como em Desempenho).
        latest: dict[str, Any] = {"youtube_id": ids[0], "ctr": None, "impressions": None, "age_hours": None}
        for row in rows:
            latest["youtube_id"] = row.youtube_id
            for key in ("ctr", "impressions", "age_hours"):
                if getattr(row, key) is not None:
                    latest[key] = getattr(row, key)
    ctr, impressions, age = latest["ctr"], latest["impressions"], latest["age_hours"]
    swap = swap_option(chosen)
    if ctr is None:
        verdict = "sem_dado"
        text = ("Ainda sem CTR deste vídeo: a coleta traz em até 2 dias depois de ir ao ar "
                "(ou lance à mão em Desempenho).")
    else:
        seen = f"CTR de {_fmt_pct(float(ctr))}" + (f" em {int(impressions):,} impressões".replace(",", ".")
                                                   if impressions else "")
        if eligible:
            verdict, text = "ab", f"{seen}. O teste A/B do YouTube Studio decide a miniatura."
        elif age is not None and age < CTR_HOURS:
            verdict, text = "cedo", f"{seen}. Espere 48 h no ar para decidir a troca."
        elif impressions is not None and impressions < CTR_MIN_IMPRESSIONS:
            verdict, text = "cedo", (f"{seen}: ainda há poucas impressões para o CTR valer. "
                                     f"Espere passar de {CTR_MIN_IMPRESSIONS} para decidir a troca.")
        elif float(ctr) < CTR_SWAP:
            verdict, text = "trocar", f"{seen}, abaixo de 4%: troque pela {swap}ª opção."
        else:
            verdict, text = "manter", f"{seen}: mantenha a {chosen}ª."
    return {**latest, "verdict": verdict, "swap": swap, "text": text}


def _short_projects(project_ids: set[str]) -> set[str]:
    out: set[str] = set()
    for pid in project_ids:
        try:
            doc = service.get_doc(pid)
            if get_style(doc.style_id, doc.style_overrides).format.type == "short":
                out.add(pid)
        except Exception:  # noqa: BLE001 - projeto apagado ou estilo inválido: conta como longo
            continue
    return out


def _channel_videos(channel_id: str) -> tuple[dict[str, float], dict[str, date], dict[str, Any]]:
    """Da coleta: duração (s) e dia de publicação de cada vídeo (`settings["analytics"]["videos"]`) e os dados de
    alcance da Reporting API (`settings["analytics_reach"]`)."""
    with session_scope() as s:
        channel = s.get(Channel, channel_id)
        settings = (channel.settings or {}) if channel else {}
    videos = ((settings.get("analytics") or {}).get("videos") or {})
    durations: dict[str, float] = {}
    published: dict[str, date] = {}
    for yid, info in videos.items():
        if not isinstance(info, dict):
            continue
        if isinstance(info.get("duration_s"), int | float):
            durations[yid] = float(info["duration_s"])
        try:
            published[yid] = datetime.fromisoformat(str(info["published_at"])).astimezone(UTC).date()
        except (KeyError, TypeError, ValueError):
            pass
    return durations, published, dict(settings.get(reach.SETTINGS_KEY) or {})


def ab_rule(channel_id: str | None, chosen: int = 1) -> dict[str, Any]:
    """Mediana das impressões em 7 dias dos longos do canal. Mediana >= 2.000 → teste A/B do YouTube Studio; senão,
    troca à mão em 48 h.

    Por vídeo, as impressões de 7 dias vêm, nesta ordem: dos dias da Reporting API (soma do dia da publicação e dos 6
    seguintes, só quando os 7 estão guardados); ou da linha de `VideoMetric` com impressões e idade entre 168 h e 240 h
    (lançamento à mão ou coleta perto dos 7 dias), a mais perto de 168 h. A coleta grava o total desde o início do
    histórico do relatório, não "da vida do vídeo": uma linha de vídeo antigo (idade bem acima de 240 h) somaria semanas
    e não entra. Short (estilo do projeto ou até 3 min, como em "O que funcionou") fica de fora, também os enviados
    fora do Vulto."""
    best: dict[str, VideoMetric] = {}
    if channel_id:
        durations, published, reach_data = _channel_videos(channel_id)
        week = reach.first_week(reach_data, published)
        with session_scope() as s:
            rows = (s.query(VideoMetric)
                    .filter(VideoMetric.channel_id == channel_id, VideoMetric.age_hours.isnot(None),
                            VideoMetric.age_hours >= AB_AGE_HOURS, VideoMetric.age_hours <= AB_AGE_MAX_HOURS,
                            VideoMetric.impressions.isnot(None)).all())
            for row in rows:
                cur = best.get(row.youtube_id)
                if cur is None or (row.age_hours or 0) < (cur.age_hours or 0):
                    best[row.youtube_id] = row
            projects = {yid: pid for yid, pid in s.query(VideoMetric.youtube_id, VideoMetric.project_id)
                        .filter(VideoMetric.channel_id == channel_id, VideoMetric.youtube_id.in_(list(week)),
                                VideoMetric.project_id.isnot(None)).all()} if week else {}
            picked = [(yid, r.project_id, r.impressions) for yid, r in best.items() if yid not in week]
        picked += [(yid, projects.get(yid), imp) for yid, imp in week.items()]
    else:
        picked, durations = [], {}
    shorts = _short_projects({pid for _, pid, _ in picked if pid})
    values = [float(imp) for yid, pid, imp in picked
              if pid not in shorts and video_format(None, durations.get(yid)) == "long"]
    med = round(median(values), 1) if values else None
    eligible = med is not None and med >= AB_MIN_IMPRESSIONS
    return {"eligible": eligible, "median_impressions": med, "n": len(values),
            "rule": RULE_AB if eligible else manual_rule(chosen)}


# --------------------------------------------------------------------------------------------
# Especificação das 3
# --------------------------------------------------------------------------------------------


def _timeline_images(row: dict[str, Any], lang: str) -> dict[str, str]:
    """Imagem de cada cena no último build (`src` da timeline do vídeo final ou da prévia)."""
    settings = row.get("settings") or {}
    data_dir = get_settings().data_dir
    for key in ("last_final", "last_preview"):
        entry = ((settings.get(key) or {}).get("langs") or {}).get(lang) or {}
        rel = entry.get("timeline")
        if not rel or not (data_dir / rel).is_file():
            continue
        try:
            timeline = json.loads((data_dir / rel).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        return {s["id"]: s["src"] for s in timeline.get("scenes", []) if s.get("id") and s.get("src")}
    return {}


def scene_images(row: dict[str, Any], doc: ProjectDoc, lang: str) -> dict[str, str]:
    """Imagem de base por cena (relativa à pasta de dados): a mídia real (original), a imagem fixada ou a do build."""
    data_dir = get_settings().data_dir
    built = _timeline_images(row, lang)
    out: dict[str, str] = {}
    for scene in doc.ordered_scenes():
        media = doc.shown_media(scene)
        for rel in (media.asset if media else None, scene.visual.asset, built.get(scene.id)):
            if rel and (data_dir / rel).is_file():
                out[scene.id] = rel
                break
    return out


def _editorial(doc: ProjectDoc, scene_id: str) -> bool:
    media = doc.shown_media(doc.scene(scene_id))
    return bool(media is not None and media.editorial)


def default_specs(doc: ProjectDoc, template: str, lang: str, images: dict[str, str], count: int = 3
                  ) -> list[dict[str, Any]]:
    """As 3 de partida (1ª do projeto, 2ª outra cena, 3ª outro modelo)."""
    ordered = [s for s in doc.ordered_scenes() if s.id in images]
    first = doc.thumbnail.scene_id if doc.thumbnail.scene_id in {s.id for s in doc.scenes} else None
    if first is None:  # como o build: a primeira com imagem que não é material de terceiros
        first = next((s.id for s in ordered if not _editorial(doc, s.id)), ordered[0].id if ordered else None)
    text = doc.thumbnail.text.get(lang) or doc.thumbnail.text.get(doc.primary_language) or ""
    roles = {seg.id: seg.role for seg in doc.segments}
    others = [s for s in ordered if s.id != first and not _editorial(doc, s.id)]
    second = next((s.id for s in others if s.kind in CARD_KINDS or roles.get(s.segment_id) == "hook"),
                  others[0].id if others else next((s.id for s in ordered if s.id != first), first))
    specs = [
        {"scene_id": first, "text": text, "template": template},
        {"scene_id": second, "text": text, "template": template},
        {"scene_id": first, "text": text, "template": tv.next_template(template)},
    ]
    return specs[: max(1, min(3, count))]


def _with_labels(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{**spec, "label": LABELS[i] if i < len(LABELS) else f"Opção {i + 1}"} for i, spec in enumerate(specs)]


def _context(project_id: str, lang: str | None) -> tuple[dict[str, Any], ProjectDoc, Any, str]:
    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    return row, doc, style, lang or doc.primary_language


def get_state(project_id: str, lang: str | None = None) -> dict[str, Any]:
    """Estado das miniaturas do idioma, com a especificação de partida quando ainda não há e a regra do A/B."""
    row, doc, style, lang = _context(project_id, lang)
    saved = ((row.get("settings") or {}).get("thumbs") or {}).get(lang) or {}
    count = min(3, style.thumbnail.variations)
    images = scene_images(row, doc, lang)
    spec_base = saved.get("spec_base") or saved.get("base")
    base_changed = bool(spec_base) and spec_base != _base(doc, style, lang)
    # A capa do projeto mudou desde a geração: recomeça da especificação de partida (com o texto e a cena novos).
    spec = (None if base_changed else saved.get("spec")) or default_specs(doc, style.thumbnail.template, lang, images,
                                                                          count)
    chosen = int(saved.get("chosen") or 1)
    ab = ab_rule(row.get("channel_id") or doc.channel_id, chosen)
    return {
        "lang": lang,
        "variants": saved.get("variants") or [],
        "chosen": chosen,
        "stale": bool(saved.get("variants")) and _is_stale(saved, doc, style, lang, images),
        "spec": _with_labels(spec)[:count],
        "sheet_big": saved.get("sheet_big") or "",
        "sheet_small": saved.get("sheet_small") or "",
        "made_at": saved.get("made_at"),
        "ab": {k: ab[k] for k in ("eligible", "median_impressions", "n")},
        "rule": ab["rule"],
        # CTR coletado do vídeo deste projeto no YouTube (None sem publicação): decide a troca em 48 h.
        "ctr": ctr_check(project_id, chosen, bool(ab["eligible"])),
        "is_long": style.format.type == "long",
        "size": "x".join(map(str, tv.target_size(style.format.type == "long"))),
        "templates": list(tv.TEMPLATES),
        "max": count,
    }


def _base(doc: ProjectDoc, style: Any, lang: str) -> dict[str, Any]:
    """A capa do projeto (texto, cena e modelo do estilo) de que as miniaturas partiram."""
    text = doc.thumbnail.text.get(lang) or doc.thumbnail.text.get(doc.primary_language) or ""
    return {"text": text, "scene_id": doc.thumbnail.scene_id, "template": style.thumbnail.template}


def _images_stamp(specs: list[dict[str, Any]], images: dict[str, str]) -> str:
    """Impressão das imagens de fundo usadas (caminho, tamanho e data): muda quando a cena troca de imagem."""
    data_dir = get_settings().data_dir
    parts = []
    for spec in specs:
        rel = images.get(spec.get("scene_id") or "") or ""
        try:
            st = (data_dir / rel).stat() if rel else None
        except OSError:
            st = None
        parts.append([spec.get("scene_id"), rel, st.st_size if st else 0, st.st_mtime_ns if st else 0])
    return hashlib.sha1(json.dumps(parts).encode("utf-8")).hexdigest()


def _is_stale(saved: dict[str, Any], doc: ProjectDoc, style: Any, lang: str, images: dict[str, str]) -> bool:
    """As miniaturas são de antes da última mudança na capa do projeto ou nas imagens das cenas usadas."""
    if "base" not in saved:  # gerada antes deste controle: não dá para saber
        return False
    if saved.get("base") != _base(doc, style, lang):
        return True
    specs = [{"scene_id": v.get("scene_id")} for v in saved.get("variants") or []]
    return saved.get("images") != _images_stamp(specs, images)


def _save(project_id: str, lang: str, patch: dict[str, Any]) -> None:
    with _LOCK:
        row = service.get_project_row(project_id)
        thumbs = dict((row.get("settings") or {}).get("thumbs") or {})
        thumbs[lang] = {**(thumbs.get(lang) or {}), **patch}
        service.update_settings(project_id, {"thumbs": thumbs})


def set_spec(project_id: str, lang: str | None, variants: list[dict[str, Any]]) -> dict[str, Any]:
    row, doc, style, lang = _context(project_id, lang)
    count = min(3, style.thumbnail.variations)
    if not 1 <= len(variants) <= count:
        raise ThumbsError(f"Envie de 1 a {count} miniaturas.")
    scene_ids = {s.id for s in doc.scenes}
    clean = []
    for item in variants:
        template = str(item.get("template") or style.thumbnail.template)
        if template not in tv.TEMPLATES:
            raise ThumbsError(f"Modelo desconhecido: {template}.")
        scene_id = item.get("scene_id") or None
        if scene_id is not None and scene_id not in scene_ids:
            raise ThumbsError("Cena não encontrada.")
        clean.append({"scene_id": scene_id, "text": " ".join(str(item.get("text") or "").split())[:120],
                      "template": template})
    _save(project_id, lang, {"spec": clean, "spec_base": _base(doc, style, lang)})
    return get_state(project_id, lang)


def choose(project_id: str, lang: str | None, n: int) -> dict[str, Any]:
    row, _, _, lang = _context(project_id, lang)
    variants = (((row.get("settings") or {}).get("thumbs") or {}).get(lang) or {}).get("variants") or []
    if not 1 <= n <= len(variants):
        raise ThumbsError("Gere as miniaturas antes de escolher.")
    _save(project_id, lang, {"chosen": n})
    return get_state(project_id, lang)


def _rel(path: Any) -> str:
    return path.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()


def make_variants(project_id: str, lang: str | None = None,
                  progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Renderiza as miniaturas (3840 x 2160 no longo, 1080 x 1920 no Short), as prévias e as duas folhas."""
    say = progress or (lambda f, m: None)
    row, doc, style, lang = _context(project_id, lang)
    state = get_state(project_id, lang)
    images = scene_images(row, doc, lang)
    is_long = style.format.type == "long"
    data_dir = get_settings().data_dir
    folder = get_settings().outputs_dir / project_id / lang / "miniaturas"
    folder.mkdir(parents=True, exist_ok=True)
    variants: list[dict[str, Any]] = []
    specs = state["spec"]
    fallback = default_specs(doc, style.thumbnail.template, lang, images, 1)[0].get("scene_id")
    for i, spec in enumerate(specs, 1):
        say(0.05 + 0.75 * (i - 1) / len(specs), f"Miniatura {i} de {len(specs)}")
        base_rel = images.get(spec.get("scene_id") or "")
        note = None
        if spec.get("scene_id") and not base_rel:
            # A cena foi apagada ou ficou sem imagem depois da escolha: usa a cena da capa, ou fundo liso.
            base_rel = images.get(fallback or "")
            note = ("A cena escolhida não tem mais imagem: usei a cena da capa." if base_rel
                    else "A cena escolhida não tem mais imagem: saiu com fundo liso.")
        done = tv.render_variant(data_dir / base_rel if base_rel else None, folder / f"miniatura-{i}.jpg",
                                 text=spec.get("text") or "", template=spec.get("template") or style.thumbnail.template,
                                 is_long=is_long, font=style.thumbnail.font, color=style.thumbnail.color,
                                 accent=style.thumbnail.accent)
        variants.append({"n": i, "label": spec.get("label") or LABELS[min(i - 1, 2)], "scene_id": spec.get("scene_id"),
                         "text": spec.get("text") or "", "template": spec.get("template"), "path": _rel(done["path"]),
                         "preview": _rel(done["preview"]), "size": done["size"], "mb": done["mb"],
                         "errors": done["errors"], "warnings": ([note] if note else []) + done["warnings"]})
    say(0.85, "Folhas de conferência")
    settings = row.get("settings") or {}
    entry = ((settings.get("last_final") or {}).get("langs") or {}).get(lang) or \
        ((settings.get("last_preview") or {}).get("langs") or {}).get(lang) or {}
    previews = [data_dir / v["preview"] for v in variants]
    big = tv.sheet_big(previews, folder / "folha-grande.jpg")
    small = tv.sheet_small(previews, [v["label"] for v in variants], tv.mmss(entry.get("duration")),
                           folder / "folha-pequena.jpg")
    chosen = state["chosen"] if 1 <= state["chosen"] <= len(variants) else 1
    ab = state["ab"]
    _save(project_id, lang, {"variants": variants, "chosen": chosen,
                             "spec": [{k: s.get(k) for k in ("scene_id", "text", "template")} for s in specs],
                             "sheet_big": _rel(big), "sheet_small": _rel(small),
                             "made_at": datetime.now(UTC).isoformat(), "ab": ab, "base": _base(doc, style, lang),
                             "spec_base": _base(doc, style, lang), "images": _images_stamp(variants, images)})
    say(1.0, "Miniaturas prontas")
    return get_state(project_id, lang)


# --------------------------------------------------------------------------------------------
# Envio (publish_tasks e pacote)
# --------------------------------------------------------------------------------------------


def thumbs_of(settings: dict[str, Any], lang: str) -> dict[str, Any]:
    return ((settings or {}).get("thumbs") or {}).get(lang) or {}


def chosen_number(settings: dict[str, Any], lang: str) -> int:
    try:
        return int(thumbs_of(settings, lang).get("chosen") or 1)
    except (TypeError, ValueError):
        return 1


def pick_thumbnail(settings: dict[str, Any], lang: str, project_id: str | None = None
                   ) -> tuple[str | None, str | None]:
    """(`variants[chosen - 1].path`, aviso). None (vale a do build) quando o arquivo sumiu, a variação tem erro (ex.:
    passa de 2 MB) ou, com `project_id`, as miniaturas estão desatualizadas; o aviso diz por quê."""
    state = thumbs_of(settings, lang)
    variants = state.get("variants") or []
    chosen = chosen_number(settings, lang)
    if not 1 <= chosen <= len(variants):
        return None, None
    variant = variants[chosen - 1]
    rel = variant.get("path")
    if not rel or not (get_settings().data_dir / rel).is_file():
        return None, None
    if variant.get("errors"):
        return None, (f"A miniatura {chosen} não serve ({'; '.join(variant['errors'])}): "
                      "foi a capa do vídeo final.")
    if project_id:
        try:
            row = service.get_project_row(project_id)
            doc = service.get_doc(project_id)
            style = get_style(doc.style_id, doc.style_overrides)
            if _is_stale(state, doc, style, lang, scene_images(row, doc, lang)):
                return None, STALE_WARNING
        except Exception:  # noqa: BLE001 - sem como conferir: vale a escolhida
            pass
    return rel, None


def chosen_thumbnail(settings: dict[str, Any], lang: str, project_id: str | None = None) -> str | None:
    """A miniatura escolhida que vai no envio, ou None (vale a do build). Ver `pick_thumbnail`."""
    return pick_thumbnail(settings, lang, project_id)[0]


def existing_variants(settings: dict[str, Any], lang: str) -> list[dict[str, Any]]:
    data_dir = get_settings().data_dir
    return [v for v in thumbs_of(settings, lang).get("variants") or []
            if v.get("path") and (data_dir / v["path"]).is_file()]


def project_channel(project_id: str) -> str | None:
    with session_scope() as s:
        row = s.get(Project, project_id)
        return row.channel_id if row else None
