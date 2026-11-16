"""Importação do histórico do projeto `canais`: banco sqlite e canal.toml sintéticos, montados no teste."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from studio import cli
from studio.api.app import create_app
from studio.db import session_scope
from studio.ids import new_id
from studio.models import (
    AffiliateOffer,
    Channel,
    Experiment,
    Idea,
    Lesson,
    PendingTask,
    RevenueEntry,
    VideoMetric,
)
from studio.workers.canais_import.apply import import_canais, variants_of
from studio.workers.canais_import.reader import CanaisError

SCHEMA = """
CREATE TABLE canais (id TEXT PRIMARY KEY, nome TEXT NOT NULL, status TEXT, nivel TEXT, idioma TEXT, mercado TEXT,
  nicho TEXT, channel_id TEXT, handle TEXT, projeto TEXT, toml TEXT, ordem INTEGER, sincronizado_em TEXT);
CREATE TABLE ideias (id INTEGER PRIMARY KEY AUTOINCREMENT, canal TEXT NOT NULL, titulo TEXT NOT NULL, angulo TEXT,
  formato TEXT, origem TEXT, evidencia TEXT, busca TEXT, nota REAL, status TEXT NOT NULL DEFAULT 'nova', motivo TEXT,
  video_id TEXT, criado_em TEXT NOT NULL, decidido_em TEXT);
CREATE TABLE videos (id TEXT PRIMARY KEY, canal TEXT NOT NULL, slug TEXT NOT NULL, titulo TEXT, formato TEXT,
  youtube_id TEXT, publicado_em TEXT, duracao_s REAL, variaveis TEXT NOT NULL DEFAULT '{}', criado_em TEXT NOT NULL);
CREATE TABLE execucoes (id INTEGER PRIMARY KEY AUTOINCREMENT, video_id TEXT, canal TEXT, etapa TEXT NOT NULL,
  inicio TEXT NOT NULL, segundos REAL);
CREATE TABLE metricas (youtube_id TEXT NOT NULL, data TEXT NOT NULL, video_id TEXT, canal TEXT, idade_h REAL,
  views INTEGER, likes INTEGER, comentarios INTEGER, impressoes INTEGER, ctr REAL, avd_s REAL, pct_assistido REAL,
  inscritos INTEGER, receita_usd REAL, rpm REAL, fonte TEXT, PRIMARY KEY (youtube_id, data));
CREATE TABLE experimentos (id INTEGER PRIMARY KEY AUTOINCREMENT, canal TEXT, hipotese TEXT NOT NULL, variavel TEXT,
  variantes TEXT, metrica TEXT, videos TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'aberto',
  resultado TEXT, aprendizado TEXT, criado_em TEXT NOT NULL, concluido_em TEXT);
CREATE TABLE licoes (id INTEGER PRIMARY KEY AUTOINCREMENT, escopo TEXT NOT NULL, texto TEXT NOT NULL, evidencia TEXT,
  origem TEXT, video_id TEXT, status TEXT NOT NULL DEFAULT 'proposta', aplicada_em TEXT, motivo TEXT,
  criado_em TEXT NOT NULL, decidido_em TEXT);
CREATE TABLE pendencias (id TEXT PRIMARY KEY, canal TEXT, texto TEXT NOT NULL, onde TEXT,
  status TEXT NOT NULL DEFAULT 'aberta', ordem INTEGER, criado_em TEXT NOT NULL, feito_em TEXT);
CREATE TABLE receita (canal TEXT NOT NULL, fonte TEXT NOT NULL, periodo TEXT NOT NULL, rastreio TEXT NOT NULL DEFAULT '',
  cliques INTEGER, pedidos INTEGER, vendas REAL, comissao REAL, moeda TEXT NOT NULL DEFAULT 'BRL', nota TEXT, quem TEXT,
  registrado_em TEXT NOT NULL, PRIMARY KEY (canal, fonte, periodo, rastreio));
