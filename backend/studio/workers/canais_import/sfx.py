"""Importa os efeitos do canais para a biblioteca de SFX do usuário (ADR 0004 §3.8 e §4).

Lê `<canais>/plantao-vice/video/public/sfx/*.wav` **só para leitura** (cada arquivo é decodificado e copiado; nada é
escrito na pasta do canais) e importa em `<dados>/library/sfx/` com o id `canais-<nome>`. Nada vai para o repositório.

Licença:
- os nomes rastreados no `motor/passos/get_audio.py` (o dicionário `SFX`, id do Mixkit → arquivo) foram baixados de
  assets.mixkit.co. A Mixkit Free License cobre YouTube monetizado sem crédito, mas alguns itens são "Restricted"
  (só não comercial) e o ADR §4 pede a conferência item a item: sem ela (`license_checked=False`, o padrão) entram
  como "desconhecida", com o link do arquivo no Mixkit para conferir e declarar `mixkit-free` na biblioteca; com
  `license_checked=True` (o usuário conferiu os 17, como o `--licenca-conferida` do `scripts/fetch_sfx.py`) entram
  como `mixkit-free`. O `source_url` é o arquivo do item (`MIXKIT_ASSET`, o mesmo que o canais baixou);
- "desconhecida" para o resto (click, pop, stamp, type, whoosh, static, bleep, corte...): ficam fora do automático.

Os tipos banidos no padrão do estúdio (whip, whip2, ding, trombone) entram, mas nunca no automático (`SfxItem.auto`).
Cópias de trabalho do canais (`*_orig.wav`) ficam de fora.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from studio.media import sfx_library
from studio.workers.canais_import.reader import default_root

SFX_RELATIVE = Path("plantao-vice") / "video" / "public" / "sfx"
MIXKIT_IDS: dict[str, int] = {
    "whip": 1490, "whip2": 1492, "scratch": 704, "scratch2": 702, "coins": 2003, "moneybag": 1989, "cheer": 462,
    "shutter": 1133, "paper": 1530, "page": 1104, "riser": 790, "impact": 1143, "bass": 2299, "ding": 1368,
    "typeclick": 1119, "rimshot": 579, "trombone": 744,
}
"""Nome do arquivo → id do Mixkit (cópia de `canais/motor/passos/get_audio.py:48-52`, o dicionário `SFX`)."""
MIXKIT_ASSET = "https://assets.mixkit.co/active_storage/sfx/{id}/{id}-preview.mp3"
"""O arquivo do item no Mixkit (o mesmo que o `get_audio.py` do canais baixa): identifica o efeito para conferir."""
MIXKIT_LICENSE_URL = "https://mixkit.co/license/#sfxFree"
KIND_OF: dict[str, str] = {
    "scratch2": "scratch", "whip2": "whip", "bass": "bass_drop", "moneybag": "coins", "page": "paper",
    "typeclick": "type", "corte": "cut",
}
"""Nome do arquivo → tipo na biblioteca (o resto: o próprio nome)."""
TAGS: dict[str, list[str]] = {
    "scratch": ["meme", "pera", "interrupcao"], "scratch2": ["meme", "pera", "interrupcao"],
    "cheer": ["plateia", "comemoracao"], "coins": ["dinheiro"], "moneybag": ["dinheiro"],
    "rimshot": ["piada", "ba-dum-tss"], "trombone": ["fracasso", "piada"], "impact": ["impacto", "reveal"],
    "riser": ["tensao", "subida"], "bass": ["reveal", "grave"], "shutter": ["foto", "print"],
    "paper": ["documento"], "page": ["documento"], "typeclick": ["digitacao"], "type": ["digitacao"],
    "stamp": ["carimbo"], "pop": ["texto"], "click": ["interface"], "bleep": ["censura"],
}
SKIP_SUFFIXES = ("_orig",)


@dataclass
class SfxImportReport:
    source: str
    imported: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {"source": self.source, "imported": self.imported, "skipped": self.skipped, "warnings": self.warnings}


def sfx_dir(root: Path | None = None) -> Path:
    return (root or default_root()) / SFX_RELATIVE


def plan(folder: Path, *, license_checked: bool = False) -> list[dict[str, Any]]:
    """O que seria importado de `folder` (sem tocar em nada): nome, tipo, licença e origem de cada .wav."""
    out = []
    for path in sorted(folder.glob("*.wav")):
        stem = path.stem.lower()
        if stem.endswith(SKIP_SUFFIXES):
            out.append({"file": path.name, "skip": "cópia de trabalho do canais"})
            continue
        mixkit = MIXKIT_IDS.get(stem)
        out.append({
            "file": path.name,
            "id": f"canais-{sfx_library.slug(stem)}",
            "kind": KIND_OF.get(stem, sfx_library.slug(stem)),
            "license": "mixkit-free" if mixkit and license_checked else sfx_library.UNKNOWN_LICENSE,
            "source_url": MIXKIT_ASSET.format(id=mixkit) if mixkit else "",
            "author": "Mixkit" if mixkit else "",
            "tags": ["canais", *TAGS.get(stem, [])],
            "note": ((f"Mixkit {mixkit} ({MIXKIT_LICENSE_URL}); baixado pelo canais/get_audio.py"
                      + ("; licença conferida pelo usuário" if license_checked
                         else "; confira se não é Restricted e declare mixkit-free"))
                     if mixkit else "efeito do canais sem origem registrada"),
        })
    return out


def import_sfx(folder: Path | None = None, *, dry_run: bool = False, replace: bool = False,
               license_checked: bool = False, progress: Any = None) -> SfxImportReport:
    """Importa os .wav do canais para a biblioteca do usuário. `dry_run`: só o relatório. Itens que já existem ficam
    (com `replace`, são medidos e copiados de novo). `license_checked`: o usuário conferiu no Mixkit que os itens
    rastreados não são "Restricted" (entram como `mixkit-free`; sem isso, "desconhecida")."""
    folder = folder or sfx_dir()
    report = SfxImportReport(source=str(folder))
    if not folder.is_dir():
        report.warnings.append(f"pasta de efeitos do canais não encontrada: {folder}")
        return report
    existing = {i.id: i for i in sfx_library.user_items()}
    entries = plan(folder, license_checked=license_checked)
    for index, entry in enumerate(entries):
        if progress is not None:
            progress(index / max(1, len(entries)), f"Importando {entry['file']}")
        if entry.get("skip"):
            report.skipped.append({"file": entry["file"], "reason": entry["skip"]})
            continue
        if entry["id"] in existing and not replace:
            report.skipped.append({"file": entry["file"], "reason": f"já importado como '{entry['id']}'"})
            continue
        # Refazer não desfaz a licença que o usuário já declarou na biblioteca.
        before = existing.get(entry["id"])
        license, author = entry["license"], entry["author"]
        if before is not None and license == sfx_library.UNKNOWN_LICENSE and before.license != license:
            license, author = before.license, before.author or author
        if dry_run:
            report.imported.append({**{k: entry[k] for k in ("file", "id", "kind")}, "license": license})
            continue
        try:
            item = sfx_library.import_file(
                folder / entry["file"], kind=entry["kind"], license=license, tags=entry["tags"],
                source_url=entry["source_url"], author=author, lib_id=entry["id"], replace=replace,
                imported_from="canais:plantao-vice", note=entry["note"])
        except sfx_library.SfxLibraryError as exc:
            report.skipped.append({"file": entry["file"], "reason": str(exc)})
            continue
        report.imported.append({"file": entry["file"], "id": item.id, "kind": item.kind, "license": item.license,
                                "tonal": item.tonal, "apito": item.apito, "auto": item.auto()})
        warning = item.warning()
        if warning:
            report.warnings.append(warning)
    return report
