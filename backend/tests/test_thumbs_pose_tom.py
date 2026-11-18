"""Tom da pose do avatar nas thumbs do pacote: a variedade (gerador e render) nunca troca a cara de alerta pela de
festa. Os casos reais do pacote de 18/11 (GTA 6 de PC com malware) com poses sintéticas iguais às do canal."""

from __future__ import annotations

from studio.core.project.document import ThumbLayers
from studio.media import pose_tone as PT
from studio.media import thumb_variety as V
from studio.workers import tasks
from studio.workers.pipeline import authoring

# as poses de thumb do canal (ids, emoções e apelidos como no manifesto do apresentador)
POSES = [
    {"id": "apontando", "emotions": ["explicar", "mostrar a prova"], "aliases": ["explicando"], "facing": "right"},
    {"id": "comemorando", "emotions": ["boa notícia grande", "lançamento", "comemoração"], "aliases": ["animado"]},
    {"id": "dinheiro", "emotions": ["custo", "preço", "vendas"], "aliases": []},
    {"id": "lupa", "emotions": ["detalhe escondido", "investigação"], "aliases": ["investigando"]},
    {"id": "mao-na-testa", "emotions": ["vergonha alheia", "erro"], "aliases": ["vergonha"]},
    {"id": "segredo", "emotions": ["rumor", "segredo", "spoiler"], "aliases": []},
    {"id": "surpreso", "emotions": ["choque", "surpresa"], "aliases": ["espantado"]},
]
# os candidatos do dono que ficaram na tela (GOLPE com mão na testa e com surpreso)
KEPT = {"mao-na-testa", "surpreso"}


def _text(title: str, word: str, concept: str) -> str:
    return " ".join([title, word, concept])


def test_tom_das_poses_do_canal():
    tones = {p["id"]: PT.pose_tone(p) for p in POSES}
    assert tones == {"apontando": PT.NEUTRO, "comemorando": PT.POSITIVO, "dinheiro": PT.POSITIVO,
                     "lupa": PT.MISTERIO, "mao-na-testa": PT.ALERTA, "segredo": PT.MISTERIO, "surpreso": PT.ALERTA}
    assert PT.pose_tone({"id": "xyz", "emotions": ["algo novo"]}) == PT.NEUTRO  # sem emoção conhecida: neutra
    assert PT.pose_tone({"id": "chocado"}) == PT.ALERTA  # sem emoção, pelo nome
    assert PT.pose_tone({"id": "x", "emotions": ["choque"], "tone": "positivo"}) == PT.POSITIVO  # explícito vence


def test_tom_do_texto():
    assert PT.text_tone("Esse GTA 6 de PC rouba suas SENHAS") == PT.ALERTA
    assert PT.text_tone("GTA 6 no PC: NÃO baixe esse arquivo", "VÍRUS") == PT.ALERTA
    assert PT.text_tone("GTA 6 grátis é golpe") == PT.ALERTA  # alerta vence a festa
    assert PT.text_tone("Dá pra jogar GTA 6 no PC pela nuvem?", "SÓ XBOX", "dúvida da nuvem, fazendo segredo") \
        == PT.MISTERIO
    assert PT.text_tone("GTA 6 de graça: finalmente chegou", "GRÁTIS") == PT.POSITIVO
    assert PT.text_tone("GTA 6 no PC: quando sai?") is None


def test_1_1_mb_rouba_senhas_nao_comemora():
    # concept pedia "apresentador grande e surpreso"; surpreso e mão na testa já estão no pacote. Antes: comemorando.
    notes: list[str] = []
    text = _text("Esse GTA 6 de PC rouba suas SENHAS", "1,1 MB",
                 "Avatar-reage: o apresentador grande e surpreso em primeiro plano, clima de crime.")
    for hint in ("surpreso", None):
        pick = authoring._pick_pose(hint, POSES, set(KEPT), concept=text, text=text, notes=notes)
        assert pick == "surpreso"  # nenhuma outra de alerta livre: repete em vez de inverter o tom
    assert any("AVISO: a pose surpreso repete" in n for n in notes)
    # o LLM citou a festa numa thumb de golpe: sai, entra uma de alerta
    notes = []
    pick = authoring._pick_pose("comemorando", POSES, set(), concept="", text=text, notes=notes)
    assert PT.pose_tone(next(p for p in POSES if p["id"] == pick)) == PT.ALERTA
    assert any("CORRIGIDO: a pose comemorando (positivo)" in n for n in notes)


def test_so_xbox_fazendo_segredo_nao_vira_dinheiro():
    # concept pedia o avatar "fazendo segredo" e segredo já estava em outra thumb do pacote. Antes: dinheiro.
    concept = "Ângulo pergunta, focado na dúvida da nuvem. O avatar pequeno fazendo segredo."
    text = _text("Dá pra jogar GTA 6 no PC pela nuvem?", "SÓ XBOX", concept)
    assert authoring._pick_pose(None, POSES, set(KEPT), concept=concept, text=text) == "segredo"
    used = KEPT | {"segredo"}
    assert authoring._pick_pose(None, POSES, used, concept=concept, text=text) == "lupa"  # mesmo tom (mistério)
    assert authoring._pick_pose(None, POSES, used | {"lupa"}, concept=concept, text=text) == "segredo"


