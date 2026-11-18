"""As thumbs recentes do canal: o que elas usaram (para o pacote novo não repetir) e o hash de cada imagem (para
avisar quando uma candidata sai parecida demais com uma delas).

Vale o que o dono viu: dos últimos `RECENT_PROJECTS` projetos do mesmo canal, o candidato escolhido e os aprovados
do pacote (peso 1) e os outros candidatos com prévia (peso `SHOWN_WEIGHT`: o dono reclamou de tudo o que viu, não
só da escolhida). Todos entram inteiros na comparação das imagens (a thumb nova não pode sair igual a nenhuma que o
dono já viu). Projeto sem pacote conta com a miniatura escolhida do envio (só a imagem). O uso conta só as thumbs da
mesma orientação do pacote (os Shorts não mexem nas escolhas do vídeo longo, e vice-versa).

A comparação não se engana com espelho: a distância é a menor entre a imagem e o espelho dela (`image_hashes`), e
mesmo fundo com a mesma pose (ou a mesma palavra) de uma recente conta como repetição (`ChannelThumbs.like`), em
qualquer lado.
"""

from __future__ import annotations

import logging
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from PIL import Image, ImageOps

from studio.config import get_settings
from studio.media.thumb_variety import Usage, bg_key

log = logging.getLogger(__name__)

RECENT_PROJECTS = 8
RECENT_MIN_DISTANCE = 17
"""dHash (64 bits, na cópia de 320 px) abaixo disso contra uma thumb recente do canal: "parecida demais" (o pacote
troca o fundo, depois a figura ou a pose e, se não adiantar, avisa e manda a candidata para o fim). Medido nas 19
thumbs dos 2 últimos longos (18/11): mesma figura no mesmo fundo dá 8; mesmo fundo e template com outra pose, 12 a
16; thumbs diferentes, 19+. Vale a menor distância entre a imagem e o espelho dela."""
PENDING_WEIGHT = 0.8
SHOWN_WEIGHT = 0.8


def fold_words(text: str | None) -> str:
    """O texto da thumb comparável ("NÃO!" e "nao" são a mesma palavra)."""
    raw = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    return " ".join(w.strip("?!.,:;\"'") for w in raw.split() if w.strip("?!.,:;\"'"))


def recent_features(layers: Any, words: str | None) -> dict[str, Any]:
    """O que identifica uma thumb para o olho: fundo, pose do avatar, figuras e a palavra."""
    return {"background": bg_key(layers), "pose": layers.presenter.pose if layers.presenter is not None else None,
            "characters": sorted(c.asset for c in layers.characters
                                 if not c.asset.startswith(("logo:", "object:"))),
            "words": fold_words(words if words is not None else (layers.text.words if layers.text else ""))}


def same_look(a: dict[str, Any], b: dict[str, Any]) -> str | None:
    """Por que duas thumbs são "a mesma" para quem vê, ou None: o mesmo fundo com a mesma pose, o mesmo fundo com a
    mesma palavra, ou a mesma figura com a mesma palavra (o lado do texto não importa: espelhar não muda a thumb)."""
    bg = a.get("background")
    if bg and bg == b.get("background"):
        if a.get("pose") and a.get("pose") == b.get("pose"):
            return f"mesmo fundo ({bg}) e mesma pose ({a['pose']})"
        if a.get("words") and a.get("words") == b.get("words"):
            return f"mesmo fundo ({bg}) e mesmo texto"
    chars = set(a.get("characters") or []) & set(b.get("characters") or [])
    if chars and a.get("words") and a.get("words") == b.get("words"):
        return f"mesma figura ({', '.join(sorted(chars))}) e mesmo texto"
    return None


@dataclass
class ChannelThumbs:
    usage: Usage = field(default_factory=Usage)
    thumbs: list[dict[str, Any]] = field(default_factory=list)
    """[{project_id, title, candidate_id, image, dhash, landscape, weight, feats}] das thumbs recentes com imagem."""

    def nearest(self, digest: int | tuple[int, ...] | list[int], *,
                landscape: bool = True) -> tuple[int, dict[str, Any] | None]:
        """(distância, thumb) da recente mais parecida (mesma orientação); (64, None) sem nenhuma. Com mais de um
        hash (a imagem e o espelho dela, `image_hashes`), vale o menor: espelhar não "resolve" a semelhança."""
        from studio.media.thumb_check import hamming

        digests = [digest] if isinstance(digest, int) else list(digest)
        best: tuple[int, dict[str, Any] | None] = (64, None)
        for t in self.thumbs:
            if t.get("dhash") is None or bool(t.get("landscape", True)) != landscape:
                continue
            d = min(hamming(h, int(t["dhash"])) for h in digests)
            if d < best[0]:
                best = (d, t)
        return best

    def like(self, layers: Any, words: str | None = None, *,
             landscape: bool = True) -> tuple[dict[str, Any], str] | None:
        """(thumb, motivo) da recente que esta repete pelos atributos (`same_look`), ou None."""
        mine = recent_features(layers, words)
        for t in self.thumbs:
            feats = t.get("feats")
            if not feats or bool(t.get("landscape", True)) != landscape:
                continue
            why = same_look(mine, feats)
            if why:
                return t, why
        return None


def _hash(im: Image.Image) -> tuple[int, int, bool]:
    from studio.media.thumb_check import dhash

    small = im.convert("RGB")
    small.thumbnail((320, 320))
    return dhash(small), dhash(ImageOps.mirror(small)), small.width >= small.height


def image_hashes(img: Image.Image) -> tuple[int, int]:
    """(dHash, dHash do espelho) de uma prévia, na cópia de 320 px (a mesma medida das recentes)."""
    h, m, _ = _hash(img.copy())
    return h, m


@lru_cache(maxsize=256)
def _hash_cached(path: str, mtime: float) -> tuple[int, bool]:
    with Image.open(path) as im:
        im.draft("RGB", (320, 180))
        h, _, landscape = _hash(im)
        return h, landscape


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


def channel_history(channel_id: str | None, exclude: str | None = None, limit: int = RECENT_PROJECTS, *,
                    landscape: bool | None = None) -> ChannelThumbs:
    """As thumbs dos últimos `limit` projetos do canal (fora `exclude`, o projeto atual). Com `landscape`, o uso só
    soma os candidatos daquela orientação (longo: tudo menos o template `livre`; Short: só o `livre`); as imagens
    já são comparadas só com as da mesma orientação. Nunca derruba quem chama: banco ou documento ilegível vira
    histórico vazio (o pacote sai como antes)."""
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
            if landscape is None or (cand.layers.template == "livre") != landscape:
                out.usage.add_layers(cand.layers, weight)
            h = image_hash(cand.image)
            if h is not None:
                out.thumbs.append({"project_id": pid, "title": cand.title or title, "candidate_id": cand.id,
                                   "image": cand.image, "dhash": h[0], "landscape": h[1], "weight": weight,
                                   "feats": recent_features(cand.layers, cand.thumb_text or None)})
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
