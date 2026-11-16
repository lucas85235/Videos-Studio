from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta

from studio.core.graph import gc
from studio.core.graph.store import ArtifactStore
from studio.core.project import service
from studio.core.timeline import chunk_timeline
from studio.db import session_scope
from studio.media.render import renderer_version
from studio.models import Artifact, Job, Publication
from studio.workers.jobs import get_job, get_runner

OLD = datetime.now(UTC) - timedelta(days=3)


def _artifact(settings, kind: str, h: str, *, status: str = "done", when: datetime = OLD, with_file: bool = True,
              used: datetime | None = None) -> None:
    folder = settings.cache_dir / kind / h[:2] / h
    rel = f"cache/{kind}/{h[:2]}/{h}/file.bin"
    if with_file:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "file.bin").write_bytes(b"x" * 1000)
    with session_scope() as s:
        s.add(Artifact(kind=kind, input_hash=h, status=status, output={"path": rel, "files": [rel]},
                       created_at=when, updated_at=when, last_used_at=used))


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


def _days(n: float) -> datetime:
    return datetime.now(UTC) - timedelta(days=n)


def test_old_versions_removes_unused_voice_and_images_only_when_asked(isolated_settings):
    settings = isolated_settings
    doc = service.create_project(title="Versões", style_id="misterios-short", topic="teste")
    service.update_settings(doc.id, {"last_final": {"artifacts": ["tts:atual1", "image:atual1"], "langs": {}}})
    with session_scope() as s:  # publicação feita com um render anterior: o que ela usou fica
        s.add(Publication(id="pub_1", project_id=doc.id, platform="youtube", language="pt-BR", driver="manual_helper",
                          details={"artifacts": ["tts:public", "image:public"]}))
    _artifact(settings, "tts", "atual1")
    _artifact(settings, "image", "atual1")
    _artifact(settings, "tts", "public")
    _artifact(settings, "image", "public")
    _artifact(settings, "tts", "velha1")                         # versão antiga: sai com a opção
    _artifact(settings, "align", "velha1")                       # alinhamento da narração antiga: sai
    _artifact(settings, "image", "velha1")                       # imagem antiga: sai
    _artifact(settings, "tts", "usada1", used=datetime.now(UTC))  # reaproveitada agora há pouco: fica
    _artifact(settings, "music", "musica")                       # trilha não entra nesta opção

    normal = gc.collect(dry_run=True)
    assert "old_voice" not in normal.removed and "old_image" not in normal.removed

    preview = gc.collect(dry_run=True, old_versions=True)
    assert preview.removed == {"old_voice": 2, "old_image": 1}
    assert preview.freed["old_voice"] == 2000 and preview.to_dict()["freed_total"] == 3000
    assert _exists("tts", "velha1")  # prévia não apaga

    gc.collect(dry_run=False, old_versions=True)
    for kind, h in (("tts", "velha1"), ("align", "velha1"), ("image", "velha1")):
        assert not _exists(kind, h), (kind, h)
        assert not (settings.cache_dir / kind / h[:2] / h).exists()
    for kind, h in (("tts", "atual1"), ("image", "atual1"), ("tts", "public"), ("image", "public"),
                    ("tts", "usada1"), ("music", "musica")):
        assert _exists(kind, h), (kind, h)


def test_legacy_build_without_manifest_keeps_voice_and_images(isolated_settings):
    settings = isolated_settings
    doc = service.create_project(title="Projeto antigo", style_id="misterios-short", topic="teste")
    service.update_settings(doc.id, {"last_preview": {"langs": {}}})  # build de antes do manifesto
    _artifact(settings, "tts", "velha2")
    _artifact(settings, "image", "velha2")
    _artifact(settings, "music", "velha2")
    report = gc.collect(dry_run=False, old_versions=True, limit_bytes=0)
    assert report.legacy_projects == ["Projeto antigo"]
    assert _exists("tts", "velha2") and _exists("image", "velha2")
    assert not _exists("music", "velha2")  # o barato de refazer ainda sai pelo limite
    assert report.to_dict()["over_limit"] is True