def test_virus_mao_na_testa_nao_vira_segredo():
    # concept pedia "mão na testa", que já estava no pacote. Antes: segredo.
    concept = "Template prova: a captura da Malwarebytes sobre os sites falsos. Avatar pequeno com a mão na testa."
    text = _text("GTA 6 no PC: NÃO baixe esse arquivo", "VÍRUS", concept)
    notes: list[str] = []
    assert authoring._pick_pose(None, POSES, set(KEPT), concept=concept, text=text, notes=notes) == "mao-na-testa"
    assert any("repete outra thumb do pacote" in n for n in notes)
    # com a mão na testa vista nas recentes do canal e surpreso livre: troca dentro do alerta
    recent = V.Usage()
    recent.add({"pose": ["mao-na-testa"]}, 0.8)
    assert authoring._pick_pose("mão na testa", POSES, set(), recent, concept=concept, text=text) == "surpreso"


def test_sem_pose_citada_escolhe_pelo_tom_e_sem_tom_a_neutra():
    alerta = _text("Cuidado com esse download falso", "GOLPE", "")
    assert authoring._pick_pose(None, POSES, set(), text=alerta) in {"surpreso", "mao-na-testa"}
    assert authoring._pick_pose(None, POSES, {"surpreso", "mao-na-testa"}, text=alerta) == "apontando"
    assert authoring._pick_pose(None, POSES, set(), text="GTA 6 no PC: quando sai?") == "apontando"
    # sem tom e sem a neutra livre: as que não são de festa antes das positivas
    pick = authoring._pick_pose(None, POSES, {"apontando"}, text="GTA 6 no PC: quando sai?")
    assert PT.pose_tone(next(p for p in POSES if p["id"] == pick)) != PT.POSITIVO
    # só poses de festa: thumb de alerta fica sem avatar
    party = [p for p in POSES if p["id"] in ("comemorando", "dinheiro")]
    assert authoring._pick_pose(None, party, set(), text=alerta) is None


def test_comemorando_continua_em_thumb_positiva():
    text = _text("GTA 6 de graça: finalmente chegou", "GRÁTIS", "o apresentador animado")
    assert authoring._pick_pose("comemorando", POSES, set(), text=text) == "comemorando"
    assert authoring._pick_pose(None, POSES, set(), concept="o apresentador animado", text=text) == "comemorando"
    assert authoring._pick_pose(None, POSES, {"comemorando"}, text=text) == "dinheiro"  # o mesmo tom


def _layers(pose: str) -> ThumbLayers:
    return ThumbLayers.model_validate({"template": "avatar-reage", "background": {"kind": "frame", "ref": "neon"},
                                       "presenter": {"pose": pose, "side": "right"},
                                       "text": {"words": "VÍRUS", "fill": "yellow", "pos": "left"}})


def test_troca_do_render_fica_no_mesmo_tom():
    cost = V.Usage()
    cost.add({"pose": ["mao-na-testa"]}, 2.0)
    swapped = tasks._swap_pose(_layers("surpreso"), POSES, lambda pid: cost.n("pose", pid))
    assert swapped is not None and swapped[1] == "mao-na-testa"  # a única outra de alerta, mesmo mais usada
    # sem outra de alerta: a pose fica (None) em vez de virar segredo, lupa ou comemorando
    only = [p for p in POSES if p["id"] != "mao-na-testa"]
    assert tasks._swap_pose(_layers("surpreso"), only, lambda pid: 0.0) is None
    # positiva troca por positiva
    swapped = tasks._swap_pose(_layers("comemorando"), POSES, lambda pid: 0.0)
    assert swapped is not None and swapped[1] == "dinheiro"


def test_pacote_com_os_tres_casos_reais():
    raw = [
        {"title": "Esse GTA 6 de PC rouba suas SENHAS", "thumb_text": "1,1 MB", "template": "avatar-reage",
         "concept": "Avatar-reage: o apresentador grande e surpreso em primeiro plano.", "presenter_pose": "surpreso"},
        {"title": "Dá pra jogar GTA 6 no PC pela nuvem?", "thumb_text": "SÓ XBOX", "template": "numero-lista",
         "concept": "Ângulo pergunta, focado na dúvida da nuvem; o avatar pequeno fazendo segredo.",
         "presenter_pose": None},
        {"title": "GTA 6 no PC: NÃO baixe esse arquivo", "thumb_text": "VÍRUS", "template": "avatar-reage",
         "concept": "Sites falsos de demo. Avatar com a mão na testa.", "presenter_pose": "mão na testa"},
    ]
    for item in raw:
        item.update(promise="p", background_hint=None, characters_hint=[], pill=None, cards=[])
    assets = authoring.PackageAssets(poses=POSES)
    cands = authoring._normalize_candidates(raw, assets=assets, templates=["avatar-reage", "numero-lista"],
                                            is_long=True, used_poses=set(KEPT))
    poses = {c.thumb_text: c.layers.presenter.pose for c in cands}
    assert poses == {"1,1 MB": "surpreso", "SÓ XBOX": "segredo", "VÍRUS": "mao-na-testa"}
