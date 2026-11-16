"""Rotas da importação do histórico do projeto antigo `canais` (prévia e importação)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from studio.workers.canais_import.apply import import_canais
from studio.workers.canais_import.reader import CanaisError, default_root

router = APIRouter(prefix="/api", tags=["importacao"])


class CanaisImportBody(BaseModel):
    root: str = Field(default="", max_length=1000)  # vazio = pasta padrão
    # {slug do canal no canais: "auto" | "pular" | id de um canal do Vulto}
    channels: dict[str, str] = {}


def _run(body: CanaisImportBody, dry_run: bool) -> dict[str, Any]:
    try:
        return import_canais(body.root.strip() or None, body.channels, dry_run=dry_run)
    except (CanaisError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/import/canais")
def canais_defaults() -> dict[str, Any]:
    root = default_root()
    return {"default_root": str(root), "exists": root.is_dir()}


@router.post("/import/canais/preview")
def canais_preview(body: CanaisImportBody) -> dict[str, Any]:
    """O que entraria, sem gravar nada."""
    return _run(body, dry_run=True)


@router.post("/import/canais")
def canais_import(body: CanaisImportBody) -> dict[str, Any]:
    """Importa de verdade. Rodar de novo não duplica."""
    return _run(body, dry_run=False)
