"""Etapas de autoria: geram/alteram o documento do projeto usando a cadeia de LLM."""

from __future__ import annotations

import logging
from typing import Any

from studio.core.hashing import seed_from
from studio.core.project.document import Metadata, ProjectDoc, Scene, Segment, Source, Visual
from studio.core.styles.schema import Structure, StyleSpec
from studio.core.text import estimate_seconds, split_balanced
from studio.core.text import words as split_words
from studio.providers import registry
from studio.providers.base import LLMResult
from studio.workers.pipeline.schemas import (
    METADATA_SCHEMA,
    SCRIPT_SCHEMA,
    STORYBOARD_SCHEMA,
    TRANSLATE_SCHEMA,
)

log = logging.getLogger(__name__)

LANG_NAMES = {
    "pt-BR": "português do Brasil",
    "pt-PT": "português de Portugal",
    "en-US": "inglês (EUA)",
    "en-GB": "inglês (Reino Unido)",
    "es-ES": "espanhol (Espanha)",
    "es-MX": "espanhol (México)",
    "fr-FR": "francês",
    "de-DE": "alemão",
    "it-IT": "italiano",
    "ja-JP": "japonês",
    "ko-KR": "coreano",
    "hi-IN": "hindi",
    "ar-SA": "árabe",
    "ru-RU": "russo",
    "tr-TR": "turco",
    "id-ID": "indonésio",
}


def lang_name(code: str) -> str:
    return LANG_NAMES.get(code, code)


def _llm(task: str, system: str, prompt: str, schema: dict[str, Any], context: dict[str, Any],
         project_id: str | None, job_id: str | None) -> tuple[LLMResult, str]:
    chain = registry.resolve_chain("llm", None)

    def call(provider):
        return provider.complete_json(task=task, system=system, prompt=prompt, schema=schema, context=context)

    result, provider, _ = registry.run_chain(
        "llm",
        chain,
        call,
        usage_of=lambda r: (r.usage.units, r.usage.unit_name, r.usage.cost_usd),
        project_id=project_id,
        job_id=job_id,
    )
    return result, provider.info.id


SCRIPT_SYSTEM = """Você é roteirista de canais "dark" (sem rosto) do YouTube, TikTok e Reels.
Escreva narrações originais, envolventes e fáceis de ouvir: frases curtas, ritmo de fala, nada de markdown,
emojis, listas ou indicações de cena. Cada segmento é um bloco de narração contínua.
Regras de qualidade: conteúdo original, com ângulo próprio e informação real; nunca invente fatos apresentados como
verdadeiros; quando o tema for real, use só fatos verificáveis e liste as fontes. Evite frases genéricas e repetitivas.
Escreva números de forma natural para leitura em voz alta."""


def generate_script(doc: ProjectDoc, style: StyleSpec, *, project_id: str | None = None, job_id: str | None = None,
                    persona: str = "") -> tuple[ProjectDoc, str]:
    lang = doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150)
    target_words = max(30, round(style.format.duration.target_s * wpm / 60 * style.voice.speed))
    structure = style.script.structure
    is_top = style.structure == Structure.top10
    prompt = f"""Tema: {doc.topic or doc.title}
Briefing: {doc.brief or "(nenhum)"}
Idioma da narração: {lang_name(lang)}
Formato: {"Short vertical (até 3 min)" if style.format.type == "short" else "vídeo longo"} com cerca de {style.format.duration.target_s:.0f} segundos.
Tamanho: cerca de {target_words} palavras no total (siga isso de perto).
Tom: {style.script.tone}
Estrutura (um ou mais segmentos por etapa, nesta ordem): {", ".join(structure)}
O gancho inicial precisa prender em até {style.script.hook_max_s:.0f} segundos.
Chamada final: {style.script.cta or "uma frase curta convidando a seguir o canal"}
Proibido usar: {", ".join(style.script.banned_phrases) or "(nada)"}
{"Formato Top 10: cada item é um segmento com role 'item' e number de 10 até 1." if is_top else ""}
{"Inclua fontes verificáveis em 'sources'." if style.script.fact_check else "Se não houver fontes, deixe 'sources' vazio."}
{f"Persona do canal: {persona}" if persona else ""}
Use role 'hook' no primeiro segmento, 'chapter' para aberturas de capítulo, 'cta' no último e 'body' no resto.
Use speaker 'narrator' (ou o nome de um personagem, se houver diálogo)."""
    context = {
        "topic": doc.topic or doc.title,
        "structure": structure,
        "target_words": target_words,
        "cta": style.script.cta,
        "structure_kind": style.structure.value,
    }
    result, provider_id = _llm("script", SCRIPT_SYSTEM, prompt, SCRIPT_SCHEMA, context, project_id, job_id)
    data = result.data
    new_doc = doc.model_copy(deep=True)
    segments = []
    for item in data.get("segments", []):
        text = (item.get("text") or "").strip()
        if not text:
            continue
        segments.append(
            Segment(
                role=item.get("role") or "body",
                speaker=item.get("speaker") or "narrator",
                chapter=item.get("chapter"),
                text={lang: text},
            )
        )
        if item.get("number") is not None:
            segments[-1].chapter = segments[-1].chapter or f"#{item['number']}"
    if not segments:
        raise ValueError("o LLM não devolveu nenhum segmento de roteiro")
    new_doc.segments = segments
    new_doc.scenes = []
    new_doc.script_approved = False
    if data.get("title"):
        new_doc.title = data["title"].strip()[:200]
    new_doc.sources = [Source(**s) for s in data.get("sources", []) if s.get("title")]
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


