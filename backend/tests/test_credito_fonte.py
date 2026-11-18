"""O "Fonte:" na tela diz quem assina o que aparece e só onde a cena diz algo da fonte (QA r3 do prj_gq4krrs11w).

Casos reais do "GTA 6 no PC: quando sai?":
- a captura credita quem assina a página, pelo site dela (WeLiveSecurity = ESET; a busca do Google não credita
  ninguém), e não a fonte que o roteiro pôs no cartão (a Windows Central e a Dexerto vinham trocadas em 1:41 e 1:54);
- cartão tipográfico com a fala do apresentador não leva "Fonte:" ("ou de horas de nuvem" com Windows Central,
  "cartão ou Pix" com Malwarebytes), a não ser que a frase cite a fonte ou traga um número dela;
- a figura da matéria (a busca do Google reproduzida pela Malwarebytes, 5:39) e o título da lista que junta fatos de
  duas fontes ("2. O TAMANHO NÃO BATE": 1 MB da Malwarebytes e ISO da ESET) ficam sem crédito; o dado da ISO segue
  só com a ESET, como em 3:42.
"""

from __future__ import annotations

import pytest

from studio.core import timeline
from studio.core.project.document import MediaRef, ProjectDoc, Scene, SceneCard, Segment, Source, Visual
from studio.core.sources import names_source, outlet_for_url, scene_claims_source, scene_sentence
from studio.workers.pipeline import storyboard_check

SOURCES = [
    Source(title="Rockstar Games – Grand Theft Auto VI (site oficial)", url="https://www.rockstargames.com/VI",
           note="Plataformas oficiais (PS5 e Xbox Series X|S), data de lançamento 19/11/2026; nenhuma versão de PC "
                "anunciada."),
    Source(title="Windows Central – GTA 6 will be playable on Xbox Cloud Gaming, but there are a few catches",
           url="https://www.windowscentral.com/gaming/xbox/grand-theft-auto-6-will-be-playable-on-xbox-cloud-gaming",
           note="Nuvem só em consoles Xbox (incluindo Xbox One), compra à parte; PC, celular, TV e navegador de fora."),
    Source(title="Dexerto – GTA 6 confirms Xbox Cloud Gaming support but PC players face a catch",
           url="https://www.dexerto.com/gta/gta-6-confirms-xbox-cloud-gaming-support-but-pc-players-face-a-catch/",
           note="Anúncio de 10/11 sobre nuvem e suporte a Remote Play do PlayStation e do Xbox."),
    Source(title="Malwarebytes – Sites falsos de demo do GTA 6 distribuem infostealer",
           url="https://www.malwarebytes.com/pt-br/blog/threat-intel/2026/08/fake-gta-6-extended-look-and-demo-sites",
           note="Agosto de 2026: sites tipo gta6demo, gta6_installer.exe de ~1,1 MB, Vidar roubando dados de 19 "
                "navegadores; orientações pós-infecção."),
    Source(title="WeLiveSecurity (ESET) – GTA 6: golpes com downloads falsos e malware aumentam antes do lançamento",
           url="https://www.welivesecurity.com/pt/golpes-fraudes/gta-6-golpes-com-downloads-falsos-e-malware/",
           note="13/11/2026: ISO de 100+ GB com njRAT e DCRat (citando Huntress), golpes em cripto (US$ 250 e US$ 100), "
                "ransomware/wiper Chaos, previsões pós-lançamento."),
    Source(title="Canaltech – Cuidado: falso GTA 6 para PC espalha vírus",
           url="https://canaltech.com.br/games/cuidado-falso-gta-6-para-pc-espalha-virus/",
           note="Cobertura brasileira do golpe do falso GTA 6 para PC."),
]

MALWAREBYTES_PAGE = SOURCES[3].url
WLS_PAGE = SOURCES[4].url


def _doc(segments: dict[str, str], scenes: list[Scene]) -> ProjectDoc:
    return ProjectDoc(id="prj_x", title="GTA 6 no PC: quando sai? (NÃO baixe esse download)", topic="GTA 6 no PC",
                      style_id="sty_x", primary_language="pt-BR", languages=["pt-BR"],
                      segments=[Segment(id=k, text={"pt-BR": v}) for k, v in segments.items()],
                      scenes=scenes, sources=SOURCES)


def _scene(scene_id: str, seg: str, kind: str = "text", *, part: int = 0, parts: int = 1, span: int | None = None,
           media: MediaRef | None = None, overlay: str = "", **card) -> Scene:
    # Sem mídia, o cartão fica no fundo desenhado pelo app (`graphic`).
    visual = Visual(type="none") if media is None else Visual(prompt="x", media=media)
    return Scene(id=scene_id, segment_id=seg, part=part, parts=parts, kind=kind, visual=visual, span=span,
                 overlay_text={"pt-BR": overlay} if overlay else {}, card=SceneCard(**card))


