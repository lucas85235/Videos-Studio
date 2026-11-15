"""Curadoria visual de ponta a ponta, offline (LLM, voz e imagens de mentira).

Roteiro mock → texto com números → aprova o roteiro → storyboard v2 para no portão → aprova as cenas →
prévia → timeline com cartões `stat`/`compare`, cenas que reaproveitam imagem e legenda sem número cortado.
"""

from __future__ import annotations

import json
import re

from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.numbers import number_runs
from studio.core.project import service
from studio.core.project.document import CARD_KINDS
from studio.core.text import span_ranges, words
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline.build import scene_needs_image

# Números por extenso que a legenda nova mostra com algarismos.
TEXTS = [
    "Em dois mil e treze, o jogo vendeu mais de duzentos milhões de cópias. Pensa comigo.",
    "O primeiro vídeo teve noventa milhões de visualizações em vinte e quatro horas. No segundo, de noventa "
    "milhões para quatrocentos e setenta e cinco milhões.",
    "Foram dois adiamentos até agora. Em maio de dois mil e vinte e cinco, o segundo vídeo mostrou a cidade inteira.",
]
SPELLED = re.compile(r"\b(treze|duzentos|noventa|vinte|quatrocentos|setenta|milhões)\b", re.IGNORECASE)


class _InlineRunner:
    """Roda na hora o job que a rota enviaria para a fila."""

    def __init__(self) -> None:
        self.jobs: list[dict] = []

    def submit(self, kind, payload=None, project_id=None, **_kw):
        job = get_runner().submit(kind, payload, project_id, run_inline=True)
        self.jobs.append(job)
        return job


def _done(job: dict) -> dict:
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    return final


