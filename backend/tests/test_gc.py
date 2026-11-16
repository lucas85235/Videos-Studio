from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from studio.core.graph import gc
from studio.core.project import service
from studio.core.timeline import chunk_timeline
from studio.db import session_scope
from studio.media.render import renderer_version
from studio.models import Artifact
from studio.workers.jobs import get_job, get_runner

OLD = datetime.now(UTC) - timedelta(days=3)


def _artifact(settings, kind: str, h: str, *, status: str = "done", when: datetime = OLD, with_file: bool = True) -> None:
    folder = settings.cache_dir / kind / h[:2] / h
    rel = f"cache/{kind}/{h[:2]}/{h}/file.bin"
    if with_file:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "file.bin").write_bytes(b"x" * 1000)
    with session_scope() as s:
        s.add(Artifact(kind=kind, input_hash=h, status=status, output={"path": rel, "files": [rel]},
                       created_at=when, updated_at=when))


def _old_path(path) -> None:
    ts = OLD.timestamp()
    os.utime(path, (ts, ts))


def _exists(kind: str, h: str) -> bool:
    with session_scope() as s:
        return s.query(Artifact).filter_by(kind=kind, input_hash=h).one_or_none() is not None


def test_collect_sweeps_only_unused_renders(isolated_settings):
    settings = isolated_settings
    doc = service.create_project(title="GC", style_id="misterios-short", topic="teste")
    # build com manifesto
    service.update_settings(doc.id, {"last_final": {"artifacts": ["chunk:keep01", "final:fin001"], "langs": {}}})
    # build antigo sem manifesto: blocos recalculados pela timeline salva
    timeline = {"fps": 30, "width": 1080, "height": 1920, "lang": "pt-BR", "style": {},
                "scenes": [{"id": "scn_a", "from": 0, "durationInFrames": 30}]}
    tl_path = settings.outputs_dir / doc.id / "pt-BR" / "timeline.json"
    tl_path.parent.mkdir(parents=True, exist_ok=True)
    tl_path.write_text(json.dumps(timeline), encoding="utf-8")
    legacy_key = chunk_timeline(timeline, renderer_version())[0]["key"]
    other = service.create_project(title="GC2", style_id="misterios-short", topic="teste")
    service.update_settings(other.id, {"last_preview": {"langs": {"pt-BR": {
        "timeline": tl_path.relative_to(settings.data_dir).as_posix(), "mix": "cache/mix/mi/mix001/mix.m4a"}}}})

    _artifact(settings, "chunk", "keep01")
    _artifact(settings, "final", "fin001")
    _artifact(settings, "chunk", legacy_key)
    _artifact(settings, "mix", "mix001")
    _artifact(settings, "chunk", "old001")                                  # sem uso e antigo: sai
    _artifact(settings, "mix", "oldmix")                                    # sem uso e antigo: sai
    _artifact(settings, "chunk", "new001", when=datetime.now(UTC))          # recente: fica
    _artifact(settings, "tts", "tts001")                                    # narração nunca é varrida
    _artifact(settings, "image", "img001")                                  # imagem nunca é varrida
    _artifact(settings, "image", "fail01", status="failed")                 # falha antiga: sai
    _artifact(settings, "image", "miss01", with_file=False)                 # registro sem arquivo: sai
    orphan = settings.cache_dir / "chunk" / "zz" / "zzorphan"
    orphan.mkdir(parents=True)
    (orphan / "chunk.mp4").write_bytes(b"y" * 500)
    _old_path(orphan)
    bundles = settings.data_dir / "renderer-bundle"
    (bundles / "versao-velha").mkdir(parents=True)
    (bundles / "versao-velha" / "index.html").write_text("x", encoding="utf-8")
    _old_path(bundles / "versao-velha")
    (bundles / renderer_version()).mkdir(parents=True)
    _old_path(bundles / renderer_version())
    settings.tmp_dir.mkdir(parents=True, exist_ok=True)
    (settings.tmp_dir / "sobra.wav").write_bytes(b"z" * 100)
    _old_path(settings.tmp_dir / "sobra.wav")

    preview = gc.collect(dry_run=True)
    assert preview.removed == {"chunk": 1, "mix": 1, "failed": 1, "missing": 1, "orphan": 1, "bundles": 1, "tmp": 1}
    assert _exists("chunk", "old001") and orphan.exists()  # simulação não apaga nada

    done = gc.collect(dry_run=False)
    assert done.removed == preview.removed
    assert done.freed["chunk"] == 1000 and done.freed["orphan"] == 500
    for kind, h in (("chunk", "old001"), ("mix", "oldmix"), ("image", "fail01"), ("image", "miss01")):
        assert not _exists(kind, h), (kind, h)
    for kind, h in (("chunk", "keep01"), ("final", "fin001"), ("chunk", legacy_key), ("mix", "mix001"),
                    ("chunk", "new001"), ("tts", "tts001"), ("image", "img001")):
        assert _exists(kind, h), (kind, h)
    assert not orphan.exists() and not (bundles / "versao-velha").exists()
    assert (bundles / renderer_version()).exists()
    assert not (settings.tmp_dir / "sobra.wav").exists()
    assert not (settings.cache_dir / "chunk" / "ol" / "old001").exists()


def test_build_records_manifest(isolated_settings):
    doc = service.create_project(title="Manifesto", style_id="misterios-short", topic="o farol abandonado")
    for kind in ("script", "storyboard", "preview"):
        job = get_runner().submit(kind, {}, doc.id, run_inline=True)
        final = get_job(job["id"])
        assert final and final["status"] == "done", final and final["error"]
    result = final["result"]
    kinds = {item.split(":", 1)[0] for item in result["artifacts"]}
    assert {"tts", "image", "mix", "music"} <= kinds
    report = gc.collect(dry_run=True)
    assert report.kept_in_use == len(result["artifacts"])
    assert "mix" not in report.removed


def test_collect_sweeps_unused_lut_images(isolated_settings):
    """Imagens gradadas por LUT são baratas de refazer: as sem uso saem, as do último build ficam."""
    settings = isolated_settings
    doc = service.create_project(title="LUT", style_id="misterios-short", topic="teste")
    service.update_settings(doc.id, {"last_preview": {"artifacts": ["lut:lutuse"], "langs": {}}})
    _artifact(settings, "lut", "lutuse")
    _artifact(settings, "lut", "lutold")                              # intensidade antiga, sem uso: sai
    _artifact(settings, "lut", "lutnew", when=datetime.now(UTC))      # recente (build em andamento): fica

    report = gc.collect(dry_run=False)
    assert report.removed.get("lut") == 1
    assert "lut" in report.to_dict()["labels"]
    assert not _exists("lut", "lutold") and not (settings.cache_dir / "lut" / "lu" / "lutold").exists()
    assert _exists("lut", "lutuse") and _exists("lut", "lutnew")
