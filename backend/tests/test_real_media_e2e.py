"""Mídia real de ponta a ponta, offline: o storyboard do mock sugere uma foto real, o usuário anexa um candidato da
busca do Commons (resposta gravada, rede falsa), o plano do build passa a gerar uma imagem a menos, a timeline leva o
crédito na tela (na cena e em quem a reaproveita) e a descrição publicada no YouTube leva os créditos.

Nada sai da máquina: `media_search.new_client`/`safe_http.new_client` usam `MockTransport` e o DNS resolve para um IP
público fixo. O roteiro, as cenas, a voz e as imagens de IA saem dos provedores offline (mock).
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import Metadata
from studio.media import safe_http
from studio.providers import media_search
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import storyboard_check
from studio.workers.publish import youtube as yt
from studio.workers.publish.credits import credits_block

FIXTURES = Path(__file__).parent / "fixtures" / "media"
CANDIDATE = "commons:File:Miami skyline from the ocean.jpg"
CREDIT = "Matthew T Rader / Wikimedia Commons · CC BY-SA 4.0"


def _fixture(name: str) -> dict:
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    data.pop("_nota", None)
    return data


def _jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (40, 110, 170)).save(buf, "JPEG")
    return buf.getvalue()


class Net:
    """A internet de mentira: Commons (busca e consulta gravadas), miniatura grande e Openverse fora do ar."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host = request.url.host
        if host == "commons.wikimedia.org":
            name = "commons_lookup.json" if "titles" in request.url.params else "commons_search.json"
            return httpx.Response(200, json=_fixture(name))
        if host == "thumb.wikimedia.org":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_jpeg(3840, 2400))
        if host == "api.openverse.org":
            return httpx.Response(504, text="error code: 504")
        return httpx.Response(404)


@pytest.fixture
def net(monkeypatch):
    monkeypatch.delenv("PEXELS_API_KEY", raising=False)
    fake = Net()
    monkeypatch.setattr(media_search, "new_client", lambda *a, **k: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(safe_http, "new_client", lambda: httpx.Client(transport=httpx.MockTransport(fake)))
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    return fake


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": list, "object": dict,
          "null": type(None)}


def _schema_errors(schema: dict, value: object, path: str = "$") -> list[str]:
    """Validador mínimo do subconjunto estrito usado em `schemas.py` (type, enum, properties, required,
    additionalProperties, items)."""
    errors: list[str] = []
    if "type" in schema:
        names = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(isinstance(value, _TYPES[n]) and not (n in ("integer", "number") and isinstance(value, bool))
                   for n in names):
            return [f"{path}: {type(value).__name__} fora de {names}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} fora do enum")
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        errors += [f"{path}: falta {k}" for k in schema.get("required", []) if k not in value]
        if schema.get("additionalProperties") is False:
            errors += [f"{path}: chave a mais {k}" for k in value if k not in props]
        for key, item in value.items():
            if key in props:
                errors += _schema_errors(props[key], item, f"{path}.{key}")
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors += _schema_errors(schema["items"], item, f"{path}[{i}]")
    return errors


@pytest.mark.parametrize("style_id", ["misterios-short", "top10-listas", "historia-documental"])
def test_mock_storyboard_follows_the_strict_schema(isolated_settings, style_id):
    """O storyboard do mock tem a mesma forma que o Claude devolve (esquema estrito), com as sugestões de foto e de
    cartão de notícia (fonte com link)."""
    from studio.core.project.document import Source
    from studio.core.styles.library import get_style
    from studio.workers.pipeline import authoring, mock_content
    from studio.workers.pipeline.schemas import STORYBOARD_SCHEMA

    doc = service.create_project(title="Farol", style_id=style_id, topic="o farol abandonado da ilha")
    _run("script", doc.id)
    doc = service.get_doc(doc.id).model_copy(update={"sources": [Source(title="Sem link", url=""),
                                                                 Source(title="Jornal", url="https://example.org/n")]})
    style = get_style(style_id)
    beats = authoring.plan_beats(doc, style)
    data = mock_content.mock_storyboard(authoring._storyboard_context(doc, style, beats,
                                                                      authoring.image_budget(len(beats), style)))
    assert _schema_errors(STORYBOARD_SCHEMA, data) == []
    hints = [(s["kind"], s["real_kind"], s["real_source_ref"]) for s in data["scenes"] if s["real_kind"]]
    assert hints == [("image", "photo", None), ("image", "news", 2)]


