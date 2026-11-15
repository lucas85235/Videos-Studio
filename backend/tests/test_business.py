"""Bloco D (ADR 0002): banco de ideias, ofertas de afiliado, links na descrição e receita.
Regras trazidas da seção de ideias e da seção dinheiro do `estudio/estudio.py` do canais."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.project.document import Metadata
from studio.db import session_scope
from studio.models import Channel, Project, Publication
from studio.workers.business import ideas, revenue
from studio.workers.business.monetization import (
    AMAZON_DISCLOSURE,
    PUBLI,
    MonetizationError,
    build_links,
    choose_offers,
    config,
    offer_link,
    suggest_tracking_id,
)
from studio.workers.jobs import get_job, get_runner
from studio.workers.publish import youtube as yt
from studio.workers.publish.package import build_package, caption_for
from studio.workers.publish_tasks import DESCRIPTION_MAX, _description


def _client() -> TestClient:
    return TestClient(create_app())


def _channel(cid: str = "chn_a", name: str = "Plantão") -> str:
    with session_scope() as s:
        s.add(Channel(id=cid, name=name, settings={}))
    return cid


def offer(slug: str, **kw) -> dict:
    return {"id": f"ofe_{slug}", "slug": slug, "name": kw.pop("name", slug.upper()), "store": "amazon", "asin": "",
            "link": "", "text": "", "topics": [], "status": "ativa", "checked_on": "", "note": "", **kw}


# --------------------------------------------------------------------------------------------
# Ideias
# --------------------------------------------------------------------------------------------


def test_repeated_title_in_the_same_channel_is_409_unless_forced():
    _channel()
    _channel("chn_b", "Outro")
    first = ideas.create({"title": "Por que o GTA 6 atrasou?", "channel_id": "chn_a", "score": 8})
    with pytest.raises(ideas.DuplicateIdea) as exc:
        ideas.create({"title": "por que o gta 6 ATRASOU", "channel_id": "chn_a"})  # acento, caixa e pontuação
    assert exc.value.idea["id"] == first["id"]
    assert ideas.create({"title": "Por que o GTA 6 atrasou?", "channel_id": "chn_b"})["id"]  # outro canal: pode
    forced = ideas.create({"title": "Por que o GTA 6 atrasou?", "channel_id": "chn_a"}, force=True)
    assert forced["id"] != first["id"]
    with pytest.raises(ideas.IdeaError):
        ideas.create({"title": "Nota alta", "score": 11})
    client = _client()
    res = client.post("/api/ideas", json={"title": "Por quê o GTA 6 atrasou", "channel_id": "chn_a"})
    assert res.status_code == 409 and res.json()["id"] == first["id"]
    assert client.post("/api/ideas", json={"title": "x", "score": -1}).status_code == 422


def test_list_orders_by_score_with_no_score_last_then_newest():
    _channel()
    low = ideas.create({"title": "Baixa", "channel_id": "chn_a", "score": 5})
    none_old = ideas.create({"title": "Sem nota velha", "channel_id": "chn_a"})
    high = ideas.create({"title": "Alta", "channel_id": "chn_a", "score": 9})
    none_new = ideas.create({"title": "Sem nota nova", "channel_id": "chn_a"})
    tie = ideas.create({"title": "Alta empatada nova", "channel_id": "chn_a", "score": 9})
    order = [i["id"] for i in ideas.list_ideas(channel_id="chn_a")]
    assert order == [tie["id"], high["id"], low["id"], none_new["id"], none_old["id"]]
    assert [i["id"] for i in ideas.list_ideas(min_score=6)] == [tie["id"], high["id"]]
    ideas.decide([low["id"], none_old["id"]], "rejeitada", "fraca")
    rejected = ideas.list_ideas(status="rejeitada")
    assert {i["id"] for i in rejected} == {low["id"], none_old["id"]}
    assert all(i["reason"] == "fraca" and i["decided_at"] for i in rejected)
    ideas.decide([low["id"]], "descartada")  # sem motivo: mantém o anterior
    assert ideas.get(low["id"])["reason"] == "fraca"


def test_idea_becomes_project_and_is_done_when_published():
    _channel()
    idea = ideas.create({"title": "O mistério do farol", "channel_id": "chn_a", "angle": "Ninguém sabe quem acende",
                         "evidence": "3 vídeos com 1 mi de views"})
    out = _client().post(f"/api/ideas/{idea['id']}/project", json={"style_id": "misterios-short"})
    assert out.status_code == 200, out.text
    body = out.json()
    pid = body["project"]["id"]
    assert body["idea"]["status"] == "em_producao" and body["idea"]["project_id"] == pid
    doc = service.get_doc(pid)
    assert doc.title == doc.topic == "O mistério do farol" and doc.channel_id == "chn_a"
    assert "Ninguém sabe quem acende" in doc.brief and "3 vídeos com 1 mi de views" in doc.brief
    assert _client().post(f"/api/ideas/{idea['id']}/project", json={}).status_code == 409  # já virou projeto
    with session_scope() as s:
        s.add(Publication(id="pub_x", project_id=pid, platform="youtube", language="pt-BR", driver="youtube_native",
                          status="scheduled"))
    assert ideas.get(idea["id"])["status"] == "feita"


# --------------------------------------------------------------------------------------------
# Links de afiliado
# --------------------------------------------------------------------------------------------

CFG = {"amazon_tag": "plantaovice-20", "tracking_ids": {"prj_ep2": "plantaoviceep2-20"}}


def test_offer_link_cases():
    cfg = config(CFG)
    own = offer("gta", asin="B0ABC", link="https://amzn.to/x")
    assert offer_link(cfg, own, "prj_ep2") == ("https://www.amazon.com.br/dp/B0ABC?tag=plantaoviceep2-20", "plantaoviceep2-20")
    assert offer_link(cfg, own, "prj_outro") == ("https://amzn.to/x", "plantaovice-20")  # link curto com o padrão
    assert offer_link(cfg, offer("ps5", asin="B0PS5"), "prj_outro") == (
        "https://www.amazon.com.br/dp/B0PS5?tag=plantaovice-20", "plantaovice-20")
    assert offer_link(cfg, offer("ml", store="mercadolivre", link="https://ml.com/x"), "prj_ep2") == ("https://ml.com/x", "")
    assert offer_link(config({}), offer("vazio", asin="B0"), None) == ("", "")
    assert suggest_tracking_id(cfg, "prj_AB-12") == "plantaoviceprjab12-20"
    assert suggest_tracking_id(config({"amazon_tag": "semtraco"}), "ep3") == "semtracoep3-20"


def test_choose_offers_by_topic_and_inactive_error():
    offers = [offer("sempre", topics=["sempre"]), offer("ps5", topics=["compra", "console"]),
              offer("pausada", topics=["compra"], status="pausada"), offer("outra", topics=["tamanho"])]
    assert [o["slug"] for o in choose_offers(offers, [], ["Compra"])] == ["sempre", "ps5"]
    assert [o["slug"] for o in choose_offers(offers, [], [])] == ["sempre"]
    assert [o["slug"] for o in choose_offers(offers, ["outra", "ps5"], [])] == ["outra", "ps5"]
    with pytest.raises(MonetizationError, match="não ativa: pausada"):
        choose_offers(offers, ["pausada"], [])
    with pytest.raises(MonetizationError, match="não existe"):
        choose_offers(offers, ["nada"], [])


def test_build_links_top_pinned_end_and_checklist():
    cfg = {**CFG, "publi_pinned": "#publi · link de afiliado"}
    offers = [offer("gta", asin="B0GTA", text="🎮 GTA 6", checked_on="2026-11-14"),
              offer("ps5", asin="B0PS5", name="PS5", checked_on="2026-11-01"),
              offer("ml", store="mercadolivre", link="https://ml.com/x", text="Controle"),
              offer("quarta", asin="B04")]
    r = build_links(cfg, "prj_novo", offers, is_short=False, today=date(2026, 11, 15))
    assert len(r["offers"]) == 3  # no máximo 3 links
    assert r["top"] == ("🎮 GTA 6: https://www.amazon.com.br/dp/B0GTA?tag=plantaovice-20\n"
                        "PS5: https://www.amazon.com.br/dp/B0PS5?tag=plantaovice-20\n"
                        f"Controle: https://ml.com/x\n{PUBLI}")
    assert r["pinned"] == "🎮 GTA 6: https://www.amazon.com.br/dp/B0GTA?tag=plantaovice-20\n#publi · link de afiliado"
    assert r["end"] == AMAZON_DISCLOSURE
    text = "\n".join(r["checklist"])
    assert r["checklist"][0].startswith('Marcar "Inclui promoção paga"') and "selo PUBLI" in r["checklist"][1]
    assert '"plantaoviceprjnovo-20"' in text  # sem ID próprio: sugere um
    assert "ok: GTA (conferida em 2026-11-14)" in text  # 1 dia
    assert "CONFERIR: PS5 (conferida em 2026-11-01)" in text  # 14 dias > 3
    assert "CONFERIR: ML (nunca conferida)" in text
    assert "Short:" not in text
    short = build_links(cfg, "prj_ep2", offers[:1], is_short=True, today=date(2026, 11, 17))
    assert "não é clicável" in short["checklist"][-1]
    assert not any("ID de rastreamento" in c for c in short["checklist"])  # tem ID próprio
    assert "ok: GTA (conferida em 2026-11-14)" in "\n".join(short["checklist"])  # 3 dias: ainda ok (só mais de 3 pede conferência)
    only_ml = build_links(cfg, "x", [offer("ml", store="mercadolivre", link="https://ml.com/x")])
    assert only_ml["end"] == ""  # sem Amazon: sem aviso
    assert build_links(cfg, "x", [])["checklist"] == ["Nenhuma oferta ativa para este vídeo."]


def test_youtube_description_with_links_fits_and_without_links_is_unchanged():
    meta = Metadata(description="Resumo do vídeo.", hashtags=["#gta", "#ps5"])
    old = _description(meta)
    assert _description(meta, "", None) == old == _description(meta, "", {"top": "", "end": ""})
    links = {"top": "GTA: https://a\n#publi", "end": AMAZON_DISCLOSURE}
    assert _description(meta, "", links) == f"GTA: https://a\n#publi\n\nResumo do vídeo.\n\n#gta #ps5\n\n{AMAZON_DISCLOSURE}"
    credits = "Créditos das imagens:\n- Foto — CC0"
    huge = Metadata(description="x" * 6000, hashtags=["#a"])
    text = _description(huge, credits, links)
    assert len(text) <= DESCRIPTION_MAX
    assert text.startswith("GTA: https://a\n#publi\n\nxxx")
    assert text.endswith(f"{credits}\n\n#a\n\n{AMAZON_DISCLOSURE}")
    meta_dict = huge.model_dump()
    assert caption_for("youtube", meta_dict, "T", credits, links) == text
    assert caption_for("tiktok", meta_dict, "T", credits, links) == caption_for("tiktok", meta_dict, "T", credits)


# --------------------------------------------------------------------------------------------
# Receita
# --------------------------------------------------------------------------------------------


def test_revenue_sums_by_tracking_id_without_counting_the_total_twice():
    _channel()
    revenue.upsert("chn_a", "amazon", "2026-11", "", clicks=100, orders=5, commission=50.0)  # total da loja
    revenue.upsert("chn_a", "amazon", "2026-11", "plantaoviceep2-20", clicks=60, orders=3, commission=30.0)
    revenue.upsert("chn_a", "amazon", "2026-11", "plantaoviceep3-20", clicks=40, commission=20.0)
    revenue.upsert("chn_a", "amazon", "2026-11", "plantaoviceep3-20", orders=2)  # COALESCE: não apaga cliques
    revenue.upsert("chn_a", "amazon", "2026-10", "", clicks=10, commission=4.5)  # só total: conta o total
    nov, oct = revenue.summary("chn_a")
    assert (nov["period"], nov["clicks"], nov["orders"], nov["commission"], nov["sales"]) == ("2026-11", 100, 5, 50.0, None)
    assert {r["tracking_id"]: r["clicks"] for r in nov["by_tracking"]} == {"plantaoviceep2-20": 60, "plantaoviceep3-20": 40}
    assert (oct["clicks"], oct["commission"], oct["by_tracking"]) == (10, 4.5, [])
    assert [r["period"] for r in revenue.summary("chn_a", months=1)] == ["2026-11"]
    for bad in ("2026-13", "26-11", "2026-1"):
        with pytest.raises(revenue.RevenueError):
            revenue.upsert("chn_a", "amazon", bad, clicks=1)
    with pytest.raises(revenue.RevenueError, match="ao menos um número"):
        revenue.upsert("chn_a", "amazon", "2026-11")
    client = _client()
    assert client.post("/api/revenue", json={"channel_id": "chn_a", "store": "amazon", "period": "2026-00",
                                             "clicks": 1}).status_code == 422
    assert client.post("/api/revenue", json={"channel_id": "chn_a", "store": "amazon", "period": "2026-12"}).status_code == 422
    got = client.get("/api/revenue", params={"channel_id": "chn_a", "months": 12}).json()
    assert len(got["entries"]) == 4 and got["summary"][0]["commission"] == 50.0
    entry_id = got["entries"][0]["id"]
    assert client.delete(f"/api/revenue/{entry_id}").json() == {"deleted": True}
    assert client.delete(f"/api/revenue/{entry_id}").status_code == 404


# --------------------------------------------------------------------------------------------
# Rotas e integração com a publicação
# --------------------------------------------------------------------------------------------


def _rendered_project(isolated_settings, channel_id: str) -> str:
    doc = service.create_project(title="Episódio", style_id="misterios-short", channel_id=channel_id)
    meta = {"pt-BR": Metadata(title="Episódio", description="Resumo.", hashtags=["#a"])}
    service.commit(doc.id, doc.model_copy(update={"metadata": meta}), "pronto")
    out = isolated_settings.outputs_dir / doc.id / "pt-BR"
    out.mkdir(parents=True, exist_ok=True)
    (out / "video.mp4").write_bytes(b"mp4")
    service.update_settings(doc.id, {"last_final": {"langs": {"pt-BR": {"video": f"outputs/{doc.id}/pt-BR/video.mp4"}},
                                                    "preview_only_providers": []}})
    return doc.id


def test_routes_offers_monetization_affiliate_and_publish(isolated_settings, monkeypatch):
    cid = _channel()
    client = _client()
    cfg = client.get(f"/api/channels/{cid}/monetization").json()
    assert cfg["max_links"] == 3 and cfg["amazon_domain"] == "www.amazon.com.br" and cfg["publi"] == PUBLI
    saved = client.put(f"/api/channels/{cid}/monetization", json={
        "amazon_tag": "plantaovice-20", "fronts": [{"name": "Afiliado Amazon", "status": "ativa"}]}).json()
    assert saved["amazon_tag"] == "plantaovice-20" and saved["fronts"][0]["id"] == "afiliado-amazon"
    assert saved["publi"] == PUBLI  # o resto não muda
    assert client.put(f"/api/channels/{cid}/monetization", json={"fronts": [{"name": "x", "status": "zzz"}]}).status_code == 422

    body = {"slug": "gta6-ps5", "name": "GTA 6 (PS5)", "asin": "b0gta", "text": "🎮 GTA 6", "topics": ["Compra"],
            "status": "ativa", "checked_on": date.today().isoformat()}
    created = client.post(f"/api/channels/{cid}/offers", json=body)
    assert created.status_code == 200, created.text
    off = created.json()
    assert off["asin"] == "B0GTA" and off["topics"] == ["compra"] and off["stale"] is False
    assert client.post(f"/api/channels/{cid}/offers", json=body).status_code == 409  # slug único no canal
    assert client.post(f"/api/channels/{cid}/offers", json={**body, "slug": "b", "status": "x"}).status_code == 422
    assert client.post(f"/api/channels/{cid}/offers", json={**body, "slug": "c", "checked_on": "2026-02-30"}).status_code == 422
    assert client.put(f"/api/channels/{cid}/offers/{off['id']}", json={"note": "ok"}).json()["note"] == "ok"
    assert client.post(f"/api/channels/{cid}/offers", json={**body, "slug": "outra", "status": "ideia"}).status_code == 200

    pid = _rendered_project(isolated_settings, cid)
    old_caption = caption_for("youtube", service.get_doc(pid).metadata["pt-BR"].model_dump(), "Episódio")
    off_aff = client.get(f"/api/projects/{pid}/affiliate").json()
    assert off_aff["enabled"] is False and off_aff["is_short"] is True and len(off_aff["available"]) == 2
    on = client.put(f"/api/projects/{pid}/affiliate", json={"enabled": True, "topics": ["compra"]}).json()
    assert on["errors"] == [] and [o["slug"] for o in on["offers"]] == ["gta6-ps5"]
    assert on["top"].startswith("🎮 GTA 6: https://www.amazon.com.br/dp/B0GTA?tag=plantaovice-20")
    assert "não é clicável" in on["checklist"][-1] and on["end"] == AMAZON_DISCLOSURE
    bad = client.put(f"/api/projects/{pid}/affiliate", json={"offer_ids": ["outra"]}).json()
    assert bad["errors"] and "não ativa" in bad["errors"][0] and bad["top"] == ""
    tracked = client.put(f"/api/projects/{pid}/affiliate", json={"offer_ids": [], "tracking_id": "plantaoviceep1-20"}).json()
    assert "tag=plantaoviceep1-20" in tracked["top"] and tracked["tracking_id"] == "plantaoviceep1-20"
    assert client.get(f"/api/channels/{cid}/monetization").json()["tracking_ids"] == {pid: "plantaoviceep1-20"}

    # Pacote manual: legenda com links, comentário fixado e checklist.
    pkg = build_package(pid, "pt-BR", ["youtube", "tiktok"])
    yt_item = next(i for i in pkg["items"] if i["platform"] == "youtube")
    assert yt_item["caption"] == f"{tracked['top']}\n\nResumo.\n\n#a\n\n{AMAZON_DISCLOSURE}"
    assert (Path(yt_item["folder"]) / "comentario-fixado.txt").read_text(encoding="utf-8") == tracked["pinned"] + "\n"
    assert "Inclui promoção paga" in (Path(yt_item["folder"]) / "CHECKLIST.md").read_text(encoding="utf-8")
    assert "#publi" not in next(i for i in pkg["items"] if i["platform"] == "tiktok")["caption"]

    # Envio ao YouTube (simulado): topo e aviso na descrição.
    sent: dict = {}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None,
                     paid_placement=False):
        sent.update(snippet=snippet)
        return {"id": "vid1"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private"}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    assert sent["snippet"]["description"] == yt_item["caption"]

    # Desligado: descrição igual à de antes.
    client.put(f"/api/projects/{pid}/affiliate", json={"enabled": False})
    pkg = build_package(pid, "pt-BR", ["youtube"])
    assert pkg["items"][0]["caption"] == old_caption == "Resumo.\n\n#a"

    summary = client.get("/api/money/summary").json()["channels"][0]
    assert summary["channel_id"] == cid and summary["fronts"][0]["status"] == "ativa"
    gta = next(o for o in summary["offers"] if o["slug"] == "gta6-ps5")
    assert gta["url"] == "https://www.amazon.com.br/dp/B0GTA?tag=plantaovice-20" and gta["stale"] is False
    assert summary["revenue"] == []
    assert client.delete(f"/api/channels/{cid}/offers/{off['id']}").json() == {"deleted": True}
    assert client.get("/api/projects/nada/affiliate").status_code == 404


def test_idea_routes_crud_and_decide():
    _channel()
    client = _client()
    a = client.post("/api/ideas", json={"title": "A", "channel_id": "chn_a", "score": 7.5, "format": "short"}).json()
    b = client.post("/api/ideas", json={"title": "B", "channel_id": "chn_a"}).json()
    assert client.post("/api/ideas", json={"title": "C", "channel_id": "nada"}).status_code == 422
    assert client.put(f"/api/ideas/{a['id']}", json={"angle": "novo"}).json()["angle"] == "novo"
    assert client.put(f"/api/ideas/{b['id']}", json={"title": "a"}).status_code == 409
    decided = client.post("/api/ideas/decide", json={"ids": [a["id"], b["id"]], "status": "aprovada", "reason": "boa"}).json()
    assert {d["status"] for d in decided} == {"aprovada"}
    assert client.post("/api/ideas/decide", json={"ids": ["nada"], "status": "aprovada"}).status_code == 404
    assert client.post("/api/ideas/decide", json={"ids": [a["id"]], "status": "zzz"}).status_code == 422
    listed = client.get("/api/ideas", params={"channel_id": "chn_a", "status": "aprovada", "min_score": 7}).json()
    assert [i["id"] for i in listed] == [a["id"]]
    assert client.delete(f"/api/ideas/{b['id']}").json() == {"deleted": True}
    assert client.delete(f"/api/ideas/{b['id']}").status_code == 404
    with session_scope() as s:
        assert s.query(Project).count() == 0


# --------------------------------------------------------------------------------------------
# Correções da revisão
# --------------------------------------------------------------------------------------------


def test_publish_youtube_sends_paid_placement_and_warns_stale_offer(isolated_settings, monkeypatch):
    cid = _channel()
    client = _client()
    client.put(f"/api/channels/{cid}/monetization", json={"amazon_tag": "plantaovice-20"})
    off = client.post(f"/api/channels/{cid}/offers", json={"slug": "gta", "name": "GTA 6", "asin": "B0GTA",
                                                           "status": "ativa", "checked_on": "2020-01-01"}).json()
    pid = _rendered_project(isolated_settings, cid)
    on = client.put(f"/api/projects/{pid}/affiliate", json={"enabled": True, "mode": "ofertas", "offer_ids": [off["id"]]}).json()
    assert on["mode"] == "ofertas" and on["errors"] == [] and on["top"]
    # Trocar o apelido não quebra o projeto: ele guarda o id da oferta.
    client.put(f"/api/channels/{cid}/offers/{off['id']}", json={"slug": "gta-novo"})
    assert client.get(f"/api/projects/{pid}/affiliate").json()["errors"] == []
    sent: dict = {}

    def upload_video(path, *, snippet, status, localizations=None, http=None, on_progress=None, account=None,
                     paid_placement=False):
        sent.update(paid=paid_placement)
        return {"id": "vid1"}

    monkeypatch.setattr(yt, "upload_video", upload_video)
    monkeypatch.setattr(yt, "account_for_channel", lambda channel_id: "global")
    monkeypatch.setattr(yt, "account_title", lambda account: "Canal")
    job = get_runner().submit("publish_youtube", {"lang": "pt-BR", "privacy": "private"}, pid, run_inline=True)
    final = get_job(job["id"])
    assert final and final["status"] == "done", final and final["error"]
    assert sent["paid"] is True
    warnings = final["result"]["warnings"]
    assert any(w.startswith("CONFERIR: GTA 6 (conferida em 2020-01-01)") for w in warnings)
    assert any(on["pinned"] in w for w in warnings)
    with session_scope() as s:
        pub = s.query(Publication).filter_by(project_id=pid).one()
        assert pub.details["affiliate"]["pinned"] == on["pinned"] and pub.details["affiliate"]["checklist"]


def test_offers_mode_with_nothing_marked_has_no_links_and_warns(isolated_settings):
    from studio.workers.business.monetization import description_links

    cid = _channel()
    client = _client()
    client.put(f"/api/channels/{cid}/monetization", json={"amazon_tag": "plantaovice-20"})
    client.post(f"/api/channels/{cid}/offers", json={"slug": "sempre", "name": "Sempre", "asin": "B0S", "topics": ["sempre"],
                                                     "status": "ativa"})
    pid = _rendered_project(isolated_settings, cid)
    temas = client.put(f"/api/projects/{pid}/affiliate", json={"enabled": True, "topics": ["compra"]}).json()
    assert [o["slug"] for o in temas["offers"]] == ["sempre"]
    none = client.put(f"/api/projects/{pid}/affiliate", json={"mode": "ofertas", "offer_ids": []}).json()
    assert none["mode"] == "ofertas" and none["offers"] == [] and none["top"] == ""  # não volta aos temas
    links = description_links(pid)
    assert links["top"] == "" and links["errors"] == ["links ligados, mas nenhuma oferta ativa combina com este vídeo"]


def test_fit_description_never_cuts_the_legal_notice():
    from studio.workers.business.monetization import fit_description

    text = fit_description("corpo", "c" * 3000, top="t" * 2500, end=AMAZON_DISCLOSURE, limit=5000)
    assert len(text) == 5000 and text.endswith(AMAZON_DISCLOSURE) and text.startswith("t" * 2500)
    text = fit_description("corpo", "c" * 10, top="t" * 6000, end=AMAZON_DISCLOSURE, limit=5000)
    assert len(text) <= 5000 and text.endswith(AMAZON_DISCLOSURE)


def test_idea_with_deleted_project_can_become_project_again_and_empty_channel_is_none():
    _channel()
    client = _client()
    idea = client.post("/api/ideas", json={"title": "Sem canal", "channel_id": ""})
    assert idea.status_code == 200 and idea.json()["channel_id"] is None
    assert client.put(f"/api/ideas/{idea.json()['id']}", json={"channel_id": ""}).json()["channel_id"] is None
    a = ideas.create({"title": "Farol", "channel_id": "chn_a"})
    pid = client.post(f"/api/ideas/{a['id']}/project", json={"style_id": "misterios-short"}).json()["project"]["id"]
    assert client.delete(f"/api/projects/{pid}").status_code in (200, 204)
    listed = next(i for i in client.get("/api/ideas").json() if i["id"] == a["id"])
    assert listed["status"] == "aprovada" and listed["project_id"] is None
    again = client.post(f"/api/ideas/{a['id']}/project", json={"style_id": "misterios-short"})
    assert again.status_code == 200 and again.json()["idea"]["project_id"] not in (None, pid)


def test_idea_without_style_uses_one_of_its_format():
    from studio.core.styles.library import get_style, list_styles

    with session_scope() as s:
        s.add(Channel(id="chn_s", name="Shorts", settings={}, default_style_id="misterios-short"))
    longs = [i["style"]["id"] for i in list_styles() if i["style"]["format"]["type"] == "long"]
    assert ideas._default_style("chn_s", "") == "misterios-short"
    assert ideas._default_style("chn_s", "short") == "misterios-short"
    if longs:
        assert get_style(ideas._default_style("chn_s", "long")).format.type == "long"
