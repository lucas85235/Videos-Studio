"""Bloco B (ADR 0002): Shorts cortados do vídeo longo (projeto derivado)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import (
    CardItem,
    MediaRef,
    Metadata,
    MusicCue,
    ProjectDoc,
    RealHint,
    Scene,
    SceneCard,
    Segment,
    Source,
)
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.models import Channel
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline.build import Builder, plan
from studio.workers.shorts import derive
from studio.workers.shorts.rules import (
    ShortTooLong,
    check_short,
    description_hashtags,
    final_title,
    limits_from,
)
from studio.workers.shorts.suggest import heuristic

LONG_STYLE = "curiosidades-longo"


# ---------------------------------------------------------------------------------------------
# regras (números de canais/motor/passos/shorts.py)
# ---------------------------------------------------------------------------------------------
def test_regras_titulo():
    assert check_short("O que a BBC escondeu", 22, "normal") == ['fonte no título ("BBC"): vai para a descrição']
    assert check_short("O jogo atrasou segundo a imprensa", 22, "normal")[0] == (
        'fonte no título ("segundo a"): vai para a descrição')
    assert check_short("GTA 6 atrasou segundo a BBC", 22) == ['fonte no título ("segundo a"): vai para a descrição']
    assert check_short("URGENTE: o trailer saiu", 22, "normal") == ['gancho gasto no título ("URGENTE")']
    assert check_short("Só 3% sabe disso", 22)[0] == 'gancho gasto no título ("Só 3%")'
    assert check_short("Faltam 10 dias para o lançamento", 22) == ['gancho gasto no título ("Faltam 10 dias")']
    title = "a" * 51
    assert check_short(title, 22) == ["título com 51 caracteres antes das hashtags (máximo 50)"]
    assert check_short("a" * 50 + " #GTA6 #shorts", 22) == []  # hashtags no fim não contam


def test_regras_duracao():
    assert check_short("ok", 31, "normal") == ["31 s fora do alvo de 15 a 30 s"]
    assert check_short("ok", 14, "normal") == ["14 s fora do alvo de 15 a 30 s"]
    assert check_short("ok", 36, "normal") == ["36.0 s passa de 35 s (lista e iceberg: escolha o tipo do Short)"]
    assert check_short("ok", 40, "lista") == []
    assert check_short("ok", 10, "iceberg") == []  # lista e iceberg não têm alvo mínimo
    assert check_short("ok", 46, "lista") == ["46.0 s passa de 45 s"]
    with pytest.raises(ShortTooLong):
        check_short("ok", 181, "lista")
    custom = limits_from({"duracao_alvo": [20, 40], "duracao_max": 50, "titulo_max": 10})
    assert check_short("ok", 38, "normal", custom) == []
    assert check_short("ok", 45, "normal", custom) == ["45 s fora do alvo de 20 a 40 s"]
    assert check_short("abcdefghijk", 30, "normal", custom) == [
        "título com 11 caracteres antes das hashtags (máximo 10)"]
    assert limits_from({"duracao_alvo": "x", "duracao_max": -1}).max_s == 35


def test_titulo_final_e_hashtags():
    assert final_title("O mapa vazou", "#GTA6") == "O mapa vazou #GTA6"
    assert final_title("O mapa vazou #gta6", "#GTA6") == "O mapa vazou #gta6"  # já tem: não duplica
    assert final_title("O mapa vazou #shorts #GTA6", "#GTA6") == "O mapa vazou #shorts #GTA6"
    assert final_title("O mapa vazou ", "") == "O mapa vazou"
    assert description_hashtags(["#GTA6", "shorts", "#jogos", "#shorts"], "#gta6") == ["#shorts", "#jogos"]


# ---------------------------------------------------------------------------------------------
# derivação
# ---------------------------------------------------------------------------------------------
def _run(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None
    assert final["status"] == "done", final["error"]
    return final["result"]


def _channel(hashtag: str = "#Misterio") -> str:
    with session_scope() as s:
        s.add(Channel(id="ch_teste", name="Canal teste", short_hashtag=hashtag))
    return "ch_teste"


def _long_project(*, built: bool = True, channel_id: str | None = None) -> str:
    doc = service.create_project(title="O farol abandonado", style_id=LONG_STYLE, topic="o farol abandonado da ilha",
                                 channel_id=channel_id)
    _run("script", doc.id)
    _run("storyboard", doc.id)
    if built:
        _run("preview", doc.id)
    return doc.id


def _pick_range(overview: dict, target: float = 22.0) -> list[str]:
    """Primeiro trecho contínuo que chega perto do alvo (para o teste não depender do tamanho do roteiro offline)."""
    segs = overview["segments"]
    ids: list[str] = []
    for seg in segs[1:]:
        ids.append(seg["id"])
        if derive.short_duration(overview, ids) >= target:
            break
    return [ids[0], ids[-1]]


def test_derive_short_reuses_narration_and_images(isolated_settings):
    channel_id = _channel()
    parent_id = _long_project(channel_id=channel_id)
    parent = service.get_doc(parent_id)
    parent.metadata["pt-BR"] = Metadata(title="Longo", hashtags=["#Misterio", "#farol"])
    service.commit(parent_id, parent, "Hashtags")

    overview = derive.segments_overview(parent_id)
    assert overview["source"] == "preview"
    assert overview["voice_missing"] == [] and overview["images_redo"] == []
    assert all(s["duration_s"] for s in overview["segments"])
    assert overview["lead_in_s"] == 0.3 and overview["tail_out_s"] == 0.7  # entrada e saída do formato Short
    first, last = _pick_range(overview)

    result = derive.derive_short(parent_id, [[first, last]], "O segredo do farol", hook="Ninguém volta de lá",
                                 tipo="normal")
    child_id = result["project_id"]
    row = service.get_project_row(child_id)
    assert row["parent_id"] == parent_id
    assert row["channel_id"] == channel_id
    assert row["settings"]["gates"] == {"script": False, "storyboard": False, "preview": True, "publish": True}
    short_of = row["settings"]["short_of"]
    assert short_of["project_id"] == parent_id and short_of["ranges"] == [[first, last]]
    assert short_of["duration_s"] == result["duration_s"]

    child = service.get_doc(child_id)
    ids = [s["id"] for s in overview["segments"]]
    expected = ids[ids.index(first) : ids.index(last) + 1]
    assert [s.id for s in child.segments] == expected
    assert all(s.locked for s in child.segments)
    assert [s.text for s in child.segments] == [parent.segment(i).text for i in expected]
    assert child.script_approved and child.storyboard_approved
    style = get_style(child.style_id, child.style_overrides)
    assert style.format.type == "short" and style.format.aspect == "9:16" and style.format.width == 1080
    parent_style = get_style(parent.style_id, parent.style_overrides)
    assert style.voice == parent_style.voice  # mesmas sobreposições de voz
    assert child.languages == ["pt-BR"] and child.primary_language == "pt-BR"
    assert set(child.metadata) == {"pt-BR"}
    # O texto das cenas não muda: gancho e fim vão nos campos que o renderer desenha sobre qualquer cena.
    parent_scenes = {sc.id: sc for sc in parent.scenes}
    assert all(sc.overlay_text == parent_scenes[sc.id].overlay_text for sc in child.scenes)
    assert derive.CROP_WARNING in result["warnings"]
    meta = child.metadata["pt-BR"]
    assert meta.title == "O segredo do farol #Misterio"
    assert "Vídeo completo no canal: “Longo”" in meta.description
    assert meta.hashtags == ["#farol"]

    # O teste-chave: nenhuma narração e nenhuma imagem a gerar.
    for mode in ("final", "preview"):
        p = plan(child_id, mode=mode)
        assert p["tts_missing"] == [], mode
        assert p["images_missing"] == [], mode

    listed = derive.children_of(parent_id)
    assert [c["project_id"] for c in listed] == [child_id]

    # Na timeline montada do Short: faixa do topo com o gancho na 1ª cena e carimbo na última.
    _run("preview", child_id)
    timeline, _ = derive.load_timeline(child_id, "pt-BR", service.get_project_row(child_id)["settings"])
    assert timeline["scenes"][0]["card"]["headline"] == "Ninguém volta de lá"
    assert timeline["scenes"][-1]["card"]["stamp"] == "Vídeo completo"
    assert all("headline" not in (sc.get("card") or {}) for sc in timeline["scenes"][1:])


def test_derive_short_rejects_bad_ranges(isolated_settings):
    parent_id = _long_project()
    overview = derive.segments_overview(parent_id)
    ids = [s["id"] for s in overview["segments"]]
    with pytest.raises(derive.ShortsError) as exc:
        derive.derive_short(parent_id, [[ids[3], ids[1]]], "Título")  # termina antes de começar
    assert exc.value.status == 422
    with pytest.raises(derive.ShortsError) as exc:
        derive.derive_short(parent_id, [[ids[2], ids[3]], [ids[0], ids[1]]], "Título")  # fora de ordem
    assert exc.value.status == 422
    with pytest.raises(derive.ShortsError) as exc:
        derive.derive_short(parent_id, [[ids[1], ids[2]], [ids[2], ids[3]]], "Título")  # repete segmento
    assert exc.value.status == 422
    with pytest.raises(derive.ShortsError) as exc:
        derive.derive_short(parent_id, [["seg_x", ids[1]]], "Título")
    assert exc.value.status == 422
    with pytest.raises(derive.ShortsError) as exc:
        derive.derive_short(parent_id, [[ids[0], ids[1]]], "Título", tipo="outro")
    assert exc.value.status == 422
    assert derive.children_of(parent_id) == []  # nada criado nas recusas


def test_failed_commit_leaves_no_orphan(isolated_settings, monkeypatch):
    parent_id = _long_project()
    ids = [s["id"] for s in derive.segments_overview(parent_id)["segments"]]
    before = {p["id"] for p in service.list_projects()}

    def boom(*_args, **_kwargs):
        raise RuntimeError("disco cheio")

    monkeypatch.setattr(service, "commit", boom)
    with pytest.raises(RuntimeError):
        derive.derive_short(parent_id, [[ids[1], ids[1]]], "Título")
    assert derive.children_of(parent_id) == []
    assert {p["id"] for p in service.list_projects()} == before


def test_two_ranges_in_order(isolated_settings):
    parent_id = _long_project()
    ids = [s["id"] for s in derive.segments_overview(parent_id)["segments"]]
    result = derive.derive_short(parent_id, [[ids[1], ids[1]], [ids[3], ids[3]]], "Dois trechos")
    child = service.get_doc(result["project_id"])
    assert [s.id for s in child.segments] == [ids[1], ids[3]]
    assert plan(result["project_id"])["tts_missing"] == []


def test_single_scene_short_keeps_hook_and_card(isolated_settings):
    doc = service.create_project(title="Uma cena", style_id=LONG_STYLE, topic="o farol abandonado da ilha")
    _run("script", doc.id)
    _run("storyboard", doc.id)
    long_doc = service.get_doc(doc.id)
    seg = long_doc.segments[1].id
    only = long_doc.scenes_of(seg)[0]
    only.part, only.parts, only.span = 0, 1, None
    only.overlay_text = {"pt-BR": "Texto do cartão"}
    long_doc.scenes = [sc for sc in long_doc.scenes if sc.segment_id != seg or sc.id == only.id]
    service.commit(doc.id, long_doc, "Uma cena no segmento")
    _run("preview", doc.id)

    result = derive.derive_short(doc.id, [[seg, seg]], "Só uma cena", hook="GANCHO")
    child = service.get_doc(result["project_id"])
    assert len(child.scenes) == 1
    scene = child.scenes[0]
    assert scene.card is not None
    assert scene.card.headline == {"pt-BR": "GANCHO"} and scene.card.stamp == {"pt-BR": "Vídeo completo"}
    assert scene.overlay_text == {"pt-BR": "Texto do cartão"}  # o conteúdo do cartão do longo continua


def test_final_label_and_local_preview_voice(isolated_settings, monkeypatch):
    # Prévia com a voz local (como fora do modo offline): a narração final ainda não existe.
    monkeypatch.setattr(Builder, "local_preview", lambda self, lang: self.mode == "preview")
    parent_id = _long_project()
    overview = derive.segments_overview(parent_id)
    assert overview["source"] == "preview"
    assert overview["voice_missing"] == [s["id"] for s in overview["segments"]]
    first, last = _pick_range(overview)
    result = derive.derive_short(parent_id, [[first, last]], "O segredo do farol")
    assert derive.VOICE_WARNING in result["warnings"]
    assert service.get_project_row(result["project_id"])["settings"]["short_of"]["warnings"] == result["warnings"]

    # O mesmo timeline.json só é "do vídeo final" quando o mix é o do último render.
    settings = service.get_project_row(parent_id)["settings"]
    entry = settings["last_preview"]["langs"]["pt-BR"]
    other = {"langs": {"pt-BR": {**entry, "mix": "mix/outro/mix.m4a"}}}
    assert derive.load_timeline(parent_id, "pt-BR", {**settings, "last_final": other})[1] == "preview"
    same = {"langs": {"pt-BR": dict(entry)}}
    assert derive.load_timeline(parent_id, "pt-BR", {**settings, "last_final": same})[1] == "final"


def _doc(segments: int = 6, **kw) -> ProjectDoc:
    return ProjectDoc(id="prj_t", title="t", style_id=LONG_STYLE,
                      segments=[Segment(id=f"s{i}", text={"pt-BR": f"Frase {i}."}) for i in range(segments)], **kw)


def test_child_music_per_range():
    doc = _doc(music=[MusicCue(id="m0"), MusicCue(id="m1", start_segment="s2"), MusicCue(id="m2", start_segment="s4")])
    cues = derive._child_music(doc, ["s1", "s3", "s5"])  # três trechos, cada um abre com a deixa em vigor
    assert [(c.id, c.start_segment) for c in cues] == [("m0", None), ("m1", "s3"), ("m2", "s5")]
    cues = derive._child_music(doc, ["s3", "s4"])  # abre com a em vigor; a de dentro continua
    assert [(c.id, c.start_segment) for c in cues] == [("m1", None), ("m2", "s4")]
    cues = derive._child_music(doc, ["s2", "s3"])  # trecho começa na troca: ela abre o Short
    assert [(c.id, c.start_segment) for c in cues] == [("m1", None)]
    cues = derive._child_music(doc, ["s0", "s1"])  # trechos colados com a mesma deixa: uma só
    assert [(c.id, c.start_segment) for c in cues] == [("m0", None)]
    late = _doc(music=[MusicCue(id="m1", start_segment="s2")])  # nenhuma em vigor no começo: a primeira abre
    assert [(c.id, c.start_segment) for c in derive._child_music(late, ["s0", "s1", "s2"])] == [("m1", None)]
    back = _doc(music=[MusicCue(id="m0"), MusicCue(id="m1", start_segment="s2")])
    cues = derive._child_music(back, ["s1", "s3"])
    assert [(c.id, c.start_segment) for c in cues] == [("m0", None), ("m1", "s3")]


def test_short_sources_follow_kept_scenes():
    sources = [Source(title="Guinness World Records — recorde", url="https://g.example/r"),
               Source(title="Jornal da Ilha", url="https://ilha.example/n"),
               Source(title="Arquivo do farol", url="https://farol.example/a")]
    scenes = [
        Scene(id="c1", segment_id="s1", kind="stat",
              card=SceneCard(value={"pt-BR": "10"}, items=[CardItem()], source="Guinness World Records")),
        Scene(id="c2", segment_id="s2", real=RealHint(kind="news", source_ref=2)),
        Scene(id="c3", segment_id="s3", visual={"media": MediaRef(page_url="https://farol.example/a")}),
    ]
    doc = _doc(sources=sources, scenes=scenes)
    assert [s.title for s in derive._short_sources(doc, scenes[:1])] == [sources[0].title]
    assert [s.title for s in derive._short_sources(doc, scenes[1:])] == [sources[1].title, sources[2].title]
    plain = _doc(sources=sources, scenes=[Scene(id="c9", segment_id="s1")])  # sem vínculo: todas
    assert len(derive._short_sources(plain, plain.scenes)) == 3
    description, _ = derive.short_description(doc, "Gancho", "pt-BR", "", derive._short_sources(doc, scenes[:1]))
    assert "g.example" in description and "ilha.example" not in description


def test_test_image_is_not_pinned():
    doc = _doc(scenes=[Scene(id="c1", segment_id="s1", visual={"prompt": "farol"}),
                       Scene(id="c2", segment_id="s1", part=1, visual={"prompt": "", "reuse_of": "c1"}),
                       Scene(id="c3", segment_id="s2", visual={"prompt": "mar"})])
    srcs = {"c1": "cache/image/a.png", "c2": "cache/image/a.png", "c3": "cache/image/b.png"}
    scenes, pinned = derive._child_scenes(doc, {"s1", "s2"}, srcs, redo={"c1"})
    by_id = {s.id: s for s in scenes}
    assert by_id["c1"].visual.asset is None and by_id["c2"].visual.asset is None  # imagem de teste: o Short refaz
    assert by_id["c2"].visual.reuse_of == "c1"
    assert by_id["c3"].visual.asset == "cache/image/b.png" and pinned == 1


def test_no_timeline_is_409(isolated_settings):
    parent_id = _long_project(built=False)
    with pytest.raises(derive.ShortsError) as exc:
        derive.segments_overview(parent_id)
    assert exc.value.status == 409
    assert exc.value.message == "Renderize o vídeo (ou gere a prévia) antes de cortar Shorts"


def test_suggestions_offline(isolated_settings):
    parent_id = _long_project()
    result = _run("shorts_suggest", parent_id)
    assert result["provider"] == "heuristica"
    data = service.get_project_row(parent_id)["settings"]["shorts_suggestions"]
    # O roteiro offline tem segmentos de ~70 s: nenhum trecho cabe em 35 s, então vêm os segmentos sozinhos mais
    # curtos, com o aviso de duração.
    assert 1 <= len(data["items"]) <= 3
    for item in data["items"]:
        assert item["title"] and item["tipo"] == "normal" and len(item["segment_ids"]) == 1
        assert item["warnings"] == check_short(item["title"], item["duration_s"], "normal")


def _overview(durations: list[float], chapters: set[int] = frozenset()) -> dict:
    return {
        "lang": "pt-BR", "gap_s": 0.35, "lead_in_s": 0.3, "tail_out_s": 0.7,
        "segments": [{"id": f"s{i}", "index": i, "text": f"Frase {i}. Resto.", "role": "body",
                      "chapter": f"Capítulo {i}" if i in chapters else None, "chapter_start": i in chapters,
                      "duration_s": d, "scenes": []} for i, d in enumerate(durations)],
    }


def test_heuristic_picks_contiguous_cuts_in_target():
    overview = _overview([6, 7, 8, 5, 9, 7, 6, 8, 7, 9, 6, 7, 8, 5, 6, 7], chapters={4, 10})
    items = heuristic(overview, limits_from(None))
    assert 3 <= len(items) <= 5
    used: set[str] = set()
    for item in items:
        assert 15 <= item["duration_s"] <= 30
        ids = item["segment_ids"]
        idx = [int(i[1:]) for i in ids]
        assert idx == list(range(idx[0], idx[-1] + 1))  # contínuo
        assert item["ranges"] == [[ids[0], ids[-1]]]
        assert not used & set(ids)
        used |= set(ids)
        assert item["warnings"] == []
    assert any(item["segment_ids"][0] == "s4" for item in items)  # começo de capítulo tem preferência


# ---------------------------------------------------------------------------------------------
# rotas
# ---------------------------------------------------------------------------------------------
def test_routes(isolated_settings):
    channel_id = _channel("#Farol")
    parent_id = _long_project(channel_id=channel_id)
    no_video = _long_project(built=False)
    with TestClient(create_app()) as client:
        r = client.post("/api/shorts/check", json={"title": "URGENTE: o farol", "duration_s": 31,
                                                   "channel_id": channel_id})
        assert r.status_code == 200
        assert r.json() == {"warnings": ["31 s fora do alvo de 15 a 30 s", 'gancho gasto no título ("URGENTE")'],
                            "final_title": "URGENTE: o farol #Farol"}
        assert client.post("/api/shorts/check", json={"title": "x", "duration_s": 200}).status_code == 422
        assert client.post("/api/shorts/check", json={"title": "x", "duration_s": 20, "tipo": "outro"}).status_code == 422

        assert client.get("/api/projects/prj_nada/shorts").status_code == 404
        blocked = client.get(f"/api/projects/{no_video}/shorts").json()
        assert blocked["blocker"] == "Renderize o vídeo (ou gere a prévia) antes de cortar Shorts"
        assert client.post(f"/api/projects/{no_video}/shorts/suggest").status_code == 409

        data = client.get(f"/api/projects/{parent_id}/shorts").json()
        assert data["segments"] and data["children"] == [] and data["parent"] is None and data["hashtag"] == "#Farol"
        ids = [s["id"] for s in data["segments"]]
        r = client.post(f"/api/projects/{parent_id}/shorts", json={"ranges": [[ids[2], ids[1]]], "title": "x"})
        assert r.status_code == 422
        r = client.post(f"/api/projects/{parent_id}/shorts",
                        json={"ranges": [[ids[1], ids[2]]], "title": "O farol", "hook": "Olhe de novo"})
        assert r.status_code == 200, r.text
        child_id = r.json()["project_id"]
        assert set(r.json()) == {"project_id", "duration_s", "warnings"}

        data = client.get(f"/api/projects/{parent_id}/shorts").json()
        assert [c["project_id"] for c in data["children"]] == [child_id]
        child = client.get(f"/api/projects/{child_id}/shorts").json()
        assert child["parent"] == {"project_id": parent_id, "title": service.get_project_row(parent_id)["title"],
                                   "exists": True}
        assert child["short_of"]["hook"] == "Olhe de novo"
        # Short não vira longo de outro Short
        assert client.post(f"/api/projects/{child_id}/shorts",
                           json={"ranges": [[ids[1], ids[1]]], "title": "x"}).status_code == 409
        projects = {p["id"]: p for p in client.get("/api/projects").json()}
        assert projects[child_id]["parent_id"] == parent_id

        job = client.post(f"/api/projects/{parent_id}/shorts/suggest").json()
        assert job["kind"] == "shorts_suggest"


def test_reuse_outside_the_short_becomes_pinned_image(isolated_settings):
    doc = service.create_project(title="Reuso", style_id=LONG_STYLE, topic="o farol abandonado da ilha")
    _run("script", doc.id)
    _run("storyboard", doc.id)
    long_doc = service.get_doc(doc.id)
    source = next(s for s in long_doc.scenes_of(long_doc.segments[1].id) if s.kind == "image" and s.visual.prompt)
    target = long_doc.scenes_of(long_doc.segments[3].id)[0]
    target.visual.reuse_of = source.id
    service.commit(doc.id, long_doc, "Reaproveita imagem")
    _run("preview", doc.id)
    overview = derive.segments_overview(doc.id)
    src = {sc["id"]: sc["src"] for s in overview["segments"] for sc in s["scenes"]}
    assert src[target.id] == src[source.id]

    seg = long_doc.segments[3].id
    result = derive.derive_short(doc.id, [[seg, seg]], "Só o trecho")
    child = service.get_doc(result["project_id"])
    pinned = child.scene(target.id)
    assert pinned.visual.reuse_of is None and pinned.visual.asset == src[source.id]
    assert plan(result["project_id"])["images_missing"] == []
