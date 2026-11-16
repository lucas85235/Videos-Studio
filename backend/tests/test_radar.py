"""Radar de nichos e vídeos fora da curva (ADR 0003, bloco E). Sem rede: a YouTube Data API é simulada com
`httpx.MockTransport` (via `youtube.new_client`)."""

from __future__ import annotations

import copy
import json
import time
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Channel, Idea, Job, PendingTask, RadarChannel, RadarNiche, RadarRun, RadarVideo
from studio.providers import credentials
from studio.workers import jobs
from studio.workers.ops import routines
from studio.workers.publish import youtube as yt
from studio.workers.radar import access, analysis, catalog, collect, ideas, panel

NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _sem_chave_do_ambiente(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)


def _iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cfg(**radar) -> dict:
    base = catalog.default_catalog()
    base["radar"].update(radar)
    base["nichos"] = [
        {
            "id": "dinheiro",
            "nome": "História do dinheiro",
            "cat": "Business & Finance",
            "duracao": "medium",
            "en": ["money history"],
            "pt": ["história do dinheiro"],
        }
    ]
    return base


# ----------------------------------------------------------------------------- análise pura


def test_idioma_ok():
    pt = {"title": "Como o dinheiro foi inventado"}
    en = {"title": "How the money was invented"}
    assert analysis.idioma_ok(pt, "br") and not analysis.idioma_ok(pt, "us")
    assert analysis.idioma_ok(en, "us") and not analysis.idioma_ok(en, "br")
    acento = {"title": "Inflação"}  # nenhuma palavra das listas: o acento conta 2 para o português
    assert analysis.idioma_ok(acento, "br") and not analysis.idioma_ok(acento, "us")
    outra = {"title": "История денег", "defaultAudioLanguage": "en"}
    assert not analysis.idioma_ok(outra, "us") and not analysis.idioma_ok(outra, "br")
    declarado = {"title": "How the money was invented", "defaultAudioLanguage": "pt-BR"}
    assert analysis.idioma_ok(declarado, "br") and not analysis.idioma_ok(declarado, "us")


def test_alertas():
    cfg = {"radar": {"alertas": [["bodycam", "Bodycam: imagens de terceiros"]]}}
    canais = {
        "UCin": {"nome": "Money Facts", "pais": "IN"},
        "UCnome": {"nome": "Bodycam Daily", "pais": "US"},
        "UCmetade": {"nome": "Police Stories", "pais": "US"},
        "UCpouco": {"nome": "Crime Files", "pais": "US"},
    }
    linhas = [
        {"canal": "UCmetade", "titulo": "Bodycam shows the arrest"},
        {"canal": "UCmetade", "titulo": "The trial of the year"},
        {"canal": "UCpouco", "titulo": "Bodycam shows the arrest"},
        {"canal": "UCpouco", "titulo": "Case one"},
        {"canal": "UCpouco", "titulo": "Case two"},
    ]
    analysis.alertas(cfg, linhas, canais)
    assert canais["UCin"]["alerta"] == (
        "Canal do sul da Ásia (país IN): público e RPM diferentes dos mercados-alvo"
    )
    assert canais["UCnome"]["alerta"] == "Bodycam: imagens de terceiros"
    assert canais["UCmetade"]["alerta"] == "Bodycam: imagens de terceiros"  # 2 × 1 >= 2
    assert canais["UCpouco"]["alerta"] is None  # 2 × 1 < 3


def test_agregar_com_numeros_conferidos_a_mao():
    cfg = {"radar": {"hit_us": 100000, "novo_meses": 12}}
    canais = {
        "A": {"idade_m": 5.0, "inscritos": 1000},
        "B": {"idade_m": 30.0, "inscritos": 50000},
        "C": {"idade_m": None, "inscritos": None},  # idade desconhecida conta 999; inscritos escondidos
        "D": {"idade_m": 1.0, "inscritos": 10, "alerta": "fora"},
    }
    linhas = [
        {"nicho": "n", "mercado": "us", "canal": "A", "views": 200000, "dur": 600},
        {"nicho": "n", "mercado": "us", "canal": "A", "views": 20000, "dur": 900},
        {"nicho": "n", "mercado": "us", "canal": "B", "views": 600000, "dur": 1200},
        {"nicho": "n", "mercado": "us", "canal": "C", "views": 5000, "dur": 300},
        {"nicho": "n", "mercado": "us", "canal": "D", "views": 9000000, "dur": 600},  # com alerta: fora
    ]
    out = analysis.agregar(cfg, linhas, canais, [("n", "us"), ("vazio", "br")])
    a = out[("n", "us")]
    assert a["n"] == 4
    assert a["med"] == 110000.0  # (20.000 + 200.000) / 2
    assert a["p75"] == 300000.0  # 200.000 + (600.000 − 200.000) × 0,25
    assert (a["h100"], a["h500"], a["n10k"], a["best"]) == (2, 1, 3, 600000)
    assert a["new_share"] == 0.5 and a["new_hit"] == 1 and a["best_new"] == 200000
    assert a["subs_med"] == 25500.0  # mediana de 1.000 e 50.000 (C esconde)
    assert a["length_med"] == 12.5  # minutos: 10, 15, 20, 5
    assert out[("vazio", "br")]["n"] == 0 and out[("vazio", "br")]["med"] == 0.0


