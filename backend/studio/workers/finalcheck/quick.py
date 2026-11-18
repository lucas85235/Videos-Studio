"""Conferência rápida antes de publicar (sem FFmpeg): o que dá para saber só pelo banco e pelos arquivos.

Lê o `last_final` do projeto, as afirmações da checagem de fatos (`FactClaim`, bloco F, com a mesma regra de
"desatualizada" do `factcheck.apply.is_stale`) e o relatório da conferência de áudio (`QualityReport(kind="audio")`,
bloco G) pelo banco.

A timeline do vídeo final fica em `outputs/<projeto>/<idioma>/timeline.json`, o mesmo arquivo que a Prévia regrava
(junto com `legendas.srt`). Quando o timeline.json é mais novo que o MP4 final, ele não é o do vídeo: a versão do vídeo
fica desconhecida (aviso `final_timeline`) e o que depende da timeline (gatilhos, voz × música) não é conferido.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.styles.library import get_style
from studio.core.styles.schema import Family, StyleSpec
from studio.db import session_scope
from studio.media.real_media import EDITORIAL_WARNING
from studio.models import FactClaim, QualityReport
from studio.workers.factcheck.claims import segment_hash
from studio.workers.finalcheck.measure import duration_items, item
from studio.workers.publish.credits import has_editorial

LEVELS = {"ok": 0, "aviso": 1, "erro": 2}
# Códigos que a conferência rápida recalcula sempre (no relatório salvo, valem os de agora).
QUICK_CODES = frozenset({
    "final_video", "final_version", "preview_only", "editorial", "short_duration", "short_editorial",
    "factcheck_open", "factcheck_rumor", "factcheck_outdated", "factcheck_missing", "audio_check", "thumbnail",
    "captions", "ai_label", "final_timeline",
})
# Folga entre o MP4 final e o timeline.json do mesmo render (o timeline é gravado antes do vídeo).
MTIME_SLACK_S = 1.0


def worst(items: list[dict[str, Any]]) -> str:
    level = max((LEVELS.get(i.get("level", "ok"), 0) for i in items), default=0)
    return next(k for k, v in LEVELS.items() if v == level)


@dataclass
class FinalContext:
    """O que a conferência precisa do projeto num idioma."""

    project_id: str
    lang: str
    row: dict[str, Any]
    doc: ProjectDoc
    style: StyleSpec
    last: dict[str, Any]
    entry: dict[str, Any]
    timeline: dict[str, Any] = field(default_factory=dict)
    # O timeline.json foi regravado (pela Prévia) depois do vídeo final: não descreve o MP4.
    timeline_stale: bool = False

    @property
    def video_rel(self) -> str | None:
        return self.entry.get("video") or None

    @property
    def video_path(self) -> Path | None:
        rel = self.video_rel
        return get_settings().data_dir / rel if rel else None

    @property
    def has_video(self) -> bool:
        path = self.video_path
        return bool(path and path.is_file())

    @property
    def version(self) -> int:
        try:
            return int(self.timeline.get("version") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def duration(self) -> float | None:
        value = self.timeline.get("duration") or self.entry.get("duration")
        return float(value) if value else None

    @property
    def is_short(self) -> bool:
        return self.style.format.type == "short"


def load_context(project_id: str, lang: str | None = None) -> FinalContext:
    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    style = get_style(doc.style_id, doc.style_overrides)
    lang = lang or doc.primary_language
    last = (row.get("settings") or {}).get("last_final") or {}
    entry = ((last.get("langs") or {}).get(lang)) or {}
    timeline: dict[str, Any] = {}
    stale = False
    if entry.get("timeline"):
        path = get_settings().data_dir / entry["timeline"]
        try:
            timeline = json.loads(path.read_text(encoding="utf-8"))
            video = get_settings().data_dir / entry["video"] if entry.get("video") else None
            if video is not None and video.is_file() and path.stat().st_mtime > video.stat().st_mtime + MTIME_SLACK_S:
                timeline, stale = {}, True
        except (OSError, ValueError):
            timeline = {}
    return FinalContext(project_id, lang, row, doc, style, last, entry, timeline, stale)


def _factcheck_items(ctx: FinalContext) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with session_scope() as s:
        claims = [
            {"verdict": c.verdict, "status": c.status, "segment_id": c.segment_id, "segment_hash": c.segment_hash,
             "claim": c.claim}
            for c in s.query(FactClaim).filter_by(project_id=ctx.project_id, lang=ctx.lang).all()
        ]
    # Mesma regra do `factcheck.apply.is_stale`: texto do idioma, sem cair no principal; segmento apagado = desatualizada.
    texts = {seg.id: seg.text.get(ctx.lang, "") for seg in ctx.doc.segments}

    def stale(c: dict[str, Any]) -> bool:
        text = texts.get(c["segment_id"])
        return text is None or segment_hash(text) != c["segment_hash"]

    # Afirmação aberta de trecho que mudou ou saiu não trava: vira "checagem desatualizada".
    opened = [c for c in claims if c["status"] == "aberta" and not stale(c)]
    bad = [c for c in opened if c["verdict"] in ("corrigir", "sem_fonte")]
    if bad:
        n = len(bad)
        out.append(item("factcheck_open", "erro",
                        f"{n} afirmaç{'ões' if n > 1 else 'ão'} sem fonte ou a corrigir",
                        "o que não tem fonte sai do roteiro: corrija ou tire na Checagem de fatos (aba Roteiro). "
                        f"Ex.: \"{bad[0]['claim'][:90]}\""))
    rumors = [c for c in opened if c["verdict"] == "boato"]
    if rumors:
        n = len(rumors)
        out.append(item("factcheck_rumor", "aviso", f"{n} boato{'s' if n > 1 else ''} na checagem",
                        "deixe claro na fala que é boato ou tire do roteiro."))
    outdated = {c["segment_id"] for c in claims if c["status"] != "ignorada" and stale(c)}
    if outdated:
        n = len(outdated)
        out.append(item("factcheck_outdated", "aviso", f"Checagem desatualizada em {n} trecho{'s' if n > 1 else ''}",
                        "o roteiro mudou depois da checagem: rode a Checagem de fatos de novo."))
    checked = ((ctx.row.get("settings") or {}).get("factcheck") or {}).get(ctx.lang)
    if ctx.style.script.fact_check and not claims and not checked:
        out.append(item("factcheck_missing", "aviso", "Roteiro sem checagem de fatos",
                        "o estilo pede checagem: rode a Checagem de fatos na aba Roteiro."))
    return out


def _audio_items(ctx: FinalContext) -> list[dict[str, Any]]:
    with session_scope() as s:
        report = (s.query(QualityReport).filter_by(project_id=ctx.project_id, kind="audio", lang=ctx.lang)
                  .order_by(QualityReport.created_at.desc(), QualityReport.id.desc()).first())
        found = (list(report.items or []), report.version, report.source) if report is not None else None
    if found is None:
        return [item("audio_check", "ok", "Conferência do áudio ainda não rodou",
                     "rode a conferência de áudio na aba Prévia (painel Trilha).")]
    items, version, source = found
    # Só vale o relatório do mesmo mix do vídeo final ou da mesma versão; um antigo não trava o envio.
    same_mix = bool(source) and source == ctx.entry.get("mix")
    same_version = bool(ctx.version) and version == ctx.version
    if not (same_mix or same_version):
        issues = any(i.get("level") in ("erro", "aviso") for i in items)
        return [item("audio_check", "aviso" if issues else "ok", "A conferência do áudio é de outra versão",
                     "ela não vale para este vídeo final: rode a conferência de áudio de novo na aba Prévia.")]
    out = [
        item("audio_check", "erro", f"Áudio: {i.get('title') or 'problema'}", i.get("detail") or "",
             i.get("at_s"), i.get("scene_id"))
        for i in items if i.get("level") == "erro"
    ]
    warns = [i for i in items if i.get("level") == "aviso"]
    if warns:
        n = len(warns)
        titles = "; ".join(str(i.get("title") or "") for i in warns[:3])
        out.append(item("audio_check", "aviso", f"Conferência do áudio com {n} aviso{'s' if n > 1 else ''}",
                        titles + ("…" if n > 3 else "")))
    return out


def _thumbnail_ok(ctx: FinalContext) -> bool:
    data_dir = get_settings().data_dir
    thumbs = ((ctx.row.get("settings") or {}).get("thumbs") or {}).get(ctx.lang) or {}
    variants = thumbs.get("variants") or []
    chosen = thumbs.get("chosen") or 1
    if isinstance(chosen, int) and 0 < chosen <= len(variants):
        path = (variants[chosen - 1] or {}).get("path")
        if path and (data_dir / path).is_file():
            return True
    return bool(ctx.entry.get("thumbnail") and (data_dir / ctx.entry["thumbnail"]).is_file())


def _ai_item(style: StyleSpec) -> dict[str, Any]:
    """Mesma regra de `publish_tasks.youtube_status_body` (containsSyntheticMedia)."""
    if style.publish.ai_label == "never":
        return item("ai_label", "ok", "O envio não declara conteúdo feito com IA", "o estilo desliga essa marcação.")
    synthetic = style.art.photoreal or style.publish.ai_label == "always" or Family.ai_video in style.all_families()
    if synthetic:
        return item("ai_label", "ok", "O envio marca o vídeo como conteúdo feito com IA",
                    "o YouTube mostra o selo de conteúdo alterado ou sintético.")
    return item("ai_label", "ok", "O envio declara que não há conteúdo sintético realista",
                "as imagens do estilo não são fotorrealistas.")


def quick_items(ctx: FinalContext) -> list[dict[str, Any]]:
    """Avisos rápidos na ordem do ADR."""
    if not ctx.has_video:
        return [item("final_video", "erro", "Sem vídeo final deste idioma", "renderize o vídeo final antes de publicar.")]
    out: list[dict[str, Any]] = []
    current = int(ctx.row.get("current_version") or 0)
    if ctx.timeline_stale:
        out.append(item("final_timeline", "aviso", "A Prévia foi refeita depois do vídeo final",
                        "não dá para saber de que versão é o vídeo, e a timeline e as legendas salvas agora são as da "
                        "prévia: renderize o vídeo final de novo antes de publicar."))
    elif ctx.version and current and ctx.version != current:
        out.append(item("final_version", "aviso", f"O vídeo final é da versão {ctx.version}; o projeto está na {current}",
                        "houve mudanças depois do render: renderize de novo para incluí-las."))
    preview_only = ctx.last.get("preview_only_providers") or []
    if preview_only:
        out.append(item("preview_only", "erro", "Voz ou imagens de teste no vídeo",
                        f"o render usou provedores só de prévia ({', '.join(preview_only)}): envie como privado ou "
                        "configure provedores comerciais e renderize de novo.")
                   # "Publicar mesmo assim" não libera: o job de envio recusa do mesmo jeito (publish_tasks).
                   | {"blocking": True})
    editorial = has_editorial(ctx.doc)
    if editorial:
        out.append(item("editorial", "aviso", "Material oficial no vídeo", EDITORIAL_WARNING))
    out.extend(duration_items(ctx.is_short, ctx.duration, editorial))
    out.extend(_factcheck_items(ctx))
    out.extend(_audio_items(ctx))
    if not _thumbnail_ok(ctx):
        out.append(item("thumbnail", "aviso", "Sem miniatura", "o vídeo sobe com a imagem que o YouTube escolher."))
    srt = ctx.entry.get("srt")
    if not srt or not (get_settings().data_dir / srt).is_file():
        out.append(item("captions", "aviso", "Sem legendas", "o vídeo sobe sem o arquivo de legendas."))
    out.append(_ai_item(ctx.style))
    return out


# --- Conferência da edição (ADR 0004 §3.10): itens `perfil.*` a partir da timeline, sem FFmpeg ----------------------

EDIT_CODES = ("perfil.ia", "perfil.ritmo", "perfil.material", "perfil.sfx", "perfil.eventos", "perfil.parado",
              "perfil.repeticao", "perfil.licenca")
UNKNOWN_LICENSES = frozenset({"", "desconhecida", "unknown", "?"})
PROV_LABELS = {"own": "material próprio", "official": "material oficial", "capture": "captura de página",
               "free": "foto livre", "stock": "banco de imagens", "graphic": "gráfico", "meme": "meme",
               "ai": "imagem de IA"}


def _pct(value: float) -> str:
    return f"{round(value * 100):d}%"


def _n(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _library_licenses() -> dict[str, str]:
    """Licença de cada item das bibliotecas de SFX e memes (manifestos `library/sfx|memes/manifest.json` do usuário e
    o do pacote básico do repositório). Biblioteca ainda inexistente: vazio."""
    from studio.config import REPO_ROOT

    out: dict[str, str] = {}
    paths = [REPO_ROOT / "assets" / "sfx" / "vulto-basico" / "manifest.json",
             get_settings().library_dir / "sfx" / "manifest.json",
             get_settings().library_dir / "memes" / "manifest.json"]
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        rows = data.get("items", []) if isinstance(data, dict) else data
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and row.get("id"):
                out[str(row["id"])] = str(row.get("license") or "")
    return out


def _license_items(doc: ProjectDoc, timeline: dict[str, Any], starts: dict[str, float]) -> list[dict[str, Any]]:
    """Meme anexado sem licença e inserto da biblioteca (SFX ou meme, por `lib_id`) com licença desconhecida."""
    out: list[dict[str, Any]] = []
    for scene in doc.ordered_scenes():
        media = scene.visual.media
        if media is not None and media.kind == "meme" and media.license.strip().lower() in UNKNOWN_LICENSES:
            out.append(item("perfil.licenca", "aviso", "Meme sem licença declarada",
                            "material de terceiros só entra com licença registrada: declare a licença ou troque.",
                            starts.get(scene.id), scene.id))
    libs: list[tuple[str, str]] = []
    for scene in doc.scenes:
        libs += [(str(fx.params["lib_id"]), scene.id) for fx in scene.fx if fx.params.get("lib_id")]
    libs += [(str(e["lib_id"]), str(e.get("scene") or e.get("scene_id") or "")) for e in timeline.get("sfx") or []
             if isinstance(e, dict) and e.get("lib_id")]
    if libs:
        licenses = _library_licenses()
        seen: set[str] = set()
        for lib_id, scene_id in libs:
            if lib_id in seen or lib_id not in licenses:
                continue
            seen.add(lib_id)
            if licenses[lib_id].strip().lower() in UNKNOWN_LICENSES:
                out.append(item("perfil.licenca", "aviso", f"Inserto \"{lib_id}\" com licença desconhecida",
                                "som ou meme sem licença conhecida fica fora do automático: troque ou declare a "
                                "licença na biblioteca.", starts.get(scene_id), scene_id or None))
    return out


def edit_items(timeline: dict[str, Any], doc: ProjectDoc, style: StyleSpec, profile: Any = None,
               fp: Any = None) -> list[dict[str, Any]]:
    """Itens `perfil.*` da edição a partir da timeline (um `ok` por código quando está tudo certo; `perfil.material`
    e `perfil.licenca` podem ter mais de um aviso). `profile`: o `EditProfile` da referência (alvos medidos). `fp`:
    impressão a usar no lugar da medida da timeline (a do MP4 mesclada, na conferência do render); os tempos e as cenas
    dos itens continuam saindo da timeline. Nenhum item é erro: edição é gosto, não trava o envio."""
    from studio.core.editing import metrics

    measure = metrics.measure_timeline(timeline, doc)
    if not measure.shots:
        return [item("perfil.ia", "ok", "Edição não medida", "a timeline não tem cenas.")]
    fp = fp or measure.fp
    t = metrics.targets_for(style, profile)
    index = metrics.ai_look_index(fp, style, profile)
    starts: dict[str, float] = {}
    for shot in measure.shots:
        starts.setdefault(shot.scene_id, shot.start)
    out: list[dict[str, Any]] = []

    def longest_run(prov: str) -> tuple[float | None, str | None]:
        best, best_at, best_scene = 0.0, None, None
        run, run_at, run_scene = 0.0, 0.0, None
        for shot in measure.shots:
            if shot.prov != prov:
                run = 0.0
                continue
            if run == 0:
                run_at, run_scene = shot.start, shot.scene_id
            run += shot.dur
            if run > best:
                best, best_at, best_scene = run, run_at, run_scene
        return best_at, best_scene

    # IA na tela (com o índice no detalhe)
    ai = metrics.ai_screen_share(fp)
    summary = f"Índice \"cara de IA\": {index.score} de 100 ({index.band})."
    title = f"Imagem de IA em {_pct(ai)} da tela (teto {_pct(t.ai_max_share)})"
    if ai > t.ai_max_share + 1e-9:
        at, sid = longest_run("ai")
        out.append(item("perfil.ia", "aviso", title,
                        f"{summary} Troque IA por material real nas cenas marcadas (Acervo) ou reedite.", at, sid))
    else:
        out.append(item("perfil.ia", "ok", title, summary))

    # Ritmo dos planos
    lo, hi = t.shot_median_band
    pace_issues = []
    if fp.pace.median_s < lo or fp.pace.median_s > hi:
        pace_issues.append(f"plano mediano de {_n(fp.pace.median_s)} s (alvo de {_n(lo)} a {_n(hi)} s)")
    if fp.pace.cv < t.cv_min:
        pace_issues.append(f"durações parecidas demais (variação {_n(fp.pace.cv)}, mínimo {_n(t.cv_min)})")
    if pace_issues:
        out.append(item("perfil.ritmo", "aviso", "Ritmo de slideshow", "; ".join(pace_issues) + "."))
    else:
        out.append(item("perfil.ritmo", "ok",
                        f"Ritmo dos planos: mediana {_n(fp.pace.median_s)} s, variação {_n(fp.pace.cv)}"))

    # Material: gancho, real, oficial e sequência do mesmo tipo
    material: list[dict[str, Any]] = []
    if fp.hook_prov == "ai":
        material.append(item("perfil.material", "aviso", "O gancho abre com imagem de IA",
                             "os 3 primeiros segundos pedem material real (oficial, captura ou foto).", 0.0,
                             measure.shots[0].scene_id))
    real = metrics.real_share(fp)
    if real < t.real_share_min:
        material.append(item("perfil.material", "aviso", f"Material real em {_pct(real)} da tela",
                             f"o alvo é pelo menos {_pct(t.real_share_min)}."))
    official = fp.materials.prov_mix.get("official", 0.0)
    if official > t.official_max_share + 1e-9:
        at, sid = longest_run("official")
        material.append(item("perfil.material", "aviso", f"Material oficial em {_pct(official)} da tela",
                             f"acima de {_pct(t.official_max_share)} cresce o risco de Content ID.", at, sid))
    if fp.materials.max_same_prov_run > t.max_run_shots or fp.materials.max_same_prov_run_s > t.max_run_s:
        run, run_at, run_scene, prev = 0, 0.0, "", ""
        best: tuple[int, float, str, str] = (0, 0.0, "", "")
        for shot in measure.shots:
            if shot.prov == prev:
                run += 1
            else:
                run, run_at, run_scene, prev = 1, shot.start, shot.scene_id, shot.prov
            if run > best[0]:
                best = (run, run_at, run_scene, prev)
        label = PROV_LABELS.get(best[3], best[3])
        material.append(item("perfil.material", "aviso",
                             f"{fp.materials.max_same_prov_run} planos seguidos de {label}",
                             f"troque o tipo de mídia a cada {t.max_run_shots} planos ou {_n(t.max_run_s)} s.",
                             best[1], best[2] or None))
    out += material or [item("perfil.material", "ok", f"Material real em {_pct(real)} da tela")]

    # SFX (sem contar os da transição)
    s_lo, s_hi = t.sfx_band
    sfx = fp.audio.sfx_per_min
    if sfx < s_lo or sfx > s_hi:
        how = "poucos" if sfx < s_lo else "muitos"
        out.append(item("perfil.sfx", "aviso", f"{_n(sfx)} efeitos sonoros por minuto ({how})",
                        f"alvo de {_n(s_lo)} a {_n(s_hi)} por minuto, sem contar os da transição."))
    else:
        out.append(item("perfil.sfx", "ok", f"{_n(sfx)} efeitos sonoros por minuto"))

    # Eventos (cortes + punch + fx + insertos)
    events = fp.pace.events_per_min
    floor = t.events_min * (1 - min(t.tolerance, 0.9))
    if events < floor:
        out.append(item("perfil.eventos", "aviso", f"{_n(events)} eventos por minuto",
                        f"cortes, punch, textos e insertos somam menos que {_n(floor)} por minuto (alvo "
                        f"{_n(t.events_min)})."))
    else:
        out.append(item("perfil.eventos", "ok", f"{_n(events)} eventos por minuto"))

    # Maior trecho parado
    if fp.pace.max_still_s > t.max_still_s:
        still_at: float | None = None
        still_scene: str | None = None
        if fp is measure.fp:  # a medida do MP4 não diz onde: só a da timeline aponta o trecho
            still_at = measure.still[0]
            still_scene = next((s.scene_id for s in measure.shots if s.start <= still_at < s.end), None)
        out.append(item("perfil.parado", "aviso", f"{_n(fp.pace.max_still_s)} s sem nenhum evento visual",
                        f"o máximo é {_n(t.max_still_s)} s: um corte, punch ou texto quebra o trecho.", still_at,
                        still_scene))
    else:
        out.append(item("perfil.parado", "ok", f"Maior trecho sem evento: {_n(fp.pace.max_still_s)} s"))

    # Repetições
    if fp.repeat_hits:
        rep_at, rep_scene = measure.repeats[0] if measure.repeats else (None, None)
        n = fp.repeat_hits
        out.append(item("perfil.repeticao", "aviso", f"{n} repetiç{'ões' if n > 1 else 'ão'} de imagem ou meme",
                        f"a mesma imagem aparece em mais de {metrics.MAX_APPEARANCES} cenas, ou o mesmo meme volta.",
                        rep_at, rep_scene))
    else:
        out.append(item("perfil.repeticao", "ok", "Sem repetição de imagem ou meme"))

    out += _license_items(doc, timeline, starts) or [item("perfil.licenca", "ok", "Insertos com licença registrada")]
    return out
