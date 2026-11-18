"""Biblioteca de vídeos de referência: envio do arquivo, link (yt-dlp opcional), análise em planos e remoção.

Arquivos em `library/references/`: `<id>.<ext>` (vídeo) e `<id>-planos-<n>.jpg` (folhas de contato).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import IO, Any

from studio.config import get_settings
from studio.core.media_title import clean_media_title
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
    name = " ".join((title or clean_media_title(Path(filename).stem)).split())[:300] or "Referência"
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
            if title and ref["title"] in ("", ref["source_url"]):
                fields["title"] = clean_media_title(title)[:300]
            ref = _update(ref_id, **fields)
        result = rv.analyze(data_dir / ref["path"], folder, ref_id,
                            progress=lambda f, m: say(0.1 + 0.9 * f, m))
    except Exception as exc:
        _update(ref_id, status="falhou", error=str(exc)[:1000] or exc.__class__.__name__)
        raise
    return _update(ref_id, info=result["info"], shots=result["shots"],
                   sheets=[_rel(p) for p in result["sheets"]], stats=result["stats"], status="pronto", error=None)


def delete_reference(ref_id: str) -> bool:
    """Apaga a referência e os arquivos dela (vídeo e folhas, só dentro de `library/references`)."""
    ref = get_reference(ref_id)
    if ref is None:
        return False
    data_dir = get_settings().data_dir.resolve()
    root = rv.references_dir().resolve()
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