"""

TOML_PV = """
id = "plantao-vice"
nome = "Plantão Vice"
idioma = "pt-BR"
fuso = "America/Manaus"
nicho = "Notícias de GTA 6"
publico = "Gamers de 14 a 30 anos."

[youtube]
handle = "@plantao"
channel_id = "UCplantao"

[motor]
roteiro.ppm = 165
shorts.hashtag = "#GTA6"

[voz]
final = "ElevenLabs Matheus (xWdpADtEio43ew1zGxUQ), Eleven v4"
creditos_por_caractere = 0.44

[agenda]
cadencia = "2 Shorts por dia"
horarios = ["11:00", "15:00"]
posts_por_dia = 2
estoque_minimo = 3
estoque_formato = "short-noticia"

[monetizacao]
desde = "2026-11"
amazon_tag = "plantao-20"
max_links = 3

[[monetizacao.frente]]
id = "amazon"
nome = "Afiliado Amazon"
status = "ativa"
proximo = "IDs por vídeo"

[[monetizacao.oferta]]
id = "gta6-ps5"
nome = "GTA 6 (PS5)"
loja = "amazon"
asin = "B0TESTE"
texto = "GTA 6 em caixa"
temas = ["sempre"]
status = "ativa"
conferido = "2026-11-06"
"""

TOML_MR = """
id = "money-rerun"
nome = "Money Rerun"
idioma = "en"
fuso = "America/New_York"
nicho = "História do dinheiro"

[voz]
final = "ElevenLabs Chris, modelo eleven_v4"

