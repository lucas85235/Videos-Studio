"""LUTs .cube: leitura, aplicação trilinear, LUTs prontos, cache no build e rotas da biblioteca."""

from __future__ import annotations

import io

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pydantic import ValidationError

from studio.core.project import service
from studio.core.styles.schema import LUT_PRESETS, EffectSpec
from studio.media import lut as lut_mod
from studio.media.lut import Lut, LutError, apply_lut, builtin_lut, identity_grid, parse_cube, write_cube


def _cube_from(fn, size: int = 5, title: str = "teste") -> str:
    data = np.clip(fn(identity_grid(size)), 0, 1)
    return write_cube(Lut(size=size, dim=3, data=data.astype(np.float32), title=title))


# --------------------------------------------------------------------------------------------
# Leitura
# --------------------------------------------------------------------------------------------


def test_parse_cube_identidade_com_comentarios_e_titulo():
    text = """# feito à mão
TITLE "Identidade"
LUT_3D_SIZE 2
DOMAIN_MIN 0 0 0
DOMAIN_MAX 1 1 1

0 0 0
1 0 0
0 1 0
1 1 0  # comentário no fim da linha
0 0 1
1 0 1
0 1 1
1 1 1
"""
    lut = parse_cube(text)
    assert lut.size == 2 and lut.dim == 3 and lut.title == "Identidade"
    colors = np.random.default_rng(1).random((50, 3), dtype=np.float32)
    assert np.allclose(apply_lut(colors, lut), colors, atol=1e-6)


def test_ordem_do_cube_vermelho_varia_mais_rapido():
    # LUT que troca vermelho com azul: só funciona se a ordem dos dados for lida certo.
    text = _cube_from(lambda c: c[..., ::-1], size=3)
    lut = parse_cube(text)
    out = apply_lut(np.array([[0.9, 0.2, 0.1]], dtype=np.float32), lut)
    assert np.allclose(out, [[0.1, 0.2, 0.9]], atol=1e-5)


def test_trilinear_exato_para_funcao_linear():
    # Uma função afim por canal é reproduzida exatamente pela interpolação trilinear, com qualquer tamanho.
    fn = lambda c: 0.1 + 0.7 * c[..., [1, 2, 0]]  # noqa: E731
    lut = parse_cube(_cube_from(fn, size=4))
    colors = np.random.default_rng(2).random((200, 3), dtype=np.float32)
    assert np.allclose(apply_lut(colors, lut), fn(colors), atol=1e-4)


def test_dominio_e_1d():
    one_d = parse_cube("LUT_1D_SIZE 3\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 2 2 2\n0 0 0\n0.5 0.5 0.5\n0 0 0\n")
    assert one_d.dim == 1
    out = apply_lut(np.array([[0.5, 1.0, 2.0]], dtype=np.float32), one_d)
    assert np.allclose(out, [[0.25, 0.5, 0.0]])
    resolve = parse_cube("LUT_3D_INPUT_RANGE 0 2\nLUT_3D_SIZE 2\n" + "\n".join(
        f"{r} {g} {b}" for b in (0, 1) for g in (0, 1) for r in (0, 1)))
    assert resolve.domain_max == (2.0, 2.0, 2.0)
    assert np.allclose(apply_lut(np.array([[1.0, 1.0, 1.0]], dtype=np.float32), resolve), [[0.5, 0.5, 0.5]])


@pytest.mark.parametrize(
    ("text", "trecho"),
    [
        ("0 0 0\n", "não parece um .cube"),
        ("LUT_3D_SIZE 2\n0 0 0\n", "esperava 8"),
        ("LUT_3D_SIZE 1\n0 0 0\n", "fora de 2"),
        ("LUT_3D_SIZE 2\n" + "0 0 x\n" * 8, "número inválido"),
        ("LUT_3D_SIZE 2\n" + "0 0\n" * 8, "esperava 3"),
        ("LUT_3D_SIZE 2\nDOMAIN_MIN 1 1 1\nDOMAIN_MAX 0 0 0\n" + "0 0 0\n" * 8, "DOMAIN_MAX"),
        ("LUT_3D_SIZE 2\n0 0 0\nLUT_1D_SIZE 2\n", "antes dos dados"),
    ],
)
def test_cube_invalido_diz_o_motivo(text, trecho):
    with pytest.raises(LutError, match=trecho):
        parse_cube(text)


def test_escrever_e_ler_de_novo_da_o_mesmo_lut():
    lut = builtin_lut("teal-orange")
    again = parse_cube(write_cube(lut))
    assert again.size == lut.size and np.allclose(again.data, lut.data, atol=1e-6)
    assert again.title.startswith("Vulto")