def _item(vid: str, views: int | None, days: float, dur: str = "PT10M", **snippet) -> dict:
    st = {"viewCount": str(views)} if views is not None else {}
    return {
        "id": vid,
        "snippet": {"publishedAt": _iso(days), "title": f"Vídeo {vid}", **snippet},
        "statistics": st,
        "contentDetails": {"duration": dur},
    }


def test_recentes_nos_tres_vereditos_e_sem_veredito():
    hit = 100000
    modelar = [
        _item("f", 999999, 3),
        _item("a", 200000, 8),
        _item("b", 150000, 10),
        _item("c", 50000, 15),
        _item("d", 120000, 20),
    ]
    r = analysis.recentes(modelar, hit, NOW)
    assert r["verdict"] == "Modelar" and (r["hits"], r["settled"], r["up30"]) == (3, 4, 5)
    assert r["format"] == "10 min · 5 vídeos em 30 dias · 3/4 com 100 mil+"
    assert r["risk"] == "Formato que se repete: estude a estrutura, não copie os vídeos."
    assert [x["id"] for x in r["latest"]] == ["f", "a", "b", "c", "d"]  # do mais novo ao mais antigo

    observar = [_item(str(i), v, 10 + i) for i, v in enumerate([200000, 150000, 10000, 10000, 10000])]
    r = analysis.recentes(observar, hit, NOW)
    assert (
        r["verdict"] == "Observar"
        and r["risk"] == "Mais de um hit, mas ainda irregular: acompanhe por mais semanas."
    )

    tres_hits = [_item(str(i), 500000, 10 + i) for i in range(3)]  # 3 assentados: pouco para Modelar
    assert analysis.recentes(tres_hits, hit, NOW)["verdict"] == "Observar"

    viral = [_item("a", 900000, 10), _item("b", 1000, 12), _item("c", None, 14)]
    r = analysis.recentes(viral, hit, NOW)
    assert r["verdict"] == "Estudar o viral" and r["settled"] == 2
    assert r["risk"] == "Um hit isolado: entenda por que funcionou antes de apostar no formato."

    nada = analysis.recentes([_item("a", 1000, 10), _item("b", 2000, 40, "PT1M")], hit, NOW)
    assert "verdict" not in nada and "format" not in nada and nada["up30"] == 1 and nada["typLen"] == 10.0


def test_arbitragem():
    cfg = {
        "nichos": [
            {"id": "a", "nome": "A"},
            {"id": "b", "nome": "B"},
            {"id": "c", "nome": "C"},
            {"id": "so-us", "nome": "Só EUA"},
        ]
    }

    def us(med):
        return {"med": med, "h100": 1, "new_hit": 1, "n": 10}

    def br(n10k):
        return {"med": 1000, "n10k": n10k, "best": 5000, "new_share": 0.1, "n": 20}

    nichos = {
        ("a", "us"): us(250000),
        ("a", "br"): br(3),
        ("b", "us"): us(500000),
        ("b", "br"): br(15),
        ("c", "us"): us(50000),
        ("c", "br"): br(6),
        ("so-us", "us"): us(1),
    }
    out = analysis.arbitragem(nichos, cfg)
    by = {x["id"]: x for x in out}
    assert by["a"]["leitura"] == "Aberto em PT" and by["a"]["score"] == 45.9
    assert by["b"]["leitura"] == "Concorrido em PT" and by["b"]["score"] == round(5.69897 * 0.25 * 10, 1)
    assert by["c"]["leitura"] == "Morno"
    assert "so-us" not in by and [x["id"] for x in out][0] == "a"


def test_fmt_e_utilidades():
    assert analysis.fmt(1_200_000) == "1,2 mi" and analysis.fmt(2_000_000) == "2 mi"
    assert analysis.fmt(350_000) == "350 mil" and analysis.fmt(999) == "999" and analysis.fmt(None) == "-"
    assert analysis.segundos_iso("PT1H2M3S") == 3723 and analysis.segundos_iso("P1DT1S") == 86401
    assert analysis.quantil([1, 2, 3, 4], 0.75) == 3.25


