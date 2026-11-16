"""Importa o histórico do canais para as tabelas da gestão de canal (ADR 0002).

| canais (estudio.db)  | Vulto            | chave que evita duplicar             |
|----------------------|------------------|--------------------------------------|
| canais + canal.toml  | Channel (+ ofertas em AffiliateOffer) | settings["canais"]["id"]; canal + slug |
| ideias               | Idea             | id `ide_cn<id>`                      |
| metricas             | VideoMetric      | id do YouTube + dia                  |
| experimentos         | Experiment       | id `exp_cn<id>`                      |
| licoes               | Lesson           | id `lic_cn<id>`                      |
| pendencias           | PendingTask      | id `pen_cn_<id>`                     |
| receita              | RevenueEntry     | canal + loja + mês + ID de rastreamento |

Rodar de novo não duplica nada: o que já existe fica como está (o Vulto pode ter sido editado depois). A exceção são
as métricas, que só ganham os números que estavam vazios. `execucoes` e `videos` não têm destino (ver `NOTES`).

Com `dry_run=True` tudo é feito numa transação desfeita no fim: a prévia é exatamente o que entraria.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from studio.db import session_scope
from studio.models import (
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
}

IDEA_STATUS = {"nova", "aprovada", "rejeitada", "em_producao", "feita", "descartada"}
METRIC_SOURCES = {"yt-dlp": "public", "geradormg": "youtube_api", "central-youtube-videos": "youtube_api"}
EXPERIMENT_METRICS = ("views", "views_dia", "ctr", "retencao", "avd_s", "likes")

# canais → Vulto (métricas)
METRIC_COLUMNS = {
    "idade_h": "age_hours", "views": "views", "likes": "likes", "comentarios": "comments", "impressoes": "impressions",
    "ctr": "ctr", "avd_s": "avg_view_s", "pct_assistido": "pct_watched", "inscritos": "subscribers_gained",
    "receita_usd": "revenue_usd", "rpm": "rpm",
}


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


def variants_of(raw: Any) -> list[str]:
    """Mesma leitura do canais (`melhoria.variantes_de`): lista JSON ou texto separado por vírgula."""
    text = _text(raw)
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x) for x in data]
    except (ValueError, TypeError):
        pass
    return [x.strip() for x in text.split(",") if x.strip()]


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
        return f"formato:{chmap.FORMATS.get(value, value)}"
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
            missing = {k: v for k, v in values.items() if getattr(row, k) is None}
            if not row.channel_id and cid:
                missing["channel_id"] = cid
            for k, v in missing.items():
                setattr(row, k, v)
            c.bump("updated" if missing else "existing")
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


def _experiments(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> None:
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
        videos = variants_of(r.get("videos"))
        if videos:
            notes.append(f"Vídeos no canais: {', '.join(videos)}.")
        status = _text(r.get("status")) or "aberto"
        s.add(Experiment(
            id=eid, channel_id=cid, hypothesis=_with_note(_text(r.get("hipotese")), " ".join(notes)),
            variable=_text(r.get("variavel")).removeprefix("var.")[:60], variants=variants_of(r.get("variantes")),
            metric=metric, project_ids=[], status=status if status in ("aberto", "concluido", "abandonado") else "aberto",
            result=_text(r.get("resultado")), learning=_text(r.get("aprendizado")),
            created_at=_when(r.get("criado_em")) or datetime.now(UTC), closed_at=_when(r.get("concluido_em")),
        ))
        c.new(_text(r.get("hipotese")))


def _lessons(s: Session, rows: list[dict[str, Any]], channel_of: Callable, c: _Counter) -> int:
    """Devolve quantas lições novas e aplicadas passam a entrar no roteiro (escopo global, canal ou formato)."""
    to_script = 0
    for r in rows:
        scope = lesson_scope(_text(r.get("escopo")), channel_of)
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
        s.add(Lesson(
            id=lid, scope=scope, text=_text(r.get("texto")),
            evidence=_with_note(_text(r.get("evidencia")), f"Vídeo no canais: {video}" if video else ""),
            origin=(_text(r.get("origem")) or "canais")[:40], status=status,
            applied_where=_text(r.get("aplicada_em")), reason=_text(r.get("motivo")),
            created_at=_when(r.get("criado_em")) or datetime.now(UTC), decided_at=_when(r.get("decidido_em")),
        ))
        if status == "aplicada" and scope.partition(":")[0] in ("global", "canal", "formato"):
            to_script += 1
        c.new(_text(r.get("texto")))
    return to_script


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


def _notes(src: CanaisSource) -> list[str]:
    notes = list(src.warnings)
    if src.counts.get("execucoes"):
        notes.append(f"Execuções ({src.counts['execucoes']}) não entram: o Vulto mede o tempo por etapa pelas tarefas do "
                     "próprio app (ADR 0002).")
    if src.counts.get("videos"):
        notes.append(f"Vídeos ({src.counts['videos']}) não viram projetos: o canais não guarda o documento do vídeo. As "
                     "métricas entram pelo id do YouTube e as ideias e lições citam o vídeo no texto.")
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
            mapped[slug] = row.id if row is not None else None
            report["channels"].append(item)

        def channel_of(slug: Any) -> tuple[bool, str | None]:
            slug = _text(slug).strip()
            if not slug or slug not in mapped:
                return True, None  # sem canal (ou canal que só aparece nas linhas): entra sem canal
            return mapped[slug] is not None, mapped[slug]

        steps: list[tuple[str, Callable]] = [("ideias", _ideas), ("metricas", _metrics), ("experimentos", _experiments),
                                             ("licoes", _lessons), ("pendencias", _pending), ("receita", _revenue)]
        for table, fn in steps:
            counter = _Counter(table, len(src.rows.get(table) or []))
            out = fn(s, src.rows.get(table) or [], channel_of, counter)
            if table == "licoes" and out:
                report["notes"].append(f"{out} lições aplicadas (globais, do canal ou do formato) passam a entrar no "
                                       "roteiro dos próximos vídeos. Descarte em Desempenho › Lições as que não "
                                       "servirem mais.")
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
