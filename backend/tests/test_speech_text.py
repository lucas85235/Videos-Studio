"""Texto falado para a voz local (números, datas, siglas, símbolos e estrangeirismos), dicionário de pronúncia do
canal e glossário de tradução por canal."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from studio.api.app import create_app
from studio.config import get_settings, set_settings
from studio.core.glossary import GlossaryEntry, check_pairs, contains, glossary_of, glossary_prompt
from studio.core.numbers import number_words, spoken_number_runs
from studio.core.project import service
from studio.core.project.document import Scene, Segment, Visual
from studio.core.speech import PronunciationEntry, pronunciation_of, script_words, speech_plan
from studio.core.text import words
from studio.db import session_scope
from studio.media.ffmpeg import write_wav
from studio.models import Channel
from studio.providers import registry
from studio.providers.base import ProviderInfo, TTSProvider, TTSResult, Usage, Word
from studio.workers.jobs import get_job, get_runner
from studio.workers.pipeline import authoring
from studio.workers.pipeline.build import Builder

# --- números ---------------------------------------------------------------------------------


def _spoken(text: str, lang: str = "pt-BR") -> list[tuple[list[str], str]]:
    tokens = words(text)
    return [(tokens[a:b], spoken) for a, b, spoken in spoken_number_runs(tokens, lang)]


def test_numbers_with_currency_scale_percent_and_units():
    assert _spoken("Em 2023, R$ 2 milhões sumiram.") == [
        (["2023,"], "dois mil e vinte e três,"), (["R$", "2", "milhões"], "dois milhões de reais"),
    ]
    assert _spoken("Cerca de 30% das 2 mil pessoas") == [(["30%"], "trinta por cento"), (["2", "mil"], "duas mil")]
    assert _spoken("Foram 24h, 3x mais, o 1º lugar e a 2ª vez") == [
        (["24h,"], "vinte e quatro horas,"), (["3x"], "três vezes"), (["1º"], "primeiro"), (["2ª"], "segunda"),
    ]
    assert _spoken("Custou R$ 1,5 bilhão e US$ 1.") == [
        (["R$", "1,5", "bilhão"], "um bilhão e quinhentos milhões de reais"), (["US$", "1."], "um dólar."),
    ]
    assert _spoken("Lucro de 475mi, 1.000.000 e -5") == [
        (["475mi,"], "quatrocentos e setenta e cinco milhões,"), (["1.000.000"], "um milhão"), (["-5"], "menos cinco"),
    ]
    # algarismo depois de nome também é falado (na legenda ele continua "GTA 6")
    assert _spoken("O GTA 6 sai em 2026") == [(["6"], "seis"), (["2026"], "dois mil e vinte e seis")]
    # número já por extenso fica como está
    assert _spoken("dois mil e treze") == []


def test_numbers_in_english_and_spanish():
    assert _spoken("In 2013, $5 billion and 3rd place", "en-US") == [
        (["2013,"], "twenty thirteen,"), (["$5", "billion"], "five billion dollars"), (["3rd"], "third"),
    ]
    assert speech_plan("Son 30% y 2,5 kg", "es-ES", auto=True).spoken == "Son treinta por ciento y dos punto cinco kilos"
    assert speech_plan("Ganhou 2 mil reais em 100 km; anos 90", "pt-BR", auto=True).spoken == (
        "Ganhou dois mil reais em cem quilômetros; anos noventa")
    # idioma sem tabela de unidades: o número e o % solto ainda viram palavras
    assert speech_plan("Le prix monte de 30 %", "fr-FR", auto=True).spoken == "Le prix monte de trente pour cent"
    assert number_words(21, "pt-BR", ordinal=True, feminine=True) == "vigésima primeira"


# --- texto falado ----------------------------------------------------------------------------


def test_local_voice_text_spells_dates_acronyms_symbols_and_foreign_words():
    plan = speech_plan("Em 15/11/2026, o FBI e a NASA gastaram R$ 2 milhões no YouTube.", "pt-BR", auto=True)
    assert plan.spoken == ("Em quinze de novembro de dois mil e vinte e seis, o éfe bê í e a NASA gastaram dois "
                           "milhões de reais no iutúbi.")
    plan = speech_plan("Às 14h30 fazia 30°C & o PIB caiu 2,5%. Siga @vulto.", "pt-BR", auto=True)
    assert plan.spoken == ("Às catorze e trinta fazia trinta graus Celsius e o PIB caiu dois vírgula cinco por "
                           "cento. Siga arroba vulto.")
    # siglas: legíveis ficam (ONU, PIB), as outras são soletradas (EUA, IBGE); a pontuação continua no lugar
    assert speech_plan("A ONU e os EUA (IBGE).", "pt-BR", auto=True).spoken == "A ONU e os é ú á (í bê gê é)."
    # algarismo romano não é sigla; unidade solta concorda com o número
    assert speech_plan("Dom Pedro II andou 1 km e 3 km.", "pt-BR", auto=True).spoken == (
        "Dom Pedro II andou um quilômetro e três quilômetros.")
    # texto todo em maiúsculas (título gritado) não vira soletração
    assert not speech_plan("TUDO EM MAIÚSCULAS COM FBI", "pt-BR", auto=True).changed
    # em inglês, datas no formato do idioma e sigla como está
    plan = speech_plan("On 11/15/2026 at 9:05, the FBI paid $5 billion.", "en-US", auto=True)
    assert plan.spoken == "On November fifteenth, twenty twenty six at nine oh five, the FBI paid five billion dollars."
    # sem nada para trocar, o texto é o mesmo (as gravações antigas continuam valendo)
    plan = speech_plan("Um texto curto para narrar.", "pt-BR", auto=True)
    assert not plan.changed and plan.spoken == "Um texto curto para narrar." and plan.groups == []


def test_channel_dictionary_wins_and_applies_to_every_voice():
    entries = [
        PronunciationEntry(term="Nvidia", say="Ênvidia"),
        PronunciationEntry(term="GTA 6", say="gê tê á seis", lang="pt"),
        PronunciationEntry(term="YouTube", say="iú-tiúbe"),  # troca a pronúncia pronta
        PronunciationEntry(term="Vulto", say="Vúlto", lang="en-US"),  # outro idioma: não vale aqui
        PronunciationEntry(term="PIB", say="pê í bê"),  # sigla: só casa em maiúsculas
    ]
    plan = speech_plan("A Nvidia, o GTA 6 e o YouTube do Vulto; pib e PIB.", "pt-BR", entries, auto=True)
    assert plan.spoken == "A Ênvidia, o gê tê á seis e o iú-tiúbe do Vulto; pib e pê í bê."
    # voz de API: só o dicionário (números e estrangeirismos ficam para a própria voz)
    plan = speech_plan("A nvidia vendeu 3 GPUs no YouTube.", "pt-BR", entries, auto=False)
    assert plan.spoken == "A Ênvidia vendeu 3 GPUs no iú-tiúbe."
    assert pronunciation_of({"pronunciation": [{"term": "x", "say": "xis"}, {"term": "", "say": "y"}]}) == [
        PronunciationEntry(term="x", say="xis")]


def test_script_words_keep_the_script_tokens_with_spoken_times():
    text = "Em 2025, o FBI gastou R$ 2 milhões & mais."
    plan = speech_plan(text, "pt-BR", auto=True)
    spoken = words(plan.spoken)
    timed = [Word(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate(spoken)]
    mapped = script_words(text, timed, plan.groups)
    assert [w.text for w in mapped] == words(text)
    by_text = {w.text: w for w in mapped}
    # "2025," ocupa o tempo das seis palavras faladas ("dois mil e vinte e cinco,")
    assert by_text["2025,"].start == pytest.approx(0.5) and by_text["2025,"].end == pytest.approx(3.4)
    # as três palavras do roteiro dividem o tempo das quatro faladas, em ordem
    trio = mapped[5:8]
    assert [w.text for w in trio] == ["R$", "2", "milhões"]
    assert trio[0].start == timed[12].start and trio[-1].end == pytest.approx(timed[15].end)
    assert all(a.end <= b.start + 1e-9 for a, b in zip(mapped, mapped[1:], strict=False))


# --- build -----------------------------------------------------------------------------------


class SpokenTTS(TTSProvider):
    """Voz de teste que guarda o texto recebido e devolve uma palavra por token falado."""

    def __init__(self, provider_id: str, *, local: bool) -> None:
        self.info = ProviderInfo(id=provider_id, kind="tts", name=provider_id, commercial_use=True, local=local,
                                 deterministic=True, spoken_text=local)
        self.texts: list[str] = []

    def synthesize(self, *, text, lang, voice, style_prompt, speed, out_dir):
        self.texts.append(text)
        tokens = words(text)
        path = write_wav(Path(out_dir) / "voice.wav", np.zeros(24000 * max(1, len(tokens)) // 4, dtype=np.float32))
        timed = [Word(t, i * 0.25, i * 0.25 + 0.2) for i, t in enumerate(tokens)]
        return TTSResult(path=path, duration_s=len(tokens) * 0.25, words=timed, usage=Usage(units=len(text)))


@pytest.fixture
def voices(monkeypatch):
    set_settings(get_settings().model_copy(update={"offline": False}))
    api, local = SpokenTTS("api_tts", local=False), SpokenTTS("kokoro_local", local=True)
    for tts in (api, local):
        monkeypatch.setitem(registry._PROVIDERS, tts.info.id, tts)
    return api, local


def _channel(settings: dict) -> str:
    with session_scope() as s:
        s.add(Channel(id="chn_fala", name="Canal", settings=settings))
    return "chn_fala"


def _project(text: str, channel_id: str | None = None, langs: list[str] | None = None) -> str:
    doc = service.create_project(title="Fala", style_id="misterios-short", channel_id=channel_id, languages=langs)
    doc = doc.model_copy(update={
        "segments": [Segment(id="seg_1", text={"pt-BR": text})],
        "scenes": [Scene(id="scn_1", segment_id="seg_1", visual=Visual(prompt="x"))],
        "style_overrides": {"voice": {"chain": ["api_tts"], "voice": "Kore", "qa": {"enabled": False}}},
    })
    service.commit(doc.id, doc, "teste")
    return doc.id


def test_local_preview_speaks_numbers_but_captions_keep_the_script(voices):
    api, local = voices
    text = "Em 2025, o FBI pagou R$ 2 milhões ao Vulto."
    project_id = _project(text, _channel({"pronunciation": [{"term": "Vulto", "say": "Vúlto"}]}))

    preview = Builder(project_id, mode="preview")
    h, out = preview.narration("seg_1", "pt-BR")
    assert local.texts == ["Em dois mil e vinte e cinco, o éfe bê í pagou dois milhões de reais ao Vúlto."]
    assert out["spoken"] == local.texts[0] and out["spoken_map"]
    captions = preview.alignment("seg_1", "pt-BR", h, out)
    assert [w.text for w in captions] == words(text)  # legenda com "2025", "FBI" e "R$ 2 milhões"
    assert captions[1].text == "2025," and captions[1].end - captions[1].start > 1.0

    # voz final (API): só o dicionário do canal; números e siglas vão como estão
    final = Builder(project_id, mode="final")
    _, out = final.narration("seg_1", "pt-BR")
    assert api.texts == ["Em 2025, o FBI pagou R$ 2 milhões ao Vúlto."]
    assert [w.text for w in final.alignment("seg_1", "pt-BR", *final.narration("seg_1", "pt-BR"))] == words(text)
    assert len(api.texts) == 1  # a segunda chamada veio do cache


def test_text_without_changes_keeps_the_old_cache_key(voices):
    _, local = voices
    project_id = _project("Um texto curto para narrar.")
    inputs = Builder(project_id, mode="preview").narration_inputs("seg_1", "pt-BR")
    assert "spoken" not in inputs
    _, out = Builder(project_id, mode="preview").narration("seg_1", "pt-BR")
    assert "spoken" not in out and local.texts == ["Um texto curto para narrar."]


def test_editing_the_dictionary_records_again_only_what_changed(voices):
    api, _ = voices
    channel_id = _channel({"pronunciation": []})
    project_id = _project("O Vulto chegou.", channel_id)
    Builder(project_id, mode="final").narration("seg_1", "pt-BR")
    with session_scope() as s:
        s.get(Channel, channel_id).settings = {"pronunciation": [{"term": "Vulto", "say": "Vúlto"}]}
    Builder(project_id, mode="final").narration("seg_1", "pt-BR")
    assert api.texts == ["O Vulto chegou.", "O Vúlto chegou."]


# --- canais ----------------------------------------------------------------------------------


def test_channel_saves_pronunciation_and_glossary():
    with TestClient(create_app()) as client:
        created = client.post("/api/channels", json={
            "name": "Canal", "languages": ["pt-BR", "en-US"],
            "pronunciation": [{"term": " Nvidia ", "say": "Ênvidia", "lang": "pt-BR"}],
            "glossary": [{"term": "Vulto", "keep": True}, {"term": "canal dark", "keep": False,
                                                            "translations": {"en-US": "faceless channel", "es-ES": " "}}],
        }).json()
        assert created["pronunciation"] == [{"term": "Nvidia", "say": "Ênvidia", "lang": "pt-BR"}]
        assert created["glossary"][1]["translations"] == {"en-US": "faceless channel"}
        # a tela antiga (sem os campos) não apaga o que já existe
        updated = client.put(f"/api/channels/{created['id']}", json={"name": "Canal 2"}).json()
        assert updated["pronunciation"] == created["pronunciation"] and len(updated["glossary"]) == 2
        cleared = client.put(f"/api/channels/{created['id']}", json={"name": "Canal 2", "pronunciation": []}).json()
        assert cleared["pronunciation"] == [] and len(cleared["glossary"]) == 2
        bad = client.put(f"/api/channels/{created['id']}", json={"name": "x", "pronunciation": [{"term": "", "say": "a"}]})
        assert bad.status_code == 422


# --- glossário -------------------------------------------------------------------------------


def test_glossary_rules_prompt_and_check():
    entries = [GlossaryEntry(term="Vulto"), GlossaryEntry(term="canal dark", keep=False,
                                                          translations={"en-US": "faceless channel"}),
               GlossaryEntry(term="spoiler", keep=False)]
    assert entries[0].expected("en-US") == "Vulto" and entries[1].expected("en-GB") == "faceless channel"
    assert entries[1].expected("es-ES") is None and entries[2].expected("en-US") is None
    assert contains("Bem-vindo ao Canal Dark!", "canal dark") and not contains("Vultos", "Vulto")
    prompt = glossary_prompt(entries, ["O Vulto é um canal dark."], "en-US")
    assert '"Vulto": não traduza, mantenha "Vulto"' in prompt
    assert '"canal dark": traduza sempre como "faceless channel"' in prompt and "spoiler" not in prompt
    assert glossary_prompt(entries, ["Nada aqui."], "en-US") == ""
    issues = check_pairs([("segment", "seg_1", "O Vulto é um canal dark.", "The Shadow is a dark channel.")],
                         entries, "en-US")
    assert [(i["term"], i["expected"]) for i in issues] == [("Vulto", "Vulto"), ("canal dark", "faceless channel")]
    assert glossary_of({"glossary": [{"term": "ok"}, {"keep": True}]}) == [GlossaryEntry(term="ok")]


def _run(kind: str, project_id: str, payload: dict | None = None) -> dict:
    job = get_runner().submit(kind, payload or {}, project_id, run_inline=True)
    final = get_job(job["id"])
    assert final is not None and final["status"] == "done", final and final["error"]
    return final["result"]


def test_translation_uses_the_glossary_and_warns_when_it_was_not_followed(monkeypatch):
    channel_id = _channel({"glossary": [{"term": "Vulto", "keep": True},
                                        {"term": "canal dark", "keep": False, "translations": {"en-US": "faceless channel"}}]})
    project_id = _project("O Vulto é um canal dark.", channel_id, langs=["pt-BR", "en-US"])
    prompts: list[str] = []
    real_llm = authoring._llm

    def spy(task, system, prompt, schema, context, project_id, job_id):
        prompts.append(prompt)
        return real_llm(task, system, prompt, schema, context, project_id, job_id)

    monkeypatch.setattr(authoring, "_llm", spy)
    result = _run("translate", project_id)
    assert "Glossário do canal" in prompts[0] and '"faceless channel"' in prompts[0]
    # a tradução de teste só põe "[en-US]" na frente: "Vulto" ficou, "canal dark" não virou "faceless channel"
    assert result["glossary_warnings"] == ['en-US · seg_1: "canal dark" devia aparecer como "faceless channel"']
    with TestClient(create_app()) as client:
        detail = client.get(f"/api/projects/{project_id}").json()
        assert [(w["lang"], w["id"], w["term"]) for w in detail["glossary_warnings"]] == [("en-US", "seg_1", "canal dark")]
        # corrigido à mão, o aviso some
        doc = service.get_doc(project_id)
        doc.segments[0].text["en-US"] = "Vulto is a faceless channel."
        service.commit(project_id, doc, "correção")
        assert client.get(f"/api/projects/{project_id}").json()["glossary_warnings"] == []