# ----------------------------------------------------------------------------- catálogo e plano


def test_catalogo_padrao_e_validacao():
    cfg = catalog.load_catalog()
    assert len(cfg["nichos"]) == 15
    assert (
        cfg["radar"]["orcamento"] == 3500
        and cfg["radar"]["hit_br"] == 50000
        and cfg["radar"]["candidatos"] == 40
    )
    assert cfg["radar"]["alertas"][0][0] == "bodycam" and cfg["rpm"]["Education & Science"]["rpm"] == 10.22

    bad = copy.deepcopy(cfg)
    bad["nichos"][0]["en"] = ["history | documentary"]
    bad["nichos"][1]["id"] = bad["nichos"][2]["id"]
    bad["nichos"][3]["duracao"] = "short"
    bad["nichos"][4]["pt"] = []
    with pytest.raises(catalog.CatalogError) as err:
        catalog.save_catalog(bad)
    text = str(err.value)
    assert "não use | (OR): a API devolve vídeos minúsculos; um termo por linha" in text
    assert "id repetido" in text and "duracao" in text and "falta a busca 'pt'" in text

    sem_padroes = {
        "radar": {},
        "nichos": [{"id": "x", "nome": "X", "cat": "Gaming", "en": ["a"], "pt": ["b"]}],
    }
    saved = catalog.save_catalog(sem_padroes)
    assert (
        saved["radar"]["orcamento"] == 3500
        and saved["radar"]["dias"] == 30
        and saved["radar"]["novo_meses"] == 12
    )
    assert saved["nichos"][0]["duracao"] == "medium"
    with pytest.raises(catalog.CatalogError) as err:
        catalog.save_catalog({**sem_padroes, "radar": {"orcamento": 200, "candidatos": 40}})
    assert "orcamento: precisa ser 240 ou mais" in str(err.value)
    assert catalog.load_catalog()["nichos"][0]["id"] == "x" and catalog.is_custom()
    assert len(catalog.reset_catalog()["nichos"]) == 15 and not catalog.is_custom()


def test_plan_quinze_nichos_dois_mercados_dois_termos():
    cfg = catalog.default_catalog()
    p = collect.plan(cfg, orcamento=3500, agora=NOW)
    assert p["reserva"] == 140 and p["cabem"] == 33 and len(p["feitos"]) == 33 and p["pulados"] == []
    qs = [parse_qs(urlparse(c).query) for _, c in p["feitos"]]
    primeiros = {
        (n["id"], mk): n["en" if mk == "us" else "pt"][0] for n in cfg["nichos"] for mk in ("us", "br")
    }
    # rodízio por termo: o 1º termo de todos os 30 pares nicho/mercado, depois o 2º
    assert [q["q"][0] for q in qs[:30]] == [primeiros[k] for k, _ in p["feitos"][:30]]
    assert [k for k, _ in p["feitos"][30:]] == [
        ("historia", "us"),
        ("historia", "br"),
        ("historia-dinheiro", "us"),
    ]
    assert qs[30]["q"] == ["ancient history documentary"] and qs[31]["q"] == ["história antiga documentário"]
    q0 = qs[0]
    assert q0 == {
        "part": ["snippet"],
        "type": ["video"],
        "order": ["viewCount"],
        "maxResults": ["50"],
        "publishedAfter": ["2026-10-16T12:00:00Z"],
        "q": ["history documentary"],
        "regionCode": ["US"],
        "relevanceLanguage": ["en"],
        "videoDuration": ["long"],
        "safeSearch": ["none"],
    }
    assert p["feitos"][0][1].startswith("search?")
    assert parse_qs(urlparse(p["feitos"][1][1]).query)["regionCode"] == ["BR"]

    pouco = collect.plan(cfg, orcamento=1000, agora=NOW)
    assert len(pouco["feitos"]) == 8 and len(pouco["pulados"]) == 22 and "games/br" in pouco["pulados"]

    with pytest.raises(collect.RadarError) as err:  # 140 de reserva: 200 não cobre a busca de 100
        collect.plan(cfg, orcamento=200, agora=NOW)
    assert "use pelo menos 240" in str(err.value)

    sim = collect.simulate(cfg, orcamento=3500, agora=NOW)
    assert sim["buscas"] == 33 and sim["unidades_previstas"] == 3440 and sim["orcamento"] == 3500
    assert sim["pulados"] == [] and sim["exemplo"].startswith("search?")
    with pytest.raises(collect.RadarError):
        collect.plan(cfg, ["nao-existe"])


# ----------------------------------------------------------------------------- coleta simulada

