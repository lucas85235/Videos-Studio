"""Estilos e padrões genéricos para canal sem rosto: nichos variados, padrão neutro e climas leves na trilha."""

from __future__ import annotations

import random

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.core.project import service
from studio.core.styles.library import DEFAULT_STYLE_ID, PRESET_ORDER, load_presets
from studio.core.styles.schema import ArtSpec, MusicSpec
from studio.media import audio
from studio.media.ffmpeg import read_wav
from studio.workers.jobs import get_job, get_runner

# Estilos que projetos já usam ou podem usar: os ids não podem sumir.
LEGACY_IDS = {
    "misterios-short",
    "documentario-sombrio",
    "terror-analog",
    "true-crime",
    "top10-listas",
    "estoico-short",
}
NEW_IDS = {
    "curiosidades-short",
    "curiosidades-longo",
    "historia-documental",
    "financas-negocios",
    "tecnologia-ia",
    "historias-narradas",
    "biografias",
    "explicador-vetorial",
}
DARK_GRADES = {"cold-teal-dark", "noir", "vhs-faded", "blood-red", "night-green", "bleach-bypass"}
DARK_MOODS = {"drone", "horror", "tension", "mystery"}


def _grade(style) -> str | None:
    return next((e.preset for e in style.edit.effects if e.type == "grade"), None)


def test_presets_cover_many_niches_and_keep_old_ids():
    presets = load_presets()
    assert LEGACY_IDS | NEW_IDS <= set(presets)
    # a lista de ordem não pode citar preset que não existe
    assert set(PRESET_ORDER) <= set(presets)
    light = [
        s
        for s in presets.values()
        if _grade(s) not in DARK_GRADES and audio.mood_key(s.audio.music.mood) not in DARK_MOODS
    ]
    assert len(light) >= 8
    assert {s.format.type for s in light} == {"short", "long"}


def test_default_style_is_first_and_neutral():
    presets = load_presets()
    assert next(iter(presets)) == DEFAULT_STYLE_ID
    style = presets[DEFAULT_STYLE_ID]
    assert _grade(style) not in DARK_GRADES
    assert audio.mood_key(style.audio.music.mood) not in DARK_MOODS
    assert "sombri" not in style.art.bible and "escur" not in style.art.bible


def test_light_styles_do_not_depend_on_dark_background():
    presets = load_presets()
    for style_id in NEW_IDS | {"top10-listas", "estoico-short"}:
        bible = presets[style_id].art.bible
        assert "fundo escuro" not in bible and "sombri" not in bible, style_id


def test_schema_defaults_are_neutral():
    assert audio.mood_key(MusicSpec().mood) == "neutral"
    assert "sombri" not in ArtSpec().bible


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("dark ambient drone", "drone"),
        ("trilha sombria", "drone"),
        ("terror analógico", "horror"),
        ("tension", "tension"),
        ("calm", "calm"),
        ("lo-fi para estudar", "lofi"),
        ("lofi/faixa.mp3", "lofi"),
        ("animado", "upbeat"),
        ("inspirador", "inspiring"),
        ("motivacional", "inspiring"),
        ("corporativo leve", "corporate"),
        ("curioso / divertido", "playful"),
        ("neutral", "neutral"),
        ("qualquer coisa", "neutral"),
        (None, "neutral"),
    ],
)
def test_mood_key(text, key):
    assert audio.mood_key(text) == key


def test_every_mood_key_is_listed():
    assert set(audio.MOODS) == {key for key, _ in audio._MOOD_RULES} | {"neutral"}


@pytest.mark.parametrize("mood", ["neutral", "upbeat", "inspiring", "corporate", "lofi", "playful"])
def test_light_moods_render(tmp_path, mood):
    out = audio.generate_music(mood, 9.0, 42, tmp_path / f"{mood}.wav")
    samples, rate = read_wav(out)
    assert samples.size / rate == pytest.approx(9.0, abs=0.8)
    rms = float(np.sqrt(np.mean(samples**2)))
    assert 0.05 < rms < 0.5
    assert not np.isnan(samples).any()


def test_light_moods_vary_with_seed():
    a = audio._light_track("upbeat", 49.0, random.Random(1))
    b = audio._light_track("upbeat", 49.0, random.Random(2))
    assert a.loop != b.loop


def test_create_project_without_style_uses_default_or_channel_style():
    with TestClient(create_app()) as client:
        plain = client.post("/api/projects", json={"title": "Sem estilo", "topic": "por que o céu é azul"})
        assert plain.status_code == 200, plain.text
        assert plain.json()["doc"]["style_id"] == DEFAULT_STYLE_ID

        channel = client.post(
            "/api/channels", json={"name": "Canal de história", "default_style_id": "historia-documental"}
        ).json()
        with_channel = client.post("/api/projects", json={"title": "Roma", "channel_id": channel["id"]})
        assert with_channel.status_code == 200, with_channel.text
        assert with_channel.json()["doc"]["style_id"] == "historia-documental"


@pytest.mark.parametrize(
    ("style_id", "mood"), [("curiosidades-short", "playful"), ("financas-negocios", "corporate")]
)
def test_new_style_preview_offline(isolated_settings, style_id, mood):
    doc = service.create_project(title="Teste", style_id=style_id, topic="por que o céu é azul")
    for kind in ("script", "storyboard", "preview"):
        job = get_runner().submit(kind, {}, doc.id, run_inline=True)
        final = get_job(job["id"])
        assert final is not None and final["status"] == "done", final and final["error"]
    preview = final["result"]
    assert preview["music"] == {"source": "procedural", "track": None, "mood": mood}
    assert (isolated_settings.data_dir / preview["langs"]["pt-BR"]["mix"]).exists()
