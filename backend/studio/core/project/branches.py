"""Ramos A/B de versões ("versão B do gancho").

A linha principal é a versão **A** (cabeça = `Project.current_version`). Um ramo nasce de uma versão qualquer, recebe
as próprias edições (por instrução ou por operações) sem mexer na principal, tem prévia e vídeo final próprios
(`outputs/<projeto>/ramos/<nome>`) e termina de um destes jeitos:

- **mesclado**: os trechos escolhidos do ramo (texto + cenas do trecho) e, se pedido, título e estilo entram na
  principal como uma versão nova;
- **promovido**: o ramo inteiro vira a principal (uma versão nova na principal com o conteúdo do ramo);
- **descartado**: fica só no histórico.

Quando A e B são publicados no YouTube, as publicações levam `details["versao"]` ("A", "B"...) e o ramo abre um
experimento em Desempenho com a variável `versao` (ver `ab_experiment`).
"""

from __future__ import annotations

import json
import shutil
import string
from datetime import UTC, datetime
from typing import Any

from studio.config import get_settings
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.db import session_scope
from studio.models import ProjectBranch, ProjectVersion

MAIN = service.MAIN_BRANCH
LETTERS = string.ascii_uppercase[1:]  # B..Z (a principal é a A)
OPEN = "aberto"
CLOSED_STATUSES = ("mesclado", "promovido", "descartado")
GLOBAL_PARTS = ("titulo", "estilo")
"""Partes fora dos trechos que dá para mesclar: `titulo` (título, metadados e capa) e `estilo` (estilo, ajustes do
estilo e trilha)."""


class BranchNotFound(LookupError):
    pass


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def branch_dict(row: ProjectBranch) -> dict[str, Any]:
    return {
        "name": row.name,
        "letter": row.letter,
        "label": f"Versão {row.letter}",
        "note": row.note,
        "base": row.base_number,
        "head": row.head_number,
        "status": row.status,
        "is_main": False,
        "last_preview": row.last_preview or None,
        "last_final": row.last_final or None,
        "experiment_id": row.experiment_id,
        "created_at": _iso(row.created_at),
        "closed_at": _iso(row.closed_at),
    }


def main_dict(project_id: str) -> dict[str, Any]:
    """A principal no mesmo formato de um ramo (versão A)."""
    row = service.get_project_row(project_id)
    settings = row["settings"] or {}
    return {
        "name": MAIN,
        "letter": "A",
        "label": "Versão A (principal)",
        "note": "",
        "base": None,
        "head": row["current_version"],
        "status": "principal",
        "is_main": True,
        "last_preview": settings.get("last_preview") or None,
        "last_final": settings.get("last_final") or None,
        "experiment_id": None,
        "created_at": row["created_at"],
        "closed_at": None,
    }


def list_branches(project_id: str) -> list[dict[str, Any]]:
    """Ramos do projeto (abertos primeiro, depois os fechados, mais novos antes). Não inclui a principal."""
    service.get_project_row(project_id)
    with session_scope() as s:
        rows = s.query(ProjectBranch).filter_by(project_id=project_id).order_by(ProjectBranch.id.desc()).all()
        items = [branch_dict(r) for r in rows]
    return sorted(items, key=lambda b: b["status"] != OPEN)


def get_branch(project_id: str, name: str) -> dict[str, Any]:
    if name == MAIN:
        return main_dict(project_id)
    with session_scope() as s:
        row = s.query(ProjectBranch).filter_by(project_id=project_id, name=name).one_or_none()
        if row is None:
            raise BranchNotFound(f"ramo não encontrado: {name}")
        return branch_dict(row)


def _open_row(s: Any, project_id: str, name: str) -> ProjectBranch:
    row = s.query(ProjectBranch).filter_by(project_id=project_id, name=name).one_or_none()
    if row is None:
        raise BranchNotFound(f"ramo não encontrado: {name}")
    if row.status != OPEN:
        raise service.BranchNotOpen(f"a versão {row.letter} já foi fechada ({row.status})")
    return row


def head_of(project_id: str, name: str) -> int:
    return int(get_branch(project_id, name)["head"])


