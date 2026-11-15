"""Agente de edição por instrução (README §6.4).

O LLM recebe um resumo compacto do projeto (uma linha por segmento e por cena), devolve operações
pequenas e tipadas, e o app valida tudo antes de mostrar a prévia de custo. Nada é aplicado sem você.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel

from studio.core.project import ops as ops_mod
from studio.core.project import service
from studio.core.project.document import ProjectDoc
from studio.core.project.ops import OpError, apply_ops, parse_ops
from studio.core.styles.library import deep_merge, get_style, list_styles, set_path
from studio.core.styles.schema import GRADE_PRESETS, StyleSpec
from studio.media.audio import MOODS
from studio.providers import registry
from studio.workers.pipeline.schemas import EDIT_SCHEMA, STYLE_FIELDS_HELP, STYLE_PATCH_SCHEMA

_OP_CLASSES: dict[str, type[BaseModel]] = {}
for _name in dir(ops_mod):
    _obj = getattr(ops_mod, _name)
    if isinstance(_obj, type) and issubclass(_obj, BaseModel) and "op" in getattr(_obj, "model_fields", {}):
        default = _obj.model_fields["op"].default
        if isinstance(default, str):
            _OP_CLASSES[default] = _obj


EDIT_SYSTEM = """Você é o editor de um estúdio de vídeos narrados para canais sem rosto ("dark"), de qualquer nicho. Você NÃO edita arquivos: você propõe
operações pequenas sobre o documento do projeto, e o app regenera só o que mudou. Regras:
- Use o menor conjunto de operações que atende o pedido. Não mexa no que não foi pedido.
- Luz, cor, contraste, calor, desfoque de UMA cena: set_scene_look (sem gerar imagem nova; valores de -1 a 1).
- Conteúdo diferente na imagem: update_scene_prompt (escreva o prompt completo novo, sem texto na imagem).
- "Outra versão/variação" da mesma imagem: regenerate_scene. Cena com "mídia real" (foto, notícia ou material
  anexado pelo usuário): não use regenerate_scene nela; a mídia se troca no painel Mídia real da aba Cenas.
- Câmera: set_scene_motion. Transição de entrada: set_scene_transition. Tipo de cena: set_scene_kind.
- Mudança de texto narrado: update_segment_text (texto completo do segmento, no idioma indicado).
- Ajustes globais do estilo (legendas, efeitos, ritmo, trilha): set_style com path e value_json (JSON válido).
- Trocar o estilo do vídeo por outro estilo da lista: set_style_id (style_id exato), sozinha, só quando pedido. A direção de
  arte é refeita quando as cenas forem refeitas; até lá, as imagens novas esperam (diga isso no summary).
- Música: set_music (mood, volume_db, enabled). Travar/destravar: set_lock.
- Respeite itens [travado]/travada: não proponha operações neles.
- Quando o usuário citar "cena N" ou "segmento N", use a numeração do resumo (C1, C2... e S1, S2...).
- Preencha todos os campos de cada operação; use null nos que não se aplicam. Em 'reason', explique em poucas palavras.
- 'summary' descreve em português, em uma ou duas frases, o que será feito.