# --------------------------------------------------------------------------------------------
# LUTs prontos e aplicação na imagem
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", LUT_PRESETS)
def test_luts_prontos_mudam_a_cor_e_ficam_no_intervalo(name):
    lut = builtin_lut(name)
    assert lut.size == lut_mod.BUILTIN_SIZE and lut.data.shape == (lut.size**3, 3)
    assert lut.data.min() >= 0 and lut.data.max() <= 1
    grid = identity_grid(lut.size)
    assert np.abs(lut.data - grid).mean() > 0.01  # não é identidade
    assert lut.digest == builtin_lut(name).digest


def test_mono_contrast_sai_cinza():
    out = apply_lut(np.array([[0.8, 0.2, 0.1], [0.1, 0.6, 0.9]], dtype=np.float32), builtin_lut("mono-contrast"))
    assert np.all(np.ptp(out, axis=1) < 0.08)


def test_intensidade_mistura_com_a_original():
    rgb = (np.random.default_rng(3).random((300, 40, 3)) * 255).astype(np.uint8)  # mais de um bloco de linhas
    lut = builtin_lut("cross-process")
    assert np.array_equal(lut_mod.apply_to_array(rgb, lut, 0.0), rgb)
    full = lut_mod.apply_to_array(rgb, lut, 1.0).astype(int)
    half = lut_mod.apply_to_array(rgb, lut, 0.5).astype(int)
    assert np.abs(half - (rgb.astype(int) + full) / 2).max() <= 1.0


def test_grade_image_gira_pelo_exif_e_mantem_transparencia(tmp_path):
    src = tmp_path / "foto.jpg"
    img = Image.new("RGB", (40, 20), (200, 100, 50))
    exif = Image.Exif()
    exif[0x0112] = 6  # girar 90°
    img.save(src, exif=exif)
    out = lut_mod.grade_image(src, tmp_path / "out", builtin_lut("warm-amber"), 1.0)
    with Image.open(out) as graded:
        assert graded.size == (20, 40) and out.suffix == ".jpg"
    png = tmp_path / "logo.png"
    rgba = Image.new("RGBA", (10, 10), (255, 255, 255, 0))
    rgba.putpixel((5, 5), (255, 0, 0, 255))
    rgba.save(png)
    out_png = lut_mod.grade_image(png, tmp_path / "out2", builtin_lut("cold-night"), 1.0)
    with Image.open(out_png) as graded:
        assert out_png.suffix == ".png" and graded.mode == "RGBA" and graded.getpixel((0, 0))[3] == 0


# --------------------------------------------------------------------------------------------
# Estilo (StyleSpec)
# --------------------------------------------------------------------------------------------


def test_efeito_lut_no_stylespec():
    assert EffectSpec(type="lut").preset == LUT_PRESETS[0]
    assert EffectSpec(type="lut", preset="faded-film").preset == "faded-film"
    assert EffectSpec(type="lut", preset="Meu Look (v2).cube").preset == "Meu Look (v2).cube"
    for bad in ("nao-existe", "../fora.cube", "c:\\x.cube"):
        with pytest.raises(ValidationError, match="LUT desconhecido"):
            EffectSpec(type="lut", preset=bad)


# --------------------------------------------------------------------------------------------
# Build: imagens gradadas em cache
# --------------------------------------------------------------------------------------------


def _builder_with_lut(isolated_settings, preset: str, amount: float = 1.0):
    from studio.workers.pipeline.build import Builder

    doc = service.create_project(title="LUT", style_id="misterios-short", topic="teste de cor")
    builder = Builder(doc.id)
    effects = [e for e in builder.style.edit.effects if e.type != "lut"]
    effects.append(EffectSpec(type="lut", preset=preset, amount=amount))
    builder.style = builder.style.model_copy(update={"edit": builder.style.edit.model_copy(update={"effects": effects})})
    folder = isolated_settings.data_dir / "uploads"
    folder.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 48), (180, 120, 60)).save(folder / "a.jpg")
    (folder / "clip.mp4").write_bytes(b"nao e imagem")
    return builder


