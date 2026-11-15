"""Corte da narração por frase (batidas) e números falados, com os textos reais do Short do GTA 6."""

from __future__ import annotations

import pytest

from studio.core.numbers import card_number, is_number_token, merge_number_words, number_runs, text_numbers
from studio.core.text import normalize_token, span_ranges, split_beats, word_count, words
from studio.providers.base import Word

# Segmentos do projeto prj_8befnbkkzr (cópia do banco, versão 6), na ordem.
GTA = [
    "GTA 6 vai ser o melhor jogo de todos os tempos? Tem um detalhe que quase ninguém comenta.",
    "Pensa comigo. GTA 5 saiu em dois mil e treze. Vendeu mais de duzentos milhões de cópias. Depois disso, silêncio.",
    "Até dezembro de dois mil e vinte e três. O primeiro trailer passou de noventa milhões de visualizações em "
    "vinte e quatro horas. Recorde no YouTube para um vídeo que não era de música.",
    "Em maio de dois mil e vinte e cinco, o segundo trailer foi ainda mais longe. Quatrocentos e setenta e cinco "
    "milhões de visualizações em um dia, somando as plataformas.",
    "E o cenário? Leonida. A volta de Vice City. Com Lucia, a primeira protagonista mulher da era moderna da série.",
    "Agora, o segredo. Depois de dois adiamentos, quase ninguém fora da Rockstar jogou um minuto sequer. Melhor da "
    "história ainda é promessa. O recorde que ele já quebrou é outro: o da expectativa.",
    "Siga para descobrir o próximo mistério.",
]
# Ritmo do estilo de games: 150 palavras/min, voz 1,05×, cenas de 1,8 a 3,0 s.
SHORT = {"wpm": 150, "speed": 1.05, "min_s": 1.8, "max_s": 3.0}


def _beats(text: str, **kw) -> list[str]:
    return [" ".join(b) for b in split_beats(text, **{**SHORT, **kw})]


def _secs(beat: list[str]) -> float:
    return len(beat) / (SHORT["wpm"] * SHORT["speed"]) * 60


def test_split_beats_keeps_every_word_in_order():
    total = 0
    for text in GTA:
        beats = split_beats(text, **SHORT)
        assert [w for b in beats for w in b] == words(text)
        assert sum(len(b) for b in beats) == word_count(text)
        assert all(beats)
        total += len(beats)
    # 21 batidas contra as 29 cenas cortadas por caracteres
    assert total == 21


def test_split_beats_never_cuts_inside_numbers():
    phrases = [
        "dois mil e treze",
        "duzentos milhões de cópias",
        "dois mil e vinte e três",
        "noventa milhões de visualizações",
        "vinte e quatro horas",
        "dois mil e vinte e cinco",
        "quatrocentos e setenta e cinco milhões de visualizações",
        "dois adiamentos",
    ]
    joined = [b.lower() for text in GTA for b in _beats(text)]
    for phrase in phrases:
        assert any(phrase in beat for beat in joined), phrase
    # nenhuma fronteira de batida cai dentro de um número
    for text in GTA:
        tokens = words(text)
        cuts, pos = set(), 0
        for beat in split_beats(text, **SHORT)[:-1]:
            pos += len(beat)
            cuts.add(pos)
        for i0, i1, _ in number_runs(tokens):
            assert not any(i0 < k < i1 for k in cuts), (text, tokens[i0:i1])


def test_split_beats_never_ends_on_preposition_or_article():
    stops = {"de", "do", "da", "o", "a", "os", "as", "um", "uma", "em", "no", "na", "para", "e", "com"}
    for text in GTA:
        for beat in split_beats(text, **SHORT)[:-1]:
            assert normalize_token(beat[-1]) not in stops, beat


def test_split_beats_cuts_at_sentence_end_and_merges_short():
    beats = _beats(GTA[1])
    # "Pensa comigo." (0,8 s, 2 palavras) se junta à frase seguinte; "Depois disso, silêncio." (frase de efeito)
    # não cola na frase do número: fica sozinha
    assert beats == [
        "Pensa comigo. GTA 5 saiu em dois mil e treze.",
        "Vendeu mais de duzentos milhões de cópias.",
        "Depois disso, silêncio.",
    ]
    assert _beats(GTA[0]) == [
        "GTA 6 vai ser o melhor jogo de todos os tempos?",
        "Tem um detalhe que quase ninguém comenta.",
    ]
    # frase curta sozinha num segmento fica como está
    assert _beats("Leonida.") == ["Leonida."]
    assert _beats(GTA[6]) == ["Siga para descobrir o próximo mistério."]


def test_split_beats_prefers_comma():
    beats = _beats(GTA[3])
    assert beats[0] == "Em maio de dois mil e vinte e cinco,"
    assert beats[1].startswith("o segundo trailer")
    assert beats[2] == "Quatrocentos e setenta e cinco milhões de visualizações"
    assert beats[3] == "em um dia, somando as plataformas."