def _run(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def test_suggestion_to_published_credits(isolated_settings, net, monkeypatch):
    # 1) Roteiro e cenas do mock: a primeira imagem nova depois do gancho sugere uma foto real do tema.
    doc = service.create_project(title="Miami", style_id="misterios-short", topic="Miami Beach skyline")
    pid = doc.id
    _run("script", pid)
    result = _run("storyboard", pid)
    doc = service.get_doc(pid)
    hinted = [s for s in doc.ordered_scenes() if s.real is not None]
    assert [s.real.kind for s in hinted] == ["photo"]  # roteiro do mock sem fontes com link: sem cartão de notícia
    scene = hinted[0]
    assert scene.kind == "image" and scene.real.query == "Miami Beach skyline" and scene.visual.media is None
    assert storyboard_check.has_own_image(scene)
    assert result["real_hints"] == 1
    review = service.get_project_row(pid)["settings"]["storyboard_review"]
    assert review["counts"]["real_hints"] == 1
    reusers = [s.id for s in doc.ordered_scenes() if s.visual.reuse_of == scene.id]

    with TestClient(create_app()) as client:
        before = client.get(f"/api/projects/{pid}/plan").json()
        assert scene.id in before["images_missing"]
        assert "media_scenes" not in before and "media_missing" not in before

        # 2) Busca (só a rede falsa) e anexação do candidato do Commons.
        isolated_settings.offline = False
        base = f"/api/projects/{pid}/scenes/{scene.id}"
        found = client.get(f"{base}/media/candidates")
        assert found.status_code == 200, found.text
        data = found.json()
        assert data["query"] == "Miami Beach skyline" and data["sources"]["commons"] == "ok"
        assert data["sources"]["openverse"] == "erro"  # uma fonte fora do ar não derruba a busca
        assert CANDIDATE in [c["id"] for c in data["candidates"]]
        search = next(r for r in net.requests if r.url.host == "commons.wikimedia.org")
        assert "Miami Beach skyline" in search.url.params["gsrsearch"]
        attached = client.post(f"{base}/media", json={"via": "candidate", "candidate_id": CANDIDATE})
        assert attached.status_code == 200, attached.text
        media = attached.json()["media"]
        assert media["credit"] == CREDIT and media["source"] == "commons" and media["editorial"] is False
        assert (isolated_settings.data_dir / media["asset"]).is_file()
        isolated_settings.offline = True

        # 3) O plano conta uma imagem a menos e lista a cena com mídia; a revisão também.
        after = client.get(f"/api/projects/{pid}/plan").json()
        assert len(after["images_missing"]) == len(before["images_missing"]) - 1
        assert scene.id not in after["images_missing"] and after["media_scenes"] == [scene.id]
        assert "media_missing" not in after
        doc = service.get_doc(pid)
        counts = storyboard_check.review_counts(doc, review["counts"]["budget"])
        assert counts["new_images"] == review["counts"]["new_images"] - 1 and "real_hints" not in counts

        # 4) Prévia: a cena mostra a foto enquadrada (cache "media") com o crédito na tela; quem reaproveita também.
        preview = _run("preview", pid)
        entry = preview["langs"]["pt-BR"]
        timeline = json.loads((isolated_settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
        by_id = {s["id"]: s for s in timeline["scenes"]}
        shown = by_id[scene.id]
        assert shown["credit"] == f"Foto: {CREDIT}" and "news" not in shown
        assert shown["src"] and "/media/" in shown["src"] and shown["src"] != media["asset"]
        assert scene.motion or shown["motion"] in ("ken_burns_in", "ken_burns_out", "drift")  # câmera suave na foto
        for sid in reusers:
            assert by_id[sid]["credit"] == f"Foto: {CREDIT}" and by_id[sid]["src"] == shown["src"]
        others = [s for s in timeline["scenes"] if s["id"] not in {scene.id, *reusers}]
        assert others and all("credit" not in s for s in others)
        assert preview["generated"].get("image", 0) == len(after["images_missing"])

        # 5) Publicação: a descrição enviada ao YouTube leva o bloco de créditos antes das hashtags.
        credits = client.get(f"/api/projects/{pid}/media/credits").json()
        assert credits["lines"] == [
            "Miami skyline from the ocean — Matthew T Rader — CC BY-SA 4.0 "
            "(https://creativecommons.org/licenses/by-sa/4.0) — "
            "https://commons.wikimedia.org/wiki/File:Miami_skyline_from_the_ocean.jpg"
        ]
    doc = service.get_doc(pid)
    doc.metadata["pt-BR"] = Metadata(title="Miami vista do mar", description="Resumo do vídeo.", hashtags=["#miami"])
    service.commit(pid, doc, "Metadados de teste")
    video = isolated_settings.outputs_dir / pid / "pt-BR" / "video.mp4"
    video.parent.mkdir(parents=True, exist_ok=True)
    video.write_bytes(b"mp4")
    service.update_settings(pid, {"last_final": {"langs": {"pt-BR": {"video": f"outputs/{pid}/pt-BR/video.mp4"}},
                                                 "preview_only_providers": []}})
    sent: dict = {}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None):
        sent.update(snippet=snippet)
        return {"id": "vid_e2e"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    published = _run("publish_youtube", pid, {"lang": "pt-BR", "privacy": "private"})
    block = credits_block(service.get_doc(pid), "pt-BR")
    assert block == credits["text"] and block.startswith("Créditos das imagens:\n- Miami skyline from the ocean")
    assert sent["snippet"]["description"] == f"Resumo do vídeo.\n\n{block}\n\n#miami"
    assert published["warnings"] == []  # foto livre: sem aviso de Content ID


def test_news_og_image_has_no_small_image_warning(isolated_settings, monkeypatch):
    """A og:image comum (1200x630) só vira miniatura do cartão e fundo desfocado: sem aviso de imagem pequena. Foto
    do mesmo tamanho continua avisando."""
    from studio.media.real_media import media_from_link, media_from_upload

    page = ('<html><head><meta property="og:type" content="article"><meta property="og:title" content="Farol volta a '
            'acender"><meta property="og:image" content="/capa.jpg"></head></html>')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/capa.jpg":
            return httpx.Response(200, headers={"content-type": "image/jpeg"}, content=_jpeg(1200, 630))
        return httpx.Response(200, headers={"content-type": "text/html"}, text=page)

    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        media, warnings = media_from_link("https://noticias.example.com/farol", "prj_x", kind=None, lang="pt-BR",
                                          http=http)
    assert media.kind == "news" and media.asset and not any("pequena" in w for w in warnings)
    _, news_upload = media_from_upload(_jpeg(1200, 630), "prj_x", kind="news", credit="Jornal")
    assert not any("pequena" in w for w in news_upload)
    _, photo_upload = media_from_upload(_jpeg(1200, 630), "prj_x", kind="photo")
    assert photo_upload == ["A imagem é pequena (630 px) e pode sair borrada."]
