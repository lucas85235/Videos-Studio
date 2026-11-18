"""Jobs do pipeline: roteiro, storyboard, tradução, metadados, prévia, render final e piloto automático."""

from __future__ import annotations

import logging
from typing import Any

from studio.config import get_settings
from studio.core.glossary import GlossaryEntry, glossary_of
from studio.core.hashing import hash_obj
from studio.core.project import service
from studio.core.project.document import THUMB_WORDS_MAX, Metadata
from studio.core.project.pool import Pool, save_pool
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel, utcnow
from studio.providers import media_search
from studio.workers import novelty
from studio.workers.analytics import lessons as lessons_mod
from studio.workers.jobs import JobContext, get_runner, job
from studio.workers.pipeline import authoring, edit_plan, media_find, media_pool, video_clips
from studio.workers.pipeline.build import BuildCancelled, Builder

log = logging.getLogger(__name__)


def _channel_info(channel_id: str | None) -> dict[str, Any]:
    """Nome, nicho, persona e identidade visual do canal (vazio sem canal)."""
    if not channel_id:
        return {}
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            return {}
        return {"name": row.name or "", "niche": row.niche or "", "persona": row.persona or "",
                "style_bible": dict(row.style_bible or {})}


def _project_lessons(doc: Any, style: Any) -> list[dict[str, Any]]:
    """Lições aplicadas que valem para o projeto (global, canal, estilo e formato). Nunca derruba o job."""
    try:
        return lessons_mod.applied_lessons(doc.channel_id, doc.style_id, style.format.type)
    except Exception as exc:  # noqa: BLE001 - sem lições o pedido sai como antes
        log.warning("não consegui ler as lições aplicadas: %s", exc)
        return []


def _record_lessons(project_id: str, stage: str, lessons: list[dict[str, Any]], version: int | None) -> None:
    """Grava em `settings["lessons_used"][stage]` quais lições entraram no pedido desta etapa.
    Nunca derruba o job: se a gravação falhar, só o registro se perde."""
    try:
        service.update_settings_key(project_id, "lessons_used", stage,
                                    lessons_mod.usage_record(lessons, version=version))
    except Exception as exc:  # noqa: BLE001 - o roteiro já foi gravado; o registro é só um extra
        log.warning("não consegui registrar as lições usadas (%s): %s", stage, exc)


def channel_glossary(channel_id: str | None) -> list[GlossaryEntry]:
    """Glossário de tradução do canal (vazio sem canal)."""
    if not channel_id:
        return []
    with session_scope() as s:
        row = s.get(Channel, channel_id)
        return glossary_of(row.settings) if row is not None else []


def _gates(project_id: str) -> dict[str, Any]:
    return service.get_project_row(project_id)["settings"].get("gates", {})


# Status do projeto parado na conferência das cenas (a lista de projetos mostra "Cenas para conferir").
SCENES_REVIEW_STATUS = "storyboard_review"

# --- Pacote: título e thumb antes do roteiro ------------------------------------------------------------------------
PACKAGE_STATUS = "package_review"
PACKAGE_LONG_SIZE = (1280, 720)
PACKAGE_SHORT_SIZE = (1080, 1920)
DUP_MIN_DISTANCE = 10
"""Distância mínima (dHash de 64 bits) entre as prévias de dois candidatos: abaixo disso, são a mesma thumb."""
STUB_NOTICE = "prévia simplificada: o compositor de thumbs (fase 2) ainda não está disponível"


class PackageLocked(ValueError):
    """O pacote está travado (aprovado): destrave antes de gerar ou trocar candidatos."""


def _style_of(doc: Any) -> Any:
    return get_style(doc.style_id, doc.style_overrides)


def package_gate_on(gates: dict[str, Any], style: Any) -> bool:
    """O portão do pacote vale para este projeto: ligado nos gates (só projeto novo grava a chave) e formato efetivo
    longo (um Short derivado de estilo longo, com override de formato, nunca trava)."""
    return bool(gates.get("package", False)) and style.format.type != "short"


def package_missing(doc: Any) -> bool:
    """O pacote ainda não foi gerado: nenhum candidato automático e sem `generated_at`. O candidato manual semeado
    pela ideia (ou pelo título de trabalho) não conta: o piloto gera os outros ao lado dele."""
    pkg = doc.package
    return pkg.generated_at is None and not any(c.source != "manual" for c in pkg.candidates)


def package_size(style: Any) -> tuple[int, int]:
    return PACKAGE_SHORT_SIZE if style.format.type == "short" else PACKAGE_LONG_SIZE


def layers_hash(candidate: Any) -> str:
    """Hash das camadas + título + texto do candidato: muda quando a thumb precisa de nova prévia."""
    return hash_obj({"layers": candidate.layers.model_dump(mode="json"), "title": candidate.title,
                     "thumb_text": candidate.thumb_text})[:12]


