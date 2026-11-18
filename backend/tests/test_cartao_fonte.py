"""Cartão de dado x fala e "Fonte:" coerente (crítica do vídeo do GTA 6 no PC, 18/11/2026).

- O decimal falado ("um vírgula um megabyte") é número: vira "1,1" na legenda, cobra o cartão e acha o momento em
  que o número é dito (o cartão "1,1 MB" nunca entrava porque a fala não tinha número nenhum para o leitor).
- O título que não diz nada da fonte citada ("2. TAMANHO QUE NÃO BATE" com "Fonte: Malwarebytes", numa conclusão que
  junta o 1,1 MB da Malwarebytes e a ISO da ESET) perde a fonte, no storyboard e na timeline.
"""

from __future__ import annotations

import pytest

from studio.core import timeline
from studio.core.numbers import merge_number_words, notable_number_runs, number_runs, number_words
from studio.core.project.document import ProjectDoc, Scene, SceneCard, Segment, Source, Visual
from studio.core.sources import title_backs_source, title_keeps_source
from studio.providers.base import Word
from studio.workers.pipeline import storyboard_check

# --- decimal falado -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("um instalador de cerca de um vírgula um megabyte.", [(5, 8, 1.1)]),
        ("custa dois vírgula cinco milhões de reais", [(1, 5, 2_500_000)]),
        ("treze vírgula setenta e cinco por cento", [(0, 7, 13.75)]),
        ("treze vírgula sete cinco por cento", [(0, 6, 13.75)]),  # como o num2words lê: dígito a dígito
        ("dois vírgula zero cinco", [(0, 4, 2.05)]),
        ("um vírgula cinco mil pessoas", [(0, 4, 1500)]),
    ],
)
def test_spoken_decimal_is_one_number(text, expected):
    assert number_runs(text.split(), "pt-BR") == expected


@pytest.mark.parametrize("text", ["coloca uma vírgula aqui", "dois vírgula, tipo assim"])
def test_comma_word_without_digits_after_is_not_a_decimal(text):
    runs = number_runs(text.split(), "pt-BR")
    assert all(isinstance(v, int) for _a, _b, v in runs)
    assert not any(v == 1 for _a, _b, v in runs)  # "uma vírgula" continua artigo


@pytest.mark.parametrize("value", [1.1, 2.05, 13.75, 0.5, 1.8])
def test_spoken_decimal_round_trips_with_the_voice_text(value):
    spoken = number_words(value, "pt-BR")
    assert [v for _a, _b, v in number_runs(spoken.split(), "pt-BR")] == [value]


def test_spoken_decimal_is_notable_and_becomes_digits_in_the_caption():
    tokens = "Um desses arquivos, de um vírgula um megabyte, não é o jogo:".split()
    assert notable_number_runs(tokens, "pt-BR") == [(4, 7, 1.1)]
    merged = merge_number_words([Word(t, i, i + 1) for i, t in enumerate(tokens)], "pt-BR")
    assert [w.text for w in merged][3:6] == ["de", "1,1", "megabyte,"]
    assert (merged[4].start, merged[4].end) == (4, 7)


def test_card_reveal_finds_the_spoken_decimal():
    words = [Word(t, i * 0.3, i * 0.3 + 0.25) for i, t in enumerate("baixava um instalador de um vírgula um megabyte".split())]
    hit = timeline._spoken_number(words, "pt-BR", [1.1], fallback=False)
    assert hit is not None and hit.text == "um" and hit.start == pytest.approx(1.2)


# --- fonte sobre título ---------------------------------------------------------------------------------------------

SOURCES = [
    Source(title="Malwarebytes – Sites falsos de demo do GTA 6 distribuem infostealer",
           note="Agosto de 2026: sites tipo gta6demo, gta6_installer.exe de ~1,1 MB, Vidar roubando dados de 19 "
                "navegadores; orientações pós-infecção."),
    Source(title="WeLiveSecurity (ESET) – GTA 6: golpes com downloads falsos e malware aumentam antes do lançamento",
           note="13/11/2026: ISO de 100+ GB com njRAT e DCRat (citando Huntress), golpes em cripto, ransomware/wiper "
                "Chaos."),
]


