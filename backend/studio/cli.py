"""Linha de comando: `python -m studio serve` inicia o app."""

from __future__ import annotations

import argparse
import logging
import webbrowser


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
                       help="apaga pelo último uso até o cache caber neste tamanho (padrão: o salvo em Configurações)")
    clean.add_argument("--auto", action="store_true",
                       help="rotina agendada: segue a política salva em Configurações e só limpa se passou do limite")
    sub.add_parser("voz-local", help="baixa o modelo da voz local da prévia (Kokoro, ~350 MB, uma vez só)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from studio.config import get_settings

    settings = get_settings()
    if args.command == "serve":
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
            limit = int(args.limite_gb * gc.GB) if args.limite_gb else policy.limit_bytes
        report = gc.collect(dry_run=args.dry_run, old_versions=old_versions, limit_bytes=limit)
        mb = 1024 * 1024
        print(f"Cache: {sum(report.sizes.values()) / mb:.1f} MB")
        for category, size in sorted(report.freed.items(), key=lambda kv: -kv[1]):
            print(f"  {gc.CATEGORY_LABELS.get(category, category)}: {report.removed[category]} itens, {size / mb:.1f} MB")
        verb = "seriam liberados" if args.dry_run else "liberados"
        print(f"{sum(report.freed.values()) / mb:.1f} MB {verb}; {report.kept_in_use} artefatos em uso preservados.")
        if report.legacy_projects and (old_versions or limit is not None):
            print("Narrações e imagens não foram tocadas: refaça a prévia destes projetos (build antigo, sem "
                  f"manifesto): {', '.join(report.legacy_projects)}")
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