def _dhash(img: Any) -> int:
    """dHash de 64 bits (9×8 em cinza), escrito à mão com Pillow."""
    small = img.convert("L").resize((9, 8))
    px = small.tobytes()
    bits = 0
    for row in range(8):
        for col in range(8):
            bits = (bits << 1) | (1 if px[row * 9 + col] > px[row * 9 + col + 1] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _stub_render(layers: Any, size: tuple[int, int], palette: dict[str, str]) -> Any:
    """Prévia de reserva enquanto o compositor por camadas não existe: degradê da paleta + o texto da thumb."""
    from PIL import Image, ImageDraw

    from studio.media.thumbnail import load_font

    w, h = size
    top, bottom = _rgb(palette.get("pink", "#FF2E88")), _rgb(palette.get("orange", "#FF8A1F"))
    img = Image.new("RGB", size)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        draw.line([(0, y), (w, y)], fill=tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    words = (layers.text.words if layers.text else "") or ""
    if words:
        font = load_font("Anton", max(24, int(h * (0.22 if w > h else 0.1))))
        x, y = int(w * 0.06), int(h * (0.12 if w > h else 0.4))
        draw.text((x, y), words, font=font, fill=_rgb(palette.get("yellow", "#FFD400")),
                  stroke_width=max(2, int(h * 0.015)), stroke_fill=_rgb(palette.get("ink", "#1A0B2E")))
    return img


def _compose(layers: Any, size: tuple[int, int], *, resolver: Any, palette: dict[str, str],
             logo_asset: str | None = None) -> tuple[Any, list[str]]:
    """Renderiza as camadas com o compositor por camadas (`media/thumb_compose.py`) e o resolvedor de imagens do
    projeto (`thumbs.resolver_for`: biblioteca do canal, poses do avatar, mídia do projeto, material oficial só com o
    canal liberando). Os avisos do compositor (ativo que faltou, material bloqueado) voltam na lista. Se o compositor
    falhar, sai a prévia de reserva com um aviso: nunca derruba o job."""
    try:
        from studio.media import thumb_compose
    except ImportError:  # pragma: no cover - compositor ausente
        return _stub_render(layers, size, palette), [STUB_NOTICE]
    try:
        img = thumb_compose.render(layers, size, resolver, palette, logo_asset=logo_asset)
    except Exception as exc:  # noqa: BLE001 - a prévia de reserva segura a tela; o aviso conta o que houve
        log.warning("o compositor de thumbs falhou: %s", exc)
        return _stub_render(layers, size, palette), [f"o compositor falhou ({exc}); prévia simplificada"]
    notes = [str(w) for w in (img.info.get("layout") or {}).get("warnings") or []]
    return img, list(dict.fromkeys(notes))


def _check(img: Any, candidate: Any, is_long: bool) -> dict[str, Any]:
    """Checagem de legibilidade (`media/thumb_check.py`, com o título do candidato para o `repeat_title`); sempre soma
    as métricas que o pacote mede sozinho (palavras e repetição do título). Nunca bloqueia."""
    score: dict[str, Any] = {}
    try:
        from studio.media import thumb_check

        # Título gerado: avisa acima do alvo (o manual vai até 90 sem aviso).
        title_max = authoring.PACKAGE_TITLE_TARGET if candidate.source == "auto" else None
        score = dict(thumb_check.check(img, candidate.layers, is_long, title=candidate.title, title_max=title_max)
                     or {})
        score.pop("dhash", None)  # o dup do pacote é medido aqui (contra os outros candidatos), não no check
    except ImportError:
        pass
    except Exception as exc:  # noqa: BLE001 - checagem nunca bloqueia
        score = {"warnings": [f"a checagem de legibilidade falhou: {exc}"]}
    warnings = [str(w) for w in score.get("warnings") or []]
    words = len(candidate.thumb_text.split())
    score.setdefault("words", words)
    if words > THUMB_WORDS_MAX and not any("palavra" in w for w in warnings):
        warnings.append(f"texto com {words} palavras (máximo {THUMB_WORDS_MAX})")
    layers = candidate.layers
    total = authoring.thumb_visible_words(candidate.thumb_text, layers.pill, list(layers.cards))
    score.setdefault("words_total", total)
    if total > authoring.THUMB_TOTAL_WORDS_MAX and not any("palavra" in w for w in warnings):
        warnings.append(f"texto total visível com {total} palavras (máximo {authoring.THUMB_TOTAL_WORDS_MAX})")
    if (candidate.source == "auto" and len(candidate.title) > authoring.PACKAGE_TITLE_TARGET
            and not any(w.startswith("título com") for w in warnings)):
        warnings.append(f"título com {len(candidate.title)} caracteres: o alvo é até {authoring.PACKAGE_TITLE_TARGET}")
    _, removed = authoring._title_overlap(candidate.thumb_text, candidate.title)
    score.setdefault("repeat_title", len(removed) + 1 if removed else 0)
    if removed:
        warnings.append("o texto da thumb repete o título")
    score["warnings"] = warnings
    return score


def render_package_thumbs(project_id: str, candidate_ids: list[str] | None = None,
                          progress: Any = None) -> dict[str, Any]:
    """Prévia (1280×720 no longo, 1080×1920 no Short) e checagem de cada candidato (ou só de `candidate_ids`). Não
    depende de cenas. Diversidade: se dois candidatos saem iguais (dHash < 10), o segundo troca o fundo pelo próximo
    quadro da biblioteca com a mesma tag e renderiza de novo. Grava `image` e `score` só nos candidatos cujas camadas
    não mudaram durante o render (a edição feita em paralelo vence; ela enfileira a própria prévia)."""
    from PIL import Image

    from studio.workers.visual import thumbs as visual_thumbs

    doc = service.get_doc(project_id)
    style = _style_of(doc)
    is_long = style.format.type != "short"
    size = package_size(style)
    palette = dict(style.thumbnail.palette)
    resolver = visual_thumbs.resolver_for(doc, style)
    logo = style.thumbnail.logo_asset
    wanted = set(candidate_ids or [])
    targets = [c for c in doc.package.candidates if not wanted or c.id in wanted]
    target_ids = {c.id for c in targets}
    data_dir = get_settings().data_dir
    out_dir = get_settings().outputs_dir / project_id / "package"
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = [i for i in authoring.thumbkit_manifest(doc.channel_id) if (i.get("kind") or "frame") == "frame"]
    hashes: dict[str, int] = {}
    for other in doc.package.candidates:  # as prévias que não vão ser refeitas entram na comparação
        if other.id in target_ids or not other.image:
            continue
        try:
            with Image.open(data_dir / other.image) as im:
                hashes[other.id] = _dhash(im)
        except OSError:
            pass
    results: dict[str, tuple[str, dict[str, Any], Any, str]] = {}
    for index, cand in enumerate(targets):
        if progress:
            progress(index / max(1, len(targets)), f"Thumb {index + 1} de {len(targets)}")
        layers = cand.layers
        img, notes = _compose(layers, size, resolver=resolver, palette=palette, logo_asset=logo)
        digest = _dhash(img)
        dup = min((_hamming(digest, h) for h in hashes.values()), default=64)
        if dup < DUP_MIN_DISTANCE and layers.background.kind == "frame" and frames:
            ref_id = (layers.background.ref or "").removeprefix("frame:")  # id puro ou "frame:<id>"
            current = next((f for f in frames if str(f["id"]) == ref_id), None)
            tags = set((current or {}).get("tags") or [])
            others = [f for f in frames if str(f["id"]) != ref_id]
            options = [f for f in others if tags & set(f.get("tags") or [])] or others
            if options:
                swap = options[index % len(options)]
                layers = layers.model_copy(deep=True)
                fresh = authoring._frame_layer(swap, layers.template)  # foco e zoom do quadro novo, como na geração
                layers.background.ref = str(swap["id"])
                layers.background.t = fresh.get("t")
                layers.background.focus_x = float(fresh["focus_x"])
                layers.background.zoom = float(fresh["zoom"])
                img, notes = _compose(layers, size, resolver=resolver, palette=palette, logo_asset=logo)
                digest = _dhash(img)
                dup = min((_hamming(digest, h) for h in hashes.values()), default=64)
                notes = [*notes, f"CORRIGIDO: fundo trocado por {swap['id']} (a thumb saía igual a outro candidato)"]
        hashes[cand.id] = digest
        probe = cand.model_copy(update={"layers": layers})
        score = _check(img, probe, is_long)
        score["dup"] = dup
        if dup < DUP_MIN_DISTANCE:
            score["warnings"].append("thumb quase igual a outro candidato")
        cand_notes = [str(n) for n in cand.score.get("notes") or []]
        score["warnings"] = list(dict.fromkeys([*score["warnings"], *notes, *cand_notes]))
        if cand_notes:
            score["notes"] = cand_notes
        name = f"{cand.id}-{layers_hash(probe)}.jpg"
        for old in out_dir.glob(f"{cand.id}-*.jpg"):
            if old.name != name:
                old.unlink(missing_ok=True)
        img.convert("RGB").save(out_dir / name, "JPEG", quality=90)
        rel = (out_dir / name).relative_to(data_dir).as_posix()
        results[cand.id] = (rel, score, layers, layers_hash(cand))
    current = service.get_doc(project_id)  # pode ter mudado durante o render
    changed = 0
    for cand in current.package.candidates:
        if cand.id not in results:
            continue
        rel, score, layers, seen = results[cand.id]
        if layers_hash(cand) != seen:
            continue
        cand.layers, cand.image, cand.score = layers, rel, score
        changed += 1
    version = (service.commit(project_id, current, f"Prévias das thumbs do pacote ({changed})", author="ai")
               if changed else None)
    return {"version": version, "rendered": changed, "candidates": list(results)}


def _planned_publish_date(project_id: str, given: Any = None) -> Any:
    """Data prevista de publicação para o pacote: a do payload (`publish_date`, AAAA-MM-DD) ou a publicação agendada
    mais cedo do projeto; None sem nenhuma (o pacote usa hoje). Nunca derruba o job."""
    from datetime import date

    if given:
        try:
            return date.fromisoformat(str(given)[:10])
        except ValueError:
            log.warning("pacote: publish_date inválida: %r", given)
    try:
        from studio.models import Publication

        with session_scope() as s:
            rows = [r.scheduled_at for r in s.query(Publication).filter(Publication.project_id == project_id)
                    if r.scheduled_at is not None]
        return min(rows).date() if rows else None
    except Exception as exc:  # noqa: BLE001 - data prevista é só uma dica para o prompt
        log.warning("pacote: data prevista indisponível (%s)", exc)
        return None


def generate_package(project_id: str, payload: dict[str, Any], *, job_id: str | None = None,
                     progress: Any = None) -> dict[str, Any]:
    """Gera os candidatos (título + thumb) e as prévias. Payload: `{n_titles?, n_thumbs?, keep_manual=True,
    append=False}`. Sem `append`, os candidatos automáticos antigos saem (ficam os manuais, com `keep_manual`, o
    escolhido e os aprovados). Com o pacote travado, recusa (`PackageLocked`)."""
    doc = service.get_doc(project_id)
    if doc.package.locked:
        raise PackageLocked("o pacote está aprovado: destrave para gerar outros candidatos")
    style = _style_of(doc)
    channel = _channel_info(doc.channel_id)
    lessons = _project_lessons(doc, style)
    references = authoring.package_references(doc.channel_id, doc.topic or doc.title)
    n_titles = int(payload.get("n_titles") or style.thumbnail.title_candidates)
    n_thumbs = int(payload.get("n_thumbs") or style.thumbnail.thumb_candidates)
    keep_manual = payload.get("keep_manual", True)
    if progress:
        progress(0.05, "Pensando títulos e thumbs")
    existing = doc.package.candidates
    kept = existing if payload.get("append") else [
        c for c in existing
        if (keep_manual and c.source == "manual") or c.approved or c.id == doc.package.chosen_id]
    candidates, provider = authoring.package_ideas(
        doc, channel, style, max(1, min(n_titles, 8)), max(1, min(n_thumbs, 4)), references=references,
        lessons=lessons, existing_titles=[c.title for c in kept if c.title], project_id=project_id, job_id=job_id,
        publish_date=_planned_publish_date(project_id, payload.get("publish_date")))
    current = service.get_doc(project_id)  # o documento pode ter mudado enquanto o LLM pensava
    if current.package.locked:
        raise PackageLocked("o pacote foi aprovado enquanto os candidatos eram gerados")
    kept_ids = {c.id for c in kept}
    current.package.candidates = [c for c in current.package.candidates if c.id in kept_ids] + candidates
    current.package.references = [r["id"] for r in references]
    current.package.generated_at = utcnow().isoformat(timespec="seconds")
    current = type(current).model_validate(current.model_dump())
    version = service.commit(project_id, current,
                             f"Pacote: {len(candidates)} candidatos de título e thumb ({provider})", author="ai")
    _record_lessons(project_id, "package", lessons, version)
    sub = (lambda f, m="": progress(0.5 + 0.5 * f, m)) if progress else None
    if progress:
        progress(0.5, "Renderizando as thumbs")
    thumbs = render_package_thumbs(project_id, [c.id for c in candidates] + [c.id for c in kept if not c.image],
                                   progress=sub)
    return {"version": thumbs["version"] or version, "provider": provider, "candidates": len(candidates),
            "kept": len(kept), "references": len(references)}


def approve_package(project_id: str, chosen_id: str | None = None, approved_ids: list[str] | None = None) -> int:
    """Trava o pacote no candidato escolhido: o título do projeto, o título dos metadados e o texto da thumb do idioma
    principal passam a ser os dele. `approved_ids` (opcional) troca os aprovados do A/B. Devolve a versão gravada."""
    doc = service.get_doc(project_id)
    if chosen_id is not None:
        if doc.package.candidate(chosen_id) is None:
            raise ValueError(f"candidato não encontrado: {chosen_id}")
        doc.package.chosen_id = chosen_id
    chosen = doc.chosen_package()
    if chosen is None:
        raise ValueError("escolha um candidato antes de aprovar")
    if not chosen.title.strip():
        raise ValueError("o candidato escolhido não tem título")
    if approved_ids is not None:
        wanted = set(approved_ids)
        for cand in doc.package.candidates:
            cand.approved = cand.id in wanted
    chosen.approved = True
    doc.package.locked = True
    doc.title = chosen.title[:200]
    lang = doc.primary_language
    meta = doc.metadata.get(lang) or Metadata()
    before = (meta.title, doc.thumbnail.text.get(lang, ""))
    if before != (chosen.title[:100], chosen.thumb_text[:60] if chosen.thumb_text else before[1]):
        # Outro par aprovado: o título e o texto da thumb dos outros idiomas eram do par antigo. Saem, e a próxima
        # tradução (ou os metadados do idioma) parte do candidato novo.
        for other in [lg for lg in {*doc.languages, *doc.metadata, *doc.thumbnail.text} if lg != lang]:
            doc.thumbnail.text.pop(other, None)
            if other in doc.metadata:
                doc.metadata[other].title = ""
    meta.title = chosen.title[:100]
    doc.metadata[lang] = meta
    if chosen.thumb_text:
        doc.thumbnail.text[lang] = chosen.thumb_text[:60]
    return service.commit(project_id, doc, f"Pacote aprovado: {chosen.title}")


@job("package")
def run_package(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Pacote (título e thumb): candidatos + prévias. Projeto ainda sem roteiro passa para `package_review`."""
    project_id = ctx.project_id
    assert project_id
    result = generate_package(project_id, payload, job_id=ctx.job_id, progress=ctx.progress)
    doc = service.get_doc(project_id)
    if not doc.segments and service.get_project_row(project_id)["status"] in ("draft", PACKAGE_STATUS):
        service.set_status(project_id, PACKAGE_STATUS)
    elif doc.segments:
        result["warnings"] = ["O roteiro foi escrito antes do pacote: o gancho pode não cumprir a promessa da thumb."]
    ctx.progress(1.0, "Títulos e thumbs prontos para escolher")
    return result


@job("package_thumbs")
def run_package_thumbs(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prévia e checagem das thumbs do pacote (todas ou `candidate_ids`). Não depende de cenas."""
    project_id = ctx.project_id
    assert project_id
    result = render_package_thumbs(project_id, payload.get("candidate_ids") or None, progress=ctx.progress)
    ctx.progress(1.0, "Thumbs atualizadas")
    return result


def _auto_package(ctx: JobContext, payload: dict[str, Any]) -> list[str]:
    """Short e série (portão do pacote gravado desligado na criação): gera o pacote sem parar e trava o 1º candidato,
    para o roteiro já sair com o título e a promessa. Falha vira aviso (o roteiro sai como antes)."""
    project_id = ctx.project_id
    assert project_id
    try:
        generate_package(project_id, {k: v for k, v in payload.items() if k in ("n_titles", "n_thumbs")},
                         job_id=ctx.job_id, progress=ctx.progress)
        doc = service.get_doc(project_id)
        if doc.package.candidates:
            approve_package(project_id, doc.package.candidates[0].id)
        return []
    except Exception as exc:  # noqa: BLE001 - sem pacote, o roteiro sai como antes
        log.warning("o pacote automático falhou: %s", exc)
        return [f"O pacote de título e thumb não saiu: {exc}"]


@job("script")
def run_script(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.1, "Escrevendo o roteiro")
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    lessons = _project_lessons(doc, style)
    new_doc, provider = authoring.generate_script(doc, style, project_id=project_id, job_id=ctx.job_id,
                                                  persona=_channel_info(doc.channel_id).get("persona", ""),
                                                  lessons=lessons)
    version = service.commit(project_id, new_doc, f"Roteiro gerado ({provider})", author="ai")
    _record_lessons(project_id, "script", lessons, version)
    service.set_status(project_id, "script_review")
    ctx.progress(1.0, "Roteiro pronto para revisão")
    return {"version": version, "provider": provider, "segments": len(new_doc.segments)}


@job("storyboard")
def run_storyboard(ctx: JobContext, payload: dict[str, Any], *, find_inline: bool = False) -> dict[str, Any]:
    """`find_inline`: a busca de arte oficial e notícias roda aqui mesmo (piloto automático); senão vira um job
    `media_find` próprio na fila."""
    project_id = ctx.project_id
    assert project_id
    ctx.progress(0.1, "Planejando as cenas")
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    pool, edit_warnings = _gather_pool(project_id, doc, style)
    new_doc, provider, review = authoring.storyboard(
        doc, style, project_id=project_id, job_id=ctx.job_id,
        channel=_channel_info(doc.channel_id), styles=authoring.style_catalog(style),
    )
    edit_warnings += _attach_and_plan(new_doc, style, pool)
    counts = review["counts"]
    message = f"Cenas planejadas ({provider}): {counts['scenes']} cenas, {counts['new_images']} imagens novas"
    version = service.commit(project_id, new_doc, message, author="ai")
    service.update_settings(project_id, {"storyboard_review": {**review, "provider": provider, "version": version}})
    # Com a parada das cenas ligada, as cenas novas esperam a conferência (storyboard() zera a aprovação).
    service.set_status(project_id, SCENES_REVIEW_STATUS if _gates(project_id).get("storyboard", False) else "storyboard")
    _prefetch_media(ctx, new_doc, style)
    result = {"version": version, "provider": provider, "scenes": counts["scenes"], "new_images": counts["new_images"],
              "reused": counts["reused"], "cards": counts["cards"]}
    if counts.get("real_hints"):
        result["real_hints"] = counts["real_hints"]
    warning: str | None = None
    if ctx.cancelled():
        pass  # cancelado: a busca na web (paga) não começa
    elif find_inline:
        found, warning = _find_media(ctx, new_doc)
        if found:
            result["media_find"] = found
    else:
        find_job, warning = media_find.queue_find(project_id, new_doc, get_runner().submit)
        if find_job:
            result["media_find_job"] = find_job
    ctx.progress(1.0, "Cenas prontas para revisão")
    warnings = [*edit_warnings, *([warning] if warning else [])]
    if warnings:
        result["warnings"] = warnings
    return result


def _gather_pool(project_id: str, doc: Any, style: Any) -> tuple[Pool, list[str]]:
    """Acervo de material real ANTES do storyboard (ADR 0004): coletado, gravado em `pool.json` (o storyboard lê de
    lá com `load_pool`) e devolvido. Acervo vazio não grava nada. Falha vira aviso: o storyboard sai como antes."""
    try:
        pool = media_pool.gather(doc, style)
        if pool.items or pool.warnings:
            save_pool(project_id, pool)
        return pool, list(pool.warnings)
    except Exception as exc:  # noqa: BLE001 - sem acervo, o storyboard segue com IA e cartões
        log.warning("a coleta do acervo falhou: %s", exc)
        return Pool(), [f"O acervo de material real não saiu: {exc}"]


def _attach_and_plan(doc: Any, style: Any, pool: Pool) -> list[str]:
    """Depois do storyboard e antes de gravar: anexa a mídia escolhida do acervo e planeja subplanos e eventos (no
    próprio `doc`). Falha vira aviso e a cena fica como o storyboard deixou."""
    warnings: list[str] = []
    try:
        warnings += media_pool.attach_chosen(doc, pool)
    except Exception as exc:  # noqa: BLE001
        log.warning("o anexo do acervo falhou: %s", exc)
        warnings.append(f"A mídia do acervo não foi anexada: {exc}")
    try:
        edit_plan.plan(doc, style, pool)
    except Exception as exc:  # noqa: BLE001
        log.warning("o planejador de edição falhou: %s", exc)
        warnings.append(f"O planejador de edição falhou: {exc}")
    return warnings


def _find_media(ctx: JobContext, doc: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Busca na web a arte oficial e as notícias das sugestões (uma chamada do Claude Code para o vídeo inteiro), só
    online e com busca web. Falha vira aviso, nunca derruba o storyboard."""
    assert ctx.project_id
    return media_find.auto_find(ctx.project_id, doc, job_id=ctx.job_id,
                                progress=lambda f, m: ctx.progress(0.92 + 0.07 * f, m), cancelled=ctx.cancelled)


@job("media_find")
def run_media_find(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Acha na web arte oficial e imagens de notícia para as sugestões de mídia real pendentes. Payload:
    `{refresh?}` (busca de novo as sugestões já buscadas)."""
    assert ctx.project_id
    return media_find.run_media_find(ctx.project_id, refresh=bool(payload.get("refresh")), job_id=ctx.job_id,
                                     progress=ctx.progress, cancelled=ctx.cancelled)


def _prefetch_media(ctx: JobContext, doc: Any, style: Any) -> None:
    """Busca de antemão as fotos livres das sugestões (só online): o usuário abre a cena e já vê os candidatos.
    Nunca derruba o job."""
    if get_settings().offline:
        return
    if not any(s.real is not None and s.real.kind == "photo" and s.real.query.strip() and s.visual.media is None
               for s in doc.scenes):
        return
    ctx.progress(0.9, "Buscando fotos livres para as sugestões")
    try:
        media_search.prefetch(doc, style, budget_s=25)
    except Exception as exc:  # noqa: BLE001 - a busca é só uma ajuda
        log.warning("a pré-busca de fotos livres falhou: %s", exc)


@job("translate")
def run_translate(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    targets: list[str] = payload.get("langs") or []
    # Só os textos de tela que faltam (cartões e sobreposições criados depois da tradução): a narração fica.
    screen_only = bool(payload.get("screen_only"))
    doc = service.get_doc(project_id)
    targets = targets or [lang for lang in doc.languages if lang != doc.primary_language]
    if payload.get("shorten"):
        # Modo B: só encurta os trechos que passam do tempo (correções manuais e trechos travados ficam).
        shortened: dict[str, list[str]] = {}
        for index, lang in enumerate(targets):
            ctx.progress(index / max(1, len(targets)), f"Encurtando a tradução em {authoring.lang_name(lang)}")
            doc, ids = authoring.shorten_translation(doc, lang, project_id=project_id, job_id=ctx.job_id)
            if ids:
                shortened[lang] = ids
        total = sum(len(ids) for ids in shortened.values())
        version = service.commit(project_id, doc, f"Tradução encurtada para caber no tempo ({total} trechos)",
                                 author="ai") if shortened else None
        return {"version": version, "langs": sorted(shortened), "shortened": shortened}
    glossary = channel_glossary(doc.channel_id)
    done = []
    for index, lang in enumerate(targets):
        ctx.progress(index / max(1, len(targets)), f"Traduzindo para {authoring.lang_name(lang)}")
        doc, _provider = authoring.translate(doc, lang, screen_only=screen_only, project_id=project_id, job_id=ctx.job_id,
                                             glossary=glossary)
        done.append(lang)
    message = f"{'Tradução dos textos de tela' if screen_only else 'Tradução'}: {', '.join(done)}"
    version = service.commit(project_id, doc, message, author="ai") if done else None
    result: dict[str, Any] = {"version": version, "langs": done}
    # Conferência do glossário: o aviso aparece no roteiro (o detalhe do projeto refaz a conta a cada abertura).
    issues = authoring.glossary_issues(doc, glossary, done)
    if issues:
        result["glossary_warnings"] = [f"{i['lang']} · {i['id']}: {i['message']}" for i in issues]
    return result


@job("metadata")
def run_metadata(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    langs = payload.get("langs") or doc.languages
    chapters = payload.get("chapters") or []
    lessons = _project_lessons(doc, style)
    for index, lang in enumerate(langs):
        ctx.progress(index / max(1, len(langs)), f"Metadados em {authoring.lang_name(lang)}")
        doc, _ = authoring.metadata(doc, style, lang, chapters=chapters, project_id=project_id, job_id=ctx.job_id,
                                    lessons=lessons)
    version = service.commit(project_id, doc, "Metadados (título, descrição, tags)", author="ai")
    _record_lessons(project_id, "metadata", lessons, version)
    return {"version": version, "langs": langs}


def _build(ctx: JobContext, payload: dict[str, Any], mode: str) -> dict[str, Any]:
    project_id = ctx.project_id
    assert project_id
    version = service.get_project_row(project_id)["current_version"]
    builder = Builder(project_id, mode=mode, langs=payload.get("langs"), sink=ctx)  # type: ignore[arg-type]
    result = {**builder.run(), "version": version}
    result["built_at"] = utcnow().isoformat()  # ordem de renderização (música sem repetir no canal)
    service.update_settings(project_id, {f"last_{mode}": result})
    # Impressão digital para o verificador de novidade: feita aqui, no job, e não na hora de conferir.
    novelty.refresh(project_id)
    if mode == "final":
        # Conferência de novidade (só aviso): imagens, miniatura e título parecidos com os últimos vídeos do canal.
        extra = [w for w in novelty.safe_warnings(project_id) if w not in result["warnings"]]
        if extra:
            result["warnings"] = [*result["warnings"], *extra]
            service.update_settings(project_id, {f"last_{mode}": result})
    service.set_status(project_id, "preview_ready" if mode == "preview" else "rendered")
    # Rotina: com limite de espaço e limpeza automática ligados, o cache volta ao limite depois do build.
    from studio.core.graph import gc

    gc.auto_clean(exclude_job=ctx.job_id)
    return result


class _PartSink:
    """Progresso de uma parte do job (ex.: a 2ª de 2 versões) dentro da barra do job inteiro."""

    def __init__(self, ctx: JobContext, start: float, size: float, label: str) -> None:
        self.ctx, self.start, self.size, self.label = ctx, start, size, label

    def progress(self, fraction: float, message: str = "") -> None:
        self.ctx.progress(self.start + self.size * fraction, f"{self.label}: {message}" if message else self.label)

    def cancelled(self) -> bool:
        return self.ctx.cancelled()

    @property
    def job_id(self) -> str | None:
        return self.ctx.job_id


@job("ab_build")
def run_ab_build(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Prévia ou vídeo final de uma ou mais versões A/B (`branches`: "main", "b"...), uma depois da outra.

    Cada ramo grava na pasta dele (`outputs/<projeto>/ramos/<nome>`); a principal grava no lugar de sempre.
    """
    from studio.core.project import branches as br

    project_id = ctx.project_id
    assert project_id
    mode = "final" if payload.get("mode") == "final" else "preview"
    names = list(dict.fromkeys(payload.get("branches") or [br.MAIN]))
    infos = [br.get_branch(project_id, name) for name in names]
    results: dict[str, Any] = {}
    for index, info in enumerate(infos):
        if ctx.cancelled():
            raise BuildCancelled("cancelado pelo usuário")
        label = f"Versão {info['letter']}"
        sink = _PartSink(ctx, index / len(infos), 1 / len(infos), label)
        version = info["head"]
        builder = Builder(project_id, mode=mode, langs=payload.get("langs"), sink=sink,  # type: ignore[arg-type]
                          version=None if info["is_main"] else version, branch=info["name"])
        result = builder.run()
        result["built_at"] = utcnow().isoformat()  # ordem de renderização (música sem repetir no canal)
        br.save_result(project_id, info["name"], mode, result, version)
        if info["is_main"]:
            service.set_status(project_id, "preview_ready" if mode == "preview" else "rendered")
        results[info["name"]] = {"letter": info["letter"], "version": version, "langs": result["langs"],
                                 "generated": result["generated"], "reused": result["reused"]}
    ctx.progress(1.0, "Versões prontas")
    return {"mode": mode, "branches": results}


@job("preview")
def run_preview(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _build(ctx, payload, "preview")


@job("render")
def run_render(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    return _build(ctx, payload, "final")


@job("video_clips")
def run_video_clips(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Gera o vídeo IA das cenas-chave, sem passar do valor confirmado pelo usuário (`max_cost_usd`)."""
    project_id = ctx.project_id
    assert project_id
    max_cost = float(payload.get("max_cost_usd") or 0.0)
    builder = Builder(project_id, mode="preview", sink=ctx)
    ctx.progress(0.0, "Gerando os clipes de vídeo IA")
    result = video_clips.generate(builder, max_cost)
    if not result["generated"] and not result["reused"] and result["errors"]:
        raise RuntimeError("nenhum clipe de vídeo IA saiu:\n- " + "\n- ".join(result["errors"][:10]))
    ctx.progress(1.0, "Clipes prontos: atualize a prévia para ver")
    return result


@job("autopilot")
def run_autopilot(ctx: JobContext, payload: dict[str, Any]) -> dict[str, Any]:
    """Roteiro → cenas → prévia (→ render), parando nos portões de aprovação ativos.

    O portão das cenas (`gates.storyboard`) só existe nos projetos novos: sem a chave, segue direto.
    """
    project_id = ctx.project_id
    assert project_id
    gates = _gates(project_id)
    steps: list[str] = []
    doc = service.get_doc(project_id)
    notes: list[str] = []
    if not doc.segments or payload.get("rewrite"):
        # Pacote (título e thumb antes do roteiro). Só projeto novo grava a chave `gates.package`: projeto antigo segue
        # direto. Longo com o portão ligado para até o pacote ser aprovado; Short e série geram sem parar. O portão só
        # vale antes do 1º roteiro: reescrever um roteiro que já existe não para no pacote (a aprovação dele não
        # retoma o piloto quando já há roteiro).
        style = _style_of(doc)
        package_ready = doc.package.locked and doc.package.chosen_id is not None
        if (package_gate_on(gates, style) and not package_ready and not doc.segments
                and not payload.get("skip_gates")):
            if package_missing(doc):
                run_package(ctx, payload)
                steps.append("package")
            service.set_status(project_id, PACKAGE_STATUS)
            return {"steps": steps, "stopped_at": "package", "message": "Título e thumb aguardando sua escolha"}
        if "package" in gates and package_missing(doc) and not doc.package.locked and not doc.segments:
            notes = _auto_package(ctx, payload)
            steps.append("package")
        run_script(ctx, payload)
        steps.append("script")
        if gates.get("script", True) and not payload.get("skip_gates"):
            out = {"steps": steps, "stopped_at": "script", "message": "Roteiro aguardando sua aprovação"}
            return {**out, "warnings": notes} if notes else out
    doc = service.get_doc(project_id)
    extra: dict[str, Any] = {"warnings": notes} if notes else {}
    if not doc.scenes:
        # No piloto, a busca entra no mesmo job (nada corre junto); o resultado e o aviso dela vão no do piloto.
        board = run_storyboard(ctx, payload, find_inline=True)
        extra = {**extra, **{k: board[k] for k in ("media_find", "warnings") if board.get(k)}}
        if notes and board.get("warnings"):
            extra["warnings"] = [*notes, *board["warnings"]]
        steps.append("storyboard")
        doc = service.get_doc(project_id)
    if gates.get("storyboard", False) and not doc.storyboard_approved and not payload.get("skip_gates"):
        service.set_status(project_id, SCENES_REVIEW_STATUS)
        return {"steps": steps, "stopped_at": "storyboard", "message": "Cenas aguardando sua aprovação", **extra}
    other_langs = [lang for lang in doc.languages if lang != doc.primary_language]
    doc = service.get_doc(project_id)
    # Narração que falta: tradução completa. Só textos de tela (cartões de cenas refeitas depois da tradução):
    # traduz só eles, sem refazer a narração nem a voz.
    full = [lang for lang in other_langs if any(not seg.text_for(lang).strip() for seg in doc.segments)]
    screen = [lang for lang in other_langs if lang not in full and authoring.needs_translation(doc, lang)]
    if full:
        run_translate(ctx, {"langs": full})
    if screen:
        run_translate(ctx, {"langs": screen, "screen_only": True})
    if full or screen:
        steps.append("translate")
    result = run_preview(ctx, payload)
    steps.append("preview")
    if gates.get("preview", True) and not payload.get("skip_gates"):
        return {"steps": steps, "stopped_at": "preview", "message": "Prévia aguardando sua aprovação", "preview": result,
                **extra}
    final = run_render(ctx, payload)
    steps.append("render")
    return {"steps": steps, "stopped_at": None, "render": final, **extra}