def branch_doc(project_id: str, name: str) -> ProjectDoc:
    return service.get_doc(project_id, head_of(project_id, name))


def has_ab(project_id: str) -> bool:
    """O projeto tem (ou teve) um ramo que não foi descartado? Então a principal publicada é a versão "A"."""
    with session_scope() as s:
        return (
            s.query(ProjectBranch)
            .filter(ProjectBranch.project_id == project_id, ProjectBranch.status != "descartado")
            .first()
            is not None
        )


def create_branch(project_id: str, *, from_number: int | None = None, note: str = "") -> dict[str, Any]:
    """Cria um ramo a partir de uma versão (padrão: a atual da principal). O ramo começa com uma cópia dela."""
    row = service.get_project_row(project_id)
    base = from_number or row["current_version"]
    doc = service.get_doc(project_id, base)  # confere que a versão existe
    note = (note or "").strip()[:300]
    with session_scope() as s:
        used = {r.letter for r in s.query(ProjectBranch).filter_by(project_id=project_id).all()}
        letter = next((x for x in LETTERS if x not in used), None)
        if letter is None:
            raise ValueError("limite de ramos deste projeto atingido (B a Z)")
        last = s.query(ProjectVersion).filter_by(project_id=project_id).order_by(ProjectVersion.number.desc()).first()
        number = (last.number if last else 0) + 1
        name = letter.lower()
        message = f"Versão {letter} criada a partir da v{base}" + (f": {note}" if note else "")
        s.add(ProjectVersion(project_id=project_id, number=number, parent_number=base, doc=doc.model_dump(mode="json"),
                             message=message, author="user", branch=name))
        branch = ProjectBranch(project_id=project_id, name=name, letter=letter, note=note, base_number=base,
                               head_number=number, status=OPEN, last_preview={}, last_final={})
        s.add(branch)
        s.flush()
        return branch_dict(branch)


def save_result(project_id: str, name: str, mode: str, result: dict[str, Any], version: int) -> None:
    """Guarda o resultado da prévia ou do vídeo final do ramo (com a versão que foi gerada)."""
    data = {**result, "version": version}
    if name == MAIN:
        service.update_settings(project_id, {f"last_{mode}": data})
        return
    with session_scope() as s:
        row = s.query(ProjectBranch).filter_by(project_id=project_id, name=name).one_or_none()
        if row is None:
            raise BranchNotFound(f"ramo não encontrado: {name}")
        if mode == "final":
            row.last_final = data
        else:
            row.last_preview = data


def _close(project_id: str, name: str, status: str) -> None:
    with session_scope() as s:
        row = _open_row(s, project_id, name)
        row.status = status
        row.closed_at = datetime.now(UTC)


def discard(project_id: str, name: str) -> dict[str, Any]:
    _close(project_id, name, "descartado")
    return get_branch(project_id, name)


# --------------------------------------------------------------------------------------------
# Comparação
# --------------------------------------------------------------------------------------------


def _aligned_segments(a: ProjectDoc, b: ProjectDoc) -> list[str]:
    """Ids dos trechos na ordem da A, com os trechos que só existem na B logo depois do vizinho anterior deles."""
    order = [s.id for s in a.segments]
    for i, seg in enumerate(b.segments):
        if seg.id in order:
            continue
        prev = next((b.segments[j].id for j in range(i - 1, -1, -1) if b.segments[j].id in order), None)
        order.insert(order.index(prev) + 1 if prev else 0, seg.id)
    return order


def _scene_text(doc: ProjectDoc, scene: Any, lang: str) -> str:
    card = scene.card.resolved(lang, doc.primary_language) if scene.card else {}
    return scene.visual.prompt or scene.overlay_text.get(lang, "") or " · ".join(
        str(card[k]) for k in ("value", "label", "headline") if card.get(k))


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json") if model is not None else None


def _segment_state(doc: ProjectDoc, seg_id: str) -> Any:
    """Texto e cenas de um trecho (None: o trecho não existe nesta versão), para saber se ele mudou."""
    if not any(s.id == seg_id for s in doc.segments):
        return None
    return {"segment": _dump(doc.segment(seg_id)), "scenes": [_dump(sc) for sc in doc.scenes_of(seg_id)]}


