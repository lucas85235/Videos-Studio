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
    clean = sub.add_parser("clean", help="limpa renders antigos e sobras do cache (narração e imagens ficam)")
    clean.add_argument("--dry-run", action="store_true", help="só mostra o que seria apagado")
    sub.add_parser("voz-local", help="baixa o modelo da voz local da prévia (Kokoro, ~350 MB, uma vez só)")
    sub.add_parser("rotinas", help="roda as rotinas do dia uma vez, sem abrir o app (para a tarefa agendada)")
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
        report = gc.collect(dry_run=args.dry_run)
        mb = 1024 * 1024
        print(f"Cache: {sum(report.sizes.values()) / mb:.1f} MB")
        for category, size in sorted(report.freed.items(), key=lambda kv: -kv[1]):
            print(f"  {gc.CATEGORY_LABELS.get(category, category)}: {report.removed[category]} itens, {size / mb:.1f} MB")
        verb = "seriam liberados" if args.dry_run else "liberados"
        print(f"{sum(report.freed.values()) / mb:.1f} MB {verb}; {report.kept_in_use} artefatos em uso preservados.")
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
