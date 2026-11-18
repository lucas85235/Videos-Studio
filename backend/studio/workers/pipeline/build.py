"""Build do vídeo: executa o grafo de artefatos e regenera só o que mudou (README §6.3).

Etapas:
  A. narração (TTS) por segmento/idioma e imagens por cena  → caros, em paralelo
  B. alinhamento por palavra, timeline, trilha, SFX e mix     → locais
  C. render dos blocos (Remotion)                             → só no modo "final"
  D. montagem final, legendas SRT e thumbnail                 → só no modo "final"

Cada artefato tem hash das entradas; se já existe no cache, é reaproveitado.
"""

from __future__ import annotations

import json
import logging
import shutil
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any, Literal

from PIL import Image

from studio.config import get_settings
from studio.core.graph.store import ArtifactStore
from studio.core.hashing import hash_file, hash_obj, seed_from
from studio.core.project import characters as characters_mod
from studio.core.project import service
from studio.core.project.document import ArtDirection, Character, Presenter, ProjectDoc, Scene, Visual
from studio.core.project.media_edit import scene_label
from studio.core.speech import (
    SPEECH_VERSION,
    PronunciationEntry,
    SpeechPlan,
    pronunciation_of,
    script_words,
    speech_plan,
)
from studio.core.styles.library import get_style
from studio.core.styles.schema import StyleSpec, grammar_for
from studio.core.styles.visual import composition_text, language_prefix, rotate_variant, shot_text
from studio.core.timeline import (
    PARALLAX,
    NarrationClip,
    build_timeline,
    chunk_timeline,
    normalize_words,
    scene_motion,
)
from studio.db import session_scope
from studio.media import audio as audio_mod
from studio.media import audio_check, music_library, sfx_library
from studio.media import depth as depth_mod
from studio.media import lut as lut_mod
from studio.media.ffmpeg import read_wav
from studio.media.real_media import EDITORIAL_WARNING, anchor_for, fit_media, resolve_fit
from studio.media.render import get_render_client, renderer_version
from studio.media.thumbnail import make_thumbnail
from studio.media.video import assemble
from studio.models import Channel
from studio.providers import kokoro, registry
from studio.providers.align import map_to_script, narration_problems, word_error_rate
from studio.providers.base import Word
from studio.workers.pipeline import dubbing, video_clips
from studio.workers.pipeline.lighting import names_light, strip_light

log = logging.getLogger(__name__)

Mode = Literal["preview", "final"]

_codex_lock = threading.Lock()
# Geradores por CLI do plano (Codex, Antigravity): um pedido por vez em cada um, cada um com a sua trava.
_CLI_IMAGE_LOCKS = {"codex_cli": _codex_lock, "antigravity_cli": threading.Lock()}
# Segundos por imagem na estimativa do plano de geração (os outros geradores: 12 s).
_IMAGE_SECONDS = {"codex_cli": 40, "antigravity_cli": 40}
# Tipos de cena que não desenham o VisualLayer (o documento é um cartão com a imagem parada): sem parallax.
NO_VISUAL_LAYER_KINDS = frozenset({"document"})
_depth_lock = threading.Lock()  # mesma imagem em duas cenas: um mapa e um jogo de camadas só
_media_lock = threading.Lock()  # duas cenas com a mesma mídia real não recortam nem gravam o mesmo cache ao mesmo tempo


class BuildCancelled(Exception):
    pass


class BuildError(RuntimeError):
    pass


@dataclass
class BuildStats:
    generated: dict[str, int] = field(default_factory=dict)
    reused: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    noncommercial: set[str] = field(default_factory=set)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def hit(self, kind: str, reused: bool, cost: float = 0.0) -> None:
        with self.lock:
            bucket = self.reused if reused else self.generated
            bucket[kind] = bucket.get(kind, 0) + 1
            self.cost_usd += cost


class ProgressSink:
    """Recebe progresso (0..1) e mensagens; o JobRunner implementa isso."""

    def progress(self, fraction: float, message: str = "") -> None:  # pragma: no cover - interface
        pass

    def cancelled(self) -> bool:  # pragma: no cover - interface
        return False

    @property
    def job_id(self) -> str | None:  # pragma: no cover - interface
        return None


# Bíblias dos estilos antes de tirar delas a hora do dia, o céu e a luz fixa (a imagem dirigida não usa a bíblia). A
# cena antiga (sem linguagem) continua com o prompt de antes e a mesma chave de cache: nada é gerado de novo.
_LEGACY_BIBLES: dict[str, str] = {
    "cores saturadas e neon, contraste limpo, sem grão":
        "cores saturadas de pôr do sol e neon, contraste limpo, luz de fim de tarde, sem grão",
    "pintura clássica a óleo, estátuas de mármore greco-romanas, templos e paisagens mediterrâneas, tons quentes, "
    "atmosfera serena e contemplativa":
        "pintura clássica a óleo, estátuas de mármore greco-romanas, templos e paisagens mediterrâneas, luz dourada do "
        "amanhecer, céu aberto e tons quentes, atmosfera serena e contemplativa",
    "pintura digital histórica realista e luminosa, cores naturais da época, luz natural suave, cenas amplas de "
    "cidades, mercados, paisagens e pessoas comuns, figurinos e arquitetura fiéis ao período":
        "pintura digital histórica realista e luminosa, cores naturais da época, luz do dia suave, cenas amplas de "
        "cidades, mercados, paisagens e pessoas comuns, figurinos e arquitetura fiéis ao período",
    "pintura editorial luminosa e realista, cenas de cada fase da vida e da época, lugares, objetos e paisagens "
    "marcantes, cores naturais, luz natural suave; a pessoa retratada aparece de costas, em silhueta ou à distância":
        "pintura editorial luminosa e realista, cenas de cada fase da vida e da época, lugares, objetos e paisagens "
        "marcantes, cores naturais, luz suave de janela; a pessoa retratada aparece de costas, em silhueta ou à distância",
    "pintura a óleo sombria e cinematográfica, paleta azul petróleo e dourado envelhecido, pouca luz, contraste "
    "dramático, grão de tela":
        "pintura a óleo sombria e cinematográfica, paleta azul petróleo e dourado envelhecido, luz de vela, contraste "
        "dramático, grão de tela",
    "fotografia granulada de câmera VHS, corredores vazios, luz fluorescente doentia, espaços liminares, cores "
    "lavadas, sem pessoas nítidas":
        "fotografia noturna granulada de câmera VHS, corredores vazios, luz fluorescente doentia, espaços liminares, "
        "cores lavadas, sem pessoas nítidas",
    "estética noir de arquivo policial, fotografias antigas em preto e branco, papel envelhecido, mesa de "
    "investigação, luz baixa e contrastada, sem rostos identificáveis":
        "estética noir de arquivo policial, fotografias antigas em preto e branco, papel envelhecido, mesa de "
        "investigação, luz de luminária, sem rostos identificáveis",
}  # fmt: skip


def compose_image_prompt(style: StyleSpec, prompt: str) -> str:
    banned = ", ".join(style.art.banned)
    bible = _LEGACY_BIBLES.get(style.art.bible, style.art.bible)
    return (
        f"{prompt.strip().rstrip('.')}. Estilo visual: {bible}. "
        f"Sem nenhum texto, letra, número, legenda ou marca d'água. Evitar: {banned}."
    )


ORIGINAL_ONLY = "Original designs only: no logos, no brand typography, no official characters, no game interface"


def directed_variant(art: ArtDirection, visual: Visual) -> tuple[str | None, str | None, str | None]:
    """Plano, composição e linguagem da imagem dirigida: "Outra imagem" (variation) gira os três."""
    return rotate_variant(visual.shot, visual.composition, visual.language, art.languages, visual.variation)


def compose_directed_prompt(style: StyleSpec, art: ArtDirection, visual: Visual) -> str:
    """Prompt em camadas da cena dirigida: enquadramento, linguagem, sujeito, entidades e o mundo do vídeo.

    A bible do estilo não entra (a direção já adaptou o tratamento ao assunto) e o "sem texto" vai no
    envelope do gerador de imagem. A paleta entra como cores de destaque, não como a cor da imagem toda. Quando o
    prompt da cena já diz a hora, o clima ou a luz, a hora, o clima e o céu saem do mundo, das aparências e da
    paleta, e a luz sai também do tratamento (direção antiga com "golden hour" não pinta todas as imagens iguais).
    A direção definida pelo usuário fica como ele escreveu.
    """
    shot, composition, language = directed_variant(art, visual)
    framing = ", ".join(t for t in (shot_text(shot), composition_text(composition)) if t)
    own_light = art.source != "user" and names_light(visual.prompt)

    def keep(text: str, *, light: bool = False) -> str:
        return strip_light(text, light=light) if own_light else text.strip()

    looks = [keep(e.look) for e in (art.entity(i) for i in visual.entities) if e and e.look.strip()]
    palette = [c for c in (keep(x) for x in art.palette if x.strip()) if c]
    world, treatment = keep(art.world), keep(art.treatment, light=True)
    avoid = list(dict.fromkeys(x.strip() for x in (*style.art.banned, *art.avoid) if x and x.strip()))
    parts = [
        framing[:1].upper() + framing[1:],
        language_prefix(language),
        visual.prompt.strip(),
        "; ".join(look for look in looks if look),
        f"World: {world}" if world else "",
        f"Accent colors: {', '.join(palette)}" if palette else "",
        f"Treatment: {treatment}" if treatment else "",
        ORIGINAL_ONLY,
        f"Avoid: {', '.join(avoid)}" if avoid else "",
    ]
    return ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip().rstrip(".")) + "."


def legacy_directed_prompt(style: StyleSpec, art: ArtDirection, visual: Visual) -> str:
    """Prompt da cena dirigida de antes ("Palette:", com a direção inteira, sem tirar a luz). Só serve para achar no
    cache a imagem já gerada com ele (ver `Builder.cached_image`): a prévia aprovada não muda na renderização e a
    cena antiga não é gerada de novo. Imagem nova (cena nova, "Outra imagem", prompt editado) usa o prompt novo."""
    shot, composition, language = directed_variant(art, visual)
    framing = ", ".join(t for t in (shot_text(shot), composition_text(composition)) if t)
    looks = [e.look.strip() for e in (art.entity(i) for i in visual.entities) if e and e.look.strip()]
    avoid = list(dict.fromkeys(x.strip() for x in (*style.art.banned, *art.avoid) if x and x.strip()))
    palette = [c.strip() for c in art.palette if c.strip()]
    parts = [framing[:1].upper() + framing[1:], language_prefix(language), visual.prompt.strip(), "; ".join(looks),
             f"World: {art.world.strip()}" if art.world.strip() else "",
             f"Palette: {', '.join(palette)}" if palette else "",
             f"Treatment: {art.treatment.strip()}" if art.treatment.strip() else "",
             ORIGINAL_ONLY, f"Avoid: {', '.join(avoid)}" if avoid else ""]
    return ". ".join(p.strip().rstrip(".") for p in parts if p and p.strip().rstrip(".")) + "."


# Cadeias de imagem de versões anteriores. A chave do cache das imagens incluía a cadeia de provedores, e trocar
# a cadeia (ex.: tirar os provedores pagos) escondia imagens já geradas, que eram geradas de novo gastando cota.
# A chave nova não depende da cadeia; estas só servem para achar o que foi guardado com a chave antiga.
_LEGACY_IMAGE_CHAINS: tuple[list[str], ...] = (
    ["codex_cli", "bfl_flux", "fal_image", "openai_image", "placeholder"],
)


def image_key(inputs: dict[str, Any]) -> str:
    """Chave da imagem no cache: o que define a imagem (prompt, tamanho, semente), não quem pode gerá-la."""
    return hash_obj({**{k: v for k, v in inputs.items() if k != "chain"}, "key": 2})


def scene_needs_image(scene: Scene) -> bool:
    """A cena gera imagem própria? (cena que reaproveita a imagem de outra não gera; ver `ProjectDoc.reuse_root`).
    Cena com mídia real mostra a mídia: o prompt fica de reserva e nada é gerado."""
    visual = scene.visual
    if visual.media is not None:
        return False
    if visual.reuse_of:
        return False
    if visual.type in ("none", "upload"):
        return False
    if scene.kind == "text" and not visual.prompt:
        return False
    if scene.kind == "map":
        return False  # o mapa ocupa a tela inteira: a imagem nunca apareceria (o prompt fica para quando voltar a imagem)
    return bool(visual.prompt or visual.query)


def scenes_without_direction(doc: ProjectDoc) -> list[str]:
    """Cenas dirigidas que perderam a direção de arte (o estilo foi trocado e as cenas não foram refeitas).

    Gerar essas imagens agora usaria o prompt antigo e jogaria a cota fora: elas seriam refeitas com as cenas.
    """
    if doc.art is not None:
        return []
    return [s.id for s in doc.ordered_scenes()
            if s.visual.language and scene_needs_image(s) and not s.visual.asset and s.visual.type != "stock"]


