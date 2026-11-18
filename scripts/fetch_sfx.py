"""Baixa efeitos sonoros para a biblioteca do usuário (ADR 0004 §3.8 e §4). Roda só à mão, pelo usuário.

Nada vai para o repositório: os arquivos ficam em `<dados>/library/sfx/` com a licença no manifesto.

    cd backend
    uv run python ../scripts/fetch_sfx.py freesound 123456 --kind boom --tags meme,reveal
    uv run python ../scripts/fetch_sfx.py mixkit 704 --kind scratch --licenca-conferida
    uv run python ../scripts/fetch_sfx.py canais --simular

- `freesound`: só CC0 (outra licença é recusada); precisa da chave de API do Freesound (Configurações ou
  `VS_FREESOUND_API_KEY`). Baixa a prévia HQ (o original exige OAuth).
- `mixkit`: a Mixkit Free License permite YouTube monetizado sem crédito, mas não a redistribuição do arquivo, e alguns
  itens são "Restricted" (só não comercial). Confira a página do efeito; com `--licenca-conferida` ele entra como
  `mixkit-free`, sem isso como "desconhecida" (fora do automático).
- `canais`: importa os .wav do projeto `canais` (só leitura lá).
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from studio.config import get_settings  # noqa: E402
from studio.media import sfx_library  # noqa: E402

MIXKIT_PREVIEW = "https://assets.mixkit.co/active_storage/sfx/{id}/{id}-preview.mp3"
MIXKIT_PAGE = "https://mixkit.co/free-sound-effects/"


def _tags(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def fetch_mixkit(sound_id: int, kind: str, tags: list[str], checked: bool) -> sfx_library.SfxItem:
    import httpx

    url = MIXKIT_PREVIEW.format(id=sound_id)
    resp = httpx.get(url, timeout=30, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    if resp.status_code != 200 or len(resp.content) < 2000:
        raise SystemExit(f"falhou: {url} ({resp.status_code})")
    with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
        path = Path(tmp) / f"mixkit-{sound_id}.mp3"
        path.write_bytes(resp.content)
        return sfx_library.import_file(
            path, kind=kind, tags=tags, license="mixkit-free" if checked else sfx_library.UNKNOWN_LICENSE,
            author="Mixkit", source_url=f"{MIXKIT_PAGE}#{sound_id}", lib_id=f"mixkit-{sound_id}", replace=True,
            imported_from=f"mixkit:{sound_id}",
            note="licença conferida pelo usuário" if checked else "confira se não é Restricted e declare a licença")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="source", required=True)
    fs = sub.add_parser("freesound", help="som CC0 do Freesound (id ou link)")
    fs.add_argument("sound")
    fs.add_argument("--kind", required=True)
    fs.add_argument("--tags", default="")
    mk = sub.add_parser("mixkit", help="efeito do Mixkit (id numérico)")
    mk.add_argument("sound", type=int)
    mk.add_argument("--kind", required=True)
    mk.add_argument("--tags", default="")
    mk.add_argument("--licenca-conferida", action="store_true",
                    help="você conferiu na página que o item é Free License (não Restricted)")
    cn = sub.add_parser("canais", help="efeitos do projeto canais (só leitura lá)")
    cn.add_argument("--pasta", default=None)
    cn.add_argument("--simular", action="store_true")
    cn.add_argument("--substituir", action="store_true")
    args = parser.parse_args(argv)

    if get_settings().offline and args.source != "canais":
        print("modo offline (VS_OFFLINE=1): nada a baixar")
        return 1
    try:
        if args.source == "freesound":
            item = sfx_library.fetch_freesound(args.sound, kind=args.kind, tags=_tags(args.tags))
        elif args.source == "mixkit":
            item = fetch_mixkit(args.sound, args.kind, _tags(args.tags), args.licenca_conferida)
        else:
            from studio.workers.canais_import import sfx as canais_sfx

            report = canais_sfx.import_sfx(Path(args.pasta) if args.pasta else None, dry_run=args.simular,
                                           replace=args.substituir)
            for row in report.imported:
                print(f"  + {row['id']:24s} {row['kind']:12s} {row['license']}")
            for row in report.skipped:
                print(f"  - {row['file']}: {row['reason']}")
            for warning in report.warnings:
                print(f"  ! {warning}")
            return 0
    except sfx_library.SfxLibraryError as exc:
        print(f"erro: {exc}")
        return 1
    print(f"{item.id}: {item.kind}, licença {item.license}, {item.dur:.2f} s, tonal={item.tonal}, apito={item.apito}")
    if item.warning():
        print(f"aviso: {item.warning()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