STORYBOARD_SYSTEM = """Você é diretor de arte de vídeos narrados. Para cada cena, escreva um prompt de imagem detalhado
(em inglês ou português) que ilustre aquele trecho da narração, sempre no estilo de arte indicado. Regras:
- NUNCA peça texto, letras, números, legendas, logotipos ou marcas d'água dentro da imagem.
- Descreva composição, sujeito, ambiente, luz e enquadramento; varie planos (aberto, médio, detalhe) entre cenas.
- Mantenha personagens e lugares recorrentes consistentes entre as cenas.
- Para cenas do tipo title/count/text, escreva também o texto curto da tela em overlay_text (máx. 6 palavras)."""


def _parts_for(text: str, style: StyleSpec, lang: str) -> int:
    wpm = style.script.words_per_minute.get(lang, 150)
    seconds = estimate_seconds(text, wpm, style.voice.speed)
    avg = (style.edit.shot_seconds.min + style.edit.shot_seconds.max) / 2
    return max(1, round(seconds / max(0.5, avg)))


def storyboard(doc: ProjectDoc, style: StyleSpec, *, project_id: str | None = None, job_id: str | None = None) -> tuple[ProjectDoc, str]:
    lang = doc.primary_language
    keep_segments = {s.segment_id for s in doc.scenes if s.locked}
    plan: list[dict[str, Any]] = []
    for index, seg in enumerate(doc.segments):
        if seg.id in keep_segments:
            continue
        text = seg.text_for(lang)
        parts = _parts_for(text, style, lang)
        groups = split_balanced(split_words(text), parts)
        for k, group in enumerate(groups):
            kind_hint = "image"
            overlay = None
            if k == 0 and seg.role == "chapter":
                kind_hint, overlay = "title", seg.chapter or None
            elif k == 0 and seg.role == "item" and style.structure == Structure.top10:
                kind_hint, overlay = "count", seg.chapter
            plan.append({"key": f"{seg.id}:{k}", "segment_id": seg.id, "part": k, "parts": len(groups),
                         "text": " ".join(group), "kind_hint": kind_hint, "overlay_hint": overlay, "index": index})
    if not plan:
        return doc, "nenhum"
    listing = "\n".join(f"- {p['key']} [{p['kind_hint']}]: {p['text']}" for p in plan)
    prompt = f"""Estilo de arte: {style.art.bible}
Evitar: {", ".join(style.art.banned)}
Formato da imagem: {style.format.aspect} ({"vertical" if style.format.height > style.format.width else "horizontal"})
{"Arte fotorrealista permitida." if style.art.photoreal else "Prefira arte estilizada (pintura, ilustração), não fotorrealista."}
Tema do vídeo: {doc.topic or doc.title}

Cenas (chave [tipo sugerido]: trecho narrado):
{listing}

Devolva uma entrada em 'scenes' para cada chave, mantendo a chave exata. Use o tipo sugerido."""
    context = {"bible": style.art.bible, "scenes": plan}
    result, provider_id = _llm("storyboard", STORYBOARD_SYSTEM, prompt, STORYBOARD_SCHEMA, context, project_id, job_id)
    by_key = {item["key"]: item for item in result.data.get("scenes", []) if item.get("key")}
    new_doc = doc.model_copy(deep=True)
    new_doc.scenes = [s for s in new_doc.scenes if s.segment_id in keep_segments]
    for p in plan:
        item = by_key.get(p["key"], {})
        kind = item.get("kind") or p["kind_hint"]
        if kind not in ("image", "text", "title", "count", "document", "quote"):
            kind = "image"
        overlay = item.get("overlay_text") or p.get("overlay_hint")
        number = None
        if kind == "count":
            seg = new_doc.segment(p["segment_id"])
            digits = "".join(ch for ch in (seg.chapter or overlay or "") if ch.isdigit())
            number = int(digits) if digits else None
        new_doc.scenes.append(
            Scene(
                segment_id=p["segment_id"],
                part=p["part"],
                parts=p["parts"],
                kind=kind,  # type: ignore[arg-type]
                visual=Visual(prompt=(item.get("prompt") or f"{style.art.bible}. {p['text']}").strip(),
                              seed=seed_from(doc.seed, p["segment_id"], p["part"])),
                overlay_text={lang: overlay} if overlay else {},
                number=number,
            )
        )
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