def test_curadoria_offline_end_to_end(monkeypatch, isolated_settings):
    from studio.api import routes_projects

    runner = _InlineRunner()
    monkeypatch.setattr(routes_projects, "get_runner", lambda: runner)
    with TestClient(create_app()) as client:
        created = client.post(
            "/api/projects",
            json={"title": "Jogo", "style_id": "games-cultura-pop-short", "topic": "o jogo mais esperado da década"},
        )
        assert created.status_code == 200, created.text
        pid = created.json()["project"]["id"]
        assert created.json()["project"]["settings"]["gates"]["storyboard"] is True

        # 1) roteiro mock, com números por extenso nos primeiros trechos
        _done(get_runner().submit("script", {}, pid, run_inline=True))
        doc = service.get_doc(pid)
        lang = doc.primary_language
        assert len(doc.segments) >= 4
        ops = [{"op": "update_segment_text", "segment_id": seg.id, "lang": lang, "text": text}
               for seg, text in zip(doc.segments, TEXTS, strict=False)]  # fmt: skip
        edited = client.post(f"/api/projects/{pid}/ops", json={"ops": ops, "message": "Números no roteiro"})
        assert edited.status_code == 200, edited.text

        # 2) aprovar o roteiro cria as cenas e para no portão, sem imagem nenhuma
        approved = client.post(f"/api/projects/{pid}/approve", json={"stage": "script"})
        assert approved.status_code == 200, approved.text
        stopped = _done(runner.jobs[-1])
        assert stopped["result"]["stopped_at"] == "storyboard" and stopped["result"]["steps"] == ["storyboard"]
        detail = client.get(f"/api/projects/{pid}").json()
        assert "last_preview" not in detail["project"]["settings"]
        assert not (isolated_settings.data_dir / "outputs" / pid).exists()
        review = detail["project"]["settings"]["storyboard_review"]
        assert review["provider"] == "mock_llm" and review["version"] == detail["project"]["current_version"]
        assert not [i for i in review["issues"] if i["severity"] == "grave"], review["issues"]

        doc = service.get_doc(pid)
        assert doc.art is not None and doc.art.languages and not doc.storyboard_approved
        kinds = [s.kind for s in doc.ordered_scenes()]
        assert "stat" in kinds and "compare" in kinds, kinds
        reused = [s for s in doc.scenes if s.visual.reuse_of]
        assert reused and all(doc.reuse_root(s) is not None for s in reused)
        new_images = [s for s in doc.scenes if scene_needs_image(s) and not s.visual.asset]
        counts = review["counts"]
        assert counts["scenes"] == len(doc.scenes) and counts["new_images"] == len(new_images)
        assert counts["reused"] == len(reused) and counts["cards"] == sum(s.kind in CARD_KINDS for s in doc.scenes)
        assert counts["new_images"] <= counts["budget"] < counts["scenes"]

        # nenhuma cena começa ou termina no meio de um número (spans do storyboard)
        for seg in doc.segments:
            scenes = doc.scenes_of(seg.id)
            tokens = words(seg.text_for(lang))
            assert all(s.span is not None for s in scenes)
            assert sum(s.span for s in scenes) == len(tokens)
            cuts = {a for a, _b in span_ranges([s.span for s in scenes], len(tokens))[1:]}
            for i0, i1, _value in number_runs(tokens, lang):
                assert not any(i0 < cut < i1 for cut in cuts), (seg.id, tokens[i0:i1])

        # 3) aprovar as cenas gera as imagens e a prévia, e para no portão da prévia
        approved = client.post(f"/api/projects/{pid}/approve", json={"stage": "storyboard"})
        assert approved.status_code == 200, approved.text
        job = _done(approved.json()["job"])
        assert job["result"]["steps"] == ["preview"] and job["result"]["stopped_at"] == "preview"
        preview = job["result"]["preview"]
        assert preview["generated"].get("image", 0) == counts["new_images"]
        assert client.get(f"/api/projects/{pid}").json()["doc"]["storyboard_approved"] is True

    # 4) a timeline gravada
    entry = preview["langs"][lang]
    timeline = json.loads((isolated_settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in timeline["scenes"]}
    assert len(by_id) == len(doc.scenes)
    for a, b in zip(timeline["scenes"], timeline["scenes"][1:], strict=False):
        assert a["from"] + a["durationInFrames"] == b["from"]

    stats = [s for s in timeline["scenes"] if s["kind"] == "stat"]
    assert stats and all(s["card"]["value"] and re.search(r"\d", s["card"]["value"]) for s in stats)
    compares = [s for s in timeline["scenes"] if s["kind"] == "compare"]
    assert compares
    for scene in compares:
        items = scene["card"]["items"]
        assert 2 <= len(items) <= 3 and all(isinstance(i["label"], str) and isinstance(i["value"], str) for i in items)
        assert scene["card"].get("highlight") in range(len(items))
    hook = timeline["scenes"][0]
    assert hook["card"]["headline"] and hook["src"] and hook["motion"] == "punch_zoom"

    # cena que reaproveita mostra a imagem da raiz; cartão sem fundo fica sem imagem
    for scene in doc.scenes:
        out = by_id[scene.id]
        if scene.visual.reuse_of:
            root = doc.reuse_root(scene)
            assert root is not None and out["src"] and out["src"] == by_id[root.id]["src"]
        elif scene.visual.type == "none":
            assert out["src"] is None
        if scene.focus:
            assert out["focus"] == scene.focus
    assert {s["src"] for s in timeline["scenes"] if s["src"]} == {by_id[s.id]["src"] for s in new_images}
    impact = {x["scene_id"] for x in timeline["sfx"] if x["type"] == "impact"}
    assert {s["id"] for s in stats + compares if s["id"] != hook["id"]} <= impact

    # legenda com algarismos e sem número partido entre linhas
    caption_words = [w["text"] for s in timeline["scenes"] for line in s["captions"] for w in line["words"]]
    caption_text = " ".join(caption_words)
    # cada número vira uma palavra só da legenda (não quebra entre linhas nem entre cenas)
    for expected in ("2013,", "200 milhões", "90 milhões", "24", "475 milhões.", "2025,"):
        assert expected in caption_words, (expected, caption_text)
    assert not SPELLED.search(" ".join(w for w in caption_words if not re.search(r"\d", w))), caption_text
    assert "dois" in caption_words  # "dois adiamentos" fica por extenso (um algarismo só, valor < 10)
    for scene in timeline["scenes"]:
        assert [w["text"] for w in scene["words"]] == [w["text"] for line in scene["captions"] for w in line["words"]]