def _title_state(doc: ProjectDoc) -> Any:
    return [doc.title, {k: _dump(v) for k, v in doc.metadata.items()}, _dump(doc.thumbnail)]


def _style_state(doc: ProjectDoc) -> Any:
    return [doc.style_id, doc.style_overrides, [_dump(m) for m in doc.music]]


def _base_doc(project_id: str, b_info: dict[str, Any]) -> ProjectDoc | None:
    """Versão de onde o ramo saiu (None: a principal, ou a versão base não existe mais)."""
    if b_info["is_main"] or not b_info.get("base"):
        return None
    try:
        return service.get_doc(project_id, b_info["base"])
    except LookupError:
        return None


def branch_changes(project_id: str, name: str) -> tuple[set[str], set[str]]:
    """O que o ramo mudou em relação à versão de onde saiu: (ids dos trechos, partes "titulo"/"estilo").

    É o que dá para levar para a principal: um trecho que só a A mudou depois de o ramo nascer não entra (levar a
    cópia da B desfaria a mudança da A).
    """
    info = get_branch(project_id, name)
    b = service.get_doc(project_id, info["head"])
    base = _base_doc(project_id, info)
    if base is None:
        return {s.id for s in b.segments}, set(GLOBAL_PARTS)
    ids = {s.id for s in b.segments} | {s.id for s in base.segments}
    segs = {sid for sid in ids if _segment_state(base, sid) != _segment_state(b, sid)}
    parts = {p for p, fn in (("titulo", _title_state), ("estilo", _style_state)) if fn(base) != fn(b)}
    return segs, parts


def compare(project_id: str, a_name: str = MAIN, b_name: str = "", lang: str | None = None) -> dict[str, Any]:
    """Diferenças entre duas versões (cabeças de A e B): trecho a trecho, cenas de cada trecho, título e estilo.

    Compara também com a versão de onde a B saiu: o que mudou só na A depois disso vem com `only_a` (não dá para
    mesclar: a B não mexeu ali).
    """
    a_info, b_info = get_branch(project_id, a_name), get_branch(project_id, b_name)
    a = service.get_doc(project_id, a_info["head"])
    b = service.get_doc(project_id, b_info["head"])
    base = _base_doc(project_id, b_info)
    lang = lang or a.primary_language
    a_ids, b_ids = {s.id for s in a.segments}, {s.id for s in b.segments}
    segments = []
    changed_count = 0
    for index, seg_id in enumerate(_aligned_segments(a, b), 1):
        sa = a.segment(seg_id) if seg_id in a_ids else None
        sb = b.segment(seg_id) if seg_id in b_ids else None
        scenes_a = a.scenes_of(seg_id) if sa else []
        scenes_b = b.scenes_of(seg_id) if sb else []
        by_id_a = {sc.id: sc for sc in scenes_a}
        scene_changes = []
        for sc in scenes_b:
            old = by_id_a.get(sc.id)
            if old is None:
                scene_changes.append({"id": sc.id, "status": "nova", "a": None, "b": _scene_text(b, sc, lang)})
            elif _dump(old) != _dump(sc):
                scene_changes.append({"id": sc.id, "status": "mudou", "a": _scene_text(a, old, lang),
                                      "b": _scene_text(b, sc, lang)})
        b_scene_ids = {sc.id for sc in scenes_b}
        scene_changes += [{"id": sc.id, "status": "saiu", "a": _scene_text(a, sc, lang), "b": None}
                          for sc in scenes_a if sc.id not in b_scene_ids]
        if sa and sb:
            text_changed = _dump(sa) != _dump(sb)
            status = "mudou" if text_changed or scene_changes else "igual"
        else:
            status = "novo" if sb else "saiu"
        only_a = (status != "igual" and base is not None
                  and _segment_state(base, seg_id) == _segment_state(b, seg_id))
        if status != "igual":
            changed_count += 1
        ref = sb or sa
        assert ref is not None
        segments.append({
            "id": seg_id,
            "index": index,
            "role": ref.role,
            "status": status,
            "only_a": only_a,
            "a": sa.text_for(lang, a.primary_language) if sa else None,
            "b": sb.text_for(lang, b.primary_language) if sb else None,
            "scenes_a": len(scenes_a),
            "scenes_b": len(scenes_b),
            "scene_changes": scene_changes,
        })
    meta_a, meta_b = a.metadata.get(lang), b.metadata.get(lang)
    title_diff = _title_state(a) != _title_state(b)
    style_diff = _style_state(a) != _style_state(b)
    title_only_a = title_diff and base is not None and _title_state(base) == _title_state(b)
    style_only_a = style_diff and base is not None and _style_state(base) == _style_state(b)
    parts = [
        {"key": "titulo", "label": "Título, metadados e capa", "changed": title_diff and not title_only_a,
         "only_a": title_only_a,
         "a": (meta_a.title if meta_a and meta_a.title else a.title), "b": (meta_b.title if meta_b and meta_b.title else b.title)},
        {"key": "estilo", "label": "Estilo e trilha", "changed": style_diff and not style_only_a, "only_a": style_only_a,
         "a": a.style_id, "b": b.style_id},
    ]
    return {
        "lang": lang,
        "a": a_info,
        "b": b_info,
        "segments": segments,
        "parts": parts,
        "changed": changed_count + sum(1 for p in parts if p["changed"]),
    }


