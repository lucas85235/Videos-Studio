"""Avatar do apresentador: o efetivo de um projeto (canal + projeto + estilo), a escolha da pose pelo nome e a leitura
e gravação no `style_bible` do canal.

O canal guarda o avatar em `style_bible["presenter"]` (como os personagens, sem migração de banco); o projeto só
ajusta (`ProjectDoc.presenter`: ligado/desligado, thumb, vídeo, poses fora); o estilo pode desligar
(`EditSpec.presenter == "off"`). Nada aqui toca o disco além da leitura do canal no banco.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Literal

from studio.core.project.document import (
    PRESENTER_KEY,
    Presenter,
    PresenterOverride,
    PresenterPose,
    ProjectDoc,
)

Purpose = Literal["video", "thumb"]


def fold(text: str | None) -> str:
    """Minúsculas, sem acento e com espaço/underline virando hífen ("Mão na testa" → "mao-na-testa")."""
    raw = unicodedata.normalize("NFKD", (text or "").strip().lower())
    raw = "".join(ch for ch in raw if not unicodedata.combining(ch))
    return "-".join(raw.replace("_", " ").split())


def _is_vertical(style: Any) -> bool:
    fmt = getattr(style, "format", None)
    if fmt is None:
        return False
    if getattr(fmt, "type", None) == "short":
        return True
    width, height = getattr(fmt, "width", 0) or 0, getattr(fmt, "height", 0) or 0
    return bool(width and height and height > width)


def _style_off(style: Any) -> bool:
    edit = getattr(style, "edit", None)
    return getattr(edit, "presenter", "auto") == "off"


def effective(
    channel: Presenter | dict[str, Any] | None,
    project: ProjectDoc | PresenterOverride | None,
    style: Any = None,
    *,
    purpose: Purpose = "video",
    vertical: bool | None = None,
) -> Presenter | None:
    """O avatar que vale neste projeto, ou None (desligado).

    - `channel`: o `Presenter` do canal, ou o `style_bible` inteiro do canal (lido com `Presenter.from_bible`);
    - liga com `project.enabled ?? channel.enabled`;
    - `style.edit.presenter == "off"` desliga as cenas (a thumb não depende do estilo de edição);
    - `purpose="video"`: segue `project.video ?? (use_in_shorts no vertical, use_in_video no deitado)`;
      `purpose="thumb"`: segue `project.thumb ?? use_in_thumb` e, no vertical, também `use_in_shorts`;
    - as poses de `poses_off` saem da cópia devolvida; sem nenhuma pose, None.
    `vertical` None: decide pelo formato do estilo (Short ou altura maior que a largura)."""
    if channel is None:
        return None
    base = channel if isinstance(channel, Presenter) else Presenter.from_bible(channel)
    override = project.presenter if isinstance(project, ProjectDoc) else (project or PresenterOverride())
    enabled = override.enabled if override.enabled is not None else base.enabled
    if not enabled:
        return None
    is_vertical = _is_vertical(style) if vertical is None else vertical
    if purpose == "video":
        if style is not None and _style_off(style):
            return None
        default = base.use_in_shorts if is_vertical else base.use_in_video
        if not (override.video if override.video is not None else default):
            return None
    else:
        default = base.use_in_thumb and (base.use_in_shorts or not is_vertical)
        if not (override.thumb if override.thumb is not None else default):
            return None
    off = {fold(p) for p in override.poses_off}
    out = base.model_copy(deep=True)
    out.enabled = True
    out.poses = [p for p in out.poses if fold(p.id) not in off]
    if not out.poses:
        return None
    return out


def resolve_pose(presenter: Presenter, name: str | None) -> PresenterPose | None:
    """A pose pelo nome: id → apelido → emoção → `default_pose` → a primeira. None só sem nenhuma pose.
    A comparação ignora caixa, acento e espaço/hífen ("Mão na testa" acha "mao-na-testa")."""
    if not presenter.poses:
        return None
    key = fold(name)
    if key:
        for pose in presenter.poses:
            if fold(pose.id) == key:
                return pose
        for pose in presenter.poses:
            if any(fold(a) == key for a in pose.aliases):
                return pose
        for pose in presenter.poses:
            if any(fold(e) == key for e in pose.emotions):
                return pose
        # Emoção composta ("detalhe escondido") citada só por uma palavra ("detalhe"), e vice-versa.
        for pose in presenter.poses:
            if any(key in fold(e).split("-") or fold(e) in key.split("-") for e in pose.emotions):
                return pose
    return presenter.pose(presenter.default_pose) or presenter.poses[0]


def pose_exists(presenter: Presenter, name: str | None) -> bool:
    """O nome é o id ou um apelido de uma pose (sem cair na emoção nem na padrão)."""
    key = fold(name)
    return bool(key) and any(fold(p.id) == key or any(fold(a) == key for a in p.aliases) for p in presenter.poses)


def word_key(text: str | None) -> str:
    """Forma de comparar uma palavra da narração com o gatilho (sem acento, caixa e pontuação)."""
    return re.sub(r"[^\w\-]", "", fold(text)).strip("-_")


def parse_at(at: str | None) -> tuple[str | None, int, int | None]:
    """Gatilho da cena → (palavra, ocorrência, milissegundos). "@1200" → (None, 1, 1200); "palavra#2" →
    ("palavra", 2, None); None/vazio → (None, 1, None)."""
    value = (at or "").strip()
    if not value:
        return None, 1, None
    if value.startswith("@"):
        try:
            return None, 1, max(0, int(value[1:]))
        except ValueError:
            return None, 1, None
    word, _, nth = value.partition("#")
    try:
        occurrence = max(1, int(nth)) if nth else 1
    except ValueError:
        occurrence = 1
    return word_key(word) or None, occurrence, None


def trigger_index(texts: list[str], at: str | None) -> int | None:
    """Índice da palavra-gatilho em `texts` (a n-ésima ocorrência), ou None (sem gatilho, "@ms" ou palavra ausente).
    Aceita a palavra no começo de uma palavra maior ("chocante" acha "chocante," e "chocantes")."""
    word, occurrence, _ms = parse_at(at)
    if not word:
        return None
    keys = [word_key(t) for t in texts]
    for exact in (True, False):
        seen = 0
        for i, key in enumerate(keys):
            if key == word if exact else (len(word) >= 4 and key.startswith(word)):
                seen += 1
                if seen == occurrence:
                    return i
    return None


def thumb_poses(presenter: Presenter | None) -> list[PresenterPose]:
    """As poses boas para thumb (`thumb_ok`); vazio sem avatar."""
    return presenter.thumb_poses() if presenter is not None else []


# --- canal (style_bible) ------------------------------------------------------------------------------------------


def channel_presenter(channel_id: str | None) -> Presenter:
    """O avatar guardado no canal (o padrão, desligado, sem canal ou sem avatar)."""
    from studio.core.project.characters import channel_bible

    return Presenter.from_bible(channel_bible(channel_id))


def save_channel_presenter(channel_id: str, presenter: Presenter) -> Presenter:
    """Grava o avatar no `style_bible` do canal sem tocar nas outras chaves. KeyError: canal inexistente."""
    from studio.db import session_scope
    from studio.models import Channel

    with session_scope() as s:
        row = s.get(Channel, channel_id)
        if row is None:
            raise KeyError(channel_id)
        row.style_bible = {**(row.style_bible or {}), PRESENTER_KEY: presenter.model_dump(mode="json")}
    return presenter


def project_presenter(doc: ProjectDoc, style: Any = None, *, purpose: Purpose = "video") -> Presenter | None:
    """O efetivo do projeto, lendo o canal do banco."""
    if not doc.channel_id:
        return None
    return effective(channel_presenter(doc.channel_id), doc, style, purpose=purpose)