def test_space_limit_evicts_least_recently_used_cheap_first(isolated_settings):
    settings = isolated_settings
    doc = service.create_project(title="Limite", style_id="misterios-short", topic="teste")
    service.update_settings(doc.id, {"last_final": {"artifacts": ["tts:emuso", "chunk:emuso"], "langs": {}}})
    _artifact(settings, "tts", "emuso", used=_days(90))      # protegido, por mais velho que seja
    _artifact(settings, "chunk", "emuso", used=_days(90))
    _artifact(settings, "tts", "tts020", used=_days(20))     # caro: só depois dos baratos
    _artifact(settings, "music", "mus010", used=_days(10))
    _artifact(settings, "media", "med005", used=_days(5))
    _artifact(settings, "thumbnail", "thu003", used=_days(3))
    _artifact(settings, "image", "img040", used=_days(40))
    _artifact(settings, "music", "nova01", used=datetime.now(UTC))  # usada agora: fica
    assert sum(gc.cache_sizes().values()) == 8000

    two = gc.collect(dry_run=True, limit_bytes=6000)
    assert two.removed == {"lru": 2} and two.total_after == 6000

    done = gc.collect(dry_run=False, limit_bytes=6000)
    assert done.removed == {"lru": 2}
    assert not _exists("music", "mus010") and not _exists("media", "med005")  # os dois baratos mais velhos
    assert _exists("thumbnail", "thu003") and _exists("tts", "tts020")
    assert sum(gc.cache_sizes().values()) == 6000

    tight = gc.collect(dry_run=False, limit_bytes=3000)
    # o barato que sobrou primeiro, depois os caros do mais velho para o mais novo
    assert tight.removed == {"lru": 3}
    assert not _exists("thumbnail", "thu003") and not _exists("image", "img040") and not _exists("tts", "tts020")
    for kind, h in (("tts", "emuso"), ("chunk", "emuso"), ("music", "nova01")):
        assert _exists(kind, h), (kind, h)
    assert tight.total_after == 3000 and not tight.to_dict()["over_limit"]


def test_lookup_records_last_use_without_touching_updated_at(isolated_settings):
    settings = isolated_settings
    _artifact(settings, "tts", "uso001")
    store = ArtifactStore()
    assert store.lookup("tts", "uso001") is not None
    with session_scope() as s:
        row = s.query(Artifact).filter_by(kind="tts", input_hash="uso001").one()
        assert row.last_used_at is not None and datetime.now(UTC) - row.last_used_at < timedelta(minutes=1)
        assert abs((row.updated_at - OLD).total_seconds()) < 1
    # usada agora: o limite não leva
    assert "lru" not in gc.collect(dry_run=True, limit_bytes=0).removed


def test_auto_clean_follows_policy_and_waits_for_jobs(isolated_settings):
    settings = isolated_settings
    _artifact(settings, "music", "auto01")
    _artifact(settings, "tts", "auto02")
    assert gc.get_policy() == gc.CachePolicy()
    assert gc.auto_clean() is None  # desligada
    gc.set_policy(gc.CachePolicy(limit_gb=1, auto=True))
    assert gc.auto_clean() is None and _exists("music", "auto01")  # abaixo do limite
    gc.set_policy(gc.CachePolicy(limit_gb=1e-9, auto=True))
    with session_scope() as s:
        s.add(Job(id="job_fila", kind="preview", status="queued"))
    assert gc.auto_clean() is None and _exists("music", "auto01")  # tarefa na fila: espera
    report = gc.auto_clean(exclude_job="job_fila")  # o próprio build que terminou não conta
    assert report is not None and report.removed == {"lru": 2}
    gc.set_policy(gc.CachePolicy(limit_gb=2.5, old_versions=True, auto=True))
    assert gc.get_policy().to_dict() == {"limit_gb": 2.5, "old_versions": True, "auto": True}


def test_cache_routes_preview_and_policy(isolated_settings):
    from fastapi.testclient import TestClient

    from studio.api.app import create_app

    settings = isolated_settings
    _artifact(settings, "tts", "rota01")
    client = TestClient(create_app())
    plain = client.get("/api/maintenance/cache").json()
    assert plain["freed_total"] == 0 and plain["policy"]["auto"] is False
    with_old = client.get("/api/maintenance/cache", params={"old_versions": "true"}).json()
    assert with_old["removed"] == {"old_voice": 1} and with_old["labels"]["old_voice"]
    assert client.put("/api/maintenance/cache/policy", json={"limit_gb": 0, "auto": True}).status_code == 422
    saved = client.put("/api/maintenance/cache/policy", json={"limit_gb": 20, "old_versions": True, "auto": True})
    assert saved.json() == {"limit_gb": 20.0, "old_versions": True, "auto": True}
    assert client.get("/api/maintenance/cache/policy").json()["limit_gb"] == 20.0
    done = client.post("/api/maintenance/cache/clean", json={"old_versions": True}).json()
    assert done["dry_run"] is False and done["removed"] == {"old_voice": 1}
    assert not _exists("tts", "rota01")
    assert client.post("/api/maintenance/cache/clean").status_code == 200  # sem corpo continua valendo


def test_cli_clean_with_old_versions_and_auto(isolated_settings, capsys):
    from studio.cli import main

    settings = isolated_settings
    _artifact(settings, "tts", "cli001")
    main(["clean", "--dry-run", "--versoes-antigas"])
    out = capsys.readouterr().out
    assert "narrações de versões antigas: 1 itens" in out and "seriam liberados" in out
    assert _exists("tts", "cli001")
    main(["clean", "--auto"])
    assert "desligada" in capsys.readouterr().out
    gc.set_policy(gc.CachePolicy(limit_gb=1e-9, old_versions=True, auto=True))
    main(["clean", "--auto"])
    assert "liberados" in capsys.readouterr().out
    assert not _exists("tts", "cli001")
