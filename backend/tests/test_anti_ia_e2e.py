"""Ponta a ponta da "cara de IA" (ADR 0004 §3.11), offline e sem rede.

Roteiro mock → coleta do acervo (fixture: fotos próprias, clipes `lavfi`, captura com trechos e um link oficial já
baixado) → storyboard mock real-primeiro com anexo automático → planejador de edição → timeline (narração estimada) →
impressão digital. As asserções são as do ADR: IA ≤ 25% do tempo, CV dos planos ≥ 0,45, SFX/min entre 2 e 10, gancho
real e nenhum trecho parado acima de 6 s. Depois o "Reeditar" (calmo e agitado) e a trava de um evento pela rota.
"""

from __future__ import annotations

import shutil
import socket

import pytest
from fastapi.testclient import TestClient
from test_media_pool import ACERVO, LINK, _seed_capture, _seed_link, lavfi_clip

from studio.api.app import create_app
from studio.config import get_settings
from studio.core.editing import metrics
from studio.core.project import service
from studio.core.project.pool import load_pool
from studio.core.styles.library import get_style
from studio.workers.finalcheck.profile_check import estimate_timeline
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import media_pool

STYLE = "curiosidades-longo"
TOPIC = "o Farol da Ilha Rasa, que voltou a acender depois de 30 anos apagado"


@pytest.fixture
def no_network(monkeypatch):
    """Qualquer conexão para fora da máquina falha o teste (o laço do asyncio no Windows conecta em 127.0.0.1)."""
    real_connect = socket.socket.connect

    def connect(sock, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else str(address)
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise AssertionError(f"o teste de ponta a ponta tocou a rede ({host})")
        return real_connect(sock, address, *args, **kwargs)

    def resolve(host, *args, **kwargs):
        raise AssertionError(f"o teste de ponta a ponta tocou a rede ({host})")

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _run(kind: str, project_id: str) -> dict:
    job = get_runner().submit(kind, {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def _seed_acervo(project_id: str) -> None:
    folder = get_settings().uploads_dir / project_id / media_pool.OWN_DIR
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ACERVO / "farol.jpg", folder / "farol.jpg")
    shutil.copyfile(ACERVO / "ilha.jpg", folder / "ilha.jpg")
    lavfi_clip(folder / "mergulho.mp4", first="testsrc", second="smptebars")
    lavfi_clip(folder / "costa.mp4", cut_at=1.5, total=4.5, first="rgbtestsrc", second="color=c=0x40a060")
    _seed_capture(project_id)
    _seed_link(project_id)


@pytest.fixture
def project(isolated_settings, no_network):
    doc = service.create_project(title="O farol", style_id=STYLE, topic=TOPIC)
    _seed_acervo(doc.id)
    _run("script", doc.id)
    # a coleta completa (job `media_pool`, a que roda ao enviar arquivos ou dar links) corta os clipes; a do storyboard
    # é a rápida e só usa o que já foi cortado
    _run("media_pool", doc.id)
    _run("storyboard", doc.id)
    return service.get_doc(doc.id)


def _fingerprint(doc):
    style = get_style(doc.style_id, doc.style_overrides)
    timeline = estimate_timeline(doc, style)
    return metrics.fingerprint_from_timeline(timeline, doc), style


def test_mock_script_to_fingerprint_has_no_ai_face(project):
    doc = project
    pool = load_pool(doc.id)
    assert {"own", "capture", "official"} <= {i.tier for i in pool.items}
    assert any(s.visual.media is not None and s.visual.media.pool_id for s in doc.scenes)  # anexado sem clique
    assert any(s.shots for s in doc.scenes) and any(s.fx for s in doc.scenes)  # o planejador rodou

    fp, style = _fingerprint(doc)
    flat = metrics.flat_metrics(fp)
    assert flat["ai_screen_share"] <= 0.25, flat
    assert fp.pace.cv >= 0.45, fp.pace
    assert 2.0 <= fp.audio.sfx_per_min <= 10.0, fp.audio
    assert fp.hook_prov is not None and fp.hook_prov != "ai", fp.hook_prov
    assert fp.pace.max_still_s <= 6.0, fp.pace
    index = metrics.ai_look_index(fp, style)
    assert index.score <= 55, index  # fora do vermelho


def test_reedit_intensity_and_locked_event_through_the_routes(project):
    doc = project
    client = TestClient(create_app())
    calm = client.post(f"/api/projects/{doc.id}/reedit", json={"intensity": 0}).json()
    busy = client.post(f"/api/projects/{doc.id}/reedit", json={"intensity": 3}).json()
    assert calm["summary"]["fx_total"] < busy["summary"]["fx_total"]

    track = client.get(f"/api/projects/{doc.id}/edit-track").json()
    assert track["intensity"] == 3 and track["scenes"]
    scene = next(s for s in track["scenes"] if s["fx"] and len(s["words"]) > 2)
    words = [w["at"] for w in scene["words"]]
    events = [{k: e[k] for k in ("type", "at", "dur_ms", "params", "origin", "locked")} for e in scene["fx"]]
    target = words[-2] if events[0]["at"] != words[-2] else words[1]
    events[0] = {**events[0], "at": target, "locked": True, "origin": "user"}
    patched = client.patch(f"/api/projects/{doc.id}/scenes/{scene['scene_id']}/fx", json={"fx": events})
    assert patched.status_code == 200 and patched.json()["changed"]

    again = client.post(f"/api/projects/{doc.id}/reedit", json={"intensity": 1}).json()
    assert again["intensity"] == 1
    kept = service.get_doc(doc.id).scene(scene["scene_id"]).fx
    assert any(e.locked and e.at == target and e.type == events[0]["type"] for e in kept)  # o travado ficou

    # o índice da prévia segue medindo depois da reedição
    body = client.get(f"/api/projects/{doc.id}/edit-metrics", params={"source": "estimate"}).json()
    assert body["source"] == "estimate" and 0 <= body["index"]["score"] <= 100


def test_link_without_download_is_only_a_warning(isolated_settings, no_network):
    doc = service.create_project(title="Sem baixar", style_id=STYLE, topic=TOPIC)
    service.update_settings(doc.id, {media_pool.LINKS_KEY: [{"url": LINK + "x", "label": "", "tier": "official"}]})
    _run("script", doc.id)
    result = _run("storyboard", doc.id)
    assert any("link" in w for w in result.get("warnings", []))
    assert service.get_doc(doc.id).scenes  # o storyboard sai mesmo assim
