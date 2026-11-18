"""Subcomando `studio fix-reference-names` (registrado pelo `cli.py`): limpa nomes que vieram do arquivo baixado.

    studio fix-reference-names [--dry-run]

- Referências: `reference_videos.title` passa por `clean_media_title` quando o valor muda
  ("YTDown.com_YouTube_Media_-hT4bLj6Jdg_VOCÊ-VAI-ZERAR-GTA-6-PELO-YOUTUBE_001_1080p" → "VOCÊ VAI ZERAR GTA 6 PELO
  YOUTUBE").
- Estilos criados a partir de uma referência e nunca salvos de novo (tag "referencia", versão 1, nome
  "<título> (referência)"): troca o nome (coluna e `data.name`) e o título na descrição. Um estilo renomeado ou
  editado pelo usuário (versão > 1, ou nome diferente do automático) fica como está.
- **O id nunca muda**: projetos e canais apontam para ele (o endereço `/estilos/ref-ytdown-…` continua o mesmo).
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from typing import Any

COMMAND = "fix-reference-names"


@dataclass
class NameFix:
    kind: str  # "referencia" | "estilo"
    id: str
    old: str
    new: str
    data: dict[str, Any] | None = None  # estilo: o `data` novo


@dataclass
class FixReport:
    fixes: list[NameFix] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # estilos que mudariam, mas o resultado não valida
    dry_run: bool = False


def _style_fix(row_id: str, name: str, version: int, data: dict[str, Any]) -> NameFix | str | None:
    """A correção de um estilo, um aviso (str) quando não dá para corrigir, ou None quando não há o que fazer."""
    from studio.core.media_title import clean_media_title
    from studio.core.styles.from_reference import REF_SUFFIX, default_style_name
    from studio.core.styles.schema import StyleSpec

    if version != 1 or "referencia" not in (data.get("tags") or []) or not name.endswith(REF_SUFFIX):
        return None
    old_title = name[: -len(REF_SUFFIX)]
    new_title = clean_media_title(old_title)
    if not new_title or new_title == old_title:
        return None
    new_name = default_style_name(new_title)
    new_data = {**data, "name": new_name}
    description = str(data.get("description") or "")
    quoted = f"“{old_title}”"  # como `build_style` escreve: Criado a partir da referência “<título>” …
    if quoted in description:
        new_data["description"] = description.replace(quoted, f"“{new_title}”")
    try:
        StyleSpec.model_validate(new_data)
    except Exception as exc:  # noqa: BLE001 - estilo que não valida fica como está e vai para o relatório
        return f"{row_id}: não corrigido ({str(exc)[:200]})"
    return NameFix("estilo", row_id, name, new_name, new_data)


def fix_reference_names(*, dry_run: bool = False) -> FixReport:
    """Acha (e, sem `dry_run`, grava) os nomes a limpar. Nunca troca id."""
    from studio.core.media_title import clean_media_title
    from studio.db import session_scope
    from studio.models import ReferenceVideo, Style

    report = FixReport(dry_run=dry_run)
    with session_scope() as s:
        for ref in s.query(ReferenceVideo).order_by(ReferenceVideo.created_at).all():
            new = " ".join(clean_media_title(ref.title or "").split())[:300]
            if new and new != ref.title:
                report.fixes.append(NameFix("referencia", ref.id, ref.title, new))
        for row in s.query(Style).order_by(Style.created_at).all():
            out = _style_fix(row.id, row.name or "", row.version, dict(row.data or {}))
            if isinstance(out, NameFix):
                report.fixes.append(out)
            elif out:
                report.skipped.append(out)
    if dry_run or not report.fixes:
        return report
    with session_scope() as s:
        for fix in report.fixes:
            if fix.kind == "referencia":
                ref_row = s.get(ReferenceVideo, fix.id)
                if ref_row is not None and ref_row.title == fix.old:
                    ref_row.title = fix.new
            else:
                style_row = s.get(Style, fix.id)
                if style_row is not None and style_row.name == fix.old and style_row.version == 1:
                    style_row.name = fix.new
                    style_row.data = fix.data  # dict novo: o SQLAlchemy só grava JSON trocado por inteiro
    return report


def format_report(report: FixReport) -> str:
    if not report.fixes and not report.skipped:
        return "Nenhum nome para corrigir."
    verb = "mudaria" if report.dry_run else "mudou"
    lines = []
    for fix in report.fixes:
        label = "Referência" if fix.kind == "referencia" else "Estilo"
        lines.append(f"{label} {fix.id} {verb}:\n  de   {fix.old}\n  para {fix.new}")
    lines += report.skipped
    if report.dry_run and report.fixes:
        lines.append("Nada foi gravado (--dry-run). Rode sem --dry-run para aplicar.")
    return "\n".join(lines)


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="limpa nomes de referências e estilos que vieram do nome do arquivo baixado")
    parser.add_argument("--dry-run", action="store_true", help="só mostra o que mudaria, sem gravar")


def run(args: argparse.Namespace) -> int:
    from studio.db import init_db

    init_db()
    print(format_report(fix_reference_names(dry_run=args.dry_run)))
    return 0