VIDEOS = {
    "vUS1": _item(
        "vUS1",
        300000,
        10,
        "PT12M",
        channelId="UCnew1",
        title="How the empire lost all its money",
        defaultAudioLanguage="en",
    ),
    "vUS2": _item("vUS2", 50000, 5, "PT20M", channelId="UCold1", title="Why the dollar is dying"),
    "vUS3": _item("vUS3", 900000, 5, "PT2M", channelId="UCold1", title="The money short"),  # Short: fora
    "vBR1": _item("vBR1", 80000, 3, "PT15M", channelId="UCbr1", title="Como o dinheiro foi inventado"),
    "vBR2": _item("vBR2", 1000000, 4, "PT10M", channelId="UCin1", title="Como ganhar dinheiro de verdade"),
    "r1": _item("r1", 200000, 8, channelId="UCnew1"),
    "r2": _item("r2", 150000, 12, channelId="UCnew1"),
    "r3": _item("r3", 120000, 16, channelId="UCnew1"),
    "r4": _item("r4", 50000, 20, channelId="UCnew1"),
}
CHANNELS = {
    "UCnew1": {
        "id": "UCnew1",
        "snippet": {
            "title": "Empire Money",
            "publishedAt": _iso(150),
            "country": "US",
            "customUrl": "@empiremoney",
        },
        "statistics": {"subscriberCount": "20000", "viewCount": "1500000", "videoCount": "12"},
    },
    "UCold1": {
        "id": "UCold1",
        "snippet": {"title": "Dollar Daily", "publishedAt": "2015-01-01T00:00:00Z"},
        "statistics": {"subscriberCount": "900000", "viewCount": "90000000", "videoCount": "800"},
    },
    "UCbr1": {
        "id": "UCbr1",
        "snippet": {"title": "Grana Antiga", "publishedAt": _iso(300), "country": "BR"},
        "statistics": {"subscriberCount": "4000", "viewCount": "200000", "videoCount": "9"},
    },
    "UCin1": {
        "id": "UCin1",
        "snippet": {"title": "Dinheiro Já", "publishedAt": _iso(60), "country": "IN"},
        "statistics": {"hiddenSubscriberCount": True, "viewCount": "5000000", "videoCount": "40"},
    },
}
PLAYLISTS = {"UUnew1": ["vUS1", "r1", "r2", "r3", "r4"], "UUbr1": ["vBR1"]}