def _capture(url: str, credit: str) -> MediaRef:
    return MediaRef(kind="capture", source="capture", asset="uploads/prj_x/media/c.jpg", url=url, page_url=url,
                    credit=credit, editorial=True, tier="capture")


def _figure(url: str, page_url: str) -> MediaRef:
    """Imagem de dentro da matéria (a figura que a matéria publica), não a captura da página."""
    return MediaRef(kind="official", source="link", asset="uploads/prj_x/media/f.jpg", url=url, page_url=page_url,
                    credit="Malwarebytes", author="Stefan Dasic", editorial=True, tier="official")


def _shown(doc: ProjectDoc, scene_id: str, card: dict) -> dict | None:
    resolved = {"card": dict(card)}
    timeline.drop_foreign_source(doc, doc.scene(scene_id), resolved, None)
    return resolved.get("card")


# --- quem assina a página ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "outlet"),
    [
        (WLS_PAGE, "WeLiveSecurity (ESET)"),
        ("https://www.welivesecurity.com/en/scams/outra-materia/", "WeLiveSecurity (ESET)"),
        (MALWAREBYTES_PAGE, "Malwarebytes"),
        ("https://www.google.com/search?q=gta+6+pc+download", ""),  # busca não é veículo
        ("https://www.google.com.br/search?q=gta+6", ""),
        ("https://www.bing.com/search?q=gta+6", ""),
        ("https://canaltech.com.br/games/outra/", "Canaltech"),  # ".com.br": o site é "canaltech", não "com"
        ("https://exemplo-desconhecido.net/post", None),
    ],
)
def test_outlet_for_url_comes_from_the_page_domain(url, outlet):
    assert outlet_for_url(url, SOURCES) == outlet


def test_outlet_for_url_knows_eset_blog_without_a_script_source():
    assert outlet_for_url(WLS_PAGE, []) == "WeLiveSecurity (ESET)"


def test_capture_of_welivesecurity_credited_to_malwarebytes_becomes_eset():
    """A captura da matéria da WeLiveSecurity com "Fonte: Malwarebytes" passa a creditar quem assina a página."""
    doc = _doc({"s": "E se você já baixou? Calma, mas age rápido."},
               [_scene("scn_cap", "s", "image", media=_capture(WLS_PAGE, "welivesecurity.com"),
                       headline={"pt-BR": "JÁ BAIXEI, E AGORA?"}, source="Malwarebytes")])
    card = _shown(doc, "scn_cap", {"headline": "JÁ BAIXEI, E AGORA?", "source": "Malwarebytes"})
    assert card == {"headline": "JÁ BAIXEI, E AGORA?", "source": "WeLiveSecurity (ESET)"}


def test_capture_of_the_sources_own_page_keeps_the_credit_without_a_claim():
    """A matéria da Malwarebytes (Stefan Dasic, "Golpes, inteligência de ameaças") é da Malwarebytes: a captura
    mostra quem assina, então o crédito fica mesmo quando a fala da cena não cita a fonte (5:42)."""
    doc = _doc({"s": "E se você já baixou? Calma, mas age rápido."},
               [_scene("scn_cap", "s", "image", media=_capture(MALWAREBYTES_PAGE, "malwarebytes.com"),
                       headline={"pt-BR": "JÁ BAIXEI, E AGORA?"}, source="Malwarebytes")])
    card = _shown(doc, "scn_cap", {"headline": "JÁ BAIXEI, E AGORA?", "source": "Malwarebytes"})
    assert card == {"headline": "JÁ BAIXEI, E AGORA?", "source": "Malwarebytes"}


@pytest.mark.parametrize(("page", "credit", "cited", "after"), [
    (SOURCES[1].url, "windowscentral.com", "Dexerto", "Windows Central"),  # 1:41: vinham trocadas
    (SOURCES[2].url, "dexerto.com", "Windows Central", "Dexerto"),  # 1:54
])
def test_swapped_capture_credits_follow_the_page(page, credit, cited, after):
    doc = _doc({"s": "No dia dez de novembro, a própria Rockstar anunciou que o GTA 6 roda no Xbox Cloud Gaming."},
               [_scene("scn_cap", "s", "image", media=_capture(page, credit), source=cited,
                       stamp={"pt-BR": "CONFIRMADO"})])
    assert _shown(doc, "scn_cap", {"stamp": "CONFIRMADO", "source": cited}) == {"stamp": "CONFIRMADO", "source": after}


