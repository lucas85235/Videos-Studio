"""Biblioteca de memes e reações do usuário, com licença declarada (ADR 0004 §3.8 e §4).

Nenhum meme vem embutido no Vulto. Três fontes:

- **biblioteca do usuário** (`<dados>/library/memes/`, manifesto `manifest.json`): PNG ou WebM com alfa, cada um com
  `{id, file, tags, license: own|cc0|cc_by|fair_comment, risk, max_ms, sfx_id}`. O upload exige a licença;
- **reações do avatar**: as poses do apresentador do canal viram reações (licença `own`), achadas pela emoção;
- **"meme sem meme"** (`TEMPLATES`): congela, zoom, vinheta e legenda, só parâmetros (eventos `FxEvent`), sem arquivo.

O planejador (`editor-planejador`) usa `pick(tag, seed, exclude)`; sem meme com a tag, usa `template_events`.
Nunca extrair meme da referência para reutilizar; GIPHY e Tenor ficam de fora (o GIF não licencia a obra de origem).
"""

from __future__ import annotations

import json
import random
import shutil
import threading
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from studio.config import get_settings
from studio.core.project.document import FxEvent, Presenter
from studio.media.sfx_library import slug

MANIFEST = "manifest.json"
MemeLicense = Literal["own", "cc0", "cc_by", "fair_comment"]
MEME_LICENSES: tuple[str, ...] = ("own", "cc0", "cc_by", "fair_comment")
LICENSE_LABELS = {
    "own": "próprio",
    "cc0": "CC0 (domínio público)",
    "cc_by": "CC BY (crédito obrigatório)",
    "fair_comment": "citação/comentário (uso por conta e risco: pode gerar reivindicação)",
}
Risk = Literal["low", "medium", "high"]
RISKS: tuple[str, ...] = ("low", "medium", "high")
MEDIA_EXTS = {".png": "png", ".webm": "webm"}
MAX_UPLOAD_BYTES = 40_000_000
_ID_RE = r"^[a-z0-9][a-z0-9_\-]{0,63}$"
_lock = threading.RLock()


class MemeLibraryError(ValueError):
    """Pedido inválido para a biblioteca de memes (mensagem em português)."""


class MemeItem(BaseModel):
    """Um meme ou reação. `file` relativo à pasta de dados (`library/memes/<id>.png` ou a pose do avatar)."""

    id: str = Field(pattern=_ID_RE)
    file: str
    media: Literal["png", "webm"] = "png"
    tags: list[str] = []
    license: MemeLicense
    risk: Risk = "low"
    """Risco de reivindicação (claim): `high` nunca entra no automático."""
    max_ms: int = Field(default=1200, ge=200, le=4000)
    """Quanto tempo o meme pode ficar na tela (pontuação de 0,4 a 1,5 s)."""
    sfx_id: str | None = None
    """Efeito da biblioteca de SFX que entra junto (None: o planejador escolhe)."""
    author: str = ""
    source_url: str = ""
    origin: Literal["user", "presenter"] = "user"
    note: str = ""

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(t for t in (slug(v) for v in value) if t))

    def path(self) -> Path:
        return get_settings().data_dir / self.file

    def public(self) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "license_label": LICENSE_LABELS.get(self.license, self.license),
                "auto": self.risk != "high"}


class MemeTemplate(BaseModel):
    """"Meme sem meme": a reação feita só com a edição (eventos do `FxEvent`, sem arquivo de terceiros)."""

    id: str
    label: str
    tags: list[str]
    events: list[dict[str, Any]]
    """Eventos sem `at` (o `template_events` põe o gatilho). `{line}` no texto vira a fala da piada."""


TEMPLATES: dict[str, MemeTemplate] = {t.id: t for t in (
    MemeTemplate(
        id="freeze_zoom", label="Congela + zoom + boom", tags=["reveal", "piada", "choque"],
        events=[
            {"type": "freeze", "dur_ms": 900, "params": {"vignette": True}},
            {"type": "punch", "dur_ms": 900, "params": {"scale": 1.3}},
            {"type": "sfx", "params": {"lib_id": "boom"}},
            {"type": "text_pop", "dur_ms": 900, "params": {"text": "{line}"}},
        ]),
    MemeTemplate(
        id="record_scratch", label="Arranhão + congela (\"pera...\")", tags=["pera", "interrupcao", "aparte"],
        events=[
            {"type": "freeze", "dur_ms": 1100, "params": {"vignette": False}},
            {"type": "sfx", "params": {"lib_id": "scratch"}},
            {"type": "text_pop", "dur_ms": 1100, "params": {"text": "{line}"}},
        ]),
    MemeTemplate(
        id="dramatic_zoom", label="Zoom dramático + vinheta", tags=["tensao", "suspeita", "drama"],
        events=[
            {"type": "punch", "dur_ms": 1400, "params": {"scale": 1.45}},
            {"type": "sfx", "params": {"lib_id": "bass_drop"}},
            {"type": "music_drop", "dur_ms": 500},
        ]),
    MemeTemplate(
        id="caption_only", label="Legenda seca", tags=["ironia", "comentario"],
        events=[
            {"type": "text_pop", "dur_ms": 1200, "params": {"text": "{line}"}},
            {"type": "sfx", "params": {"lib_id": "pop"}},
        ]),
)}


