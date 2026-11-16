"""Vídeo IA nas cenas-chave (README §8.5): marcar cenas, sugerir, estimar o custo e gerar os clipes.

Regras:
- a cena-chave é uma cena com `kind == "video"`; a imagem dela (gerada como sempre) é o primeiro quadro do clipe;
- no máximo `max_video_scenes(style)` cenas por vídeo (vídeo IA é pago);
- a prévia e a renderização **nunca** geram clipe: só usam os que já estão no cache. Gerar é um job à parte
  ("video_clips"), que só roda com o custo confirmado pelo usuário e para antes de passar dele;
- o clipe fica no cache pelo hash do que o define (o arquivo da imagem, o movimento, a duração), não de quem gera:
  trocar a imagem ou o movimento pede um clipe novo; voltar ao que era reaproveita o antigo sem pagar de novo;
- sem clipe (não gerado, sem chave, imagem nova), a cena mostra a imagem com o movimento de câmera e o build avisa.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from studio.core.hashing import hash_file, hash_obj
from studio.core.project.document import ClipSpec, ProjectDoc, Scene
from studio.core.project.media_edit import scene_label
from studio.core.styles.schema import StyleSpec
from studio.providers import registry
from studio.providers.base import NotConfigured, VideoProvider

if TYPE_CHECKING:
    from studio.workers.pipeline.build import Builder

log = logging.getLogger(__name__)

CLIP_KIND = "clip"
"""Tipo do artefato no cache (pasta cache/clip)."""
DEFAULT_MOTION = "slow cinematic camera push-in, subtle natural movement in the scene, stable composition"
_COST_SLACK = 0.005


def usd(value: float) -> str:
    """Valor em dólar do jeito brasileiro ("US$ 1,50")."""
    return f"US$ {value:.2f}".replace(".", ",")


class ClipError(ValueError):
    """Pedido inválido de vídeo IA (mensagem em português para o usuário)."""


def max_video_scenes(style: StyleSpec) -> int:
    """Quantas cenas podem ter vídeo IA neste vídeo: a do estilo ou 2 no Short e 6 no longo (README §15.2)."""
    if style.art.video_scenes is not None:
        return style.art.video_scenes
    return 2 if style.format.type == "short" else 6


def video_scenes(doc: ProjectDoc) -> list[Scene]:
    return [s for s in doc.ordered_scenes() if s.kind == "video"]


def blocked_reason(doc: ProjectDoc, scene: Scene) -> str | None:
    """Por que a cena não pode virar vídeo IA (None: pode)."""
    if scene.kind not in ("image", "video"):
        return "Só cena de imagem vira vídeo IA (cartões e textos ficam como estão)."
    if doc.shown_media(scene) is not None:
        return ("Cena com mídia real não vira vídeo IA: a foto é de outra pessoa ou de uma marca, e animar com IA "
                "mudaria o que ela mostra. Tire a mídia real para animar a imagem de IA.")
    visual = scene.visual
    if visual.reuse_of and not visual.asset:
        root = doc.reuse_root(scene)
        if root is None:
            return "A cena reaproveita uma imagem que não existe mais."
        if root.id != scene.id:
            return None if (root.visual.asset or root.visual.prompt) else "A cena não tem imagem para animar."
    if visual.asset or visual.prompt or visual.query:
        return None
    return "A cena não tem imagem para animar."


def clip_spec(scene: Scene) -> ClipSpec:
    return scene.clip or ClipSpec()


def clip_prompt(scene: Scene) -> str:
    """O pedido do clipe: o movimento (do usuário, da sugestão ou o padrão) e o que a imagem mostra."""
    motion = " ".join((clip_spec(scene).prompt or scene.video_hint or DEFAULT_MOTION).split())
    subject = " ".join(scene.visual.prompt.split())[:500]
    text = f"{motion}." if not motion.endswith(".") else motion
    if subject:
        text += f" Scene: {subject}"
    return text + " Keep the original look, no text, no logos."


def set_video(doc: ProjectDoc, style: StyleSpec, scene_id: str, *, enabled: bool, prompt: str | None = None,
              seconds: int | None = None) -> ProjectDoc:
    """Marca (ou desmarca) a cena-chave e ajusta movimento e duração. Devolve uma cópia do documento."""
    new = doc.model_copy(deep=True)
    try:
        scene = new.scene(scene_id)
    except KeyError as exc:
        raise ClipError("cena não encontrada") from exc
    if scene.locked:
        raise ClipError("A cena está travada: destrave para mudar o vídeo IA.")
    if not enabled:
        if scene.kind == "video":
            scene.kind = "image"
        return new
    reason = blocked_reason(new, scene)
    if reason:
        raise ClipError(reason)
    limit = max_video_scenes(style)
    others = [s for s in video_scenes(new) if s.id != scene_id]
    if scene.kind != "video" and len(others) >= limit:
        raise ClipError(f"Este vídeo já tem {len(others)} cena(s) com vídeo IA (o limite do estilo é {limit}). "
                        "Tire uma antes de marcar outra.")
    spec = clip_spec(scene).model_copy()
    if prompt is not None:
        spec.prompt = " ".join(prompt.split())[:600]
    if seconds is not None:
        spec = ClipSpec(prompt=spec.prompt, seconds=seconds)
    scene.kind = "video"
    scene.clip = spec
    return new


def suggest(doc: ProjectDoc, style: StyleSpec) -> list[str]:
    """Cenas sugeridas para o vídeo IA, até completar o limite: primeiro as que o storyboard sugeriu, depois o gancho,
    as aberturas de capítulo e as cenas mais longas. Uma por imagem (duas cenas da mesma imagem animariam igual)."""
    current = video_scenes(doc)
    free = max_video_scenes(style) - len(current)
    if free <= 0:
        return []
    taken = {_image_owner(doc, s) for s in current}
    order = {s.id: i for i, s in enumerate(doc.ordered_scenes())}
    first_scene_of: dict[str, str] = {}
    for scene in doc.ordered_scenes():
        first_scene_of.setdefault(scene.segment_id, scene.id)
    roles = {seg.id: seg.role for seg in doc.segments}

    def rank(scene: Scene) -> tuple[int, int, int, int]:
        role = roles.get(scene.segment_id, "body")
        opening = first_scene_of.get(scene.segment_id) == scene.id
        if scene.video_hint:
            tier = 0
        elif order[scene.id] == 0 or (role == "hook" and opening):
            tier = 1
        elif opening and role in ("chapter", "item"):
            tier = 2
        else:
            tier = 3
        own = 0 if not scene.visual.reuse_of else 1
        return (tier, own, -(scene.span or 0), order[scene.id])

    picks: list[str] = []
    for scene in sorted((s for s in doc.ordered_scenes() if s.kind == "image" and not s.locked
                         and blocked_reason(doc, s) is None), key=rank):
        owner = _image_owner(doc, scene)
        if owner in taken:
            continue
        taken.add(owner)
        picks.append(scene.id)
        if len(picks) >= free:
            break
    return picks


def _image_owner(doc: ProjectDoc, scene: Scene) -> str:
    if scene.visual.asset:
        return f"asset:{scene.visual.asset}"
    root = doc.reuse_root(scene) if scene.visual.reuse_of else scene
    return (root or scene).id


# --- cache e build ----------------------------------------------------------------------------


def chain_for(style: StyleSpec) -> list[str]:
    return registry.resolve_chain("video", style.art.video_chain)


def known_image(builder: Builder, scene: Scene) -> str | None:
    """A imagem que a cena mostra, se já existe (nada é gerado)."""
    doc = builder.doc
    owner = scene
    if scene.visual.reuse_of and not scene.visual.asset and scene.visual.media is None:
        owner = doc.reuse_root(scene) or scene
    if owner.visual.asset:
        return owner.visual.asset
    inputs = builder.image_inputs(owner)
    if inputs is None:
        return None
    _, cached = builder.cached_image(inputs, owner)
    return cached["path"] if cached else None


def clip_inputs(builder: Builder, scene: Scene, image_rel: str) -> dict[str, Any] | None:
    """O que define o clipe (o arquivo da imagem pelo conteúdo, não pelo caminho). None: a imagem sumiu."""
    path = builder.store.abs(image_rel)
    if not path.is_file():
        return None
    return {"image": hash_file(path), "prompt": clip_prompt(scene), "seconds": clip_spec(scene).seconds,
            "size": [builder.style.format.width, builder.style.format.height], "v": 1}


def clip_key(inputs: dict[str, Any]) -> str:
    return hash_obj(inputs)


def cached_clip(builder: Builder, scene: Scene, image_rel: str | None) -> dict[str, Any] | None:
    if not image_rel:
        return None
    inputs = clip_inputs(builder, scene, image_rel)
    return builder.store.lookup(CLIP_KIND, clip_key(inputs)) if inputs else None


def build_clips(builder: Builder, images: dict[str, str | None]) -> dict[str, dict[str, Any]]:
    """Os clipes que o build usa (só do cache: o build nunca gera vídeo IA). Avisa das cenas que ficam na imagem."""
    out: dict[str, dict[str, Any]] = {}
    for scene in video_scenes(builder.doc):
        label = scene_label(builder.doc, scene.id)
        reason = blocked_reason(builder.doc, scene)
        if reason:
            builder.warnings.append(f"{label}: {reason} A cena ficou com a imagem.")
            continue
        cached = cached_clip(builder, scene, images.get(scene.id))
        if cached is None:
            builder.warnings.append(f"{label}: o vídeo IA ainda não foi gerado; a cena mostra a imagem com movimento de "
                                    "câmera. Gere os clipes na aba Cenas.")
            continue
        if not cached.get("commercial", True):
            builder.stats.noncommercial.add(cached.get("provider", "?"))
        builder.stats.hit(CLIP_KIND, True)
        out[scene.id] = {"path": cached["path"], "seconds": cached.get("seconds")}
    return out


# --- estimativa e geração -------------------------------------------------------------------------


@dataclass
class _Provider:
    id: str
    name: str
    provider: VideoProvider


def _available(style: StyleSpec) -> tuple[list[_Provider], list[str]]:
    usable: list[_Provider] = []
    reasons: list[str] = []
    for provider_id in chain_for(style):
        provider = registry.get(provider_id)
        ok, reason = provider.availability()
        if ok and not registry.quota_exhausted_today(provider_id):
            usable.append(_Provider(provider_id, provider.info.name, provider))  # type: ignore[arg-type]
        else:
            reasons.append(f"{provider.info.name}: {reason if not ok else 'cota esgotada hoje'}")
    return usable, reasons


def estimate(builder: Builder) -> dict[str, Any]:
    """Custo de gerar os clipes que faltam: com o primeiro modelo disponível e, no pior caso (ele falha e o próximo
    da cadeia assume), com o mais caro. Nada é gerado nem cobrado."""
    doc, style = builder.doc, builder.style
    usable, reasons = _available(style)
    first = usable[0] if usable else None
    items: list[dict[str, Any]] = []
    missing = 0
    cost = worst = 0.0
    for scene in video_scenes(doc):
        spec = clip_spec(scene)
        item: dict[str, Any] = {"scene_id": scene.id, "label": scene_label(doc, scene.id), "seconds": spec.seconds,
                                "prompt": spec.prompt, "hint": scene.video_hint or "", "status": "missing", "detail": ""}
        reason = blocked_reason(doc, scene)
        if reason:
            item.update(status="blocked", detail=reason)
            items.append(item)
            continue
        image_rel = known_image(builder, scene)
        cached = cached_clip(builder, scene, image_rel)
        if cached is not None:
            item.update(status="ready", path=cached["path"], provider=cached.get("provider"),
                        seconds=cached.get("seconds") or spec.seconds)
            items.append(item)
            continue
        if not image_rel:
            item["detail"] = "A imagem da cena ainda não existe: ela é gerada antes do clipe."
        missing += 1
        if first is not None:
            item["clip_seconds"] = first.provider.clip_seconds(spec.seconds)
            item["cost_usd"] = first.provider.price(spec.seconds)
            cost += item["cost_usd"]
            worst += max(p.provider.price(spec.seconds) for p in usable)
        items.append(item)
    return {
        "max_scenes": max_video_scenes(style),
        "provider": first.id if first else None,
        "provider_name": first.name if first else None,
        "available": first is not None,
        "reason": "; ".join(reasons) if first is None else "",
        "chain": [p.id for p in usable],
        "scenes": items,
        "missing": missing,
        "estimated_cost_usd": round(cost, 4),
        "max_cost_usd": round(worst, 4),
        "suggestions": suggest(doc, style),
    }


def check_confirmed(plan: dict[str, Any], confirmed: float) -> None:
    """Recusa a geração quando o custo mudou depois da confirmação (outra cena marcada, outro modelo)."""
    if not plan["missing"]:
        raise ClipError("Todos os clipes das cenas marcadas já estão prontos.")
    if not plan["available"]:
        raise ClipError(f"Nenhum gerador de vídeo IA disponível ({plan['reason'] or 'configure a chave do fal.ai'}).")
    if plan["max_cost_usd"] > confirmed + _COST_SLACK:
        raise ClipError(f"O custo mudou para até {usd(plan['max_cost_usd'])}: confira e confirme de novo.")


def generate(builder: Builder, max_cost_usd: float) -> dict[str, Any]:
    """Gera os clipes que faltam, sem passar de `max_cost_usd` (o valor confirmado). A imagem da cena é gerada
    antes, se ainda não existir (usa a cota de imagens, como a prévia)."""
    doc = builder.doc
    chain = chain_for(builder.style)
    spent = 0.0
    generated: list[str] = []
    reused: list[str] = []
    skipped: list[str] = []
    errors: list[str] = []
    todo = [s for s in video_scenes(doc) if blocked_reason(doc, s) is None]
    builder._total = max(1, len(todo))  # noqa: SLF001 - o progresso do Builder conta os clipes
    for scene in todo:
        builder._check()  # noqa: SLF001
        label = scene_label(doc, scene.id)
        owner = scene
        if scene.visual.reuse_of and not scene.visual.asset:
            owner = doc.reuse_root(scene) or scene
        try:
            image_rel = builder.image(owner)
        except Exception as exc:  # noqa: BLE001 - a imagem falhou: o clipe desta cena fica para depois
            errors.append(f"{label}: a imagem não saiu ({exc})")
            continue
        inputs = clip_inputs(builder, scene, image_rel) if image_rel else None
        if inputs is None:
            errors.append(f"{label}: a cena ficou sem imagem para animar")
            continue
        key = clip_key(inputs)
        if builder.store.lookup(CLIP_KIND, key):
            reused.append(scene.id)
            builder._tick(f"{label}: clipe já estava pronto")  # noqa: SLF001
            continue
        out_dir = builder.store.dir_for(CLIP_KIND, key)
        image_path = builder.store.abs(image_rel)  # type: ignore[arg-type]

        def call(provider: VideoProvider, seconds: int = inputs["seconds"], prompt: str = inputs["prompt"],
                 remaining: float = max_cost_usd - spent, image_path: Path = image_path, out_dir: Path = out_dir):
            price = provider.price(seconds)
            if price > remaining + _COST_SLACK:
                raise NotConfigured(f"passaria do valor confirmado ({usd(max_cost_usd)})")
            builder._check()  # noqa: SLF001
            return provider.animate(image=image_path, prompt=prompt, seconds=seconds, out_dir=out_dir)

        try:
            result, provider, _ = registry.run_chain("video", chain, call, usage_of=builder._usage,  # noqa: SLF001
                                                     project_id=builder.project_id, job_id=builder.sink.job_id)
        except registry.ChainFailed as exc:
            if all("valor confirmado" in a.detail or a.outcome == "indisponível" for a in exc.attempts):
                skipped.append(scene.id)
                errors.append(f"{label}: não gerado para não passar do valor confirmado")
            else:
                errors.append(f"{label}: {exc}")
            continue
        spent += result.usage.cost_usd
        rel = builder.store.rel(result.path)
        builder.store.save(CLIP_KIND, key, {"path": rel, "files": [rel], "provider": provider.info.id,
                                            "model": result.model, "seconds": result.seconds,
                                            "commercial": provider.info.commercial_use, "meta": result.meta},
                           provider.info.id, result.usage.cost_usd)
        builder.stats.hit(CLIP_KIND, False, result.usage.cost_usd)
        generated.append(scene.id)
        builder._tick(f"{label}: clipe pronto")  # noqa: SLF001
    return {"generated": generated, "reused": reused, "skipped": skipped, "errors": errors,
            "cost_usd": round(spent, 4), "max_cost_usd": round(max_cost_usd, 4)}