# --------------------------------------------------------------------------------------------
# Escolher o vencedor
# --------------------------------------------------------------------------------------------


def merge_parts(main: ProjectDoc, branch: ProjectDoc, segment_ids: list[str], parts: list[str]) -> ProjectDoc:
    """Leva para a principal os trechos escolhidos do ramo (texto e cenas do trecho) e as partes globais pedidas."""
    out = main.model_copy(deep=True)
    b_ids = [s.id for s in branch.segments]
    for seg_id in segment_ids:
        in_main = any(s.id == seg_id for s in out.segments)
        if seg_id in b_ids:
            new_seg = branch.segment(seg_id).model_copy(deep=True)
            if in_main:
                out.segments = [new_seg if s.id == seg_id else s for s in out.segments]
            else:
                pos = b_ids.index(seg_id)
                prev = next((b_ids[j] for j in range(pos - 1, -1, -1) if any(s.id == b_ids[j] for s in out.segments)),
                            None)
                at = next(i for i, s in enumerate(out.segments) if s.id == prev) + 1 if prev else 0
                out.segments.insert(at, new_seg)
            new_scenes = [sc.model_copy(deep=True) for sc in branch.scenes_of(seg_id)]
            taken = {sc.id for sc in new_scenes}
            out.scenes = [sc for sc in out.scenes if sc.segment_id != seg_id and sc.id not in taken] + new_scenes
        elif in_main:
            out.segments = [s for s in out.segments if s.id != seg_id]
            out.scenes = [sc for sc in out.scenes if sc.segment_id != seg_id]
        else:
            raise ValueError(f"trecho não encontrado em nenhuma das versões: {seg_id}")
    if "titulo" in parts:
        out.title = branch.title
        out.metadata = {k: v.model_copy(deep=True) for k, v in branch.metadata.items()}
        out.thumbnail = branch.thumbnail.model_copy(deep=True)
    if "estilo" in parts:
        out.style_id = branch.style_id
        out.style_overrides = dict(branch.style_overrides)
        out.music = [m.model_copy(deep=True) for m in branch.music]
    # Cena que reaproveitava a imagem de uma cena que saiu fica sem a referência (mostra a própria imagem).
    scene_ids = {sc.id for sc in out.scenes}
    for sc in out.scenes:
        if sc.visual.reuse_of and sc.visual.reuse_of not in scene_ids:
            sc.visual.reuse_of = None
    return ProjectDoc.model_validate(out.model_dump(mode="json"))


