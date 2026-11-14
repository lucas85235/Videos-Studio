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
from studio.core.hashing import hash_obj, seed_from
from studio.core.project import service
from studio.core.project.document import ProjectDoc, Scene
from studio.core.styles.library import get_style
from studio.core.styles.schema import StyleSpec
from studio.core.timeline import NarrationClip, build_timeline, chunk_timeline
from studio.media import audio as audio_mod
from studio.media.render import get_render_client, renderer_version
from studio.media.thumbnail import make_thumbnail
from studio.media.video import assemble
from studio.providers import registry
from studio.providers.align import map_to_script, word_error_rate
from studio.providers.base import Word

log = logging.getLogger(__name__)

Mode = Literal["preview", "final"]

_codex_lock = threading.Lock()


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


def compose_image_prompt(style: StyleSpec, prompt: str) -> str:
    banned = ", ".join(style.art.banned)
    return (
        f"{prompt.strip().rstrip('.')}. Estilo visual: {style.art.bible}. "
        f"Sem nenhum texto, letra, número, legenda ou marca d'água. Evitar: {banned}."
    )


def scene_needs_image(scene: Scene) -> bool:
    visual = scene.visual
    if visual.type in ("none", "upload"):
        return False
    if scene.kind == "text" and not visual.prompt:
        return False
    return bool(visual.prompt or visual.query)


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

    def _usage(self, result: Any) -> tuple[float, str, float]:
        return result.usage.units, result.usage.unit_name, result.usage.cost_usd

    def outputs_dir(self, lang: str) -> Path:
        path = self.settings.outputs_dir / self.project_id / lang
        path.mkdir(parents=True, exist_ok=True)
        return path

    # ---------------------------------------------------------------------------------------
    # nós
    # ---------------------------------------------------------------------------------------
    def narration_inputs(self, seg_id: str, lang: str) -> dict[str, Any]:
        seg = self.doc.segment(seg_id)
        text = seg.text_for(lang)
        speaker = self.style.script.speakers.get(seg.speaker)
        voice = (speaker.voice if speaker and speaker.voice else None) or self.style.voice.voice
        style_prompt = (speaker.style_prompt if speaker and speaker.style_prompt else None) or self.style.voice.style_prompt
        return {
            "text": text,
            "lang": lang,
            "voice": voice,
            "style_prompt": style_prompt,
            "speed": self.style.voice.speed,
            "chain": registry.resolve_chain("tts", self.style.voice.chain),
        }

    def narration(self, seg_id: str, lang: str) -> tuple[str, dict[str, Any]]:
        inputs = self.narration_inputs(seg_id, lang)
        if not inputs["text"].strip():
            raise BuildError(f"o segmento {seg_id} não tem texto em {lang} (traduza antes)")
        h = hash_obj(inputs)
        cached = self.store.lookup("tts", h)
        if cached and not self._better_available(inputs["chain"], cached.get("provider")):
            self.stats.hit("tts", True)
            if not cached.get("commercial", True):
                self.stats.noncommercial.add(cached.get("provider", "?"))
            return h, cached
        out_dir = self.store.dir_for("tts", h)

        def synthesize(target: Path):
            return registry.run_chain(
                "tts",
                inputs["chain"],
                lambda p: p.synthesize(text=inputs["text"], lang=lang, voice=inputs["voice"], style_prompt=inputs["style_prompt"],
                                       speed=inputs["speed"], out_dir=target),
                usage_of=self._usage,
                project_id=self.project_id,
                job_id=self.sink.job_id,
            )

        result, provider, _ = synthesize(out_dir)
        qa_spec = self.style.voice.qa
        qa = self._qa(inputs["text"], lang, result.path)
        attempts = 1
        if qa and qa["wer"] > qa_spec.max_wer and not provider.info.deterministic:
            best = (qa["wer"], result, provider, qa)
            for retry in range(qa_spec.retries):
                self._check()
                again, again_provider, _ = synthesize(out_dir / f"tentativa{retry + 2}")
                again_qa = self._qa(inputs["text"], lang, again.path)
                attempts += 1
                if again_qa and again_qa["wer"] < best[0]:
                    best = (again_qa["wer"], again, again_provider, again_qa)
                if best[0] <= qa_spec.max_wer:
                    break
            _, result, provider, qa = best
        if qa and qa["wer"] > qa_spec.max_wer:
            self.warnings.append(
                f"Narração de {seg_id} ({lang}) pode ter erros: {qa['wer']:.0%} das palavras diferem do roteiro."
            )
        output = {
            "path": self.store.rel(result.path),
            "duration": round(result.duration_s, 4),
            "words": [w.to_dict() for w in result.words] if result.words else None,
            "heard": qa["heard"] if qa else None,
            "qa": {"wer": round(qa["wer"], 4), "attempts": attempts, "ok": qa["wer"] <= qa_spec.max_wer} if qa else None,
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
        return {"wer": word_error_rate(text, [w.text for w in heard]), "heard": [w.to_dict() for w in heard]}

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

    def image(self, scene: Scene) -> str | None:
        if scene.visual.type == "upload" and scene.visual.asset:
            return scene.visual.asset
        inputs = self.image_inputs(scene)
        if inputs is None:
            return None
        h = hash_obj(inputs)
        cached = self.store.lookup("image", h)
        if cached and not self._better_available(inputs["chain"], cached.get("provider")):
            self.stats.hit("image", True)
            if not cached.get("commercial", True):
                self.stats.noncommercial.add(cached.get("provider", "?"))
            return cached["path"]
        out_dir = self.store.dir_for("image", h)

        def call(provider):
            if provider.info.id == "codex_cli":
                with _codex_lock:
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

    def music(self, duration: float) -> tuple[str, dict[str, Any]] | None:
        music_spec = self.style.audio.music
        cue = next((c for c in self.doc.music if c.enabled), None)
        if music_spec.source == "none" or cue is None:
            return None
        mood = cue.mood or music_spec.mood
        bucket = float(int(duration // 5 + 1) * 5)
        inputs = {"mood": audio_mod.mood_key(mood), "duration": bucket, "seed": seed_from(self.doc.seed, "music", mood), "v": 1}
        h = hash_obj(inputs)
        cached = self.store.lookup("music", h)
        volume = cue.volume_db if cue.volume_db is not None else music_spec.volume_db
        if cached:
            self.stats.hit("music", True)
            return h, {**cached, "volume_db": volume}
        path = audio_mod.generate_music(mood, bucket, inputs["seed"], self.store.dir_for("music", h) / "music.wav")
        output = {"path": self.store.rel(path), "files": [self.store.rel(path)], "mood": inputs["mood"]}
        self.store.save("music", h, output, "procedural")
        self.stats.hit("music", False)
        return h, {**output, "volume_db": volume}

    def sfx_file(self, kind: str) -> Path:
        h = hash_obj({"sfx": kind, "v": 1})
        cached = self.store.lookup("sfx", h)
        if cached:
            return self.store.abs(cached["path"])
        path = audio_mod.generate_sfx(kind, self.store.dir_for("sfx", h) / f"{kind}.wav")
        self.store.save("sfx", h, {"path": self.store.rel(path), "files": [self.store.rel(path)]}, "procedural")
        return path

    def mix(self, lang: str, timeline: dict[str, Any], tts_hashes: dict[str, str]) -> str:
        total = timeline["duration"]
        music = self.music(total)
        sfx_events = timeline.get("sfx", [])
        inputs = {
            "voice": [(tts_hashes[n["segment_id"]], n["start"]) for n in timeline["narration"]],
            "music": (music[0], music[1]["volume_db"], self.style.audio.music.ducking) if music else None,
            "sfx": [(e["type"], e["start"]) for e in sfx_events],
            "sfx_db": self.style.audio.sfx.volume_db,
            "loudness": self.style.audio.loudness_lufs,
            "total": total,
            "v": 1,
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

    def thumbnail(self, lang: str, images: dict[str, str | None]) -> str | None:
        doc, style = self.doc, self.style
        base_scene = doc.thumbnail.scene_id or next((s.id for s in doc.ordered_scenes() if images.get(s.id)), None)
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

    def run(self) -> dict[str, Any]:
        self.validate()
        doc = self.doc
        scenes = doc.ordered_scenes()
        n_seg = len(doc.segments) * len(self.langs)
        n_img = sum(1 for s in scenes if scene_needs_image(s))
        approx_chunks = max(1, len(scenes) // 6) * len(self.langs)
        self._total = n_seg * 2 + n_img + len(self.langs) * 2 + (approx_chunks + len(self.langs) * 2 if self.mode == "final" else 0)
        self.sink.progress(0.0, "Gerando narração e imagens")

        tts_out: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        images: dict[str, str | None] = {}
        errors: list[str] = []
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
                kind, ident, lang = futures[future]
                try:
                    value = future.result()
                except Exception as exc:  # noqa: BLE001 - junta os erros e falha no fim
                    errors.append(f"{kind} {ident}{f' ({lang})' if lang else ''}: {exc}")
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
            raise BuildError("falhas na geração:\n- " + "\n- ".join(errors[:20]))

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
            timeline = build_timeline(doc, self.style, lang, clips, images)
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
            "warnings": self.warnings,
        }


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


def plan(project_id: str, langs: list[str] | None = None, doc: ProjectDoc | None = None) -> dict[str, Any]:
    """Estimativa do que um build vai gerar (para a prévia de custo das edições)."""
    builder = Builder.__new__(Builder)
    builder.project_id = project_id
    builder.mode = "final"
    builder.doc = doc or service.get_doc(project_id)
    builder.style = get_style(builder.doc.style_id, builder.doc.style_overrides)
    builder.langs = langs or list(builder.doc.languages)
    builder.sink = ProgressSink()
    builder.store = ArtifactStore()
    builder.settings = get_settings()
    builder.stats = BuildStats()
    builder.warnings = []
    missing_tts: list[dict[str, Any]] = []
    for lang in builder.langs:
        for seg in builder.doc.segments:
            inputs = builder.narration_inputs(seg.id, lang)
            if not builder.store.lookup("tts", hash_obj(inputs)):
                missing_tts.append({"segment_id": seg.id, "lang": lang, "chars": len(inputs["text"])})
    missing_images = []
    for scene in builder.doc.ordered_scenes():
        inputs = builder.image_inputs(scene)
        if inputs and not builder.store.lookup("image", hash_obj(inputs)):
            missing_images.append(scene.id)
    tts_provider = _first_available(registry.resolve_chain("tts", builder.style.voice.chain))
    img_provider = _first_available(registry.resolve_chain("image", builder.style.art.image_chain))
    img_price = getattr(registry.get(img_provider), "price", 0.0) if img_provider else 0.0
    est = len(missing_images) * img_price
    return {
        "tts_missing": missing_tts,
        "tts_chars": sum(m["chars"] for m in missing_tts),
        "images_missing": missing_images,
        "tts_provider": tts_provider,
        "image_provider": img_provider,
        "estimated_cost_usd": round(est, 4),
        "estimated_seconds": len(missing_tts) * 6 + len(missing_images) * (40 if img_provider == "codex_cli" else 12),
    }


def _first_available(chain: list[str]) -> str | None:
    for provider_id in chain:
        ok, _ = registry.get(provider_id).availability()
        if ok and not registry.quota_exhausted_today(provider_id):
            return provider_id
    return None
