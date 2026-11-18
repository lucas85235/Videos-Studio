"""Subcomando `studio edit-metrics` (registrado pelo `cli.py`): o índice "cara de IA" e as métricas de edição dos
projetos (ADR 0004 §3.10 e §6). **Só leitura**: o banco é aberto em modo só leitura e nada é gravado.

    studio edit-metrics <projeto> [<projeto>...] [--lang pt-BR] [--fonte auto|final|preview|estimate] [--json]
    studio edit-metrics --all

Serve de linha de base: rode antes de mudar a edição e depois, nos mesmos projetos, para comparar.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

COMMAND = "edit-metrics"
# O console do Windows (cp1252) não tem ≤, ≥ nem o travessão curto.
_ASCII_MATH = str.maketrans({"≤": "<=", "≥": ">=", "–": "-"})


def _pct(value: float) -> str:
    return f"{round(float(value) * 100):d}%"


def _num(value: Any, digits: int = 1) -> str:
    return f"{float(value):.{digits}f}"


def format_result(row: dict[str, Any], title: str = "") -> str:
    """Bloco de texto de um projeto."""
    m = row["metrics"]
    idx = row["index"]
    lines = [f"{row['project_id']}  {title}".rstrip(),
             f"  timeline: {row['source_label']} ({row['lang']}, {_num(row['duration'], 0)} s)"
             + ("  [desatualizada: o projeto mudou depois]" if row.get("outdated") else ""),
             f"  Cara de IA: {idx['score']} de 100 ({idx['band']})"]
    for part in idx["parts"]:
        target = str(part["target"]).translate(_ASCII_MATH)
        lines.append(f"    {part['label']:<36} {_num(part['value'], 2):>7}  alvo {target:<9} "
                     f"{_num(part['points'], 1):>5} de {part['weight']}")
    lines.append(f"  IA na tela {_pct(m['ai_screen_share'])} · real {_pct(m['real_share'])} · oficial "
                 f"{_pct(m['official_share'])} · gancho {m['hook_prov'] or '-'}")
    lines.append(f"  planos: mediana {_num(m['shot_median'], 2)} s (p10 {_num(m['p10'], 2)}, p90 {_num(m['p90'], 2)}), "
                 f"CV {_num(m['shot_cv'], 2)} · Ken Burns/pan {_pct(m['kb_share'])}")
    lines.append(f"  por minuto: eventos {_num(m['events_per_min'])}, SFX {_num(m['sfx_per_min'])}, trocas de tipo "
                 f"{_num(m['switch_per_min'])} · maior trecho parado {_num(m['max_still_s'])} s · mesma fonte seguida "
                 f"{m['max_same_prov_run']} planos ({_num(m['max_same_prov_run_s'])} s) · repetições "
                 f"{m['repeat_hits']}")
    adh = row.get("adherence")
    if adh and adh.get("score") is not None:
        lines.append(f"  aderência ao perfil da referência: {adh['score']} de 100")
    warns = [i for i in row["items"] if i["level"] != "ok"]
    for it in warns:
        at = f" (em {int(it['at_s']) // 60}:{int(it['at_s']) % 60:02d})" if it.get("at_s") is not None else ""
        lines.append(f"  ! {it['code']}: {it['title']}{at}")
    return "\n".join(lines)


def collect(project_ids: list[str], *, all_projects: bool, lang: str | None, source: str) -> list[dict[str, Any]]:
    """A medida de cada projeto (erro de um não interrompe os outros: vira `{"project_id", "error"}`)."""
    from studio.core.project import service
    from studio.workers.finalcheck.profile_check import project_metrics

    titles = {p["id"]: p.get("title") or "" for p in service.list_projects()}
    ids = list(titles) if all_projects else project_ids
    out: list[dict[str, Any]] = []
    for pid in ids:
        try:
            row = project_metrics(pid, lang, source)  # type: ignore[arg-type]
        except service.ProjectNotFound:
            out.append({"project_id": pid, "error": "projeto não encontrado"})
            continue
        except Exception as exc:  # noqa: BLE001 - um projeto quebrado não para a linha de base dos outros
            out.append({"project_id": pid, "error": str(exc)[:300]})
            continue
        row["title"] = titles.get(pid, "")
        out.append(row)
    return out


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="índice \"cara de IA\" e métricas de edição dos projetos (só leitura)")
    parser.add_argument("projects", nargs="*", metavar="PROJETO", help="ids dos projetos")
    parser.add_argument("--all", action="store_true", help="todos os projetos")
    parser.add_argument("--lang", default=None, help="idioma (padrão: o principal de cada projeto)")
    parser.add_argument("--fonte", dest="source", default="auto", choices=("auto", "final", "preview", "estimate"),
                        help="timeline medida: auto (final, senão prévia que bate com as cenas, senão estimada)")
    parser.add_argument("--json", action="store_true", help="saída em JSON")


def run(args: argparse.Namespace) -> int:
    from studio.db import readonly_engine

    if not args.all and not args.projects:
        print("Informe um ou mais projetos, ou --all.")
        return 2
    try:
        with readonly_engine():
            rows = collect(list(args.projects), all_projects=args.all, lang=args.lang, source=args.source)
    except (ValueError, FileNotFoundError) as exc:  # banco que não é SQLite em arquivo, ou que não existe
        print(f"Não deu para abrir o banco só para leitura: {exc}")
        return 2
    if args.json:
        slim = [{k: v for k, v in r.items() if k not in ("strip", "report")} for r in rows]
        print(json.dumps(slim, ensure_ascii=True, indent=2))
    else:
        blocks = [f"{r['project_id']}: erro: {r['error']}" if "error" in r else format_result(r, r.get("title", ""))
                  for r in rows]
        print("\n\n".join(blocks) if blocks else "Nenhum projeto.")
    return 1 if any("error" in r for r in rows) else 0
