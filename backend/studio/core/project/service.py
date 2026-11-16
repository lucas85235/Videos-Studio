"""Criação de projetos e versões (cada alteração vira um commit)."""

from __future__ import annotations

import secrets
from typing import Any

from studio.core.project.document import ProjectDoc
from studio.core.styles.library import get_style
from studio.db import session_scope
from studio.ids import new_id
from studio.models import Project, ProjectBranch, ProjectVersion


class ProjectNotFound(LookupError):
    pass


def create_project(
    *,
    title: str,
    style_id: str,
    topic: str = "",
    brief: str = "",
    channel_id: str | None = None,
    languages: list[str] | None = None,
    settings: dict[str, Any] | None = None,
    voice: dict[str, str] | None = None,
) -> ProjectDoc:
    style = get_style(style_id)
    overrides: dict[str, Any] = {}
    if voice:
        # A voz escolhida vale para o projeto: o provedor dela vem primeiro na cadeia (o resto fica de reserva).
        chain = [voice["provider"], *(p for p in style.voice.chain if p != voice["provider"])]
        overrides = {"voice": {"voice": voice["voice"], "chain": chain}}
        get_style(style_id, overrides)  # valida
    langs = languages or style.languages.outputs
    primary = langs[0] if langs else style.languages.primary
    project_id = new_id("prj")
    doc = ProjectDoc(
        id=project_id,
        title=title,
        topic=topic,
        brief=brief,
        channel_id=channel_id,
        style_id=style_id,
        primary_language=primary,
        languages=langs,
        seed=secrets.randbelow(2**31),
        style_overrides=overrides,
    )
    # Projeto novo para nas cenas antes de gastar imagens; projeto antigo, sem a chave "storyboard", segue direto.
    default_settings = {"gates": {"script": True, "storyboard": True, "preview": True, "publish": True}}
    with session_scope() as s:
        s.add(
            Project(
                id=project_id,
                title=title,
                channel_id=channel_id,
                style_id=style_id,
                status="draft",
                current_version=1,
                settings={**default_settings, **(settings or {})},
            )
        )
        s.add(ProjectVersion(project_id=project_id, number=1, parent_number=None, doc=doc.model_dump(mode="json"), message="Projeto criado"))
    return doc


def get_project_row(project_id: str) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        return {
            "id": row.id,
            "title": row.title,
            "channel_id": row.channel_id,
            "style_id": row.style_id,
            "status": row.status,
            "current_version": row.current_version,
            "parent_id": row.parent_id,  # longo de onde o Short foi cortado
            "settings": row.settings,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }


def get_doc(project_id: str, version: int | None = None) -> ProjectDoc:
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        number = version or row.current_version
        ver = s.query(ProjectVersion).filter_by(project_id=project_id, number=number).one_or_none()
        if ver is None:
            raise ProjectNotFound(f"{project_id} v{number}")
        return ProjectDoc.model_validate(ver.doc)


MAIN_BRANCH = "main"


class BranchNotOpen(ValueError):
    """O ramo não existe ou já foi fechado (mesclado, promovido ou descartado)."""


def commit(project_id: str, doc: ProjectDoc, message: str, author: str = "user", *, branch: str | None = None) -> int:
    """Salva uma nova versão do documento. Retorna o número da versão.

    Sem `branch` (ou com "main"), a versão vai para a linha principal e vira a atual. Com o nome de um ramo A/B aberto,
    a versão vai para o ramo (o pai é a cabeça do ramo) e a principal não muda.
    """
    if doc.id != project_id:
        raise ValueError("o documento pertence a outro projeto")
    data = doc.model_dump(mode="json")
    on_branch = bool(branch) and branch != MAIN_BRANCH
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        ramo = None
        if on_branch:
            ramo = s.query(ProjectBranch).filter_by(project_id=project_id, name=branch).one_or_none()
            if ramo is None or ramo.status != "aberto":
                raise BranchNotOpen(f"o ramo {branch} não está aberto")
        last = s.query(ProjectVersion).filter_by(project_id=project_id).order_by(ProjectVersion.number.desc()).first()
        number = (last.number if last else 0) + 1
        s.add(
            ProjectVersion(
                project_id=project_id,
                number=number,
                parent_number=ramo.head_number if ramo is not None else row.current_version,
                doc=data,
                message=message,
                author=author,
                branch=branch if on_branch else MAIN_BRANCH,
            )
        )
        if ramo is not None:
            ramo.head_number = number
            return number
        row.current_version = number
        row.title = doc.title
        row.style_id = doc.style_id  # a op set_style_id troca o estilo pelo documento
        return number


def list_versions(project_id: str) -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.query(ProjectVersion).filter_by(project_id=project_id).order_by(ProjectVersion.number.desc()).all()
        return [
            {
                "number": r.number,
                "parent": r.parent_number,
                "message": r.message,
                "author": r.author,
                "branch": r.branch or MAIN_BRANCH,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in rows
        ]


def checkout(project_id: str, number: int) -> int:
    """Volta para uma versão anterior criando um novo commit com o conteúdo dela (desfazer)."""
    doc = get_doc(project_id, number)
    return commit(project_id, doc, f"Voltou para a versão {number}")


def set_status(project_id: str, status: str) -> None:
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        row.status = status


def update_settings(project_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        merged = {**(row.settings or {}), **patch}
        row.settings = merged
        return merged


def update_settings_key(project_id: str, key: str, subkey: str, value: Any) -> dict[str, Any]:
    """Troca só `settings[key][subkey]`, lendo e gravando na mesma transação (jobs em paralelo não se apagam)."""
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            raise ProjectNotFound(project_id)
        settings = dict(row.settings or {})
        settings[key] = {**(settings.get(key) or {}), subkey: value}
        row.settings = settings
        return settings


def list_projects() -> list[dict[str, Any]]:
    with session_scope() as s:
        rows = s.query(Project).order_by(Project.updated_at.desc()).all()
        return [
            {
                "id": r.id,
                "title": r.title,
                "style_id": r.style_id,
                "channel_id": r.channel_id,
                "status": r.status,
                "current_version": r.current_version,
                "parent_id": r.parent_id,
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in rows
        ]


def delete_project(project_id: str) -> bool:
    with session_scope() as s:
        row = s.get(Project, project_id)
        if row is None:
            return False
        s.query(ProjectVersion).filter_by(project_id=project_id).delete()
        s.query(ProjectBranch).filter_by(project_id=project_id).delete()
        s.delete(row)
        return True
