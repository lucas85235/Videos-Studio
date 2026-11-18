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
    "captions", "ai_label", "final_timeline", "library_license",
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


def library_items(doc: ProjectDoc) -> list[dict[str, Any]]:
    """Licença do que veio da biblioteca de efeitos e de memes (ADR 0004 §3.8), pelos eventos das cenas (`Scene.fx`):

    - SFX de licença desconhecida escolhido pelo planejador ou pelo LLM: `erro` (ninguém declarou a licença); pelo
      usuário: `aviso`;
    - SFX ou meme CC BY: `aviso` com a linha de crédito a pôr na descrição (o crédito ainda não sai sozinho);
    - meme `fair_comment` (material de terceiros): `aviso` de reivindicação.
    Um item sem os campos (projeto antigo) não gera nada."""
    from studio.media import meme_library, sfx_library

    unknown_auto: list[str] = []
    unknown_user: list[str] = []
    credits: dict[str, str] = {}
    claims: list[str] = []
    try:
        for scene in doc.ordered_scenes():
            for event in scene.fx:
                params = event.params or {}
                sfx_id = params.get("lib_id") if event.type == "sfx" else params.get("sfx_id")
                if isinstance(sfx_id, str) and sfx_id:
                    sfx = sfx_library.get(sfx_id)
                    if sfx is not None and sfx.license == sfx_library.UNKNOWN_LICENSE:
                        (unknown_user if event.origin == "user" else unknown_auto).append(sfx.id)
                    elif sfx is not None and sfx.license == "cc_by":
                        credits[f"sfx:{sfx.id}"] = f"Efeitos sonoros: {sfx.credit()}"
                meme_id = params.get("lib_id") if event.type == "meme" else None
                meme = meme_library.get(meme_id) if isinstance(meme_id, str) else None
                if meme is not None and meme.license == "cc_by":
                    credits[f"meme:{meme.id}"] = f"Meme: {meme.credit()}"
                elif meme is not None and meme.license == "fair_comment":
                    claims.append(meme.id)
    except Exception:  # noqa: BLE001 - a biblioteca ilegível não derruba a conferência
        return []
    out: list[dict[str, Any]] = []
    names = lambda ids: ", ".join(f"'{i}'" for i in dict.fromkeys(ids))  # noqa: E731
    if unknown_auto:
        out.append(item("library_license", "erro", "Efeito de licença desconhecida escolhido automaticamente",
                        f"{names(unknown_auto)} entrou pelo planejador ou pelo LLM sem licença declarada: declare a "
                        "licença na biblioteca de efeitos (Configurações) ou troque o efeito."))
    if unknown_user:
        out.append(item("library_license", "aviso", "Efeito de licença desconhecida no vídeo",
                        f"{names(unknown_user)}: você escolheu, mas a licença não está declarada na biblioteca."))
    if credits:
        out.append(item("library_license", "aviso", "Crédito CC BY a pôr na descrição",
                        "\n".join(credits.values())))
    if claims:
        out.append(item("library_license", "aviso", "Meme de terceiros no vídeo",
                        f"{names(claims)}: citação/comentário pode gerar reivindicação de direitos."))
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
    out.extend(library_items(ctx.doc))
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


def _clock(seconds: float) -> str:
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"




