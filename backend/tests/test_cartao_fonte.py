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
from studio.core.sources import title_backs_source
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
               _title("scn_stamp", "1. DOWNLOAD DE PC", 1, source="Malwarebytes", stamp={"pt-BR": "GOLPE"}))
    lone = {"text": "2. TAMANHO QUE NÃO BATE", "card": {"source": "Malwarebytes"}}
    timeline.drop_foreign_source(doc, doc.scene("scn_list"), lone, None)
    assert "card" not in lone
    # com carimbo, a fonte apoia o veredito e fica (fundo desenhado pelo app)
    stamped = {"text": "1. DOWNLOAD DE PC", "card": {"source": "Malwarebytes", "stamp": "GOLPE"}}
    timeline.drop_foreign_source(doc, doc.scene("scn_stamp"), stamped, None)
    assert stamped["card"]["source"] == "Malwarebytes"
    backed = {"text": "VIDAR: LADRÃO DE DADOS", "card": {"source": "Malwarebytes"}}
    timeline.drop_foreign_source(doc, doc.scene("scn_list"), backed, None)
    assert backed["card"] == {"source": "Malwarebytes"}
