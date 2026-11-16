"""Importa o histórico do canais para as tabelas da gestão de canal (ADR 0002).

| canais (estudio.db)  | Vulto            | chave que evita duplicar             |
|----------------------|------------------|--------------------------------------|
| canais + canal.toml  | Channel (+ ofertas em AffiliateOffer) | settings["canais"]["id"]; canal + slug |
| videos (com YouTube) | settings["analytics"]["videos"] do canal | id do YouTube                |
| ideias               | Idea             | id `ide_cn<id>`                      |
| metricas             | VideoMetric      | id do YouTube + dia                  |
| experimentos         | Experiment       | id `exp_cn<id>`                      |
| licoes               | Lesson           | id `lic_cn<id>`                      |
| pendencias           | PendingTask      | id `pen_cn_<id>`                     |
| receita              | RevenueEntry     | canal + loja + mês + ID de rastreamento |

Rodar de novo não duplica nada e NÃO atualiza o que já entrou (ideias, experimentos, lições, pendências e receita
ficam como estão: o Vulto pode ter sido editado depois). A exceção são as métricas: os campos vazios são preenchidos e
os contadores que só crescem (views, likes, idade...) ficam com o maior número, como no dia que ainda corria.
`execucoes` não tem destino (ver `_notes`).

Lições aplicadas no canais que entrariam no roteiro (escopo global, de canal ou de formato) chegam como propostas: muitas
falam de arquivos do pipeline antigo e o usuário aprova em Desempenho › Lições as que ainda valem.

Com `dry_run=True` tudo é feito numa transação desfeita no fim: a prévia é exatamente o que entraria.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from studio.db import session_scope
from studio.models import (
    Channel,
    Experiment,
    Idea,
    Lesson,
    PendingTask,
    Project,
    Publication,
    RevenueEntry,
    VideoMetric,
)
from studio.workers.canais_import import channels as chmap
from studio.workers.canais_import.reader import CanaisSource, read_source

TABLE_LABELS = {
    "ideias": "Ideias",
    "metricas": "Métricas",
    "experimentos": "Experimentos",
    "licoes": "Lições",
    "pendencias": "Pendências",
    "receita": "Receita",
    "videos": "Vídeos (título, data e duração)",
}

IDEA_STATUS = {"nova", "aprovada", "rejeitada", "em_producao", "feita", "descartada"}
METRIC_SOURCES = {"yt-dlp": "public", "geradormg": "youtube_api", "central-youtube-videos": "youtube_api"}
EXPERIMENT_METRICS = ("views", "views_dia", "ctr", "retencao", "avd_s", "likes")
# Variável do canais com nome diferente no Vulto (vale só quando as variantes já são os valores do Vulto).
VARIABLE_ALIASES = {"horario": "hora"}
# Formato do canais → escopo de lição. Short de notícia e corte de Short são mais estreitos que "todo Short": ficam
# com o nome do canais (não valem para nenhum projeto até o usuário trocar o escopo).
LESSON_FORMATS = {"doc-longo": "long", "longo": "long", "long": "long", "short": "short"}

# canais → Vulto (métricas)
METRIC_COLUMNS = {
    "idade_h": "age_hours", "views": "views", "likes": "likes", "comentarios": "comments", "impressoes": "impressions",
    "ctr": "ctr", "avd_s": "avg_view_s", "pct_assistido": "pct_watched", "inscritos": "subscribers_gained",
    "receita_usd": "revenue_usd", "rpm": "rpm",
}
# Números que só crescem ao longo do dia: numa nova importação, o maior (o mais recente) fica.
GROWING = ("age_hours", "views", "likes", "comments", "impressions", "subscribers_gained", "revenue_usd")


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _with_note(text: str, note: str) -> str:
    return f"{text}\n{note}".strip() if note else text


def _legacy_key(prefix: str, raw: Any) -> str:
    """Id determinístico (até 32 caracteres) para uma linha do canais."""
    key = f"{prefix}{raw}"
    if len(key) <= 32:
        return key
    return f"{prefix}{hashlib.sha1(str(raw).encode()).hexdigest()[: 32 - len(prefix)]}"


_LETTER = re.compile(r"^[A-Z]:\s+")


def variants_of(raw: Any) -> list[str]:
    """Variantes de um experimento do canais: lista JSON, texto livre separado por " | " (com ou sem "A: "/"B: ";
    vírgulas dentro dos parênteses), " x " entre duas variantes sem vírgula, ou texto separado por vírgula."""
    text = _text(raw).strip()
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (ValueError, TypeError):
        pass
    if "|" in text:
        parts = text.split("|")
    elif " x " in text and "," not in text:
        parts = text.split(" x ")
    else:
        parts = text.split(",")
    return [_LETTER.sub("", x.strip()) for x in parts if x.strip()]


def _is_json_list(raw: Any) -> bool:
    try:
        return isinstance(json.loads(_text(raw)), list)
    except (ValueError, TypeError):
        return False


def lesson_scope(raw: str, channel_of: Callable[[str], tuple[bool, str | None]]) -> str | None:
    """Escopo da lição no Vulto (None = canal pulado). `canal:<slug>` vira `canal:<id do Vulto>` e o formato vira
    short/long; o resto (`global`, `skill:...`) fica como está."""
    raw = (raw or "global").strip()
    kind, _, value = raw.partition(":")
    if kind == "canal" and value:
        ok, cid = channel_of(value)
        if not ok:
            return None
        return f"canal:{cid}" if cid else raw
    if kind == "formato" and value:
        return f"formato:{LESSON_FORMATS.get(value, value)}"
    return raw[:120]


class _Counter:
    def __init__(self, table: str, total: int) -> None:
        self.data: dict[str, Any] = {"key": table, "label": TABLE_LABELS[table], "total": total, "new": 0,
                                     "existing": 0, "updated": 0, "skipped": 0, "examples": []}

    def new(self, example: str = "") -> None:
        self.data["new"] += 1
        if example and len(self.data["examples"]) < 3:
            self.data["examples"].append(example[:120])

    def bump(self, key: str) -> None:
        self.data[key] += 1


def _ideas(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> None:
    for r in rows:
        ok, cid = channel_of(r.get("canal"))
        if not ok:
            c.bump("skipped")
            continue
        iid = _legacy_key("ide_cn", r.get("id"))
        if s.get(Idea, iid) is not None:
            c.bump("existing")
            continue
        status = _text(r.get("status") or "nova").replace("-", "_")
        video = _text(r.get("video_id"))
        s.add(Idea(
            id=iid, channel_id=cid, title=_text(r.get("titulo"))[:300] or "(sem título)", angle=_text(r.get("angulo")),
            format=chmap.FORMATS.get(_text(r.get("formato")), ""), origin=(_text(r.get("origem")) or "canais")[:60],
            evidence=_with_note(_text(r.get("evidencia")), f"Vídeo no canais: {video}" if video else ""),
            search=_text(r.get("busca")), score=r.get("nota"),
            status=status if status in IDEA_STATUS else "nova", reason=_text(r.get("motivo")),
            created_at=_when(r.get("criado_em")) or datetime.now(UTC), decided_at=_when(r.get("decidido_em")),
        ))
        c.new(_text(r.get("titulo")))


def _metrics(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> None:
    for r in rows:
        yid, day = _text(r.get("youtube_id")).strip(), _text(r.get("data")).strip()[:10]
        if not yid or not day:
            c.bump("skipped")
            continue
        ok, cid = channel_of(r.get("canal"))
        if not ok:
            c.bump("skipped")
            continue
        values = {dst: r.get(src) for src, dst in METRIC_COLUMNS.items() if r.get(src) is not None}
        row = s.query(VideoMetric).filter_by(youtube_id=yid, day=day).first()
        if row is not None:
            # Vazio é preenchido; contador que cresceu (dia que ainda corria na importação anterior) fica com o maior.
            changes = {k: v for k, v in values.items() if getattr(row, k) is None
                       or (k in GROWING and isinstance(v, int | float) and v > getattr(row, k))}
            if not row.channel_id and cid:
                changes["channel_id"] = cid
            for k, v in changes.items():
                setattr(row, k, v)
            if changes:
                row.updated_at = datetime.now(UTC)
            c.bump("updated" if changes else "existing")
            continue
        pub = (s.query(Publication).filter(Publication.external_id == yid, Publication.platform == "youtube")
               .order_by(Publication.created_at.desc()).first())
        project = s.get(Project, pub.project_id) if pub else None
        s.add(VideoMetric(
            youtube_id=yid, day=day, publication_id=pub.id if pub else None, project_id=pub.project_id if pub else None,
            channel_id=(project.channel_id if project else None) or cid,
            source=METRIC_SOURCES.get(_text(r.get("fonte")), "manual"), **values,
        ))
        s.flush()  # a mesma chave pode aparecer de novo mais abaixo
        c.new(f"{yid} em {day}")


def _experiment_variable(raw: str, variants: list[str]) -> str | None:
    """Variável do Vulto para a variável do canais (None = o Vulto não calcula)."""
    from studio.workers.analytics.variables import VARIABLES, hour_band

    var = VARIABLE_ALIASES.get(raw, raw)
    if var not in VARIABLES:
        return None
    if var == "hora":  # só serve se as variantes já são as faixas do Vulto
        bands = {hour_band(datetime(2026, 1, 1, h)) for h in range(24)}
        if variants and not set(variants) <= bands:
            return None
    return var


def _experiments(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> list[str]:
    """Devolve os experimentos abertos que o Vulto não consegue medir (entram como abandonados)."""
    stuck: list[str] = []
    for r in rows:
        ok, cid = channel_of(r.get("canal"))
        if not ok:
            c.bump("skipped")
            continue
        eid = _legacy_key("exp_cn", r.get("id"))
        if s.get(Experiment, eid) is not None:
            c.bump("existing")
            continue
        metric = _text(r.get("metrica")) or "views"
        notes = []
        if metric not in EXPERIMENT_METRICS:
            notes.append(f"Métrica no canais: {metric}.")
            metric = "views"
        raw_variants = _text(r.get("variantes")).strip()
        if raw_variants and not _is_json_list(raw_variants):
            notes.append(f"Variantes no canais: {raw_variants}.")
        videos = variants_of(r.get("videos"))
        if videos:
            notes.append(f"Vídeos no canais: {', '.join(videos)}.")
        variants = variants_of(raw_variants)
        raw_var = _text(r.get("variavel")).strip().removeprefix("var.")
        variable = _experiment_variable(raw_var, variants)
        status = _text(r.get("status")) or "aberto"
        status = status if status in ("aberto", "concluido", "abandonado") else "aberto"
        result = _text(r.get("resultado"))
        closed = _when(r.get("concluido_em"))
        if variable is None and status == "aberto":
            # Ficaria "cedo" para sempre: o Vulto não calcula essa variável nos vídeos.
            status, closed = "abandonado", datetime.now(UTC)
            result = _with_note(result, f"Fechado na importação: o Vulto não calcula a variável '{raw_var}'. Para "
                                        "seguir com o teste, abra um experimento com uma variável do Vulto.")
            stuck.append(f"{raw_var} ({_text(r.get('hipotese'))[:60]})")
        s.add(Experiment(
            id=eid, channel_id=cid, hypothesis=_with_note(_text(r.get("hipotese")), " ".join(notes)),
            variable=(variable or raw_var)[:60], variants=variants, metric=metric, project_ids=[], status=status,
            result=result, learning=_text(r.get("aprendizado")),
            created_at=_when(r.get("criado_em")) or datetime.now(UTC), closed_at=closed,
        ))
        c.new(_text(r.get("hipotese")))
    return stuck


def _lessons(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> int:
    """Devolve quantas lições aplicadas no canais entram como propostas (as que iriam para o roteiro: escopo global,
    de canal ou de formato). As de skill:* ficam aplicadas: não entram em nenhum pedido ao LLM do Vulto."""
    to_review = 0
    for r in rows:
        raw_scope = _text(r.get("escopo")).strip() or "global"
        scope = lesson_scope(raw_scope, channel_of)
        if scope is None:
            c.bump("skipped")
            continue
        lid = _legacy_key("lic_cn", r.get("id"))
        if s.get(Lesson, lid) is not None:
            c.bump("existing")
            continue
        status = _text(r.get("status")) or "proposta"
        status = status if status in ("proposta", "aplicada", "descartada") else "proposta"
        video = _text(r.get("video_id"))
        applied = _text(r.get("aplicada_em"))
        notes = [f"Vídeo no canais: {video}" if video else ""]
        if raw_scope.startswith("formato:") and scope != raw_scope:
            notes.append(f"Escopo no canais: {raw_scope}.")
        decided = _when(r.get("decidido_em"))
        if status == "aplicada" and scope.partition(":")[0] in ("global", "canal", "formato"):
            status, decided = "proposta", None
            notes.append("Aplicada no canais" + (f" em: {applied}" if applied else "") + ". Confira se ainda vale no "
                         "Vulto antes de aprovar.")
            applied = ""
            to_review += 1
        s.add(Lesson(
            id=lid, scope=scope, text=_text(r.get("texto")),
            evidence=_with_note(_text(r.get("evidencia")), "\n".join(n for n in notes if n)),
            origin=(_text(r.get("origem")) or "canais")[:40], status=status,
            applied_where=applied, reason=_text(r.get("motivo")),
            created_at=_when(r.get("criado_em")) or datetime.now(UTC), decided_at=decided,
        ))
        c.new(_text(r.get("texto")))
    return to_review


def _pending(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> None:
    for r in rows:
        ok, cid = channel_of(r.get("canal"))
        if not ok:
            c.bump("skipped")
            continue
        pid = _legacy_key("pen_cn_", r.get("id"))
        if s.get(PendingTask, pid) is not None:
            c.bump("existing")
            continue
        status = _text(r.get("status")) or "aberta"
        s.add(PendingTask(
            id=pid, channel_id=cid, text=_text(r.get("texto")), where_hint=_text(r.get("onde")), kind="manual",
            status=status if status in ("aberta", "feita", "descartada") else "aberta", order=int(r.get("ordem") or 0),
            created_at=_when(r.get("criado_em")) or datetime.now(UTC), done_at=_when(r.get("feito_em")),
        ))
        c.new(_text(r.get("texto")))


def _revenue(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> None:
    for r in rows:
        ok, cid = channel_of(r.get("canal"))
        store, period = _text(r.get("fonte"))[:40], _text(r.get("periodo"))[:7]
        if not ok or not cid or not store or not period:
            c.bump("skipped")  # receita sempre pertence a um canal
            continue
        tracking = _text(r.get("rastreio"))[:60]
        found = s.query(RevenueEntry).filter_by(channel_id=cid, store=store, period=period, tracking_id=tracking).first()
        if found is not None:
            c.bump("existing")
            continue
        s.add(RevenueEntry(
            channel_id=cid, store=store, period=period, tracking_id=tracking, clicks=r.get("cliques"),
            orders=r.get("pedidos"), sales=r.get("vendas"), commission=r.get("comissao"),
            currency=(_text(r.get("moeda")) or "BRL")[:3], note=_text(r.get("nota")),
        ))
        s.flush()
        c.new(f"{store} {period}" + (f" ({tracking})" if tracking else ""))


def _videos(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> None:
    """Título, data de publicação e duração dos vídeos do canais com id do YouTube vão para os dados da coleta do
    canal (`settings["analytics"]["videos"]`, mesmo formato de analytics/collect.py): é daí que Desempenho tira hora,
    dia, duração e formato. Entrada que já existe (a coleta do Vulto manda) fica como está."""
    for r in rows:
        yid = _text(r.get("youtube_id")).strip()
        ok, cid = channel_of(r.get("canal"))
        row = s.get(Channel, cid) if yid and ok and cid else None
        if row is None:
            c.bump("skipped")  # sem vídeo no YouTube, ou sem canal no Vulto onde guardar
            continue
        settings = dict(row.settings or {})
        analytics = dict(settings.get("analytics") or {})
        videos = dict(analytics.get("videos") or {})
        if yid in videos:
            c.bump("existing")
            continue
        try:
            extra = json.loads(_text(r.get("variaveis")) or "{}")
        except ValueError:
            extra = {}
        duration = r.get("duracao_s")
        if duration is None and isinstance(extra, dict):
            duration = extra.get("duracao_s")
        published = _when(r.get("publicado_em"))
        videos[yid] = {"title": _text(r.get("titulo")), "published_at": published.isoformat() if published else None,
                       "duration_s": float(duration) if isinstance(duration, int | float) else None, "privacy": None}
        analytics["videos"] = videos
        settings["analytics"] = analytics
        row.settings = settings  # atribuição nova: o SQLAlchemy só vê a troca do JSON inteiro
        c.new(_text(r.get("titulo")) or yid)


def _notes(src: CanaisSource) -> list[str]:
    notes = list(src.warnings)
    if src.counts.get("execucoes"):
        notes.append(f"Execuções ({src.counts['execucoes']}) não entram: o Vulto mede o tempo por etapa pelas tarefas do "
                     "próprio app (ADR 0002).")
    if src.counts.get("videos"):
        notes.append(f"Vídeos ({src.counts['videos']}) não viram projetos: o canais não guarda o documento do vídeo. Os "
                     "que estão no YouTube levam título, data e duração para Desempenho; as métricas entram pelo id "
                     "do YouTube e as ideias e lições citam o vídeo no texto.")
    notes.append("Rodar de novo não duplica nada, mas só as métricas são atualizadas: ideias, experimentos, lições, "
                 "pendências e receita que já entraram ficam como estão no Vulto.")
    notes.append("O radar e o registro de eventos do canais ficam de fora.")
    return notes


def import_canais(root: str | Path | None = None, choices: dict[str, str] | None = None,
                  dry_run: bool = True) -> dict[str, Any]:
    """Importa (ou, com `dry_run`, só mostra) o histórico do canais. `choices`: {slug do canal: "auto" | "pular" |
    id de um canal do Vulto}."""
    src = read_source(root)
    choices = choices or {}
    unknown = set(choices) - set(src.channels)
    if unknown:
        raise ValueError(f"canal que não existe no canais: {', '.join(sorted(unknown))}")
    report: dict[str, Any] = {"root": str(src.root), "db": str(src.db_path) if src.db_path else None,
                              "dry_run": dry_run, "channels": [], "tables": [], "notes": _notes(src)}
    with session_scope() as s:
        mapped: dict[str, str | None] = {}  # slug → id do Vulto (None = pulado)
        for slug, ch in sorted(src.channels.items(), key=lambda kv: (kv[1].db.get("ordem") or 99, kv[0])):
            action, row, reason = chmap.resolve(s, ch, choices.get(slug, chmap.AUTO))
            item: dict[str, Any] = {"slug": slug, "name": ch.name, "action": action, "reason": reason,
                                    "toml": ch.toml_path, "youtube_channel_id": chmap.youtube_id_of(ch),
                                    "vulto_id": None, "vulto_name": None, "fields": [], "offers_new": 0,
                                    "offers_existing": 0}
            if row is not None:
                item["fields"] = chmap.fill(s, row, ch, created=action == "criar")
                item["offers_new"], item["offers_existing"] = chmap.offers(s, row, ch)
                item["vulto_id"] = None if action == "criar" and dry_run else row.id
                item["vulto_name"] = row.name
            if row is not None:
                twin = next((k for k, v in mapped.items() if v == row.id), None)
                if twin is not None:
                    raise ValueError(f"os canais '{twin}' e '{slug}' iriam para o mesmo canal do Vulto ({row.name}): "
                                     "escolha um destino diferente para um deles")
            mapped[slug] = row.id if row is not None else None
            report["channels"].append(item)

        def channel_of(slug: Any) -> tuple[bool, str | None]:
            slug = _text(slug).strip()
            if not slug or slug not in mapped:
                return True, None  # sem canal (ou canal que só aparece nas linhas): entra sem canal
            return mapped[slug] is not None, mapped[slug]

        steps: list[tuple[str, Callable]] = [("ideias", _ideas), ("metricas", _metrics), ("experimentos", _experiments),
                                             ("licoes", _lessons), ("pendencias", _pending), ("receita", _revenue),
                                             ("videos", _videos)]
        for table, fn in steps:
            counter = _Counter(table, len(src.rows.get(table) or []))
            out = fn(s, src.rows.get(table) or [], channel_of, counter)
            if table == "licoes" and out:
                report["notes"].append(f"{out} lições aplicadas no canais (globais, do canal ou do formato) entram como "
                                       "propostas: aprove em Desempenho › Lições as que ainda valem no Vulto. Só as "
                                       "aprovadas vão para o roteiro.")
            if table == "experimentos" and out:
                report["notes"].append(f"{len(out)} experimentos abertos entram como abandonados porque o Vulto não "
                                       f"calcula a variável: {'; '.join(out)}.")
            report["tables"].append(counter.data)
        s.flush()
        if dry_run:
            s.rollback()
    return report


def format_report(report: dict[str, Any]) -> str:
    """Texto do relatório para a linha de comando."""
    lines = [f"Pasta: {report['root']}", f"Banco: {report['db'] or '(sem banco)'}",
             "PRÉVIA: nada foi gravado." if report["dry_run"] else "Importação feita.", "", "Canais:"]
    for ch in report["channels"]:
        target = f" → {ch['vulto_name']}" if ch["vulto_name"] else ""
        lines.append(f"  {ch['slug']}: {ch['action']}{target} ({ch['reason']})")
        if ch["fields"]:
            lines.append(f"    campos: {', '.join(ch['fields'])}")
        if ch["offers_new"] or ch["offers_existing"]:
            lines.append(f"    ofertas: {ch['offers_new']} novas, {ch['offers_existing']} já existiam")
    lines += ["", "Tabelas:"]
    for t in report["tables"]:
        lines.append(f"  {t['label']}: {t['total']} no canais · {t['new']} novas · {t['existing']} já existiam"
                     + (f" · {t['updated']} completadas" if t["updated"] else "")
                     + (f" · {t['skipped']} puladas" if t["skipped"] else ""))
    if report["notes"]:
        lines += ["", "Observações:"] + [f"  - {n}" for n in report["notes"]]
    return "\n".join(lines)