def test_split_beats_respects_min_and_max():
    for text in GTA:
        beats = split_beats(text, **SHORT)
        for beat in beats:
            assert _secs(beat) <= 1.6 * SHORT["max_s"] + 1e-9
            if len(beats) > 1 and _secs(beat) < SHORT["min_s"] - 1e-9:
                # só a frase curta de efeito (3+ palavras, ~0,9 s, sem número) fica abaixo do mínimo
                assert len(beat) >= 3 and _secs(beat) >= 0.9 and not number_runs(beat), beat


def test_split_beats_keeps_short_dramatic_sentence_off_the_number():
    # "Agora, o segredo." ia colar em "Depois de dois adiamentos...": o cartão do 2 cobriria a virada
    assert _beats(GTA[5])[0] == "Agora, o segredo."
    assert _beats(GTA[5])[1].startswith("Depois de dois adiamentos,")
    # frase curta perto de frase sem número continua juntando
    assert _beats(GTA[4]) == [
        "E o cenário? Leonida. A volta de Vice City.",
        "Com Lucia, a primeira protagonista mulher da era moderna da série.",
    ]
    # duas palavras (0,8 s) ainda juntam, mesmo na frente de um número
    assert _beats("Pensa comigo. Vendeu duzentos milhões de cópias no mundo inteiro.") == [
        "Pensa comigo. Vendeu duzentos milhões de cópias no mundo inteiro."]
    # frase curta com número próprio segue a regra de sempre (junta à seguinte)
    assert _beats("Dez anos atrás. Vendeu duzentos milhões de cópias no mundo inteiro.") == [
        "Dez anos atrás. Vendeu duzentos milhões de cópias no mundo inteiro."]


def test_split_beats_long_sentence_without_valid_cut_stays_whole():
    text = "Trezentos e quarenta e cinco mil novecentos e oitenta e sete milhões de pessoas de todo o mundo da terra"
    beats = split_beats(text, wpm=150, speed=1.0, min_s=3.0, max_s=3.0)
    assert len(beats) == 1 and beats[0] == words(text)


def test_split_beats_slow_style_keeps_longer_beats():
    beats = _beats(GTA[2], min_s=3.5, max_s=6.0)
    # cenas de 3,5 a 6 s: a primeira frase (3,4 s) junta à seguinte e nada passa de 1,6 × 6 s
    assert beats == [
        "Até dezembro de dois mil e vinte e três. O primeiro trailer passou de noventa milhões de visualizações "
        "em vinte e quatro horas.",
        "Recorde no YouTube para um vídeo que não era de música.",
    ]


def test_split_beats_empty_text():
    assert split_beats("", **SHORT) == []
    assert split_beats("  — ", **SHORT) == []


def test_span_ranges_exact_and_proportional():
    assert span_ranges([3, 2, 4], 9) == [(0, 3), (3, 5), (5, 9)]
    # outra contagem de palavras (tradução): proporcional, contíguo e até o fim
    assert span_ranges([3, 2, 4], 18) == [(0, 6), (6, 10), (10, 18)]
    assert span_ranges([5, 5], 3) == [(0, 2), (2, 3)]
    assert span_ranges([0, 4], 4) == [(0, 0), (0, 4)]
    assert span_ranges([0, 0], 4) == [(0, 2), (2, 4)]
    assert span_ranges([], 4) == []
    # as batidas do storyboard batem com as palavras do segmento
    beats = split_beats(GTA[3], **SHORT)
    ranges = span_ranges([len(b) for b in beats], word_count(GTA[3]))
    assert [words(GTA[3])[a:b] for a, b in ranges] == beats


def test_number_runs_pt():
    values = [v for text in GTA for v in text_numbers(text)]
    assert values == [2013, 200_000_000, 2023, 90_000_000, 24, 2025, 475_000_000, 2]
    tokens = words(GTA[3])
    runs = number_runs(tokens)
    assert [(" ".join(tokens[a:b]), v) for a, b, v in runs] == [
        ("dois mil e vinte e cinco,", 2025),
        ("Quatrocentos e setenta e cinco milhões", 475_000_000),
    ]
    # "GTA 6" é nome, não número (a não ser que se peça os nomes também)
    assert text_numbers(GTA[0]) == []
    assert text_numbers(GTA[0], names=True) == [6]
    assert text_numbers("O iPhone 15 chegou") == []
    # "um"/"uma" sozinhos são artigo; "dois adiamentos" vale 2
    assert text_numbers("em um dia e um minuto") == []
    assert text_numbers("Depois de dois adiamentos") == [2]
    assert text_numbers("um milhão e meio de cópias") == [1_500_000]
    assert text_numbers("dois milhões e duzentos mil") == [2_200_000]
    assert text_numbers("mil novecentos e oitenta e quatro") == [1984]
    assert text_numbers("dois e três") == [2, 3]
    assert text_numbers("milhões de pessoas") == []
    assert text_numbers("Em 2013, custava R$ 5.000 e 2,5 bilhões") == [2013, 5000, 2_500_000_000]
    assert text_numbers("trinta por cento") == [30]
    assert text_numbers("200 million views", "en-US") == [200_000_000]
    assert text_numbers("dois mil", "en-US") == []


