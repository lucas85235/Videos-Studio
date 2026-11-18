"""Material que casa com a fala e com a regra (QA r3 do `prj_gq4krrs11w`, rodada 4).

- veredito negativo ("golpe" que pula, carimbo) só sobre o material do golpe: nem sobre a foto de banco de um produto;
- foto de aparelho com vários modelos na fala só do modelo principal (Xbox One não ilustra "Series X e Series S");
- plano com texto de outra pessoa (post, legenda de usuário, slate) só na cena que fala dele;
- "site oficial da X" na fala põe a captura do site de X na tela a partir de "site";
- a imagem de IA abre com o aparelho que a fala cita; um tratamento (técnica) só em toda imagem de IA;
- avatar não divide o quadro com rosto de IA;
- a captura não foca o título achado no rodapé (newsletter) da página.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from studio.core import verdict
from studio.core.project.document import (
    ArtDirection,
    BeatMark,
    MediaRef,
    ProjectDoc,
    Scene,
    Visual,
)
from studio.core.project.document import Segment as Seg
from studio.core.project.pool import Pool, PoolItem
from studio.core.sources import foreign_overlay, on_screen_text, overlay_blocks
from studio.core.styles.library import get_style
from studio.core.styles.schema import grammar_for
from studio.core.timeline import drop_false_verdict_fx, false_verdict_on_true_material
from studio.workers.pipeline import build, edit_plan, media_pool, storyboard_check
from studio.workers.pipeline.storyboard_check import Beat

LONG = "curiosidades-longo"
CONSOLE = MediaRef(kind="photo", source="pexels", asset="uploads/prj_mc/media/00112233445566aa.jpg",
                   title="close up of wireless joysticks on a video game console", credit="Autor / Pexels",
                   tier="stock", pool_id="p1")
HACKER = MediaRef(kind="photo", source="pexels", asset="uploads/prj_mc/media/00112233445566bb.jpg",
                  title="hacker in a dark room stealing passwords", credit="Autor / Pexels", tier="stock",
                  pool_id="p2")


def _doc(texts: list[str], media: list[MediaRef | None] | None = None, prompts: list[str] | None = None,
         art: ArtDirection | None = None) -> ProjectDoc:
    media = media or [None] * len(texts)
    prompts = prompts or [f"cena {i}" for i in range(len(texts))]
    segments = [Seg(id=f"seg{i}", role="body", text={"pt-BR": t}) for i, t in enumerate(texts)]
    scenes = [Scene(id=f"scn{i}", segment_id=f"seg{i}", visual=Visual(prompt=prompts[i], media=media[i]),
                    mark=BeatMark(role="list", emphasis=[]))
              for i in range(len(texts))]
    return ProjectDoc(id="prj_mc", title="GTA 6 no PC", style_id=LONG, seed=7, segments=segments, scenes=scenes,
                      art=art)


POOL = Pool(items=[
    PoolItem(id="p1", tier="stock", media="image", path="uploads/prj_mc/pool/p1.jpg", topic="Xbox",
             desc="close up of wireless joysticks on a video game console"),
    PoolItem(id="p2", tier="stock", media="image", path="uploads/prj_mc/pool/p2.jpg",
             desc="hacker in a dark room stealing passwords"),
])


# --- veredito negativo ------------------------------------------------------------------------------------------------


def test_negative_verdict_over_stock_only_with_the_scam_theme():
    # foto de banco de um produto de marca (a busca "Xbox" achou os controles): "GOLPE" diria que o console é golpe
    # (6:33 do vídeo)
    assert not verdict.allows_negative("stock", CONSOLE, POOL.items[0].desc, "Xbox")
    assert not verdict.allows_negative("stock", POOL.items[0])
    assert not verdict.allows_negative("free", None, "Xbox Series X and Series S consoles")
    # foto do tema do golpe, ilustração de IA e desenho do app continuam aceitando
    assert verdict.allows_negative("stock", HACKER)
    assert verdict.allows_negative("free", None, "phishing email on a laptop screen")
    assert verdict.allows_negative("stock", None, "Xbox controller next to a hacker hoodie", "Xbox")
    # a foto de banco comum é ilustração: o "golpe do Pix" e o canal que desmente mito ficam com o veredito
    assert verdict.allows_negative("stock", None, "woman holding a smartphone", "Pix")
    assert verdict.allows_negative("stock", None, "glass of ice water")
    assert verdict.allows_negative("free", None, "close up of wireless joysticks on a video game console")
    assert verdict.allows_negative("ai", None) and verdict.allows_negative("graphic", None)
    doc = _doc(["Nuvem: só em console Xbox. Download de PC, grátis ou pago: golpe.", "Golpe de novo."],
               media=[CONSOLE, HACKER])
    assert false_verdict_on_true_material(doc, doc.scenes[0], "golpe", POOL)
    assert not false_verdict_on_true_material(doc, doc.scenes[1], "GOLPE", POOL)
    assert not false_verdict_on_true_material(doc, doc.scenes[0], "Series", POOL)  # não é veredito


def test_text_pop_with_negative_verdict_leaves_the_screen_like_the_stamp():
    doc = _doc(["Download de PC, grátis ou pago: golpe."], media=[CONSOLE])
    resolved = {"fx": [{"type": "punch", "from": 3, "dur": 18, "params": {"scale": 1.2}},
                       {"type": "text_pop", "from": 3, "dur": 22, "params": {"text": "golpe"}},
                       {"type": "text_pop", "from": 30, "dur": 22, "params": {"text": "pago"}}]}
    drop_false_verdict_fx(doc, doc.scenes[0], resolved, POOL)
    assert [(e["type"], e["params"].get("text")) for e in resolved["fx"]] == [("punch", None), ("text_pop", "pago")]
    only = {"fx": [{"type": "text_pop", "from": 3, "dur": 22, "params": {"text": "FALSO"}}]}
    drop_false_verdict_fx(doc, doc.scenes[0], only, POOL)
    assert "fx" not in only
    # sobre o material do golpe fica
    hacker = _doc(["golpe"], media=[HACKER])
    kept = {"fx": [{"type": "text_pop", "from": 3, "dur": 22, "params": {"text": "golpe"}}]}
    drop_false_verdict_fx(hacker, hacker.scenes[0], kept, POOL)
    assert len(kept["fx"]) == 1


def _planner(doc: ProjectDoc, pool: Pool = POOL) -> edit_plan._Planner:
    style = get_style(LONG)
    return edit_plan._Planner(doc, style, grammar_for(style), pool, set())


def test_planner_never_pops_the_verdict_over_a_product_photo():
    text = "Download de PC, grátis ou pago, é golpe e mais nada aqui."
    on_console = _planner(_doc([text], media=[CONSOLE]))
    beat = on_console.beats[0]
    word = next(w for w in beat.words if w.key == "golpe")
    assert not on_console._free(beat, word, "text_pop")
    assert on_console._free(beat, word, "punch")  # o punch não acusa nada
    on_ai = _planner(_doc([text]))
    assert on_ai._free(on_ai.beats[0], next(w for w in on_ai.beats[0].words if w.key == "golpe"), "text_pop")


# --- aparelho: o modelo principal da fala ------------------------------------------------------------------------------


def test_device_photo_shows_the_lead_model_of_the_speech():
    speech = "Funciona nos consoles Xbox Series X e Series S, e até no velho Xbox One, sempre com os gráficos do Series S."

    def item(desc: str) -> PoolItem:
        return PoolItem(id="p99", tier="stock", media="clip", desc=desc, topic="Xbox")

    assert not media_pool.fits_scene(item("an xbox one console blinking light"), speech)
    assert media_pool.fits_scene(item("Xbox Series X and Xbox Series S consoles side by side"), speech)
    # o One é o principal quando a fala começa por ele
    assert media_pool.fits_scene(item("an xbox one console blinking light"),
                                 "O velho Xbox One também roda, e o Xbox Series S também.")
    # um modelo só na fala: a regra de sempre (o mesmo modelo)
    assert media_pool.fits_scene(item("an xbox one console blinking light"), "Roda até no velho Xbox One.")


# --- texto de outra pessoa no plano -----------------------------------------------------------------------------------


def test_clip_with_someone_elses_overlay_only_where_the_speech_is_about_it():
    rip = "Trailer 1 oficial (Rockstar) · trailer · texto \"RIP Rudi - C U in heaven, cuz.\""
    assert on_screen_text(rip) == "RIP Rudi - C U in heaven, cuz."
    speech = "e, com o jogo lançado, cheats falsos, menu de mod, gerador de dinheiro e a famosa versão de PC."
    assert foreign_overlay(rip, speech)
    assert foreign_overlay('x · texto "MAY CONTAIN CONTENT INAPPROPRIATE FOR CH"', speech)  # slate
    assert foreign_overlay('x · texto "Stay Alert VC! Remember to check for uni"', speech)  # post
    assert foreign_overlay('x · texto "siga @canal_do_fulano"', speech)
    # letreiro do cenário (caixa alta ou título curto) não é de ninguém
    for sign in ("VICE", "THRILLBILLY MUD CLUB", "Grand Theft Auto VI", "Brian's Boat Works & Marina"):
        assert not foreign_overlay(f'x · texto "{sign}"', speech), sign
    # a fala que cita o post pode mostrá-lo
    assert not foreign_overlay(rip, "O post dizia RIP Rudi, e virou meme.")
    # vídeo de terceiro (banco, imprensa, web) com o post ou a sobreposição de outro criador: só onde a fala é dele
    reup = PoolItem(id="p40", tier="free", media="clip", desc=rip.replace("Trailer 1 oficial (Rockstar)", "Reação"))
    assert overlay_blocks(reup, speech) and not media_pool.fits_scene(reup, speech)
    assert not overlay_blocks(reup.model_copy(update={"desc": "Reação · trailer"}), speech)
    streamer = reup.model_copy(update={"desc": 'Reação · trailer · sobreposição "facecam"'})
    assert overlay_blocks(streamer, speech) and not media_pool.fits_scene(streamer, speech)
    # o oficial é a própria obra (o post dentro do trailer é do trailer) e o do usuário é a escolha dele: ficam
    trailer = PoolItem(id="p133", tier="official", media="clip", desc=rip)
    assert media_pool.fits_scene(trailer, speech)
    own = PoolItem(id="p41", tier="own", media="clip", source_id="acervo:a#1",
                   desc='Minha gravação · texto "Press E to enter the vehicle"')
    assert media_pool.fits_scene(own, "Aqui eu entro no carro e saio dirigindo")
    assert media_pool.fits_scene(own.model_copy(update={"source_id": "kitvideo:k#2", "tier": "official"}),
                                 "Aqui eu entro no carro e saio dirigindo")


def test_clip_description_carries_the_streamer_overlay(monkeypatch, tmp_path):
    """A visão já diz se há logo, mascote, meme ou print colado por cima do plano (ou a câmera do rosto do
    streamer): a descrição do plano leva isso, e o texto da tela vai inteiro (o @ no fim do post decidia)."""
    from studio.media import clips
    from studio.providers import vision

    answers = [
        {"material": "gameplay", "on_screen_text": "RIP Rudi - C U in heaven, cuz. Posted by @YoMammaJammer today",
         "has_overlay": True, "overlay_kind": "logo"},
        {"material": "facecam", "on_screen_text": None, "has_overlay": False, "overlay_kind": None},
        {"material": "gameplay", "on_screen_text": None, "has_overlay": True, "overlay_kind": "arrow"},
    ]
    monkeypatch.setattr(clips, "_is_mock_vision", lambda: False)
    monkeypatch.setattr(vision, "classify_frames", lambda posters, schema: answers)
    out = [d for d, _ in clips.describe([tmp_path / f"{i}.jpg" for i in range(3)], "Vídeo")]
    assert '@YoMammaJammer' in out[0] and 'sobreposição "logo"' in out[0]
    assert 'sobreposição "facecam"' in out[1]
    assert "sobreposição" not in out[2]  # seta é edição comum
    assert foreign_overlay(out[1], "fala qualquer") and not foreign_overlay(out[2], "fala qualquer")


def test_planner_cutaway_skips_the_frame_with_someone_elses_overlay():
    tags = ["gta", "trailer", "cheats", "menu", "dinheiro"]
    pool = Pool(items=[
        PoolItem(id="p9", tier="official", media="image", path="uploads/prj_mc/pool/p9.jpg", tags=tags,
                 desc='Trailer 1 · trailer · texto "RIP Rudi - C U in heaven, cuz."'),
    ])
    text = ("E com o jogo lançado vêm os cheats falsos, o menu de mod do gta, o gerador de dinheiro do trailer, "
            "e a famosa versão de PC que nunca existiu, com cheats, menu e dinheiro infinito de trailer.")
    doc = _doc([text, text, text])
    edit_plan.plan(doc, get_style(LONG), pool, recent=set())
    assert not [s for scene in doc.scenes for s in scene.shots if s.pool_id == "p9"]
    clean = Pool(items=[pool.items[0].model_copy(update={"desc": "Trailer 1 · trailer"})])
    doc = _doc([text, text, text])
    edit_plan.plan(doc, get_style(LONG), clean, recent=set())
    assert [s for scene in doc.scenes for s in scene.shots if s.pool_id == "p9"]


# --- "site oficial" na fala ---------------------------------------------------------------------------------------------


SITE_POOL = Pool(items=[
    PoolItem(id="p20", tier="capture", media="capture", path="uploads/prj_mc/pool/p20.jpg",
             desc='Grand Theft Auto VI – Rockstar Games — "Rockstar Games – Grand Theft Auto VI (site oficial)"',
             url="https://www.rockstargames.com/VI", page_url="https://www.rockstargames.com/VI",
             credit="rockstargames.com", rects=[[0.1, 0.3, 0.6, 0.06]]),
    PoolItem(id="p21", tier="capture", media="capture", path="uploads/prj_mc/pool/p21.jpg",
             desc="GTA 6 PC release — GamesBeat", page_url="https://gamesbeat.com/gta-6-pc/", credit="gamesbeat.com"),
])


def test_official_site_in_the_speech_shows_the_site_capture():
    xbox = MediaRef(kind="official", source="link", asset="uploads/prj_mc/media/aa.jpg", tier="official")
    doc = _doc(["Mas só no PlayStation 5 e no Xbox Series X e Series S, e isso tá no site oficial da Rockstar."],
               media=[xbox])
    edit_plan.plan(doc, get_style(LONG), SITE_POOL, recent=set())
    shots = [(s.at, s.source, s.pool_id) for s in doc.scenes[0].shots]
    assert ("site", "pool", "p20") in shots
    # a captura sem trecho marcado não prova que mostra a página (pode ser só o rodapé): não entra pelo "site"
    bare = Pool(items=[SITE_POOL.items[0].model_copy(update={"rects": []}), SITE_POOL.items[1]])
    doc = _doc(["Mas só no PlayStation 5 e no Xbox Series X e Series S, e isso tá no site oficial da Rockstar."],
               media=[xbox])
    edit_plan.plan(doc, get_style(LONG), bare, recent=set())
    assert all(s.pool_id != "p20" for s in doc.scenes[0].shots)
    # "site oficial do fabricante" não diz de quem: nenhuma captura entra por ele
    doc = _doc(["Passa um antivírus confiável, baixado só do site oficial do fabricante, e pronto."], media=[xbox])
    edit_plan.plan(doc, get_style(LONG), SITE_POOL, recent=set())
    assert all(s.pool_id not in ("p20", "p21") for s in doc.scenes[0].shots)


# --- imagem de IA -------------------------------------------------------------------------------------------------------


def _beat(i: int, text: str) -> Beat:
    return Beat(key=f"seg{i}:0", segment_id=f"seg{i}", part=0, parts=1, index=i, role="body", words=text.split(),
                seconds=4.0)


def test_ai_prompt_opens_with_the_device_the_speech_cites():
    speech = "O jogo roda no seu console e a imagem vai pra outra tela pelo app oficial da Sony ou do Xbox."
    prompt = ("Over-the-shoulder view of a gamer at a desktop PC in a bedroom while a game console on a shelf "
              "glows.")
    assert storyboard_check.device_mismatch(speech, prompt) == ("pc", ["console"])
    assert storyboard_check.device_mismatch(speech, "A game console on a TV stand streams to a laptop.") is None
    assert storyboard_check.device_mismatch("Troca a senha pelo celular, nunca do computador.",
                                            "A hand holding a smartphone next to a desktop PC.") is None
    assert storyboard_check.device_mismatch("Calma, mas age rápido.", "A desktop PC in a dark room.") is None
    # TV é a tela de qualquer aparelho e "notebook" em inglês é o caderno: não trocam o aparelho (a revisão grave
    # pediria outro storyboard pago à toa)
    assert storyboard_check.device_mismatch(speech, "A cozy living room, a TV glowing above a game console.") is None
    assert storyboard_check.device_mismatch("Vai sair no PlayStation 5.",
                                            "A television showing a racing game, a controller on the couch.") is None
    assert storyboard_check.device_mismatch("Anota tudo antes de comprar o console.",
                                            "A hand writing a checklist in a paper notebook.") is None
    doc = _doc([speech], prompts=[prompt])
    issues = storyboard_check._check_prompts(doc, [_beat(0, speech)])
    assert [(i.code, i.severity) for i in issues] == [("aparelho_trocado", storyboard_check.GRAVE)]


def test_every_ai_image_of_the_video_keeps_the_treatment():
    style = get_style(LONG)
    art = ArtDirection(world="gamer bedrooms, 2020s", treatment="smooth painterly gradients, subtle grain",
                       palette=["#FF4FA3"], languages=["key_art"])
    visual = Visual(prompt="A game console on a shelf", language="key_art")
    lean = build.compose_directed_prompt(style, art, visual, lean=True)
    assert "Treatment: smooth painterly gradients, subtle grain" in lean
    assert "World:" not in lean and "Accent colors:" not in lean
    bare = build.compose_directed_prompt(style, art, visual, lean=True, treatment=False)
    assert "Treatment:" not in bare  # o prompt de antes, só para achar a imagem já gerada no cache
    # a luz do tratamento sai quando a cena diz a dela
    lit = Visual(prompt="A game console on a shelf at sunset", language="key_art")
    art_lit = art.model_copy(update={"treatment": "painterly gradients, neon glow"})
    assert "neon glow" not in build.compose_directed_prompt(style, art_lit, lit, lean=True)


def test_another_image_keeps_the_ai_style_of_the_video():
    """"Outra imagem" girava a linguagem entre as da direção (key_art → render_3d): no storyboard novo a variação muda
    plano e composição e fica na técnica das outras imagens; o prompt de antes (com o giro) só acha o cache."""
    from studio.core.styles.visual import language_prefix

    style = get_style(LONG)
    art = ArtDirection(treatment="smooth painterly gradients", languages=["key_art", "editorial", "render_3d"])
    for variation in range(6):
        visual = Visual(prompt="A game console on a shelf", language="key_art", variation=variation)
        assert build.directed_variant(art, visual, one_language=True)[2] == "key_art"
        assert language_prefix("key_art") in build.compose_directed_prompt(style, art, visual, lean=True)
    rotated = Visual(prompt="A game console on a shelf", language="key_art", variation=2)
    assert build.directed_variant(art, rotated)[2] != "key_art"  # o documento antigo continua igual
    assert language_prefix("key_art") not in build.compose_directed_prompt(style, art, rotated, lean=True,
                                                                           treatment=False)


def test_presenter_never_shares_the_frame_with_an_ai_face():
    from studio.workers.pipeline import presenter as presenter_mod

    prompts = ["A gamer at the desk.", "A worried young man at his gaming desk holds a smartphone.",
               "Over-the-shoulder view of a gamer at a desk.",
               "Close-up of a hand pulling a network cable out of a PC.",
               "A gamer seen from behind on a couch.",
               "A young gamer sits at a desk, face lit by the monitor."]
    doc = _doc([f"fala {i} com bastante palavra para o avatar" for i in range(len(prompts))], prompts=prompts)
    faces = [storyboard_check.ai_face(doc, s) for s in doc.scenes]
    assert faces == [True, True, False, False, False, True]
    # material real com gente não conta: não é rosto de IA
    real = _doc(["fala"], media=[HACKER], prompts=[prompts[1]])
    assert not storyboard_check.ai_face(real, real.scenes[0])
    from studio.core.project.document import Presenter, PresenterPose

    pres = Presenter(enabled=True, poses=[PresenterPose(id="apontando", file="library/presenter/c/a.png")])
    words = presenter_mod.estimate_words(doc)
    slots = presenter_mod._slots(doc, pres, words, None, set())
    assert slots[0].blocked == "primeira cena"  # a 1ª cena já fica sem avatar
    assert [s.blocked == "imagem de IA com rosto" for s in slots[1:]] == faces[1:]


# --- captura: o título fora do rodapé ----------------------------------------------------------------------------------


def _chrome() -> str | None:
    import shutil

    for path in (shutil.which("chrome"), shutil.which("google-chrome"), shutil.which("chromium"),
                 r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"):
        if path and Path(path).is_file():
            return path
    return None


def _find(tmp_path: Path, body: str, phrase: str) -> dict:
    from studio.media import page_capture

    find = page_capture.FIND_JS % (json.dumps([phrase], ensure_ascii=False), 0, "1", "true")
    probe = f"""window.addEventListener('load', () => setTimeout(() => {{
      const res = {{found: {find}}};
      res.rects = {page_capture.RECTS_JS};
      res.boiler = {page_capture.BOILER_JS};
      const pre = document.createElement('pre'); pre.id = 'RES'; pre.textContent = JSON.stringify(res);
      document.documentElement.appendChild(pre); }}, 200));"""
    html = tmp_path / "page.html"
    html.write_text("<!doctype html><html><head><meta charset=utf-8><style>body{margin:0;font:16px sans-serif}"
                    "</style></head><body>" + body + "<script>" + probe + "</script></body></html>", encoding="utf-8")
    chrome = _chrome()
    assert chrome is not None
    out = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run",
                          f"--user-data-dir={tmp_path / 'perfil'}", "--virtual-time-budget=3000",
                          "--window-size=1100,680", "--dump-dom", html.as_uri()],
                         capture_output=True, text=True, encoding="utf-8", timeout=90)
    start = out.stdout.find('<pre id="RES">')
    if start < 0:
        pytest.skip("o Chrome sem janela não devolveu a página")
    import html as html_mod

    return json.loads(html_mod.unescape(out.stdout[start + len('<pre id="RES">'):out.stdout.index("</pre>", start)]))


FOOTER_SITE = """
<main><section style="height:900px;background:#223"><h1>VI</h1><p>Disponível no PlayStation 5 e Xbox Series X|S</p>
</section></main>
<footer><div class="newsletter"><b>RECEBER ROCKSTAR PROPAGANDA</b>
<p>Receba os anúncios mais recentes sobre os jogos e muito mais da Rockstar Games.</p></div></footer>
"""


@pytest.mark.skipif(_chrome() is None, reason="sem Chrome para rodar o JavaScript da captura")
def test_capture_headline_fallback_ignores_the_footer(tmp_path):
    """"Rockstar Games – Grand Theft Auto VI (site oficial)": a parte "Rockstar Games" só estava na caixa de
    newsletter do rodapé, e a captura do site oficial virava o rodapé (o vídeo mostrou "RECEBER ROCKSTAR PROPAGANDA"
    como prova)."""
    title = "Rockstar Games – Grand Theft Auto VI (site oficial)"
    res = _find(tmp_path, FOOTER_SITE, title)
    assert res["found"] == [False]  # sem foco: a captura fica no topo da página
    body = FOOTER_SITE.replace("<p>Disponível", "<p>Rockstar Games anuncia: disponível")
    res = _find(tmp_path, body, title)
    assert res["found"] == [True]
    assert res["rects"][0] and res["rects"][0][0][1] < 0.9  # o trecho do corpo, não o do rodapé
    from studio.media import page_capture

    assert res["boiler"] < page_capture.BOILER_MAX_SHARE  # a página montou: o rodapé não domina o quadro
    # o site em JS pesado que não montou o conteúdo: o quadro é o rodapé com a newsletter, e a captura é recusada
    empty = FOOTER_SITE.replace("height:900px", "height:40px").replace("<footer>", '<footer style="height:640px">')
    res = _find(tmp_path, empty, title)
    assert res["boiler"] > page_capture.BOILER_MAX_SHARE


# --- a mesma captura de novo, cedo demais, só onde a fala é sobre ela ------------------------------------------------


def test_capture_back_too_soon_only_where_the_speech_cites_it(isolated_settings, monkeypatch):
    """A matéria da Malwarebytes voltava nos passos práticos ("Calma, mas age rápido", com a fonte só no crédito do
    cartão): repetida cedo demais numa fala que não a cita, a cena vira cartão tipográfico; na fala que cita a
    Malwarebytes, ela fica (com o aviso da repetição)."""
    from studio.core.project.document import SceneCard
    from tests.test_captura_reuso import _attach_doc, _fake_materialize

    mb = PoolItem(id="p232", tier="capture", media="capture",
                  desc='Sites falsos de demo do GTA 6 roubam informações — "Malwarebytes – Sites falsos de demo"',
                  page_url="https://www.malwarebytes.com/pt-br/blog/threat-intel/2026/08/fake-gta-6",
                  credit="malwarebytes.com", path="uploads/prj/media/mb.jpg")
    filler = "Enquanto isso, a imagem de IA mostra outra coisa por um tempo, sem nada da matéria."
    doc = _attach_doc(["A Malwarebytes achou sites falsos de demo do GTA 6 que roubam senhas.", filler,
                       "E se você já baixou? Calma, mas age rápido.", filler,
                       "Aí, como recomenda a Malwarebytes, troca as senhas de outro aparelho."],
                      ["image"] * 5)
    doc.scenes[2].card = SceneCard(source="Malwarebytes", headline={"pt-BR": "JÁ BAIXEI, E AGORA?"})
    for scene in doc.scenes[::2]:
        scene.visual.media = media_pool.pending_ref(mb)
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    warnings = media_pool.attach_chosen(doc, Pool(items=[mb]))
    first, calm, again = doc.ordered_scenes()[::2]
    assert first.visual.media is not None and first.visual.media.pool_id == "p232"
    assert calm.kind == "text" and calm.visual.media is None  # papel de parede: sai
    assert again.visual.media is not None and again.visual.media.pool_id == "p232"  # a fala é sobre ela
    assert any("cartão tipográfico" in w and "cedo demais" in w for w in warnings)


def test_any_media_back_too_soon_only_where_the_speech_is_about_it(isolated_settings, monkeypatch):
    """O limite vale para qualquer mídia, não só a captura: a mesma imagem do site falso volta cedo demais numa fala
    que não é sobre ela e vira cartão tipográfico; na fala sobre o site falso, fica."""
    from tests.test_captura_reuso import _attach_doc, _fake_materialize

    shot = PoolItem(id="p247", tier="official", media="image", desc="Site falso de GTA 6 nos resultados do Google",
                    topic="Malwarebytes", path="uploads/prj/media/g.jpg")
    filler = "Enquanto isso, a imagem de IA mostra outra coisa por um tempo, sem nada daquilo."
    doc = _attach_doc(["O site falso aparece no topo do Google, pago como anúncio.", filler,
                       "E se você já baixou? Calma, mas age rápido.", filler,
                       "Desconfie do site que aparece primeiro nos resultados do Google."],
                      ["image"] * 5)
    for scene in doc.scenes[::2]:
        scene.visual.media = media_pool.pending_ref(shot)
    monkeypatch.setattr(media_pool, "materialize", _fake_materialize(doc))
    media_pool.attach_chosen(doc, Pool(items=[shot]))
    first, calm, again = doc.ordered_scenes()[::2]
    assert first.visual.media is not None and first.visual.media.pool_id == "p247"
    assert calm.kind == "text" and calm.visual.media is None
    assert again.visual.media is not None and again.visual.media.pool_id == "p247"
    assert media_pool.line_about(shot, "Desconfie do site que aparece no Google")
    assert not media_pool.line_about(shot, "Calma, mas age rápido.")

