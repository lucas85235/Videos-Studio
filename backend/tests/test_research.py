"""Pesquisa de vídeos do canal. Sem rede e sem cota: a YouTube Data API é simulada com `httpx.MockTransport` e a
síntese usa o LLM offline (`mock_video_ideas`)."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.db import session_scope
from studio.ids import new_id
from studio.media import safe_http
from studio.models import Channel, Idea, Job, PendingTask, ResearchRun, YouTubeComment
from studio.providers import credentials
from studio.providers.base import TransientError
from studio.workers import jobs
from studio.workers.ops import routines, today
from studio.workers.pipeline.build import BuildCancelled
from studio.workers.publish import youtube as yt
from studio.workers.radar import access
from studio.workers.radar.access import Access, Api
from studio.workers.research import check, config, service, signals, synthesis

NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)
TODAY = NOW.astimezone().date()
COMP = "UC" + "c" * 22


@pytest.fixture(autouse=True)
def _sem_chave_do_ambiente(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(vid: str, views: int, days: float, dur: str = "PT12M", **snippet) -> dict:
    return {"id": vid, "snippet": {"publishedAt": _iso(days), "title": f"Vídeo {vid}", **snippet},
            "statistics": {"viewCount": str(views)}, "contentDetails": {"duration": dur}}


VIDEOS = {
    # concorrente: longos assentados (mediana 11.000) + um novo fora da curva e um antigo fora da janela
    "c1": _item("c1", 10000, 20, channelId=COMP),
    "c2": _item("c2", 12000, 30, channelId=COMP),
    "c3": _item("c3", 8000, 40, channelId=COMP),
    "c4": _item("c4", 11000, 50, channelId=COMP),
    "c5": _item("c5", 100000, 200, channelId=COMP),  # entra na linha de base, mas é velho demais para outlier
    "c6": _item("c6", 50000, 3, channelId=COMP, title="O cartel que comprou um país"),  # 4,55× (fora da base: 3 dias)
    # concorrente: Shorts (mediana própria de 100.000)
    "s1": _item("s1", 100000, 20, "PT45S", channelId=COMP),
    "s2": _item("s2", 120000, 25, "PT50S", channelId=COMP),
    "s3": _item("s3", 90000, 30, "PT30S", channelId=COMP),
    "s4": _item("s4", 400000, 2, "PT40S", channelId=COMP, title="O guarda que viu tudo"),  # 4×
    "s5": _item("s5", 150000, 1, "PT40S", channelId=COMP),  # 1,5×: não é outlier
    # buscas
    "kbr1": _item("kbr1", 80000, 10, "PT15M", channelId="UCkbr", title="Como a máfia lavava dinheiro no Brasil",
                  defaultAudioLanguage="pt-BR"),
    "kbr2": _item("kbr2", 900000, 10, "PT15M", channelId="UCkbr", title="How the mafia laundered the money"),
    "kin": _item("kin", 5000000, 5, "PT10M", channelId="UCin", title="Como ganhar dinheiro de verdade"),
    "kus1": _item("kus1", 200000, 8, "PT20M", channelId="UCkus", title="Inside the cartel money machine"),
    "kus2": _item("kus2", 150000, 4, "PT50S", channelId="UCksh", title="The cartel boss nobody caught"),
}
CHANNELS = {
    COMP: {"id": COMP, "snippet": {"title": "Vice Rival", "customUrl": "@rival"},
           "statistics": {"subscriberCount": "50000"},
           "contentDetails": {"relatedPlaylists": {"uploads": "UUrival"}}},
    "UCkbr": {"id": "UCkbr", "snippet": {"title": "Crime BR", "country": "BR"},
              "statistics": {"subscriberCount": "2000"}},
    "UCkus": {"id": "UCkus", "snippet": {"title": "Cartel Files", "country": "US"},
              "statistics": {"subscriberCount": "500000"}},
    "UCksh": {"id": "UCksh", "snippet": {"title": "Short Crime", "country": "US"},
              "statistics": {"subscriberCount": "1000"}},
    "UCin": {"id": "UCin", "snippet": {"title": "Dinheiro Já", "country": "IN"},
             "statistics": {"subscriberCount": "3000"}},
}
PLAYLISTS = {"UUrival": ["c1", "c2", "c3", "c4", "c5", "c6", "s1", "s2", "s3", "s4", "s5"]}


def _handler(calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        calls.append(url)
        q = parse_qs(urlparse(url).query)
        path = urlparse(url).path.rsplit("/", 1)[-1]
        if path == "search":
            ids = ["kbr1", "kbr2", "kin"] if q["regionCode"] == ["BR"] else ["kus1", "kus2"]
            return httpx.Response(200, json={"items": [{"id": {"videoId": i}} for i in ids]})
        if path == "videos":
            return httpx.Response(200, json={"items": [VIDEOS[i] for i in q["id"][0].split(",") if i in VIDEOS]})
        if path == "channels":
            return httpx.Response(200, json={"items": [CHANNELS[i] for i in q["id"][0].split(",")
                                                       if i in CHANNELS]})
        if path == "playlistItems":
            vids = PLAYLISTS.get(q["playlistId"][0], [])
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v}} for v in vids]})
        return httpx.Response(404, json={})

    return handler


def _client(calls: list[str]) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(_handler(calls)))


def _api(calls: list[str], budget: int = 600) -> Api:
    return Api(Access(source="chave própria (YOUTUBE_API_KEY)", key="chave-teste"), budget, http=_client(calls))


def _channel(name: str = "Lucas em Vice", **settings) -> str:
    cid = new_id("cha")
    with session_scope() as s:
        s.add(Channel(id=cid, name=name, niche="crime e máfia", settings=settings))
    return cid


def _comment(cid: str, text: str, likes: int, days: float, **kw) -> None:
    with session_scope() as s:
        s.add(YouTubeComment(id=new_id("thr"), channel_id=cid, video_id="vidown12345", video_title="Ep. 1",
                             comment_id=new_id("cmt"), text=text, like_count=likes,
                             published_at=NOW - timedelta(days=days), **kw))


# ----------------------------------------------------------------------------- cota


def test_plan_units_buscas_no_balde_proprio_e_leituras_na_cota_geral():
    # o radar ainda conta a busca como 100; a pesquisa usa o modelo do medidor (publish/quota.py)
    assert Api.custo("search?q=x") == 100 and Api.custo("channels?id=x") == 1
    assert signals.competitor_units(10) == 21  # 1 channels + 10 playlistItems + 10 videos
    cfg = {"competitors": [{"id": f"UC{i:022d}"} for i in range(10)], "keywords": ["a", "b", "c"],
           "keywords_us": ["x", "y"], "markets": ["br", "us"], "budget": 600}
    plan = signals.plan_units(cfg)
    assert plan["competitors_units"] == 21
    # intercaladas por termo: a/br, x/us, b/br, y/us, c/br; cada busca = 1 chamada do balde de buscas + 2 leituras
    assert [(s["term"], s["market"]) for s in plan["searches"]] == [
        ("a", "br"), ("x", "us"), ("b", "br"), ("y", "us"), ("c", "br")]
    assert plan["keyword_units"] == 10 and plan["total"] == 31 and plan["pulados"] == [] and plan["cabe"]
    assert plan["search_calls"] == 5 and plan["search_limit"] == 100
    small = signals.plan_units({**cfg, "budget": 25})  # (25 − 21) // 2 = 2 buscas pelo orçamento
    assert len(small["searches"]) == 2 and small["pulados"] == ["b (BR)", "y (EUA)", "c (BR)"]
    assert small["cabe"] is False and small["pulados_motivo"] == "orçamento"
    dia = signals.plan_units(cfg, search_left=3)  # o medidor diz que só cabem 3 buscas hoje
    assert dia["search_calls"] == 3 and dia["pulados_motivo"] == "buscas do dia"
    sem_us = signals.plan_units({**cfg, "markets": ["us"], "keywords_us": []})  # sem termos em inglês: os mesmos
    assert [s["term"] for s in sem_us["searches"]] == ["a", "b", "c"]
    assert signals.plan_units(cfg, ["keywords", "competitors"])["total"] == 0


def test_api_com_token_entra_no_medidor_da_cota(monkeypatch):
    from studio.workers.publish import quota

    monkeypatch.setattr(yt, "access_token", lambda http, account: "token-teste")
    api = Api(Access(source="token do canal", account="conta"), 600, http=_client([]), custo_busca=0)
    assert api.medir
    api.lote(["search?part=snippet&q=x&regionCode=BR", "videos?part=snippet&id=c1"])
    assert api.unidades == 1 and api.buscas == 1
    usage = {r["method"]: r for r in quota.status()["by_method"]}
    assert usage["search.list"]["calls"] == 1 and usage["videos.list"]["units"] == 1
    assert quota.status()["used"] == 1 and quota.search_left() == quota.SEARCH_LIMIT - 1

    def esgotada(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"errors": [{"reason": "quotaExceeded"}]}})

    api = Api(Access(source="token do canal", account="conta"), 600,
              http=httpx.Client(transport=httpx.MockTransport(esgotada)), custo_busca=0)
    api.lote(["search?q=y"], strict=False)
    assert quota.search_left() == 0 and quota.status()["exhausted"] is False  # o balde das buscas não trava envios
    # chave própria: projeto que pode ser outro, nada entra no medidor
    assert not _api([]).medir


# ----------------------------------------------------------------------------- sinais


def test_outliers_de_concorrente_pela_mediana_dele_e_shorts_marcados():
    calls: list[str] = []
    res = signals.competitor_signals(_api(calls), [{"id": COMP, "name": "Vice Rival"}], days=90, agora=NOW)
    assert not any("search?" in c for c in calls)
    assert res["units"] == signals.competitor_units(1) == 3 and res["erro"] is None
    ids = [r["id"] for r in res["items"]]
    assert ids == ["c6", "s4"]  # c5 (9×) passou da janela de 90 dias; s5 só 1,5×
    c6, s4 = res["items"]
    assert c6["format"] == "long" and c6["med"] == 11000 and c6["mult"] == round(50000 / 11000, 2)
    assert s4["format"] == "short" and s4["med"] == 100000 and s4["mult"] == 4.0
    assert s4["url"] == "https://www.youtube.com/shorts/s4" and c6["url"] == "https://youtu.be/c6"
    summary = res["channels"][0]
    assert summary["med_long"] == 11000 and summary["med_short"] == 100000 and summary["subs"] == 50000

    missing = signals.competitor_signals(_api([]), [{"id": "UC" + "z" * 22}], days=90, agora=NOW)
    assert missing["items"] == [] and "não encontrado" in missing["erro"]


def test_sinais_de_palavra_chave_por_mercado():
    calls: list[str] = []
    searches = [{"term": "máfia brasileira", "market": "br"}, {"term": "cartel money", "market": "us"}]
    res = signals.keyword_signals(_api(calls), searches, days=90, agora=NOW)
    assert res["units"] == 202  # 2 buscas + 1 videos + 1 channels
    regions = [parse_qs(urlparse(c).query).get("regionCode") for c in calls if "/search?" in c]
    assert regions == [["BR"], ["US"]]
    search_q = parse_qs(urlparse(calls[0]).query)
    assert search_q["relevanceLanguage"] == ["pt"] and "videoDuration" not in search_q  # Shorts e longos juntos
    by_id = {r["id"]: r for r in res["items"]}
    assert set(by_id) == {"kbr1", "kus1", "kus2"}  # kbr2 em inglês na busca BR; kin de canal do sul da Ásia
    assert by_id["kbr1"]["market"] == "br" and by_id["kbr1"]["outlier"] and by_id["kbr1"]["ratio"] == 40.0
    assert by_id["kus2"]["format"] == "short" and by_id["kus2"]["outlier"] and by_id["kus2"]["market"] == "us"
    assert by_id["kus1"]["outlier"] is False and by_id["kus1"]["ratio"] == 0.4
    assert [r["id"] for r in res["items"]][-1] == "kus1"  # os fora da curva primeiro
    assert res["searches"] == [{"term": "máfia brasileira", "market": "br", "n": 3},
                               {"term": "cartel money", "market": "us", "n": 2}]


def test_sinais_proprios_separam_longos_e_shorts(monkeypatch):
    from studio.workers.analytics import variables

    def video(yid, fmt, vpd):
        return {"youtube_id": yid, "title": f"T {yid}", "format": fmt, "views_per_day": vpd,
                "metric": {"views": vpd * 10, "ctr": 5.0}, "published_at": None}

    vids = [video(f"l{i}", "long", v) for i, v in enumerate([100, 200, 300, 400, 500, 600, 700])]
    vids += [video(f"s{i}", "short", v) for i, v in enumerate([1000, 3000])]
    vids.append({"youtube_id": "x", "title": "sem métrica", "format": "long", "views_per_day": None})
    monkeypatch.setattr(variables, "load_videos", lambda channel_id=None: vids)
    out = signals.own_signals("qualquer")
    assert out["long"]["n"] == 7 and out["long"]["med"] == 400
    assert out["long"]["top"][0]["youtube_id"] == "l6" and out["long"]["top"][0]["mult"] == 1.75
    assert [r["youtube_id"] for r in out["long"]["bottom"]] == ["l0", "l1"]  # os 2 que sobram depois do top 5
    assert out["short"]["med"] == 2000 and out["short"]["top"][0]["url"] == "https://www.youtube.com/shorts/s1"


def test_pre_filtro_de_comentarios():
    cid = _channel()
    _comment(cid, "Faz um vídeo sobre o Banco Imperial", 10, 5)
    _comment(cid, "Como faz pra entrar nessa facção", 30, 3)
    _comment(cid, "please make a video about the Medellin cartel", 2, 1)
    _comment(cid, "parte 2 urgente", 7, 2)
    _comment(cid, "Muito bom o vídeo", 99, 1)  # elogio: fora
    _comment(cid, "Qual a fonte disso?", 50, 200)  # fora da janela
    res = signals.comment_signals(cid, days=90, now=NOW)
    assert [(c["text"][:12], c["kind"], c["likes"]) for c in res["items"]] == [
        ("Como faz pra", "pergunta", 30), ("Faz um vídeo", "pedido", 10), ("parte 2 urge", "pedido", 7),
        ("please make ", "pedido", 2)]
    assert res["items"][0]["url"].startswith("https://www.youtube.com/watch?v=vidown12345&lc=")
    assert signals.comment_kind("fala sobre o PCC") == "pedido"
    assert signals.comment_kind("next video on Escobar") == "pedido"
    assert signals.comment_kind("Que vídeo!") is None


# ----------------------------------------------------------------------------- checagem e pós-processamento


def test_check_contra_videos_feitos_e_banco():
    cid = _channel(youtube={"recent_videos": [
        {"title": "A história secreta do Banco Imperial", "video_id": "abcdefghijk"}]})
    with session_scope() as s:
        s.add(Idea(id="ide_existente", channel_id=cid, title="O Ouro de Serra Pelada", status="aprovada"))
    done, bank = check.done_titles(cid), check.open_ideas(cid)
    assert done == [{"title": "A história secreta do Banco Imperial", "url": "https://youtu.be/abcdefghijk"}]
    rep = check.check_idea({"title": "A história secreta do Banco Imperial!"}, done, bank)
    assert rep["status"] == "repetida" and rep["similar_to"]["url"] == "https://youtu.be/abcdefghijk"
    par = check.check_idea({"title": "Banco Imperial: a história secreta que ninguém contou"}, done, bank)
    assert par["status"] == "parecida" and 0.5 <= par["similarity"] < 0.75
    assert check.check_idea({"title": "o ouro de serra pelada"}, done, bank) == {
        "status": "ja_no_banco", "similar_to": {"title": "O Ouro de Serra Pelada", "url": ""}, "similarity": 1.0,
        "idea_id": "ide_existente"}
    # o mesmo título com as palavras em outra ordem também é a mesma ideia do banco
    reordenada = check.check_idea({"title": "Serra Pelada: o ouro"}, done, bank)
    assert reordenada["status"] == "ja_no_banco" and reordenada["idea_id"] == "ide_existente"
    assert check.check_idea({"title": "Como o dinheiro foi inventado"}, done, bank)["status"] == "nova"
    # títulos curtos do mesmo nicho, com assunto diferente, não colam
    assert check.check_idea({"title": "O Ouro de Carajás"}, done, bank)["status"] == "nova"


def _ctx(formats=("long", "short")):
    sig = {"competitors": {"items": [{"id": "c6", "title": "O cartel", "channel": "Rival", "views": 50000,
                                      "mult": 4.5, "format": "long", "url": "https://youtu.be/c6"}]},
           "keywords": {"items": [{"id": "kus2", "title": "The boss", "channel": "S", "views": 1, "ratio": 2.0,
                                   "term": "cartel money", "market": "us", "format": "short",
                                   "url": "https://www.youtube.com/shorts/kus2"}]},
           "comments": {"items": [{"id": "t1", "text": "faz um vídeo", "likes": 3, "kind": "pedido",
                                   "url": "https://www.youtube.com/watch?v=vidown12345&lc=x"}]}}
    return synthesis.build_context({"name": "Canal", "languages": ["pt-BR"]}, sig, formats=list(formats),
                                   markets=["br", "us"], done=[], open_ideas=[], today=TODAY)


def _idea(title, evidence, score=7.0, fmt="long", when=None):
    return {"rank": 1, "title": title, "format": fmt, "angle": "a", "why": "w", "evidence": evidence,
            "when": when or {"type": "data", "date": (TODAY + timedelta(days=5)).isoformat(), "until": "",
                             "note": ""},
            "needs": [], "score": score, "risk": {"level": "baixo", "note": ""}}


def test_pos_processamento_dos_refs_e_nota_sem_prova(monkeypatch):
    ctx = _ctx()
    assert set(ctx["refs"]) == {"C1", "K1", "M1"} and "C1" in synthesis.build_prompt(ctx)
    assert ctx["keywords"]["short"][0]["mercado"] == "us" and ctx["competitors"]["long"][0]["ref"] == "C1"
    data = {"ideas": [
        _idea("Com prova", [{"kind": "concorrente", "ref": "c1", "url": "https://inventado.com", "note": "n"},
                            {"kind": "busca", "ref": "Z9", "url": "", "note": "ref que não existe"}], score=6),
        _idea("Só link do YouTube fora dos sinais",
              [{"kind": "concorrente", "ref": "", "url": "https://youtu.be/AAAAAAAAAAA", "note": ""}], score=9),
        _idea("Pelo link do sinal", [{"kind": "web", "ref": "", "url": "https://www.youtube.com/shorts/kus2",
                                      "note": ""}], score=5, fmt="short"),
        _idea("Notícia", [{"kind": "web", "ref": "", "url": "https://g1.globo.com/ok", "note": ""},
                          {"kind": "web", "ref": "", "url": "https://exemplo.com/sumiu", "note": ""}], score=8,
              when={"type": "janela", "date": "2020-01-01", "until": "", "note": "passou"}),
        _idea("Short num canal só de longos", [], fmt="short"),
    ]}
    post = synthesis.postprocess(data, ctx["refs"], ["long", "short"], TODAY)
    first = post["ideas"][0]
    assert first["evidence"] == [{"kind": "concorrente", "ref": "C1", "url": "https://youtu.be/c6",
                                  "title": "O cartel", "note": "n", "checked": True, "term": ""}]
    assert first["refs_dropped"] == 1
    assert post["ideas"][1]["evidence"] == [] and post["ideas"][1]["refs_dropped"] == 1
    assert post["ideas"][2]["evidence"][0]["ref"] == "K1" and post["ideas"][2]["evidence"][0]["term"] == "cartel money"
    assert post["ideas"][3]["when"]["date"] == ""  # data no passado não vale

    only_long = synthesis.postprocess(data, ctx["refs"], ["long"], TODAY)
    assert [d["title"] for d in only_long["discarded"]] == ["Pelo link do sinal", "Short num canal só de longos"]

    # links de notícia: o que não abre sai; a ideia que fica sem nenhuma evidência perde 2 pontos
    monkeypatch.setattr(safe_http, "resolve", lambda host: ["93.184.216.34"])

    def web(request: httpx.Request) -> httpx.Response:
        if request.url.host == "g1.globo.com":
            return httpx.Response(200, headers={"content-type": "text/html"}, text="<html>ok</html>")
        return httpx.Response(404)

    ideas = post["ideas"][:4]
    removed = check.validate_urls(ideas, http=httpx.Client(transport=httpx.MockTransport(web)))
    assert removed == ["https://exemplo.com/sumiu"]
    assert [e["url"] for e in ideas[3]["evidence"]] == ["https://g1.globo.com/ok"]
    final = synthesis.finalize(ideas, TODAY)
    assert [(i["rank"], i["title"], i["score"]) for i in final] == [
        (1, "Notícia", 8.0), (2, "Só link do YouTube fora dos sinais", 7.0), (3, "Com prova", 6.0),
        (4, "Pelo link do sinal", 5.0)]
    sem_prova = final[1]
    assert sem_prova["no_proof"] and sem_prova["risk"]["note"] == synthesis.NO_PROOF_NOTE
    assert final[0]["when"]["date"] == (TODAY + timedelta(days=synthesis.FILL_DAYS)).isoformat()
    assert "data sugerida" in final[0]["when"]["note"]
    agenda = synthesis.agenda(final)
    assert [a["date"] for a in agenda] == sorted(a["date"] for a in agenda)


def test_schema_respeita_os_formatos_do_canal():
    sch = synthesis.schema(["long"])
    item = sch["properties"]["ideas"]["items"]
    assert item["properties"]["format"]["enum"] == ["long"]
    assert set(sch["required"]) == {"resumo", "temas", "ideas", "web_sources", "lacunas"}
    assert "date" in item["properties"]["when"]["required"]


# ----------------------------------------------------------------------------- rodada


def _research_channel(**extra) -> str:
    cid = _channel(youtube={"recent_videos": [
        {"title": "O cartel que comprou um país: o que ninguém contou", "video_id": "ownvideo001"}]})
    config.save_config(cid, competitors=[{"id": COMP, "name": "Vice Rival", "handle": "@rival"}],
                       keywords=["máfia brasileira"], keywords_us=["cartel money"], markets=["br", "us"],
                       formats=["long", "short"], **extra)
    _comment(cid, "Faz um vídeo sobre o PCC", 12, 4)
    with session_scope() as s:  # a ideia do Short do concorrente já está no banco
        s.add(Idea(id="ide_ja", channel_id=cid, title="O guarda que viu tudo: o que ninguém contou", status="nova"))
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    return cid


def test_rodada_grava_ideias_no_banco_com_origem_pesquisa():
    cid = _research_channel()
    calls: list[str] = []
    steps: list[str] = []
    out = service.run(cid, http=_client(calls), now=NOW, progress=lambda f, m: steps.append(m))
    # cota geral: 3 dos concorrentes + 1 videos + 1 channels das buscas; as 2 buscas contam no balde delas
    assert out["status"] == "ok" and out["units"] == 5 and out["searches"] == 2 and out["retry"] is False
    assert steps[:4] == ["Lendo o desempenho do canal", "Concorrentes", "Buscas", "Comentários"]
    run = service.get_run(out["run_id"])
    ideas = run["synthesis"]["ideas"]
    assert len(ideas) == 10  # 6 sinais + 4 de preenchimento (sem prova)
    status = {i["title"]: i["check"]["status"] for i in ideas}
    assert status["O cartel que comprou um país: o que ninguém contou"] == "repetida"
    assert status["O guarda que viu tudo: o que ninguém contou"] == "ja_no_banco"
    assert [i["rank"] for i in ideas] == list(range(1, 11))
    assert [i["score"] for i in ideas] == sorted((i["score"] for i in ideas), reverse=True)
    saved = [i for i in ideas if i["check"].get("saved")]
    assert len(saved) == out["saved"] == len(run["idea_ids"]) == 8
    with session_scope() as s:
        rows = {r.id: r for r in s.query(Idea).filter(Idea.id.in_(run["idea_ids"])).all()}
        assert {r.origin for r in rows.values()} == {"pesquisa"}
        kbr = next(r for r in rows.values() if r.title.startswith("Como a máfia"))
        assert kbr.search == "máfia brasileira" and kbr.format == "long"
        assert "https://youtu.be/kbr1" in kbr.evidence and "Quando: " in kbr.evidence
        rank = next(i["rank"] for i in ideas if i["title"] == kbr.title)
        assert kbr.evidence.endswith(f", ideia #{rank}") and "Pesquisa de vídeos de " in kbr.evidence
        assert run["id"] not in kbr.evidence and "(qualquer dia)" in kbr.evidence and "evergreen" not in kbr.evidence
        short = next(r for r in rows.values() if r.title.startswith("The cartel boss"))
        assert short.format == "short"
        filler = next(r for r in rows.values() if r.title.startswith("Ideia "))
        assert "sem prova" in filler.evidence
    assert all(i["check"]["idea_status"] == "nova" for i in saved)
    sig = run["signals"]
    assert {r["id"] for r in sig["competitors"]["items"]} == {"c6", "s4"}
    assert {r["market"] for r in sig["keywords"]["items"]} == {"br", "us"}
    assert len(sig["comments"]["items"]) == 1 and sig["falhas"] == []
    assert service.ANALYTICS_GAP in run["lacunas"]
    assert "síntese offline: sem notícias, datas nem tendências da web" in run["lacunas"]
    assert any(g.startswith("sem métricas dos vídeos do próprio canal") for g in run["lacunas"])
    agenda = run["synthesis"]["agenda"]
    assert len(agenda) == 9 and [a["date"] for a in agenda] == sorted(a["date"] for a in agenda)  # sem a repetida
    assert config.get_config(cid)["last_run_id"] == run["id"]

    md = service.report_markdown(run["id"])
    assert "## Agenda (por data de publicação)" in md and "## Lacunas" in md and "## Ranking (por nota)" in md
    assert "Shorts" in md and "repetida" in md

    # a repetida pode ir para o banco à mão
    rep = next(i for i in ideas if i["check"]["status"] == "repetida")
    res = service.to_ideas(run["id"], [rep["rank"]])
    assert len(res["created"]) == 1 and len(res["run"]["idea_ids"]) == 9
    assert len(res["run"]["synthesis"]["agenda"]) == 10  # salva à mão, entra na agenda
    assert service.to_ideas(run["id"], [rep["rank"]])["skipped"][0]["motivo"] == "já está no banco"

    # Hoje mostra as ideias novas da pesquisa e leva para Ideias, onde se decide
    expected = [{"kind": "research", "text": "9 ideias novas da pesquisa de vídeos do Lucas em Vice",
                 "where": f"/ideias?canal={cid}", "channel_id": cid, "project_id": None}]
    assert [w for w in today.waiting(NOW) if w["kind"] == "research"] == expected
    # uma rodada nova sem ideias não esconde as da anterior que ainda esperam decisão
    later = NOW + timedelta(hours=7)
    assert service.run(cid, http=_client([]), now=later, skip=["synthesis"])["status"] == "parcial"
    assert [w for w in today.waiting(later) if w["kind"] == "research"] == expected
    # a mesma ideia reescrita na rodada seguinte não volta ao banco como nova
    second = service.get_run(service.last_run(cid)["id"])
    assert second["idea_ids"] == []


def test_rodada_parcial_sem_web_e_sem_acesso(monkeypatch):
    cid = _channel()
    config.save_config(cid, competitors=[{"id": COMP}], keywords=["máfia"])
    _comment(cid, "Qual o nome da música?", 3, 2)
    monkeypatch.setattr(synthesis, "web_ready", lambda: (None, "busca web desligada (VS_FACTCHECK_WEB=0)"))
    out = service.run(cid, now=NOW)
    assert out["status"] == "parcial" and out["ideas"] == 0 and out["saved"] == 0 and out["units"] == 0
    run = service.get_run(out["run_id"])
    assert run["lacunas"][0].startswith(synthesis.UNAVAILABLE_MESSAGE)
    assert any("sem acesso à YouTube Data API" in g for g in run["lacunas"])
    assert run["signals"]["comments"]["items"][0]["kind"] == "pergunta"
    assert "competitors" not in run["signals"]
    with session_scope() as s:
        assert s.query(Idea).count() == 0

    # limite do plano Claude: a rodada fecha parcial com os sinais
    monkeypatch.setattr(synthesis, "web_ready", lambda: (["mock_llm"], ""))

    def limite(ctx, job_id=None):
        raise TransientError("usage limit reached")

    monkeypatch.setattr(synthesis, "synthesize", limite)
    out = service.run(cid, now=NOW)
    run = service.get_run(out["run_id"])
    assert run["status"] == "parcial" and run["lacunas"][0].startswith("a síntese falhou: usage limit")
    assert out["retry"] is True  # erro passageiro: a rotina tenta de novo

    # a síntese rodou e não sobrou ideia: "ok" (não "parcial"), com a lacuna dizendo isso
    monkeypatch.setattr(synthesis, "synthesize",
                        lambda ctx, job_id=None: {"tokens": 10, "api_equiv_usd": 0.01, "data": {"ideas": []}})
    out = service.run(cid, now=NOW)
    run = service.get_run(out["run_id"])
    assert out["status"] == "ok" and out["ideas"] == 0 and out["retry"] is False
    assert run["lacunas"][0] == "a síntese rodou, mas não devolveu ideias"


def test_cancelamento_antes_do_claude_guarda_os_sinais(monkeypatch):
    cid = _research_channel()
    with pytest.raises(BuildCancelled):
        service.run(cid, http=_client([]), now=NOW, cancelled=lambda: True)
    with session_scope() as s:
        row = s.query(ResearchRun).one()
        assert row.status == "cancelada" and row.idea_ids == [] and row.signals.get("competitors")
        assert s.query(Idea).filter(Idea.origin == "pesquisa").count() == 0
    # a rodada cancelada conta para a espera entre rodadas manuais
    assert service.next_manual_at(cid, NOW) is not None


def test_cancelamento_depois_do_claude_guarda_a_sintese_sem_gravar_ideias(monkeypatch):
    cid = _research_channel()
    flag = {"stop": False}
    real = synthesis.synthesize

    def pago(ctx, job_id=None):
        out = real(ctx, job_id=job_id)
        flag["stop"] = True  # o clique em Cancelar chegou durante a síntese
        return out

    monkeypatch.setattr(synthesis, "synthesize", pago)
    with pytest.raises(BuildCancelled):
        service.run(cid, http=_client([]), now=NOW, cancelled=lambda: flag["stop"])
    run = service.get_run(service.last_run(cid)["id"])
    assert run["status"] == "cancelada" and run["idea_ids"] == [] and len(run["synthesis"]["ideas"]) == 10
    assert run["lacunas"][0].startswith("pesquisa cancelada")
    with session_scope() as s:
        assert s.query(Idea).filter(Idea.origin == "pesquisa").count() == 0
    nova = next(i for i in run["synthesis"]["ideas"] if i["check"]["status"] == "nova")
    assert len(service.to_ideas(run["id"], [nova["rank"]])["created"]) == 1  # dá para salvar à mão


def test_erro_depois_da_coleta_fica_no_historico(monkeypatch):
    cid = _research_channel()

    def quebra(*a, **k):
        raise RuntimeError("resposta estranha")

    monkeypatch.setattr(synthesis, "postprocess", quebra)
    with pytest.raises(RuntimeError):
        service.run(cid, http=_client([]), now=NOW)
    run = service.last_run(cid)
    assert run["status"] == "falhou" and run["error"] == "resposta estranha" and run["units"] == 5
    assert "Erro: resposta estranha" in service.report_markdown(run["id"])


def test_job_semanal_marca_a_semana(monkeypatch):
    cid = _research_channel()
    monkeypatch.setattr(yt, "new_client", lambda timeout=60: _client([]))
    job = jobs.get_runner().submit("video_research", {"channel_id": cid, "weekly": "2026-W47"}, run_inline=True)
    with session_scope() as s:
        row = s.get(Job, job["id"])
        assert row.status == "done", row.error
        assert row.result["saved"] == 8 and row.result["status"] == "ok"
        # o aviso fica só em Hoje (calculado na hora): nenhuma pendência que precise ser fechada à mão
        assert s.query(PendingTask).filter(PendingTask.auto_key.like("pesquisa:%")).count() == 0
    cfg = config.get_config(cid)
    assert cfg["last_week"] == "2026-W47" and cfg["last_job"] == job["id"]


def test_job_semanal_nao_marca_a_semana_com_erro_passageiro_do_claude(monkeypatch):
    cid = _research_channel()
    monkeypatch.setattr(yt, "new_client", lambda timeout=60: _client([]))

    def limite(ctx, job_id=None):
        raise TransientError("usage limit reached")

    monkeypatch.setattr(synthesis, "synthesize", limite)
    job = jobs.get_runner().submit("video_research", {"channel_id": cid, "weekly": "2026-W47"}, run_inline=True)
    with session_scope() as s:
        assert s.get(Job, job["id"]).result["status"] == "parcial"
    assert config.get_config(cid)["last_week"] is None  # a rotina tenta de novo numa manhã seguinte


# ----------------------------------------------------------------------------- configuração


def test_config_padroes_e_validacao():
    cid = _channel(youtube={"keywords": ["máfia", "crime", "cartel", "PCC", "facção", "tráfico"]})
    cfg = config.get_config(cid)
    assert cfg["markets"] == ["br"] and cfg["budget"] == 600 and cfg["days"] == 90 and cfg["auto"] is False
    assert cfg["keywords"] == ["máfia", "crime", "cartel", "PCC", "facção"] and cfg["keywords_default"]
    with pytest.raises(config.ResearchError, match="um termo por linha"):
        config.save_config(cid, keywords=["máfia | crime"])
    with pytest.raises(config.ResearchError, match="no máximo 8"):
        config.save_config(cid, keywords=[f"t{i}" for i in range(9)])
    with pytest.raises(config.ResearchError):
        config.save_config(cid, markets=["br", "xx"])
    with pytest.raises(config.ResearchError):
        config.save_config(cid, competitors=[{"id": "@canal"}])
    cfg = config.save_config(cid, markets=["us", "br"], keywords=["máfia", "Máfia", " crime  organizado "])
    assert cfg["markets"] == ["br", "us"] and cfg["keywords"] == ["máfia", "crime organizado"]
    assert not cfg["keywords_default"]
    assert config.channel_formats(cid) == ["long", "short"]  # nada indica: os dois
    with session_scope() as s:
        s.get(Channel, cid).min_stock = 3  # estoque mínimo de Shorts (stock_format padrão)
    assert config.channel_formats(cid) == ["short"]
    assert config.channel_formats(cid, {**cfg, "formats": ["long"]}) == ["long"]


# ----------------------------------------------------------------------------- rotina semanal


def _wait_jobs() -> None:
    for _ in range(250):
        with session_scope() as s:
            if not s.query(Job).filter(Job.status.in_(("queued", "running"))).count():
                return
        time.sleep(0.02)
    raise AssertionError("job não terminou")


def test_rotina_um_canal_por_manha_e_pulo_com_15_ideias(monkeypatch):
    assert ("pesquisa", routines.step_research) in routines.STEPS
    submitted: list[dict] = []

    def fake(ctx, payload):
        submitted.append(payload)
        config.update_state(payload["channel_id"], last_week=payload["weekly"])
        return {}

    monkeypatch.setitem(jobs._REGISTRY, "video_research", fake)
    a, b, c = _channel("A canal"), _channel("B canal"), _channel("C canal")
    assert routines.step_research(NOW) == []  # ninguém com "Rodar toda semana"
    config.save_config(a, auto=True)
    config.save_config(b, auto=True)
    lines = routines.step_research(NOW)
    assert len(lines) == 1 and lines[0].startswith("pesquisa de vídeos: rodando para A canal")
    _wait_jobs()
    lines = routines.step_research(NOW)
    assert lines[0].startswith("pesquisa de vídeos: rodando para B canal")
    _wait_jobs()
    assert routines.step_research(NOW) == []  # os dois já rodaram nesta semana
    assert [p["channel_id"] for p in submitted] == [a, b]

    with session_scope() as s:
        for i in range(15):
            s.add(Idea(id=new_id("ide"), channel_id=a, title=f"Ideia {i}", status="nova"))
        # job do canal B já na fila (clique na página): a rotina não duplica
        s.add(Job(id=new_id("job"), kind="video_research", status="queued", payload={"channel_id": b}))
    config.save_config(c, auto=True)
    next_week = NOW + timedelta(days=7)
    lines = routines.step_research(next_week)
    # uma pesquisa por vez: com o job de B na fila, nenhuma entra hoje (A só é marcada como pulada)
    assert lines == ["pesquisa de vídeos do A canal pulada: 15 ideias novas esperando você"]
    with session_scope() as s:
        s.query(Job).filter(Job.status == "queued").update({"status": "done"})
    lines = routines.step_research(next_week)
    assert len(lines) == 1 and lines[0].startswith("pesquisa de vídeos: rodando para B canal")
    _wait_jobs()
    assert routines.step_research(next_week)[0].startswith("pesquisa de vídeos: rodando para C canal")
    _wait_jobs()
    assert routines.step_research(next_week) == []  # A pulada; B e C já foram


def test_rotina_canal_que_falha_nao_trava_os_outros(monkeypatch):
    submitted: list[str] = []

    def fake(ctx, payload):
        submitted.append(payload["channel_id"])
        if payload["channel_id"] == a:
            raise RuntimeError("quebrou")  # A falha sempre: nunca marca a semana
        config.update_state(payload["channel_id"], last_week=payload["weekly"])
        return {}

    monkeypatch.setitem(jobs._REGISTRY, "video_research", fake)
    a, b = _channel("A canal"), _channel("B canal")
    config.save_config(a, auto=True)
    config.save_config(b, auto=True)
    for day in range(4):  # quatro manhãs da mesma semana
        routines.step_research(NOW + timedelta(days=day, hours=-day))
        _wait_jobs()
    # A tenta, B passa na frente na manhã seguinte, A tenta a 2ª vez e então a semana dele é encerrada
    assert submitted == [a, b, a]
    assert config.get_config(a)["last_week"] == config.get_config(b)["last_week"]


def test_rotina_espera_a_rodada_manual_recente(monkeypatch):
    monkeypatch.setitem(jobs._REGISTRY, "video_research", lambda ctx, payload: {})
    a = _channel("A canal")
    config.save_config(a, auto=True)
    with session_scope() as s:
        s.add(ResearchRun(id=new_id("rsr"), channel_id=a, created_at=NOW - timedelta(hours=1), status="parcial"))
    assert routines.step_research(NOW) == []
    assert routines.step_research(NOW + timedelta(hours=6))[0].startswith("pesquisa de vídeos: rodando para A")
    _wait_jobs()


# ----------------------------------------------------------------------------- rotas


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_rotas(client, monkeypatch):
    cid = _channel()
    assert client.get("/api/research", params={"channel_id": "nao-existe"}).status_code == 404
    st = client.get("/api/research", params={"channel_id": cid}).json()
    assert st["config"]["markets"] == ["br"] and st["last_run"] is None and st["active_job"] is None
    assert st["access"]["ok"] is False and st["formats"] == ["long", "short"]

    bad = client.put("/api/research/config", json={"channel_id": cid, "keywords": ["a | b"]})
    assert bad.status_code == 422 and "um termo por linha" in bad.json()["detail"]
    assert client.put("/api/research/config", json={"channel_id": cid, "budget": 50}).status_code == 422
    assert client.put("/api/research/config", json={"channel_id": cid, "markets": ["xx"]}).status_code == 422
    saved = client.put("/api/research/config", json={"channel_id": cid, "keywords": ["máfia"], "markets": ["br", "us"],
                                                     "competitors": [{"id": COMP, "name": "Rival"}]}).json()
    assert saved["markets"] == ["br", "us"] and saved["competitors"][0]["id"] == COMP
    sim = client.post("/api/research/simulate", json={"channel_id": cid}).json()
    assert sim["competitors_units"] == 3 and [s["market"] for s in sim["searches"]] == ["br", "us"]

    monkeypatch.setattr(synthesis, "web_ready", lambda: (None, "sem web"))
    no = client.post("/api/research/run", json={"channel_id": cid})
    assert no.status_code == 422 and "nem busca na web" in no.json()["detail"]

    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    monkeypatch.setitem(jobs._REGISTRY, "video_research", lambda ctx, payload: {})
    job = client.post("/api/research/run", json={"channel_id": cid, "skip": ["comments"]}).json()
    assert job["kind"] == "video_research" and job["payload"] == {"channel_id": cid, "skip": ["comments"]}
    _wait_jobs()
    with session_scope() as s:
        s.add(Job(id=new_id("job"), kind="video_research", status="running", payload={"channel_id": cid}))
    dup = client.post("/api/research/run", json={"channel_id": cid})
    assert dup.status_code == 409 and "em andamento" in dup.json()["detail"]
    assert client.get("/api/research", params={"channel_id": cid}).json()["active_job"]["status"] == "running"
    other = _channel("Outro")  # job ativo de outro canal não trava este
    assert client.post("/api/research/run", json={"channel_id": other}).status_code == 200
    with session_scope() as s:
        s.query(Job).filter(Job.status == "running", Job.started_at.is_(None)).update({"status": "failed"})
    _wait_jobs()
    with session_scope() as s:
        s.add(ResearchRun(id=new_id("rsr"), channel_id=cid, created_at=datetime.now(UTC) - timedelta(hours=2),
                          status="ok"))
    cool = client.post("/api/research/run", json={"channel_id": cid})
    assert cool.status_code == 409 and "A próxima pesquisa manual deste canal libera às" in cool.json()["detail"]
    assert client.get("/api/research", params={"channel_id": cid}).json()["next_manual_at"]

    assert client.get("/api/research/runs/nao-existe").status_code == 404
    runs = client.get("/api/research/runs", params={"channel_id": cid}).json()
    assert len(runs) == 1 and runs[0]["n_ideas"] == 0
    rep = client.get(f"/api/research/runs/{runs[0]['id']}/report").json()
    assert rep["markdown"].startswith("# Pesquisa de vídeos: Lucas em Vice") and rep["filename"].endswith(".md")
    dl = client.get(f"/api/research/runs/{runs[0]['id']}/report", params={"download": True})
    assert dl.headers["content-type"].startswith("text/markdown") and "attachment" in dl.headers["content-disposition"]
    assert client.post("/api/research/runs/nao-existe/ideas", json={"ranks": [1]}).status_code == 404


def test_resolver_concorrente_sem_cota():
    page = ('<html><link rel="canonical" href="https://www.youtube.com/channel/' + COMP + '">'
            '<meta property="og:title" content="Vice Rival"></html>')
    feed = ('<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:yt="http://www.youtube.com/xml/schemas/2015"><title>Vice Rival</title></feed>')

    def handler(request: httpx.Request) -> httpx.Response:
        assert "googleapis" not in str(request.url)
        if request.url.path.startswith("/feeds"):
            return httpx.Response(200, text=feed)
        return httpx.Response(200, text=page)

    out = config.resolve_competitor("https://www.youtube.com/@rival", http=httpx.Client(
        transport=httpx.MockTransport(handler)))
    assert out == {"id": COMP, "name": "Vice Rival", "handle": "@rival", "url": "https://www.youtube.com/@rival",
                   "subs": None}
    with pytest.raises(config.ResearchError):
        config.resolve_competitor("https://youtu.be/abcdefghijk")


def test_web_tasks_inclui_a_pesquisa():
    from studio.providers.llm import WEB_TASKS

    assert "video_ideas" in WEB_TASKS
    assert date.fromisoformat(_ctx()["today"]) == TODAY