def test_is_number_token():
    assert is_number_token("2013,")
    assert is_number_token("475mi")
    assert is_number_token("milhões")
    assert is_number_token("três")
    assert is_number_token("um")
    assert not is_number_token("e")
    assert not is_number_token("trailer")
    assert not is_number_token("três", "en-US")
    assert not is_number_token("3D") and not is_number_token("5G")
    assert text_numbers("um jogo em 3D com 24h de campanha") == [24]


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("475 MI", 475e6),
        ("200 MI+", 200e6),
        ("90 MI", 90e6),
        ("2×", 2),
        ("2x", 2),
        ("24 H", 24),
        ("DEZ 2023", 2023),
        ("2013", 2013),
        ("R$ 1,5 BI", 1.5e9),
        ("475M", 475e6),
        ("10 MIL", 10_000),
        ("1.000", 1000),
        ("30%", 30),
        ("100 m", 100),
        ("dois mil", 2000),
        ("", None),
        ("RECORDE", None),
    ],
)
def test_card_number_parsing(text, value):
    assert card_number(text) == value


def _timed(text: str) -> list[Word]:
    return [Word(t, i * 0.4, i * 0.4 + 0.3) for i, t in enumerate(words(text))]


def test_merge_number_words_for_captions():
    out = merge_number_words(_timed(GTA[1]))
    texts = [w.text for w in out]
    assert texts == [
        "Pensa", "comigo.", "GTA", "5", "saiu", "em", "2013.", "Vendeu", "mais", "de", "200 milhões", "de",
        "cópias.", "Depois", "disso,", "silêncio.",
    ]  # fmt: skip
    year = out[6]
    src = _timed(GTA[1])
    assert year.start == src[6].start and year.end == src[9].end  # de "dois" até "treze."
    texts = [w.text for w in merge_number_words(_timed(GTA[2] + " " + GTA[3]))]
    assert "2023." in texts and "90 milhões" in texts and "24" in texts
    assert "2025," in texts and "475 milhões" in texts
    # "dois adiamentos" (uma palavra, valor < 10) e "um minuto" ficam por extenso
    texts = [w.text for w in merge_number_words(_timed(GTA[5]))]
    assert "dois" in texts and "um" in texts
    assert [w.text for w in merge_number_words(_timed("um milhão e meio de cópias"))][0] == "1,5 milhão"
    assert [w.text for w in merge_number_words(_timed("cem mil pessoas"))][0] == "100.000"
    assert [w.text for w in merge_number_words(_timed("vinte anos"))][0] == "20"
    assert [w.text for w in merge_number_words(_timed("trinta por cento."))] == ["30%."]


def test_merge_number_words_keeps_other_languages_and_empty():
    words_en = _timed("two thousand and thirteen")
    assert merge_number_words(words_en, "en-US") == words_en
    assert merge_number_words([], "pt-BR") == []


def test_split_beats_keeps_heads_names_and_roman_numerals_with_their_number():
    long = {**SHORT, "max_s": 2.0}
    cases = [
        ("O grande salto aconteceu nos anos 90 quando os consoles passaram a ter discos e a indústria "
         "inteira mudou de patamar para sempre.", "anos 90"),
        ("Os primeiros jogos surgiram durante todo o século XIX e mudaram completamente a forma como as "
         "pessoas viam o entretenimento em casa.", "século XIX"),
        ("Todo mundo espera o lançamento oficial de GTA VI desde o primeiro anúncio feito pela empresa há "
         "muitos anos atrás.", "GTA VI"),
        ("Todo mundo está esperando ansiosamente pelo lançamento de Grand Theft Auto VI desde o primeiro "
         "anúncio feito pela empresa.", "Grand Theft Auto VI"),
    ]
    for text, phrase in cases:
        beats = [" ".join(b) for b in split_beats(text, **long)]
        assert len(beats) > 1 and any(phrase in b for b in beats), beats


def test_split_beats_keeps_spoken_numbers_together_in_english():
    text = ("The first trailer passed seventy-five million views in twenty-four hours and became the biggest "
            "non-music debut ever recorded on the platform.")
    beats = [" ".join(b) for b in split_beats(text, **SHORT, lang="en-US")]
    assert len(beats) > 1
    assert any("seventy-five million views" in b for b in beats) and any("twenty-four hours" in b for b in beats)
