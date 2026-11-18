"""Biblioteca de efeitos sonoros com licença registrada (ADR 0004 §3.8).

Duas fontes, com o mesmo manifesto (`SfxItem`: `{id, file, kind, tags, license, source_url, author, hit_s, dur, lufs,
tonal}`):

- **embutida** (`assets/sfx/vulto-basico/manifest.json`, no git): só efeitos procedurais (`file` vazio, gerados por
  `media/audio.generate_sfx`) ou CC0 com origem e autor. Os ids são os próprios tipos (`pop`, `boom`...);
- **do usuário** (`<dados>/library/sfx/manifest.json` e os arquivos ao lado): o que ele envia, importa do canais
  (`workers/canais_import/sfx.py`), baixa do Freesound (só CC0) ou gera com a voz do canal ("ué", "pera"). Nada disso
  entra no repositório.

Na importação o arquivo é medido (`analyze`): duração, ponto forte, loudness, apito (a guarda do padrão do estúdio) e a
**trava tonal** (`tonal_metrics`: picos espectrais estreitos que se sustentam, e a planura). Som tonal (com nota, que
briga com a trilha) só entra no automático com `grammar.sfx_tonal_ok`; licença "desconhecida", apito e os tipos banidos
(`audio_check.SFX_BANNED`) nunca entram no automático e geram aviso (`SfxItem.warning`).

`resolve(lib_id | tipo) -> Path` dá o arquivo pronto para a mixagem (48 kHz mono, loudness na referência da voz), na
ordem: id da biblioteca → efeito procedural do tipo → item automático da biblioteca com esse tipo → o gerador
procedural (que faz whoosh para tipo desconhecido, como antes). O tipo que tem procedural fica com ele: importar um
"impact" não troca o som das transições de projetos antigos; o da biblioteca entra pelo `lib_id` do evento.
"""

from __future__ import annotations

import json
import random
import re
import shutil
import threading
import unicodedata
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import BaseModel, Field, field_validator

from studio.config import REPO_ROOT, get_settings
from studio.core.hashing import hash_file, hash_obj
from studio.media import audio as audio_mod
from studio.media import audio_check
from studio.media.ffmpeg import SAMPLE_RATE, write_wav