def test_build_aplica_lut_e_reaproveita_o_cache(isolated_settings):
    builder = _builder_with_lut(isolated_settings, "teal-orange", 0.8)
    images = {"s1": "uploads/a.jpg", "s2": "uploads/a.jpg", "s3": None, "s4": "uploads/clip.mp4"}
    out = builder.lut_images(images)
    assert out["s1"] == out["s2"] and out["s1"].startswith("cache/lut/")
    assert out["s3"] is None and out["s4"] == "uploads/clip.mp4"  # vídeo e cena sem imagem ficam como estão
    assert images["s1"] == "uploads/a.jpg"  # a thumbnail continua com a original
    with Image.open(isolated_settings.data_dir / out["s1"]) as graded:
        assert graded.getpixel((10, 10)) != (180, 120, 60)
    assert builder.stats.generated.get("lut") == 1
    assert ("lut", out["s1"].split("/")[3]) in builder.store.touched  # entra no manifesto (limpeza do cache)

    again = _builder_with_lut(isolated_settings, "teal-orange", 0.8)
    assert again.lut_images(images) == out
    assert again.stats.reused.get("lut") == 1 and not again.stats.generated.get("lut")

    other = _builder_with_lut(isolated_settings, "teal-orange", 0.4)
    assert other.lut_images(images)["s1"] != out["s1"]  # outra intensidade, outra imagem


def test_build_sem_lut_ou_com_lut_que_sumiu(isolated_settings):
    from studio.workers.pipeline.build import Builder

    doc = service.create_project(title="Sem LUT", style_id="misterios-short", topic="teste")
    plain = Builder(doc.id)
    images = {"s1": "uploads/a.jpg"}
    assert plain.lut_images(images) is images
    missing = _builder_with_lut(isolated_settings, "apagado.cube")
    assert missing.lut_images(images) is images
    assert any("LUT de cor ignorado" in w for w in missing.warnings)


# --------------------------------------------------------------------------------------------
# Rotas da biblioteca
# --------------------------------------------------------------------------------------------


def test_rotas_de_lut(isolated_settings):
    from studio.api.app import create_app

    client = TestClient(create_app())
    listing = client.get("/api/luts").json()
    assert [b["id"] for b in listing["builtin"]] == list(LUT_PRESETS) and listing["user"] == []

    cube = _cube_from(lambda c: 1 - c, size=3, title="Negativo")
    sent = client.post("/api/luts", files={"file": ("../Negativo.cube", cube.encode(), "text/plain")})
    assert sent.status_code == 200, sent.text
    assert sent.json()["name"] == "Negativo.cube" and sent.json()["size"] == 3
    assert (isolated_settings.library_dir / "luts" / "Negativo.cube").is_file()
    assert client.get("/api/luts").json()["user"][0]["title"] == "Negativo"

    bad = client.post("/api/luts", files={"file": ("ruim.cube", b"LUT_3D_SIZE 2\n0 0 0\n", "text/plain")})
    assert bad.status_code == 422 and "esperava 8" in bad.json()["detail"]

    swatch = client.get("/api/luts/Negativo.cube/swatch.png")
    assert swatch.status_code == 200 and swatch.content.startswith(b"\x89PNG")
    with Image.open(io.BytesIO(swatch.content)) as img:
        assert img.size == (256, 96)
    exported = client.get("/api/luts/faded-film/file.cube")
    assert exported.status_code == 200 and parse_cube(exported.text).size == lut_mod.BUILTIN_SIZE
    assert client.get("/api/luts/nao-existe/swatch.png").status_code == 404

    assert client.delete("/api/luts/Negativo.cube").json() == {"deleted": "Negativo.cube"}
    assert client.delete("/api/luts/Negativo.cube").status_code == 404


def test_previa_com_lut_aponta_a_timeline_para_as_imagens_gradadas(isolated_settings):
    import json

    from studio.core.styles.library import get_style, save_user_style
    from studio.workers.jobs import get_job, get_runner

    base = get_style("misterios-short")
    effects = [*base.edit.effects, EffectSpec(type="lut", preset="cold-night", amount=0.7)]
    style = base.model_copy(update={"id": "misterios-lut", "name": "Mistérios com LUT",
                                    "edit": base.edit.model_copy(update={"effects": effects})})
    save_user_style(style)
    doc = service.create_project(title="LUT", style_id="misterios-lut", topic="o farol abandonado da ilha")
    final = None
    for kind in ("script", "storyboard", "preview"):
        job = get_runner().submit(kind, {}, doc.id, run_inline=True)
        final = get_job(job["id"])
        assert final is not None and final["status"] == "done", final and final["error"]
    assert final is not None
    entry = final["result"]["langs"]["pt-BR"]
    timeline = json.loads((isolated_settings.data_dir / entry["timeline"]).read_text(encoding="utf-8"))
    images = [s["src"] for s in timeline["scenes"] if (s.get("src") or "").lower().endswith(lut_mod.IMAGE_EXTS)]
    assert images and all(src.startswith("cache/lut/") for src in images)
    assert any(e["type"] == "lut" for e in timeline["style"]["effects"])
    assert final["result"]["generated"].get("lut", 0) >= 1
