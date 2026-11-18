"""Subcomando `studio thumbkit` (biblioteca de imagens das thumbs por canal). Registrado pelo `cli.py`.

    studio thumbkit import <channel_id> [pasta_canais] [--sem-quadros]
    studio thumbkit list <channel_id>
    studio thumbkit render <camadas.json> <saida.png|.jpg> --canal <channel_id> [--short] [--exportar] [--poses PASTA]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

COMMAND = "thumbkit"


def register(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(COMMAND, help="biblioteca de imagens das thumbs (quadros oficiais, logos, recortes)")
    cmds = p.add_subparsers(dest="thumbkit_cmd", required=True)
    imp = cmds.add_parser("import", help="copia logos e quadros do canais e extrai os tempos curados dos trailers")
    imp.add_argument("channel_id")
    imp.add_argument("pasta", nargs="?", default=None, help="pasta do canais (padrão: ~/canais ou VS_CANAIS_DIR)")
    imp.add_argument("--sem-quadros", action="store_true", help="não extrai quadros dos trailers (só copia arquivos)")
    imp.add_argument("--seed", default=None, help="semente alternativa (JSON no formato de thumbkit-seed.json)")
    ls = cmds.add_parser("list", help="lista os itens da biblioteca do canal")
    ls.add_argument("channel_id")
    rd = cmds.add_parser("render", help="desenha uma thumb a partir de um ThumbLayers em JSON e mostra a checagem")
    rd.add_argument("layers", help="arquivo JSON com o ThumbLayers")
    rd.add_argument("out", help="arquivo de saída (.png/.jpg)")
    rd.add_argument("--canal", required=True, help="id do canal (biblioteca de thumbs e poses do avatar)")
    rd.add_argument("--short", action="store_true", help="capa vertical 1080×1920")
    rd.add_argument("--exportar", action="store_true", help="tamanho do YouTube (3840×2160, JPG ≤ 2 MB)")
    rd.add_argument("--poses", default=None, help="pasta extra com as poses do avatar (PNG com alfa)")
    rd.add_argument("--titulo", default=None, help="título do vídeo, para a checagem de repetição")
    rd.add_argument("--oficial", dest="oficial", action="store_true", default=None,
                    help="libera o material oficial só neste render (o padrão segue o canal)")
    rd.add_argument("--sem-oficial", dest="oficial", action="store_false", help="bloqueia o material oficial")


def _channel_rules(channel_id: str) -> tuple[bool, object]:
    """(material oficial liberado, avatar do canal na thumb) pelo `style_bible` do canal; sem banco, tudo desligado."""
    try:
        from studio.core.project.document import Presenter, effective_allow_editorial
        from studio.workers.visual.thumbs import channel_bible

        bible = channel_bible(channel_id)
    except Exception:  # noqa: BLE001 - banco ausente: o render manual segue bloqueado
        return False, None
    presenter = Presenter.from_bible(bible)
    on = presenter.enabled and presenter.use_in_thumb and bool(presenter.poses)
    return effective_allow_editorial(False, bible), presenter if on else None


def run(args: argparse.Namespace) -> int:
    from studio.media import thumb_assets as A

    if args.thumbkit_cmd == "import":
        pasta = Path(args.pasta) if args.pasta else A.default_canais_dir()
        if not pasta.is_dir():
            print(f"pasta do canais não encontrada: {pasta}")
            return 2
        seed = A.load_seed(Path(args.seed)) if args.seed else None
        summary = A.import_kit(args.channel_id, pasta, seed=seed, frames=not args.sem_quadros)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
        return 0
    if args.thumbkit_cmd == "list":
        manifest = A.load_manifest(A.thumbkit_dir(args.channel_id), args.channel_id)
        for it in manifest["items"]:
            flag = " [oficial]" if it.get("editorial") else ""
            print(f"{it['kind']:7} {it['id']:24} {', '.join(it.get('tags') or [])}{flag}")
        print(f"{len(manifest['items'])} itens; vídeos: {', '.join(manifest['videos']) or '-'}")
        return 0
    if args.thumbkit_cmd == "render":
        from studio.core.project.document import ThumbLayers
        from studio.media import thumb_check, thumb_compose
        from studio.media import thumb_templates as T

        layers = ThumbLayers.model_validate(json.loads(Path(args.layers).read_text(encoding="utf-8")))
        allow, presenter = _channel_rules(args.canal)
        if args.oficial is not None:
            allow = args.oficial
        resolver = A.AssetResolver.for_channel(args.canal, presenter=presenter, allow_editorial=allow)
        if args.poses:
            resolver.pose_dirs.append(Path(args.poses))
            resolver.presenter_on = True
        if not allow:
            print("material oficial bloqueado neste canal (allow_editorial desligado): use --oficial para testar",
                  file=sys.stderr)
        out = Path(args.out)
        if args.exportar:
            info = thumb_compose.export(layers, out, is_long=not args.short, assets=resolver)
            print(json.dumps({k: str(v) if isinstance(v, Path) else v for k, v in info.items() if k != "layout"},
                             ensure_ascii=False))
            return 0
        size = T.SHORT_CANVAS if args.short else T.CANVAS
        img = thumb_compose.render(layers, size, resolver)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out)
        score = thumb_check.check(img, layers, not args.short, title=args.titulo)
        print(json.dumps(score, ensure_ascii=False, indent=1))
        return 0
    return 2