def _plural(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _library_rows() -> dict[str, dict[str, Any]]:
    """Cada item das bibliotecas de SFX e memes (manifestos `library/sfx|memes/manifest.json` do usuário e o do pacote
    básico do repositório), por id: `{license, risk, author, credit, source_url, ...}`. Biblioteca ainda
    inexistente: vazio."""
    from studio.config import REPO_ROOT

    out: dict[str, dict[str, Any]] = {}
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
                out[str(row["id"])] = row
    return out


def _unknown(license_: str | None) -> bool:
    return (license_ or "").strip().lower() in UNKNOWN_LICENSES


def _norm_license(license_: str | None) -> str:
    return (license_ or "").strip().lower().replace("-", "_").replace(" ", "_")


HIGH_RISKS = frozenset({"high", "alto", "alta"})
THIRD_PARTY_TIERS = frozenset({"meme", "free", "stock", "official"})
"""Níveis de material de terceiros cuja licença ou origem a conferência confere."""


def _project_pool(doc: ProjectDoc) -> Any:
    from studio.core.project.pool import Pool, load_pool

    try:
        return load_pool(doc.id)
    except (ValueError, OSError):
        return Pool()


def _license_items(doc: ProjectDoc, timeline: dict[str, Any], starts: dict[str, float],
                   pool: Any = None) -> tuple[list[dict[str, Any]], int]:
    """Licença do que é de terceiros: (avisos, quantos insertos foram conferidos).

    - mídia anexada (a da cena e a dos subplanos): meme sem licença; foto livre ou de banco sem licença; material
      oficial sem a página de origem;
    - item do acervo usado (por `pool_id`, na mídia ou no subplano): o mesmo, pelo nível do item;
    - inserto da biblioteca (SFX ou meme, por `lib_id`): sem registro em nenhum manifesto, licença desconhecida,
      `fair_comment` ou risco alto (uso como comentário: pode cair) e `cc_by` sem autor nem crédito."""
    from studio.core.sources import prov_of_media

    out: list[dict[str, Any]] = []
    checked = 0
    pool = pool if pool is not None else _project_pool(doc)
    problems: dict[str, list[str]] = {}
    texts = {
        "meme": ("Meme sem licença declarada",
                 "material de terceiros só entra com licença registrada: declare a licença ou troque."),
        "free": ("Foto livre ou de banco sem licença declarada",
                 "sem a licença não dá para conferir o uso nem montar o crédito: declare a licença ou troque."),
        "official": ("Material oficial sem a página de origem",
                     "guarde o link de onde veio o material (crédito e defesa no Content ID)."),
    }

    def check(prov: str, license_: str, origin: str, scene_id: str) -> None:
        if prov == "meme" and _unknown(license_):
            problems.setdefault("meme", []).append(scene_id)
        elif prov in ("free", "stock") and _unknown(license_):
            problems.setdefault("free", []).append(scene_id)
        elif prov == "official" and not origin.strip():
            problems.setdefault("official", []).append(scene_id)

    used_pool: list[tuple[str, str]] = []
    for scene in doc.ordered_scenes():
        for media in (scene.visual.media, *(sh.media for sh in scene.shots)):
            if media is None:
                continue
            prov = prov_of_media(media)
            if prov in THIRD_PARTY_TIERS:
                checked += 1
                check(prov, media.license, media.page_url or media.url, scene.id)
            elif media.pool_id:
                used_pool.append((media.pool_id, scene.id))
        used_pool += [(sh.pool_id, scene.id) for sh in scene.shots if sh.pool_id and sh.media is None]
    seen_pool: set[str] = set()
    for pool_id, scene_id in used_pool:
        found = pool.item(pool_id) if pool is not None else None
        if found is None or pool_id in seen_pool:
            continue
        seen_pool.add(pool_id)
        if found.tier in THIRD_PARTY_TIERS:
            checked += 1
            check(found.tier, found.license, found.page_url or found.url, scene_id)
    for key, found_in in problems.items():
        title, detail = texts[key]
        scenes = list(dict.fromkeys(found_in))
        n = len(scenes)
        out.append(item("perfil.licenca", "aviso", title + (f" ({n} cenas)" if n > 1 else ""), detail,
                        starts.get(scenes[0]), scenes[0]))

    libs: list[tuple[str, str]] = []
    for scene in doc.scenes:
        libs += [(str(fx.params["lib_id"]), scene.id) for fx in scene.fx if fx.params.get("lib_id") and not fx.off]
    for tl_scene in timeline.get("scenes") or []:
        libs += [(str(fx["params"]["lib_id"]), str(tl_scene.get("id") or "")) for fx in tl_scene.get("fx") or []
                 if isinstance(fx, dict) and isinstance(fx.get("params"), dict) and fx["params"].get("lib_id")]
    libs += [(str(e["lib_id"]), str(e.get("scene") or e.get("scene_id") or "")) for e in timeline.get("sfx") or []
             if isinstance(e, dict) and e.get("lib_id")]
    if libs:
        rows = _library_rows()
        seen: set[str] = set()
        for lib_id, scene_id in libs:
            if lib_id in seen:
                continue
            seen.add(lib_id)
            checked += 1
            at, sid = starts.get(scene_id), scene_id or None
            row = rows.get(lib_id)
            if row is None:
                out.append(item("perfil.licenca", "aviso", f"Inserto \"{lib_id}\" sem registro na biblioteca",
                                "não há licença registrada para este som ou meme: importe na biblioteca com a licença "
                                "ou troque.", at, sid))
                continue
            license_ = str(row.get("license") or "")
            norm = _norm_license(license_)
            if _unknown(license_):
                out.append(item("perfil.licenca", "aviso", f"Inserto \"{lib_id}\" com licença desconhecida",
                                "som ou meme sem licença conhecida fica fora do automático: troque ou declare a "
                                "licença na biblioteca.", at, sid))
            elif norm == "fair_comment" or str(row.get("risk") or "").strip().lower() in HIGH_RISKS:
                out.append(item("perfil.licenca", "aviso", f"Inserto \"{lib_id}\" usado como comentário",
                                "uso como comentário (fair comment) ou de risco alto pode gerar reclamação: confira o "
                                "contexto ou troque.", at, sid))
            elif norm.startswith("cc_by") and not str(row.get("author") or row.get("credit") or "").strip():
                out.append(item("perfil.licenca", "aviso", f"Inserto \"{lib_id}\" sem crédito",
                                "a licença CC BY pede o crédito do autor: preencha o autor na biblioteca.", at, sid))
    return out, checked


def edit_items(timeline: dict[str, Any], doc: ProjectDoc, style: StyleSpec, profile: Any = None,
               fp: Any = None, *, channel_memes: frozenset[str] | set[str] = frozenset(),
               pool: Any = None) -> list[dict[str, Any]]:
    """Itens `perfil.*` da edição a partir da timeline (um `ok` por código quando está tudo certo; `perfil.material`
    e `perfil.licenca` podem ter mais de um aviso). `profile`: o `EditProfile` da referência (alvos medidos). `fp`:
    impressão a usar no lugar da medida da timeline (a do MP4 mesclada, na conferência do render); os tempos e as cenas
    dos itens continuam saindo da timeline. `channel_memes`: os memes dos últimos vídeos do canal. `pool`: o acervo
    (None: o gravado do projeto). Nenhum item é erro: edição é gosto, não trava o envio."""
    from studio.core.editing import metrics

    measure = metrics.measure_timeline(timeline, doc, channel_memes)
    if not measure.shots:
        return [item("perfil.ia", "ok", "Edição não medida", "a timeline não tem cenas.")]
    fp = fp or measure.fp
    t = metrics.targets_for(style, profile)
    index = metrics.ai_look_index(fp, style, profile)
    starts: dict[str, float] = {}
    for shot in measure.shots:
        starts.setdefault(shot.scene_id, shot.start)
    out: list[dict[str, Any]] = []

    # Sequências seguidas do mesmo nível: (planos, segundos, início, cena, nível).
    runs: list[tuple[int, float, float, str, str]] = []
    for shot in measure.shots:
        if runs and runs[-1][4] == shot.prov:
            n, secs, at, sid, prov = runs[-1]
            runs[-1] = (n + 1, secs + shot.dur, at, sid, prov)
        else:
            runs.append((1, shot.dur, shot.start, shot.scene_id, shot.prov))

    def longest_run(prov: str) -> tuple[float | None, str | None]:
        mine = [r for r in runs if r[4] == prov]
        if not mine:
            return None, None
        best = max(mine, key=lambda r: r[1])
        return best[2], best[3]

    # IA na tela (com o índice no detalhe)
    ai = metrics.ai_screen_share(fp)
    summary = f"Índice \"cara de IA\": {index.score} de 100 ({index.band})."
    title = f"Imagem de IA em {_pct(ai)} da tela (teto {_pct(t.ai_max_share)})"
    window = fp.ai_window_share
    if ai > t.ai_max_share + 1e-9:
        at, sid = longest_run("ai")
        out.append(item("perfil.ia", "aviso", title,
                        f"{summary} Troque IA por material real nas cenas marcadas (Acervo) ou reedite.", at, sid))
    elif window > t.ai_window_max + 1e-9:
        # A média cabe, mas a IA se concentra num trecho (o bloco de imagens geradas que vira slideshow).
        start = measure.ai_window_start
        end = start + min(metrics.AI_WINDOW_S, measure.duration)
        first = next((s for s in measure.shots if s.ai > 0 and s.end > start and s.start < end), None)
        out.append(item("perfil.ia", "aviso",
                        f"Imagem de IA em {_pct(window)} do trecho de {_clock(start)} a {_clock(end)} "
                        f"(teto {_pct(t.ai_window_max)} por trecho de {metrics.AI_WINDOW_S:.0f} s)",
                        f"{summary} Na média são {_pct(ai)} (teto {_pct(t.ai_max_share)}), mas a IA se concentra "
                        "nesse trecho: espalhe a IA ou troque por material real nas cenas dele.",
                        first.start if first else start, first.scene_id if first else None))
    else:
        out.append(item("perfil.ia", "ok", title, summary))

    # Ritmo dos planos
    lo, hi = t.shot_median_band
    pace_issues = []
    short_shots = fp.pace.median_s < lo
    if short_shots or fp.pace.median_s > hi:
        pace_issues.append(f"plano mediano de {_n(fp.pace.median_s)} s (alvo de {_n(lo)} a {_n(hi)} s)")
    flat = fp.pace.cv < t.cv_min
    if flat:
        pace_issues.append(f"durações parecidas demais (variação {_n(fp.pace.cv)}, mínimo {_n(t.cv_min)})")
    if pace_issues:
        if short_shots and flat:
            pace_title = "Planos curtos e de duração parecida"
        elif short_shots:
            pace_title = "Planos curtos demais"
        else:
            pace_title = "Ritmo de slideshow"
        out.append(item("perfil.ritmo", "aviso", pace_title, "; ".join(pace_issues) + "."))
    else:
        out.append(item("perfil.ritmo", "ok",
                        f"Ritmo dos planos: mediana {_n(fp.pace.median_s)} s, variação {_n(fp.pace.cv)}"))

    # Material: gancho, real, oficial e sequência do mesmo tipo
    material: list[dict[str, Any]] = []
    if fp.hook_prov is not None and not metrics.hook_ok(fp.hook_prov, t):
        label = PROV_LABELS.get(fp.hook_prov, fp.hook_prov)
        accepted = ", ".join(PROV_LABELS.get(p, p) for p in t.hook_tiers)
        material.append(item("perfil.material", "aviso", f"O gancho abre com {label}",
                             f"os 3 primeiros segundos pedem material real ({accepted}).", 0.0,
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
    over = [r for r in runs if r[0] > t.max_run_shots or r[1] > t.max_run_s + 1e-9]
    if over:
        # A sequência que mais passa do limite (por planos ou por segundos), e o título pelo motivo.
        worst_run = max(over, key=lambda r: max(r[0] / max(1, t.max_run_shots), r[1] / max(1e-9, t.max_run_s)))
        n, secs, at, sid, prov = worst_run
        label = PROV_LABELS.get(prov, prov)
        if n > t.max_run_shots:
            run_title = f"{n} planos seguidos de {label} ({_n(secs)} s)"
        else:
            run_title = f"{_n(secs)} s seguidos de {label}"
        material.append(item("perfil.material", "aviso", run_title,
                             f"troque o tipo de mídia a cada {t.max_run_shots} planos ou {_n(t.max_run_s)} s.",
                             at, sid or None))
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

    # Eventos (cortes + punch + fx + insertos): a mesma faixa do índice, alvo ÷/× (1 + tolerância)
    events = fp.pace.events_per_min
    e_lo, e_hi = t.events_band
    if events < e_lo or (e_hi > 0 and events > e_hi):
        how = "poucos" if events < e_lo else "muitos"
        out.append(item("perfil.eventos", "aviso", f"{_n(events)} eventos por minuto ({how})",
                        f"cortes, punch, textos e insertos: o alvo é de {_n(e_lo)} a {_n(e_hi)} por minuto "
                        f"(perfil {_n(t.events_min)})."))
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
        rep_at, rep_scene, _ = measure.repeats[0] if measure.repeats else (None, None, "")
        n = fp.repeat_hits
        kinds = sorted({what for _, _, what in measure.repeats})
        reasons = {
            "imagem": f"a mesma imagem em mais de {metrics.MAX_APPEARANCES} cenas",
            "meme": "o mesmo meme volta no vídeo",
            "meme do canal": "um meme já usado num dos últimos vídeos do canal",
            "som": "o mesmo som da biblioteca toca vezes demais",
        }
        detail = "; ".join(reasons[k] for k in kinds if k in reasons)
        out.append(item("perfil.repeticao", "aviso", f"{n} {_plural(n, 'repetição', 'repetições')} no vídeo",
                        (detail[:1].upper() + detail[1:] + ".") if detail else "", rep_at, rep_scene))
    else:
        out.append(item("perfil.repeticao", "ok", "Sem repetição de imagem, meme ou som"))

    licenses, checked = _license_items(doc, timeline, starts, pool)
    if not licenses:
        licenses = [item("perfil.licenca", "ok",
                         "Insertos com licença registrada" if checked else "Nenhum inserto de terceiros")]
    out += licenses
    return out