TRANSLATE_SYSTEM = """Você é tradutor e adaptador de roteiros de vídeo narrado. Traduza mantendo o tom, o ritmo de fala e
a duração aproximada de cada segmento (o mesmo número de sílabas faladas, mais ou menos 10%). Adapte expressões, unidades
e referências culturais. Não acrescente nem remova informações. Mantenha os IDs exatamente iguais."""


def translate(doc: ProjectDoc, target_lang: str, *, project_id: str | None = None, job_id: str | None = None) -> tuple[ProjectDoc, str]:
    source = doc.primary_language
    segments = [{"id": s.id, "text": s.text_for(source)} for s in doc.segments]
    overlays = [{"id": sc.id, "text": sc.overlay_text[source]} for sc in doc.scenes if sc.overlay_text.get(source)]
    prompt = f"""Traduza de {lang_name(source)} para {lang_name(target_lang)}.
Título: {doc.title}
Segmentos:
""" + "\n".join(f"- {s['id']}: {s['text']}" for s in segments) + ("\nTextos de tela:\n" + "\n".join(f"- {o['id']}: {o['text']}" for o in overlays) if overlays else "")
    context = {"target_lang": target_lang, "title": doc.title, "segments": segments, "overlays": overlays}
    result, provider_id = _llm("translate", TRANSLATE_SYSTEM, prompt, TRANSLATE_SCHEMA, context, project_id, job_id)
    new_doc = doc.model_copy(deep=True)
    seg_text = {item["id"]: item["text"] for item in result.data.get("segments", [])}
    ov_text = {item["id"]: item["text"] for item in result.data.get("overlays", [])}
    for seg in new_doc.segments:
        if seg.id in seg_text and seg_text[seg.id].strip():
            seg.text[target_lang] = seg_text[seg.id].strip()
    for scene in new_doc.scenes:
        if scene.id in ov_text:
            scene.overlay_text[target_lang] = ov_text[scene.id]
    if target_lang not in new_doc.languages:
        new_doc.languages.append(target_lang)
    meta = new_doc.metadata.get(target_lang) or Metadata()
    if result.data.get("title") and not meta.title:
        meta.title = result.data["title"]
    new_doc.metadata[target_lang] = meta
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


METADATA_SYSTEM = """Você é especialista em SEO de YouTube, TikTok e Reels. Escreva título chamativo e honesto (sem clickbait
enganoso, máx. 90 caracteres), descrição útil (resumo, capítulos se houver, fontes se houver), tags relevantes e hashtags.
O texto da thumbnail deve ter no máximo 4 palavras fortes."""


def metadata(doc: ProjectDoc, style: StyleSpec, lang: str, *, chapters: list[str] | None = None,
             project_id: str | None = None, job_id: str | None = None) -> tuple[ProjectDoc, str]:
    text = doc.full_text(lang)
    sources = "\n".join(f"- {s.title} {s.url}".strip() for s in doc.sources)
    prompt = f"""Idioma: {lang_name(lang)}
Plataforma principal: {"YouTube Shorts / TikTok / Reels" if style.format.type == "short" else "YouTube (vídeo longo)"}
Título de trabalho: {doc.title}
Capítulos (tempo e nome): {", ".join(chapters or []) or "(nenhum)"}
Fontes: {sources or "(nenhuma)"}
Narração completa:
{text[:6000]}"""
    context = {"title": doc.title, "topic": doc.topic, "summary": text[:300], "chapters": chapters or []}
    result, provider_id = _llm("metadata", METADATA_SYSTEM, prompt, METADATA_SCHEMA, context, project_id, job_id)
    data = result.data
    new_doc = doc.model_copy(deep=True)
    description = data.get("description", "")
    if sources and "http" not in description:
        description += "\n\nFontes:\n" + sources
    new_doc.metadata[lang] = Metadata(
        title=(data.get("title") or doc.title)[:100],
        description=description.strip(),
        tags=[t for t in data.get("tags", []) if t][:30],
        hashtags=[h if h.startswith("#") else f"#{h}" for h in data.get("hashtags", []) if h][:15],
    )
    if data.get("thumbnail_text"):
        new_doc.thumbnail.text[lang] = data["thumbnail_text"][:60]
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id
