"""Acervo de material real do projeto (ADR 0004 §3.0): `pool.json`, fora do `ProjectDoc`.

O acervo é coletado antes do storyboard (`workers/pipeline/media_pool.gather`) e o LLM escolhe dele pelo `id`. Fica num
arquivo próprio na pasta do projeto (`uploads/<projeto>/pool.json`) para não inchar o documento nem as versões.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from studio.config import get_settings
from studio.core.project.document import Prov, _unit_rect

POOL_FILE = "pool.json"
POOL_ID_RE = re.compile(r"^p\d{1,5}$")
PoolMedia = Literal["image", "clip", "capture", "meme"]
_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")


class PoolItem(BaseModel):
    """Um candidato do acervo: imagem, trecho de vídeo, captura de página ou meme, com nível, licença e crédito."""

    id: str = Field(pattern=POOL_ID_RE.pattern)
    """Curto e estável dentro do projeto ("p12"): é o que o LLM cita em `pool_id`."""
    tier: Prov
    media: PoolMedia = "image"
    desc: str = ""
    """Descrição curta do que aparece (vai para o pedido do storyboard)."""
    tags: list[str] = []
    entities: list[str] = []
    """Nomes/ids de entidades que aparecem (personagem, lugar, produto)."""
    path: str | None = None
    """Arquivo local (relativo a VS_DATA_DIR): imagem ou captura; num trecho (`clip`), o vídeo de ORIGEM, e o plano é
    `start_s` a `start_s + dur_s` (o anexo faz o proxy só do plano escolhido). None: ainda não baixado (ou o vídeo de
    origem fica fora da pasta de dados)."""
    poster: str | None = None
    """Pôster JPEG do clipe (relativo a VS_DATA_DIR)."""
    url: str = ""
    page_url: str = ""
    start_s: float = Field(default=0.0, ge=0)
    """Clipe: onde o trecho começa no vídeo de ORIGEM (`url`), para o crédito e para cortar de novo. O `path` já é o
    proxy cortado a partir desse ponto (começa em 0); a `MediaRef` anexada guarda em `start_s` o deslocamento dentro
    do `video` dela, que para o proxy é 0."""
    dur_s: float | None = Field(default=None, gt=0)
    rects: list[list[float]] = []
    """Captura: retângulos do trecho, `[x, y, w, h]` de 0 a 1."""
    w: int = Field(default=0, ge=0)
    h: int = Field(default=0, ge=0)
    license: str = ""
    credit: str = ""
    editorial: bool = False
    dhash: str = ""
    """Hash perceptual (hex) para a anti-repetição entre vídeos."""
    score: float = 0.0
    source_id: str = ""
    """De onde o item veio, com prefixo da origem ("commons:File:X.jpg", "pexels-video:123", "yt:<chave>#3",
    "cap:<id>", "acervo:<arquivo>", "kit:<id>", "found:<url>", "file:<asset>"): mantém o `id` estável entre coletas e
    diz como baixar o item no anexo."""
    topic: str = ""
    """Tópico do roteiro a que o item responde (as linhas do pedido do storyboard são agrupadas por ele)."""

    @field_validator("rects")
    @classmethod
    def _rects(cls, value: list[list[float]]) -> list[list[float]]:
        return [_unit_rect(r) for r in value]

    @field_validator("path", "poster")
    @classmethod
    def _relative(cls, value: str | None) -> str | None:
        if value is None:
            return None
        clean = value.replace("\\", "/").strip()
        if not clean or clean.startswith("/") or ":" in clean or any(p in ("", ".", "..") for p in clean.split("/")):
            raise ValueError("caminho do acervo inválido")
        return clean


class Pool(BaseModel):
    items: list[PoolItem] = []
    gathered_at: str | None = None
    """Quando a coleta rodou (ISO, UTC). None: nunca."""
    warnings: list[str] = []
    """Avisos dos coletores (falha de um coletor não derruba os outros), em português, sem texto cru de exceção."""
    searched: bool = False
    """A coleta já rodou a busca na web paga (`media_find`) deste storyboard: o job não chama `media_find` de novo
    depois do storyboard. A coleta rápida do storyboard (`gather(full=False)`) nunca faz a busca paga, então hoje
    fica False e o job do storyboard segue com o `media_find` como antes."""
    ids: dict[str, str] = {}
    """Registro `source_id → id` de TODO item que já passou pelo acervo (não encolhe quando a coleta perde itens): um
    id dado uma vez nunca vai para outro item, e a cena que cita "p7" continua certa."""
    next_id: int = Field(default=1, ge=1)
    """O próximo número de id livre (só cresce)."""

    @field_validator("items")
    @classmethod
    def _unique(cls, value: list[PoolItem]) -> list[PoolItem]:
        ids = [i.id for i in value]
        if len(set(ids)) != len(ids):
            raise ValueError("ids repetidos no acervo")
        return value

    def item(self, pool_id: str | None) -> PoolItem | None:
        return next((i for i in self.items if i.id == pool_id), None) if pool_id else None


def pool_path(project_id: str) -> Path:
    if not _PROJECT_ID_RE.match(project_id):
        raise ValueError(f"id de projeto inválido: {project_id!r}")
    return get_settings().uploads_dir / project_id / POOL_FILE


def load_pool(project_id: str) -> Pool:
    """O acervo gravado do projeto; sem arquivo (ou ilegível), um acervo vazio."""
    path = pool_path(project_id)
    if not path.is_file():
        return Pool()
    try:
        return Pool.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, ValidationError):
        return Pool(warnings=["O acervo gravado estava ilegível e foi ignorado."])


def save_pool(project_id: str, pool: Pool) -> Path:
    """Grava `pool.json` (troca atômica: quem lê nunca vê o arquivo pela metade)."""
    path = pool_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.stem}.{uuid.uuid4().hex[:10]}.json.tmp")  # único: duas gravações não se cruzam
    tmp.write_text(pool.model_dump_json(indent=1), encoding="utf-8")
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
