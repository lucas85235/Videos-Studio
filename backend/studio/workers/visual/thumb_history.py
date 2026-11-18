"""As thumbs recentes do canal: o que elas usaram (para o pacote novo não repetir) e o hash de cada imagem (para
avisar quando uma candidata sai parecida demais com uma delas).

Vale o que o dono escolheu: dos últimos `RECENT_PROJECTS` projetos do mesmo canal, o candidato escolhido e os
aprovados do pacote (peso 1). Os outros candidatos com prévia também apareceram na tela: contam pouco no uso (peso
0,3; 0,5 quando o projeto ainda não escolheu) e entram inteiros na comparação das imagens (a thumb nova não pode
sair igual a nenhuma que o dono já viu). Projeto sem pacote conta com a miniatura escolhida do envio (só a imagem).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image

from studio.config import get_settings
from studio.media.thumb_variety import Usage

log = logging.getLogger(__name__)

RECENT_PROJECTS = 8
RECENT_MIN_DISTANCE = 17
"""dHash (64 bits, na cópia de 320 px) abaixo disso contra uma thumb recente do canal: "parecida demais" (o pacote
troca o fundo e, se não adiantar, avisa e manda a candidata para o fim). Medido nas 19 thumbs dos 2 últimos longos
(18/11): mesma figura no mesmo fundo dá 8; mesmo fundo e template com outra pose, 12 a 16; thumbs diferentes, 19+."""
PENDING_WEIGHT = 0.5
SHOWN_WEIGHT = 0.3


@dataclass
class ChannelThumbs:
    usage: Usage = field(default_factory=Usage)
    thumbs: list[dict[str, Any]] = field(default_factory=list)
    """[{project_id, title, candidate_id, image, dhash, landscape}] das thumbs recentes com imagem."""

    def nearest(self, digest: int, *, landscape: bool = True) -> tuple[int, dict[str, Any] | None]:
        """(distância, thumb) da recente mais parecida (mesma orientação); (64, None) sem nenhuma."""
        from studio.media.thumb_check import hamming

        best: tuple[int, dict[str, Any] | None] = (64, None)
        for t in self.thumbs:
            if t.get("dhash") is None or bool(t.get("landscape", True)) != landscape:
                continue
            d = hamming(digest, int(t["dhash"]))
            if d < best[0]:
                best = (d, t)
        return best


@lru_cache(maxsize=256)
def _hash_cached(path: str, mtime: float) -> tuple[int, bool]:
    from studio.media.thumb_check import dhash

    with Image.open(path) as im:
        im.draft("RGB", (320, 180))
        small = im.convert("RGB")
        small.thumbnail((320, 320))  # o mesmo tamanho do `tasks._recent_hash`
        return dhash(small), small.width >= small.height


def image_hash(rel: str | None) -> tuple[int, bool] | None:
    """(dHash, deitada) da imagem relativa à pasta de dados; None quando o arquivo não existe."""
    if not rel:
        return None
    path = get_settings().data_dir / rel
    try:
        return _hash_cached(str(path), path.stat().st_mtime)
    except (OSError, ValueError):
        return None


def _chosen_miniature(settings: dict[str, Any]) -> str | None:
    for state in ((settings or {}).get("thumbs") or {}).values():
        variants = (state or {}).get("variants") or []
        try:
            chosen = int((state or {}).get("chosen") or 1)
        except (TypeError, ValueError):
            chosen = 1
        if 1 <= chosen <= len(variants) and variants[chosen - 1].get("path"):
            return str(variants[chosen - 1]["path"])
    return None


def channel_history(channel_id: str | None, exclude: str | None = None,
                    limit: int = RECENT_PROJECTS) -> ChannelThumbs:
    """As thumbs dos últimos `limit` projetos do canal (fora `exclude`, o projeto atual). Nunca derruba quem chama:
    banco ou documento ilegível vira histórico vazio (o pacote sai como antes)."""
    out = ChannelThumbs()
    if not channel_id:
        return out
    try:
        from studio.core.project import service
        from studio.db import session_scope
        from studio.models import Project

        with session_scope() as s:
            rows = [(r.id, r.title, dict(r.settings or {})) for r in
                    s.query(Project).filter(Project.channel_id == channel_id, Project.id != (exclude or ""))
                    .order_by(Project.created_at.desc()).limit(limit).all()]
    except Exception as exc:  # noqa: BLE001 - histórico é um extra
        log.warning("thumbs recentes do canal indisponíveis: %s", exc)
        return out
    for pid, title, settings in rows:
        try:
            doc = service.get_doc(pid)
        except Exception:  # noqa: BLE001 - versão ilegível: só a miniatura do envio
            doc = None
        picked = []
        if doc is not None:
            chosen = doc.package.chosen()
            ids = {c.id for c in doc.package.candidates if c.approved or (chosen and c.id == chosen.id)}
            rest = SHOWN_WEIGHT if ids else PENDING_WEIGHT
            picked = [(c, 1.0 if c.id in ids else rest) for c in doc.package.candidates
                      if c.id in ids or c.image]
        for cand, weight in picked:
            out.usage.add_layers(cand.layers, weight)
            h = image_hash(cand.image)
            if h is not None:
                out.thumbs.append({"project_id": pid, "title": cand.title or title, "candidate_id": cand.id,
                                   "image": cand.image, "dhash": h[0], "landscape": h[1], "weight": weight})
        if not picked:
            rel = _chosen_miniature(settings)
            h = image_hash(rel)
            if h is not None:
                out.thumbs.append({"project_id": pid, "title": title, "candidate_id": None, "image": rel,
                                   "dhash": h[0], "landscape": h[1], "weight": 1.0})
    return out


def describe(thumb: dict[str, Any] | None) -> str:
    if not thumb:
        return ""
    title = str(thumb.get("title") or thumb.get("project_id") or "")
    return title if len(title) <= 50 else title[:47].rstrip() + "..."
