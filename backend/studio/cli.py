"""Linha de comando: `python -m studio serve` inicia o app."""

from __future__ import annotations

import argparse
import logging
import webbrowser


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="videos-studio", description="Videos Studio")
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="inicia a API e a interface web")
    serve.add_argument("--host", default=None)
    serve.add_argument("--port", type=int, default=None)
    serve.add_argument("--open", action="store_true", help="abre o navegador")
    serve.add_argument("--reload", action="store_true", help="recarrega ao editar o código (desenvolvimento)")
    sub.add_parser("check", help="verifica FFmpeg, Node, renderer e provedores")
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