[agenda]
horarios = ["13:00"]
estoque_minimo = 1
"""


@pytest.fixture
def canais(tmp_path: Path) -> Path:
    root = tmp_path / "canais"
    (root / "estudio").mkdir(parents=True)
    (root / "plantao-vice").mkdir()
    (root / "money-rerun").mkdir()
    (root / "plantao-vice" / "canal.toml").write_text(TOML_PV, encoding="utf-8")
    (root / "money-rerun" / "canal.toml").write_text(TOML_MR, encoding="utf-8")
    con = sqlite3.connect(root / "estudio" / "estudio.db")
    con.executescript(SCHEMA)
    t = "2026-11-03T17:54:14-04:00"
    con.executemany("INSERT INTO canais (id, nome, idioma, nicho, channel_id, handle, toml, ordem) VALUES (?,?,?,?,?,?,?,?)", [
        ("money-rerun", "Money Rerun", "en", "História", "UCmoney", "@MoneyRerun", str(root / "money-rerun" / "canal.toml"), 1),
        ("plantao-vice", "Plantão Vice", "pt-BR", "GTA", "UCplantao", "@plantao", str(root / "plantao-vice" / "canal.toml"), 2),
    ])
    con.executemany(
        "INSERT INTO ideias (canal, titulo, angulo, formato, origem, busca, nota, status, motivo, video_id, criado_em, "
        "decidido_em) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            ("plantao-vice", "Por que GTA 6 demorou?", "ângulo", "doc-longo", "central", "busca", 8.2, "em-producao",
             None, "plantao-vice-ep1", t, None),
            ("money-rerun", "O fim do ouro", None, "corte-short", "radar", None, None, "rejeitada", "fraca", None, t, t),
        ])
    con.executemany("INSERT INTO videos VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("plantao-vice-ep1", "plantao-vice", "ep1", "Por que GTA 6 demorou?", "doc-longo", "yt1",
         "2026-11-02T16:54:10-04:00", None, '{"duracao_s": 944}', t),
        ("plantao-vice-ep2", "plantao-vice", "ep2", "Ainda sem YouTube", "doc-longo", None, None, None, "{}", t),
    ])
    con.executemany("INSERT INTO execucoes (video_id, canal, etapa, inicio, segundos) VALUES (?,?,?,?,?)",
                    [("plantao-vice-ep1", "plantao-vice", "roteiro", t, 12.0)] * 3)
    con.executemany(
        "INSERT INTO metricas (youtube_id, data, canal, idade_h, views, likes, comentarios, avd_s, pct_assistido, fonte) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)", [
            ("yt1", "2026-11-03", "plantao-vice", 26.9, 1367, 20, 1, None, None, "geradormg"),
            ("yt1", "2026-11-04", "plantao-vice", 50.0, 2000, 30, 2, 18.5, 61.0, "yt-dlp"),
            ("yt2", "2026-11-04", None, 10.0, 50, None, None, None, None, "outra"),
        ])
    con.executemany(
        "INSERT INTO experimentos (canal, hipotese, variavel, variantes, metrica, videos, status, resultado, "
        "aprendizado, criado_em, concluido_em) VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
            ("plantao-vice", "Tarde rende mais", "hora", "tarde (12-18h),noite (18-24h)", "views", "[]", "concluido",
             "sem diferença", "hora não muda", t, t),
            ("plantao-vice", "Ângulo de busca", "var.angulo", '["jogador", "noticia"]', "views_48h",
             '["plantao-vice-ep1"]', "aberto", None, None, t, None),
            ("plantao-vice", "Thumb sem pose", "thumb", "atual (personagem de quatro, 2-isso-foi-real) | sem a pose",
             "views", "[]", "concluido", None, None, t, t),
        ])
    con.executemany(
        "INSERT INTO licoes (escopo, texto, evidencia, origem, video_id, status, aplicada_em, criado_em, decidido_em) "
        "VALUES (?,?,?,?,?,?,?,?,?)", [
            ("canal:plantao-vice", "Shorts só de dia", "19h fraco", "metricas", None, "aplicada", "canal.toml", t, t),
            ("formato:doc-longo", "Abrir com a pergunta", None, "revisao-lucas", "plantao-vice-ep1", "proposta", None, t, None),
            ("skill:estudio-roteiro", "Conferir fontes", None, "producao", None, "aplicada", None, t, t),
            ("global", "Menos é mais", None, "metricas", None, "aplicada", None, t, t),
            ("canal:money-rerun", "Título com busca", None, "metricas", None, "proposta", None, t, None),
            ("formato:short-noticia", "Fato repetido não", None, "metricas", None, "aplicada", "GeradorMG", t, t),
        ])
    con.executemany("INSERT INTO pendencias VALUES (?,?,?,?,?,?,?,?)", [
        ("pv-amazon-ids", "plantao-vice", "Criar IDs de rastreamento", "Associados", "aberta", 1, t, None),
        ("geral", None, "Fazer backup", None, "feita", 2, t, t),
    ])
    con.executemany("INSERT INTO receita (canal, fonte, periodo, rastreio, cliques, pedidos, vendas, comissao, moeda, "
                    "registrado_em) VALUES (?,?,?,?,?,?,?,?,?,?)", [
        ("plantao-vice", "amazon", "2026-11", "", 40, 2, 899.8, 26.99, "BRL", t),
        ("plantao-vice", "amazon", "2026-11", "plantao-ep1-20", 10, 1, 449.9, 13.5, "BRL", t),
    ])
    con.commit()
    con.close()
    return root


def _snapshot(root: Path) -> dict[str, tuple[int, int]]:
    return {str(p.relative_to(root)): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def _counts() -> dict[str, int]:
    with session_scope() as s:
        return {m.__name__: s.query(m).count() for m in
                (Channel, Idea, VideoMetric, Experiment, Lesson, PendingTask, RevenueEntry, AffiliateOffer)}


def _table(report: dict, key: str) -> dict:
    return next(t for t in report["tables"] if t["key"] == key)


def test_dry_run_nao_grava_nada_e_nao_mexe_no_canais(canais: Path):
    before = _snapshot(canais)
    report = import_canais(canais, dry_run=True)
    assert report["dry_run"] is True
    assert _counts() == dict.fromkeys(_counts(), 0)
    assert {c["slug"]: c["action"] for c in report["channels"]} == {"money-rerun": "criar", "plantao-vice": "criar"}
    assert all(c["vulto_id"] is None for c in report["channels"])
    assert _table(report, "ideias")["new"] == 2
    assert _table(report, "metricas")["new"] == 3
    assert _table(report, "licoes")["new"] == 6
    assert _table(report, "videos")["new"] == 1 and _table(report, "videos")["skipped"] == 1
    assert _table(report, "receita")["new"] == 2
    assert any("Execuções (3)" in n for n in report["notes"])
    # canal, global e formato viram propostas; skill:* não entra no roteiro e fica aplicada
    assert any("3 lições aplicadas no canais" in n and "propostas" in n for n in report["notes"])
    assert any("1 experimentos abertos entram como abandonados" in n and "angulo" in n for n in report["notes"])
    assert any("só as métricas são atualizadas" in n for n in report["notes"])
    # nada novo na pasta do canais (nem -shm/-wal ao lado do banco)
    assert _snapshot(canais) == before


def test_importa_tudo_para_as_tabelas_do_vulto(canais: Path):
    before = _snapshot(canais)
    report = import_canais(canais, dry_run=False)
    assert _snapshot(canais) == before
    with session_scope() as s:
        pv = s.query(Channel).filter_by(name="Plantão Vice").one()
        mr = s.query(Channel).filter_by(name="Money Rerun").one()
        assert pv.niche == "Notícias de GTA 6" and pv.audience == "Gamers de 14 a 30 anos."
        assert (pv.posts_per_day, pv.min_stock, pv.stock_format) == (2, 3, "short")
        assert pv.short_hashtag == "#GTA6" and pv.credits_per_char == pytest.approx(0.44)
        assert pv.voice_defaults == {"final": {"provider": "elevenlabs", "voice": "xWdpADtEio43ew1zGxUQ"}}
        assert pv.languages == ["pt-BR"] and mr.languages == ["en"]
        assert mr.voice_defaults == {}  # voz sem id: não reconhecida
        assert pv.settings["posting"]["timezone"] == "America/Manaus"
        assert [x["time"] for x in pv.settings["posting"]["slots"]] == ["11:00", "15:00"]
        assert pv.settings["monetization"]["amazon_tag"] == "plantao-20"
        assert pv.settings["monetization"]["fronts"][0]["name"] == "Afiliado Amazon"
        assert pv.settings["canais"]["id"] == "plantao-vice"
        assert pv.settings["canais"]["youtube_channel_id"] == "UCplantao"
        assert "youtube" not in pv.settings  # não finge vínculo com o YouTube
        offer = s.query(AffiliateOffer).one()
        assert (offer.channel_id, offer.slug, offer.topics, offer.checked_on) == (pv.id, "gta6-ps5", ["sempre"], "2026-11-06")

        ideas = {i.title: i for i in s.query(Idea)}
        gta = ideas["Por que GTA 6 demorou?"]
        assert (gta.channel_id, gta.format, gta.status, gta.score) == (pv.id, "long", "em_producao", 8.2)
        assert "plantao-vice-ep1" in gta.evidence
        assert gta.created_at.isoformat().startswith("2026-11-03T21:54:14")
        ouro = ideas["O fim do ouro"]
        assert (ouro.channel_id, ouro.format, ouro.status, ouro.reason) == (mr.id, "short", "rejeitada", "fraca")

        m = s.query(VideoMetric).filter_by(youtube_id="yt1", day="2026-11-04").one()
        assert (m.views, m.comments, m.avg_view_s, m.pct_watched, m.source, m.channel_id) == (
            2000, 2, 18.5, 61.0, "public", pv.id)
        assert s.query(VideoMetric).filter_by(youtube_id="yt1", day="2026-11-03").one().source == "youtube_api"
        orphan = s.query(VideoMetric).filter_by(youtube_id="yt2").one()
        assert (orphan.channel_id, orphan.source) == (None, "manual")

        exps = {e.variable: e for e in s.query(Experiment)}
        assert exps["thumb"].variants == ["atual (personagem de quatro, 2-isso-foi-real)", "sem a pose"]
        assert "Variantes no canais: atual (personagem" in exps["thumb"].hypothesis
        assert exps["hora"].variants == ["tarde (12-18h)", "noite (18-24h)"]
        assert exps["hora"].status == "concluido" and exps["hora"].closed_at is not None
        assert exps["angulo"].variants == ["jogador", "noticia"]
        assert exps["angulo"].metric == "views" and "views_48h" in exps["angulo"].hypothesis
        assert "plantao-vice-ep1" in exps["angulo"].hypothesis and exps["angulo"].project_ids == []
        # aberto com variável que o Vulto não calcula: ficaria "cedo" para sempre
        assert exps["angulo"].status == "abandonado" and "não calcula a variável 'angulo'" in exps["angulo"].result
        assert exps["hora"].result == "sem diferença"

        lessons = {lesson.text: lesson for lesson in s.query(Lesson)}
        assert {k: v.scope for k, v in lessons.items()} == {
            "Shorts só de dia": f"canal:{pv.id}", "Abrir com a pergunta": "formato:long",
            "Conferir fontes": "skill:estudio-roteiro", "Menos é mais": "global", "Título com busca": f"canal:{mr.id}",
            "Fato repetido não": "formato:short-noticia"}  # não vira "todo Short"
        assert {k for k, v in lessons.items() if v.status == "aplicada"} == {"Conferir fontes"}
        dia = lessons["Shorts só de dia"]
        assert dia.status == "proposta" and dia.applied_where == "" and dia.decided_at is None
        assert "Aplicada no canais em: canal.toml" in dia.evidence and "19h fraco" in dia.evidence

        cache = pv.settings["analytics"]["videos"]
        assert cache == {"yt1": {"title": "Por que GTA 6 demorou?", "published_at": "2026-11-02T16:54:10-04:00",
                                 "duration_s": 944.0, "privacy": None}}

        pend = {p.id: p for p in s.query(PendingTask)}
        assert pend["pen_cn_pv-amazon-ids"].where_hint == "Associados"
        assert pend["pen_cn_geral"].channel_id is None and pend["pen_cn_geral"].status == "feita"

        rev = s.query(RevenueEntry).order_by(RevenueEntry.tracking_id).all()
        assert [(r.tracking_id, r.commission) for r in rev] == [("", 26.99), ("plantao-ep1-20", 13.5)]
    assert {c["action"] for c in report["channels"]} == {"criar"}


def test_rodar_duas_vezes_nao_duplica_e_completa_metricas(canais: Path):
    import_canais(canais, dry_run=False)
    first = _counts()
    # o canais ganhou a retenção de um dia que já tinha entrado
    con = sqlite3.connect(canais / "estudio" / "estudio.db")
    con.execute("UPDATE metricas SET avd_s = 20.0, views = 9999 WHERE youtube_id = 'yt1' AND data = '2026-11-03'")
    con.commit()
    con.close()
    report = import_canais(canais, dry_run=False)
    assert _counts() == first
    assert {c["action"] for c in report["channels"]} == {"associar"}
    assert all(c["reason"] == "importado antes" for c in report["channels"])
    assert _table(report, "ideias") | {"examples": []} == {
        "key": "ideias", "label": "Ideias", "total": 2, "new": 0, "existing": 2, "updated": 0, "skipped": 0,
        "examples": []}
    assert _table(report, "metricas")["updated"] == 1
    assert _table(report, "videos")["existing"] == 1
    with session_scope() as s:
        m = s.query(VideoMetric).filter_by(youtube_id="yt1", day="2026-11-03").one()
        # vazio é preenchido; contador que cresceu (o dia ainda corria na 1ª importação) fica com o maior
        assert (m.avg_view_s, m.views, m.likes) == (20.0, 9999, 20)
    con = sqlite3.connect(canais / "estudio" / "estudio.db")
    con.execute("UPDATE metricas SET views = 10 WHERE youtube_id = 'yt1' AND data = '2026-11-03'")
    con.commit()
    con.close()
    import_canais(canais, dry_run=False)
    with session_scope() as s:
        assert s.query(VideoMetric).filter_by(youtube_id="yt1", day="2026-11-03").one().views == 9999  # não diminui


def test_associa_por_nome_sem_sobrescrever_e_pula_canal(canais: Path):
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name="plantao  VICE", niche="Meu nicho", settings={}, posts_per_day=4))
    report = import_canais(canais, {"money-rerun": "pular"}, dry_run=False)
    by_slug = {c["slug"]: c for c in report["channels"]}
    assert by_slug["plantao-vice"]["action"] == "associar" and by_slug["plantao-vice"]["reason"] == "mesmo nome"
    assert by_slug["plantao-vice"]["vulto_id"] == cid
    assert by_slug["money-rerun"]["action"] == "pular"
    with session_scope() as s:
        assert s.query(Channel).count() == 1
        row = s.get(Channel, cid)
        assert (row.name, row.niche, row.posts_per_day) == ("plantao  VICE", "Meu nicho", 4)
        assert row.audience == "Gamers de 14 a 30 anos."  # vazio: preenchido
        assert s.query(Idea).count() == 1  # a ideia do canal pulado ficou de fora
        assert {lesson.scope for lesson in s.query(Lesson)} == {f"canal:{cid}", "formato:long", "skill:estudio-roteiro",
                                                                "global", "formato:short-noticia"}
    assert _table(report, "ideias")["skipped"] == 1
    assert _table(report, "licoes")["skipped"] == 1


def test_associa_pelo_canal_do_youtube_e_por_escolha(canais: Path):
    linked, other = new_id("chn"), new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=linked, name="Outro nome", settings={"youtube": {"channel_id": "UCplantao"}}))
        s.add(Channel(id=other, name="Destino", settings={}))
    report = import_canais(canais, {"money-rerun": other}, dry_run=False)
    by_slug = {c["slug"]: c for c in report["channels"]}
    assert (by_slug["plantao-vice"]["vulto_id"], by_slug["plantao-vice"]["reason"]) == (linked, "mesmo canal do YouTube")
    assert (by_slug["money-rerun"]["vulto_id"], by_slug["money-rerun"]["reason"]) == (other, "escolhido à mão")
    with pytest.raises(ValueError, match="não encontrado"):
        import_canais(canais, {"money-rerun": "chn_naoexiste"}, dry_run=True)
    with pytest.raises(ValueError, match="não existe no canais"):
        import_canais(canais, {"outro": "auto"}, dry_run=True)


def test_so_toml_sem_banco_e_pasta_vazia(tmp_path: Path, canais: Path):
    (canais / "estudio" / "estudio.db").unlink()
    report = import_canais(canais, dry_run=True)
    assert {c["slug"] for c in report["channels"]} == {"plantao-vice", "money-rerun"}
    assert any("Sem banco" in n for n in report["notes"])
    empty = tmp_path / "vazia"
    empty.mkdir()
    with pytest.raises(CanaisError):
        import_canais(empty)
    with pytest.raises(CanaisError):
        import_canais(tmp_path / "nao-existe")


def test_variantes_como_no_canais():
    assert variants_of('["a", "b"]') == ["a", "b"]
    assert variants_of("a, b,") == ["a", "b"]
    assert variants_of(None) == []
    # texto livre do canais real: " | " com vírgulas dentro dos parênteses e "A: "/"B: " na frente
    assert variants_of("A: They Ended Gold (curiosidade, sem termo de busca) | B: Nixon Shock (termo do autocompletar)") == [
        "They Ended Gold (curiosidade, sem termo de busca)", "Nixon Shock (termo do autocompletar)"]
    assert variants_of("CTA entre 0:20 e 0:30 (ep. 7: red-dead, guia) | sem CTA (ep1, jailbreak, iceberg)") == [
        "CTA entre 0:20 e 0:30 (ep. 7: red-dead, guia)", "sem CTA (ep1, jailbreak, iceberg)"]
    assert variants_of("com Vídeo relacionado (todos os cortes) x ritmo antes dos cortes") == [
        "com Vídeo relacionado (todos os cortes)", "ritmo antes dos cortes"]


def test_dois_canais_no_mesmo_destino_e_vinculo_de_outro(canais: Path):
    cid = new_id("chn")
    with session_scope() as s:
        s.add(Channel(id=cid, name="Destino", settings={}))
    with pytest.raises(ValueError, match="mesmo canal do Vulto"):
        import_canais(canais, {"money-rerun": cid, "plantao-vice": cid}, dry_run=True)
    # canal já ligado a outro canal do canais não é achado pelo nome nem pelo YouTube
    with session_scope() as s:
        s.add(Channel(id=new_id("chn"), name="Plantão Vice", settings={"canais": {"id": "outro"},
                                                                       "youtube": {"channel_id": "UCplantao"}}))
    report = import_canais(canais, {"money-rerun": cid}, dry_run=True)
    assert {c["slug"]: c["action"] for c in report["channels"]} == {"money-rerun": "associar", "plantao-vice": "criar"}


def test_formato_do_estoque_sem_estoque_formato(canais: Path):
    toml = canais / "money-rerun" / "canal.toml"
    text = toml.read_text(encoding="utf-8").replace("fuso = ", 'formatos = ["doc-longo", "corte-short"]\nfuso = ')
    toml.write_text(text, encoding="utf-8")
    import_canais(canais, dry_run=False)
    with session_scope() as s:
        assert s.query(Channel).filter_by(name="Money Rerun").one().stock_format == "long"
        assert s.query(Channel).filter_by(name="Plantão Vice").one().stock_format == "short"


def test_copia_refeita_quando_o_canais_grava_no_meio(canais: Path, monkeypatch: pytest.MonkeyPatch):
    from studio.workers.canais_import import reader

    calls = {"n": 0}
    real = reader._state

    def moving(paths):  # o banco "muda" a cada olhada: a cópia nunca fica estável
        calls["n"] += 1
        return [(calls["n"], 0)] + real(paths)[1:]

    monkeypatch.setattr(reader, "_state", moving)
    report = import_canais(canais, dry_run=True)
    assert calls["n"] == 2 * reader.COPY_TRIES
    assert any("estava gravando" in n for n in report["notes"])
    assert _table(report, "ideias")["new"] == 2  # ainda lê o que copiou


def test_api_previa_e_importacao(canais: Path):
    with TestClient(create_app()) as c:
        assert "default_root" in c.get("/api/import/canais").json()
        prev = c.post("/api/import/canais/preview", json={"root": str(canais)}).json()
        assert prev["dry_run"] is True and _counts()["Idea"] == 0
        done = c.post("/api/import/canais", json={"root": str(canais), "channels": {"money-rerun": "pular"}}).json()
        assert done["dry_run"] is False and _counts()["Idea"] == 1
        bad = c.post("/api/import/canais/preview", json={"root": str(canais / "nada")})
        assert bad.status_code == 400 and "não encontrada" in bad.json()["detail"]


def test_cli_dry_run(canais: Path, capsys: pytest.CaptureFixture[str]):
    cli.main(["importar-canais", "--pasta", str(canais), "--dry-run", "--canal", "money-rerun=pular"])
    out = capsys.readouterr().out
    assert "PRÉVIA: nada foi gravado." in out
    assert "money-rerun: pular" in out and "plantao-vice: criar" in out
    assert _counts()["Channel"] == 0
    with pytest.raises(SystemExit):
        cli.main(["importar-canais", "--pasta", str(canais), "--canal", "sem-igual"])
