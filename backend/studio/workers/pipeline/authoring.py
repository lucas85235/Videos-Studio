"""Etapas de autoria: geram/alteram o documento do projeto usando a cadeia de LLM."""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from studio.core.hashing import seed_from
from studio.core.numbers import card_number, notable_number_runs, number_runs
from studio.core.project.document import (
    DATA_KINDS,
    REAL_KINDS,
    ArtDirection,
    CardItem,
    Entity,
    Metadata,
    ProjectDoc,
    RealHint,
    Scene,
    SceneCard,
    Segment,
    Source,
    Visual,
)
from studio.core.regions import find_regions, region_code
from studio.core.styles.schema import CAMERA_MOVES, CHART_TYPES, GRADE_PRESETS, Family, Structure, StyleSpec
from studio.core.styles.visual import (
    APPROACHES,
    COMPOSITIONS,
    FOCUS_POINTS,
    FOCUS_ROTATION,
    GENRES,
    SHOT_ROTATION,
    SHOTS,
    STYLE_FITS,
    VISUAL_LANGUAGES,
    catalog_text,
    clean_id,
    clean_languages,
    compositions_for_caption,
    pick,
)
from studio.core.text import estimate_seconds, normalize_token, split_beats
from studio.providers import registry
from studio.providers.base import LLMResult
from studio.workers.pipeline import storyboard_check
from studio.workers.pipeline.schemas import (
    METADATA_SCHEMA,
    SCRIPT_SCHEMA,
    STORYBOARD_KINDS,
    STORYBOARD_SCHEMA,
    STORYBOARD_SOURCES,
    TRANSLATE_SCHEMA,
)
from studio.workers.pipeline.storyboard_check import CORRIGIDO, Beat, Issue

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


SCRIPT_SYSTEM = """Você é roteirista de canais sem rosto ("dark") do YouTube, TikTok e Reels, de qualquer nicho: curiosidades,
história, finanças, tecnologia, biografias, histórias narradas, listas, mistério e outros. Siga o tom pedido pelo estilo.
Escreva narrações originais, envolventes e fáceis de ouvir: frases curtas, ritmo de fala, nada de markdown,
emojis, listas ou indicações de cena. Cada segmento é um bloco de narração contínua.
Regras de qualidade: conteúdo original, com ângulo próprio e informação real; nunca invente fatos apresentados como
verdadeiros; quando o tema for real, use só fatos verificáveis e liste as fontes. Evite frases genéricas e repetitivas.
Escreva números de forma natural para leitura em voz alta.
Datas: o pedido traz a data de hoje. Se o tema tem data marcada (lançamento, estreia, evento) perto de hoje, diga a
data e quanto falta ("chega em <dia> de <mês>, daqui a <n> dias"). Evite afirmação que vence em poucos dias ("ainda
não saiu", "ninguém jogou") sem dizer a data ("até hoje, <dia> de <mês>")."""


def generate_script(doc: ProjectDoc, style: StyleSpec, *, project_id: str | None = None, job_id: str | None = None,
                    persona: str = "", today: date | None = None) -> tuple[ProjectDoc, str]:
    """Roteiro do tema. `today` (padrão: a data de hoje) entra no pedido para o roteiro situar datas próximas."""
    today = today or date.today()
    lang = doc.primary_language
    wpm = style.script.words_per_minute.get(lang, 150)
    target_words = max(30, round(style.format.duration.target_s * wpm / 60 * style.voice.speed))
    structure = style.script.structure
    is_top = style.structure == Structure.top10
    prompt = f"""Data de hoje: {today.isoformat()}
Tema: {doc.topic or doc.title}
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
    new_doc.storyboard_approved = False
    if data.get("title"):
        new_doc.title = data["title"].strip()[:200]
    new_doc.sources = [Source(**s) for s in data.get("sources", []) if s.get("title")]
    return ProjectDoc.model_validate(new_doc.model_dump()), provider_id


STORYBOARD_SYSTEM = """Você é o diretor de arte e o editor de um canal sem rosto (Shorts, TikTok, Reels e vídeos longos).
O roteiro já foi aprovado e chega cortado em batidas (uma frase ou ideia por batida). Sua tarefa é a
curadoria visual: escolher a linguagem visual certa para ESTE assunto e decidir, batida por batida, o que
aparece na tela usando só o que o app sabe produzir.

1) Direção de arte ("direction"), decidida pelo assunto e pelo roteiro, não pelo nome do estilo:
- genre: o tipo de vídeo (notícia de games não é mistério, mesmo com estilo de mistério).
- approach: literal (mostra o assunto), misto ou alegorico (metáforas).
- world: só o cenário, lugar e época, em até 20 palavras em inglês ("big Brazilian city, 1990s"), sem hora do dia,
  clima nem céu (cada imagem diz os seus). O app junta o world a TODA imagem nova: nada de pessoas, objetos ou ações
  nele. Personagem, lugar ou objeto que volta em várias batidas vira entidade (só entra nas imagens que a citam); o
  que é de uma batida só vai no prompt dela.
- languages: 1 a 3 ids do catálogo, o primeiro dominante. Prefira os do estilo; se não combinarem com o
  assunto, escolha os que combinam e diga em style_fit/style_note.
- treatment: contraste, saturação e textura em até 12 palavras, em inglês, SEM hora do dia, clima, céu, luz ou
  objetos. Use o tratamento do estilo como referência e adapte ao assunto.
- palette: 3 cores do mundo do assunto (entram como cores de destaque, não pintam a imagem toda). grade: um id de
  gradação, ou null para manter o do estilo.
- entities: personagens, lugares ou objetos que voltam em mais de uma batida, com id curto ("detetive"), nome, real e
  aparência fixa em inglês; o look começa por quem ou o que é ("tall woman in her 40s, ...", "seaside city with ...");
  o look de lugar não fixa céu, hora nem luz.
  real: true só para figura histórica já falecida ou lugar, monumento, navio ou evento reais (o prompt pode citar
  pelo nome). Personagem de ficção, marca, produto ou pessoa viva: real false, aparência ORIGINAL (nunca copie
  personagem oficial, logotipo ou tipografia de marca) e o prompt cita pela aparência ou pelo papel ("the
  detective"), nunca pelo nome. Personagem oficial de ficção que a narração cita pelo nome (protagonista de jogo,
  filme, série) não vira entidade com rosto: mostre de costas, em silhueta, ou por objetos e lugar.
- avoid: objetos, clichês e anacronismos que não pertencem ao assunto, em inglês (inclusive motivos do
  estilo que não servem aqui, como "candles").
- style_fit: ok | fraco | errado; style_note: o porquê numa frase; suggested_style: id da lista ou null.
- hook: o que o primeiro quadro mostra, em inglês.

2) Uma entrada em "scenes" por batida, com a chave exata e na mesma ordem. O que o app desenha:
- image: imagem em tela cheia com câmera. source "new" = imagem nova de IA (~35 s de cota cada; respeite o
  limite); source "reuse" = a imagem de uma batida anterior (reuse_key) com outro enquadramento (focus), custo zero.