def _api(calls: list[str], *, fail: bool = False, key: str | None = "chave-teste"):
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(yt.TOKEN_URL):
            return httpx.Response(200, json={"access_token": "tok"})
        calls.append(url)
        q = parse_qs(urlparse(url).query)
        if key:
            assert q["key"] == [key]
        else:
            assert request.headers["Authorization"] == "Bearer tok"
        if fail:
            return httpx.Response(
                403,
                json={"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}},
            )
        path = urlparse(url).path.rsplit("/", 1)[-1]
        if path == "search":
            ids = ["vUS1", "vUS2", "vUS3"] if q["regionCode"] == ["US"] else ["vBR1", "vBR2"]
            return httpx.Response(200, json={"items": [{"id": {"videoId": i}} for i in ids]})
        if path == "videos":
            ids = q["id"][0].split(",")
            return httpx.Response(200, json={"items": [VIDEOS[i] for i in ids if i in VIDEOS]})
        if path == "channels":
            ids = q["id"][0].split(",")
            return httpx.Response(200, json={"items": [CHANNELS[i] for i in ids if i in CHANNELS]})
        if path == "playlistItems":
            vids = PLAYLISTS.get(q["playlistId"][0], [])
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": v}} for v in vids]})
        return httpx.Response(404, json={})

    return handler


def _mock(monkeypatch, handler) -> None:
    monkeypatch.setattr(
        yt, "new_client", lambda timeout=60: httpx.Client(transport=httpx.MockTransport(handler))
    )


def _collect_ok(monkeypatch, **radar) -> dict:
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    catalog.save_catalog(_cfg(**radar))  # o painel e as ideias leem o catálogo salvo
    calls: list[str] = []
    _mock(monkeypatch, _api(calls))
    result = collect.collect(agora=NOW)
    result["_calls"] = calls
    return result


def test_coleta_simulada_ponta_a_ponta(monkeypatch):
    steps: list[str] = []
    catalog.save_catalog(_cfg())  # o painel lê o catálogo salvo
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    calls: list[str] = []
    _mock(monkeypatch, _api(calls))
    r = collect.collect(cfg=_cfg(), agora=NOW, progress=lambda f, m: steps.append(m))
    # 2 buscas (200) + videos (1) + channels (1) + 2 playlists + 2 detalhes = 206
    assert (r["buscas"], r["unidades"], r["videos"], r["canais"]) == (2, 206, 4, 4)
    assert r["pulados"] == [] and r["falhas"] == [] and r["fonte"] == "chave própria (YOUTUBE_API_KEY)"
    assert len(calls) == 8 and any("playlistId=UUnew1" in c for c in calls) and steps

    with session_scope() as s:
        run = s.get(RadarRun, r["run_id"])
        assert (run.status, run.searches, run.units, run.videos, run.channels) == ("ok", 2, 206, 4, 4)
        assert run.params["orcamento"] == 3500 and run.params["mercados"] == ["us", "br"]
        niches = {(n.niche, n.market): n for n in s.query(RadarNiche).all()}
        us, br = niches[("dinheiro", "us")], niches[("dinheiro", "br")]
        assert (us.n, us.med, us.h100, us.new_hit, us.best_new) == (2, 175000.0, 1, 1, 300000)
        assert (br.n, br.med, br.n10k) == (1, 80000.0, 1)  # o canal do sul da Ásia fica fora das contas
        assert {v.youtube_id for v in s.query(RadarVideo).all()} == {"vUS1", "vUS2", "vBR1", "vBR2"}
        new1 = s.get(RadarChannel, "UCnew1")
        assert (
            new1.recent["verdict"] == "Modelar" and new1.recent["hits"] == 4 and new1.recent["settled"] == 5
        )
        assert new1.url == "https://www.youtube.com/@empiremoney" and new1.subscribers == 20000
        assert s.get(RadarChannel, "UCin1").alert.startswith("Canal do sul da Ásia (país IN)")
        assert s.get(RadarChannel, "UCin1").subscribers is None
        assert "verdict" not in s.get(RadarChannel, "UCbr1").recent  # só 1 vídeo, com 3 dias

    d = panel.panel_data(r["run_id"])
    assert (
        d["meta"]["captured"] == "15/11/2026" and d["meta"]["searches"] == 2 and len(d["meta"]["method"]) == 4
    )
    assert {n["name"] for n in d["niches"]} == {"História do dinheiro · EUA", "História do dinheiro · BR"}
    assert d["recs"] == [{"n": "1", "mk": "us", "id": "UCnew1", "t": "História do dinheiro · EUA · 10 min"}]
    assert d["arb"][0]["leitura"] == "Aberto em PT" and d["arb"][0]["en"]["med"] == 175000
    ch = {c["id"]: c for c in d["channels"]}
    assert ch["UCnew1"]["verdict"] == "Modelar" and ch["UCnew1"]["best"] == "vUS1"
    assert ch["UCnew1"]["vpm"] == round(1500000 / (150 / 30.44))
    assert len(d["facts"]) == 6 and d["markets"][0] == {"country": "Austrália", "rpm": 7.08}
    assert d["avoid"][0][0] == "Bodycam narrada"
    lines = panel.report_lines()
    assert lines[0].startswith("YouTube · EUA (inglês) e Brasil (português) · coleta de 15/11/2026")
    assert any(line.startswith("Para modelar (EUA): Empire Money") for line in lines)

    # nova coleta sem candidatos: o "recent" do canal não é apagado
    calls.clear()
    r2 = collect.collect(cfg=_cfg(candidatos=0), agora=NOW + timedelta(days=1))
    assert r2["unidades"] == 202 and not any("playlistItems" in c for c in calls)
    with session_scope() as s:
        assert s.get(RadarChannel, "UCnew1").recent["verdict"] == "Modelar"


def test_recentes_falhando_nao_derrubam_a_coleta(monkeypatch):
    """playlistItems 404 (canal sem uploads públicos) em todos os candidatos: a coleta continua 'ok'."""
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    base = _api([])
    erro = {"error": {"code": 404, "message": "playlistNotFound", "errors": [{"reason": "playlistNotFound"}]}}

    def handler(request: httpx.Request) -> httpx.Response:
        if "/playlistItems" in str(request.url):
            return httpx.Response(404, json=erro)
        return base(request)

    _mock(monkeypatch, handler)
    r = collect.collect(cfg=_cfg(), agora=NOW)
    assert r["videos"] == 4 and r["unidades"] == 204  # 2 buscas + videos + channels + 2 playlists
    with session_scope() as s:
        run = s.get(RadarRun, r["run_id"])
        assert run.status == "ok" and s.query(RadarVideo).count() == 4
        assert not (s.get(RadarChannel, "UCnew1").recent or {}).get("verdict")


def test_orcamento_sem_busca_nao_grava_coleta_vazia(monkeypatch):
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    calls: list[str] = []
    _mock(monkeypatch, _api(calls))
    with pytest.raises(collect.RadarError):
        collect.collect(cfg=_cfg(), orcamento=100, agora=NOW)
    assert calls == []
    with session_scope() as s:
        assert s.query(RadarRun).count() == 0


def test_painel_usa_os_hits_da_coleta(monkeypatch):
    r = _collect_ok(monkeypatch)
    catalog.save_catalog(_cfg(hit_us=500000))  # hit mudou depois da coleta
    d = panel.panel_data(r["run_id"])
    assert d["meta"]["hit_us"] == 100000 and "100 mil+ nos EUA" in d["meta"]["method"][2]
    collect.reprocess(r["run_id"])  # refazer as contas passa a usar o hit novo
    d = panel.panel_data(r["run_id"])
    assert d["meta"]["hit_us"] == 500000


def test_coleta_pelo_token_do_canal_vinculado(monkeypatch):
    credentials.set_credential("youtube", {"client_id": "cid", "client_secret": "s"})
    yt.save_channel_token("UCmeu", "rt", "Meu Canal")
    calls: list[str] = []
    _mock(monkeypatch, _api(calls, key=None))
    r = collect.collect(cfg=_cfg(), agora=NOW)
    assert r["fonte"] == "token do canal Meu Canal" and r["videos"] == 4


def test_tudo_falhando_vira_erro(monkeypatch):
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    _mock(monkeypatch, _api([], fail=True))
    with pytest.raises(access.RadarAccessError) as err:
        collect.collect(cfg=_cfg(), agora=NOW)
    assert str(err.value) == "a API recusou tudo: a cota do dia acabou: tente amanhã ou diminua o orçamento"
    with session_scope() as s:
        run = s.query(RadarRun).one()
        assert run.status == "falhou" and run.units == 200 and "cota do dia" in run.error


def test_sem_chave_nem_token():
    with pytest.raises(access.RadarAccessError) as err:
        access.resolve_access()
    assert str(err.value).startswith("Sem acesso à YouTube Data API: cole uma chave de API em Radar → Acesso")
    assert access.access_state()["ok"] is False
    with pytest.raises(access.RadarAccessError):
        collect.collect(cfg=_cfg(), agora=NOW)
    with session_scope() as s:
        assert s.query(RadarRun).count() == 0  # nada gasto, nada gravado


def test_reprocess_sem_cota(monkeypatch):
    r = _collect_ok(monkeypatch)
    calls = r["_calls"]
    n_calls = len(calls)
    cfg = _cfg()
    cfg["radar"]["alertas"] = [["empire", "Império: formato a evitar"]]
    out = collect.reprocess(r["run_id"], cfg)
    assert out["id"] == r["run_id"] and len(calls) == n_calls  # nenhum pedido novo
    with session_scope() as s:
        assert s.get(RadarChannel, "UCnew1").alert == "Império: formato a evitar"
        us = s.query(RadarNiche).filter_by(run_id=r["run_id"], niche="dinheiro", market="us").one()
        assert (us.n, us.med, us.new_hit) == (1, 50000.0, 0)
    collect.reprocess(r["run_id"], _cfg())  # catálogo de volta: alerta some
    with session_scope() as s:
        assert s.get(RadarChannel, "UCnew1").alert is None
    with pytest.raises(collect.RadarError):
        collect.reprocess(999)


def test_ideia_de_video_canal_e_repetida(monkeypatch):
    r = _collect_ok(monkeypatch)
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name="Meu canal", settings={}))
    first = ideas.idea_from_video(r["run_id"], "vUS1", cid)
    idea = first["idea"]
    assert first["created"] is True and idea["origin"] == "outlier" and idea["format"] == "long"
    assert idea["status"] == "nova" and idea["search"] == "money history" and idea["channel_id"] == cid
    assert idea["title"] == "How the empire lost all its money"
    assert idea["evidence"] == (
        "300 mil views em 10 dias; canal com 20 mil inscritos (15× inscritos); Modelar; https://youtu.be/vUS1"
    )
    again = ideas.idea_from_video(r["run_id"], "vUS1", cid)
    assert again["created"] is False and again["idea"]["id"] == idea["id"]
    other_channel = ideas.idea_from_video(r["run_id"], "vUS1", None)
    assert other_channel["created"] is True

    ch = ideas.idea_from_channel(r["run_id"], "UCnew1", cid)
    assert ch["created"] is True and ch["idea"]["origin"] == "radar"
    assert ch["idea"]["evidence"].endswith("https://www.youtube.com/@empiremoney")
    assert ideas.idea_from_channel(r["run_id"], "UCnew1", cid)["created"] is False
    with session_scope() as s:
        assert s.query(Idea).count() == 3

    # handle que é prefixo de outro (@empire × @empiremoney) e `_` não é curinga
    with session_scope() as s:
        s.get(RadarChannel, "UCnew1").url = "https://www.youtube.com/@empire"
        s.get(RadarChannel, "UCbr1").url = "https://www.youtube.com/@empir_"
    curto = ideas.idea_from_channel(r["run_id"], "UCnew1", cid)
    assert curto["created"] is True and curto["idea"]["id"] != ch["idea"]["id"]
    assert ideas.idea_from_channel(r["run_id"], "UCbr1", cid)["created"] is True
    with session_scope() as s:
        assert s.query(Idea).count() == 5
    with pytest.raises(collect.RadarError):
        ideas.idea_from_video(r["run_id"], "naoexiste", cid)
    with pytest.raises(KeyError):
        ideas.idea_from_video(r["run_id"], "vUS1", "chn_naoexiste")

    assert ideas.ensure_pending("2026-W46") is not None
    assert ideas.ensure_pending("2026-W46") is None
    with session_scope() as s:
        p = s.query(PendingTask).filter_by(auto_key="radar:decidir:2026-W46").one()
        assert p.where_hint == "/radar" and p.kind == "auto"


