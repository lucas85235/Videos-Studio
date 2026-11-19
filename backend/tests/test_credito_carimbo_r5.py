"""Crédito, carimbo e faixa no lugar certo (QA r4 do prj_gq4krrs11w, "GTA 6 no PC: quando sai?").

Casos reais:
- "Fonte:" e carimbo sobre cartão tipográfico: o capítulo "O GOLPE / Então vamos ao golpe" com "Fonte: Canaltech"
  (2:42), "que PC fica de fora," e "só cita os consoles Xbox." com CONFIRMADO e "Fonte: Windows Central" (2:00-2:03),
  "REMOTE PLAY" com CONFIRMADO e "Fonte: Dexerto" (2:15). Crédito e veredito ficam na mídia da fonte e no cartão de dado;
- a faixa "ROCKSTAR · 10/11/2026" sobre a captura da Dexerto (1:42): a faixa diz o veículo da mídia mostrada;
- a faixa "SITE FALSO · DEMO PC" sobre o close da arte oficial do Jason antes de o site falso entrar (0:00-1,7 s);
- o primeiro quadro do vídeo sem legenda (0,0 s);
- o cartão "T1 2027" (notação de relatório em inglês) com a fala "primeiro trimestre";
- o carimbo BOATO invadindo a borda do cartão "T1 2027" (1:03);
- o cartão "PC SEGUNDO A GAMESBEAT" sem o número por 2,7 s (6:24).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from studio.config import REPO_ROOT
from studio.core.numbers import localize_periods
from studio.core.project.document import (
    MediaRef,
    ProjectDoc,
    Scene,
    SceneCard,
    Segment,
    Source,
    SubShot,
    Visual,
)
from studio.core.project.pool import Pool, PoolItem
from studio.core.styles.library import get_style
from studio.core.timeline import NarrationClip, build_timeline, lead_in
from studio.providers.base import Word

TEXT = "no dia dez de novembro a própria Rockstar anunciou que o jogo roda na nuvem do Xbox"
STEP = 0.5
STYLE = get_style("curiosidades-longo")
FPS = STYLE.format.fps
COMP = REPO_ROOT / "renderer" / "src" / "components"

SOURCES = [
    Source(title="Rockstar Games – Grand Theft Auto VI (site oficial)", url="https://www.rockstargames.com/VI",
           note="PS5 e Xbox Series X|S em 19/11/2026; nenhuma versão de PC anunciada."),
    Source(title="Dexerto – GTA 6 confirms Xbox Cloud Gaming support but PC players face a catch",
           url="https://www.dexerto.com/gta/gta-6-confirms-xbox-cloud-gaming-support-but-pc-players-face-a-catch/",
           note="Anúncio de 10/11 sobre nuvem e Remote Play."),
    Source(title="GamesBeat – Grand Theft Auto VI's PC release is slated for Q1 2027",
           url="https://gamesbeat.com/grand-theft-auto-vis-pc-release-is-slated-for-q1-2027/",
           note="Quatro fontes anônimas: PC no primeiro trimestre de 2027."),
    Source(title="Malwarebytes – Sites falsos de demo do GTA 6 distribuem infostealer",
           url="https://www.malwarebytes.com/pt-br/blog/threat-intel/2026/08/fake-gta-6-demo-sites",
           note="Sites tipo gta6demo, instalador de 1,1 MB."),
    Source(title="WeLiveSecurity (ESET) – GTA 6: golpes com downloads falsos",
           url="https://www.welivesecurity.com/pt/golpes-fraudes/gta-6-golpes/",
           note="13/11/2026: ISO de 100 GB (Huntress)."),
    Source(title="Canaltech – Cuidado: falso GTA 6 para PC espalha vírus",
           url="https://canaltech.com.br/games/cuidado-falso-gta-6-para-pc-espalha-virus/", note="Golpe no Brasil."),
    Source(title="Windows Central – GTA 6 will be playable on Xbox Cloud Gaming",
           url="https://www.windowscentral.com/gaming/xbox/gta-6-xbox-cloud-gaming", note="Nuvem só em consoles Xbox."),
]


def _capture(url: str, credit: str, asset: str = "uploads/prj_r5/media/c.jpg") -> MediaRef:
    return MediaRef(kind="capture", source="capture", asset=asset, url=url, page_url=url, credit=credit,
                    editorial=True, tier="capture")


def _doc(*scenes: Scene) -> ProjectDoc:
    segments = [Segment(id=f"seg_{i}", text={"pt-BR": TEXT}) for i in range(len(scenes))]
    return ProjectDoc(id="prj_r5", title="GTA 6 no PC: quando sai?", topic="GTA 6 no PC", style_id="curiosidades-longo",
                      primary_language="pt-BR", languages=["pt-BR"], segments=segments, scenes=list(scenes), seed=5,
                      sources=SOURCES)


def _scene(i: int, kind: str = "image", media: MediaRef | None = None, overlay: str = "", **kwargs) -> Scene:
    visual = Visual(prompt="x", media=media) if media is not None else Visual(type="none") if kind != "image" \
        else Visual(prompt="x")
    return Scene(id=f"scn_{i}", segment_id=f"seg_{i}", kind=kind, visual=visual,
                 overlay_text={"pt-BR": overlay} if overlay else {}, **kwargs)


def _edit_on(scene: Scene) -> Scene:
    """Liga a camada de edição (um subplano do próprio visual): as regras novas valem só com ela."""
    scene.shots = [SubShot(at="novembro", source="self")]
    return scene


def _clips(doc: ProjectDoc) -> dict[str, NarrationClip]:
    toks = TEXT.split()
    return {seg.id: NarrationClip(seg.id, "x.wav", len(toks) * STEP + 0.2,
                                  [Word(t, k * STEP, k * STEP + 0.4) for k, t in enumerate(toks)])
            for seg in doc.segments}


def _timeline(doc: ProjectDoc, pool: Pool | None = None, images: dict[str, str] | None = None) -> dict:
    images = images if images is not None else {s.id: f"cache/images/{s.id}.jpg" for s in doc.scenes}
    return build_timeline(doc, STYLE, "pt-BR", _clips(doc), images, pool=pool)


# --- (1) "Fonte:" e carimbo nunca em cartão tipográfico ---------------------------------------------------------------


def test_typographic_cards_never_carry_source_or_verdict():
    canaltech = _capture(SOURCES[5].url, "canaltech.com.br")
    wc = _capture(SOURCES[6].url, "windowscentral.com")
    dexerto = _capture(SOURCES[1].url, "dexerto.com")
    doc = _doc(
        _edit_on(_scene(0, "title", canaltech, "O GOLPE", card=SceneCard(source="Canaltech"))),  # capítulo
        _scene(1, "text", wc, card=SceneCard(stamp={"pt-BR": "CONFIRMADO"}, source="Windows Central")),
        _scene(2, "title", None, "REMOTE PLAY", card=SceneCard(stamp={"pt-BR": "CONFIRMADO"}, source="Dexerto")),
        _scene(3, "count", None, "5 SINAIS", card=SceneCard(stamp={"pt-BR": "GOLPE"}, source="Malwarebytes")),
        # mídia que É da fonte e cartão de dado da fonte continuam com crédito e veredito
        _scene(4, "image", dexerto, card=SceneCard(stamp={"pt-BR": "CONFIRMADO"}, source="Dexerto")),
        _scene(5, "stat", None, card=SceneCard(value={"pt-BR": "19"}, label={"pt-BR": "navegadores"},
                                               stamp={"pt-BR": "CONFIRMADO"}, source="Malwarebytes")),
    )
    cards = [s.get("card") or {} for s in _timeline(doc)["scenes"]]
    for card in cards[:4]:
        assert "source" not in card and "stamp" not in card, card
    assert cards[4] == {"stamp": "CONFIRMADO", "source": "Dexerto", **({"stamp_at": cards[4]["stamp_at"]}
                                                                       if "stamp_at" in cards[4] else {})}
    assert cards[5]["source"] == "Malwarebytes" and cards[5]["stamp"] == "CONFIRMADO"


def test_old_project_keeps_typographic_credit():
    """Sem a camada de edição, a timeline sai como antes (ADR 0004 §3.0)."""
    doc = _doc(_scene(0, "title", None, "REMOTE PLAY", card=SceneCard(stamp={"pt-BR": "CONFIRMADO"},
                                                                       source="Dexerto")))
    assert _timeline(doc)["scenes"][0]["card"]["stamp"] == "CONFIRMADO"


# --- (2) a faixa diz o veículo da mídia mostrada -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("headline", "media", "expected"),
    [
        ("ROCKSTAR · 10/11/2026", _capture(SOURCES[1].url, "dexerto.com"), "DEXERTO · 10/11/2026"),
        ("GAMESBEAT · 13/10/2026", _capture(SOURCES[2].url, "gamesbeat.com"), "GAMESBEAT · 13/10/2026"),
        ("ESET + HUNTRESS · 13/11", _capture(SOURCES[4].url, "welivesecurity.com"), "ESET + HUNTRESS · 13/11"),
        ("MALWAREBYTES · AGO/2026", _capture(SOURCES[3].url, "malwarebytes.com"), "MALWAREBYTES · AGO/2026"),
        # assunto, não veículo: fica
        ("LANÇAMENTO · 19/11/2026", _capture(SOURCES[1].url, "dexerto.com"), "LANÇAMENTO · 19/11/2026"),
        ("SITE FALSO · DEMO PC", _capture(SOURCES[1].url, "dexerto.com"), "SITE FALSO · DEMO PC"),
        # ilustração de IA não é veículo nenhum: fica
        ("ROCKSTAR · 10/11/2026", None, "ROCKSTAR · 10/11/2026"),
    ],
)
def test_headline_names_the_outlet_of_the_media_on_screen(headline, media, expected):
    doc = _doc(_edit_on(_scene(0, "image", media, card=SceneCard(headline={"pt-BR": headline}))))
    assert _timeline(doc)["scenes"][0]["card"]["headline"] == expected


# --- (3) faixa de golpe só sobre o material do golpe ----------------------------------------------------------------

POOL = Pool(items=[
    PoolItem(id="p49", tier="official", media="image", path="uploads/prj_r5/pool/p49.jpg",
             desc="capa-casal · personagem · jason · lucia", credit="Rockstar Games (capa oficial)"),
    PoolItem(id="p15", tier="official", media="image", path="uploads/prj_r5/pool/p15.jpg",
             desc="Site falso de demo de GTA 6 para PC", credit="Malwarebytes"),
])


def _scam_figure() -> MediaRef:
    return MediaRef(kind="official", source="link", asset="uploads/prj_r5/media/fake.jpg",
                    url="https://www.malwarebytes.com/wp-content/uploads/fake-gta-6-demo1.png",
                    page_url=SOURCES[3].url, credit="Malwarebytes", editorial=True, tier="official", pool_id="p15")


def _word_frame(k: int) -> int:
    return int((lead_in(STYLE) + k * STEP) * FPS + 0.5)


def test_scam_headline_waits_for_the_scam_material():
    """O gancho abre na arte oficial (subplano do acervo) e corta para o print do site falso no "anunciou": a faixa
    "SITE FALSO · DEMO PC" entra no corte para o site falso, não sobre a arte oficial."""
    scene = _scene(0, "image", _scam_figure(), card=SceneCard(headline={"pt-BR": "SITE FALSO · DEMO PC"}),
                   shots=[SubShot(at=None, source="pool", pool_id="p49"), SubShot(at="anunciou", source="self")])
    out = _timeline(_doc(scene), pool=POOL)["scenes"][0]
    card = out["card"]
    second = out["shots"][1]["from"]
    assert second > 0 and card["headline_from"] == second
    assert "headline_to" not in card
    assert "_shot_owners" not in out  # a chave interna não vai para a timeline


def test_scam_headline_leaves_when_the_scam_shot_ends():
    scene = _scene(0, "image", _scam_figure(), card=SceneCard(headline={"pt-BR": "SITE FALSO · DEMO PC"}),
                   shots=[SubShot(at=None, source="self"), SubShot(at="anunciou", source="pool", pool_id="p49")])
    out = _timeline(_doc(scene), pool=POOL)["scenes"][0]
    assert "headline_from" not in out["card"] and out["card"]["headline_to"] == out["shots"][1]["from"]


def test_headline_stays_when_every_shot_is_the_scam_or_it_is_not_an_accusation():
    only_scam = _scene(0, "image", _scam_figure(), card=SceneCard(headline={"pt-BR": "SITE FALSO · DEMO PC"}),
                       shots=[SubShot(at="anunciou", source="self")])
    news = _scene(1, "image", _scam_figure(), card=SceneCard(headline={"pt-BR": "MALWAREBYTES · AGO/2026"}),
                  shots=[SubShot(at=None, source="pool", pool_id="p49"), SubShot(at="anunciou", source="self")])
    scenes = _timeline(_doc(only_scam, news), pool=POOL)["scenes"]
    for out in scenes:
        assert "headline_from" not in out["card"] and "headline_to" not in out["card"]


# --- (5) período na língua do vídeo ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "lang", "expected"),
    [
        ("T1 2027", "pt-BR", "1º TRI 2027"),
        ("Q1 2027", "pt-BR", "1º TRI 2027"),
        ("1Q 2027", "pt-BR", "1º TRI 2027"),
        ("H2 2026", "pt-BR", "2º SEM 2026"),
        ("janela T3/2026", "pt-BR", "janela 3º TRI 2026"),
        ("T1 2027", "en", "Q1 2027"),
        ("T1 2027", "es", "T1 2027"),  # o trimestre em espanhol já é "T1"
        ("T1", "pt-BR", "T1"),  # sem ano: pode ser nome próprio (o time T1)
        ("Q7 de 2020", "pt-BR", "Q7 de 2020"),
        ("PS5 2026", "pt-BR", "PS5 2026"),
    ],
)
def test_period_notation_follows_the_video_language(text, lang, expected):
    assert localize_periods(text, lang) == expected


def test_data_card_period_is_written_in_the_video_language():
    doc = _doc(_scene(0, "stat", None, card=SceneCard(value={"pt-BR": "T1 2027"}, label={"pt-BR": "PC (GamesBeat)"},
                                                      source="GamesBeat")))
    assert _timeline(doc)["scenes"][0]["card"]["value"] == "1º TRI 2027"


# --- (6) número com o cartão; (7) legenda desde o quadro 0 ----------------------------------------------------------


def test_bare_data_card_shows_its_number_at_the_cut():
    """Sem fundo para ler (só o fundo do estilo), o cartão e o número entram juntos no corte (sem `reveal`)."""
    doc = _doc(_edit_on(_scene(0, "image")),
               _scene(1, "stat", None, card=SceneCard(value={"pt-BR": "10"}, label={"pt-BR": "de novembro"})))
    scenes = _timeline(doc, images={"scn_0": "cache/images/scn_0.jpg"})["scenes"]
    assert "reveal" not in scenes[1]


def test_first_frame_of_the_video_has_a_caption():
    doc = _doc(_edit_on(_scene(0, "image")), _scene(1, "image"))
    timeline = _timeline(doc)
    first = timeline["scenes"][0]["captions"][0]
    assert first["from"] == 0 and first["words"][0]["from"] >= 0
    # só a primeira cena do vídeo: as outras seguem a fala
    second = timeline["scenes"][1]["captions"][0]
    assert second["from"] == second["words"][0]["from"]


# --- renderer --------------------------------------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_renderer_keeps_panel_stamp_off_the_card_and_times_the_headline():
    data = _read(COMP / "DataCards.tsx")
    # o carimbo do painel fica inteiro acima da borda (folga para o canto que a rotação abaixa), nunca pousado nela
    assert "bottom: `calc(100% + ${stampClear(" in data
    assert "stampOverlap" not in data
    assert "return stampClear(label, font, maxWidth, u) + h + w * 0.09;" in data
    scene = _read(COMP / "SceneView.tsx")
    assert "scene.card?.headline_from" in scene and "scene.card?.headline_to" in scene
    assert '<Sequence from={headFrom} durationInFrames={Math.max(1, headTo - headFrom)} layout="none" name="Faixa">' \
        in scene
    types = _read(REPO_ROOT / "renderer" / "src" / "types.ts")
    assert "headline_from?: number;" in types and "headline_to?: number;" in types