def merge(project_id: str, name: str, *, segment_ids: list[str], parts: list[str] | None = None,
          close: bool = True) -> int:
    """Mescla partes do ramo na principal (nova versão na principal). Com `close`, o ramo fica como mesclado."""
    parts = [p for p in (parts or []) if p in GLOBAL_PARTS]
    if not segment_ids and not parts:
        raise ValueError("escolha pelo menos um trecho ou parte para mesclar")
    info = get_branch(project_id, name)
    if info["is_main"]:
        raise ValueError("escolha um ramo para mesclar na principal")
    if info["status"] != OPEN:
        raise service.BranchNotOpen(f"a versão {info['letter']} já foi fechada ({info['status']})")
    seg_changed, parts_changed = branch_changes(project_id, name)
    if any(sid not in seg_changed for sid in segment_ids) or any(p not in parts_changed for p in parts):
        letter = info["letter"]
        raise ValueError(f"escolha só o que a versão {letter} mudou: o resto mudou só na A depois que a {letter} "
                         f"nasceu, e levar a cópia da {letter} desfaria essa mudança")
    main = service.get_doc(project_id)
    branch = service.get_doc(project_id, info["head"])
    merged = merge_parts(main, branch, segment_ids, parts)
    labels = []
    index = {sid: i for i, sid in enumerate(_aligned_segments(main, branch), 1)}
    labels += [f"trecho {index.get(s, '?')}" for s in segment_ids]
    labels += ["título" if p == "titulo" else "estilo" for p in parts]
    version = service.commit(project_id, merged, f"Mesclou da versão {info['letter']}: {', '.join(labels)}")
    if close:
        _close(project_id, name, "mesclado")
    return version


def promote(project_id: str, name: str) -> int:
    """O ramo inteiro vira a principal: nova versão na principal com o conteúdo da cabeça do ramo.

    A prévia e o vídeo final do ramo passam a ser os da principal quando foram gerados da cabeça dele (os arquivos
    continuam na pasta do ramo); a principal antiga continua no histórico (dá para voltar a ela).
    """
    info = get_branch(project_id, name)
    if info["is_main"]:
        raise ValueError("a versão A já é a principal")
    if info["status"] != OPEN:
        raise service.BranchNotOpen(f"a versão {info['letter']} já foi fechada ({info['status']})")
    doc = service.get_doc(project_id, info["head"])
    version = service.commit(project_id, doc, f"Versão {info['letter']} virou a principal (era a v{info['head']})")
    # As miniaturas do teste A/B eram da A antiga: a principal nova faz as dela.
    patch: dict[str, Any] = {"thumbs": {}}
    copied: dict[str, str] = {}
    for mode in ("preview", "final"):
        result = info[f"last_{mode}"]
        if result and result.get("version") == info["head"]:
            patch[f"last_{mode}"] = _adopt_outputs(project_id, result, info["head"], version, copied)
    service.update_settings(project_id, patch)
    _close(project_id, name, "promovido")
    return version


_ADOPTED_FILES = ("timeline", "srt", "video", "thumbnail")


def _adopt_outputs(project_id: str, result: dict[str, Any], old_version: int, version: int,
                   copied: dict[str, str]) -> dict[str, Any]:
    """Copia a timeline, as legendas, o vídeo e a thumbnail do ramo para a pasta da principal.

    A rota da timeline, a aba Prévia e a conferência antes de publicar leem `outputs/<projeto>/<idioma>`; a timeline
    copiada passa a dizer a versão nova da principal. `copied` guarda o que já foi copiado (prévia e vídeo final
    dividem a mesma timeline). Devolve o resultado com os caminhos novos.
    """
    settings = get_settings()
    data_dir = settings.data_dir
    langs: dict[str, Any] = {}
    for lang, entry in (result.get("langs") or {}).items():
        if not isinstance(entry, dict):
            langs[lang] = entry
            continue
        target_dir = settings.outputs_dir / project_id / lang
        new_entry = dict(entry)
        for key in _ADOPTED_FILES:
            rel = entry.get(key)
            if not rel:
                continue
            if rel in copied:
                new_entry[key] = copied[rel]
                continue
            src = data_dir / rel
            if not src.is_file():
                continue
            target_dir.mkdir(parents=True, exist_ok=True)
            dst = target_dir / src.name
            if key == "timeline":
                data = json.loads(src.read_text(encoding="utf-8"))
                if data.get("version") in (None, old_version):
                    data["version"] = version
                dst.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            else:
                shutil.copyfile(src, dst)
            copied[rel] = dst.resolve().relative_to(data_dir.resolve()).as_posix()
            new_entry[key] = copied[rel]
        langs[lang] = new_entry
    return {**result, "langs": langs, "version": version}