- stat: número grande que conta na tela; value com algarismos e unidade curta ("475 MI", "200 MI+", "2013",
  "2"), label até 6 palavras com o qualificador que a fala dá (plataforma, janela de tempo: "views em 24 h no
  YouTube"), mesmo que ele venha na batida seguinte.
- compare: 2 ou 3 itens (label, value), só com números medidos do mesmo jeito (mesma plataforma, mesma janela,
  mesma unidade). Se a fala qualifica um número ("no YouTube", "somando as plataformas"), o qualificador vai no
  label do item; o que é comum aos itens vai em label (o título do cartão). Valores numéricos viram barras
  proporcionais; highlight é o índice (base 0) do item em destaque.
- title: até 5 palavras grandes no centro (overlay_text). text: as palavras da narração aparecem grandes no
  ritmo da fala (pergunta ou frase de impacto). quote: citação curta (overlay_text).
- count: só no formato Top 10, na primeira batida de cada item (o app desenha o número do item).
- chart e map: só quando o pedido trouxer as regras do estilo de dados (senão, use stat ou compare).
Todo cartão (stat, compare, title, text, quote) usa de fundo a imagem de uma batida anterior (source "reuse" e
reuse_key), desfocada e escurecida.
Em qualquer cena: headline (faixa no topo, até 5 palavras), stamp (carimbo, até 2 palavras, ex. "RECORDE"; nunca
"INÉDITO" nem "EXCLUSIVO": as imagens são ilustrações), source_ref (o número da fonte em "Fontes do roteiro" que
traz o dado do cartão, ou null). Campo que não se aplica vai como null. A faixa compete com a legenda: use no
gancho e só onde acrescenta o que a fala não diz (data, nome, virada de assunto: "TRAILER 2 · MAIO 2025", não só
"MAIO DE 2025"), no máximo 1 a cada 3 cenas, sem repetir a fala da batida.

Mídia real (real_kind, real_query, real_note, real_source_ref): marque as batidas em que uma imagem real vale mais
que a de IA: pessoa real citada, lugar real, produto, personagem oficial, notícia ou anúncio, documento. O app não
baixa nada sozinho: ele busca fotos livres e o usuário escolhe, cola um link ou envia o arquivo.
- real_kind: photo (pessoa, lugar ou objeto real que pode ter foto livre no Wikimedia Commons), news (a batida cita
  uma notícia ou anúncio: vira cartão de notícia; real_source_ref = o número da fonte com o link, ou null), official
  (arte, trailer ou foto de imprensa oficial: só o usuário pode anexar).
- real_query: 2 a 6 palavras em inglês para a busca (nome próprio vale: "Miami Beach skyline"); null quando não há o
  que buscar.
- real_note: em português, o que mostrar e onde achar ("arte oficial da Lucia no Newswire da Rockstar").
- Só em kind image, no máximo 1 a cada 3 batidas. Sem mídia real: os quatro null.
- A cena continua com o prompt de IA de reserva (o vídeo funciona sem a mídia real). Com real_kind official de
  personagem, a reserva mostra de costas, em silhueta ou só objetos e lugar, nunca o rosto.

Regras:
1. Mostre o sujeito concreto da batida (pessoa, lugar, objeto, ação, na época real do assunto). Metáfora só
   quando a frase não tiver nada concreto, no máximo 1 a cada 5 imagens.
2. Número, data, valor, recorde ou comparação vira stat ou compare, ou vai em headline/stamp, sempre com
   algarismos. Nunca peça número ou texto dentro da imagem. Use só números da narração ou das fontes.
3. A primeira batida é o gancho: image, source "new", a imagem mais forte e literal do assunto, headline curta,
   motion "punch_zoom".
4. Quando a narração volta ao MESMO momento, lugar, personagem ou objeto, use reuse em vez de outra imagem
   parecida, com outro focus e motion "zoom_to". Batida com outra data, outro evento ou outra headline nunca
   reaproveita imagem em tela cheia (kind image): use imagem nova. A chamada final pode reaproveitar o gancho.
   Espalhe o reaproveitamento: no máximo 3 batidas seguidas na mesma imagem, contando os cartões sobre ela;
   depois alterne com imagem nova ou mídia real.
5. No máximo 2 cartões (stat, compare, title, text, quote) seguidos; não repita o shot em imagens novas seguidas.
6. prompt (só imagem nova): 1 a 3 frases em inglês com sujeito, ação, lugar, época, hora e luz. Cada imagem nova
   diz a própria hora e luz, e elas variam: no máximo 1 em cada 3 no mesmo momento (ex.: pôr do sol); siga o clima
   da narração (passado = luz fria ou de arquivo, revelação = escuro). Tela dentro da imagem (TV, celular, telão)
   não repete o mesmo conteúdo. NÃO escreva estilo, técnica, paleta, "no text" nem "vertical": o app acrescenta.
   Nada de logotipo, personagem oficial, tipografia de marca, interface de jogo, "screenshot", "gameplay", "leak"
   ou cena de trailer. Nome de personagem de ficção, marca, jogo, produto ou pessoa viva não entra no prompt, no
   world, no hook nem no look: descreva o que se vê (figura histórica, cidade e país reais podem). Personagem
   oficial de ficção aparece de costas, em silhueta ou só por objetos, nunca de frente. Na imagem nova, preencha
   também language, shot, composition e entities (ids da direção que aparecem nela).
7. composition: deixe livre a faixa da legenda informada no pedido."""

# Regras dos cartões de gráfico e mapa, só no pedido de estilo da família de dados e geografia.
STORYBOARD_DATA_RULES = """Estilo de dados e geografia: além de stat e compare, o app desenha gráficos e mapas animados.
- chart: gráfico com 3 a 8 itens (label, value), todos medidos do mesmo jeito e na ordem do eixo. chart = "line"
  para série no tempo (label = ano ou mês, em ordem), "bar" para categorias, "pie" ou "donut" para partes de um
  todo (valores em %, somando perto de 100). label (título do cartão) diz o que é medido e a unidade; highlight é
  o índice do item em destaque (ex.: o último ano). Só números da narração ou das fontes, nunca inventados.
- map: mapa com 1 a 6 países ou estados dos EUA destacados; em cada item, region = código ISO ("BRA", "USA",
  "US-TX") ou nome do lugar, label curto (ou vazio para o nome do lugar) e value opcional ("214 MI"). label (título)
  diz o que o mapa mostra; highlight é o lugar principal. Use quando a batida fala de onde algo acontece, de rota,
  fronteira ou comparação entre lugares. O mapa ocupa a tela inteira: não usa imagem de fundo (source "none").
- stat continua sendo o número grande que conta na tela.
- Prefira chart e map a imagens quando a batida trouxer série de números ou lugares, sem passar de 2 cartões
  seguidos."""

# Faixa da legenda na tela (mesmas alturas do renderer, Captions.tsx).
_CAPTION_BANDS = {
    "top": "em cima, por volta de 16% da altura",
    "center": "no meio, por volta de 56% da altura",
    "bottom": "embaixo, por volta de 80% da altura",
}
_KINDS = dict.fromkeys(STORYBOARD_KINDS)
_SOURCES = dict.fromkeys(STORYBOARD_SOURCES)
_MOTIONS = dict.fromkeys(CAMERA_MOVES)


def image_budget(n_beats: int, style: StyleSpec) -> int:
    """Limite de imagens novas de IA do storyboard (o resto reaproveita imagem ou é cartão)."""
    return max(3, math.ceil(n_beats * style.edit.new_image_ratio))


def plan_beats(doc: ProjectDoc, style: StyleSpec, keep_segments: set[str] | None = None) -> list[Beat]:
    """Batidas (uma cena por batida) dos segmentos fora de `keep_segments`, na ordem do roteiro."""
    lang = doc.primary_language
    keep = keep_segments or set()
    wpm = style.script.words_per_minute.get(lang, 150)
    is_top = style.structure == Structure.top10
    beats: list[Beat] = []
    for index, seg in enumerate(doc.segments):
        if seg.id in keep:
            continue
        groups = split_beats(seg.text_for(lang), wpm=wpm, speed=style.voice.speed, min_s=style.edit.shot_seconds.min,
                             max_s=style.edit.shot_seconds.max, lang=lang) or [[]]
        for k, group in enumerate(groups):
            kind_hint, overlay = "image", None
            if k == 0 and seg.role == "chapter":
                kind_hint, overlay = "title", seg.chapter or None
            elif k == 0 and seg.role == "item" and is_top:
                kind_hint, overlay = "count", seg.chapter
            beats.append(Beat(key=f"{seg.id}:{k}", segment_id=seg.id, part=k, parts=len(groups), index=index,
                              role=seg.role, words=list(group),
                              seconds=estimate_seconds(" ".join(group), wpm, style.voice.speed),
                              kind_hint=kind_hint, overlay_hint=overlay))
    return beats


def style_catalog(style: StyleSpec | None = None) -> dict[str, str]:
    """Estilos disponíveis (id → nome) para a sugestão de estilo do storyboard.

    Com `style`, só os do mesmo formato (Short ou longo, mesma proporção): a sugestão troca a cara do vídeo,
    nunca o formato dele.
    """
    from studio.core.styles.library import list_styles

    out: dict[str, str] = {}
    for item in list_styles():
        data = item.get("style") or {}
        if not data.get("id"):
            continue
        if style is not None:
            fmt = data.get("format") or {}
            if fmt.get("type", "short") != style.format.type or fmt.get("aspect", "9:16") != style.format.aspect:
                continue
        out[data["id"]] = data.get("name") or data["id"]
    return out


def _seconds_text(seconds: float) -> str:
    return f"{seconds:.1f}".replace(".", ",")


def _beat_line(beat: Beat) -> str:
    hint = ""
    if beat.kind_hint == "count":
        hint = f', count "{beat.overlay_hint or ""}"'
    elif beat.kind_hint == "title":
        hint = f', abre o capítulo "{beat.overlay_hint}"' if beat.overlay_hint else ", abre capítulo"
    return f"- {beat.key} [{beat.role}, ~{_seconds_text(beat.seconds)} s{hint}]: {beat.text}"


def storyboard_prompt(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], budget: int, *,
                      channel: dict[str, Any] | None = None, styles: dict[str, str] | None = None) -> str:
    """Pedido do storyboard v2: contexto completo do vídeo, catálogos e as batidas."""
    lang = doc.primary_language
    channel = channel or {}
    planned = {b.segment_id for b in beats}
    total = sum(b.seconds for b in beats)
    orientation = "vertical" if style.format.height > style.format.width else "horizontal"
    position = style.edit.captions.position
    lines = [
        f"Título: {doc.title}",
        f"Tema: {doc.topic or doc.title}",
        f"Briefing: {doc.brief or '(nenhum)'}",
        f"Idioma da narração: {lang_name(lang)}",
        f"Formato: {'Short' if style.format.type == 'short' else 'vídeo longo'} {orientation} ({style.format.aspect}), "
        f"cerca de {total:.0f} s de narração nas batidas, {len(beats)} batidas.",
    ]
    if channel:
        lines.append(f"Canal: {channel.get('name') or '(sem nome)'}; nicho: {channel.get('niche') or '(não informado)'}; "
                     f"persona: {channel.get('persona') or '(não informada)'}")
        if channel.get("style_bible"):
            lines.append("Identidade visual do canal: " + json.dumps(channel["style_bible"], ensure_ascii=False))
    else:
        lines.append("Canal: (nenhum)")
    lines += [
        f"Estilo do projeto: {style.name} (id {style.id})" + (f": {style.description}" if style.description else ""),
        f"Tags do estilo: {', '.join(style.tags) or '(nenhuma)'}",
        f"Tratamento de referência do estilo: {style.art.bible}",
        f"Linguagens preferidas do estilo: {', '.join(style.art.languages) or '(livre)'}",
        f"Sempre evitar: {', '.join(style.art.banned) or '(nada)'}",
        "Fotorrealismo: permitido." if style.art.photoreal else "Fotorrealismo: não permitido (não use a linguagem photo).",
        f"Legenda: {_CAPTION_BANDS.get(position, position)}. Composições que deixam essa faixa livre: "
        f"{', '.join(compositions_for_caption(position))}.",
        f'Limite de imagens novas (source "new"): {budget} de {len(beats)} batidas. O resto reaproveita imagem '
        "(reuse) ou é cartão.",
        "",
        "Estilos disponíveis (id: nome):",
        *([f"- {sid}: {name}" for sid, name in (styles or {}).items()] or ["(nenhum)"]),
        "",
        catalog_text(photoreal=style.art.photoreal),
        "Gradações (grade): " + ", ".join(GRADE_PRESETS),
        "Câmera (motion): " + ", ".join(CAMERA_MOVES) + " (zoom_to aproxima do focus; feito para reuse)",
        "",
        "Fontes do roteiro (source_ref é o número entre colchetes):",
        *([f"- [{n}] {s.title}" + (f": {s.note}" if s.note else "") + (f" ({s.url})" if s.url else "")
           for n, s in enumerate(doc.sources, 1)] or ["(nenhuma)"]),
        "",
        "Roteiro completo (papel do segmento):",
        *[f"[{seg.role}{'' if seg.id in planned else ', cenas travadas'}] {seg.text_for(lang)}" for seg in doc.segments],
        "",
        "Batidas (chave [papel, duração]: frase):",
        *[_beat_line(b) for b in beats],
    ]
    if is_data_style(style):
        lines += ["", STORYBOARD_DATA_RULES]
    if doc.art is not None and doc.art.source == "user":
        user_art = doc.art.model_dump(mode="json", exclude={"source"})
        lines += ["", "Direção definida pelo usuário (não mude): " + json.dumps(user_art, ensure_ascii=False)]
    lines += ["", 'Devolva "direction" e uma entrada em "scenes" para cada batida, com a chave exata e na mesma ordem.']
    return "\n".join(lines)


def is_data_style(style: StyleSpec) -> bool:
    """O estilo usa a família de dados e geografia (gráficos e mapas no storyboard)."""
    return Family.data_geo in style.all_families()


def _number_label(value: float) -> str:
    """Valor curto de cartão: 2e8 → "200 MI", 1.5e9 → "1,5 BI", 2013 → "2013"."""

    def fmt(x: float) -> str:
        return str(int(x)) if float(x).is_integer() else f"{x:.2f}".rstrip("0").rstrip(".").replace(".", ",")

    for scale, suffix in ((10**12, "TRI"), (10**9, "BI"), (10**6, "MI")):
        if abs(value) >= scale:
            return f"{fmt(value / scale)} {suffix}"
    if abs(value) >= 10_000:
        return f"{fmt(value / 1000)} MIL"
    return fmt(value)


def _storyboard_context(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], budget: int) -> dict[str, Any]:
    """Dados para o LLM offline (o Claude recebe só o system e o pedido)."""
    lang = doc.primary_language
    scenes = []
    for beat in beats:
        runs = number_runs(beat.words, lang)
        scenes.append({
            "key": beat.key, "segment_id": beat.segment_id, "part": beat.part, "text": beat.text, "role": beat.role,
            "kind_hint": beat.kind_hint, "overlay_hint": beat.overlay_hint, "seconds": round(beat.seconds, 2),
            "has_number": bool(runs), "value": _number_label(runs[0][2]) if runs else None,
            "values": [_number_label(run[2]) for run in runs],
            "regions": find_regions(beat.text),
        })  # fmt: skip
    return {"topic": doc.topic or doc.title, "bible": style.art.bible, "languages": list(style.art.languages),
            "photoreal": style.art.photoreal, "budget": budget, "scenes": scenes, "data_geo": is_data_style(style),
            "sources": [{"n": n, "title": s.title, "url": s.url} for n, s in enumerate(doc.sources, 1)]}


# --- normalização da resposta -------------------------------------------------------------------


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object, limit: int = 300) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit].strip()


# Palavras que não podem terminar um texto curto cortado ("TRAILER 2 · MAIO DE").
_DANGLING = {
    "de", "do", "da", "dos", "das", "e", "o", "a", "os", "as", "em", "no", "na", "nos", "nas", "um", "uma",
    "para", "pra", "por", "pelo", "pela", "com", "sem", "ao", "of", "the", "an", "and", "in", "on", "to", "for",
    "at", "by", "with", "el", "la", "los", "las", "y", "en", "del", "al", "con",
}  # fmt: skip


def _counts_as_word(token: str) -> bool:
    return any(ch.isalnum() for ch in token)


def _words_max(value: object, n: int, limit: int = 120) -> str:
    """Até `n` palavras (separadores como "·" e "—" não contam). O corte não deixa preposição, artigo nem
    separador solto no fim; se a palavra cortada é um número logo depois de uma preposição ("EM 2013"), ele fica."""
    tokens = _text(value, limit).split()
    kept: list[str] = []
    count = 0
    cut = None
    for index, token in enumerate(tokens):
        if _counts_as_word(token):
            if count == n:
                cut = index
                break
            count += 1
        kept.append(token)
    if cut is None:
        return " ".join(kept)
    if kept and normalize_token(kept[-1]) in _DANGLING and any(ch.isdigit() for ch in tokens[cut]):
        kept.append(tokens[cut])
        return " ".join(kept)
    while kept and (not _counts_as_word(kept[-1]) or normalize_token(kept[-1]) in _DANGLING):
        kept.pop()
    return " ".join(kept)


def _entity_id(value: object) -> str:
    return normalize_token(clean_id(value) or "")[:40]


def _direction_from(raw: object, style: StyleSpec, *, current_style_id: str, style_ids: set[str]) -> ArtDirection:
    """Direção de arte da resposta, com cada valor fora do catálogo trocado pelo padrão."""
    data = raw if isinstance(raw, dict) else {}
    languages = clean_languages(_list(data.get("languages")), photoreal=style.art.photoreal)
    languages = languages or list(style.art.languages[:3]) or ["editorial"]
    entities: list[Entity] = []
    for item in _list(data.get("entities")):
        if not isinstance(item, dict):
            continue
        entity_id = _entity_id(item.get("id")) or _entity_id(item.get("name"))
        if not entity_id or any(e.id == entity_id for e in entities):
            continue
        entities.append(Entity(id=entity_id, name=_text(item.get("name"), 80), look=_text(item.get("look"), 300),
                               real=item.get("real") is True))
        if len(entities) >= 8:
            break
    grade = _text(data.get("grade"), 40).lower()
    suggested = _text(data.get("suggested_style"), 80)
    return ArtDirection(
        genre=pick(data.get("genre"), GENRES, "") or "",
        approach=pick(data.get("approach"), APPROACHES, "literal"),  # type: ignore[arg-type]
        world=_text(data.get("world")),
        treatment=_text(data.get("treatment"), 200),
        palette=[c for c in (_text(x, 40) for x in _list(data.get("palette"))) if c][:3],
        languages=languages,
        entities=entities,
        avoid=[t for t in (_text(x, 60) for x in _list(data.get("avoid"))) if t][:16],
        grade=grade if grade in GRADE_PRESETS else None,
        style_fit=pick(data.get("style_fit"), STYLE_FITS, "ok"),  # type: ignore[arg-type]
        style_note=_text(data.get("style_note")),
        suggested_style=suggested if suggested in style_ids and suggested != current_style_id else None,
        hook=_text(data.get("hook")),
        source="ai",
    )


@dataclass
class _Pick:
    """O que a batida vira, antes de criar a cena."""

    beat: Beat
    kind: str
    source: str
    """new | reuse | none"""
    root: int | None = None
    """Índice da batida dona da imagem (source "reuse")."""
    prompt: str = ""
    language: str | None = None
    shot: str | None = None
    composition: str | None = None
    focus: str | None = None
    motion: str | None = None
    entities: list[str] = field(default_factory=list)
    overlay: str | None = None
    number: int | None = None
    card: SceneCard | None = None
    ahead: int | None = None
    """Contador do Top 10: batida seguinte do mesmo item cuja imagem ele mostra (resolvida depois do orçamento)."""
    real: RealHint | None = None
    """Sugestão de mídia real da batida (só em cena de imagem)."""


_REAL_KINDS = dict.fromkeys(REAL_KINDS)
_DOUBLE_QUOTES = re.compile(r"[\"“”«»]")


def _ref_number(ref: object) -> int | None:
    if isinstance(ref, str) and ref.strip().isdigit():
        ref = int(ref.strip())
    return ref if isinstance(ref, int) and not isinstance(ref, bool) else None


def _real_from(data: dict[str, Any], kind: str, sources: list[Source]) -> RealHint | None:
    """Sugestão de mídia real da resposta: só em cena de imagem (o gancho também), com tipo válido e algo para o
    usuário usar (busca, nota ou fonte com link)."""
    real_kind = pick(data.get("real_kind"), _REAL_KINDS)
    if real_kind is None or kind != "image":
        return None
    query = _DOUBLE_QUOTES.sub("", _text(data.get("real_query"), 100)).strip(" '‘’")
    note = _text(data.get("real_note"), 200)
    ref = _ref_number(data.get("real_source_ref"))
    if ref is not None and not (1 <= ref <= len(sources) and sources[ref - 1].url.strip().lower().startswith(
            ("http://", "https://"))):
        ref = None
    if not (query or note or ref):
        return None
    return RealHint(kind=real_kind, query=query, note=note, source_ref=ref)  # type: ignore[arg-type]


# Sugestões de mídia real: no máximo 1 a cada 3 batidas (o usuário confere cada uma).
REAL_SHARE = 1 / 3


def _real_rank(hint: RealHint) -> int:
    """Prioridade da sugestão quando passa do limite: notícia com fonte, material oficial, notícia, foto."""
    if hint.kind == "news" and hint.source_ref is not None:
        return 0
    return {"official": 1, "news": 2}.get(hint.kind, 3)


def _limit_real(picks: list[_Pick]) -> list[Issue]:
    hinted = [i for i, p in enumerate(picks) if p.real is not None]
    limit = max(2, math.ceil(len(picks) * REAL_SHARE))
    if len(hinted) <= limit:
        return []
    keep = set(sorted(hinted, key=lambda i: (_real_rank(picks[i].real), i))[:limit])  # type: ignore[arg-type]
    for i in hinted:
        if i not in keep:
            picks[i].real = None
    removed = len(hinted) - limit
    text = ("1 sugestão de mídia real tirada" if removed == 1 else f"{removed} sugestões de mídia real tiradas")
    return [Issue("sugestoes_cortadas", CORRIGIDO, f"{text}: ficam {limit} neste vídeo.")]


def _source_of(ref: object, sources: list[Source]) -> str | None:
    """Fonte do cartão pelo número dela em "Fontes do roteiro" (base 1): o nome curto da fonte, nunca texto livre."""
    if isinstance(ref, str) and ref.strip().isdigit():
        ref = int(ref.strip())
    if not isinstance(ref, int) or isinstance(ref, bool) or not 1 <= ref <= len(sources):
        return None
    return storyboard_check.source_label(sources[ref - 1]) or None


# Itens por tipo de cartão: comparação, gráfico e mapa.
MAX_ITEMS = {"compare": 3, "chart": 8, "map": 6}
_YEAR = re.compile(r"(1[89]|20)\d\d")


def _map_items(data: dict[str, Any], lang: str) -> list[CardItem]:
    """Regiões do mapa que o renderer sabe desenhar (pelo código ou pelo nome), sem repetir. O rótulo que só repete o
    nome da região sai (a timeline põe o nome no idioma de cada vídeo)."""
    items: list[CardItem] = []
    seen: set[str] = set()
    for raw in _list(data.get("items")):
        if not isinstance(raw, dict):
            continue
        item_label, item_value = _words_max(raw.get("label"), 4), _text(raw.get("value"), 24)
        code = region_code(raw.get("region")) or region_code(item_label)
        if code is None or code in seen:
            continue
        seen.add(code)
        own_label = item_label and region_code(item_label) != code
        items.append(CardItem(label={lang: item_label} if own_label else {},
                              value={lang: item_value} if item_value else {}, region=code))
        if len(items) == MAX_ITEMS["map"]:
            break
    return items


def _chart_type(data: dict[str, Any], items: list[CardItem], lang: str) -> str:
    """Tipo do gráfico pedido; pizza e rosca só com valores positivos. Sem tipo: linha quando os rótulos são anos."""
    chart = pick(data.get("chart"), dict.fromkeys(CHART_TYPES))
    amounts = [card_number(item.value.get(lang, "")) for item in items]
    if chart in ("pie", "donut") and not all(a is not None and a > 0 for a in amounts):
        chart = "bar"
    if chart is None:
        years = all(_YEAR.fullmatch(item.label.get(lang, "").strip()) for item in items)
        chart = "line" if years else "bar"
    return chart


def _card_from(data: dict[str, Any], kind: str, lang: str,
               sources: list[Source] | None = None) -> tuple[str, SceneCard | None]:
    """Tipo final e cartão da cena: stat sem valor, compare sem 2 itens, gráfico sem 3 números e mapa sem região
    conhecida viram outro tipo."""
    value = _text(data.get("value"), 24)
    label = _words_max(data.get("label"), 6)
    items: list[CardItem] = []
    if kind == "map":
        items = _map_items(data, lang)
        if not items:
            kind = "stat" if value else "text"
    else:
        for raw in _list(data.get("items"))[:MAX_ITEMS.get(kind, 3)]:
            if isinstance(raw, dict):
                item_label, item_value = _words_max(raw.get("label"), 6), _text(raw.get("value"), 24)
                if item_label or item_value:
                    items.append(CardItem(label={lang: item_label} if item_label else {},
                                          value={lang: item_value} if item_value else {}))
    if kind == "chart":
        numeric = [item for item in items if card_number(item.value.get(lang, "")) is not None]
        if len(numeric) >= 3:
            items = numeric
        else:
            kind, items = "compare", (numeric or items)[:3]
    if kind == "compare" and len(items) < 2:
        if len(items) == 1 and items[0].value:
            value, label = value or items[0].value[lang], label or items[0].label.get(lang, "")
        kind = "stat" if value else "text"
    if kind == "stat" and not value:
        kind = "text"
    highlight = data.get("highlight")
    valid_highlight = isinstance(highlight, int) and not isinstance(highlight, bool) and 0 <= highlight < len(items)
    headline = _words_max(data.get("headline"), 5)
    stamp = _words_max(data.get("stamp"), 2)
    with_items = kind in ("compare", "chart", "map")
    card = SceneCard(
        value={lang: value} if kind == "stat" and value else {},
        label={lang: label} if kind in DATA_KINDS and label else {},
        items=items if with_items else [],
        highlight=highlight if with_items and valid_highlight else None,  # type: ignore[arg-type]
        chart=_chart_type(data, items, lang) if kind == "chart" else None,  # type: ignore[arg-type]
        headline={lang: headline} if headline else {},
        stamp={lang: stamp} if stamp else {},
        source=_source_of(data.get("source_ref"), sources or []),
    )
    return kind, (card if card.resolved(lang) else None)


def _next_shot(previous: str | None) -> str:
    if previous not in SHOT_ROTATION:
        return SHOT_ROTATION[0]
    return SHOT_ROTATION[(SHOT_ROTATION.index(previous) + 1) % len(SHOT_ROTATION)]


def _scenes_from(doc: ProjectDoc, style: StyleSpec, art: ArtDirection, beats: list[Beat], raw_scenes: object,
                 budget: int, positions: list[int]) -> tuple[list[Scene], list[Issue]]:
    """Cenas das batidas a partir da resposta (determinístico; nada aqui chama serviço externo)."""
    lang = doc.primary_language
    by_key: dict[str, dict[str, Any]] = {}
    for item in _list(raw_scenes):
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            by_key.setdefault(item["key"].strip(), item)
    is_top = style.structure == Structure.top10
    safe = compositions_for_caption(style.edit.captions.position)
    languages = list(art.languages) or list(style.art.languages[:3]) or ["editorial"]
    entity_ids = {e.id for e in art.entities}
    index_of = {beat.key: i for i, beat in enumerate(beats)}
    # O gancho (1ª cena do vídeo) tem prioridade no orçamento; o contador do Top 10 fica como é.
    hook = 0 if beats and storyboard_check.is_hook_beat(doc, beats[0]) else None

    picks: list[_Pick] = []
    last_new: int | None = None
    last_shot: str | None = None
    new_count = 0
    for i, beat in enumerate(beats):
        item = by_key.get(beat.key)
        data = item or {}
        kind = beat.kind_hint if item is None else (pick(data.get("kind"), _KINDS) or "image")
        if is_top and beat.role == "item" and beat.part == 0:
            kind = "count"  # Top 10: o contador do item fica como sempre foi
        elif kind == "count":
            kind = "stat"
        kind, card = _card_from(data, kind, lang, doc.sources)
        overlay: str | None = _words_max(data.get("overlay_text"), 12) or None
        number = None
        if kind == "title":
            overlay = _words_max(overlay, 6) or (beat.overlay_hint if beat.kind_hint == "title" else None) or None
            if not overlay:
                kind = "text"
        elif kind == "count":
            overlay = beat.overlay_hint or overlay
            digits = "".join(ch for ch in (overlay or "") if ch.isdigit())
            number = int(digits) if digits else None
        elif kind == "quote":
            overlay = overlay or beat.text or None
        elif kind != "text":
            overlay = None

        source = pick(data.get("source"), _SOURCES) if item is not None else None
        root = None
        ahead = None
        j = index_of.get(_text(data.get("reuse_key"), 120)) if source == "reuse" else None
        if j is not None and j < i:
            root = j if picks[j].source == "new" else picks[j].root
        if kind == "count" and (root is None or beats[root].segment_id != beat.segment_id):
            # O contador abre o item: o fundo é a imagem do próprio item, nunca a do item anterior. Com mais
            # batidas no item, é a de uma batida seguinte (sem nenhuma, o fundo animado); item de uma batida só
            # tem imagem própria, como o contador sempre teve.
            root = None
            if beat.parts > 1:
                ahead = j if j is not None and j > i and beats[j].segment_id == beat.segment_id else -1
                source = "none"
            else:
                source = "new"
        if kind == "image":
            if item is None or source == "new":
                source = "new"
            elif root is None:  # reuse inválido ou "none": imagem nova se couber, senão a última imagem nova
                source = "new" if new_count < budget or last_new is None else "reuse"
                root = None if source == "new" else last_new
        elif kind == "count" and source == "new":
            pass  # contador com imagem própria (item de uma batida só)
        elif kind == "map":
            source, root = "none", None  # o mapa ocupa a tela inteira: não usa imagem de fundo
        elif root is None and not (kind == "count" and source == "none"):
            # Cartão: fundo desfocado da última imagem nova, mesmo que a resposta peça o fundo liso ("none", que parece
            # slide); sem imagem antes, o fundo animado. O contador do Top 10 sem imagem no item fica como é.
            source, root = ("reuse", last_new) if last_new is not None else ("none", None)

        p = _Pick(beat, kind, source or "none", root, overlay=overlay, number=number, card=card, ahead=ahead,
                  real=_real_from(data, kind, doc.sources))
        if p.source == "new":
            p.prompt = _text(data.get("prompt"), 700) or beat.text
            chosen = pick(data.get("language"), VISUAL_LANGUAGES)
            p.language = chosen if chosen in languages else languages[0]
            p.shot = pick(data.get("shot"), SHOTS) or _next_shot(last_shot)
            composition = pick(data.get("composition"), COMPOSITIONS)
            p.composition = composition if composition in safe else safe[new_count % len(safe)]
            for raw in _list(data.get("entities")):
                entity_id = _entity_id(raw)
                if entity_id in entity_ids and entity_id not in p.entities:
                    p.entities.append(entity_id)
            p.focus = pick(data.get("focus"), FOCUS_POINTS)
            new_count += 1
            last_new, last_shot = i, p.shot
        elif p.source == "reuse":
            p.focus = pick(data.get("focus"), FOCUS_POINTS)
        p.motion = pick(data.get("motion"), _MOTIONS)
        picks.append(p)

    issues = _enforce_budget(picks, budget, hook, positions)
    _resolve_ahead(picks)
    issues += _trim_headlines(picks, lang, hook)
    issues += _limit_real(picks)

    uses: dict[int, int] = {}
    for i, p in enumerate(picks):
        if p.source == "reuse" and p.root is not None:
            uses[p.root] = uses.get(p.root, 0) + 1
            p.focus = p.focus or FOCUS_ROTATION[uses[p.root] % len(FOCUS_ROTATION)]
            if p.motion is None and p.kind == "image":
                p.motion = "zoom_to"
        elif p.source == "none":
            p.motion = "static"
        elif i == hook and p.motion is None:
            p.motion = "punch_zoom"

    scenes: list[Scene] = []
    for p in picks:
        beat = p.beat
        if p.source == "new":
            visual = Visual(prompt=p.prompt, seed=seed_from(doc.seed, beat.segment_id, beat.part), language=p.language,
                            shot=p.shot, composition=p.composition, entities=p.entities)
        elif p.source == "reuse":
            visual = Visual()
        else:
            visual = Visual(type="none")
        scenes.append(Scene(segment_id=beat.segment_id, part=beat.part, parts=beat.parts, kind=p.kind,  # type: ignore[arg-type]
                            visual=visual, overlay_text={lang: p.overlay} if p.overlay else {}, number=p.number,
                            motion=p.motion, span=len(beat.words), card=p.card, focus=p.focus, real=p.real))
    for p, scene in zip(picks, scenes, strict=True):
        if p.source == "reuse" and p.root is not None:
            scene.visual.reuse_of = scenes[p.root].id
    id_of = {p.beat.key: scene.id for p, scene in zip(picks, scenes, strict=True)}
    for issue in issues:
        issue.scene_id = id_of.get(issue.scene_key or "")
    return scenes, issues


def _resolve_ahead(picks: list[_Pick]) -> None:
    """Contador do Top 10: mostra a imagem de uma batida seguinte do mesmo item (a indicada pelo LLM ou a
    primeira com imagem); sem nenhuma, fica com o fundo animado."""
    for i, p in enumerate(picks):
        if p.ahead is None:
            continue
        same = [k for k in range(i + 1, len(picks)) if picks[k].beat.segment_id == p.beat.segment_id]
        order = ([p.ahead] if p.ahead in same else []) + [k for k in same if k != p.ahead]
        for k in order:
            target = k if picks[k].source == "new" else picks[k].root if picks[k].source == "reuse" else None
            if target is not None and picks[target].source == "new":
                p.source, p.root = "reuse", target
                break


# Faixas no topo (headline): no máximo 1 a cada 3 cenas, senão competem com a legenda o vídeo inteiro.
HEADLINE_SHARE = 1 / 3


def _other_digits(p: _Pick, lang: str) -> bool:
    """A cena mostra algum número além da faixa (contador, cartão, itens, carimbo ou sobreposição)."""
    if p.number is not None:
        return True
    texts = [p.overlay or ""]
    if p.card:
        texts += [p.card.value.get(lang, ""), p.card.label.get(lang, ""), p.card.stamp.get(lang, "")]
        for item in p.card.items:
            texts += [item.value.get(lang, ""), item.label.get(lang, "")]
    return any(ch.isdigit() for t in texts for ch in t)


def _trim_headlines(picks: list[_Pick], lang: str, hook: int | None) -> list[Issue]:
    """Tira as faixas acima do limite. Ficam sempre a do gancho e as que mostram o único número que a batida fala
    (sem elas a revisão acusaria número sem cartão). As vagas que sobram vão, nesta ordem, para a 1ª batida de um
    trecho (virada de assunto: imagem ou contador do Top 10), as outras imagens e por último os cartões (que já têm
    texto); dentro de cada grupo, para a faixa mais longe das que já ficaram, para não amontoar no começo."""
    with_headline = [i for i, p in enumerate(picks) if p.card and p.card.headline.get(lang)]
    limit = max(2, math.ceil(len(picks) * HEADLINE_SHARE))
    if len(with_headline) <= limit:
        return []

    def protected(i: int) -> bool:
        p = picks[i]
        headline = p.card.headline[lang]  # type: ignore[union-attr]
        number_only_here = (p.kind != "text" and any(ch.isdigit() for ch in headline)
                            and bool(notable_number_runs(p.beat.words, lang)) and not _other_digits(p, lang))
        return i == hook or number_only_here

    def rank(i: int) -> int:
        p = picks[i]
        if p.beat.part == 0 and p.kind in ("image", "count"):
            return 1
        return 2 if p.kind == "image" else 3

    keep = {i for i in with_headline if protected(i)}
    for group in (1, 2, 3):
        pool = [i for i in with_headline if i not in keep and rank(i) == group]
        while pool and len(keep) < limit:
            best = max(pool, key=lambda i: (min((abs(i - k) for k in keep), default=len(picks)), i))
            keep.add(best)
            pool.remove(best)
    removed = [i for i in with_headline if i not in keep]
    for i in removed:
        card = picks[i].card
        assert card is not None
        card.headline = {}
        picks[i].card = card if card.resolved(lang) else None
    if not removed:
        return []
    return [Issue("faixas_cortadas", CORRIGIDO,
                  f"{len(removed)} faixas no topo tiradas: ficam {len(keep)} neste vídeo (limite {limit}), para não "
                  "competir com a legenda.")]


def _enforce_budget(picks: list[_Pick], budget: int, hook: int | None, positions: list[int]) -> list[Issue]:
    """Imagem nova acima do limite vira reuse da imagem nova mais próxima (gancho e 1ª aparição de cada
    entidade têm prioridade)."""
    # O gancho que veio como cartão vira imagem nova na revisão: a vaga dele fica guardada.
    reserve = 1 if hook is not None and picks[hook].source != "new" and picks[hook].kind != "count" else 0
    limit = max(1, budget - reserve)
    news = [i for i, p in enumerate(picks) if p.source == "new"]
    if len(news) <= limit:
        return []
    keep: list[int] = []
    seen: set[str] = set()
    if hook is not None and picks[hook].source == "new":
        keep.append(hook)
        seen |= set(picks[hook].entities)
    for i in news:
        if len(keep) >= limit:
            break
        if i not in keep and set(picks[i].entities) - seen:
            keep.append(i)
            seen |= set(picks[i].entities)
    for i in news:
        if len(keep) >= limit:
            break
        if i not in keep:
            keep.append(i)
    kept = sorted(keep)
    issues: list[Issue] = []
    for i in news:
        if i in keep:
            continue
        p = picks[i]
        pool = [k for k in kept if set(picks[k].entities) & set(p.entities)] or kept
        earlier = [k for k in pool if k < i]
        target = earlier[-1] if earlier else min(pool, key=lambda k: abs(k - i))
        p.source, p.root, p.prompt = "reuse", target, ""
        p.language = p.shot = p.composition = p.focus = None
        p.entities = []
        issues.append(Issue("orcamento", CORRIGIDO,
                            f"Passou do limite de {budget} imagens novas: reaproveita a imagem da cena {positions[target]}.",
                            p.beat.key))
    for p in picks:  # quem apontava para uma imagem rebaixada passa a usar a raiz dela
        hops = 0
        while p.source == "reuse" and p.root is not None and picks[p.root].source == "reuse" and hops < len(picks):
            p.root = picks[p.root].root
            hops += 1
    return issues


def _positions(doc: ProjectDoc, beats: list[Beat], keep_segments: set[str]) -> list[int]:
    """Posição (base 1) de cada batida no vídeo final, contando as cenas dos segmentos travados."""
    count = {seg.id: len(doc.scenes_of(seg.id)) for seg in doc.segments if seg.id in keep_segments}
    for beat in beats:
        count[beat.segment_id] = beat.parts
    start: dict[str, int] = {}
    acc = 0
    for seg in doc.segments:
        start[seg.id] = acc
        acc += count.get(seg.id, 0)
    return [start[b.segment_id] + b.part + 1 for b in beats]


def _detach_kept(old: ProjectDoc, kept: list[Scene]) -> None:
    """Cena travada que reaproveitava imagem de um segmento refeito herda o Visual da raiz (mesmo cache)."""
    keep_ids = {s.segment_id for s in kept}
    for scene in kept:
        if not scene.visual.reuse_of:
            continue
        root = old.reuse_root(old.scene(scene.id))
        if root is not None and root.segment_id not in keep_ids:
            scene.visual = root.visual.model_copy(deep=True)
            scene.visual.reuse_of = None


def _carry_media(old: ProjectDoc, new: ProjectDoc, keep_segments: set[str]) -> int:
    """A mídia real anexada pelo usuário sobrevive a "refazer as cenas": a cena nova que cobre o mesmo trecho
    (mesmo segmento, parte, número de partes e palavras) recebe uma cópia dela. Devolve quantas cenas."""
    attached = {(s.segment_id, s.part, s.parts, s.span): s.visual.media for s in old.scenes
                if s.visual.media is not None and s.segment_id not in keep_segments}
    carried = 0
    for scene in new.scenes:
        if scene.segment_id in keep_segments:
            continue
        media = attached.get((scene.segment_id, scene.part, scene.parts, scene.span))
        if media is not None:
            scene.visual.media = media.model_copy(deep=True)
            carried += 1
    return carried


@dataclass
class _Attempt:
    doc: ProjectDoc
    issues: list[Issue]

    @property
    def graves(self) -> list[Issue]:
        return storyboard_check.graves(self.issues)


def _attempt(doc: ProjectDoc, style: StyleSpec, beats: list[Beat], data: object, keep_segments: set[str],
             budget: int, style_ids: set[str]) -> _Attempt:
    """Documento novo a partir de uma resposta do LLM, já normalizado e revisado."""
    payload = data if isinstance(data, dict) else {}
    if doc.art is not None and doc.art.source == "user":
        art = doc.art.model_copy(deep=True)
    else:
        art = _direction_from(payload.get("direction"), style, current_style_id=doc.style_id, style_ids=style_ids)
    new_doc = doc.model_copy(deep=True)
    kept = [s for s in new_doc.scenes if s.segment_id in keep_segments]
    _detach_kept(doc, kept)
    scenes, issues = _scenes_from(new_doc, style, art, beats, payload.get("scenes"), budget,
                                  _positions(doc, beats, keep_segments))
    new_doc.scenes = kept + scenes
    carried = _carry_media(doc, new_doc, keep_segments)
    if carried:
        text = "1 cena manteve" if carried == 1 else f"{carried} cenas mantiveram"
        issues.append(Issue("midia_mantida", CORRIGIDO, f"{text} a mídia real que você anexou."))
    new_doc.art = art
    new_doc.storyboard_approved = False
    new_doc = ProjectDoc.model_validate(new_doc.model_dump())
    issues += storyboard_check.check(new_doc, style, beats, budget)
    return _Attempt(new_doc, issues)


def _correction_block(previous: object, severe: list[Issue]) -> str:
    lines = [f"- [{i.code}] batida {i.scene_key} ({i.message})" if i.scene_key else f"- [{i.code}] ({i.message})"
             for i in severe]
    answer = json.dumps(previous, ensure_ascii=False)[:60_000]
    return ("\n\nSua resposta anterior (JSON):\n" + answer
            + "\n\nSua resposta anterior teve estes problemas; devolva tudo corrigindo:\n" + "\n".join(lines))


def _swap_invented_cards(doc: ProjectDoc, issues: list[Issue]) -> list[Issue]:
    """Número inventado que sobrou depois da correção: o cartão vira `text` (a narração aparece grande)."""
    out: list[Issue] = []
    done: set[str] = set()
    for issue in issues:
        if issue.code != "numero_inventado" or issue.severity != storyboard_check.GRAVE or not issue.scene_id:
            continue
        if issue.scene_id in done:
            continue
        done.add(issue.scene_id)
        scene = doc.scene(issue.scene_id)
        if scene.card:
            scene.card.value, scene.card.label, scene.card.items, scene.card.highlight = {}, {}, [], None
            scene.card = scene.card if scene.card.resolved(doc.primary_language) else None
        scene.kind = "text"
        out.append(Issue("cartao_trocado", CORRIGIDO,
                         f'O número "{issue.extra.get("text", "")}" não está na narração nem nas fontes: o cartão virou texto.',
                         issue.scene_key, issue.scene_id))
    return out


def _review(doc: ProjectDoc, budget: int, issues: list[Issue], retried: bool) -> dict[str, Any]:
    """Resumo da curadoria (vai para `settings.storyboard_review`, sem provider e version)."""
    art = doc.art
    return {
        "counts": storyboard_check.review_counts(doc, budget),
        "issues": [i.as_dict() for i in storyboard_check.sort_issues(issues)],
        "retried": retried,
        "style_fit": art.style_fit if art else "ok",
        "style_note": art.style_note if art else "",
        "suggested_style": art.suggested_style if art else None,
    }


def storyboard(doc: ProjectDoc, style: StyleSpec, *, project_id: str | None = None, job_id: str | None = None,
               channel: dict[str, Any] | None = None,
               styles: dict[str, str] | None = None) -> tuple[ProjectDoc, str, dict[str, Any]]:
    """Storyboard v2: direção de arte do vídeo e uma cena por batida, revisadas antes de gastar imagens.

    Segmentos com cena travada ficam como estão. Se a revisão achar problema grave, pede uma correção ao
    LLM (uma vez só) e fica a resposta com menos problemas graves. Devolve (documento, provedor, revisão).
    """
    keep_segments = {s.segment_id for s in doc.scenes if s.locked}
    beats = plan_beats(doc, style, keep_segments)
    budget = image_budget(len(beats), style)
    if not beats:
        new_doc = doc.model_copy(deep=True)
        new_doc.storyboard_approved = False
        new_doc = ProjectDoc.model_validate(new_doc.model_dump())
        return new_doc, "nenhum", _review(new_doc, budget, [], retried=False)
    catalog = styles if styles is not None else style_catalog(style)
    prompt = storyboard_prompt(doc, style, beats, budget, channel=channel, styles=catalog)
    context = _storyboard_context(doc, style, beats, budget)
    result, provider_id = _llm("storyboard", STORYBOARD_SYSTEM, prompt, STORYBOARD_SCHEMA, context, project_id, job_id)
    best = _attempt(doc, style, beats, result.data, keep_segments, budget, set(catalog))
    severe = best.graves
    retried = False
    if severe:
        retry_context = {**context, "retry": True, "issues": [i.as_dict() for i in severe]}
        try:
            second, second_provider = _llm("storyboard", STORYBOARD_SYSTEM, prompt + _correction_block(result.data, severe),
                                           STORYBOARD_SCHEMA, retry_context, project_id, job_id)
        except Exception as exc:  # noqa: BLE001 - a primeira resposta continua valendo
            log.warning("a correção do storyboard falhou; fica a primeira resposta: %s", exc)
        else:
            retried = True
            other = _attempt(doc, style, beats, second.data, keep_segments, budget, set(catalog))
            if len(other.graves) < len(severe):
                best, provider_id = other, second_provider
    issues = best.issues
    swapped = _swap_invented_cards(best.doc, issues)
    if swapped:
        issues = [i for i in issues if not (i.code == "numero_inventado" and i.severity == storyboard_check.GRAVE)]
        issues += swapped
    return best.doc, provider_id, _review(best.doc, budget, issues, retried)


TRANSLATE_SYSTEM = """Você é tradutor e adaptador de roteiros de vídeo narrado. Traduza mantendo o tom, o ritmo de fala e
a duração aproximada de cada segmento (o mesmo número de sílabas faladas, mais ou menos 10%). Adapte expressões, unidades
e referências culturais. Não acrescente nem remova informações. Mantenha os IDs exatamente iguais."""


_CARD_FIELDS = ("value", "label", "headline", "stamp")
# Número com separador de decimal ou de milhar ("3,5%", "1.000"): o separador muda com o idioma.
_SEPARATED_NUMBER_RE = re.compile(r"\d[.,]\d")


def _translatable_value(text: str) -> bool:
    """Valor de cartão que muda com o idioma: tem letras ("475 MI") ou separador ("3,5%", "1.000"); "2×" fica."""
    return any(ch.isalpha() for ch in text) or bool(_SEPARATED_NUMBER_RE.search(text))


_MEDIA_HEADLINE = "media.headline"


def _card_texts(scene: Scene, lang: str) -> list[dict[str, str]]:
    """Textos do cartão para traduzir, com id "{cena}#{campo}" (valor só quando muda com o idioma). A manchete do
    cartão de notícia (mídia real) entra como "{cena}#media.headline"."""
    out = []
    media = scene.visual.media
    if media is not None and (media.headline.get(lang) or "").strip():
        out.append({"id": f"{scene.id}#{_MEDIA_HEADLINE}", "text": media.headline[lang].strip()})
    card = scene.card
    if card is None:
        return out
    for name in _CARD_FIELDS:
        text = (getattr(card, name).get(lang) or "").strip()
        if text and (name != "value" or _translatable_value(text)):
            out.append({"id": f"{scene.id}#{name}", "text": text})
    for index, item in enumerate(card.items):
        for name in ("label", "value"):
            text = (getattr(item, name).get(lang) or "").strip()
            if text and (name != "value" or _translatable_value(text)):
                out.append({"id": f"{scene.id}#items.{index}.{name}", "text": text})
    return out


def _card_target(scene: Scene, path: str) -> dict[str, str] | None:
    """Textos por idioma de um campo do cartão ("label", "items.1.value", "media.headline"), ou None."""
    if path == _MEDIA_HEADLINE:
        return scene.visual.media.headline if scene.visual.media is not None else None
    if scene.card is None:
        return None
    parts = path.split(".")
    if parts[0] == "items":
        ok = len(parts) == 3 and parts[1].isdigit() and int(parts[1]) < len(scene.card.items) and parts[2] in ("label", "value")
        target = getattr(scene.card.items[int(parts[1])], parts[2]) if ok else None
    else:
        target = getattr(scene.card, parts[0]) if len(parts) == 1 and parts[0] in _CARD_FIELDS else None
    return target if isinstance(target, dict) else None


def _set_card_text(scene: Scene, path: str, lang: str, text: str) -> None:
    target = _card_target(scene, path)
    if target is not None and text.strip():
        target[lang] = text.strip()


def missing_screen_texts(doc: ProjectDoc, lang: str) -> list[dict[str, str]]:
    """Textos de tela (sobreposições e cartões) do idioma principal que ainda não existem em `lang`."""
    source = doc.primary_language
    if lang == source:
        return []
    out: list[dict[str, str]] = []
    for scene in doc.scenes:
        if (scene.overlay_text.get(source) or "").strip() and not (scene.overlay_text.get(lang) or "").strip():
            out.append({"id": scene.id, "text": scene.overlay_text[source]})
        for entry in _card_texts(scene, source):
            target = _card_target(scene, entry["id"].split("#", 1)[1])
            if target is not None and not (target.get(lang) or "").strip():
                out.append(entry)
    return out


def needs_translation(doc: ProjectDoc, lang: str) -> bool:
    """Falta traduzir algo para `lang`: a narração de algum segmento ou um texto de tela."""
    if lang == doc.primary_language:
        return False
    return any(not seg.text_for(lang).strip() for seg in doc.segments) or bool(missing_screen_texts(doc, lang))


def translate(doc: ProjectDoc, target_lang: str, *, screen_only: bool = False, project_id: str | None = None,
              job_id: str | None = None) -> tuple[ProjectDoc, str]:
    """Traduz a narração e os textos de tela. Com `screen_only`, só os textos de tela que faltam em
    `target_lang`: a narração já traduzida (e a voz gravada dela) fica como está."""
    source = doc.primary_language
    if screen_only:
        segments: list[dict[str, str]] = []
        overlays = missing_screen_texts(doc, target_lang)
        if not overlays:
            return doc, "nenhum"
    else:
        segments = [{"id": s.id, "text": s.text_for(source)} for s in doc.segments]
        overlays = [{"id": sc.id, "text": sc.overlay_text[source]} for sc in doc.scenes if sc.overlay_text.get(source)]
        overlays += [entry for sc in doc.scenes for entry in _card_texts(sc, source)]
    body = ("Segmentos:\n" + "\n".join(f"- {s['id']}: {s['text']}" for s in segments)) if segments else (
        "A narração já está traduzida: traduza só os textos de tela.")
    prompt = f"""Traduza de {lang_name(source)} para {lang_name(target_lang)}.
Título: {doc.title}
""" + body + (
        "\nTextos de tela (curtos; números com algarismos, adapte a unidade e o separador: 475 MI → 475M, "
        "3,5% → 3.5%):\n" + "\n".join(f"- {o['id']}: {o['text']}" for o in overlays) if overlays else "")
    context = {"target_lang": target_lang, "title": doc.title, "segments": segments, "overlays": overlays}
    result, provider_id = _llm("translate", TRANSLATE_SYSTEM, prompt, TRANSLATE_SCHEMA, context, project_id, job_id)
    new_doc = doc.model_copy(deep=True)
    seg_text = {} if screen_only else {item["id"]: item["text"] for item in result.data.get("segments", [])}
    ov_text = {item["id"]: item["text"] for item in result.data.get("overlays", [])}
    for seg in new_doc.segments:
        if seg.id in seg_text and seg_text[seg.id].strip():
            seg.text[target_lang] = seg_text[seg.id].strip()
    for scene in new_doc.scenes:
        if scene.id in ov_text and ov_text[scene.id].strip():
            scene.overlay_text[target_lang] = ov_text[scene.id]
        prefix = f"{scene.id}#"
        for key, text in ov_text.items():
            if key.startswith(prefix):
                _set_card_text(scene, key[len(prefix):], target_lang, text)
    if target_lang not in new_doc.languages:
        new_doc.languages.append(target_lang)
    meta = new_doc.metadata.get(target_lang) or Metadata()
    if result.data.get("title") and not meta.title and not screen_only:
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
