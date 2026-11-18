"""Nome legível da referência e do estilo criado a partir dela (ADR 0004, item card-nome).

O nome sai do arquivo do downloader sem site, id, contador e qualidade; a referência pode ser renomeada (PATCH); o
comando `fix-reference-names` corrige os dados antigos sem trocar id nenhum.
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.cli_references import fix_reference_names
from studio.core.media_title import clean_media_title
from studio.core.styles import from_reference as fr
from studio.core.styles.schema import StyleSpec
from studio.db import session_scope
from studio.media import reference_video as rv
from studio.models import ReferenceVideo, Style
from studio.workers.visual import references

YTDOWN = "YTDown.com_YouTube_Media_-hT4bLj6Jdg_VOCÊ-VAI-ZERAR-GTA-6-PELO-YOUTUBE_001_1080p"
CLEAN = "VOCÊ VAI ZERAR GTA 6 PELO YOUTUBE"

MEASURED = {
    "info": {"w": 1920, "h": 1080, "fps": 30.0, "dur": 120.0},
    "shots": [{"k": 1, "a": 0.0, "b": 2.0, "dur": 2.0}, {"k": 2, "a": 2.0, "b": 5.0, "dur": 3.0}],
    "stats": {"shots": 2, "median_s": 2.5, "cuts_per_min": 24.0,
              "palette": ["#101820", "#203040", "#c0a080", "#f0e0d0", "#405060"]},
}


@pytest.fixture
def client():
    with TestClient(create_app()) as test_client:
        yield test_client


# --- clean_media_title ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("raw", "expected"), [
    (YTDOWN, CLEAN),
    ("y2mate.com - Rick Astley Never Gonna Give You Up_1080p", "Rick Astley Never Gonna Give You Up"),
    ("Título [dQw4w9WgXcQ]", "Título"),
    ("Top-10 jogos 2024", "Top-10 jogos 2024"),
    ("gameplay_1080p60.mp4", "gameplay"),
])
def test_clean_media_title_report_cases(raw, expected):
    assert clean_media_title(raw, from_filename=True) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("Como o GTA 6 vai mudar tudo", "Como o GTA 6 vai mudar tudo"),  # título já legível passa igual
    ("Minha análise [Documentary]", "Minha análise [Documentary]"),  # palavra entre colchetes não é id
    ("Mortal_Kombat_1.mp4", "Mortal Kombat 1"),  # número do título fica
    ("James_Bond_007", "James Bond 007"),  # "_007" sem outro marcador de downloader é do título
    ("Aula_012.mp4", "Aula 012"),
    ("clip_001_1080p", "clip"),  # contador junto da qualidade é do downloader
    ("Spider-Man.mp4", "Spider-Man"),  # um hífen só é da palavra
    ("COVID-19", "COVID-19"),
    ("1080p", "1080p"),  # a limpeza não come o nome inteiro
    ("", ""),
])
def test_clean_media_title_keeps_real_titles(raw, expected):
    assert clean_media_title(raw, from_filename=True) == expected


@pytest.mark.parametrize(("raw", "expected"), [
    ("Título [dQw4w9WgXcQ]", "Título"),
    ("Trailer oficial (1080p)", "Trailer oficial"),
    ("Spider-Man", "Spider-Man"),  # título do yt-dlp: hífen e sublinhado ficam
    ("Agente_007", "Agente_007"),
    ("Meu nome_001", "Meu nome_001"),
    ("VOCÊ-VAI-ZERAR", "VOCÊ-VAI-ZERAR"),
])
def test_clean_media_title_light_mode_for_real_titles(raw, expected):
    assert clean_media_title(raw) == expected


@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=hT4bLj6-Jdg",
    "https://www.youtube.com/watch?v=hT4bL-6J_dg",
    "http://youtu.be/ab_cd-ef_gh",
])
def test_clean_media_title_never_touches_urls(url):
    assert clean_media_title(url) == url
    assert clean_media_title(url, from_filename=True) == url


# --- envio e link -----------------------------------------------------------------------------------------------------
def test_upload_without_name_uses_clean_file_name():
    ref = references.save_upload(io.BytesIO(b"video"), f"{YTDOWN}.mp4")
    assert ref["title"] == CLEAN
    # Nome digitado pelo usuário vale como está.
    typed = references.save_upload(io.BytesIO(b"video"), f"{YTDOWN}.mp4", "Meu nome_001")
    assert typed["title"] == "Meu nome_001"


def test_title_from_ytdlp_is_cleaned(monkeypatch, tmp_path):
    monkeypatch.setattr(rv, "ytdlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(rv.safe_http, "check_url", lambda url: url)
    ref = references.create_from_link("https://youtu.be/dQw4w9WgXcQ")

    def download(url, folder, ref_id):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{ref_id}.mp4"
        path.write_bytes(b"mp4")
        return path, "Como o GTA 6 vai mudar tudo [dQw4w9WgXcQ]"

    monkeypatch.setattr(rv, "download", download)
    monkeypatch.setattr(rv, "analyze", lambda *a, **k: {"info": {}, "shots": [], "sheets": [], "stats": {}})
    done = references.analyze_reference(ref["id"])
    assert done["title"] == "Como o GTA 6 vai mudar tudo"


def test_rename_during_download_wins_over_ytdlp_title(monkeypatch):
    monkeypatch.setattr(rv, "ytdlp_path", lambda: "yt-dlp")
    monkeypatch.setattr(rv.safe_http, "check_url", lambda url: url)
    ref = references.create_from_link("https://youtu.be/dQw4w9WgXcQ")

    def download(url, folder, ref_id):
        references.rename_reference(ref_id, "Nome que eu dei")  # o usuário renomeia enquanto baixa
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{ref_id}.mp4"
        path.write_bytes(b"mp4")
        return path, "Título do yt-dlp"

    monkeypatch.setattr(rv, "download", download)
    monkeypatch.setattr(rv, "analyze", lambda *a, **k: {"info": {}, "shots": [], "sheets": [], "stats": {}})
    assert references.analyze_reference(ref["id"])["title"] == "Nome que eu dei"


# --- estilo a partir da referência ------------------------------------------------------------------------------------
def test_style_name_from_old_reference_is_clean_and_short():
    out = fr.build_style({**MEASURED, "title": YTDOWN}, use_llm=False)
    style = out["style"]
    assert "YTDown" not in style["name"] and len(style["name"]) <= 120
    assert style["name"] == f"{CLEAN} (referência)"
    assert "YTDown" not in style["description"] and f"“{CLEAN}”" in style["description"]
    assert style["id"].startswith("ref-voce-vai-zerar-gta-6")


def test_long_title_keeps_the_suffix_within_120():
    out = fr.build_style({**MEASURED, "title": "Palavra " * 40}, use_llm=False)
    name = out["style"]["name"]
    assert len(name) <= 120 and name.endswith(" (referência)")


def test_style_route_without_name_uses_clean_title(client):
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_suja", title=YTDOWN, status="pronto", **MEASURED))
    ref = client.get("/api/references/ref_suja").json()
    assert ref["suggested_style_name"] == f"{CLEAN} (referência)"
    r = client.post("/api/references/ref_suja/style", json={"use_llm": False})
    assert r.status_code == 200, r.text
    assert r.json()["style"]["name"] == f"{CLEAN} (referência)"
    # Nome digitado vale como está.
    r = client.post("/api/references/ref_suja/style", json={"use_llm": False, "name": "Meu nome_001"})
    assert r.status_code == 200, r.text
    assert r.json()["style"]["name"] == "Meu nome_001"


# --- PATCH /api/references/{id} ---------------------------------------------------------------------------------------
def test_rename_reference_route(client):
    ref = references.save_upload(io.BytesIO(b"video"), "trailer.mp4")
    r = client.patch(f"/api/references/{ref['id']}", json={"title": "  Trailer   do GTA  "})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "Trailer do GTA"
    assert client.get(f"/api/references/{ref['id']}").json()["title"] == "Trailer do GTA"
    assert client.patch(f"/api/references/{ref['id']}", json={"title": ""}).status_code == 422
    assert client.patch(f"/api/references/{ref['id']}", json={"title": "   "}).status_code == 422
    assert client.patch("/api/references/ref_nao_existe", json={"title": "x"}).status_code == 404
    assert references.get_reference(ref["id"])["title"] == "Trailer do GTA"


def test_rename_reference_errors():
    with pytest.raises(LookupError):
        references.rename_reference("ref_nao_existe", "x")
    ref = references.save_upload(io.BytesIO(b"video"), "trailer.mp4")
    with pytest.raises(ValueError):
        references.rename_reference(ref["id"], " ")


# --- fix-reference-names ----------------------------------------------------------------------------------------------
OLD_STYLE_ID = "ref-ytdown-com-youtube-media-ht4blj6jdg-voce-vai-zerar"
LINK = "https://www.youtube.com/watch?v=hT4bL-6J_dg"


def _seed_old_data() -> None:
    """Como o app gravava antes: título da referência e nome do estilo com o nome do arquivo."""
    data = fr.build_style({**MEASURED, "title": "x"}, use_llm=False, new_id=OLD_STYLE_ID)["style"]
    data["name"] = f"{YTDOWN} (referência)"
    data["description"] = f"Criado a partir da referência “{YTDOWN}” sobre o preset X: plano mediano de 2.5 s"
    edited = {**data, "id": "ref-editado", "name": f"{YTDOWN} (referência)"}
    with session_scope() as s:
        s.add(ReferenceVideo(id="ref_velha", title=YTDOWN, status="pronto", **MEASURED))
        s.add(ReferenceVideo(id="ref_limpa", title="Já está limpo", status="novo"))
        s.add(ReferenceVideo(id="ref_link", title=LINK, source_url=LINK, status="falhou"))  # link ainda não baixado
        s.add(ReferenceVideo(id="ref_digitada", title="Meu nome_001", status="novo"))
        s.add(ReferenceVideo(id="ref_hifen", title="Spider-Man", status="novo"))
        s.add(Style(id=OLD_STYLE_ID, name=data["name"], data=data, version=1, parent_id="curiosidades-longo"))
        s.add(Style(id="ref-editado", name=edited["name"], data=edited, version=2))  # salvo pelo usuário
        typed = {**data, "id": "ref-digitado", "name": "Meu nome_001 (referência)"}
        s.add(Style(id="ref-digitado", name=typed["name"], data=typed, version=1))  # nome digitado no campo novo


def _snapshot() -> dict:
    with session_scope() as s:
        return {
            "refs": {r.id: r.title for r in s.query(ReferenceVideo).all()},
            "styles": {r.id: (r.name, r.version, dict(r.data)) for r in s.query(Style).all()},
        }


def test_fix_reference_names_dry_run_writes_nothing():
    _seed_old_data()
    before = _snapshot()
    report = fix_reference_names(dry_run=True)
    assert {(f.kind, f.id) for f in report.fixes} == {("referencia", "ref_velha"), ("estilo", OLD_STYLE_ID)}
    assert _snapshot() == before


def test_fix_reference_names_fixes_titles_and_keeps_ids():
    _seed_old_data()
    fix_reference_names()
    after = _snapshot()
    assert after["refs"] == {"ref_velha": CLEAN, "ref_limpa": "Já está limpo", "ref_link": LINK,
                             "ref_digitada": "Meu nome_001", "ref_hifen": "Spider-Man"}
    name, version, data = after["styles"][OLD_STYLE_ID]
    assert name == data["name"] == f"{CLEAN} (referência)"
    assert data["id"] == OLD_STYLE_ID and version == 1
    assert data["description"].startswith(f"Criado a partir da referência “{CLEAN}”")
    StyleSpec.model_validate(data)
    # Estilo já salvo pelo usuário (versão 2) fica como está.
    assert after["styles"]["ref-editado"][0] == f"{YTDOWN} (referência)"
    # Nome digitado no campo (versão 1, termina em " (referência)") também fica.
    assert after["styles"]["ref-digitado"][0] == "Meu nome_001 (referência)"
    # Rodar de novo não acha mais nada.
    assert fix_reference_names().fixes == []


def test_fix_reference_names_cli_dry_run(capsys):
    from studio.cli import main

    _seed_old_data()
    before = _snapshot()
    main(["fix-reference-names", "--dry-run"])
    out = capsys.readouterr().out
    assert CLEAN in out and "--dry-run" in out
    assert _snapshot() == before