@pytest.mark.parametrize(
    ("title", "source", "backed"),
    [
        ("2. TAMANHO QUE NÃO BATE", "Malwarebytes", False),  # índice de lista e conclusão do vídeo
        ("ATIVE A VERIFICAÇÃO EM 2 ETAPAS", "Malwarebytes", False),  # o 2 não é número da fonte
        ("VIDAR: LADRÃO DE DADOS", "Malwarebytes", True),
        ("19 NAVEGADORES", "Malwarebytes", True),
        ("RANSOMWARE + WIPER", "WeLiveSecurity (ESET)", True),
        ("3. ISO DE 100 GB", "WeLiveSecurity (ESET)", True),
        ("QUALQUER COISA", "Fonte Fora Do Roteiro", True),  # não dá para conferir: fica
        # outra flexão da mesma palavra (plural, particípio x gerúndio)
        ("GOLPE DO DOWNLOAD FALSO", "WeLiveSecurity (ESET)", True),
        ("O SITE FALSO", "Malwarebytes", True),
        ("SENHAS ROUBADAS", "Malwarebytes", True),
        ("COOKIES ROUBADOS", "Malwarebytes", True),
        ("2. TAMANHO QUE NÃO BATE", "WeLiveSecurity (ESET)", False),
    ],
)
def test_title_backs_source(title, source, backed):
    assert title_backs_source(title, source, SOURCES, "pt-BR") is backed


def _doc(*scenes: Scene) -> ProjectDoc:
    seg = Segment(id="seg_a", text={"pt-BR": "Dois: o tamanho não bate, tipo um arquivinho de um megabyte ou uma ISO."})
    return ProjectDoc(id="prj_x", title="t", topic="t", style_id="sty_x", primary_language="pt-BR", languages=["pt-BR"],
                      segments=[seg], scenes=list(scenes), sources=SOURCES)


def _title(scene_id: str, text: str, part: int, **card) -> Scene:
    return Scene(id=scene_id, segment_id="seg_a", part=part, parts=2, kind="title", visual=Visual(),
                 overlay_text={"pt-BR": text}, card=SceneCard(**card))


def test_lone_source_check_drops_source_from_title_that_says_nothing_of_it():
    doc = _doc(_title("scn_list", "2. TAMANHO QUE NÃO BATE", 0, source="Malwarebytes"),
               _title("scn_vidar", "VIDAR: LADRÃO DE DADOS", 1, source="Malwarebytes"))
    beats = [storyboard_check.Beat(key=f"seg_a:{i}", segment_id="seg_a", part=i, parts=2, index=0, role="body",
                                   words=["Dois:"], seconds=2.0) for i in range(2)]
    issues = storyboard_check.fix_lone_sources(doc, beats)
    assert [(i.code, i.scene_id) for i in issues] == [("fonte_solta", "scn_list")]
    assert doc.scene("scn_list").card is None
    assert doc.scene("scn_vidar").card.source == "Malwarebytes"


def test_timeline_drops_source_from_title_that_says_nothing_of_it():
    doc = _doc(_title("scn_list", "2. TAMANHO QUE NÃO BATE", 0, source="Malwarebytes"),
               _title("scn_vidar", "VIDAR: LADRÃO DE DADOS", 1, source="Malwarebytes"))
    lone = {"text": "2. TAMANHO QUE NÃO BATE", "card": {"source": "Malwarebytes"}}
    timeline.drop_foreign_source(doc, doc.scene("scn_list"), lone, None)
    assert "card" not in lone
    backed = {"text": "VIDAR: LADRÃO DE DADOS", "card": {"source": "Malwarebytes"}}
    timeline.drop_foreign_source(doc, doc.scene("scn_vidar"), backed, None)
    assert backed["card"] == {"source": "Malwarebytes"}