def test_google_search_capture_has_no_credit():
    doc = _doc({"s": "Nada de link de anúncio, de vídeo curto ou de grupo."},
               [_scene("scn_busca", "s", "image", media=_capture("https://www.google.com/search?q=gta+6", "google.com"),
                       source="Malwarebytes", stamp={"pt-BR": "GOLPE"})])
    assert _shown(doc, "scn_busca", {"stamp": "GOLPE", "source": "Malwarebytes"}) == {"stamp": "GOLPE"}


# --- "Fonte:" só onde a cena diz algo da fonte -------------------------------------------------------------------


@pytest.mark.parametrize(("text", "cited"), [
    ("Segunda: o jogo é comprado à parte, e pra usar a nuvem você ainda precisa do Game Pass ou de horas de nuvem "
     "compradas.", "Windows Central"),
    ("O cookie é o pior: com ele, o golpista pode entrar na sua conta como se fosse você.", "Malwarebytes"),
    ("Começa pelo e-mail principal, que recupera todo o resto.", "Malwarebytes"),
    ("Depois encerra todas as sessões abertas, inclusive no Discord e nas contas de jogos, como Steam, PlayStation e "
     "Xbox.", "Malwarebytes"),
    ("E se pagou alguma coisa, com cartão ou Pix, fala com o seu banco o quanto antes e guarda print e comprovante.",
     "Malwarebytes"),
    ("Por que quase sempre cripto? Porque cripto enviada praticamente não volta.", "WeLiveSecurity (ESET)"),
])
def test_typographic_card_with_the_presenters_speech_has_no_credit(text, cited):
    """Cartão tipográfico é a fala do apresentador: palavra comum com a nota ("nuvem") não faz dela dado da fonte."""
    doc = _doc({"s": text}, [_scene("scn_txt", "s", overlay=text.split(":")[0], source=cited)])
    assert not scene_claims_source(doc, doc.scene("scn_txt"), cited)
    assert _shown(doc, "scn_txt", {"source": cited}) is None


@pytest.mark.parametrize(("text", "cited"), [
    ("A ESET descreve como um ransomware que também age como wiper.", "WeLiveSecurity (ESET)"),
    ("é um programa que rouba as senhas salvas de dezenove navegadores.", "Malwarebytes"),  # número da nota
    ("O botão Jogar agora baixava um instalador de cerca de um vírgula um megabyte.", "Malwarebytes"),
    ("Boato mais forte até agora: primeiro trimestre, segundo a GamesBeat.", "GamesBeat"),  # fora do roteiro: passa
])
def test_typographic_card_keeps_the_credit_when_the_sentence_cites_the_source(text, cited):
    doc = _doc({"s": text}, [_scene("scn_txt", "s", source=cited)])
    assert scene_claims_source(doc, doc.scene("scn_txt"), cited)
    assert _shown(doc, "scn_txt", {"source": cited}) == {"source": cited}


def test_small_numbers_and_the_topic_number_are_not_a_claim():
    """"duas etapas", "cinco sinais" e o "6" de "GTA 6" estão em qualquer nota: não ligam a cena à fonte."""
    doc = _doc({"s": "Ativa a verificação em duas etapas onde ainda não tiver, no GTA 6 e em cinco contas."},
               [_scene("scn_txt", "s", source="Malwarebytes")])
    assert not scene_claims_source(doc, doc.scene("scn_txt"), "Malwarebytes")


def test_google_results_figure_from_the_article_has_no_credit_on_unrelated_speech():
    """5:39: a figura da busca do Google publicada pela Malwarebytes, com "GOLPE", sob "Nada de link de anúncio"."""
    figure = _figure("https://www.malwarebytes.com/wp-content/uploads/sites/2/2026/08/gta-6-leak-google-results.png",
                     MALWAREBYTES_PAGE)
    doc = _doc({"s": "Nada de link de anúncio, de vídeo curto ou de grupo."},
               [_scene("scn_busca", "s", "image", media=figure, stamp={"pt-BR": "GOLPE"}, source="Malwarebytes")])
    assert _shown(doc, "scn_busca", {"stamp": "GOLPE", "source": "Malwarebytes"}) == {"stamp": "GOLPE"}


