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
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from studio.config import get_settings
from studio.core.graph.store import ArtifactStore
from studio.core.hashing import hash_file, hash_obj, seed_from
from studio.core.project import service
from studio.core.project.document import ArtDirection, ProjectDoc, Scene, Visual
from studio.core.project.media_edit import scene_label
from studio.core.styles.library import get_style
from studio.core.styles.schema import StyleSpec
from studio.core.styles.visual import composition_text, language_prefix, rotate_variant, shot_text
from studio.core.timeline import NarrationClip, build_timeline, chunk_timeline
from studio.media import audio as audio_mod
from studio.media import lut as lut_mod
from studio.media import music_library
from studio.media.real_media import EDITORIAL_WARNING, anchor_for, fit_media, resolve_fit
from studio.media.render import get_render_client, renderer_version
from studio.media.thumbnail import make_thumbnail
from studio.media.video import assemble
from studio.providers import kokoro, registry
from studio.providers.align import map_to_script, narration_problems, word_error_rate
from studio.providers.base import Word
from studio.workers.pipeline.lighting import names_light, strip_light

log = logging.getLogger(__name__)

Mode = Literal["preview", "final"]

_codex_lock = threading.Lock()
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


class Builder:
    def __init__(self, project_id: str, *, mode: Mode = "preview", langs: list[str] | None = None,
                 version: int | None = None, sink: ProgressSink | None = None) -> None:
        self.project_id = project_id
        self.mode = mode
        self.doc: ProjectDoc = service.get_doc(project_id, version)
        self.style: StyleSpec = get_style(self.doc.style_id, self.doc.style_overrides)
        self.langs = langs or list(self.doc.languages)
        self.sink = sink or ProgressSink()
        self.store = ArtifactStore()
        self.settings = get_settings()
        self.stats = BuildStats()
        self.warnings: list[str] = []
        self.music_used: dict[str, Any] | None = None
        self._done = 0
        self._total = 1
        self._progress_lock = threading.Lock()

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
        path = self.settings.outputs_dir / self.project_id / lang
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

    def narration_inputs(self, seg_id: str, lang: str) -> dict[str, Any]:
        seg = self.doc.segment(seg_id)
        text = seg.text_for(lang)
        speaker = self.style.script.speakers.get(seg.speaker)
        voice = (speaker.voice if speaker and speaker.voice else None) or self.style.voice.voice
        style_prompt = (speaker.style_prompt if speaker and speaker.style_prompt else None) or self.style.voice.style_prompt
        if self.local_preview(lang):
            # Voz local: sem instrução de estilo (não há o que interpretar nem o que vazar no áudio).
            return {
                "text": text,
                "lang": lang,
                "voice": self.preview_voice(lang, voice),
                "style_prompt": None,
                "speed": self.style.voice.speed,
                "chain": registry.resolve_chain("tts", registry.PREVIEW_TTS_CHAIN),
                "v": 2,
            }
        return {
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

    def narration(self, seg_id: str, lang: str) -> tuple[str, dict[str, Any]]:
        inputs = self.narration_inputs(seg_id, lang)
        if not inputs["text"].strip():
            raise BuildError(f"o segmento {seg_id} não tem texto em {lang} (traduza antes)")
        h = hash_obj(inputs)
        cached = self.store.lookup("tts", h)
        final = self.final_voice_only()
        if cached and final and not cached.get("commercial", True):
            cached = None  # gravação com voz de teste (ex.: cota esgotada na prévia) não vai para o vídeo final
        if cached and not self._better_available(inputs["chain"], cached.get("provider")):
            self.stats.hit("tts", True)
            if not cached.get("commercial", True):
                self.stats.noncommercial.add(cached.get("provider", "?"))
            problems = self._recheck_wer(cached, inputs["text"], lang)
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

        def take(target: Path, style_prompt: str | None) -> dict[str, Any]:
            result, provider, _ = registry.run_chain(
                "tts",
                chain,
                lambda p: p.synthesize(text=inputs["text"], lang=lang, voice=inputs["voice"], style_prompt=style_prompt,
                                       speed=inputs["speed"], out_dir=target),
                usage_of=self._usage,
                project_id=self.project_id,
                job_id=self.sink.job_id,
            )
            qa = self._qa(inputs["text"], lang, result.path)
            heard = [w["text"] for w in qa["heard"]] if qa else None
            problems = narration_problems(inputs["text"], heard, result.duration_s, style_prompt=inputs["style_prompt"],
                                          words_per_minute=wpm, speed=inputs["speed"] or 1.0)
            if qa and qa["wer"] > qa_spec.max_wer and not problems:
                problems.append(f"{qa['wer']:.0%} das palavras diferem do roteiro")
            return {"result": result, "provider": provider, "qa": qa, "problems": problems}

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
        self.store.save("tts", h, output, provider.info.id, result.usage.cost_usd)
        self.stats.hit("tts", False, result.usage.cost_usd)
        if not provider.info.commercial_use:
            self.stats.noncommercial.add(provider.info.id)
        return h, output

    def _recheck_wer(self, cached: dict[str, Any], text: str, lang: str) -> list[str]:
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
        wer = word_error_rate(text, [w["text"] for w in heard], lang)
        if wer > self.style.voice.qa.max_wer:
            problems.append(f"{wer:.0%} {suffix}")
        return problems

    def _qa(self, text: str, lang: str, audio_path: Path) -> dict[str, Any] | None:
        """Transcreve a narração e mede o erro contra o roteiro (só com o Whisper local instalado)."""
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
        return {"wer": word_error_rate(text, [w.text for w in heard], lang), "heard": [w.to_dict() for w in heard]}

    def alignment(self, seg_id: str, lang: str, tts_hash: str, tts_out: dict[str, Any]) -> list[Word]:
        text = self.doc.segment(seg_id).text_for(lang)
        chain = registry.resolve_chain("align", None)
        inputs = {"tts": tts_hash, "text": text, "chain": chain, "from_tts": bool(tts_out.get("words")),
                  "heard": bool(tts_out.get("heard")), "v": 2}
        h = hash_obj(inputs)
        cached = self.store.lookup("align", h)
        if cached and not (not tts_out.get("words") and not tts_out.get("heard") and self._better_available(chain, cached.get("provider"))):
            self.stats.hit("align", True)
            return [Word.from_dict(w) for w in cached["words"]]
        audio_path = self.store.abs(tts_out["path"])
        if tts_out.get("words"):
            words = map_to_script(text, [Word.from_dict(w) for w in tts_out["words"]], audio_path)
            provider_id = "tts"
        elif tts_out.get("heard"):
            words = map_to_script(text, [Word.from_dict(w) for w in tts_out["heard"]], audio_path)
            provider_id = "faster_whisper"
        else:
            words, provider, _ = registry.run_chain(
                "align", chain, lambda p: p.align(audio_path=audio_path, text=text, lang=lang),
                project_id=self.project_id, job_id=self.sink.job_id,
            )
            provider_id = provider.info.id
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
                prompt = compose_directed_prompt(self.style, self.doc.art, visual)
            else:
                # Cena antiga (sem linguagem ou sem direção): o prompt de sempre, com a mesma chave de cache.
                prompt = compose_image_prompt(self.style, visual.prompt)
        return {
            "prompt": prompt,
            "width": self.style.format.width,
            "height": self.style.format.height,
            "seed": seed_from(visual.seed, visual.variation),
            "variation": visual.variation,
            "type": visual.type,
            "chain": chain,
        }

    def cached_image(self, inputs: dict[str, Any],
                     scene: Scene | None = None) -> tuple[str, dict[str, Any] | None]:
        """Chave da imagem e o que já existe no cache (também com a chave antiga). Entre as guardadas, prefere a
        que pode ir para o canal: uma imagem de teste guardada depois não esconde a imagem boa. A cena dirigida
        também acha a imagem gerada com o prompt de antes (`legacy_directed_prompt`); a imagem nova vai na chave nova."""
        h = image_key(inputs)
        variants = [inputs]
        visual = scene.visual if scene is not None else None
        if visual is not None and visual.type != "stock" and visual.language and self.doc.art is not None:
            old = legacy_directed_prompt(self.style, self.doc.art, visual)
            if old != inputs["prompt"]:
                variants.append({**inputs, "prompt": old})
        keys = [k for v in variants for k in (
            image_key(v), *(hash_obj({**v, "chain": chain}) for chain in (inputs["chain"], *_LEGACY_IMAGE_CHAINS)))]
        found = None
        for key in dict.fromkeys(keys):
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
        if cached and not self._better_available(inputs["chain"], cached.get("provider")):
            self.stats.hit("image", True)
            if not cached.get("commercial", True):
                self.stats.noncommercial.add(cached.get("provider", "?"))
            return cached["path"]
        out_dir = self.store.dir_for("image", h)

        def call(provider):
            self._check()
            if provider.info.id == "codex_cli":
                with _codex_lock:
                    self._check()  # cancelado enquanto esperava a vez do Codex: não começa outra imagem
                    return provider.generate(prompt=inputs["prompt"], width=inputs["width"], height=inputs["height"],
                                             seed=inputs["seed"], out_dir=out_dir)
            return provider.generate(prompt=inputs["prompt"], width=inputs["width"], height=inputs["height"],
                                     seed=inputs["seed"], out_dir=out_dir)

        result, provider, _ = registry.run_chain("image", inputs["chain"], call, usage_of=self._usage,
                                                 project_id=self.project_id, job_id=self.sink.job_id)
        rel = self.store.rel(result.path)
        output = {"path": rel, "provider": provider.info.id, "model": result.model, "commercial": provider.info.commercial_use,
                  "license": result.meta.get("license"), "files": [rel]}
        self.store.save("image", h, output, provider.info.id, result.usage.cost_usd)
        self.stats.hit("image", False, result.usage.cost_usd)
        if not provider.info.commercial_use:
            self.stats.noncommercial.add(provider.info.id)
        return rel

    def lut_images(self, images: dict[str, str | None]) -> dict[str, str | None]:
        """Imagens das cenas com o LUT do estilo aplicado (efeito "lut"), em cache por imagem + LUT + intensidade.
        Sem LUT, devolve as mesmas. LUT que sumiu da biblioteca vira aviso e o vídeo sai sem ele."""
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

        todo = sorted({rel for rel in images.values()
                       if rel and rel.lower().endswith(lut_mod.IMAGE_EXTS) and self.store.abs(rel).is_file()})
        with ThreadPoolExecutor(max_workers=4) as pool:
            for rel, future in [(rel, pool.submit(grade, rel)) for rel in todo]:
                self._check()
                try:
                    done[rel] = future.result()
                except Exception as exc:  # noqa: BLE001 - imagem ilegível fica sem LUT, o vídeo segue
                    log.warning("LUT não aplicado em %s: %s", rel, exc)
                    self.warnings.append(f"LUT de cor não aplicado em uma imagem: {exc}")
        if todo:
            self._tick("Cor (LUT) aplicada")
        return {sid: done.get(rel, rel) if rel else rel for sid, rel in images.items()}

    def music(self, duration: float) -> tuple[str, dict[str, Any]] | None:
        music_spec = self.style.audio.music
        cue = next((c for c in self.doc.music if c.enabled), None)
        if music_spec.source == "none" or cue is None:
            return None
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

    def _library_music(self, mood: str, bucket: float, pinned: str | None) -> tuple[str, dict[str, Any]] | None:
        """Trilha da biblioteca do usuário (library/music), ajustada à duração. None = biblioteca vazia."""
        tracks = music_library.list_tracks()
        if pinned and not any(t.path == pinned for t in tracks):
            self.warnings.append(f"A faixa fixada '{pinned}' não está mais na biblioteca; escolhi outra.")
        track = music_library.pick_track(tracks, mood, seed_from(self.doc.seed, "music", mood), pinned)
        src = music_library.resolve(track.path) if track else None
        if track is None or src is None:
            self.warnings.append(
                "A biblioteca de músicas está vazia; usei a trilha procedural. "
                f"Coloque arquivos de áudio em {music_library.music_root()}."
            )
            return None
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

    def sfx_file(self, kind: str) -> Path:
        h = hash_obj({"sfx": kind, "v": 2})  # v2: impacto em camadas e loudness normalizada por arquivo
        cached = self.store.lookup("sfx", h)
        if cached:
            return self.store.abs(cached["path"])
        path = audio_mod.generate_sfx(kind, self.store.dir_for("sfx", h) / f"{kind}.wav")
        self.store.save("sfx", h, {"path": self.store.rel(path), "files": [self.store.rel(path)]}, "procedural")
        return path

    def mix(self, lang: str, timeline: dict[str, Any], tts_hashes: dict[str, str]) -> str:
        total = timeline["duration"]
        music = self.music(total)
        self.music_used = (
            {"source": music[1].get("source", "procedural"), "track": music[1].get("track"), "mood": music[1].get("mood")}
            if music
            else None
        )
        sfx_events = timeline.get("sfx", [])
        short = self.style.format.type == "short"
        music_hp = audio_mod.music_highpass(music[1].get("mood")) if music else None
        # v3: trilha medida em mono depois do passa-altas; o mix dura exatamente o vídeo e termina num fade curto (v2:
        # passa-altas e ducking leve, fades contra o fim real, SFX normalizados e master com ganho medido).
        inputs = {
            "voice": [(tts_hashes[n["segment_id"]], n["start"]) for n in timeline["narration"]],
            "music": (music[0], music[1]["volume_db"], self.style.audio.music.ducking, music_hp) if music else None,
            "sfx": [(e["type"], e["start"]) for e in sfx_events],
            "sfx_db": self.style.audio.sfx.volume_db,
            "loudness": self.style.audio.loudness_lufs,
            "total": total,
            "short": short,
            "v": 3,
        }
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
            gain = 10 ** (self.style.audio.sfx.volume_db / 20)
            sfx_track = audio_mod.place_clips([(self.sfx_file(e["type"]), e["start"], gain) for e in sfx_events], total,
                                              out_dir / "sfx.wav")
        mixed = audio_mod.mix_tracks(
            voice,
            out_dir / "mix.m4a",
            music=self.store.abs(music[1]["path"]) if music else None,
            music_db=music[1]["volume_db"] if music else -22,
            ducking=self.style.audio.music.ducking,
            sfx=sfx_track,
            loudness=self.style.audio.loudness_lufs,
            total=total,
            short=short,
            music_highpass=music_hp or audio_mod.MUSIC_HIGHPASS_HZ,
        )
        for tmp in (voice, sfx_track):
            if tmp is not None:
                tmp.unlink(missing_ok=True)
        rel = self.store.rel(mixed)
        self.store.save("mix", h, {"path": rel, "files": [rel]}, "ffmpeg")
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

    def thumbnail(self, lang: str, images: dict[str, str | None]) -> str | None:
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
        if any((m := doc.shown_media(s)) is not None and m.editorial for s in scenes):
            self.warnings.append(EDITORIAL_WARNING)

        # LUT do estilo: a timeline mostra as imagens já gradadas (a thumbnail continua com as originais).
        timeline_images = self.lut_images(images)

        results: dict[str, Any] = {}
        for lang in self.langs:
            self._check()
            clips: dict[str, NarrationClip] = {}
            hashes: dict[str, str] = {}
            for seg in doc.segments:
                h, out = tts_out[(seg.id, lang)]
                words = self.alignment(seg.id, lang, h, out)
                clips[seg.id] = NarrationClip(segment_id=seg.id, path=out["path"], duration=out["duration"], words=words)
                hashes[seg.id] = h
                self._tick("Alinhamento das palavras")
            timeline = build_timeline(doc, self.style, lang, clips, timeline_images)
            self.warnings += timeline.pop("warnings", [])  # ex.: manchete do cartão de notícia sem tempo de leitura
            mix_rel = self.mix(lang, timeline, hashes)
            self._tick("Áudio mixado")
            out_dir = self.outputs_dir(lang)
            timeline_doc = {**timeline, "mix": mix_rel, "version": service.get_project_row(self.project_id)["current_version"]}
            (out_dir / "timeline.json").write_text(json.dumps(timeline_doc, ensure_ascii=False), encoding="utf-8")
            srt_path = out_dir / "legendas.srt"
            srt_path.write_text(timeline_to_srt(timeline), encoding="utf-8")
            entry: dict[str, Any] = {
                "timeline": self.store.rel(out_dir / "timeline.json"),
                "mix": mix_rel,
                "srt": self.store.rel(srt_path),
                "duration": timeline["duration"],
                "scenes": len(timeline["scenes"]),
                "chapters": timeline.get("chapters", []),
            }
            self._tick("Prévia pronta")
            if self.mode == "final":
                chunk_paths = self.render_chunks(lang, timeline, lambda msg: self._tick(msg))
                final_rel = self.final_video(lang, timeline, chunk_paths, mix_rel)
                slug = "".join(ch if ch.isalnum() else "-" for ch in doc.title.lower())[:60].strip("-") or "video"
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
            results[lang] = entry
        self.sink.progress(1.0, "Concluído")
        return {
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


def plan(project_id: str, langs: list[str] | None = None, doc: ProjectDoc | None = None,
         mode: Mode = "final") -> dict[str, Any]:
    """Estimativa do que um build vai gerar: no modo "final", o que a renderização grava com a voz final; no
    "preview", o que a prévia refaz depois de uma edição (a narração da prévia sai da voz local, sem cota)."""
    builder = Builder.__new__(Builder)
    builder.project_id = project_id
    builder.mode = mode
    builder.doc = doc or service.get_doc(project_id)
    builder.style = get_style(builder.doc.style_id, builder.doc.style_overrides)
    builder.langs = langs or list(builder.doc.languages)
    builder.sink = ProgressSink()
    builder.store = ArtifactStore()
    builder.settings = get_settings()
    builder.stats = BuildStats()
    builder.warnings = []
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
        if not cached or builder._better_available(inputs["chain"], cached.get("provider")):
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
        "estimated_seconds": round(tts_seconds + len(missing_images) * (40 if img_provider == "codex_cli" else 12)),
    }
    # Mídia real (só quando há: o plano de um projeto sem mídia fica igual): cenas que mostram a mídia em vez de
    # gerar imagem, e as que perderam o arquivo.
    media_scenes = [s for s in builder.doc.ordered_scenes() if s.visual.media is not None and s.visual.media.asset]
    if media_scenes:
        result["media_scenes"] = [s.id for s in media_scenes]
    media_missing = [s.id for s in media_scenes if not builder.store.abs(s.visual.media.asset).is_file()]  # type: ignore[union-attr,arg-type]
    if media_missing:
        result["media_missing"] = media_missing
    return result


def _first_available(chain: list[str]) -> str | None:
    for provider_id in chain:
        ok, _ = registry.get(provider_id).availability()
        if ok and not registry.quota_exhausted_today(provider_id):
            return provider_id
    return None