def test_job_radar_collect(monkeypatch):
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    _mock(monkeypatch, _api([]))
    catalog.save_catalog(_cfg())
    job = jobs.get_runner().submit("radar_collect", {"markets": ["us"]}, run_inline=True)
    with session_scope() as s:
        row = s.get(Job, job["id"])
        assert row.status == "done", row.error
        assert row.result["buscas"] == 1 and row.result["videos"] == 2
        assert s.query(PendingTask).filter(PendingTask.auto_key.like("radar:decidir:%")).count() == 1
    assert catalog.radar_state()["last_job"] == job["id"]
    assert catalog.radar_state().get("last_week") is None  # coleta manual não marca a semana
    jobs.get_runner().submit("radar_collect", {"markets": ["us"], "weekly": "2026-W46"}, run_inline=True)
    assert catalog.radar_state()["last_week"] == "2026-W46"


# ----------------------------------------------------------------------------- rotina semanal


def _wait_jobs() -> None:
    for _ in range(250):
        with session_scope() as s:
            if not s.query(Job).filter(Job.status.in_(("queued", "running"))).count():
                return
        time.sleep(0.02)
    raise AssertionError("job não terminou")


def test_passo_da_rotina_uma_vez_por_semana_e_pulo_com_15_ideias(monkeypatch):
    assert ("radar", routines.step_radar) in routines.STEPS
    submitted: list[dict] = []

    def fake(ctx, payload):  # como o job real: marca a semana só no fim, quando dá certo
        submitted.append(payload)
        catalog.update_radar_state(last_week=payload["weekly"])
        return {}

    monkeypatch.setitem(jobs._REGISTRY, "radar_collect", fake)
    assert routines.step_radar(NOW) == []  # "Rodar toda semana" desligado
    catalog.update_radar_state(auto=True)
    assert routines.step_radar(NOW)[0].startswith("radar de nichos: sem acesso")
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})

    lines = routines.morning(NOW)
    assert any(line.startswith("radar de nichos na fila") for line in lines)
    _wait_jobs()
    assert submitted == [{"weekly": ideas.iso_week(NOW)}]
    assert routines.step_radar(NOW - timedelta(days=2)) == []  # mesma semana ISO (15/11/2026 é domingo)
    assert catalog.radar_state()["last_week"] == ideas.iso_week(NOW)

    with session_scope() as s:
        for i in range(15):
            s.add(Idea(id=new_id("ide"), title=f"Ideia {i}", status="nova"))
    next_week = NOW + timedelta(days=7)
    assert routines.step_radar(next_week) == ["radar pulado: 15 ideias novas esperando você"]
    assert routines.step_radar(next_week) == []  # pulada uma vez por semana
    with session_scope() as s:
        assert s.query(Job).filter(Job.kind == "radar_collect").count() == 1


