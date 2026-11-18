"""Fase 1 (modelos): pacote título+thumb, camadas da thumb, avatar do apresentador, campos novos do estilo,
PACKAGE_SCHEMA e o registro dos subcomandos da CLI."""

from __future__ import annotations

import sys
import types

import pytest
from pydantic import ValidationError

from studio.core.project.document import (
    CHANNEL_THUMBS_KEY,
    PRESENTER_KEY,
    ChannelThumbs,
    PackageCandidate,
    PackageDoc,
    Presenter,
    PresenterOverride,
    PresenterPose,
    ProjectDoc,
    Scene,
    ScenePresenter,
    ThumbLayers,
    effective_allow_editorial,
)
from studio.core.styles.library import load_presets
from studio.core.styles.schema import (
    DEFAULT_THUMB_PALETTE,
    THUMB_TEMPLATES,
    EditSpec,
    StyleSpec,
    ThumbnailSpec,
    package_gate_default,
)

OLD_DOC = {
    "schema_version": 1,
    "id": "prj_antigo",
    "title": "Projeto antigo",
    "style_id": "lucas-em-vice-longo",
    "segments": [{"id": "seg_a", "role": "hook", "text": {"pt-BR": "Olá"}}],
    "scenes": [{"id": "scn_a", "segment_id": "seg_a"}],
    "thumbnail": {"text": {"pt-BR": "VELHO"}, "scene_id": "scn_a", "prompt": None, "variation": 1},
}


def _doc(**extra) -> ProjectDoc:
    return ProjectDoc.model_validate({**OLD_DOC, **extra})


# --- compatibilidade ----------------------------------------------------------------------------------------------
def test_documento_antigo_carrega_sem_campos_novos():
    doc = _doc()
    assert doc.package == PackageDoc()
    assert doc.package.candidates == [] and doc.package.chosen_id is None and not doc.package.locked
    assert doc.presenter == PresenterOverride()
    assert doc.scenes[0].presenter is None
    assert doc.chosen_package() is None
    assert doc.effective_title() == "Projeto antigo"
    # ThumbnailDoc continua igual.
    assert doc.thumbnail.text == {"pt-BR": "VELHO"} and doc.thumbnail.variation == 1


def test_estilo_antigo_sem_campos_novos_valida():
    old = {"template": "texto-centro", "variations": 2, "per_language": True, "font": "Anton",
           "color": "#FFFFFF", "accent": "#FFD400"}
    spec = ThumbnailSpec.model_validate(old)
    assert spec.templates == ["heroi-palavra", "numero-lista", "avatar-reage"]
    assert spec.title_candidates == 5 and spec.thumb_candidates == 3
    assert spec.allow_editorial is False  # material oficial só no canal que liga
    assert spec.gate_package is True
    assert spec.presenter is True  # segue o canal; o avatar do canal vem desligado
    assert spec.palette == DEFAULT_THUMB_PALETTE
    assert EditSpec().presenter == "auto"


def test_presets_continuam_validando():
    presets = load_presets()
    assert presets
    for style in presets.values():
        StyleSpec.model_validate(style.model_dump(mode="json"))


def test_thumbnail_spec_rejeita_valores_invalidos():
    with pytest.raises(ValidationError):
        ThumbnailSpec(templates=["nao-existe"])
    with pytest.raises(ValidationError):
        ThumbnailSpec(title_candidates=9)
    with pytest.raises(ValidationError):
        ThumbnailSpec(thumb_candidates=1)
    with pytest.raises(ValidationError):
        ThumbnailSpec(palette={"yellow": "amarelo"})
    with pytest.raises(ValidationError):
        ThumbnailSpec.model_validate({"campo_estranho": 1})  # continua estrito
    # Paleta parcial completa com o padrão.
    assert ThumbnailSpec(palette={"yellow": "#EEEE00"}).palette["pink"] == DEFAULT_THUMB_PALETTE["pink"]


def test_gate_package_nunca_no_short():
    base = load_presets()
    style = next(iter(base.values()))
    data = style.model_dump(mode="json")
    data["format"]["type"] = "long"
    assert package_gate_default(StyleSpec.model_validate(data)) is True
    data["thumbnail"]["gate_package"] = False
    assert package_gate_default(StyleSpec.model_validate(data)) is False
    data["thumbnail"]["gate_package"] = True
    data["format"]["type"] = "short"
    assert package_gate_default(StyleSpec.model_validate(data)) is False