Valores válidos: movimentos de câmera ken_burns_in, ken_burns_out, pan_left, pan_right, pan_up, pan_down, punch_zoom, drift,
static, zoom_to (aproxima do foco da cena); transições cut, flash, fade_black, glitch, light_leak, zoom_blur, film_burn, static_burst; tipos de cena image,
text, title, count, document, quote; grade (set_scene_look.grade) """ + ", ".join(GRADE_PRESETS) + """;
climas da música (set_music.mood) """ + ", ".join(MOODS) + """.
""" + STYLE_FIELDS_HELP


def numbered_outline(doc: ProjectDoc, lang: str, max_chars: int = 110) -> tuple[str, list[str], list[str]]:
    lines = [f"Projeto: {doc.title} | estilo {doc.style_id} | idiomas {', '.join(doc.languages)}"]
    scene_ids: list[str] = []
    segment_ids: list[str] = []
    for s_index, seg in enumerate(doc.segments, 1):
        segment_ids.append(seg.id)
        text = seg.text_for(lang, doc.primary_language).replace("\n", " ")
        lock = " [travado]" if seg.locked else ""
        lines.append(f"S{s_index} id={seg.id} ({seg.role}){lock}: {text[:max_chars]}")
        for scene in doc.scenes_of(seg.id):
            scene_ids.append(scene.id)
            extras = [f"tipo={scene.kind}"]
            if scene.motion:
                extras.append(f"câmera={scene.motion}")
            if scene.transition_in:
                extras.append(f"transição={scene.transition_in}")
            look = {k: v for k, v in scene.look.model_dump().items() if v}
            if look:
                extras.append(f"look={json.dumps(look, ensure_ascii=False)}")
            if scene.visual.reuse_of:
                extras.append(f"reaproveita a imagem de {scene.visual.reuse_of}")
            if scene.visual.media is not None:
                extras.append("mídia real")
            if scene.locked:
                extras.append("travada")
            card = scene.card.resolved(lang, doc.primary_language) if scene.card else {}
            desc = scene.visual.prompt or scene.overlay_text.get(lang, "") or " · ".join(
                str(card[k]) for k in ("value", "label", "headline") if k in card
            )
            lines.append(f"   C{len(scene_ids)} id={scene.id} [{', '.join(extras)}]: {desc[:max_chars]}")
    return "\n".join(lines), scene_ids, segment_ids


def flat_to_ops(flat: list[dict[str, Any]]) -> list[Any]:
    """Converte operações 'achatadas' (todos os campos anuláveis) em operações tipadas e validadas."""
    raw: list[dict[str, Any]] = []
    for item in flat:
        name = item.get("op")
        cls = _OP_CLASSES.get(name or "")
        if cls is None:
            raise OpError(f"operação desconhecida: {name}")
        fields = set(cls.model_fields)
        data: dict[str, Any] = {"op": name}
        for key, value in item.items():
            if key in ("op", "value_json") or value is None or key not in fields:
                continue
            data[key] = value
        if name == "set_style":
            try:
                data["value"] = json.loads(item.get("value_json") or "null")
            except json.JSONDecodeError as exc:
                raise OpError(f"value_json inválido em set_style: {exc}") from exc
        if name in ("set_scene_motion", "set_scene_transition"):
            key = "motion" if name == "set_scene_motion" else "transition"
            data.setdefault(key, None)
        raw.append(data)
    return parse_ops(raw)


def _llm(task: str, system: str, prompt: str, schema: dict[str, Any], context: dict[str, Any], project_id: str | None):
    chain = registry.resolve_chain("llm", None)
    result, provider, _ = registry.run_chain(
        "llm",
        chain,
        lambda p: p.complete_json(task=task, system=system, prompt=prompt, schema=schema, context=context),
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
        project_id=project_id,
    )
    return result, provider.info.id


def _style_choices() -> str:
    """Estilos que a op set_style_id aceita (id: nome)."""
    return "; ".join(f"{item['style']['id']}: {item['style']['name']}" for item in list_styles())


def propose_edit(project_id: str, instruction: str, *, scope_scene_ids: list[str] | None = None, lang: str | None = None) -> dict[str, Any]:
    from studio.workers.pipeline.build import plan

    row = service.get_project_row(project_id)
    doc = service.get_doc(project_id)
    lang = lang or doc.primary_language
    style = get_style(doc.style_id, doc.style_overrides)
    outline, scene_ids, segment_ids = numbered_outline(doc, lang)
    scope_text = "vídeo inteiro"
    if scope_scene_ids:
        labels = [f"C{scene_ids.index(s) + 1} ({s})" for s in scope_scene_ids if s in scene_ids]
        scope_text = "apenas as cenas " + ", ".join(labels)
    prompt = f"""{outline}

