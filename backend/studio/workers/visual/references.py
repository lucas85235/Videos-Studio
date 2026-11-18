"""Biblioteca de vídeos de referência: envio do arquivo, link (yt-dlp opcional), análise em planos e remoção.

Arquivos em `library/references/`: `<id>.<ext>` (vídeo) e `<id>-planos-<n>.jpg` (folhas de contato).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from studio.config import get_settings
from studio.core.media_title import clean_media_title
from studio.core.styles.from_reference import default_style_name, reference_title
from studio.db import session_scope
from studio.ids import new_id
from studio.media import reference_video as rv
from studio.models import ReferenceVideo

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
UPLOAD_MAX_BYTES = 4_000_000_000


def reference_dict(row: ReferenceVideo) -> dict[str, Any]:
    return {
        "id": row.id, "title": row.title, "source_url": row.source_url, "path": row.path, "info": row.info or {},
        "shots": row.shots or [], "sheets": row.sheets or [], "stats": row.stats or {}, "status": row.status,
        "error": row.error, "created_at": row.created_at.isoformat() if row.created_at else None,
        # O nome que o estilo criado dela recebe sem nome digitado (título limpo + " (referência)").
        "suggested_style_name": default_style_name(reference_title({"title": row.title})),
    }


def _rel(path: Path) -> str:
    return path.resolve().relative_to(get_settings().data_dir.resolve()).as_posix()


def list_references() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.query(ReferenceVideo).order_by(ReferenceVideo.created_at.desc()).all()
        return [reference_dict(r) for r in rows]


def get_reference(ref_id: str) -> dict[str, Any] | None:
    with session_scope() as s:
        row = s.get(ReferenceVideo, ref_id)
        return reference_dict(row) if row else None


def save_upload(stream: IO[bytes], filename: str, title: str = "") -> dict[str, Any]:
    """Guarda o arquivo enviado e cria a referência (status "novo")."""
    ext = Path(filename or "").suffix.lower()
    if ext not in VIDEO_EXTS:
        raise ValueError("Envie um vídeo (MP4, MOV, MKV, WEBM).")
    ref_id = new_id("ref")
    folder = rv.references_dir()
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{ref_id}{ext}"
    written = 0
    with target.open("wb") as fh:
        while chunk := stream.read(1 << 20):
            written += len(chunk)
            if written > UPLOAD_MAX_BYTES:
                fh.close()
                target.unlink(missing_ok=True)
                raise ValueError("O arquivo passa de 4 GB.")
            fh.write(chunk)
    if not written:
        target.unlink(missing_ok=True)
        raise ValueError("O arquivo está vazio.")
    # Sem nome digitado, o título sai do nome do arquivo, sem o lixo do downloader (site, id, _001, 1080p).
    name = " ".join((title or clean_media_title(Path(filename).stem, from_filename=True)).split())[:300] or "Referência"
    with session_scope() as s:
        row = ReferenceVideo(id=ref_id, title=name, path=_rel(target), status="novo")
        s.add(row)
        s.flush()
        return reference_dict(row)


def create_from_link(url: str, title: str = "") -> dict[str, Any]:
    """Referência por link (o vídeo baixa no job `reference_shots`)."""
    if not rv.ytdlp_path():
        raise ValueError(rv.YTDLP_MISSING)
    url = rv.safe_http.check_url(url)
    with session_scope() as s:
        row = ReferenceVideo(id=new_id("ref"), title=" ".join((title or url).split())[:300], source_url=url,
                             status="novo")
        s.add(row)
        s.flush()
        return reference_dict(row)


def _update(ref_id: str, **fields: Any) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(ReferenceVideo, ref_id)
        if row is None:
            raise LookupError(ref_id)
        for key, value in fields.items():
            setattr(row, key, value)
        s.flush()
        return reference_dict(row)


def rename_reference(ref_id: str, title: str) -> dict[str, Any]:
    """Troca o nome da referência (só o título; o id e os arquivos ficam). Vazio dá ValueError; id inexistente,
    LookupError."""
    name = " ".join((title or "").split())[:300]
    if not name:
        raise ValueError("O nome não pode ficar vazio.")
    return _update(ref_id, title=name)


def analyze_reference(ref_id: str, progress: Callable[[float, str], None] | None = None) -> dict[str, Any]:
    """Baixa (se veio por link e ainda não baixou), corta em planos e monta as folhas e o ritmo."""
    say = progress or (lambda f, m: None)
    ref = get_reference(ref_id)
    if ref is None:
        raise LookupError(ref_id)
    data_dir = get_settings().data_dir
    folder = rv.references_dir()
    try:
        if not ref["path"] or not (data_dir / ref["path"]).is_file():
            if not ref["source_url"]:
                raise rv.ReferenceVideoError("O arquivo do vídeo sumiu: envie de novo.")
            say(0.02, "Baixando o vídeo (yt-dlp)")
            path, title = rv.download(ref["source_url"], folder, ref_id)
            fields: dict[str, Any] = {"path": _rel(path)}
            # Relê a linha: o usuário pode ter renomeado durante o download, e o nome dele manda.
            current = get_reference(ref_id) or ref
            if title and current["title"] in ("", current["source_url"]):
                fields["title"] = clean_media_title(title)[:300]
            ref = _update(ref_id, **fields)
        result = rv.analyze(data_dir / ref["path"], folder, ref_id,
                            progress=lambda f, m: say(0.1 + 0.9 * f, m))
    except Exception as exc:
        _update(ref_id, status="falhou", error=str(exc)[:1000] or exc.__class__.__name__)
        raise
    stats = dict(result["stats"])
    previous = (get_reference(ref_id) or ref)["stats"]
    if profile_matches_file(previous, data_dir / ref["path"]):  # o perfil do MESMO arquivo continua valendo
        for key in ("profile", "profile_notes"):
            if key in previous:
                stats[key] = previous[key]
    return _update(ref_id, info=result["info"], shots=result["shots"],
                   sheets=[_rel(p) for p in result["sheets"]], stats=stats, status="pronto", error=None)


def get_profile(ref_id: str) -> dict[str, Any] | None:
    """O perfil de edição gravado em `stats["profile"]` (None se ainda não foi medido). LookupError sem referência."""
    ref = get_reference(ref_id)
    if ref is None:
        raise LookupError(ref_id)
    return ref["stats"].get("profile")


def profile_matches_file(stats: dict[str, Any], path: Path) -> bool:
    """O perfil gravado é deste arquivo (a chave do arquivo nas notas bate)? Arquivo baixado de novo, ou extrator
    novo, muda a chave: o perfil antigo não vale mais."""
    from studio.media import refprofile

    key = (stats.get("profile_notes") or {}).get("file_key")
    if not stats.get("profile") or not key or not path.is_file():
        return False
    return key == refprofile.file_key(path)


def has_deep_profile(stats: dict[str, Any], path: Path) -> bool:
    """Já há perfil profundo (visão e fala) deste arquivo."""
    return profile_matches_file(stats, path) and not (stats.get("profile") or {}).get("quick", True)


def profile_reference(ref_id: str, *, deep: bool = False, progress: Callable[[float, str], None] | None = None,
                      cancelled: Callable[[], bool] | None = None) -> dict[str, Any]:
    """Mede o perfil de edição da referência (ADR 0004 §3.2) e grava em `stats["profile"]`, sem migração.

    Modo rápido: cortes, movimento e áudio. Modo profundo: mais fala e visão. As miniaturas dos elementos recorrentes
    ficam em `library/references/<id>-rec-<n>.jpg` (apagadas com a referência).

    O modo rápido nunca rebaixa um perfil profundo do mesmo arquivo (a visão e a fala gastaram cota; as medidas da
    base seriam as mesmas, do mesmo cache): nesse caso nada é refeito nem gravado. Cancelado (`cancelled`), levanta
    `refprofile.Cancelled` e não grava nada."""
    from studio.media import refprofile

    say = progress or (lambda f, m: None)
    ref = get_reference(ref_id)
    if ref is None:
        raise LookupError(ref_id)
    data_dir = get_settings().data_dir
    if not ref["path"] or not (data_dir / ref["path"]).is_file():
        raise rv.ReferenceVideoError("O arquivo do vídeo não está na biblioteca: analise a referência antes.")
    path = data_dir / ref["path"]
    if not deep and has_deep_profile(ref["stats"], path):
        say(1.0, "Já existe o perfil profundo deste vídeo")
        return ref
    profile, notes = refprofile.analyze(path, quick=not deep, progress=say, thumbs_dir=rv.references_dir(),
                                        thumb_prefix=ref_id, cancelled=cancelled)
    if cancelled is not None and cancelled():
        raise refprofile.Cancelled()
    profile.refs = [ref_id]
    current = get_reference(ref_id) or ref  # relê: a análise leva minutos e o título pode ter mudado
    if not deep and has_deep_profile(current["stats"], path):  # um job profundo terminou no meio: ele vale
        return current
    stats = {**current["stats"], "profile": profile.model_dump(mode="json"), "profile_notes": notes}
    return _update(ref_id, stats=stats)


def delete_reference(ref_id: str) -> bool:
    """Apaga a referência e os arquivos dela (vídeo e folhas, só dentro de `library/references`) e a pasta de cache
    da análise de perfil (`cache/refprofile/<chave>`: tiras de quadros, transcrição, medidas), que são cópias de
    material de terceiros."""
    import shutil

    from studio.media import refprofile

    ref = get_reference(ref_id)
    if ref is None:
        return False
    data_dir = get_settings().data_dir.resolve()
    root = rv.references_dir().resolve()
    video = (data_dir / ref["path"]).resolve() if ref["path"] else None
    if video is not None and video.is_file():
        cache = refprofile.cache_dir_for(video)  # antes de apagar o vídeo: a chave sai do arquivo
        shutil.rmtree(cache, ignore_errors=True)
    for rel in [ref["path"], *ref["sheets"]]:
        if not rel:
            continue
        path = (data_dir / rel).resolve()
        if path.is_relative_to(root):
            path.unlink(missing_ok=True)
    for extra in root.glob(f"{ref_id}*") if root.is_dir() else []:
        if extra.is_file():
            extra.unlink(missing_ok=True)
    with session_scope() as s:
        row = s.get(ReferenceVideo, ref_id)
        if row is not None:
            s.delete(row)
    return True