# --- pacote -------------------------------------------------------------------------------------------------------
def _candidate(cid: str, title: str, words: str = "SEM SPOILER", **extra) -> PackageCandidate:
    return PackageCandidate(id=cid, title=title, thumb_text=words, promise="o que muda", concept="rosto + VI",
                            **extra)


def test_package_round_trip_e_titulo_efetivo():
    layers = ThumbLayers.model_validate({
        "template": "avatar-reage",
        "background": {"kind": "frame", "ref": "frame:GTAVI_Trailer_2@41.5", "t": 41.5, "zoom": 1.4, "focus_x": 0.7},
        "big_vi": True,
        "characters": [{"asset": "cutout:lucia", "x": 0.75, "y": 1.0, "h": 0.95, "flip": True}],
        "presenter": {"pose": "surpreso", "side": "right", "crop": "busto", "h": 1.5, "outline_px": 10},
        "text": {"words": "1ª SEMANA", "fill": "yellow", "pos": "left", "size": 0.3},
        "pill": "SEM SPOILER",
        "cards": [{"asset": None, "label": "mapa"}],
        "arrow": {"from_x": 0.3, "from_y": 0.6, "to_x": 0.5, "to_y": 0.4, "color": "yellow"},
        "strike": True,
    })
    a = _candidate("pkc_a", "GTA 6: a primeira semana", layers=layers, approved=True)
    b = _candidate("pkc_b", "Joguei 7 dias de GTA 6", words="7 DIAS", approved=True)
    c = _candidate("pkc_c", "Outro", words="NÃO")
    doc = _doc(package={"candidates": [a.model_dump(), b.model_dump(), c.model_dump()], "chosen_id": "pkc_b"})
    again = ProjectDoc.model_validate(doc.model_dump(mode="json"))
    assert again.package == doc.package
    assert again.package.candidates[0].layers.presenter.pose == "surpreso"
    assert again.chosen_package().id == "pkc_b"
    assert [x.id for x in again.package.approved()] == ["pkc_b", "pkc_a"]
    # Sem trava, o título é o do projeto; com trava, o do escolhido.
    assert again.effective_title() == "Projeto antigo"
    again.package.locked = True
    assert again.effective_title() == "Joguei 7 dias de GTA 6"


def test_package_escolhido_apagado_perde_escolha_e_trava():
    pkg = PackageDoc.model_validate({"candidates": [_candidate("pkc_a", "A").model_dump()], "chosen_id": "pkc_x",
                                     "locked": True})
    assert pkg.chosen_id is None and pkg.locked is False


def test_package_ids_repetidos_recusados():
    with pytest.raises(ValidationError):
        PackageDoc(candidates=[_candidate("pkc_a", "A"), _candidate("pkc_a", "B")])


def test_candidato_padroes_e_limpeza():
    c = PackageCandidate(title="  GTA   6  ", thumb_text=" UMA  DUAS TRES QUATRO ")
    assert c.id.startswith("pkc_")
    assert c.title == "GTA 6"
    assert c.thumb_words() == 4  # o modelo não corta: quem normaliza é a autoria (aviso, não erro)
    assert c.source == "auto" and c.layers.template == "heroi-palavra" and c.layers.logo == "corner"
    with pytest.raises(ValidationError):
        ThumbLayers(template="inexistente")
    with pytest.raises(ValidationError):
        ThumbLayers(cards=[{"label": "1"}, {"label": "2"}, {"label": "3"}, {"label": "4"}])


# --- avatar -------------------------------------------------------------------------------------------------------
def _pose(pid: str, **extra) -> dict:
    return {"id": pid, "file": f"library/presenter/chn_x/{pid}.png", **extra}


def test_presenter_do_canal_padrao_desligado_e_tolerante():
    assert Presenter.from_bible(None).enabled is False
    assert Presenter.from_bible({"characters": []}) == Presenter()
    assert Presenter.from_bible({PRESENTER_KEY: {"poses": "lixo"}}) == Presenter()  # inválido não quebra
    p = Presenter.from_bible({PRESENTER_KEY: {
        "enabled": True, "character_id": "lucas",
        "poses": [_pose("surpreso", emotions=["Choque", "choque", " surpresa "], thumb_ok=True),
                  _pose("apontando", facing="right", aliases=["explicando"])],
    }})
    assert p.enabled and p.use_in_thumb and p.use_in_video and not p.use_in_shorts
    assert p.pose("surpreso").emotions == ["choque", "surpresa"]
    assert [x.id for x in p.thumb_poses()] == ["surpreso"]
    assert p.pose("apontando").names() == ["apontando", "explicando"]
    assert p.video.hide_on_vertical is True and p.video.every_s == (60, 90)