Estilo atual (resumo): legendas {style.edit.captions.style} tamanho {style.edit.captions.size} cor {style.edit.captions.color};
efeitos {", ".join(e.type for e in style.edit.effects)}; música '{style.audio.music.mood}' a {style.audio.music.volume_db} dB.
Estilos disponíveis (set_style_id): {_style_choices()}
Idioma da instrução e dos textos: {lang}
Escopo selecionado: {scope_text}

Instrução do usuário: {instruction}"""
    context = {
        "instruction": instruction,
        "scene_ids": scene_ids,
        "segment_ids": segment_ids,
        "scope_scene_ids": scope_scene_ids or [],
        "captions_size": style.edit.captions.size,
        "lang": lang,
    }
    result, provider_id = _llm("edit", EDIT_SYSTEM, prompt, EDIT_SCHEMA, context, project_id)
    flat = result.data.get("ops", [])
    ops = flat_to_ops(flat) if flat else []
    new_doc = apply_ops(doc, ops, get_style(doc.style_id)) if ops else doc
    # A edição aplicada refaz a prévia (narração pela voz local, sem cota).
    estimate = plan(project_id, doc=new_doc, mode="preview") if ops else None
    return {
        "summary": result.data.get("summary", ""),
        "ops": [op.model_dump(mode="json") for op in ops],
        "estimate": estimate,
        "provider": provider_id,
        "base_version": row["current_version"],
    }


def apply_edit(project_id: str, raw_ops: list[dict[str, Any]], *, message: str, author: str = "ai-edit") -> int:
    doc = service.get_doc(project_id)
    ops = parse_ops(raw_ops)
    new_doc = apply_ops(doc, ops, get_style(doc.style_id))
    return service.commit(project_id, new_doc, message, author=author)


STYLE_SYSTEM = """Você ajusta estilos de vídeo (StyleSpec) a partir de descrições em linguagem natural. Devolva apenas as
mudanças necessárias como pares path/value_json (JSON válido), sem inventar campos. Mantenha o que não foi pedido.
""" + STYLE_FIELDS_HELP


def style_from_text(description: str, base_style_id: str, *, new_id: str | None = None, name: str | None = None) -> dict[str, Any]:
    base = get_style(base_style_id)
    data = base.model_dump(mode="json")
    prompt = f"""Estilo base ({base.id}):
{json.dumps({k: data[k] for k in ("family", "structure", "format", "voice", "art", "edit", "audio")}, ensure_ascii=False)[:6000]}

Descrição do novo estilo: {description}"""
    context = {"description": description, "effects": data["edit"]["effects"]}
    result, provider_id = _llm("style", STYLE_SYSTEM, prompt, STYLE_PATCH_SCHEMA, context, None)
    applied, rejected = [], []
    for change in result.data.get("changes", []):
        path = change.get("path", "")
        try:
            value = json.loads(change.get("value_json") or "null")
            candidate = set_path(data, path, value)
            StyleSpec.model_validate(candidate)
            data = candidate
            applied.append({"path": path, "value": value, "reason": change.get("reason", "")})
        except Exception as exc:  # noqa: BLE001 - mudança inválida é descartada e reportada
            rejected.append({"path": path, "error": str(exc)[:300]})
    data["id"] = new_id or f"{base.id}-custom"
    data["name"] = name or f"{base.name} (personalizado)"
    data["description"] = description[:500]
    style = StyleSpec.model_validate(data)
    return {
        "style": style.model_dump(mode="json"),
        "summary": result.data.get("summary", ""),
        "applied": applied,
        "rejected": rejected,
        "provider": provider_id,
        "base_style_id": base.id,
    }


def merge_style(base_id: str, overrides: dict[str, Any]) -> StyleSpec:
    return StyleSpec.model_validate(deep_merge(get_style(base_id).model_dump(mode="json"), overrides))
