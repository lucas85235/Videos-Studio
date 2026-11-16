"""Personagens fixos com imagens âncora: cadastro (canal e projeto), storyboard, prompt e envio ao gerador.

O LLM é falso (o mesmo do Short do GTA 6 em test_storyboard_v2) e o gerador de imagem também: nada de rede nem de
Codex de verdade.
"""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError
from test_storyboard_v2 import GAMES, STYLES, FakeLLM, codes, gta_doc, load_fixture, run, scene_data

from studio.api.app import create_app
from studio.config import get_settings, set_settings
from studio.core.project import characters as characters_mod
from studio.core.project import service
from studio.core.project.document import (
    ArtDirection,
    Character,
    Entity,
    ProjectDoc,
    Scene,
    Segment,
    Visual,
)
from studio.core.styles.library import get_style
from studio.providers import image as image_mod
from studio.providers import registry
from studio.providers.base import ImageProvider, ImageResult, ProviderInfo, Usage
from studio.workers.pipeline import characters as character_scenes
from studio.workers.pipeline import storyboard_check
from studio.workers.pipeline.build import Builder, image_key

LOOK = "tall woman in her 30s with short silver hair and a red leather jacket"


def _png(color=(200, 40, 40), size=(640, 640)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def _client() -> TestClient:
    return TestClient(create_app())


def heroine(images: list[str] | None = None) -> Character:
    return Character(id="heroina", name="Lucia", aliases=["a protagonista"], look=LOOK, images=images or [])


# --- modelo -------------------------------------------------------------------------------------


def test_character_model_validates_ids_and_images():
    c = Character(id="detetive", name="Sam", aliases=["o detetive", " o detetive ", ""], look="old man")
    assert c.aliases == ["o detetive"] and c.names() == ["Sam", "o detetive"]
    with pytest.raises(ValidationError):
        Character(id="Com Espaço")
    with pytest.raises(ValidationError):  # só imagens guardadas pelo app (o arquivo vai para o gerador)
        Character(id="x", images=["../segredos.key"])
    with pytest.raises(ValidationError):
        Character(id="x", images=[f"library/characters/{i:016x}.jpg" for i in range(4)])


def test_merged_project_overrides_channel_by_id():
    channel = [Character(id="a", look="canal"), Character(id="b", look="canal b")]
    project = [Character(id="a", look="projeto")]
    out = characters_mod.merged(project, channel)
    assert [(c.id, c.look) for c in out] == [("a", "projeto"), ("b", "canal b")]
    bible = {"characters": [{"id": "ok", "look": "x"}, {"id": "Inválido"}, {"id": "ok"}], "identidade": "y"}
    assert [c.id for c in characters_mod.from_bible(bible)] == ["ok"]


def test_with_characters_uses_the_registered_look():
    art = ArtDirection(entities=[Entity(id="heroina", name="Lucia", look="look da IA"), Entity(id="cidade", look="c")])
    out = characters_mod.with_characters(art, [heroine(), Character(id="novo", look="small robot")])
    assert [e.id for e in out.entities] == ["heroina", "cidade", "novo"]
    assert out.entity("heroina").look == LOOK and out.entity("heroina").character
    assert art.entity("heroina").look == "look da IA"  # a direção original fica como estava


def test_reference_images_alternate_between_characters(tmp_path):
    root = tmp_path
    rels = []
    for i in range(5):
        rel = f"library/characters/{i:016x}.jpg"
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_bytes(b"x")
        rels.append(rel)
    a = Character(id="a", images=rels[:3])
    b = Character(id="b", images=rels[3:] + ["library/characters/ffffffffffffffff.jpg"])  # a última sumiu do disco
    visual = Visual(prompt="p", entities=["b", "a", "cidade"])
    refs = characters_mod.reference_images(visual, [a, b], root, limit=4)
    assert [p.name for p in refs] == [Path(rels[3]).name, Path(rels[0]).name, Path(rels[4]).name, Path(rels[1]).name]


# --- storyboard -----------------------------------------------------------------------------------


def test_storyboard_uses_registered_character_without_the_name(monkeypatch):
    doc = gta_doc()
    doc.characters = [heroine()]
    data = load_fixture(doc)
    # outra batida cita a personagem pela narração, mas a IA não marcou a entidade
    scene_data(data, doc, "4:1")["entities"] = []
    new_doc, _, review, fake = run(monkeypatch, doc, get_style(GAMES), data)

    prompt = fake.calls[0]["prompt"]
    assert "Personagens fixos" in prompt and "heroina" in prompt and LOOK in prompt

    art = new_doc.art
    assert art.entity("lucia") is None  # a entidade que a IA criou virou o personagem cadastrado
    entity = art.entity("heroina")
    assert entity is not None and entity.character and entity.look == LOOK
    assert storyboard_check.FACELESS not in entity.look  # personagem do canal aparece de frente

    scene = new_doc.ordered_scenes()[14]
    assert "heroina" in scene.visual.entities
    assert "lucia" not in scene.visual.prompt.lower()
    assert scene.visual.prompt.startswith("The tall woman")
    for s in new_doc.scenes:
        assert "lucia" not in s.visual.prompt.lower()
        assert "lucia" not in s.visual.entities
    assert "personagem_na_cena" in codes(review) and "nome_no_prompt" in codes(review)
    assert "personagem_sem_rosto" not in codes(review)


def test_storyboard_without_characters_is_unchanged(monkeypatch):
    doc = gta_doc()
    new_doc, _, _, fake = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    assert "Personagens fixos" not in fake.calls[0]["prompt"]
    assert new_doc.art.entity("lucia").look.endswith(storyboard_check.FACELESS)


def test_channel_characters_reach_the_storyboard_without_image_paths(monkeypatch):
    from studio.workers.pipeline import authoring

    doc = gta_doc()
    fake = FakeLLM(load_fixture(doc))
    monkeypatch.setattr(authoring, "_llm", fake)
    path = "library/characters/0123456789abcdef.jpg"
    channel = {"name": "Canal", "style_bible": {"identidade": "neon", "characters": [heroine([path]).model_dump()]}}
    new_doc, _, _ = authoring.storyboard(doc, get_style(GAMES), styles=STYLES, channel=channel)
    prompt = fake.calls[0]["prompt"]
    assert '"identidade": "neon"' in prompt and path not in prompt and "Personagens fixos" in prompt
    assert new_doc.art.entity("heroina").character


# --- build: prompt e imagens âncora -------------------------------------------------------------


class FakeImages(ImageProvider):
    def __init__(self, provider_id: str, references: bool) -> None:
        self.info = ProviderInfo(id=provider_id, kind="image", name=provider_id, commercial_use=True,
                                 accepts_references=references)
        self.calls: list[dict] = []

    def availability(self):
        return True, "ok"

    def generate(self, *, prompt, width, height, seed, out_dir, references=None):
        self.calls.append({"prompt": prompt, "references": references})
        path = Path(out_dir) / "image.jpg"
        Image.new("RGB", (8, 8)).save(path)
        return ImageResult(path=path, usage=Usage(units=1, unit_name="imagem"), model=self.info.id)


@pytest.fixture
def generator(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    fake = FakeImages("codex_cli", references=True)
    monkeypatch.setitem(registry._PROVIDERS, "codex_cli", fake)
    return fake


def _directed_project(characters: list[Character]):
    doc = service.create_project(title="Personagens", style_id="misterios-short")
    doc = doc.model_copy(update={
        "segments": [Segment(id="seg_1", text={"pt-BR": "Lucia entra no bar."})],
        "scenes": [
            Scene(id="scn_1", segment_id="seg_1", visual=Visual(prompt="A woman enters a bar at night.",
                                                                language="editorial", entities=["heroina"])),
        ],
        "art": ArtDirection(languages=["editorial"], entities=[Entity(id="heroina", name="Lucia", look="old look")]),
        "characters": characters,
        "style_overrides": {"art": {"image_chain": ["codex_cli"]}},
    })
    service.commit(doc.id, doc, "teste")
    return doc


def test_build_sends_anchor_images_and_the_look(generator):
    rel = _client().post("/api/characters/images", files={"file": ("a.png", _png(), "image/png")}).json()["path"]
    doc = _directed_project([heroine([rel])])
    builder = Builder(doc.id, mode="preview")
    scene = builder.doc.scenes[0]
    inputs = builder.image_inputs(scene)
    assert LOOK in inputs["prompt"] and "old look" not in inputs["prompt"] and "Lucia" not in inputs["prompt"]
    assert inputs["references"] == [Path(rel).stem]
    assert builder.image(scene)
    call = generator.calls[0]
    assert [p.name for p in call["references"]] == [Path(rel).name]

    # trocar a imagem âncora gera a cena de novo (outra chave de cache)
    other = _client().post("/api/characters/images", files={"file": ("b.png", _png((10, 200, 10)), "image/png")})
    builder2 = Builder(doc.id, mode="preview")
    builder2.doc.characters = [heroine([other.json()["path"]])]
    builder2.__dict__.pop("characters", None)
    assert image_key(builder2.image_inputs(scene)) != image_key(inputs)


def test_build_without_anchor_images_keeps_the_old_cache_key(generator):
    doc = _directed_project([])
    builder = Builder(doc.id, mode="preview")
    inputs = builder.image_inputs(builder.doc.scenes[0])
    assert "references" not in inputs and "old look" in inputs["prompt"]


def test_provider_without_references_gets_only_the_look(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    fake = FakeImages("codex_cli", references=False)
    monkeypatch.setitem(registry._PROVIDERS, "codex_cli", fake)
    rel = _client().post("/api/characters/images", files={"file": ("a.png", _png(), "image/png")}).json()["path"]
    doc = _directed_project([heroine([rel])])
    builder = Builder(doc.id, mode="preview")
    assert builder.image(builder.doc.scenes[0])
    assert fake.calls[0]["references"] is None and LOOK in fake.calls[0]["prompt"]


def _clap_exec(cmd: list[str]) -> tuple[list[str], str | None]:
    """Lê o comando como o clap do `codex exec`: `-i/--image` com `num_args = 1..` e `value_delimiter = ','` engole os
    valores seguintes até a próxima opção ou o `--`; o que sobra é o prompt (posicional)."""
    images: list[str] = []
    positional: list[str] = []
    args = cmd[2:]
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            positional += args[i + 1 :]
            break
        if arg in ("-i", "--image"):
            i += 1
            while i < len(args) and not args[i].startswith("-"):
                images += args[i].split(",")
                i += 1
            continue
        if arg.startswith("-"):
            i += 2 if arg == "--sandbox" else 1
            continue
        positional.append(arg)
        i += 1
    return images, (positional[0] if positional else None)


def test_codex_attaches_reference_images(monkeypatch, tmp_path):
    folder = tmp_path / "Silva, Ana"  # vírgula no caminho: o `-i` do Codex separa por vírgula
    folder.mkdir()
    ref = folder / "ref.jpg"
    Image.new("RGB", (8, 8), (200, 10, 10)).save(ref)
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setattr(image_mod.shutil, "which", lambda _name: "codex")
    seen: dict = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        images, prompt = _clap_exec(cmd)
        seen["images"] = [(Path(p).name, Path(p).read_bytes()) for p in images if Path(p).is_file()]
        seen["parsed"] = (images, prompt)
        out = codex_home / "generated_images" / "sessao"
        out.mkdir(parents=True)
        Image.new("RGB", (64, 64)).save(out / "img.png")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(image_mod, "run_tree", fake_run)
    provider = image_mod.CodexCLIImage()
    assert provider.info.accepts_references
    provider.generate(prompt="a woman, red jacket, night", width=64, height=64, seed=1, out_dir=tmp_path / "out",
                      references=[ref, tmp_path / "sumiu.jpg"])
    images, prompt = seen["parsed"]
    # a imagem chega inteira (cópia sem vírgula no nome) e a instrução não vira mais uma imagem
    assert len(images) == 1 and seen["images"] == [("ref1.jpg", ref.read_bytes())]
    assert prompt is not None and "referência de aparência" in prompt
    assert prompt.endswith("Descrição: a woman, red jacket, night")
    cmd = seen["cmd"]
    assert cmd[-2] == "--" and cmd[-1] == prompt


# --- rotas ----------------------------------------------------------------------------------------


def test_channel_characters_routes_and_form_keeps_them():
    client = _client()
    channel = client.post("/api/channels", json={"name": "Canal"}).json()
    up = client.post("/api/characters/images", files={"file": ("a.png", _png(), "image/png")})
    assert up.status_code == 200 and up.json()["url"].startswith("/files/library/characters/")
    body = {"characters": [{"id": "heroina", "name": "Lucia", "look": LOOK, "images": [up.json()["path"]]}]}
    saved = client.put(f"/api/channels/{channel['id']}/characters", json=body)
    assert saved.status_code == 200 and saved.json()[0]["images"] == [up.json()["path"]]

    # o formulário do canal, aberto antes, manda o style_bible sem os personagens: eles ficam
    client.put(f"/api/channels/{channel['id']}", json={"name": "Canal", "style_bible": {"identidade": "neon"}})
    got = client.get(f"/api/channels/{channel['id']}/characters").json()
    assert [c["id"] for c in got] == ["heroina"]
    bible = next(c for c in client.get("/api/channels").json() if c["id"] == channel["id"])["style_bible"]
    assert bible["identidade"] == "neon"

    dup = {"characters": [{"id": "a"}, {"id": "a"}]}
    assert client.put(f"/api/channels/{channel['id']}/characters", json=dup).status_code == 422
    bad = client.post("/api/characters/images", files={"file": ("a.txt", b"nada", "text/plain")})
    assert bad.status_code == 422


def test_project_characters_routes_merge_with_the_channel():
    client = _client()
    channel = client.post("/api/channels", json={"name": "Canal"}).json()
    client.put(f"/api/channels/{channel['id']}/characters",
               json={"characters": [{"id": "heroina", "look": "do canal"}, {"id": "robo", "look": "robot"}]})
    doc = service.create_project(title="P", style_id="misterios-short", channel_id=channel["id"])
    res = client.put(f"/api/projects/{doc.id}/characters", json={"characters": [{"id": "heroina", "look": "do projeto"}]})
    assert res.status_code == 200
    view = client.get(f"/api/projects/{doc.id}/characters").json()
    assert [(c["id"], c["look"]) for c in view["effective"]] == [("heroina", "do projeto"), ("robo", "robot")]
    assert service.get_doc(doc.id).characters[0].look == "do projeto"
    assert client.get("/api/projects/nao_existe/characters").status_code == 404


# --- nome composto, outra ordem, cena sem gente -----------------------------------------------


def test_full_name_registered_and_first_name_in_the_narration(monkeypatch):
    doc = gta_doc()
    doc.characters = [Character(id="heroina", name="Lucia Caminos", look=LOOK)]
    new_doc, _, review, _ = run(monkeypatch, doc, get_style(GAMES), load_fixture(doc))
    assert new_doc.art.entity("lucia") is None  # a entidade "Lucia" da IA virou o personagem cadastrado
    for s in new_doc.scenes:
        assert "lucia" not in s.visual.prompt.lower()
        assert "lucia" not in s.visual.entities
    scene = new_doc.ordered_scenes()[14]
    assert "heroina" in scene.visual.entities and scene.visual.prompt.startswith("The tall woman")
    assert "nome_no_prompt" in codes(review)


def test_entity_with_the_name_in_another_order_becomes_the_character():
    sam = Character(id="detetive", name="Detetive Sam", look="old man with a grey beard")
    art = ArtDirection(entities=[Entity(id="sam_detective", name="Sam (detective)", look="AI look"),
                                 Entity(id="detetive_rival", name="Rival Detective", look="young man")])
    out, remap = character_scenes.prepare(art, [sam])
    assert remap == {"sam_detective": "detetive"}
    assert out.entity("detetive").look == "old man with a grey beard"
    # sobrenome da família: a entidade que casa com dois personagens não é trocada
    a = Character(id="mae", name="Ana Caminos")
    b = Character(id="filha", name="Lia Caminos")
    _, remap = character_scenes.prepare(ArtDirection(entities=[Entity(id="caminos", name="Caminos")]), [a, b])
    assert remap == {}


def _narrated_doc(prompts: list[str]):
    text = "Sam nunca imaginou que o banco fecharia."
    segments = [Segment(id=f"seg_{i}", text={"pt-BR": text}) for i in range(len(prompts))]
    scenes = [Scene(id=f"scn_{i}", segment_id=f"seg_{i}", visual=Visual(prompt=p, language="editorial"))
              for i, p in enumerate(prompts)]
    doc = ProjectDoc(id="prj_sam", title="Sam", style_id="misterios-short", segments=segments, scenes=scenes,
                     art=ArtDirection(languages=["editorial"]))
    beats = [storyboard_check.Beat(key=f"seg_{i}:0", segment_id=f"seg_{i}", part=0, parts=1, index=i, role="body",
                                   words=text.split(), seconds=3.0) for i in range(len(prompts))]
    return doc, beats


def test_narration_alone_marks_only_scenes_that_show_someone():
    sam = Character(id="detetive", name="Sam", look="old man with a grey beard")
    doc, beats = _narrated_doc(["Establishing shot of an old bank facade at dusk",
                                "A man counts coins at a kitchen table"])
    issues = character_scenes.finish(doc, beats, [sam], {})
    bank, kitchen = doc.scenes
    assert "detetive" not in bank.visual.entities and "detetive" in kitchen.visual.entities
    found = {(i.scene_id, i.severity) for i in issues if i.code == "personagem_na_cena"}
    assert found == {("scn_0", storyboard_check.AVISO), ("scn_1", storyboard_check.CORRIGIDO)}


# --- build: descrição editada, cache sem as imagens âncora -------------------------------------


def _scene_with_reference(characters: list[Character], **update):
    doc = _directed_project(characters)
    doc = service.get_doc(doc.id)
    doc.scenes[0].visual.prompt = "The tall woman enters a bar at night."
    doc.art.entities = [Entity(id="heroina", name="Lucia", look=LOOK, character=True)]
    for key, value in update.items():
        setattr(doc, key, value)
    service.commit(doc.id, doc, "teste")
    return doc


def test_edited_look_updates_the_prompt_and_makes_the_image_again(generator):
    doc = _scene_with_reference([heroine()])
    builder = Builder(doc.id, mode="preview")
    assert builder.image(builder.doc.scenes[0])
    assert "The tall woman enters" in generator.calls[0]["prompt"]

    doc = service.get_doc(doc.id)
    doc.characters = [Character(id="heroina", name="Lucia", look="old man with a grey beard and a brown coat")]
    service.commit(doc.id, doc, "teste")
    builder2 = Builder(doc.id, mode="preview")
    prompt = builder2.image_inputs(builder2.doc.scenes[0])["prompt"]
    assert "The old man enters" in prompt and "tall woman" not in prompt
    assert builder2.image(builder2.doc.scenes[0])
    assert len(generator.calls) == 2  # nem o prompt antigo acha a imagem da descrição velha


def test_image_made_without_anchor_images_is_made_again_when_codex_is_back(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    codex = FakeImages("codex_cli", references=True)
    fallback = FakeImages("fal_image", references=False)
    up = {"ok": False}
    monkeypatch.setattr(codex, "availability", lambda: (up["ok"], "ok" if up["ok"] else "sem cota"))
    monkeypatch.setitem(registry._PROVIDERS, "codex_cli", codex)
    monkeypatch.setitem(registry._PROVIDERS, "fal_image", fallback)
    rel = _client().post("/api/characters/images", files={"file": ("a.png", _png(), "image/png")}).json()["path"]
    doc = _scene_with_reference([heroine([rel])],
                                style_overrides={"art": {"image_chain": ["codex_cli", "fal_image"]}})

    builder = Builder(doc.id, mode="preview")
    assert builder.image(builder.doc.scenes[0])
    assert not codex.calls and fallback.calls[0]["references"] is None

    up["ok"] = True
    builder2 = Builder(doc.id, mode="preview")
    assert builder2.image(builder2.doc.scenes[0])
    assert [p.name for p in codex.calls[0]["references"]] == [Path(rel).name]

    builder3 = Builder(doc.id, mode="preview")  # agora com as imagens âncora: fica no cache
    assert builder3.image(builder3.doc.scenes[0])
    assert len(codex.calls) == 1