def test_presenter_pose_valida_caminho_e_ids():
    with pytest.raises(ValidationError):
        PresenterPose(id="x", file="C:/fora/x.png")
    with pytest.raises(ValidationError):
        PresenterPose(id="x", file="library/presenter/chn_x/x.jpg")
    with pytest.raises(ValidationError):
        PresenterPose(id="Com Espaço", file="library/presenter/chn_x/x.png")
    with pytest.raises(ValidationError):
        Presenter(poses=[_pose("a"), _pose("a")])
    with pytest.raises(ValidationError):
        Presenter.model_validate({"video": {"every_s": [90, 60]}})


def test_scene_presenter_gatilho():
    assert ScenePresenter(at="chocante").at == "chocante"
    assert ScenePresenter(at="chocante#2").at == "chocante#2"
    assert ScenePresenter(at="@1200").at == "@1200"
    assert ScenePresenter(at="  ").at is None
    for bad in ("duas palavras", "@abc", "x#y"):
        with pytest.raises(ValidationError):
            ScenePresenter(at=bad)
    scene = Scene(segment_id="seg_a", presenter={"pose": "surpreso", "at": "GTA", "mode": "explica",
                                                 "source": "manual"})
    assert scene.presenter.mode == "explica" and scene.presenter.side == "auto"
    doc = _doc(scenes=[scene.model_dump()], presenter={"enabled": False, "poses_off": ["oculos"]})
    again = ProjectDoc.model_validate(doc.model_dump(mode="json"))
    assert again.scenes[0].presenter == scene.presenter
    assert again.presenter.enabled is False and again.presenter.thumb is None


def test_arte_oficial_canal_vence_estilo():
    assert effective_allow_editorial(False, None) is False
    assert effective_allow_editorial(True, {}) is True
    assert effective_allow_editorial(False, {CHANNEL_THUMBS_KEY: {"allow_editorial": True}}) is True
    assert effective_allow_editorial(True, {CHANNEL_THUMBS_KEY: {"allow_editorial": False}}) is False
    # IA de personagem oficial nunca é liberada: valor diferente de False é recusado (e vira o padrão).
    with pytest.raises(ValidationError):
        ChannelThumbs(ai_official_characters=True)
    assert ChannelThumbs.from_bible({CHANNEL_THUMBS_KEY: {"ai_official_characters": True}}) == ChannelThumbs()


# --- esquema do LLM -----------------------------------------------------------------------------------------------
def _strict(schema: dict) -> None:
    if schema.get("type") == "object":
        assert schema["additionalProperties"] is False
        assert set(schema["required"]) == set(schema["properties"])
        for sub in schema["properties"].values():
            _strict(sub)
    if "items" in schema:
        _strict(schema["items"])


def test_package_schema_estrito():
    from studio.workers.pipeline.schemas import PACKAGE_SCHEMA, PRESENTER_HINT_FIELDS

    _strict(PACKAGE_SCHEMA)
    item = PACKAGE_SCHEMA["properties"]["candidates"]["items"]["properties"]
    assert set(item) == {"title", "thumb_text", "promise", "concept", "template", "background_hint",
                         "characters_hint", "presenter_pose", "pill", "cards"}
    assert item["template"]["enum"] == list(THUMB_TEMPLATES)
    assert set(PRESENTER_HINT_FIELDS) == {"presenter_pose", "presenter_at", "presenter_mode"}


# --- CLI ----------------------------------------------------------------------------------------------------------
def test_cli_registra_subcomandos_tolerante(monkeypatch: pytest.MonkeyPatch):
    from studio import cli

    calls: list[str] = []
    fake = types.ModuleType("studio.cli_thumbkit")
    fake.COMMAND = "thumbkit"

    def register(sub):
        p = sub.add_parser("thumbkit")
        p.add_argument("acao")

    def run(args):
        calls.append(args.acao)
        return 0

    fake.register = register
    fake.run = run
    monkeypatch.setitem(sys.modules, "studio.cli_thumbkit", fake)
    # O módulo do avatar fica "inexistente": não pode derrubar os outros comandos.
    monkeypatch.setitem(sys.modules, "studio.cli_presenter", None)
    plugins = cli._load_plugins()
    assert set(plugins) == {"thumbkit"}
    cli.main(["thumbkit", "importar"])
    assert calls == ["importar"]

    fake.run = lambda args: 3
    with pytest.raises(SystemExit) as exc:
        cli.main(["thumbkit", "x"])
    assert exc.value.code == 3