SR = SAMPLE_RATE
BUILTIN_DIR = REPO_ROOT / "assets" / "sfx" / "vulto-basico"
MANIFEST = "manifest.json"
AUDIO_EXTS = frozenset({".wav", ".mp3", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac"})

License = Literal["own", "cc0", "mixkit-free", "cc_by", "desconhecida"]
LICENSES: tuple[str, ...] = ("own", "cc0", "mixkit-free", "cc_by", "desconhecida")
UNKNOWN_LICENSE = "desconhecida"
AUTO_LICENSES = frozenset({"own", "cc0", "mixkit-free", "cc_by"})
"""Licenças que deixam o efeito entrar no automático (`cc_by` exige autor; o crédito vai na descrição do vídeo)."""
LICENSE_LABELS = {
    "own": "próprio (gerado pelo Vulto ou pelo usuário)",
    "cc0": "CC0 (domínio público, sem crédito)",
    "mixkit-free": "Mixkit Free License (YouTube monetizado ok, sem crédito; não redistribuir o arquivo)",
    "cc_by": "CC BY (exige crédito ao autor)",
    "desconhecida": "desconhecida (fica fora do automático)",
}

TONAL_SHARE = 0.25
"""Fração da energia (150 Hz–12 kHz) em picos estreitos sustentados a partir da qual o som é tonal. Calibrada nos
efeitos do canais: trombone 0,92, bleep 0,94, ding 0,27 e cheer 0,30 (tonais); rimshot 0,22, scratch 0, impacto 0."""
TONAL_BAND = (150.0, 12000.0)
TONAL_PROM_DB = 15.0
PREP_VERSION = 1
"""Versão do arquivo preparado para a mixagem (`prepared_path`)."""
MAX_SFX_S = 6.0
"""Teto de duração de um efeito na mixagem (o resto sai em fade)."""
LEAD_SILENCE_DB = -50.0
"""Silêncio do começo do arquivo cortado na preparação (abaixo disso, relativo ao pico)."""
EXEMPT_TONAL_KINDS = frozenset({"voice"})
"""Tipos fora da trava tonal: a voz tem altura, mas não é nota sustentada."""

_ID_RE = r"^[a-z0-9][a-z0-9_\-]{0,63}$"
_lock = threading.RLock()


class SfxLibraryError(ValueError):
    """Pedido inválido para a biblioteca (licença, arquivo, id): mensagem em português."""


class SfxItem(BaseModel):
    """Um efeito da biblioteca. `file` relativo à pasta da fonte; vazio = procedural (`audio.generate_sfx(kind)`)."""

    id: str = Field(pattern=_ID_RE)
    file: str | None = None
    kind: str = Field(min_length=1, max_length=40)
    """Tipo do efeito (pop, boom, scratch, cheer...): é por ele que o planejador e a timeline pedem."""
    tags: list[str] = []
    license: License = UNKNOWN_LICENSE
    source_url: str = ""
    author: str = ""
    hit_s: float = Field(default=0.0, ge=0)
    """Ponto forte, em s desde o início do arquivo preparado (a palavra do evento cai nele)."""
    dur: float = Field(default=0.0, ge=0)
    lufs: float | None = None
    """Loudness momentânea máxima do original (antes da normalização)."""
    tonal: bool = False
    tonal_share: float = 0.0
    flatness: float = 0.0
    apito: bool = False
    origin: Literal["builtin", "user"] = "user"
    imported_from: str = ""
    """De onde veio ("canais:plantao-vice", "freesound:123", "upload", "voz:pm_alex")."""
    note: str = ""

    @field_validator("tags")
    @classmethod
    def _tags(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(t for t in (slug(v) for v in value) if t))

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        key = slug(value)
        if not key:
            raise ValueError("tipo vazio")
        return key

    @property
    def procedural(self) -> bool:
        return not self.file

    @property
    def banned(self) -> bool:
        return self.kind in audio_check.SFX_BANNED or self.id in audio_check.SFX_BANNED

    def auto(self, tonal_ok: bool = True) -> bool:
        """Pode entrar no automático (planejador e escolha por tipo)."""
        if self.license not in AUTO_LICENSES or self.apito or self.banned:
            return False
        if self.license == "cc_by" and not self.author:
            return False
        return tonal_ok or not self.tonal or self.kind in EXEMPT_TONAL_KINDS

    def warning(self) -> str | None:
        """Por que o efeito fica fora do automático (None: entra)."""
        if self.license == UNKNOWN_LICENSE:
            return f"'{self.id}': licença desconhecida, fica fora do automático (declare a licença para usar)."
        if self.license == "cc_by" and not self.author:
            return f"'{self.id}': CC BY sem autor, fica fora do automático (o crédito é obrigatório)."
        if self.banned:
            return f"'{self.id}': tipo banido no padrão do estúdio (apito), fica fora do automático."
        if self.apito:
            return f"'{self.id}': tem apito, fica fora do automático e da mixagem."
        return None

    def path(self, root: Path | None = None) -> Path | None:
        """Arquivo original (None: procedural)."""
        if not self.file:
            return None
        base = root or (BUILTIN_DIR if self.origin == "builtin" else user_root())
        return base / self.file

    def public(self, tonal_ok: bool = True) -> dict[str, Any]:
        return {**self.model_dump(mode="json"), "procedural": self.procedural, "auto": self.auto(tonal_ok),
                "warning": self.warning(), "license_label": LICENSE_LABELS.get(self.license, self.license)}


class SfxManifest(BaseModel):
    version: int = 1
    items: list[SfxItem] = []


# --------------------------------------------------------------------------------------------
# Nomes
# --------------------------------------------------------------------------------------------


def slug(text: str | None) -> str:
    """Forma de id/tag: sem acento, minúsculas, `[a-z0-9_-]`."""
    folded = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9_\-]+", "-", folded.replace(" ", "-")).strip("-_")[:64]


# --------------------------------------------------------------------------------------------
# Manifestos
# --------------------------------------------------------------------------------------------


def user_root() -> Path:
    root = get_settings().library_dir / "sfx"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _read_manifest(path: Path, origin: Literal["builtin", "user"]) -> list[SfxItem]:
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    items = []
    for entry in raw.get("items", []):
        items.append(SfxItem.model_validate({**entry, "origin": origin}))
    return items


_builtin_cache: tuple[float, list[SfxItem]] | None = None


def builtin_items() -> list[SfxItem]:
    """Os efeitos embutidos (procedurais e CC0), lidos de `assets/sfx/vulto-basico/manifest.json`."""
    global _builtin_cache
    path = BUILTIN_DIR / MANIFEST
    mtime = path.stat().st_mtime if path.is_file() else 0.0
    if _builtin_cache is None or _builtin_cache[0] != mtime:
        _builtin_cache = (mtime, _read_manifest(path, "builtin"))
    return list(_builtin_cache[1])


def user_items(root: Path | None = None) -> list[SfxItem]:
    return _read_manifest((root or user_root()) / MANIFEST, "user")


def _save_user(items: list[SfxItem], root: Path | None = None) -> None:
    root = root or user_root()
    data = SfxManifest(items=sorted(items, key=lambda i: i.id))
    payload = data.model_dump(mode="json", exclude={"items": {"__all__": {"origin"}}})
    tmp = root / f".{MANIFEST}.tmp"
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(root / MANIFEST)


def all_items() -> list[SfxItem]:
    """Embutidos e do usuário (o id do usuário nunca repete um embutido: ver `_free_id`)."""
    return [*builtin_items(), *user_items()]


def get(lib_id: str | None) -> SfxItem | None:
    if not lib_id:
        return None
    for item in all_items():
        if item.id == lib_id:
            return item
    return None


def warnings(items: list[SfxItem] | None = None) -> list[str]:
    return [w for w in (i.warning() for i in (items if items is not None else all_items())) if w]


# --------------------------------------------------------------------------------------------
# Medidas: trava tonal, ponto forte, loudness
# --------------------------------------------------------------------------------------------


def tonal_metrics(x: np.ndarray, sr: int = SR) -> dict[str, Any]:
    """Trava tonal (numpy): fração da energia entre 150 Hz e 12 kHz que está em picos espectrais estreitos (15 dB
    acima da mediana de ±300 Hz) e que se sustentam de um quadro para o outro, ponderada pela energia de cada quadro;
    mais a planura espectral mediana (média geométrica ÷ aritmética: ~0 para seno, ~0,5 para ruído branco).

    `tonal` = fração ≥ `TONAL_SHARE`. Varredura rápida (whip, riser de ruído) não sustenta o pico e não conta."""
    x = np.asarray(x, dtype=np.float64)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = 4096
    if x.size < n:
        x = np.pad(x, (0, n - x.size))
    freqs = np.fft.rfftfreq(n, 1 / sr)
    band = np.where((freqs >= TONAL_BAND[0]) & (freqs <= TONAL_BAND[1]))[0]
    peak = float(np.max(np.abs(x))) + 1e-12
    half = max(1, int(round(300 / (sr / n))))
    window = np.hanning(n)
    num = den = 0.0
    flats: list[float] = []
    prev = np.zeros(band.size, dtype=bool)
    for i in range(0, x.size - n + 1, n // 4):
        seg = x[i:i + n]
        if np.sqrt(np.mean(seg ** 2)) < peak * 10 ** (-40 / 20):
            prev[:] = False
            continue
        power = (np.abs(np.fft.rfft(seg * window)) ** 2)[band] + 1e-20
        db = 10 * np.log10(power)
        med = np.median(np.lib.stride_tricks.sliding_window_view(np.pad(db, half, mode="edge"), 2 * half + 1), axis=1)
        is_max = np.r_[False, (db[1:-1] > db[:-2]) & (db[1:-1] >= db[2:]), False]
        cur = is_max & (db - med >= TONAL_PROM_DB)
        sustained = cur & (prev | np.r_[prev[1:], False] | np.r_[False, prev[:-1]])
        prev = cur
        mask = np.zeros(band.size, dtype=bool)
        for k in np.flatnonzero(sustained):
            mask[max(0, k - 2):k + 3] = True
        num += float(power[mask].sum())
        den += float(power.sum())
        flats.append(float(np.exp(np.mean(np.log(power))) / np.mean(power)))
    share = num / den if den > 0 else 0.0
    return {"tonal_share": round(share, 3), "flatness": round(float(np.median(flats)) if flats else 0.0, 3),
            "tonal": bool(share >= TONAL_SHARE)}


def hit_point(x: np.ndarray, sr: int = SR) -> float:
    """Ponto forte: início da janela de 10 ms com mais energia."""
    win = max(1, int(0.01 * sr))
    if x.size <= win:
        return 0.0
    usable = x[: (x.size // win) * win].astype(np.float64).reshape(-1, win)
    return round(float(np.argmax((usable ** 2).mean(axis=1)) * win / sr), 3)


def analyze(x: np.ndarray, sr: int = SR) -> dict[str, Any]:
    """Medidas gravadas no manifesto: dur, hit_s, lufs (momentânea máxima), tonal_share, flatness, tonal e apito."""
    x = np.asarray(x, dtype=np.float32)
    lufs = audio_mod.peak_momentary_lufs(x) if sr == SR else float("nan")
    return {
        "dur": round(x.size / sr, 3),
        "hit_s": hit_point(x, sr),
        "lufs": round(lufs, 1) if np.isfinite(lufs) else None,
        **tonal_metrics(x, sr),
        "apito": bool(audio_check.tone_report(x, sr)["apito"]),
    }


def decode(path: Path) -> np.ndarray:
    """Qualquer áudio → float32 mono a 48 kHz."""
    return audio_check.decode(path, SR, 1)


def prepare(x: np.ndarray) -> np.ndarray:
    """Pronto para a mixagem: sem o silêncio do começo, até `MAX_SFX_S` (com fade) e com a loudness na referência
    da voz (`audio.sfx.volume_db` vale para a biblioteca como para os procedurais)."""
    x = np.asarray(x, dtype=np.float32)
    if x.size == 0:
        return x
    peak = float(np.abs(x).max())
    if peak > 0:
        loud = np.flatnonzero(np.abs(x) >= peak * 10 ** (LEAD_SILENCE_DB / 20))
        x = x[max(0, int(loud[0]) - int(0.002 * SR)):] if loud.size else x
    cap = int(MAX_SFX_S * SR)
    if x.size > cap:
        x = x[:cap].copy()
        fade = int(0.25 * SR)
        x[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
    return audio_mod.normalize_sfx(x, audio_mod.VOICE_LUFS)


# --------------------------------------------------------------------------------------------
# Importação, upload e remoção
# --------------------------------------------------------------------------------------------


def check_license(license: str, author: str = "") -> str:
    value = (license or "").strip().lower()
    if value not in LICENSES:
        raise SfxLibraryError(f"licença inválida: {license!r} (use {', '.join(LICENSES)})")
    if value == "cc_by" and not author.strip():
        raise SfxLibraryError("CC BY exige o nome do autor (o crédito é obrigatório)")
    return value


def _free_id(wanted: str, taken: set[str]) -> str:
    """Id livre: nunca um tipo procedural nem um id embutido (o `resolve` acharia o outro)."""
    base = slug(wanted) or "sfx"
    reserved = taken | set(audio_mod.SFX_KINDS) | {i.id for i in builtin_items()}
    if base not in reserved:
        return base
    n = 2
    while f"{base}-{n}" in reserved:
        n += 1
    return f"{base}-{n}"


def import_file(
    src: Path,
    *,
    kind: str,
    license: str = UNKNOWN_LICENSE,
    tags: list[str] | None = None,
    source_url: str = "",
    author: str = "",
    lib_id: str | None = None,
    imported_from: str = "",
    note: str = "",
    replace: bool = False,
    copy: bool = True,
) -> SfxItem:
    """Copia `src` para a biblioteca do usuário, mede e grava no manifesto. `lib_id` existente: erro, ou troca o item
    com `replace`. `copy=False`: o arquivo já está na pasta da biblioteca (arquivo solto achado pelo `scan`)."""
    src = Path(src)
    if src.suffix.lower() not in AUDIO_EXTS:
        raise SfxLibraryError(f"formato não aceito: {src.suffix or 'sem extensão'} (use wav, mp3, ogg, flac ou m4a)")
    if not src.is_file():
        raise SfxLibraryError(f"arquivo não encontrado: {src.name}")
    license = check_license(license, author)
    root = user_root()
    with _lock:
        items = user_items(root)
        by_id = {i.id: i for i in items}
        if lib_id and lib_id in by_id and replace:
            new_id = lib_id
        elif lib_id and lib_id in by_id:
            raise SfxLibraryError(f"já existe um efeito com o id '{lib_id}'")
        else:
            new_id = _free_id(lib_id or src.stem, set(by_id))
        try:
            x = decode(src)
        except Exception as exc:  # noqa: BLE001 - arquivo corrompido vira erro de pedido
            raise SfxLibraryError(f"não consegui ler o áudio de {src.name}: {exc}") from exc
        if x.size < int(0.01 * SR) or float(np.abs(x).max()) <= 1e-4:
            raise SfxLibraryError(f"{src.name} está vazio ou mudo")
        measures = analyze(x)
        prepared = prepare(x)
        measures["hit_s"] = hit_point(prepared)
        measures["dur"] = round(prepared.size / SR, 3)
        if copy:
            rel = f"{new_id}{src.suffix.lower()}"
            dest = root / rel
            old = by_id.get(new_id)
            if old is not None and old.file and old.file != rel:
                (root / old.file).unlink(missing_ok=True)
            if src.resolve() != dest.resolve():
                shutil.copyfile(src, dest)
        else:
            rel = src.resolve().relative_to(root.resolve()).as_posix()
        item = SfxItem(id=new_id, file=rel, kind=kind, tags=tags or [], license=license, source_url=source_url,
                       author=author, imported_from=imported_from, note=note, **measures)
        by_id[new_id] = item
        _save_user(list(by_id.values()), root)
        return item


def update(lib_id: str, **fields: Any) -> SfxItem:
    """Muda licença, tipo, tags, autor ou origem de um efeito do usuário (os embutidos não mudam)."""
    allowed = {"license", "kind", "tags", "author", "source_url", "note"}
    with _lock:
        items = user_items()
        by_id = {i.id: i for i in items}
        item = by_id.get(lib_id)
        if item is None:
            raise KeyError(lib_id)
        changes = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if "license" in changes:
            changes["license"] = check_license(changes["license"], changes.get("author", item.author))
        new = SfxItem.model_validate({**item.model_dump(), **changes})
        by_id[lib_id] = new
        _save_user(list(by_id.values()))
        return new


def delete(lib_id: str) -> bool:
    """Tira o efeito do usuário (e o arquivo) da biblioteca. Embutido: erro."""
    if any(i.id == lib_id for i in builtin_items()):
        raise SfxLibraryError("os efeitos embutidos não podem ser apagados")
    with _lock:
        root = user_root()
        items = user_items(root)
        keep = [i for i in items if i.id != lib_id]
        if len(keep) == len(items):
            return False
        gone = next(i for i in items if i.id == lib_id)
        if gone.file and not any(i.file == gone.file for i in keep):
            (root / gone.file).unlink(missing_ok=True)
        _save_user(keep, root)
        return True


def scan() -> list[SfxItem]:
    """Arquivos de áudio soltos na pasta da biblioteca (copiados à mão) entram no manifesto com licença desconhecida
    e o nome do arquivo como tipo. Devolve os novos."""
    root = user_root()
    known = {i.file for i in user_items(root)}
    found: list[SfxItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS or path.name.startswith("."):
            continue
        rel = path.relative_to(root).as_posix()
        if rel in known:
            continue
        try:
            found.append(import_file(path, kind=slug(path.stem) or "sfx", copy=False, imported_from="pasta",
                                     note="arquivo solto na pasta da biblioteca"))
        except SfxLibraryError:
            continue
    return found


# --------------------------------------------------------------------------------------------
# Escolha e resolução
# --------------------------------------------------------------------------------------------


def candidates(kind: str, *, tonal_ok: bool = True, include_builtin: bool = True) -> list[SfxItem]:
    """Itens automáticos de um tipo (embutidos primeiro, depois os do usuário, por id)."""
    key = slug(kind)
    pool = all_items() if include_builtin else user_items()
    return [i for i in pool if i.kind == key and i.auto(tonal_ok)]


def pick(kind: str, seed: int, exclude: list[str] | tuple[str, ...] | set[str] = (), *, tonal_ok: bool = True) -> SfxItem | None:
    """Um efeito automático do tipo, determinístico pela semente, fora dos `exclude` (usados nos últimos vídeos).
    Se todos foram usados, a escolha ignora `exclude` (o tipo vale mais que a novidade). None: nenhum automático."""
    options = candidates(kind, tonal_ok=tonal_ok)
    if not options:
        return None
    fresh = [i for i in options if i.id not in set(exclude)] or options
    return random.Random(f"{seed}:{slug(kind)}").choice(fresh)


def hit_s(ref: str | None, *, tonal_ok: bool = True, explicit: bool = False) -> float:
    """Ponto forte do efeito que `resolve` tocaria para `ref` (`lib_id` ou tipo)."""
    if not ref:
        return 0.0
    item = lookup(ref, tonal_ok=tonal_ok, explicit=explicit)
    if item is not None:
        return float(item.hit_s)
    return audio_mod.sfx_hit_s(ref)


def event_ref(event: dict[str, Any]) -> tuple[str, bool]:
    """O que tocar num evento da timeline: (`lib_id`, True) quando ele está na biblioteca; senão (tipo, False)."""
    lib_id = event.get("lib_id")
    if lib_id and get(str(lib_id)) is not None:
        return str(lib_id), True
    return str(event.get("type") or "whoosh"), False


def event_start(event: dict[str, Any], *, tonal_ok: bool = True) -> float:
    """Onde o arquivo começa (s). Evento de antes (`start`, já adiantado pela timeline) fica igual; evento de palavra
    (`at`, contrato do ADR 0004) começa `hit_s` antes, para o ponto forte cair na palavra."""
    if event.get("start") is not None:
        return float(event["start"])
    ref, explicit = event_ref(event)
    lead = hit_s(ref, tonal_ok=tonal_ok, explicit=explicit)
    return round(max(0.0, float(event.get("at") or 0.0) - lead), 3)


def _store(store: Any = None) -> Any:
    if store is not None:
        return store
    from studio.core.graph.store import ArtifactStore

    return ArtifactStore()


def procedural_path(kind: str, store: Any = None) -> Path:
    """Arquivo do efeito procedural no cache (a mesma chave do `Builder.sfx_file` e da conferência de áudio)."""
    store = _store(store)
    h = hash_obj({"sfx": kind, "v": audio_mod.SFX_VERSION})
    cached = store.lookup("sfx", h)
    if cached:
        return store.abs(cached["path"])
    path = audio_mod.generate_sfx(kind, store.dir_for("sfx", h) / f"{kind}.wav")
    store.save("sfx", h, {"path": store.rel(path), "files": [store.rel(path)]}, "procedural")
    return path


def content_key(item: SfxItem) -> str:
    """Identidade do som de um item (entra na chave do mix): o conteúdo do arquivo, ou o tipo e a versão."""
    path = item.path()
    if path is None:
        return hash_obj({"sfx": item.kind, "v": audio_mod.SFX_VERSION})
    if not path.is_file():
        raise SfxLibraryError(f"o arquivo do efeito '{item.id}' sumiu da biblioteca: {item.file}")
    return hash_file(path)


def prepared_path(item: SfxItem, store: Any = None) -> Path:
    """Arquivo do item pronto para a mixagem (48 kHz mono, normalizado), no cache."""
    if item.procedural:
        return procedural_path(item.kind, store)
    store = _store(store)
    h = hash_obj({"sfx_lib": item.id, "content": content_key(item), "v": PREP_VERSION})
    cached = store.lookup("sfx_lib", h)
    if cached:
        return store.abs(cached["path"])
    src = item.path()
    assert src is not None
    out = write_wav(store.dir_for("sfx_lib", h) / f"{item.id}.wav", prepare(decode(src)))
    store.save("sfx_lib", h, {"path": store.rel(out), "files": [store.rel(out)]}, "library")
    return out


def lookup(ref: str, *, tonal_ok: bool = True, explicit: bool = False) -> SfxItem | None:
    """O item que `resolve` tocaria para `ref` (None: o gerador procedural, que faz whoosh para tipo desconhecido).

    Um id do usuário vale quando foi pedido pelo id (`explicit`, o `lib_id` do evento) ou quando o item entra no
    automático: um evento só com o tipo "tapa" não toca o item "tapa" de licença desconhecida."""
    item = get(ref)
    if item is not None and (explicit or item.origin == "builtin" or item.auto(tonal_ok)):
        return item
    if ref in audio_mod.SFX_KINDS:
        return None
    options = candidates(ref, tonal_ok=tonal_ok)
    return options[0] if options else None


def resolve(ref: str, *, store: Any = None, tonal_ok: bool = True, explicit: bool = False) -> Path:
    """Arquivo pronto para a mixagem de `ref` (`lib_id` ou tipo). Ordem: id da biblioteca (embutido, automático ou
    pedido pelo id com `explicit`) → procedural do tipo → item automático da biblioteca com esse tipo (o primeiro por
    id) → gerador procedural (whoosh para tipo desconhecido, como antes)."""
    item = lookup(ref, tonal_ok=tonal_ok, explicit=explicit)
    if item is not None:
        return prepared_path(item, store)
    return procedural_path(ref, store)


# --------------------------------------------------------------------------------------------
# Freesound (só CC0, com a chave do usuário)
# --------------------------------------------------------------------------------------------

FREESOUND_PROVIDER = "freesound"
FREESOUND_ENV = ("VS_FREESOUND_API_KEY", "FREESOUND_API_KEY")
FREESOUND_API = "https://freesound.org/apiv2"
_FREESOUND_ID_RE = re.compile(
    r"^(?:(?:https?://)?(?:www\.)?freesound\.org/(?:people/[^/]+/)?(?:sounds|s)/)?(\d{1,10})/?$")
CC0_MARK = "publicdomain/zero"


def freesound_id(value: str | int) -> int:
    """Id do som a partir do número ou do link (`https://freesound.org/people/x/sounds/123/`)."""
    match = _FREESOUND_ID_RE.match(str(value).strip())
    if not match:
        raise SfxLibraryError("link ou id do Freesound inválido")
    return int(match.group(1))


def fetch_freesound(sound: str | int, *, kind: str, tags: list[str] | None = None, client: Any = None,
                    api_key: str | None = None) -> SfxItem:
    """Baixa a prévia HQ de um som do Freesound **CC0** e importa (licença `cc0`, autor e link no manifesto).

    Usa a chave de API do usuário (cofre `freesound`/`api_key` ou `VS_FREESOUND_API_KEY`). O original exige OAuth; a
    prévia (mp3 de ~128 kbps) basta para efeito curto. Outra licença (CC BY, BY-NC...) é recusada."""
    import tempfile

    import httpx

    if get_settings().offline:
        raise SfxLibraryError("modo offline: o Freesound precisa de internet")
    if api_key is None:
        from studio.providers import credentials

        api_key = credentials.resolve(FREESOUND_PROVIDER, "api_key", FREESOUND_ENV)
    if not api_key:
        raise SfxLibraryError("configure a chave de API do Freesound em Configurações")
    sound_id = freesound_id(sound)
    own_client = client is None
    http = client or httpx.Client(timeout=httpx.Timeout(20.0, connect=10.0), follow_redirects=True,
                                  headers={"User-Agent": "Vulto/0.1 (ferramenta pessoal de video)"})
    try:
        resp = http.get(f"{FREESOUND_API}/sounds/{sound_id}/",
                        params={"token": api_key, "fields": "id,name,username,license,previews,url"})
        if resp.status_code == 404:
            raise SfxLibraryError(f"som {sound_id} não existe no Freesound")
        if resp.status_code in (401, 403):
            raise SfxLibraryError("o Freesound recusou a chave de API")
        if resp.status_code != 200:
            raise SfxLibraryError(f"o Freesound respondeu {resp.status_code}")
        info = resp.json()
        license_url = str(info.get("license") or "")
        if CC0_MARK not in license_url:
            raise SfxLibraryError(f"o som {sound_id} não é CC0 ({license_url or 'sem licença'}): só CC0 entra")
        preview = (info.get("previews") or {}).get("preview-hq-mp3")
        if not preview or not str(preview).startswith("https://"):
            raise SfxLibraryError("o Freesound não deu a prévia do som")
        audio = http.get(preview)
        if audio.status_code != 200 or not audio.content:
            raise SfxLibraryError("não consegui baixar a prévia do Freesound")
        with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
            path = Path(tmp) / f"{slug(str(info.get('name') or sound_id)) or sound_id}.mp3"
            path.write_bytes(audio.content)
            return import_file(path, kind=kind, license="cc0", tags=tags or [], author=str(info.get("username") or ""),
                               source_url=str(info.get("url") or f"https://freesound.org/s/{sound_id}/"),
                               lib_id=f"fs-{sound_id}", replace=True, imported_from=f"freesound:{sound_id}")
    finally:
        if own_client:
            http.close()


# --------------------------------------------------------------------------------------------
# SFX de voz ("ué", "pera"...), gerados pela voz do canal
# --------------------------------------------------------------------------------------------

VOICE_SFX: dict[str, str] = {"ue": "Ué?", "pera": "Pera...", "oxe": "Oxe!", "ha": "Hã?"}
"""Interjeições de reação (id → texto falado)."""


def _voice_provider() -> Any:
    """Kokoro local quando o modelo já está baixado; senão (ou offline sem o modelo) a voz de teste. Nunca baixa."""
    from studio.providers import kokoro, registry

    if kokoro.model_ready():
        provider = registry.get("kokoro_local")
        if provider.availability()[0]:
            return provider
    return registry.get("mock_tts")


def ensure_voice_sfx(voice: str | None = None, lang: str = "pt-BR", *, provider: Any = None,
                     words: dict[str, str] | None = None) -> list[SfxItem]:
    """Gera (uma vez, em cache na biblioteca) as interjeições com a voz `voice` do canal e registra cada uma com
    licença `own` e tipo `voice`. Devolve os itens (os já existentes não são refeitos)."""
    import tempfile

    provider = provider or _voice_provider()
    voice_key = slug(voice or "padrao") or "padrao"
    out: list[SfxItem] = []
    for key, text in (words or VOICE_SFX).items():
        lib_id = f"voz-{slug(key)}-{voice_key}"[:64]
        existing = get(lib_id)
        if existing is not None and existing.path() is not None and existing.path().is_file():  # type: ignore[union-attr]
            out.append(existing)
            continue
        with tempfile.TemporaryDirectory(dir=get_settings().tmp_dir) as tmp:
            result = provider.synthesize(text=text, lang=lang, voice=voice, style_prompt=None, speed=1.0,
                                         out_dir=Path(tmp))
            item = import_file(Path(result.path), kind="voice", license="own", tags=["voz", key, "reacao"],
                               author=f"voz do canal ({provider.info.id})", lib_id=lib_id, replace=True,
                               imported_from=f"voz:{voice or 'padrao'}", note=f"\"{text}\" na voz do canal")
        out.append(item)
    return out


# --------------------------------------------------------------------------------------------
# Manifesto embutido
# --------------------------------------------------------------------------------------------

BUILTIN_TAGS: dict[str, list[str]] = {
    "whoosh": ["transicao", "passagem"],
    "impact": ["impacto", "cartao", "reveal"],
    "glitch": ["transicao", "digital", "erro"],
    "static": ["transicao", "chiado", "tv"],
    "riser": ["tensao", "subida", "antes-do-corte"],
    "pop": ["texto", "aparece", "leve"],
    "tick": ["relogio", "contagem", "lista"],
    "click": ["mouse", "interface", "print"],
    "thump": ["batida", "peso", "enfase"],
    "boom": ["meme", "reveal", "piada", "vine-boom"],
    "scratch": ["meme", "pera", "interrupcao", "disco"],
    "shutter": ["foto", "print", "captura"],
    "swish": ["passagem", "rapido", "subplano"],
    "bass_drop": ["reveal", "queda", "grave"],
    "riser_short": ["tensao", "antes-do-corte", "curto"],
}


def procedural_entry(kind: str, work: Path) -> dict[str, Any]:
    """Entrada do manifesto embutido de um procedural, medida no arquivo gerado (o teste confere o JSON com isto)."""
    from studio.media.ffmpeg import read_wav

    x, _rate = read_wav(audio_mod.generate_sfx(kind, work / f"{kind}.wav"))
    m = analyze(x)
    return {"id": kind, "file": None, "kind": kind, "tags": BUILTIN_TAGS.get(kind, []), "license": "own",
            "source_url": "", "author": "Vulto (procedural)", "hit_s": audio_mod.sfx_hit_s(kind), "dur": m["dur"],
            "lufs": m["lufs"], "tonal": m["tonal"], "tonal_share": m["tonal_share"], "flatness": m["flatness"],
            "apito": m["apito"], "imported_from": "", "note": ""}


def write_builtin_manifest(work: Path) -> Path:
    """Regrava `assets/sfx/vulto-basico/manifest.json` a partir dos procedurais (rodar quando um som mudar)."""
    entries = [procedural_entry(k, work) for k in audio_mod.SFX_KINDS]
    BUILTIN_DIR.mkdir(parents=True, exist_ok=True)
    path = BUILTIN_DIR / MANIFEST
    payload = {"version": 1, "sfx_version": audio_mod.SFX_VERSION,
               "note": "Só efeitos procedurais (código do Vulto) ou CC0 com origem e autor. Nada de terceiros.",
               "items": entries}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def mix_key(ref: str, *, tonal_ok: bool = True, explicit: bool = False) -> str | None:
    """Parte da chave do mix para `ref`: o conteúdo do item da biblioteca (None para o procedural, cuja chave já é
    o tipo e a versão)."""
    item = lookup(ref, tonal_ok=tonal_ok, explicit=explicit)
    if item is None or item.procedural:
        return None
    return content_key(item)
