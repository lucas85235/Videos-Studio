"""Subcomando `studio presenter` (registrado pelo `cli.py`): importa as poses do avatar do apresentador.

    studio presenter import <channel_id> [pasta] [--enable | --disable] [--replace-anchors]

Sem pasta: `<canais>/plantao-vice/video/public/apresentador`. O avatar importado fica desligado, a não ser com
`--enable` (decisão de 17/11: ligado no Lucas em Vice para thumb e vídeo longo).
"""

from __future__ import annotations

import argparse

COMMAND = "presenter"


def register(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(COMMAND, help="avatar do apresentador (importar poses PNG)")
    actions = parser.add_subparsers(dest="presenter_command", required=True)
    imp = actions.add_parser("import", help="importa as poses PNG (com alfa) de uma pasta para o canal")
    imp.add_argument("channel_id", help="id do canal no Vulto (ex.: chn_yplob5wrcl)")
    imp.add_argument("folder", nargs="?", default=None, help="pasta com os PNG (padrão: a do apresentador no canais)")
    toggle = imp.add_mutually_exclusive_group()
    toggle.add_argument("--enable", dest="enable", action="store_const", const=True, default=None,
                        help="liga o avatar no canal")
    toggle.add_argument("--disable", dest="enable", action="store_const", const=False, help="desliga o avatar")
    imp.add_argument("--replace-anchors", action="store_true",
                     help="troca as âncoras do personagem 'lucas' pelas poses base, oculos e apontando")


def run(args: argparse.Namespace) -> int:
    from studio.db import init_db
    from studio.workers.canais_import.presenter import format_report, import_folder

    if args.presenter_command != "import":
        return 2
    init_db()
    try:
        report = import_folder(args.channel_id, args.folder, enable=args.enable, replace_anchors=args.replace_anchors)
    except KeyError:
        print(f"canal não encontrado: {args.channel_id}")
        return 1
    except FileNotFoundError as exc:
        print(f"pasta não encontrada: {exc}")
        return 1
    print(format_report(report))
    return 1 if report.rejected and not (report.imported or report.updated) else 0
