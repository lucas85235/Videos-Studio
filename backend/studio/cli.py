"""Linha de comando: `python -m studio serve` inicia o app."""

from __future__ import annotations

import argparse
import importlib
import logging
import webbrowser
from types import ModuleType

# Subcomandos em arquivo próprio (cada fase cuida do seu sem disputar este arquivo). Contrato de cada módulo:
#   COMMAND: str                                      nome do subcomando ("thumbkit", "presenter")
#   register(sub: argparse._SubParsersAction) -> None  cria o parser do subcomando (com os sub-subcomandos)
#   run(args: argparse.Namespace) -> int | None        executa; um inteiro diferente de 0 vira o código de saída
# O import é tolerante: módulo ainda inexistente (fase em andamento) ou quebrado não derruba os outros comandos.
PLUGIN_MODULES = ("studio.cli_thumbkit", "studio.cli_presenter", "studio.cli_references", "studio.cli_edit_metrics")


def _load_plugins() -> dict[str, ModuleType]:
    plugins: dict[str, ModuleType] = {}
    for name in PLUGIN_MODULES:
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name != name:  # o módulo existe, mas importa algo que falta
                logging.getLogger(__name__).warning("subcomando %s indisponível: %s", name, exc)
            continue
        except Exception as exc:  # noqa: BLE001 - um subcomando quebrado não pode derrubar o `serve`
            logging.getLogger(__name__).warning("subcomando %s indisponível: %s", name, exc)
            continue
        command = getattr(module, "COMMAND", None)
        if isinstance(command, str) and callable(getattr(module, "register", None)) and callable(
            getattr(module, "run", None)
        ):
            plugins[command] = module
    return plugins


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="videos-studio", description="Vulto: estúdio de vídeos sem rosto")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="inicia a API e a interface web")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--open", action="store_true", help="abre o navegador")
    serve.add_argument("--reload", action="store_true", help="recarrega ao editar o código (desenvolvimento)")
    sub.add_parser("check", help="verifica FFmpeg, Node, renderer e provedores")
    clean = sub.add_parser("clean", help="limpa renders antigos e sobras do cache (narração e imagens só com --versoes-antigas ou pelo limite)")
    clean.add_argument("--dry-run", action="store_true", help="só mostra o que seria apagado")
    clean.add_argument("--versoes-antigas", action="store_true",
                       help="também apaga narrações e imagens que só versões antigas usavam")
    clean.add_argument("--limite-gb", type=float, default=None,
                       help="apaga pelo último uso até o cache caber neste tamanho; 0 = sem limite (padrão: o salvo em Configurações)")
    clean.add_argument("--auto", action="store_true",
                       help="rotina agendada: segue a política salva em Configurações e só limpa se passou do limite")
    sub.add_parser("voz-local", help="baixa o modelo da voz local da prévia (Kokoro, ~350 MB, uma vez só)")
    sub.add_parser("rotinas", help="roda as rotinas do dia uma vez, sem abrir o app (para a tarefa agendada)")
    imp = sub.add_parser("importar-canais", help="importa o histórico do projeto antigo canais (só lê a pasta dele)")
    imp.add_argument("--pasta", default=None, help="pasta do canais (padrão: ~/canais ou VS_CANAIS_DIR)")
    imp.add_argument("--dry-run", action="store_true", help="só mostra o que entraria, sem gravar")
    imp.add_argument(
        "--canal", action="append", default=[], metavar="SLUG=DESTINO",
        help="destino de um canal do canais: auto (padrão), pular ou o id de um canal do Vulto; repita por canal",
    )
    plugins = _load_plugins()
    for module in plugins.values():
        module.register(sub)
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # O httpx registra em INFO a URL inteira de cada chamada, e algumas APIs (Instagram) levam segredo e token na
    # query string. Só avisos e erros do httpx vão para o log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    from studio.config import get_settings

    settings = get_settings()
    if args.command in plugins:
        code = plugins[args.command].run(args)
        if code:
            raise SystemExit(code)
    elif args.command == "serve":
        import uvicorn

        host = args.host or settings.host
        port = args.port or settings.port
        if args.open:
            webbrowser.open(f"http://{host}:{port}/")
        uvicorn.run("studio.api.app:create_app", factory=True, host=host, port=port, reload=args.reload, log_level="info")
    elif args.command == "clean":
        from studio.core.graph import gc
        from studio.db import init_db

        init_db()
        policy = gc.get_policy()
        if args.auto:
            if not policy.auto or policy.limit_bytes is None:
                print("Limpeza automática desligada em Configurações (ou sem limite de espaço). Nada a fazer.")
                return
            if gc.busy_jobs():
                print("Há tarefas em andamento; a limpeza fica para a próxima.")
                return
            old_versions, limit = policy.old_versions, policy.limit_bytes
        else:
            old_versions = args.versoes_antigas
            if args.limite_gb is None:
                limit = policy.limit_bytes
            elif args.limite_gb < 0:
                parser.error("--limite-gb não pode ser negativo (use 0 para limpar sem limite de espaço)")
            else:
                limit = int(args.limite_gb * gc.GB) or None  # 0: sem limite, mesmo com um limite salvo
        report = gc.collect(dry_run=args.dry_run, old_versions=old_versions, limit_bytes=limit,
                            abort=gc.busy_jobs if args.auto else None)
        if report.aborted:
            print("Uma tarefa começou enquanto a limpeza calculava; nada foi apagado. Fica para a próxima.")
            return
        mb = 1024 * 1024
        print(f"Cache: {sum(report.sizes.values()) / mb:.1f} MB")
        for category, size in sorted(report.freed.items(), key=lambda kv: -kv[1]):
            print(f"  {gc.CATEGORY_LABELS.get(category, category)}: {report.removed[category]} itens, {size / mb:.1f} MB")
        verb = "seriam liberados" if args.dry_run else "liberados"
        print(f"{sum(report.freed.values()) / mb:.1f} MB {verb}; {report.kept_in_use} artefatos em uso preservados.")
        if old_versions or limit is not None:
            if report.unreadable_projects:
                print("Narrações e imagens não foram tocadas: não deu para ler a versão atual destes projetos "
                      f"(abra-os no app para conferir): {', '.join(report.unreadable_projects)}")
            if report.legacy_publications and report.legacy_until:
                print(f"Narrações e imagens criadas até {report.legacy_until:%d/%m/%Y} ficam: estas publicações são de "
                      "antes do registro do que cada vídeo usa: " + ", ".join(report.legacy_publications))
        if limit is not None and report.total_after > limit:
            print(f"O cache ainda passa do limite ({report.total_after / mb:.0f} MB de {limit / mb:.0f} MB): o resto "
                  "está em uso ou foi criado nas últimas 24 h.")
    elif args.command == "voz-local":
        from studio.providers import kokoro

        if kokoro.model_ready():
            print(f"A voz local já está instalada em {kokoro.models_dir()}")
        else:
            print(f"Baixando a voz local ({kokoro.MODEL_MB} MB)…")
            print(f"Pronto: {kokoro.ensure_model()}")
    elif args.command == "rotinas":
        from studio.db import init_db
        from studio.workers.ops.offline import run_once

        init_db()
        result = run_once()
        print(f"Rotinas ({result['status']}): {result['message']}")
        if result["status"] in ("falhou", "parcial"):  # o Agendador mostra o código de saída
            raise SystemExit(1)
    elif args.command == "importar-canais":
        from studio.db import init_db
        from studio.workers.canais_import.apply import format_report, import_canais
        from studio.workers.canais_import.reader import CanaisError

        choices: dict[str, str] = {}
        for item in args.canal:
            slug, sep, target = item.partition("=")
            if not sep or not slug.strip() or not target.strip():
                parser.error(f"--canal no formato SLUG=DESTINO (recebido: {item})")
            choices[slug.strip()] = target.strip()
        init_db()
        try:
            report = import_canais(args.pasta, choices, dry_run=args.dry_run)
        except (CanaisError, ValueError) as exc:
            print(f"Erro: {exc}")
            raise SystemExit(1) from exc
        print(format_report(report))
    elif args.command == "check":
        from studio.db import init_db
        from studio.media.ffmpeg import available_encoders
        from studio.media.render import renderer_version
        from studio.providers.registry import describe_all

        init_db()
        print(f"Dados: {settings.data_dir}")
        print(f"Renderer: versão {renderer_version()}")
        print(f"NVENC: {'sim' if 'h264_nvenc' in available_encoders() else 'não'}")
        for p in describe_all():
            mark = "OK " if p["available"] else "-- "
            print(f"  {mark}{p['kind']:<6} {p['id']:<18} {p['reason'] if not p['available'] else ''}")


if __name__ == "__main__":
    main()