def template_events(name: str, at: str, line: str = "") -> list[FxEvent]:
    """Os eventos do template no gatilho `at` (palavra, "palavra#2" ou "@ms"). Texto vazio: o `text_pop` sai."""
    template = TEMPLATES.get(name)
    if template is None:
        raise KeyError(name)
    out: list[FxEvent] = []
    for raw in template.events:
        params = dict(raw.get("params", {}))
        if "text" in params:
            text = str(params["text"]).replace("{line}", line).strip()
            if not text:
                continue
            params["text"] = text
        out.append(FxEvent.model_validate({**raw, "params": params, "at": at}))
    return out


def template_for(tag: str, seed: int) -> MemeTemplate:
    """O template da tag (o primeiro que a tem; senão um sorteado pela semente)."""
    key = slug(tag)
    for template in TEMPLATES.values():
        if key in template.tags:
            return template
    return random.Random(f"{seed}:{key}").choice(list(TEMPLATES.values()))


# --------------------------------------------------------------------------------------------
# Biblioteca do usuário
# --------------------------------------------------------------------------------------------


def memes_root() -> Path:
    root = get_settings().library_dir / "memes"
    root.mkdir(parents=True, exist_ok=True)
    return root


def user_items() -> list[MemeItem]:
    path = memes_root() / MANIFEST
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [MemeItem.model_validate(entry) for entry in raw.get("items", [])]


def _save(items: list[MemeItem]) -> None:
    root = memes_root()
    payload = {"version": 1, "items": [i.model_dump(mode="json") for i in sorted(items, key=lambda i: i.id)]}
    tmp = root / f".{MANIFEST}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(root / MANIFEST)


def check_license(license: str | None, author: str = "") -> str:
    value = (license or "").strip().lower()
    if value not in MEME_LICENSES:
        raise MemeLibraryError(f"licença obrigatória: use {', '.join(MEME_LICENSES)}")
    if value == "cc_by" and not author.strip():
        raise MemeLibraryError("CC BY exige o nome do autor (o crédito é obrigatório)")
    return value


def add(
    src: Path,
    *,
    license: str,
    tags: list[str] | None = None,
    risk: str = "low",
    max_ms: int = 1200,
    sfx_id: str | None = None,
    author: str = "",
    source_url: str = "",
    lib_id: str | None = None,
    note: str = "",
) -> MemeItem:
    """Copia o PNG/WebM para a biblioteca com a licença declarada (obrigatória)."""
    src = Path(src)
    license = check_license(license, author)
    media = MEDIA_EXTS.get(src.suffix.lower())
    if media is None:
        raise MemeLibraryError("formato não aceito: use PNG ou WebM (com fundo transparente)")
    if risk not in RISKS:
        raise MemeLibraryError(f"risco inválido: use {', '.join(RISKS)}")
    if not src.is_file() or src.stat().st_size == 0:
        raise MemeLibraryError("arquivo vazio")
    if src.stat().st_size > MAX_UPLOAD_BYTES:
        raise MemeLibraryError("arquivo grande demais (até 40 MB)")
    with _lock:
        items = user_items()
        taken = {i.id for i in items}
        base = slug(lib_id or src.stem) or "meme"
        new_id, n = base, 2
        while new_id in taken:
            new_id, n = f"{base}-{n}", n + 1
        root = memes_root()
        dest = root / f"{new_id}{src.suffix.lower()}"
        shutil.copyfile(src, dest)
        rel = dest.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()
        item = MemeItem(id=new_id, file=rel, media=media, tags=tags or [], license=license, risk=risk,  # type: ignore[arg-type]
                        max_ms=max_ms, sfx_id=sfx_id or None, author=author, source_url=source_url, note=note)
        _save([*items, item])
        return item


def delete(lib_id: str) -> bool:
    with _lock:
        items = user_items()
        keep = [i for i in items if i.id != lib_id]
        if len(keep) == len(items):
            return False
        gone = next(i for i in items if i.id == lib_id)
        gone.path().unlink(missing_ok=True)
        _save(keep)
        return True


# --------------------------------------------------------------------------------------------
# Reações do avatar e escolha
# --------------------------------------------------------------------------------------------


def presenter_reactions(presenter: Presenter | None) -> list[MemeItem]:
    """As poses do avatar como reações: tags = emoções e apelidos; licença `own` (é o próprio avatar do canal)."""
    if presenter is None or not presenter.enabled:
        return []
    out = []
    for pose in presenter.poses:
        tags = [*pose.emotions, *pose.aliases, pose.id]
        out.append(MemeItem(id=f"pose-{pose.id}", file=pose.render_file or pose.file, media="png", tags=tags,
                            license="own", risk="low", max_ms=1500, origin="presenter",
                            note="pose do avatar do canal"))
    return out


def list_items(presenter: Presenter | None = None) -> list[MemeItem]:
    return [*user_items(), *presenter_reactions(presenter)]


def pick(tag: str, seed: int, exclude: list[str] | tuple[str, ...] | set[str] = (), *,
         presenter: Presenter | None = None) -> MemeItem | None:
    """Um meme ou reação com a tag, determinístico pela semente, fora dos `exclude` (usados nos últimos vídeos do
    canal) e sem risco alto. Os da biblioteca vêm antes das poses do avatar. None: nenhum (o planejador usa um
    template de "meme sem meme")."""
    key = slug(tag)
    if not key:
        return None
    skip = set(exclude)
    rnd = random.Random(f"{seed}:{key}")
    for pool in (user_items(), presenter_reactions(presenter)):
        options = [i for i in pool if key in i.tags and i.risk != "high" and i.id not in skip]
        if options:
            return rnd.choice(sorted(options, key=lambda i: i.id))
    return None