def test_figure_keeps_the_credit_when_the_sentence_started_in_the_previous_scene_cites_it():
    """2:51: "Em agosto, a Malwarebytes encontrou sites | com nomes tipo GTA 6 demo": a frase é uma só."""
    figure = _figure("https://www.malwarebytes.com/wp-content/uploads/sites/2/2026/08/fake-gta-6-demo2.png",
                     MALWAREBYTES_PAGE)
    text = ("Primeiro: o site falso de demo. Em agosto, a Malwarebytes encontrou sites com nomes tipo GTA 6 demo, "
            "copiando a divulgação oficial da Rockstar.")
    doc = _doc({"s": text}, [
        _scene("scn_a", "s", "image", part=0, parts=2, span=12, media=_capture(MALWAREBYTES_PAGE, "malwarebytes.com"),
               source="Malwarebytes"),
        _scene("scn_b", "s", "image", part=1, parts=2, span=11, media=figure, stamp={"pt-BR": "GOLPE"},
               source="Malwarebytes"),
    ])
    assert scene_sentence(doc, doc.scene("scn_b")).startswith("Em agosto, a Malwarebytes")
    assert _shown(doc, "scn_b", {"stamp": "GOLPE", "source": "Malwarebytes"}) == {"stamp": "GOLPE",
                                                                                  "source": "Malwarebytes"}


def test_iso_fact_is_not_credited_to_malwarebytes_on_the_list_title():
    """5:12: "2. O TAMANHO NÃO BATE" (1 MB da Malwarebytes e ISO da ESET) sobre a figura da Malwarebytes não credita a
    Malwarebytes, nem emprestando o crédito do "GOLPE" da busca do Google no mesmo trecho. O cartão "100 GB+" da ISO
    continua com a ESET (3:42): o mesmo dado não aparece com duas fontes."""
    demo = _figure("https://www.malwarebytes.com/wp-content/uploads/sites/2/2026/08/fake-gta-6-demo2.png",
                   MALWAREBYTES_PAGE)
    google = _figure("https://www.malwarebytes.com/wp-content/uploads/sites/2/2026/08/gta-6-leak-google-results.png",
                     MALWAREBYTES_PAGE)
    doc = _doc({
        "iso": "pela empresa de segurança Huntress: uma ISO com mais de cem gigabytes.",
        "sinais": "Dois: o tamanho não bate, tipo um arquivinho de um megabyte ou uma ISO vazada gigante. "
                  "Nada de link de anúncio, de vídeo curto ou de grupo.",
    }, [
        _scene("scn_iso", "iso", "stat", value={"pt-BR": "100 GB+"}, label={"pt-BR": "ISO falsa"},
               source="WeLiveSecurity (ESET)"),
        _scene("scn_list", "sinais", "title", part=0, parts=2, span=16, media=demo, overlay="2. O TAMANHO NÃO BATE",
               source="Malwarebytes"),
        _scene("scn_busca", "sinais", "image", part=1, parts=2, span=11, media=google, stamp={"pt-BR": "GOLPE"},
               source="Malwarebytes"),
    ])
    assert _shown(doc, "scn_list", {"source": "Malwarebytes"}) is None
    assert _shown(doc, "scn_iso", {"value": "100 GB+", "source": "WeLiveSecurity (ESET)"})["source"] == (
        "WeLiveSecurity (ESET)")


def test_positive_verdict_stamp_is_the_sources_claim():
    """"CONFIRMADO" com "Fonte: Rockstar Games" no resumo: quem confirma é a fonte."""
    doc = _doc({"s": "Recapitulando rapidinho. GTA 6 no PC hoje: não existe."},
               [_scene("scn_rec", "s", "title", overlay="PC HOJE: NÃO EXISTE", stamp={"pt-BR": "CONFIRMADO"},
                       source="Rockstar Games")])
    assert _shown(doc, "scn_rec", {"stamp": "CONFIRMADO", "source": "Rockstar Games"})["source"] == "Rockstar Games"


@pytest.mark.parametrize(("text", "named"), [
    ("Num alerta de 13 de novembro, a ESET citou um caso.", True),
    ("Em agosto, a Malwarebytes encontrou sites.", False),
    ("O site da welivesecurity.com publicou o alerta.", True),
    ("A segurança da sua conta.", False),
])
def test_names_source_by_part_of_the_name_or_the_site(text, named):
    assert names_source(text, "WeLiveSecurity (ESET)", [SOURCES[4]]) is named


def test_storyboard_drops_the_credit_of_a_typographic_card_without_a_claim():
    """No documento também: a revisão do storyboard tira a fonte do cartão tipográfico do conselho sobre Pix."""
    text = "E se pagou alguma coisa, com cartão ou Pix, fala com o seu banco o quanto antes e guarda print e comprovante."
    doc = _doc({"s": text}, [_scene("scn_pix", "s", source="Malwarebytes")])
    beats = [storyboard_check.Beat(key="s:0", segment_id="s", part=0, parts=1, index=0, role="body",
                                   words=text.split(), seconds=6.0)]
    issues = storyboard_check.fix_lone_sources(doc, beats)
    assert [(i.code, i.scene_id) for i in issues] == [("fonte_solta", "scn_pix")]
    assert doc.scene("scn_pix").card is None