def test_timeline_keeps_source_on_title_with_stamp():
    doc = _doc(_title("scn_stamp", "1. DOWNLOAD DE PC", 0, source="Malwarebytes", stamp={"pt-BR": "GOLPE"}))
    stamped = {"text": "1. DOWNLOAD DE PC", "card": {"source": "Malwarebytes", "stamp": "GOLPE"}}
    timeline.drop_foreign_source(doc, doc.scene("scn_stamp"), stamped, None)
    assert stamped["card"]["source"] == "Malwarebytes"


def test_dub_keeps_the_same_credit_as_the_primary_language():
    """A conta é feita no título do idioma principal (o das fontes): o título traduzido não perde o crédito."""
    scene = _title("scn_vidar", "VIDAR: LADRÃO DE DADOS", 0, source="Malwarebytes")
    scene.overlay_text["en"] = "VIDAR: THE DATA THIEF"
    doc = _doc(scene)
    dubbed = {"text": "VIDAR: THE DATA THIEF", "card": {"source": "Malwarebytes"}}
    timeline.drop_foreign_source(doc, scene, dubbed, None)
    assert dubbed["card"] == {"source": "Malwarebytes"}


def _beat(part: int, text: str, parts: int = 2) -> storyboard_check.Beat:
    return storyboard_check.Beat(key=f"seg_a:{part}", segment_id="seg_a", part=part, parts=parts, index=0,
                                 role="body", words=text.split(), seconds=3.0)


def test_title_keeps_source_cited_by_the_same_passage():
    """O conselho no meio das orientações da fonte fica com o mesmo crédito das outras cenas do trecho."""
    advice = Scene(id="scn_txt", segment_id="seg_a", part=0, parts=2, kind="text", visual=Visual(),
                   overlay_text={"pt-BR": "Comece pelo e-mail principal"}, card=SceneCard(source="Malwarebytes"))
    doc = _doc(advice, _title("scn_2fa", "ATIVE A VERIFICAÇÃO EM 2 ETAPAS", 1, source="Malwarebytes"))
    assert title_keeps_source(doc, doc.scene("scn_2fa"), "Malwarebytes")
    beats = [_beat(i, "Ative a verificação em duas etapas.") for i in range(2)]
    assert storyboard_check.fix_lone_sources(doc, beats) == []
    assert doc.scene("scn_2fa").card.source == "Malwarebytes"


def test_source_found_by_url_domain_when_title_has_no_publisher():
    sources = [Source(title="Sites falsos de demo do GTA 6 distribuem infostealer",
                      url="https://www.malwarebytes.com/pt-br/blog/threat-intel/2026/08/fake-gta-6-demo-sites",
                      note="Vidar roubando dados de 19 navegadores.")]
    assert title_backs_source("2. TAMANHO QUE NÃO BATE", "Malwarebytes", sources, "pt-BR") is False
    assert title_backs_source("O SITE FALSO", "Malwarebytes", sources, "pt-BR") is True


# --- número do cartão x fala ----------------------------------------------------------------------------------------


def _stat(value: str, *, source: str | None = None, locked: bool = False) -> Scene:
    return Scene(id="scn_stat", segment_id="seg_a", part=0, parts=1, kind="stat", visual=Visual(), locked=locked,
                 card=SceneCard(value={"pt-BR": value}, label={"pt-BR": "ISO FALSA"}, source=source))


# Outra fonte com números perto dos da fala (o cartão pode vir dela: não é número inventado).
OTHER = Source(title="Outra – estimativa", note="ISO de 87 GB; 475 milhões de pessoas.")


def _stat_doc(value: str, *, locked: bool = False) -> ProjectDoc:
    doc = _doc(_stat(value, locked=locked))
    doc.sources = [*SOURCES, OTHER]
    return doc


