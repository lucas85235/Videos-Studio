"""Pacote enxuto: títulos gerados curtos (nunca cortados no meio) e thumb com no máximo 3 palavras visíveis
(palavra + pílula + cartões), pílula e cartões desligados por padrão, selo de verificação fora da thumb."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from studio.config import get_settings, set_settings
from studio.core.project import service
from studio.core.project.document import CardLayer, PackageCandidate, TextLayer, ThumbLayers
from studio.core.styles.library import get_style
from studio.media import thumb_check as K
from studio.media import thumb_compose as C
from studio.media import thumb_templates as T
from studio.workers import tasks
from studio.workers.pipeline import authoring

LONG = "curiosidades-longo"
APROVADO = "GTA 6: sua PRIMEIRA SEMANA, dia a dia"
LONGOS = [
    "GTA 6 já no console: o plano da primeira semana (e o que não comprar ainda)",
    "GTA 6: o que fazer, o que evitar e o que tem prazo depois de começar",
    "GTA 6: itens da Ultimate saem por capítulo e o Online fica pra depois",
    "GTA 6: upgrade pra Ultimate, 60 FPS e GTA+ antes de abrir a carteira",
]


def _item(title: str, thumb: str = "TUDO", template: str = "heroi-palavra", pill: str | None = None,
          cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"title": title, "thumb_text": thumb, "promise": "p", "concept": "c", "template": template,
            "background_hint": None, "characters_hint": [], "presenter_pose": None, "pill": pill,
            "cards": cards or []}


def _norm(items: list[dict[str, Any]], rejected: list[str] | None = None) -> list[PackageCandidate]:
    return authoring._normalize_candidates(items, assets=authoring.PackageAssets(),
                                           templates=["heroi-palavra", "numero-lista"], is_long=True,
                                           rejected=rejected)


# --- títulos -----------------------------------------------------------------------------------------------------
def test_titulo_aprovado_passa_intacto():
    title, notes = authoring.normalize_generated_title(APROVADO)
    assert title == APROVADO and notes == []
    assert len(APROVADO) <= authoring.PACKAGE_TITLE_IDEAL < authoring.PACKAGE_TITLE_GEN_MAX


def test_titulo_longo_nunca_sai_truncado():
    rejected: list[str] = []
    cands = _norm([_item(t) for t in LONGOS] + [_item(APROVADO, "7 DIAS")], rejected)
    titles = [c.title for c in cands]
    assert APROVADO in titles
    for c in cands:
        assert len(c.title) <= authoring.PACKAGE_TITLE_GEN_MAX
        # o que ficou é um título inteiro: o original ou o original sem a segunda promessa entre parênteses
        assert any(c.title == t or t.startswith(c.title + " (") for t in [*LONGOS, APROVADO])
    # os longos sem parênteses vão para a lista dos descartados (o LLM refaz), sem corte no meio da palavra
    assert LONGOS[1] in rejected and LONGOS[2] in rejected and LONGOS[3] in rejected


def test_segunda_promessa_entre_parenteses_sai_inteira():
    title, notes = authoring.normalize_generated_title(LONGOS[0])
    assert title == "GTA 6 já no console: o plano da primeira semana"
    assert any("segunda promessa" in n for n in notes)


def test_caixa_alta_no_maximo_duas_palavras():
    title, notes = authoring.normalize_generated_title("FAÇA ISSO ANTES DE JOGAR GTA 6!")
    assert title == "FAÇA ISSO antes de jogar GTA 6!"
    assert any("caixa alta" in n for n in notes)
    # siglas e números não contam
    assert authoring.normalize_generated_title("GTA 6: POR QUE SÓ 30 FPS?")[0] == "GTA 6: POR QUE SÓ 30 FPS?"


def _online() -> None:
    set_settings(get_settings().model_copy(update={"offline": False}))


def test_package_ideas_refaz_os_titulos_longos(monkeypatch: pytest.MonkeyPatch):
    _online()
    calls: list[str] = []
    answers = [
        {"candidates": [_item(LONGOS[1], "PRAZO"), _item(APROVADO, "7 DIAS", "numero-lista")]},
        {"candidates": [_item("GTA 6: NÃO compre o upgrade ainda", "ESPERE")]},
    ]

    def fake(task, system, prompt, schema, context, project_id, job_id):
        calls.append(prompt)
        return SimpleNamespace(data=answers[len(calls) - 1]), "fake"

    monkeypatch.setattr(authoring, "_llm", fake)
    doc = service.create_project(title="GTA 6", topic="GTA 6 primeira semana", style_id=LONG)
    cands, _ = authoring.package_ideas(doc, {}, get_style(LONG), 3, 2)
    assert len(calls) == 2 and "REFAÇA 2 candidato" in calls[1] and LONGOS[1] in calls[1]
    assert [c.title for c in cands] == [APROVADO, "GTA 6: NÃO compre o upgrade ainda"]
    assert all("AVISO" not in n for c in cands for n in c.score.get("notes") or [])
    # o pedido traz as regras novas com os exemplos bons e ruins
    system = " ".join(authoring.PACKAGE_SYSTEM.split())
    assert APROVADO in system and LONGOS[0] in system
    assert f"nunca mais de {authoring.PACKAGE_TITLE_GEN_MAX}" in calls[0]


def test_package_ideas_avisa_quando_o_refeito_ainda_e_longo(monkeypatch: pytest.MonkeyPatch):
    _online()
    data = {"candidates": [_item(LONGOS[2]), _item(APROVADO, "7 DIAS")]}
    monkeypatch.setattr(authoring, "_llm", lambda *a, **k: (SimpleNamespace(data=data), "fake"))
    doc = service.create_project(title="GTA 6", style_id=LONG)
    cands, _ = authoring.package_ideas(doc, {}, get_style(LONG), 3, 2)
    assert [c.title for c in cands] == [APROVADO]
    assert any(n.startswith("AVISO: 1 título") for n in cands[0].score["notes"])


def test_offline_gera_titulos_curtos():
    doc = service.create_project(title="GTA 6", topic="GTA 6: guia da primeira semana jogando (dia a dia)",
                                 style_id=LONG)
    cands, provider = authoring.package_ideas(doc, {}, get_style(LONG), 5, 3)
    assert provider == "offline" and len(cands) == 5
    assert all(len(c.title) <= authoring.PACKAGE_TITLE_GEN_MAX for c in cands)


# --- thumb: texto total visível ----------------------------------------------------------------------------------
def test_pilula_e_cartoes_saem_quando_passam_de_3_palavras():
    cands = _norm([
        _item("GTA 6: a semana 1", "SEMANA 1", pill="SEM SPOILER"),
        _item("GTA 6: o que procurar", "NÃO PROCURE", pill="CONFIRMADO"),
        _item("GTA 6: sua primeira semana", "7 DIAS", "numero-lista",
              cards=[{"asset": None, "label": "PRAZO"}, {"asset": None, "label": "NÃO GASTE"},
                     {"asset": None, "label": "EXPLORE"}]),
        _item("GTA 6: três regras", "3", "numero-lista",
              cards=[{"asset": None, "label": "PRAZO"}, {"asset": None, "label": "GOLPE"}]),
        _item("GTA 6: o mapa", "MAPA", pill="ANTES"),
    ])
    sem_spoiler, confirmado, sete, tres, mapa = cands
    assert sem_spoiler.layers.pill is None and confirmado.layers.pill is None  # selo/rótulo genérico
    assert any("selo" in n for n in confirmado.score["notes"])
    assert sete.layers.cards == [] and sete.layers.text.words == "7 DIAS"  # número + 1 palavra, sem cartões
    assert any("cartões saíram" in n for n in sete.score["notes"])
    assert [c.label for c in tres.layers.cards] == ["PRAZO", "GOLPE"]  # cabem nas 3 palavras: ficam
    assert mapa.layers.pill == "ANTES"  # 2 palavras no total
    for c in cands:
        assert authoring.thumb_visible_words(c.thumb_text, c.layers.pill, list(c.layers.cards)) <= 3


def test_camadas_padrao_sem_pilula_nem_cartoes():
    for tpl in ("heroi-palavra", "numero-lista", "avatar-reage", "lugar-mapa"):
        layers = T.default_layers(tpl, {"words": "7 DIAS"})
        assert layers.pill is None and layers.cards == []


def test_numero_sem_cartoes_usa_a_coluna_larga():
    base = {"template": "numero-lista", "background": {"kind": "gradient", "ref": "vice"},
            "text": {"words": "7 DIAS", "pos": "left", "size": 0.6}}
    solo = C.render(ThumbLayers.model_validate(base), T.CANVAS)
    with_cards = C.render(ThumbLayers.model_validate({**base, "cards": [{"label": "PRAZO"}]}), T.CANVAS)
    w_solo = solo.info["layout"]["text_bbox"][2]
    w_cards = with_cards.info["layout"]["text_bbox"][2]
    assert w_cards <= 0.31 * T.CANVAS[0] and w_cards < w_solo <= 0.47 * T.CANVAS[0]


# --- checagem ----------------------------------------------------------------------------------------------------
def test_check_conta_o_texto_total_e_o_titulo():
    layers = ThumbLayers(template="numero-lista", text=TextLayer(words="7 DIAS", size=0.6), pill="SEM SPOILER",
                         cards=[CardLayer(label="PRAZO"), CardLayer(label="NÃO GASTE")])
    img = C.render(layers, T.CANVAS)
    score = K.check(img, layers, True, title=LONGOS[0], title_max=authoring.PACKAGE_TITLE_TARGET)
    assert score["words"] == 2 and score["words_total"] == 7
    assert any("texto total visível com 7 palavras" in w for w in score["warnings"])
    assert any(w.startswith("título com") for w in score["warnings"])
    enxuto = ThumbLayers(template="numero-lista", text=TextLayer(words="7 DIAS", size=0.6))
    ok = K.check(C.render(enxuto, T.CANVAS), enxuto, True, title=APROVADO, title_max=authoring.PACKAGE_TITLE_TARGET)
    assert ok["words_total"] == 2 and ok["title_len"] == len(APROVADO)
    assert not any("texto total" in w or w.startswith("título com") for w in ok["warnings"])
    # sem `title_max` (título manual) não avisa o tamanho
    assert not any(w.startswith("título com") for w in K.check(img, layers, True, title=LONGOS[0])["warnings"])


def test_check_do_pacote_avisa_titulo_gerado_e_nao_o_manual():
    layers = ThumbLayers(template="heroi-palavra", text=TextLayer(words="TUDO"))
    img = C.render(layers, T.CANVAS)
    auto = PackageCandidate(title=LONGOS[1], thumb_text="TUDO", layers=layers, source="auto")
    manual = auto.model_copy(update={"source": "manual"})
    assert any(w.startswith("título com") for w in tasks._check(img, auto, True)["warnings"])
    assert not any(w.startswith("título com") for w in tasks._check(img, manual, True)["warnings"])


# --- correções da revisão ----------------------------------------------------------------------------------------
def test_segunda_chamada_que_falha_nao_derruba_o_pacote(monkeypatch: pytest.MonkeyPatch):
    _online()
    calls: list[str] = []

    def fake(task, system, prompt, schema, context, project_id, job_id):
        calls.append(prompt)
        if len(calls) == 2:
            raise RuntimeError("todos os provedores falharam")
        return SimpleNamespace(data={"candidates": [_item(LONGOS[2]), _item(APROVADO, "7 DIAS")]}), "fake"

    monkeypatch.setattr(authoring, "_llm", fake)
    doc = service.create_project(title="GTA 6", style_id=LONG)
    cands, _ = authoring.package_ideas(doc, {}, get_style(LONG), 3, 2)
    assert len(calls) == 2 and [c.title for c in cands] == [APROVADO]
    assert any(n.startswith("AVISO: 1 título") for n in cands[0].score["notes"])


def test_titulo_com_prazo_vencido_na_publicacao_ganha_aviso_e_vai_para_o_fim():
    from datetime import date

    hoje = date(2026, 11, 17)
    assert authoring.title_deadline("GTA 6: o bônus que SOME dia 20", hoje) == date(2026, 11, 20)
    assert authoring.title_deadline("GTA 6: até 20/11 de graça", hoje) == date(2026, 11, 20)
    for t in (APROVADO, "GTA 6 em 7 dias", "GTA 6: 24/7 no mapa", "dia 1 do lançamento", "até 30 dias"):
        assert authoring.title_deadline(t, hoje) is None
    items = [_item("GTA 6: o bônus que SOME dia 20", "CORRE!"), _item(APROVADO, "7 DIAS")]
    cands = authoring._normalize_candidates(items, assets=authoring.PackageAssets(), templates=["heroi-palavra"],
                                            is_long=True, publish_date=date(2026, 11, 21))
    assert [c.title for c in cands] == [APROVADO, "GTA 6: o bônus que SOME dia 20"]
    assert any("vence antes da publicação" in n for n in cands[1].score["notes"])
    # publicado antes do prazo: sem aviso
    ok = authoring._normalize_candidates(items, assets=authoring.PackageAssets(), templates=["heroi-palavra"],
                                         is_long=True, publish_date=date(2026, 11, 19))
    assert ok[0].title.endswith("dia 20") and not ok[0].score.get("notes")


def test_prompt_do_pacote_traz_a_publicacao_prevista(monkeypatch: pytest.MonkeyPatch):
    from datetime import date

    _online()
    seen: list[str] = []
    monkeypatch.setattr(authoring, "_llm", lambda task, system, prompt, *a: (
        seen.append(prompt) or SimpleNamespace(data={"candidates": [_item(APROVADO, "7 DIAS")]}), "fake"))
    doc = service.create_project(title="GTA 6", style_id=LONG)
    authoring.package_ideas(doc, {}, get_style(LONG), 1, 1, today=date(2026, 11, 17),
                            publish_date=date(2026, 11, 21))
    assert "Publicação prevista: 2026-11-21" in seen[0]
    assert tasks._planned_publish_date("prj_nao_existe", "2026-11-22") == date(2026, 11, 22)


def test_avatar_e_personagem_nao_dividem_a_thumb():
    """Um foco só: no avatar-reage/numero-lista o recorte sai; no heroi-palavra o avatar sai."""
    assets = authoring.PackageAssets(cutouts=[{"id": "casal-capa"}], poses=[{"id": "surpreso", "emotions": []}],
                                     allow_editorial=True)
    num = _item("GTA 6: seus 7 dias", "7 DIAS", template="numero-lista")
    num.update(characters_hint=["casal-capa"], presenter_pose="surpreso")
    heroi = _item("GTA 6: a Lucia voltou", "VOLTOU")
    heroi.update(characters_hint=["casal-capa"], presenter_pose="surpreso")
    a, b = authoring._normalize_candidates([num, heroi], assets=assets,
                                           templates=["heroi-palavra", "numero-lista"], is_long=True)
    assert a.layers.presenter is not None and not a.layers.characters
    assert b.layers.characters and b.layers.presenter is None