def test_passo_da_rotina_tenta_de_novo_se_a_coleta_falhou(monkeypatch):
    def falha(ctx, payload):
        raise access.RadarAccessError("a API recusou tudo: a cota do dia acabou")

    monkeypatch.setitem(jobs._REGISTRY, "radar_collect", falha)
    catalog.update_radar_state(auto=True)
    credentials.set_credential(access.PROVIDER, {access.FIELD: "chave-teste"})
    friday = NOW - timedelta(days=2)
    assert routines.step_radar(friday)[0].startswith("radar de nichos na fila")
    _wait_jobs()
    st = catalog.radar_state()
    assert st.get("last_week") is None and st["last_week_submitted"] == ideas.iso_week(friday)
    # coleta em andamento (clique na página Radar): a rotina não submete outra
    with session_scope() as s:
        s.add(Job(id=new_id("job"), kind="radar_collect", status="running", payload={}))
    assert routines.step_radar(friday + timedelta(days=1)) == []
    with session_scope() as s:
        s.query(Job).filter(Job.status == "running").update({"status": "failed"})
    # a manhã seguinte, na mesma semana, tenta de novo
    assert routines.step_radar(friday + timedelta(days=1))[0].startswith("radar de nichos na fila")
    _wait_jobs()
    with session_scope() as s:
        assert s.query(Job).filter(Job.kind == "radar_collect").count() == 3