@pytest.mark.parametrize(
    ("value", "speech", "after"),
    [
        ("87 GB+", "uma ISO de cem gigabytes que não é o jogo.", "100 GB+"),
        ("475 MI", "foram quinhentos milhões de pessoas.", "500 MI"),
        ("1 MB", "um instalador de um vírgula cinco megabyte.", "1,5 MB"),
    ],
)
def test_card_follows_the_number_spoken_in_its_beat(value, speech, after):
    doc = _stat_doc(value)
    issues = storyboard_check.fix_card_speech(doc, [_beat(0, speech, parts=1)])
    assert [i.code for i in issues] == ["cartao_segue_fala"]
    assert doc.scene("scn_stat").card.value["pt-BR"] == after


@pytest.mark.parametrize(
    ("value", "speech"),
    [
        ("100 GB+", "uma ISO de cem gigabytes."),  # bate
        ("100 GB+", "uma ISO enorme, maior que o jogo."),  # a fala não lê o número: o cartão fica
        ("1º TRI 2027", "no primeiro trimestre de dois mil e vinte e sete."),  # ordinal
        ("24 H", "em um dia inteiro."),  # "um" é artigo
        ("999 GB", "uma ISO de cem gigabytes."),  # número inventado: `numero_inventado` troca o cartão
    ],
)
def test_card_matching_or_unspoken_stays(value, speech):
    doc = _stat_doc(value)
    assert storyboard_check.fix_card_speech(doc, [_beat(0, speech, parts=1)]) == []
    assert doc.scene("scn_stat").card.value["pt-BR"] == value


@pytest.mark.parametrize(
    ("speech", "locked"),
    [
        ("uma ISO de cem gigabytes e um arquivo de vinte megabytes.", False),  # dois números: qual?
        ("uma ISO de quinhentos gigabytes.", False),  # outra ordem de grandeza: não é o mesmo dado
        ("uma ISO de cem gigabytes.", True),  # cena travada
    ],
)
def test_card_that_contradicts_the_speech_is_flagged(speech, locked):
    doc = _stat_doc("87 GB+", locked=locked)
    issues = storyboard_check.fix_card_speech(doc, [_beat(0, speech, parts=1)])
    assert [(i.code, i.severity) for i in issues] == [("cartao_contradiz_fala", storyboard_check.GRAVE)]
    assert doc.scene("scn_stat").card.value["pt-BR"] == "87 GB+"


# --- o mesmo fato com a mesma fonte ---------------------------------------------------------------------------------


def test_fact_number_is_credited_to_the_source_that_has_it():
    doc = _doc(_stat("100 GB+", source="Malwarebytes"))
    issues = storyboard_check.fix_fact_sources(doc, [_beat(0, "uma ISO de cem gigabytes.", parts=1)])
    assert [i.code for i in issues] == ["fonte_do_dado"]
    assert doc.scene("scn_stat").card.source == "WeLiveSecurity (ESET)"


@pytest.mark.parametrize(
    ("value", "source"),
    [
        ("19", "Malwarebytes"),  # a fonte citada traz o número
        ("2026", "Malwarebytes"),  # as duas trazem: não dá para dizer de qual
        ("42", "Malwarebytes"),  # nenhuma traz (é da narração)
        ("100 GB+", "Fonte Fora Do Roteiro"),  # não dá para conferir
    ],
)
def test_fact_source_stays_when_it_cannot_be_told_apart(value, source):
    doc = _doc(_stat(value, source=source))
    assert storyboard_check.fix_fact_sources(doc, [_beat(0, "texto.", parts=1)]) == []
    assert doc.scene("scn_stat").card.source == source


# --- decimal depois de milhar ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", [1000.5, 1250.5, 2_000_000.5, 1500.25])
def test_spoken_decimal_after_thousands_round_trips(value):
    spoken = number_words(value, "pt-BR")
    assert [v for _a, _b, v in number_runs(spoken.split(), "pt-BR")] == [value]


def test_decimal_after_thousands_in_the_caption_keeps_the_group_separator():
    tokens = "custou mil duzentos e cinquenta vírgula cinco reais".split()
    merged = merge_number_words([Word(t, i, i + 1) for i, t in enumerate(tokens)], "pt-BR")
    assert [w.text for w in merged] == ["custou", "1.250,5", "reais"]