DIRECTION_MISSING = "O estilo do projeto mudou e as cenas ficaram sem direção de arte: refaça as cenas antes de gerar as imagens."


def branch_outputs_dir(outputs_dir: Path, project_id: str, branch: str | None) -> Path:
    """Pasta de saída do projeto (principal) ou de um ramo A/B (`ramos/<nome>`), sem o idioma."""
    base = outputs_dir / project_id
    return base / "ramos" / branch if branch and branch != service.MAIN_BRANCH else base


class Builder:
    def __init__(self, project_id: str, *, mode: Mode = "preview", langs: list[str] | None = None,
                 version: int | None = None, sink: ProgressSink | None = None, branch: str | None = None) -> None:
        self.project_id = project_id
        self.mode = mode
        self.version = version
        # Ramo A/B: a saída vai para outputs/<projeto>/ramos/<nome>/<idioma> e não pisa na da principal.
        self.branch = branch if branch and branch != service.MAIN_BRANCH else None
        self.doc: ProjectDoc = service.get_doc(project_id, version)
        self.style: StyleSpec = get_style(self.doc.style_id, self.doc.style_overrides)
        self.langs = langs or list(self.doc.languages)
        self.sink = sink or ProgressSink()
        self.store = ArtifactStore()
        self.settings = get_settings()
        self.stats = BuildStats()
        self.warnings: list[str] = []
        self.lut_graded: dict[str, str] = {}  # imagem original → versão com o LUT do estilo (lut_images)
        self.music_used: dict[str, Any] | None = None
        self._done = 0
        self._total = 1
        self._progress_lock = threading.Lock()
        self.multi_track = dubbing.is_multi_track(self.style, self.doc)
        if self.multi_track:
            # Modo B: a imagem é a do idioma principal e serve a todas as faixas. O principal sempre entra (é a
            # referência do tempo) e nada de legenda queimada: as legendas vão como faixas de closed caption.
            primary = self.doc.primary_language
            if primary not in self.langs:
                self.langs = [primary, *self.langs]
            self.style.edit.captions.style = "none"
        self._pronunciation: list[PronunciationEntry] | None = None

    # ---------------------------------------------------------------------------------------
    # utilidades
    # ---------------------------------------------------------------------------------------
    def _check(self) -> None:
        if self.sink.cancelled():
            raise BuildCancelled("cancelado pelo usuário")

    def _tick(self, message: str) -> None:
        with self._progress_lock:
            self._done += 1
            fraction = min(0.999, self._done / max(1, self._total))
        self.sink.progress(fraction, message)

    def _better_available(self, chain: list[str], used: str | None) -> bool:
        """Saída veio de um provedor de prévia, mas agora existe um melhor disponível na cadeia?"""
        if not used or used not in chain:
            return False
        provider = registry.get(used)
        if provider.info.commercial_use:
            return False
        for candidate in chain[: chain.index(used)]:
            ok, _ = registry.get(candidate).availability()
            if ok and not registry.quota_exhausted_today(candidate):
                return True
        return False

    def final_voice_only(self) -> bool:
        """No vídeo final, a narração só pode sair de voz que vai para o canal (nada de voz de teste)."""
        return self.mode == "final" and not self.settings.offline

    @staticmethod
    def final_chain(chain: list[str]) -> list[str]:
        return [p for p in chain if registry.get(p).info.commercial_use]

    def narrations_to_record(self) -> list[tuple[str, str, int]]:
        """Narrações que esta build ainda precisa gravar: (segmento, idioma, caracteres)."""
        missing = []
        final = self.final_voice_only()
        for lang in self.langs:
            for seg in self.doc.segments:
                inputs = self.narration_inputs(seg.id, lang)
                cached = self.store.lookup("tts", hash_obj(inputs))
                if not cached or (final and not cached.get("commercial", True)):
                    missing.append((seg.id, lang, len(inputs["text"])))
        return missing

    def check_final_voice(self) -> None:
        """Falha logo no início se o vídeo final precisa gravar narração e nenhuma voz final está disponível."""
        if not self.final_voice_only() or not self.narrations_to_record():
            return
        chain = self.final_chain(registry.resolve_chain("tts", self.style.voice.chain))
        reasons = []
        for provider_id in chain:
            provider = registry.get(provider_id)
            ok, reason = provider.availability()
            if ok and not registry.quota_exhausted_today(provider_id):
                return
            reasons.append(f"{provider.info.name}: {'cota esgotada hoje' if ok else reason}")
        detail = "; ".join(reasons) or "nenhuma voz final configurada"
        raise BuildError(
            f"A voz do vídeo final não está disponível ({detail}). Escolha outra voz final na aba Vídeo final ou "
            "tente de novo quando a cota voltar."
        )

    def _usage(self, result: Any) -> tuple[float, str, float]:
        return result.usage.units, result.usage.unit_name, result.usage.cost_usd

    def outputs_dir(self, lang: str) -> Path:
        path = branch_outputs_dir(self.settings.outputs_dir, self.project_id, self.branch) / lang
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------------------------------------------------------------------------------------
    # nós
    # ---------------------------------------------------------------------------------------
    def local_preview(self, lang: str) -> bool:
        """A narração desta build sai da voz local grátis (prévia) em vez da voz final?"""
        return (self.mode == "preview" and self.style.voice.preview.local and not self.settings.offline
                and kokoro.supports(lang))

    def _final_gender(self, voice: str | None, lang: str) -> str | None:
        if not voice:
            return None
        for provider_id in self.style.voice.chain[:1]:
            try:
                provider = registry.get(provider_id)
            except Exception:  # noqa: BLE001
                return None
            for option in getattr(provider, "voices", lambda _lang: [])(lang):
                if option.id == voice:
                    return option.gender
        return None

    def preview_voice(self, lang: str, speaker_voice: str | None = None) -> str | None:
        """Voz local do narrador (a escolhida ou uma do mesmo gênero da voz final). Personagens com voz própria
        ganham outra voz local, do gênero da voz final deles."""
        narrator = kokoro.pick_voice(lang, self.style.voice.preview.voice,
                                     self._final_gender(self.style.voice.voice, lang))
        if not speaker_voice or speaker_voice == self.style.voice.voice:
            return narrator
        return kokoro.pick_voice(lang, None, self._final_gender(speaker_voice, lang), avoid=narrator)

    @property
    def pronunciation(self) -> list[PronunciationEntry]:
        """Dicionário de pronúncia do canal do projeto (vazio sem canal)."""
        if getattr(self, "_pronunciation", None) is None:
            entries: list[PronunciationEntry] = []
            if self.doc.channel_id:
                with session_scope() as s:
                    row = s.get(Channel, self.doc.channel_id)
                    entries = pronunciation_of(row.settings) if row is not None else []
            self._pronunciation = entries
        return self._pronunciation

    def speech(self, text: str, lang: str, *, local: bool) -> SpeechPlan:
        """Texto falado: o dicionário do canal sempre; nas vozes locais, também números, datas, siglas e
        estrangeirismos por extenso (`core.speech`). A legenda continua com `text`."""
        return speech_plan(text, lang, self.pronunciation, auto=local)

    def narration_inputs(self, seg_id: str, lang: str) -> dict[str, Any]:
        seg = self.doc.segment(seg_id)
        text = seg.text_for(lang)
        speaker = self.style.script.speakers.get(seg.speaker)
        voice = (speaker.voice if speaker and speaker.voice else None) or self.style.voice.voice
        style_prompt = (speaker.style_prompt if speaker and speaker.style_prompt else None) or self.style.voice.style_prompt
        if self.local_preview(lang):
            # Voz local: sem instrução de estilo (não há o que interpretar nem o que vazar no áudio).
            inputs = {
                "text": text,
                "lang": lang,
                "voice": self.preview_voice(lang, voice),
                "style_prompt": None,
                "speed": self.style.voice.speed,
                "chain": registry.resolve_chain("tts", registry.PREVIEW_TTS_CHAIN),
                "v": 2,
            }
            plan = self.speech(text, lang, local=True)
        else:
            inputs = {
                "text": text,
                "lang": lang,
                "voice": voice,
                "style_prompt": style_prompt,
                "speed": self.style.voice.speed,
                "chain": registry.resolve_chain("tts", self.style.voice.chain),
                # v2: novo pedido ao Gemini TTS e conferência de cada narração. As gravações antigas podiam ter a
                # instrução de estilo lida em voz alta, então não são reaproveitadas.
                "v": 2,
            }
            plan = self.speech(text, lang, local=False)
            if "elevenlabs" in inputs["chain"]:
                # Modelo da ElevenLabs na chave: trocar o modelo grava de novo só as narrações das cadeias com ela.
                from studio.providers.tts import ELEVEN_MODEL

                inputs["eleven_model"] = ELEVEN_MODEL
        if plan.changed:
            # Só quando o texto falado muda: as gravações de antes continuam valendo para os textos sem troca.
            inputs["spoken"] = plan.spoken
            inputs["speech_v"] = SPEECH_VERSION
        return inputs

    def narration(self, seg_id: str, lang: str) -> tuple[str, dict[str, Any]]:
        inputs = self.narration_inputs(seg_id, lang)
        if not inputs["text"].strip():
            raise BuildError(f"o segmento {seg_id} não tem texto em {lang} (traduza antes)")
        h = hash_obj(inputs)
        cached = self.store.lookup("tts", h)
        final = self.final_voice_only()
        if cached and final and not cached.get("commercial", True):
            cached = None  # gravação com voz de teste (ex.: cota esgotada na prévia) não vai para o vídeo final
        if cached and self._stale_local_speech(cached, inputs["text"], lang):
            cached = None
        if cached and not self._better_available(inputs["chain"], cached.get("provider")):
            self.stats.hit("tts", True)
            if not cached.get("commercial", True):
                self.stats.noncommercial.add(cached.get("provider", "?"))
            problems = self._recheck_wer(cached, inputs["text"], lang, cached.get("spoken"))
            if problems:
                self.warnings.append(
                    f"Narração de {seg_id} ({lang}) com problema: {'; '.join(problems)}. Ouça esse trecho antes "
                    "de publicar."
                )
            return h, cached
        out_dir = self.store.dir_for("tts", h)
        qa_spec = self.style.voice.qa
        wpm = self.style.script.words_per_minute.get(lang, 150)

        # O hash usa a cadeia completa (as gravações já feitas continuam valendo); no vídeo final, só as vozes que
        # podem ir para o canal entram: sem elas, a build falha antes (ver `check_final_voice`) em vez de cair na
        # voz do Windows.
        chain = self.final_chain(inputs["chain"]) if final else inputs["chain"]

        # Texto falado de cada tipo de voz: as locais recebem números, siglas etc. por extenso.
        plans = {local: self.speech(inputs["text"], lang, local=local) for local in (False, True)}

        def plan_for(provider: Any) -> SpeechPlan:
            return plans[bool(getattr(provider.info, "spoken_text", False))]

        def take(target: Path, style_prompt: str | None) -> dict[str, Any]:
            result, provider, _ = registry.run_chain(
                "tts",
                chain,
                lambda p: p.synthesize(text=plan_for(p).spoken, lang=lang, voice=inputs["voice"],
                                       style_prompt=style_prompt, speed=inputs["speed"], out_dir=target),
                usage_of=self._usage,
                project_id=self.project_id,
                job_id=self.sink.job_id,
            )
            plan = plan_for(provider)
            qa = self._qa(inputs["text"], lang, result.path, plan.spoken if plan.changed else None)
            heard = [w["text"] for w in qa["heard"]] if qa else None
            problems = narration_problems(qa["ref"] if qa else inputs["text"], heard, result.duration_s,
                                          style_prompt=inputs["style_prompt"], words_per_minute=wpm,
                                          speed=inputs["speed"] or 1.0)
            if qa and qa["wer"] > qa_spec.max_wer and not problems:
                problems.append(f"{qa['wer']:.0%} das palavras diferem do roteiro")
            return {"result": result, "provider": provider, "qa": qa, "problems": problems, "plan": plan}

        def rank(t: dict[str, Any]) -> tuple[bool, float]:
            return bool(t["problems"]), t["qa"]["wer"] if t["qa"] else 0.0

        best = take(out_dir, inputs["style_prompt"])
        attempts = 1
        # Voz que falou fora do roteiro é refeita. A última tentativa vai sem a instrução de estilo: é o único
        # pedido em que não há instrução para ler em voz alta.
        retries = max(qa_spec.retries, 2)
        while best["problems"] and not best["provider"].info.deterministic and attempts <= retries:
            self._check()
            last = attempts == retries
            style_prompt = None if last and inputs["style_prompt"] else inputs["style_prompt"]
            log.warning("narração de %s (%s) com problema (%s); tentativa %d", seg_id, lang,
                        "; ".join(best["problems"]), attempts + 1)
            again = take(out_dir / f"tentativa{attempts + 1}", style_prompt)
            attempts += 1
            if rank(again) < rank(best):
                best = again
        result, provider, qa, problems = best["result"], best["provider"], best["qa"], best["problems"]
        plan = best["plan"]
        if problems:
            self.warnings.append(
                f"Narração de {seg_id} ({lang}) com problema depois de {attempts} tentativa(s): {'; '.join(problems)}. "
                "Ouça esse trecho antes de publicar."
            )
        output = {
            "path": self.store.rel(result.path),
            "duration": round(result.duration_s, 4),
            "words": [w.to_dict() for w in result.words] if result.words else None,
            "heard": qa["heard"] if qa else None,
            "qa": {"wer": round(qa["wer"], 4), "attempts": attempts, "ok": not problems} if qa else None,
            "problems": problems,
            "attempts": attempts,
            "provider": provider.info.id,
            "model": result.model,
            "commercial": provider.info.commercial_use,
            "files": [self.store.rel(result.path)],
        }
        if plan.changed:
            # O que a voz recebeu e o mapa para devolver os tempos às palavras do roteiro (a legenda).
            output["spoken"] = plan.spoken
            output["spoken_map"] = [list(g) for g in plan.groups]
        self.store.save("tts", h, output, provider.info.id, result.usage.cost_usd)
        self.stats.hit("tts", False, result.usage.cost_usd)
        if not provider.info.commercial_use:
            self.stats.noncommercial.add(provider.info.id)
        return h, output

    def _stale_local_speech(self, cached: dict[str, Any], text: str, lang: str) -> bool:
        """Gravação de voz local (Kokoro, Windows, Qwen3: recebem o texto falado das vozes locais) feita com outro
        texto falado. A chave do cache só leva o texto das vozes de API (assim a ElevenLabs não regrava à toa), então
        uma linha do dicionário marcada "Só a voz local" que mudou depois da gravação só aparece aqui."""
        try:
            provider = registry.get(str(cached.get("provider") or ""))
        except Exception:  # noqa: BLE001 - provedor que saiu do app: a gravação vale como está
            return False
        if not getattr(provider.info, "spoken_text", False):
            return False
        plan = self.speech(text, lang, local=True)
        return cached.get("spoken") != (plan.spoken if plan.changed else None)

    def _recheck_wer(self, cached: dict[str, Any], text: str, lang: str, spoken: str | None = None) -> list[str]:
        """Problemas de uma narração já gravada, refazendo a conta do erro com os números por extenso (as gravações
        de antes de 14/11/2026 contavam "2025" × "dois mil e vinte e cinco" como erro)."""
        problems = list(cached.get("problems") or [])
        heard = cached.get("heard")
        if not problems or not heard:
            return problems
        suffix = "das palavras diferem do roteiro"
        if not any(p.endswith(suffix) for p in problems):
            return problems
        problems = [p for p in problems if not p.endswith(suffix)]
        said = [w["text"] for w in heard]
        wer = min(word_error_rate(ref, said, lang) for ref in (text, spoken) if ref)
        if wer > self.style.voice.qa.max_wer:
            problems.append(f"{wer:.0%} {suffix}")
        return problems

    def _qa(self, text: str, lang: str, audio_path: Path, spoken: str | None = None) -> dict[str, Any] | None:
        """Transcreve a narração e mede o erro contra o roteiro (só com o Whisper local instalado). Com o texto
        falado (voz local), vale o menor erro entre o roteiro e o texto falado (o Whisper escreve "FBI" ou "efe bê
        i"); `ref` é o que ficou mais perto do que foi ouvido."""
        if not self.style.voice.qa.enabled or self.settings.offline:
            return None
        try:
            whisper = registry.get("faster_whisper")
        except Exception:  # noqa: BLE001
            return None
        ok, _ = whisper.availability()
        if not ok:
            return None
        try:
            heard = whisper.transcribe(audio_path, lang)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - QA nunca derruba o build
            log.warning("controle de qualidade da narração falhou: %s", exc)
            return None
        said = [w.text for w in heard]
        wer, ref = min((word_error_rate(r, said, lang), r) for r in (text, spoken) if r)
        return {"wer": wer, "ref": ref, "heard": [w.to_dict() for w in heard]}

    def align_inputs(self, seg_id: str, lang: str, tts_hash: str, tts_out: dict[str, Any]) -> dict[str, Any]:
        inputs = {"tts": tts_hash, "text": self.doc.segment(seg_id).text_for(lang),
                  "chain": registry.resolve_chain("align", None), "from_tts": bool(tts_out.get("words")),
                  "heard": bool(tts_out.get("heard")), "v": 2}
        if _spoken_text(tts_out):
            inputs["spoken"] = True
        return inputs

    def alignment(self, seg_id: str, lang: str, tts_hash: str, tts_out: dict[str, Any]) -> list[Word]:
        inputs = self.align_inputs(seg_id, lang, tts_hash, tts_out)
        text, chain = inputs["text"], inputs["chain"]
        spoken = _spoken_text(tts_out)
        h = hash_obj(inputs)
        cached = self.store.lookup("align", h)
        if cached and not (not tts_out.get("words") and not tts_out.get("heard") and self._better_available(chain, cached.get("provider"))):
            self.stats.hit("align", True)
            return [Word.from_dict(w) for w in cached["words"]]
        audio_path = self.store.abs(tts_out["path"])
        # A voz recebeu o texto falado ("dois mil e vinte e cinco"): alinha contra ele e devolve os tempos às
        # palavras do roteiro ("2025"), que são as da legenda.
        target = spoken or text
        if tts_out.get("words"):
            words = map_to_script(target, [Word.from_dict(w) for w in tts_out["words"]], audio_path)
            provider_id = "tts"
        elif tts_out.get("heard"):
            words = map_to_script(target, [Word.from_dict(w) for w in tts_out["heard"]], audio_path)
            provider_id = "faster_whisper"
        else:
            words, provider, _ = registry.run_chain(
                "align", chain, lambda p: p.align(audio_path=audio_path, text=target, lang=lang),
                project_id=self.project_id, job_id=self.sink.job_id,
            )
            provider_id = provider.info.id
        if spoken:
            words = script_words(text, words, tts_out["spoken_map"])
        self.store.save("align", h, {"words": [w.to_dict() for w in words], "provider": provider_id, "files": []}, provider_id)
        self.stats.hit("align", False)
        return words

    def image_inputs(self, scene: Scene) -> dict[str, Any] | None:
        if not scene_needs_image(scene):
            return None
        visual = scene.visual
        if visual.type == "stock":
            chain = registry.resolve_chain("image", ["pexels"])
            prompt = visual.query or visual.prompt
        else:
            chain = registry.resolve_chain("image", self.style.art.image_chain)
            if visual.language and self.doc.art is not None:
                # Personagem cadastrado: a descrição do cadastro entra no prompt (nunca o nome).
                art, visual = self.character_view(visual)
                prompt = compose_directed_prompt(self.style, art, visual)
            else:
                # Cena antiga (sem linguagem ou sem direção): o prompt de sempre, com a mesma chave de cache.
                prompt = compose_image_prompt(self.style, visual.prompt)
        inputs: dict[str, Any] = {
            "prompt": prompt,
            "width": self.style.format.width,
            "height": self.style.format.height,
            "seed": seed_from(visual.seed, visual.variation),
            "variation": visual.variation,
            "type": visual.type,
            "chain": chain,
        }
        refs = self.reference_images(scene)
        if refs:
            # Só quando há imagem âncora: as cenas sem personagem mantêm a chave de cache de antes. O nome do arquivo
            # já é o hash do conteúdo (trocar a imagem do personagem gera a cena de novo).
            inputs["references"] = [p.stem for p in refs]
        return inputs

    def character_view(self, visual: Visual) -> tuple[ArtDirection, Visual]:
        """Direção com a descrição atual dos personagens e a cena com a referência ao personagem em dia no prompt
        (editar a descrição não deixa "The tall woman" no prompt de quem virou homem idoso)."""
        assert self.doc.art is not None
        art = characters_mod.with_characters(self.doc.art, self.characters)
        if self.characters and visual.entities:
            from studio.workers.pipeline.characters import (
                current_prompt,  # import circular (storyboard_check)
            )

            prompt = current_prompt(visual.prompt, self.doc.art, self.characters, visual.entities)
            if prompt != visual.prompt:
                visual = visual.model_copy(update={"prompt": prompt})
        return art, visual

    def references_pending(self, chain: list[str], cached: dict[str, Any]) -> bool:
        """A imagem do cache saiu sem as imagens âncora (o gerador que as aceita estava sem cota ou fora do ar) e
        agora há na cadeia um gerador que as aceita disponível: gera de novo para manter o personagem."""
        if cached.get("references_used", True):
            return False
        for candidate in chain:
            provider = registry.get(candidate)
            if not provider.info.accepts_references:
                continue
            ok, _ = provider.availability()
            if ok and not registry.quota_exhausted_today(candidate):
                return True
        return False

    @cached_property
    def characters(self) -> list[Character]:
        """Personagens do projeto e do canal (lidos uma vez por build)."""
        try:
            return characters_mod.for_doc(self.doc)
        except Exception as exc:  # noqa: BLE001 - sem o canal, fica só o que está no projeto
            log.warning("não deu para ler os personagens do canal: %s", exc)
            return list(self.doc.characters)

    def reference_images(self, scene: Scene) -> list[Path]:
        """Imagens âncora dos personagens da cena (só cena dirigida de imagem de IA)."""
        visual = scene.visual
        if visual.type == "stock" or not visual.language or self.doc.art is None or not visual.entities:
            return []
        return characters_mod.reference_images(visual, self.characters, self.settings.data_dir)

    def image_keys(self, inputs: dict[str, Any], scene: Scene | None = None) -> list[str]:
        """Todas as chaves de cache em que a imagem desta cena pode estar (a atual primeiro, depois as antigas)."""
        variants = [inputs]
        visual = scene.visual if scene is not None else None
        if visual is not None and visual.type != "stock" and visual.language and self.doc.art is not None:
            # Com os personagens de agora: depois de editar a descrição, a imagem velha não é achada pelo prompt antigo.
            art, current = self.character_view(visual)
            old = legacy_directed_prompt(self.style, art, current)
            if old != inputs["prompt"]:
                variants.append({**inputs, "prompt": old})
        keys = [k for v in variants for k in (
            image_key(v), *(hash_obj({**v, "chain": chain}) for chain in (inputs["chain"], *_LEGACY_IMAGE_CHAINS)))]
        return list(dict.fromkeys(keys))

    def cached_image(self, inputs: dict[str, Any],
                     scene: Scene | None = None) -> tuple[str, dict[str, Any] | None]:
        """Chave da imagem e o que já existe no cache (também com a chave antiga). Entre as guardadas, prefere a
        que pode ir para o canal: uma imagem de teste guardada depois não esconde a imagem boa. A cena dirigida
        também acha a imagem gerada com o prompt de antes (`legacy_directed_prompt`); a imagem nova vai na chave nova."""
        h = image_key(inputs)
        found = None
        for key in self.image_keys(inputs, scene):
            cached = self.store.lookup("image", key)
            if cached and cached.get("commercial", True):
                return h, cached
            found = found or cached
        return h, found

    def media_image(self, scene: Scene) -> str | None:
        """A mídia real da cena no tamanho do vídeo (cache "media": recortar de novo é só tempo de máquina). None:
        notícia sem imagem (fundo animado) ou arquivo sumido (com aviso; nunca gera IA no lugar sem avisar)."""
        media = scene.visual.media
        if media is None or not media.asset:
            return None
        src = self.store.abs(media.asset)
        if not src.is_file():
            self.warnings.append(f"A mídia real da {scene_label(self.doc, scene.id)} sumiu da pasta de dados; a cena "
                                 "ficou sem imagem. Anexe de novo ou tire a mídia.")
            return None
        width, height = self.style.format.width, self.style.format.height
        fit = resolve_fit(media, width, height)
        anchor = anchor_for(width, height, self.style.edit.captions.position)
        inputs = {"media": media.asset, "size": [width, height], "fit": fit, "focus": media.focus or "center",
                  "anchor": anchor, "v": 1}
        h = hash_obj(inputs)
        # Trava: duas cenas com a mesma mídia (mesma chave) não recortam juntas nem gravam o mesmo cache duas vezes
        # (o banco recusaria a segunda e o build inteiro falharia). Recortar é rápido: a trava global sai barata.
        with _media_lock:
            cached = self.store.lookup("media", h)
            if cached:
                self.stats.hit("media", True)
                return cached["path"]
            out = fit_media(src, self.store.dir_for("media", h) / "image.jpg", width, height, fit=fit,
                            focus=media.focus, anchor_y=anchor)
            rel = self.store.rel(out)
            self.store.save("media", h, {"path": rel, "files": [rel], "source": media.source}, "pillow")
        self.stats.hit("media", False)
        return rel

    def image(self, scene: Scene) -> str | None:
        if scene.visual.media is not None:
            return self.media_image(scene)  # mídia real vale mais que a imagem fixada, o reaproveitamento e a IA
        if scene.visual.asset:
            return scene.visual.asset  # upload ou imagem fixada (qualquer tipo): nada a gerar
        inputs = self.image_inputs(scene)
        if inputs is None:
            return None
        h, cached = self.cached_image(inputs, scene)
        if cached and inputs.get("references") and self.references_pending(inputs["chain"], cached):
            cached = None
        if cached and not self._better_available(inputs["chain"], cached.get("provider")):
            self.stats.hit("image", True)
            if not cached.get("commercial", True):
                self.stats.noncommercial.add(cached.get("provider", "?"))
            return cached["path"]
        out_dir = self.store.dir_for("image", h)

        refs = self.reference_images(scene) if inputs.get("references") else []
        sent: dict[str, bool] = {}

        def call(provider):
            self._check()
            # Imagens âncora só para o gerador que as aceita; os outros ficam com a descrição do prompt.
            extra = {"references": refs} if refs and provider.info.accepts_references else {}
            sent[provider.info.id] = bool(extra)
            cli_lock = _CLI_IMAGE_LOCKS.get(provider.info.id)
            if cli_lock is not None:
                with cli_lock:
                    self._check()  # cancelado enquanto esperava a vez do CLI: não começa outra imagem
                    return provider.generate(prompt=inputs["prompt"], width=inputs["width"], height=inputs["height"],
                                             seed=inputs["seed"], out_dir=out_dir, **extra)
            return provider.generate(prompt=inputs["prompt"], width=inputs["width"], height=inputs["height"],
                                     seed=inputs["seed"], out_dir=out_dir, **extra)

        result, provider, _ = registry.run_chain("image", inputs["chain"], call, usage_of=self._usage,
                                                 project_id=self.project_id, job_id=self.sink.job_id)
        rel = self.store.rel(result.path)
        output = {"path": rel, "provider": provider.info.id, "model": result.model, "commercial": provider.info.commercial_use,
                  "license": result.meta.get("license"), "files": [rel]}
        if refs:
            # Saiu sem as imagens âncora (outro gerador da cadeia): o próximo build tenta de novo com elas.
            output["references_used"] = sent.get(provider.info.id, False)
        self.store.save("image", h, output, provider.info.id, result.usage.cost_usd)
        self.stats.hit("image", False, result.usage.cost_usd)
        if not provider.info.commercial_use:
            self.stats.noncommercial.add(provider.info.id)
        return rel

    def depth_map(self, image_rel: str, *, estimate: bool = True) -> tuple[str, str]:
        """Mapa de profundidade da imagem (cache "depth" pelo hash do arquivo e do modelo). Devolve (hash, caminho).
        Com `estimate=False` só serve o cache (sem o modelo): levanta DepthUnavailable se precisar estimar."""
        src = self.store.abs(image_rel)
        inputs = {"image": hash_file(src), "model": depth_mod.MODEL_ID, "v": 1}
        h = hash_obj(inputs)
        with _depth_lock:
            cached = self.store.lookup("depth", h)
            if cached:
                self.stats.hit("depth", True)
                return h, cached["path"]
            if not estimate:
                raise depth_mod.DepthUnavailable("sem mapa de profundidade guardado")
            depth = depth_mod.get_estimator()(src)
            out = depth_mod.save_depth(depth, self.store.dir_for("depth", h) / "depth.png")
            rel = self.store.rel(out)
            self.store.save("depth", h, {"path": rel, "files": [rel], "model": depth_mod.MODEL_ID}, "depth_anything")
        self.stats.hit("depth", False)
        return h, rel

    def parallax_layers(self, image_rel: str, *, estimate: bool = True) -> list[str]:
        """Camadas do parallax 2.5D (cache "parallax" pela imagem e pelo mapa). Levanta DepthUnavailable sem modelo
        (com `estimate=False`, sempre que o mapa não estiver no cache). Mapa plano dá uma camada só."""
        depth_h, depth_rel = self.depth_map(image_rel, estimate=estimate)
        inputs = {"image": hash_file(self.store.abs(image_rel)), "depth": depth_h, "layers": depth_mod.LAYERS,
                  "v": depth_mod.LAYERS_VERSION}
        h = hash_obj(inputs)
        with _depth_lock:
            cached = self.store.lookup("parallax", h)
            if cached:
                self.stats.hit("parallax", True)
                return list(cached["layers"])
            with Image.open(self.store.abs(image_rel)) as im:
                image = im.convert("RGB")
            paths = depth_mod.make_layers(image, depth_mod.load_depth(self.store.abs(depth_rel)),
                                          self.store.dir_for("parallax", h))
            rels = [self.store.rel(p) for p in paths]
            self.store.save("parallax", h, {"layers": rels, "files": rels, "depth": depth_rel}, "numpy")
        self.stats.hit("parallax", False)
        return rels

    def _download_depth_model(self) -> None:
        """Baixa o modelo de profundidade mostrando o andamento no passo atual; cancelar o build interrompe."""
        with self._progress_lock:
            fraction = min(0.999, self._done / max(1, self._total))
        total_mb = depth_mod.MODEL_MB
        self.sink.progress(fraction, f"Baixando o modelo de profundidade (0/{total_mb} MB)")
        shown = [0]

        def on_chunk(done: int) -> None:
            self._check()
            mb = done // 1_000_000
            if mb - shown[0] >= 5:  # a cada 5 MB: não escreve no banco a cada pedaço
                shown[0] = mb
                self.sink.progress(fraction, f"Baixando o modelo de profundidade ({mb}/{total_mb} MB)")

        depth_mod.ensure_model(on_chunk=on_chunk)

    def parallax(self, images: dict[str, str | None]) -> dict[str, list[str]]:
        """Camadas das cenas com movimento "parallax" e imagem. Sem o modelo de profundidade (ou se falhar), a
        cena fica sem camadas (a timeline usa o Ken Burns) e o usuário recebe um aviso só. Camadas que já estão no
        cache valem mesmo sem o modelo."""
        wanted = [s for s in self.doc.ordered_scenes()
                  if images.get(s.id) and s.kind not in NO_VISUAL_LAYER_KINDS
                  and scene_motion(self.doc, self.style, s) == PARALLAX]
        if not wanted:
            return {}
        ok, reason = depth_mod.availability()
        out: dict[str, list[str]] = {}
        todo: list[str] = []
        # 1ª passada: o que já está no cache (não precisa do modelo).
        for scene in wanted:
            self._check()
            try:
                out[scene.id] = self.parallax_layers(images[scene.id], estimate=False)  # type: ignore[arg-type]
            except depth_mod.DepthUnavailable:
                todo.append(scene.id)
            except Exception as exc:  # noqa: BLE001 - imagem ruim: só esta cena cai no Ken Burns
                self._parallax_failed(scene.id, exc)
        problem = None if ok else reason
        if todo and problem is None and depth_mod.uses_model() and not depth_mod.model_ready():
            try:
                self._download_depth_model()
            except depth_mod.DepthUnavailable as exc:
                problem = str(exc)
        fallback: list[str] = []
        for scene_id in todo:
            self._check()
            if problem is None:
                try:
                    out[scene_id] = self.parallax_layers(images[scene_id])  # type: ignore[arg-type]
                    continue
                except depth_mod.DepthUnavailable as exc:
                    problem = str(exc)
                except Exception as exc:  # noqa: BLE001
                    self._parallax_failed(scene_id, exc)
                    continue
            fallback.append(scene_id)
        if fallback:
            n = len(fallback)
            self.warnings.append(
                f"Parallax 2.5D indisponível ({problem}): {n} cena{'s' if n > 1 else ''} "
                f"ficara{'m' if n > 1 else ''} com o Ken Burns.")
        # Mapa plano dá uma camada só: sem o que separar, a cena fica com o Ken Burns.
        return {sid: layers for sid, layers in out.items() if len(layers) > 1}

    def _parallax_failed(self, scene_id: str, exc: Exception) -> None:
        log.warning("parallax falhou na cena %s: %s", scene_id, exc)
        self.warnings.append(f"Parallax da {scene_label(self.doc, scene_id)} falhou ({exc}); a cena usa o Ken Burns.")

    def lut_images(self, images: dict[str, str | None], extra: Iterable[str] = ()) -> dict[str, str | None]:
        """Imagens das cenas com o LUT do estilo aplicado (efeito "lut"), em cache por imagem + LUT + intensidade.
        Sem LUT, devolve as mesmas. LUT que sumiu da biblioteca vira aviso e o vídeo sai sem ele.

        `extra` são outras imagens que aparecem no vídeo (a foto do cartão de notícia): ficam gradadas em
        `self.lut_graded` (original → gradada). Cena de vídeo fica sem LUT, com aviso (a cor dela destoa)."""
        effect = next((e for e in self.style.edit.effects if e.type == "lut" and e.amount > 0), None)
        if effect is None or not effect.preset:
            return images
        try:
            lut = lut_mod.load_lut(effect.preset)
        except (lut_mod.LutError, OSError) as exc:
            self.warnings.append(f"LUT de cor ignorado: {exc}")
            return images
        amount = round(effect.amount, 3)
        done: dict[str, str] = {}

        def grade(rel: str) -> str:
            src = self.store.abs(rel)
            h = hash_obj({"v": lut_mod.LUT_VERSION, "src": hash_file(src), "lut": lut.digest, "amount": amount})
            cached = self.store.lookup("lut", h)
            if cached:
                self.stats.hit("lut", True)
                return cached["path"]
            out = lut_mod.grade_image(src, self.store.dir_for("lut", h), lut, amount)
            out_rel = self.store.rel(out)
            self.store.save("lut", h, {"path": out_rel, "files": [out_rel], "source": rel, "lut": effect.preset}, "numpy")
            self.stats.hit("lut", False)
            return out_rel

        videos = sum(1 for rel in images.values() if rel and not rel.lower().endswith(lut_mod.IMAGE_EXTS))
        if videos:
            self.warnings.append(f"LUT de cor não é aplicado em cenas de vídeo ({videos} "
                                 f"{'cena' if videos == 1 else 'cenas'}): a cor delas fica diferente das imagens.")
        todo = sorted({rel for rel in [*images.values(), *extra]
                       if rel and rel.lower().endswith(lut_mod.IMAGE_EXTS) and self.store.abs(rel).is_file()})
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [(rel, pool.submit(grade, rel)) for rel in todo]
            for rel, future in futures:
                if self.sink.cancelled():
                    # Cancela o que ainda não começou: o `with` só espera as (no máximo 4) imagens em andamento.
                    for _, f in futures:
                        f.cancel()
                    raise BuildCancelled("cancelado pelo usuário")
                try:
                    done[rel] = future.result()
                except Exception as exc:  # noqa: BLE001 - imagem ilegível fica sem LUT, o vídeo segue
                    log.warning("LUT não aplicado em %s: %s", rel, exc)
                    self.warnings.append(f"LUT de cor não aplicado em uma imagem: {exc}")
        if todo:
            self._tick("Cor (LUT) aplicada")
        self.lut_graded = done
        return {sid: done.get(rel, rel) if rel else rel for sid, rel in images.items()}

    def _lut_news_photos(self, timeline: dict[str, Any]) -> None:
        """Troca a foto do cartão de notícia pela versão com LUT (gradada em lut_images)."""
        if not self.lut_graded:
            return
        for scene in timeline.get("scenes", []):
            news = scene.get("news")
            if isinstance(news, dict) and news.get("image") in self.lut_graded:
                news["image"] = self.lut_graded[news["image"]]

    def music(self, duration: float, timeline: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]] | None:
        """Trilha do vídeo. Uma faixa ativa sem `loop` = o caminho de sempre (mesma chave de cache: projeto antigo não
        refaz o áudio). Com várias faixas ou `loop` (e a `timeline`, que diz onde cada segmento começa), monta uma
        cama por faixa com `audio.build_beds` (ADR 0003, bloco G)."""
        music_spec = self.style.audio.music
        cues = [c for c in self.doc.music if c.enabled]
        if music_spec.source == "none" or not cues:
            return None
        if timeline is not None and (len(cues) > 1 or any(c.loop for c in cues)):
            return self._music_beds_of(cues, duration, timeline)
        cue = cues[0]
        mood = cue.mood or music_spec.mood
        bucket = float(int(duration // 5 + 1) * 5)
        volume = cue.volume_db if cue.volume_db is not None else music_spec.volume_db
        if music_spec.source == "library" or cue.track:
            found = self._library_music(mood, bucket, cue.track)
            if found:
                return found[0], {**found[1], "volume_db": volume}
        # v2: a trilha sai sem os fades longos (o mix faz a entrada e a saída contra o vídeo real).
        inputs = {"mood": audio_mod.mood_key(mood), "duration": bucket, "seed": seed_from(self.doc.seed, "music", mood), "v": 2}
        h = hash_obj(inputs)
        cached = self.store.lookup("music", h)
        if cached:
            self.stats.hit("music", True)
            return h, {**cached, "volume_db": volume}
        path = audio_mod.generate_music(mood, bucket, inputs["seed"], self.store.dir_for("music", h) / "music.wav")
        output = {"path": self.store.rel(path), "files": [self.store.rel(path)], "mood": inputs["mood"]}
        self.store.save("music", h, output, "procedural")
        self.stats.hit("music", False)
        return h, {**output, "volume_db": volume}

    def _music_beds_of(self, cues: list[Any], duration: float, timeline: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Parte de `music()`: uma cama por faixa ativa, trocando no início da narração do segmento marcado.

        Ordem pelo segmento de início (vazio = começo do vídeo); a 1ª cama começa em 0. Duas faixas no mesmo ponto:
        vale a que vem depois na lista."""
        music_spec = self.style.audio.music
        seg_order = {s.id: i for i, s in enumerate(self.doc.segments)}
        seg_start = {n["segment_id"]: float(n["start"]) for n in timeline.get("narration", [])}

        def order(pair: tuple[int, Any]) -> tuple[int, int]:
            index, cue = pair
            return (seg_order.get(cue.start_segment, -1) if cue.start_segment else -1, index)

        # Faixa que começa num segmento que não existe mais (apagado, roteiro refeito): fica fora, com aviso. Sem
        # isso ela caía no 0,0 e tomava o lugar da faixa de abertura.
        lost = [c for c in cues if c.start_segment and (c.start_segment not in seg_order or c.start_segment not in seg_start)]
        kept = [c for c in cues if c not in lost]
        if not kept:  # só sobraram faixas soltas: a 1ª delas toca o vídeo todo, como antes das várias faixas
            kept, lost = lost[:1], lost[1:]
        if lost:
            self.warnings.append(f"{len(lost)} faixa(s) de música começava(m) num trecho que não está mais no vídeo "
                                 "(apagado ou sem narração) e ficou(aram) fora. Escolha outro início no painel Trilha.")
        cues = kept
        has_library = bool(music_library.list_tracks())
        by_start: dict[float, Any] = {}
        for _index, cue in sorted(enumerate(cues), key=order):
            start = seg_start.get(cue.start_segment, 0.0) if cue.start_segment else 0.0
            by_start[round(min(max(0.0, start), duration), 3)] = cue
        starts = sorted(by_start)
        chosen = [by_start[s] for s in starts]
        starts[0] = 0.0  # a 1ª cama começa no início do vídeo
        durs = audio_mod.bed_durations(starts, duration)
        beds: list[dict[str, Any]] = []
        base_volume: float | None = None
        for i, (cue, start, dur) in enumerate(zip(chosen, starts, durs, strict=True)):
            mood = cue.mood or music_spec.mood
            volume = cue.volume_db if cue.volume_db is not None else music_spec.volume_db
            base_volume = volume if base_volume is None else base_volume
            # a 1ª faixa mantém a semente de sempre; as outras, uma por faixa
            seed = seed_from(self.doc.seed, "music", mood) if i == 0 else seed_from(self.doc.seed, "music", cue.id)
            bed: dict[str, Any] = {"cue_id": cue.id, "start_s": round(start, 3), "dur_s": round(dur, 3),
                                   "gain_db": round(volume - base_volume, 2), "loop": None, "intro": False}
            lib = None
            # `loop` só puxa a biblioteca quando ela tem faixas: com o estilo procedural e a biblioteca vazia, a
            # faixa segue procedural sem o aviso de "biblioteca vazia" (o usuário não pediu biblioteca)
            if music_spec.source == "library" or cue.track or (cue.loop and has_library):
                lib = self._library_music(mood, 0.0, cue.track, seed=seed, prepare=False, prefer_used=i == 0)
            if lib:
                key, info = lib
                bed.update({"src": info["src"], "key": key, "source": "library", "track": info["track"],
                            "mood": info["mood"]})
                if cue.loop:
                    cur = music_library.curate_track(info["track"])
                    if cur.get("ok"):
                        bed.update({"loop": [cur["loop_start_s"], cur["loop_s"]], "intro": bool(cur.get("intro")),
                                    "voz_db": cur.get("voz_db"), "trim_db": cur.get("trim_db")})
                    else:
                        reason = cur.get("reason") or "sem curadoria"
                        self.warnings.append(f"A faixa '{info['track']}' ficou sem loop no compasso ({reason}): "
                                             "tocou desde o começo.")
            else:
                bucket = float(int(dur // 5 + 1) * 5)
                inputs = {"mood": audio_mod.mood_key(mood), "duration": bucket, "seed": seed, "v": 2}
                h = hash_obj(inputs)
                cached = self.store.lookup("music", h)
                if cached:
                    self.stats.hit("music", True)
                else:
                    path = audio_mod.generate_music(mood, bucket, seed, self.store.dir_for("music", h) / "music.wav")
                    cached = {"path": self.store.rel(path), "files": [self.store.rel(path)], "mood": inputs["mood"]}
                    self.store.save("music", h, cached, "procedural")
                    self.stats.hit("music", False)
                bed.update({"src": str(self.store.abs(cached["path"])), "key": h, "source": "procedural",
                            "track": None, "mood": inputs["mood"]})
            beds.append(bed)
        spec = [(b["key"], b["start_s"], b["dur_s"], b["loop"], b["intro"], b["gain_db"], b.get("voz_db"),
                 b.get("trim_db")) for b in beds]
        h = hash_obj({"beds": spec, "total": duration, "v": 1})
        public = [{"cue_id": b["cue_id"], "track": b["track"], "mood": b["mood"], "start_s": b["start_s"],
                   "dur_s": b["dur_s"], "loop": b["loop"]} for b in beds]
        first = beds[0]
        meta = {"mood": first["mood"], "source": first["source"], "track": first["track"], "beds": public}
        cached = self.store.lookup("music", h)
        if cached:
            self.stats.hit("music", True)
            return h, {**cached, **meta, "volume_db": base_volume}
        path = audio_mod.build_beds(beds, duration, self.store.dir_for("music", h) / "beds.wav")
        output = {"path": self.store.rel(path), "files": [self.store.rel(path)], **meta}
        self.store.save("music", h, output, "beds")
        self.stats.hit("music", False)
        return h, {**output, "volume_db": base_volume}

    def _library_music(self, mood: str, bucket: float, pinned: str | None, *, seed: int | None = None,
                       prepare: bool = True, prefer_used: bool = True) -> tuple[str, dict[str, Any]] | None:
        """Trilha da biblioteca do usuário (library/music), ajustada à duração. None = biblioteca vazia.

        Com `prepare=False` (camas de várias faixas), só escolhe a faixa: devolve o hash do arquivo e `src` (absoluto)."""
        tracks = music_library.list_tracks()
        pinned_found = bool(pinned) and any(t.path == pinned for t in tracks)
        if pinned and not pinned_found:
            self.warnings.append(f"A faixa fixada '{pinned}' não está mais na biblioteca; escolhi outra.")
        seed = seed_from(self.doc.seed, "music", mood) if seed is None else seed
        # Sem repetir a faixa dos últimos vídeos do canal (a fixada vale mais; a que o projeto já usava fica, só na
        # 1ª cama: nas outras ela não é preferida, para as faixas seguintes não repetirem a de abertura).
        avoid = [] if pinned_found else music_library.recent_tracks(self.project_id)
        prefer = (music_library.used_track(service.get_project_row(self.project_id)["settings"])
                  if avoid and prefer_used else None)
        track = music_library.pick_track(tracks, mood, seed, pinned, avoid=avoid, prefer=prefer)
        if track is not None and track.path in avoid:
            self.warnings.append(
                f"Todas as faixas do clima '{track.mood}' tocaram nos últimos vídeos do canal; repeti a que tocou há "
                f"mais tempo ({track.path}). Coloque mais músicas desse clima em {music_library.music_root()}."
            )
        src = music_library.resolve(track.path) if track else None
        if track is None or src is None:
            self.warnings.append(
                "A biblioteca de músicas está vazia; usei a trilha procedural. "
                f"Coloque arquivos de áudio em {music_library.music_root()}."
            )
            return None
        if not prepare:
            return hash_file(src), {"source": "library", "track": track.path, "mood": track.mood, "src": str(src)}
        inputs = {"source": "library", "track": hash_file(src), "duration": bucket, "v": 1}
        h = hash_obj(inputs)
        cached = self.store.lookup("music", h)
        if cached:
            self.stats.hit("music", True)
            return h, {**cached, "track": track.path}
        path = music_library.prepare_track(src, bucket, self.store.dir_for("music", h) / "music.wav")
        output = {"path": self.store.rel(path), "files": [self.store.rel(path)], "mood": track.mood, "source": "library",
                  "track": track.path}
        self.store.save("music", h, output, "library")
        self.stats.hit("music", False)
        return h, output

    def music_track(self, timeline: dict[str, Any]) -> tuple[Path, dict[str, Any]] | None:
        """Trilha já montada para a timeline (a mesma do mix), para a conferência de áudio: (arquivo, medidas)."""
        found = self.music(timeline["duration"], timeline)
        if found is None:
            return None
        return self.store.abs(found[1]["path"]), found[1]

    def sfx_file(self, kind: str) -> Path:
        """Efeito procedural do tipo, no cache (chave com a `SFX_VERSION`)."""
        return sfx_library.procedural_path(kind, self.store)

    @cached_property
    def sfx_tonal_ok(self) -> bool:
        return bool(grammar_for(self.style).sfx_tonal_ok)

    def sfx_source(self, ref: str, explicit: bool = False) -> Path:
        """Arquivo pronto para o mix (`lib_id` ou tipo; ver `sfx_library.resolve`)."""
        return sfx_library.resolve(ref, store=self.store, tonal_ok=self.sfx_tonal_ok, explicit=explicit)

    def sfx_plan(self, timeline: dict[str, Any]) -> list[dict[str, Any]]:
        """Os efeitos da timeline como entram no mix (`type, start, ref, explicit, gain_db, lib_id, extra`), sem os
        bloqueados pela guarda de apito (regra 13 do padrão do estúdio), com aviso.

        - Evento de antes (`start`, sem `lib_id`): igual ao que era (o procedural do tipo).
        - Evento do ADR 0004 (`at`, `lib_id?`, `gain_db?`): o `lib_id` da biblioteca (ou o tipo, se o id sumiu), posto
          em `at - hit_s` para o ponto forte cair na palavra (`sfx_library.event_start`).
        - Efeito da biblioteca com licença desconhecida toca se foi pedido pelo `lib_id` (alguém o escolheu), com aviso.
        """
        tonal_ok = self.sfx_tonal_ok
        planned: list[dict[str, Any]] = []
        for e in timeline.get("sfx", []):
            ref, explicit = sfx_library.event_ref(e)
            lib_id = e.get("lib_id")
            if lib_id and not explicit:
                self.warnings.append(f"efeito '{lib_id}' não está na biblioteca: tocou o '{e['type']}' no lugar")
            planned.append({"type": e["type"], "start": sfx_library.event_start(e, tonal_ok=tonal_ok), "ref": ref,
                            "explicit": explicit, "gain_db": float(e.get("gain_db") or 0.0), "lib_id": lib_id,
                            "extra": "lib_id" in e or "gain_db" in e})
        blocked: set[tuple[str, bool]] = set()
        for ref, explicit in sorted({(p["ref"], p["explicit"]) for p in planned}):
            item = sfx_library.lookup(ref, tonal_ok=tonal_ok, explicit=explicit)
            if item is None or item.procedural:
                kind = item.kind if item is not None else ref
                gh = hash_obj({"sfx_guard": kind, "sfx_v": audio_mod.SFX_VERSION, "v": 1})
                banned = kind in audio_check.SFX_BANNED
            else:
                gh = hash_obj({"sfx_guard": item.id, "content": sfx_library.content_key(item), "v": 1})
                banned = item.banned
                if item.license == sfx_library.UNKNOWN_LICENSE:
                    self.warnings.append(f"efeito '{item.id}' com licença desconhecida no vídeo: declare a licença na "
                                         "biblioteca de efeitos")
            guard = self.store.lookup("sfx_guard", gh)
            if guard:
                report = guard["report"]
            else:
                sound, _rate = read_wav(self.sfx_source(ref, explicit))
                report = audio_check.tone_report(sound)
                self.store.save("sfx_guard", gh, {"report": report, "files": []}, "numpy")
            if banned or report.get("apito"):
                blocked.add((ref, explicit))
                self.warnings.append(f"efeito sonoro '{ref}' tem apito: ficou fora do mix (padrão do estúdio: sem apito)")
        return [p for p in planned if (p["ref"], p["explicit"]) not in blocked]

    def mix(self, lang: str, timeline: dict[str, Any], tts_hashes: dict[str, str], *, wav: bool = False) -> str:
        total = timeline["duration"]
        music = self.music(total, timeline)
        beds = music[1].get("beds") if music else None
        if music:
            first_cue = next((c.id for c in self.doc.music if c.enabled), None)
            single = [{"cue_id": first_cue, "track": music[1].get("track"), "mood": music[1].get("mood"),
                       "start_s": 0.0, "dur_s": total, "loop": None}]
            self.music_used = {"source": music[1].get("source", "procedural"), "track": music[1].get("track"),
                               "mood": music[1].get("mood"), "beds": beds or single}
        else:
            self.music_used = None
        # Guarda de apito (regra 13 do padrão do estúdio): efeito com apito ou banido sai do mix, com aviso.
        sfx_events = self.sfx_plan(timeline)
        short = self.style.format.type == "short"
        music_hp = audio_mod.music_highpass(music[1].get("mood")) if music else None
        music_ref = music_ref_s(music[1]) if music else None
        # v5: o ganho da trilha de várias faixas é medido só na 1ª cama (`music_ref_s`). v4: várias faixas ou loop no compasso (uma cama por faixa, `build_beds`). v3: trilha medida em mono depois do
        # passa-altas; o mix dura exatamente o vídeo e termina num fade curto (v2: passa-altas e ducking leve, fades
        # contra o fim real, SFX normalizados e master com ganho medido).
        inputs = {
            "voice": [(tts_hashes[n["segment_id"]], n["start"]) for n in timeline["narration"]],
            "music": (music[0], music[1]["volume_db"], self.style.audio.music.ducking, music_hp) if music else None,
            # evento de antes: (tipo, início), a chave de sempre; o do ADR 0004 leva também o lib_id e o ganho
            "sfx": [(e["type"], e["start"], e["lib_id"], e["gain_db"]) if e["extra"] else (e["type"], e["start"])
                    for e in sfx_events],
            "sfx_db": self.style.audio.sfx.volume_db,
            "loudness": self.style.audio.loudness_lufs,
            "total": total,
            "short": short,
            "v": 5 if beds else 3,
        }
        # Efeito que mudou de som em SFX_RETUNED_VERSION (glitch e riser perderam o apito): essa versão entra na chave
        # e o mix guardado com o som antigo é refeito. Efeito de evento (tipo novo da v4): a versão atual entra (um
        # tipo novo pedido antes da v4 saía como whoosh). Sem esses tipos, a chave fica a de antes (nada é refeito).
        refs = {e["ref"] for e in sfx_events}
        if refs & set(audio_mod.EVENT_SFX_KINDS):
            inputs["sfx_v"] = audio_mod.SFX_VERSION
        elif any(e["type"] in audio_mod.SFX_RETUNED for e in sfx_events):
            inputs["sfx_v"] = audio_mod.SFX_RETUNED_VERSION
        # Efeito da biblioteca: o conteúdo do arquivo entra na chave (trocar o arquivo refaz o mix).
        lib_keys = [(ref, explicit, key) for ref, explicit in sorted({(e["ref"], e["explicit"]) for e in sfx_events})
                    if (key := sfx_library.mix_key(ref, tonal_ok=self.sfx_tonal_ok, explicit=explicit)) is not None]
        if lib_keys:
            inputs["sfx_lib"] = lib_keys
        # Queda da trilha antes do reveal (`timeline.music_drops`, só quando houver).
        drops = audio_mod.clamp_drops(timeline.get("music_drops"), total) if music else []
        if drops:
            inputs["music_drops"] = drops
        if wav:
            inputs["fmt"] = "wav"  # faixa do Modo B para o YouTube Studio (sem perda)
        h = hash_obj(inputs)
        cached = self.store.lookup("mix", h)
        if cached:
            self.stats.hit("mix", True)
            return cached["path"]
        out_dir = self.store.dir_for("mix", h)
        voice = audio_mod.place_clips(
            [(self.store.abs(n["path"]), n["start"], 1.0) for n in timeline["narration"]], total, out_dir / "voice.wav"
        )
        sfx_track = None
        if sfx_events:
            volume_db = self.style.audio.sfx.volume_db
            sfx_track = audio_mod.place_clips(
                [(self.sfx_source(e["ref"], e["explicit"]), e["start"], 10 ** ((volume_db + e["gain_db"]) / 20))
                 for e in sfx_events],
                total, out_dir / "sfx.wav")
        mixed = audio_mod.mix_tracks(
            voice,
            out_dir / ("mix.wav" if wav else "mix.m4a"),
            music=self.store.abs(music[1]["path"]) if music else None,
            music_db=music[1]["volume_db"] if music else -22,
            ducking=self.style.audio.music.ducking,
            sfx=sfx_track,
            loudness=self.style.audio.loudness_lufs,
            total=total,
            short=short,
            music_highpass=music_hp or audio_mod.MUSIC_HIGHPASS_HZ,
            music_ref_s=music_ref,
            music_drops=[{"at": a, "dur": b - a} for a, b in drops] or None,
        )
        for tmp in (voice, sfx_track):
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        rel = self.store.rel(mixed)
        # `sfx_v`: a conferência de áudio sabe com que versão dos efeitos este mix foi montado
        self.store.save("mix", h, {"path": rel, "files": [rel], "sfx_v": audio_mod.SFX_VERSION}, "ffmpeg")
        self.stats.hit("mix", False)
        return rel

    def render_chunks(self, lang: str, timeline: dict[str, Any], on_chunk: Callable[[str], None]) -> list[Path]:
        version = renderer_version()
        chunks = chunk_timeline(timeline, version)
        paths: list[Path] = []
        client = None
        for index, chunk in enumerate(chunks, 1):
            self._check()
            cached = self.store.lookup("chunk", chunk["key"])
            if cached:
                self.stats.hit("chunk", True)
                paths.append(self.store.abs(cached["path"]))
                on_chunk(f"bloco {index}/{len(chunks)} reaproveitado")
                continue
            client = client or get_render_client()
            out = self.store.dir_for("chunk", chunk["key"]) / "chunk.mp4"
            client.render(chunk["props"], out)
            rel = self.store.rel(out)
            self.store.save("chunk", chunk["key"], {"path": rel, "files": [rel], "scenes": chunk["scene_ids"]}, "remotion")
            self.stats.hit("chunk", False)
            paths.append(out)
            on_chunk(f"bloco {index}/{len(chunks)} renderizado")
        return paths

    def final_video(self, lang: str, timeline: dict[str, Any], chunk_paths: list[Path], mix_rel: str) -> str:
        style = self.style
        watermark = self.settings.library_dir / style.edit.watermark if style.edit.watermark else None
        inputs = {
            "chunks": [self.store.rel(p) for p in chunk_paths],
            "mix": mix_rel,
            "progress": style.edit.progress_color if style.edit.progress_bar else None,
            "watermark": watermark.as_posix() if watermark and watermark.exists() else None,
            "fps": timeline["fps"],
            "size": [timeline["width"], timeline["height"]],
            "duration": timeline["duration"],
            "v": 1,
        }
        h = hash_obj(inputs)
        cached = self.store.lookup("final", h)
        if cached:
            self.stats.hit("final", True)
            return cached["path"]
        out = self.store.dir_for("final", h) / "video.mp4"
        assemble(
            chunk_paths,
            self.store.abs(mix_rel),
            out,
            width=timeline["width"],
            height=timeline["height"],
            fps=timeline["fps"],
            duration=timeline["duration"],
            progress_color=inputs["progress"],
            watermark=watermark if inputs["watermark"] else None,
        )
        rel = self.store.rel(out)
        self.store.save("final", h, {"path": rel, "files": [rel]}, "ffmpeg")
        self.stats.hit("final", False)
        return rel

    def default_thumbnail_scene(self, images: dict[str, str | None]) -> str | None:
        """Base padrão da thumbnail: a primeira cena com imagem que não é material de terceiros (a capa é o que mais
        chama reivindicação de direitos); sem nenhuma, a primeira com imagem."""
        doc = self.doc
        with_image = [s for s in doc.ordered_scenes() if images.get(s.id)]
        free = next((s.id for s in with_image if not ((m := doc.shown_media(s)) is not None and m.editorial)), None)
        return free or (with_image[0].id if with_image else None)

    def package_thumbnail(self, lang: str, images: dict[str, str | None]) -> str | None:
        """Capa pelo candidato escolhido no pacote: o mesmo desenho da prévia (compositor por camadas), no tamanho que
        a capa já usa (1280 x 720 no longo, 1080 x 1920 no Short), com o texto da thumb no idioma. None sem pacote
        escolhido ou se o compositor falhar (vale a capa antiga, pela cena)."""
        doc, style = self.doc, self.style
        chosen = doc.locked_package()
        if chosen is None:
            return None
        from studio.workers.visual import thumbs as visual_thumbs

        width, height = (1280, 720) if style.format.type == "long" else (1080, 1920)
        try:
            from studio.media import thumb_compose

            layers = visual_thumbs.candidate_layers(doc, chosen, lang)
            bible = visual_thumbs.channel_bible(doc.channel_id)
            allow = visual_thumbs.allow_editorial(doc, style, bible)
            resolver = visual_thumbs.resolver_for(doc, style, bible=bible,
                                                  images={k: v for k, v in images.items() if v})
            # A chave inclui o conteúdo do que as camadas usam (imagem da cena em `media:`, quadros, poses, o
            # manifesto da biblioteca) e a versão do compositor: trocar qualquer um refaz a capa.
            inputs = {"layers": layers.model_dump(mode="json"), "title": doc.effective_title(), "size": [width, height],
                      "palette": dict(style.thumbnail.palette), "logo": style.thumbnail.logo_asset, "allow": allow,
                      "candidate": chosen.id, "v": thumb_compose.VERSION,
                      "files": visual_thumbs.assets_stamp(layers, resolver)}
            h = hash_obj(inputs)
            cached = self.store.lookup("thumbnail", h)
            if cached:
                return cached["path"]
            img = thumb_compose.render(layers, (width, height), resolver, inputs["palette"],
                                       logo_asset=style.thumbnail.logo_asset)
        except Exception as exc:  # noqa: BLE001 - a capa antiga (pela cena) segura o render
            log.warning("a capa pelo pacote falhou; vale a capa pela cena: %s", exc)
            self.warnings.append(f"A capa pelo candidato escolhido falhou ({exc}): saiu a capa pela cena.")
            return None
        out = self.store.dir_for("thumbnail", h) / "thumbnail.jpg"
        out.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out, "JPEG", quality=92)
        rel = self.store.rel(out)
        self.store.save("thumbnail", h, {"path": rel, "files": [rel]}, "pillow")
        return rel

    def thumbnail(self, lang: str, images: dict[str, str | None]) -> str | None:
        packaged = self.package_thumbnail(lang, images)
        if packaged:
            return packaged
        doc, style = self.doc, self.style
        base_scene = doc.thumbnail.scene_id or self.default_thumbnail_scene(images)
        base = images.get(base_scene) if base_scene else None
        text = doc.thumbnail.text.get(lang) or doc.thumbnail.text.get(doc.primary_language) or ""
        if style.format.type == "long":
            width, height = 1280, 720
        else:
            width, height = 1080, 1920
        inputs = {"base": base, "text": text, "template": style.thumbnail.model_dump(), "size": [width, height],
                  "variation": doc.thumbnail.variation, "v": 1}
        h = hash_obj(inputs)
        cached = self.store.lookup("thumbnail", h)
        if cached:
            return cached["path"]
        out = self.store.dir_for("thumbnail", h) / "thumbnail.jpg"
        make_thumbnail(
            self.store.abs(base) if base else None,
            out,
            text=text,
            template=style.thumbnail.template,
            width=width,
            height=height,
            font=style.thumbnail.font,
            color=style.thumbnail.color,
            accent=style.thumbnail.accent,
        )
        rel = self.store.rel(out)
        self.store.save("thumbnail", h, {"path": rel, "files": [rel]}, "pillow")
        return rel

    # ---------------------------------------------------------------------------------------
    # execução
    # ---------------------------------------------------------------------------------------
    def validate(self) -> None:
        doc = self.doc
        if not doc.segments:
            raise BuildError("o projeto ainda não tem roteiro")
        if not doc.scenes:
            raise BuildError("o projeto ainda não tem storyboard (cenas)")
        for lang in self.langs:
            missing = [s.id for s in doc.segments if not s.text_for(lang).strip()]
            if missing:
                raise BuildError(f"faltam textos em {lang} para {len(missing)} segmento(s); traduza antes")
        seg_with_scenes = {s.segment_id for s in doc.scenes}
        orphan = [s.id for s in doc.segments if s.id not in seg_with_scenes]
        if orphan:
            raise BuildError(f"{len(orphan)} segmento(s) sem cenas; rode o storyboard de novo")
        if scenes_without_direction(doc):
            raise BuildError(DIRECTION_MISSING)

    def run(self) -> dict[str, Any]:
        self.validate()
        self.check_final_voice()
        doc = self.doc
        scenes = doc.ordered_scenes()
        n_seg = len(doc.segments) * len(self.langs)
        n_img = sum(1 for s in scenes if scene_needs_image(s))
        approx_chunks = max(1, len(scenes) // 6) * len(self.langs)
        self._total = n_seg * 2 + n_img + len(self.langs) * 2 + (approx_chunks + len(self.langs) * 2 if self.mode == "final" else 0)
        self.sink.progress(0.0, "Gerando narração e imagens")

        tts_out: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        images: dict[str, str | None] = {}
        errors: dict[str, list[str]] = {}  # mensagem → itens que falharam com ela
        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="tts") as tts_pool, ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="img"
        ) as img_pool:
            futures = {}
            for lang in self.langs:
                for seg in doc.segments:
                    futures[tts_pool.submit(self.narration, seg.id, lang)] = ("tts", seg.id, lang)
            for scene in scenes:
                futures[img_pool.submit(self.image, scene)] = ("image", scene.id, None)
            for future in as_completed(futures):
                # Confere antes de tudo: um item que falha não pode pular o cancelamento (senão os próximos
                # continuam rodando e gastando cota).
                if self.sink.cancelled():
                    for f in futures:
                        f.cancel()
                    raise BuildCancelled("cancelado pelo usuário")
                kind, ident, lang = futures[future]
                try:
                    value = future.result()
                except Exception as exc:  # noqa: BLE001 - junta os erros e falha no fim
                    errors.setdefault(f"{'narração' if kind == 'tts' else 'imagem'}: {exc}", []).append(
                        f"{ident}{f' ({lang})' if lang else ''}")
                    continue
                if kind == "tts":
                    tts_out[(ident, lang)] = value
                    self._tick(f"Narração pronta ({len(tts_out)}/{n_seg})")
                else:
                    images[ident] = value
                    if value is not None:
                        self._tick("Imagem pronta")
                if self.sink.cancelled():
                    for f in futures:
                        f.cancel()
                    raise BuildCancelled("cancelado pelo usuário")
        if errors:
            # A mesma falha em vários itens (ex.: cota acabou no meio) vira uma linha só.
            lines = [f"{message} ({items[0]}{f' e mais {len(items) - 1}' if len(items) > 1 else ''})"
                     for message, items in errors.items()]
            raise BuildError("falhas na geração:\n- " + "\n- ".join(lines[:20]))
        resolve_reused_images(doc, images)
        # LUT do estilo: a timeline mostra as imagens já gradadas (a thumbnail continua com as originais), inclusive
        # a foto do cartão de notícia (que usa a original da mídia, não a versão enquadrada da cena). As camadas do
        # parallax saem da imagem gradada, para a cor bater com as outras cenas.
        news_photos = [m.asset for s in scenes if s.kind == "image" and (m := s.visual.media) is not None
                       and m.kind == "news" and m.asset]
        timeline_images = self.lut_images(images, news_photos)
        layers = self.parallax(timeline_images)
        videos = video_clips.build_clips(self, images)  # só do cache: o build nunca gera vídeo IA (é pago)
        if videos and self.lut_graded:
            n = len(videos)
            self.warnings.append(f"LUT de cor não é aplicado nos clipes de vídeo IA ({n} {'cena' if n == 1 else 'cenas'}): "
                                 "a cor deles fica diferente das imagens.")
        if any((m := doc.shown_media(s)) is not None and m.editorial for s in scenes):
            self.warnings.append(EDITORIAL_WARNING)

        results: dict[str, Any] = {}
        extra: dict[str, Any] = {}
        if self.multi_track:
            results, extra = self._run_multi_track(tts_out, timeline_images, layers, videos)
        for lang in [] if self.multi_track else self.langs:
            self._check()
            narration: dict[str, NarrationClip] = {}
            hashes: dict[str, str] = {}
            for seg in doc.segments:
                h, out = tts_out[(seg.id, lang)]
                words = self.alignment(seg.id, lang, h, out)
                narration[seg.id] = NarrationClip(segment_id=seg.id, path=out["path"], duration=out["duration"], words=words)
                hashes[seg.id] = h
                self._tick("Alinhamento das palavras")
            timeline = self.timeline(lang, narration, timeline_images, layers, videos)
            self._lut_news_photos(timeline)
            self.warnings += timeline.pop("warnings", [])  # ex.: manchete do cartão de notícia sem tempo de leitura
            mix_rel = self.mix(lang, timeline, hashes)
            self._tick("Áudio mixado")
            entry = self._write_outputs(lang, timeline, mix_rel, timeline_to_srt(timeline))
            self._tick("Prévia pronta")
            if self.mode == "final":
                self._render_final(lang, timeline, mix_rel, images, entry)
            results[lang] = entry
        self.sink.progress(1.0, "Concluído")
        return {
            **extra,
            "mode": self.mode,
            "langs": results,
            "generated": self.stats.generated,
            "reused": self.stats.reused,
            "cost_usd": round(self.stats.cost_usd, 4),
            "preview_only_providers": sorted(self.stats.noncommercial),
            "warnings": list(dict.fromkeys(self.warnings)),
            "music": self.music_used,
            "artifacts": sorted(f"{kind}:{h}" for kind, h in self.store.touched),
        }

    @cached_property
    def video_presenter(self) -> Presenter | None:
        """O avatar do apresentador que vale nas cenas deste projeto (canal + projeto + estilo; None desligado)."""
        if not self.doc.channel_id:
            return None
        try:
            from studio.core.project import presenter as presenter_core

            return presenter_core.project_presenter(self.doc, self.style, purpose="video")
        except Exception as exc:  # noqa: BLE001 - sem avatar o vídeo sai como antes
            log.warning("avatar do apresentador indisponível no build: %s", exc)
            return None

    def timeline(self, lang: str, narration: dict[str, NarrationClip], images: dict[str, str | None],
                 layers: dict[str, list[str]] | None, videos: dict[str, dict[str, Any]] | None) -> dict[str, Any]:
        """A timeline do idioma. Com o avatar ligado, distribui o avatar pelas cenas com os tempos reais da narração
        (`workers.pipeline.presenter.assign`, numa cópia do documento: o manual vence, o automático é refeito) e
        monta a timeline com a camada do avatar. Sem avatar, é a timeline de sempre (mesmos hashes de bloco)."""
        presenter = self.video_presenter
        if presenter is None:
            return build_timeline(self.doc, self.style, lang, narration, images, layers, videos)
        from studio.workers.pipeline import presenter as presenter_scenes

        draft = build_timeline(self.doc, self.style, lang, narration, images, layers, videos)
        fps = float(draft["fps"])
        words: dict[str, list[Word]] = {}
        times: dict[str, tuple[float, float]] = {}
        for scene in draft["scenes"]:
            f0 = int(scene["from"])
            times[scene["id"]] = (f0 / fps, (f0 + int(scene["durationInFrames"])) / fps)
            words[scene["id"]] = [Word(w["text"], (f0 + w["from"]) / fps, (f0 + w["to"]) / fps)
                                  for w in scene.get("words") or []]
        doc = self.doc.model_copy(deep=True)
        vertical = self.style.format.height > self.style.format.width
        issues = presenter_scenes.assign(doc, presenter, words, scene_times=times, vertical=vertical)
        if issues:
            log.info("avatar: %s", "; ".join(i.message for i in issues))
        return build_timeline(doc, self.style, lang, narration, images, layers, videos, presenter=presenter)

    def _write_outputs(self, lang: str, timeline: dict[str, Any], mix_rel: str, srt: str) -> dict[str, Any]:
        """Grava a timeline e as legendas do idioma na pasta de saída e devolve a entrada do resultado.

        `timeline.json` e `legendas.srt` são da última prévia ou render (o player lê a timeline daí). No render final
        também sai uma cópia própria (`timeline-final.json`, `legendas-final.srt`) e a entrada aponta para ela: uma
        prévia depois do render (tradução editada, outro modo de entrega) regrava os arquivos comuns, e as legendas
        enviadas ao YouTube ou postas no pacote precisam bater com o MP4 e as faixas do último render final."""
        out_dir = self.outputs_dir(lang)
        timeline_doc = {**timeline, "mix": mix_rel, "version": self.version or service.get_project_row(self.project_id)["current_version"]}
        timeline_text = json.dumps(timeline_doc, ensure_ascii=False)
        (out_dir / "timeline.json").write_text(timeline_text, encoding="utf-8")
        srt_path = out_dir / "legendas.srt"
        srt_path.write_text(srt, encoding="utf-8")
        timeline_path = out_dir / "timeline.json"
        if self.mode == "final":
            timeline_path = out_dir / "timeline-final.json"
            timeline_path.write_text(timeline_text, encoding="utf-8")
            srt_path = out_dir / "legendas-final.srt"
            srt_path.write_text(srt, encoding="utf-8")
        return {
            "timeline": self.store.rel(timeline_path),
            "mix": mix_rel,
            "srt": self.store.rel(srt_path),
            "duration": timeline["duration"],
            "scenes": len(timeline["scenes"]),
            "chapters": timeline.get("chapters", []),
        }

    def _render_final(self, lang: str, timeline: dict[str, Any], mix_rel: str, images: dict[str, str | None],
                      entry: dict[str, Any]) -> None:
        """Renderiza os blocos, monta o MP4 e a thumbnail do idioma (modo "final")."""
        out_dir = self.outputs_dir(lang)
        chunk_paths = self.render_chunks(lang, timeline, lambda msg: self._tick(msg))
        final_rel = self.final_video(lang, timeline, chunk_paths, mix_rel)
        slug = "".join(ch if ch.isalnum() else "-" for ch in self.doc.title.lower())[:60].strip("-") or "video"
        target = out_dir / f"{slug}.mp4"
        shutil.copyfile(self.store.abs(final_rel), target)
        entry["video"] = self.store.rel(target)
        self._tick("Vídeo final montado")
        thumb_rel = self.thumbnail(lang, images)
        if thumb_rel:
            thumb_target = out_dir / "thumbnail.jpg"
            shutil.copyfile(self.store.abs(thumb_rel), thumb_target)
            entry["thumbnail"] = self.store.rel(thumb_target)
        self._tick("Thumbnail pronta")

    # ---------------------------------------------------------------------------------------
    # Modo B: várias faixas de idioma no mesmo vídeo (README §7.3)
    # ---------------------------------------------------------------------------------------
    def stretched(self, tts_hash: str, tts_out: dict[str, Any], tempo: float) -> tuple[str, str]:
        """Narração acelerada ou desacelerada por `tempo` (FFmpeg atempo), em cache: (caminho relativo, hash). Sem
        ajuste (`fit_segments` já arredonda para 1.0 o que o FFmpeg não aplicaria), devolve a própria narração."""
        if abs(tempo - 1.0) < dubbing.TEMPO_STEP:
            return tts_out["path"], tts_hash
        inputs = {"tts": tts_hash, "tempo": round(tempo, 3), "v": 1}
        h = hash_obj(inputs)
        cached = self.store.lookup("dub", h)
        if cached and self.store.abs(cached["path"]).is_file():
            self.stats.hit("dub", True)
            return cached["path"], h
        out = dubbing.stretch(self.store.abs(tts_out["path"]), self.store.dir_for("dub", h) / "voice.wav", tempo)
        rel = self.store.rel(out)
        self.store.save("dub", h, {"path": rel, "files": [rel], "tempo": round(tempo, 3)}, "ffmpeg")
        self.stats.hit("dub", False)
        return rel, h

    def _run_multi_track(self, tts_out: dict[tuple[str, str], tuple[str, dict[str, Any]]],
                         images: dict[str, str | None],
                         layers: dict[str, list[str]] | None = None,
                         videos: dict[str, dict[str, Any]] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Uma imagem (a do idioma principal) e uma faixa de áudio por idioma, todas com a mesma duração.

        O tempo de cada segmento é o da narração principal, esticado só quando outro idioma não cabe nem acelerado
        até `dubbing.MAX_SPEEDUP`. Cada idioma é mixado sobre a mesma timeline (mesma trilha e efeitos). No modo
        "final", só o idioma principal vira MP4; os outros viram WAV e entram no pacote para o YouTube Studio.
        """
        from studio.workers.pipeline.authoring import lang_name

        doc = self.doc
        primary = doc.primary_language
        others = [lang for lang in self.langs if lang != primary]
        seg_ids = [seg.id for seg in doc.segments]
        durations = {lang: {sid: float(tts_out[(sid, lang)][1]["duration"]) for sid in seg_ids} for lang in self.langs}
        fits = {f.segment_id: f for f in dubbing.fit_segments(seg_ids, durations, primary)}
        self.warnings += dubbing.fit_warnings(list(fits.values()), primary, lang_name)
        if any(s.overlay_text or s.card is not None for s in doc.scenes):
            self.warnings.append(
                f"Faixas de idioma no mesmo vídeo: os textos na tela ficam em {lang_name(primary)} em todas as faixas "
                "(as legendas não são queimadas: vão como legenda de cada idioma)."
            )
        gap = self.style.voice.gap_s

        # Idioma principal: a timeline do vídeo, com a vaga de cada segmento (a narração e silêncio no fim).
        clips: dict[str, NarrationClip] = {}
        hashes: dict[str, str] = {}
        for seg in doc.segments:
            h, out = tts_out[(seg.id, primary)]
            words = self.alignment(seg.id, primary, h, out)
            clips[seg.id] = NarrationClip(segment_id=seg.id, path=out["path"], duration=fits[seg.id].slot, words=words)
            hashes[seg.id] = h
            self._tick("Alinhamento das palavras")
        timeline = self.timeline(primary, clips, images, layers, videos)
        self._lut_news_photos(timeline)
        self.warnings += timeline.pop("warnings", [])
        starts = {n["segment_id"]: n["start"] for n in timeline["narration"]}
        placed = [
            (starts[seg.id], starts[seg.id] + fits[seg.id].slot + gap,
             normalize_words(clips[seg.id].words, float(tts_out[(seg.id, primary)][1]["duration"])))
            for seg in doc.segments
        ]
        final = self.mode == "final"
        mix_rel = self.mix(primary, timeline, hashes)
        self._tick("Áudio mixado")
        results: dict[str, Any] = {primary: self._write_outputs(primary, timeline, mix_rel, dubbing.narration_srt(placed))}
        self._tick("Prévia pronta")

        # Outros idiomas: a narração (acelerada quando precisa) nos mesmos inícios de segmento.
        for lang in others:
            self._check()
            narration = []
            dub_hashes: dict[str, str] = {}
            placed = []
            for seg in doc.segments:
                h, out = tts_out[(seg.id, lang)]
                fit = fits[seg.id]
                tempo = fit.tempos.get(lang, 1.0)
                words = normalize_words(self.alignment(seg.id, lang, h, out), float(out["duration"]))
                path, dh = self.stretched(h, out, tempo)
                narration.append({"segment_id": seg.id, "path": path, "start": starts[seg.id],
                                  "duration": round(float(out["duration"]) / tempo, 3)})
                dub_hashes[seg.id] = dh
                placed.append((starts[seg.id], starts[seg.id] + fit.slot + gap, dubbing.scale_words(words, tempo)))
                self._tick("Alinhamento das palavras")
            dub_timeline = {**timeline, "lang": lang, "narration": narration}
            dub_mix = self.mix(lang, dub_timeline, dub_hashes, wav=final)
            self._tick("Faixa de idioma mixada")
            entry = self._write_outputs(lang, dub_timeline, dub_mix, dubbing.narration_srt(placed))
            entry["dub_of"] = primary
            entry["max_tempo"] = max((fits[sid].tempos.get(lang, 1.0) for sid in seg_ids), default=1.0)
            entry["min_tempo"] = min((fits[sid].tempos.get(lang, 1.0) for sid in seg_ids), default=1.0)
            results[lang] = entry

        if final:
            self._render_final(primary, timeline, mix_rel, images, results[primary])
        summary = dubbing.fit_summary(list(fits.values()), primary)
        extra: dict[str, Any] = {"delivery": dubbing.MULTI_TRACK, "dub": summary}
        if final:
            folder = self.outputs_dir("faixas-youtube")
            try:
                manifest = dubbing.write_package(folder, doc=doc, entries=results, data_dir=self.settings.data_dir,
                                                 summary=summary, warnings=list(dict.fromkeys(self.warnings)),
                                                 label=lang_name)
            except OSError as exc:
                # O vídeo e as faixas já estão prontos: o pacote é só a cópia para o Studio e pode ser refeito na
                # aba Saída. Não perde o render por um arquivo aberto no player ou no editor.
                log.warning("pacote de faixas não gravado: %s", exc)
                self.warnings.append(f"{dubbing.PACKAGE_BUSY[0].upper()}{dubbing.PACKAGE_BUSY[1:]} (aba Saída).")
            else:
                extra["dub_package"] = {"folder": self.store.rel(folder),
                                        "checklist": self.store.rel(folder / "CHECKLIST.md"),
                                        "langs": manifest["langs"]}
        return results, extra


def music_ref_s(info: dict[str, Any]) -> float | None:
    """Trecho (s) onde o mix mede o ganho da trilha: com várias camas, só a 1ª (cada faixa fica no `volume_db` dela);
    com uma, None (o arquivo todo). Vai até o início da 2ª cama (antes do cruzamento)."""
    beds = info.get("beds") or []
    return float(beds[1]["start_s"]) if len(beds) > 1 else None


def resolve_reused_images(doc: ProjectDoc, images: dict[str, str | None]) -> None:
    """Cena que reaproveita mostra a imagem da raiz da cadeia; referência solta ou ciclo fica sem imagem (o
    renderer usa o fundo animado). Imagem fixada na própria cena vale mais que a da raiz."""
    for scene in doc.scenes:
        if scene.visual.reuse_of and not scene.visual.asset:
            root = doc.reuse_root(scene)
            images[scene.id] = images.get(root.id) if root else None


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def timeline_to_srt(timeline: dict[str, Any]) -> str:
    fps = timeline["fps"]
    entries = []
    for scene in timeline["scenes"]:
        for line in scene.get("captions", []):
            start = (scene["from"] + line["from"]) / fps
            end = (scene["from"] + line["to"]) / fps
            text = " ".join(w["text"] for w in line["words"]).strip()
            if text:
                entries.append((start, end, text))
    out = []
    for i, (start, end, text) in enumerate(entries, 1):
        out.append(f"{i}\n{_srt_time(start)} --> {_srt_time(max(end, start + 0.3))}\n{text}\n")
    return "\n".join(out)


def _spoken_text(tts_out: dict[str, Any]) -> str | None:
    """Texto falado que a voz recebeu ("dois mil e vinte e cinco" no lugar de "2025"), quando há mapa de volta."""
    return tts_out.get("spoken") if tts_out.get("spoken_map") is not None else None


def _bare_builder(project_id: str, mode: Mode, doc: ProjectDoc | None = None,
                  langs: list[str] | None = None) -> Builder:
    """Builder só para fazer contas (plano, chaves de cache): não grava nada nem chama provedores."""
    builder = Builder.__new__(Builder)
    builder.project_id = project_id
    builder.mode = mode
    builder.doc = doc or service.get_doc(project_id)
    builder.style = get_style(builder.doc.style_id, builder.doc.style_overrides)
    builder.langs = langs or list(builder.doc.languages)
    if dubbing.is_multi_track(builder.style, builder.doc) and builder.doc.primary_language not in builder.langs:
        # Modo B: o build sempre grava o idioma principal (referência do tempo), como em `Builder.__init__`.
        builder.langs = [builder.doc.primary_language, *builder.langs]
    builder.sink = ProgressSink()
    builder.store = ArtifactStore()
    builder.settings = get_settings()
    builder.stats = BuildStats()
    builder.warnings = []
    builder._pronunciation = None
    return builder


def current_artifact_keys(project_id: str, doc: ProjectDoc | None = None) -> set[tuple[str, str]]:
    """(tipo, hash) das narrações, alinhamentos e imagens que a versão atual do projeto (ou `doc`, ex.: a cabeça de
    um ramo A/B) usa, na prévia e no vídeo final, existam ou não no cache. A limpeza do cache protege essas chaves
    mesmo sem build salvo (ex.: render que gravou as narrações pagas e falhou depois). Só faz contas: não marca o
    último uso nem chama provedores."""
    keys: set[tuple[str, str]] = set()
    doc = doc or service.get_doc(project_id)
    modes: tuple[Mode, ...] = ("preview", "final")
    for mode in modes:
        builder = _bare_builder(project_id, mode, doc)
        for lang in builder.langs:
            for seg in doc.segments:
                inputs = builder.narration_inputs(seg.id, lang)
                tts_hash = hash_obj(inputs)
                keys.add(("tts", tts_hash))
                tts_out = builder.store.peek("tts", tts_hash)
                if tts_out is not None:
                    keys.add(("align", hash_obj(builder.align_inputs(seg.id, lang, tts_hash, tts_out))))
        for scene in doc.ordered_scenes():
            inputs = builder.image_inputs(scene)
            if inputs is not None:
                keys.update(("image", key) for key in builder.image_keys(inputs, scene))
    return keys


def plan(project_id: str, langs: list[str] | None = None, doc: ProjectDoc | None = None,
         mode: Mode = "final") -> dict[str, Any]:
    """Estimativa do que um build vai gerar: no modo "final", o que a renderização grava com a voz final; no
    "preview", o que a prévia refaz depois de uma edição (a narração da prévia sai da voz local, sem cota)."""
    builder = _bare_builder(project_id, mode, doc, langs)
    missing_tts = [{"segment_id": seg_id, "lang": lang, "chars": chars}
                   for seg_id, lang, chars in builder.narrations_to_record()]
    missing_images = []
    # Cenas dirigidas sem direção (estilo trocado): o build recusa até as cenas serem refeitas, então não entram.
    lost_ids = scenes_without_direction(builder.doc)
    lost = set(lost_ids)
    for scene in builder.doc.ordered_scenes():
        if scene.visual.asset or scene.id in lost:
            continue  # imagem fixada (ou upload): nada a gerar
        inputs = builder.image_inputs(scene)
        if inputs is None:
            continue
        # Imagem de teste (placeholder) é refeita quando há um gerador melhor: também entra na conta.
        _, cached = builder.cached_image(inputs, scene)
        if (not cached or builder._better_available(inputs["chain"], cached.get("provider"))
                or (inputs.get("references") and builder.references_pending(inputs["chain"], cached))):
            missing_images.append(scene.id)
    # Só a voz local quando todos os trechos que faltam saem dela (idioma sem voz local usa a voz final).
    langs = {m["lang"] for m in missing_tts} or set(builder.langs)
    local = all(builder.local_preview(lang) for lang in langs)
    if local:
        tts_provider = _first_available(registry.resolve_chain("tts", registry.PREVIEW_TTS_CHAIN))
    else:
        tts_chain = registry.resolve_chain("tts", builder.style.voice.chain)
        tts_provider = _first_available(builder.final_chain(tts_chain) if builder.final_voice_only() else tts_chain)
    img_provider = _first_available(registry.resolve_chain("image", builder.style.art.image_chain))
    img_price = getattr(registry.get(img_provider), "price", 0.0) if img_provider else 0.0
    est = len(missing_images) * img_price
    tts_seconds = sum(m["chars"] for m in missing_tts) / 15 * 0.5 if local else len(missing_tts) * 6
    result: dict[str, Any] = {
        "mode": mode,
        "tts_missing": missing_tts,
        "tts_chars": sum(m["chars"] for m in missing_tts),
        "tts_local": local,
        "images_missing": missing_images,
        "direction_missing": lost_ids,
        "tts_provider": tts_provider,
        "tts_voice": builder.style.voice.voice,
        "image_provider": img_provider,
        "estimated_cost_usd": round(est, 4),
        "estimated_seconds": round(tts_seconds + len(missing_images) * _IMAGE_SECONDS.get(img_provider, 12)),
    }
    # Créditos da voz final pelo `credits_per_char` do canal (ADR 0002); None quando não se aplica.
    from studio.workers.ops.estimate import credits_estimate

    result["credits_estimate"] = credits_estimate(project_id, result["tts_chars"], local, tts_provider)
    # Mídia real (só quando há: o plano de um projeto sem mídia fica igual): cenas que mostram a mídia em vez de
    # gerar imagem, e as que perderam o arquivo.
    media_scenes = [s for s in builder.doc.ordered_scenes() if s.visual.media is not None and s.visual.media.asset]
    if media_scenes:
        result["media_scenes"] = [s.id for s in media_scenes]
    media_missing = [s.id for s in media_scenes if not builder.store.abs(s.visual.media.asset).is_file()]  # type: ignore[union-attr,arg-type]
    if media_missing:
        result["media_missing"] = media_missing
    # Parallax 2.5D (só quando alguma cena usa): cenas e o download do modelo de profundidade, se faltar.
    parallax_scenes = [s.id for s in builder.doc.ordered_scenes()
                       if (scene_needs_image(s) or s.visual.asset or s.visual.media is not None or s.visual.reuse_of)
                       and s.kind not in NO_VISUAL_LAYER_KINDS
                       and scene_motion(builder.doc, builder.style, s) == PARALLAX]
    if parallax_scenes:
        result["parallax_scenes"] = parallax_scenes
        depth_ok, depth_reason = depth_mod.availability()
        if not depth_ok:
            # Nada vai ser baixado: as cenas sem camadas no cache ficam com o Ken Burns.
            result["parallax_unavailable"] = depth_reason
        elif not depth_mod.model_ready():
            result["depth_model_mb"] = depth_mod.MODEL_MB
    # Vídeo IA (só quando há cena marcada): a prévia e a renderização não geram clipe; aqui só o aviso do que falta.
    marked = video_clips.video_scenes(builder.doc)
    if marked:
        missing_clips = [s.id for s in marked if video_clips.blocked_reason(builder.doc, s) is None
                         and video_clips.cached_clip(builder, s, video_clips.known_image(builder, s)) is None]
        result["video_scenes"] = [s.id for s in marked]
        if missing_clips:
            result["video_missing"] = missing_clips
    return result


def _first_available(chain: list[str]) -> str | None:
    for provider_id in chain:
        ok, _ = registry.get(provider_id).availability()
        if ok and not registry.quota_exhausted_today(provider_id):
            return provider_id
    return None