# ----------------------------------------------------------------------------- rotas


@pytest.fixture
def client():
    with TestClient(create_app()) as c:
        yield c


def test_rotas_principais(client, monkeypatch):
    st = client.get("/api/radar").json()
    assert st["access"]["ok"] is False and st["auto"] is False and st["last_run"] is None
    assert st["catalog"]["niches"] == 15 and st["catalog"]["markets"] == ["us", "br"]
    assert client.post("/api/radar/collect", json={}).status_code == 400
    assert "Sem acesso" in client.post("/api/radar/collect", json={}).json()["detail"]

    saved = client.put("/api/radar/key", json={"api_key": "AIzaChaveDeTeste123"}).json()
    assert saved == {"ok": True, "masked": "AIza…e123"}
    st = client.get("/api/radar").json()
    assert st["access"]["ok"] is True and st["access"]["source"] == "chave própria (YOUTUBE_API_KEY)"
    assert "AIzaChaveDeTeste123" not in json.dumps(st)

    cat = client.get("/api/radar/catalog").json()
    assert len(cat["nichos"]) == 15 and cat["custom"] is False
    cat["nichos"][0]["en"] = ["a | b"]
    bad = client.put("/api/radar/catalog", json=cat)
    assert bad.status_code == 422 and "um termo por linha" in bad.json()["detail"]
    cat["nichos"] = cat["nichos"][1:3]
    assert client.put("/api/radar/catalog", json=cat).json()["custom"] is True
    sim = client.post("/api/radar/simulate", json={"budget": 3500}).json()
    assert sim["buscas"] == 8 and sim["pulados"] == []  # 2 nichos × 2 mercados × 2 termos
    assert client.post("/api/radar/simulate", json={"niches": ["xx"]}).status_code == 422
    assert len(client.post("/api/radar/catalog/reset").json()["nichos"]) == 15

    monkeypatch.setitem(jobs._REGISTRY, "radar_collect", lambda ctx, payload: {})
    job = client.post("/api/radar/collect", json={"markets": ["br"], "budget": 1000}).json()
    assert job["kind"] == "radar_collect" and job["payload"] == {"markets": ["br"], "budget": 1000}
    _wait_jobs()
    assert client.post("/api/radar/collect", json={"budget": 100}).status_code == 422  # não cobre nenhuma busca
    with session_scope() as s:
        s.add(Job(id=new_id("job"), kind="radar_collect", status="queued", payload={}))
    dup = client.post("/api/radar/collect", json={})
    assert dup.status_code == 409 and "em andamento" in dup.json()["detail"]

    assert client.put("/api/radar/settings", json={"auto": True}).json()["auto"] is True
    assert client.get("/api/radar/runs").json() == []
    assert client.get("/api/radar/runs/1/panel").status_code == 404
    assert client.get("/api/radar/report").json()["lines"] == ["nenhuma coleta ainda: rode a coleta do radar"]
    assert client.delete("/api/radar/key").json() == {"ok": True}
    assert client.get("/api/radar").json()["access"]["ok"] is False


def test_rotas_com_coleta(client, monkeypatch):
    r = _collect_ok(monkeypatch)
    runs = client.get("/api/radar/runs").json()
    assert runs[0]["id"] == r["run_id"] and runs[0]["units"] == 206
    data = client.get(f"/api/radar/runs/{r['run_id']}/panel").json()
    assert data["meta"]["videos"] == 4 and data["recs"][0]["id"] == "UCnew1"
    assert client.post(f"/api/radar/runs/{r['run_id']}/reprocess").json()["id"] == r["run_id"]
    assert client.get("/api/radar").json()["last_run"]["id"] == r["run_id"]
    made = client.post("/api/radar/ideas", json={"run_id": r["run_id"], "youtube_id": "vBR1"}).json()
    assert made["created"] is True and made["idea"]["search"] == "história do dinheiro"
    again = client.post("/api/radar/ideas", json={"run_id": r["run_id"], "youtube_id": "vBR1"}).json()
    assert again["created"] is False
    assert client.post("/api/radar/ideas", json={"run_id": r["run_id"]}).status_code == 422
    assert client.post("/api/radar/ideas", json={"run_id": r["run_id"], "youtube_id": "x"}).status_code == 404
    assert len(client.get("/api/radar/report").json()["lines"]) >= 2
